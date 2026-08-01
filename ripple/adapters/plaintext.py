"""Plain-text adapter.

A typed screenplay carries its structure in indentation: action at the left
margin, dialogue indented, character cues indented further. This adapter
measures the document's own margins rather than assuming US Letter defaults,
because a text dump can be reflowed, tab-indented, or flattened to column zero.

When a document has no usable indentation the adapter falls back to
capitalisation rules and lowers parser confidence, which pushes the import to
needs_review rather than pretending the structure is known.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

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
    UnitType,
    parse_character_cue,
    split_heading,
)
from ripple.adapters.detect import detect_format

logger = logging.getLogger(__name__)

TAB_WIDTH = 8
# Offsets from the action margin, in characters, for a 12pt Courier page.
DIALOGUE_OFFSET = 6
PARENTHETICAL_OFFSET = 12
CHARACTER_OFFSET = 18
# Below this many distinctly indented lines the document is treated as flat.
MIN_INDENTED_LINES = 4


@dataclass(frozen=True)
class _Margins:
    """Column positions this document actually uses."""

    action: int
    dialogue: int
    parenthetical: int
    character: int
    is_flat: bool


class PlainTextAdapter:
    """Parses an indented or flat plain-text screenplay."""

    name = "plain_text"
    detected_format = DetectedFormat.PLAIN_TEXT

    def detect(self, payload: SourcePayload) -> float:
        detected, confidence = detect_format(payload)
        return confidence if detected is DetectedFormat.PLAIN_TEXT else 0.0

    def parse(
        self, payload: SourcePayload
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        text = payload.text()
        lines = text.splitlines(keepends=True)
        margins = self._measure_margins(lines)

        warnings: list[ImportWarning] = []
        if margins.is_flat:
            warnings.append(
                ImportWarning(
                    code="no_indentation",
                    message="The document has no screenplay indentation, so "
                    "element types were inferred from capitalisation alone.",
                )
            )

        scenes: list[ParsedScene] = []
        scene: ParsedScene | None = None
        previous: UnitType | None = None
        speaker: str | None = None
        offset = 0
        confidence = 0.6 if margins.is_flat else 0.9

        for raw_line in lines:
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if not stripped:
                previous = None
                speaker = None
                offset += len(raw_line)
                continue

            indent = self._indent_of(line)
            unit_type = self._classify(stripped, indent, margins, previous)

            if unit_type is UnitType.SCENE_HEADING:
                scene = self._start_scene(scenes, stripped)
                previous, speaker = unit_type, None
                self._append(
                    scene, unit_type, scene.heading, offset, raw_line, confidence, None
                )
                offset += len(raw_line)
                continue

            if scene is None:
                scene = ParsedScene(sequence_index=0, heading="")
                scenes.append(scene)
                warnings.append(
                    ImportWarning(
                        code="content_before_first_scene",
                        message="Content appears before the first scene heading "
                        "and was placed in an unheaded opening scene.",
                        scene_index=0,
                    )
                )

            if unit_type is UnitType.CHARACTER:
                parsed = parse_character_cue(stripped)
                speaker = parsed[0] if parsed else stripped
            elif unit_type not in (UnitType.DIALOGUE, UnitType.PARENTHETICAL):
                speaker = None

            self._append(
                scene,
                unit_type,
                stripped,
                offset,
                raw_line,
                confidence,
                speaker
                if unit_type
                in (UnitType.CHARACTER, UnitType.DIALOGUE, UnitType.PARENTHETICAL)
                else None,
            )
            previous = unit_type
            offset += len(raw_line)

        if not scenes:
            raise ImportRejected(
                "no_scenes",
                "No scene headings were found. A plain-text screenplay needs "
                "lines beginning INT., EXT., or EST.",
            )
        return scenes, warnings

    @staticmethod
    def _indent_of(line: str) -> int:
        """Leading whitespace in columns, expanding tabs."""
        return len(line) - len(line.expandtabs(TAB_WIDTH).lstrip())

    def _measure_margins(self, lines: list[str]) -> _Margins:
        """Derive this document's column positions from its own content."""
        indents = Counter(
            self._indent_of(line) for line in lines if line.strip()
        )
        if not indents:
            raise ImportRejected("empty_document", "The file contains no text.")

        action = min(indents)
        distinct = [indent for indent in indents if indent > action + 2]
        if sum(indents[indent] for indent in distinct) < MIN_INDENTED_LINES:
            return _Margins(action, action, action, action, is_flat=True)

        return _Margins(
            action=action,
            dialogue=action + DIALOGUE_OFFSET,
            parenthetical=action + PARENTHETICAL_OFFSET,
            character=action + CHARACTER_OFFSET,
            is_flat=False,
        )

    def _classify(
        self,
        stripped: str,
        indent: int,
        margins: _Margins,
        previous: UnitType | None,
    ) -> UnitType:
        """Decide a line's unit type from indentation, then capitalisation."""
        if SCENE_HEADING.match(stripped):
            return UnitType.SCENE_HEADING
        if TRANSITION.match(stripped):
            return UnitType.TRANSITION
        if stripped.startswith("(") and stripped.endswith(")"):
            return UnitType.PARENTHETICAL

        in_speech = previous in (
            UnitType.CHARACTER,
            UnitType.DIALOGUE,
            UnitType.PARENTHETICAL,
        )

        if not margins.is_flat:
            if indent >= margins.character and parse_character_cue(stripped):
                return UnitType.CHARACTER
            if indent >= margins.dialogue:
                return UnitType.DIALOGUE if in_speech else UnitType.ACTION

        # Flat documents, and indented ones at the action margin, fall back to
        # capitalisation: a short all-caps line that is not a heading, a
        # transition, or a shot is a character cue.
        if not in_speech and parse_character_cue(stripped) and len(stripped) <= 40:
            if SHOT_PREFIX.match(stripped):
                return UnitType.SHOT
            return UnitType.CHARACTER
        if in_speech:
            return UnitType.DIALOGUE
        if SHOT_PREFIX.match(stripped):
            return UnitType.SHOT
        return UnitType.ACTION

    @staticmethod
    def _start_scene(scenes: list[ParsedScene], stripped: str) -> ParsedScene:
        match = SCENE_NUMBER.search(stripped)
        heading = SCENE_NUMBER.sub("", stripped).strip()
        int_ext, _, time_of_day = split_heading(heading)
        scene = ParsedScene(
            sequence_index=len(scenes),
            heading=heading,
            display_scene_number=match.group("number") if match else None,
            int_ext=int_ext,
            time_of_day=time_of_day,
        )
        scenes.append(scene)
        return scene

    @staticmethod
    def _append(
        scene: ParsedScene,
        unit_type: UnitType,
        text: str,
        offset: int,
        raw_line: str,
        confidence: float,
        speaker: str | None,
    ) -> None:
        scene.units.append(
            ParsedUnit(
                unit_type=unit_type,
                sequence_index=len(scene.units),
                text=text,
                parser_method=ParserMethod.RULE,
                parser_confidence=confidence,
                speaker_name=speaker,
                anchor=SourceAnchor(
                    extraction_method="plain_text",
                    start_offset=offset,
                    end_offset=offset + len(raw_line),
                ),
            )
        )
