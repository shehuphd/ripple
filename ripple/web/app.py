"""The Ripple web application.

A thin HTTP layer over the services. Every route resolves a session, calls into
`ripple.*`, and returns data; no business rule lives here, so the same
operations stay testable without a client.

The extraction loop is browser-driven, per PRD section 10: the page asks for one
scene at a time and the server commits each independently. That keeps the work
inside request handlers, with no background worker to pay for.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ripple.adapters import import_screenplay
from ripple.adapters.base import MAX_UPLOAD_BYTES
from ripple.config.secrets import SecretStore
from ripple.db.models import (
    Assertion,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    ExtractionRun,
    Import,
    QueryLog,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.repository import (
    clear_all_graphs,
    delete_script,
    deletion_preview,
    persist_import,
)
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction.service import (
    claim_next_scene,
    extract_scene,
    progress,
    start_run,
)
from ripple.graph.continuity import detect_orphaned_references, retrieve
from ripple.graph.diff import Edge, EdgeRef, diff_edges, to_operations
from ripple.graph.layout import DEPARTMENT_ORDER
from ripple.graph.layout import layout as graph_layout
from ripple.llm import ProviderError, get_provider
from ripple.services import changeset
from ripple.services.settings import SettingsService
from ripple.services.synthesizer import answer_question, synthesize
from ripple.tracing import configure_tracing
from ripple.web.stats import eighths, page_of, runtime, script_pages

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
DEMO_SCRIPTS = HERE.parent.parent / "demo-scripts"

_engine = None
_sessions = None


def get_session() -> Session:
    """One session per request, committed on success and rolled back on error."""
    session = _sessions()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def seed_demo_corpus(session: Session) -> int:
    """Import the bundled screenplays on an empty database.

    The application is worth opening on first run rather than showing an empty
    library and asking for an upload before anything can be explored.
    """
    if session.scalar(select(func.count()).select_from(Script)):
        return 0
    imported = 0
    for path in sorted(DEMO_SCRIPTS.glob("*/*.fountain")):
        result = import_screenplay(path.read_bytes(), path.name)
        if result.accepted:
            persist_import(session, result)
            imported += 1
    session.commit()
    logger.info("seeded %d demo screenplays", imported)
    return imported


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepare the database and load any locally stored credentials."""
    global _engine, _sessions
    configure_tracing()
    SecretStore().load()
    _engine = create_db_engine()
    create_all(_engine)
    _sessions = session_factory(_engine)
    with _sessions() as session:
        seed_demo_corpus(session)
    yield


app = FastAPI(title="Ripple", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
settings_service = SettingsService()


def asset_version() -> str:
    """A cache key for the static assets, from their newest modification time.

    Without it a browser keeps a stylesheet it already has and the page runs
    old CSS against new markup. That failure is invisible: the file on disk is
    correct, the served file is correct, and only the loaded sheet is stale.
    """
    newest = max(
        (
            path.stat().st_mtime
            for path in (HERE / "static").rglob("*")
            if path.is_file()
        ),
        default=0.0,
    )
    return f"{int(newest)}"


templates.env.globals["asset_version"] = asset_version()


@app.exception_handler(ProviderError)
async def provider_error_handler(_request: Request, error: ProviderError):
    """Provider failures are expected states, not server faults."""
    return JSONResponse(
        status_code=400, content={"code": error.code, "message": error.message}
    )


@app.exception_handler(changeset.StaleProposal)
async def stale_handler(_request: Request, error: changeset.StaleProposal):
    """A stale proposal is a 409: the client must regenerate, not retry."""
    return JSONResponse(
        status_code=409, content={"code": error.code, "message": error.message}
    )


@app.exception_handler(changeset.InvalidOperation)
async def invalid_operation_handler(
    _request: Request, error: changeset.InvalidOperation
):
    return JSONResponse(
        status_code=400, content={"code": error.code, "message": error.message}
    )


def sidebar_counts(session: Session) -> dict[str, int]:
    """The figures beside each sidebar entry."""

    def count(model, *where) -> int:
        return (
            session.scalar(select(func.count()).select_from(model).where(*where)) or 0
        )

    return {
        "scripts": count(Script),
        "recent": count(Script, Script.last_opened_at.is_not(None)),
        "needs_review": count(Script, Script.import_status == "needs_review"),
        "reports": count(RippleReport),
        "findings": count(ContinuityFinding, ContinuityFinding.status == "open"),
        "queries": count(QueryLog),
        "entities": count(Entity),
        "assertions": count(Assertion, Assertion.active.is_(True)),
    }


OUTCOME_LABELS = {
    "accepted": "Accepted",
    "accepted_with_warnings": "Accepted with warnings",
    "needs_review": "Needs review",
    "rejected": "Rejected",
}
FORMAT_LABELS = {
    "fountain": "Fountain",
    "fdx": "Final Draft",
    "pdf": "PDF",
    "plain_text": "Plain text",
}


# Pages


@app.get("/")
def library(
    request: Request, filter: str = "all", session: Session = Depends(get_session)
):
    """The script library."""
    query = select(Script).order_by(Script.created_at.desc())
    if filter == "review":
        query = query.where(Script.import_status == "needs_review")
    elif filter == "recent":
        query = (
            select(Script)
            .where(Script.last_opened_at.is_not(None))
            .order_by(Script.last_opened_at.desc())
        )
    scripts = list(session.scalars(query))

    rows = []
    for script in scripts:
        record = session.scalar(
            select(Import).where(Import.script_id == script.id).limit(1)
        )
        pages = script_pages(session, script.id)
        rows.append(
            {
                "id": str(script.id),
                "title": script.title,
                "format": FORMAT_LABELS.get(
                    record.detected_format if record else "", "Unknown"
                ),
                "pages": pages,
                "scenes": session.scalar(
                    select(func.count())
                    .select_from(Scene)
                    .where(Scene.script_id == script.id)
                ),
                "runtime": runtime(pages),
                "outcome": script.import_status,
                "outcome_label": OUTCOME_LABELS.get(
                    script.import_status, script.import_status
                ),
            }
        )

    _, model = settings_service.selected_model(session)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "rows": rows,
            "counts": sidebar_counts(session),
            "active": {"review": "review", "recent": "recent"}.get(filter, "all"),
            "heading": {
                "review": "Needs review",
                "recent": "Recently opened",
            }.get(filter, "All scripts"),
            "subtitle": {
                "review": "Imports that need a human to look before the graph is built",
                "recent": "Ordered by when you last opened them",
            }.get(filter, "Fountain, Final Draft XML, PDF, plain text"),
            "empty_message": {
                "review": "No import needs review.",
                "recent": "No scripts opened yet. Open one from All scripts.",
            }.get(filter, "No scripts yet. Import one above."),
            "model": model,
            "max_bytes": MAX_UPLOAD_BYTES,
        },
    )


