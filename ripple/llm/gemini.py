"""Google Gemini adapter, via the google-genai SDK.

This is the provider Ripple ships with. PRD section 14 bars non-Google models
at runtime for the submission; the other adapters in this package exist for
development only and are listed for removal in TODO.md.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ripple.llm.base import (
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
    explain_auth_failure,
    infer_tier,
    is_text_model,
    strip_code_fence,
)

logger = logging.getLogger(__name__)

CREDENTIAL_VARIABLE = "GOOGLE_API_KEY"
# Gemini reports what each model supports; anything without this cannot answer
# a prompt, whatever its identifier suggests.
GENERATE_ACTION = "generateContent"


class GeminiProvider:
    """Google Gemini text generation."""

    name = "google"
    credential_variable = CREDENTIAL_VARIABLE

    def is_configured(self) -> bool:
        """True when a Google credential is in the environment."""
        return bool(os.environ.get(CREDENTIAL_VARIABLE, "").strip())

    def _client(self):
        """Build a client, or say which variable is missing."""
        if not self.is_configured():
            raise ProviderNotConfigured(self.name, CREDENTIAL_VARIABLE)
        try:
            from google import genai
        except ImportError as error:
            raise ProviderError(
                "sdk_missing", "Install google-genai to use Gemini."
            ) from error
        return genai.Client(api_key=os.environ[CREDENTIAL_VARIABLE])

    def list_models(self) -> list[ModelInfo]:
        """Text-generation models, read from the provider's own endpoint."""
        client = self._client()
        try:
            listed = list(client.models.list())
        except Exception as error:  # SDK raises provider-specific types
            raise ProviderError(
                "auth_failed", explain_auth_failure("Google", str(error))
            ) from error

        models: list[ModelInfo] = []
        for entry in listed:
            identifier = (getattr(entry, "name", "") or "").removeprefix("models/")
            if not identifier or not is_text_model(identifier):
                continue
            actions = getattr(entry, "supported_actions", None) or []
            if actions and GENERATE_ACTION not in actions:
                continue
            models.append(
                ModelInfo(
                    id=identifier,
                    provider=self.name,
                    display_name=getattr(entry, "display_name", None) or identifier,
                    tier=infer_tier(identifier),
                    context_window=getattr(entry, "input_token_limit", None),
                )
            )
        return sorted(models, key=lambda model: model.id)

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate text, using Gemini's native JSON mode when a schema is given."""
        client = self._client()
        from google.genai import types

        config: dict[str, Any] = {"max_output_tokens": max_output_tokens}
        if system:
            config["system_instruction"] = system
        if json_schema:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = json_schema

        try:
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(**config),
            )
        except Exception as error:
            raise ProviderError(
                "generate_failed", f"Gemini call failed: {error}"
            ) from error

        usage = getattr(response, "usage_metadata", None)
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        text = response.text or ""
        return GenerationResult(
            text=strip_code_fence(text) if json_schema else text,
            model_id=model_id,
            provider=self.name,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            finish_reason=str(finish) if finish is not None else None,
        )
