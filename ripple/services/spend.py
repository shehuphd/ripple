"""The spend ledger and the budget gate.

The ledger is an aggregation over `model_calls`, so it
costs nothing to keep and cannot drift from the audit trail. The budget is
one user-set number; when the recorded spend reaches it, every call site
refuses before contacting the provider and writes the refusal to the same
audit table it would have written the call to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ripple.db.models import BudgetSetting, ModelCall

BUDGET_KEY = "default"


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
