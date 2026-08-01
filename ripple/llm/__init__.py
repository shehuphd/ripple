"""Provider registry.

Gemini is the provider Ripple ships with. OpenAI, Anthropic, and DeepSeek are
present for development only: DeepSeek in particular is cheap enough to iterate
against without spending the hackathon's Google Cloud credit. All three are
listed for removal in TODO.md before submission, because PRD section 14 bars
non-Google models at runtime.
"""

from __future__ import annotations

import logging

from ripple.llm.anthropic_provider import AnthropicProvider
from ripple.llm.base import (
    GenerationResult,
    LLMProvider,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
    Tier,
)
from ripple.llm.gemini import GeminiProvider
from ripple.llm.openai_compatible import deepseek_provider, openai_provider

logger = logging.getLogger(__name__)

SUBMISSION_PROVIDER = "google"

PROVIDERS: dict[str, LLMProvider] = {
    "google": GeminiProvider(),
    # Development only. See TODO.md.
    "openai": openai_provider(),
    "anthropic": AnthropicProvider(),
    "deepseek": deepseek_provider(),
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
