import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

import app.models  # noqa: F401
from app.admin_router import router as admin_router
from app.agent import process_message
from app.config import Settings, get_settings
from app.db import Base, SessionLocal, engine
from app.evolution_http import close_evolution_httpx
from app.evolution_client import extract_inbound_text, is_duplicate_inbound_stanza, webhook_event_name
from app.evolution_filter import evolution_should_process_message
from app.latency_metrics import emit_latency
from app.llm import narrate_company_results
from app.matching import CompanySearchResult, rank_seekers_merged
from app.schema_migrate import ensure_sqlite_schema
from app.message_debounce import (
    mark_evolution_message_debounce,
    start_evolution_debounce_worker,
    stop_evolution_debounce_worker,
)
from app.profile_merge import merge_any_phone_duplicates, merge_lid_profiles_into_canonical
from app.seed import seed_if_empty
from app.setup_router import router as setup_router
from app.whatsapp_jid import resolve_stable_wa_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _company_search_rank_worker(prompt: str) -> tuple[list[CompanySearchResult], list[dict[str, Any]], float]:
    """DB + matching en hilo auxiliar (no bloquea el event loop)."""
    t0 = time.perf_counter()
    db = SessionLocal()
    try:
        ranked = rank_seekers_merged(db, prompt, top_n=8)
    finally:
        db.close()
    rank_worker_ms = (time.perf_counter() - t0) * 1000
    out: list[CompanySearchResult] = []
    dicts: list[dict[str, Any]] = []
    for score, reason, m in ranked:
        item = CompanySearchResult(
            display_name=m.display_name,
            region=m.region,
            goal=m.goal,
            skills=m.skills,
            score=score,
            reason=reason,
            summary=m.summary,
            source=m.source,
        )
        out.append(item)
        dicts.append(
            {
                "nombre": m.display_name,
                "region": m.region,
                "meta": m.goal,
                "skills": m.skills,
                "score": score,
                "razon": reason,
                "resumen": m.summary,
                "origen": m.source,
            }
        )
    return out, dicts, rank_worker_ms


