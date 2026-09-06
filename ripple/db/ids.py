"""Identifier coercion shared by every layer that accepts ids as strings.

Route parameters, JSON payloads, and stored history all hand ids around as
strings; the columns want `uuid.UUID`. One conversion, so a caller never
guesses which form it holds.
"""

from __future__ import annotations

import uuid


def as_uuid(value):
    """A UUID from a string or a UUID; anything else is left to raise."""
    return value if not isinstance(value, str) else uuid.UUID(value)
