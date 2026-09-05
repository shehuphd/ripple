"""Shared contract for LLM providers.

No model identifier is hardcoded anywhere in this package. Each adapter lists
models from its provider's own endpoint, because a name taken from memory or a
naming pattern goes stale silently and only fails at runtime.

The model picker offers text-generation models and excludes
image, video, audio, speech-to-text, embedding, and realtime models. The
filtering lives here so every provider applies the same rule.

Credentials come from the environment only, never from the database and never
from client state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """A provider call failed. Carries a stable code for the UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderNotConfigured(ProviderError):
    """The provider has no credential in the environment."""

    def __init__(self, provider: str, variable: str) -> None:
        super().__init__(
            "provider_not_configured",
            f"{provider} is not configured. Set {variable} in the environment.",
        )


# Codes meaning the model or provider is unavailable right now, rather than
# the request being wrong. A configured fallback model answers these; a
# malformed or truncated reply is not an availability problem and never
# fails over, since a different model would produce a different answer.
AVAILABILITY_CODES = frozenset(
    {
        "model_not_available",
        "provider_unavailable",
        "rate_limited",
        "timeout",
        "network_error",
    }
)


class Tier(str, Enum):
    """Rough cost tier, used to route work to the cheapest capable model.

    Assigned from the model identifier by each adapter, so it is a hint rather
    than a guarantee. Bulk extraction routes to CHEAP; synthesis reserves
    STRONG for synthesis.
    """

    CHEAP = "cheap"
    MID = "mid"
    STRONG = "strong"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelInfo:
    """One selectable text-generation model."""

    id: str
    provider: str
    display_name: str
    tier: Tier = Tier.UNKNOWN
    context_window: int | None = None


@dataclass(frozen=True)
class GenerationResult:
    """What a completed generation returns.

    Token counts are recorded because the spend cap and the audit trail
    both read them. A provider that does not report them leaves them None
    rather than guessing.
    """

    text: str
    model_id: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Hidden reasoning, billed against the same output budget as the answer
    # on models that think before replying. Reported so a truncation can be
    # explained: the cap can run out before any visible text finishes.
    reasoning_tokens: int | None = None
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """True when the provider stopped for length rather than completion."""
        return (self.finish_reason or "").lower() in {
            "length",
            "max_tokens",
            "max_output_tokens",
        }


# Substrings marking a model as something other than text generation. Matching
# is on the identifier because every provider encodes modality there, and a
# false exclusion is cheaper than offering a picker entry that cannot generate.
NON_TEXT_MARKERS = (
    "embed",
    "embedding",
    "image",
    "imagen",
    "vision-only",
    "video",
    "veo",
    "audio",
    "tts",
    "speech",
    "whisper",
    "transcribe",
    "realtime",
    "moderation",
    "rerank",
    "guard",
    "dall-e",
    "sora",
    "clip",
    "aqa",
)

# The thinking level every call carries unless a caller overrides it.
# Extraction and judgement are transcription-shaped work; verified live
# (2026-09-02, gemini-flash-lite-latest) that Gemini accepts "medium" and
# "low" as thinkingLevel and thought-token counts follow the level.
DEFAULT_REASONING_EFFORT = "medium"

# Identifier substrings that suggest a cost tier.
CHEAP_MARKERS = ("flash-lite", "mini", "nano", "haiku", "lite", "flash", "small")
STRONG_MARKERS = ("opus", "ultra", "pro", "-o1", "o3", "reasoner", "thinking")


def is_text_model(model_id: str) -> bool:
    """True when the identifier does not mark a non-text modality."""
    lowered = model_id.lower()
    return not any(marker in lowered for marker in NON_TEXT_MARKERS)


def infer_tier(model_id: str) -> Tier:
    """Guess a cost tier from the identifier.

    Cheap markers win over strong ones so that a name carrying both, such as a
    "flash" variant of a "pro" family, routes to the cheaper tier. Sending bulk
    work to an unexpectedly expensive model costs money; the reverse costs a
    retry.
    """
    lowered = model_id.lower()
    if any(marker in lowered for marker in CHEAP_MARKERS):
        return Tier.CHEAP
    if any(marker in lowered for marker in STRONG_MARKERS):
        return Tier.STRONG
    return Tier.MID


@dataclass(frozen=True)
class ToolCall:
    """One action a model asked for, before anything has run it."""

    name: str
    arguments: dict[str, Any]
    call_id: str | None = None
    # Gemini signs the hidden reasoning behind a function call and requires
    # the signature back when the call is replayed in the turn history; a
    # history without it is rejected. Base64 text, so it survives JSON.
    thought_signature: str | None = None


@dataclass
class AgentReply:
    """One turn from a model that may call tools instead of answering.

    Either the model asked for tools or it wrote an answer; a turn carrying
    both is possible, and the caller runs the tools before reading the text.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_id: str = ""
    provider: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return (self.finish_reason or "").lower() in {
            "max_tokens",
            "length",
            "max_output_tokens",
        }


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every provider adapter satisfies."""

    name: str
    credential_variable: str

    def is_configured(self) -> bool:
        """True when a credential is present. Never returns the credential."""

    def list_models(self, *, api_key: str | None = None) -> list[ModelInfo]:
        """Text-generation models from the provider's own endpoint.

        `api_key` checks a candidate credential without storing it anywhere,
        including the process environment.
        """

    def converse(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 2048,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
    ) -> AgentReply:
        """Continue a conversation, with tools the model may call.

        `messages` is the turn history in provider-neutral form: each entry
        carries a role of "user", "model", or "tool", with "text" for the
        first two and "name" plus "response" for a tool result. The provider
        maps that onto its own transport.

        Optional: only the providers behind Ask Ripple implement it, and the
        agent refuses to start on a provider that does not.
        """

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
    ) -> GenerationResult:
        """Generate text, optionally constrained to a JSON schema.

        `reasoning_effort` caps how much a model thinks before answering.
        Hidden reasoning bills at the output rate, and on the default
        (provider-chosen) budget it was the second-largest cost of a graph
        build, so every call states a level; None restores the provider's
        default for a caller that wants the model to think at length.
        """
