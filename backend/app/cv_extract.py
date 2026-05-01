from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedCV:
    full_name: str | None
    email: str | None
    phone: str | None
    age: str | None
    languages: list[str]
    skills: list[str]
    experience_lines: list[str]
    education_lines: list[str]
    raw_summary: str


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_AGE_RE = re.compile(r"\b(edad|años)\s*[:\-]?\s*(\d{2})\b", re.I)


def _uniq(seq: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in seq:
        k = s.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(s.strip())
    return out


def _extract_languages(text: str) -> list[str]:
    # Strict: only extract from an explicit "Idiomas:" line/section.
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    idi_line = next((ln for ln in lines if ln.lower().startswith("idiomas")), "")
    if ":" not in idi_line:
        return []
    rhs = idi_line.split(":", 1)[1]
    # "Inglés C1 | Alemán B1 | Francés A2"
    tokens = re.split(r"[|,/;]+", rhs)
    langs: list[str] = []
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        # keep only the language word(s), drop levels like C1/B2/A2
        t = re.sub(r"\b[A-C]\d\b", "", t, flags=re.I).strip()
        # normalize common variants
        low = t.lower()
        if "ingles" in low or "inglés" in low:
            langs.append("Inglés")
        elif "aleman" in low or "alemán" in low:
            langs.append("Alemán")
        elif "frances" in low or "francés" in low:
            langs.append("Francés")
        elif "español" in low or "espanol" in low:
            langs.append("Español")
        else:
            # keep as written (title-cased)
            langs.append(t[:1].upper() + t[1:])
    return _uniq(langs)


def _extract_skills(text: str) -> list[str]:
    """
    Strict: only extract from an explicit "Conocimientos y habilidades" section.
    Avoids hallucinating skills from a predefined list.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # find header line
    header_idx = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "conocimientos" in low and "habil" in low:
            header_idx = i
            break
    if header_idx is None:
        return []

    # read until next divider/section header
    out_lines: list[str] = []
    for ln in lines[header_idx + 1 :]:
        low = ln.lower()
        if low.startswith("premios") or low.startswith("idiomas") or low.startswith("educación") or low.startswith("educacion"):
            break
        if set(ln) == {"_"} or ln.startswith("___"):
            break
        out_lines.append(ln)
        if len(out_lines) >= 10:
            break

    blob = " ".join(out_lines)
    if not blob:
        return []

    # Extract explicit tool names from the section as written.
    # Handle common pattern: "Microsoft Office (Word, Excel, PowerPoint, etc)"
    skills: list[str] = []
    m = re.search(r"microsoft\s+office\s*\(([^)]{1,120})\)", blob, flags=re.I)
    if m:
        inner = m.group(1)
        for tok in re.split(r"[,/|;]+", inner):
            t = tok.strip()
            if not t or t.lower() in ("etc", "etc.", "etc)"):
                continue
            skills.append(t[:1].upper() + t[1:])
        skills.append("Microsoft Office")

    # Also accept short, explicit tokens separated by commas after ":" in bullet lines
    for part in re.split(r"[•\u2022]+", blob):
        if ":" in part:
            rhs = part.split(":", 1)[1]
            for tok in re.split(r"[,/|;]+", rhs):
                t = tok.strip()
                if not t:
                    continue
                # avoid long sentences
                if len(t) > 35:
                    continue
                skills.append(t[:1].upper() + t[1:])

    # Comma-split short tokens on lines without ":" (still strict inside the skills section).
    # Example line: "Word, Excel, PowerPoint."
    split_bits = []
    for raw_ln in out_lines:
        ln = raw_ln.strip().rstrip(".").strip()
        if ln and ":" not in ln:
            split_bits.append(ln)
    for bit in split_bits:
        low = bit.lower()
        if "microsoft office" in low and "(" not in low:
            continue
        if len(bit) <= 120:
            for tok in re.split(r"[,/|]+", bit):
                t = tok.strip().rstrip(".").strip()
                if not t or len(t) > 35:
                    continue
                low_t = t.lower()
                if low_t in ("etc", "etc.)", "entre otros"):
                    continue
                skills.append(t[:1].upper() + t[1:])

    return _uniq(skills)


def _grab_section_lines(text: str, headers: list[str], max_lines: int = 12) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    idxs = [i for i, ln in enumerate(lines) if any(h in ln.lower() for h in headers)]
    if not idxs:
        return []
    start = idxs[0] + 1
    chunk = lines[start : start + max_lines]
    return chunk


def extract_cv(text: str) -> ExtractedCV:
    m_email = _EMAIL_RE.search(text or "")
    email = m_email.group(0) if m_email else None
    phone = None
    m = _PHONE_RE.search(text or "")
    if m:
        phone = re.sub(r"[^\d+]", "", m.group(1))

    age = None
    m2 = _AGE_RE.search(text or "")
    if m2:
        age = m2.group(2)

    languages = _extract_languages(text)
    skills = _extract_skills(text)

    experience = _grab_section_lines(text, ["experiencia", "experience", "trabajo", "empleo", "laboral"])
    education = _grab_section_lines(text, ["educación", "educacion", "education", "formación", "formacion"])

    # Best-effort name: first non-empty line that's not an obvious header
    full_name = None
    for ln in [l.strip() for l in (text or "").splitlines() if l.strip()][:10]:
        low = ln.lower()
        if len(ln) < 4:
            continue
        if any(x in low for x in ["curriculum", "cv", "resume", "perfil", "datos personales"]):
            continue
        if _EMAIL_RE.search(ln) or _PHONE_RE.search(ln):
            continue
        if len(ln.split()) >= 2 and len(ln) <= 60:
            full_name = ln
            break

    summary_parts = []
    if full_name:
        summary_parts.append(f"Nombre: {full_name}")
    if age:
        summary_parts.append(f"Edad: {age}")
    if languages:
        summary_parts.append("Idiomas: " + ", ".join(languages[:6]))
    if skills:
        summary_parts.append("Conocimientos: " + ", ".join(skills[:12]))
    if experience:
        summary_parts.append("Experiencia (extracto): " + " | ".join(experience[:6]))
    if education:
        summary_parts.append("Educación (extracto): " + " | ".join(education[:6]))

    return ExtractedCV(
        full_name=full_name,
        email=email,
        phone=phone,
        age=age,
        languages=languages,
        skills=skills,
        experience_lines=experience,
        education_lines=education,
        raw_summary="\n".join(summary_parts).strip(),
    )

