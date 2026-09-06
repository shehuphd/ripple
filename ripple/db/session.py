"""Engine and session construction.

PostgreSQL in the deployed Replit application, SQLite locally and in tests,
through the same models.

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
# A background extraction worker writes while requests read, so a write lock
# has to be waited out rather than failed on. WAL keeps readers out of the
# writer's way, leaving only writer-against-writer contention, which is brief.
SQLITE_TIMEOUT_SECONDS = 5.0


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

        file_backed = ":memory:" not in resolved

        @event.listens_for(engine, "connect")
        def _connection_pragmas(dbapi_connection, _record) -> None:
            """SQLite ignores foreign keys unless told otherwise, per connection.

            Write-ahead logging is set alongside them so a reader never blocks
            on the extraction worker's writes. It is a property of the database
            file, not the connection, and an in-memory database cannot take it.
            """
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if file_backed:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_all(engine: Engine) -> None:
    """Create every table the models declare.

    There is no migration layer: the schema is whatever the models say, and a
    database from an earlier schema is rebuilt rather than carried forward.
    """
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
