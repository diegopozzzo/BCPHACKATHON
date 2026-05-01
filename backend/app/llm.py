import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.models import Opportunity, UserProfile

logger = logging.getLogger(__name__)


def _client(settings: Settings) -> AsyncOpenAI | None:
    if not settings.openai_api_key:
        return None
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def narrate_matches(
    settings: Settings,
    profile: UserProfile,
    ranked: list[tuple[float, str, Opportunity]],
) -> str | None:
    """Genera texto breve y motivador para el top de oportunidades."""
    client = _client(settings)
    if not client or not ranked:
        return None
    lines = []
    for score, reason, o in ranked[:5]:
        lines.append(
            {
                "title": o.title,
                "type": o.type,
                "org": o.organization,
                "region": o.region,
                "score": score,
                "reason": reason,
                "url": o.url,
            }
        )
    system = (
        "Eres un coach laboral juvenil en Perú. Explica en 2-4 oraciones en español "
        "por qué estas oportunidades encajan (o son un buen siguiente paso). Sé concreto, "
        "cálido y sin prometer empleo. Menciona máximo 3 ítems con nombre corto."
    )
    user = json.dumps(
        {"perfil": profile.skills or "", "meta": profile.goal or "", "matches": lines},
        ensure_ascii=False,
    )
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=280,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI narrate_matches: %s", exc)
        return None


async def narrate_company_results(
    settings: Settings,
    prompt: str,
    results: list[dict[str, Any]],
) -> str | None:
    client = _client(settings)
    if not client:
        return None
    system = (
        "Eres reclutador junior. Resume en 2-3 oraciones en español por qué estos perfiles "
        "responden al prompt del cliente. Sin sesgos protegidos; solo competencias declaradas."
    )
    user = json.dumps({"prompt": prompt, "candidatos": results}, ensure_ascii=False)
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.5,
            max_tokens=220,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI narrate_company_results: %s", exc)
        return None