def _configure_logging_from_settings() -> None:
    settings = get_settings()
    level_name = (settings.log_level or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.getLogger().setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


async def _run_evolution_process_message(wa_id: str, text: str, body: dict[str, Any]) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        await process_message(db, settings, wa_id, text, payload=body)
        logger.info(
            "Evolution mensaje procesado wa=%s preview=%r",
            wa_id,
            (text or "")[:80],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando mensaje")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging_from_settings()
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema(engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
        st = get_settings()
        merge_any_phone_duplicates(db)
        merge_lid_profiles_into_canonical(db, st)
    finally:
        db.close()
    allowed = get_settings().allowed_jid_set
    if not allowed:
        logger.error(
            "Sin chat permitido: define EVOLUTION_MY_PHONE (solo tu número) o "
            "EVOLUTION_ALLOWED_JID / EVOLUTION_ALLOWED_JIDS. El bot no responderá a nadie."
        )
    else:
        logger.info("Modo solo tu(s) chat(s): %s conversación(es) permitida(s).", len(allowed))
    start_evolution_debounce_worker(_run_evolution_process_message)
    try:
        yield
    finally:
        await stop_evolution_debounce_worker()
        await close_evolution_httpx()


app = FastAPI(title="Agente WhatsApp MVP", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(setup_router)


@app.get("/health")
def health(settings: Settings = Depends(get_settings)):
    allowed = settings.allowed_jid_set
    base = (settings.app_base_url or "http://localhost:8000").rstrip("/")
    return {
        "status": "ok",
        "solo_chat_configurado": bool(allowed),
        "chats_permitidos": len(allowed),
        "webhook_url_backend": f"{base}/webhooks/evolution",
        "webhook_tip_docker": (
            "Si Evolution corre en Docker y el backend en tu PC, en Evolution configurá el webhook a "
            "http://host.docker.internal:8000/webhooks/evolution (puerto igual que APP_BASE_URL/PORT)."
        ),
    }


def _verify_webhook_secret(
    settings: Settings,
    apikey_header: str | None,
    authorization: str | None,
    body: dict[str, Any],
) -> None:
    secret = settings.webhook_secret
    if not secret:
        return
    candidates: list[str] = []
    if apikey_header:
        candidates.append(apikey_header.strip())
    if authorization and authorization.lower().startswith("bearer "):
        candidates.append(authorization.split(" ", 1)[1].strip())
    body_key = body.get("apikey")
    if isinstance(body_key, str):
        candidates.append(body_key.strip())
    if secret not in candidates:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _is_messages_upsert_event(event: str) -> bool:
    """Evolution envía `messages.upsert`, `MESSAGES_UPSERT`, etc."""
    n = str(event or "").lower().replace("-", "_").replace(".", "_")
    return "messages" in n and "upsert" in n


async def _handle_evolution_payload(body: dict[str, Any]) -> None:
    settings = get_settings()
    event = webhook_event_name(body)
    if not _is_messages_upsert_event(event):
        logger.debug(
            "Evolution webhook ignorado (no es messages upsert): event=%s",
            event or "(vacío)",
        )
        return

    remote_jid, text, from_me, stanza_id = extract_inbound_text(body)
    # Allow document-only messages (e.g. CV upload) to pass the filter.
    if not text:
        from app.evolution_media import extract_inbound_document

        doc = extract_inbound_document(body)
        if doc:
            remote_jid = remote_jid or doc.remote_jid
            from_me = from_me or doc.from_me
            stanza_id = stanza_id or doc.stanza_id
            text = "(documento)"
    ok, reason = evolution_should_process_message(
        body,
        remote_jid=remote_jid,
        text=text,
        from_me=from_me,
        stanza_id=stanza_id,
        settings=settings,
    )
    if not ok:
        logger.info("Webhook Evolution no procesado (%s): fromMe=%s jid=%s", reason, from_me, remote_jid)
        return

    stable_wa = resolve_stable_wa_id(remote_jid or "", settings.allowed_jid_set, body)
    if not stable_wa:
        logger.info("Evolution: sin wa_id estable (remote_jid vacío tras resolver).")
        return

    if is_duplicate_inbound_stanza(stable_wa, stanza_id):
        logger.info(
            "Evolution: mensaje ya procesado (stanza duplicado), ignorado wa=%s id=%s",
            stable_wa,
            stanza_id or "(vacío)",
        )
        return

    delay = float(settings.whatsapp_reply_debounce_sec or 0.0)
    if delay <= 0:
        await _run_evolution_process_message(stable_wa, text, body)
    else:
        await mark_evolution_message_debounce(stable_wa, text, body, delay)


@app.post("/webhooks/evolution")
async def evolution_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    apikey: str | None = Header(default=None, alias="apikey"),
    authorization: str | None = Header(default=None),
):
    try:
        raw: Any = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Cuerpo JSON inválido") from None

    items: list[dict[str, Any]]
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
        if not items:
            raise HTTPException(status_code=400, detail="Lista JSON vacía o sin objetos")
    else:
        raise HTTPException(status_code=400, detail="Se esperaba un objeto JSON o una lista de objetos")

    _verify_webhook_secret(settings, apikey, authorization, items[0])
    for payload in items:
        background_tasks.add_task(_handle_evolution_payload, payload)
    return {"received": True, "queued": len(items)}


class CompanySearchBody(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)


@app.post("/company/search")
async def company_search(body: CompanySearchBody, settings: Settings = Depends(get_settings)):
    """Async: ranking en threadpool; narración LLM con await (sin asyncio.run)."""
    t_req = time.perf_counter()
    out, dicts, rank_worker_ms = await asyncio.to_thread(_company_search_rank_worker, body.prompt)
    mode = settings.effective_llm_narration()
    openai_ms = 0.0
    narrative: str | None = None
    if mode != "off":
        t_o = time.perf_counter()
        narrative = await narrate_company_results(settings, body.prompt, dicts[:5])
        openai_ms = (time.perf_counter() - t_o) * 1000
    total_ms = (time.perf_counter() - t_req) * 1000
    emit_latency(
        settings,
        "company_search",
        match_ms=round(rank_worker_ms, 2),
        rank_worker_ms=round(rank_worker_ms, 2),
        openai_ms=round(openai_ms, 2),
        total_ms=round(total_ms, 2),
        results=len(out),
    )
    return {"results": [asdict(o) for o in out], "narrative": narrative}
