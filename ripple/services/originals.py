"""Retention of an imported script's original file.

Ripple parses an upload into scenes and units and, before this, kept nothing
of the file itself. Keeping the original lets a reader download exactly what
was imported and re-upload it later. The copy is written to the local data
directory and, on Replit, mirrored to Object Storage as well: a Replit
Autoscale deployment has an ephemeral filesystem, so the local copy does not
survive a redeploy, and the bucket is the durable copy a download falls back
to. Retention is best-effort and additive: a storage failure never fails the
import, which has already succeeded by the time this runs.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ripple.services import replit_store

logger = logging.getLogger(__name__)

#: Where originals are written locally, under `data/`, which is runtime state
#: kept out of version control. A module attribute so tests can redirect it.
ORIGINALS_DIR = Path("data/originals")

#: The Object Storage key prefix a mirrored original is stored under.
_KEY_PREFIX = "originals"


def _key(script_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}/{script_id}"


def _local_path(script_id: uuid.UUID) -> Path:
    return ORIGINALS_DIR / str(script_id)


def keep_original(script_id: uuid.UUID, data: bytes) -> None:
    """Retain an imported file's bytes: local always, Replit when present.

    Best-effort. A local write failure or a Replit failure is logged and
    swallowed, because retention is additive and must never turn a successful
    import into a failed one.
    """
    try:
        ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        _local_path(script_id).write_bytes(data)
    except OSError:
        logger.exception("could not write original for %s locally", script_id)
    replit_store.put_bytes(_key(script_id), data)


def read_original(script_id: uuid.UUID) -> bytes | None:
    """The retained original's bytes, or None when none was kept.

    Local first, then the durable Replit copy: after a deployment's filesystem
    is recycled the local copy is gone and the bucket still holds it.
    """
    path = _local_path(script_id)
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            logger.exception("could not read local original for %s", script_id)
    return replit_store.get_bytes(_key(script_id))


def has_original(script_id: uuid.UUID) -> bool:
    """Whether a retained original exists in either store.

    The local check is a filesystem stat and costs nothing; the Replit check
    runs only when there is no local copy, and only reaches the network inside
    a Replit App.
    """
    if _local_path(script_id).exists():
        return True
    return replit_store.exists(_key(script_id))


def forget_original(script_id: uuid.UUID) -> None:
    """Drop a retained original from both stores when its script is deleted.

    Best-effort, so a storage hiccup never blocks a deletion; without it a
    deleted script would leave its file orphaned in the directory and the
    bucket.
    """
    try:
        _local_path(script_id).unlink(missing_ok=True)
    except OSError:
        logger.exception("could not remove local original for %s", script_id)
    replit_store.delete(_key(script_id))
