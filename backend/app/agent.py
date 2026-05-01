import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.cv_extract import extract_cv
from app.cv_parse import parse_cv_bytes
from app.evolution_media import extract_inbound_document, get_media_base64
from app.evolution_client import send_whatsapp_text
from app.flow_log import log_flow
from app.llm import narrate_company_results, narrate_matches
from app.matching import rank_opportunities, rank_seekers_merged
from app.models import Opportunity, UserProfile
from app.whatsapp_jid import canonical_dm_jid, same_dm_contact

logger = logging.getLogger(__name__)

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

GOAL_ALIASES = {
    "empleo": "empleo",
    "trabajo": "empleo",
    "laboral": "empleo",
    "curso": "curso",
    "estudiar": "curso",
    "capacitacion": "curso",
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
    if t.startswith("1") or t in ("trabajo", "busco trabajo", "busco empleo", "postulante"):
        return "job_seeker"
    if t.startswith("2") or t in ("empleador", "empresa", "contratar", "reclutar", "personal"):
        return "employer"
    if "empleador" in t or ("empresa" in t and "trabajo" not in t):
        return "employer"
    if "trabajo" in t or "empleo" in t:
        return "job_seeker"
    return None


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
    lines = [
        f"Perfil ({role_txt})",
        f"- Región: {u.region or '—'}",
        f"- Meta / foco: {u.goal or u.hiring_summary or '—'}",
        f"- Intereses (qué ver): {prefs or '—'}",
        f"- Skills: {u.skills or '—'}",
        f"- Educación: {u.education_level or '—'}",
        f"- Empresa: {u.company_name or '—'}",
        f"- Estado conversación: {u.conversation_state}",
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
            "Menú persona que busca oportunidades\n"
            "• /oportunidades — ver empleos, cursos y voluntariados que encajan\n"
            "• /intereses — elegir qué quieres ver (empleos/cursos/voluntariado)\n"
            "• /actualizar — subir CV (PDF/DOCX) o actualizar tu info\n"
            "• /perfil — ver tu perfil\n"
            "• /reset — borrar y empezar de cero"
        )
    return "Escribe /menu cuando hayas elegido rol (1 o 2) en el mensaje inicial."


def _selected_opp_types(u: UserProfile) -> set[str]:
    src = (u.interests or "").lower()
    picks: set[str] = set()
    for t in ("empleo", "curso", "voluntariado"):
        if t in src:
            picks.add(t)
    if not picks:
        g = (u.goal or "").strip().lower()
        if g in ("empleo", "curso", "voluntariado"):
            picks.add(g)
    return picks


def _parse_interest_choice(text: str) -> set[str]:
    t = (text or "").strip().lower()
    if "4" in t or "todo" in t or "todos" in t:
        return {"empleo", "curso", "voluntariado"}
    picks: set[str] = set()
    if "1" in t:
        picks.add("empleo")
    if "2" in t:
        picks.add("curso")
    if "3" in t:
        picks.add("voluntariado")
    if "empleo" in t or "trabajo" in t:
        picks.add("empleo")
    if "curso" in t or "capacit" in t:
        picks.add("curso")
    if "volunt" in t or "ong" in t:
        picks.add("voluntariado")
    return picks


async def handle_opportunities(db: Session, settings: Settings, u: UserProfile) -> None:
    picks = _selected_opp_types(u)
    q = (
        select(Opportunity)
        .where(Opportunity.active == True)  # noqa: E712
        .where(~Opportunity.url.contains("indeed.com"))
        .where(~Opportunity.url.contains("example.com"))
    )
    if picks:
        q = q.where(Opportunity.type.in_(sorted(picks)))
    opps = list(db.scalars(q).all())
    ranked = rank_opportunities(u, opps, top_n=5)
    lines = []
    for i, (score, reason, o) in enumerate(ranked[:3], start=1):
        src = " (publicado por empleador)" if o.employer_wa_id else ""
        lines.append(
            f"{i}) *{o.title}* ({o.type}) — {o.organization}{src}\n"
            f"   Región: {o.region}\n"
            f"   Por qué: {reason}\n"
            f"   Link: {o.url}"
        )
    body = "Oportunidades para ti:\n\n" + "\n\n".join(lines) if lines else "Aún no hay oportunidades en la base."
    narration = await narrate_matches(settings, u, ranked)
    if narration:
        body += "\n\n" + narration
    body += "\n\n" + _menu_text(u)
    await _send(settings, u.wa_id, body)


async def handle_candidate_prompt(db: Session, settings: Settings, u: UserProfile, prompt: str) -> None:
    ranked = rank_seekers_merged(db, prompt, top_n=8)
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
    intro = "Candidatos sugeridos:\n\n" + "\n\n".join(lines) if lines else "No hay candidatos con datos suficientes."
    extra = await narrate_company_results(settings, prompt, results)
    if extra:
        intro += "\n\n" + extra
    intro += "\n\n" + _menu_text(u)
    await _send(settings, u.wa_id, intro)
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
            # Persist minimal structured data (keep it cheap).
            if extracted.skills:
                u.skills = ", ".join(extracted.skills[:20])
            if extracted.languages:
                u.interests = (u.interests or "").strip()
                lang_line = "Idiomas: " + ", ".join(extracted.languages[:10])
                u.interests = (u.interests + "\n" + lang_line).strip() if u.interests else lang_line
            # Keep a compact JSON in notes (admin can display it).
            u.notes = json.dumps(
                {
                    "cv_file": {"filename": cv.filename, "mimetype": cv.mimetype},
                    "email": extracted.email,
                    "phone": extracted.phone,
                    "age": extracted.age,
                    "languages": extracted.languages,
                    "skills": extracted.skills,
                    "experience": extracted.experience_lines[:12],
                    "education": extracted.education_lines[:12],
                },
                ensure_ascii=False,
            )
            # Ask the user to confirm extracted data before proceeding.
            u.conversation_state = "cv_confirm"
            _touch(u)
            db.commit()

            await _send(
                settings,
                u.wa_id,
                "Procesé tu CV y armé este resumen (solo con lo que aparece en el documento):\n\n"
                + (extracted.raw_summary or "Resumen: (sin extracto)")
                + "\n\n¿Está correcto?\n"
                "Responde *sí* / *no*.\n"
                "Si quieres agregar algo extra (skills, cursos, certificaciones), dime después.",
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
            "¿Qué quieres ver ahora?\n"
            "*1)* Empleos\n"
            "*2)* Cursos\n"
            "*3)* Voluntariado\n"
            "*4)* Todo\n\n"
            "Responde con números (ej: *1 2*) o con palabras (ej: *empleo y cursos*).",
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
        u.notes = json.dumps({"pub_type": ot})
        u.conversation_state = "emp_pub_title"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 2/5: *Título* corto de la vacante u oferta (una línea).")
        log_flow(db, wa_id=u.wa_id, state="emp_pub_title", role=u.role, snippet=text[:200])
        return

    if u.conversation_state == "emp_pub_title":
        draft = json.loads(u.notes or "{}")
        draft["title"] = text[:250]
        u.notes = json.dumps(draft)
        u.conversation_state = "emp_pub_region"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 3/5: *Región* (ej. Lima, Remoto, Arequipa).")
        return

    if u.conversation_state == "emp_pub_region":
        draft = json.loads(u.notes or "{}")
        draft["region"] = text[:120]
        u.notes = json.dumps(draft)
        u.conversation_state = "emp_pub_req"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 4/5: *Requisitos* o descripción breve (skills, experiencia).")
        return

    if u.conversation_state == "emp_pub_req":
        draft = json.loads(u.notes or "{}")
        draft["requirements"] = text[:2000]
        u.notes = json.dumps(draft)
        u.conversation_state = "emp_pub_url"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Paso 5/5: *URL* de postulación o más información (https://...).")
        return

    if u.conversation_state == "emp_pub_url":
        draft = json.loads(u.notes or "{}")
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
        u.notes = None
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

    if state == "welcome":
        await _send(
            settings,
            u.wa_id,
            "Hola, soy tu asistente *RutaPe* (demo).\n\n"
            "¿Cómo te ayudo hoy?\n"
            "*1)* Busco trabajo u oportunidades (cursos / voluntariado)\n"
            "*2)* Soy empleador y busco personas o quiero publicar ofertas\n\n"
            "Responde con *1* o *2* (o una frase corta equivalente).",
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
                "No entendí. Responde *1* si buscas trabajo/oportunidades, o *2* si eres empleador.",
            )
            db.commit()
            return
        u.role = choice
        if choice == "job_seeker":
            u.conversation_state = "ask_region"
            await _send(
                settings,
                u.wa_id,
                "Perfecto. ¿En qué *ciudad o región* buscas? (ej. Lima, Arequipa, Remoto)",
            )
        else:
            u.conversation_state = "emp_company"
            await _send(settings, u.wa_id, "Genial. ¿Nombre de tu *empresa u organización*?")
        _touch(u)
        db.commit()
        log_flow(db, wa_id=u.wa_id, state=u.conversation_state, role=u.role, snippet=text[:200])
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
            await _send(settings, u.wa_id, "No entendí. Responde 1/2/3/4 o escribe: empleo, curso, voluntariado.")
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
        g = _normalize_goal(text)
        if g:
            u.goal = g
        # cheap heuristics for region
        if any(w in low for w in ["lima", "arequipa", "cusco", "remoto"]):
            u.region = (u.region or "").strip() or text[:120]
        # if they provide a skills blob, store it
        if any(k in low for k in ["skills", "habilidades", "conocimientos", "stack", "tecnolog"]):
            u.skills = text[:2000]
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "Actualizado. Usa */oportunidades* o */intereses* para cambiar qué ver.")
        return

    if state == "ask_region":
        u.region = text[:120]
        u.conversation_state = "ask_goal"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "¿Qué buscas primero? Una palabra: *empleo*, *curso* o *voluntariado*.",
        )
        log_flow(db, wa_id=u.wa_id, state="ask_goal", role=u.role, snippet=text[:200])
        return

    if state == "ask_goal":
        g = _normalize_goal(text)
        if not g:
            await _send(settings, u.wa_id, "Escribe: empleo, curso o voluntariado.")
            db.commit()
            return
        u.goal = g
        u.conversation_state = "ask_has_cv"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "Antes de seguir: ¿tienes tu *CV* en *PDF o DOCX*?\n\n"
            "• Si sí: envíalo como *Documento* aquí.\n"
            "• Si no: responde *no* y seguimos.\n\n"
            "Tip: esto ahorra tiempo y mejora tus recomendaciones.",
        )
        log_flow(db, wa_id=u.wa_id, state="ask_has_cv", role=u.role, snippet=text[:200])
        return

    if state == "ask_has_cv":
        if "no" in low:
            u.conversation_state = "ask_skills"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Perfecto. Cuéntame tus *skills* (separadas por coma).\nEj: excel, python, atención al cliente",
            )
            log_flow(db, wa_id=u.wa_id, state="ask_skills", role=u.role, snippet=text[:200])
            return
        # If user says yes (or anything else), we keep waiting for a document.
        u.conversation_state = "await_cv"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "Genial. Envíame tu CV como *Documento* (PDF/DOCX).\n"
            "Cuando lo reciba, lo proceso y armamos tu perfil.",
        )
        return

    if state == "await_cv":
        # Document handler runs above and will set ready. Here we just nudge.
        if "no" in low:
            u.conversation_state = "ask_skills"
            _touch(u)
            db.commit()
            await _send(settings, u.wa_id, "Ok. Cuéntame tus *skills* (separadas por coma).")
            return
        await _send(
            settings,
            u.wa_id,
            "Te leo. Si quieres, envía tu CV como *Documento* (PDF/DOCX) o responde *no* para seguir sin CV.",
        )
        return

    if state == "cv_confirm":
        stripped = low.strip()

        def _persist_corrections() -> None:
            try:
                existing = json.loads(u.notes or "{}") if (u.notes or "").strip().startswith("{") else {}
            except Exception:
                existing = {}
            existing["user_confirmation"] = "needs_changes"
            existing["user_corrections"] = text[:1500]
            u.notes = json.dumps(existing, ensure_ascii=False)
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
            u.conversation_state = "ready"
            _touch(u)
            db.commit()
            await _send(
                settings,
                u.wa_id,
                "Perfecto. Ya quedó tu perfil.\n\n"
                "Ahora escribe:\n"
                "• */oportunidades* para recomendaciones\n"
                "• */intereses* para cambiar qué ver\n"
                "• */actualizar* si quieres reenviar CV o ajustar datos",
            )
            return

        await _send(
            settings,
            u.wa_id,
            "Responde *sí* para confirmar, o escribe un mensaje aclarando qué falta "
            "(empieza con *no* si el resumen no es correcto).",
        )
        db.commit()
        return

    if state == "ask_skills":
        u.skills = text[:2000]
        u.conversation_state = "ask_education"
        _touch(u)
        db.commit()
        await _send(settings, u.wa_id, "¿*Nivel educativo*? (secundaria, tecnico, universitario)")
        log_flow(db, wa_id=u.wa_id, state="ask_education", role=u.role, snippet=text[:200])
        return

    if state == "ask_education":
        e = _normalize_edu(text)
        if not e:
            await _send(settings, u.wa_id, "Indica: secundaria, tecnico o universitario.")
            db.commit()
            return
        u.education_level = e
        u.interests = u.goal
        u.availability = "flexible"
        u.role = "job_seeker"
        u.conversation_state = "ready"
        _touch(u)
        db.commit()
        await _send(
            settings,
            u.wa_id,
            "¡Listo! Usa /oportunidades para ver empleos, cursos y voluntariados.\n\n" + _menu_text(u),
        )
        log_flow(db, wa_id=u.wa_id, state="ready_job_seeker", role=u.role, snippet=text[:200])
        return

    if state == "ready" and u.role == "job_seeker":
        await _send(settings, u.wa_id, _menu_text(u))
        db.commit()
        return

    await _send(
        settings,
        u.wa_id,
        "Escribe *hola* o /menu. Si acabas de usar /reset, envía hola para ver opciones 1 y 2.",
    )
    db.commit()
