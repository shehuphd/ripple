"""What a question is answered from: the evidence packet over one script.

Ask the graph built this inline in its route. Ask Ripple's agent needs the
same packet from a tool call, so it lives here, called from both. The packet
is deterministic: keyword retrieval over the accepted assertions, plus the
facts a count question needs (the roster by department and the ordered scene
list) that no single assertion carries.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ripple.db.models import Assertion, Entity, Scene, Script, ScriptUnit
from ripple.db.repository import graph_labels

# The most entity names of one department the packet lists. A department this
# large is enumerable by its count; listing every name past this would bloat
# the packet with no gain. The count beside the names is always the true total.
ROSTER_NAME_CAP = 80

# The most assertions the packet carries when a question falls back to the
# whole graph. Set above the largest demo-corpus script so scene-anchored and
# aggregate questions see every assertion; it only truncates a far larger
# graph, and a keyword-scoped question sends its focused hits regardless.
PACKET_ASSERTION_CAP = 1000


def fold(text: str) -> str:
    """Casefold and strip diacritics for keyword matching.

    A question typed without accents must still find the accented entity:
    "bela" matches "Béla", "matias" matches "Matías". NFKC (the entity-name
    normalizer) keeps the accents, so this decomposes and drops the combining
    marks instead.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


@dataclass
class QueryPacket:
    """The evidence one question is answered from."""

    assertions: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    # Every assertion the script holds, before keyword scoping. The agent's
    # coverage list reads this: which scenes cite a thing is a question about
    # the whole graph, not about what a question happened to match.
    all_assertions: list[dict[str, Any]] = field(default_factory=list)
    matched_by_keyword: bool = False


def build_packet(session: Session, script: Script, question: str) -> QueryPacket:
    """Retrieve the evidence for one question over one script."""
    labels = graph_labels(session, script.id)
    terms = [fold(word) for word in question.split() if len(word) > 3]
    rows = list(
        session.scalars(
            select(Assertion).where(
                Assertion.script_id == script.id, Assertion.active.is_(True)
            )
        )
    )
    # Heading as well as number: a script whose scenes carry no numbers would
    # otherwise reach the model with no scene identity at all, and "which
    # scenes use the mug" would be unanswerable from a graph that records it.
    # Numbers are never invented, so the heading is the fallback identity.
    scene_of = {
        unit_id: {"number": number, "heading": heading}
        for unit_id, number, heading in session.execute(
            select(ScriptUnit.id, Scene.display_scene_number, Scene.heading)
            .join(Scene)
            .where(Scene.script_id == script.id)
        ).all()
    }
    unit_text = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.current_text)
            .join(Scene)
            .where(Scene.script_id == script.id)
        ).all()
    )

    mapped: list[dict[str, Any]] = []
    keyword_hits: list[dict[str, Any]] = []
    for row in rows:
        subject = labels.get(row.subject_entity_id or row.subject_scene_id, "")
        obj = labels.get(row.object_entity_id or row.object_scene_id, "")
        haystack = fold(f"{subject} {row.predicate} {obj}")
        where = scene_of.get(row.source_unit_id) or {}
        item = {
            "id": str(row.id),
            "subject": subject,
            "predicate": row.predicate,
            "object": obj,
            "scene": where.get("number"),
            "scene_heading": where.get("heading"),
            "unit_id": str(row.source_unit_id),
            "unit_text": unit_text.get(row.source_unit_id, ""),
            "confidence": row.confidence,
        }
        mapped.append(item)
        if terms and any(term in haystack for term in terms):
            keyword_hits.append(item)

    # A question that names graph content is scoped to its keyword hits, kept
    # tight so the grounding count stays honest. A question that names nothing
    # the keyword pass can match, a scene-anchored or aggregate one ("what
    # props are in scene 8", "which scene has the most props"), falls back to
    # the whole graph rather than a sample: those scenes' assertions carry no
    # term to match, so a small sample misses them. A user query is infrequent
    # and costs a fraction of a cent, so the cap holds a whole demo-corpus
    # script and only bounds a pathologically large graph.
    matched = (keyword_hits or mapped)[:PACKET_ASSERTION_CAP]

    # Entities as a roster grouped by department type: the count and the
    # names. A "who / list / name the X" question needs the names, not only
    # the total; a packet with counts alone answered "the graph does not
    # record the full list" about a graph that records every one.
    roster: dict[str, dict[str, Any]] = {}
    for etype, name in session.execute(
        select(Entity.entity_type, Entity.canonical_name)
        .where(Entity.script_id == script.id)
        .order_by(Entity.entity_type, Entity.canonical_name)
    ).all():
        slot = roster.setdefault(etype, {"count": 0, "names": []})
        slot["count"] += 1
        if len(slot["names"]) < ROSTER_NAME_CAP:
            slot["names"].append(name)

    # Every scene in order, so scene enumeration ("list the scene headings",
    # "what is the opening scene") answers from the packet rather than from
    # whichever assertions happened to be sampled.
    scene_list = [
        {"number": number, "heading": heading}
        for number, heading in session.execute(
            select(Scene.display_scene_number, Scene.heading)
            .where(Scene.script_id == script.id, Scene.omitted.is_(False))
            .order_by(Scene.sequence_index)
        ).all()
    ]

    return QueryPacket(
        assertions=matched,
        all_assertions=mapped,
        matched_by_keyword=bool(keyword_hits),
        facts={
            "title": script.title,
            "scenes": len(scene_list),
            "entities": sum(slot["count"] for slot in roster.values()),
            "entities_by_type": roster,
            "scene_list": scene_list,
            "assertions": len(rows),
        },
    )
