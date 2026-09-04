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
import re
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
    _add_script_origin(engine)
    _add_scene_omitted(engine)
    _add_run_forced(engine)
    _add_lineage_columns(engine)
    _widen_change_vocabularies(engine)
    _repair_dangling_references(engine)
    Base.metadata.create_all(engine)


# Nullable columns added to databases that predate them. Each entry is
# (table, column, DDL fragment after the column name).
_ADDED_COLUMNS = (
    ("scripts", "draft_number", "INTEGER NOT NULL DEFAULT 1"),
    ("scripts", "predecessor_script_id", "CHAR(32) REFERENCES scripts (id)"),
    ("scenes", "predecessor_scene_id", "CHAR(32) REFERENCES scenes (id)"),
    ("scenes", "lineage_kind", "VARCHAR(16)"),
    (
        "script_units",
        "predecessor_unit_id",
        "CHAR(32) REFERENCES script_units (id)",
    ),
    (
        "entities",
        "predecessor_entity_id",
        "CHAR(32) REFERENCES entities (id)",
    ),
    ("continuity_findings", "payload_json", "JSON"),
    ("model_calls", "reasoning_tokens", "INTEGER"),
)


def _add_lineage_columns(engine: Engine) -> None:
    """Add the newer nullable columns to a database that predates them."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        for table, column, ddl in _ADDED_COLUMNS:
            exists = connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                )
            }
            if column in columns:
                continue
            logger.info("adding %s.%s", table, column)
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
            )
        connection.commit()


def _repair_dangling_references(engine: Engine) -> None:
    """Rebuild any table whose foreign keys reference a missing table.

    SQLite rewrites referencing tables' DDL when their target is renamed, so
    a rebuild that renamed a parent aside and dropped it leaves children
    pointing at a table that no longer exists; with foreign keys on, every
    later insert into such a child fails. Scanning for the damage and
    rebuilding the child in place (same copy-out pattern, no rename) makes a
    startup heal it, whichever migration caused it.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        existing = {name for name, _ in rows}
        reference = re.compile(r'REFERENCES\s+"?(\w+)"?', re.IGNORECASE)
        for name, sql in rows:
            if name not in Base.metadata.tables or not sql:
                continue
            missing = [
                target
                for target in reference.findall(sql)
                if target not in existing
            ]
            if not missing:
                continue
            logger.warning(
                "rebuilding %s: its foreign keys reference missing %s",
                name,
                ", ".join(sorted(set(missing))),
            )
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            bag = f"{name}_repair"
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {bag}")
            connection.exec_driver_sql(
                f"CREATE TABLE {bag} AS SELECT * FROM {name}"
            )
            connection.exec_driver_sql(f"DROP TABLE {name}")
            Base.metadata.tables[name].create(connection)
            columns = ", ".join(
                column.name for column in Base.metadata.tables[name].columns
            )
            connection.exec_driver_sql(
                f"INSERT OR IGNORE INTO {name} ({columns}) "
                f"SELECT {columns} FROM {bag}"
            )
            connection.exec_driver_sql(f"DROP TABLE {bag}")
            for index in Base.metadata.tables[name].indexes:
                index.create(connection, checkfirst=True)
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _add_scene_omitted(engine: Engine) -> None:
    """Add `scenes.omitted` to a database that predates it."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        table_exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenes'"
        ).fetchone()
        if not table_exists:
            return
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(scenes)")
        }
        if "omitted" in columns:
            return
        logger.info("adding scenes.omitted")
        connection.exec_driver_sql(
            "ALTER TABLE scenes ADD COLUMN omitted BOOLEAN NOT NULL DEFAULT 0"
        )
        connection.commit()


def _add_run_forced(engine: Engine) -> None:
    """Add `extraction_runs.forced` to a database that predates it."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        table_exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='extraction_runs'"
        ).fetchone()
        if not table_exists:
            return
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(extraction_runs)")
        }
        if "forced" in columns:
            return
        logger.info("adding extraction_runs.forced")
        connection.exec_driver_sql(
            "ALTER TABLE extraction_runs ADD COLUMN forced BOOLEAN NOT NULL DEFAULT 0"
        )
        connection.commit()


def _widen_change_vocabularies(engine: Engine) -> None:
    """Rebuild the change tables in a database that predates scene omission.

    SQLite bakes CHECK constraints into a table's DDL, so the added
    "omit_scene" kind and "set_scene_omitted" operation never reach an
    existing database without a rebuild; the first such write would fail its
    constraint. Columns are unchanged in both tables, so each rebuild is a
    straight copy, resume-safe the same way the model_calls rebuild is.
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

        for table, needle in (
            # The needle is always the NEWEST member, so a database widened for an
            # earlier vocabulary is widened again for this one.
            ("change_sets", "'merge_entities'"),
            ("change_operations", "'set_scene_omitted'"),
        ):
            has_leftover = table_sql(f"{table}_old") is not None
            current = table_sql(table)
            needs_widening = current is not None and needle not in current
            if not has_leftover and not needs_widening:
                if current is not None:
                    for index in Base.metadata.tables[table].indexes:
                        index.create(connection, checkfirst=True)
                    connection.commit()
                continue

            logger.info("widening the %s vocabulary", table)
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            # Never RENAME here: SQLite rewrites other tables' foreign keys
            # to follow a renamed table, and the children of these tables
            # (findings, reports, audited calls) would end up referencing the
            # dropped _old copy. The _old table is a plain data bag created by
            # copy, so the original's name never moves and the children's DDL
            # never changes.
            if not has_leftover:
                connection.exec_driver_sql(
                    f"CREATE TABLE {table}_old AS SELECT * FROM {table}"
                )
                connection.exec_driver_sql(f"DROP TABLE {table}")
            elif needs_widening:
                # A half-run stopped after copying and before recreating: the
                # narrow table under the original name is the stale one.
                connection.exec_driver_sql(f"DROP TABLE {table}")

            if table_sql(table) is None:
                Base.metadata.tables[table].create(connection)
            columns = ", ".join(
                column.name for column in Base.metadata.tables[table].columns
            )
            connection.exec_driver_sql(
                f"INSERT OR IGNORE INTO {table} ({columns}) "
                f"SELECT {columns} FROM {table}_old"
            )
            connection.exec_driver_sql(f"DROP TABLE {table}_old")
            for index in Base.metadata.tables[table].indexes:
                index.create(connection, checkfirst=True)
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _add_script_origin(engine: Engine) -> None:
    """Add `scripts.origin` to a database that predates it.

    Every pre-existing script is stamped "upload", the safe default: seeding
    never touches an upload. The demo scripts are re-marked "bundled" at
    startup by the seeder's backfill, which requires evidence (a seeded graph,
    no extraction) before trusting a title match.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        table_exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scripts'"
        ).fetchone()
        if not table_exists:
            return
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(scripts)")
        }
        if "origin" in columns:
            return
        logger.info("adding scripts.origin")
        connection.exec_driver_sql(
            "ALTER TABLE scripts ADD COLUMN origin VARCHAR(16) "
            "NOT NULL DEFAULT 'upload'"
        )
        connection.commit()


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
        # Only the columns the old table also has: a database old enough to
        # need this rebuild predates later ADD COLUMN migrations too, and
        # this rebuild runs before them, so selecting the full new column
        # list from the old table would name columns it never had.
        old_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(model_calls_old)"
            ).fetchall()
        }
        columns = ", ".join(
            column.name
            for column in Base.metadata.tables["model_calls"].columns
            if column.name in old_columns
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
