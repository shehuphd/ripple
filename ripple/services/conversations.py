"""Ask Ripple threads: what was proposed, what was decided, and what it cost.

A conversation is a record, not a source of truth. Every turn stores what the
page drew (tool chips, the coverage list, the ripple card, the spend line) so
reopening a thread costs nothing and shows what was said at the time rather
than what the graph would say now.

The provider-neutral message history rides on the same row, so the next turn
continues the conversation the model was actually having.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ripple.db.models import (
    ChangeSet,
    ContinuityFinding,
    Conversation,
    ConversationTurn,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.services import changeset
from ripple.text import when_label

logger = logging.getLogger(__name__)

# How long a thread's name can be in the sidebar. The name is the request's
# own opening words, cut at a word boundary: naming a thread with a model call
# would bill a call for a label.
TITLE_CHARS = 48

# How many messages of history one turn carries forward. A thread can run
# long; the model needs the recent exchange, not the whole transcript, and an
# unbounded history is an unbounded input bill.
HISTORY_MESSAGES = 40


def title_for(message: str) -> str:
    """A thread name from the request's own words."""
    words = " ".join(message.split())
    if len(words) <= TITLE_CHARS:
        return words or "Untitled request"
    cut = words[:TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut or words[:TITLE_CHARS]}…"


def start(session: Session, script: Script, message: str) -> Conversation:
    """Open a thread over one script, named for the request that opened it."""
    conversation = Conversation(script_id=script.id, title=title_for(message))
    session.add(conversation)
    session.flush()
    return conversation


def add_turn(
    session: Session,
    conversation: Conversation,
    role: str,
    text: str,
    *,
    payload: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    change_set_id: str | None = None,
) -> ConversationTurn:
    """Append one message and keep the thread's ordering timestamp current."""
    turn = ConversationTurn(
        conversation_id=conversation.id,
        role=role,
        text=text,
        payload_json=payload or {},
        messages_json=(messages or [])[-HISTORY_MESSAGES:],
        # The agent hands ids around as strings; the column wants the type.
        change_set_id=(
            uuid.UUID(str(change_set_id)) if change_set_id else None
        ),
    )
    session.add(turn)
    conversation.updated_at = datetime.now(UTC)
    session.flush()
    return turn


def history_for(session: Session, conversation: Conversation) -> list[dict[str, Any]]:
    """The message history the next turn continues from."""
    latest = session.scalar(
        select(ConversationTurn)
        .where(
            ConversationTurn.conversation_id == conversation.id,
            ConversationTurn.role == "ripple",
        )
        .order_by(ConversationTurn.created_at.desc())
        .limit(1)
    )
    return list(latest.messages_json or []) if latest else []


