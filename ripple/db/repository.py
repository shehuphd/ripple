"""Persistence for parsed screenplays and the deletion operations.

Adapters return data; this module is the only place import results become rows.
Every function takes a session and performs no commit of its own, so a caller
can compose several writes into one transaction. `session_scope` owns the
commit and the rollback.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ripple.adapters.base import ImportResult, ParsedScene
from ripple.db.models import (
    AppConfiguration,
    Assertion,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    ExtractionRun,
    Import,
    RippleReport,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
    SourceAnchor,
    UiPreference,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletionCounts:
    """What a destructive operation removed, or would remove.

    Counts are shown before confirmation, so the same structure
    serves the preview and the result.
    """

    scripts: int = 0
    scenes: int = 0
    units: int = 0
    entities: int = 0
    assertions: int = 0
    extraction_runs: int = 0
    change_sets: int = 0
    findings: int = 0

    @property
    def total(self) -> int:
        return (
            self.scripts
            + self.scenes
            + self.units
            + self.entities
            + self.assertions
            + self.extraction_runs
            + self.change_sets
            + self.findings
        )


def _title_for(result: ImportResult) -> str:
    """The script title: the document's own, else the uploaded filename stem."""
    if result.title:
        return result.title
    stem = PurePosixPath(result.source_name).stem
    return stem.replace("-", " ").replace("_", " ").strip() or "Untitled"


def persist_import(session: Session, result: ImportResult) -> Script:
    """Write a parsed screenplay and its provenance.

    Rejected results are not persisted: there is no screenplay to store, and a
    row for a refused upload would pollute the library.
    """
    if not result.accepted:
        raise ValueError(f"cannot persist a rejected import ({result.rejection_code})")

    script = Script(
        title=_title_for(result),
        import_status=result.outcome.value,
        graph_status="not_analysed",
        current_version=1,
    )
    session.add(script)
    session.flush()

    record = Import(
        script_id=script.id,
        detected_format=result.detected_format.value,
        adapter_name=result.adapter_name,
        source_name=result.source_name,
        content_hash=result.content_hash,
        outcome=result.outcome.value,
        warnings_json=[
            {
                "code": warning.code,
                "message": warning.message,
                "scene_index": warning.scene_index,
            }
            for warning in result.warnings
        ],
    )
    session.add(record)
    session.flush()

    for parsed_scene in result.scenes:
        _persist_scene(session, script, record, parsed_scene)

    session.flush()
    logger.info(
        "persisted script %s: %d scenes, %d units",
        script.id,
        result.scene_count,
        result.unit_count,
    )
    return script


def _persist_scene(
    session: Session, script: Script, record: Import, parsed: ParsedScene
) -> Scene:
    """Write one scene, its units, and their source anchors."""
    scene = Scene(
        script_id=script.id,
        display_scene_number=parsed.display_scene_number,
        sequence_index=parsed.sequence_index,
        heading=parsed.heading,
        int_ext=parsed.int_ext,
        time_of_day=parsed.time_of_day,
        current_version=1,
    )
    session.add(scene)
    session.flush()

    for parsed_unit in parsed.units:
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type=parsed_unit.unit_type.value,
            sequence_index=parsed_unit.sequence_index,
            speaker_name=parsed_unit.speaker_name,
            current_text=parsed_unit.text,
            current_version=1,
            parser_confidence=parsed_unit.parser_confidence,
            parser_method=parsed_unit.parser_method.value,
        )
        session.add(unit)
        session.flush()

        anchor = parsed_unit.anchor
        if anchor is None:
            continue
        session.add(
            SourceAnchor(
                script_unit_id=unit.id,
                import_id=record.id,
                source_page_number=anchor.page_number,
                source_block_index=anchor.block_index,
                source_start_offset=anchor.start_offset,
                source_end_offset=anchor.end_offset,
                bounding_box_json=(
                    {
                        "x0": anchor.bounding_box[0],
                        "y0": anchor.bounding_box[1],
                        "x1": anchor.bounding_box[2],
                        "y1": anchor.bounding_box[3],
                    }
                    if anchor.bounding_box
                    else None
                ),
                extraction_method=anchor.extraction_method,
            )
        )
    return scene


