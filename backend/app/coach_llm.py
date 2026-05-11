"""Refuerzo con LLM: extrae patch JSON + reply conversacional (máx. 2 preguntas)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from openai import AsyncOpenAI

from app.coach_intake import EMPTY_COACH, apply_coach_patch
from app.config import Settings

logger = logging.getLogger(__name__)

_ALLOWED_PATCH_KEYS = frozenset(EMPTY_COACH.keys())

CoachReplyStyle = Literal["conversational", "funnel_ack"]

_FUNNEL_MAX_CHARS = 100
_FUNNEL_MAX_TOKENS = 200
_FUNNEL_TEMPERATURE = 0.22
_CONV_MAX_TOKENS = 420
_CONV_TEMPERATURE = 0.4

# Si el modelo anticipa el siguiente paso del formulario (CV, opciones 1/2…), tiramos el reply.
_FUNNEL_REPLY_CONTAMINATION = re.compile(
    r"(cv\b|pdf|docx|documento|adjunt|mand[ao]|"
    r"opción\s*[12]|respond[ée]\s*[12]|/oportunidades|whatsapp)",
    re.IGNORECASE,
)


def _count_question_marks(s: str) -> int:
    return (s or "").count("?")


def sanitize_coach_funnel_reply(reply: str | None) -> str | None:
    """
    Reply corto para pasos de embudo: sin preguntas, sin duplicar el guion del bot.
    Devuelve None si no sirve (el caller usa solo el texto fijo del flujo).
    """
    if not reply or not (reply := reply.strip()):
        return None
    line = reply.split("\n", 1)[0].strip()
    if not line:
        return None
    if "?" in line or "？" in line:
        return None
    if _FUNNEL_REPLY_CONTAMINATION.search(line):
        return None
    if len(line) > _FUNNEL_MAX_CHARS:
        cut = line[: _FUNNEL_MAX_CHARS - 1].rsplit(" ", 1)[0]
        line = (cut or line[: _FUNNEL_MAX_CHARS - 1]) + "…"
    return line


def _client(settings: Settings) -> AsyncOpenAI | None:
    if not settings.openai_api_key:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_sec,
        max_retries=settings.openai_max_retries,
    )


def _coach_snapshot_serializable(coach: dict[str, Any]) -> dict[str, Any]:
    return {k: coach.get(k, EMPTY_COACH[k]) for k in EMPTY_COACH}


async def coach_increment(
    settings: Settings,
    coach: dict[str, Any],
    user_message: str,
    *,
    context_hint: str = "",
    reply_style: CoachReplyStyle = "conversational",
) -> tuple[dict[str, Any], str | None]:
    """
    Devuelve (coach_actualizado, reply opcional).
    Si no hay API key o falla el modelo, coach sin cambios y reply None.

    reply_style:
    - conversational: mensaje natural (p.ej. /actualizar o usuario en ready).
    - funnel_ack: solo reconocimiento brevísimo + patch; el bot ya envía la pregunta del paso
      (evita muchas líneas, preguntas duplicadas y “siguiente paso” alucinado).
    """
    client = _client(settings)
    if not client or not (user_message or "").strip():
        return coach, None

    funnel = reply_style == "funnel_ack"
    system_core = (
        "Eres coach laboral en Perú por WhatsApp. Personalidad: profesional, cercana, clara, "
        "conversacional, eficiente; no robótica ni repetitiva; no excesivamente formal.\n"
    )
    if funnel:
        system_rules = (
            "MODO EMBUDO (REPLY_STYLE=funnel_ack):\n"
            "- El producto ya muestra la pregunta oficial del paso; NO la repitas, NO la anticipes.\n"
            "- En \"reply\" pon cadena vacía \"\" si no aporta nada, O UNA sola oración corta (máx ~90 caracteres) "
            "que reconozca lo dicho (sin listas, sin viñetas, sin URLs).\n"
            "- PROHIBIDO en reply: signos de interrogación, pedir CV/PDF/documento, mencionar opciones 1/2 del menú, "
            "mencionar /oportunidades o pasos futuros del formulario.\n"
            "- patch: solo datos que el usuario dijo con claridad en USER_MESSAGE; si no hay, patch: {}.\n"
        )
    else:
        system_rules = (
            "REGLAS ESTRICTAS (modo conversacional):\n"
            "- Tu reply debe tener como máximo 2 signos de interrogación en total (máximo 2 preguntas).\n"
            "- No pidas de nuevo datos que ya aparecen en CURRENT_PROFILE (no repitas).\n"
            "- Mensajes cortos, estilo WhatsApp (2–4 líneas salvo listas mínimas).\n"
            "- Puedes usar un emoji suave como 👌 si encaja; no abuses.\n"
        )
    json_rules = (
        "Devuelve SOLO un JSON con forma exacta: "
        '{"reply":"texto al usuario","patch":{...}}. '
        "patch es opcional; solo incluye claves del perfil que el usuario aportó o infirió con base clara. "
        f"Claves permitidas en patch: {sorted(_ALLOWED_PATCH_KEYS)}. "
        "Para listas (skills, goals, job_type, etc.) usa arrays de strings cortos. "
        "Si no necesitas patch, usa patch: {}."
    )
    system = system_core + system_rules + json_rules
    user_payload: dict[str, Any] = {
        "CURRENT_PROFILE": _coach_snapshot_serializable(coach),
        "USER_MESSAGE": user_message.strip()[:2500],
        "CONTEXT": context_hint or "",
        "REPLY_STYLE": reply_style,
    }
    max_tokens = _FUNNEL_MAX_TOKENS if funnel else _CONV_MAX_TOKENS
    temperature = _FUNNEL_TEMPERATURE if funnel else _CONV_TEMPERATURE
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.exception("coach_llm: %s", exc)
        return coach, None

    reply_raw = data.get("reply")
    reply = (reply_raw.strip() if isinstance(reply_raw, str) else "") or None
    patch = data.get("patch")
    if not isinstance(patch, dict):
        patch = {}
    patch = {k: v for k, v in patch.items() if k in _ALLOWED_PATCH_KEYS}
    merged = apply_coach_patch(coach, patch)

    if funnel:
        reply = sanitize_coach_funnel_reply(reply)

    if reply and _count_question_marks(reply) > 2:
        out_chars: list[str] = []
        q_seen = 0
        for ch in reply:
            if ch == "?":
                q_seen += 1
                out_chars.append("?" if q_seen <= 2 else ".")
            else:
                out_chars.append(ch)
        reply = "".join(out_chars).strip()

    return merged, reply