def listing(session: Session, script_id) -> list[dict[str, Any]]:
    """Every thread over one script, most recently used first."""
    rows = session.scalars(
        select(Conversation)
        .where(Conversation.script_id == script_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        {
            "id": str(row.id),
            "title": row.title,
            "updated_at": when_label(row.updated_at),
        }
        for row in rows
    ]


def replay(session: Session, conversation: Conversation) -> list[dict[str, Any]]:
    """Every turn of one thread, as the page rendered it."""
    rows = session.scalars(
        select(ConversationTurn)
        .where(ConversationTurn.conversation_id == conversation.id)
        .order_by(ConversationTurn.created_at)
    )
    replayed = []
    for row in rows:
        # A replayed proposal must not offer a decision that was already
        # taken: the page reads the status to know whether Confirm still
        # means anything.
        status = None
        if row.change_set_id:
            change_set = session.get(ChangeSet, row.change_set_id)
            status = change_set.status if change_set else "gone"
        replayed.append(
            {
                "role": row.role,
                "text": row.text,
                "payload": row.payload_json or {},
                "change_set_id": (
                    str(row.change_set_id) if row.change_set_id else None
                ),
                "change_set_status": status,
            }
        )
    return replayed


def abandon_pending(session: Session, conversation: Conversation) -> int:
    """Reject any proposal this thread left pending.

    A pending change set the user walked away from must not sit there waiting
    to be accepted from another screen, and its preview cache must not replay
    it as a fresh suggestion later.
    """
    rejected = 0
    for turn in session.scalars(
        select(ConversationTurn).where(
            ConversationTurn.conversation_id == conversation.id,
            ConversationTurn.change_set_id.is_not(None),
        )
    ):
        try:
            change_set = changeset.reject(
                session, turn.change_set_id, "Abandoned in Ask Ripple"
            )
        except Exception:
            # Already accepted, already rejected, or gone: nothing to undo,
            # and a thread must always close.
            continue
        if change_set.status == "rejected":
            rejected += 1
    return rejected


# What an operation did to a scene, in the words the summary table uses. The
# graph operations are grouped under the text edit that caused them, since one
# rewritten line is what the user did and the assertions are its consequence.
_APPLIED_LABELS = {
    "set_unit_text": "Text rewritten",
    "set_scene_omitted": "Scene omitted",
    "create_entity": "Entity created",
    "update_entity": "Entity updated",
    "set_entity_attribute": "Attribute set",
    "remove_entity_attribute": "Attribute removed",
}


def applied_summary(
    session: Session, script: Script, change_set: ChangeSet
) -> dict[str, Any]:
    """What an accepted proposal did, composed from the operations themselves.

    Every figure here comes from the change set, so the summary says what was
    applied rather than what a model remembers proposing.
    """
    scene_of_unit = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.scene_id).join(Scene).where(
                Scene.script_id == script.id
            )
        ).all()
    )
    scene_numbers = {
        row.id: row.label
        for row in session.scalars(select(Scene).where(Scene.script_id == script.id))
    }

    by_scene: dict[str, dict[str, Any]] = {}
    units_touched: set[Any] = set()
    for operation in change_set.operations:
        payload = operation.after_json or operation.before_json or {}
        scene_id = None
        if operation.operation_type == "set_unit_text":
            scene_id = scene_of_unit.get(operation.target_id)
            units_touched.add(operation.target_id)
        elif operation.operation_type == "set_scene_omitted":
            scene_id = operation.target_id
        elif payload.get("source_unit_id"):
            try:
                import uuid as _uuid

                scene_id = scene_of_unit.get(
                    _uuid.UUID(str(payload["source_unit_id"]))
                )
            except (ValueError, TypeError):
                scene_id = None
        number = scene_numbers.get(scene_id, "—")
        slot = by_scene.setdefault(
            number, {"scene": number, "actions": set(), "predicates": set()}
        )
        label = _APPLIED_LABELS.get(operation.operation_type)
        if label:
            slot["actions"].add(label)
        if payload.get("predicate"):
            slot["predicates"].add(str(payload["predicate"]).upper())

    rows = [
        {
            "scene": slot["scene"],
            "applied": ", ".join(sorted(slot["actions"])) or "Graph updated",
            "predicate": ", ".join(sorted(slot["predicates"])),
        }
        for slot in by_scene.values()
    ]
    rows.sort(key=lambda row: (len(row["scene"]), row["scene"]))

    findings = [
        {
            "severity": row.severity,
            "type": row.finding_type,
            "message": row.message,
        }
        for row in session.scalars(
            select(ContinuityFinding).where(
                ContinuityFinding.change_set_id == change_set.id,
                ContinuityFinding.status == "open",
            )
        )
    ]

    operations = len(change_set.operations)
    scenes = [row["scene"] for row in rows if row["scene"] != "—"]
    where = ", ".join(scenes)
    text = (
        f"Applied. {operations} graph operation(s) across "
        f"{len(units_touched) or len(rows)} line(s)"
    )
    text += f" in scene {where}." if scenes else "."
    if findings:
        text += (
            f" {len(findings)} continuity finding(s) remain open; they are "
            "listed below and in the reader."
        )
    return {
        "text": text,
        "rows": rows,
        "operations": operations,
        "units": len(units_touched),
        "findings": findings,
        "version_to": script.current_version,
        "version_from": max(script.current_version - 1, 0),
    }