def _count(session: Session, model, script_id=None) -> int:
    """Count rows of a model, optionally scoped to one script."""
    statement = select(func.count()).select_from(model)
    if script_id is not None and hasattr(model, "script_id"):
        statement = statement.where(model.script_id == script_id)
    return session.scalar(statement) or 0


def graph_labels(session: Session, script_id) -> dict:
    """Display labels for every node in one script's graph.

    Entities are labelled by canonical name; scenes by their display number.
    An unnumbered scene (an intercut sub-heading, an OMITTED slug) stays
    visibly unnumbered as "Sc —": substituting the position invents a number
    that collides with a numbered scene further along the spine.
    """
    labels: dict = {}
    for entity_id, name in session.execute(
        select(Entity.id, Entity.canonical_name).where(Entity.script_id == script_id)
    ):
        labels[entity_id] = name
    for scene_id, number in session.execute(
        select(Scene.id, Scene.display_scene_number).where(
            Scene.script_id == script_id
        )
    ):
        labels[scene_id] = f"Sc {number}" if number else "Sc —"
    return labels


def deletion_preview(session: Session, script_id=None) -> DeletionCounts:
    """What deleting one script, or all scripts, would remove."""
    scene_filter = select(Scene.id)
    if script_id is not None:
        scene_filter = scene_filter.where(Scene.script_id == script_id)
    scene_ids = list(session.scalars(scene_filter))

    units = 0
    if scene_ids:
        units = (
            session.scalar(
                select(func.count())
                .select_from(ScriptUnit)
                .where(ScriptUnit.scene_id.in_(scene_ids))
            )
            or 0
        )

    # Findings hang off change sets, so scoping to one script goes through
    # the change set's script_id rather than a column findings do not have.
    findings_query = select(func.count()).select_from(ContinuityFinding)
    if script_id is not None:
        findings_query = findings_query.join(
            ChangeSet, ContinuityFinding.change_set_id == ChangeSet.id
        ).where(ChangeSet.script_id == script_id)

    return DeletionCounts(
        scripts=1 if script_id is not None else _count(session, Script),
        scenes=len(scene_ids),
        units=units,
        entities=_count(session, Entity, script_id),
        assertions=_count(session, Assertion, script_id),
        extraction_runs=_count(session, ExtractionRun, script_id),
        change_sets=_count(session, ChangeSet, script_id),
        findings=session.scalar(findings_query) or 0,
    )


def delete_script(session: Session, script_id) -> DeletionCounts:
    """Delete one script and everything under it."""
    counts = deletion_preview(session, script_id)
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"no script with id {script_id}")
    session.delete(script)
    session.flush()
    return counts


def delete_all_scripts(session: Session) -> DeletionCounts:
    """Delete every script. Application configuration is untouched."""
    counts = deletion_preview(session)
    for script in session.scalars(select(Script)):
        session.delete(script)
    session.flush()
    return counts


def clear_all_graphs(session: Session) -> DeletionCounts:
    """Delete graph data while preserving scripts, scenes, units, and anchors.

    Query log rows survive, because a question concerns the script rather than
    one extraction run.
    """
    counts = DeletionCounts(
        entities=_count(session, Entity),
        assertions=_count(session, Assertion),
        extraction_runs=_count(session, ExtractionRun),
        change_sets=_count(session, ChangeSet),
        findings=_count(session, ContinuityFinding),
    )

    # Units reference entities as speakers; clear the link before the entities
    # go, so the delete is not order-dependent on database cascade behaviour.
    session.execute(ScriptUnit.__table__.update().values(speaker_entity_id=None))
    for model in (
        RippleReport,
        ContinuityFinding,
        ChangeSet,
        Assertion,
        SceneExtraction,
        ExtractionRun,
        EntityAlias,
        Entity,
    ):
        session.execute(delete(model))

    session.execute(Script.__table__.update().values(graph_status="not_analysed"))
    session.flush()
    # Core updates bypass the identity map, so objects already loaded in this
    # session would keep reporting their old graph_status.
    session.expire_all()
    return counts


