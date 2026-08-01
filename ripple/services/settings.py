"""Settings: choose a provider and model, enter and validate credentials.

The screen this backs shows one row per provider with a key field, a Validate
button, and a Save button. What crosses the boundary in each direction is
deliberately asymmetric: a key goes in, and only a status comes back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ripple.config.secrets import SecretsError, SecretStore, timestamp
from ripple.db.repository import get_active_model, set_active_model
from ripple.llm import PROVIDERS, ProviderError, get_provider
from ripple.llm.base import ModelInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderStatus:
    """One row of the settings screen. Holds no credential material."""

    provider: str
    credential_variable: str
    configured: bool
    source: str
    is_submission_provider: bool


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of pressing Validate."""

    provider: str
    valid: bool
    model_count: int = 0
    checked_at: str = field(default_factory=timestamp)
    error_code: str | None = None
    error_message: str | None = None


class SettingsService:
    """Reads and writes provider configuration."""

    def __init__(self, store: SecretStore | None = None) -> None:
        self.store = store or SecretStore()

    def provider_statuses(self) -> list[ProviderStatus]:
        """Every provider and whether it holds a credential."""
        from ripple.llm import SUBMISSION_PROVIDER

        return [
            ProviderStatus(
                provider=name,
                credential_variable=provider.credential_variable,
                configured=self.store.status(
                    name, provider.credential_variable
                ).configured,
                source=self.store.status(name, provider.credential_variable).source,
                is_submission_provider=name == SUBMISSION_PROVIDER,
            )
            for name, provider in sorted(PROVIDERS.items())
        ]

    def validate(
        self, provider_name: str, api_key: str | None = None
    ) -> ValidationResult:
        """Check a credential by listing models, without storing it.

        A key passed here is used for the call and discarded, so a user can
        test a key before committing to saving it. Nothing about the key value
        appears in the result or the logs.
        """
        provider = get_provider(provider_name)
        restore: tuple[str, str | None] | None = None

        if api_key is not None:
            import os

            variable = provider.credential_variable
            restore = (variable, os.environ.get(variable))
            os.environ[variable] = api_key.strip()

        try:
            models = provider.list_models()
            return ValidationResult(
                provider=provider_name, valid=True, model_count=len(models)
            )
        except ProviderError as error:
            logger.info("validation failed for %s: %s", provider_name, error.code)
            return ValidationResult(
                provider=provider_name,
                valid=False,
                error_code=error.code,
                error_message=error.message,
            )
        finally:
            if restore is not None:
                import os

                variable, previous = restore
                if previous is None:
                    os.environ.pop(variable, None)
                else:
                    os.environ[variable] = previous

    def save_credential(self, provider_name: str, api_key: str) -> ValidationResult:
        """Validate a credential, then store it only if it works.

        Storing an invalid key produces a settings screen that claims to be
        configured and fails on first use, so validation gates the write.
        """
        provider = get_provider(provider_name)
        result = self.validate(provider_name, api_key)
        if not result.valid:
            return result

        try:
            self.store.save(provider.credential_variable, api_key)
        except SecretsError as error:
            return ValidationResult(
                provider=provider_name,
                valid=False,
                error_code=error.code,
                error_message=error.message,
            )
        return result

    def forget_credential(self, provider_name: str) -> bool:
        """Remove a stored credential."""
        provider = get_provider(provider_name)
        return self.store.forget(provider.credential_variable)

    def available_models(self, provider_name: str) -> list[ModelInfo]:
        """Selectable models for a configured provider.

        Already filtered to text generation by the adapter, so an embedding or
        image model cannot reach the picker.
        """
        return get_provider(provider_name).list_models()

    def select_model(self, session: Session, provider_name: str, model_id: str) -> None:
        """Persist the chosen provider and model identifiers.

        Identifiers only. The credential stays outside the database.
        """
        provider = get_provider(provider_name)
        available = {model.id for model in provider.list_models()}
        if model_id not in available:
            raise ProviderError(
                "unknown_model",
                f"{model_id!r} is not offered by {provider_name}.",
            )
        set_active_model(session, provider_name, model_id)

    def selected_model(self, session: Session) -> tuple[str | None, str | None]:
        """The chosen provider and model, or (None, None) before a choice.

        PRD section 11 defaults to no selection rather than guessing one.
        """
        return get_active_model(session)
