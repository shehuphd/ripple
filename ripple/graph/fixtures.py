"""Ground-truth graph seeding from a demo script's dependencies.md.

Each bundled screenplay ships a dependencies.md recording the entities,
attributes, and dependency chains a correct extraction must produce. This
module parses those tables and writes the graph they describe, so the demo
corpus opens with a full graph and zero model calls. Rows carry
`provenance="system"`, which distinguishes them from a model's output.

Seeding is idempotent: the assertion dedupe key and the active-attribute key
make a second run write nothing, so it is safe to run at every startup.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ripple.db.models import (
    Assertion,
    Entity,
    EntityAlias,
    EntityAttribute,
    ExtractionRun,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize, normalize_key
from ripple.graph.predicates import SignatureError, canonical_endpoints, validate_edge

logger = logging.getLogger(__name__)

# Confidence bands, as the ground-truth tables state them.
CONFIDENCE_BANDS = {"high": 0.9, "medium": 0.68, "low": 0.55}

SCENE_TOKEN = re.compile(r"\b(\d+[A-Z]?)\b")
BACKTICKED = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class Chain:
    """One planted dependency chain: an introduction and its dependants."""

    entity: str
    entity_type: str
    establishes: str
    dependants: tuple[str, ...]


@dataclass
class GroundTruth:
    """Everything a dependencies.md states about one script's graph."""

    title: str
    entities: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    attributes: list[tuple[str, str, str, str]] = field(default_factory=list)
    chains: list[Chain] = field(default_factory=list)
    #: (scene_number, subject_name_or_scene, predicate, object_name_or_scene,
    #: confidence): "Sc N" cells become the literal string "scene".
    scene_assertions: list[tuple[str, str, str, str, float]] = field(
        default_factory=list
    )


def _tables(text: str) -> list[tuple[list[str], list[list[str]], str]]:
    """Every markdown table as (header cells, rows, preceding heading)."""
    tables = []
    heading = ""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.startswith("|") and index + 1 < len(lines):
            divider = lines[index + 1].strip()
            if divider.startswith("|") and set(divider) <= set("|-: "):
                header = [cell.strip() for cell in line.strip("|").split("|")]
                rows = []
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    rows.append(
                        [cell.strip() for cell in lines[index].strip("|").split("|")]
                    )
                    index += 1
                tables.append((header, rows, heading))
                continue
        index += 1
    return tables


def _entity_type(cell: str) -> str:
    """The entity type a table cell states.

    The last backticked token wins, so "`makeup` on the vehicle, modelled as
    `set_design`" reads as set_design, which is what the sentence says.
    """
    found = BACKTICKED.findall(cell)
    return found[-1] if found else cell.strip().strip("`")


def _scene_numbers(cell: str) -> tuple[str, ...]:
    """Scene numbers in a cell: "5 (as THE PASSENGER)" is 5, "32 only" is 32."""
    return tuple(SCENE_TOKEN.findall(cell))


def parse_ground_truth(text: str) -> GroundTruth:
    """Read one dependencies.md into structured ground truth."""
    title_match = re.match(r"#\s*(.+?):\s*ground truth", text)
    truth = GroundTruth(title=title_match.group(1).strip() if title_match else "")

    for header, rows, heading in _tables(text):
        columns = [cell.casefold() for cell in header]

        if "canonical name" in columns and "type" in columns:
            alias_column = next(
                (i for i, c in enumerate(columns) if "alias" in c), None
            )
            for row in rows:
                aliases = ()
                if alias_column is not None and len(row) > alias_column:
                    aliases = tuple(
                        alias.strip()
                        for alias in row[alias_column].split(",")
                        if alias.strip()
                    )
                truth.entities.append((row[0], _entity_type(row[1]), aliases))

        elif columns[:3] == ["entity", "key", "value"]:
            for row in rows:
                truth.attributes.append(
                    (row[0], row[1], row[2], row[3] if len(row) > 3 else "")
                )

        elif "establishes" in columns and any("dependant" in c for c in columns):
            establishes_at = columns.index("establishes")
            dependants_at = next(i for i, c in enumerate(columns) if "dependant" in c)
            entity_at = columns.index("entity")
            type_at = columns.index("type")
            for row in rows:
                established = _scene_numbers(row[establishes_at])
                if not established:
                    continue
                truth.chains.append(
                    Chain(
                        entity=row[entity_at],
                        entity_type=_entity_type(row[type_at]),
                        establishes=established[0],
                        dependants=_scene_numbers(row[dependants_at]),
                    )
                )

        elif columns[:3] == ["subject", "predicate", "object"]:
            scene_match = re.search(r"scene\s+(\w+)", heading, re.IGNORECASE)
            if scene_match is None:
                continue
            number = scene_match.group(1)
            for row in rows:
                confidence = CONFIDENCE_BANDS.get(
                    row[3].strip().casefold() if len(row) > 3 else "high", 0.9
                )
                subject = "scene" if row[0].casefold().startswith("sc ") else row[0]
                obj = "scene" if row[2].casefold().startswith("sc ") else row[2]
                truth.scene_assertions.append(
                    (number, subject, row[1].strip("`"), obj, confidence)
                )

    return truth


