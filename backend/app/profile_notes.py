"""Estructura unificada de `UserProfile.notes`: cv, perfil coach, borrador empleador (pub)."""

from __future__ import annotations

import json
from typing import Any

from app.models import UserProfile


def empty_v2_blob() -> dict[str, Any]:
    return {"v": 2, "cv": {}, "coach": {}, "pub": None}


def parse_notes_blob(raw: str | None) -> dict[str, Any]:
    if not (raw or "").strip():
        return empty_v2_blob()
    try:
        d = json.loads(raw or "{}")
    except Exception:
        return empty_v2_blob()
    if not isinstance(d, dict):
        return empty_v2_blob()
    if isinstance(d, dict) and "pub_type" in d and "cv_file" not in d:
        return {"v": 2, "cv": {}, "coach": {}, "pub": dict(d)}
    if d.get("v") == 2:
        out = empty_v2_blob()
        out["cv"] = d.get("cv") if isinstance(d.get("cv"), dict) else {}
        out["coach"] = d.get("coach") if isinstance(d.get("coach"), dict) else {}
        pub = d.get("pub")
        out["pub"] = pub if isinstance(pub, dict) else None
        return out
    # Legacy CV payload
    if "cv_file" in d or "skills" in d and isinstance(d.get("skills"), list):
        return {"v": 2, "cv": d, "coach": {}, "pub": None}
    # Legacy employer draft (solo claves tipo pub_*)
    if any(k.startswith("pub_") or k in ("title", "region", "requirements", "url", "draft") for k in d):
        return {"v": 2, "cv": {}, "coach": {}, "pub": d}
    return empty_v2_blob()


def serialize_notes_blob(blob: dict[str, Any]) -> str:
    return json.dumps(blob, ensure_ascii=False)


def load_user_notes(u: UserProfile) -> dict[str, Any]:
    return parse_notes_blob(u.notes)


def save_user_notes(u: UserProfile, blob: dict[str, Any]) -> None:
    u.notes = serialize_notes_blob(blob)


def merge_cv_layer(u: UserProfile, cv_payload: dict[str, Any]) -> None:
    blob = load_user_notes(u)
    blob["cv"] = cv_payload
    blob["v"] = 2
    save_user_notes(u, blob)


def get_coach_blob(u: UserProfile) -> dict[str, Any]:
    b = load_user_notes(u)["coach"]
    return b if isinstance(b, dict) else {}


def set_coach_blob(u: UserProfile, coach: dict[str, Any]) -> None:
    blob = load_user_notes(u)
    blob["coach"] = coach
    blob["v"] = 2
    save_user_notes(u, blob)


def get_pub_blob(u: UserProfile) -> dict[str, Any] | None:
    blob = load_user_notes(u)
    p = blob.get("pub")
    return p if isinstance(p, dict) else None


def set_pub_blob(u: UserProfile, pub: dict[str, Any] | None) -> None:
    blob = load_user_notes(u)
    blob["pub"] = pub
    blob["v"] = 2
    save_user_notes(u, blob)