@app.get("/scripts/{script_id}")
def reader(request: Request, script_id: str, session: Session = Depends(get_session)):
    """The reader: scenes, units, and the requirement pane."""
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")

    # Opening is what "Recently opened" means, so record it here rather than
    # relying on updated_at, which moves on any write.
    script.last_opened_at = datetime.now(UTC)

    _, model = settings_service.selected_model(session)
    page_total = script_pages(session, script.id)
    scenes = []
    running = 0
    for scene in script.scenes:
        characters = sum(len(unit.current_text) for unit in scene.units)
        entity_ids = set(
            session.scalars(
                select(Assertion.subject_entity_id).where(
                    Assertion.subject_scene_id == scene.id, Assertion.active.is_(True)
                )
            )
        ) | set(
            session.scalars(
                select(Assertion.object_entity_id).where(
                    Assertion.object_scene_id == scene.id, Assertion.active.is_(True)
                )
            )
        )
        scenes.append(
            {
                "id": str(scene.id),
                # An unnumbered scene shows no number rather than a position.
                # Intercut sub-scenes carry no number of their own, and
                # substituting sequence_index + 1 invents one that collides
                # with the real scene holding that number later in the script.
                "number": scene.display_scene_number or "",
                "heading": scene.heading,
                "page": page_of(running),
                "eighths": eighths(characters),
                "entities": len([e for e in entity_ids if e]),
                "units": [
                    {
                        "id": str(unit.id),
                        "type": unit.unit_type,
                        "text": unit.current_text,
                    }
                    for unit in scene.units
                    if unit.unit_type != "scene_heading"
                ],
            }
        )
        running += characters

    findings = session.scalar(
        select(func.count())
        .select_from(ContinuityFinding)
        .join(ChangeSet)
        .where(ChangeSet.script_id == script.id, ContinuityFinding.status == "open")
    )
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "script": script,
            "scenes": scenes,
            "pages": page_total,
            "findings": findings,
            "model": model,
            "counts": sidebar_counts(session),
        },
    )


@app.get("/ask")
def ask_page(
    request: Request, script: str | None = None, session: Session = Depends(get_session)
):
    """Grounded natural-language query over one script's accepted graph."""
    chosen = session.get(Script, _uuid(script)) if script else None
    if chosen is None:
        chosen = session.scalar(select(Script).order_by(Script.created_at.desc()))
    counts = sidebar_counts(session)
    return templates.TemplateResponse(
        request,
        "ask.html",
        {
            "script": chosen,
            "counts": counts,
            "assertions": (
                session.scalar(
                    select(func.count())
                    .select_from(Assertion)
                    .where(
                        Assertion.script_id == chosen.id if chosen else False,
                        Assertion.active.is_(True),
                    )
                )
                if chosen
                else 0
            ),
            "entities": (
                session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(Entity.script_id == chosen.id)
                )
                if chosen
                else 0
            ),
            "lock": _graph_lock(session) if chosen else None,
        },
    )


def _list_page(request, session, **kwargs):
    """Render the shared list template with the sidebar counts filled in."""
    return templates.TemplateResponse(
        request, "list.html", {"counts": sidebar_counts(session), **kwargs}
    )


