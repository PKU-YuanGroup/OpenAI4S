"""First-run model configuration for the command-line interface.

The Web workbench remains the richest configuration surface.  This module is
the small, testable service behind ``openai4s init`` so a headless install can
select a provider without editing a checkout-local ``.env`` file.  Secrets are
accepted only as values supplied by the caller and are never returned from a
public result.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from openai4s.config import Config, LLMConfig, is_placeholder_api_key


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """Secret-free projection of the active first-run configuration."""

    provider: str
    model: str
    base_url: str
    has_api_key: bool
    complete: bool
    data_dir: str
    platform: str
    native_runtime_supported: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "has_api_key": self.has_api_key,
            "complete": self.complete,
            "data_dir": self.data_dir,
            "platform": self.platform,
            "native_runtime_supported": self.native_runtime_supported,
        }


class OnboardingService:
    """Validate and persist the model settings used by a first-run wizard."""

    def __init__(
        self,
        cfg: Config,
        store: Any,
        providers: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.providers = providers

    def defaults(self, provider: str | None = None) -> dict[str, str]:
        selected = self._provider(
            provider or self._stored("llm_provider") or self.cfg.llm.provider
        )
        spec = self.providers[selected]
        current_provider = self._stored("llm_provider") or self.cfg.llm.provider
        use_current = selected == current_provider
        return {
            "provider": selected,
            "model": (
                self._stored("llm_model") or self.cfg.llm.model
                if use_current
                else str(spec.get("model") or "")
            ),
            "base_url": (
                self._stored("llm_base_url") or self.cfg.llm.base_url
                if use_current
                else str(spec.get("base_url") or "")
            ),
        }

    def configure(
        self,
        *,
        provider: str,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> OnboardingResult:
        selected = self._provider(provider)
        previous_provider = self._provider(
            self._stored("llm_provider") or self.cfg.llm.provider
        )
        spec = self.providers[selected]
        # Fall back to the currently persisted configuration, not the raw provider
        # spec: a non-interactive call that only touches the key (model/base_url
        # left None) must not silently reset an operator's stored model/base_url.
        # defaults() already returns the spec values when the provider changed.
        current = self.defaults(selected)
        chosen_model = str(model or current["model"] or spec.get("model") or "").strip()
        chosen_url = (
            str(base_url or current["base_url"] or spec.get("base_url") or "")
            .strip()
            .rstrip("/")
        )
        if not chosen_model:
            raise ValueError("model must not be empty")
        parsed = urlsplit(chosen_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base URL must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("base URL must not contain credentials")
        key: str | None = None
        if api_key is not None:
            key = api_key.strip()
            if is_placeholder_api_key(key):
                raise ValueError("API key is empty or a placeholder")

        self.store.set_setting("llm_provider", selected)
        self.store.set_setting("llm_model", chosen_model)
        self.store.set_setting("llm_base_url", chosen_url)
        if clear_api_key:
            self.store.set_secret_setting("llm_api_key", "", scope="llm")
        elif key is not None:
            self.store.set_secret_setting("llm_api_key", key, scope="llm")
        elif selected != previous_provider:
            # Stored keys are provider credentials, not a transferable default.
            # Clearing the runtime override lets LLMConfig resolve the newly
            # selected provider's own environment variables instead of sending
            # the previous provider's secret to a different endpoint.
            self.store.set_secret_setting("llm_api_key", "", scope="llm")
        self.store.set_setting("onboarding_complete", "1")
        return self.status()

    def status(self) -> OnboardingResult:
        defaults = self.defaults()
        # Through the broker: the row holds a reference once migrated, and a
        # reference is truthy whether or not the keychain still has the value.
        # Reading it raw would report "configured" for a key that was revoked
        # by hand or belongs to another machine.
        stored_key = (self._stored_secret("llm_api_key") or "").strip()
        configured_provider = str(self.cfg.llm.provider or "").strip().lower()
        fallback_key = (
            self.cfg.llm.api_key
            if defaults["provider"] == configured_provider
            else LLMConfig(provider=defaults["provider"]).api_key
        )
        has_key = not is_placeholder_api_key(stored_key or fallback_key)
        system = platform.system() or "Unknown"
        return OnboardingResult(
            provider=defaults["provider"],
            model=defaults["model"],
            base_url=defaults["base_url"],
            has_api_key=has_key,
            complete=self._stored("onboarding_complete") == "1",
            data_dir=str(self.cfg.data_dir),
            platform=system,
            native_runtime_supported=system in {"Linux", "Darwin"},
        )

    def _stored(self, key: str) -> str:
        return str(self.store.get_setting(key) or "").strip()

    def _stored_secret(self, key: str) -> str:
        """Resolve a credential setting, whether reference or legacy plaintext."""
        return str(self.store.get_secret_setting(key) or "").strip()

    def _provider(self, value: str) -> str:
        provider = str(value or "").strip().lower()
        if provider not in self.providers:
            known = ", ".join(sorted(self.providers))
            raise ValueError(f"unknown provider {provider!r}; choose one of: {known}")
        return provider


__all__ = ["OnboardingResult", "OnboardingService"]
