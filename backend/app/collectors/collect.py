from __future__ import annotations

from collections.abc import Iterable

from app.collectors.schema import NormalizedOpportunity
from app.collectors.sources.courses import collect_courses
from app.collectors.sources.jobs import collect_computrabajo, collect_jobs
from app.collectors.sources.volunteering import collect_volunteering
from app.collectors.sources.generic_listings import dedupe_by_url


def collect_from_seed_url(*, seed_url: str, query: str, limit: int) -> list[NormalizedOpportunity]:
    """
    Importación dirigida desde una URL (panel admin).
    Los hosts soportados crecen aquí; hoy: Computrabajo (cualquier TLD → listados PE + Scrapling).
    """
    u = (seed_url or "").strip().lower()
    if "computrabajo.com" in u:
        return collect_computrabajo(query=query, limit=min(max(1, limit), 300), seed_url=seed_url.strip())
    raise ValueError("unsupported_seed_host")


def collect_all(*, query: str, limit_total: int = 90) -> list[NormalizedOpportunity]:
    """
    Collect a mixed set of opportunities (jobs + courses + volunteering).

    We keep it simple and run each family collector synchronously.
    """
    limit_total = max(1, min(int(limit_total), 300))
    jobs = collect_jobs(query=query, limit=max(10, limit_total // 3))
    courses = collect_courses(query=query, limit=max(10, limit_total // 3))
    vol = collect_volunteering(query=query, limit=max(10, limit_total // 3))
    return dedupe_by_url([*jobs, *courses, *vol])[:limit_total]


def to_seed_rows(items: Iterable[NormalizedOpportunity]) -> list[dict]:
    return [
        {
            "title": it.title,
            "type": it.type,
            "organization": it.organization,
            "region": it.region,
            "requirements": it.requirements,
            "url": it.url,
        }
        for it in items
    ]

