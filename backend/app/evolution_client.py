import logging
import re
import threading
import time
from typing import Any

from app.config import Settings
from app.evolution_http import get_evolution_httpx
from app.whatsapp_jid import is_dm_allowed

logger = logging.getLogger(__name__)

_BOT_MSG_TTL_SEC = 120.0
_bot_message_expirations: dict[str, float] = {}

# Eco por texto (self-chat): si el id del webhook no coincide con el de sendText, igual detectamos
# el mensaje saliente reciente y no lo re-procesamos como si fuera del usuario.
_BOT_OUTBOUND_TEXT_TTL_SEC = 120.0
_BOT_OUTBOUND_TEXT_MAX_PER_JID = 6
_MIN_ECHO_TEXT_LEN = 24
_bot_outbound_text_buffer: dict[str, list[tuple[float, str]]] = {}

# Respaldo si sendText no devuelve id: evita eco inmediato con fromMe (segundos por JID canónico).
_last_send_mono: dict[str, float] = {}
_ECHO_TIME_SEC = 5.0

# Evolution a veces re-envía el mismo stanza_id o manda upserts solo de protocolo (sin texto de usuario).
_STANZA_DEDUPE: dict[str, float] = {}
_STANZA_DEDUPE_TTL_SEC = 180.0
_STANZA_LOCK = threading.Lock()


def _prune_stanza_dedupe(now: float) -> None:
    dead = [k for k, t in _STANZA_DEDUPE.items() if now - t > _STANZA_DEDUPE_TTL_SEC]
    for k in dead:
        del _STANZA_DEDUPE[k]


def is_duplicate_inbound_stanza(wa_id: str, stanza_id: str | None) -> bool:
    """
    True si ya procesamos este mensaje (mismo wa + id de Baileys).
    Evita doble respuesta por webhooks duplicados o reintentos de Evolution.
    """
    if not stanza_id or not (wa_id or "").strip():
        return False
    from app.whatsapp_jid import canonical_dm_jid

    key = f"{canonical_dm_jid(wa_id)}:{stanza_id}"
    now = time.monotonic()
    with _STANZA_LOCK:
        _prune_stanza_dedupe(now)
        if key in _STANZA_DEDUPE:
            return True
        _STANZA_DEDUPE[key] = now
        return False


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


def _normalize_echo_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _prune_bot_outbound_text_buffer(now: float) -> None:
    for jid, rows in list(_bot_outbound_text_buffer.items()):
        alive = [(exp, t) for exp, t in rows if exp >= now]
        if alive:
            _bot_outbound_text_buffer[jid] = alive[-_BOT_OUTBOUND_TEXT_MAX_PER_JID :]
        else:
            del _bot_outbound_text_buffer[jid]


def register_bot_outbound_text_for_echo(remote_jid: str, text: str) -> None:
    """Registra el cuerpo enviado para filtrar ecos en self-chat cuando falle el match por id."""
    from app.whatsapp_jid import canonical_dm_jid

    norm = _normalize_echo_text(text)
    if len(norm) < _MIN_ECHO_TEXT_LEN:
        return
    jid = canonical_dm_jid(remote_jid)
    now = time.monotonic()
    deadline = now + _BOT_OUTBOUND_TEXT_TTL_SEC
    _prune_bot_outbound_text_buffer(now)
    rows = _bot_outbound_text_buffer.get(jid, [])
    rows.append((deadline, norm))
    _bot_outbound_text_buffer[jid] = rows[-_BOT_OUTBOUND_TEXT_MAX_PER_JID:]


def is_bot_outbound_text_echo(remote_jid: str | None, inbound_text: str | None) -> bool:
    """True si el texto coincide con un envío nuestro reciente (mismo JID), típico eco del bot."""
    if not remote_jid or not inbound_text:
        return False
    norm = _normalize_echo_text(inbound_text)
    if len(norm) < _MIN_ECHO_TEXT_LEN:
        return False
    from app.whatsapp_jid import canonical_dm_jid

    jid = canonical_dm_jid(remote_jid)
    now = time.monotonic()
    _prune_bot_outbound_text_buffer(now)
    for exp, stored in _bot_outbound_text_buffer.get(jid, []):
        if exp >= now and stored == norm:
            return True
    return False


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
    """Igual que NEKOBOT `extractProviderMessageId` (client.ts); admite respuesta envuelta en `data`."""
    if not isinstance(response, dict):
        return None
    roots: list[dict[str, Any]] = [response]
    data = response.get("data")
    if isinstance(data, dict):
        roots.append(data)

    for root in roots:
        key = root.get("key")
        if isinstance(key, dict):
            kid = key.get("id")
            if isinstance(kid, str) and kid.strip():
                return kid.strip()
        messages = root.get("messages")
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                mid = first.get("id")
                if isinstance(mid, str) and mid.strip():
                    return mid.strip()
                k2 = first.get("key")
                if isinstance(k2, dict):
                    kid2 = k2.get("id")
                    if isinstance(kid2, str) and kid2.strip():
                        return kid2.strip()
    return None


