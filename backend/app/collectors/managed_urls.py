"""
URLs consideradas gestionadas por los scrapers (no publicaciones de empleadores por WhatsApp).
Sirve para sincronizar la BD con un fetch: desactivar filas que ya no vienen en la lista nueva.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Fragmentos de host/path típicos de los collectors (jobs, cursos, voluntariado).
_SCRAPER_HINTS: frozenset[str] = frozenset(
    (
        "computrabajo.com",
        "bumeran.com",
        "getonbrd.com",
        "getonboard.com",
        "laborum",
        "mtpe.gob.pe",
        "platzi.com",
        "udemy.com",
        "coursera.org",
        "edx.org",
        "expandperu.org",
        "expandperu",
        "proa.pe",
        "unv.org",
        "volunteer",
        "onlinevolunteering.org",
    )
)


def is_managed_scrape_url(url: str | None) -> bool:
    """True si la fila parece venida de collectors (no empleador manual ni seed)."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    if "example.com" in u or "indeed.com" in u:
        return False
    try:
        host = (urlparse(u).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(h in host or h in u for h in _SCRAPER_HINTS)


def is_valid_http_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        return False
    if len(u) > 2048:
        return False
    return bool(re.match(r"^https?://[^\s]+$", u))


_ALLOWED_TYPES = frozenset({"empleo", "curso", "voluntariado"})


def validate_collected_row(
    *,
    title: str,
    type_: str,
    organization: str,
    region: str,
    requirements: str,
    url: str,
) -> tuple[bool, str]:
    """Validación mínima antes de insert/update en BD."""
    if not is_valid_http_url(url):
        return False, "url_invalida"
    t = (title or "").strip()
    if len(t) < 3 or len(t) > 255:
        return False, "titulo"
    typ = (type_ or "").strip().lower()
    if typ not in _ALLOWED_TYPES:
        return False, "tipo"
    if len((organization or "").strip()) > 255:
        return False, "organizacion"
    if len((region or "").strip()) > 128:
        return False, "region"
    if len(requirements or "") > 12000:
        return False, "requisitos"
    return True, ""


def validate_normalized(it: Any) -> tuple[bool, str]:
    return validate_collected_row(
        title=it.title,
        type_=it.type,
        organization=it.organization,
        region=it.region,
        requirements=it.requirements,
        url=it.url,
    )
