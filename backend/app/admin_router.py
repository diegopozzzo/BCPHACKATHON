from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.collectors.collect import collect_all
from app.db import get_db
from app.models import FlowEvent, Opportunity, UserProfile

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _require_admin(
    settings: Settings,
    x_admin_token: str | None,
    token: str | None,
) -> None:
    expected = (settings.admin_dashboard_token or "").strip()
    if not expected:
        return
    got = (x_admin_token or token or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Token admin inválido o faltante")


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Query(None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _require_admin(settings, x_admin_token, token)
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "admin_token": token or x_admin_token or ""},
    )


@router.get("/api/snapshot")
def admin_snapshot(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Query(None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    _require_admin(settings, x_admin_token, token)
    profiles = list(db.scalars(select(UserProfile).order_by(UserProfile.id.desc()).limit(40)).all())
    # Hide sources we intentionally removed + demo seeds.
    opps = list(
        db.scalars(
            select(Opportunity)
            .where(~Opportunity.url.contains("indeed.com"))
            .where(~Opportunity.url.contains("example.com"))
            .order_by(Opportunity.id.desc())
            .limit(50)
        ).all()
    )
    flows = list(db.scalars(select(FlowEvent).order_by(FlowEvent.id.desc()).limit(100)).all())

    def p_row(u: UserProfile) -> dict[str, Any]:
        return {
            "wa_id": u.wa_id,
            "role": u.role or "—",
            "state": u.conversation_state,
            "region": u.region,
            "goal": u.goal,
            "company": u.company_name,
            "skills": (u.skills or "")[:120],
            "updated": u.updated_at.isoformat() if u.updated_at else None,
        }

    def o_row(o: Opportunity) -> dict[str, Any]:
        return {
            "id": o.id,
            "title": o.title,
            "type": o.type,
            "organization": o.organization,
            "region": o.region,
            "requirements": (o.requirements or "")[:800],
            "url": o.url,
            "employer_wa": o.employer_wa_id,
            "active": o.active,
            "created": o.created_at.isoformat() if o.created_at else None,
        }

    def f_row(f: FlowEvent) -> dict[str, Any]:
        return {
            "wa_id": f.wa_id,
            "state": f.state,
            "role": f.role,
            "snippet": (f.message_snippet or "")[:160],
            "at": f.created_at.isoformat() if f.created_at else None,
        }

    opp_total = int(db.scalar(select(func.count()).select_from(Opportunity)) or 0)
    prof_total = int(db.scalar(select(func.count()).select_from(UserProfile)) or 0)
    flow_total = int(db.scalar(select(func.count()).select_from(FlowEvent)) or 0)
    return {
        "profiles": [p_row(u) for u in profiles],
        "opportunities": [o_row(o) for o in opps],
        "flow": [f_row(f) for f in flows],
        "counts": {
            "profiles_total": prof_total,
            "opportunities_total": opp_total,
            "flow_events_total": flow_total,
        },
    }


@router.post("/api/refresh-opportunities")
def refresh_opportunities(
    q: str = Query("python"),
    limit: int = Query(60, ge=1, le=300),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Query(None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    _require_admin(settings, x_admin_token, token)
    # Hard-disable sources we do not support anymore (and that look bad in UI).
    db.query(Opportunity).filter(Opportunity.url.contains("indeed.com")).update(
        {"active": False},
        synchronize_session=False,
    )
    # Disable initial demo rows.
    db.query(Opportunity).filter(Opportunity.url.contains("example.com")).update(
        {"active": False},
        synchronize_session=False,
    )
    db.commit()

    items = collect_all(query=q, limit_total=limit)

    existing_urls = {u for (u,) in db.query(Opportunity.url).all() if isinstance(u, str)}
    inserted = 0
    for it in items:
        if it.url in existing_urls:
            continue
        db.add(
            Opportunity(
                title=it.title,
                type=it.type,
                organization=it.organization,
                region=it.region,
                requirements=it.requirements,
                url=it.url,
                active=True,
            )
        )
        inserted += 1
    if inserted:
        db.commit()

    return {"fetched": len(items), "inserted": inserted}
