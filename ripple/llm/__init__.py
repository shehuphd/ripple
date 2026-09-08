"""Provider registry.

Gemini is the only provider Ripple builds and tests against, reached over two
transports. `PROVIDERS` holds a KeycallProvider (`keycall_provider.py`) for
extraction, judgement, continuity, and synthesis, where KeyCall
(https://github.com/shehuphd/keycall) talks to the provider's API.
`QUERY_PROVIDERS` holds a GoogleGenaiProvider (`genai_provider.py`) for the Ask
path, reached through `get_query_provider`. Same credential, same model ids,
same GenerationResult; only the transport differs.
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
from ripple.llm.genai_provider import GoogleGenaiProvider
from ripple.llm.keycall_provider import KeycallProvider

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, LLMProvider] = {
    "google": KeycallProvider("google", "GOOGLE_API_KEY"),
}

# The grounded query (Ask the graph) runs through Google's own google-genai
# SDK instead of KeyCall's HTTP path, so a Google SDK generation is imported
# and called at runtime. Extraction and judgement stay on PROVIDERS above. Same
# credential, same model ids, same GenerationResult; only the transport differs.
QUERY_PROVIDERS: dict[str, LLMProvider] = {
    "google": GoogleGenaiProvider("google", "GOOGLE_API_KEY"),
}

__all__ = [
    "PROVIDERS",
    "QUERY_PROVIDERS",
    "GenerationResult",
    "LLMProvider",
    "ModelInfo",
    "ProviderError",
    "ProviderNotConfigured",
    "Tier",
    "configured_providers",
    "get_provider",
    "get_query_provider",
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


def get_query_provider(name: str) -> LLMProvider:
    """The provider the Ask path uses: the google-genai SDK for Google.

    Falls back to the standard registry for any other provider name, so the
    query path never has fewer providers than the rest of the app.
    """
    return QUERY_PROVIDERS.get(name) or get_provider(name)


def configured_providers() -> list[str]:
    """Names of providers holding a credential.

    Reports which providers are usable without revealing any credential value,
    masked or otherwise.
    """
    return sorted(
        name for name, provider in PROVIDERS.items() if provider.is_configured()
    )
