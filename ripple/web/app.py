"""The Ripple web application.

A thin HTTP layer over the services. Every route resolves a session, calls into
`ripple.*`, and returns data; no business rule lives here, so the same
operations stay testable without a client.

The extraction loop is browser-driven: the page asks for one
scene at a time and the server commits each independently. That keeps the work
inside request handlers, with no background worker to pay for.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from traceact import ActionTrace

from ripple.adapters import import_screenplay
from ripple.adapters.base import MAX_UPLOAD_BYTES, ImportRejected
from ripple.config.secrets import SecretStore
from ripple.db.ids import as_uuid
from ripple.db.models import (
    Assertion,
    ChangeOperation,
    ChangeSet,
    ContinuityFinding,
    Conversation,
    Entity,
    EntityAlias,
    ExtractionRun,
    FindingEvidence,
    Import,
    ModelCall,
    QueryLog,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
    now,
)
from ripple.db.repository import (
    AGENT_CONFIDENCE_FLOORS,
    AGENT_TOOL_CEILINGS,
    LANDING_VIEWS,
    SCREENSAVER_COLOURS,
    SCREENSAVER_FADE_SECONDS,
    SCREENSAVER_IDLE_MINUTES,
    SCREENSAVER_THEMES,
    SCREENSAVER_THROTTLE_SECONDS,
    clear_all_graphs,
    clear_graph,
    delete_script,
    deletion_preview,
    get_agent_settings,
    get_landing_view,
    get_screensaver_settings,
    graph_labels,
    persist_import,
    set_agent_setting,
    set_landing_view,
    set_screensaver_setting,
    unavailable_models,
)
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction import worker as extraction_worker
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
from ripple.services import agent, authoring, changeset, conversations, pricing, spend
from ripple.services import draft_report as report_service
from ripple.services import drafts as drafts_service
from ripple.services import duplicates as duplicates_service
from ripple.services import preview as preview_service
from ripple.services import renames as renames_service
from ripple.services import scenes as scenes_service
from ripple.services.changeset import InvalidOperation
from ripple.services.preview import PreviewFailed, PreviewRefused, PreviewResult
from ripple.services.retrieval import PRESENCE_PREDICATES, build_packet, fold
from ripple.services.settings import SettingsService
from ripple.services.synthesizer import (
    answer_question,
    synthesize,
    ungrounded_entities,
)
from ripple.text import when_label
from ripple.tracing import configure_tracing
from ripple.tracing import ensure_configured as ensure_tracing
from ripple.web.paging import paginate
from ripple.web.stats import eighths, page_of, pages_by_script, runtime, script_pages

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
        "graphs": count(Script, Script.graph_status != "not_analysed"),
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
    ids = [script.id for script in scripts]
    # One read per figure across the library, rather than four per script.
    imports: dict = {}
    for record in session.scalars(
        select(Import).where(Import.script_id.in_(ids)).order_by(Import.imported_at)
    ):
        imports.setdefault(record.script_id, record)
    scene_counts = dict(
        session.execute(
            select(Scene.script_id, func.count())
            .where(Scene.script_id.in_(ids))
            .group_by(Scene.script_id)
        ).all()
    )
    page_counts = pages_by_script(session, ids)
    graphed = {
        script_id
        for (script_id,) in session.execute(
            select(Assertion.script_id)
            .where(Assertion.script_id.in_(ids))
            .group_by(Assertion.script_id)
        ).all()
    }

    rows = []
    for script in scripts:
        record = imports.get(script.id)
        pages = page_counts.get(script.id, 1)
        rows.append(
            {
                "id": str(script.id),
                "title": script.title,
                "has_graph": script.id in graphed,
                "format": FORMAT_LABELS.get(
                    record.detected_format if record else "", "Unknown"
                ),
                "pages": pages,
                "scenes": scene_counts.get(script.id, 0),
                "runtime": runtime(pages),
                # The sort reads minutes, so 38m orders under 2h 51m rather
                # than beside it alphabetically.
                "runtime_minutes": pages,
                "outcome": script.import_status,
                "outcome_label": OUTCOME_LABELS.get(
                    script.import_status, script.import_status
                ),
                "warnings": [
                    warning.get("message", "")
                    for warning in (record.warnings_json or [])
                    if warning.get("message")
                ] if record else [],
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

    # A line an open finding cites carries a mark of its own, so the warnings
    # are visible in the script rather than only on the findings page.
    flagged = set(_flagged_units(session, script.id))

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
                        "flagged": str(unit.id) in flagged,
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
    # "Needs review" without the reasons on screen is a label pointing at
    # the database; the banner puts the stored warnings where the review
    # happens.
    review_warnings = []
    if script.import_status == "needs_review":
        record = session.scalar(
            select(Import).where(Import.script_id == script.id).limit(1)
        )
        review_warnings = [
            warning.get("message", "")
            for warning in (record.warnings_json or [])
            if warning.get("message")
        ] if record else []
        if not review_warnings:
            review_warnings = [
                "The importer was not confident in this parse; check the "
                "scene list against the source."
            ]
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
            "review_warnings": review_warnings,
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
        # No script named, none assumed: the page opens on a chooser, since
        # asking is always about one script and the choice is the user's.
        chosen = None
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
            "conversations": (
                conversations.listing(session, chosen.id) if chosen else []
            ),
            "agent": get_agent_settings(session),
            "history": (
                [
                    {
                        "id": str(row.id),
                        "question": row.question,
                        "asked_at": when_label(row.asked_at),
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
    """Render the shared list template with the sidebar counts filled in.

    The query string chooses the sort, the search, and the page; only that
    page's rows are rendered. `fixed` names parameters every link on the
    page keeps, such as the findings page's script filter.
    """
    page = paginate(
        request.query_params,
        request.url.path,
        kwargs["items"],
        kwargs["columns"],
        fixed=kwargs.pop("fixed", None),
    )
    return templates.TemplateResponse(
        request,
        "list.html",
        {"counts": sidebar_counts(session), "page": page, **kwargs},
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
            "cells": {
                "when": {
                    "text": when_label(report.generated_at),
                    "class": "tiny muted num",
                },
                "severity": {
                    "text": report.severity,
                    "tag": True,
                    "tag_class": {
                        "high": "stunt",
                        "medium": "prop",
                        "low": "set_design",
                    }.get(report.severity, ""),
                },
                "summary": {"text": report.summary},
                "script": {"text": script.title, "class": "tiny"},
                "kind": {"text": change_set.kind, "class": "tiny muted"},
                "status": {"text": change_set.status, "class": "tiny muted"},
                "model": {
                    "text": report.model_id or "deterministic",
                    "class": "tiny muted",
                },
            },
            "sort": {
                "when": report.generated_at.isoformat(),
                # High first when the column is sorted the way it opens, so
                # the ranking is by weight rather than by alphabet.
                "severity": {"high": 3, "medium": 2, "low": 1}.get(
                    report.severity, 0
                ),
                "summary": report.summary,
                "script": script.title,
                "kind": change_set.kind,
                "status": change_set.status,
                "model": report.model_id or "deterministic",
            },
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
        columns=[
            {"key": "when", "label": "Generated", "width": "9%"},
            {"key": "severity", "label": "Severity", "numeric": True, "width": "9%"},
            {"key": "summary", "label": "Report", "width": "36%"},
            {"key": "script", "label": "Script", "width": "13%"},
            {"key": "kind", "label": "Kind", "width": "11%"},
            {"key": "status", "label": "Status", "width": "10%"},
            {"key": "model", "label": "Model", "width": "12%"},
        ],
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
            "cells": {
                "when": {
                    "text": when_label(finding.created_at),
                    "class": "tiny muted num",
                },
                "status": {
                    "text": finding.status,
                    "tag": True,
                    "tag_class": {
                        "open": "stunt",
                        "dismissed": "",
                        "resolved": "set_design",
                    }.get(finding.status, ""),
                },
                "message": {
                    "text": finding.message,
                    "sub": (
                        f"dismissed: {finding.dismissal_reason}"
                        if finding.dismissal_reason
                        else None
                    ),
                },
                "script": {"text": script.title, "class": "tiny"},
                "type": {"text": finding.finding_type, "class": "tiny muted"},
                "severity": {"text": finding.severity, "class": "tiny muted"},
            },
            "sort": {
                "when": finding.created_at.isoformat(),
                # Open first, then dismissed, then resolved: the order the
                # user works through them in.
                "status": {"open": 3, "dismissed": 2, "resolved": 1}.get(
                    finding.status, 0
                ),
                "message": finding.message,
                "script": script.title,
                "type": finding.finding_type,
                "severity": {"high": 3, "medium": 2, "low": 1}.get(
                    finding.severity, 0
                ),
            },
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
        columns=[
            {"key": "when", "label": "Raised", "width": "9%"},
            {"key": "status", "label": "Status", "numeric": True, "width": "9%"},
            {"key": "message", "label": "Finding", "width": "34%"},
            {"key": "script", "label": "Script", "width": "12%"},
            {"key": "type", "label": "Type", "width": "13%"},
            {"key": "severity", "label": "Severity", "numeric": True, "width": "9%"},
            {"key": "actions", "label": "", "width": "14%"},
        ],
        empty=(
            f"No findings for {chosen.title}." if chosen is not None
            else "No findings yet."
        ),
        lock=None,
        fixed={"script": str(chosen.id)} if chosen is not None else None,
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
        # The page sorts over this window, so it has to be wide enough that
        # a sort by cost means something. Older calls than this stay in the
        # database and in the trace files.
        .limit(1000)
    ).all()

    def cost_of(call: ModelCall) -> float | None:
        return pricing.cost_usd(
            "google",
            call.model_id,
            call.input_tokens,
            call.output_tokens,
            call.reasoning_tokens,
        )

    def total_tokens(call: ModelCall) -> int:
        return (
            (call.input_tokens or 0)
            + (call.output_tokens or 0)
            + (call.reasoning_tokens or 0)
        )

    def tokens_of(call: ModelCall) -> str:
        """The split behind the total: the columns carry the rest."""
        if call.input_tokens is None and call.output_tokens is None:
            return "no token counts"
        counts = f"{call.input_tokens or 0} in · {call.output_tokens or 0} out"
        if call.reasoning_tokens:
            counts += f" · {call.reasoning_tokens} reasoning"
        return counts

    items = [
        {
            "cells": {
                "when": {
                    "text": when_label(call.created_at),
                    "class": "tiny muted num",
                },
                "outcome": {
                    "text": call.outcome,
                    "tag": True,
                    "tag_class": {
                        "ok": "set_design",
                        "cached": "set_design",
                        "budget_refused": "prop",
                    }.get(call.outcome, "stunt"),
                },
                "purpose": {
                    "text": call.purpose,
                    "sub": call.error_message or call.prompt_version,
                },
                "model": {"text": call.model_id, "class": "tiny muted"},
                "script": {
                    "text": script.title if script else "—",
                    "class": "tiny",
                },
                "tokens": {
                    "text": f"{total_tokens(call):,}",
                    "sub": tokens_of(call),
                },
                "cost": {"text": pricing.display(cost_of(call)) or "—"},
                "duration": {
                    "text": f"{call.duration_ms} ms" if call.duration_ms else "—",
                    "class": "tiny muted",
                },
            },
            # What the columns order on, kept apart from the display strings
            # so a cost sorts as a number and a date as a date.
            "sort": {
                "when": call.created_at.isoformat(),
                "purpose": call.purpose,
                "model": call.model_id,
                "script": script.title if script else "",
                "tokens": total_tokens(call),
                "cost": cost_of(call) if cost_of(call) is not None else -1,
                "duration": call.duration_ms or 0,
                "outcome": call.outcome,
            },
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
        columns=[
            {"key": "when", "label": "Time", "width": "9%"},
            {"key": "outcome", "label": "Outcome", "width": "11%"},
            {"key": "purpose", "label": "Purpose", "width": "20%"},
            {"key": "model", "label": "Model", "width": "14%"},
            {"key": "script", "label": "Script", "width": "13%"},
            {"key": "tokens", "label": "Tokens", "numeric": True, "width": "15%"},
            {"key": "cost", "label": "Cost", "numeric": True, "width": "9%"},
            {"key": "duration", "label": "Duration", "numeric": True, "width": "9%"},
        ],
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


@app.post("/api/settings/agent")
def choose_agent_setting(
    key: str = Form(...),
    value: str = Form(...),
    session: Session = Depends(get_session),
):
    """Persist one Ask Ripple preference."""
    try:
        set_agent_setting(session, key, value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return get_agent_settings(session).__dict__


@app.get("/api/settings/screensaver")
def read_screensaver_settings(session: Session = Depends(get_session)):
    """The screensaver preferences, read by the overlay on every page."""
    return get_screensaver_settings(session).__dict__


@app.post("/api/settings/screensaver")
def choose_screensaver_setting(
    key: str = Form(...),
    value: str = Form(...),
    session: Session = Depends(get_session),
):
    """Persist one screensaver preference."""
    try:
        set_screensaver_setting(session, key, value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return get_screensaver_settings(session).__dict__


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


@app.get("/graphs")
def graphs_page(request: Request, session: Session = Depends(get_session)):
    """Every built graph, one row per script, with batch delete.

    Deleting here removes the graph alone: entities, assertions, runs,
    change sets, and findings. The script, its scenes, and its units stay,
    ready to be re-analysed, which is the point: a graph can be rebuilt for
    pennies, a script cannot.
    """
    scripts = list(
        session.scalars(
            select(Script)
            .where(Script.graph_status != "not_analysed")
            .order_by(Script.title)
        )
    )
    entity_counts = dict(
        session.execute(
            select(Entity.script_id, func.count()).group_by(Entity.script_id)
        ).all()
    )
    assertion_counts = dict(
        session.execute(
            select(Assertion.script_id, func.count())
            .where(Assertion.active.is_(True))
            .group_by(Assertion.script_id)
        ).all()
    )
    latest_runs: dict = {}
    for run in session.scalars(
        select(ExtractionRun).order_by(ExtractionRun.started_at)
    ):
        latest_runs[run.script_id] = run

    items = []
    for script in scripts:
        entities = entity_counts.get(script.id, 0)
        assertions = assertion_counts.get(script.id, 0)
        run = latest_runs.get(script.id)
        built = ""
        if run is not None and run.completed_at is not None:
            built = run.completed_at.strftime("%Y-%m-%d %H:%M")
        elif run is not None and run.started_at is not None:
            built = run.started_at.strftime("%Y-%m-%d %H:%M")
        model = run.model_id if run is not None else "seeded"
        items.append(
            {
                "id": str(script.id),
                "batch_kinds": ["clear_graph"],
                "assertions": entities + assertions,
                "cells": {
                    "script": {"text": script.title},
                    "status": {
                        "text": script.graph_status.replace("_", " "),
                        "tag": True,
                        "tag_class": {
                            "ready": "location",
                            "failed": "stunt",
                        }.get(script.graph_status, "off"),
                    },
                    "entities": {"text": str(entities)},
                    "assertions": {"text": str(assertions)},
                    "model": {"text": model, "class": "tiny muted"},
                    "built": {"text": built or "—", "class": "tiny muted num"},
                },
                "sort": {
                    "script": script.title,
                    "status": script.graph_status,
                    "entities": entities,
                    "assertions": assertions,
                    "model": model,
                    "built": built,
                },
                "actions": [
                    {"href": f"/scripts/{script.id}/graph", "label": "Open"},
                ],
            }
        )
    return _list_page(
        request,
        session,
        heading="Graphs",
        active="graphs",
        subtitle=(
            f"{len(items)} built graph(s). Deleting one keeps its script; "
            "Build graph starts it over."
        ),
        items=items,
        columns=[
            {"key": "script", "label": "Script", "width": "30%"},
            {"key": "status", "label": "Status", "width": "12%"},
            {"key": "entities", "label": "Entities", "numeric": True, "width": "10%"},
            {"key": "assertions", "label": "Assertions", "numeric": True,
             "width": "11%"},
            {"key": "model", "label": "Model", "width": "17%"},
            {"key": "built", "label": "Built", "numeric": True, "width": "12%"},
            {"key": "actions", "label": "", "width": "8%"},
        ],
        empty="No graph has been built yet. Open a script and press Build graph.",
        batch_actions=[
            {
                "kind": "clear_graph",
                "label": "Delete selected graphs",
                "url": "/api/graphs/batch/delete",
                "danger": True,
                "confirm": True,
            },
        ],
    )


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
    scripts = {script.id: script for _, script in rows}
    for script_id, script in scripts.items():
        for pair in duplicates_service.detect(session, script_id):
            items.append(
                {
                    "id": f"{pair.keep.id}:{pair.absorb.id}",
                    "batch_kinds": ["merge", "keep_separate"],
                    # Suspected duplicates lead the list however it is
                    # sorted, since they need a decision.
                    "lead": True,
                    "cells": {
                        "type": {
                            "text": "duplicate?",
                            "tag": True,
                            "tag_class": "duplicate",
                        },
                        "name": {
                            "text": (
                                f"{pair.keep.canonical_name} and "
                                f"{pair.absorb.canonical_name}"
                            ),
                            "sub": (
                                f"{pair.reason}; merging keeps "
                                f"{pair.keep.canonical_name} and records the "
                                "other name as its alias"
                            ),
                        },
                        "script": {"text": script.title, "class": "tiny"},
                        "aliases": {"text": "", "class": "tiny muted"},
                        "uses": {"text": ""},
                    },
                    "sort": {
                        "type": "duplicate?",
                        "name": pair.keep.canonical_name,
                        "script": script.title,
                        "aliases": "",
                        "uses": -1,
                    },
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
    # Aliases and citation counts for every entity in two reads, rather
    # than two reads per entity.
    aliases_of: dict = {}
    for entity_id, alias in session.execute(
        select(EntityAlias.entity_id, EntityAlias.alias)
    ).all():
        aliases_of.setdefault(entity_id, []).append(alias)
    cited = duplicates_service.cited_counts(session)
    for entity, script in rows:
        aliases = aliases_of.get(entity.id, [])
        uses = cited.get(entity.id, 0)
        items.append(
            {
                "id": str(entity.id),
                "batch_kinds": ["delete"],
                "assertions": uses,
                "cells": {
                    "type": {
                        "text": entity.entity_type.replace("_", " "),
                        "tag": True,
                        "tag_class": entity.entity_type,
                    },
                    "name": {"text": entity.canonical_name},
                    "script": {"text": script.title, "class": "tiny"},
                    "aliases": {
                        "text": ", ".join(sorted(set(aliases))[:4]) or "—",
                        "class": "tiny muted",
                    },
                    "uses": {"text": str(uses)},
                },
                "sort": {
                    "type": entity.entity_type,
                    "name": entity.canonical_name,
                    "script": script.title,
                    "aliases": str(len(set(aliases))),
                    "uses": uses,
                },
            }
        )
    return _list_page(
        request,
        session,
        heading="Entities",
        active="entities",
        subtitle=f"{len(items)} entity(s) across every script",
        items=items,
        columns=[
            {"key": "type", "label": "Type", "width": "11%"},
            {"key": "name", "label": "Name", "width": "22%"},
            {"key": "script", "label": "Script", "width": "14%"},
            {"key": "aliases", "label": "Also known as", "width": "26%"},
            {"key": "uses", "label": "Assertions", "numeric": True, "width": "11%"},
            {"key": "actions", "label": "", "width": "16%"},
        ],
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
        labels = graph_labels(session, script_id)
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

        def _row(row: Assertion, active: bool, script=script) -> dict:
            subject, obj = _label(row)
            return {
                "id": str(row.id),
                "batch_kinds": ["deactivate" if active else "reactivate"],
                "cells": {
                    "state": {
                        "text": "active" if active else "inactive",
                        "tag": True,
                        "tag_class": "set_design" if active else "off",
                    },
                    "predicate": {
                        "text": row.predicate.replace("_", " "),
                        "tag": True,
                        "tag_class": "location" if active else "off",
                    },
                    "claim": {"text": f"{subject} → {obj}"},
                    "script": {"text": script.title, "class": "tiny"},
                    "source": {
                        "text": row.provenance,
                        "sub": row.model_id or None,
                        "class": "tiny muted",
                    },
                    "confidence": {"text": f"{row.confidence:.2f}"},
                },
                "sort": {
                    # Active first, so a sort by state opens on the graph as
                    # it stands.
                    "state": 1 if active else 0,
                    "predicate": row.predicate,
                    "claim": f"{subject} {obj}",
                    "script": script.title,
                    "source": row.provenance,
                    "confidence": row.confidence,
                },
            }

        items.extend(_row(row, True) for row in active_rows[:500])
        items.extend(_row(row, False) for row in inactive_rows[:200])
    return _list_page(
        request,
        session,
        heading="Assertions",
        active="assertions",
        subtitle=f"{active_total} active"
        + (f", {inactive_total} inactive" if inactive_total else "")
        + " assertion(s)",
        items=items,
        columns=[
            {"key": "state", "label": "State", "numeric": True, "width": "9%"},
            {"key": "predicate", "label": "Predicate", "width": "13%"},
            {"key": "claim", "label": "Assertion", "width": "28%"},
            {"key": "script", "label": "Script", "width": "14%"},
            {"key": "source", "label": "Source", "width": "23%"},
            {"key": "confidence", "label": "Confidence", "numeric": True, "width": "13%"},
        ],
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
            "agent": get_agent_settings(session),
            "tool_ceilings": AGENT_TOOL_CEILINGS,
            "confidence_floors": AGENT_CONFIDENCE_FLOORS,
            "screensaver": get_screensaver_settings(session),
            "idle_minutes": SCREENSAVER_IDLE_MINUTES,
            "throttle_seconds": SCREENSAVER_THROTTLE_SECONDS,
            "screensaver_themes": SCREENSAVER_THEMES,
            "screensaver_colours": SCREENSAVER_COLOURS,
            "screensaver_fades": SCREENSAVER_FADE_SECONDS,
        },
    )


# Scripts


@app.post("/api/scripts")
async def upload_script(
    file: UploadFile = File(...),
    force: str | None = Form(None),
    session: Session = Depends(get_session),
):
    """Import an uploaded screenplay.

    A rejection is a 400 with the adapter's stable code, not a 500: an
    unparseable file is an expected outcome the UI has to explain.

    A file whose bytes already back a script in the library is not imported
    again on the first ask: the reply names the twin and the client asks the
    user, whose yes comes back as `force`.
    """
    data = await file.read()
    if not force:
        digest = hashlib.sha256(data).hexdigest()
        twin = session.scalars(
            select(Script)
            .join(Import, Import.script_id == Script.id)
            .where(Import.content_hash == digest)
            .order_by(Import.imported_at.desc())
        ).first()
        if twin is not None:
            return {"duplicate": {"id": str(twin.id), "title": twin.title}}
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


@app.post("/api/scripts/batch/delete")
def batch_delete_scripts(ids: str = Form(...), session: Session = Depends(get_session)):
    """Delete several scripts and everything under them."""
    deleted = 0
    scenes = entities = assertions = 0
    for raw in _batch_ids(ids):
        script = session.get(Script, _uuid(raw))
        if script is None:
            continue
        counts = delete_script(session, script.id)
        scenes += counts.scenes
        entities += counts.entities
        assertions += counts.assertions
        deleted += 1
    return {
        "deleted": deleted,
        "scenes": scenes,
        "entities": entities,
        "assertions": assertions,
    }


@app.post("/api/graphs/batch/delete")
def batch_delete_graphs(ids: str = Form(...), session: Session = Depends(get_session)):
    """Delete several scripts' graphs, keeping the scripts themselves."""
    cleared = 0
    entities = assertions = 0
    for raw in _batch_ids(ids):
        script = session.get(Script, _uuid(raw))
        if script is None:
            continue
        counts = clear_graph(session, script.id)
        entities += counts.entities
        assertions += counts.assertions
        cleared += 1
    return {"cleared": cleared, "entities": entities, "assertions": assertions}


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
    labels = graph_labels(session, scene.script_id)
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
        # The open warnings against this line, so the pane that explains the
        # line also carries what is wrong with it.
        "findings": _findings_citing(session, unit),
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
    labels = graph_labels(session, scene.script_id)
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
    labels = graph_labels(session, script.id)
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
    labels = graph_labels(session, entity.script_id)

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
    # A cue left dangling at the end of the scene being left was never a
    # cue: nothing spoke under it. Settled before the new scene opens.
    demoted: list[str] = []
    if payload.get("after_scene_id"):
        leaving = session.get(Scene, _uuid(payload["after_scene_id"]))
        if leaving is not None:
            demoted = authoring.settle_cues(session, leaving, include_last=True)
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
    # A scene created while writing sends extract=false: billing a model
    # read per skeleton scene would price the act of typing. Build graph
    # picks the written scenes up later in one run.
    if model_id and payload.get("extract", True):
        run = start_run(
            session,
            _uuid(script_id),
            model_id,
            scene_ids=[_uuid(inserted.scene_id)],
        )
        run_progress = progress(session, run.id).__dict__
    return {"scene": inserted.__dict__, "run": run_progress, "demoted": demoted}