def _graph_lock(session) -> dict[str, str] | None:
    """Why the graph pages are empty, when they are.

    An empty page with no explanation reads as a broken feature. Naming the
    missing step, and linking to it, is the difference.
    """
    if session.scalar(select(func.count()).select_from(Assertion)):
        return None
    _, model = settings_service.selected_model(session)
    if not model:
        return {
            "message": "No graph has been built yet, and no model is selected.",
            "action": "Choose a model in Settings",
            "href": "/settings",
        }
    return {
        "message": "No graph has been built yet.",
        "action": "Open a script and build its graph",
        "href": "/",
    }


@app.get("/reports")
def reports_page(request: Request, session: Session = Depends(get_session)):
    """Every ripple report, newest first."""
    rows = session.execute(
        select(RippleReport, ChangeSet, Script)
        .join(ChangeSet, RippleReport.change_set_id == ChangeSet.id)
        .join(Script, ChangeSet.script_id == Script.id)
        .order_by(RippleReport.generated_at.desc())
    ).all()
    items = [
        {
            "tag": report.severity,
            "tag_class": {"high": "stunt", "medium": "prop", "low": "set_design"}.get(
                report.severity, ""
            ),
            "title": report.summary,
            "sub": f"{script.title} · {change_set.kind} · {change_set.status}"
            + (f" · {report.model_id}" if report.model_id else " · deterministic"),
            "right": report.generated_at.strftime("%d %b %H:%M"),
        }
        for report, change_set, script in rows
    ]
    return _list_page(
        request,
        session,
        heading="Ripple reports",
        active="reports",
        subtitle=f"{len(items)} report(s) · one per proposal",
        items=items,
        empty="No reports yet. Edit a line and press See ripple.",
        lock=None,
    )


@app.get("/findings")
def findings_page(request: Request, session: Session = Depends(get_session)):
    """Continuity findings across every script."""
    rows = session.execute(
        select(ContinuityFinding, Script)
        .join(ChangeSet, ContinuityFinding.change_set_id == ChangeSet.id)
        .join(Script, ChangeSet.script_id == Script.id)
        .order_by(ContinuityFinding.created_at.desc())
    ).all()
    items = [
        {
            "tag": finding.status,
            "tag_class": {
                "open": "stunt",
                "dismissed": "",
                "resolved": "set_design",
            }.get(finding.status, ""),
            "title": finding.message,
            "sub": f"{script.title} · {finding.finding_type} · {finding.severity}"
            + (
                f" · dismissed: {finding.dismissal_reason}"
                if finding.dismissal_reason
                else ""
            ),
            "right": finding.created_at.strftime("%d %b %H:%M"),
        }
        for finding, script in rows
    ]
    return _list_page(
        request,
        session,
        heading="Continuity findings",
        active="findings",
        subtitle=f"{len(items)} finding(s) · warnings do not block a decision",
        items=items,
        empty="No findings yet.",
        lock=None,
    )


@app.get("/entities")
def entities_page(request: Request, session: Session = Depends(get_session)):
    """Every extracted entity, with its aliases and how often it is asserted."""
    rows = session.execute(
        select(Entity, Script)
        .join(Script, Entity.script_id == Script.id)
        .order_by(Entity.entity_type, Entity.canonical_name)
    ).all()
    items = []
    for entity, script in rows:
        aliases = list(
            session.scalars(
                select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
            )
        )
        uses = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.active.is_(True),
                (Assertion.subject_entity_id == entity.id)
                | (Assertion.object_entity_id == entity.id),
            )
        )
        items.append(
            {
                "tag": entity.entity_type.replace("_", " "),
                "tag_class": entity.entity_type,
                "title": entity.canonical_name,
                "sub": f"{script.title}"
                + (
                    f" · also: {', '.join(sorted(set(aliases))[:4])}" if aliases else ""
                ),
                "right": f"{uses} assertions",
            }
        )
    return _list_page(
        request,
        session,
        heading="Entities",
        active="entities",
        subtitle=f"{len(items)} entity(s) across every script",
        items=items,
        empty="No entities yet.",
        lock=_graph_lock(session),
    )


@app.get("/assertions")
def assertions_page(request: Request, session: Session = Depends(get_session)):
    """Every active assertion, with the unit that supports it."""
    scripts = {script.id: script for script in session.scalars(select(Script))}
    items = []
    for script_id, script in scripts.items():
        labels = _labels(session, script_id)
        rows = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script_id, Assertion.active.is_(True)
                )
            )
        )
        for row in rows[:500]:
            subject = labels.get(row.subject_entity_id or row.subject_scene_id, "?")
            obj = labels.get(row.object_entity_id or row.object_scene_id, "?")
            items.append(
                {
                    "tag": row.predicate,
                    "tag_class": "location",
                    "title": f"{subject} → {obj}",
                    "sub": f"{script.title} · {row.provenance}"
                    + (f" · {row.model_id}" if row.model_id else ""),
                    "right": f"{row.confidence:.2f}",
                }
            )
    return _list_page(
        request,
        session,
        heading="Assertions",
        active="assertions",
        subtitle=f"{len(items)} active assertion(s)",
        items=items,
        empty="No assertions yet.",
        lock=_graph_lock(session),
    )


