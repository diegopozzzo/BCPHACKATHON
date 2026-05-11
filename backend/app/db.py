import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if path != ":memory:":
            p = Path(path).parent
            if str(p) not in (".", ""):
                os.makedirs(p, exist_ok=True)


_settings = get_settings()
_ensure_sqlite_dir(_settings.database_url)

_engine_args: dict = {}
if _settings.database_url.startswith("sqlite"):
    _engine_args["connect_args"] = {"check_same_thread": False}
else:
    _engine_args["pool_pre_ping"] = True

engine = create_engine(
    _settings.database_url,
    **_engine_args,
)


@event.listens_for(engine, "connect")
def _sqlite_wal_pragmas(dbapi_connection, _connection_record) -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA cache_size=-32000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