@app.post("/api/scripts/new")
def create_script(
    payload: dict[str, Any] = Body(...), session: Session = Depends(get_session)
):
    """Create an empty script to write into. The body takes `title`."""
    script = authoring.create_script(session, str(payload.get("title") or ""))
    return {"id": str(script.id), "title": script.title}


@app.post("/api/scripts/{script_id}/title")
def rename_script(
    script_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Rename a script. The body takes `title`."""
    try:
        script = authoring.rename_script(
            session, _uuid(script_id), str(payload.get("title") or "")
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    return {"id": str(script.id), "title": script.title}


@app.post("/api/units/{unit_id}/text")
def save_unit_text(
    unit_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Save one line's text directly, for a line no active fact cites."""
    try:
        return authoring.save_unit_text(
            session, _uuid(unit_id), str(payload.get("text") or "")
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@app.post("/api/scenes/{scene_id}/units")
def insert_unit(
    scene_id: str,
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Write one new line into a scene.

    The body takes `text` and an optional `after_unit_id` (omitted or null
    writes the line at the top of the scene). The line's type is read from
    the text by the same conventions the importers use.
    """
    try:
        composed = authoring.insert_unit(
            session,
            _uuid(scene_id),
            _uuid(payload["after_unit_id"]) if payload.get("after_unit_id") else None,
            str(payload.get("text") or ""),
            unit_type=payload.get("unit_type") or None,
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    return composed.__dict__


@app.delete("/api/units/{unit_id}")
def delete_unit(unit_id: str, session: Session = Depends(get_session)):
    """Remove one line, when no active fact cites it as its source."""
    try:
        return authoring.delete_unit(session, _uuid(unit_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@app.delete("/api/scenes/{scene_id}")
def delete_scene(scene_id: str, session: Session = Depends(get_session)):
    """Remove a scene written by mistake, when no fact cites its lines."""
    try:
        return authoring.delete_scene(session, _uuid(scene_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@app.get("/api/scripts/{script_id}/export")
def export_script(script_id: str, session: Session = Depends(get_session)):
    """The script as Fountain plain text, downloaded as a file."""
    try:
        text = authoring.export_fountain(session, _uuid(script_id))
        script = session.get(Script, _uuid(script_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    slug = re.sub(r"[^A-Za-z0-9]+", "-", script.title).strip("-").lower() or "script"
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.fountain"'
        },
    )


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
    background: bool = Form(False),
    session: Session = Depends(get_session),
):
    """Create an extraction run for a script.

    `force` re-reads every scene instead of replaying the cache, which is what
    the reader's Rebuild asks for. It bills each scene, so the page confirms
    before sending it.

    `background` hands the run to a worker thread and lets the page poll,
    instead of the page posting once per scene. A build then survives the tab
    that started it.
    """
    provider_name, model_id = settings_service.selected_model(session)
    if not provider_name or not model_id:
        raise HTTPException(400, "Choose a provider and model in Settings first.")
    try:
        run = start_run(session, _uuid(script_id), model_id, force=force)
    except ValueError:
        raise HTTPException(404, "No such script") from None
    payload = progress(session, run.id).__dict__
    if background:
        # Committed before the worker starts: it opens its own sessions, and
        # they must be able to see the run and its jobs.
        session.commit()
        extraction_worker.start(_sessions, get_provider(provider_name), run.id)
        payload["background"] = True
    return payload


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


@app.post("/api/extract/{run_id}/background")
def background_extraction(run_id: str, session: Session = Depends(get_session)):
    """Hand an existing run to a worker thread.

    The draft link creates its run server-side, so the reader arrives holding
    a run id rather than starting one; this is how that run gets drained
    without the page posting per scene.
    """
    provider_name, _ = settings_service.selected_model(session)
    if not provider_name:
        raise HTTPException(400, "No provider selected.")
    try:
        payload = progress(session, _uuid(run_id)).__dict__
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None
    session.commit()
    started = extraction_worker.start(
        _sessions, get_provider(provider_name), _uuid(run_id)
    )
    payload["background"] = True
    payload["working"] = started or extraction_worker.is_running(run_id)
    return payload


@app.post("/api/extract/{run_id}/cancel")
def cancel_extraction(run_id: str, session: Session = Depends(get_session)):
    """Stop the run; scenes already extracted stay in the graph."""
    try:
        cancel_run(session, _uuid(run_id))
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None
    return progress(session, _uuid(run_id)).__dict__


@app.post("/api/scripts/{script_id}/mark-reviewed")
def mark_reviewed(script_id: str, session: Session = Depends(get_session)):
    """Close a needs_review import after a human has looked.

    The script's status becomes accepted_with_warnings, so the warnings stay
    visible in the library without holding the review queue open; the Import
    record keeps the original outcome for the audit.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    if script.import_status != "needs_review":
        raise HTTPException(400, "This script is not waiting on a review.")
    script.import_status = "accepted_with_warnings"
    return {"import_status": script.import_status}


@app.get("/api/extract/{run_id}/progress")
def extraction_progress(run_id: str, session: Session = Depends(get_session)):
    """Current run progress, and whether a worker is still draining it."""
    try:
        payload = progress(session, _uuid(run_id)).__dict__
    except ValueError:
        raise HTTPException(404, "No such extraction run") from None
    payload["working"] = extraction_worker.is_running(run_id)
    return payload


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


# What the judgement card says for each verification drop. The pipeline
# records rejections as internal codes and audit phrasing; the card gets a
# plain sentence for each, and the trace keeps the originals.
DROP_LINES = {
    "verdict names an unlisted id": (
        "The model answered about a fact that wasn't under review."
    ),
    "duplicate verdict": (
        "The model answered twice about one fact; the extra answer was ignored."
    ),
    "holds on vanished evidence, downgraded to removed": (
        "A fact was called unchanged, but the line supporting it is gone; "
        "recorded as removed."
    ),
    "removal not visible in the edited lines, downgraded to holds": (
        "A fact was called removed, but the edited lines don't show that; "
        "it was kept."
    ),
    "removal of an untouched fact the edit never names, downgraded to holds": (
        "A fact from an untouched line was called removed; the edit never "
        "mentions it, so it was kept."
    ),
    "changed without a new value, treated as holds": (
        "An attribute was called changed without a new value; it was kept."
    ),
    "value not stated in the edited lines, treated as holds": (
        "An attribute was called changed, but the edited lines never state "
        "its value; it was kept."
    ),
    "not_an_object": "A malformed item in the reply was skipped.",
    "missing_field": "A new fact arrived incomplete.",
    "unknown_source_unit": "A new fact cited a line outside this edit.",
    "bad_confidence": "An item came without a usable confidence.",
    "below_confidence_floor": "A new fact came with too little confidence.",
    "unresolved_endpoint": (
        "A new fact referred to something the reply never introduced."
    ),
    "signature_mismatch": (
        "A new fact connected things in a way the graph's rules don't allow."
    ),
    "duplicate_local_id": (
        "Two new entities shared one id; the later one was skipped."
    ),
    "unreferenced": "A new entity appeared with no fact referring to it.",
    "missing_local_id": "A new entity arrived without an id.",
    "unknown_entity_type": "A new entity had a type Ripple doesn't track.",
    "missing_canonical_name": "A new entity arrived without a name.",
    "pronoun_or_group_name": (
        "A new entity was named with a pronoun or a group label."
    ),
    "descriptive_location": (
        "A new location's name described the place rather than naming it."
    ),
    "sentence_like_name": "A new entity's name read as a sentence.",
    "missing_key": "A new attribute arrived without a name.",
    "missing_value": "A new attribute arrived without a value.",
    "duplicate_key": "Two attributes claimed one key; the later one was skipped.",
    "attribute missing key or value": (
        "A new attribute arrived without both a name and a value."
    ),
    "attribute below the floor": (
        "A new attribute came with too little confidence."
    ),
    "attribute cites an unshown unit": (
        "A new attribute cited a line outside this edit."
    ),
}


def _drop_line(reason: str) -> str:
    """One rejection reason as the judgement card shows it."""
    if reason in DROP_LINES:
        return DROP_LINES[reason]
    if reason.startswith("unknown verdict"):
        return "The model gave an answer outside the allowed verdicts."
    return "Part of the reply didn't fit the expected shape and was skipped."


def _judgement_payload(judgement) -> dict[str, Any]:
    """The judgement summary with its drop reasons reworded for the card.

    summary() is phrased for the model-call audit record. The card gets the
    same counts, each rejection translated, repeats collapsed with a tally;
    the full record stays in the trace.
    """
    summary = judgement.summary()
    lines: list[str] = []
    counts: dict[str, int] = {}
    for _, reason in summary["rejected"]:
        line = _drop_line(reason)
        if line not in counts:
            lines.append(line)
        counts[line] = counts.get(line, 0) + 1
    summary["dropped"] = len(summary["rejected"])
    summary["rejected"] = [_tallied(line, counts[line]) for line in lines]
    return summary


def _tallied(line: str, count: int) -> str:
    """A drop line, with how often it repeated when more than once."""
    if count == 1:
        return line
    return f"{line} (twice)" if count == 2 else f"{line} ({count} times)"


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
                "scene_number": edit_scene.label,
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
        "summary_source": result.summary_source,
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
        "judgement": (
            _judgement_payload(result.judgement) if result.judgement else None
        ),
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
        report.summary_model_id = synthesis.model_id
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


def _flagged_units(session: Session, script_id) -> list[str]:
    """Every line in a script that an open finding cites."""
    return [
        str(unit_id)
        for unit_id in session.scalars(
            select(FindingEvidence.script_unit_id)
            .join(ContinuityFinding, ContinuityFinding.id == FindingEvidence.finding_id)
            .join(ChangeSet, ChangeSet.id == ContinuityFinding.change_set_id)
            .where(
                ChangeSet.script_id == script_id,
                ContinuityFinding.status == "open",
                FindingEvidence.script_unit_id.is_not(None),
            )
            .distinct()
        )
    ]


def _finding_state(session: Session, finding: ContinuityFinding) -> dict[str, Any]:
    """What the reader repaints after a finding closes: the script's open
    count and the lines still carrying a mark.

    Read back rather than counted down in the page, so a reader open beside
    another tab that closed something shows the same script as the database.
    """
    change_set = session.get(ChangeSet, finding.change_set_id)
    if change_set is None:
        return {"open": 0, "flagged_units": []}
    open_count = session.scalar(
        select(func.count())
        .select_from(ContinuityFinding)
        .join(ChangeSet)
        .where(
            ChangeSet.script_id == change_set.script_id,
            ContinuityFinding.status == "open",
        )
    )
    return {
        "open": open_count,
        "flagged_units": _flagged_units(session, change_set.script_id),
    }


def _findings_citing(session: Session, unit: ScriptUnit) -> list[dict[str, Any]]:
    """The open continuity findings whose evidence cites this line.

    Each carries every line it cites, so the pane can offer to walk the other
    end of a conflict: a contradiction is two lines, and the one in front of
    you is only half of it.
    """
    findings = list(
        session.scalars(
            select(ContinuityFinding)
            .join(FindingEvidence, FindingEvidence.finding_id == ContinuityFinding.id)
            .where(
                FindingEvidence.script_unit_id == unit.id,
                ContinuityFinding.status == "open",
            )
            .order_by(ContinuityFinding.created_at.desc())
            .distinct()
        )
    )
    payload = []
    for finding in findings:
        cited = [
            str(evidence.script_unit_id)
            for evidence in finding.evidence
            if evidence.script_unit_id is not None
            and str(evidence.script_unit_id) != str(unit.id)
        ]
        payload.append(
            {
                "id": str(finding.id),
                "finding_type": finding.finding_type,
                "severity": finding.severity,
                "message": finding.message,
                # dict.fromkeys keeps the citation order while dropping the
                # repeats a multi-evidence finding leaves.
                "elsewhere": list(dict.fromkeys(cited)),
            }
        )
    return payload


@app.get("/api/scripts/{script_id}/findings")
def script_findings(script_id: str, session: Session = Depends(get_session)):
    """Every open finding on a script, in script order.

    The reader's Continuity card reads this with no line selected: the
    warnings on the script as a contents list, each one a way to the line it
    is about.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    rows = session.execute(
        select(ContinuityFinding, FindingEvidence, ScriptUnit, Scene)
        .join(ChangeSet, ChangeSet.id == ContinuityFinding.change_set_id)
        .outerjoin(
            FindingEvidence, FindingEvidence.finding_id == ContinuityFinding.id
        )
        .outerjoin(ScriptUnit, ScriptUnit.id == FindingEvidence.script_unit_id)
        .outerjoin(Scene, Scene.id == ScriptUnit.scene_id)
        .where(
            ChangeSet.script_id == script.id, ContinuityFinding.status == "open"
        )
        .order_by(ContinuityFinding.created_at.desc(), FindingEvidence.rank)
    ).all()

    findings: dict[str, dict[str, Any]] = {}
    for finding, _evidence, unit, scene in rows:
        entry = findings.setdefault(
            str(finding.id),
            {
                "id": str(finding.id),
                "finding_type": finding.finding_type,
                "severity": finding.severity,
                "message": finding.message,
                "units": [],
                "scene": None,
                "line": None,
                # Where the finding falls in the script, so the list reads in
                # the order a person walks the pages. A finding citing no line
                # has no place in that order and goes last.
                "at": (10**9, 10**9),
            },
        )
        if unit is None or scene is None:
            continue
        if str(unit.id) not in entry["units"]:
            entry["units"].append(str(unit.id))
        place = (scene.sequence_index, unit.sequence_index)
        if place < entry["at"]:
            entry["at"] = place
            entry["scene"] = scene.display_scene_number or ""
            entry["line"] = unit.current_text[:90]

    ordered = sorted(findings.values(), key=lambda item: item["at"])
    for entry in ordered:
        entry.pop("at")
    return {"findings": ordered}


@app.post("/api/findings/{finding_id}/resolve")
def resolve_finding(finding_id: str, session: Session = Depends(get_session)):
    """Close a finding as handled in the script.

    Dismiss says the warning was not a problem; this says the script now
    answers it. Neither touches the graph: the edit that settles a conflict
    is a ripple of its own.
    """
    finding = session.get(ContinuityFinding, _uuid(finding_id))
    if finding is None:
        raise HTTPException(404, "No such finding")
    finding.status = "resolved"
    finding.resolved_at = now()
    session.flush()
    return {
        "id": str(finding.id),
        "status": finding.status,
        **_finding_state(session, finding),
    }


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
    return {
        "id": str(finding.id),
        "status": finding.status,
        **_finding_state(session, finding),
    }


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

# The evidence panel shows a sample of the grounding units, not all of them; the
# true total rides alongside so the count on screen is never mistaken for it.
EVIDENCE_UNIT_SAMPLE = 6


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
    return grounded_answer(session, script, question)


def grounded_answer(session: Session, script: Script, question: str) -> dict:
    """The graph-grounded answer to one question, logged and cited.

    Ask Ripple routes a question here rather than to the agent: a question
    needs no tools, so it is answered on this path at this path's cost.
    """
    # A question is a question, not a place to paste a payload. The cap keeps
    # a wall of injected instructions from arriving as a "query" and bounds the
    # input tokens a single ask can bill, before any provider contact.
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            422,
            f"A question can be at most {MAX_QUESTION_CHARS} characters; "
            f"this one is {len(question)}.",
        )

    packet = build_packet(session, script, question)
    matched = packet.assertions
    facts = packet.facts

    # The Ask path runs through Google's google-genai SDK, not KeyCall's HTTP
    # path: a Google SDK generation on every asked question.
    provider_name, model_id = settings_service.selected_model(session)
    provider = get_query_provider(provider_name) if provider_name else None
    ensure_tracing()
    with ActionTrace.start(action="graph.query", kind="query") as query_trace:
        query_trace.input(
            {
                "question": question,
                "scoped": packet.matched_by_keyword,
                "matched": len(matched),
            }
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

    # The shown evidence prefers substantive lines. A presence edge (appears_in,
    # occurs_at), and any edge cited to a bare speaker cue (a lone all-caps
    # name), reads as noise as evidence: it names who is in a scene, not the
    # fact asked about. Those are held back unless the question is about who
    # appears where, or nothing else is grounding the answer. The grounding
    # count still reflects every scoped assertion; this only orders the six
    # units the card displays.
    asks_presence = any(
        marker in fold(question)
        for marker in ("appear", "which scene", "what scene", "in scene")
    )

    def _is_cue(text: str) -> bool:
        stripped = (text or "").strip()
        return bool(stripped) and " " not in stripped and stripped.isupper()

    seen: set[str] = set()
    substantive: list[dict] = []
    held_back: list[dict] = []
    for item in matched:
        if item["unit_id"] in seen:
            continue
        seen.add(item["unit_id"])
        entry = {
            "scene": item["scene"],
            "scene_heading": item["scene_heading"],
            "unit_id": item["unit_id"],
            "text": item["unit_text"],
        }
        presence = item["predicate"] in PRESENCE_PREDICATES and not asks_presence
        if presence or _is_cue(item["unit_text"]):
            held_back.append(entry)
        else:
            substantive.append(entry)
    cited = substantive or held_back

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


# One Ask Ripple request. The agent's own ceilings bound what a turn spends;
# this bounds what arrives, before any provider contact.
MAX_RIPPLE_CHARS = 2000


def _turn_payload(turn: agent.AgentTurn) -> dict:
    """One agent turn as the chat renders it."""
    preview = next(
        (
            run.payload
            for run in reversed(turn.tools)
            if run.name == "preview_edits" and run.ok
        ),
        None,
    )
    omission = next(
        (
            run.payload
            for run in reversed(turn.tools)
            if run.name == "preview_omit" and run.ok
        ),
        None,
    )
    return {
        "reply": turn.reply,
        "stage": turn.stage,
        "tools": [
            {"name": run.name, "summary": run.summary, "ok": run.ok}
            for run in turn.tools
        ],
        "plan": turn.plan,
        "question": turn.question,
        "ripple": preview,
        "omission": omission,
        "change_set_id": turn.change_set_id,
        "drafts": len(turn.drafts),
        "held_back": turn.held_back,
        "model_calls": turn.model_calls,
        "tokens": turn.tokens,
        "cost": pricing.display(turn.cost_usd),
        "stopped_at_ceiling": turn.stopped_at_ceiling,
        "trace_id": turn.trace_id,
    }


def _thread(session: Session, conversation_id: str, script_id=None) -> Conversation:
    """One conversation, checked against the script it belongs to."""
    thread = session.get(Conversation, _uuid(conversation_id))
    if thread is None or (script_id is not None and thread.script_id != script_id):
        raise HTTPException(404, "No such conversation")
    return thread


@app.post("/api/scripts/{script_id}/ripple")
def ripple_turn(
    script_id: str,
    message: str = Form(...),
    conversation_id: str = Form(None),
    stage: str = Form(None),
    session: Session = Depends(get_session),
):
    """One Ask Ripple exchange: a question answered, or an agent turn.

    There is one input box. A message that asks something is answered on the
    grounded fast path at that path's cost; a message that asks for a change
    opens a conversation and the agent works. Inside an open conversation
    every message goes to the agent, which is holding the context.
    """
    script = session.get(Script, _uuid(script_id))
    if script is None:
        raise HTTPException(404, "No such script")
    if len(message) > MAX_RIPPLE_CHARS:
        raise HTTPException(
            422,
            f"A request can be at most {MAX_RIPPLE_CHARS} characters; "
            f"this one is {len(message)}.",
        )

    agent_settings = get_agent_settings(session)
    provider_name, model_id = settings_service.selected_model(session)
    provider = get_query_provider(provider_name) if provider_name else None
    thread = (
        _thread(session, conversation_id, script.id) if conversation_id else None
    )

    if thread is None and not stage:
        kind = agent.route_message(session, script, message, provider, model_id)
        if kind == "question":
            return {"kind": "answer", **grounded_answer(session, script, message)}

    requested = (stage or "").strip()
    chosen = (
        requested
        if requested in (agent.PLAN_STAGE, agent.DRAFT_STAGE)
        else (agent.PLAN_STAGE if agent_settings.show_plan else agent.DRAFT_STAGE)
    )

    opened = thread is None
    thread = thread or conversations.start(session, script, message)
    history = [] if opened else conversations.history_for(session, thread)
    conversations.add_turn(session, thread, "user", message)
    try:
        turn = agent.run_turn(
            session,
            script,
            message,
            provider,
            model_id,
            agent_settings,
            history=history,
            stage=chosen,
        )
    except agent.AgentRefused as error:
        raise HTTPException(422, str(error)) from None

    payload = _turn_payload(turn)
    conversations.add_turn(
        session,
        thread,
        "ripple",
        turn.reply,
        payload=payload,
        messages=turn.messages,
        change_set_id=turn.change_set_id,
    )
    return {
        "kind": "turn",
        "conversation": {"id": str(thread.id), "title": thread.title},
        **payload,
    }


@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, session: Session = Depends(get_session)):
    """A stored thread, replayed at no cost."""
    thread = _thread(session, conversation_id)
    return {
        "id": str(thread.id),
        "title": thread.title,
        "script_id": str(thread.script_id),
        "turns": conversations.replay(session, thread),
    }


@app.post("/api/conversations/{conversation_id}/confirm")
def confirm_conversation_change(
    conversation_id: str,
    change_set_id: str = Form(...),
    session: Session = Depends(get_session),
):
    """Accept a proposal from the chat, and record what it did.

    Acceptance is a button on this page and nowhere else: the agent has no
    tool that reaches this endpoint.
    """
    thread = _thread(session, conversation_id)
    change_set = session.get(ChangeSet, _uuid(change_set_id))
    if change_set is None or change_set.script_id != thread.script_id:
        raise HTTPException(404, "No such proposal")
    try:
        accepted = changeset.accept(session, change_set.id)
    except InvalidOperation as error:
        raise HTTPException(409, str(error)) from None
    script = session.get(Script, thread.script_id)
    summary = conversations.applied_summary(session, script, change_set)
    conversations.add_turn(
        session,
        thread,
        "applied",
        summary["text"],
        payload=summary,
        change_set_id=str(change_set.id),
    )
    return {"kind": "applied", "accepted": accepted.__dict__, **summary}


@app.post("/api/conversations/{conversation_id}/close")
def close_conversation(conversation_id: str, session: Session = Depends(get_session)):
    """Reject whatever this thread left pending, so nothing waits unattended."""
    thread = _thread(session, conversation_id)
    return {"rejected": conversations.abandon_pending(session, thread)}


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
    labels = graph_labels(session, record.script_id)
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
        "asked_at": when_label(record.asked_at),
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
    try:
        return as_uuid(value)
    except ValueError:
        raise HTTPException(400, "Not a valid identifier") from None


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
