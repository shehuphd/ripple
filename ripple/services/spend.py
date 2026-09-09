"""The spend ledger and the budget gate.

The ledger is an aggregation over `model_calls`, so it
costs nothing to keep and cannot drift from the audit trail. The budget is
one user-set number; when the recorded spend reaches it, every call site
refuses before contacting the provider and writes the refusal to the same
audit table it would have written the call to.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ripple.db.models import (
    BudgetSetting,
    ExtractionRun,
    ModelCall,
    QueryLog,
    Scene,
    Script,
)

BUDGET_KEY = "default"

# Extraction calls further apart than this belong to separate builds. A scene
# takes seconds to read, so a long pause means the user came back later.
BUILD_GAP = timedelta(minutes=20)


class BudgetExceeded(Exception):
    """The recorded spend has reached the user's cap. No call was made.

    `refusal` is the audit row check_budget wrote for the refused call, when
    it wrote one. A caller whose transaction rolls back with this exception
    re-persists that row in a fresh transaction, so the refusal reaches the
    ledger either way.
    """

    code = "budget_exceeded"

    def __init__(
        self, spent: int, cap: int, refusal: ModelCall | None = None
    ) -> None:
        self.spent = spent
        self.cap = cap
        self.refusal = refusal
        self.message = (
            f"The token budget is spent: {spent:,} of {cap:,} recorded tokens. "
            "Raise or clear the budget in Settings to keep calling the model."
        )
        super().__init__(self.message)


@dataclass
class SpendSummary:
    """What the model calls so far have cost, in tokens."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def get_budget(session: Session) -> int | None:
    """The cap in total tokens, or None when no cap is set."""
    row = session.get(BudgetSetting, BUDGET_KEY)
    return row.max_total_tokens if row else None


def set_budget(session: Session, max_total_tokens: int | None) -> None:
    """Set or clear the cap. None clears it."""
    row = session.get(BudgetSetting, BUDGET_KEY)
    if max_total_tokens is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(
            BudgetSetting(key=BUDGET_KEY, max_total_tokens=max_total_tokens)
        )
    else:
        row.max_total_tokens = max_total_tokens
    session.flush()


def summary(session: Session) -> SpendSummary:
    """The ledger: recorded calls and token totals, split by purpose."""
    calls, input_tokens, output_tokens = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(ModelCall.input_tokens), 0),
            func.coalesce(func.sum(ModelCall.output_tokens), 0),
        ).select_from(ModelCall)
    ).one()
    by_purpose = {
        purpose: total
        for purpose, total in session.execute(
            select(
                ModelCall.purpose,
                func.coalesce(func.sum(ModelCall.input_tokens), 0)
                + func.coalesce(func.sum(ModelCall.output_tokens), 0),
            ).group_by(ModelCall.purpose)
        )
    }
    return SpendSummary(
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        by_purpose=by_purpose,
    )


