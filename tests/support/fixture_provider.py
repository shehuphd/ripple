"""A provider that answers from recorded fixtures instead of a network call.

Extraction is the first thing that spends money, so the tests must exercise the
whole pipeline without a credential. This provider is deterministic: the same
prompt always returns the same reply, so a test that passes once passes again.

It lives with the tests rather than in the package: nothing shipped imports it,
and the registry in `ripple.llm` never lists it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ripple.llm.base import (
    AgentReply,
    GenerationResult,
    ModelInfo,
    ProviderError,
    Tier,
)

logger = logging.getLogger(__name__)

FIXTURE_MODELS = (
    ("fixture-cheap", Tier.CHEAP),
    ("fixture-mid", Tier.MID),
    ("fixture-strong", Tier.STRONG),
)


def prompt_key(model_id: str, prompt: str, system: str | None) -> str:
    """A stable key for one request, used as the fixture filename."""
    digest = hashlib.sha256()
    for part in (model_id, system or "", prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


class FixtureProvider:
    """Replays recorded replies, and records new ones when told to.

    In replay mode an unrecorded prompt raises rather than inventing a reply:
    a silent empty answer would look like a model that found nothing, which is
    a legitimate outcome, so the two must not be confusable.
    """

    name = "fixture"
    credential_variable = "FIXTURE_API_KEY"

    def __init__(
        self,
        directory: Path,
        *,
        record: bool = False,
        default_reply: str | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.record = record
        self.default_reply = default_reply
        self.calls: list[dict[str, Any]] = []
        # Agent turns queued by a test, and what the agent asked for.
        self.turns: list[AgentReply] = []
        self.conversations: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        """Always true: the fixture provider needs no credential."""
        return True

    def list_models(self, *, api_key: str | None = None) -> list[ModelInfo]:
        """The three fixture models, one per tier. Any key is accepted."""
        return [
            ModelInfo(
                id=identifier,
                provider=self.name,
                display_name=identifier,
                tier=tier,
                context_window=100_000,
            )
            for identifier, tier in FIXTURE_MODELS
        ]

    def put(
        self, model_id: str, prompt: str, reply: str, system: str | None = None
    ) -> None:
        """Record a reply for a prompt, so a test can arrange its own answer."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{prompt_key(model_id, prompt, system)}.json"
        path.write_text(json.dumps({"reply": reply}), encoding="utf-8")

    def script_turns(self, turns: list[AgentReply]) -> None:
        """Queue the agent turns this provider will return, in order.

        The agent loop is control flow, and control flow is what tests need
        to pin: which tool the model asks for, what it does with the result,
        when it stops. A queue of turns makes that deterministic and free.
        """
        self.turns = list(turns)

    def converse(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 2048,
        reasoning_effort: str | None = None,
    ) -> AgentReply:
        """Return the next queued turn, recording what it was asked."""
        self.conversations.append(
            {
                "model_id": model_id,
                "messages": messages,
                "system": system,
                "tools": [tool["name"] for tool in (tools or [])],
            }
        )
        if not getattr(self, "turns", None):
            return AgentReply(
                text="No turn was queued for this call.",
                model_id=model_id,
                provider=self.name,
                input_tokens=0,
                output_tokens=0,
            )
        turn = self.turns.pop(0)
        turn.model_id = turn.model_id or model_id
        turn.provider = turn.provider or self.name
        return turn

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> GenerationResult:
        """Return the recorded reply for this prompt."""
        self.calls.append(
            {
                "model_id": model_id,
                "prompt": prompt,
                "system": system,
                "max_output_tokens": max_output_tokens,
                "has_schema": json_schema is not None,
            }
        )

        path = self.directory / f"{prompt_key(model_id, prompt, system)}.json"
        if path.exists():
            reply = json.loads(path.read_text(encoding="utf-8"))["reply"]
        elif self.default_reply is not None:
            reply = self.default_reply
        else:
            raise ProviderError(
                "fixture_missing",
                f"No fixture recorded for this prompt under {path.name}. "
                "Record one, or set default_reply.",
            )

        return GenerationResult(
            text=reply,
            model_id=model_id,
            provider=self.name,
            input_tokens=len(prompt) // 4,
            output_tokens=len(reply) // 4,
            finish_reason="stop",
        )
