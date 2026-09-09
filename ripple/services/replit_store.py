"""Best-effort blob storage on Replit Object Storage.

The integration is additive. Outside a Replit App the module stores nothing
and every call is a no-op, so no path through the app depends on Replit being
present or reachable. `REPL_ID` is set both in a Replit workspace and in a
deployment; without it there is no App bucket to talk to. Inside a Replit App
the SDK authenticates through Google Application Default Credentials against
the App's default bucket, with no key to configure. A missing SDK, an
unconfigured bucket, or a failed call is logged and swallowed: the copy a
deployment keeps here is a durable convenience, never a requirement.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def available() -> bool:
    """Whether the process is running inside a Replit App."""
    return bool(os.environ.get("REPL_ID"))


def _client():
    """A Replit Object Storage client, or None when one cannot be made.

    The SDK is imported here, not at module load, so the dependency is only
    needed where the code runs on Replit; a checkout without it imports and
    runs everywhere else unchanged.
    """
    if not available():
        return None
    try:
        from replit.object_storage import Client
    except ImportError:
        logger.info("replit-object-storage not installed; storing nothing there")
        return None
    try:
        return Client()
    except Exception:
        # No default bucket configured for the App, or the SDK could not
        # reach the service.
        logger.info("Replit Object Storage unavailable; storing nothing there")
        return None


def put_bytes(key: str, data: bytes) -> bool:
    """Store bytes under a key. False when there is nowhere to store them."""
    client = _client()
    if client is None:
        return False
    try:
        client.upload_from_bytes(key, data)
        return True
    except Exception:
        logger.exception("Replit Object Storage upload failed for %s", key)
        return False


def get_bytes(key: str) -> bytes | None:
    """The bytes stored under a key, or None when absent or unreachable."""
    client = _client()
    if client is None:
        return None
    try:
        return client.download_as_bytes(key)
    except Exception:
        # A missing object raises; the caller reads that as "not stored here".
        return None


def exists(key: str) -> bool:
    """Whether an object is stored under a key on Replit."""
    client = _client()
    if client is None:
        return False
    try:
        return bool(client.exists(key))
    except Exception:
        return False


def delete(key: str) -> bool:
    """Remove an object. False when there is nothing here to remove."""
    client = _client()
    if client is None:
        return False
    try:
        client.delete(key)
        return True
    except Exception:
        # Already gone, or unreachable; nothing to clean up either way.
        return False