def set_active_model(session: Session, provider_id: str, model_id: str) -> None:
    """Record the selected provider and model. Never a credential."""
    row = session.get(AppConfiguration, "active_model")
    if row is None:
        row = AppConfiguration(key="active_model")
        session.add(row)
    row.provider_id = provider_id
    row.model_id = model_id
    session.flush()


def get_active_model(session: Session) -> tuple[str | None, str | None]:
    """The selected provider and model, or (None, None) before a choice."""
    row = session.get(AppConfiguration, "active_model")
    return (row.provider_id, row.model_id) if row else (None, None)


def set_fallback_model(
    session: Session, provider_id: str | None, model_id: str | None
) -> None:
    """Record the fallback model, or clear it when either identifier is None.

    The fallback answers when the main model refuses for an availability
    reason; it is stored the same way as the active model, identifiers only.
    """
    row = session.get(AppConfiguration, "fallback_model")
    if provider_id is None or model_id is None:
        if row is not None:
            session.delete(row)
        session.flush()
        return
    if row is None:
        row = AppConfiguration(key="fallback_model")
        session.add(row)
    row.provider_id = provider_id
    row.model_id = model_id
    session.flush()


def get_fallback_model(session: Session) -> tuple[str | None, str | None]:
    """The fallback provider and model, or (None, None) when none is set."""
    row = session.get(AppConfiguration, "fallback_model")
    return (row.provider_id, row.model_id) if row else (None, None)


# Where clicking a script in the library goes. The reader is the default;
# the graph stays one click away either way.
LANDING_VIEWS = ("reader", "graph")


def get_landing_view(session: Session) -> str:
    """The chosen landing view, defaulting to the reader."""
    row = session.get(UiPreference, "landing_view")
    return row.value if row is not None and row.value in LANDING_VIEWS else "reader"


def set_landing_view(session: Session, view: str) -> None:
    """Persist the landing-view choice."""
    if view not in LANDING_VIEWS:
        raise ValueError(
            f"landing view must be one of {', '.join(LANDING_VIEWS)}, not {view!r}"
        )
    row = session.get(UiPreference, "landing_view")
    if row is None:
        session.add(UiPreference(key="landing_view", value=view))
    else:
        row.value = view
    session.flush()


# How Ask Ripple behaves. Stored as interface preferences because that is
# what they are: the agent's powers are fixed in code, and these only change
# how much it does before it stops and asks.
AGENT_TOOL_CEILINGS = (6, 12, 24, 48)

# The confidence a proposed fact must reach before Ripple offers it. The judge
# scores every proposed assertion; one scoring below the floor is dropped from
# the proposal and named on the card, so a doubtful fact is reported rather
# than slipped into an otherwise good change set. Zero offers everything.
AGENT_CONFIDENCE_FLOORS = (0.0, 0.5, 0.6, 0.7, 0.8)
AGENT_DEFAULTS = {
    "agent_draft_around_cut": "on",
    "agent_show_plan": "on",
    "agent_keep_conversations": "on",
    "agent_tool_ceiling": "12",
    "agent_confidence_floor": "0.7",
}


@dataclass(frozen=True)
class AgentSettings:
    """The Ask Ripple preferences, with the defaults already applied."""

    draft_around_cut: bool = True
    show_plan: bool = True
    keep_conversations: bool = True
    tool_ceiling: int = 12
    confidence_floor: float = 0.7


