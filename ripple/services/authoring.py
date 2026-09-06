"""Writing a script inside Ripple, not only importing one.

Creation, renaming, line composition, and export. The boundary with the
judgement engine is deliberate: a line no fact cites saves directly, as an
accepted `direct_save` change set with undo, while a line the graph cites
goes through See ripple so the removal or change is judged. Structure
(inserting and deleting lines) follows the scene-insertion precedent: a
version bump and a trace, no proposal, and the changed scene's next
extraction picks the new text up through the content hash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from traceact import ActionTrace

from ripple.adapters.base import SCENE_HEADING, SHOT_PREFIX, parse_character_cue
from ripple.db.models import (
    Assertion,
    EntityAttribute,
    Import,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.services import changeset
from ripple.services.changeset import InvalidOperation
from ripple.tracing import ensure_configured

MAX_TITLE_CHARS = 200
_MAX_CUE_CHARS = 40
_TRANSITION = re.compile(r"^[A-Z0-9 .'-]+TO:$")
_FORCED_HEADING = re.compile(r"^\.[A-Za-z]")

# Far above any scene's unit count, so the two-phase index shift can never
# collide with a live sequence_index.
_SHIFT = 1_000_000


def _id(value) -> UUID:
    """An id however the caller spells it: UUID in code, string over HTTP."""
    return value if isinstance(value, UUID) else UUID(str(value))


@dataclass
class ComposedUnit:
    """One line written in the reader, typed and positioned."""

    unit_id: str
    unit_type: str
    # The words as stored, with any element marker consumed.
    text: str
    speaker_name: str | None
    sequence_index: int
    script_version: int


def create_script(session, title: str) -> Script:
    """Create an empty script to write into.

    It is born accepted with nothing to review: there is no source document,
    so there is nothing an import could have misread. An Import row records
    the provenance the same way an upload's does, so the library's format
    column and the audit trail read the same for both.
    """
    ensure_configured()
    title = _clean_title(title)
    with ActionTrace.start(action="script.create", kind="change") as trace:
        trace.input({"title": title})
        script = Script(
            title=title,
            import_status="accepted",
            graph_status="not_analysed",
            origin="upload",
            current_version=1,
        )
        session.add(script)
        session.flush()
        session.add(
            Import(
                script_id=script.id,
                detected_format="fountain",
                adapter_name="authored",
                source_name="Written in Ripple",
                content_hash="",
                outcome="accepted",
                warnings_json=[],
            )
        )
        session.flush()
        trace.output({"script_id": str(script.id)})
        return script


def rename_script(session, script_id, title: str) -> Script:
    """Change a script's title. The id, and every link, stays."""
    ensure_configured()
    title = _clean_title(title)
    script = session.get(Script, _id(script_id))
    if script is None:
        raise ValueError(f"no script with id {script_id}")
    with ActionTrace.start(action="script.rename", kind="change") as trace:
        trace.input({"script_id": str(script_id), "from": script.title, "to": title})
        script.title = title
        session.flush()
    return script


def save_unit_text(session, unit_id, text: str) -> dict:
    """Save an edit directly, as an accepted change set with undo.

    Only for a line the graph does not cite: an edit to a line that is the
    source of an active fact or attribute must go through See ripple, where
    the change is judged against what the line supports. That keeps direct
    save an authoring tool rather than a door around the judgement engine.
    """
    ensure_configured()
    text = " ".join(text.split())
    if not text:
        raise InvalidOperation("A line cannot be saved empty. Delete it instead.")
    unit = session.get(ScriptUnit, _id(unit_id))
    if unit is None:
        raise ValueError(f"no unit with id {unit_id}")
    cited = _facts_citing(session, unit)
    if cited:
        raise InvalidOperation(
            f"This line is the source of {cited} fact(s). "
            "Use See ripple so the change is judged."
        )
    if text == unit.current_text:
        return {"change_set_id": None, "script_version": None}
    with ActionTrace.start(action="unit.direct_save", kind="change") as trace:
        trace.input({"unit_id": str(unit_id)})
        proposal = changeset.create_proposal(
            session, unit.id, text, [], kind="direct_save"
        )
        changeset.accept(session, proposal.id)
        script = session.get(Script, session.get(Scene, unit.scene_id).script_id)
        trace.output({"change_set": str(proposal.id)})
        return {
            "change_set_id": str(proposal.id),
            "script_version": script.current_version,
        }


WRITABLE_TYPES = (
    "action",
    "character",
    "dialogue",
    "parenthetical",
    "transition",
    "shot",
    "note",
)


