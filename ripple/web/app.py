"""The Ripple web application.

A thin HTTP layer over the services. Every route resolves a session, calls into
`ripple.*`, and returns data; no business rule lives here, so the same
operations stay testable without a client.

The extraction loop is browser-driven: the page asks for one
scene at a time and the server commits each independently. That keeps the work
inside request handlers, with no background worker to pay for.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from traceact import ActionTrace

from ripple.adapters import import_screenplay
from ripple.adapters.base import MAX_UPLOAD_BYTES, ImportRejected
from ripple.config.secrets import SecretStore
from ripple.db.models import (
    Assertion,
    ChangeOperation,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    ExtractionRun,
    Import,
    ModelCall,
    QueryLog,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.repository import (
    LANDING_VIEWS,
    clear_all_graphs,
    delete_script,
    deletion_preview,
    get_landing_view,
    graph_labels,
    persist_import,
    set_landing_view,
    unavailable_models,
)
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction.service import (
    cancel_run,
    claim_next_scene,
    extract_scene,
    pending_scene_count,
    progress,
    start_run,
)
from ripple.graph.alignment import align_revisions
from ripple.graph.diff import Edge, GraphDiff
from ripple.graph.fixtures import seed_demo_graphs
from ripple.graph.layout import DEPARTMENT_ORDER, script_layout
from ripple.graph.layout import layout as graph_layout
from ripple.llm import ProviderError, get_provider, get_query_provider
from ripple.services import changeset, pricing, spend
from ripple.services import draft_report as report_service
from ripple.services import drafts as drafts_service
from ripple.services import duplicates as duplicates_service
from ripple.services import preview as preview_service
from ripple.services import renames as renames_service
from ripple.services import scenes as scenes_service
from ripple.services.preview import PreviewFailed, PreviewRefused, PreviewResult
from ripple.services.settings import SettingsService
from ripple.services.synthesizer import (
    answer_question,
    synthesize,
    ungrounded_entities,
)
from ripple.tracing import configure_tracing
from ripple.tracing import ensure_configured as ensure_tracing
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

    The application opens with something to explore on first run rather than
    showing an empty library and asking for an upload first.
    """
    if session.scalar(select(func.count()).select_from(Script)):
        return 0
    imported = 0
    for path in sorted(DEMO_SCRIPTS.glob("*/*.fountain")):
        result = import_screenplay(path.read_bytes(), path.name)
        if result.accepted:
            script = persist_import(session, result)
            script.origin = "bundled"
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
        # Ground-truth graphs, so the demo corpus opens with a full graph and
        # zero model calls. Idempotent; a cleared graph is rebuilt on restart.
        seed_demo_graphs(session, DEMO_SCRIPTS)
        session.commit()
    yield