@app.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    """Provider credentials and model selection."""
    provider, model = settings_service.selected_model(session)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "statuses": settings_service.provider_statuses(),
            "selected_provider": provider,
            "selected_model": model,
            "counts": sidebar_counts(session),
        },
    )


# Scripts


@app.post("/api/scripts")
async def upload_script(
    file: UploadFile = File(...), session: Session = Depends(get_session)
):
    """Import an uploaded screenplay.

    A rejection is a 400 with the adapter's stable code, not a 500: an
    unparseable file is an expected outcome the UI has to explain.
    """
    data = await file.read()
    result = import_screenplay(data, file.filename or "upload")
    if not result.accepted:
        return JSONResponse(
            status_code=400,
            content={
                "code": result.rejection_code,
                "message": result.rejection_message,
            },
        )
    script = persist_import(session, result)
    return {
        "id": str(script.id),
        "title": script.title,
        "outcome": result.outcome.value,
        "scenes": result.scene_count,
        "units": result.unit_count,
        "warnings": [{"code": w.code, "message": w.message} for w in result.warnings],
    }


@app.delete("/api/scripts/{script_id}")
def remove_script(script_id: str, session: Session = Depends(get_session)):
    """Delete one script and everything under it."""
    counts = delete_script(session, _uuid(script_id))
    return counts.__dict__


@app.get("/api/scripts/{script_id}/deletion-preview")
def preview_deletion(script_id: str, session: Session = Depends(get_session)):
    """What deleting this script would remove. PRD section 9."""
    return deletion_preview(session, _uuid(script_id)).__dict__


@app.post("/api/graphs/clear")
def clear_graphs(session: Session = Depends(get_session)):
    """Delete graph data while preserving scripts and parsed units."""
    return clear_all_graphs(session).__dict__


@app.get("/api/scenes/{scene_id}/units")
def scene_units(scene_id: str, session: Session = Depends(get_session)):
    """The units of one scene, for the reader."""
    units = session.scalars(
        select(ScriptUnit)
        .where(ScriptUnit.scene_id == _uuid(scene_id))
        .order_by(ScriptUnit.sequence_index)
    )
    return [
        {
            "id": str(unit.id),
            "type": unit.unit_type,
            "text": unit.current_text,
            "speaker": unit.speaker_name,
            "confidence": unit.parser_confidence,
            "method": unit.parser_method,
        }
        for unit in units
    ]


@app.get("/api/units/{unit_id}/requirements")
def unit_requirements(unit_id: str, session: Session = Depends(get_session)):
    """Entities and assertions this unit supports, with their evidence."""
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")

    assertions = list(
        session.scalars(
            select(Assertion).where(
                Assertion.source_unit_id == unit.id, Assertion.active.is_(True)
            )
        )
    )
    scene = session.get(Scene, unit.scene_id)
    labels = _labels(session, scene.script_id)
    entity_ids = {
        endpoint
        for assertion in assertions
        for endpoint in (assertion.subject_entity_id, assertion.object_entity_id)
        if endpoint
    }
    entities = (
        [
            {"id": str(e.id), "name": e.canonical_name, "type": e.entity_type}
            for e in session.scalars(select(Entity).where(Entity.id.in_(entity_ids)))
        ]
        if entity_ids
        else []
    )

    return {
        "unit": {"id": str(unit.id), "type": unit.unit_type, "text": unit.current_text},
        "scene": {
            "id": str(scene.id),
            "number": scene.display_scene_number,
            "heading": scene.heading,
        },
        "entities": sorted(entities, key=lambda e: e["name"]),
        "assertions": [_assertion_payload(a, labels) for a in assertions],
    }