def ground_truths(directory: Path) -> list[GroundTruth]:
    """Parse every dependencies.md under the demo-scripts directory."""
    truths = []
    for path in sorted(directory.glob("*/dependencies.md")):
        truth = parse_ground_truth(path.read_text(encoding="utf-8"))
        if truth.title:
            truths.append(truth)
    return truths


@dataclass
class SeedCounts:
    """What one seeding run wrote."""

    entities: int = 0
    aliases: int = 0
    attributes: int = 0
    assertions: int = 0
    skipped: int = 0


class _Seeder:
    """Writes one script's ground-truth graph."""

    def __init__(self, session: Session, script: Script, truth: GroundTruth) -> None:
        self.session = session
        self.script = script
        self.truth = truth
        self.counts = SeedCounts()
        self.scenes: dict[str, Scene] = {}
        self.units: dict[object, list[ScriptUnit]] = {}
        self.entities: dict[str, Entity] = {}
        self.alias_index: dict[str, Entity] = {}
        # Normalized alias keys already present or pending per entity, since a
        # pending add is not in the relationship collection yet and two spelt
        # forms of one alias would otherwise collide inside a single flush.
        self._alias_keys: dict[object, set[str]] = {}
        # Alias display strings per entity, pending included. Evidence search
        # reads these rather than the relationship, so the unit an edge cites
        # is identical on a first seeding and a re-run: the relationship is
        # empty for a fresh entity and loaded on the next run, and a different
        # cited unit means a different dedupe key, which defeats idempotence.
        self._alias_strings: dict[object, list[str]] = {}

        for scene in session.scalars(
            select(Scene)
            .where(Scene.script_id == script.id)
            .order_by(Scene.sequence_index)
        ):
            if scene.display_scene_number:
                self.scenes[scene.display_scene_number] = scene
            self.units[scene.id] = list(
                session.scalars(
                    select(ScriptUnit)
                    .where(ScriptUnit.scene_id == scene.id)
                    .order_by(ScriptUnit.sequence_index)
                )
            )

    def run(self) -> SeedCounts:
        self._seed_entities()
        self._seed_attributes()
        self._seed_occurs_at()
        self._seed_speaker_appearances()
        self._seed_chains()
        self._seed_scene_assertions()
        self.session.flush()
        return self.counts

    # Entities

    def _seed_entities(self) -> None:
        for name, entity_type, aliases in self.truth.entities:
            entity = self._entity(name, entity_type)
            for alias in aliases:
                self._alias(entity, alias)
        for chain in self.truth.chains:
            self._entity(chain.entity, chain.entity_type)

    def _entity(self, name: str, entity_type: str) -> Entity:
        key = normalize(name)
        if key in self.entities:
            return self.entities[key]
        entity = self.session.scalar(
            select(Entity).where(
                Entity.script_id == self.script.id,
                Entity.entity_type == entity_type,
                Entity.normalized_name == key,
            )
        )
        if entity is None:
            entity = Entity(
                script_id=self.script.id,
                entity_type=entity_type,
                canonical_name=name,
                normalized_name=key,
            )
            self.session.add(entity)
            self.session.flush()
            self.counts.entities += 1
        self.entities[key] = entity
        self.alias_index[key] = entity
        if entity.id not in self._alias_strings:
            self._alias_strings[entity.id] = [a.alias for a in entity.aliases]
            self._alias_keys[entity.id] = {a.normalized_alias for a in entity.aliases}
        return entity

    def _alias(self, entity: Entity, alias: str) -> None:
        key = normalize(alias)
        if not key or key == entity.normalized_name:
            self.alias_index.setdefault(key, entity)
            return
        self.alias_index.setdefault(key, entity)
        if key in self._alias_keys.setdefault(entity.id, set()):
            return
        self._alias_keys[entity.id].add(key)
        self._alias_strings.setdefault(entity.id, []).append(alias)
        self.session.add(
            EntityAlias(
                entity_id=entity.id,
                alias=alias,
                normalized_alias=key,
                provenance="system",
            )
        )
        self.counts.aliases += 1

    def _find(self, name: str) -> Entity | None:
        return self.alias_index.get(normalize(name)) or self.entities.get(
            normalize(name)
        )

    # Evidence

    def _evidence(
        self, scene: Scene, entity: Entity
    ) -> tuple[ScriptUnit, int | None, int | None] | None:
        """The first unit in a scene that mentions the entity, with offsets.

        Presence in the scene is what the ground-truth table asserts; this
        only chooses the best citing unit. When no unit names the entity, the
        scene's first unit is cited with no offsets rather than inventing a
        span.
        """
        units = self.units.get(scene.id, [])
        if not units:
            return None
        needles = [entity.canonical_name, *self._alias_strings.get(entity.id, [])]
        # A ground-truth row with a blank name has no tail; guarded so a
        # malformed dependencies.md cannot crash startup.
        name_words = entity.canonical_name.split()
        if name_words and len(name_words[-1]) > 4:
            needles.append(name_words[-1])
        # Longest first, ties broken alphabetically, so the chosen span does
        # not depend on the order the needles were gathered in.
        needles.sort(key=lambda needle: (-len(needle), needle))
        for unit in units:
            haystack = unit.current_text.casefold()
            for needle in needles:
                at = haystack.find(needle.casefold())
                if at >= 0:
                    return unit, at, at + len(needle)
        return units[0], None, None

    # Edges

    def _edge(
        self,
        predicate: str,
        subject: Entity | Scene,
        obj: Entity | Scene,
        scene: Scene,
        confidence: float,
    ) -> None:
        subject_kind = "scene" if isinstance(subject, Scene) else "entity"
        object_kind = "scene" if isinstance(obj, Scene) else "entity"
        try:
            validate_edge(
                predicate,
                subject_kind,
                object_kind,
                subject.entity_type if subject_kind == "entity" else None,
                obj.entity_type if object_kind == "entity" else None,
            )
        except SignatureError as error:
            logger.info("skipping %s: %s", predicate, error)
            self.counts.skipped += 1
            return

        cited = subject if subject_kind == "entity" else obj
        if isinstance(cited, Entity):
            located = self._evidence(scene, cited)
        else:
            # A heading-only scene has an entry in self.units holding an empty
            # list, so indexing it directly would crash on such a scene.
            scene_units = self.units.get(scene.id) or [None]
            located = (scene_units[0], None, None)
        if located is None or located[0] is None:
            self.counts.skipped += 1
            return
        unit, start, end = located

        subject_id, object_id = canonical_endpoints(predicate, subject.id, obj.id)
        assertion = Assertion(
            script_id=self.script.id,
            subject_kind=subject_kind,
            subject_entity_id=subject_id if subject_kind == "entity" else None,
            subject_scene_id=subject_id if subject_kind == "scene" else None,
            predicate=predicate,
            object_kind=object_kind,
            object_entity_id=object_id if object_kind == "entity" else None,
            object_scene_id=object_id if object_kind == "scene" else None,
            source_unit_id=unit.id,
            evidence_start=start,
            evidence_end=end,
            confidence=confidence,
            provenance="system",
        )
        existing = self.session.scalar(
            select(Assertion).where(
                Assertion.script_id == self.script.id,
                Assertion.dedupe_key == assertion.compute_dedupe_key(),
                Assertion.active.is_(True),
            )
        )
        if existing is None:
            self.session.add(assertion)
            self.counts.assertions += 1

    def _seed_occurs_at(self) -> None:
        """Derive occurs_at from headings: the longest location name wins."""
        locations = [
            entity
            for entity in self.entities.values()
            if entity.entity_type == "location"
        ]
        for scene in self.scenes.values():
            heading = normalize(scene.heading or "")
            best = None
            for location in locations:
                if location.normalized_name in heading and (
                    best is None
                    or len(location.normalized_name) > len(best.normalized_name)
                ):
                    best = location
            if best is not None:
                self._edge("occurs_at", scene, best, scene, 0.9)

    def _seed_speaker_appearances(self) -> None:
        """A speaking character appears in the scene it speaks in."""
        for scene in self.scenes.values():
            seen: set[str] = set()
            for unit in self.units.get(scene.id, []):
                if not unit.speaker_name:
                    continue
                entity = self._find(unit.speaker_name)
                if entity is None or entity.entity_type != "cast":
                    continue
                if entity.normalized_name in seen:
                    continue
                seen.add(entity.normalized_name)
                self._edge("appears_in", entity, scene, scene, 0.9)

    def _seed_chains(self) -> None:
        for chain in self.truth.chains:
            entity = self._find(chain.entity)
            if entity is None:
                continue
            established = self.scenes.get(chain.establishes)
            if established is not None:
                self._edge("establishes", established, entity, established, 0.9)
                if entity.entity_type != "location":
                    self._edge("appears_in", entity, established, established, 0.9)
            for number in chain.dependants:
                scene = self.scenes.get(number)
                if scene is None:
                    continue
                if entity.entity_type == "location":
                    self._edge("occurs_at", scene, entity, scene, 0.9)
                else:
                    self._edge("appears_in", entity, scene, scene, 0.88)

    def _seed_scene_assertions(self) -> None:
        for number, subject_name, predicate, object_name, confidence in (
            self.truth.scene_assertions
        ):
            scene = self.scenes.get(number)
            if scene is None:
                continue
            subject = scene if subject_name == "scene" else self._find(subject_name)
            obj = scene if object_name == "scene" else self._find(object_name)
            if subject is None or obj is None:
                self.counts.skipped += 1
                continue
            self._edge(predicate, subject, obj, scene, confidence)

    def _seed_attributes(self) -> None:
        for entity_name, key, value, scene_number in self.truth.attributes:
            entity = self._find(entity_name)
            if entity is None:
                continue
            token = normalize_key(key)
            active = self.session.scalar(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == entity.id,
                    EntityAttribute.key == token,
                    EntityAttribute.active.is_(True),
                )
            )
            if active is not None:
                continue
            scene = self.scenes.get(scene_number)
            unit, start, end = None, None, None
            if scene is not None:
                located = self._evidence(scene, entity)
                if located is not None:
                    unit, start, end = located
            self.session.add(
                EntityAttribute(
                    entity_id=entity.id,
                    key=token,
                    value=value,
                    source_unit_id=unit.id if unit is not None else None,
                    evidence_start=start,
                    evidence_end=end,
                    confidence=0.9,
                    provenance="system",
                )
            )
            self.counts.attributes += 1


