"""Cliente HTTP compartido para Evolution (mismo event loop que FastAPI)."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_shared: httpx.AsyncClient | None = None


def _timeouts() -> httpx.Timeout:
    return httpx.Timeout(120.0, connect=30.0)


async def get_evolution_httpx() -> httpx.AsyncClient:
    global _shared
    async with _lock:
        if _shared is None or _shared.is_closed:
            _shared = httpx.AsyncClient(timeout=_timeouts())
            logger.debug("Evolution AsyncClient inicializado (pool compartido).")
        return _shared


async def close_evolution_httpx() -> None:
    global _shared
    async with _lock:
        if _shared is not None and not _shared.is_closed:
            await _shared.aclose()
        _shared = None