app = FastAPI(title="Ripple", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
settings_service = SettingsService()


@app.middleware("http")
async def no_store(request: Request, call_next):
    """Every response says not to cache it.

    Without this a browser tab keeps last version's HTML, CSS, or JS after a
    local restart, with no visible sign anything is stale.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


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


class _LiveAssetVersion:
    """Re-reads the asset timestamps on every render, not once at import.

    Computed at import, the version freezes for the life of the process, and
    an edited stylesheet keeps serving under its old cache key until the
    server restarts. Rendering through `__str__` keeps every template's
    `{{ asset_version }}` working unchanged.
    """

    def __str__(self) -> str:
        return asset_version()


templates.env.globals["asset_version"] = _LiveAssetVersion()


def app_version() -> str:
    """The installed package version, shown beside the mark for support.

    A screenshot of any page then says which build it came from. Falls back
    to "dev" when the package metadata is absent (running from a checkout
    with no install).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ripple")
    except PackageNotFoundError:
        return "dev"


templates.env.globals["app_version"] = app_version()


ERROR_HEADINGS = {
    404: "Not found",
    400: "That request can't be processed",
    409: "That version has moved on",
}

ERROR_HINTS = {
    404: "It may have been deleted, or the link may be from before a "
    "re-import gave everything new identifiers.",
    400: "The address is malformed. Follow a link from inside the app "
    "rather than editing the address bar.",
    409: "The script changed since this page was loaded. Reopen it to "
    "work against the current version.",
}


def _error_page(request: Request, status_code: int, message: str):
    """Render the in-app error view for a page route."""
    counts = None
    if _sessions is not None:
        try:
            with _sessions() as session:
                counts = sidebar_counts(session)
        except Exception:
            counts = None
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status_code,
            "heading": ERROR_HEADINGS.get(status_code, "Something went wrong"),
            "message": message,
            "hint": ERROR_HINTS.get(status_code, "Start again from the library."),
            "counts": counts,
        },
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, error: StarletteHTTPException):
    """API routes answer in JSON; page routes render an in-app error view.

    A person following a stale bookmark to a page route should see the app
    saying what happened and where to go, never a naked JSON body.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}
        )
    return _error_page(
        request, error.status_code, str(error.detail or "The page can't be shown.")
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception):
    """Anything unhandled still answers in the app's own voice.

    Page routes render the error view; API routes answer JSON. The stack
    trace goes to the server log, never to the browser.
    """
    logger.exception("unhandled error on %s", request.url.path)
    message = (
        "Something went wrong on the server. The details are in the "
        "server log."
    )
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=500, content={"detail": message})
    return _error_page(request, 500, message)


@app.exception_handler(ProviderError)
async def provider_error_handler(_request: Request, error: ProviderError):
    """Provider failures are expected states, not server faults."""
    return JSONResponse(
        status_code=400, content={"code": error.code, "message": error.message}
    )


def _actionable_error(code: str, message: str) -> str:
    """A failure message with a next step appended when there is one.

    Gemini's own model-listing endpoint carries no lifecycle field, so a model
    KeyCall lists can still turn out to be dead on the account that owns the
    key. KeyCall's `model_not_available` code is the live-call signal for that,
    since the catalog cannot carry an account-specific entitlement problem.
    """
    if code == "model_not_available":
        message += " Pick a different model in Settings."
    return message


@app.exception_handler(PreviewRefused)
async def preview_refused_handler(_request: Request, error: PreviewRefused):
    """A refusal is a user-fixable state: no model call was made or billed."""
    return JSONResponse(
        status_code=400,
        content={
            "code": error.code,
            "message": error.message,
            "trace_id": getattr(error, "trace_id", None),
        },
    )


@app.exception_handler(PreviewFailed)
async def preview_failed_handler(_request: Request, error: PreviewFailed):
    """The judge call failed after retry. Nothing was persisted.

    The trace id lets the client offer "Open trace": the run that failed,
    in the TraceAct viewer, with the failing step marked.
    """
    return JSONResponse(
        status_code=400,
        content={
            "code": error.code,
            "message": _actionable_error(error.code, error.message),
            "trace_id": getattr(error, "trace_id", None),
        },
    )


def _record_proposal_status(change_set_id: str | None, status: str | None) -> None:
    """Persist a proposal status that a raise rolled back.

    The service marks a proposal stale or failed and then raises; the
    request's session rolls the mark back with everything else, so without
    this the proposal stays pending forever. A fresh session re-applies the
    one status write on its own.
    """
    if change_set_id is None or status is None or _sessions is None:
        return
    try:
        with _sessions() as session:
            change_set = session.get(ChangeSet, _uuid(change_set_id))
            if change_set is not None and change_set.status == "pending":
                change_set.status = status
                session.commit()
    except Exception:  # the status write must never mask the 4xx response
        logger.exception("could not record proposal %s as %s", change_set_id, status)


@app.exception_handler(changeset.StaleProposal)
async def stale_handler(_request: Request, error: changeset.StaleProposal):
    """A stale proposal is a 409: the client must regenerate, not retry."""
    _record_proposal_status(error.change_set_id, error.durable_status)
    return JSONResponse(
        status_code=409, content={"code": error.code, "message": error.message}
    )


@app.exception_handler(changeset.InvalidOperation)
async def invalid_operation_handler(
    _request: Request, error: changeset.InvalidOperation
):
    _record_proposal_status(error.change_set_id, error.durable_status)
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
        "model_calls": count(ModelCall),
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
    "stage_play": "Stage play",
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
            "landing_view": get_landing_view(session),
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

    _, model = settings_service.selected_model(session)
    page_total = script_pages(session, script.id)

    # Words changed by accepted ripples render highlighted, so a reader
    # skimming sees at a glance what moved. Each revised unit is diffed
    # against its text before the first accepted edit; undone and rejected
    # change sets leave no highlight.
    revised: dict[str, str] = {}
    for operation, _change in session.execute(
        select(ChangeOperation, ChangeSet)
        .join(ChangeSet, ChangeOperation.change_set_id == ChangeSet.id)
        .where(
            ChangeSet.script_id == script.id,
            ChangeSet.status == "accepted",
            # An undo is itself an accepted change set writing text; its
            # before is the undone wording, never the original.
            ChangeSet.kind != "undo",
            ChangeOperation.operation_type == "set_unit_text",
        )
        .order_by(ChangeSet.created_at, ChangeOperation.sequence_index)
    ):
        target = str(operation.target_id)
        if target not in revised:
            revised[target] = (operation.before_json or {}).get("text", "")

    # A linked draft marks what changed against the predecessor draft too,
    # through the same rendering. In each modified scene, the lines the link
    # left unlinked are the edited ones; align_revisions pairs each with the
    # old wording it revises, and a line with no counterpart marks whole.
    # This baseline predates any in-draft edit, so it wins over `revised`.
    draft_baseline: dict[str, str] = {}
    heading_baseline: dict[str, str] = {}
    new_scene_ids: set[str] = set()
    if script.predecessor_script_id is not None:
        for scene in script.scenes:
            if scene.omitted or scene.lineage_kind == "unchanged":
                continue
            if scene.predecessor_scene_id is None:
                new_scene_ids.add(str(scene.id))
                continue
            old_scene = session.get(Scene, scene.predecessor_scene_id)
            if old_scene is None:
                continue
            if old_scene.heading != scene.heading:
                heading_baseline[str(scene.id)] = old_scene.heading
            old_units = [
                unit
                for unit in old_scene.units
                if unit.unit_type != "scene_heading"
            ]
            new_units = [
                unit for unit in scene.units if unit.unit_type != "scene_heading"
            ]
            mapping = align_revisions(
                [unit.current_text for unit in old_units],
                [unit.current_text for unit in new_units],
            )
            for unit, source in zip(new_units, mapping, strict=True):
                if unit.predecessor_unit_id is not None:
                    continue
                draft_baseline[str(unit.id)] = (
                    "" if source is None else old_units[source].current_text
                )

    def marked_segments(
        original: str | None, current: str
    ) -> list[dict[str, str]] | None:
        if original is None or original == current:
            return None
        segments = [
            segment
            for segment in preview_service.word_diff(original, current)
            if segment["op"] != "del"
        ]
        # The rendered text must reproduce the current text to the byte, or
        # the reader would mark the unit as an unapplied draft. Unusual
        # spacing falls back to plain rendering rather than risking that.
        if " ".join(segment["text"] for segment in segments) != current:
            return None
        return segments

    def revision_segments(unit) -> list[dict[str, str]] | None:
        key = str(unit.id)
        original = draft_baseline.get(key)
        if original is None:
            original = revised.get(key)
        return marked_segments(original, unit.current_text)

    scenes = []
    running = 0
    for scene in script.scenes:
        characters = sum(len(unit.current_text) for unit in scene.units)
        # The entity is on the opposite end from the scene: a scene-subject
        # edge (requires, occurs_at) names its entity as the object, and a
        # scene-object edge (appears_in, establishes) as the subject.
        entity_ids = set(
            session.scalars(
                select(Assertion.object_entity_id).where(
                    Assertion.subject_scene_id == scene.id, Assertion.active.is_(True)
                )
            )
        ) | set(
            session.scalars(
                select(Assertion.subject_entity_id).where(
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
                # with the scene that carries that number later in the script.
                "number": scene.display_scene_number or "",
                "heading": scene.heading,
                "heading_segments": marked_segments(
                    heading_baseline.get(str(scene.id)), scene.heading
                ),
                "new_in_draft": str(scene.id) in new_scene_ids,
                "omitted": scene.omitted,
                "page": page_of(running),
                "eighths": eighths(characters),
                "entities": len([e for e in entity_ids if e]),
                "units": [
                    {
                        "id": str(unit.id),
                        "type": unit.unit_type,
                        "text": unit.current_text,
                        "segments": revision_segments(unit),
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
    # Building the graph bills model calls, so the button is gated on there
    # being billable work: scenes whose current content has no completed
    # extraction under the selected model. Accepting a ripple, adding or
    # restoring a scene, and linking a draft all change scene content, so
    # they re-enable the button without any bookkeeping of their own.
    pending = (
        pending_scene_count(session, script.id, model) if model else 0
    )
    # A ripple compares an edit against the graph, so it needs one to exist. The
    # test is any active assertion, not graph_status: a seeded or partly-built
    # graph carries assertions without ever reaching "ready".
    has_graph = session.scalar(
        select(Assertion.id)
        .where(Assertion.script_id == script.id, Assertion.active.is_(True))
        .limit(1)
    ) is not None
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "script": script,
            "scenes": scenes,
            "pages": page_total,
            "findings": findings,
            "model": model,
            "pending_scenes": pending,
            "has_graph": has_graph,
            "counts": sidebar_counts(session),
        },
    )


@app.get("/ask")
def ask_page(
    request: Request, script: str | None = None, session: Session = Depends(get_session)
):
    """Grounded natural-language query over one script's accepted graph."""
    if script:
        chosen = session.get(Script, _uuid(script))
        if chosen is None:
            # A stale link names a script that is gone. Falling back to a
            # different script would answer questions about the wrong one.
            raise HTTPException(404, "No such script")
    else:
        chosen = session.scalar(select(Script).order_by(Script.created_at.desc()))
    counts_by_script = dict(
        session.execute(
            select(Assertion.script_id, func.count())
            .where(Assertion.active.is_(True))
            .group_by(Assertion.script_id)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "ask.html",
        {
            "script": chosen,
            "scripts": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "assertions": counts_by_script.get(row.id, 0),
                }
                for row in session.scalars(select(Script).order_by(Script.title))
            ],
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
            "lock": _graph_lock(session, chosen.id) if chosen else None,
            "model": settings_service.selected_model(session)[1],
            "history": (
                [
                    {
                        "id": str(row.id),
                        "question": row.question,
                        "asked_at": row.asked_at.strftime("%d %b %H:%M"),
                    }
                    for row in session.scalars(
                        select(QueryLog)
                        .where(QueryLog.script_id == chosen.id)
                        .order_by(QueryLog.asked_at.desc())
                        .limit(15)
                    )
                ]
                if chosen
                else []
            ),
        },
    )


def _list_page(request, session, **kwargs):
    """Render the shared list template with the sidebar counts filled in."""
    return templates.TemplateResponse(
        request, "list.html", {"counts": sidebar_counts(session), **kwargs}
    )


def _graph_lock(session, script_id=None) -> dict[str, str] | None:
    """Why the graph pages are empty, when they are.

    An empty page with no explanation reads as a broken feature. Naming the
    missing step, and linking to it, is the difference. Scoped to one script
    when the page is: another script's graph existing does not explain this
    script's empty canvas.
    """
    query = select(func.count()).select_from(Assertion)
    if script_id is not None:
        query = query.where(Assertion.script_id == script_id)
    if session.scalar(query):
        return None
    _, model = settings_service.selected_model(session)
    if not model:
        return {
            "message": "No graph has been built yet, and no model is selected.",
            "action": "Choose a model in Settings",
            "href": "/settings",
        }
    if script_id is not None:
        return {
            "message": "No graph has been built for this script yet.",
            "action": "Open the reader and build it",
            "href": f"/scripts/{script_id}",
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
def findings_page(
    request: Request, script: str | None = None, session: Session = Depends(get_session)
):
    """Continuity findings, across every script or filtered to one.

    The reader's findings chip links here with its script, so the notice
    leads to the findings it counted.
    """
    chosen = None
    if script:
        chosen = session.get(Script, _uuid(script))
        if chosen is None:
            # Widening to every script here would show findings the link
            # never pointed at, without saying so.
            raise HTTPException(404, "No such script")
    query = (
        select(ContinuityFinding, Script)
        .join(ChangeSet, ContinuityFinding.change_set_id == ChangeSet.id)
        .join(Script, ChangeSet.script_id == Script.id)
        .order_by(ContinuityFinding.created_at.desc())
    )
    if chosen is not None:
        query = query.where(ChangeSet.script_id == chosen.id)
    rows = session.execute(query).all()
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
            "actions": [
                {
                    "label": "Review",
                    "href": f"/scripts/{script.id}?finding={finding.id}",
                },
                *(
                    [
                        {
                            "label": "Confirm rename",
                            "url": f"/api/findings/{finding.id}/confirm-rename",
                        }
                    ]
                    if finding.status == "open"
                    and finding.finding_type == "possible_rename"
                    else []
                ),
                *(
                    [
                        {
                            "label": "Dismiss",
                            "url": f"/api/findings/{finding.id}/dismiss",
                            "danger": True,
                            "confirm": (
                                "Dismiss this continuity finding? It leaves the "
                                "open list; nothing in the script changes."
                            ),
                        }
                    ]
                    if finding.status == "open"
                    else []
                ),
            ],
        }
        for finding, script in rows
    ]
    return _list_page(
        request,
        session,
        heading="Continuity findings",
        active="findings",
        subtitle=f"{len(items)} finding(s)"
        + (f" · {chosen.title}" if chosen is not None else "")
        + " · warnings do not block a decision",
        items=items,
        empty=(
            f"No findings for {chosen.title}." if chosen is not None
            else "No findings yet."
        ),
        lock=None,
    )


