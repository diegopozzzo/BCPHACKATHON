from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models import FlowEvent


def log_flow(db: Session, *, wa_id: str, state: str, role: str | None, snippet: str) -> None:
    ev = FlowEvent(
        wa_id=wa_id,
        state=state[:120],
        role=(role or "")[:64],
        message_snippet=snippet[:500],
        created_at=datetime.now(timezone.utc),
    )
    db.add(ev)
    db.commit()