def get_agent_settings(session: Session) -> AgentSettings:
    """The stored agent preferences; a missing row means the default."""
    stored = {
        row.key: row.value
        for row in session.scalars(
            select(UiPreference).where(UiPreference.key.in_(AGENT_DEFAULTS))
        )
    }

    def flag(key: str) -> bool:
        return stored.get(key, AGENT_DEFAULTS[key]) == "on"

    default_ceiling = AGENT_DEFAULTS["agent_tool_ceiling"]
    try:
        ceiling = int(stored.get("agent_tool_ceiling", default_ceiling))
    except ValueError:
        ceiling = int(AGENT_DEFAULTS["agent_tool_ceiling"])
    if ceiling not in AGENT_TOOL_CEILINGS:
        ceiling = int(AGENT_DEFAULTS["agent_tool_ceiling"])
    try:
        floor = float(stored.get("agent_confidence_floor", "0.7"))
    except ValueError:
        floor = float(AGENT_DEFAULTS["agent_confidence_floor"])
    if floor not in AGENT_CONFIDENCE_FLOORS:
        floor = float(AGENT_DEFAULTS["agent_confidence_floor"])
    return AgentSettings(
        draft_around_cut=flag("agent_draft_around_cut"),
        show_plan=flag("agent_show_plan"),
        keep_conversations=flag("agent_keep_conversations"),
        tool_ceiling=ceiling,
        confidence_floor=floor,
    )


def set_agent_setting(session: Session, key: str, value: str) -> None:
    """Persist one agent preference, refusing a key or value it does not know."""
    if key not in AGENT_DEFAULTS:
        raise ValueError(f"no agent setting named {key!r}")
    if key == "agent_tool_ceiling":
        try:
            number = int(value)
        except ValueError:
            raise ValueError("the tool ceiling must be a whole number") from None
        if number not in AGENT_TOOL_CEILINGS:
            allowed = ", ".join(str(one) for one in AGENT_TOOL_CEILINGS)
            raise ValueError(f"the tool ceiling must be one of {allowed}")
        value = str(number)
    elif key == "agent_confidence_floor":
        try:
            floor = float(value)
        except ValueError:
            raise ValueError("the confidence floor must be a number") from None
        if floor not in AGENT_CONFIDENCE_FLOORS:
            allowed = ", ".join(f"{one:g}" for one in AGENT_CONFIDENCE_FLOORS)
            raise ValueError(f"the confidence floor must be one of {allowed}")
        value = f"{floor:g}"
    elif value not in ("on", "off"):
        raise ValueError(f"{key} is on or off, not {value!r}")

    row = session.get(UiPreference, key)
    if row is None:
        session.add(UiPreference(key=key, value=value))
    else:
        row.value = value
    session.flush()


# The lake screensaver. Decorative, per user, and stored beside the other
# interface preferences. The idle period is minutes, or "never" for a
# shortcut-only screensaver; the throttle is the least number of seconds
# between two thrown stones, which caps how much the lake can be crowded.
SCREENSAVER_IDLE_MINUTES = ("2", "5", "10", "20", "never")
SCREENSAVER_THROTTLE_SECONDS = ("1", "2", "3", "4", "5")
SCREENSAVER_DEFAULTS = {
    "screensaver_enabled": "on",
    "screensaver_idle_minutes": "5",
    "screensaver_shortcut": "ctrl+alt+Space",
    "screensaver_throttle_seconds": "3",
}

# A chord, as the browser reports it: modifiers in a fixed order, then one
# key. A bare key or a lone modifier is refused where the chord is set, so a
# single keystroke can never take the app over.
_CHORD = re.compile(
    r"^(?:(?:ctrl|alt|shift|meta)\+){2,3}[A-Za-z0-9]\w*$"
)


@dataclass(frozen=True)
class ScreensaverSettings:
    """The screensaver preferences, with the defaults already applied."""

    enabled: bool = True
    idle_minutes: str = "5"
    shortcut: str = "ctrl+alt+Space"
    throttle_seconds: int = 3