@app.get("/api/units/{unit_id}/graph")
def unit_graph(
    unit_id: str,
    depth: int = 1,
    min_confidence: float = 0.0,
    include_removed: bool = False,
    departments: str | None = None,
    session: Session = Depends(get_session),
):
    """The graph around one unit, with deterministic positions.

    `depth` 1 returns the unit's own scene and its edges; 2 adds one hop out
    from every entity found, which is where a coordinator sees that a prop is
    shared with three other scenes.
    """
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    scene = session.get(Scene, unit.scene_id)
    labels = _labels(session, scene.script_id)
    wanted = set(departments.split(",")) if departments else None

    seed = list(
        session.scalars(
            select(Assertion).where(
                Assertion.script_id == scene.script_id,
                Assertion.active.is_(True) if not include_removed else True,
                (Assertion.source_unit_id == unit.id)
                | (Assertion.subject_scene_id == scene.id)
                | (Assertion.object_scene_id == scene.id),
            )
        )
    )
    edges = list(seed)

    if depth > 1:
        entity_ids = {
            endpoint
            for assertion in seed
            for endpoint in (assertion.subject_entity_id, assertion.object_entity_id)
            if endpoint
        }
        if entity_ids:
            edges.extend(
                session.scalars(
                    select(Assertion).where(
                        Assertion.script_id == scene.script_id,
                        Assertion.active.is_(True) if not include_removed else True,
                        Assertion.id.notin_([a.id for a in seed]),
                        Assertion.subject_entity_id.in_(entity_ids)
                        | Assertion.object_entity_id.in_(entity_ids),
                    )
                )
            )

    below = 0
    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    for assertion in edges:
        if assertion.confidence < min_confidence:
            below += 1
            continue
        subject = assertion.subject_entity_id or assertion.subject_scene_id
        obj = assertion.object_entity_id or assertion.object_scene_id
        ends = (
            (subject, assertion.subject_kind, assertion.subject_entity_id),
            (obj, assertion.object_kind, assertion.object_entity_id),
        )
        types = [
            _entity_type(session, entity_id) if kind == "entity" else None
            for _, kind, entity_id in ends
        ]
        if wanted and not any(t in wanted for t in types if t):
            continue
        for (node_id, kind, _), entity_type in zip(ends, types):
            nodes.setdefault(
                str(node_id),
                {
                    "id": str(node_id),
                    "label": labels.get(node_id, "?"),
                    "kind": kind,
                    "entity_type": entity_type,
                },
            )
        links.append(
            {
                "source": str(subject),
                "target": str(obj),
                "predicate": assertion.predicate,
                "confidence": round(assertion.confidence, 2),
                "removed": not assertion.active,
            }
        )

    # The unit's own scene anchors the view even when nothing cites it yet, so
    # an empty graph still shows where the selection sits.
    nodes.setdefault(
        str(scene.id),
        {
            "id": str(scene.id),
            "label": labels.get(scene.id, "Scene"),
            "kind": "scene",
            "entity_type": None,
        },
    )

    placed = graph_layout(list(nodes.values()), focus_id=str(scene.id))
    positions = {p.id: p for p in placed}
    return {
        "focus": str(scene.id),
        "nodes": [
            {
                **nodes[p.id],
                "x": round(p.x, 4),
                "y": round(p.y, 4),
                "ring": p.ring,
            }
            for p in placed
        ],
        "links": [
            link
            for link in links
            if link["source"] in positions and link["target"] in positions
        ],
        "departments": sorted(
            {n["entity_type"] for n in nodes.values() if n["entity_type"]}
        ),
        "hidden_below_threshold": below,
    }


@app.get("/graph/{unit_id}")
def graph_page(request: Request, unit_id: str, session: Session = Depends(get_session)):
    """The expanded graph view for one unit."""
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    scene = session.get(Scene, unit.scene_id)
    script = session.get(Script, scene.script_id)

    by_department = dict(
        session.execute(
            select(Entity.entity_type, func.count())
            .where(Entity.script_id == script.id)
            .group_by(Entity.entity_type)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "graph.html",
        {
            "script": script,
            "scene": scene,
            "unit": unit,
            "departments": [
                {"name": name, "count": by_department.get(name, 0)}
                for name in DEPARTMENT_ORDER
                if by_department.get(name)
            ],
            "total_departments": len(DEPARTMENT_ORDER),
            "counts": sidebar_counts(session),
            "lock": _graph_lock(session),
        },
    )


# Extraction


@app.post("/api/scripts/{script_id}/extract")
def begin_extraction(script_id: str, session: Session = Depends(get_session)):
    """Create an extraction run for a script."""
    provider_name, model_id = settings_service.selected_model(session)
    if not provider_name or not model_id:
        raise HTTPException(400, "Choose a provider and model in Settings first.")
    run = start_run(session, _uuid(script_id), model_id)
    return progress(session, run.id).__dict__


@app.post("/api/extract/{run_id}/next")
def extract_next(run_id: str, session: Session = Depends(get_session)):
    """Do one scene. The browser calls this in a loop until nothing is pending."""
    provider_name, _ = settings_service.selected_model(session)
    if not provider_name:
        raise HTTPException(400, "No provider selected.")
    provider = get_provider(provider_name)

    job = claim_next_scene(session, _uuid(run_id))
    if job is None:
        return {"done": True, "progress": progress(session, _uuid(run_id)).__dict__}
    outcome = extract_scene(session, job, provider)
    session.commit()
    return {
        "done": False,
        "scene": outcome.__dict__,
        "progress": progress(session, _uuid(run_id)).__dict__,
    }


@app.get("/api/extract/{run_id}/progress")
def extraction_progress(run_id: str, session: Session = Depends(get_session)):
    """Current run progress."""
    return progress(session, _uuid(run_id)).__dict__


@app.get("/api/scripts/{script_id}/runs")
def script_runs(script_id: str, session: Session = Depends(get_session)):
    """Extraction runs for a script, newest first."""
    runs = session.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.script_id == _uuid(script_id))
        .order_by(ExtractionRun.started_at.desc())
    )
    return [
        {
            "id": str(run.id),
            "status": run.status,
            "model_id": run.model_id,
            "total": run.total_scenes,
            "completed": run.completed_scenes,
            "failed": run.failed_scenes,
        }
        for run in runs
    ]


