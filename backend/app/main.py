import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.admin_router import router as admin_router
from app.agent import process_message
from app.config import Settings, get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.evolution_client import extract_inbound_text, webhook_event_name
from app.evolution_filter import evolution_should_process_message
from app.llm import narrate_company_results
from app.matching import CompanySearchResult, rank_seekers_merged
from app.schema_migrate import ensure_sqlite_schema
from app.seed import seed_if_empty
from app.setup_router import router as setup_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _configure_logging_from_settings() -> None:
    settings = get_settings()
    level_name = (settings.log_level or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.getLogger().setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging_from_settings()
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema(engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
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
    yield


app = FastAPI(title="Agente WhatsApp MVP", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(setup_router)


@app.get("/health")
def health(settings: Settings = Depends(get_settings)):
    allowed = settings.allowed_jid_set
    return {
        "status": "ok",
        "solo_chat_configurado": bool(allowed),
        "chats_permitidos": len(allowed),
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


async def _handle_evolution_payload(body: dict[str, Any]) -> None:
    settings = get_settings()
    event = webhook_event_name(body)
    if "upsert" not in event:
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

    db = SessionLocal()
    try:
        await process_message(db, settings, remote_jid, text, payload=body)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando mensaje")
    finally:
        db.close()


@app.post("/webhooks/evolution")
async def evolution_webhook(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any],
    settings: Settings = Depends(get_settings),
    apikey: str | None = Header(default=None, alias="apikey"),
    authorization: str | None = Header(default=None),
):
    _verify_webhook_secret(settings, apikey, authorization, payload)
    background_tasks.add_task(_handle_evolution_payload, payload)
    return {"received": True}


class CompanySearchBody(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)


@app.post("/company/search")
async def company_search(
    body: CompanySearchBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    ranked = rank_seekers_merged(db, body.prompt, top_n=8)
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
    narrative = await narrate_company_results(settings, body.prompt, dicts[:5])
    return {"results": [asdict(o) for o in out], "narrative": narrative}
