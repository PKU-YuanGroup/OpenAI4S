"""Provider/model capability metadata and normalized token accounting.

This module deliberately contains no transport or configuration imports.  It
is safe to use from the CLI, context budgeting, settings UI, and provider
adapters without creating an import cycle.  The built-in catalogue describes
what OpenAI4S' *adapter* currently supports; it does not claim every feature a
remote vendor may expose through a different SDK.
"""

from __future__ import annotations

import ipaddress
import json
import threading
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Mapping
from urllib.parse import urlsplit


class CapabilityError(ValueError):
    """A capability lookup, override, or requested feature is invalid."""


@dataclass(frozen=True, slots=True)
class UsageMapping:
    """Candidate provider paths for each canonical usage counter.

    Paths use dotted object notation.  The first present value wins, allowing a
    provider family to accept both its native response and OpenAI-compatible
    proxy variants without leaking those wire-specific names to callers.
    """

    input_tokens: tuple[str, ...]
    output_tokens: tuple[str, ...]
    cache_read: tuple[str, ...] = ()
    cache_write: tuple[str, ...] = ()
    reasoning_tokens: tuple[str, ...] = ()
    total_tokens: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CostMetadata:
    """Optional public price metadata, expressed per one million tokens.

    Prices change independently of code.  Unknown prices remain ``None`` and
    can be supplied by a deployment-level capability override; they are never
    guessed.  ``source`` and ``as_of`` make user-supplied price tables auditable.
    """

    currency: str = "USD"
    input_per_million: float | None = None
    output_per_million: float | None = None
    cache_read_per_million: float | None = None
    cache_write_per_million: float | None = None
    source: str | None = None
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Stable capability description for one configured provider adapter."""

    provider: str
    wire: str
    default_base_url: str
    default_model: str
    context_window_tokens: int | None
    max_output_tokens: int | None
    tool_calling: bool
    parallel_tool_calls: bool
    strict_tool_schema: bool
    vision: bool
    audio: bool
    reasoning: bool
    streaming: bool
    usage_mapping: UsageMapping
    cost: CostMetadata = CostMetadata()
    custom_endpoint: bool = False
    local_endpoint: bool = False

    @property
    def context_limit(self) -> int | None:
        """Concise compatibility alias used by capability-aware schedulers."""
        return self.context_window_tokens

    @property
    def output_limit(self) -> int | None:
        return self.max_output_tokens

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot, safe for UI/API responses."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Effective capabilities for a concrete provider/model/endpoint tuple."""

    provider: str
    model: str
    wire: str
    endpoint: str
    context_window_tokens: int | None
    max_output_tokens: int | None
    tool_calling: bool
    parallel_tool_calls: bool
    strict_tool_schema: bool
    vision: bool
    audio: bool
    reasoning: bool
    streaming: bool
    usage_mapping: UsageMapping
    cost: CostMetadata = CostMetadata()
    custom_endpoint: bool = False
    local_endpoint: bool = False

    @property
    def context_limit(self) -> int | None:
        return self.context_window_tokens

    @property
    def output_limit(self) -> int | None:
        return self.max_output_tokens

    @property
    def usable_context_tokens(self) -> int | None:
        """Context available after reserving the model's maximum output."""
        if self.context_window_tokens is None:
            return None
        return max(0, self.context_window_tokens - (self.max_output_tokens or 0))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CapabilityCacheInfo:
    hits: int
    misses: int
    provider_entries: int
    model_entries: int
    generation: int


_OPENAI_USAGE = UsageMapping(
    input_tokens=("prompt_tokens", "input_tokens"),
    output_tokens=("completion_tokens", "output_tokens"),
    cache_read=(
        "prompt_tokens_details.cached_tokens",
        "input_tokens_details.cached_tokens",
        "cache_read_input_tokens",
        "cache_read_tokens",
        "cache_read",
    ),
    cache_write=(
        "prompt_tokens_details.cache_write_tokens",
        "input_tokens_details.cache_write_tokens",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_write_tokens",
        "cache_write",
    ),
    reasoning_tokens=(
        "completion_tokens_details.reasoning_tokens",
        "output_tokens_details.reasoning_tokens",
        "reasoning_tokens",
    ),
    total_tokens=("total_tokens",),
)

