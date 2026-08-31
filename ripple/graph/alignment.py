"""Deterministic scene and unit alignment between two drafts.

Three passes, each consuming only what the previous pass left unmatched:

1. Exact content: identical heading and unit texts link with certainty,
   wherever the scene moved to, which also handles pure reordering.
2. Locked numbering: when both drafts carry production numbers, a matching
   number is the author stating identity, trusted above similarity. A new
   scene whose heading is the OMITTED placeholder declares its number's
   predecessor deleted.
3. Order-preserving similarity: dynamic-programming alignment over a
   text-similarity score. Only a strong score links; a middling score is
   treated as no match, so the scene re-extracts rather than inheriting a
   graph it may not deserve. Nothing links on a guess.

No model is involved anywhere in this module, so an alignment is
reproducible from its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

# A similarity below this is not evidence of identity. The safe failure is
# "new scene, extract it": a wrong link inherits a graph, a missing link
# costs one extraction.
STRONG_MATCH = 0.65

# The production placeholder for a cut scene.
OMITTED_HEADINGS = {"OMITTED", "SCENE OMITTED"}


@dataclass(frozen=True)
class SceneContent:
    """What alignment reads about one scene. Ids are opaque strings."""

    scene_id: str
    number: str | None
    heading: str
    unit_texts: tuple[str, ...]


@dataclass(frozen=True)
class ScenePair:
    """One aligned pair: kind is "unchanged" or "modified"."""

    new_id: str
    old_id: str
    kind: str
    score: float
    method: str


@dataclass
class Alignment:
    """The full result: pairs plus what matched nothing on either side."""

    pairs: list[ScenePair] = field(default_factory=list)
    inserted: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def pair_for_new(self, new_id: str) -> ScenePair | None:
        return next((p for p in self.pairs if p.new_id == new_id), None)


def _content_key(scene: SceneContent) -> tuple:
    return (scene.heading.strip().casefold(), tuple(t.strip() for t in scene.unit_texts))


def _body(scene: SceneContent) -> str:
    return "\n".join(scene.unit_texts)


def _score(old: SceneContent, new: SceneContent) -> float:
    """Heading and body similarity, weighted toward the body.

    The body carries the scene's identity; headings are short and edited
    often (INT to EXT, DAY to NIGHT), so they steer only 30%.
    """
    heading = SequenceMatcher(
        None, old.heading.casefold(), new.heading.casefold()
    ).ratio()
    body = SequenceMatcher(None, _body(old), _body(new)).ratio()
    return 0.3 * heading + 0.7 * body


def _is_omitted_marker(scene: SceneContent) -> bool:
    return scene.heading.strip().upper() in OMITTED_HEADINGS


def align_scenes(
    old_scenes: list[SceneContent], new_scenes: list[SceneContent]
) -> Alignment:
    """Align a new draft's scenes to a predecessor's."""
    result = Alignment()
    matched_old: set[str] = set()
    matched_new: set[str] = set()

    def pair(new: SceneContent, old: SceneContent, method: str) -> None:
        kind = (
            "unchanged" if _content_key(new) == _content_key(old) else "modified"
        )
        result.pairs.append(
            ScenePair(
                new_id=new.scene_id,
                old_id=old.scene_id,
                kind=kind,
                score=1.0 if kind == "unchanged" else _score(old, new),
                method=method,
            )
        )
        matched_old.add(old.scene_id)
        matched_new.add(new.scene_id)

    # Pass 1: exact content. Duplicated identical scenes match in order.
    by_content: dict[tuple, list[SceneContent]] = {}
    for scene in old_scenes:
        by_content.setdefault(_content_key(scene), []).append(scene)
    for scene in new_scenes:
        if _is_omitted_marker(scene):
            continue
        candidates = by_content.get(_content_key(scene))
        if candidates:
            pair(scene, candidates.pop(0), "content")

    # Pass 2: locked numbering, only when both drafts carry numbers.
    old_numbered = {
        scene.number: scene
        for scene in old_scenes
        if scene.number and scene.scene_id not in matched_old
    }
    both_numbered = any(s.number for s in old_scenes) and any(
        s.number for s in new_scenes
    )
    if both_numbered:
        for scene in new_scenes:
            if scene.scene_id in matched_new or not scene.number:
                continue
            old = old_numbered.get(scene.number)
            if old is None:
                continue
            if _is_omitted_marker(scene):
                # The author cut this scene and left the placeholder.
                result.deleted.append(old.scene_id)
                matched_old.add(old.scene_id)
                matched_new.add(scene.scene_id)
                continue
            pair(scene, old, "number")

    # Pass 3: order-preserving similarity over the remainder.
    old_rest = [s for s in old_scenes if s.scene_id not in matched_old]
    new_rest = [
        s
        for s in new_scenes
        if s.scene_id not in matched_new and not _is_omitted_marker(s)
    ]
    for new, old, score in _sequence_align(old_rest, new_rest):
        result.pairs.append(
            ScenePair(
                new_id=new.scene_id,
                old_id=old.scene_id,
                kind="modified",
                score=score,
                method="similarity",
            )
        )
        matched_old.add(old.scene_id)
        matched_new.add(new.scene_id)

    result.inserted = [
        s.scene_id
        for s in new_scenes
        if s.scene_id not in matched_new and not _is_omitted_marker(s)
    ]
    result.deleted.extend(
        s.scene_id for s in old_scenes if s.scene_id not in matched_old
    )
    return result


def _sequence_align(
    old_rest: list[SceneContent], new_rest: list[SceneContent]
) -> list[tuple[SceneContent, SceneContent, float]]:
    """Order-preserving best-score alignment, strong matches only.

    Standard alignment DP: each cell chooses skip-old, skip-new, or link,
    where a link is only offered when the pair scores at or above
    STRONG_MATCH. Skips cost nothing, so the result is the highest-scoring
    set of order-preserving strong links.
    """
    if not old_rest or not new_rest:
        return []
    rows, cols = len(old_rest), len(new_rest)
    scores = [
        [_score(old_rest[i], new_rest[j]) for j in range(cols)] for i in range(rows)
    ]
    best = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            best[i][j] = max(best[i - 1][j], best[i][j - 1])
            if scores[i - 1][j - 1] >= STRONG_MATCH:
                best[i][j] = max(
                    best[i][j], best[i - 1][j - 1] + scores[i - 1][j - 1]
                )
    links: list[tuple[SceneContent, SceneContent, float]] = []
    i, j = rows, cols
    while i > 0 and j > 0:
        if (
            scores[i - 1][j - 1] >= STRONG_MATCH
            and best[i][j] == best[i - 1][j - 1] + scores[i - 1][j - 1]
        ):
            links.append((new_rest[j - 1], old_rest[i - 1], scores[i - 1][j - 1]))
            i -= 1
            j -= 1
        elif best[i - 1][j] >= best[i][j - 1]:
            i -= 1
        else:
            j -= 1
    links.reverse()
    return links


def align_units(old_texts: list[str], new_texts: list[str]) -> list[int | None]:
    """For each new unit, the index of the old unit it continues, or None.

    Whole-line equality through SequenceMatcher's matching blocks: an edited
    line maps to nothing, so evidence citing it is judged rather than
    carried, while untouched lines keep their citations.
    """
    mapping: list[int | None] = [None] * len(new_texts)
    matcher = SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[block.b + offset] = block.a + offset
    return mapping
