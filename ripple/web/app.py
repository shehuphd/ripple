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
    Entity,
    ExtractionRun,
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
from ripple.graph.diff import Edge, EdgeRef, diff_edges
from ripple.llm import ProviderError, get_provider
from ripple.services.settings import SettingsService
from ripple.tracing import configure_tracing

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


@app.exception_handler(ProviderError)
async def provider_error_handler(_request: Request, error: ProviderError):
    """Provider failures are expected states, not server faults."""
    return JSONResponse(
        status_code=400, content={"code": error.code, "message": error.message}
    )


# Pages


@app.get("/")
def library(request: Request, session: Session = Depends(get_session)):
    """The script library."""
    scripts = list(session.scalars(select(Script).order_by(Script.created_at.desc())))
    rows = []
    for script in scripts:
        scenes = session.scalar(
            select(func.count()).select_from(Scene).where(Scene.script_id == script.id)
        )
        entities = session.scalar(
            select(func.count())
            .select_from(Entity)
            .where(Entity.script_id == script.id)
        )
        assertions = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.script_id == script.id, Assertion.active.is_(True))
        )
        rows.append(
            {
                "script": script,
                "scenes": scenes,
                "entities": entities,
                "assertions": assertions,
            }
        )
    return templates.TemplateResponse(
        request, "library.html", {"rows": rows, "max_bytes": MAX_UPLOAD_BYTES}
    )


@app.get("/scripts/{script_id}")
def reader(request: Request, script_id: str, session: Session = Depends(get_session)):
    """The reader: scenes, units, and the requirement pane."""
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "script": script,
            "scenes": script.scenes,
            "selected_model": settings_service.selected_model(session),
        },
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
    return {
        "unit": {"id": str(unit.id), "type": unit.unit_type, "text": unit.current_text},
        "scene": {
            "id": str(scene.id),
            "number": scene.display_scene_number,
            "heading": scene.heading,
        },
        "assertions": [_assertion_payload(a, labels) for a in assertions],
    }


@app.get("/api/units/{unit_id}/graph")
def unit_graph(unit_id: str, session: Session = Depends(get_session)):
    """The local graph around one unit: its edges and their direct neighbours."""
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    scene = session.get(Scene, unit.scene_id)
    labels = _labels(session, scene.script_id)

    edges = list(
        session.scalars(
            select(Assertion).where(
                Assertion.active.is_(True),
                (
                    (Assertion.source_unit_id == unit.id)
                    | (Assertion.subject_scene_id == scene.id)
                    | (Assertion.object_scene_id == scene.id)
                ),
            )
        )
    )
    nodes: dict[str, dict[str, Any]] = {}
    links = []
    for assertion in edges:
        subject = assertion.subject_entity_id or assertion.subject_scene_id
        obj = assertion.object_entity_id or assertion.object_scene_id
        for node_id, kind in (
            (subject, assertion.subject_kind),
            (obj, assertion.object_kind),
        ):
            key = str(node_id)
            if key not in nodes:
                nodes[key] = {
                    "id": key,
                    "label": labels.get(node_id, "?"),
                    "kind": kind,
                    "entity_type": (
                        _entity_type(session, node_id) if kind == "entity" else None
                    ),
                }
        links.append(
            {
                "source": str(subject),
                "target": str(obj),
                "predicate": assertion.predicate,
                "confidence": assertion.confidence,
            }
        )
    return {"nodes": list(nodes.values()), "links": links}


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
    """Compute the deterministic diff and the continuity findings for an edit.

    The synthesizer that writes the plain-language explanation is not wired
    yet, so this returns the diff and the findings code can determine alone.
    Everything here is deterministic: no model runs on this path.
    """
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    scene = session.get(Scene, unit.scene_id)

    accepted_rows = list(
        session.scalars(
            select(Assertion).where(
                Assertion.source_unit_id == unit.id, Assertion.active.is_(True)
            )
        )
    )
    labels = _labels(session, scene.script_id)
    accepted = [_to_edge(session, a, labels) for a in accepted_rows]

    # Without a provider the proposed side cannot be extracted, so the preview
    # reports the removal side only and says so rather than pretending.
    diff = diff_edges(accepted, [])

    removed_establishes = [
        (row.object_entity_id, labels.get(row.object_entity_id, "?"), row.id)
        for row in accepted_rows
        if row.predicate == "establishes" and row.object_entity_id
    ]
    orphans = detect_orphaned_references(session, scene.script_id, removed_establishes)
    packet = retrieve(
        session,
        scene.script_id,
        [
            row.object_entity_id or row.subject_entity_id
            for row in accepted_rows
            if row.object_entity_id or row.subject_entity_id
        ],
        scene.sequence_index,
    )

    return {
        "accepted_text": unit.current_text,
        "proposed_text": proposed_text,
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
                "message": orphan.message,
                "cited_units": orphan.later_unit_ids,
                "scenes": orphan.later_scene_numbers,
            }
            for orphan in orphans
        ],
        "evidence": packet.as_prompt_payload(),
        "synthesizer": "not_wired",
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
