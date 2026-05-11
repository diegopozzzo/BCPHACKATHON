"""
Normalización y lista blanca para WhatsApp (Evolution).
Solo conversaciones 1:1 con números permitidos; grupos (@g.us) siempre ignorados.
"""

from __future__ import annotations

import re
from typing import Any


def _user_part_digits(user: str) -> str:
    """Quita sufijo :device de multidispositivo (ej. 51999:2@s... → 51999)."""
    u = user.split(":", 1)[0] if user else ""
    return re.sub(r"\D", "", u)


def canonical_dm_jid(raw: str) -> str:
    """
    Unifica JID de usuario a {digits}@s.whatsapp.net para comparar entradas
    (Evolution puede mandar @s.whatsapp.net o @c.us en algunos casos).
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    user, _, domain = raw.partition("@")
    digits = _user_part_digits(user)
    if not digits:
        return raw.lower()
    dom = domain.lower()
    if dom == "g.us":
        return f"{digits}@g.us"
    if dom in ("s.whatsapp.net", "c.us", "whatsapp.net", ""):
        return f"{digits}@s.whatsapp.net"
    return f"{user}@{domain}".lower()


def jid_phone_digits(remote_jid: str | None) -> str:
    """Solo dígitos del número en un JID DM (limpia sufijo multidispositivo)."""
    if not remote_jid:
        return ""
    raw = (remote_jid or "").strip()
    if not raw:
        return ""
    user, _, _ = raw.partition("@")
    return _user_part_digits(user)


def same_dm_contact(a: str | None, b: str | None) -> bool:
    """
    Comparación estable entre Evolution JIDs (mezcla @s.whatsapp.net / @lid / sufijos :device).
    """
    if not a or not b:
        return False
    ca = canonical_dm_jid(a)
    cb = canonical_dm_jid(b)
    if ca == cb:
        return True
    da, db = jid_phone_digits(a), jid_phone_digits(b)
    return bool(da and db and da == db)


def is_group_jid(remote_jid: str) -> bool:
    return remote_jid.rstrip().lower().endswith("@g.us")


def build_allowed_dm_set(
    *,
    csv: str,
    single: str = "",
    my_phone: str = "",
) -> set[str]:
    """
    Construye el conjunto de JIDs permitidos (solo DM, forma canónica).
    - csv: EVOLUTION_ALLOWED_JIDS (coma)
    - single: EVOLUTION_ALLOWED_JID (un solo valor)
    - my_phone: EVOLUTION_MY_PHONE (solo dígitos, ej. 51987654321)
    """
    parts: list[str] = []
    if single.strip():
        parts.append(single.strip())
    for p in csv.split(","):
        if p.strip():
            parts.append(p.strip())
    if my_phone.strip():
        digits = re.sub(r"\D", "", my_phone.strip())
        if digits:
            parts.append(f"{digits}@s.whatsapp.net")
    return {canonical_dm_jid(p) for p in parts if p}


def _webhook_data_entries(data: Any) -> list[dict[str, Any]]:
    """Misma forma que `evolution_webhook_entries` pero sin importar evolution_client (evitar ciclos)."""
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        inner = data.get("messages")
        if isinstance(inner, list) and inner:
            return [e for e in inner if isinstance(e, dict)]
        return [data]
    return []


def _sender_from_webhook_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
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
    return None


def _first_remote_jid_alt_from_payload(payload: dict[str, Any] | None) -> str | None:
    """Baileys / Evolution: JID PN alterno cuando el chat usa @lid."""
    if not isinstance(payload, dict):
        return None
    for e in _webhook_data_entries(payload.get("data")):
        k = e.get("key")
        if not isinstance(k, dict):
            continue
        for ak in ("remoteJidAlt", "remote_jid_alt", "RemoteJidAlt"):
            alt = k.get(ak)
            if isinstance(alt, str) and alt.strip():
                a = alt.strip()
                if not a.lower().endswith("@lid"):
                    return a
    return None


def resolve_stable_wa_id(
    remote_jid: str,
    allowed_dm: set[str],
    payload: dict[str, Any] | None = None,
) -> str:
    """
    Unifica perfiles en BD: el mismo contacto no debe quedar como varias filas
    (p. ej. `...@lid` vs `519...@s.whatsapp.net`).
    """
    r = (remote_jid or "").strip()
    if not r:
        return ""
    if not r.lower().endswith("@lid"):
        return canonical_dm_jid(r)

    alt = _first_remote_jid_alt_from_payload(payload)
    if alt:
        return canonical_dm_jid(alt)

    sender = _sender_from_webhook_payload(payload)
    sd = jid_phone_digits(sender) if sender else ""
    if sd:
        for a in allowed_dm:
            if jid_phone_digits(a) == sd:
                return canonical_dm_jid(a)

    # Bot típico: un solo número en lista blanca → todo @lid permitido es ese usuario.
    if len(allowed_dm) == 1:
        return canonical_dm_jid(next(iter(allowed_dm)))

    return canonical_dm_jid(r)


def is_dm_allowed(
    remote_jid: str | None,
    allowed: set[str],
    *,
    sender_jid: str | None = None,
) -> bool:
    if not remote_jid or not allowed:
        return False
    if is_group_jid(remote_jid):
        return False
    if canonical_dm_jid(remote_jid) in allowed:
        return True
    # Mensajes contigo / Evolution pueden usar @lid, sufijos :N o dominios distintos al mismo número.
    digits_remote = jid_phone_digits(remote_jid)
    if digits_remote:
        for a in allowed:
            if jid_phone_digits(a) == digits_remote:
                return True
    # Chat 1:1 con JID @lid (p. ej. "Mensajes contigo"): el número real va en `sender`.
    if remote_jid.rstrip().lower().endswith("@lid") and sender_jid:
        sd = jid_phone_digits(sender_jid)
        if sd:
            for a in allowed:
                if jid_phone_digits(a) == sd:
                    return True
    return False