def insert_unit(
    session, scene_id, after_unit_id, text: str, unit_type: str | None = None
) -> ComposedUnit:
    """Write one new line into a scene, typed by what it says.

    The type comes from the same conventions the importers read: a short
    all-caps name is a cue, a bracketed line under a cue is a parenthetical,
    a line under a cue or a parenthetical is dialogue, an all-caps line
    ending TO: is a transition, and anything else is action. Fountain's
    force markers override the guess (@NAME a cue, !text action, >text a
    transition), and `unit_type` overrides everything, which is what the
    reader's Tab sends when the writer corrects the type by hand. A line
    that reads as a scene heading is refused, because a new scene is
    inserted with the scene control, not typed into the middle of one.
    """
    ensure_configured()
    text = " ".join(text.split())
    if not text:
        raise InvalidOperation("A new line needs some text.")
    scene = session.get(Scene, _id(scene_id))
    if scene is None:
        raise ValueError(f"no scene with id {scene_id}")
    if scene.omitted:
        raise InvalidOperation("This scene is omitted. Restore it to write in it.")
    script = session.get(Script, scene.script_id)

    after = None
    if after_unit_id is not None:
        after = session.get(ScriptUnit, _id(after_unit_id))
        if after is None or after.scene_id != scene.id:
            raise InvalidOperation("The line to insert after is not in this scene.")

    if after is None:
        # Writing "at the top" still reads under the scene's own heading
        # line, when the scene carries one as a unit.
        first = session.scalar(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene.id)
            .order_by(ScriptUnit.sequence_index)
            .limit(1)
        )
        if first is not None and first.unit_type == "scene_heading":
            after = first

    if unit_type is not None:
        if unit_type not in WRITABLE_TYPES:
            raise InvalidOperation(
                f"A written line is one of: {', '.join(WRITABLE_TYPES)}."
            )
        text, _ = _unforced(text)
        speaker = None
        if unit_type == "character":
            text = text.upper()
            parsed = parse_character_cue(text)
            speaker = parsed[0] if parsed else text
        elif unit_type in ("dialogue", "parenthetical"):
            speaker = _speaker_above(session, scene, after)
    else:
        text, unit_type, speaker = _classified(session, scene, text, after)
    if unit_type == "parenthetical":
        text = _bracketed(text)
    with ActionTrace.start(action="unit.insert", kind="change") as trace:
        trace.input({"scene_id": str(scene_id), "type": unit_type})
        insert_index = after.sequence_index + 1 if after is not None else 0
        session.execute(
            update(ScriptUnit)
            .where(
                ScriptUnit.scene_id == scene.id,
                ScriptUnit.sequence_index >= insert_index,
            )
            .values(sequence_index=ScriptUnit.sequence_index + _SHIFT)
        )
        session.execute(
            update(ScriptUnit)
            .where(
                ScriptUnit.scene_id == scene.id,
                ScriptUnit.sequence_index >= _SHIFT,
            )
            .values(sequence_index=ScriptUnit.sequence_index - _SHIFT + 1)
        )
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type=unit_type,
            sequence_index=insert_index,
            speaker_name=speaker,
            current_text=text,
            current_version=1,
            parser_confidence=1.0,
            parser_method="user_corrected",
        )
        session.add(unit)
        # A structural change moves the script version, so pending proposals
        # computed against the old shape go stale instead of applying.
        script.current_version += 1
        session.flush()
        trace.output({"unit_id": str(unit.id)})
        return ComposedUnit(
            unit_id=str(unit.id),
            unit_type=unit_type,
            text=text,
            speaker_name=speaker,
            sequence_index=insert_index,
            script_version=script.current_version,
        )


def delete_unit(session, unit_id) -> dict:
    """Remove one line from its scene.

    Refused for a scene heading (omit the scene instead) and for a line
    that is the source of an active fact or attribute: a fact cannot outlive
    the line that states it, so removing evidenced content goes through See
    ripple, where the removal is judged and the facts close with it.
    """
    ensure_configured()
    unit = session.get(ScriptUnit, _id(unit_id))
    if unit is None:
        raise ValueError(f"no unit with id {unit_id}")
    if unit.unit_type == "scene_heading":
        raise InvalidOperation("A scene heading is the scene. Omit the scene instead.")
    cited = _facts_citing(session, unit)
    if cited:
        raise InvalidOperation(
            f"This line is the source of {cited} fact(s). Edit it and use "
            "See ripple so the removal is judged, or omit the scene."
        )
    scene = session.get(Scene, unit.scene_id)
    script = session.get(Script, scene.script_id)
    with ActionTrace.start(action="unit.delete", kind="change") as trace:
        trace.input({"unit_id": str(unit_id), "type": unit.unit_type})
        # A later draft may point at this line as its predecessor; the link
        # goes rather than blocking the delete or dangling.
        session.execute(
            update(ScriptUnit)
            .where(ScriptUnit.predecessor_unit_id == unit.id)
            .values(predecessor_unit_id=None)
        )
        session.delete(unit)
        script.current_version += 1
        session.flush()
        trace.output({"script_version": script.current_version})
        return {"script_version": script.current_version}


def delete_scene(session, scene_id) -> dict:
    """Remove a scene written by mistake, with its lines.

    Only a scene the graph has nothing to say about: once a fact cites one
    of its lines, the way out is Omit, which deactivates those facts as a
    recorded change and raises the orphan findings. This is the inverse of
    writing a heading, which is what an undo of that needs.
    """
    ensure_configured()
    scene = session.get(Scene, _id(scene_id))
    if scene is None:
        raise ValueError(f"no scene with id {scene_id}")
    script = session.get(Script, scene.script_id)
    units = session.scalars(
        select(ScriptUnit).where(ScriptUnit.scene_id == scene.id)
    ).all()
    cited = sum(_facts_citing(session, unit) for unit in units)
    if cited:
        raise InvalidOperation(
            f"This scene's lines are the source of {cited} fact(s). "
            "Omit the scene instead, so the facts close with it."
        )
    with ActionTrace.start(action="scene.delete", kind="change") as trace:
        trace.input({"scene_id": str(scene.id), "units": len(units)})
        for unit in units:
            session.execute(
                update(ScriptUnit)
                .where(ScriptUnit.predecessor_unit_id == unit.id)
                .values(predecessor_unit_id=None)
            )
        session.delete(scene)
        session.flush()
        # The scenes after it close the space, so numbering stays a run.
        following = session.scalars(
            select(Scene)
            .where(
                Scene.script_id == script.id,
                Scene.sequence_index > scene.sequence_index,
            )
            .order_by(Scene.sequence_index)
        ).all()
        for step, later in enumerate(following):
            later.sequence_index = scene.sequence_index + step
        script.current_version += 1
        session.flush()
        trace.output({"script_version": script.current_version})
        return {"script_version": script.current_version}


def export_fountain(session, script_id) -> str:
    """The script as Fountain plain text, importable back as itself.

    Scene headings already in slugline form print as they are; any other
    heading prints forced with a leading period, which Fountain reads as a
    heading verbatim. Omitted scenes keep their place as a forced OMITTED
    heading, and notes travel in Fountain's double-bracket form.
    """
    script = session.get(Script, _id(script_id))
    if script is None:
        raise ValueError(f"no script with id {script_id}")
    scenes = session.scalars(
        select(Scene).where(Scene.script_id == script_id).order_by(Scene.sequence_index)
    ).all()

    blocks: list[str] = [f"Title: {script.title}"]
    for scene in scenes:
        units = session.scalars(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene.id)
            .order_by(ScriptUnit.sequence_index)
        ).all()
        opened = False
        pending_cue: list[str] = []
        for unit in units:
            text = unit.current_text
            if unit.unit_type == "scene_heading":
                blocks.append(_heading_line(text))
                opened = True
                continue
            if not opened:
                blocks.append(_heading_line(scene.heading or "SCENE"))
                opened = True
            if unit.unit_type == "character":
                pending_cue = [text.upper()]
            elif unit.unit_type in ("dialogue", "parenthetical"):
                line = (
                    f"({text})"
                    if (unit.unit_type == "parenthetical" and not text.startswith("("))
                    else text
                )
                if pending_cue:
                    pending_cue.append(line)
                else:
                    blocks.append(line)
            else:
                if pending_cue:
                    blocks.append("\n".join(pending_cue))
                    pending_cue = []
                if unit.unit_type == "transition":
                    blocks.append(text if _TRANSITION.match(text) else f"> {text}")
                elif unit.unit_type == "note":
                    blocks.append(f"[[{text}]]")
                else:  # action, shot
                    blocks.append(text)
        if pending_cue:
            blocks.append("\n".join(pending_cue))
        if not opened:
            blocks.append(_heading_line(scene.heading or "SCENE"))
        if scene.omitted and units == []:
            blocks.append("OMITTED")
    return "\n\n".join(blocks) + "\n"


def _heading_line(heading: str) -> str:
    text = heading.strip()
    return text if SCENE_HEADING.match(text) else f".{text}"


def _clean_title(title: str) -> str:
    title = " ".join(title.split())
    if not title:
        raise InvalidOperation("A script needs a title.")
    if len(title) > MAX_TITLE_CHARS:
        raise InvalidOperation(f"A title runs to {MAX_TITLE_CHARS} characters at most.")
    return title


