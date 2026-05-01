from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.evolution_client import evolution_remote_jid_from_key, evolution_webhook_entries

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InboundDocument:
    remote_jid: str
    from_me: bool
    stanza_id: str | None
    filename: str
    mimetype: str
    message_entry: dict[str, Any]


def _document_leaf(message: dict[str, Any]) -> dict[str, Any] | None:
    """
    Obtiene documentMessage dentro de variantes típicas (Baileys/Evolution).
    """
    dm = message.get("documentMessage")
    if isinstance(dm, dict):
        return dm
    dwc = message.get("documentWithCaptionMessage")
    if isinstance(dwc, dict):
        nested = dwc.get("message")
        if isinstance(nested, dict):
            dm2 = nested.get("documentMessage")
            if isinstance(dm2, dict):
                return dm2
    return None


def extract_inbound_document(payload: dict[str, Any]) -> InboundDocument | None:
    entries = evolution_webhook_entries(payload)

    for entry in entries:
        key = entry.get("key") or {}
        if not isinstance(key, dict):
            continue
        remote_jid = evolution_remote_jid_from_key(key)
        if not remote_jid:
            continue
        from_me = bool(key.get("fromMe"))
        kid = key.get("id")
        stanza_id = kid.strip() if isinstance(kid, str) else None
        message = entry.get("message") or {}
        if not isinstance(message, dict):
            continue

        doc_payload = _document_leaf(message)
        if isinstance(doc_payload, dict):
            filename = str(doc_payload.get("fileName") or "cv").strip()
            mimetype = str(doc_payload.get("mimetype") or "").strip()
            return InboundDocument(
                remote_jid=remote_jid,
                from_me=from_me,
                stanza_id=stanza_id,
                filename=filename,
                mimetype=mimetype,
                message_entry=entry,
            )

    return None


def _decode_data_uri_or_b64(s: str) -> bytes:
    raw = (s or "").strip()
    if raw.startswith("data:"):
        b64 = raw.split(",", 1)[1] if "," in raw else ""
        return base64.b64decode(b64)
    return base64.b64decode(raw)


async def get_media_base64(settings: Settings, payload: dict[str, Any]) -> tuple[str, str, bytes] | None:
    """
    Uses Evolution API endpoint:
      POST /chat/getBase64FromMediaMessage/{instance}

    Body must include the inbound webhook message object (docs call it `message`).
    """
    doc = extract_inbound_document(payload)
    if not doc:
        return None
    if not settings.evolution_instance or not settings.evolution_api_key:
        return None

    url = (
        f"{settings.evolution_base_url.rstrip('/')}"
        f"/chat/getBase64FromMediaMessage/{settings.evolution_instance}"
    )
    headers = {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}
    body = {"message": doc.message_entry, "convertToMp4": False}

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            logger.warning("getBase64FromMediaMessage %s: %s", r.status_code, r.text[:2000])
            return None
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return None

    # Best-effort parsing: different deployments wrap data differently.
    b64 = None
    if isinstance(data, dict):
        for k in ("base64", "b64", "data"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                b64 = v.strip()
                break
        # Some servers respond with nested { response: { base64, mimetype, fileName } }
        resp = data.get("response")
        if not b64 and isinstance(resp, dict):
            v = resp.get("base64")
            if isinstance(v, str) and v.strip():
                b64 = v.strip()
            if not doc.mimetype and isinstance(resp.get("mimetype"), str):
                doc = InboundDocument(
                    remote_jid=doc.remote_jid,
                    from_me=doc.from_me,
                    stanza_id=doc.stanza_id,
                    filename=str(resp.get("fileName") or doc.filename),
                    mimetype=str(resp.get("mimetype") or doc.mimetype),
                    message_entry=doc.message_entry,
                )
    if not b64:
        return None

    try:
        blob = _decode_data_uri_or_b64(b64)
    except Exception:  # noqa: BLE001
        return None
    return doc.filename, doc.mimetype, blob

