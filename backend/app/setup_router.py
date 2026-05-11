from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.evolution_setup import (
    fetch_evolution_connect,
    fetch_evolution_logout,
    fetch_evolution_restart,
    fetch_evolution_status,
)

router = APIRouter(tags=["setup"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _require_setup_token(
    settings: Settings,
    x_admin_token: str | None,
    token: str | None,
) -> None:
    expected = (settings.admin_dashboard_token or "").strip()
    if not expected:
        return
    got = (x_admin_token or token or "").strip()
    if got != expected:
        raise HTTPException(
            status_code=401,
            detail="Token inválido o faltante (mismo ADMIN_DASHBOARD_TOKEN que /admin)",
        )


@router.get("/setup/evolution-qr", response_class=HTMLResponse)
async def evolution_qr_page(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
):
    _require_setup_token(settings, x_admin_token, token)
    return templates.TemplateResponse(request, "evolution_qr.html", {})


@router.get("/setup/evolution-connect")
async def evolution_connect_api(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
):
    _require_setup_token(settings, x_admin_token, token)
    payload = await fetch_evolution_connect(settings)
    return JSONResponse(payload)


@router.get("/setup/evolution-status")
async def evolution_status_api(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
):
    """JSON de diagnóstico: URL base alcanzable, connectionState de la instancia, pistas si falla el QR."""
    _require_setup_token(settings, x_admin_token, token)
    payload = await fetch_evolution_status(settings)
    return JSONResponse(payload)


@router.post("/setup/evolution-disconnect")
async def evolution_disconnect_api(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
):
    """Proxy: cierra sesión WhatsApp en Evolution (logout)."""
    _require_setup_token(settings, x_admin_token, token)
    payload = await fetch_evolution_logout(settings)
    return JSONResponse(payload)


@router.post("/setup/evolution-restart")
async def evolution_restart_api(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
):
    """Proxy: reinicia la instancia en Evolution."""
    _require_setup_token(settings, x_admin_token, token)
    payload = await fetch_evolution_restart(settings)
    return JSONResponse(payload)
