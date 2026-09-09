"""Engine construction, with attention to the connection pool.

Replit's managed Postgres closes a connection that has been idle for a few
minutes. Without a liveness check on checkout, the first request after an idle
period drew a dropped connection and failed, and only a reload succeeded once
the pool had healed. These tests pin the pool settings that prevent that, on
Postgres only, and confirm SQLite is left alone. The Postgres case captures the
`create_engine` arguments rather than connecting, so it needs no driver.
"""

from __future__ import annotations

from ripple.db import session as db_session


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEngine:
    def __init__(self, name: str) -> None:
        self.dialect = _FakeDialect(name)


def _capture(monkeypatch, url: str, dialect_name: str) -> dict:
    """Record the keyword arguments create_db_engine passes to create_engine."""
    seen: dict = {}

    def fake_create_engine(resolved: str, **kwargs):
        seen["resolved"] = resolved
        seen.update(kwargs)
        return _FakeEngine(dialect_name)

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)
    db_session.create_db_engine(url)
    return seen


class TestPostgresPool:
    def test_a_postgres_engine_tests_a_connection_before_use(self, monkeypatch):
        seen = _capture(
            monkeypatch, "postgresql+psycopg://u:p@host/db", "postgresql"
        )
        assert seen.get("pool_pre_ping") is True

    def test_a_postgres_engine_recycles_before_the_server_idle_cutoff(
        self, monkeypatch
    ):
        seen = _capture(
            monkeypatch, "postgresql+psycopg://u:p@host/db", "postgresql"
        )
        assert seen.get("pool_recycle") == 300


class TestSqlitePool:
    def test_sqlite_is_left_with_its_default_pool(self):
        engine = db_session.create_db_engine("sqlite+pysqlite:///:memory:")
        assert engine.pool._pre_ping is False
