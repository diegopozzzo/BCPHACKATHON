import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Opportunity, SeedCandidate, UserProfile


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    lowered = text.lower()
    parts = re.split(r"[\s,;.]+", lowered)
    return {p for p in parts if len(p) > 2}


def score_text_blob_match(blob: str | None, prompt: str) -> tuple[float, str]:
    pt = _tokens(prompt)
    ct = _tokens(blob or "")
    if not pt:
        return 0.0, "Prompt vacío."
    if not ct:
        return 0.0, "Sin datos del candidato."
    inter = ct & pt
    score = len(inter) / max(3, len(pt))
    reason = f"Alineación: {', '.join(sorted(inter)[:8])}" if inter else "Pocos términos en común."
    return round(min(1.0, score), 4), reason


def score_opportunity(profile: UserProfile, opp: Opportunity) -> tuple[float, str]:
    blob = " ".join(
        filter(
            None,
            [
                profile.skills,
                profile.interests,
                profile.goal,
                profile.region,
                profile.education_level,
            ],
        )
    )
    pt = _tokens(blob)
    ot = _tokens(" ".join([opp.requirements, opp.title, opp.organization, opp.region]))
    if not pt or not ot:
        return 0.0, "Poca información para comparar."
    inter = pt & ot
    score = len(inter) / max(1, min(len(pt), len(ot)))
    reason = f"Coincidencias: {', '.join(sorted(inter)[:6])}" if inter else "Sin coincidencias directas."
    return round(score, 4), reason


def rank_opportunities(profile: UserProfile, opportunities: list[Opportunity], top_n: int = 5):
    scored: list[tuple[float, str, Opportunity]] = []
    for o in opportunities:
        if not o.active:
            continue
        s, r = score_opportunity(profile, o)
        scored.append((s, r, o))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


def score_candidate_prompt(candidate: SeedCandidate, prompt: str) -> tuple[float, str]:
    blob = " ".join(
        [
            candidate.skills,
            candidate.interests,
            candidate.summary or "",
            candidate.region,
            candidate.goal,
            candidate.education_level,
            candidate.display_name,
        ]
    )
    return score_text_blob_match(blob, prompt)


def rank_candidates_for_prompt(candidates: list[SeedCandidate], prompt: str, top_n: int = 5):
    scored: list[tuple[float, str, SeedCandidate]] = []
    for c in candidates:
        s, r = score_candidate_prompt(c, prompt)
        scored.append((s, r, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


@dataclass
class SeekerMatch:
    display_name: str
    region: str
    goal: str
    skills: str
    score: float
    reason: str
    summary: str | None
    source: str  # whatsapp | seed


def rank_seekers_merged(db: Session, prompt: str, top_n: int = 8) -> list[tuple[float, str, SeekerMatch]]:
    scored: list[tuple[float, str, SeekerMatch]] = []
    for c in db.scalars(select(SeedCandidate)).all():
        s, r = score_candidate_prompt(c, prompt)
        scored.append(
            (
                s,
                r,
                SeekerMatch(
                    display_name=c.display_name,
                    region=c.region,
                    goal=c.goal,
                    skills=c.skills,
                    score=s,
                    reason=r,
                    summary=c.summary,
                    source="seed",
                ),
            )
        )
    for u in db.scalars(select(UserProfile).where(UserProfile.role == "job_seeker")).all():
        blob = " ".join(
            filter(
                None,
                [
                    u.skills,
                    u.interests,
                    u.goal,
                    u.region,
                    u.education_level,
                    u.display_name,
                ],
            )
        )
        s, r = score_text_blob_match(blob, prompt)
        name = u.display_name or f"Candidato WhatsApp"
        scored.append(
            (
                s,
                r,
                SeekerMatch(
                    display_name=name,
                    region=u.region or "—",
                    goal=u.goal or "—",
                    skills=u.skills or "—",
                    score=s,
                    reason=r,
                    summary=u.notes,
                    source="whatsapp",
                ),
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


@dataclass
class CompanySearchResult:
    display_name: str
    region: str
    goal: str
    skills: str
    score: float
    reason: str
    summary: str | None
    source: str = "seed"
