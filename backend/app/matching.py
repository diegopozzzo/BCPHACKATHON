import heapq
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coach_intake import coach_to_search_blob
from app.models import Opportunity, SeedCandidate, UserProfile
from app.profile_notes import get_coach_blob, parse_notes_blob


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
    coach_extra = coach_to_search_blob(get_coach_blob(profile))
    blob = " ".join(
        filter(
            None,
            [
                profile.skills,
                profile.interests,
                profile.goal,
                profile.region,
                profile.education_level,
                coach_extra,
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
    def gen():
        for o in opportunities:
            if not o.active:
                continue
            s, r = score_opportunity(profile, o)
            yield (s, r, o)

    return heapq.nlargest(top_n, gen(), key=lambda x: x[0])


def score_seed_fields(
    skills: str,
    interests: str,
    summary: str | None,
    region: str,
    goal: str,
    education_level: str,
    display_name: str,
    prompt: str,
) -> tuple[float, str]:
    blob = " ".join([skills, interests, summary or "", region, goal, education_level, display_name])
    return score_text_blob_match(blob, prompt)


def score_candidate_prompt(candidate: SeedCandidate, prompt: str) -> tuple[float, str]:
    return score_seed_fields(
        candidate.skills,
        candidate.interests,
        candidate.summary,
        candidate.region,
        candidate.goal,
        candidate.education_level,
        candidate.display_name,
        prompt,
    )


def rank_candidates_for_prompt(candidates: list[SeedCandidate], prompt: str, top_n: int = 5):
    def gen():
        for c in candidates:
            s, r = score_candidate_prompt(c, prompt)
            yield (s, r, c)

    return heapq.nlargest(top_n, gen(), key=lambda x: x[0])


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


def _iter_ranked_seekers(db: Session, prompt: str):
    """
    Itera candidatos con columnas mínimas. Para N muy grande, el siguiente paso es recorte vía
    SQLite FTS5 o embeddings (OPENAI_EMBEDDINGS_MODEL) antes de este barrido.
    """
    seed_stmt = select(
        SeedCandidate.display_name,
        SeedCandidate.region,
        SeedCandidate.education_level,
        SeedCandidate.skills,
        SeedCandidate.interests,
        SeedCandidate.goal,
        SeedCandidate.summary,
    )
    for row in db.execute(seed_stmt):
        s, r = score_seed_fields(
            row.skills,
            row.interests,
            row.summary,
            row.region,
            row.goal,
            row.education_level,
            row.display_name,
            prompt,
        )
        yield (
            s,
            r,
            SeekerMatch(
                display_name=row.display_name,
                region=row.region,
                goal=row.goal,
                skills=row.skills,
                score=s,
                reason=r,
                summary=row.summary,
                source="seed",
            ),
        )

    prof_stmt = select(
        UserProfile.display_name,
        UserProfile.region,
        UserProfile.skills,
        UserProfile.interests,
        UserProfile.goal,
        UserProfile.education_level,
        UserProfile.notes,
    ).where(UserProfile.role == "job_seeker")
    for row in db.execute(prof_stmt):
        coach = (parse_notes_blob(row.notes).get("coach") if row.notes else {}) or {}
        wx_extra = coach_to_search_blob(coach if isinstance(coach, dict) else {})
        blob = (
            " ".join(
                filter(
                    None,
                    [
                        row.skills,
                        row.interests,
                        row.goal,
                        row.region,
                        row.education_level,
                        row.display_name,
                        wx_extra,
                    ],
                )
            )
        )
        s, r = score_text_blob_match(blob, prompt)
        name = row.display_name or "Candidato WhatsApp"
        yield (
            s,
            r,
            SeekerMatch(
                display_name=name,
                region=row.region or "—",
                goal=row.goal or "—",
                skills=row.skills or "—",
                score=s,
                reason=r,
                summary=row.notes,
                source="whatsapp",
            ),
        )


def rank_seekers_merged(db: Session, prompt: str, top_n: int = 8) -> list[tuple[float, str, SeekerMatch]]:
    return heapq.nlargest(top_n, _iter_ranked_seekers(db, prompt), key=lambda x: x[0])


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
