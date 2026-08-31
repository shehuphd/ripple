"""The change-set service: atomic acceptance, rejection, and undo.

Accepting a proposal runs one transaction that checks base
versions, resolves or creates entities, applies ordered operations, bumps
versions, and marks the change set accepted. Any failure rolls the whole thing
back; a stale base version marks the proposal stale rather than silently
applying it to state it was not computed against.

Undo creates an inverse change set pointing at the original and
applies it through this same path, so there is one code path that mutates
accepted state. The original becomes `reverted`, never `rejected`: rejecting
something is a decision not to apply it, and reverting is applying it and then
taking it back. Collapsing the two would lose which happened.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    ChangeOperation,
    ChangeSet,
    ChangeSetUnit,
    ContinuityFinding,
    Entity,
    EntityAttribute,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize, normalize_key
from ripple.graph.predicates import SignatureError, validate_edge
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)

# Operations that can be inverted, and what each becomes.
INVERSE = {
    "add_assertion": "remove_assertion",
    "remove_assertion": "add_assertion",
    "update_assertion": "update_assertion",
    "set_unit_text": "set_unit_text",
    "remove_entity_attribute": "set_entity_attribute",
    # Entities are never deleted as a side effect, so the
    # inverse of creating one is the idempotent create, and an entity update
    # inverts to an update with the sides swapped.
    "create_entity": "create_entity",
    "update_entity": "update_entity",
    # The payload swap does the work: before and after each state the flag.
    "set_scene_omitted": "set_scene_omitted",
    # set_entity_attribute inverts by shape: setting a fresh key inverts to a
    # removal, updating an existing one inverts to a set with the sides
    # swapped. _inverse_type below decides from the operation's before_json.
}


def _inverse_type(operation: ChangeOperation) -> str:
    """The operation that undoes this one."""
    if operation.operation_type == "set_entity_attribute":
        return (
            "set_entity_attribute" if operation.before_json else
            "remove_entity_attribute"
        )
    return INVERSE[operation.operation_type]


class StaleProposal(Exception):
    """The proposal was computed against state that has since moved.

    Raising unwinds the transaction, taking the in-session status write with
    it, so the exception carries what the web layer needs to record the
    status durably in a fresh session.
    """

    def __init__(self, message: str, change_set_id: str | None = None) -> None:
        super().__init__(message)
        self.code = "stale_proposal"
        self.message = message
        self.change_set_id = change_set_id
        self.durable_status = "stale"


class InvalidOperation(Exception):
    """An operation the schema lock does not permit.

    `change_set_id` and `durable_status` are set when the failure should
    change the proposal's recorded status; the raise rolls back any
    in-session write, so the web layer re-applies it in a fresh session.
    """

    def __init__(
        self,
        message: str,
        change_set_id: str | None = None,
        durable_status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = "invalid_operation"
        self.message = message
        self.change_set_id = change_set_id
        self.durable_status = durable_status


@dataclass(frozen=True)
class AcceptanceResult:
    """What acceptance did."""

    change_set_id: str
    script_version: int
    operations_applied: int
    entities_created: int


def _now() -> datetime:
    return datetime.now(UTC)


def create_proposal(
    session: Session,
    unit_id,
    proposed_text: str,
    operations: list[dict[str, Any]],
    kind: str = "edit",
) -> ChangeSet:
    """Record a single-unit proposal without applying any of it."""
    return create_multi_proposal(
        session, [(unit_id, proposed_text)], operations, kind=kind
    )


def create_multi_proposal(
    session: Session,
    edits: list[tuple[Any, str]],
    operations: list[dict[str, Any]],
    kind: str | None = None,
) -> ChangeSet:
    """Record a proposal over any number of units, without applying any of it.

    `edits` is (unit_id, proposed_text) pairs. Base versions are captured now,
    so acceptance can tell whether the world moved between preview and
    decision. Every edited unit gets its own `set_unit_text` operation, which
    is what lets undo restore each unit's original wording.
    """
    if not edits:
        raise ValueError("a proposal needs at least one edited unit")

    units: list[tuple[ScriptUnit, str]] = []
    script = None
    for unit_id, proposed_text in edits:
        unit = session.get(ScriptUnit, unit_id)
        if unit is None:
            raise ValueError(f"no unit with id {unit_id}")
        scene = session.get(Scene, unit.scene_id)
        unit_script = session.get(Script, scene.script_id)
        if script is None:
            script = unit_script
        elif script.id != unit_script.id:
            raise ValueError("a proposal cannot span two scripts")
        units.append((unit, proposed_text))

    change_set = ChangeSet(
        script_id=script.id,
        kind=kind or ("edit" if len(units) == 1 else "multi_unit_edit"),
        status="pending",
        base_script_version=script.current_version,
    )
    session.add(change_set)
    session.flush()

    text_operations: list[dict[str, Any]] = []
    for unit, proposed_text in units:
        session.add(
            ChangeSetUnit(
                change_set_id=change_set.id,
                script_unit_id=unit.id,
                base_unit_version=unit.current_version,
                proposed_text=proposed_text,
            )
        )
        # The text change is an operation rather than a side effect of
        # acceptance. An operation carries a before and an after, which is
        # what lets undo put the original wording back; a ChangeSetUnit holds
        # only the proposal.
        text_operations.append(
            {
                "operation_type": "set_unit_text",
                "target_type": "script_unit",
                "target_id": str(unit.id),
                "before_json": {"text": unit.current_text},
                "after_json": {"text": proposed_text},
            }
        )

    for index, operation in enumerate([*operations, *text_operations]):
        session.add(
            ChangeOperation(
                change_set_id=change_set.id,
                sequence_index=index,
                operation_type=operation["operation_type"],
                target_type=operation.get("target_type", "assertion"),
                target_id=_as_uuid(operation.get("target_id")),
                before_json=operation.get("before_json"),
                after_json=operation.get("after_json"),
            )
        )
    session.flush()
    return change_set


def accept(session: Session, change_set_id) -> AcceptanceResult:
    """Apply a proposal atomically.

    The caller commits. Every write here happens inside one transaction, so a
    failure part-way leaves the accepted graph unchanged.
    """
    ensure_configured()
    with ActionTrace.start(action="ripple.accept", kind="change") as trace:
        # Recorded before any check that can raise, so a failed acceptance
        # still leaves a trace naming what was being accepted.
        trace.input({"change_set_id": str(change_set_id)})
        change_set = _load(session, change_set_id)
        trace.set_meta("change_set", str(change_set.id))

        if change_set.status != "pending":
            raise InvalidOperation(
                f"This proposal is {change_set.status}, so it cannot be accepted."
            )

        script = session.get(Script, change_set.script_id)
        _check_versions(session, change_set, script)

        created = 0
        applied = 0
        for operation in change_set.operations:
            created += _apply(session, script, operation)
            applied += 1

        # The text itself was applied by the set_unit_text operation above.
        for unit_link in change_set.units:
            unit = session.get(ScriptUnit, unit_link.script_unit_id)
            unit.current_version += 1
            unit.updated_at = _now()

        script.current_version += 1
        change_set.status = "accepted"
        change_set.accepted_at = _now()
        session.flush()

        trace.step(f"Applied {applied} operations")
        trace.output(
            {
                "operations": applied,
                "entities_created": created,
                "script_version": script.current_version,
            }
        )
        return AcceptanceResult(
            change_set_id=str(change_set.id),
            script_version=script.current_version,
            operations_applied=applied,
            entities_created=created,
        )


def reject(session: Session, change_set_id, reason: str | None = None) -> ChangeSet:
    """Record a decision not to apply a proposal. Nothing else changes."""
    change_set = _load(session, change_set_id)
    if change_set.status != "pending":
        raise InvalidOperation(f"This proposal is already {change_set.status}.")
    change_set.status = "rejected"
    if reason:
        session.add(
            ContinuityFinding(
                change_set_id=change_set.id,
                finding_type="rejection_note",
                severity="low",
                message=reason,
                status="resolved",
                resolved_at=_now(),
            )
        )
    session.flush()
    return change_set


def undo_latest(session: Session, unit_id) -> AcceptanceResult:
    """Undo the most recent accepted change affecting a unit.

    Only the latest: undoing an older change while a
    newer one touches the same unit would apply an inverse against state that
    change never saw.
    """
    ensure_configured()
    with ActionTrace.start(action="ripple.undo", kind="change") as trace:
        trace.input({"unit_id": str(unit_id)})
        latest = session.scalar(
            select(ChangeSet)
            .join(ChangeSetUnit)
            .where(
                ChangeSetUnit.script_unit_id == unit_id,
                ChangeSet.status == "accepted",
            )
            .order_by(ChangeSet.accepted_at.desc())
            .limit(1)
        )
        if latest is None:
            raise InvalidOperation("No accepted change on this unit to undo.")

        script = session.get(Script, latest.script_id)
        inverse = ChangeSet(
            script_id=script.id,
            reverts_change_set_id=latest.id,
            kind="undo",
            status="pending",
            base_script_version=script.current_version,
        )
        session.add(inverse)
        session.flush()

        # Reversed order: the last thing applied is the first thing undone.
        for index, operation in enumerate(reversed(latest.operations)):
            session.add(
                ChangeOperation(
                    change_set_id=inverse.id,
                    sequence_index=index,
                    operation_type=_inverse_type(operation),
                    target_type=operation.target_type,
                    target_id=operation.target_id,
                    before_json=operation.after_json,
                    after_json=operation.before_json,
                )
            )

        for unit_link in latest.units:
            unit = session.get(ScriptUnit, unit_link.script_unit_id)
            session.add(
                ChangeSetUnit(
                    change_set_id=inverse.id,
                    script_unit_id=unit.id,
                    base_unit_version=unit.current_version,
                    proposed_text=_original_text(session, latest, unit_link),
                )
            )
        session.flush()

        result = accept(session, inverse.id)
        latest.status = "reverted"
        session.flush()

        trace.step("Reverted the latest accepted change")
        trace.output({"reverted": str(latest.id), "inverse": str(inverse.id)})
        return result


def _original_text(
    session: Session, change_set: ChangeSet, unit_link: ChangeSetUnit
) -> str:
    """The text a unit held before this change set was accepted.

    Taken from the change set's own `set_unit_text` operation when it has one,
    so undo restores what was there rather than re-deriving it.
    """
    for operation in change_set.operations:
        if (
            operation.operation_type == "set_unit_text"
            and operation.target_id == unit_link.script_unit_id
            and operation.before_json
        ):
            return operation.before_json.get("text", "")
    # Every proposal records a set_unit_text operation, so reaching here means
    # the change set was built by something that did not.
    raise InvalidOperation(
        "This change recorded no original text, so it cannot be undone."
    )


def _load(session: Session, change_set_id) -> ChangeSet:
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise ValueError(f"no change set with id {change_set_id}")
    return change_set


def _check_versions(session: Session, change_set: ChangeSet, script: Script) -> None:
    """Refuse a proposal computed against state that has moved.

    Marking it stale rather than rechecking continuity against the new state is
    deliberate: the diff the user approved was computed against the old graph,
    so silently applying it would apply a decision they did not make.
    """
    if change_set.base_script_version != script.current_version:
        change_set.status = "stale"
        raise StaleProposal(
            f"The script moved from v{change_set.base_script_version} to "
            f"v{script.current_version} since this preview. Regenerate it.",
            change_set_id=str(change_set.id),
        )
    for unit_link in change_set.units:
        unit = session.get(ScriptUnit, unit_link.script_unit_id)
        if unit is None:
            change_set.status = "failed"
            raise InvalidOperation(
                "A unit in this proposal no longer exists.",
                change_set_id=str(change_set.id),
                durable_status="failed",
            )
        if unit.current_version != unit_link.base_unit_version:
            change_set.status = "stale"
            raise StaleProposal(
                f"A unit moved from v{unit_link.base_unit_version} to "
                f"v{unit.current_version} since this preview. Regenerate it.",
                change_set_id=str(change_set.id),
            )


def _apply(session: Session, script: Script, operation: ChangeOperation) -> int:
    """Apply one operation. Returns how many entities it had to create."""
    handler = {
        "add_assertion": _add_assertion,
        "remove_assertion": _remove_assertion,
        "update_assertion": _update_assertion,
        "set_unit_text": _set_unit_text,
        "create_entity": _create_entity,
        "update_entity": _update_entity,
        "set_entity_attribute": _set_entity_attribute,
        "remove_entity_attribute": _remove_entity_attribute,
        "set_scene_omitted": _set_scene_omitted,
    }.get(operation.operation_type)
    if handler is None:
        raise InvalidOperation(f"Unknown operation {operation.operation_type!r}.")
    return handler(session, script, operation)


def _resolve_endpoint(
    session: Session, script: Script, kind: str, ref: str, entity_type: str | None
) -> tuple[Any, int]:
    """Turn a payload endpoint into a row id, creating an entity if needed."""
    if kind == "scene":
        return _as_uuid(ref), 0

    key = normalize(ref)
    entity = session.scalar(
        select(Entity).where(
            Entity.script_id == script.id,
            Entity.entity_type == entity_type,
            Entity.normalized_name == key,
        )
    )
    if entity is not None:
        return entity.id, 0

    entity = Entity(
        script_id=script.id,
        entity_type=entity_type,
        canonical_name=ref,
        normalized_name=key,
    )
    session.add(entity)
    session.flush()
    return entity.id, 1


def _add_assertion(session: Session, script: Script, operation: ChangeOperation) -> int:
    # Undoing a removal produces an addition that already names the row it is
    # putting back. Reactivating it needs no payload and keeps the assertion's
    # identity stable, so anything citing it still resolves.
    if operation.target_id is not None:
        existing = session.get(Assertion, operation.target_id)
        if existing is not None and not existing.active:
            existing.active = True
            session.flush()
            return 0

    return _add_from_payload(session, script, operation)


def _add_from_payload(
    session: Session, script: Script, operation: ChangeOperation
) -> int:
    """Write the edge the operation's after_json describes.

    Separate from _add_assertion because an update names the row it replaces
    in target_id; routing an update through the reactivation fast-path above
    would re-activate the row the update just deactivated and never read the
    new edge at all.
    """
    payload = operation.after_json or {}
    _validate_payload(payload)
    subject_id, created_a = _resolve_endpoint(
        session,
        script,
        payload["subject_kind"],
        payload["subject_ref"],
        payload.get("subject_entity_type"),
    )
    object_id, created_b = _resolve_endpoint(
        session,
        script,
        payload["object_kind"],
        payload["object_ref"],
        payload.get("object_entity_type"),
    )
    assertion = Assertion(
        script_id=script.id,
        subject_kind=payload["subject_kind"],
        subject_entity_id=(subject_id if payload["subject_kind"] == "entity" else None),
        subject_scene_id=subject_id if payload["subject_kind"] == "scene" else None,
        predicate=payload["predicate"],
        object_kind=payload["object_kind"],
        object_entity_id=object_id if payload["object_kind"] == "entity" else None,
        object_scene_id=object_id if payload["object_kind"] == "scene" else None,
        source_unit_id=_as_uuid(payload["source_unit_id"]),
        confidence=payload.get("confidence", 0.8),
        provenance="accepted_change",
    )

    # An edge this evidence already supports may exist and be deactivated,
    # which is the state undo restores from. Reactivating that row
    # rather than inserting a second one keeps the assertion's identity stable
    # across an undo, and avoids colliding with the dedupe index.
    duplicate = session.scalar(
        select(Assertion).where(
            Assertion.script_id == script.id,
            Assertion.dedupe_key == assertion.compute_dedupe_key(),
        )
    )
    if duplicate is not None:
        # The operation asks for this edge to exist. Reactivating a deactivated
        # row, or leaving an active one alone, both satisfy that. Inserting a
        # second row would not, and would collide with the dedupe index.
        duplicate.active = True
        duplicate.confidence = assertion.confidence
        operation.target_id = duplicate.id
        session.flush()
        return created_a + created_b

    session.add(assertion)
    session.flush()
    # An addition has no target until it is applied, because the row did not
    # exist when the proposal was written. Recording it now is what makes the
    # operation invertible: undo turns this into a removal, and a removal has
    # to name what it removes.
    operation.target_id = assertion.id
    session.flush()
    return created_a + created_b


def _remove_assertion(
    session: Session, _script: Script, operation: ChangeOperation
) -> int:
    """Deactivate rather than delete, so history survives and undo can restore.

    An entity is never removed just because one assertion was;
    garbage collection is a separate decision with its own reference checks.
    """
    if operation.target_id is None:
        raise InvalidOperation("A removal must name the assertion it removes.")
    assertion = session.get(Assertion, operation.target_id)
    if assertion is None:
        raise InvalidOperation("The assertion to remove no longer exists.")
    assertion.active = False
    session.flush()
    return 0


def _update_assertion(
    session: Session, script: Script, operation: ChangeOperation
) -> int:
    """An update is a deactivation plus an addition, so both sides stay auditable."""
    if operation.target_id is not None:
        existing = session.get(Assertion, operation.target_id)
        if existing is not None:
            existing.active = False
            session.flush()
    return _add_from_payload(session, script, operation)


def _set_unit_text(
    session: Session, _script: Script, operation: ChangeOperation
) -> int:
    unit = session.get(ScriptUnit, operation.target_id)
    if unit is None:
        raise InvalidOperation("The unit to edit no longer exists.")
    unit.current_text = (operation.after_json or {}).get("text", unit.current_text)
    session.flush()
    return 0


def _create_entity(session: Session, script: Script, operation: ChangeOperation) -> int:
    # An undo swaps the sides, so the inverse create finds its payload in
    # before_json. Entities are never deleted as a side effect, which is
    # why the inverse of a create is the same idempotent create.
    payload = operation.after_json or operation.before_json or {}
    if not payload.get("canonical_name") or not payload.get("entity_type"):
        raise InvalidOperation("A create_entity operation must name its entity.")
    _, created = _resolve_endpoint(
        session, script, "entity", payload["canonical_name"], payload["entity_type"]
    )
    return created


def _update_entity(
    session: Session, _script: Script, operation: ChangeOperation
) -> int:
    entity = session.get(Entity, operation.target_id)
    if entity is None:
        raise InvalidOperation("The entity to update no longer exists.")
    payload = operation.after_json or {}
    if "description" in payload:
        entity.description = payload["description"]
    session.flush()
    return 0


def _attribute_entity(
    session: Session, script: Script, payload: dict[str, Any]
) -> tuple[Any, int]:
    """The entity an attribute operation targets, created when it is new."""
    if payload.get("entity_id"):
        entity = session.get(Entity, _as_uuid(payload["entity_id"]))
        if entity is None:
            raise InvalidOperation("The attribute's entity no longer exists.")
        return entity.id, 0
    if not payload.get("entity_ref") or not payload.get("entity_type"):
        raise InvalidOperation("An attribute operation must name its entity.")
    return _resolve_endpoint(
        session, script, "entity", payload["entity_ref"], payload["entity_type"]
    )


def _set_entity_attribute(
    session: Session, script: Script, operation: ChangeOperation
) -> int:
    """Set one attribute value, deactivating any value the key already holds.

    History-preserving, mirroring assertions: the old row deactivates and a
    new row carries the new value, so undo can restore the old one and the
    audit trail keeps both.
    """
    payload = operation.after_json or {}
    key = normalize_key(payload.get("key", ""))
    value = payload.get("value")
    if not key or value is None:
        raise InvalidOperation("An attribute operation needs a key and a value.")
    entity_id, created = _attribute_entity(session, script, payload)

    current = session.scalar(
        select(EntityAttribute).where(
            EntityAttribute.entity_id == entity_id,
            EntityAttribute.key == key,
            EntityAttribute.active.is_(True),
        )
    )
    if current is not None:
        if current.value == value:
            operation.target_id = current.id
            session.flush()
            return created
        current.active = False

    attribute = EntityAttribute(
        entity_id=entity_id,
        key=key,
        value=value,
        source_unit_id=_as_uuid(payload.get("source_unit_id")),
        evidence_start=payload.get("evidence_start"),
        evidence_end=payload.get("evidence_end"),
        confidence=payload.get("confidence", 0.8),
        provenance="accepted_change",
    )
    session.add(attribute)
    session.flush()
    # Recorded for the same reason _add_assertion records it: undo turns this
    # into a removal, and a removal has to name what it removes.
    operation.target_id = attribute.id
    session.flush()
    return created


def _remove_entity_attribute(
    session: Session, _script: Script, operation: ChangeOperation
) -> int:
    """Deactivate an attribute value, keeping the row for history and undo.

    Undo of an update never reaches here: a set that displaced an old value
    inverts to a set of that old value. Only a set that introduced a fresh
    key inverts to this removal, naming the row the set recorded.
    """
    if operation.target_id is None:
        raise InvalidOperation("A removal must name the attribute it removes.")
    attribute = session.get(EntityAttribute, operation.target_id)
    if attribute is None:
        raise InvalidOperation("The attribute to remove no longer exists.")
    attribute.active = False
    session.flush()
    return 0


def _set_scene_omitted(
    session: Session, _script: Script, operation: ChangeOperation
) -> int:
    """Flip a scene's omitted flag. The payload states the resulting value.

    The row and its number stay, matching the production convention: a cut
    scene reads OMITTED rather than renumbering everything after it.
    """
    scene = session.get(Scene, operation.target_id)
    if scene is None:
        raise InvalidOperation("The scene to mark no longer exists.")
    payload = operation.after_json or {}
    if "omitted" not in payload:
        raise InvalidOperation("A scene marker must state the resulting state.")
    scene.omitted = bool(payload["omitted"])
    session.flush()
    return 0


def _validate_payload(payload: dict[str, Any]) -> None:
    """Check an operation payload against the schema lock before it is applied.

    The synthesizer may not create operations, but an operation reaching this
    point could still come from a stale client. Validating here means an
    invalid edge is refused before it can enter the accepted graph.
    """
    required = (
        "subject_kind",
        "subject_ref",
        "predicate",
        "object_kind",
        "object_ref",
        "source_unit_id",
    )
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise InvalidOperation(f"Operation payload is missing {', '.join(missing)}.")
    try:
        validate_edge(
            payload["predicate"],
            payload["subject_kind"],
            payload["object_kind"],
            payload.get("subject_entity_type"),
            payload.get("object_entity_type"),
        )
    except SignatureError as error:
        raise InvalidOperation(str(error)) from error


def _as_uuid(value: Any):
    """Coerce a payload identifier to a UUID, tolerating one already parsed."""
    if value is None or isinstance(value, uuid_module.UUID):
        return value
    try:
        return uuid_module.UUID(str(value))
    except ValueError as error:
        raise InvalidOperation(f"{value!r} is not a valid identifier.") from error


__all__ = [
    "AcceptanceResult",
    "InvalidOperation",
    "StaleProposal",
    "accept",
    "create_multi_proposal",
    "create_proposal",
    "reject",
    "undo_latest",
]
