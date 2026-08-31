"""Format detection from content and file signature.

Extensions are hints, not authority. Every check here reads the
bytes. The extension only breaks a tie between two text formats that scored
identically, and never overrides a positive content signal.
"""

from __future__ import annotations

import logging
import re

from ripple.adapters.base import (
    SCENE_HEADING,
    DetectedFormat,
    SourcePayload,
)

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
# Final Draft writes an XML declaration then a FinalDraft root. Some exporters
# emit a BOM or leading whitespace, so the root is searched for in a prefix
# rather than anchored at byte zero.
FDX_ROOT = re.compile(rb"<\s*FinalDraft[\s>]", re.IGNORECASE)
XML_DECLARATION = re.compile(rb"^\s*(?:\xef\xbb\xbf)?<\?xml[\s?]", re.IGNORECASE)
SNIFF_BYTES = 4096

# Fountain's own markers. None of these appear in Final Draft XML or in a PDF's
# text layer, so a hit is strong evidence rather than a weak prior.
FOUNTAIN_TITLE_KEY = re.compile(
    r"^(?:title|credit|author|authors|source|draft date|contact|notes|"
    r"copyright|revision)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
FOUNTAIN_MARKERS = (
    re.compile(r"^\s*\.[A-Za-z]", re.MULTILINE),  # forced scene heading
    re.compile(r"^\s*@[A-Za-z]", re.MULTILINE),  # forced character cue
    re.compile(r"^\s*!", re.MULTILINE),  # forced action
    re.compile(r"^\s*>.+<\s*$", re.MULTILINE),  # centred text
    re.compile(r"\[\[.+?\]\]", re.DOTALL),  # note
    re.compile(r"^\s*={3,}\s*$", re.MULTILINE),  # page break
    re.compile(r"#[\w.\-]+#\s*$", re.MULTILINE),  # scene number
    re.compile(r"^\s*\S.*\^\s*$", re.MULTILINE),  # dual dialogue caret
)


def _looks_binary(data: bytes) -> bool:
    """True when the prefix holds NUL bytes or is mostly non-text."""
    prefix = data[:SNIFF_BYTES]
    if not prefix:
        return False
    if b"\x00" in prefix and not prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    printable = sum(
        1 for byte in prefix if byte in (9, 10, 13) or 32 <= byte <= 126 or byte >= 160
    )
    return printable / len(prefix) < 0.85


def detect_format(payload: SourcePayload) -> tuple[DetectedFormat, float]:
    """Return the detected format and a confidence in [0, 1].

    Confidence below roughly 0.5 means the caller should treat the result as a
    guess and let the screenplay check decide whether to accept the import.
    """
    data = payload.data
    if not data.strip():
        return DetectedFormat.UNKNOWN, 0.0

    if data.startswith(PDF_MAGIC):
        return DetectedFormat.PDF, 1.0
    # A PDF produced by a tool that prepends junk still carries the magic in
    # the first block; anything later is not a PDF we should trust.
    if PDF_MAGIC in data[:1024]:
        logger.debug("PDF magic found at offset %d", data.index(PDF_MAGIC))
        return DetectedFormat.PDF, 0.8

    # A byte-order mark settles the text question: UTF-16 is half NUL bytes
    # and the binary heuristic below would reject it.
    has_bom = data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))

    prefix = data[:SNIFF_BYTES]
    if FDX_ROOT.search(prefix):
        return DetectedFormat.FDX, 1.0
    if has_bom:
        decoded = payload.text().encode("utf-8")[:SNIFF_BYTES]
        if FDX_ROOT.search(decoded):
            return DetectedFormat.FDX, 1.0
    if XML_DECLARATION.match(prefix):
        # XML, but not Final Draft. Say so rather than handing it to a text
        # parser that would treat tag soup as action lines.
        return DetectedFormat.UNKNOWN, 0.2

    if not has_bom and _looks_binary(data):
        return DetectedFormat.UNKNOWN, 0.0

    return _detect_text_format(payload)


def _detect_text_format(payload: SourcePayload) -> tuple[DetectedFormat, float]:
    """Separate Fountain from plain text once the payload is known to be text."""
    text = payload.text()

    marker_hits = sum(1 for pattern in FOUNTAIN_MARKERS if pattern.search(text))
    has_title_page = bool(FOUNTAIN_TITLE_KEY.search(text[:2000]))
    heading_count = sum(
        1 for line in text.splitlines() if SCENE_HEADING.match(line.strip())
    )

    if marker_hits >= 2 or (has_title_page and marker_hits >= 1):
        return DetectedFormat.FOUNTAIN, 0.95
    if has_title_page and heading_count:
        return DetectedFormat.FOUNTAIN, 0.8
    if marker_hits == 1 and heading_count:
        return DetectedFormat.FOUNTAIN, 0.6

    if heading_count:
        # Screenplay-shaped text with no Fountain markers. The extension is
        # allowed to break this tie and nothing else.
        if payload.extension == "fountain":
            return DetectedFormat.FOUNTAIN, 0.6
        return DetectedFormat.PLAIN_TEXT, 0.7

    return DetectedFormat.PLAIN_TEXT, 0.3