def get_screensaver_settings(session: Session) -> ScreensaverSettings:
    """The stored screensaver preferences; a missing row means the default."""
    stored = {
        row.key: row.value
        for row in session.scalars(
            select(UiPreference).where(UiPreference.key.in_(SCREENSAVER_DEFAULTS))
        )
    }

    def value(key: str) -> str:
        return stored.get(key, SCREENSAVER_DEFAULTS[key])

    idle = value("screensaver_idle_minutes")
    if idle not in SCREENSAVER_IDLE_MINUTES:
        idle = SCREENSAVER_DEFAULTS["screensaver_idle_minutes"]
    throttle = value("screensaver_throttle_seconds")
    if throttle not in SCREENSAVER_THROTTLE_SECONDS:
        throttle = SCREENSAVER_DEFAULTS["screensaver_throttle_seconds"]
    shortcut = value("screensaver_shortcut")
    if not _CHORD.match(shortcut):
        shortcut = SCREENSAVER_DEFAULTS["screensaver_shortcut"]
    return ScreensaverSettings(
        enabled=value("screensaver_enabled") == "on",
        idle_minutes=idle,
        shortcut=shortcut,
        throttle_seconds=int(throttle),
    )


def set_screensaver_setting(session: Session, key: str, value: str) -> None:
    """Persist one screensaver preference, refusing a key or value it does
    not know."""
    if key not in SCREENSAVER_DEFAULTS:
        raise ValueError(f"no screensaver setting named {key!r}")
    if key == "screensaver_idle_minutes":
        if value not in SCREENSAVER_IDLE_MINUTES:
            allowed = ", ".join(SCREENSAVER_IDLE_MINUTES)
            raise ValueError(f"the idle period must be one of {allowed}")
    elif key == "screensaver_throttle_seconds":
        if value not in SCREENSAVER_THROTTLE_SECONDS:
            allowed = ", ".join(SCREENSAVER_THROTTLE_SECONDS)
            raise ValueError(f"the throttle must be one of {allowed} seconds")
    elif key == "screensaver_shortcut":
        if not _CHORD.match(value):
            raise ValueError(
                "a shortcut needs at least two modifiers and one key, "
                "so an ordinary keystroke cannot open the screensaver"
            )
    elif value not in ("on", "off"):
        raise ValueError(f"{key} is on or off, not {value!r}")

    row = session.get(UiPreference, key)
    if row is None:
        session.add(UiPreference(key=key, value=value))
    else:
        row.value = value
    session.flush()


def _dead_model_key(provider_id: str, model_id: str) -> str:
    """A fixed-length row key: model identifiers can exceed the key column."""
    digest = hashlib.sha256(f"{provider_id}\x00{model_id}".encode()).hexdigest()[:16]
    return f"dead_model:{digest}"


def mark_model_unavailable(session: Session, provider_id: str, model_id: str) -> None:
    """Record that a live call found this model dead on the current account.

    The provider's catalog cannot say this: availability can differ per
    account, so the only trustworthy signal is the provider rejecting an
    actual call with `model_not_available`. The picker reads these marks.
    """
    key = _dead_model_key(provider_id, model_id)
    if session.get(AppConfiguration, key) is None:
        session.add(
            AppConfiguration(key=key, provider_id=provider_id, model_id=model_id)
        )
        session.flush()


def clear_model_unavailable(session: Session, provider_id: str, model_id: str) -> None:
    """Drop the mark after a call succeeds: the account regained the model."""
    row = session.get(AppConfiguration, _dead_model_key(provider_id, model_id))
    if row is not None:
        session.delete(row)
        session.flush()


def unavailable_models(session: Session, provider_id: str) -> set[str]:
    """Model identifiers a live call has found dead for this provider."""
    rows = session.scalars(
        select(AppConfiguration).where(
            AppConfiguration.key.startswith("dead_model:"),
            AppConfiguration.provider_id == provider_id,
        )
    )
    return {row.model_id for row in rows if row.model_id}


__all__ = [
    "AgentSettings",
    "DeletionCounts",
    "ScreensaverSettings",
    "clear_all_graphs",
    "clear_model_unavailable",
    "delete_all_scripts",
    "delete_script",
    "deletion_preview",
    "get_active_model",
    "get_agent_settings",
    "get_fallback_model",
    "get_landing_view",
    "get_screensaver_settings",
    "graph_labels",
    "mark_model_unavailable",
    "persist_import",
    "set_active_model",
    "set_agent_setting",
    "set_fallback_model",
    "set_landing_view",
    "set_screensaver_setting",
    "unavailable_models",
]