# Ripple preview


@app.post("/api/units/{unit_id}/preview")
def preview_ripple(
    unit_id: str,
    proposed_text: str = Form(...),
    session: Session = Depends(get_session),
):
    """Run the preview pipeline and record the proposal without applying it.

    Stage order follows PRD section 8: extract the proposed side, diff in code,
    retrieve continuity evidence, judge, synthesize. Timings are recorded per
    stage because the preview is the demo's centrepiece and a slow stage should
    be visible rather than inferred.
    """
    import time

    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    scene = session.get(Scene, unit.scene_id)
    labels = _labels(session, scene.script_id)
    stages: list[dict[str, Any]] = []

    def stage(name: str, started: float) -> None:
        stages.append(
            {"name": name, "seconds": round(time.perf_counter() - started, 2)}
        )

    mark = time.perf_counter()
    accepted_rows = list(
        session.scalars(
            select(Assertion).where(
                Assertion.source_unit_id == unit.id, Assertion.active.is_(True)
            )
        )
    )
    accepted = [_to_edge(session, row, labels) for row in accepted_rows]
    stage("Parse unit", mark)

    provider_name, model_id = settings_service.selected_model(session)
    provider = get_provider(provider_name) if provider_name else None

    mark = time.perf_counter()
    proposed = _extract_proposed(
        session, unit, scene, proposed_text, provider, model_id
    )
    stage("Extract assertions", mark)

    mark = time.perf_counter()
    diff = diff_edges(accepted, proposed)
    stage(
        f"Diff against base v{session.get(Script, scene.script_id).current_version}",
        mark,
    )

    mark = time.perf_counter()
    removed_establishes = [
        (row.object_entity_id, labels.get(row.object_entity_id, "?"), row.id)
        for row in accepted_rows
        if row.predicate == "establishes"
        and row.object_entity_id
        and any(
            edge.predicate == "establishes" and edge.assertion_id == str(row.id)
            for edge in diff.removed
        )
    ]
    orphans = detect_orphaned_references(session, scene.script_id, removed_establishes)
    affected = [
        row.object_entity_id or row.subject_entity_id
        for row in accepted_rows
        if row.object_entity_id or row.subject_entity_id
    ]
    packet = retrieve(session, scene.script_id, affected, scene.sequence_index)
    stage("Continuity sweep", mark)

    mark = time.perf_counter()
    synthesis = synthesize(diff, orphans, provider, model_id)
    stage("Synthesize", mark)

    proposal = changeset.create_proposal(
        session, unit.id, proposed_text, to_operations(diff)
    )
    proposal.severity = synthesis.severity
    for orphan in orphans:
        session.add(
            ContinuityFinding(
                change_set_id=proposal.id,
                finding_type="orphaned_reference",
                severity="high",
                message=orphan.message,
                status="open",
            )
        )
    session.add(
        RippleReport(
            change_set_id=proposal.id,
            summary=synthesis.summary,
            severity=synthesis.severity,
            model_id=synthesis.model_id,
            prompt_version=synthesis.prompt_version,
        )
    )
    session.flush()

    anchor = unit.anchors[0] if unit.anchors else None
    return {
        "change_set_id": str(proposal.id),
        "unit_id": str(unit.id),
        "scene_number": scene.display_scene_number or scene.sequence_index + 1,
        "accepted_text": unit.current_text,
        "proposed_text": proposed_text,
        "severity": synthesis.severity,
        "summary": synthesis.summary,
        "summary_source": synthesis.source,
        "model_id": synthesis.model_id,
        "diff": {
            "summary": diff.summary(),
            "operations": diff.operation_count,
            "removed": [_edge_payload(e) for e in diff.removed],
            "added": [_edge_payload(e) for e in diff.added],
            "changed": [
                {"before": _edge_payload(b), "after": _edge_payload(a)}
                for b, a in diff.changed
            ],
        },
        "findings": [
            {
                "severity": "high",
                "title": f"Later units still reference {orphan.entity_label}",
                "message": orphan.message,
                "cited_units": orphan.later_unit_ids,
                "scenes": orphan.later_scene_numbers,
            }
            for orphan in orphans
        ],
        "evidence_count": packet.total_items,
        "pipeline": stages,
        "origin": {
            "text": unit.current_text,
            "page": anchor.source_page_number if anchor else None,
            "start": anchor.source_start_offset if anchor else None,
            "end": anchor.source_end_offset if anchor else None,
            "method": anchor.extraction_method if anchor else None,
        },
    }


