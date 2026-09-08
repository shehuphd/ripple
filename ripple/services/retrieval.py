"""What a question is answered from: the evidence packet over one script.

Ask the graph built this inline in its route. Ask Ripple's agent needs the
same packet from a tool call, so it lives here, called from both. The packet
is deterministic: keyword retrieval over the accepted assertions, plus the
facts a count question needs (the roster by department and the ordered scene
list) that no single assertion carries.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ripple.db.models import Assertion, Entity, EntityAlias, Scene, Script, ScriptUnit
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

# Question words that scope nothing and flood the packet if matched. "appear(s)"
# would hit the appears_in predicate on every cast cue; "scene(s)"/"script" name
# the scaffolding every line shares. A term has to survive this set to scope the
# packet, and an entity's own name is matched separately so a real name made of
# common words is never lost to it.
STOPWORDS = frozenset(
    """
    a an the this that these those there here and or but not of in on at to by
    as for with from into onto over about who whom whose what which when where
    why how is are was were be been being do does did done have has had will
    would can could shall should may might must it its he she they them him her
    his their our your we you i me my any all some each every both any one
    appear appears appeared appearing appearance appearances scene scenes
    script scripts line lines page pages show shows showing list lists listed
    name names named tell tells say says said does
    """.split()
)

# The two "presence" predicates: their evidence is a bare speaker cue or a scene
# heading, so they answer "who appears where" but read as noise as the evidence
# for anything else.
PRESENCE_PREDICATES = frozenset({"appears_in", "occurs_at"})

# Matching runs on whole words, so a query verb ("appear") never matches a
# predicate that merely contains it ("appears_in").
_WORD = re.compile(r"[^0-9a-z]+")


def _content_terms(question: str) -> list[str]:
    """A question's folded content words: word tokens, stopwords dropped."""
    return [
        term
        for term in _WORD.split(fold(question))
        if len(term) > 2 and term not in STOPWORDS
    ]


def _edge_words(text: str) -> set[str]:
    """The whole words of an edge, with predicate underscores split apart."""
    return {word for word in _WORD.split(fold(text).replace("_", " ")) if word}


def _named_entities(session: Session, script_id: Any, question: str) -> set:
    """Entity ids whose canonical name or an alias occurs in the question.

    Matched as a whole phrase between word boundaries, so "young woman" matches
    the entity of that name but not a passing "young" elsewhere, and a name made
    only of stopwords ("the") never scopes anything on its own.
    """
    haystack = " " + " ".join(_WORD.split(fold(question))) + " "
    names = session.execute(
        select(Entity.id, Entity.canonical_name).where(Entity.script_id == script_id)
    ).all()
    aliases = session.execute(
        select(EntityAlias.entity_id, EntityAlias.alias)
        .join(Entity, EntityAlias.entity_id == Entity.id)
        .where(Entity.script_id == script_id)
    ).all()
    found: set = set()
    for entity_id, name in [*names, *aliases]:
        words = [w for w in _WORD.split(fold(name or "")) if w]
        if not words or all(word in STOPWORDS for word in words):
            continue
        if f" {' '.join(words)} " in haystack:
            found.add(entity_id)
    return found


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
    terms = _content_terms(question)
    named = _named_entities(session, script.id, question)
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
    entity_hits: list[dict[str, Any]] = []
    for row in rows:
        subject_id = row.subject_entity_id or row.subject_scene_id
        object_id = row.object_entity_id or row.object_scene_id
        subject = labels.get(subject_id, "")
        obj = labels.get(object_id, "")
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
        if named and (subject_id in named or object_id in named):
            entity_hits.append(item)
        if terms:
            edge_words = _edge_words(f"{subject} {row.predicate} {obj}")
            if any(term in edge_words for term in terms):
                keyword_hits.append(item)

    # Scoping, in order of precision. A question that names an entity is scoped
    # to that entity's own edges, so "who is X, and what scenes is she in" reads
    # X's facts and appearances rather than the whole cast's. The name match is
    # deliberate: an entity with no edges (a photograph, say) scopes to nothing,
    # which is the honest answer, not a graph-wide sweep dressed as evidence.
    # Failing a name, a question whose content words hit the graph is scoped to
    # those hits. A scene-anchored or aggregate question ("what props are in
    # scene 8", "which scene has the most props") names nothing and matches no
    # term, so it falls back to the whole graph rather than a sample that would
    # miss the scenes it asks about. A user query is infrequent and costs a
    # fraction of a cent, so the cap holds a whole demo-corpus script and only
    # bounds a pathologically large graph.
    scoped = bool(named or keyword_hits)
    if named:
        matched = entity_hits
    elif keyword_hits:
        matched = keyword_hits
    else:
        matched = mapped
    matched = matched[:PACKET_ASSERTION_CAP]

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
        matched_by_keyword=scoped,
        facts={
            "title": script.title,
            "scenes": len(scene_list),
            "entities": sum(slot["count"] for slot in roster.values()),
            "entities_by_type": roster,
            "scene_list": scene_list,
            "assertions": len(rows),
        },
    )
