"""
Application metadata database — SQLAlchemy 2.0 sync engine.

Stores app-level state that must survive process restarts:
  - persisted DB connections (so they survive restarts)
  - conversations + messages (ChatGPT-style chat history)
  - analyses payloads (charts, tables, steps)
  - user preferences (model, provider, active conversation/connection)

The user's *data* databases (the ones they query with SQL) are managed
separately by ``app.db.manager.connection_manager``. This module is for
*our* application's internal state.

Default backend is SQLite at ``backend/data/app.db`` for zero-ops dev,
and is portable to Postgres in production by setting ``APP_DB_URL``.
WAL mode + sane pragmas keep concurrent reads/writes fast on SQLite.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


_DB_URL = settings.app_db_url
_IS_SQLITE = _DB_URL.startswith("sqlite")


engine: Engine = create_engine(
    _DB_URL,
    future=True,
    pool_pre_ping=True,
    echo=settings.app_db_echo,
    # SQLite needs check_same_thread=False because FastAPI may pass the
    # connection between threadpool workers.
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    """Enable WAL + sane pragmas on every SQLite connection.

    WAL mode lets readers and writers work concurrently, which we need
    because the FastAPI threadpool can run queries in parallel while a
    streaming agent is mid-flight.
    """
    if not _IS_SQLITE:
        return
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Declarative base for all app metadata models."""


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on clean exit, rollback on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables. Called from FastAPI lifespan on startup.

    ``create_all`` is idempotent — safe to call on every boot.
    """
    # Import models so they register with Base.metadata before create_all.
    from app.db import models  # noqa: F401  (side-effect import)

    Base.metadata.create_all(bind=engine)
