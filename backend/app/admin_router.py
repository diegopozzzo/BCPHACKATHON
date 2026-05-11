from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.collectors.collect import collect_all, collect_from_seed_url
from app.collectors.managed_urls import is_managed_scrape_url, validate_normalized
from app.db import get_db
from app.models import FlowEvent, Opportunity, UserProfile
from app.profile_merge import merge_any_phone_duplicates, merge_lid_profiles_into_canonical

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


class ScrapeSeedBody(BaseModel):
    """URL semilla + palabra clave (si la home no trae listado, se usa en pe.computrabajo.com)."""

    url: str = Field(min_length=10, max_length=2048)
    query: str = Field(default="python", max_length=160)
    limit: int = Field(default=100, ge=1, le=300)


def _ingest_scraped_opportunities(db: Session, items: list[Any]) -> dict[str, Any]:
    """Valida, inserta/actualiza y desactiva filas gestionadas igual que el sync global."""
    db.execute(update(Opportunity).where(Opportunity.url.contains("indeed.com")).values(active=False))
    db.execute(update(Opportunity).where(Opportunity.url.contains("example.com")).values(active=False))
    db.commit()

    valid_items: list[Any] = []
    skipped_invalid = 0
    skip_reasons: dict[str, int] = {}
    for it in items:
        ok, err = validate_normalized(it)
        if not ok:
            skipped_invalid += 1
            skip_reasons[err] = skip_reasons.get(err, 0) + 1
            continue
        valid_items.append(it)

    fresh_by_url = {it.url: it for it in valid_items}
    fresh_urls = set(fresh_by_url.keys())

    all_managed = db.scalars(select(Opportunity).where(Opportunity.employer_wa_id.is_(None))).all()

    deactivated = 0
    updated = 0
    revived = 0
    for o in all_managed:
        if not is_managed_scrape_url(o.url):
            continue
        if o.url in fresh_by_url:
            it = fresh_by_url[o.url]
            nt = (it.title or "")[:255]
            no = (it.organization or "")[:255]
            nr = (it.region or "")[:128]
            nreq = it.requirements or ""
            ntyp = (it.type or "empleo")[:32]
            changed = (
                (o.title or "").strip() != nt.strip()
                or (o.organization or "").strip() != no.strip()
                or (o.region or "").strip() != nr.strip()
                or (o.requirements or "").strip() != nreq.strip()
                or (o.type or "").strip() != ntyp.strip()
            )
            if changed:
                o.title = nt
                o.organization = no
                o.region = nr
                o.requirements = nreq
                o.type = ntyp
                updated += 1
            if not o.active:
                revived += 1
            o.active = True
            continue
        if o.active:
            o.active = False
            deactivated += 1

    existing_urls = set(db.scalars(select(Opportunity.url)).all())
    inserted = 0
    for it in valid_items:
        if it.url in existing_urls:
            continue
        db.add(
            Opportunity(
                title=(it.title or "")[:255],
                type=(it.type or "empleo")[:32],
                organization=(it.organization or "")[:255],
                region=(it.region or "")[:128],
                requirements=it.requirements or "",
                url=it.url[:512],
                active=True,
            )
        )
        inserted += 1
        existing_urls.add(it.url)

    db.commit()

    return {
        "fetched": len(items),
        "validated_ok": len(valid_items),
        "skipped_invalid": skipped_invalid,
        "skip_reasons": skip_reasons,
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
        "revived": revived,
        "fresh_urls": len(fresh_urls),
    }


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
        request,
        "admin.html",
        {"admin_token": token or x_admin_token or ""},
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
            .where(Opportunity.active.is_(True))
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


@router.post("/api/merge-wa-profiles")
def merge_wa_profiles(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Query(None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Fusiona perfiles @lid y duplicados por mismo número (mismo usuario)."""
    _require_admin(settings, x_admin_token, token)
    a = merge_any_phone_duplicates(db)
    b = merge_lid_profiles_into_canonical(db, settings)
    return {"phone_dupes": a, "lid_merge": b}


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
    items = collect_all(query=q, limit_total=limit)
    return _ingest_scraped_opportunities(db, items)


@router.post("/api/scrape-seed")
def scrape_seed_url(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Query(None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    body: ScrapeSeedBody = Body(...),
) -> dict[str, Any]:
    """
    Importación desde una URL (p. ej. portal Computrabajo): Scrapling + mismas reglas de validación
    y merge que «Actualizar oportunidades». Otros dominios se pueden ir añadiendo por host.
    """
    _require_admin(settings, x_admin_token, token)
    try:
        items = collect_from_seed_url(
            seed_url=body.url.strip(),
            query=body.query.strip(),
            limit=int(body.limit),
        )
    except ValueError as e:
        if str(e) == "unsupported_seed_host":
            raise HTTPException(
                status_code=422,
                detail="Dominio no soportado para importación por URL. Hoy: computrabajo.com (se usa listado PE + tu palabra clave si la URL es la home).",
            ) from e
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _ingest_scraped_opportunities(db, items)
