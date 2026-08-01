"""Shared contract for LLM providers.

No model identifier is hardcoded anywhere in this package. Each adapter lists
models from its provider's own endpoint, because a name taken from memory or a
naming pattern goes stale silently and only fails at runtime.

PRD section 11 requires the picker to offer text-generation models and exclude
image, video, audio, speech-to-text, embedding, and realtime models. The
filtering lives here so every provider applies the same rule.

Credentials come from the environment only, never from the database and never
from client state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


class Tier(str, Enum):
    """Rough cost tier, used to route work to the cheapest capable model.

    Assigned from the model identifier by each adapter, so it is a hint rather
    than a guarantee. The PRD routes bulk extraction to CHEAP and reserves
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

    Token counts are recorded because PRD section 10 caps spend and section 12
    traces token counts. A provider that does not report them leaves them None
    rather than guessing.
    """

    text: str
    model_id: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
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


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(?P<body>.*?)\s*```", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Remove a Markdown code fence a model wrapped its JSON in.

    Providers that lack native structured output frequently fence their JSON
    even when told not to. Stripping here keeps that quirk out of every caller.
    """
    match = _JSON_BLOCK.search(text)
    return match.group("body") if match else text.strip()


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every provider adapter satisfies."""

    name: str
    credential_variable: str

    def is_configured(self) -> bool:
        """True when a credential is present. Never returns the credential."""

    def list_models(self) -> list[ModelInfo]:
        """Text-generation models from the provider's own endpoint."""

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate text, optionally constrained to a JSON schema."""
