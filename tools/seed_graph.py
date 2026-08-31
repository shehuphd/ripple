#!/usr/bin/env python3
"""Seed the demo corpus's ground-truth graphs from dependencies.md.

The application does this itself at startup for any demo script without a
graph. This command exists to re-run it by hand, for development, after a
`Clear all graphs`, or to verify the ground-truth files parse.

    tools/.venv/bin/python tools/seed_graph.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from ripple.db.models import Script
from ripple.db.session import (
    create_all,
    create_db_engine,
    session_factory,
    session_scope,
)
from ripple.graph.fixtures import ground_truths, seed_demo_graphs

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    truths = ground_truths(DEMO_SCRIPTS)
    if not truths:
        logging.error("No dependencies.md files found under %s.", DEMO_SCRIPTS.name)
        return 1
    for truth in truths:
        logging.info(
            "%s: %d entities, %d attributes, %d chains, %d scene assertions",
            truth.title,
            len(truth.entities),
            len(truth.attributes),
            len(truth.chains),
            len(truth.scene_assertions),
        )

    engine = create_db_engine()
    create_all(engine)
    factory = session_factory(engine)
    with session_scope(factory) as session:
        titles = {truth.title for truth in truths}
        imported = {
            script.title
            for script in session.scalars(select(Script))
            if script.title in titles
        }
        if not imported:
            logging.error(
                "None of the demo scripts are in the database yet, so there "
                "is nothing to seed. Launch the app once; the library "
                "imports the corpus on first run."
            )
            return 1
        seeded = seed_demo_graphs(session, DEMO_SCRIPTS)
    logging.info("Seeded %d script(s). Already-ready scripts are untouched.", seeded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
