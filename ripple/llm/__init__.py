"""Provider registry.

Gemini is the only provider Ripple builds and tests against, per PRD section
14. It is a KeycallProvider (`ripple/llm/keycall_provider.py`): KeyCall
(https://github.com/shehuphd/keycall) is what talks to the provider's API, so
there is nothing provider-specific left to hand-write here.
"""

from __future__ import annotations

import logging

from ripple.llm.base import (
    GenerationResult,
    LLMProvider,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
    Tier,
)
from ripple.llm.keycall_provider import KeycallProvider

logger = logging.getLogger(__name__)

SUBMISSION_PROVIDER = "google"

PROVIDERS: dict[str, LLMProvider] = {
    "google": KeycallProvider("google", "GOOGLE_API_KEY"),
}

__all__ = [
    "PROVIDERS",
    "SUBMISSION_PROVIDER",
    "GenerationResult",
    "LLMProvider",
    "ModelInfo",
    "ProviderError",
    "ProviderNotConfigured",
    "Tier",
    "configured_providers",
    "get_provider",
]


def get_provider(name: str) -> LLMProvider:
    """Look up a provider by name, naming the valid options when it is unknown."""
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ProviderError(
            "unknown_provider",
            f"Unknown provider {name!r}. Available: {', '.join(sorted(PROVIDERS))}.",
        ) from None


def configured_providers() -> list[str]:
    """Names of providers holding a credential.

    Reports which providers are usable without revealing any credential value,
    masked or otherwise. PRD section 11.
    """
    return sorted(
        name for name, provider in PROVIDERS.items() if provider.is_configured()
    )
