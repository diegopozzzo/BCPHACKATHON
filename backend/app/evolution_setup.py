from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_CONNECT_FAILURE_CHECKS: list[str] = [
    "En la raíz del repo (donde está docker-compose.yml): ejecuta docker compose up -d y espera ~30–60 s.",
    "Comprueba que el puerto 8080 esté libre y que el contenedor bcp_evolution_api esté arriba (docker ps).",
    "EVOLUTION_API_URL en el .env del backend debe apuntar al mismo host/puerto que escucha Evolution (p. ej. http://localhost:8080).",
    "EVOLUTION_API_KEY debe coincidir exactamente con AUTHENTICATION_API_KEY del stack Docker (ver docker-compose.yml).",
    "EVOLUTION_INSTANCE debe ser el nombre de una instancia ya creada en Evolution; si no existe, créala en el manager o con POST /instance/create.",
    "En Windows, si falla solo con localhost, prueba EVOLUTION_API_URL=http://127.0.0.1:8080 y reinicia uvicorn.",
]


def _connection_failure_payload(url: str, base: str, technical: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            "No hay conexión al servidor Evolution en "
            f"{base}\n\n"
            "Eso suele significar que Evolution no está levantado en ese puerto, o la URL no coincide.\n\n"
            f"Detalle técnico: {technical}"
        ),
        "url": url,
        "evolution_api_url": base,
        "checks": _CONNECT_FAILURE_CHECKS,
    }


async def evolution_service_reachable(base: str) -> tuple[bool, int | None, str | None]:
    """True si hay respuesta HTTP (cualquier código); False si falla TCP/TLS."""
    b = (base or "").strip().rstrip("/")
    if not b:
        return False, None, "URL vacía"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(b, follow_redirects=True)
        return True, r.status_code, None
    except httpx.RequestError as e:
        return False, None, f"{type(e).__name__}: {e}"


def _format_evolution_error(root: dict[str, Any], http_status: int) -> str:
    """Evolution suele mandar el detalle en response.message (lista), no en error='Not Found'."""
    parts: list[str] = []
    rsp = root.get("response")
    if isinstance(rsp, dict):
        m = rsp.get("message")
        if isinstance(m, list):
            parts.extend(str(x) for x in m if x is not None)
        elif isinstance(m, str) and m.strip():
            parts.append(m)
    if not parts:
        m = root.get("message")
        if isinstance(m, list):
            parts.extend(str(x) for x in m if x is not None)
        elif isinstance(m, str) and m.strip():
            parts.append(m)
    if parts:
        return "[Evolution HTTP " + str(http_status) + "] " + " ".join(parts)
    err = root.get("error")
    if isinstance(err, str) and err.strip():
        return f"[Evolution HTTP {http_status}] {err}"
    return f"Evolution respondió HTTP {http_status}"


def _normalize_connect_body(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, list):
        if not data:
            return {}
        data = data[0]
    if not isinstance(data, dict):
        return {}
    if data.get("base64"):
        return data
    qr = data.get("qrcode")
    if isinstance(qr, dict):
        if qr.get("base64"):
            return {**data, **qr}
        # A veces el QR viene anidado solo con code/count en el mismo objeto
        merged = {**data, **qr}
        if merged.get("base64") or merged.get("code"):
            return merged
    return data


def _extract_state(normalized: dict[str, Any]) -> str | None:
    st = normalized.get("state") or normalized.get("status")
    if isinstance(st, dict):
        st = st.get("state") or st.get("status")
    if isinstance(st, str):
        return st
    cs = normalized.get("connectionStatus")
    if isinstance(cs, dict):
        s = cs.get("state") or cs.get("status")
        if isinstance(s, str):
            return s
    return None


