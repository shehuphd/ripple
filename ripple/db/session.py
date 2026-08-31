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
    """Create every table. Migrations replace this once the schema settles.

    The rebuild runs first: a database holding a half-renamed model_calls
    still owns that table's indexes, and creating the missing table before
    finishing the rebuild would collide with them.
    """
    _widen_model_call_outcomes(engine)
    Base.metadata.create_all(engine)


def _widen_model_call_outcomes(engine: Engine) -> None:
    """Rebuild `model_calls` in a database that predates the newer outcomes.

    SQLite bakes CHECK constraints into the table's own DDL, so adding
    "cached" and "incomplete" to the vocabulary never reaches an existing
    database and the first such write would fail its constraint. The columns
    are unchanged, so the rebuild is a straight copy. Postgres deployments
    get fresh schemas and are not touched here.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:

        def table_sql(name: str) -> str | None:
            row = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return row[0] if row else None

        # pysqlite autocommits DDL, so an interrupted rebuild persists
        # half-done: the leftover model_calls_old is the marker, and the
        # rebuild resumes from wherever it stopped rather than starting over.
        has_leftover = table_sql("model_calls_old") is not None
        current = table_sql("model_calls")
        needs_widening = current is not None and "'cached'" not in current
        if not has_leftover and not needs_widening:
            if current is not None:
                _ensure_model_call_indexes(connection)
                connection.commit()
            return

        logger.info("widening the model_calls outcome vocabulary")
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if not has_leftover:
            connection.exec_driver_sql(
                "ALTER TABLE model_calls RENAME TO model_calls_old"
            )
        elif needs_widening:
            # A half-run stopped after the rename and before the new table:
            # the narrow table under the original name is the stale one.
            connection.exec_driver_sql("DROP TABLE model_calls")

        # A rename carries the table's named indexes with it, and they keep
        # their names, so the new table's indexes would collide with them.
        for (index_name,) in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='model_calls_old' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            connection.exec_driver_sql(f'DROP INDEX "{index_name}"')

        if table_sql("model_calls") is None:
            Base.metadata.tables["model_calls"].create(connection)
        columns = ", ".join(
            column.name for column in Base.metadata.tables["model_calls"].columns
        )
        # OR IGNORE, so resuming after a partial copy never duplicates a row.
        connection.exec_driver_sql(
            f"INSERT OR IGNORE INTO model_calls ({columns}) "
            f"SELECT {columns} FROM model_calls_old"
        )
        connection.exec_driver_sql("DROP TABLE model_calls_old")
        _ensure_model_call_indexes(connection)
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()


def _ensure_model_call_indexes(connection) -> None:
    """Recreate any missing model_calls index.

    A rebuild that stopped between creating the table and its indexes leaves
    the table unindexed, and metadata.create_all skips a table that already
    exists, indexes included.
    """
    for index in Base.metadata.tables["model_calls"].indexes:
        index.create(connection, checkfirst=True)


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