_ANTHROPIC_USAGE = UsageMapping(
    input_tokens=("input_tokens", "prompt_tokens"),
    output_tokens=("output_tokens", "completion_tokens"),
    cache_read=("cache_read_input_tokens", "cache_read_tokens", "cache_read"),
    cache_write=(
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_write_tokens",
        "cache_write",
    ),
    reasoning_tokens=("reasoning_tokens",),
    total_tokens=("total_tokens",),
)

_GEMINI_USAGE = UsageMapping(
    input_tokens=("promptTokenCount", "input_tokens", "prompt_tokens"),
    output_tokens=("candidatesTokenCount", "output_tokens", "completion_tokens"),
    cache_read=(
        "cachedContentTokenCount",
        "cache_read_tokens",
        "cache_read",
    ),
    cache_write=("cache_write_tokens", "cache_write"),
    reasoning_tokens=("thoughtsTokenCount", "reasoning_tokens"),
    total_tokens=("totalTokenCount", "total_tokens"),
)


# Limits are conservative adapter defaults.  A model/deployment override is the
# authoritative mechanism for aliases, dated releases, enterprise extensions,
# or self-hosted endpoints.  Price fields intentionally remain unknown.
_BUILTIN_PROVIDERS: dict[str, ProviderCapabilities] = {
    "ark": ProviderCapabilities(
        provider="ark",
        wire="openai",
        default_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        default_model="doubao-seed-2.0-pro",
        context_window_tokens=262_144,
        max_output_tokens=32_768,
        tool_calling=True,
        parallel_tool_calls=True,
        strict_tool_schema=False,
        vision=True,
        audio=False,
        reasoning=True,
        streaming=True,
        usage_mapping=_OPENAI_USAGE,
    ),
    "chatgpt": ProviderCapabilities(
        provider="chatgpt",
        wire="openai",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-5",
        context_window_tokens=400_000,
        max_output_tokens=128_000,
        tool_calling=True,
        parallel_tool_calls=True,
        strict_tool_schema=True,
        vision=True,
        audio=False,
        reasoning=True,
        streaming=True,
        usage_mapping=_OPENAI_USAGE,
    ),
    "openai_responses": ProviderCapabilities(
        provider="openai_responses",
        wire="responses",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-5",
        context_window_tokens=400_000,
        max_output_tokens=128_000,
        tool_calling=True,
        parallel_tool_calls=True,
        strict_tool_schema=True,
        # This reflects the current local Responses message adapter, which is
        # text/tool-only even though the upstream API supports image inputs.
        vision=False,
        audio=False,
        reasoning=True,
        streaming=True,
        usage_mapping=_OPENAI_USAGE,
    ),
    "claude": ProviderCapabilities(
        provider="claude",
        wire="anthropic",
        default_base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-5",
        context_window_tokens=200_000,
        max_output_tokens=64_000,
        tool_calling=True,
        parallel_tool_calls=True,
        strict_tool_schema=False,
        vision=True,
        audio=False,
        reasoning=True,
        streaming=False,
        usage_mapping=_ANTHROPIC_USAGE,
    ),
    "gemini": ProviderCapabilities(
        provider="gemini",
        wire="gemini",
        default_base_url="https://generativelanguage.googleapis.com",
        default_model="gemini-2.5-flash",
        context_window_tokens=1_048_576,
        max_output_tokens=65_536,
        tool_calling=True,
        parallel_tool_calls=True,
        strict_tool_schema=False,
        vision=True,
        audio=False,
        reasoning=True,
        streaming=False,
        usage_mapping=_GEMINI_USAGE,
    ),
}


# Exact-model entries only contain facts that differ from their provider
# defaults.  Prefix/fuzzy matching is intentionally avoided: dated model ids
# must never silently inherit a capability merely because their names look alike.
_BUILTIN_MODELS: dict[tuple[str, str], dict[str, Any]] = {}