@app.get("/traces")
def traces_page(request: Request, session: Session = Depends(get_session)):
    """Every recorded model call, newest first, with the spend ledger on top.

    This is the application-data audit trail: full
    prompts and replies live in `model_calls` and are deleted with their
    script, distinct from TraceAct's redacted operational traces.
    """
    ledger = spend.summary(session)
    budget = spend.get_budget(session)
    rows = session.execute(
        select(ModelCall, Script)
        .outerjoin(Script, ModelCall.script_id == Script.id)
        .order_by(ModelCall.created_at.desc())
        .limit(200)
    ).all()

    def tokens_of(call: ModelCall) -> str:
        if call.input_tokens is None and call.output_tokens is None:
            return "no token counts"
        counts = f"{call.input_tokens or 0} in · {call.output_tokens or 0} out"
        if call.reasoning_tokens:
            counts += f" · {call.reasoning_tokens} reasoning"
        cost = pricing.display(
            pricing.cost_usd(
                "google",
                call.model_id,
                call.input_tokens,
                call.output_tokens,
                call.reasoning_tokens,
            )
        )
        if cost:
            counts += f" · {cost}"
        return counts

    items = [
        {
            "tag": call.outcome,
            "tag_class": {
                "ok": "set_design",
                "cached": "set_design",
                "budget_refused": "prop",
            }.get(call.outcome, "stunt"),
            "title": f"{call.purpose} · {call.model_id}",
            "sub": " · ".join(
                part
                for part in (
                    script.title if script else None,
                    call.prompt_version,
                    tokens_of(call),
                    f"{call.duration_ms} ms" if call.duration_ms else None,
                    call.error_message,
                )
                if part
            ),
            "right": call.created_at.strftime("%d %b %H:%M"),
        }
        for call, script in rows
    ]
    budget_note = (
        f"budget {ledger.total_tokens:,} of {budget:,} tokens"
        if budget
        else "no budget cap set"
    )
    # The whole ledger's cost, grouped by model so pricing is looked up
    # once per model rather than once per row.
    grouped = session.execute(
        select(
            ModelCall.model_id,
            func.coalesce(func.sum(ModelCall.input_tokens), 0),
            func.coalesce(func.sum(ModelCall.output_tokens), 0),
            func.coalesce(func.sum(ModelCall.reasoning_tokens), 0),
        ).group_by(ModelCall.model_id)
    ).all()
    ledger_cost = None
    for model_id, tokens_in, tokens_out, reasoning in grouped:
        cost = pricing.cost_usd(
            "google", model_id, tokens_in, tokens_out, reasoning
        )
        if cost is not None:
            ledger_cost = (ledger_cost or 0.0) + cost
    cost_note = pricing.display(ledger_cost)
    return _list_page(
        request,
        session,
        heading="Traces",
        active="traces",
        subtitle=(
            f"{ledger.calls} call(s) · {ledger.input_tokens:,} in · "
            f"{ledger.output_tokens:,} out"
            + (f" · {cost_note} spent" if cost_note else "")
            + f" · {budget_note}"
        ),
        items=items,
        empty="No model calls recorded yet. Every call is recorded here, "
        "successes and refusals alike.",
        lock=None,
        header_action={
            "url": "/api/traces/viewer",
            "label": "⁘ Open in TraceAct viewer",
            "tip": "Open TraceAct's viewer on Ripple's full operational "
            "trace log (data/traces), map view",
        },
    )


_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.post("/api/traces/viewer")
def open_traces_viewer():
    """Open the TraceAct viewer over Ripple's whole operational trace log.

    The page-level companion to `open_trace_viewer`: that one deep-links to a
    single trace's map, this one opens the viewer reading `data/traces`
    directly, unfiltered, so the whole run history is browsable at once.
    """
    from ripple.tracing import DEFAULT_TRACE_DIR

    if not DEFAULT_TRACE_DIR.exists():
        raise HTTPException(
            404,
            "No trace files yet. Tracing may be off (RIPPLE_TRACING=off).",
        )
    from traceact.viewer.instance import launch_or_connect

    try:
        # Absolute: a reused viewer resolves a relative path against its own
        # working directory, which may be another project's.
        url = launch_or_connect(
            source=str(DEFAULT_TRACE_DIR.resolve()), name="ripple"
        )
    except Exception as error:  # a viewer that cannot start is a 503, not a 500
        logger.exception("trace viewer launch failed")
        raise HTTPException(
            503, f"The trace viewer could not start: {error}"
        ) from error
    separator = "&" if "?" in url else "?"
    return {"url": f"{url}{separator}view=map"}


@app.post("/api/traces/{trace_id}/viewer")
def open_trace_viewer(trace_id: str):
    """Open the TraceAct viewer on one trace's map, starting it if needed.

    `launch_or_connect` reuses a running viewer or spawns one over the
    app's trace folder; the returned URL is deep-linked so the tab opens
    on the map view, pre-filtered to this trace and with it selected.
    """
    if not _TRACE_ID_PATTERN.match(trace_id):
        raise HTTPException(400, "Not a trace id")
    from ripple.tracing import DEFAULT_TRACE_DIR

    if not DEFAULT_TRACE_DIR.exists():
        raise HTTPException(
            404,
            "No trace files yet. Tracing may be off (RIPPLE_TRACING=off).",
        )
    from traceact.viewer.instance import launch_or_connect

    try:
        # Absolute, because a reused viewer resolves a relative path against
        # its own working directory, which may be another project's.
        url = launch_or_connect(
            source=str(DEFAULT_TRACE_DIR.resolve()), name="ripple"
        )
    except Exception as error:  # a viewer that cannot start is a 503, not a 500
        logger.exception("trace viewer launch failed")
        raise HTTPException(
            503, f"The trace viewer could not start: {error}"
        ) from error
    separator = "&" if "?" in url else "?"
    return {
        "url": (
            f"{url}{separator}view=map&open=latest"
            f"&pf_trace_id={trace_id}"
        )
    }


@app.get("/api/settings/budget")
def get_budget(session: Session = Depends(get_session)):
    """The token budget and the recorded spend it is measured against."""
    ledger = spend.summary(session)
    return {
        "max_total_tokens": spend.get_budget(session),
        "spent_tokens": ledger.total_tokens,
        "calls": ledger.calls,
        "by_purpose": ledger.by_purpose,
    }


@app.post("/api/settings/budget")
def set_budget(
    max_total_tokens: str = Form(""),
    session: Session = Depends(get_session),
):
    """Set or clear the token budget. An empty value clears it."""
    text = max_total_tokens.strip().replace(",", "")
    if not text:
        spend.set_budget(session, None)
        return {"max_total_tokens": None}
    try:
        cap = int(text)
    except ValueError:
        raise HTTPException(
            400, "The budget must be a whole number of tokens."
        ) from None
    if cap <= 0:
        raise HTTPException(400, "The budget must be above zero.")
    spend.set_budget(session, cap)
    return {"max_total_tokens": cap}


