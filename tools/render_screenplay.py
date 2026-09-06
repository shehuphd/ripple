#!/usr/bin/env python3
"""Render an authored Fountain screenplay into the other three import formats.

One authored source per demo script produces .fdx, .pdf, and .txt, so all four
adapters have a fixture derived from identical content. Any
difference an adapter produces is the adapter's, not the source material's.

    python tools/render_screenplay.py                    # every demo script
    python tools/render_screenplay.py path/to/x.fountain # one script

screenplain drops Fountain's #N# scene numbers, so this script reinjects them
into the FDX as Number attributes. The PDF and TXT renders carry no printed
numbers, which is correct for a spec draft: numbering there comes from scene
order, and the demo corpus is deliberately contiguous from 1.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger("ripple.render")

REPO = Path(__file__).resolve().parent.parent
SCREENPLAIN = REPO / ".venv" / "bin" / "screenplain"
DEMO_SCRIPTS = REPO / "demo-scripts"

SCENE_NUMBER = re.compile(r"#([\w.\-]+)#\s*$")

# Character columns for a 12pt Courier page, where one character is 0.1 inch.
INDENT = {
    "Scene Heading": 0,
    "Action": 0,
    "Character": 22,
    "Parenthetical": 16,
    "Dialogue": 10,
    "Transition": 60,
}
WRAP = {
    "Scene Heading": 60,
    "Action": 60,
    "Character": 38,
    "Parenthetical": 26,
    "Dialogue": 35,
    "Transition": 20,
}


def scene_numbers(fountain: Path) -> list[str]:
    """Scene numbers in document order, from #N# markers on scene headings."""
    numbers = []
    for line in fountain.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        # A leading dot is Fountain's forced scene heading, used for sequences
        # such as MONTAGE that carry a scene number but no INT/EXT prefix.
        forced = stripped.startswith(".") and not stripped.startswith("..")
        if not forced and not stripped.upper().startswith(
            ("INT.", "EXT.", "EST.", "INT/EXT", "I/E")
        ):
            continue
        match = SCENE_NUMBER.search(stripped)
        numbers.append(match.group(1) if match else "")
    return numbers


def run_screenplain(fountain: Path, out: Path, fmt: str) -> None:
    """Invoke screenplain for one output format, raising on a non-zero exit."""
    result = subprocess.run(
        [str(SCREENPLAIN), "--format", fmt, str(fountain), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"screenplain {fmt} failed: {result.stderr.strip()}")


def inject_scene_numbers(fdx: Path, numbers: list[str]) -> int:
    """Add Number attributes to FDX scene headings, in document order."""
    ET.register_namespace("", "")
    tree = ET.parse(fdx)
    headings = [
        p for p in tree.getroot().iter("Paragraph") if p.get("Type") == "Scene Heading"
    ]
    if len(headings) != len(numbers):
        raise RuntimeError(
            f"{fdx.name}: {len(headings)} FDX headings but {len(numbers)} "
            "Fountain scene numbers. The Fountain source has a heading the "
            "parser did not recognise, or a missing #N# marker."
        )
    injected = 0
    for heading, number in zip(headings, numbers, strict=False):
        if number:
            heading.set("Number", number)
            injected += 1
    tree.write(fdx, encoding="UTF-8", xml_declaration=True)
    return injected


def fdx_to_text(fdx: Path, txt: Path) -> None:
    """Lay the typed FDX out as an indented plain-text screenplay.

    This is the fixture for the plain-text adapter, so it keeps the
    indentation a typed screenplay carries and drops everything else:
    no scene numbers, no markup, no title page.
    """
    root = ET.parse(fdx).getroot()
    lines: list[str] = []
    previous = None

    for paragraph in root.iter("Paragraph"):
        kind = paragraph.get("Type", "Action")
        if kind not in INDENT:
            continue
        text = "".join(paragraph.itertext()).strip()
        if not text:
            continue
        if kind == "Scene Heading":
            text = text.upper()

        # Blank line between blocks, except between a cue and what it says.
        if (
            lines
            and not (previous == "Character" and kind in ("Dialogue", "Parenthetical"))
            and not (previous == "Parenthetical" and kind == "Dialogue")
        ):
            lines.append("")

        pad = " " * INDENT[kind]
        for wrapped in textwrap.wrap(text, WRAP[kind]) or [""]:
            lines.append(pad + wrapped)
        previous = kind

    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(fountain: Path) -> None:
    """Produce the .fdx, .pdf, and .txt siblings of one Fountain source."""
    stem = fountain.with_suffix("")
    fdx, pdf, txt = (
        stem.with_suffix(".fdx"),
        stem.with_suffix(".pdf"),
        stem.with_suffix(".txt"),
    )

    numbers = scene_numbers(fountain)
    run_screenplain(fountain, fdx, "fdx")
    injected = inject_scene_numbers(fdx, numbers)
    run_screenplain(fountain, pdf, "pdf")
    fdx_to_text(fdx, txt)

    logger.info("%s", fountain.relative_to(REPO))
    logger.info("  %s  %d scenes, %d numbered", fdx.name, len(numbers), injected)
    logger.info("  %s  %s bytes", pdf.name, f"{pdf.stat().st_size:,}")
    logger.info("  %s  %s bytes", txt.name, f"{txt.stat().st_size:,}")


def main(argv: list[str]) -> int:
    """Render the given Fountain files, or the whole corpus. Returns an exit code."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not SCREENPLAIN.exists():
        logger.error(
            "screenplain missing. Run:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/python -m pip install -e '.[dev]'"
        )
        return 1

    targets = (
        [Path(a).resolve() for a in argv]
        if argv
        else sorted(DEMO_SCRIPTS.glob("*/*.fountain"))
    )
    if not targets:
        logger.error("no .fountain files found")
        return 1

    failed = 0
    for target in targets:
        try:
            render(target)
        except (RuntimeError, OSError) as error:
            logger.error("%s: %s", target.name, error)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
