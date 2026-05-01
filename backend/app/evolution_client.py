import logging
import re
import time
from typing import Any

import httpx

from app.config import Settings
from app.whatsapp_jid import is_dm_allowed

logger = logging.getLogger(__name__)

_BOT_MSG_TTL_SEC = 120.0
_bot_message_expirations: dict[str, float] = {}

# Respaldo si sendText no devuelve id: evita eco inmediato con fromMe (segundos por JID canónico).
_last_send_mono: dict[str, float] = {}
_ECHO_TIME_SEC = 5.0


def _prune_bot_message_ids() -> None:
    now = time.monotonic()
    dead = [k for k, exp in _bot_message_expirations.items() if exp < now]
    for k in dead:
        del _bot_message_expirations[k]


def register_bot_message_id_from_response(data: dict[str, Any] | None) -> None:
    """Registra el id del mensaje que devolvió Evolution tras sendText (NEKOBOT: extractProviderMessageId)."""
    mid = extract_provider_message_id(data or {})
    if not mid:
        return
    _prune_bot_message_ids()
    _bot_message_expirations[mid] = time.monotonic() + _BOT_MSG_TTL_SEC


def is_recent_bot_message_id(stanza_id: str | None) -> bool:
    """True si este id fue el de un envío nuestro reciente (eco con fromMe)."""
    if not stanza_id:
        return False
    _prune_bot_message_ids()
    return stanza_id in _bot_message_expirations


def mark_outbound_time(remote_jid: str) -> None:
    from app.whatsapp_jid import canonical_dm_jid

    _last_send_mono[canonical_dm_jid(remote_jid)] = time.monotonic()


def is_recent_outbound_time_echo(remote_jid: str | None) -> bool:
    from app.whatsapp_jid import canonical_dm_jid

    if not remote_jid:
        return False
    jid = canonical_dm_jid(remote_jid)
    t = _last_send_mono.get(jid)
    if t is None:
        return False
    return (time.monotonic() - t) < _ECHO_TIME_SEC


def extract_provider_message_id(response: dict[str, Any]) -> str | None:
    """Igual que NEKOBOT `extractProviderMessageId` (client.ts)."""
    key = response.get("key")
    if isinstance(key, dict):
        kid = key.get("id")
        if isinstance(kid, str) and kid.strip():
            return kid.strip()
    messages = response.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            mid = first.get("id")
            if isinstance(mid, str) and mid.strip():
                return mid.strip()
    return None


def normalize_phone_from_jid(remote_jid: str) -> str:
    base = remote_jid.split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", base)


def _extract_text_from_message(message: dict[str, Any]) -> str | None:
    if "conversation" in message and isinstance(message["conversation"], str):
        return message["conversation"]
    if "extendedTextMessage" in message:
        etm = message["extendedTextMessage"] or {}
        if isinstance(etm.get("text"), str):
            return etm["text"]
    if "imageMessage" in message and isinstance((message["imageMessage"] or {}).get("caption"), str):
        return (message["imageMessage"] or {})["caption"]
    return None


def evolution_webhook_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def evolution_remote_jid_from_key(key: dict[str, Any]) -> str | None:
    if not isinstance(key, dict):
        return None
    for kk in ("remoteJid", "remote_jid", "RemoteJid"):
        rj = key.get(kk)
        if isinstance(rj, str) and rj.strip():
            return rj.strip()
    return None


def extract_inbound_text(payload: dict[str, Any]) -> tuple[str | None, str | None, bool, str | None]:
    """
    Retorna (remote_jid, texto, from_me, stanza_id).
    Si `data` es lista, recorre entradas (como NEKOBOT readEntries) hasta hallar texto.
    """
    entries = evolution_webhook_entries(payload)

    fallback_jid: str | None = None
    fallback_from_me = False
    fallback_stanza: str | None = None

    for entry in entries:
        key = entry.get("key") or {}
        if not isinstance(key, dict):
            continue
        remote_jid = evolution_remote_jid_from_key(key)
        from_me = bool(key.get("fromMe"))
        kid = key.get("id")
        stanza_id = kid.strip() if isinstance(kid, str) else None
        message = entry.get("message") or {}
        if remote_jid and not fallback_jid:
            fallback_jid = remote_jid
            fallback_from_me = from_me
            fallback_stanza = stanza_id
        if not isinstance(message, dict):
            continue
        text = _extract_text_from_message(message)
        if text and text.strip() and remote_jid:
            return remote_jid, text.strip(), from_me, stanza_id

    # Sin cuerpo de texto (ej. sólo archivo o evento), pero sí hay JID.
    return fallback_jid, None, fallback_from_me, fallback_stanza


def webhook_event_name(payload: dict[str, Any]) -> str:
    ev = payload.get("event") or payload.get("Event") or ""
    return str(ev).lower()


async def send_whatsapp_text(settings: Settings, remote_jid: str, body: str) -> None:
    allowed = settings.allowed_jid_set
    if not is_dm_allowed(remote_jid, allowed):
        logger.warning("Envío bloqueado: el JID no está en tu lista blanca (solo tu chat).")
        return
    if not settings.evolution_instance or not settings.evolution_api_key:
        logger.warning("Evolution no configurado: no se envía mensaje.")
        return
    url = (
        f"{settings.evolution_base_url.rstrip('/')}"
        f"/message/sendText/{settings.evolution_instance}"
    )
    number = normalize_phone_from_jid(remote_jid)
    headers = {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}
    payload = {"number": number, "text": body}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            logger.error("Evolution sendText error %s: %s", r.status_code, r.text)
            return
        mark_outbound_time(remote_jid)
        try:
            data = r.json()
            if isinstance(data, dict):
                register_bot_message_id_from_response(data)
        except Exception:  # noqa: BLE001
            pass