@app.post("/api/settings/landing")
def choose_landing_view(
    landing_view: str = Form(...),
    session: Session = Depends(get_session),
):
    """Persist where clicking a script in the library goes."""
    if landing_view not in LANDING_VIEWS:
        raise HTTPException(
            400, f"The opening view must be one of: {', '.join(LANDING_VIEWS)}."
        )
    set_landing_view(session, landing_view)
    return {"landing_view": landing_view}


@app.get("/entities")
def entities_page(request: Request, session: Session = Depends(get_session)):
    """Every extracted entity, with its aliases and how often it is asserted.

    Suspected duplicates lead the list: same-type pairs whose names or
    aliases overlap, each with Merge and Keep separate. Merging is recorded
    as an accepted change set; keeping separate stops the suggestion.
    """
    rows = session.execute(
        select(Entity, Script)
        .join(Script, Entity.script_id == Script.id)
        .order_by(Entity.entity_type, Entity.canonical_name)
    ).all()
    items = []
    for script_id in {script.id for _, script in rows}:
        script = session.get(Script, script_id)
        for pair in duplicates_service.detect(session, script_id):
            items.append(
                {
                    "id": f"{pair.keep.id}:{pair.absorb.id}",
                    "batch_kinds": ["merge", "keep_separate"],
                    "tag": "duplicate?",
                    "tag_class": "duplicate",
                    "title": (
                        f"{pair.keep.canonical_name} and "
                        f"{pair.absorb.canonical_name}"
                    ),
                    "sub": (
                        f"{script.title} · {pair.keep.entity_type} · "
                        f"{pair.reason}; merging keeps "
                        f"{pair.keep.canonical_name} and records the other "
                        "name as its alias"
                    ),
                    "actions": [
                        {
                            "url": (
                                f"/api/entities/{pair.keep.id}"
                                f"/merge/{pair.absorb.id}"
                            ),
                            "label": "Merge",
                        },
                        {
                            "url": (
                                f"/api/entities/{pair.keep.id}"
                                f"/distinct/{pair.absorb.id}"
                            ),
                            "label": "Keep separate",
                        },
                    ],
                }
            )
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
                "id": str(entity.id),
                "batch_kinds": ["delete"],
                "assertions": uses,
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
        batch_actions=[
            {
                "kind": "merge",
                "label": "Merge selected",
                "url": "/api/entities/batch/merge",
                "confirm": True,
            },
            {
                "kind": "keep_separate",
                "label": "Keep selected separate",
                "url": "/api/entities/batch/keep-separate",
            },
            {
                "kind": "delete",
                "label": "Delete selected",
                "url": "/api/entities/batch/delete",
                "danger": True,
                "confirm": True,
            },
        ],
    )


@app.get("/assertions")
def assertions_page(request: Request, session: Session = Depends(get_session)):
    """Every assertion, active and deactivated, with the unit that supports it.

    Deactivated rows are shown too, muted and marked, so a deactivation can be
    reversed: selecting them and pressing Reactivate returns them to the graph.
    """
    scripts = {script.id: script for script in session.scalars(select(Script))}
    items = []
    active_total = 0
    inactive_total = 0
    for script_id, script in scripts.items():
        labels = _labels(session, script_id)
        active_rows = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script_id, Assertion.active.is_(True)
                )
            )
        )
        inactive_rows = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script_id, Assertion.active.is_(False)
                )
            )
        )
        active_total += len(active_rows)
        inactive_total += len(inactive_rows)

        def _label(row: Assertion, labels=labels) -> tuple[str, str]:
            subject = labels.get(row.subject_entity_id or row.subject_scene_id, "?")
            obj = labels.get(row.object_entity_id or row.object_scene_id, "?")
            return subject, obj

        for row in active_rows[:500]:
            subject, obj = _label(row)
            items.append(
                {
                    "id": str(row.id),
                    "batch_kinds": ["deactivate"],
                    "tag": row.predicate.replace("_", " "),
                    "tag_class": "location",
                    "title": f"{subject} → {obj}",
                    "sub": f"{script.title} · {row.provenance}"
                    + (f" · {row.model_id}" if row.model_id else ""),
                    "right": f"{row.confidence:.2f}",
                }
            )
        for row in inactive_rows[:200]:
            subject, obj = _label(row)
            items.append(
                {
                    "id": str(row.id),
                    "batch_kinds": ["reactivate"],
                    "tag": row.predicate.replace("_", " "),
                    "tag_class": "off",
                    "title": f"{subject} → {obj}",
                    "sub": f"inactive · {script.title} · {row.provenance}"
                    + (f" · {row.model_id}" if row.model_id else ""),
                    "right": f"{row.confidence:.2f}",
                }
            )
    return _list_page(
        request,
        session,
        heading="Assertions",
        active="assertions",
        subtitle=f"{active_total} active"
        + (f", {inactive_total} inactive" if inactive_total else "")
        + " assertion(s)",
        items=items,
        empty="No assertions yet.",
        lock=_graph_lock(session),
        batch_actions=[
            {
                "kind": "deactivate",
                "label": "Deactivate selected",
                "url": "/api/assertions/batch/deactivate",
                "danger": True,
                "confirm": True,
            },
            {
                "kind": "reactivate",
                "label": "Reactivate selected",
                "url": "/api/assertions/batch/reactivate",
            },
        ],
    )


@app.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    """Provider credentials and model selection."""
    provider, model = settings_service.selected_model(session)
    _, fallback = settings_service.fallback_model(session)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "statuses": settings_service.provider_statuses(),
            "selected_provider": provider,
            "selected_model": model,
            "fallback_model": fallback,
            "counts": sidebar_counts(session),
            "ledger": spend.summary(session),
            "spend_actions": spend.actions(session),
            "budget": spend.get_budget(session),
            "landing_view": get_landing_view(session),
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
        # Same-titled scripts with graphs this upload could continue. An
        # offer for the user to accept, never a link made on a title alone.
        "draft_candidates": [
            {
                "id": str(candidate.id),
                "title": candidate.title,
                "draft_number": candidate.draft_number,
            }
            for candidate in drafts_service.draft_candidates(session, script)
        ],
    }


