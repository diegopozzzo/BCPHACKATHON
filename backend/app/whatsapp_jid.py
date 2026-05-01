"""
Normalización y lista blanca para WhatsApp (Evolution).
Solo conversaciones 1:1 con números permitidos; grupos (@g.us) siempre ignorados.
"""

from __future__ import annotations

import re


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


def is_dm_allowed(remote_jid: str | None, allowed: set[str]) -> bool:
    if not remote_jid or not allowed:
        return False
    if is_group_jid(remote_jid):
        return False
    return canonical_dm_jid(remote_jid) in allowed
