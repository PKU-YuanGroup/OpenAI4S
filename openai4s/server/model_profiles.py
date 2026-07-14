"""Model-provider profile lifecycle kept out of the HTTP gateway facade."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai4s.config import Config, is_placeholder_api_key
from openai4s.llm.catalog import ModelPreset, model_presets

# Model profiles select a transport contract, not an arbitrary vendor name.
# Keep the persisted ids compatible with the existing LLM registry while the
# UI presents these as human-readable protocol choices.
PROFILE_PROTOCOLS = ("chatgpt", "claude", "ark")


class ModelProfileError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_api_key(value: Any) -> str:
    """Trim API keys and collapse obvious template stubs to empty."""
    key = str(value or "").strip()
    return "" if is_placeholder_api_key(key) else key


class ModelProfileService:
    """Own migration, CRUD, activation, and public projection of model profiles."""

    def __init__(
        self,
        store: Any,
        cfg: Config,
        *,
        providers: Callable[[], Mapping[str, Mapping[str, Any]]],
        presets: Callable[[], Sequence[ModelPreset]] = model_presets,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg
        self._providers = providers
        self._presets = presets
        self._id_factory = id_factory or (lambda: "mp-" + uuid.uuid4().hex[:8])

    def effective_model_id(self, provider: Any, model: Any) -> str:
        explicit = str(model or "").strip()
        if explicit:
            return explicit
        provider_id = str(provider or "").strip().lower()
        spec = self._providers().get(provider_id, {})
        return str(spec.get("model") or self.cfg.llm.model or "default")

    @staticmethod
    def public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
        """Return a profile projection that never includes the raw API key."""
        return {
            "id": profile.get("id"),
            "name": profile.get("name") or "",
            "provider": profile.get("provider") or "",
            "base_url": profile.get("base_url") or "",
            "model": profile.get("model") or "",
            "has_api_key": bool(clean_api_key(profile.get("api_key"))),
        }

    def models_payload(self, default_model_id: str) -> dict[str, Any]:
        """Build the header selector from the live model and the saved profiles.

        Built-in provider defaults are deliberately absent. An endpoint the user
        never configured must not be selectable: picking it would only fail at
        send time for want of a key. A profile that leaves `model` blank still
        appears, resolved through its protocol's default.
        """
        live = self.store.get_setting("llm_model") or self.cfg.llm.model or "default"
        seen: set[str] = set()
        models: list[dict[str, str]] = []

        def add(model_id: Any, name: Any, description: Any) -> None:
            normalized = str(model_id or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                models.append(
                    {
                        "id": normalized,
                        "name": str(name or normalized),
                        "description": str(description or ""),
                    }
                )

        add(
            live,
            live,
            f"{self.store.get_setting('llm_provider') or self.cfg.llm.provider} (当前)",
        )
        for profile in self.store.list_model_profiles():
            model_id = self.effective_model_id(
                profile.get("provider"), profile.get("model")
            )
            add(model_id, model_id, profile.get("name") or "profile")
        return {"models": {"default": models}, "default_model_id": default_model_id}

    def profiles_payload(self) -> tuple[dict[str, Any], str | None]:
        """Return saved profiles without materializing built-in endpoints.

        Older releases seeded the model catalog into every user's saved profile
        list. Remove rows matching those generated preset identities once,
        while preserving profiles with customized names, providers, or models.
        """
        if not self.store.get_setting("builtin_profiles_removed"):
            removed_ids: set[str] = set()
            if self.store.get_setting("builtin_profiles_seeded"):
                seeded_signatures = {
                    (preset.profile_name, preset.provider, preset.model)
                    for preset in self._presets()
                }

                def remove_seeded(profiles: list[dict[str, Any]]) -> None:
                    kept = []
                    for profile in profiles:
                        signature = (
                            str(profile.get("name") or ""),
                            str(profile.get("provider") or ""),
                            str(profile.get("model") or ""),
                        )
                        if signature in seeded_signatures:
                            removed_ids.add(str(profile.get("id") or ""))
                        else:
                            kept.append(profile)
                    profiles[:] = kept

                self.store.mutate_model_profiles(remove_seeded)
            active_id = self.store.get_setting("active_model_profile") or ""
            if active_id in removed_ids:
                self.store.set_setting("active_model_profile", "")
            self.store.set_setting("builtin_profiles_removed", "1")

        profiles = self.store.list_model_profiles()
        return (
            {
                "profiles": [self.public_profile(profile) for profile in profiles],
                "active_id": self.store.get_setting("active_model_profile") or "",
                "protocols": list(PROFILE_PROTOCOLS),
            },
            None,
        )

    @staticmethod
    def _protocol(value: Any) -> str:
        protocol = str(value or "").strip().lower()
        if protocol not in PROFILE_PROTOCOLS:
            raise ModelProfileError(
                "protocol must be one of: " + ", ".join(PROFILE_PROTOCOLS)
            )
        return protocol

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise ModelProfileError("name required")
        profile = {
            "id": self._id_factory(),
            "name": name,
            "provider": self._protocol(body.get("provider")),
            "base_url": str(body.get("base_url") or "").strip(),
            "model": str(body.get("model") or "").strip(),
            "api_key": clean_api_key(body.get("api_key")),
        }
        self.store.mutate_model_profiles(lambda profiles: profiles.append(profile))
        return self.public_profile(profile)

    def activate(self, profile_id: str) -> tuple[dict[str, Any], str]:
        profile = next(
            (
                item
                for item in self.store.list_model_profiles()
                if item.get("id") == profile_id
            ),
            None,
        )
        if profile is None:
            raise ModelProfileError("profile not found", 404)
        for field, setting in (
            ("provider", "llm_provider"),
            ("base_url", "llm_base_url"),
            ("model", "llm_model"),
        ):
            self.store.set_setting(setting, str(profile.get(field) or "").strip())
        self.store.set_setting("llm_api_key", clean_api_key(profile.get("api_key")))
        self.store.set_setting("active_model_profile", profile["id"])

        def to_front(profiles: list[dict[str, Any]]) -> None:
            index = next(
                (i for i, item in enumerate(profiles) if item.get("id") == profile_id),
                -1,
            )
            if index > 0:
                profiles.insert(0, profiles.pop(index))

        self.store.mutate_model_profiles(to_front)
        return (
            {"ok": True, "active_id": profile["id"]},
            self.effective_model_id(profile.get("provider"), profile.get("model")),
        )

    def edit(
        self, profile_id: str, body: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        protocol = self._protocol(body["provider"]) if "provider" in body else None

        def mutate(profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
            profile = next(
                (item for item in profiles if item.get("id") == profile_id), None
            )
            if profile is None:
                return None
            for field in ("name", "base_url", "model"):
                if field in body and body[field] is not None:
                    profile[field] = str(body[field]).strip()
            if protocol is not None:
                profile["provider"] = protocol
            if body.get("api_key"):
                profile["api_key"] = clean_api_key(body["api_key"])
            if body.get("clear_api_key"):
                profile["api_key"] = ""
            return dict(profile)

        profile = self.store.mutate_model_profiles(mutate)
        if profile is None:
            raise ModelProfileError("profile not found", 404)
        selected_model: str | None = None
        if self.store.get_setting("active_model_profile") == profile["id"]:
            for field, setting in (
                ("provider", "llm_provider"),
                ("base_url", "llm_base_url"),
                ("model", "llm_model"),
            ):
                self.store.set_setting(setting, str(profile.get(field) or ""))
            self.store.set_setting("llm_api_key", clean_api_key(profile.get("api_key")))
            selected_model = self.effective_model_id(
                profile.get("provider"), profile.get("model")
            )
        return self.public_profile(profile), selected_model

    def delete(self, profile_id: str) -> None:
        self.store.mutate_model_profiles(
            lambda profiles: profiles.__setitem__(
                slice(None),
                [profile for profile in profiles if profile.get("id") != profile_id],
            )
        )
        if self.store.get_setting("active_model_profile") == profile_id:
            self.store.set_setting("active_model_profile", "")


def migrate_provider_alias(
    store: Any,
    providers: Mapping[str, Mapping[str, Any]],
    *,
    old: str,
    new: str,
) -> None:
    """Idempotently rewrite a retired provider identity in settings/profiles."""
    new_spec = providers.get(new)
    if new_spec is None:
        raise ValueError(f"unknown replacement provider {new!r}")
    base_url = str(new_spec.get("base_url") or "")
    if str(store.get_setting("llm_provider") or "").strip() == old:
        store.set_setting("llm_provider", new)
        if not str(store.get_setting("llm_base_url") or "").strip():
            store.set_setting("llm_base_url", base_url)

    def migrate(profiles: list[dict[str, Any]]) -> None:
        for profile in profiles:
            if str(profile.get("provider") or "").strip() == old:
                profile["provider"] = new
                if not str(profile.get("base_url") or "").strip():
                    profile["base_url"] = base_url

    store.mutate_model_profiles(migrate)


__all__ = [
    "ModelProfileError",
    "ModelProfileService",
    "PROFILE_PROTOCOLS",
    "clean_api_key",
    "migrate_provider_alias",
]
