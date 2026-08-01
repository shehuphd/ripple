"""Anthropic adapter.

Development only. Listed for removal in TODO.md before submission, because PRD
section 14 bars non-Google models at runtime.
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
    infer_tier,
    is_text_model,
    strip_code_fence,
)

logger = logging.getLogger(__name__)

CREDENTIAL_VARIABLE = "ANTHROPIC_API_KEY"


class AnthropicProvider:
    """Anthropic Messages API."""

    name = "anthropic"
    credential_variable = CREDENTIAL_VARIABLE

    def is_configured(self) -> bool:
        """True when an Anthropic credential is in the environment."""
        return bool(os.environ.get(CREDENTIAL_VARIABLE, "").strip())

    def _client(self):
        """Build a client, or say which variable is missing."""
        if not self.is_configured():
            raise ProviderNotConfigured(self.name, CREDENTIAL_VARIABLE)
        try:
            from anthropic import Anthropic
        except ImportError as error:
            raise ProviderError(
                "sdk_missing", "Install anthropic to use this provider."
            ) from error
        return Anthropic(api_key=os.environ[CREDENTIAL_VARIABLE])

    def list_models(self) -> list[ModelInfo]:
        """Text-generation models, read from the provider's own endpoint."""
        client = self._client()
        try:
            listed = list(client.models.list())
        except Exception as error:
            raise ProviderError(
                "list_failed", f"Anthropic model list failed: {error}"
            ) from error

        models = [
            ModelInfo(
                id=entry.id,
                provider=self.name,
                display_name=getattr(entry, "display_name", None) or entry.id,
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
        """Generate text.

        The Messages API has no JSON mode, so a schema becomes an instruction
        and the caller validates. `temperature` is never sent: recent models
        reject it outright.
        """
        client = self._client()
        instruction = system
        if json_schema:
            requirement = (
                "Reply with a single JSON object matching this schema and "
                f"nothing else, with no code fence: {json_schema}"
            )
            instruction = f"{system}\n\n{requirement}" if system else requirement

        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if instruction:
            kwargs["system"] = instruction

        try:
            response = client.messages.create(**kwargs)
        except Exception as error:
            raise ProviderError(
                "generate_failed", f"Anthropic call failed: {error}"
            ) from error

        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        return GenerationResult(
            text=strip_code_fence(text) if json_schema else text,
            model_id=model_id,
            provider=self.name,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            finish_reason=getattr(response, "stop_reason", None),
        )
