"""
Fusiona perfiles duplicados por JID @lid vs número @s.whatsapp.net (mismo usuario).
Se usa al arrancar y opcionalmente desde /admin.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import FlowEvent, Opportunity, UserProfile
from app.whatsapp_jid import canonical_dm_jid, jid_phone_digits

logger = logging.getLogger(__name__)


def _prefer_state(a: str | None, b: str | None) -> str:
    order = (
        "welcome",
        "await_role",
        "ask_region",
        "ask_focus",
        "ask_has_cv",
        "ask_skills",
        "ask_education",
        "cv_confirm",
        "set_interests",
        "update_profile",
        "company_prompt",
        "emp_company",
        "emp_need",
        "emp_pub_type",
        "emp_pub_title",
        "emp_pub_region",
        "emp_pub_req",
        "emp_pub_url",
        "ready",
    )
    ra = (a or "welcome").lower()
    rb = (b or "welcome").lower()
    ia = order.index(ra) if ra in order else -1
    ib = order.index(rb) if rb in order else -1
    if ib > ia:
        return rb
    return ra


def merge_lid_profiles_into_canonical(db: Session, settings: Settings) -> dict[str, Any]:
    """
    Con un solo número en lista blanca (caso típico), todos los `...@lid` son el mismo usuario:
    fusiona en el perfil canónico `519...@s.whatsapp.net` y borra duplicados.
    """
    allowed = settings.allowed_jid_set
    if len(allowed) != 1:
        return {"merged": 0, "skipped": "lista blanca no tiene exactamente 1 JID"}

    canon = canonical_dm_jid(next(iter(allowed)))
    if not canon.endswith("@s.whatsapp.net"):
        return {"merged": 0, "skipped": "JID canónico no es número"}

    keeper = db.scalar(select(UserProfile).where(UserProfile.wa_id == canon))
    lid_cond = func.lower(UserProfile.wa_id).like("%@lid")
    orphans = list(db.scalars(select(UserProfile).where(lid_cond)).all())
    merged = 0

    if not keeper and orphans:
        primary = max(
            orphans,
            key=lambda u: (
                1 if (u.role or "").strip() else 0,
                len((u.notes or "").strip()),
                len((u.skills or "").strip()),
                u.id or 0,
            ),
        )
        primary.wa_id = canon
        db.flush()
        for o in orphans:
            if o.id != primary.id:
                db.execute(update(FlowEvent).where(FlowEvent.wa_id == o.wa_id).values(wa_id=canon))
                db.execute(
                    update(Opportunity).where(Opportunity.employer_wa_id == o.wa_id).values(employer_wa_id=canon)
                )
                db.delete(o)
                merged += 1
        db.commit()
        logger.info("Perfil @lid unificado en %s (eliminados %s duplicados)", canon, merged)
        return {"merged": merged, "canonical": canon, "promoted_lid_to_canonical": True}

    if not keeper:
        return {"merged": 0, "skipped": "no existe perfil canónico ni @lid"}

    for o in orphans:
        if o.id == keeper.id:
            continue
        if not keeper.role and o.role:
            keeper.role = o.role
        keeper.conversation_state = _prefer_state(keeper.conversation_state, o.conversation_state)
        for attr in (
            "display_name",
            "region",
            "education_level",
            "skills",
            "interests",
            "availability",
            "goal",
            "company_name",
            "hiring_summary",
        ):
            cur = getattr(keeper, attr, None)
            inc = getattr(o, attr, None)
            if (not cur or not str(cur).strip()) and inc and str(inc).strip():
                setattr(keeper, attr, inc)
        if (not keeper.notes or not keeper.notes.strip()) and o.notes and o.notes.strip():
            keeper.notes = o.notes
        db.execute(update(FlowEvent).where(FlowEvent.wa_id == o.wa_id).values(wa_id=canon))
        db.execute(update(Opportunity).where(Opportunity.employer_wa_id == o.wa_id).values(employer_wa_id=canon))
        db.delete(o)
        merged += 1

    if merged:
        db.commit()
        logger.info("Fusionados %s perfiles @lid en %s", merged, canon)
    return {"merged": merged, "canonical": canon}


def merge_any_phone_duplicates(db: Session) -> dict[str, Any]:
    """
    Si hay dos filas cuyo wa_id comparte los mismos dígitos de teléfono (p. ej. c.us vs s.whatsapp.net),
    conserva la que termina en @s.whatsapp.net y borra la otra.
    """
    rows = list(db.scalars(select(UserProfile)).all())
    by_digits: dict[str, list[UserProfile]] = {}
    for u in rows:
        d = jid_phone_digits(u.wa_id)
        if not d:
            continue
        by_digits.setdefault(d, []).append(u)

    merged = 0
    for d, group in by_digits.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: (0 if x.wa_id.endswith("@s.whatsapp.net") else 1, x.id))
        keeper = group[0]
        for o in group[1:]:
            if o.id == keeper.id:
                continue
            if not keeper.role and o.role:
                keeper.role = o.role
            keeper.conversation_state = _prefer_state(keeper.conversation_state, o.conversation_state)
            for attr in (
                "display_name",
                "region",
                "education_level",
                "skills",
                "interests",
                "availability",
                "goal",
                "company_name",
                "hiring_summary",
            ):
                cur = getattr(keeper, attr, None)
                inc = getattr(o, attr, None)
                if (not cur or not str(cur).strip()) and inc and str(inc).strip():
                    setattr(keeper, attr, inc)
            if (not keeper.notes or not keeper.notes.strip()) and o.notes and o.notes.strip():
                keeper.notes = o.notes
            db.execute(update(FlowEvent).where(FlowEvent.wa_id == o.wa_id).values(wa_id=keeper.wa_id))
            db.execute(
                update(Opportunity).where(Opportunity.employer_wa_id == o.wa_id).values(employer_wa_id=keeper.wa_id)
            )
            db.delete(o)
            merged += 1

    if merged:
        db.commit()
        logger.info("Fusionados %s perfiles duplicados por mismo número", merged)
    return {"merged": merged}