async def fetch_evolution_connect(settings: Settings) -> dict[str, Any]:
    """
    Llama a Evolution GET /instance/connect/{instance} (documentación v2).
    Devuelve JSON listo para el front (base64 del QR si existe).
    """
    base = (settings.evolution_base_url or "").strip().rstrip("/")
    inst = (settings.evolution_instance or "").strip()
    key = (settings.evolution_api_key or "").strip()
    if not base or not inst or not key:
        return {
            "ok": False,
            "error": "Falta EVOLUTION_API_URL, EVOLUTION_INSTANCE o EVOLUTION_API_KEY en .env",
            "instance": inst or None,
        }
    url = f"{base}/instance/connect/{inst}"
    data: Any = {}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(url, headers={"apikey": key})
            try:
                data = r.json()
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "error": f"Evolution devolvió no-JSON (HTTP {r.status_code})",
                    "http_status": r.status_code,
                    "raw_preview": (r.text or "")[:200],
                }
            # v2.1.1 a veces tarda en emitir el QR; un segundo intento reduce {"count":0} vacío.
            if (
                r.status_code == 200
                and isinstance(data, dict)
                and not _normalize_connect_body(data).get("base64")
                and _normalize_connect_body(data).get("count") == 0
            ):
                await asyncio.sleep(3.0)
                r = await client.get(url, headers={"apikey": key})
                try:
                    data = r.json()
                except json.JSONDecodeError:
                    return {
                        "ok": False,
                        "error": f"Evolution devolvió no-JSON en reintento (HTTP {r.status_code})",
                        "http_status": r.status_code,
                        "raw_preview": (r.text or "")[:200],
                    }
    except httpx.RequestError as e:
        logger.warning("Evolution connect request failed: %s", e)
        return _connection_failure_payload(url, base, str(e))

    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"Evolution devolvió JSON inesperado (HTTP {r.status_code})",
            "http_status": r.status_code,
        }

    normalized = _normalize_connect_body(data)
    state = _extract_state(normalized)
    out: dict[str, Any] = {
        "ok": 200 <= r.status_code < 300,
        "http_status": r.status_code,
        "instance": inst,
        "state": state,
    }
    if normalized.get("base64"):
        out["base64"] = normalized["base64"]
    if normalized.get("code"):
        out["code"] = normalized["code"]
    if normalized.get("pairingCode"):
        out["pairingCode"] = normalized["pairingCode"]
    if "count" in normalized:
        out["count"] = normalized["count"]

    if r.status_code == 404:
        out["ok"] = False
        out["error"] = _format_evolution_error(data, 404)
        out["checks"] = [
            f"Comprueba que EVOLUTION_INSTANCE en .env sea «{inst}» y coincida con una instancia creada en Evolution.",
            "Si cambiaste el nombre (p. ej. a rutepe), reinicia uvicorn para recargar el .env.",
            "Lista instancias: GET /instance/fetchInstances con header apikey.",
        ]
    elif r.status_code >= 400:
        out["ok"] = False
        out["error"] = _format_evolution_error(data, r.status_code)
    elif not out.get("base64"):
        st_live = (state or "").lower()
        if st_live != "open":
            try:
                async with httpx.AsyncClient(timeout=15.0) as c2:
                    rs = await c2.get(
                        f"{base}/instance/connectionState/{inst}",
                        headers={"apikey": key},
                    )
                    if rs.status_code == 200:
                        sj = rs.json()
                        ob = sj.get("instance") if isinstance(sj, dict) else None
                        if isinstance(ob, dict) and isinstance(ob.get("state"), str):
                            st_live = ob["state"].lower()
                            out["state"] = ob.get("state")
            except httpx.RequestError:
                pass
        if st_live == "open":
            out["message"] = "Instancia ya conectada (state: open). No hace falta QR; WhatsApp ya está vinculado a Evolution."
        else:
            out["message"] = (
                "Sin imagen QR todavía. Reintenta en unos segundos o revisa logs de Evolution "
                f"(estado: {st_live or 'desconocido'})"
            )
            if normalized.get("count") == 0:
                out["checks"] = [
                    "Evolution respondió count: 0 sin imagen. En Docker suele fijarse con CONFIG_SESSION_PHONE_VERSION (ya en docker-compose.yml); recrea el contenedor: docker compose up -d --force-recreate evolution-api.",
                    "Versión actual de WhatsApp Web: https://wppconnect.io/whatsapp-versions/",
                    "Revisa logs: docker logs bcp_evolution_api --tail 80",
                ]
    return out


async def fetch_evolution_status(settings: Settings) -> dict[str, Any]:
    """Diagnóstico: ¿responde la URL base? ¿connectionState de la instancia?"""
    base = (settings.evolution_base_url or "").strip().rstrip("/")
    inst = (settings.evolution_instance or "").strip()
    key = (settings.evolution_api_key or "").strip()
    out: dict[str, Any] = {
        "evolution_api_url": base or None,
        "instance": inst or None,
        "api_key_configured": bool(key),
    }
    if not base:
        out["ok"] = False
        out["error"] = "Falta EVOLUTION_API_URL en .env"
        return out

    ok_tcp, http_status, err = await evolution_service_reachable(base)
    out["base_reachable"] = ok_tcp
    out["base_http_status"] = http_status
    if err:
        out["base_error"] = err
        out["ok"] = False
        out["checks"] = _CONNECT_FAILURE_CHECKS
        return out

    if not key or not inst:
        out["ok"] = False
        out["error"] = "Falta EVOLUTION_API_KEY o EVOLUTION_INSTANCE para consultar la instancia."
        return out

    state_url = f"{base}/instance/connectionState/{inst}"
    connect_url = f"{base}/instance/connect/{inst}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(state_url, headers={"apikey": key})
    except httpx.RequestError as e:
        out["ok"] = False
        out["error"] = f"No se pudo llamar a connectionState: {e!s}"
        out["connection_state_url"] = state_url
        out["checks"] = _CONNECT_FAILURE_CHECKS
        return out

    out["connection_state_http"] = r.status_code
    try:
        out["connection_state_body"] = r.json()
    except json.JSONDecodeError:
        out["connection_state_body"] = (r.text or "")[:500]

    if r.status_code == 401:
        out["ok"] = False
        out["error"] = (
            "Evolution respondió 401 (apikey inválida). "
            "EVOLUTION_API_KEY del backend debe ser exactamente AUTHENTICATION_API_KEY del contenedor."
        )
        return out

    if r.status_code == 404:
        out["ok"] = False
        body = out.get("connection_state_body")
        if isinstance(body, dict):
            out["error"] = _format_evolution_error(body, 404)
        else:
            out["error"] = (
                f"Instancia «{inst}» no encontrada en Evolution (404). "
                "Créala en el manager o con POST /instance/create con el mismo instanceName."
            )
        out["connect_url_hint"] = connect_url
        out["checks"] = [
            f"EVOLUTION_INSTANCE debe existir en Evolution (ahora configurado: «{inst}»).",
            "Reinicia uvicorn tras cambiar .env.",
        ]
        return out

    if r.status_code >= 400:
        out["ok"] = False
        out["error"] = f"Evolution respondió HTTP {r.status_code} en connectionState."
        return out

    out["ok"] = True
    out["connect_url"] = connect_url
    out["hint"] = "Si base_reachable es true pero el QR falla, abre connect_url en la API o usa /setup/evolution-qr."
    return out
