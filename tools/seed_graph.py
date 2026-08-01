#!/usr/bin/env python3
"""Populate a graph from a demo script's documented ground truth.

For UI development and demo rehearsal only. It writes the assertions that
`demo-scripts/01-night-freight/dependencies.md` section 3 says a correct
extraction produces, so the graph views can be built and reviewed without
spending credit on a live model.

Rows are written with `provenance="system"`, which distinguishes them from
`model` rows a real extraction produces. Nothing calls this automatically.

    tools/.venv/bin/python tools/seed_graph.py
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import select

from ripple.db.models import Assertion, Entity, Scene, Script, ScriptUnit
from ripple.db.naming import normalize
from ripple.db.session import create_db_engine, session_factory, session_scope

logger = logging.getLogger("ripple.seed")

SCRIPT_TITLE = "NIGHT FREIGHT"
SCENE_NUMBER = "14"

# demo-scripts/01-night-freight/dependencies.md section 3.
ENTITIES = [
    ("Blue sedan", "transportation"),
    ("Dead forklift", "set_design"),
    ("Pallet jack", "prop"),
    ("Mara Okonjo", "cast"),
    ("Loading dock", "location"),
    ("Sodium wash", "set_design"),
    ("Engine idle", "sound"),
]
EDGES = [
    ("scene", None, "occurs_at", "entity", "Loading dock", 0.92),
    ("scene", None, "establishes", "entity", "Blue sedan", 0.90),
    ("scene", None, "establishes", "entity", "Dead forklift", 0.88),
    ("entity", "Blue sedan", "appears_in", "scene", None, 0.91),
    ("entity", "Dead forklift", "appears_in", "scene", None, 0.87),
    ("entity", "Pallet jack", "appears_in", "scene", None, 0.86),
    ("entity", "Mara Okonjo", "appears_in", "scene", None, 0.93),
    ("entity", "Mara Okonjo", "travels_by", "entity", "Blue sedan", 0.86),
    ("scene", None, "requires", "entity", "Sodium wash", 0.68),
    ("scene", None, "requires", "entity", "Pallet jack", 0.84),
    ("scene", None, "requires", "entity", "Engine idle", 0.74),
]


def main() -> int:
    """Write the ground-truth graph for scene 14 of the first demo script."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    factory = session_factory(create_db_engine())

    with session_scope(factory) as session:
        script = session.scalar(select(Script).where(Script.title == SCRIPT_TITLE))
        if script is None:
            logger.error("%s is not in the library. Start the app once first.", SCRIPT_TITLE)
            return 1

        scene = session.scalar(
            select(Scene).where(
                Scene.script_id == script.id,
                Scene.display_scene_number == SCENE_NUMBER,
            )
        )
        unit = session.scalar(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene.id, ScriptUnit.unit_type == "action")
            .order_by(ScriptUnit.sequence_index)
        )
        if scene is None or unit is None:
            logger.error("Scene %s has no action unit to cite.", SCENE_NUMBER)
            return 1

        resolved: dict[str, Entity] = {}
        for name, entity_type in ENTITIES:
            key = normalize(name)
            entity = session.scalar(
                select(Entity).where(
                    Entity.script_id == script.id,
                    Entity.entity_type == entity_type,
                    Entity.normalized_name == key,
                )
            )
            if entity is None:
                entity = Entity(
                    script_id=script.id, entity_type=entity_type,
                    canonical_name=name, normalized_name=key,
                )
                session.add(entity)
                session.flush()
            resolved[name] = entity

        written = 0
        for subject_kind, subject, predicate, object_kind, obj, confidence in EDGES:
            assertion = Assertion(
                script_id=script.id,
                subject_kind=subject_kind,
                subject_entity_id=resolved[subject].id if subject else None,
                subject_scene_id=scene.id if subject_kind == "scene" else None,
                predicate=predicate,
                object_kind=object_kind,
                object_entity_id=resolved[obj].id if obj else None,
                object_scene_id=scene.id if object_kind == "scene" else None,
                source_unit_id=unit.id,
                confidence=confidence,
                provenance="system",
            )
            existing = session.scalar(
                select(Assertion).where(
                    Assertion.script_id == script.id,
                    Assertion.dedupe_key == assertion.compute_dedupe_key(),
                )
            )
            if existing is None:
                session.add(assertion)
                written += 1

        script.graph_status = "partially_ready"
        session.flush()

    logger.info("Wrote %d assertions for scene %s of %s.", written, SCENE_NUMBER, SCRIPT_TITLE)
    logger.info("These are ground truth from dependencies.md, not a model's output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
