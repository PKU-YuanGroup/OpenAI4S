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


# The Ark entries are the text models of Volcengine's Agent Plan (the default
# `plan/v3` endpoint), in the order of its "supported models" table as of
# 2026-11-11. The plan gateway answers any other id -- including the
# doubao-seed-2.0-pro/-code/-lite and minimax ids this list carried through
# 1.0.0 -- with HTTP 404 `UnsupportedModel`, so a stale entry here is a preset
# that cannot run. kimi-k3 is left out: the plan lists it as being retired.
_ARK_PLAN_PRESETS = (
    ("doubao-seed-2.1-pro", "Doubao Seed 2.1 Pro"),
    ("doubao-seed-evolving", "Doubao Seed Evolving"),
    ("doubao-seed-2.1-lite", "Doubao Seed 2.1 Lite"),
    ("doubao-seed-2.0-mini", "Doubao Seed 2.0 Mini"),
    ("glm-5.3", "GLM 5.3"),
    ("glm-5.3-flash", "GLM 5.3 Flash"),
    ("kimi-k3.1-pro", "Kimi K3.1 Pro"),
    ("kimi-k2.8-preview", "Kimi K2.8 Preview"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
    ("deepseek-v4.1-flash-economy", "DeepSeek V4.1 Flash Economy"),
)

_BUILTIN_PRESETS = (
    *(
        ModelPreset("ark", model, label, f"Ark · {label}", True)
        for model, label in _ARK_PLAN_PRESETS
    ),
    ModelPreset("chatgpt", "", "OpenAI GPT (official)", "OpenAI GPT (official)"),
    ModelPreset(
        "claude", "", "Anthropic Claude (official)", "Anthropic Claude (official)"
    ),
    ModelPreset("gemini", "", "Google Gemini (official)", "Google Gemini (official)"),
    ModelPreset("ollama", "", "Ollama Cloud", "Ollama Cloud · Gemma 4"),
)

# `(profile_name, provider, model)` of every preset a pre-0.1.0 build seeded
# into the saved profile list. `ModelProfileService.profiles_payload` removes
# those generated rows once; it must recognise the ids that were seeded then,
# not only the ones this catalog offers now. Frozen: never edit it to follow
# the catalog.
SEEDED_PRESET_SIGNATURES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("Ark · Doubao Seed 2.0 Pro", "ark", "doubao-seed-2.0-pro"),
        ("Ark · Doubao Seed 2.0 Code", "ark", "doubao-seed-2.0-code"),
        ("Ark · Doubao Seed 2.0 Lite", "ark", "doubao-seed-2.0-lite"),
        ("Ark · Doubao Seed 2.0 Mini", "ark", "doubao-seed-2.0-mini"),
        ("Ark · GLM 5.2", "ark", "glm-5.2"),
        ("Ark · Kimi K2.7 Code", "ark", "kimi-k2.7-code"),
        ("Ark · DeepSeek V4 Pro", "ark", "deepseek-v4-pro"),
        ("Ark · DeepSeek V4 Flash", "ark", "deepseek-v4-flash"),
        ("Ark · MiniMax M3", "ark", "minimax-m3"),
        ("Ark · MiniMax M2.7", "ark", "minimax-m2.7"),
        ("Ark · Kimi K2.6", "ark", "kimi-k2.6"),
        ("OpenAI GPT (official)", "chatgpt", ""),
        ("Anthropic Claude (official)", "claude", ""),
        ("Google Gemini (official)", "gemini", ""),
    }
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
    "SEEDED_PRESET_SIGNATURES",
    "ModelPreset",
    "model_presets",
    "register_model_preset",
    "unregister_model_preset",
]
