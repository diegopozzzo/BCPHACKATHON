"""Heurísticas de perfil + detección de focos (trabajo, prácticas, cursos, etc.)."""

from __future__ import annotations

import re
from typing import Any

from app.models import UserProfile

URL_RE = re.compile(r"https?://[^\s]+\b", re.I)

_FOCUS_KEYWORDS: list[tuple[str, ...]] = [
    ("empleo", "trabajo", "laboral", "full-time", "full time", "clt"),
    ("práctica", "practicas", "prácticas", "internship", "beca práctica"),
    ("freelance", "freelancer", "por proyecto", "consultoría", "consultoria"),
    ("curso", "cursos", "capacita", "estudi", "certificación", "certificacion"),
    ("voluntariado", "voluntario", "ong", "pro bono"),
    ("investiga", "tesis", "laboratorio", "paper"),
    ("empre", "startup", "incubadora", "emprendedor"),
    ("hackathon", "hackatón"),
    ("mentor", "networking"),
]

_MODAL_KEYWORDS = {
    "remoto": ("remoto", "remote", "wfh", "desde casa", "online"),
    "híbrido": ("híbrido", "hibrido", "hybrid", "mixed"),
    "presencial": ("presencial", "oficina", "onsite"),
}

_PE_REGION_HINTS = (
    "lima",
    "arequipa",
    "cusco",
    "trujillo",
    "chiclayo",
    "piura",
    "iquitos",
    "huancayo",
    "tacna",
    "perú",
    "peru",
    "remoto",
)

EMPTY_COACH: dict[str, Any] = {
    "name": "",
    "location": "",
    "languages": [],
    "education": [],
    "skills": [],
    "soft_skills": [],
    "experience": [],
    "projects": [],
    "certifications": [],
    "goals": [],
    "preferred_roles": [],
    "job_type": [],
    "work_modality": "",
    "salary_expectation": "",
    "availability": "",
    "seniority": "",
    "areas": [],
    "links": [],
}


def _ensure_coach(coach: dict[str, Any]) -> dict[str, Any]:
    out = {**EMPTY_COACH, **{k: v for k, v in coach.items() if k in EMPTY_COACH}}
    for list_key in ("languages", "education", "skills", "soft_skills", "experience", "projects", "certifications", "goals", "preferred_roles", "job_type", "areas", "links"):
        if not isinstance(out.get(list_key), list):
            out[list_key] = []
    return out


def _append_unique(seq: list[str], val: str, cap: int = 40) -> None:
    v = (val or "").strip()
    if not v or len(v) > 200:
        return
    low = v.lower()
    if any(x.lower() == low for x in seq):
        return
    if len(seq) >= cap:
        return
    seq.append(v)


def detect_focus_tags(text: str) -> set[str]:
    t = (text or "").lower()
    found: set[str] = set()
    for group in _FOCUS_KEYWORDS:
        if any(k in t for k in group):
            found.add(group[0])
    if "práctica" in t or "practica" in t:
        found.add("práctica")
    return found


def map_focus_to_db_goal(tags: set[str]) -> str:
    """Un solo valor legacy para `UserProfile.goal` (empleo|curso|voluntariado)."""
    if "curso" in tags or "práctica" in tags:
        if "empleo" in tags or "freelance" in tags:
            return "empleo"
        return "curso"
    if "voluntariado" in tags:
        return "voluntariado"
    if tags & {"empleo", "freelance", "investiga", "empre", "hackathon", "mentor"}:
        return "empleo"
    return "empleo"


def focus_tags_to_interests_line(tags: set[str], extra: str = "") -> str:
    parts = sorted(tags)
    if extra:
        parts.append(extra.strip())
    return ", ".join(p for p in parts if p)[:1500]


def heuristic_ingest(u: UserProfile, text: str, coach: dict[str, Any]) -> dict[str, Any]:
    """Fusiona texto libre en coach + campos ORM si siguen vacíos."""
    c = _ensure_coach(coach)
    low = text.lower().strip()

    links = URL_RE.findall(text or "")
    for ln in links[:8]:
        _append_unique(c["links"], ln, cap=12)

    tags = detect_focus_tags(text)
    for tag in sorted(tags):
        _append_unique(c["job_type"], tag)

    for mod, keys in _MODAL_KEYWORDS.items():
        if any(k in low for k in keys):
            if not c["work_modality"]:
                c["work_modality"] = mod
            break

    if any(w in low for w in _PE_REGION_HINTS) and len(text.strip()) <= 160:
        if not (u.region or "").strip():
            u.region = text.strip()[:120]
        elif not c["location"]:
            c["location"] = text.strip()[:160]

    m = re.search(r"(?:nombre|me llamo|soy)\s*[:\s]+([^\n,.]{2,48})", text, re.I)
    if m and not c["name"] and not (u.display_name or "").strip():
        c["name"] = m.group(1).strip()
        if not u.display_name:
            u.display_name = c["name"][:250]

    if re.search(r"(?:\$|usd|soles|salario)", low):
        c["salary_expectation"] = (c["salary_expectation"] or text[:240]).strip()[:240]

    # Skills sueltas (coma separada corta)
    if "," in text and len(text) < 400 and any(k in low for k in ("skill", "stack", "herramienta", "tecnología", "excel", "python")):
        for chunk in re.split(r"[,;/]", text):
            _append_unique(c["skills"], chunk.strip(), cap=30)

    return c


def apply_coach_patch(base: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    if not patch:
        return _ensure_coach(base)
    c = _ensure_coach(base)
    for key, val in patch.items():
        if key not in EMPTY_COACH:
            continue
        if isinstance(EMPTY_COACH[key], list) and isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    _append_unique(c[key], item)
        elif isinstance(EMPTY_COACH[key], str) and isinstance(val, str) and val.strip():
            if not (c.get(key) or "").strip():
                c[key] = val.strip()[:2000]
    return c


def merge_cv_json_into_coach(coach: dict[str, Any], cv: dict[str, Any]) -> dict[str, Any]:
    """Pasa skills/idiomas/experiencia del JSON de CV al coach estructurado."""
    c = _ensure_coach(coach)
    for s in cv.get("skills") or []:
        if isinstance(s, str):
            _append_unique(c["skills"], s, cap=40)
    for lang in cv.get("languages") or []:
        if isinstance(lang, str):
            _append_unique(c["languages"], lang, cap=15)
    for line in (cv.get("experience") or [])[:8]:
        if isinstance(line, str):
            _append_unique(c["experience"], line[:240], cap=15)
    for line in (cv.get("education") or [])[:8]:
        if isinstance(line, str):
            _append_unique(c["education"], line[:240], cap=15)
    if cv.get("email") and not c["links"]:
        _append_unique(c["links"], f"email:{cv['email']}", cap=12)
    return c


def coach_to_search_blob(coach: dict[str, Any]) -> str:
    c = _ensure_coach(coach)
    parts: list[str] = []
    for k in ("location", "work_modality", "seniority", "availability", "salary_expectation"):
        if c.get(k):
            parts.append(str(c[k]))
    for lst in ("languages", "education", "skills", "soft_skills", "goals", "preferred_roles", "job_type", "areas"):
        parts.extend(str(x) for x in c.get(lst, []) if x)
    return " ".join(parts)
