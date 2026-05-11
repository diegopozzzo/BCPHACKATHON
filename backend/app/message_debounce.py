"""
Debounce para respuestas Evolution/WhatsApp: espera a que el usuario deje de escribir
`delay` segundos (cada mensaje nuevo *reinicia* ese reloj) y entonces ejecuta *una* vez el handler.

Implementación: worker en el lifespan del app (polling), sin asyncio.create_task anidado
dentro de BackgroundTasks (evita pérdidas de tareas y reloads).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

_process_fn: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None
_pending: dict[str, tuple[float, str, dict[str, Any]]] = {}
_lock = asyncio.Lock()
_shutdown = asyncio.Event()
_worker_task: asyncio.Task[None] | None = None


async def _sweep_loop() -> None:
    fn = _process_fn
    if fn is None:
        return
    while not _shutdown.is_set():
        await asyncio.sleep(0.12)
        if _shutdown.is_set():
            break
        now = time.perf_counter()
        batch: list[tuple[str, str, dict[str, Any]]] = []
        async with _lock:
            for wa_id, (deadline, text, body) in list(_pending.items()):
                if now >= deadline:
                    batch.append((wa_id, text, body))
                    del _pending[wa_id]
        for wa_id, text, body in batch:
            logger.info(
                "Evolution debounce: silencio cumplido, procesando wa=%s preview=%r",
                wa_id,
                (text or "")[:60],
            )
            try:
                await fn(wa_id, text, body)
            except Exception:  # noqa: BLE001
                logger.exception("Error procesando mensaje (debounce worker) wa=%s", wa_id)


def start_evolution_debounce_worker(
    process_fn: Callable[[str, str, dict[str, Any]], Awaitable[None]],
) -> None:
    """Arranca el worker (llamar una vez desde lifespan, antes del yield)."""
    global _process_fn, _worker_task, _shutdown
    _process_fn = process_fn
    _shutdown.clear()
    if _worker_task is not None and not _worker_task.done():
        return
    loop = asyncio.get_running_loop()
    _worker_task = loop.create_task(_sweep_loop(), name="evolution_debounce_sweep")
    logger.info("Evolution debounce worker iniciado")


async def stop_evolution_debounce_worker() -> None:
    global _worker_task
    _shutdown.set()
    t = _worker_task
    _worker_task = None
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    async with _lock:
        _pending.clear()
    logger.info("Evolution debounce worker detenido")


async def mark_evolution_message_debounce(
    wa_id: str,
    text: str,
    body: dict[str, Any],
    delay_sec: float,
) -> None:
    """
    Registra o actualiza el mensaje pendiente: el deadline pasa a `now + delay_sec`.
    Cada mensaje nuevo del mismo usuario *extiende* la espera (no respondemos en caliente).
    """
    deadline = time.perf_counter() + max(0.05, delay_sec)
    async with _lock:
        _pending[wa_id] = (deadline, text, body)
    logger.info(
        "Evolution debounce: respuesta en ~%.1fs si no mandas otro mensaje (wa=%s)",
        delay_sec,
        wa_id,
    )
