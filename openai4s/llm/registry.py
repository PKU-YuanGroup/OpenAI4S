"""Validated process-local provider registry over the built-in wire adapters."""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import urlsplit

from .capabilities import (
    SUPPORTED_WIRES,
    CapabilityError,
    ProviderCapabilities,
    bind_provider_registry,
    clear_capability_cache,
    clear_capability_overrides,
    get_provider_capabilities,
    legacy_provider_specs,
)
from .models import LLMError

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_LOCK = threading.RLock()

# Preserve the original mutable dictionary as a compatibility facade. New code
# should use register_provider so validation and cache invalidation are atomic.
PROVIDERS: dict[str, dict[str, Any]] = legacy_provider_specs()
_BUILTIN_PROVIDER_NAMES = frozenset(PROVIDERS)
bind_provider_registry(PROVIDERS)


def provider_spec(name: str) -> dict[str, Any]:
    spec = PROVIDERS.get(str(name or "").strip().lower())
    if spec is None:
        raise LLMError(
            f"unknown provider {name!r}; known: {', '.join(sorted(PROVIDERS))}"
        )
    return spec


def provider_specs() -> dict[str, dict[str, Any]]:
    """Return a detached snapshot suitable for catalog and UI projections."""
    with _LOCK:
        return {name: dict(spec) for name, spec in PROVIDERS.items()}


def register_provider(
    provider: str,
    *,
    wire: str,
    base_url: str,
    model: str,
    vision: bool = False,
    replace: bool = False,
    **capabilities: Any,
) -> ProviderCapabilities:
    """Register a provider that reuses one of the shipped transport wires.

    Registration is process-local. It adds routing/configuration metadata; it
    cannot load arbitrary transport code or create a new wire implementation.
    """
    name = str(provider or "").strip().lower()
    if not _PROVIDER_ID.fullmatch(name):
        raise CapabilityError(
            "provider must start with a letter and contain only lowercase "
            "letters, digits, underscores, or hyphens"
        )
    normalized_wire = str(wire or "").strip().lower()
    if normalized_wire not in SUPPORTED_WIRES:
        raise CapabilityError(
            f"unsupported wire {wire!r}; choose one of: "
            f"{', '.join(sorted(SUPPORTED_WIRES))}"
        )
    endpoint = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CapabilityError("base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise CapabilityError("base_url must not contain credentials")
    default_model = str(model or "").strip()
    if not default_model:
        raise CapabilityError("model must be a non-empty string")
    if not isinstance(vision, bool):
        raise CapabilityError("vision must be a boolean")

    spec = {
        "wire": normalized_wire,
        "base_url": endpoint,
        "model": default_model,
        "vision": vision,
        **capabilities,
    }
    with _LOCK:
        if name in _BUILTIN_PROVIDER_NAMES:
            raise CapabilityError(
                f"built-in provider {name!r} cannot be replaced through registration"
            )
        if name in PROVIDERS and not replace:
            raise CapabilityError(f"provider {name!r} is already registered")
        previous = PROVIDERS.get(name)
        PROVIDERS[name] = spec
        clear_capability_cache()
        try:
            return get_provider_capabilities(name)
        except Exception:
            if previous is None:
                PROVIDERS.pop(name, None)
            else:
                PROVIDERS[name] = previous
            clear_capability_cache()
            raise


def unregister_provider(provider: str) -> bool:
    """Remove a custom provider; built-in provider identities are immutable."""
    name = str(provider or "").strip().lower()
    with _LOCK:
        if name in _BUILTIN_PROVIDER_NAMES:
            raise CapabilityError(f"built-in provider {name!r} cannot be removed")
        removed = PROVIDERS.pop(name, None) is not None
        if removed:
            clear_capability_overrides(name)
        return removed


__all__ = [
    "PROVIDERS",
    "provider_spec",
    "provider_specs",
    "register_provider",
    "unregister_provider",
]