@app.post("/api/scripts/{script_id}/link-draft")
def link_draft_route(
    script_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Link an upload as the next draft of an existing script.

    Alignment and carry-over run here; when a model is selected, extraction
    of the changed scenes starts and the run rides back for the browser
    loop.
    """
    predecessor = payload.get("predecessor_script_id")
    if not predecessor:
        raise HTTPException(422, "Name the predecessor script to link to.")
    accepted_raw = payload.get("accepted_pairs") or {}
    accepted = {
        str(new_id): str(old_id)
        for new_id, old_id in accepted_raw.items()
        if new_id and old_id
    }
    try:
        link = drafts_service.link_draft(
            session,
            _uuid(script_id),
            _uuid(predecessor),
            accepted_pairs=accepted or None,
        )
    except drafts_service.LinkRefused as error:
        raise HTTPException(409, str(error)) from None
    except ValueError:
        raise HTTPException(404, "No such script") from None

    run_progress = None
    _, model_id = settings_service.selected_model(session)
    if model_id and link.to_extract:
        run = start_run(
            session,
            _uuid(script_id),
            model_id,
            scene_ids=[_uuid(scene_id) for scene_id in link.to_extract],
        )
        run_progress = progress(session, run.id).__dict__
    elif not link.to_extract:
        # Nothing to extract, so the cross-draft report needs no wait; the
        # judged layer has no changed scene to judge either way.
        report_service.build_draft_report(session, _uuid(script_id))
    body = link.__dict__.copy()
    body["to_extract"] = len(link.to_extract)
    return {"link": body, "run": run_progress}


@app.post("/api/scripts/{script_id}/link-draft/preview")
def preview_link_route(
    script_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Describe the alignment without writing it, for the review screen."""
    predecessor = payload.get("predecessor_script_id")
    if not predecessor:
        raise HTTPException(422, "Name the predecessor script to link to.")
    try:
        return drafts_service.preview_link(
            session, _uuid(script_id), _uuid(predecessor)
        )
    except drafts_service.LinkRefused as error:
        raise HTTPException(409, str(error)) from None
    except ValueError:
        raise HTTPException(404, "No such script") from None


@app.post("/api/scripts/{script_id}/draft-report")
def draft_report_route(script_id: str, session: Session = Depends(get_session)):
    """Build (or return) the cross-draft ripple report for a linked draft."""
    provider = None
    provider_name, model_id = settings_service.selected_model(session)
    if provider_name and model_id:
        provider = get_provider(provider_name)
    try:
        report = report_service.build_draft_report(
            session, _uuid(script_id), provider=provider, model_id=model_id
        )
    except report_service.ReportRefused as error:
        raise HTTPException(409, str(error)) from None
    except ValueError:
        raise HTTPException(404, "No such script") from None
    return report.__dict__


@app.post("/api/scripts/{script_id}/opened")
def record_opened(script_id: str, session: Session = Depends(get_session)):
    """Record that a person opened this script, for "Recently opened".

    A POST from the page's own script rather than a side effect of the GET:
    a prefetch or a crawler fetching the page must not reorder the list.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    script.last_opened_at = datetime.now(UTC)
    return {"id": str(script.id)}


@app.delete("/api/scripts/{script_id}")
def remove_script(script_id: str, session: Session = Depends(get_session)):
    """Delete one script and everything under it."""
    counts = delete_script(session, _uuid(script_id))
    return counts.__dict__


@app.get("/api/scripts/{script_id}/deletion-preview")
def preview_deletion(script_id: str, session: Session = Depends(get_session)):
    """What deleting this script would remove, counted before anything is."""
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
        for (node_id, kind, _), entity_type in zip(ends, types, strict=True):
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
    # an empty graph still shows where the selection belongs.
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
            "lock": _graph_lock(session, script.id),
        },
    )


# Script-level graph, the opening view for a script


@app.get("/api/scripts/{script_id}/graph")
def script_graph(
    script_id: str,
    min_confidence: float = 0.0,
    departments: str | None = None,
    session: Session = Depends(get_session),
):
    """The whole script's graph, with deterministic positions.

    Every scene is on the spine whether or not anything cites it yet, so the
    spine always shows the full script; entities come from the graph. Edges
    ship in full and the client decides which to draw, so selecting a node
    costs no request.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    labels = _labels(session, script.id)
    wanted = set(departments.split(",")) if departments else None

    nodes: dict[str, dict[str, Any]] = {}
    for scene in session.scalars(
        select(Scene)
        .where(Scene.script_id == script.id)
        .order_by(Scene.sequence_index)
    ):
        nodes[str(scene.id)] = {
            "id": str(scene.id),
            "label": labels.get(scene.id, "Scene"),
            "kind": "scene",
            "entity_type": None,
        }

    entity_types: dict[str, str] = {}
    for entity in session.scalars(
        select(Entity).where(Entity.script_id == script.id)
    ):
        entity_types[str(entity.id)] = entity.entity_type
        if wanted and entity.entity_type not in wanted:
            continue
        nodes[str(entity.id)] = {
            "id": str(entity.id),
            "label": entity.canonical_name,
            "kind": "entity",
            "entity_type": entity.entity_type,
        }

    below = 0
    links: list[dict[str, Any]] = []
    for assertion in session.scalars(
        select(Assertion).where(
            Assertion.script_id == script.id, Assertion.active.is_(True)
        )
    ):
        if assertion.confidence < min_confidence:
            below += 1
            continue
        subject = str(assertion.subject_entity_id or assertion.subject_scene_id)
        obj = str(assertion.object_entity_id or assertion.object_scene_id)
        if subject not in nodes or obj not in nodes:
            continue
        links.append(
            {
                "source": subject,
                "target": obj,
                "predicate": assertion.predicate,
                "confidence": round(assertion.confidence, 2),
                "removed": False,
            }
        )

    placed = script_layout(list(nodes.values()))
    return {
        "focus": None,
        "nodes": [
            {**nodes[p.id], "x": round(p.x, 4), "y": round(p.y, 4), "ring": p.ring}
            for p in placed
        ],
        "links": links,
        "departments": sorted(set(entity_types.values())),
        "hidden_below_threshold": below,
    }


@app.get("/api/entities/{entity_id}/detail")
def entity_detail(entity_id: str, session: Session = Depends(get_session)):
    """Everything the graph knows about one entity, with its evidence."""
    entity = session.get(Entity, _uuid(entity_id))
    if entity is None:
        raise HTTPException(404, "No such entity")
    labels = _labels(session, entity.script_id)

    assertions = list(
        session.scalars(
            select(Assertion).where(
                Assertion.active.is_(True),
                (Assertion.subject_entity_id == entity.id)
                | (Assertion.object_entity_id == entity.id),
            )
        )
    )
    scene_numbers: list[str] = []
    for assertion in assertions:
        scene_id = assertion.subject_scene_id or assertion.object_scene_id
        if scene_id is not None:
            label = labels.get(scene_id, "?").removeprefix("Sc ")
            if label not in scene_numbers:
                scene_numbers.append(label)

    def _snippet(unit_id) -> str:
        unit = session.get(ScriptUnit, unit_id) if unit_id else None
        if unit is None:
            return ""
        text = unit.current_text
        return text if len(text) <= 140 else text[:137] + "…"

    return {
        "id": str(entity.id),
        "name": entity.canonical_name,
        "type": entity.entity_type,
        "description": entity.description,
        "aliases": sorted(a.alias for a in entity.aliases),
        "attributes": [
            {
                "key": attribute.key,
                "value": attribute.value,
                "confidence": attribute.confidence,
                "provenance": attribute.provenance,
                "evidence": _snippet(attribute.source_unit_id),
            }
            for attribute in sorted(entity.attributes, key=lambda a: a.key)
            if attribute.active
        ],
        "assertions": [
            {
                **_assertion_payload(assertion, labels),
                "evidence": _snippet(assertion.source_unit_id),
                "source_unit_id": str(assertion.source_unit_id),
            }
            for assertion in assertions
        ],
        "scenes": scene_numbers,
    }


@app.get("/scripts/{script_id}/graph")
def script_graph_page(
    request: Request, script_id: str, session: Session = Depends(get_session)
):
    """The script's graph as its opening view; the reader is the drill-down."""
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")

    by_department = dict(
        session.execute(
            select(Entity.entity_type, func.count())
            .where(Entity.script_id == script.id)
            .group_by(Entity.entity_type)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "script_graph.html",
        {
            "script": script,
            "departments": [
                {"name": name, "count": by_department.get(name, 0)}
                for name in DEPARTMENT_ORDER
                if by_department.get(name)
            ],
            "total_departments": len(DEPARTMENT_ORDER),
            "counts": sidebar_counts(session),
            "lock": _graph_lock(session, script.id) if not by_department else None,
        },
    )


# Extraction


@app.post("/api/scripts/{script_id}/scenes")
def add_scene(
    script_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Insert one scene and start extracting it when a model is selected.

    The body takes `heading`, `body`, and an optional `after_scene_id`
    (omitted or null inserts at the top). The response carries the created
    scene and, when extraction started, the run for the browser loop to
    drive; without a model the scene still inserts and its graph waits.
    """
    try:
        inserted = scenes_service.insert_scene(
            session,
            _uuid(script_id),
            heading=str(payload.get("heading") or ""),
            body=str(payload.get("body") or ""),
            after_scene_id=(
                _uuid(payload["after_scene_id"])
                if payload.get("after_scene_id")
                else None
            ),
        )
    except ImportRejected as error:
        raise HTTPException(422, error.message) from None
    except ValueError as error:
        raise HTTPException(404, str(error)) from None

    run_progress = None
    _, model_id = settings_service.selected_model(session)
    if model_id:
        run = start_run(
            session,
            _uuid(script_id),
            model_id,
            scene_ids=[_uuid(inserted.scene_id)],
        )
        run_progress = progress(session, run.id).__dict__
    return {"scene": inserted.__dict__, "run": run_progress}


@app.post("/api/entities/{keep_id}/merge/{absorb_id}")
def merge_entities(
    keep_id: str, absorb_id: str, session: Session = Depends(get_session)
):
    """Join two entities the review suspects are one. Recorded, audited."""
    try:
        change = duplicates_service.merge(
            session, _uuid(keep_id), _uuid(absorb_id)
        )
    except duplicates_service.MergeRefused as error:
        raise HTTPException(409, str(error)) from None
    return {"change_set_id": str(change.id)}


@app.post("/api/entities/{keep_id}/distinct/{other_id}")
def keep_entities_separate(
    keep_id: str, other_id: str, session: Session = Depends(get_session)
):
    """Record that a suggested pair is two entities; the suggestion stops."""
    try:
        duplicates_service.keep_separate(session, _uuid(keep_id), _uuid(other_id))
    except duplicates_service.MergeRefused as error:
        raise HTTPException(409, str(error)) from None
    return {"recorded": True}


def _batch_ids(raw: str) -> list[str]:
    """Parse the JSON id list a batch button posts, bounding its size.

    The browser sends the selected rows' ids as one JSON array field, so a
    batch is one request rather than one per row.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Malformed batch selection.") from None
    if not isinstance(data, list) or len(data) > 1000:
        raise HTTPException(400, "Malformed or oversized batch selection.")
    return [str(item) for item in data]


@app.post("/api/entities/batch/merge")
def batch_merge_entities(ids: str = Form(...), session: Session = Depends(get_session)):
    """Merge several suspected-duplicate pairs at once.

    Each id is a `keep:absorb` pair. A pair whose entities have vanished
    (absorbed by an earlier merge in the same batch) or that the service
    refuses is skipped, not fatal, so one bad pair does not lose the rest.
    """
    merged = 0
    skipped = 0
    for raw in _batch_ids(ids):
        keep_raw, _, absorb_raw = raw.partition(":")
        if not absorb_raw:
            skipped += 1
            continue
        keep = session.get(Entity, _uuid(keep_raw))
        absorb = session.get(Entity, _uuid(absorb_raw))
        if keep is None or absorb is None:
            skipped += 1
            continue
        try:
            duplicates_service.merge(session, keep.id, absorb.id)
            merged += 1
        except duplicates_service.MergeRefused:
            skipped += 1
    return {"merged": merged, "skipped": skipped}


@app.post("/api/entities/batch/keep-separate")
def batch_keep_separate(ids: str = Form(...), session: Session = Depends(get_session)):
    """Record several suspected pairs as distinct; their suggestions stop."""
    recorded = 0
    for raw in _batch_ids(ids):
        a_raw, _, b_raw = raw.partition(":")
        if not b_raw:
            continue
        try:
            duplicates_service.keep_separate(session, _uuid(a_raw), _uuid(b_raw))
            recorded += 1
        except duplicates_service.MergeRefused:
            continue
    return {"recorded": recorded}


@app.post("/api/entities/batch/delete")
def batch_delete_entities(ids: str = Form(...), session: Session = Depends(get_session)):
    """Delete several entities. The DB cascades to their assertions,
    attributes, and aliases, so this is not reversible. The UI gates it behind
    a typed confirm that names the assertion count it will also remove.
    """
    deleted = 0
    for raw in _batch_ids(ids):
        entity = session.get(Entity, _uuid(raw))
        if entity is None:
            continue
        session.delete(entity)
        deleted += 1
    return {"deleted": deleted}


@app.post("/api/assertions/batch/deactivate")
def batch_deactivate_assertions(
    ids: str = Form(...), session: Session = Depends(get_session)
):
    """Deactivate several assertions. They leave the active graph but the rows
    remain, so this is reversible at the data layer.
    """
    deactivated = 0
    for raw in _batch_ids(ids):
        assertion = session.get(Assertion, _uuid(raw))
        if assertion is None or not assertion.active:
            continue
        assertion.active = False
        deactivated += 1
    return {"deactivated": deactivated}


@app.post("/api/assertions/batch/reactivate")
def batch_reactivate_assertions(
    ids: str = Form(...), session: Session = Depends(get_session)
):
    """Return several deactivated assertions to the active graph.

    The `uq_assertion_active_dedupe` index allows only one active row per
    dedupe key, so a row whose key another active assertion already holds (a
    re-extraction wrote a fresh one after this was deactivated) is skipped, not
    fatal: reactivating it would collide.
    """
    reactivated = 0
    skipped = 0
    for raw in _batch_ids(ids):
        assertion = session.get(Assertion, _uuid(raw))
        if assertion is None or assertion.active:
            continue
        clash = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.dedupe_key == assertion.dedupe_key,
                Assertion.active.is_(True),
                Assertion.id != assertion.id,
            )
        )
        if clash:
            skipped += 1
            continue
        assertion.active = True
        reactivated += 1
    return {"reactivated": reactivated, "skipped": skipped}


@app.post("/api/scenes/{scene_id}/omit")
def omit_scene(scene_id: str, session: Session = Depends(get_session)):
    """Mark a scene OMITTED, deactivating its facts and reporting orphans."""
    try:
        result = scenes_service.omit_scene(session, _uuid(scene_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    return result.__dict__


@app.post("/api/scenes/{scene_id}/restore")
def restore_scene(scene_id: str, session: Session = Depends(get_session)):
    """Reverse an omission, reactivating the scene's facts."""
    try:
        result = scenes_service.restore_scene(session, _uuid(scene_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    return result.__dict__


@app.post("/api/scripts/{script_id}/extract")
def begin_extraction(
    script_id: str,
    force: bool = Form(False),
    session: Session = Depends(get_session),
):
    """Create an extraction run for a script.

    `force` re-reads every scene instead of replaying the cache, which is what
    the reader's Rebuild asks for. It bills each scene, so the page confirms
    before sending it.
    """
    provider_name, model_id = settings_service.selected_model(session)
    if not provider_name or not model_id:
        raise HTTPException(400, "Choose a provider and model in Settings first.")
    try:
        run = start_run(session, _uuid(script_id), model_id, force=force)
    except ValueError:
        raise HTTPException(404, "No such script") from None
    return progress(session, run.id).__dict__


@app.post("/api/extract/{run_id}/next")
def extract_next(run_id: str, session: Session = Depends(get_session)):
    """Do one scene. The browser calls this in a loop until nothing is pending."""
    provider_name, _ = settings_service.selected_model(session)
    if not provider_name:
        raise HTTPException(400, "No provider selected.")
    provider = get_provider(provider_name)

    job = claim_next_scene(session, _uuid(run_id))
    try:
        if job is None:
            return {
                "done": True,
                "progress": progress(session, _uuid(run_id)).__dict__,
            }
        outcome = extract_scene(session, job, provider)
        session.commit()
        return {
            "done": False,
            "scene": outcome.__dict__,
            "progress": progress(session, _uuid(run_id)).__dict__,
        }
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None


@app.post("/api/extract/{run_id}/cancel")
def cancel_extraction(run_id: str, session: Session = Depends(get_session)):
    """Stop the run; scenes already extracted stay in the graph."""
    try:
        cancel_run(session, _uuid(run_id))
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None
    return progress(session, _uuid(run_id)).__dict__


@app.get("/api/extract/{run_id}/progress")
def extraction_progress(run_id: str, session: Session = Depends(get_session)):
    """Current run progress."""
    try:
        return progress(session, _uuid(run_id)).__dict__
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None


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


FINDING_TITLES = {
    "orphaned_reference": "Orphaned reference",
    "continuity_conflict": "Continuity conflict",
}


def _finding_payload(finding) -> dict:
    """One finding as the preview overlay renders it.

    Handles both the fresh rows (citations attached in memory) and the
    rebuild's stored rows (citations read from finding_evidence).
    """
    entity_label = getattr(finding, "entity_label", "") or ""
    if entity_label:
        title = f"Later units still reference {entity_label}"
    else:
        title = FINDING_TITLES.get(
            getattr(finding, "finding_type", ""), "Continuity finding"
        )
    return {
        "id": str(finding.id) if getattr(finding, "id", None) else None,
        "severity": getattr(finding, "severity", "high"),
        "title": title,
        "message": finding.message,
        "status": getattr(finding, "status", "open"),
        "cited_units": [
            str(unit_id) for unit_id in getattr(finding, "later_unit_ids", [])
        ],
        "scenes": getattr(finding, "later_scene_numbers", []),
    }


def _preview_payload(
    session: Session, result: PreviewResult, model_id: str | None
) -> dict[str, Any]:
    """The response the preview page renders, from a PreviewResult."""
    edits = []
    for edit in result.edits:
        edit_unit = session.get(ScriptUnit, _uuid(edit["unit_id"]))
        edit_scene = session.get(Scene, edit_unit.scene_id)
        edits.append(
            {
                **edit,
                # An unnumbered scene shows a dash rather than its position,
                # which would collide with a numbered scene elsewhere.
                "scene_number": edit_scene.display_scene_number or "—",
            }
        )
    first = edits[0]
    unit = session.get(ScriptUnit, _uuid(first["unit_id"]))
    anchor = unit.anchors[0] if unit.anchors else None
    diff = result.diff
    # What this preview spent: the audit rows linked to its change set,
    # tokens and money both. A cached replay sums to zero and says so.
    calls = list(
        session.scalars(
            select(ModelCall).where(
                ModelCall.change_set_id == result.change_set.id
            )
        )
    )
    spent_tokens = sum(
        (call.input_tokens or 0)
        + (call.output_tokens or 0)
        + (call.reasoning_tokens or 0)
        for call in calls
    )
    cost = pricing.calls_cost_usd(calls)
    return {
        "change_set_id": str(result.change_set.id),
        "unit_id": first["unit_id"],
        "scene_number": first["scene_number"],
        "accepted_text": first["accepted_text"],
        "proposed_text": first["proposed_text"],
        "edits": edits,
        "severity": result.severity,
        "summary": result.summary,
        "summary_source": "deterministic",
        "model_id": model_id,
        "cached": result.cached,
        "extraction_error": None,
        "synthesis_error": None,
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
        "attribute_changes": [
            change.payload() for change in result.attribute_changes
        ],
        "findings": [_finding_payload(finding) for finding in result.findings],
        "continuity_error": result.continuity_error,
        "evidence_count": result.evidence_count,
        "spend": {
            "tokens": spent_tokens,
            "cost_usd": cost,
            "cost": pricing.display(cost),
        },
        "pipeline": result.stages,
        "judgement": result.judgement.summary() if result.judgement else None,
        "trace_id": result.trace_id,
        "origin": {
            "text": unit.current_text,
            "page": anchor.source_page_number if anchor else None,
            "start": anchor.source_start_offset if anchor else None,
            "end": anchor.source_end_offset if anchor else None,
            "method": anchor.extraction_method if anchor else None,
        },
    }


def _run_preview(
    session: Session, edits: list[preview_service.UnitEdit]
) -> dict[str, Any]:
    provider_name, model_id = settings_service.selected_model(session)
    provider = get_provider(provider_name) if provider_name else None
    result = preview_service.preview_changes(session, edits, provider, model_id)
    return _preview_payload(session, result, model_id)


@app.post("/api/scripts/{script_id}/preview")
def preview_script_changes(
    script_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Preview any number of unit edits as one proposal.

    The judgement engine runs once per affected scene, against the stored
    graph.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    raw_edits = payload.get("edits") or []
    edits = [
        preview_service.UnitEdit(
            unit_id=str(edit.get("unit_id", "")),
            proposed_text=str(edit.get("proposed_text", "")),
        )
        for edit in raw_edits
        if isinstance(edit, dict)
    ]
    return _run_preview(session, edits)


@app.post("/api/units/{unit_id}/preview")
def preview_ripple(
    unit_id: str,
    proposed_text: str = Form(...),
    session: Session = Depends(get_session),
):
    """Preview one unit's edit. The single-unit form of the same engine."""
    unit = session.get(ScriptUnit, _uuid(unit_id))
    if unit is None:
        raise HTTPException(404, "No such unit")
    return _run_preview(
        session,
        [preview_service.UnitEdit(unit_id=str(unit.id), proposed_text=proposed_text)],
    )


@app.post("/api/changes/{change_set_id}/explain")
def explain_change(change_set_id: str, session: Session = Depends(get_session)):
    """Write the model explanation for an existing preview, on demand.

    Synthesis is a click rather than an automatic call: the deterministic
    summary is free and always shown, so the model only writes prose when
    someone asks for it.
    """
    change_set = session.get(ChangeSet, _uuid(change_set_id))
    if change_set is None:
        raise HTTPException(404, "No such change set")
    report = session.scalar(
        select(RippleReport).where(RippleReport.change_set_id == change_set.id)
    )
    if report is None:
        raise HTTPException(404, "This proposal has no preview to explain")

    diff = GraphDiff()
    for operation in change_set.operations:
        before = operation.before_json or {}
        after = operation.after_json or {}
        if operation.operation_type == "add_assertion":
            diff.added.append(preview_service._edge_from_payload(after))
        elif operation.operation_type == "remove_assertion":
            diff.removed.append(preview_service._edge_from_payload(before))
        elif operation.operation_type == "update_assertion":
            diff.changed.append(
                (
                    preview_service._edge_from_payload(before),
                    preview_service._edge_from_payload(after),
                )
            )
    findings = list(
        session.scalars(
            select(ContinuityFinding).where(
                ContinuityFinding.change_set_id == change_set.id,
                ContinuityFinding.finding_type == "orphaned_reference",
            )
        )
    )

    provider_name, model_id = settings_service.selected_model(session)
    provider = get_provider(provider_name) if provider_name else None
    if provider is None or not model_id:
        raise HTTPException(400, "No model is selected. Pick one in Settings.")

    synthesis = synthesize(
        diff, findings, provider, model_id, session, change_set.script_id
    )
    if synthesis.generated:
        report.summary = synthesis.summary
        report.model_id = synthesis.model_id
        session.flush()
    return {
        "summary": synthesis.summary,
        "source": synthesis.source,
        "model_id": synthesis.model_id,
        "error": synthesis.error,
    }


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


@app.get("/api/findings/{finding_id}")
def finding_detail(finding_id: str, session: Session = Depends(get_session)):
    """One finding with the unit ids its evidence cites, for Review."""
    finding = session.get(ContinuityFinding, _uuid(finding_id))
    if finding is None:
        raise HTTPException(404, "No such finding")
    cited_units = []
    cited_scenes = []
    for evidence in finding.evidence:
        unit = session.get(ScriptUnit, evidence.script_unit_id)
        if unit is None:
            continue
        # The reader renders headings as scene chrome, never as line nodes,
        # so a cited heading is addressed through its scene instead.
        if unit.unit_type == "scene_heading":
            if str(unit.scene_id) not in cited_scenes:
                cited_scenes.append(str(unit.scene_id))
        else:
            cited_units.append(str(unit.id))
    return {
        "id": str(finding.id),
        "status": finding.status,
        "finding_type": finding.finding_type,
        "message": finding.message,
        "cited_units": cited_units,
        "cited_scenes": cited_scenes,
    }


@app.post("/api/findings/{finding_id}/confirm-rename")
def confirm_rename_route(finding_id: str, session: Session = Depends(get_session)):
    """Apply the rename a possible_rename finding describes.

    The two identities join: one entity, the new name current, the old name
    recorded as an alias, and any line still under the old name reported as
    a partial rename.
    """
    finding = session.get(ContinuityFinding, _uuid(finding_id))
    if finding is None:
        raise HTTPException(404, "No such finding")
    if finding.status != "open":
        raise HTTPException(409, f"This finding is {finding.status}.")
    ensure_tracing()
    with ActionTrace.start(action="rename.confirm", kind="change") as trace:
        trace.input({"finding_id": str(finding.id), **(finding.payload_json or {})})
        try:
            survivor = renames_service.confirm_rename(session, finding)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        trace.output(
            {
                "entity_id": str(survivor.id),
                "entity": survivor.canonical_name,
            }
        )
    return {
        "id": str(finding.id),
        "status": finding.status,
        "entity_id": str(survivor.id),
        "entity": survivor.canonical_name,
    }


# A grounded question is short by nature. The cap bounds a single ask's input
# tokens and denies a large injected instruction a path in through the query.
MAX_QUESTION_CHARS = 1024

# The most entity names of one department the packet lists. A department this
# large is enumerable by its count; listing every name past this would bloat
# the packet with no gain. The count beside the names is always the true total.
ROSTER_NAME_CAP = 80

# The most assertions the packet carries when a question falls back to the whole
# graph. Set above the largest demo-corpus script so scene-anchored and
# aggregate questions see every assertion; it only truncates a far larger graph,
# and a keyword-scoped question sends its focused hits regardless.
PACKET_ASSERTION_CAP = 1000
# The evidence panel shows a sample of the grounding units, not all of them; the
# true total rides alongside so the count on screen is never mistaken for it.
EVIDENCE_UNIT_SAMPLE = 6


def _fold(text: str) -> str:
    """Casefold and strip diacritics for keyword matching.

    A question typed without accents must still find the accented entity: "bela"
    matches "Béla", "matias" matches "Matías". NFKC (the entity-name normalizer)
    keeps the accents, so this decomposes and drops the combining marks instead.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


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

    # A question is a question, not a place to paste a payload. The cap keeps
    # a wall of injected instructions from arriving as a "query" and bounds the
    # input tokens a single ask can bill, before any provider contact.
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            422,
            f"A question can be at most {MAX_QUESTION_CHARS} characters; "
            f"this one is {len(question)}.",
        )

    labels = _labels(session, script.id)
    terms = [_fold(w) for w in question.split() if len(w) > 3]
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

    mapped = []
    keyword_hits = []
    for row in rows:
        subject = labels.get(row.subject_entity_id or row.subject_scene_id, "")
        obj = labels.get(row.object_entity_id or row.object_scene_id, "")
        haystack = _fold(f"{subject} {row.predicate} {obj}")
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
    # the keyword pass can match, a scene-anchored or aggregate one ("what props
    # are in scene 8", "which scene has the most props"), falls back to the whole
    # graph rather than a 40-row sample: those scenes' assertions carry no term
    # to match, so a small sample misses them. A user query is infrequent and
    # costs a fraction of a cent, so the cap holds a whole demo-corpus script and
    # only bounds a pathologically large graph.
    matched = (keyword_hits or mapped)[:PACKET_ASSERTION_CAP]

    # Entities as a roster grouped by department type: the count and the names.
    # A "who / list / name the X" question needs the names, not only the total;
    # a packet with counts alone answered "the graph does not record the full
    # list" about a graph that records every one. Keys are the entity_type
    # vocabulary; "cast" is the characters. Names are capped per type so a huge
    # department cannot crowd the packet, with the true count kept beside them.
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

    facts = {
        "title": script.title,
        "scenes": len(scene_list),
        "entities": sum(slot["count"] for slot in roster.values()),
        "entities_by_type": roster,
        "scene_list": scene_list,
        "assertions": len(rows),
    }

    # The Ask path runs through Google's google-genai SDK, not KeyCall's HTTP
    # path: a Google SDK generation on every asked question.
    provider_name, model_id = settings_service.selected_model(session)
    provider = get_query_provider(provider_name) if provider_name else None
    ensure_tracing()
    with ActionTrace.start(action="graph.query", kind="query") as query_trace:
        query_trace.input(
            {"question": question, "terms": len(terms), "matched": len(matched)}
        )
        answer = answer_question(
            question,
            matched,
            provider,
            model_id,
            session,
            script.id,
            facts=facts,
        )
        query_trace.output(
            {
                "answer": answer.answer,
                "generated": answer.generated,
                "cited": len(answer.cited_assertion_ids),
            }
        )

    record = QueryLog(
        script_id=script.id,
        question=question,
        answer=answer.answer,
        cited_assertion_ids_json=answer.cited_assertion_ids,
        model_id=answer.model_id,
        prompt_version=answer.prompt_version,
    )
    session.add(record)
    session.flush()

    seen: set[str] = set()
    cited = []
    for item in matched:
        if item["unit_id"] in seen:
            continue
        seen.add(item["unit_id"])
        cited.append(
            {
                "scene": item["scene"],
                "scene_heading": item["scene_heading"],
                "unit_id": item["unit_id"],
                "text": item["unit_text"],
            }
        )

    grounded_names = {item["subject"] for item in matched} | {
        item["object"] for item in matched
    }
    all_names = set(
        session.scalars(
            select(Entity.canonical_name).where(Entity.script_id == script.id)
        )
    ) | set(
        session.scalars(
            select(EntityAlias.alias)
            .join(Entity, EntityAlias.entity_id == Entity.id)
            .where(Entity.script_id == script.id)
        )
    )
    # Only a written answer can reach past its evidence; the deterministic
    # answer is built from the grounding set and needs no check.
    ungrounded = (
        ungrounded_entities(answer.answer, grounded_names, all_names)
        if answer.generated
        else []
    )

    confidences = [item["confidence"] for item in matched] or [0.0]
    return {
        "query_id": str(record.id),
        "answer": answer.answer,
        "generated": answer.generated,
        "grounded_in": len(matched),
        "total_units": len(seen),
        "cited_units": cited[:EVIDENCE_UNIT_SAMPLE],
        "mean_confidence": round(sum(confidences) / len(confidences), 2),
        "entities": sorted(
            {item["subject"] for item in matched}
            | {item["object"] for item in matched}
        )[:8],
        "ungrounded_entities": ungrounded,
    }


@app.get("/api/queries/{query_id}")
def stored_query(query_id: str, session: Session = Depends(get_session)):
    """A past question's stored answer, rebuilt for the ask page at no cost.

    The answer text and citation ids come from the log; the cited units and
    entities are resolved from whichever of those assertions still exist, so a
    stored answer over a since-changed graph shows what remains rather than
    failing. The response says it is stored, and when it was asked.
    """
    record = session.get(QueryLog, _uuid(query_id))
    if record is None:
        raise HTTPException(404, "No such question")

    ids = [_uuid(item) for item in (record.cited_assertion_ids_json or [])]
    rows = (
        list(session.scalars(select(Assertion).where(Assertion.id.in_(ids))))
        if ids
        else []
    )
    labels = _labels(session, record.script_id)
    scene_of = {
        unit_id: {"number": number, "heading": heading}
        for unit_id, number, heading in session.execute(
            select(ScriptUnit.id, Scene.display_scene_number, Scene.heading)
            .join(Scene)
            .where(Scene.script_id == record.script_id)
        ).all()
    }
    unit_text = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.current_text)
            .join(Scene)
            .where(Scene.script_id == record.script_id)
        ).all()
    )
    seen: set = set()
    cited = []
    names: set[str] = set()
    for row in rows:
        names.add(labels.get(row.subject_entity_id or row.subject_scene_id, "?"))
        names.add(labels.get(row.object_entity_id or row.object_scene_id, "?"))
        if row.source_unit_id in seen:
            continue
        seen.add(row.source_unit_id)
        where = scene_of.get(row.source_unit_id) or {}
        cited.append(
            {
                "scene": where.get("number"),
                "scene_heading": where.get("heading"),
                "unit_id": str(row.source_unit_id),
                "text": unit_text.get(row.source_unit_id, ""),
            }
        )
    confidences = [row.confidence for row in rows] or [0.0]
    return {
        "query_id": str(record.id),
        "question": record.question,
        "answer": record.answer,
        "generated": record.model_id is not None,
        "grounded_in": len(rows),
        "total_units": len(seen),
        "cited_units": cited[:EVIDENCE_UNIT_SAMPLE],
        "mean_confidence": round(sum(confidences) / len(confidences), 2),
        "entities": sorted(names - {"?"})[:8],
        # No grounding badge on a stored answer: the check ran against the
        # grounding set at ask time, and the graph may have changed since.
        "ungrounded_entities": None,
        "stored": True,
        "asked_at": record.asked_at.strftime("%d %b %H:%M"),
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
def provider_models(provider: str, session: Session = Depends(get_session)):
    """Selectable text-generation models for a configured provider.

    `unavailable` marks a model a live call has found dead on this account.
    The catalog cannot say this on its own, so the mark comes from recorded
    call failures and clears when a later call succeeds.
    """
    dead = unavailable_models(session, provider)
    return [
        {
            "id": model.id,
            "display_name": model.display_name,
            "tier": model.tier.value,
            "context_window": model.context_window,
            "unavailable": model.id in dead,
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


@app.post("/api/settings/fallback")
def choose_fallback(
    provider: str = Form(...),
    model_id: str = Form(""),
    session: Session = Depends(get_session),
):
    """Persist the fallback model. An empty model clears it.

    The fallback answers a call the main model refused for an availability
    reason: a dead model on this account, a provider outage, a rate limit.
    """
    settings_service.select_fallback(session, provider, model_id or None)
    return {"provider": provider, "model_id": model_id or None}


# Helpers


def _uuid(value: str):
    """Parse a path identifier, refusing anything that is not a UUID."""
    import uuid as uuid_module

    try:
        return uuid_module.UUID(value)
    except ValueError:
        raise HTTPException(400, "Not a valid identifier") from None


def _labels(session: Session, script_id) -> dict[Any, str]:
    return graph_labels(session, script_id)


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


def _edge_payload(edge: Edge) -> dict[str, Any]:
    return {
        "subject": edge.display_subject or edge.subject.label,
        "predicate": edge.predicate,
        "object": edge.display_object or edge.obj.label,
        "confidence": round(edge.confidence, 2),
    }


__all__ = ["app", "seed_demo_corpus"]
