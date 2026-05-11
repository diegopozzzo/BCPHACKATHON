import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.coach_intake import (
    detect_focus_tags,
    focus_tags_to_interests_line,
    heuristic_ingest,
    map_focus_to_db_goal,
    merge_cv_json_into_coach,
)
from app.coach_llm import CoachReplyStyle, coach_increment
from app.config import Settings
from app.cv_extract import extract_cv
from app.cv_parse import parse_cv_bytes
from app.evolution_media import extract_inbound_document, get_media_base64
from app.evolution_client import send_whatsapp_text
from app.flow_log import log_flow
from app.latency_metrics import emit_latency
from app.llm import narrate_company_results, narrate_matches
from app.matching import rank_opportunities, rank_seekers_merged
from app.models import Opportunity, UserProfile
from app.profile_notes import get_coach_blob, load_user_notes, merge_cv_layer, save_user_notes, set_coach_blob
from app.whatsapp_jid import canonical_dm_jid, same_dm_contact

logger = logging.getLogger(__name__)

_COACH_INCREMENT_TIMEOUT_SEC = 14.0


async def _coach_increment_safe(
    settings: Settings,
    coach: dict[str, Any],
    text: str,
    *,
    context_hint: str,
    reply_style: CoachReplyStyle = "conversational",
    timeout_sec: float = _COACH_INCREMENT_TIMEOUT_SEC,
) -> tuple[dict[str, Any], str | None]:
    """Evita que el flujo WhatsApp quede colgado si OpenAI tarda o no responde."""
    if not settings.openai_api_key or not (text or "").strip():
        return coach, None
    try:
        return await asyncio.wait_for(
            coach_increment(
                settings,
                coach,
                text,
                context_hint=context_hint,
                reply_style=reply_style,
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("coach_increment superó %ss; sigo sin reply LLM.", timeout_sec)
        return coach, None


def _is_primarily_region_or_modality_reply(text: str) -> bool:
    """Respuesta corta solo de zona/modalidad (sin foco laboral explícito)."""
    t = (text or "").strip().lower()
    if not t or len(t) > 200:
        return False
    if detect_focus_tags(t):
        return False
    pe = (
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
    )
    if any(w in t for w in pe):
        return True
    mod = ("remoto", "remote", "híbrido", "hibrido", "hybrid", "presencial", "latam", "wfh", "online", "desde casa")
    return any(m in t for m in mod)


# Confirmación tipo "si." / "ok" sin texto largo detrás ("sí pero…" necesita segunda vuelta).
_CV_CONFIRM_CLEAN = re.compile(
    r"^\s*(sí|si|ok|okay|vale|dale|correcto|confirmo|listo|sip|claro)\b([.!?…])?\s*$",
    re.IGNORECASE,
)
_CV_CONFIRM_LOOSE = re.compile(r"^\s*(sí|si)\b", re.IGNORECASE)
_CV_CONFIRM_NO_PREFIX = re.compile(
    r"^\s*(no|nop|nope|incorrecto|mal)\b",
    re.IGNORECASE,
)
_CV_CONFIRM_NEG_HINT = re.compile(
    r"\b(pero|excepto|falta|faltan|correg|incorrect|agrega|añad(?:e|ir)|quita|cambia)\b",
    re.IGNORECASE,
)
_CV_CORR_DETAIL_HINT = re.compile(
    r"\b(skills|skill|idioma|cursos|teléfono|telefono|celular|email|linkedin|github|nombre|error|equivoc)\b",
    re.IGNORECASE,
)

# Opción 1/2 al inicio: evitar "10:30", "1lima", "20 años" como elección de rol.
_ROLE_CHOICE_ONE = re.compile(r"^1(?:$|\s|[).,:/\-])")
_ROLE_CHOICE_TWO = re.compile(r"^2(?:$|\s|[).,:/\-])")

_GREETING_OR_FILLER = re.compile(
    r"^\s*(hol[ao]|hey|buen[oa]s?(\s+(días|tardes|noches))?\s*|qué\s+tal|que\s+tal|"
    r"gracias|chau|bye|ok\.?|o[kk]|listo\.?|dale\.?|vale\.?)\s*$",
    re.IGNORECASE,
)

_CV_AFFIRMATIVE_SEND = re.compile(
    r"^\s*(sí|si|sip|síp|ok|okay|dale|vale|claro|confirmo|ya|listo|"
    r"mándalo|mandalo|acá\s*va|aca\s*va|envío|envio|aquí|aqui)\b",
    re.IGNORECASE,
)

GOAL_ALIASES = {
    "empleo": "empleo",
    "trabajo": "empleo",
    "laboral": "empleo",
    "práctica": "empleo",
    "practica": "empleo",
    "prácticas": "empleo",
    "beca": "empleo",
    "freelance": "empleo",
    "freelancer": "empleo",
    "consultor": "empleo",
    "investigación": "empleo",
    "investigacion": "empleo",
    "emprend": "empleo",
    "startup": "empleo",
    "hackathon": "empleo",
    "networking": "empleo",
    "mentor": "empleo",
    "curso": "curso",
    "estudiar": "curso",
    "capacitacion": "curso",
    "certificación": "curso",
    "voluntariado": "voluntariado",
    "voluntario": "voluntariado",
    "ong": "voluntariado",
}

EDU_ALIASES = {
    "secundaria": "secundaria",
    "secundario": "secundaria",
    "tecnico": "tecnico",
    "técnico": "tecnico",
    "instituto": "tecnico",
    "universitario": "universitario",
    "universidad": "universitario",
    "egresado": "universitario",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _touch(u: UserProfile) -> None:
    u.updated_at = _utcnow()


def _get_user(db: Session, wa_id: str) -> UserProfile:
    jid = canonical_dm_jid(wa_id)
    u = db.scalar(select(UserProfile).where(UserProfile.wa_id == jid))
    if u:
        return u
    u = UserProfile(wa_id=jid, conversation_state="welcome", role="")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _parse_role_choice(text: str) -> str | None:
    t = text.strip().lower()
    if _ROLE_CHOICE_ONE.match(t) or t in ("trabajo", "busco trabajo", "busco empleo", "postulante"):
        return "job_seeker"
    if _ROLE_CHOICE_TWO.match(t) or t in ("empleador", "empresa", "contratar", "reclutar", "personal"):
        return "employer"
    if "empleador" in t or ("empresa" in t and "trabajo" not in t):
        return "employer"
    if "trabajo" in t or "empleo" in t:
        return "job_seeker"
    return None


def _is_greeting_or_filler(text: str) -> bool:
    """Saludo o respuesta demasiado vaga para pedir región/modalidad o skills."""
    t = (text or "").strip().lower()
    if len(t) <= 2:
        return True
    if t in ("ok", "oka", "jeje", "jaja", "??", "???", "...", "…", "hmm", "mmm", "ejem", "ups"):
        return True
    return bool(_GREETING_OR_FILLER.match(t))


def _wants_to_send_cv_now(text: str) -> bool:
    """Afirma que tiene CV / va a mandar archivo (evita mandar 'hola' a await_cv)."""
    low = (text or "").strip().lower()
    if not low:
        return False
    if _CV_AFFIRMATIVE_SEND.match(low):
        return True
    return any(
        k in low
        for k in (
            "pdf",
            "docx",
            "word",
            "adjunto",
            "archivo",
            "documento",
            "te lo mando",
            "te lo envío",
            "te lo envio",
            "aquí está",
            "aqui esta",
        )
    )


def _reprompt_region_options() -> str:
    return (
        "Necesito una *zona* o *modalidad* para seguir 🙂 Podés responder así:\n\n"
        "• *Ciudad*: Lima, Arequipa, Cusco, Trujillo…\n"
        "• *Modalidad*: remoto, híbrido, presencial, LATAM…\n"
        "• *O en una frase* con tu foco: *prácticas data en Lima*, *empleo remoto*, *cursos + trabajo*.\n\n"
        "_Si ya lo mandaste en otra línea, copiá la misma zona acá._"
    )


def _reprompt_cv_options() -> str:
    return (
        "No leí bien la respuesta 🙂 Elegí una opción:\n\n"
        "• *Sí* / *dale* / *ok* → te espero con el archivo *PDF o DOCX* (como *Documento*).\n"
        "• *No* / *sin cv* / *todavía no* → armamos tu perfil *sin archivo* (te pido skills).\n\n"
        "_Tip: si ya mandaste el archivo, esperá unos segundos o reenvialo como documento._"
    )


def _normalize_goal(text: str) -> str | None:
    t = text.lower().strip()
    for k, v in GOAL_ALIASES.items():
        if k in t:
            return v
    return None


def _normalize_opp_type(text: str) -> str | None:
    return _normalize_goal(text)


def _normalize_edu(text: str) -> str | None:
    t = text.lower().strip()
    for k, v in EDU_ALIASES.items():
        if k in t:
            return v
    return None


def _format_profile(u: UserProfile) -> str:
    role_txt = "Busco trabajo" if u.role == "job_seeker" else "Empleador" if u.role == "employer" else "—"
    prefs = (u.interests or "").strip() or (u.goal or "").strip()
    coach = get_coach_blob(u)
    extras: list[str] = []
    if coach.get("work_modality"):
        extras.append(f"modalidad: {coach['work_modality']}")
    if coach.get("job_type"):
        extras.append("focos: " + ", ".join(coach["job_type"][:8]))
    if coach.get("areas"):
        extras.append("áreas: " + ", ".join(coach["areas"][:6]))
    lines = [
        f"Perfil ({role_txt})",
        f"- Región: {u.region or coach.get('location') or '—'}",
        f"- Meta / foco: {u.goal or u.hiring_summary or '—'}",
        f"- Intereses (qué ver): {prefs or '—'}",
        f"- Skills: {u.skills or '—'}",
        f"- Educación: {u.education_level or '—'}",
        f"- Extra: {' | '.join(extras) if extras else '—'}",
        f"- Empresa: {u.company_name or '—'}",
        f"- Estado: {u.conversation_state}",
    ]
    return "\n".join(lines)


async def _send(settings: Settings, wa_id: str, text: str) -> None:
    await send_whatsapp_text(settings, wa_id, text)


def _menu_text(u: UserProfile) -> str:
    if u.role == "employer":
        return (
            "Menú empleador\n"
            "• /publicar — crear empleo, curso o voluntariado (paso a paso)\n"
            "• /candidatos — describe el perfil que buscas (WhatsApp + demo seed)\n"
            "• /perfil — ver lo guardado\n"
            "• /reset — borrar y empezar de cero"
        )
    if u.role == "job_seeker":
        return (
            "Menú RutaPe\n"
            "• /oportunidades — ideas según tu perfil\n"
            "• /intereses — qué tipo de avisos quieres ver\n"
            "• /actualizar — CV o texto libre para tu perfil\n"
            "• /perfil — resumen\n"
            "• /reset — empezar de cero\n\n"
            "Tip: puedes escribirme cosas como *quiero prácticas remoto Python* cuando estés en tu flujo normal."
        )
    return "Escribe /menu cuando hayas elegido rol (1 o 2) en el mensaje inicial."


def _selected_opp_types(u: UserProfile) -> set[str]:
    src = ((u.interests or "") + " " + (u.goal or "")).lower()
    picks: set[str] = set()
    empleo_hits = (
        "empleo",
        "trabajo",
        "práctica",
        "practica",
        "freelance",
        "laboral",
        "hackathon",
        "networking",
        "mentor",
        "investig",
        "emprend",
        "startup",
        "beca",
    )
    if any(x in src for x in empleo_hits):
        picks.add("empleo")
    if "curso" in src or "capacit" in src or "estudi" in src or "certif" in src:
        picks.add("curso")
    if "volunt" in src or "ong" in src:
        picks.add("voluntariado")
    if not picks:
        g = (u.goal or "").strip().lower()
        if g in ("empleo", "curso", "voluntariado"):
            picks.add(g)
    return picks


def _parse_interest_choice(text: str) -> set[str]:
    t = (text or "").strip().lower()
    if any(x in t for x in ("4", "todo", "todos", "mix", "mezcla", "un poco")):
        return {"empleo", "curso", "voluntariado"}
    picks: set[str] = set()
    if "1" in t:
        picks.add("empleo")
    if "2" in t:
        picks.add("curso")
    if "3" in t:
        picks.add("voluntariado")
    if any(
        w in t
        for w in (
            "empleo",
            "trabajo",
            "práctica",
            "practica",
            "freelance",
            "laboral",
            "hackathon",
            "networking",
            "mentor",
            "investig",
            "emprend",
            "startup",
        )
    ):
        picks.add("empleo")
    if "curso" in t or "capacit" in t or "estudi" in t:
        picks.add("curso")
    if "volunt" in t or "ong" in t:
        picks.add("voluntariado")
    return picks


async def handle_opportunities(db: Session, settings: Settings, u: UserProfile) -> None:
    t_req = time.perf_counter()
    picks = _selected_opp_types(u)
    q = (
        select(Opportunity)
        .where(Opportunity.active == True)  # noqa: E712
        .where(~Opportunity.url.contains("indeed.com"))
        .where(~Opportunity.url.contains("example.com"))
    )
    if picks:
        q = q.where(Opportunity.type.in_(sorted(picks)))
    region = (u.region or "").strip()
    if region and settings.match_opportunities_region_prefilter:
        q = q.where(Opportunity.region.ilike(f"%{region}%"))
    q = q.order_by(desc(Opportunity.id)).limit(settings.match_opportunities_fetch_limit)

    t_fetch = time.perf_counter()
    opps = list(db.scalars(q).all())
    fetch_ms = (time.perf_counter() - t_fetch) * 1000

    t_match = time.perf_counter()
    ranked = rank_opportunities(u, opps, top_n=5)
    match_ms = (time.perf_counter() - t_match) * 1000

    lines = []
    for i, (score, reason, o) in enumerate(ranked[:3], start=1):
        src = " (publicado por empleador)" if o.employer_wa_id else ""
        lines.append(
            f"{i}) *{o.title}* ({o.type}) — {o.organization}{src}\n"
            f"   Región: {o.region}\n"
            f"   Por qué: {reason}\n"
            f"   Link: {o.url}"
        )
    body_core = (
        "Oportunidades para ti:\n\n" + "\n\n".join(lines) if lines else "Aún no hay oportunidades en la base."
    )
    menu = "\n\n" + _menu_text(u)
    mode = settings.effective_llm_narration()
    openai_ms = 0.0
    first_send_ms: float | None = None

    if mode == "follow_up":
        await _send(settings, u.wa_id, body_core + menu)
        first_send_ms = (time.perf_counter() - t_req) * 1000
        t_llm = time.perf_counter()
        narration = await narrate_matches(settings, u, ranked)
        openai_ms = (time.perf_counter() - t_llm) * 1000
        if narration:
            await _send(settings, u.wa_id, narration)
    elif mode == "inline":
        t_llm = time.perf_counter()
        narration = await narrate_matches(settings, u, ranked)
        openai_ms = (time.perf_counter() - t_llm) * 1000
        body = body_core
        if narration:
            body += "\n\n" + narration
        body += menu
        await _send(settings, u.wa_id, body)
    else:
        await _send(settings, u.wa_id, body_core + menu)

    total_ms = (time.perf_counter() - t_req) * 1000
    latency_payload: dict[str, Any] = {
        "fetch_ms": round(fetch_ms, 2),
        "match_ms": round(match_ms, 2),
        "openai_ms": round(openai_ms, 2),
        "total_ms": round(total_ms, 2),
        "opps_fetched": len(opps),
        "llm_mode": mode,
    }
    if first_send_ms is not None:
        latency_payload["first_send_ms"] = round(first_send_ms, 2)
    emit_latency(settings, "whatsapp_opportunities", **latency_payload)


async def handle_candidate_prompt(db: Session, settings: Settings, u: UserProfile, prompt: str) -> None:
    t_req = time.perf_counter()
    t_match = time.perf_counter()
    ranked = rank_seekers_merged(db, prompt, top_n=8)
    match_ms = (time.perf_counter() - t_match) * 1000

    results = []
    lines = []
    for score, reason, m in ranked[:5]:
        tag = "WhatsApp" if m.source == "whatsapp" else "Demo"
        results.append(
            {
                "nombre": m.display_name,
                "region": m.region,
                "meta": m.goal,
                "skills": m.skills,
                "score": score,
                "razon": reason,
                "resumen": m.summary,
                "origen": tag,
            }
        )
        lines.append(
            f"• [{tag}] {m.display_name} ({m.region})\n"
            f"  Meta: {m.goal} | Skills: {m.skills}\n"
            f"  Match: {reason}"
        )
    body_core = (
        "Candidatos sugeridos:\n\n" + "\n\n".join(lines) if lines else "No hay candidatos con datos suficientes."
    )
    menu = "\n\n" + _menu_text(u)
    mode = settings.effective_llm_narration()
    openai_ms = 0.0
    first_send_ms: float | None = None

    if mode == "follow_up":
        await _send(settings, u.wa_id, body_core + menu)
        first_send_ms = (time.perf_counter() - t_req) * 1000
        t_llm = time.perf_counter()
        extra = await narrate_company_results(settings, prompt, results)
        openai_ms = (time.perf_counter() - t_llm) * 1000
        if extra:
            await _send(settings, u.wa_id, extra)
    elif mode == "inline":
        t_llm = time.perf_counter()
        extra = await narrate_company_results(settings, prompt, results)
        openai_ms = (time.perf_counter() - t_llm) * 1000
        intro = body_core
        if extra:
            intro += "\n\n" + extra
        intro += menu
        await _send(settings, u.wa_id, intro)
    else:
        await _send(settings, u.wa_id, body_core + menu)

    total_ms = (time.perf_counter() - t_req) * 1000
    latency_payload2: dict[str, Any] = {
        "match_ms": round(match_ms, 2),
        "openai_ms": round(openai_ms, 2),
        "total_ms": round(total_ms, 2),
        "ranked": len(ranked),
        "llm_mode": mode,
    }
    if first_send_ms is not None:
        latency_payload2["first_send_ms"] = round(first_send_ms, 2)
    emit_latency(settings, "whatsapp_company_candidates", **latency_payload2)

    u.conversation_state = "ready"
    _touch(u)
    db.commit()


async def process_message(
    db: Session,
    settings: Settings,
    wa_id: str,
    raw_text: str,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    text = (raw_text or "").strip()
    if not text and not payload:
        return

    u = _get_user(db, wa_id)
    low = text.lower()
    _touch(u)

    # CV upload via WhatsApp document.
    if payload:
        doc = extract_inbound_document(payload)
        if doc and same_dm_contact(doc.remote_jid, u.wa_id):
            media = await get_media_base64(settings, payload)
            if not media:
                await _send(
                    settings,
                    u.wa_id,
                    "Recibí tu archivo, pero no pude descargarlo desde Evolution.\n"
                    "Tip: vuelve a enviarlo como *Documento* (PDF/DOCX) y espera 5s.\n"
                    "Si sigue fallando, revisa que Evolution esté conectado y con permisos.",
                )
                return
            filename, mimetype, blob = media
            cv = parse_cv_bytes(filename=filename, mimetype=mimetype, data=blob)
            if len(cv.text) < 200:
                await _send(
                    settings,
                    u.wa_id,
                    "Recibí tu CV, pero parece ser escaneado o no tiene texto extraíble.\n"
                    "Para usar pocos tokens, necesito un PDF/DOCX *con texto seleccionable*.\n"
                    "Si solo tienes escaneo, envíame también un mensaje con tu resumen (skills + experiencia + idiomas).",
                )
                return

            extracted = extract_cv(cv.text)
            if extracted.skills:
                u.skills = ", ".join(extracted.skills[:20])
            if extracted.languages:
                u.interests = (u.interests or "").strip()
                lang_line = "Idiomas: " + ", ".join(extracted.languages[:10])
                u.interests = (u.interests + "\n" + lang_line).strip() if u.interests else lang_line
            cv_payload = {
                "cv_file": {"filename": cv.filename, "mimetype": cv.mimetype},
                "email": extracted.email,
                "phone": extracted.phone,
                "age": extracted.age,
                "languages": extracted.languages,
                "skills": extracted.skills,
                "experience": extracted.experience_lines[:12],
                "education": extracted.education_lines[:12],
            }
            merge_cv_layer(u, cv_payload)
            coach = merge_cv_json_into_coach(get_coach_blob(u), cv_payload)
            set_coach_blob(u, coach)
            # Ask the user to confirm extracted data before proceeding.
            u.conversation_state = "cv_confirm"
            _touch(u)
            db.commit()

            await _send(
                settings,
                u.wa_id,
                "Ya leí tu CV 👌 Esto es lo que saqué (solo lo que aparece en el archivo):\n\n"
                + (extracted.raw_summary or "Resumen: (sin extracto)")
                + "\n\n¿Va bien así?\n"
                "*sí* para confirmar o *no* + qué corregir.\n"
                "Si quieres sumar algo (cursos, links, expectativa), mándalo en el siguiente mensaje.",
            )
            return

    if low.startswith("/reset"):
        reply_to = u.wa_id
        db.delete(u)
        db.commit()
        await _send(
            settings,
            reply_to,
            "Listo, borré tu perfil. Escribe *hola* para elegir de nuevo: "
            "1) Busco trabajo/oportunidades  2) Soy empleador",
        )
        return

    if low.startswith("/menu"):
        await _send(settings, u.wa_id, _menu_text(u))
        db.commit()
        return

    if low.startswith("/intereses"):
        if u.role != "job_seeker":
            await _send(settings, u.wa_id, "Este comando es para quien busca oportunidades (opción 1).")
            db.commit()
            return
        u.conversation_state = "set_interests"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "¿Qué clase de avisos quieres priorizar?\n\n"
            "*1)* Trabajo / prácticas / freelance / proyectos\n"
            "*2)* Cursos y capacitación\n"
            "*3)* Voluntariado\n"
            "*4)* Un mix de todo\n\n"
            "Podés responder *1 3* o decir algo como *freelance data en remoto*.",
        )
        return

    if low.startswith("/actualizar"):
        if u.role != "job_seeker":
            await _send(settings, u.wa_id, "Este comando es para quien busca oportunidades (opción 1).")
            db.commit()
            return
        u.conversation_state = "update_profile"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "Perfecto. Puedes:\n"
            "• Enviar tu *CV como Documento* (PDF/DOCX)\n"
            "• O escribir un resumen en 1 mensaje, por ejemplo:\n"
            "  Región: Lima | Skills: python, sql | Idiomas: español/inglés | Meta: empleo\n\n"
            "Tip: si solo quieres cambiar qué ver, usa */intereses*.",
        )
        return

    if low.startswith("/perfil"):
        await _send(settings, u.wa_id, _format_profile(u))
        db.commit()
        return

    if low.startswith("/oportunidades"):
        if u.role != "job_seeker":
            await _send(settings, u.wa_id, "Este comando es para quien busca trabajo (elige opción 1 al inicio).")
            db.commit()
            return
        if u.conversation_state != "ready":
            await _send(
                settings,
                u.wa_id,
                (
                    "Para ver oportunidades primero confirma tu CV o termina tu registro.\n\n"
                    "• Si preguntamos por tu CV: responde *sí* o *no* y lo que falte.\n"
                    "• Si aún no pasaste meta/skills/educación, completa esos pasos desde *hola*."
                ),
            )
            db.commit()
            return
        await handle_opportunities(db, settings, u)
        log_flow(db, wa_id=u.wa_id, state="cmd_oportunidades", role=u.role, snippet=text[:200])
        return

    if low.startswith("/publicar"):
        if u.role != "employer":
            await _send(settings, u.wa_id, "Solo empleadores (opción 2 al inicio) pueden publicar.")
            db.commit()
            return
        u.conversation_state = "emp_pub_type"
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "Publicar oportunidad — paso 1/5\n"
            "¿Tipo? Responde una palabra: *empleo*, *curso* o *voluntariado*.",
        )
        log_flow(db, wa_id=u.wa_id, state="emp_pub_type", role=u.role, snippet="/publicar")
        return

    if low.startswith("/candidatos") or low.startswith("/empresa"):
        if u.role != "employer":
            await _send(settings, u.wa_id, "Este comando es para empleadores (opción 2 al inicio).")
            db.commit()
            return
        u.conversation_state = "company_prompt"
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "Describe en un mensaje el perfil que buscas "
            "(ej.: *voluntario educación STEM en Lima con buena actitud*).",
        )
        log_flow(db, wa_id=u.wa_id, state="company_prompt", role=u.role, snippet=text[:200])
        return

    if u.conversation_state == "company_prompt":
        await handle_candidate_prompt(db, settings, u, text)
        log_flow(db, wa_id=u.wa_id, state="company_prompt_done", role=u.role, snippet=text[:200])
        return

    # Publicar oportunidad (empleador)
    if u.conversation_state == "emp_pub_type":
        ot = _normalize_opp_type(text)
        if not ot:
            await _send(settings, u.wa_id, "Responde: empleo, curso o voluntariado.")
            db.commit()
            return
        blob = load_user_notes(u)
        blob["pub"] = {"pub_type": ot}
        save_user_notes(u, blob)
        u.conversation_state = "emp_pub_title"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 2/5: *Título* corto de la vacante u oferta (una línea).")
        log_flow(db, wa_id=u.wa_id, state="emp_pub_title", role=u.role, snippet=text[:200])
        return

    if u.conversation_state == "emp_pub_title":
        blob = load_user_notes(u)
        pub = blob.setdefault("pub", {})
        pub["title"] = text[:250]
        save_user_notes(u, blob)
        u.conversation_state = "emp_pub_region"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 3/5: *Región* (ej. Lima, Remoto, Arequipa).")
        return

    if u.conversation_state == "emp_pub_region":
        blob = load_user_notes(u)
        pub = blob.setdefault("pub", {})
        pub["region"] = text[:120]
        save_user_notes(u, blob)
        u.conversation_state = "emp_pub_req"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 4/5: *Requisitos* o descripción breve (skills, experiencia).")
        return

    if u.conversation_state == "emp_pub_req":
        blob = load_user_notes(u)
        pub = blob.setdefault("pub", {})
        pub["requirements"] = text[:2000]
        save_user_notes(u, blob)
        u.conversation_state = "emp_pub_url"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 5/5: *URL* de postulación o más información (https://...).")
        return

    if u.conversation_state == "emp_pub_url":
        blob = load_user_notes(u)
        draft = blob.get("pub") if isinstance(blob.get("pub"), dict) else {}
        url = text.strip()[:512]
        org = u.company_name or "Tu organización"
        opp = Opportunity(
            title=draft.get("title", "Sin título"),
            type=draft.get("pub_type", "empleo"),
            organization=org,
            region=draft.get("region", "Perú"),
            requirements=draft.get("requirements", ""),
            url=url if url.startswith("http") else f"https://{url}",
            active=True,
            employer_wa_id=u.wa_id,
        )
        db.add(opp)
        blob["pub"] = None
        save_user_notes(u, blob)
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            f"Listo: publicamos *{opp.title}* ({opp.type}). Ya aparece en /oportunidades de quienes buscan trabajo.\n\n"
            + _menu_text(u),
        )
        log_flow(db, wa_id=u.wa_id, state="emp_pub_done", role=u.role, snippet=opp.title)
        return

    state = u.conversation_state or "welcome"

    # Rol ya guardado como buscador pero estado colgó en await_role (p. ej. excepción antes del commit).
    if state == "await_role" and u.role == "job_seeker":
        u.conversation_state = "ask_region"
        _touch(u)
        db.commit()
        state = "ask_region"

    if state == "welcome":
        await _send(
            settings,
            u.wa_id,
            "¡Hola! Soy *RutaPe* 👋\n\n"
            "Te ayudo a ordenar tu perfil y encontrar cosas que encajen (empleo, prácticas, cursos, voluntariado, etc.).\n\n"
            "*1)* Busco oportunidades para mí\n"
            "*2)* Soy empresa / ONG y busco gente o publico vacantes\n\n"
            "Respondé *1* o *2* (o una frase corta, está bien).",
        )
        u.conversation_state = "await_role"
        _touch(u)
        db.commit()
        log_flow(db, wa_id=u.wa_id, state="await_role", role=u.role, snippet=text[:200])
        return

    if state == "await_role":
        choice = _parse_role_choice(text)
        if not choice:
            await _send(
                settings,
                u.wa_id,
                "Perdón, no capté 🙂 Elegí una opción:\n\n"
                "*1)* Busco oportunidades para mí\n"
                "*2)* Soy empresa / ONG y busco gente o publico vacantes\n\n"
                "Podés escribir *1* o *2*, o una frase corta (ej. *busco trabajo* / *soy empresa*).",
            )
            db.commit()
            return
        u.role = choice
        if choice == "job_seeker":
            u.conversation_state = "ask_region"
        else:
            u.conversation_state = "emp_company"
        _touch(u)
        db.commit()
        log_flow(db, wa_id=u.wa_id, state=u.conversation_state, role=u.role, snippet=text[:200])
        if choice == "job_seeker":
            await _send(
                settings,
                u.wa_id,
                "Buenísimo 👌 ¿Desde dónde operás o qué zona te interesa?\nEj: *Lima*, *Arequipa*, *remoto LATAM*…",
            )
        else:
            await _send(settings, u.wa_id, "Genial. ¿Nombre de tu *empresa u organización*?")
        return

    if u.role == "employer":
        if state == "emp_company":
            u.company_name = text[:250]
            u.conversation_state = "emp_need"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Describe en una o dos frases *qué perfiles o roles* necesitas (lo usaremos para buscar candidatos).",
            )
            log_flow(db, wa_id=u.wa_id, state="emp_need", role=u.role, snippet=text[:200])
            return
        if state == "emp_need":
            u.hiring_summary = text[:2000]
            u.goal = "contratar"
            u.conversation_state = "ready"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Registrado. Ya puedes:\n"
                "• /publicar — crear empleo, curso o voluntariado\n"
                "• /candidatos — buscar personas (perfiles WhatsApp + demo)\n"
                "• /menu\n\n"
                "Tip: quienes elijan opción 1 verán las oportunidades que publiques.",
            )
            log_flow(db, wa_id=u.wa_id, state="ready_employer", role=u.role, snippet=text[:200])
            return
        if state == "ready":
            await _send(settings, u.wa_id, _menu_text(u))
            db.commit()
            return

    # job_seeker path
    if state == "set_interests":
        picks = _parse_interest_choice(text)
        if not picks:
            await _send(
                settings,
                u.wa_id,
                "Ups, no leí bien 🙂 Probá *1*, *2*, *3* o *4*, o decime con palabras (*empleo y cursos*, por ejemplo).",
            )
            db.commit()
            return
        u.interests = ", ".join(sorted(picks))
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            f"Listo. Ahora te muestro: *{u.interests}*.\n\nUsa */oportunidades*.",
        )
        return

    if state == "update_profile":
        coach = get_coach_blob(u)
        coach = heuristic_ingest(u, text, coach)
        reply_extra = None
        if settings.openai_api_key:
            coach, reply_extra = await _coach_increment_safe(
                settings,
                coach,
                text,
                context_hint="El usuario actualiza su perfil. No repitas datos ya guardados.",
            )
        set_coach_blob(u, coach)
        g = _normalize_goal(text)
        if g:
            u.goal = g
        tags = detect_focus_tags(text)
        if tags:
            u.interests = focus_tags_to_interests_line(tags, u.interests or "")
            u.goal = map_focus_to_db_goal(tags)
        if coach.get("skills"):
            u.skills = ", ".join(coach["skills"][:30])
        if any(w in low for w in ["lima", "arequipa", "cusco", "trujillo", "remoto", "perú", "peru"]):
            u.region = u.region or text.strip().split("\n")[0][:120]
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        out = reply_extra or "Listo: actualicé lo que mencionaste 👌"
        await _send(
            settings,
            u.wa_id,
            f"{out}\n\nCuando quieras: */oportunidades* o */intereses*.",
        )
        return

    if state == "ask_region":
        coach = get_coach_blob(u)
        coach = heuristic_ingest(u, text, coach)
        llm_reply = None
        if settings.openai_api_key:
            coach, llm_reply = await _coach_increment_safe(
                settings,
                coach,
                text,
                context_hint=(
                    "PASO=ask_region (onboarding RutaPe). "
                    "Interpretá solo lo que el usuario escribió: ciudad/región de Perú, modalidad (remoto/híbrido/presencial) "
                    "o focos laborales ya mencionados. No inventes ciudad ni país. No cites ofertas reales ni links."
                ),
                reply_style="funnel_ack",
            )
        if not (u.region or "").strip() and _is_primarily_region_or_modality_reply(text):
            line = text.strip().split("\n")[0][:120]
            if line:
                u.region = line
        set_coach_blob(u, coach)
        combined_tags = detect_focus_tags(text + " " + " ".join(coach.get("job_type") or []))
        if combined_tags and not (u.region or "").strip():
            line = text.strip().split("\n")[0][:120]
            if line and not _is_greeting_or_filler(line):
                u.region = line
        if combined_tags:
            u.goal = map_focus_to_db_goal(combined_tags)
            u.interests = focus_tags_to_interests_line(combined_tags)
            u.conversation_state = "ask_has_cv"
            _touch(u)
            db.commit()
            open_msg = (llm_reply + "\n\n") if llm_reply else "Buenísimo 👌 "
            await _send(
                settings,
                u.wa_id,
                open_msg
                + "¿Tenés tu *CV* en *PDF o DOCX*?\n"
                "• Mandalo como *Documento*\n"
                "• O *no* y seguimos sin archivo.",
            )
            log_flow(db, wa_id=u.wa_id, state="ask_has_cv", role=u.role, snippet=text[:200])
            return
        if _is_primarily_region_or_modality_reply(text):
            u.goal = u.goal or "empleo"
            u.interests = (u.interests or "").strip() or "empleo, prácticas, cursos, voluntariado"
            u.conversation_state = "ask_has_cv"
            _touch(u)
            db.commit()
            open_msg = (llm_reply + "\n\n") if llm_reply else "Buenísimo 👌 "
            await _send(
                settings,
                u.wa_id,
                open_msg
                + "¿Tenés tu *CV* en *PDF o DOCX*?\n"
                "• Mandalo como *Documento*\n"
                "• O *no* y seguimos sin archivo.\n\n"
                "_Tip: después podés afinar con */intereses*._",
            )
            log_flow(db, wa_id=u.wa_id, state="ask_has_cv", role=u.role, snippet=text[:200])
            return
        if _is_greeting_or_filler(text):
            _touch(u)
            db.commit()
            await _send(settings, u.wa_id, _reprompt_region_options())
            log_flow(db, wa_id=u.wa_id, state="ask_region_reprompt", role=u.role, snippet=text[:200])
            return
        u.conversation_state = "ask_focus"
        _touch(u)
        db.commit()
        tail = (
            "\n\nAhora contame qué buscás *ahora mismo* 👇 puede ser uno o varios focos:\n"
            "*trabajo*, *prácticas*, *freelance*, *cursos*, *voluntariado*, "
            "*investigación*, *emprender*, *hackathons*…\n"
            "_Ejemplo: prácticas de data en Lima + curso inglés_\n\n"
            "Si venía en tu mensaje de arriba, respondé nomás lo que falte."
        )
        if llm_reply:
            await _send(settings, u.wa_id, llm_reply + tail)
        else:
            await _send(settings, u.wa_id, "Buenísimo 👌 " + tail.lstrip())
        log_flow(db, wa_id=u.wa_id, state="ask_focus", role=u.role, snippet=text[:200])
        return

    if state == "ask_focus":
        coach = get_coach_blob(u)
        coach = heuristic_ingest(u, text, coach)
        hint = ""
        if settings.openai_api_key:
            coach, hint = await _coach_increment_safe(
                settings,
                coach,
                text,
                context_hint=(
                    "PASO=ask_focus (onboarding RutaPe). "
                    "El usuario debe expresar focos (trabajo, prácticas, cursos, voluntariado, freelance, etc.). "
                    "No inventes vacantes. patch solo si el mensaje aporta focos claros; si no, patch vacío."
                ),
                reply_style="funnel_ack",
            )
        glue = f"{text} {hint or ''} {' '.join(coach.get('job_type') or [])}"
        tags = detect_focus_tags(glue)
        if not tags:
            await _send(
                settings,
                u.wa_id,
                "Todavía no me queda claro el foco 🙂 Elegí o mezclá con palabras:\n\n"
                "• *Trabajo* / *empleo* / *prácticas* / *freelance*\n"
                "• *Cursos* / capacitación\n"
                "• *Voluntariado*\n"
                "• *Emprender* / *investigación* / *hackathons*\n\n"
                "Una línea alcanza, ej: *freelance diseño + curso UX en Lima*.",
            )
            set_coach_blob(u, coach)
            db.commit()
            return
        u.goal = map_focus_to_db_goal(tags)
        u.interests = focus_tags_to_interests_line(tags)
        set_coach_blob(u, coach)
        u.conversation_state = "ask_has_cv"
        _touch(u)
        db.commit()
        lead = f"{hint.strip()}\n\n" if (hint or "").strip() else ""
        await _send(
            settings,
            u.wa_id,
            lead
            + "Genial, eso me ayuda un montón.\n\n"
            "¿Tenés tu *CV* en *PDF o DOCX*?\n"
            "• Mandalo como *Documento*\n"
            "• O escribí *no* y lo armamos a mano en un toque.",
        )
        log_flow(db, wa_id=u.wa_id, state="ask_has_cv", role=u.role, snippet=text[:200])
        return

    if state == "ask_has_cv":
        if any(w in low for w in ("no", "nop", "sin cv", "no tengo", "todavía no", "todavia no")):
            u.conversation_state = "ask_skills"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Dale 👌 Entonces en una línea: *qué sabés hacer* (tecnologías + algo de actitud).\n"
                "Ej: *Python, SQL, buena comunicación, inglés intermedio*",
            )
            log_flow(db, wa_id=u.wa_id, state="ask_skills", role=u.role, snippet=text[:200])
            return
        if _wants_to_send_cv_now(text):
            u.conversation_state = "await_cv"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Joyaa. Mandalo como *Documento* (PDF/DOCX) y en un segundito lo leo.\n"
                "Si se complica el archivo, después lo hacemos por texto con */actualizar*.",
            )
            return
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, _reprompt_cv_options())
        return

    if state == "await_cv":
        # Document handler runs above and will set ready. Here we just nudge.
        if any(w in low for w in ("no", "nop", "sin cv", "cancel")):
            u.conversation_state = "ask_skills"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Ok, seguimos sin archivo. Contame tus *skills* en una línea (comma o palabras sueltas).",
            )
            return
        await _send(
            settings,
            u.wa_id,
            "Acá esperando el *PDF/DOCX* como *Documento* 📎\n\n"
            "• Escribí *no* / *sin cv* → seguimos sin archivo\n"
            "• O reenviá el archivo y esperá unos segundos",
        )
        return

    if state == "cv_confirm":
        stripped = low.strip()

        def _persist_corrections() -> None:
            blob = load_user_notes(u)
            cv = blob.setdefault("cv", {})
            cv["user_confirmation"] = "needs_changes"
            cv["user_corrections"] = text[:1500]
            save_user_notes(u, blob)
            u.conversation_state = "ready"
            _touch(u)
            db.commit()

        neg_hint = bool(_CV_CONFIRM_NEG_HINT.search(low))
        detail_hint = bool(_CV_CORR_DETAIL_HINT.search(low))
        corrections = bool(_CV_CONFIRM_NO_PREFIX.match(stripped)) or neg_hint or (
            len(stripped) >= 16 and detail_hint and not _CV_CONFIRM_CLEAN.match(stripped)
        )

        affirmative = (
            bool(_CV_CONFIRM_CLEAN.match(stripped))
            or (
                bool(_CV_CONFIRM_LOOSE.match(stripped))
                and not neg_hint
                and len(stripped) < 72
                and not _CV_CONFIRM_NO_PREFIX.match(stripped)
            )
        )

        if corrections and not affirmative:
            _persist_corrections()
            await _send(
                settings,
                u.wa_id,
                "Listo. Guardé tu corrección o comentario en tu perfil.\n\n"
                "Puedes seguir con:\n"
                "• */oportunidades*\n"
                "• */actualizar* si quieres detallar más",
            )
            return

        if affirmative:
            blob = load_user_notes(u)
            cv = blob.get("cv") if isinstance(blob.get("cv"), dict) else {}
            coach = merge_cv_json_into_coach(get_coach_blob(u), cv)
            set_coach_blob(u, coach)
            u.conversation_state = "ready"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "¡Top! Guardé tu perfil ✅\n\n"
                "• */oportunidades* cuando quieras ver recomendaciones\n"
                "• Podés mandarme *texto libre*, links o otro archivo para seguir puliendo 🙂",
            )
            return

        await _send(
            settings,
            u.wa_id,
            "¿*sí* cerramos así o *no* + qué habría que tocar?\n\n"
            "• *sí* / *ok* / *listo* → confirmo y seguimos\n"
            "• *no* + qué corregir → lo ajusto",
        )
        db.commit()
        return

    if state == "ask_skills":
        if _is_greeting_or_filler(text) or len(text.strip()) < 4:
            await _send(
                settings,
                u.wa_id,
                "Necesito al menos una pista de *skills* 🙂 Una línea alcanza, por ejemplo:\n"
                "*Python, SQL, Excel, atención al cliente, inglés intermedio*",
            )
            db.commit()
            return
        u.skills = text[:2000]
        coach = get_coach_blob(u)
        for part in re.split(r"[,;/|]+", text):
            if part.strip():
                coach = heuristic_ingest(u, part.strip(), coach)
        set_coach_blob(u, coach)
        u.conversation_state = "ask_education"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "¿Qué *nivel de estudios* tenés hoy? (una palabra alcanza)\n"
            "*secundaria*, *técnico*, *universitario/en curso*, *egresado/a*…",
        )
        log_flow(db, wa_id=u.wa_id, state="ask_education", role=u.role, snippet=text[:200])
        return

    if state == "ask_education":
        e = _normalize_edu(text)
        low_edu = low.strip()
        if not e and any(x in low_edu for x in ("egresad", "bachiller", "titulad", "estudiando", "estudiante")):
            e = "universitario"
        if not e and "secundaria" not in low_edu and len(low_edu) <= 40:
            e = _normalize_edu(low_edu)
        if not e:
            await _send(
                settings,
                u.wa_id,
                "Con *una* de estas me alcanza: *secundaria*, *técnico*, *universitario*.\n"
                "Si estás en curso, decí *universidad en curso* y listo.",
            )
            db.commit()
            return
        u.education_level = e
        if not (u.interests or "").strip():
            u.interests = u.goal or ""
        u.availability = u.availability or "flexible"
        u.role = "job_seeker"
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "¡Genial! Ya armamos la base de tu perfil 🙌\n\n"
            "Probá */oportunidades* o mandame más detalle cuando quieras (links, certis, lo que sea).\n\n"
            + _menu_text(u),
        )
        log_flow(db, wa_id=u.wa_id, state="ready_job_seeker", role=u.role, snippet=text[:200])
        return

    if state == "ready" and u.role == "job_seeker":
        if low.startswith("/"):
            await _send(settings, u.wa_id, _menu_text(u))
            db.commit()
            return
        if low in ("hola", "hey", "buenas", "buenos días", "buenas tardes", "ola") or low.startswith("hola "):
            await _send(
                settings,
                u.wa_id,
                "¡Hola de nuevo! Si querés, seguimos puliendo tu perfil o mirá opciones 👇\n\n" + _menu_text(u),
            )
            db.commit()
            return
        coach = get_coach_blob(u)
        coach = heuristic_ingest(u, text, coach)
        reply = None
        if settings.openai_api_key:
            coach, reply = await _coach_increment_safe(
                settings,
                coach,
                text,
                context_hint="Usuario comparte info extra o link. Actualiza perfil mentalmente; no repitas lo ya guardado.",
            )
        if coach.get("skills"):
            u.skills = ", ".join(coach["skills"][:30])[:2000]
        set_coach_blob(u, coach)
        tags = detect_focus_tags(text)
        if tags:
            u.interests = focus_tags_to_interests_line(tags, u.interests or "")
            u.goal = map_focus_to_db_goal(tags)
        _touch(u)
        db.commit()
        if reply:
            await _send(
                settings,
                u.wa_id,
                reply + "\n\n_Si querés ver matches: */oportunidades*._",
            )
        else:
            await _send(
                settings,
                u.wa_id,
                "Lo anoté 👌 Si querés ver ideas ahora: */oportunidades*. Si no, seguí contándome.\n\n" + _menu_text(u),
            )
        return

    await _send(
        settings,
        u.wa_id,
        "Escribe *hola* o /menu. Si acabas de usar /reset, envía hola para ver opciones 1 y 2.",
    )
    db.commit()
