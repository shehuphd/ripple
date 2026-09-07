#!/usr/bin/env python3
"""Refuse a verbose changelog entry.

An entry says what changed and why it needed to, in a few sentences. The
long ones are the ones nobody reads, so the gate caps their length. Only
entries this branch adds or rewrites are checked: the released history is a
record, not something to go back and edit.

    python3 tools/check_changelog.py            against origin/main
    python3 tools/check_changelog.py <ref>      against another ref
"""

from __future__ import annotations

import subprocess
import sys

# Characters, counting the bold lead-in. Three or four plain sentences fit;
# the paragraph-length entries this was written for do not.
LIMIT = 500
CHANGELOG = "CHANGELOG.md"


def lead_in(entry: str) -> str:
    """The bold title an entry opens with, which is its identity here."""
    parts = entry.split("**")
    return parts[1] if len(parts) > 2 else entry[:60]


def added_entries(base: str) -> list[str]:
    """Changelog bullets this branch adds, from the diff.

    An entry whose title also appears on a removed line is a reword of one
    that was already there, so the cap does not reach back and demand it be
    rewritten as well.
    """
    diff = subprocess.run(
        ["git", "diff", "-U0", f"{base}...HEAD", "--", CHANGELOG],
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        return []
    added = [line[1:] for line in diff.stdout.splitlines() if line.startswith("+- **")]
    reworded = {
        lead_in(line[1:])
        for line in diff.stdout.splitlines()
        if line.startswith("-- **")
    }
    return [entry for entry in added if lead_in(entry) not in reworded]


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/main"
    long_entries = [entry for entry in added_entries(base) if len(entry) > LIMIT]
    for entry in long_entries:
        head = lead_in(entry)
        print(
            f"changelog: {len(entry)} characters, over the {LIMIT} cap: {head}",
            file=sys.stderr,
        )
    if long_entries:
        print("Say it in fewer sentences.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
