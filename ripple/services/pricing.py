"""Money for the token counts, from the rates registry.

Every chargeable action reports its cost beside its tokens. The prices
come from the bundled `rates` snapshot (pypi.org/project/rates), looked
up by provider and model id; a model the registry does not price returns
None, and the caller shows the tokens alone rather than a guessed figure.

Hidden reasoning bills at the output rate: providers charge thinking as
output tokens, so the cost of a call is
input * input_rate + (output + reasoning) * output_rate.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Ripple provider names to rates registry provider names. One provider
# ships today; extend this map alongside ripple/llm.
_PROVIDER_NAMES = {"google": "google", "gemini": "google"}


@lru_cache(maxsize=1)
def _registry():
    """The rates snapshot, loaded once per process (~300 ms, bundled data)."""
    import rates.ai

    return rates.ai.load()


@lru_cache(maxsize=64)
def _lookup(name: str, model_id: str) -> tuple[float, float] | None:
    """(input, output) USD per token from a loaded registry.

    Raises when the registry itself cannot load, and lru_cache does not
    cache exceptions, so a transient load failure is retried on the next
    lookup instead of memoised as "no price". A model the loaded registry
    does not know caches None, which is a stable answer.
    """
    for model in _registry().models:
        if model.provider != name or model.id != model_id:
            continue
        units = model.price.units if model.price else {}
        input_mtok = units.get("input_mtok")
        output_mtok = units.get("output_mtok")
        if input_mtok is None or output_mtok is None:
            return None
        return (input_mtok / 1_000_000, output_mtok / 1_000_000)
    return None


def _rate(provider: str, model_id: str) -> tuple[float, float] | None:
    """(input, output) USD per token, or None when no price is known."""
    try:
        return _lookup(_PROVIDER_NAMES.get(provider, provider), model_id)
    except Exception:  # a pricing lookup must never break the billable action
        logger.exception("rates registry failed to load")
        return None


def cost_usd(
    provider: str,
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None = None,
) -> float | None:
    """The USD cost of one call, or None when the model has no known rate."""
    rate = _rate(provider, model_id)
    if rate is None:
        return None
    input_rate, output_rate = rate
    billable_output = (output_tokens or 0) + (reasoning_tokens or 0)
    return (input_tokens or 0) * input_rate + billable_output * output_rate


def calls_cost_usd(calls) -> float | None:
    """Total USD across ModelCall rows, or None when none has a known rate.

    Rows without a rate are skipped rather than zeroed, and a partial total
    is still returned: a known-rate call beside an unpriced one reports the
    known part, which understates rather than invents.
    """
    total = None
    for call in calls:
        cost = cost_usd(
            "google",
            call.model_id,
            call.input_tokens,
            call.output_tokens,
            getattr(call, "reasoning_tokens", None),
        )
        if cost is None:
            continue
        total = (total or 0.0) + cost
    return total


def display(cost: float | None) -> str | None:
    """A dollar figure fit for the UI, or None when there is no rate.

    Two decimal places above a cent, four below, and a floor label for
    dust: "$0.31", "$0.0043", "under $0.0001".
    """
    if cost is None:
        return None
    if cost >= 0.01:
        return f"${cost:.2f}"
    if cost >= 0.0001:
        return f"${cost:.4f}"
    if cost > 0:
        return "under $0.0001"
    return "$0.00"