def _extract_proposed(
    session: Session, unit, scene, proposed_text: str, provider, model_id
) -> list[Edge]:
    """Extract typed assertions from the proposed text.

    With no provider the proposed side is empty, so the diff shows the removal
    side only. That is honest rather than complete: the preview says which
    assertions the edit drops, and says nothing about what replaces them.
    """
    if provider is None or not model_id:
        return []

    from ripple.extraction.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_prompt
    from ripple.extraction.validate import MalformedResponse, validate_response

    units = [(str(unit.id), unit.unit_type, proposed_text)]
    prompt = build_prompt(scene.heading, scene.display_scene_number, units)
    try:
        result = provider.generate(
            model_id,
            prompt,
            system=SYSTEM_PROMPT,
            max_output_tokens=2048,
            json_schema=OUTPUT_SCHEMA,
        )
        report = validate_response(result.text, {str(unit.id)})
    except (ProviderError, MalformedResponse) as error:
        logger.info("proposed-side extraction failed: %s", error)
        return []

    by_local = {entity.local_id: entity for entity in report.entities}
    edges: list[Edge] = []
    for assertion in report.assertions:
        subject = _proposed_endpoint(
            assertion.subject_kind, assertion.subject_local_id, by_local, scene
        )
        obj = _proposed_endpoint(
            assertion.object_kind, assertion.object_local_id, by_local, scene
        )
        if subject is None or obj is None:
            continue
        edges.append(
            Edge(
                subject=subject,
                predicate=assertion.predicate,
                obj=obj,
                confidence=assertion.confidence,
                source_unit_id=str(unit.id),
                display_subject=_display(subject, by_local, scene),
                display_object=_display(obj, by_local, scene),
            )
        )
    return edges


def _proposed_endpoint(kind: str, local_id: str, by_local, scene) -> EdgeRef | None:
    if kind == "scene":
        return EdgeRef.scene(scene.id)
    entity = by_local.get(local_id)
    return EdgeRef.entity(entity.canonical_name, entity.entity_type) if entity else None


def _display(ref: EdgeRef, by_local, scene) -> str:
    if ref.kind == "scene":
        return f"Sc {scene.display_scene_number or scene.sequence_index + 1}"
    for entity in by_local.values():
        if EdgeRef.entity(entity.canonical_name, entity.entity_type) == ref:
            return entity.canonical_name
    return ref.label


@app.post("/api/changes/{change_set_id}/accept")
def accept_change(change_set_id: str, session: Session = Depends(get_session)):
    """Apply a proposal atomically."""
    result = changeset.accept(session, _uuid(change_set_id))
    return result.__dict__


@app.post("/api/changes/{change_set_id}/reject")
def reject_change(
    change_set_id: str,
    reason: str = Form(None),
    session: Session = Depends(get_session),
):
    """Record a decision not to apply a proposal."""
    change_set = changeset.reject(session, _uuid(change_set_id), reason)
    return {"id": str(change_set.id), "status": change_set.status}


@app.post("/api/units/{unit_id}/undo")
def undo_change(unit_id: str, session: Session = Depends(get_session)):
    """Undo the latest accepted change on a unit."""
    return changeset.undo_latest(session, _uuid(unit_id)).__dict__


@app.post("/api/findings/{finding_id}/dismiss")
def dismiss_finding(
    finding_id: str,
    reason: str = Form(None),
    session: Session = Depends(get_session),
):
    """Dismiss a finding. Records a reason and fixes nothing."""
    finding = session.get(ContinuityFinding, _uuid(finding_id))
    if finding is None:
        raise HTTPException(404, "No such finding")
    finding.status = "dismissed"
    finding.dismissal_reason = reason
    session.flush()
    return {"id": str(finding.id), "status": finding.status}


@app.post("/api/scripts/{script_id}/ask")
def ask_graph(
    script_id: str,
    question: str = Form(...),
    session: Session = Depends(get_session),
):
    """Answer a question from accepted assertions only."""
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")

    labels = _labels(session, script.id)
    terms = [w.lower() for w in question.split() if len(w) > 3]
    rows = list(
        session.scalars(
            select(Assertion).where(
                Assertion.script_id == script.id, Assertion.active.is_(True)
            )
        )
    )
    scene_of = dict(
        session.execute(
            select(ScriptUnit.id, Scene.display_scene_number)
            .join(Scene)
            .where(Scene.script_id == script.id)
        ).all()
    )
    unit_text = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.current_text)
            .join(Scene)
            .where(Scene.script_id == script.id)
        ).all()
    )

    matched = []
    for row in rows:
        subject = labels.get(row.subject_entity_id or row.subject_scene_id, "")
        obj = labels.get(row.object_entity_id or row.object_scene_id, "")
        haystack = f"{subject} {row.predicate} {obj}".lower()
        if not terms or any(term in haystack for term in terms):
            matched.append(
                {
                    "id": str(row.id),
                    "subject": subject,
                    "predicate": row.predicate,
                    "object": obj,
                    "scene": scene_of.get(row.source_unit_id),
                    "unit_id": str(row.source_unit_id),
                    "unit_text": unit_text.get(row.source_unit_id, ""),
                    "confidence": row.confidence,
                }
            )

    provider_name, model_id = settings_service.selected_model(session)
    provider = get_provider(provider_name) if provider_name else None
    answer = answer_question(question, matched[:40], provider, model_id)

    session.add(
        QueryLog(
            script_id=script.id,
            question=question,
            answer=answer.answer,
            cited_assertion_ids_json=answer.cited_assertion_ids,
            model_id=answer.model_id,
            prompt_version=answer.prompt_version,
        )
    )
    session.flush()

    seen: set[str] = set()
    cited = []
    for item in matched[:40]:
        if item["unit_id"] in seen:
            continue
        seen.add(item["unit_id"])
        cited.append(
            {
                "scene": item["scene"],
                "unit_id": item["unit_id"],
                "text": item["unit_text"],
            }
        )

    confidences = [item["confidence"] for item in matched] or [0.0]
    return {
        "answer": answer.answer,
        "generated": answer.generated,
        "grounded_in": len(matched),
        "cited_units": cited[:6],
        "mean_confidence": round(sum(confidences) / len(confidences), 2),
        "entities": sorted(
            {item["subject"] for item in matched[:40]}
            | {item["object"] for item in matched[:40]}
        )[:8],
    }


