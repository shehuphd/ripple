"""Derived presentation figures for the library and the reader.

Pages, runtime, and eighths are production conventions rather than stored
fields: a page is roughly a minute of screen time and is divided into eighths
for scheduling. Computing them here keeps the convention in one place and out
of the templates.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ripple.db.models import Scene, ScriptUnit, SourceAnchor

# A screenplay page holds roughly this many characters of unit text. Measured
# against the demo corpus, where each Fountain source has a rendered PDF with a
# real page count: 794, 875, and 762 characters per page across the three.
# Well below a prose page, because screenplay layout is mostly margin.
# Used only when a document carries no page provenance of its own.
CHARS_PER_PAGE = 800
EIGHTH = CHARS_PER_PAGE / 8


def script_pages(session: Session, script_id) -> int:
    """Page count: the imported document's own pagination where it has any.

    A PDF carries real page numbers in its source anchors. Fountain and plain
    text do not, so the count is estimated from length. The estimate is
    labelled nowhere because a page count is presentational; nothing in the
    graph depends on it.
    """
    from_source = session.scalar(
        select(func.max(SourceAnchor.source_page_number))
        .join(ScriptUnit)
        .join(Scene)
        .where(Scene.script_id == script_id)
    )
    if from_source:
        return int(from_source)

    characters = session.scalar(
        select(func.coalesce(func.sum(func.length(ScriptUnit.current_text)), 0))
        .join(Scene)
        .where(Scene.script_id == script_id)
    )
    return max(1, round((characters or 0) / CHARS_PER_PAGE))


def pages_by_script(session: Session, script_ids: list) -> dict:
    """`script_pages` for several scripts in two queries instead of two each."""
    if not script_ids:
        return {}
    from_source = dict(
        session.execute(
            select(Scene.script_id, func.max(SourceAnchor.source_page_number))
            .join(ScriptUnit, ScriptUnit.scene_id == Scene.id)
            .join(SourceAnchor, SourceAnchor.script_unit_id == ScriptUnit.id)
            .where(Scene.script_id.in_(script_ids))
            .group_by(Scene.script_id)
        ).all()
    )
    characters = dict(
        session.execute(
            select(
                Scene.script_id,
                func.coalesce(func.sum(func.length(ScriptUnit.current_text)), 0),
            )
            .join(ScriptUnit, ScriptUnit.scene_id == Scene.id)
            .where(Scene.script_id.in_(script_ids))
            .group_by(Scene.script_id)
        ).all()
    )
    pages = {}
    for script_id in script_ids:
        counted = from_source.get(script_id)
        if counted:
            pages[script_id] = int(counted)
        else:
            pages[script_id] = max(
                1, round((characters.get(script_id) or 0) / CHARS_PER_PAGE)
            )
    return pages


def page_of(offset_characters: int) -> int:
    """Which page a running character offset falls on."""
    return max(1, int(offset_characters / CHARS_PER_PAGE) + 1)


def runtime(pages: int) -> str:
    """One page, one minute. The oldest estimate in the business.

    Written with its units, since a bare 2:51 reads as either two hours
    fifty-one or two minutes fifty-one, and both are plausible for a script.
    """
    hours, minutes = divmod(max(0, pages), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def eighths(characters: int) -> str:
    """A scene's length in eighths of a page, as a scheduler writes it."""
    total = max(1, round(characters / EIGHTH))
    whole, part = divmod(total, 8)
    if whole and part:
        return f"{whole} {part}/8"
    if whole:
        return f"{whole}"
    return f"{part}/8"
