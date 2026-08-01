"""Final Draft XML adapter.

Final Draft already carries typed paragraphs, so this adapter maps rather than
infers. Parsing goes through defusedxml: an .fdx upload is untrusted input, and
stdlib ElementTree is vulnerable to entity-expansion denial of service.
"""

from __future__ import annotations

import logging
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from ripple.adapters.base import (
    SCENE_NUMBER,
    SHOT_PREFIX,
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

# Final Draft paragraph types that carry production content.
PARAGRAPH_TYPES: dict[str, UnitType] = {
    "scene heading": UnitType.SCENE_HEADING,
    "action": UnitType.ACTION,
    "character": UnitType.CHARACTER,
    "dialogue": UnitType.DIALOGUE,
    "parenthetical": UnitType.PARENTHETICAL,
    "transition": UnitType.TRANSITION,
    "shot": UnitType.SHOT,
    "general": UnitType.ACTION,
}
# Types that exist in Final Draft but hold no production content.
IGNORED_TYPES = frozenset(
    {"cast list", "new act", "end of act", "act break", "page break"}
)


class FdxAdapter:
    """Parses Final Draft XML into scenes and units."""

    name = "fdx"
    detected_format = DetectedFormat.FDX

    def detect(self, payload: SourcePayload) -> float:
        detected, confidence = detect_format(payload)
        return confidence if detected is DetectedFormat.FDX else 0.0

    def parse(
        self, payload: SourcePayload
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        root = self._parse_xml(payload)

        content = root.find("Content")
        if content is None:
            raise ImportRejected(
                "fdx_no_content",
                "The Final Draft file has no <Content> element, so it holds no "
                "screenplay body.",
            )

        scenes: list[ParsedScene] = []
        warnings: list[ImportWarning] = []
        scene: ParsedScene | None = None
        pending_speaker: str | None = None
        unknown_types: set[str] = set()

        for block_index, paragraph in enumerate(content.iter("Paragraph")):
            raw_type = (paragraph.get("Type") or "General").strip()
            key = raw_type.lower()
            if key in IGNORED_TYPES:
                continue

            unit_type = PARAGRAPH_TYPES.get(key)
            if unit_type is None:
                unknown_types.add(raw_type)
                unit_type = UnitType.ACTION

            text = self._paragraph_text(paragraph)
            if not text:
                continue

            if unit_type is UnitType.SCENE_HEADING:
                scene = self._start_scene(scenes, paragraph, text)
                pending_speaker = None
            elif scene is None:
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

            if unit_type is UnitType.ACTION and SHOT_PREFIX.match(text):
                unit_type = UnitType.SHOT

            speaker = None
            if unit_type is UnitType.CHARACTER:
                parsed = parse_character_cue(text)
                pending_speaker = parsed[0] if parsed else text.strip()
                speaker = pending_speaker
            elif unit_type in (UnitType.DIALOGUE, UnitType.PARENTHETICAL):
                speaker = pending_speaker

            scene.units.append(
                ParsedUnit(
                    unit_type=unit_type,
                    sequence_index=len(scene.units),
                    text=text,
                    parser_method=ParserMethod.FDX,
                    speaker_name=speaker,
                    anchor=SourceAnchor(
                        extraction_method="fdx", block_index=block_index
                    ),
                )
            )

        if unknown_types:
            warnings.append(
                ImportWarning(
                    code="unknown_paragraph_type",
                    message="Imported as action: "
                    + ", ".join(sorted(unknown_types))
                    + ".",
                )
            )
        if not scenes:
            raise ImportRejected(
                "no_scenes",
                "The Final Draft file contains no scene headings.",
            )
        return scenes, warnings

    @staticmethod
    def _parse_xml(payload: SourcePayload) -> Element:
        """Parse defensively, converting every XML failure into a rejection."""
        try:
            root = DefusedET.fromstring(payload.data)
        except DefusedXmlException as error:
            raise ImportRejected(
                "xml_unsafe",
                "The file uses XML features that are refused for safety "
                "(external entities or entity expansion).",
            ) from error
        except DefusedET.ParseError as error:
            raise ImportRejected(
                "xml_malformed", f"The file is not well-formed XML: {error}."
            ) from error

        if root.tag != "FinalDraft":
            raise ImportRejected(
                "fdx_wrong_root",
                f"Expected a <FinalDraft> root element, found <{root.tag}>.",
            )
        return root

    @staticmethod
    def _paragraph_text(paragraph: Element) -> str:
        """Join a paragraph's Text runs, dropping style-only splits."""
        return "".join(node.text or "" for node in paragraph.iter("Text")).strip()

    @staticmethod
    def _start_scene(
        scenes: list[ParsedScene], paragraph: Element, text: str
    ) -> ParsedScene:
        """Open a scene, preferring Final Draft's own scene number."""
        number = paragraph.get("Number")
        heading = SCENE_NUMBER.sub("", text).strip()
        if number is None:
            match = SCENE_NUMBER.search(text)
            number = match.group("number") if match else None

        int_ext, _, time_of_day = split_heading(heading)
        scene = ParsedScene(
            sequence_index=len(scenes),
            heading=heading,
            display_scene_number=number,
            int_ext=int_ext,
            time_of_day=time_of_day,
        )
        scenes.append(scene)
        return scene
