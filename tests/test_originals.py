"""Original-file retention and the Replit storage gate.

Retention writes a local copy always and mirrors to Replit Object Storage
only inside a Replit App. These tests run outside one, so they exercise the
local path and the gate's no-op behaviour; the Replit round trip is verified
in that environment.
"""

from __future__ import annotations

import uuid

import pytest

from ripple.services import originals, replit_store


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Retention pointed at a throwaway directory, off Replit."""
    monkeypatch.delenv("REPL_ID", raising=False)
    monkeypatch.setattr(originals, "ORIGINALS_DIR", tmp_path / "originals")
    return tmp_path / "originals"


class TestLocalRetention:
    def test_a_kept_original_reads_back_verbatim(self, local):
        script_id = uuid.uuid4()
        originals.keep_original(script_id, b"the original bytes")
        assert originals.read_original(script_id) == b"the original bytes"
        assert originals.has_original(script_id) is True

    def test_nothing_kept_reads_as_none(self, local):
        assert originals.read_original(uuid.uuid4()) is None
        assert originals.has_original(uuid.uuid4()) is False

    def test_forget_removes_a_kept_original(self, local):
        script_id = uuid.uuid4()
        originals.keep_original(script_id, b"bytes")
        assert originals.has_original(script_id) is True
        originals.forget_original(script_id)
        assert originals.has_original(script_id) is False
        originals.forget_original(script_id)  # nothing left, still no raise

    def test_a_local_write_failure_never_fails_the_import(self, local, monkeypatch):
        # A file where the directory should be: mkdir raises, and retention
        # swallows it rather than turning a done import into a failure.
        blocker = local.parent / "blocker"
        blocker.write_bytes(b"x")
        monkeypatch.setattr(originals, "ORIGINALS_DIR", blocker / "sub")
        script_id = uuid.uuid4()
        originals.keep_original(script_id, b"data")  # swallowed, no raise
        # Nothing was stored, and no exception escaped to fail the import.
        assert originals.read_original(script_id) is None


class TestReplitGate:
    def test_off_replit_the_store_is_a_no_op(self, monkeypatch):
        monkeypatch.delenv("REPL_ID", raising=False)
        assert replit_store.available() is False
        assert replit_store.put_bytes("k", b"v") is False
        assert replit_store.get_bytes("k") is None
        assert replit_store.exists("k") is False

    def test_on_replit_without_the_sdk_still_no_ops(self, monkeypatch):
        # REPL_ID is set but the SDK is absent here, so the guarded import
        # fails and every call no-ops instead of raising.
        monkeypatch.setenv("REPL_ID", "repl-test")
        assert replit_store.available() is True
        assert replit_store.put_bytes("k", b"v") is False
        assert replit_store.get_bytes("k") is None
        assert replit_store.exists("k") is False