def seed_graph(session: Session, script: Script, truth: GroundTruth) -> SeedCounts:
    """Write one script's ground-truth graph and mark it ready."""
    counts = _Seeder(session, script, truth).run()
    script.graph_status = "ready"
    session.flush()
    return counts


def _backfill_origin(session: Session, titles: set[str]) -> None:
    """Re-mark pre-`origin` demo scripts as bundled.

    The column migration stamps every existing script "upload". A demo script
    is distinguishable from an upload sharing its title: its graph was seeded
    (system-provenance assertions exist) and extraction never ran on it. A
    title match alone is not evidence, so a script with no graph rows at all
    stays an upload and is never seeded.
    """
    for script in session.scalars(
        select(Script).where(Script.origin == "upload", Script.title.in_(titles))
    ):
        ran_extraction = session.scalar(
            select(func.count())
            .select_from(ExtractionRun)
            .where(ExtractionRun.script_id == script.id)
        )
        if ran_extraction:
            continue
        provenances = set(
            session.scalars(
                select(Assertion.provenance)
                .where(Assertion.script_id == script.id)
                .distinct()
            )
        )
        if provenances == {"system"}:
            script.origin = "bundled"
            logger.info("re-marked %s as a bundled demo script", script.title)
    session.flush()


def seed_demo_graphs(session: Session, directory: Path) -> int:
    """Seed every bundled demo script that matches a ground truth and has no graph.

    Runs at startup. Only scripts marked `origin == "bundled"` are candidates:
    a user's upload sharing a demo title builds its graph by extraction, never
    from the bundled ground truth. `graph_status == "ready"` is the completion
    marker, so a library that was seeded before is not re-scanned, and a graph
    the user cleared (status back to `not_analysed`) is rebuilt on the next
    start.
    """
    seeded = 0
    truths = {truth.title: truth for truth in ground_truths(directory)}
    if not truths:
        return 0
    _backfill_origin(session, set(truths))
    for script in session.scalars(select(Script)):
        truth = truths.get(script.title)
        if truth is None or script.origin != "bundled":
            continue
        if script.graph_status == "ready":
            continue
        counts = seed_graph(session, script, truth)
        logger.info(
            "seeded %s: %d entities, %d aliases, %d attributes, %d assertions",
            script.title,
            counts.entities,
            counts.aliases,
            counts.attributes,
            counts.assertions,
        )
        seeded += 1
    return seeded
