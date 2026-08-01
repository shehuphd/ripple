"""Engine and session construction.

PostgreSQL in the deployed Replit application, SQLite locally and in tests,
through the same models. ERD section 1.

SQLite does not enforce foreign keys unless asked per connection, so the
pragma below is the difference between tests that prove referential integrity
and tests that only appear to.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ripple.db.models import Base

logger = logging.getLogger(__name__)

DEFAULT_URL = "sqlite+pysqlite:///data/ripple.db"
# Short, so a locked database fails a startup probe rather than hanging it.
SQLITE_TIMEOUT_SECONDS = 0.5


def database_url() -> str:
    """Resolve the database URL from the environment.

    Replit supplies DATABASE_URL for its included PostgreSQL. The old
    postgres:// scheme is rewritten because SQLAlchemy 2 rejects it.
    """
    url = os.environ.get("DATABASE_URL", "").strip() or DEFAULT_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def create_db_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Build an engine and enforce foreign keys on SQLite connections."""
    resolved = url or database_url()
    kwargs: dict = {"echo": echo, "future": True}

    if resolved.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": SQLITE_TIMEOUT_SECONDS}
        if ":memory:" not in resolved:
            path = resolved.split("///", 1)[-1]
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)

    engine = create_engine(resolved, **kwargs)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection, _record) -> None:
            """SQLite ignores foreign keys unless told otherwise, per connection."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_all(engine: Engine) -> None:
    """Create every table. Migrations replace this once the schema settles."""
    Base.metadata.create_all(engine)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to an engine."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A transactional scope: commit on success, roll back on any exception.

    Every write path uses this, so a partial change set cannot survive a
    failure part-way through.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
