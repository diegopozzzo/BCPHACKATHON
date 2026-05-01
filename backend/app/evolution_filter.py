"""
Filtro de mensajes Evolution alineado a NEKOBOT (packages/providers/src/evolution-api/normalizer.ts):

- *self chat*: `wa_digits(remoteJid) == wa_digits(payload["sender"])`
- `EVOLUTION_SELF_CHAT_MODE`:
  - `only`: solo conversaciones contigo mismo (Mensajes contigo).
  - `allow`: acepta chats normales + contigo; `fromMe` solo si es self-chat.
  - `disabled`: nunca procesa `fromMe` (comportamiento WhatsApp clásico).
- Eco del bot: IDs devueltos por `sendText` se ignoran si llegan de nuevo con `fromMe`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from app.config import Settings
from app.evolution_client import is_recent_bot_message_id
from app.whatsapp_jid import is_dm_allowed

logger = logging.getLogger(__name__)

SelfChatMode = Literal["disabled", "allow", "only"]


def _digits(value: str | None) -> str:
    if not value:
        return ""
    base = str(value).split("@", 1)[0]
    base = base.split(":", 1)[0]
    return re.sub(r"\D", "", base)


def _payload_sender_raw(payload: dict[str, Any]) -> str:
    """Evolution a veces pone `sender` arriba o dentro de `data` (objeto)."""
    for k in ("sender", "Sender"):
        v = payload.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for k in ("sender", "Sender"):
            v = data.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def evolution_is_self_chat(payload: dict[str, Any], remote_jid: str | None, settings: Settings) -> bool:
    """
    NEKOBOT: `wa_digits(remoteJid) == wa_digits(payload['sender'])`.
    Si Evolution no manda `sender`, y solo hay un JID en lista blanca, se asume self-chat
    cuando el chat es ese número (Mensajes contigo).
    """
    u = _digits(remote_jid)
    s = _digits(_payload_sender_raw(payload))
    if u and s and u == s:
        return True
    if not s and u and len(settings.allowed_jid_set) == 1:
        only_jid = next(iter(settings.allowed_jid_set))
        if _digits(only_jid) == u:
            return True
    return False


def _effective_self_chat_mode(settings: Settings) -> SelfChatMode:
    raw = (settings.evolution_self_chat_mode or "only").strip().lower()
    if raw in ("disabled", "allow", "only"):
        return raw  # type: ignore[return-value]
    if settings.evolution_allow_from_me:
        return "allow"
    return "only"


def evolution_should_process_message(
    payload: dict[str, Any],
    *,
    remote_jid: str | None,
    text: str | None,
    from_me: bool,
    stanza_id: str | None,
    settings: Settings,
) -> tuple[bool, str]:
    if not remote_jid or not text:
        return False, "sin_jid_o_texto"

    allowed = settings.allowed_jid_set
    if not allowed:
        return False, "lista_blanca_vacía"

    if not is_dm_allowed(remote_jid, allowed):
        return False, "jid_no_permitido_o_grupo"

    mode = _effective_self_chat_mode(settings)

    is_self = evolution_is_self_chat(payload, remote_jid, settings)

    if mode == "only" and not is_self:
        return False, "solo_chat_contigo_modo_only"

    if from_me:
        if is_recent_bot_message_id(stanza_id):
            return False, "eco_bot_por_id"
        if not (mode != "disabled" and is_self):
            return False, "fromMe_fuera_de_self_chat_o_modo_disabled"

    return True, "ok"