def normalize_phone_from_jid(remote_jid: str) -> str:
    base = remote_jid.split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", base)


def _unwrap_proto_message(message: dict[str, Any]) -> dict[str, Any]:
    """Baileys a veces envía el texto dentro de ephemeral / viewOnce / documentWithCaption."""
    if not isinstance(message, dict):
        return {}
    cur: dict[str, Any] = message
    for _ in range(10):
        progressed = False
        for outer, inner_k in (
            ("ephemeralMessage", "message"),
            ("viewOnceMessage", "message"),
            ("viewOnceMessageV2", "message"),
            ("documentWithCaptionMessage", "message"),
        ):
            blob = cur.get(outer)
            if not isinstance(blob, dict):
                continue
            nxt = blob.get(inner_k) if isinstance(blob.get(inner_k), dict) else blob
            if isinstance(nxt, dict):
                cur = nxt
                progressed = True
                break
        if not progressed:
            break
    return cur


def _extract_text_from_message(message: dict[str, Any]) -> str | None:
    leaf = _unwrap_proto_message(message)
    # Avisos de protocolo / sync / reacciones: no son “el usuario escribió” (evitan respuestas fantasma).
    _NOISE_KEYS = (
        "protocolMessage",
        "senderKeyDistributionMessage",
        "reactionMessage",
        "pollUpdateMessage",
        "keepInChatMessage",
        "encEventResponse",
        "deviceSentMessage",
        "newsletterAdminInviteMessage",
        "call",
        "contactsArrayMessage",
        "groupInviteMessage",
    )
    _USER_CONTENT_KEYS = (
        "conversation",
        "extendedTextMessage",
        "imageMessage",
        "buttonsResponseMessage",
        "listResponseMessage",
        "interactiveMessage",
        "documentMessage",
        "audioMessage",
        "videoMessage",
        "liveLocationMessage",
    )
    if any(k in leaf for k in _NOISE_KEYS) and not any(k in leaf for k in _USER_CONTENT_KEYS):
        return None

    if "conversation" in leaf and isinstance(leaf["conversation"], str):
        return leaf["conversation"]
    if "extendedTextMessage" in leaf:
        etm = leaf["extendedTextMessage"] or {}
        if isinstance(etm.get("text"), str):
            return etm["text"]
    if "imageMessage" in leaf and isinstance((leaf["imageMessage"] or {}).get("caption"), str):
        return (leaf["imageMessage"] or {})["caption"]
    br = leaf.get("buttonsResponseMessage")
    if isinstance(br, dict) and isinstance(br.get("selectedDisplayText"), str):
        t = br["selectedDisplayText"].strip()
        if t:
            return t
    lr = leaf.get("listResponseMessage")
    if isinstance(lr, dict) and isinstance(lr.get("title"), str):
        t = lr["title"].strip()
        if t:
            return t
    im = leaf.get("interactiveMessage")
    if isinstance(im, dict):
        b = im.get("body")
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            t = b["text"].strip()
            if t:
                return t
    return None


def evolution_webhook_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        inner = data.get("messages")
        if isinstance(inner, list) and inner:
            return [e for e in inner if isinstance(e, dict)]
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
    if not number:
        logger.error("Evolution sendText: número vacío (jid=%s)", remote_jid)
        return
    headers = {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}
    payload = {"number": number, "text": body, "linkPreview": False}
    client = await get_evolution_httpx()
    r = await client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        logger.error("Evolution sendText HTTP %s jid=%s body=%s", r.status_code, remote_jid, r.text[:800])
        return
    logger.info("Evolution sendText OK (dígitos=%s, chars=%s)", len(number), len(body))
    mark_outbound_time(remote_jid)
    register_bot_outbound_text_for_echo(remote_jid, body)
    try:
        data = r.json()
        if isinstance(data, dict):
            register_bot_message_id_from_response(data)
    except Exception:  # noqa: BLE001
        pass
