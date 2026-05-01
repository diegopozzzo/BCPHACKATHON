from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedOpportunity:
    title: str
    type: str  # "empleo" | "curso" | "voluntariado"
    organization: str
    region: str
    requirements: str
    url: str
    source: str


def normalize_text(s: str | None) -> str:
    return " ".join((s or "").replace("\xa0", " ").split()).strip()


def guess_region(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["remoto", "remote", "home office", "teletrabajo", "híbrido", "hibrido"]):
        return "Remoto"
    if "lima" in t:
        return "Lima"
    if "arequipa" in t:
        return "Arequipa"
    if "cusco" in t:
        return "Cusco"
    return "—"

