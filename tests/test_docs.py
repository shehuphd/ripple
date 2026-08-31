"""Docs hygiene.

Public documents and shipped source must stand on their own: no references
to internal planning material a reader of the repository cannot open, and no
relative links in the README, which is read on PyPI and GitHub where a
relative path resolves differently or not at all.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PUBLIC_DOCS = [
    "README.md",
    "USAGE.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "MANIFEST.md",
]

SHIPPED_SOURCE_GLOBS = [
    "ripple/**/*.py",
    "ripple/**/*.js",
    "ripple/**/*.html",
    "ripple/**/*.css",
    "tools/*.py",
    "demo-scripts/*/dependencies.md",
    "launch.command",
]

# Names of internal planning material. None of these may appear in anything
# a user receives: the documents they point to are not in the repository.
INTERNAL_REFERENCE_PATTERNS = [
    re.compile(r"project/"),
    re.compile(r"CODING\.md"),
    re.compile(r"ROADMAP"),
    re.compile(r"\.claude"),
    re.compile(r"\bPRD\b"),
    re.compile(r"\bERD\b"),
    re.compile(r"Schema Lock"),
    re.compile(r"TODO\.md"),
]

# The one legitimate use of "project/" is a PyPI package URL.
ALLOWED_SUBSTRING = "pypi.org/project/"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")


def shipped_files() -> list[Path]:
    files: list[Path] = []
    for pattern in SHIPPED_SOURCE_GLOBS:
        files.extend(REPO.glob(pattern))
    files.extend(REPO / name for name in PUBLIC_DOCS)
    return sorted(set(files))


class TestInternalReferences:
    def test_the_file_set_is_not_empty(self):
        files = shipped_files()
        assert len(files) > 40, "the glob set no longer matches the tree"

    def test_no_internal_references_in_public_files(self):
        offences: list[str] = []
        for path in shipped_files():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                candidate = line.replace(ALLOWED_SUBSTRING, "")
                for pattern in INTERNAL_REFERENCE_PATTERNS:
                    if pattern.search(candidate):
                        offences.append(
                            f"{path.relative_to(REPO)}:{number}: "
                            f"{pattern.pattern!r} in {line.strip()!r}"
                        )
        assert offences == []


class TestReadmeLinks:
    def test_every_readme_link_is_an_absolute_url(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        targets = MARKDOWN_LINK.findall(text)
        assert targets, "the README has no links at all; the pattern broke"
        relative = [
            target
            for target in targets
            if not target.startswith(("http://", "https://", "mailto:"))
        ]
        assert relative == []


class TestManifest:
    def test_the_manifest_exists_with_a_timestamp(self):
        manifest = REPO / "MANIFEST.md"
        assert manifest.exists()
        text = manifest.read_text(encoding="utf-8")
        stamp = re.search(
            r"^Last updated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$",
            text,
            flags=re.MULTILINE,
        )
        assert stamp, "MANIFEST.md needs a 'Last updated: YYYY-MM-DD HH:MM:SS UTC' line"

    def test_every_shipped_python_file_is_listed(self):
        text = (REPO / "MANIFEST.md").read_text(encoding="utf-8")
        missing = [
            str(path.relative_to(REPO))
            for path in REPO.glob("ripple/**/*.py")
            if "__pycache__" not in path.parts and path.name not in text
        ]
        assert missing == []
