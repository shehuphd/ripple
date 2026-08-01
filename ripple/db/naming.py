"""Canonical name normalization.

Implements Schema Lock v1 section 5. Entity uniqueness is
`(script_id, entity_type, normalize(canonical_name))`, and `entity_aliases`
uses the same function, so `THE BLUE SEDAN`, `the blue sedan`, and
`Blue Sedan (2)` all resolve to one entity.

The result is stored rather than computed in SQL, because a unique index on a
Python function is not portable between PostgreSQL and SQLite.
"""

from __future__ import annotations

import re
import unicodedata

LEADING_ARTICLE = re.compile(r"^(?:a|an|the)\s+")
TRAILING_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")
WHITESPACE = re.compile(r"\s+")
TERMINAL_PUNCTUATION = ".,;:"


def normalize(name: str) -> str:
    """Reduce a display name to its canonical key.

    NFKC first, so accented and full-width forms of the same name collapse
    together: MATÍAS and MATÍAS are one entity.
    """
    text = unicodedata.normalize("NFKC", name).casefold()
    text = WHITESPACE.sub(" ", text).strip()
    text = TRAILING_PARENTHETICAL.sub("", text)
    text = LEADING_ARTICLE.sub("", text, count=1)
    return text.rstrip(TERMINAL_PUNCTUATION).strip()
