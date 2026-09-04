"""Stage-play adapter.

Public-domain plays (Shakespeare, Wilde, Ibsen and the like, as distributed by
Project Gutenberg) carry their structure in act and scene headings named in
words or numerals, not in INT./EXT. sluglines, and their character cues are an
all-caps name ending in a period on its own line. This adapter reads that
shape: each SCENE, or an ACT that holds no numbered scenes, becomes one scene;
the setting that opens it becomes the heading, so a location reads out of it the
same way a slugline's does; cues name the speaker; bracketed lines and
Enter/Exit lines are stage directions; and the Project Gutenberg licence
wrapper and front matter are dropped before parsing.
"""

from __future__ import annotations

import logging
import re

from ripple.adapters.base import (
    STAGE_DIRECTION,
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
    stage_act_match,
    stage_scene_match,
)
from ripple.adapters.detect import detect_format

logger = logging.getLogger(__name__)

# Project Gutenberg wraps the work in a licence header and footer.
_GUTENBERG_START = re.compile(
    r"\*\*\*\s*START OF TH(?:E|IS) PROJECT GUTENBERG.*?\*\*\*", re.IGNORECASE
)
_GUTENBERG_END = re.compile(
    r"\*\*\*\s*END OF TH(?:E|IS) PROJECT GUTENBERG.*?\*\*\*", re.IGNORECASE
)
_TITLE_LINE = re.compile(r"^Title:\s*(?P<title>.+)$", re.IGNORECASE | re.MULTILINE)

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_WORD_VALUES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
# A cue is short; a setting paragraph is not. Kept in step with the plain-text
# adapter's cue ceiling.
MAX_CUE_CHARS = 40
# Location text longer than this is cut to its first sentence, so a whole
# opening stage direction does not become the location name.
MAX_LOCATION_CHARS = 120


def _ordinal(token: str | None) -> int | None:
    """Read a roman numeral, an arabic numeral, or a number word as an int."""
    if not token:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    lowered = token.lower()
    if lowered in _WORD_VALUES:
        return _WORD_VALUES[lowered]
    upper = token.upper()
    if all(character in _ROMAN_VALUES for character in upper):
        total = previous = 0
        for character in reversed(upper):
            value = _ROMAN_VALUES[character]
            total += -value if value < previous else value
            previous = max(previous, value)
        return total or None
    return None


def _bracketed_direction(text: str) -> bool:
    """True when the whole line is one bracketed stage direction, so that an
    inline "[Aside.] ..." at the head of a speech is left as dialogue."""
    inner = text.strip().strip("_").strip()
    return inner.startswith("[") and inner.rstrip("._ ").endswith("]")


def _location_of(setting: str) -> str:
    """Reduce a setting line to a location name: strip brackets, italics, and a
    leading "SCENE" label, then keep the first sentence when it runs long."""
    text = setting.strip().strip("[]_ ").strip()
    text = re.sub(r"^SCENE\b\s*[.\-:—]*\s*", "", text, flags=re.IGNORECASE)
    if len(text) > MAX_LOCATION_CHARS:
        sentence = re.match(r"(.+?[.!?])(?:\s|$)", text)
        text = sentence.group(1) if sentence else text[:MAX_LOCATION_CHARS]
    return text.strip().rstrip(".").strip()


