import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, Opportunity, SeedCandidate

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(name: str) -> list[dict]:
    path = DATA_DIR / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def seed_if_empty(db: Session) -> None:
    if db.scalar(select(Opportunity).limit(1)):
        logger.info("Seed oportunidades ya presente, se omite.")
    else:
        rows = load_json("opportunities.json")
        if rows:
            for row in rows:
                db.add(
                    Opportunity(
                        title=row["title"],
                        type=row["type"],
                        organization=row["organization"],
                        region=row["region"],
                        requirements=row["requirements"],
                        url=row["url"],
                        active=True,
                    )
                )
            logger.info("Oportunidades seed insertadas: %s", len(rows))
            db.commit()
        else:
            logger.info("Seed oportunidades vacío: se omite.")

    if db.scalar(select(SeedCandidate).limit(1)):
        logger.info("Seed candidatos ya presente, se omite.")
    else:
        for row in load_json("seed_candidates.json"):
            db.add(
                SeedCandidate(
                    display_name=row["display_name"],
                    region=row["region"],
                    education_level=row["education_level"],
                    skills=row["skills"],
                    interests=row["interests"],
                    availability=row["availability"],
                    goal=row["goal"],
                    summary=row.get("summary"),
                )
            )
        logger.info("Candidatos seed insertados.")
        db.commit()

    if db.scalar(select(Company).limit(1)):
        return
    db.add(Company(name="Demo Partner"))
    db.commit()
    logger.info("Empresa demo insertada.")
