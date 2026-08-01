"""Adapters for OpenAI and for any OpenAI-compatible endpoint.

DeepSeek exposes an OpenAI-compatible API, so one implementation serves both:
only the base URL, credential variable, and provider name differ.

Development only. Both are listed for removal in TODO.md before submission,
because PRD section 14 bars non-Google models at runtime.
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


class OpenAICompatibleProvider:
    """Chat completions against an OpenAI-compatible endpoint."""

    def __init__(
        self, name: str, credential_variable: str, base_url: str | None = None
    ) -> None:
        self.name = name
        self.credential_variable = credential_variable
        self.base_url = base_url

    def is_configured(self) -> bool:
        """True when this provider's credential is in the environment."""
        return bool(os.environ.get(self.credential_variable, "").strip())

    def _client(self):
        """Build a client, or say which variable is missing."""
        if not self.is_configured():
            raise ProviderNotConfigured(self.name, self.credential_variable)
        try:
            from openai import OpenAI
        except ImportError as error:
            raise ProviderError(
                "sdk_missing", f"Install openai to use {self.name}."
            ) from error
        kwargs: dict[str, Any] = {"api_key": os.environ[self.credential_variable]}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def list_models(self) -> list[ModelInfo]:
        """Text-generation models, read from the provider's own endpoint."""
        client = self._client()
        try:
            listed = list(client.models.list())
        except Exception as error:
            raise ProviderError(
                "auth_failed", explain_auth_failure(self.name, str(error))
            ) from error

        models = [
            ModelInfo(
                id=entry.id,
                provider=self.name,
                display_name=entry.id,
                tier=infer_tier(entry.id),
            )
            for entry in listed
            if getattr(entry, "id", None) and is_text_model(entry.id)
        ]
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
        """Generate text, requesting JSON object mode when a schema is given."""
        client = self._client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_completion_tokens": max_output_tokens,
        }
        if json_schema:
            # Object mode rather than strict schema mode: DeepSeek supports the
            # former and not the latter, and validation happens caller-side.
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as error:
            raise ProviderError(
                "generate_failed", f"{self.name} call failed: {error}"
            ) from error

        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        text = choice.message.content or ""
        return GenerationResult(
            text=strip_code_fence(text) if json_schema else text,
            model_id=model_id,
            provider=self.name,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )


def openai_provider() -> OpenAICompatibleProvider:
    """OpenAI itself."""
    return OpenAICompatibleProvider("openai", "OPENAI_API_KEY")


def deepseek_provider() -> OpenAICompatibleProvider:
    """DeepSeek, which serves an OpenAI-compatible API at its own base URL."""
    return OpenAICompatibleProvider(
        "deepseek",
        "DEEPSEEK_API_KEY",
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )
