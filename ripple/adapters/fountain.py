"""Fountain adapter.

Implements the subset of Fountain a production screenplay uses: title page,
scene headings with optional #N# numbers, forced-element markers, character
cues with extensions and dual dialogue, parentheticals, transitions, notes,
boneyard comments, and sections/synopses (discarded, since neither is
production content).

Character offsets in source anchors point into the decoded source text, so a
unit can always be located in the file the user uploaded.
"""

from __future__ import annotations

import logging
import re

from ripple.adapters.base import (
    SCENE_HEADING,
    SCENE_NUMBER,
    SHOT_PREFIX,
    TRANSITION,
    DetectedFormat,
    ImportRejected,
    ImportWarning,
    ParsedScene,
    ParsedUnit,
    ParserMethod,
    SourceAnchor,
    SourcePayload,
    parse_character_cue,
    split_heading,
)
from ripple.adapters.detect import detect_format

logger = logging.getLogger(__name__)

TITLE_KEY = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<value>.*)$")
BONEYARD = re.compile(r"/\*.*?\*/", re.DOTALL)
NOTE = re.compile(r"\[\[(?P<body>.*?)\]\]", re.DOTALL)
PAGE_BREAK = re.compile(r"^\s*={3,}\s*$")
SYNOPSIS = re.compile(r"^\s*=(?!=)")
SECTION = re.compile(r"^\s*#{1,6}\s+\S")
CENTRED = re.compile(r"^\s*>\s*(?P<body>.+?)\s*<\s*$")


def _blank_out(
    text: str, pattern: re.Pattern[str]
) -> tuple[str, list[tuple[int, str]]]:
    """Replace each match with spaces of equal length, returning what was removed.

    Blanking rather than deleting keeps every later character at its original
    offset, so source anchors stay accurate without a second offset map.
    """
    removed: list[tuple[int, str]] = []
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        removed.append((match.start(), match.group(0)))
        pieces.append(text[cursor : match.start()])
        # Newlines survive so line numbering is unchanged.
        pieces.append(
            "".join("\n" if character == "\n" else " " for character in match.group(0))
        )
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), removed


class FountainAdapter:
    """Parses Fountain into scenes and units."""

    name = "fountain"
    detected_format = DetectedFormat.FOUNTAIN

    def detect(self, payload: SourcePayload) -> float:
        detected, confidence = detect_format(payload)
        return confidence if detected is DetectedFormat.FOUNTAIN else 0.0

    @staticmethod
    def title(payload: SourcePayload) -> str | None:
        """The Title: value from the Fountain title page, if there is one."""
        for line in payload.text().splitlines():
            stripped = line.strip()
            if not stripped:
                break
            match = TITLE_KEY.match(stripped)
            if match and match.group("key").strip().lower() == "title":
                return match.group("value").strip() or None
        return None

    def parse(
        self, payload: SourcePayload
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        """Parse the payload into scenes plus any non-fatal findings."""
        source = payload.text()
        warnings: list[ImportWarning] = []

        body_text, boneyards = _blank_out(source, BONEYARD)
        if boneyards:
            warnings.append(
                ImportWarning(
                    code="boneyard_discarded",
                    message=f"{len(boneyards)} boneyard comment(s) were not imported.",
                )
            )

        body_text, notes = _blank_out(body_text, NOTE)
        notes_by_offset = dict(notes)

        body_start = self._skip_title_page(body_text)
        parser = _LineParser(body_text, body_start, notes_by_offset)
        scenes, parse_warnings = parser.run()
        warnings.extend(parse_warnings)

        if not scenes:
            raise ImportRejected(
                "no_scenes",
                "No scene headings were found. A Fountain screenplay needs at "
                "least one line beginning INT., EXT., EST., or a forced "
                "heading beginning with a full stop.",
            )
        return scenes, warnings

    @staticmethod
    def _skip_title_page(text: str) -> int:
        """Return the offset where the screenplay body begins.

        A Fountain title page is leading `Key: value` lines terminated by a
        blank line. An explicit `===` page break also ends it.
        """
        lines = text.splitlines(keepends=True)
        offset = 0
        saw_key = False

        for line in lines:
            stripped = line.strip()
            if PAGE_BREAK.match(line):
                return offset + len(line)
            if not stripped:
                # A blank line ends the title page only once a key was seen.
                if saw_key:
                    return offset + len(line)
                offset += len(line)
                continue
            match = TITLE_KEY.match(stripped)
            # An indented continuation belongs to the previous key.
            is_continuation = saw_key and line[:1] in (" ", "\t")
            if match and not SCENE_HEADING.match(stripped):
                saw_key = True
            elif not is_continuation:
                return offset if saw_key else 0
            offset += len(line)

        return offset if saw_key else 0


class _LineParser:
    """Walks Fountain lines, emitting scenes and units.

    Kept separate from the adapter so the state it carries (current scene, the
    previous element type, sequence counters) cannot leak between parses.
    """

    def __init__(self, text: str, start: int, notes_by_offset: dict[int, str]) -> None:
        self._text = text
        self._start = start
        self._notes = notes_by_offset
        self._scenes: list[ParsedScene] = []
        self._warnings: list[ImportWarning] = []
        self._scene: ParsedScene | None = None
        self._previous: str | None = None
        self._pending_speaker: str | None = None

    def run(self) -> tuple[list[ParsedScene], list[ImportWarning]]:
        offset = self._start
        for raw_line in self._text[self._start :].splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            self._consume(line, offset)
            offset += len(raw_line)
        self._emit_notes()
        return self._scenes, self._warnings

    def _consume(self, line: str, offset: int) -> None:
        stripped = line.strip()

        if not stripped:
            self._previous = None
            self._pending_speaker = None
            return
        if PAGE_BREAK.match(line) or SYNOPSIS.match(line) or SECTION.match(line):
            return

        if self._try_scene_heading(line, stripped, offset):
            return
        if self._try_transition(stripped, offset):
            return
        if self._try_character(stripped, offset):
            return
        if self._try_dialogue_block(stripped, offset):
            return
        self._add_action(stripped, offset, line)

    def _try_scene_heading(self, line: str, stripped: str, offset: int) -> bool:
        forced = stripped.startswith(".") and not stripped.startswith("..")
        if not forced and not SCENE_HEADING.match(stripped):
            return False

        heading = stripped[1:].strip() if forced else stripped
        number_match = SCENE_NUMBER.search(heading)
        display_number = number_match.group("number") if number_match else None
        heading = SCENE_NUMBER.sub("", heading).strip()

        int_ext, _, time_of_day = split_heading(heading)
        self._scene = ParsedScene(
            sequence_index=len(self._scenes),
            heading=heading,
            display_scene_number=display_number,
            int_ext=int_ext,
            time_of_day=time_of_day,
        )
        self._scenes.append(self._scene)
        self._add_unit(
            "scene_heading",
            heading,
            offset,
            offset + len(line),
            confidence=1.0 if not forced else 0.9,
        )
        self._previous = "scene_heading"
        return True

    def _try_transition(self, stripped: str, offset: int) -> bool:
        forced = stripped.startswith(">") and not CENTRED.match(stripped)
        if not forced and not TRANSITION.match(stripped):
            return False
        text = stripped[1:].strip() if forced else stripped
        self._add_unit("transition", text, offset, offset + len(stripped))
        self._previous = "transition"
        return True

    def _try_character(self, stripped: str, offset: int) -> bool:
        forced = stripped.startswith("@")
        candidate = stripped[1:].strip() if forced else stripped
        if not forced:
            # An unforced cue must be followed by dialogue, which the blank-line
            # reset in _consume approximates: a cue never follows a cue.
            if self._previous in ("character", "parenthetical", "dialogue"):
                return False
            parsed = parse_character_cue(candidate)
            if parsed is None:
                return False
        else:
            parsed = parse_character_cue(candidate) or (candidate, None, False)

        name, _extension, is_dual = parsed
        text = candidate.rstrip("^ ").strip()
        self._add_unit(
            "character",
            text,
            offset,
            offset + len(stripped),
            speaker=name,
            confidence=1.0 if forced else 0.9,
        )
        if is_dual:
            self._warnings.append(
                ImportWarning(
                    code="dual_dialogue",
                    message=f"{name} speaks in dual dialogue; ordering is sequential.",
                    scene_index=self._scene.sequence_index if self._scene else None,
                )
            )
        self._pending_speaker = name
        self._previous = "character"
        return True

    def _try_dialogue_block(self, stripped: str, offset: int) -> bool:
        if self._previous not in ("character", "parenthetical", "dialogue"):
            return False
        kind = (
            "parenthetical"
            if stripped.startswith("(") and stripped.endswith(")")
            else "dialogue"
        )
        self._add_unit(
            kind,
            stripped,
            offset,
            offset + len(stripped),
            speaker=self._pending_speaker,
        )
        self._previous = kind
        return True

    def _add_action(self, stripped: str, offset: int, line: str) -> None:
        forced = stripped.startswith("!")
        text = stripped[1:].strip() if forced else stripped
        centred = CENTRED.match(stripped)
        if centred:
            text = centred.group("body")
        kind = "shot" if not forced and SHOT_PREFIX.match(text) else "action"
        self._add_unit(kind, text, offset, offset + len(line))
        self._previous = kind

    def _add_unit(
        self,
        unit_type: str,
        text: str,
        start: int,
        end: int,
        *,
        speaker: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        if self._scene is None:
            self._scene = ParsedScene(
                sequence_index=0, heading="", display_scene_number=None
            )
            self._scenes.append(self._scene)
            self._warnings.append(
                ImportWarning(
                    code="content_before_first_scene",
                    message="Content appears before the first scene heading and "
                    "was placed in an unheaded opening scene.",
                    scene_index=0,
                )
            )
        from ripple.adapters.base import UnitType  # local: avoids a cycle at import

        self._scene.units.append(
            ParsedUnit(
                unit_type=UnitType(unit_type),
                sequence_index=len(self._scene.units),
                text=text,
                parser_method=ParserMethod.FOUNTAIN,
                parser_confidence=confidence,
                speaker_name=speaker,
                anchor=SourceAnchor(
                    extraction_method="fountain",
                    start_offset=start,
                    end_offset=end,
                ),
            )
        )

    def _emit_notes(self) -> None:
        """Attach Fountain notes to the scene whose span contains them."""
        if not self._notes or not self._scenes:
            return
        from ripple.adapters.base import UnitType

        for offset, raw in sorted(self._notes.items()):
            body = raw.strip("[]").strip()
            if not body:
                continue
            scene = self._scene_containing(offset)
            scene.units.append(
                ParsedUnit(
                    unit_type=UnitType.NOTE,
                    sequence_index=len(scene.units),
                    text=body,
                    parser_method=ParserMethod.FOUNTAIN,
                    anchor=SourceAnchor(
                        extraction_method="fountain",
                        start_offset=offset,
                        end_offset=offset + len(raw),
                    ),
                )
            )

    def _scene_containing(self, offset: int) -> ParsedScene:
        """The last scene whose heading starts at or before this offset."""
        chosen = self._scenes[0]
        for scene in self._scenes:
            anchor = scene.units[0].anchor if scene.units else None
            if (
                anchor
                and anchor.start_offset is not None
                and anchor.start_offset <= offset
            ):
                chosen = scene
        return chosen


def parse_scene_text(heading: str, body: str) -> ParsedScene:
    """Parse one user-typed scene into the same structure an import produces.

    The full line parser runs over the typed text, so a typed scene supports
    every element a file does: character cues, parentheticals, transitions,
    forced headings. A heading that is not in scene-heading form ("Rooftop -
    Night") is retried forced, because a person typing into a heading field
    has stated their intent. Raises ImportRejected when the text is not one
    scene: no parseable heading, or a second heading inside the body.
    """
    heading = heading.strip()
    body = body.strip()
    if not heading:
        raise ImportRejected("scene_no_heading", "A scene needs a heading.")

    def attempt(first_line: str) -> list[ParsedScene]:
        source = f"{first_line}\n\n{body}\n" if body else f"{first_line}\n"
        scenes, _ = _LineParser(source, 0, {}).run()
        return scenes

    scenes = attempt(heading)
    if not scenes or not scenes[0].heading:
        scenes = attempt(f".{heading}")
    if not scenes or not scenes[0].heading:
        raise ImportRejected(
            "scene_no_heading",
            "The heading could not be read as a scene heading.",
        )
    if len(scenes) > 1:
        raise ImportRejected(
            "scene_multiple_headings",
            "The body contains another scene heading; insert one scene at "
            "a time.",
        )
    scene = scenes[0]
    # Typed text has no source document, so no unit carries an anchor.
    for unit in scene.units:
        unit.anchor = None
    return scene
