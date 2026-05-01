from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    education_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)
    interests: Mapped[str | None] = mapped_column(Text, nullable=True)
    availability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_state: Mapped[str] = mapped_column(String(64), default="welcome")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="")  # job_seeker | employer
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hiring_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_utcnow, nullable=True)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32))
    organization: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(128))
    requirements: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(512))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    employer_wa_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=True)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))


class SeedCandidate(Base):
    """Perfiles ficticios para demo de búsqueda empresa (no vinculados a WhatsApp)."""

    __tablename__ = "seed_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(128))
    education_level: Mapped[str] = mapped_column(String(64))
    skills: Mapped[str] = mapped_column(Text)
    interests: Mapped[str] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(String(64))
    goal: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class FlowEvent(Base):
    """Historial para el panel admin (flujo conversacional)."""

    __tablename__ = "flow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64), default="")
    message_snippet: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
