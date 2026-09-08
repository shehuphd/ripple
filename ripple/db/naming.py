"""Canonical name normalization.

Entity uniqueness is
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
# NFKC leaves the typographic quotes and dashes alone, so a name printed with
# the curly apostrophe and the same name typed with the straight one were two
# entities. A printed play uses the curly form throughout and a person editing
# in the reader types the straight one, so the pair turns up on one script.
PUNCTUATION_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‛": "'", "ʼ": "'", "´": "'",
        "“": '"', "”": '"', "‟": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    }
)


def normalize(name: str) -> str:
    """Reduce a display name to its canonical key.

    NFKC first, so accented and full-width forms of the same name collapse
    together: MATÍAS and MATÍAS are one entity. Typographic quotes and
    dashes fold to their plain forms for the same reason.
    """
    text = unicodedata.normalize("NFKC", name).casefold().translate(PUNCTUATION_FOLD)
    text = WHITESPACE.sub(" ", text).strip()
    text = TRAILING_PARENTHETICAL.sub("", text)
    text = LEADING_ARTICLE.sub("", text, count=1)
    return text.rstrip(TERMINAL_PUNCTUATION).strip()


def normalize_key(key: str) -> str:
    """Reduce an attribute key to its stored token.

    NFKC, casefold, whitespace runs to a single
    underscore. `Color`, ` color `, and `COLOR` are one key.
    """
    text = unicodedata.normalize("NFKC", key).casefold().strip()
    return WHITESPACE.sub("_", text)