def actions(session: Session) -> list[dict]:
    """The ledger grouped by user action, newest first.

    A graph build is one row covering its per-scene calls, a ripple preview
    one row covering its judge, extraction, continuity, and synthesis calls,
    and an ask one row per question. A build's extraction calls are clustered
    by script and by time rather than by run id, so a build whose run row has
    since gone still reads as one action; a preview's extraction call carries
    the change-set id its judge does, so it groups with the preview rather than
    reading as a build of its own. The per-call record stays in `model_calls`
    and on the Traces page.
    """
    from ripple.services import pricing

    calls = list(
        session.scalars(select(ModelCall).order_by(ModelCall.created_at))
    )
    titles = {row.id: row.title for row in session.scalars(select(Script))}
    runs = list(session.scalars(select(ExtractionRun)))
    questions = list(session.scalars(select(QueryLog).order_by(QueryLog.asked_at)))
    scene_numbers = {
        row.id: row.label
        for row in session.scalars(select(Scene))
    }

    def question_for(call: ModelCall) -> QueryLog | None:
        """The logged question a query call answered: the first entry on the
        same script written within two minutes of the call."""
        for entry in questions:
            if entry.script_id != call.script_id:
                continue
            if entry.asked_at < call.created_at:
                continue
            if (entry.asked_at - call.created_at).total_seconds() <= 120:
                return entry
        return None

    # A build is a burst of extraction calls with no change set, and a preview
    # whose calls predate change-set linking is a burst of judge, continuity,
    # and synthesis calls that answer one edit. Both are keyed per script per
    # burst, closed when the next call of that kind on that script comes more
    # than BUILD_GAP later. Time is what holds a burst together, so a build
    # whose run row has gone, and a preview whose calls were recorded before
    # change sets were linked, still read as one action. A preview's own
    # extraction call carries a change-set id, so it is left to the change-set
    # grouping below rather than counted as a build.
    keys: dict[uuid.UUID, tuple] = {}
    open_bursts: dict[tuple, tuple] = {}
    last_seen: dict[tuple, datetime] = {}
    for call in calls:
        if call.purpose == "extract" and call.change_set_id is None:
            kind = "build"
        elif call.purpose in ("agent", "draft"):
            # One Ask Ripple turn is one action: the orchestrator's calls and
            # the drafter's calls answer a single request.
            kind = "agent"
        elif call.change_set_id is None and call.purpose != "query":
            kind = "preview"
        else:
            continue
        bucket = (kind, call.script_id)
        last = last_seen.get(bucket)
        if last is None or (call.created_at - last) > BUILD_GAP:
            open_bursts[bucket] = (kind, call.script_id, call.created_at)
        last_seen[bucket] = call.created_at
        keys[call.id] = open_bursts[bucket]

    groups: dict[tuple, dict] = {}
    for call in calls:
        if call.id in keys:
            key = keys[call.id]
            label = {
                "build": "Graph build",
                "agent": "Ask Ripple",
            }.get(key[0], "Ripple preview")
        elif call.change_set_id is not None:
            key = ("preview", call.change_set_id)
            label = "Ripple preview"
        else:
            key = ("ask", call.id)
            label = "Ask the graph"
        group = groups.setdefault(
            key,
            {
                "action": label,
                "detail": "",
                "script": titles.get(call.script_id, ""),
                "models": set(),
                "calls": [],
                "scenes": set(),
                "first": call.created_at,
                "when": call.created_at,
            },
        )
        group["calls"].append(call)
        group["models"].add(call.model_id)
        if call.scene_id is not None:
            group["scenes"].add(scene_numbers.get(call.scene_id))
        if call.created_at and call.created_at > group["when"]:
            group["when"] = call.created_at
        if key[0] == "ask" and not group["detail"]:
            entry = question_for(call)
            if entry is not None:
                group["detail"] = entry.question

    # A forced run reads every scene again, which is what the user pressed
    # Rebuild for, so its burst is named for what they did.
    for key, group in groups.items():
        if key[0] != "build":
            continue
        for run in runs:
            if run.script_id != key[1] or not run.forced or not run.started_at:
                continue
            if group["first"] <= run.started_at <= group["when"]:
                group["action"] = "Graph rebuild"

    rows = []
    for group in groups.values():
        scenes = sorted(number for number in group["scenes"] if number)
        if not group["detail"] and scenes:
            group["detail"] = (
                "Scene " + ", ".join(scenes)
                if len(scenes) <= 4
                else f"{len(group['calls'])} scenes read"
            )
        tokens = sum(
            (call.input_tokens or 0)
            + (call.output_tokens or 0)
            + (call.reasoning_tokens or 0)
            for call in group["calls"]
        )
        cost = pricing.calls_cost_usd(group["calls"])
        models = sorted(model for model in group["models"] if model)
        rows.append(
            {
                "when": group["when"].isoformat() if group["when"] else None,
                "action": group["action"],
                "detail": group["detail"],
                "script": group["script"],
                "model": models[0] if len(models) == 1 else ", ".join(models),
                "call_count": len(group["calls"]),
                "tokens": tokens,
                "cost": pricing.display(cost),
                # The number behind the display string, so the table can sort
                # on cost without parsing "$0.0050" back out of the cell.
                "cost_usd": cost,
            }
        )
    rows.sort(key=lambda row: row["when"] or "", reverse=True)
    return rows


def elapsed_ms(started: float) -> int:
    """Milliseconds since a `time.perf_counter()` reading."""
    return int((time.perf_counter() - started) * 1000)


def note_result(call: ModelCall, result: Any, started: float) -> None:
    """Copy a completed generation onto its audit row.

    Fields only: the caller decides when the row reaches the session, since
    a preview persists its audit rows in one commit before any rollback.
    """
    call.response_text = result.text
    call.input_tokens = result.input_tokens
    call.output_tokens = result.output_tokens
    call.reasoning_tokens = getattr(result, "reasoning_tokens", None)
    call.duration_ms = elapsed_ms(started)


def record_success(
    session: Session, call: ModelCall, result: Any, started: float
) -> None:
    """A completed generation, written to the ledger."""
    note_result(call, result, started)
    session.add(call)
    session.flush()


def record_failure(
    session: Session, call: ModelCall, error: Any, started: float
) -> None:
    """A provider failure, written to the ledger with the provider's message."""
    call.outcome = "provider_error"
    call.error_message = error.message
    call.duration_ms = elapsed_ms(started)
    session.add(call)
    session.flush()


def check_budget(session: Session, refusal: ModelCall | None = None) -> None:
    """Raise BudgetExceeded when the cap is reached, before provider contact.

    `refusal` is the ModelCall the caller was about to make; when given it is
    written with outcome `budget_refused`, so the audit shows the call that
    was not made and why.
    """
    cap = get_budget(session)
    if cap is None:
        return
    spent = summary(session).total_tokens
    if spent < cap:
        return
    error = BudgetExceeded(spent, cap, refusal=refusal)
    if refusal is not None:
        refusal.outcome = "budget_refused"
        refusal.error_message = error.message
        session.add(refusal)
        session.flush()
    raise error