_LOCK = threading.RLock()
_LEGACY_REGISTRY: Mapping[str, Mapping[str, Any]] | None = None
_PROVIDER_OVERRIDES: dict[str, dict[str, Any]] = {}
_MODEL_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {}
_PROVIDER_CACHE: dict[tuple[str, str, str], ProviderCapabilities] = {}
_MODEL_CACHE: dict[tuple[str, str, str, int], ModelCapabilities] = {}
_CACHE_HITS = 0
_CACHE_MISSES = 0
_CACHE_GENERATION = 0


def _normalize_name(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise CapabilityError(f"{label} must be a non-empty string")
    normalized = value.strip().lower()
    if not normalized:
        raise CapabilityError(f"{label} must be a non-empty string")
    return normalized


def _normalize_endpoint(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def _is_local_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def _coerce_nested(field_name: str, value: Any) -> Any:
    if field_name == "usage_mapping" and isinstance(value, Mapping):
        allowed = {item.name for item in fields(UsageMapping)}
        unknown = set(value) - allowed
        if unknown:
            raise CapabilityError(
                f"unknown usage mapping fields: {', '.join(sorted(unknown))}"
            )
        converted = {}
        for key, paths in value.items():
            if isinstance(paths, str):
                raise CapabilityError(
                    f"usage_mapping.{key} must be a sequence of paths"
                )
            try:
                converted[key] = tuple(paths)
            except TypeError as exc:
                raise CapabilityError(
                    f"usage_mapping.{key} must be a sequence of paths"
                ) from exc
        return UsageMapping(**converted)  # type: ignore[arg-type]
    if field_name == "cost" and isinstance(value, Mapping):
        allowed = {item.name for item in fields(CostMetadata)}
        unknown = set(value) - allowed
        if unknown:
            raise CapabilityError(
                f"unknown cost metadata fields: {', '.join(sorted(unknown))}"
            )
        return CostMetadata(**value)
    return value


_CAPABILITY_FIELDS = {
    "context_window_tokens",
    "max_output_tokens",
    "tool_calling",
    "parallel_tool_calls",
    "strict_tool_schema",
    "vision",
    "audio",
    "reasoning",
    "streaming",
    "usage_mapping",
    "cost",
}


def _validated_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - _CAPABILITY_FIELDS
    if unknown:
        raise CapabilityError(
            f"unknown capability fields: {', '.join(sorted(unknown))}"
        )
    result = {key: _coerce_nested(key, value) for key, value in changes.items()}
    for key in ("context_window_tokens", "max_output_tokens"):
        value = result.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise CapabilityError(f"{key} must be a positive integer or None")
    for key in (
        "tool_calling",
        "parallel_tool_calls",
        "strict_tool_schema",
        "vision",
        "audio",
        "reasoning",
        "streaming",
    ):
        if key in result and not isinstance(result[key], bool):
            raise CapabilityError(f"{key} must be a boolean")
    cost = result.get("cost")
    if isinstance(cost, CostMetadata):
        for key in (
            "input_per_million",
            "output_per_million",
            "cache_read_per_million",
            "cache_write_per_million",
        ):
            value = getattr(cost, key)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise CapabilityError(f"cost.{key} must be a number or None")
                if value < 0:
                    raise CapabilityError(f"cost.{key} must not be negative")
    mapping = result.get("usage_mapping")
    if isinstance(mapping, UsageMapping):
        for item in fields(mapping):
            paths = getattr(mapping, item.name)
            if not isinstance(paths, tuple) or not all(
                isinstance(path, str) and path for path in paths
            ):
                raise CapabilityError(
                    f"usage_mapping.{item.name} must contain non-empty paths"
                )
    return result


def _invalidate_cache_locked() -> None:
    global _CACHE_GENERATION
    _PROVIDER_CACHE.clear()
    _MODEL_CACHE.clear()
    _CACHE_GENERATION += 1


def bind_provider_registry(registry: Mapping[str, Mapping[str, Any]]) -> None:
    """Bind the legacy mutable provider registry used by ``llm.client``.

    Lookup cache keys include a deterministic snapshot of the selected entry,
    so legacy code that directly changes ``PROVIDERS[name]`` or adds a provider
    gets fresh capability records without an explicit invalidation call.
    """
    if not isinstance(registry, Mapping):
        raise CapabilityError("provider registry must be a mapping")
    global _LEGACY_REGISTRY
    with _LOCK:
        _LEGACY_REGISTRY = registry
        _invalidate_cache_locked()


def _registry_entry_locked(name: str) -> tuple[Mapping[str, Any] | None, str]:
    entry = _LEGACY_REGISTRY.get(name) if _LEGACY_REGISTRY is not None else None
    if entry is None:
        return None, ""
    if not isinstance(entry, Mapping):
        raise CapabilityError(f"provider registry entry {name!r} must be a mapping")
    try:
        fingerprint = json.dumps(
            entry,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        )
    except (TypeError, ValueError):
        fingerprint = repr(entry)
    return entry, fingerprint


def _provider_base_locked(name: str) -> tuple[ProviderCapabilities, str]:
    builtin = _BUILTIN_PROVIDERS.get(name)
    entry, fingerprint = _registry_entry_locked(name)
    if entry is None:
        if _LEGACY_REGISTRY is not None and name not in _LEGACY_REGISTRY:
            known = set(_LEGACY_REGISTRY)
            raise CapabilityError(
                f"unknown provider {name!r}; known: {', '.join(sorted(known))}"
            )
        if builtin is None:
            known = set(_BUILTIN_PROVIDERS)
            if _LEGACY_REGISTRY is not None:
                known.update(_LEGACY_REGISTRY)
            raise CapabilityError(
                f"unknown provider {name!r}; known: {', '.join(sorted(known))}"
            )
        return builtin, fingerprint

    if builtin is None:
        wire = str(entry.get("wire") or "").strip().lower()
        if wire not in {"openai", "responses", "anthropic", "gemini"}:
            raise CapabilityError(
                f"custom provider {name!r} has unsupported wire {wire!r}"
            )
        base_url = str(entry.get("base_url") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not base_url or not model:
            raise CapabilityError(
                f"custom provider {name!r} requires base_url and model"
            )
        usage_mapping = {
            "openai": _OPENAI_USAGE,
            "responses": _OPENAI_USAGE,
            "anthropic": _ANTHROPIC_USAGE,
            "gemini": _GEMINI_USAGE,
        }[wire]
        builtin = ProviderCapabilities(
            provider=name,
            wire=wire,
            default_base_url=base_url,
            default_model=model,
            context_window_tokens=None,
            max_output_tokens=None,
            tool_calling=bool(entry.get("tool_calling", True)),
            parallel_tool_calls=bool(entry.get("parallel_tool_calls", True)),
            strict_tool_schema=bool(
                entry.get("strict_tool_schema", wire in {"openai", "responses"})
            ),
            vision=bool(entry.get("vision", False)),
            audio=bool(entry.get("audio", False)),
            reasoning=bool(entry.get("reasoning", False)),
            streaming=bool(entry.get("streaming", wire in {"openai", "responses"})),
            usage_mapping=usage_mapping,
            custom_endpoint=True,
            local_endpoint=_is_local_endpoint(base_url),
        )

    identity_changes = {
        "wire": str(entry.get("wire", builtin.wire)).strip().lower(),
        "default_base_url": str(
            entry.get("base_url", builtin.default_base_url)
        ).strip(),
        "default_model": str(entry.get("model", builtin.default_model)).strip(),
        "vision": bool(entry.get("vision", builtin.vision)),
    }
    capability_changes = {key: entry[key] for key in _CAPABILITY_FIELDS if key in entry}
    capability_changes = _validated_changes(capability_changes)
    wire = identity_changes["wire"]
    if wire != builtin.wire and "usage_mapping" not in capability_changes:
        capability_changes["usage_mapping"] = {
            "openai": _OPENAI_USAGE,
            "responses": _OPENAI_USAGE,
            "anthropic": _ANTHROPIC_USAGE,
            "gemini": _GEMINI_USAGE,
        }.get(wire, builtin.usage_mapping)
    dynamic = replace(builtin, **{**identity_changes, **capability_changes})
    original = _BUILTIN_PROVIDERS.get(name)
    registry_custom = original is None or (
        _normalize_endpoint(dynamic.default_base_url)
        != _normalize_endpoint(original.default_base_url)
    )
    return replace(dynamic, custom_endpoint=registry_custom), fingerprint


def get_provider_capabilities(
    provider: str, *, base_url: str | None = None
) -> ProviderCapabilities:
    """Return effective immutable capabilities for a provider and endpoint."""
    global _CACHE_HITS, _CACHE_MISSES
    name = _normalize_name(provider, "provider")
    endpoint_arg = _normalize_endpoint(base_url)
    with _LOCK:
        base, fingerprint = _provider_base_locked(name)
        cache_key = (name, endpoint_arg, fingerprint)
        cached = _PROVIDER_CACHE.get(cache_key)
        if cached is not None:
            _CACHE_HITS += 1
            return cached
        effective = replace(base, **_PROVIDER_OVERRIDES.get(name, {}))
        endpoint = endpoint_arg or _normalize_endpoint(effective.default_base_url)
        default_endpoint = _normalize_endpoint(effective.default_base_url)
        local = _is_local_endpoint(endpoint)
        if local and name in _BUILTIN_PROVIDERS:
            # A loopback OpenAI-compatible URL proves transport shape only. It
            # does not prove the loaded model supports tools, vision, reasoning,
            # a vendor-sized context window, or the vendor's pricing. Keep the
            # automatic discovery path conservative; explicit provider/model
            # capability overrides remain authoritative below/at model lookup.
            conservative = {
                "context_window_tokens": None,
                "max_output_tokens": None,
                "tool_calling": False,
                "parallel_tool_calls": False,
                "strict_tool_schema": False,
                "vision": False,
                "audio": False,
                "reasoning": False,
                "cost": CostMetadata(source="local endpoint; capabilities unknown"),
            }
            conservative.update(_PROVIDER_OVERRIDES.get(name, {}))
            effective = replace(effective, **conservative)
        effective = replace(
            effective,
            custom_endpoint=bool(
                effective.custom_endpoint
                or (endpoint_arg and endpoint != default_endpoint)
            ),
            local_endpoint=local,
        )
        _PROVIDER_CACHE[cache_key] = effective
        _CACHE_MISSES += 1
        return effective


def get_model_capabilities(
    provider: str,
    model: str | None = None,
    *,
    base_url: str | None = None,
) -> ModelCapabilities:
    """Resolve provider defaults plus exact-model and deployment overrides."""
    global _CACHE_HITS, _CACHE_MISSES
    provider_caps = get_provider_capabilities(provider, base_url=base_url)
    model_name = (model or provider_caps.default_model).strip()
    if not model_name:
        raise CapabilityError("model must be a non-empty string")
    model_key = model_name.lower()
    endpoint = _normalize_endpoint(base_url) or _normalize_endpoint(
        provider_caps.default_base_url
    )
    cache_key = (provider_caps.provider, model_key, endpoint, hash(provider_caps))
    with _LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            _CACHE_HITS += 1
            return cached
        values: dict[str, Any] = {
            field: getattr(provider_caps, field) for field in _CAPABILITY_FIELDS
        }
        values.update(_BUILTIN_MODELS.get((provider_caps.provider, model_key), {}))
        values.update(_MODEL_OVERRIDES.get((provider_caps.provider, model_key), {}))
        result = ModelCapabilities(
            provider=provider_caps.provider,
            model=model_name,
            wire=provider_caps.wire,
            endpoint=endpoint,
            custom_endpoint=provider_caps.custom_endpoint,
            local_endpoint=provider_caps.local_endpoint,
            **values,
        )
        _MODEL_CACHE[cache_key] = result
        _CACHE_MISSES += 1
        return result


# Short lookup aliases make the public API pleasant while the explicit names
# remain self-documenting at call sites.
provider_capabilities = get_provider_capabilities
model_capabilities = get_model_capabilities


def set_capability_override(
    provider: str,
    changes: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    **kwargs: Any,
) -> None:
    """Set a provider- or exact-model override and invalidate lookup caches.

    Passing a mapping plus keyword changes is supported; keywords take
    precedence.  Overrides are process-local by design so configuration and
    plugin layers can own persistence without this pure metadata module writing
    files or environment variables.
    """
    name = _normalize_name(provider, "provider")
    merged = dict(changes or {})
    merged.update(kwargs)
    validated = _validated_changes(merged)
    with _LOCK:
        _provider_base_locked(name)
        if model is None:
            target = _PROVIDER_OVERRIDES.setdefault(name, {})
        else:
            model_name = _normalize_name(model, "model")
            target = _MODEL_OVERRIDES.setdefault((name, model_name), {})
        target.update(validated)
        _invalidate_cache_locked()


def clear_capability_overrides(
    provider: str | None = None, *, model: str | None = None
) -> None:
    """Clear all overrides, a provider's overrides, or one exact model's."""
    with _LOCK:
        if provider is None:
            if model is not None:
                raise CapabilityError("model requires provider")
            _PROVIDER_OVERRIDES.clear()
            _MODEL_OVERRIDES.clear()
        else:
            name = _normalize_name(provider, "provider")
            if model is None:
                _PROVIDER_OVERRIDES.pop(name, None)
                for key in tuple(_MODEL_OVERRIDES):
                    if key[0] == name:
                        _MODEL_OVERRIDES.pop(key, None)
            else:
                _MODEL_OVERRIDES.pop((name, _normalize_name(model, "model")), None)
        _invalidate_cache_locked()


def clear_capability_cache() -> None:
    """Drop resolved entries without changing any registered override."""
    with _LOCK:
        _invalidate_cache_locked()


def capability_cache_info() -> CapabilityCacheInfo:
    with _LOCK:
        return CapabilityCacheInfo(
            hits=_CACHE_HITS,
            misses=_CACHE_MISSES,
            provider_entries=len(_PROVIDER_CACHE),
            model_entries=len(_MODEL_CACHE),
            generation=_CACHE_GENERATION,
        )


def validate_model_request(
    provider: str,
    model: str | None = None,
    *,
    base_url: str | None = None,
    tool_calling: bool = False,
    parallel_tool_calls: bool = False,
    strict_tool_schema: bool = False,
    vision: bool = False,
    audio: bool = False,
    reasoning: bool = False,
    streaming: bool = False,
    max_output_tokens: int | None = None,
) -> ModelCapabilities:
    """Validate a planned request and return the effective capability record."""
    capabilities = get_model_capabilities(provider, model, base_url=base_url)
    requested = {
        "tool_calling": tool_calling,
        "parallel_tool_calls": parallel_tool_calls,
        "strict_tool_schema": strict_tool_schema,
        "vision": vision,
        "audio": audio,
        "reasoning": reasoning,
        "streaming": streaming,
    }
    missing = [
        name
        for name, enabled in requested.items()
        if enabled and not getattr(capabilities, name)
    ]
    if missing:
        raise CapabilityError(
            f"provider/model {capabilities.provider}/{capabilities.model} does not "
            f"support: {', '.join(missing)}"
        )
    if max_output_tokens is not None:
        if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            raise CapabilityError("max_output_tokens must be a positive integer")
        if (
            capabilities.max_output_tokens is not None
            and max_output_tokens > capabilities.max_output_tokens
        ):
            raise CapabilityError(
                f"max_output_tokens {max_output_tokens} exceeds model limit "
                f"{capabilities.max_output_tokens}"
            )
    return capabilities


def _path_value(usage: Mapping[str, Any], path: str) -> Any:
    value: Any = usage
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _token_value(usage: Mapping[str, Any], paths: tuple[str, ...]) -> int:
    for path in paths:
        raw = _path_value(usage, path)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if value >= 0:
            return value
    return 0


def normalize_usage(
    usage: Mapping[str, Any] | None,
    provider_or_mapping: str | UsageMapping,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, int]:
    """Map provider-native usage to one stable, backward-compatible shape.

    ``input_tokens``/``output_tokens`` are canonical.  The legacy
    ``prompt_tokens``/``completion_tokens`` aliases are intentionally retained
    until all external consumers have migrated.  Cache and reasoning counters
    are reported separately but are not added to ``total_tokens`` because most
    providers already include them in input/output counts.
    """
    raw: Mapping[str, Any] = usage if isinstance(usage, Mapping) else {}
    if isinstance(provider_or_mapping, UsageMapping):
        mapping = provider_or_mapping
    else:
        mapping = get_model_capabilities(
            provider_or_mapping, model, base_url=base_url
        ).usage_mapping
    input_tokens = _token_value(raw, mapping.input_tokens)
    output_tokens = _token_value(raw, mapping.output_tokens)
    explicit_total = _token_value(raw, mapping.total_tokens)
    total = explicit_total if explicit_total else input_tokens + output_tokens
    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": _token_value(raw, mapping.cache_read),
        "cache_write": _token_value(raw, mapping.cache_write),
        "reasoning_tokens": _token_value(raw, mapping.reasoning_tokens),
        # Compatibility with existing Store/Gateway integrations.
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total,
    }
    return result


def calculate_usage_cost_usd(
    usage: Mapping[str, Any] | None,
    cost: CostMetadata,
) -> float | None:
    """Calculate an auditable USD charge from canonical token counters.

    Unknown prices stay unknown: this function never substitutes a vendor
    price table.  Cache counters are treated as subsets of input tokens.  When
    a deployment supplies a cache-specific price it replaces the ordinary
    input price for that subset; otherwise those tokens retain the declared
    input price.  Reasoning tokens are not added separately because provider
    usage normally includes them in output tokens.
    """

    if str(cost.currency or "").strip().upper() != "USD":
        return None
    if cost.input_per_million is None or cost.output_per_million is None:
        return None
    raw: Mapping[str, Any] = usage if isinstance(usage, Mapping) else {}

    def counter(name: str) -> int:
        value = raw.get(name, 0)
        if value is None or isinstance(value, bool):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, parsed)

    input_tokens = counter("input_tokens") or counter("prompt_tokens")
    output_tokens = counter("output_tokens") or counter("completion_tokens")
    cache_read = min(input_tokens, counter("cache_read"))
    cache_write = min(input_tokens - cache_read, counter("cache_write"))
    regular_input = input_tokens - cache_read - cache_write

    cache_read_rate = (
        cost.cache_read_per_million
        if cost.cache_read_per_million is not None
        else cost.input_per_million
    )
    cache_write_rate = (
        cost.cache_write_per_million
        if cost.cache_write_per_million is not None
        else cost.input_per_million
    )
    total = (
        regular_input * cost.input_per_million
        + cache_read * cache_read_rate
        + cache_write * cache_write_rate
        + output_tokens * cost.output_per_million
    ) / 1_000_000
    # Floating point is sufficient for UI accounting, but keep a deterministic
    # representation so persisted timeline snapshots remain stable.
    return round(total, 12)


def legacy_provider_specs() -> dict[str, dict[str, Any]]:
    """Build the original mutable ``PROVIDERS`` dictionary shape.

    ``client.PROVIDERS`` remains a normal dict for callers that customize it;
    capability records themselves remain immutable and independently cached.
    """
    return {
        name: {
            "wire": item.wire,
            "base_url": item.default_base_url,
            "model": item.default_model,
            "vision": item.vision,
        }
        for name, item in _BUILTIN_PROVIDERS.items()
    }