class StagePlayAdapter:
    """Parses an act/scene stage play into scenes, cues, and directions."""

    name = "stage_play"
    detected_format = DetectedFormat.STAGE_PLAY

    def detect(self, payload: SourcePayload) -> float:
        detected, confidence = detect_format(payload)
        return confidence if detected is DetectedFormat.STAGE_PLAY else 0.0

    def title(self, payload: SourcePayload) -> str | None:
        match = _TITLE_LINE.search(payload.text()[:2000])
        return match.group("title").strip() if match else None

    def parse(
        self, payload: SourcePayload
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        text = payload.text()
        lines = text.splitlines(keepends=True)
        start, end = self._content_bounds(text, lines)

        scenes: list[ParsedScene] = []
        warnings: list[ImportWarning] = []
        scene: ParsedScene | None = None
        current_act: int | None = None
        need_setting = False
        previous: UnitType | None = None
        speaker: str | None = None
        started = False
        offset = 0

        for index, raw in enumerate(lines):
            advance = len(raw)
            if not (start <= index < end):
                offset += advance
                continue
            stripped = raw.rstrip("\r\n").strip()
            if not stripped:
                previous = speaker = None
                offset += advance
                continue

            act_match = stage_act_match(stripped)
            if act_match:
                current_act = _ordinal(
                    act_match.group("num") or act_match.group("word")
                )
                scene = None
                started = True
                previous = speaker = None
                offset += advance
                continue

            scene_match = stage_scene_match(stripped)
            if scene_match:
                setting = scene_match.group("rest").strip()
                scene = self._open(scenes, current_act, _ordinal(scene_match.group("num")))
                started = True
                previous = speaker = None
                if setting:
                    scene.heading = _location_of(setting)
                    self._append(scene, UnitType.SCENE_HEADING, stripped, offset, raw)
                    need_setting = False
                else:
                    need_setting = True
                offset += advance
                continue

            if not started:
                offset += advance  # front matter before the first heading
                continue
            if scene is None:
                # An act with no numbered scenes: the act is the scene.
                scene = self._open(scenes, current_act, None)
                need_setting = True

            unit_type = self._classify(stripped, previous)
            if need_setting and unit_type is UnitType.ACTION:
                scene.heading = _location_of(stripped) or scene.heading
                self._append(scene, UnitType.SCENE_HEADING, stripped, offset, raw)
                need_setting = False
                previous = UnitType.SCENE_HEADING
                offset += advance
                continue

            if unit_type is UnitType.CHARACTER:
                need_setting = False
                parsed = parse_character_cue(stripped)
                speaker = parsed[0] if parsed else stripped
            elif unit_type not in (UnitType.DIALOGUE, UnitType.PARENTHETICAL):
                speaker = None

            self._append(
                scene,
                unit_type,
                stripped,
                offset,
                raw,
                speaker
                if unit_type
                in (UnitType.CHARACTER, UnitType.DIALOGUE, UnitType.PARENTHETICAL)
                else None,
            )
            previous = unit_type
            offset += advance

        if not scenes:
            raise ImportRejected(
                "no_scenes",
                "No act or scene headings were found. A stage play needs lines "
                "such as ACT I or SCENE II.",
            )
        return scenes, warnings

    @staticmethod
    def _content_bounds(text: str, lines: list[str]) -> tuple[int, int]:
        """Line indices bounding the play inside any Gutenberg licence wrapper."""
        start, end = 0, len(lines)
        start_match = _GUTENBERG_START.search(text)
        if start_match:
            start = text.count("\n", 0, start_match.end()) + 1
        end_match = _GUTENBERG_END.search(text)
        if end_match:
            end = text.count("\n", 0, end_match.start())
        return start, end

    @staticmethod
    def _open(
        scenes: list[ParsedScene], act: int | None, scene_number: int | None
    ) -> ParsedScene:
        """Open a scene, numbering it by act and scene and giving it a label
        heading until a setting replaces it."""
        if act is not None and scene_number is not None:
            number = f"{act}.{scene_number}"
            label = f"Act {act}, Scene {scene_number}"
        elif scene_number is not None:
            number = str(scene_number)
            label = f"Scene {scene_number}"
        elif act is not None:
            number = str(act)
            label = f"Act {act}"
        else:
            number = None
            label = "Opening"
        scene = ParsedScene(
            sequence_index=len(scenes),
            heading=label,
            display_scene_number=number,
        )
        scenes.append(scene)
        return scene

    def _classify(self, stripped: str, previous: UnitType | None) -> UnitType:
        """Type a line inside a scene: a stage direction, a cue, or speech."""
        if _bracketed_direction(stripped) or STAGE_DIRECTION.match(stripped):
            return UnitType.ACTION
        if stripped.startswith("(") and stripped.endswith(")"):
            return UnitType.PARENTHETICAL
        in_speech = previous in (
            UnitType.CHARACTER,
            UnitType.DIALOGUE,
            UnitType.PARENTHETICAL,
        )
        if not in_speech and parse_character_cue(stripped) and len(stripped) <= MAX_CUE_CHARS:
            return UnitType.CHARACTER
        if in_speech:
            return UnitType.DIALOGUE
        return UnitType.ACTION

    @staticmethod
    def _append(
        scene: ParsedScene,
        unit_type: UnitType,
        text: str,
        offset: int,
        raw_line: str,
        speaker: str | None = None,
    ) -> None:
        scene.units.append(
            ParsedUnit(
                unit_type=unit_type,
                sequence_index=len(scene.units),
                text=text,
                parser_method=ParserMethod.RULE,
                parser_confidence=0.85,
                speaker_name=speaker,
                anchor=SourceAnchor(
                    extraction_method="stage_play",
                    start_offset=offset,
                    end_offset=offset + len(raw_line),
                ),
            )
        )
