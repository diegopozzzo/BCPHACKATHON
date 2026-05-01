"""Migraciones mínimas SQLite (ADD COLUMN) para no depender de Alembic en demo."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _cols(engine: Engine, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception:  # noqa: BLE001
        return set()


def _add_if_missing(engine: Engine, table: str, col_sql: str) -> None:
    colname = col_sql.split()[0]
    if colname in _cols(engine, table):
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_sql}"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ALTER %s %s: %s", table, colname, exc)


def ensure_sqlite_schema(engine: Engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    try:
        _add_if_missing(engine, "user_profiles", "role VARCHAR(32) DEFAULT ''")
        _add_if_missing(engine, "user_profiles", "company_name VARCHAR(255)")
        _add_if_missing(engine, "user_profiles", "hiring_summary TEXT")
        _add_if_missing(engine, "user_profiles", "updated_at TIMESTAMP")
        _add_if_missing(engine, "opportunities", "employer_wa_id VARCHAR(128)")
        _add_if_missing(engine, "opportunities", "created_at TIMESTAMP")
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema_migrate (sqlite): %s", exc)