# Settings API


@app.get("/api/settings/providers")
def provider_list():
    """Provider rows. Carries no credential material."""
    return [status.__dict__ for status in settings_service.provider_statuses()]


@app.post("/api/settings/validate")
def validate_provider(provider: str = Form(...), api_key: str = Form(None)):
    """Check a key without storing it."""
    return settings_service.validate(provider, api_key).__dict__


@app.post("/api/settings/save")
def save_provider(provider: str = Form(...), api_key: str = Form(...)):
    """Validate a key, then store it only if it works."""
    return settings_service.save_credential(provider, api_key).__dict__


@app.delete("/api/settings/{provider}")
def forget_provider(provider: str):
    """Remove a stored credential."""
    return {"removed": settings_service.forget_credential(provider)}


@app.get("/api/settings/models")
def provider_models(provider: str):
    """Selectable text-generation models for a configured provider."""
    return [
        {
            "id": model.id,
            "display_name": model.display_name,
            "tier": model.tier.value,
            "context_window": model.context_window,
        }
        for model in settings_service.available_models(provider)
    ]


@app.post("/api/settings/model")
def choose_model(
    provider: str = Form(...),
    model_id: str = Form(...),
    session: Session = Depends(get_session),
):
    """Persist the chosen provider and model identifiers."""
    settings_service.select_model(session, provider, model_id)
    return {"provider": provider, "model_id": model_id}


# Helpers


def _uuid(value: str):
    """Parse a path identifier, refusing anything that is not a UUID."""
    import uuid as uuid_module

    try:
        return uuid_module.UUID(value)
    except ValueError:
        raise HTTPException(400, "Not a valid identifier") from None


def _labels(session: Session, script_id) -> dict[Any, str]:
    labels: dict[Any, str] = {}
    for entity_id, name in session.execute(
        select(Entity.id, Entity.canonical_name).where(Entity.script_id == script_id)
    ):
        labels[entity_id] = name
    for scene_id, number, index in session.execute(
        select(Scene.id, Scene.display_scene_number, Scene.sequence_index).where(
            Scene.script_id == script_id
        )
    ):
        labels[scene_id] = f"Sc {number or index + 1}"
    return labels


def _entity_type(session: Session, entity_id) -> str | None:
    entity = session.get(Entity, entity_id)
    return entity.entity_type if entity else None


def _assertion_payload(assertion: Assertion, labels: dict[Any, str]) -> dict[str, Any]:
    subject = assertion.subject_entity_id or assertion.subject_scene_id
    obj = assertion.object_entity_id or assertion.object_scene_id
    return {
        "id": str(assertion.id),
        "subject": labels.get(subject, "?"),
        "predicate": assertion.predicate,
        "object": labels.get(obj, "?"),
        "confidence": assertion.confidence,
        "evidence_start": assertion.evidence_start,
        "evidence_end": assertion.evidence_end,
    }


def _to_edge(session: Session, assertion: Assertion, labels: dict[Any, str]) -> Edge:
    """Turn a stored assertion into a diff-engine edge."""

    def endpoint(entity_id, scene_id, kind) -> EdgeRef:
        if kind == "scene":
            return EdgeRef.scene(scene_id)
        entity = session.get(Entity, entity_id)
        return EdgeRef.entity(entity.canonical_name, entity.entity_type)

    return Edge(
        subject=endpoint(
            assertion.subject_entity_id,
            assertion.subject_scene_id,
            assertion.subject_kind,
        ),
        predicate=assertion.predicate,
        obj=endpoint(
            assertion.object_entity_id, assertion.object_scene_id, assertion.object_kind
        ),
        confidence=assertion.confidence,
        source_unit_id=str(assertion.source_unit_id),
        assertion_id=str(assertion.id),
        display_subject=labels.get(
            assertion.subject_entity_id or assertion.subject_scene_id, "?"
        ),
        display_object=labels.get(
            assertion.object_entity_id or assertion.object_scene_id, "?"
        ),
    )


def _edge_payload(edge: Edge) -> dict[str, Any]:
    return {
        "subject": edge.display_subject or edge.subject.label,
        "predicate": edge.predicate,
        "object": edge.display_object or edge.obj.label,
        "confidence": round(edge.confidence, 2),
    }


__all__ = ["app", "seed_demo_corpus"]
