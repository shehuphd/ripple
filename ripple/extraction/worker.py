"""Server-side extraction, so a build outlives the page that started it.

The browser used to drive extraction, posting once per scene. That works, and
the per-scene endpoint remains, but it ties a long build to an open tab: close
it and the run stops mid-way. A worker thread drains the run instead, and the
page polls progress, so leaving the screen costs nothing.

One thread per run, registered here so a second request cannot start a second
drain over the same jobs. The claim itself is still the lock, so even a double
start could not bill a scene twice; the registry keeps the threads honest.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import sessionmaker

from ripple.extraction.service import claim_next_scene, extract_scene
from ripple.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_threads: dict[str, threading.Thread] = {}
_guard = threading.Lock()


def is_running(run_id) -> bool:
    """True while a worker is draining this run."""
    with _guard:
        thread = _threads.get(str(run_id))
        return thread is not None and thread.is_alive()


def start(sessions: sessionmaker, provider: LLMProvider, run_id) -> bool:
    """Drain this run in the background. False when one is already draining.

    The provider is resolved by the caller, in the request that starts the
    run, so the worker calls whatever that request would have called.
    """
    key = str(run_id)
    with _guard:
        existing = _threads.get(key)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=_drain,
            # The run id goes through as it came in: the registry keys on its
            # string form, but the database columns want the id's own type.
            args=(sessions, provider, run_id),
            name=f"extract-{key[:8]}",
            daemon=True,
        )
        _threads[key] = thread
    thread.start()
    return True


def _drain(sessions: sessionmaker, provider: LLMProvider, run_id) -> None:
    """Take scenes until none are pending.

    A session per scene, committed as it lands, so a build that stops for any
    reason keeps every scene before it. Cancelling deletes the pending rows,
    so the next claim comes back empty and the loop ends on its own.
    """
    logger.info("background extraction started for run %s", run_id)
    scenes = 0
    try:
        while True:
            with sessions() as session:
                job = claim_next_scene(session, run_id)
                if job is None:
                    session.commit()
                    break
                try:
                    extract_scene(session, job, provider)
                    session.commit()
                    scenes += 1
                except Exception:
                    # extract_scene records its own failures and returns, so
                    # reaching here means something outside that contract
                    # broke. The claim rolls back with it, leaving the scene
                    # for a later run, and the loop stops rather than spins.
                    session.rollback()
                    logger.exception("background extraction failed on a scene")
                    break
    finally:
        with _guard:
            _threads.pop(str(run_id), None)
        logger.info("background extraction ended for run %s (%d scenes)", run_id, scenes)