def _facts_citing(session, unit: ScriptUnit) -> int:
    """How many active facts and attributes cite the unit as their source."""
    assertions = session.scalars(
        select(Assertion.id).where(
            Assertion.source_unit_id == unit.id, Assertion.active.is_(True)
        )
    ).all()
    attributes = session.scalars(
        select(EntityAttribute.id).where(
            EntityAttribute.source_unit_id == unit.id,
            EntityAttribute.active.is_(True),
        )
    ).all()
    return len(assertions) + len(attributes)


def _classify(text: str, after: ScriptUnit | None) -> tuple[str, str | None]:
    """Type a typed line by the conventions the importers read."""
    text, forced = _unforced(text)
    if forced is not None:
        if forced == "character":
            parsed = parse_character_cue(text.upper())
            return "character", parsed[0] if parsed else text.upper()
        return forced, None
    if SCENE_HEADING.match(text) or _FORCED_HEADING.match(text):
        raise InvalidOperation(
            "That reads as a scene heading. Insert a new scene with the "
            "scene control instead."
        )
    previous = after.unit_type if after is not None else None
    speaker = after.speaker_name if after is not None else None
    # A typed line has no blank-line signal, so the speech ends where the
    # writing stops following it: dialogue comes only directly under a cue
    # or a parenthetical, and a parenthetical can also interrupt dialogue.
    in_speech = previous in ("character", "dialogue", "parenthetical")
    opens_speech = previous in ("character", "parenthetical")
    if text.startswith("(") and in_speech:
        return "parenthetical", speaker
    if _TRANSITION.match(text):
        return "transition", None
    if text == text.upper() and SHOT_PREFIX.match(text):
        return "shot", None
    if (
        not opens_speech
        and text == text.upper()
        and len(text) <= _MAX_CUE_CHARS
        and any(ch.isalpha() for ch in text)
        and parse_character_cue(text)
    ):
        parsed = parse_character_cue(text)
        return "character", parsed[0] if parsed else text
    if opens_speech:
        return "dialogue", speaker
    return "action", None


def _unforced(text: str) -> tuple[str, str | None]:
    """Strip a leading element marker, naming the type it states.

    Explicit beats implicit: the marker says what the line is, and it is
    consumed rather than stored, so the saved text is the words alone. Six
    of the seven are Fountain's own markers, which is what keeps an export
    readable by any other tool; `"` for dialogue and `>>` for a shot fill
    the two elements Fountain leaves to position.
    """
    if text.startswith(">>"):
        return text[2:].strip(), "shot"
    if text.startswith("@"):
        return text[1:].strip(), "character"
    if text.startswith("!"):
        return text[1:].strip(), "action"
    if text.startswith('"'):
        return text[1:].strip().removesuffix('"').strip(), "dialogue"
    if text.startswith(">") and not text.endswith("<"):
        return text[1:].strip(), "transition"
    if text.startswith("[[") and text.endswith("]]"):
        return text[2:-2].strip(), "note"
    return text, None


def _speaker_above(session, scene: Scene, after: ScriptUnit | None) -> str | None:
    """The nearest cue above the insertion point: who a corrected dialogue
    line belongs to, even across an action line that interrupted the speech."""
    boundary = after.sequence_index if after is not None else -1
    rows = session.scalars(
        select(ScriptUnit)
        .where(ScriptUnit.scene_id == scene.id, ScriptUnit.sequence_index <= boundary)
        .order_by(ScriptUnit.sequence_index.desc())
    )
    for row in rows:
        if row.unit_type == "character":
            return row.speaker_name or row.current_text
        if row.unit_type in ("dialogue", "parenthetical") and row.speaker_name:
            return row.speaker_name
    return None


def _bracketed(text: str) -> str:
    """A parenthetical prints inside its brackets, whichever the writer typed."""
    body = text.strip().lstrip("(").rstrip(")").strip()
    return f"({body})" if body else text


def _classified(
    session, scene: Scene, text: str, after: ScriptUnit | None
) -> tuple[str, str, str | None]:
    """The stored words, the type, and the speaker for an unmarked line.

    A speech marked with its own quote still belongs to whoever is talking,
    so a forced dialogue or parenthetical looks up the cue above it rather
    than arriving unattributed.
    """
    unit_type, speaker = _classify(text, after)
    stripped, _ = _unforced(text)
    if unit_type == "character":
        # A cue prints in capitals, so that is how it is stored, whichever
        # case the marker was typed in.
        stripped = stripped.upper()
    elif unit_type in ("dialogue", "parenthetical") and speaker is None:
        speaker = _speaker_above(session, scene, after)
    return stripped, unit_type, speaker
