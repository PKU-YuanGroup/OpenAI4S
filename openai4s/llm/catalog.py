"""Extensible model-profile presets independent of transport implementations."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .capabilities import CapabilityError


@dataclass(frozen=True, slots=True)
class ModelPreset:
    provider: str
    model: str
    label: str
    profile_name: str
    inherit_live_config: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.model)


_BUILTIN_PRESETS = (
    ModelPreset(
        "ark",
        "doubao-seed-2.0-pro",
        "Doubao Seed 2.0 Pro",
        "Ark · Doubao Seed 2.0 Pro",
        True,
    ),
    ModelPreset(
        "ark",
        "doubao-seed-2.0-code",
        "Doubao Seed 2.0 Code",
        "Ark · Doubao Seed 2.0 Code",
        True,
    ),
    ModelPreset(
        "ark",
        "doubao-seed-2.0-lite",
        "Doubao Seed 2.0 Lite",
        "Ark · Doubao Seed 2.0 Lite",
        True,
    ),
    ModelPreset(
        "ark",
        "doubao-seed-2.0-mini",
        "Doubao Seed 2.0 Mini",
        "Ark · Doubao Seed 2.0 Mini",
        True,
    ),
    ModelPreset("ark", "glm-5.2", "GLM 5.2", "Ark · GLM 5.2", True),
    ModelPreset(
        "ark", "kimi-k2.7-code", "Kimi K2.7 Code", "Ark · Kimi K2.7 Code", True
    ),
    ModelPreset(
        "ark", "deepseek-v4-pro", "DeepSeek V4 Pro", "Ark · DeepSeek V4 Pro", True
    ),
    ModelPreset(
        "ark", "deepseek-v4-flash", "DeepSeek V4 Flash", "Ark · DeepSeek V4 Flash", True
    ),
    ModelPreset("ark", "minimax-m3", "MiniMax M3", "Ark · MiniMax M3", True),
    ModelPreset("ark", "minimax-m2.7", "MiniMax M2.7", "Ark · MiniMax M2.7", True),
    ModelPreset("ark", "kimi-k2.6", "Kimi K2.6", "Ark · Kimi K2.6", True),
    ModelPreset("chatgpt", "", "OpenAI GPT (official)", "OpenAI GPT (official)"),
    ModelPreset(
        "claude", "", "Anthropic Claude (official)", "Anthropic Claude (official)"
    ),
    ModelPreset("gemini", "", "Google Gemini (official)", "Google Gemini (official)"),
)

_LOCK = threading.RLock()
_PRESETS = {preset.key: preset for preset in _BUILTIN_PRESETS}
_BUILTIN_KEYS = frozenset(_PRESETS)

# Historical public projection used by the Web UI and external callers.
ARK_PLAN_MODELS: tuple[tuple[str, str], ...] = tuple(
    (preset.model, preset.label)
    for preset in _BUILTIN_PRESETS
    if preset.provider == "ark"
)


def model_presets(provider: str | None = None) -> tuple[ModelPreset, ...]:
    """Return an immutable ordered snapshot of registered presets."""
    selected = str(provider or "").strip().lower()
    with _LOCK:
        values = tuple(_PRESETS.values())
    if not selected:
        return values
    return tuple(preset for preset in values if preset.provider == selected)


def register_model_preset(
    provider: str,
    model: str,
    label: str,
    *,
    profile_name: str | None = None,
    inherit_live_config: bool = False,
    replace: bool = False,
) -> ModelPreset:
    """Register a process-local UI/profile preset for any provider identity."""
    provider_id = str(provider or "").strip().lower()
    model_id = str(model or "").strip()
    display = str(label or "").strip()
    name = str(profile_name or display).strip()
    if not provider_id:
        raise CapabilityError("provider must be a non-empty string")
    if not display or not name:
        raise CapabilityError("label and profile_name must be non-empty strings")
    if not isinstance(inherit_live_config, bool):
        raise CapabilityError("inherit_live_config must be a boolean")
    preset = ModelPreset(provider_id, model_id, display, name, inherit_live_config)
    with _LOCK:
        if preset.key in _BUILTIN_KEYS:
            raise CapabilityError(f"built-in model preset {preset.key!r} is immutable")
        if preset.key in _PRESETS and not replace:
            raise CapabilityError(f"model preset {preset.key!r} is already registered")
        _PRESETS[preset.key] = preset
    return preset


def unregister_model_preset(provider: str, model: str) -> bool:
    key = (str(provider or "").strip().lower(), str(model or "").strip())
    with _LOCK:
        if key in _BUILTIN_KEYS:
            raise CapabilityError(f"built-in model preset {key!r} cannot be removed")
        return _PRESETS.pop(key, None) is not None


__all__ = [
    "ARK_PLAN_MODELS",
    "ModelPreset",
    "model_presets",
    "register_model_preset",
    "unregister_model_preset",
]
