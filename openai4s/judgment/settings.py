"""Experimental judgment settings: flags, key, disclosure ack, egress report.

Gateway routes parse and forward; this module owns the behaviour. The TypeSafe
API key is stored through SecretBroker (``typesafe_api_key``, scope
``judgment``) and is never written to ``os.environ`` and never echoed.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from openai4s.config import Config
from openai4s.judgment.disclosure import (
    CAPABILITIES,
    DISCLOSURE_TEXT,
    DISCLOSURE_VERSION,
    FACTS_EN,
    FACTS_ZH,
    disclosure_matches,
    is_acknowledged,
)
from openai4s.judgment.flags import (
    SETTING_BY_CAPABILITY,
    SETTING_DISCLOSURE_ACK,
    SETTING_MASTER,
    resolve,
)
from openai4s.judgment.types import JudgmentResult

log = logging.getLogger("openai4s.judgment.settings")

TYPESAFE_HOST = "api.typesafe.ai"
SECRET_NAME = "typesafe_api_key"
SECRET_SCOPE = "judgment"
ENV_API_KEY = "OPENAI4S_TYPESAFE_API_KEY"

_ALLOWED_UPDATE_KEYS = frozenset(
    {"enabled", "capabilities", "acknowledge", "api_key", "clear_api_key"}
)
_ALLOWED_ACK_KEYS = frozenset({"version", "capabilities", "provider"})


class JudgmentSettingsStore(Protocol):
    """Store subset used by the experimental judgment settings surface."""

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def set_setting(self, key: str, value: str) -> None: ...

    def get_secret_setting(self, key: str, *, scope: str | None = None) -> str: ...

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str: ...


class SettingsError(ValueError):
    """Client error on the settings surface; gateway maps this to HTTP 400."""

    def __init__(self, message: str, error_code: str = "invalid_request") -> None:
        super().__init__(message)
        self.error_code = error_code


def _require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise SettingsError(f"{field} must be a boolean")
    return value


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise SettingsError(f"{field} must be a string")
    return value


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_ack(store: JudgmentSettingsStore | None) -> dict[str, Any] | None:
    if store is None:
        return None
    raw = store.get_setting(SETTING_DISCLOSURE_ACK)
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def key_is_configured(store: JudgmentSettingsStore | None) -> bool:
    """Whether a TypeSafe key is available. Never returns the value."""

    env_key = (os.environ.get(ENV_API_KEY) or "").strip()
    if env_key:
        return True
    if store is None:
        return False
    stored = (store.get_secret_setting(SECRET_NAME, scope=SECRET_SCOPE) or "").strip()
    return bool(stored)


def egress_report(host: str = TYPESAFE_HOST) -> dict[str, Any]:
    """Report whether ``api.typesafe.ai`` is already authorized.

    ``domain_allowed`` is ``egress.domain_in_allowlist`` — catalog membership,
    independent of whether allowlist mode is currently enforcing. That is the
    "already authorized" answer; ``egress.domain_allowed`` would be True in
    the default ``off`` mode even when the host is not on the catalog.
    ``remediation`` is set only when allowlist mode would currently block.
    """

    from openai4s import egress

    mode = egress.egress_mode()
    authorized = bool(host) and egress.domain_in_allowlist(host)
    remediation: str | None
    if host and mode == "allowlist" and not authorized:
        remediation = egress.blocked_message(host)
    else:
        remediation = None
    return {
        "host": host,
        "mode": mode,
        "domain_allowed": authorized,
        "remediation": remediation,
    }


def _effective_public(
    cfg: Config, store: JudgmentSettingsStore | None
) -> dict[str, Any]:
    flags = resolve(cfg, store)
    out: dict[str, Any] = {
        "master": {"enabled": flags.master.enabled, "source": flags.master.source},
    }
    for name in CAPABILITIES:
        flag = getattr(flags, name)
        out[name] = {"enabled": bool(flag.enabled), "source": flag.source}
    return out


def _disclosure_public(
    store: JudgmentSettingsStore | None, provider: str
) -> dict[str, Any]:
    capabilities = {
        name: {"zh": DISCLOSURE_TEXT[name]["zh"], "en": DISCLOSURE_TEXT[name]["en"]}
        for name in CAPABILITIES
    }
    ack = _load_ack(store)
    acked = disclosure_matches(ack, provider)
    facts = {"en": FACTS_EN, "zh": FACTS_ZH}
    if provider == "llm":
        facts = {
            "en": (
                "Judgment inputs are sent to the main model provider configured in Models. "
                "That provider's hosting, retention and training policies apply. "
                "Results are uncalibrated. Do not enable this feature for sensitive data."
            ),
            "zh": (
                "判断输入会发送给 Models 中配置的主模型服务商，其托管、保留和训练政策适用。"
                "结果未经概率校准。敏感数据不要开启。"
            ),
        }
    return {
        "version": DISCLOSURE_VERSION,
        "capabilities": capabilities,
        "facts": facts,
        "acked": acked,
        "acknowledged_capabilities": [
            name for name in CAPABILITIES if is_acknowledged(ack, name, provider)
        ],
    }


def _llm_key_ready(cfg: Config) -> bool:
    """Mirror the main client: local endpoints need no API key."""

    if cfg.llm.api_key:
        return True
    from openai4s.llm.capabilities import get_model_capabilities
    from openai4s.llm.models import LLMError
    from openai4s.llm.registry import provider_spec

    try:
        spec = provider_spec(cfg.llm.provider)
        return get_model_capabilities(
            cfg.llm.provider,
            cfg.llm.model or spec["model"],
            base_url=cfg.llm.base_url or spec["base_url"],
        ).local_endpoint
    except (ValueError, LLMError):
        return False


def resolve_settings_config(cfg: Config, store: JudgmentSettingsStore | None) -> Config:
    """Use current Models settings for the global settings/probe surfaces."""

    if resolve(cfg, store).provider != "llm":
        return cfg
    from openai4s.llm.resolve import resolve_llm_config

    return replace(cfg, llm=resolve_llm_config(cfg.llm, store))


def status(cfg: Config, store: JudgmentSettingsStore | None) -> dict[str, Any]:
    """Public settings projection. Never includes the API key."""

    cfg = resolve_settings_config(cfg, store)
    flags = resolve(cfg, store)
    model = flags.model
    host = TYPESAFE_HOST
    if flags.provider == "llm":
        model = cfg.llm.model
        configured = _llm_key_ready(cfg)
        host = urlsplit(cfg.llm.base_url).hostname or ""
    else:
        configured = key_is_configured(store)
    return {
        "experimental": True,
        "effective": _effective_public(cfg, store),
        "provider": flags.provider,
        "model": model,
        "key_configured": configured,
        "disclosure": _disclosure_public(store, flags.provider),
        "egress": egress_report(host),
    }


def _validated_acknowledge(raw: object) -> str:
    if not isinstance(raw, dict):
        raise SettingsError("acknowledge must be an object")
    unknown = set(raw) - _ALLOWED_ACK_KEYS
    if unknown:
        unknown_names = ", ".join(sorted(unknown))
        raise SettingsError(f"unknown acknowledge field: {unknown_names}")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SettingsError("acknowledge.version must be a non-empty string")
    if version != DISCLOSURE_VERSION:
        raise SettingsError(
            f"acknowledge.version {version!r} does not match "
            f"current disclosure version {DISCLOSURE_VERSION!r}",
            "disclosure_version_mismatch",
        )
    provider = raw.get("provider", "typesafe")
    if not isinstance(provider, str) or provider not in {"typesafe", "llm"}:
        raise SettingsError("acknowledge.provider must be typesafe or llm")
    listed = raw.get("capabilities")
    if not isinstance(listed, list):
        raise SettingsError("acknowledge.capabilities must be a list of names")
    names: list[str] = []
    for item in listed:
        if not isinstance(item, str) or not item.strip():
            raise SettingsError(
                "acknowledge.capabilities entries must be non-empty strings"
            )
        if item not in CAPABILITIES:
            raise SettingsError(f"unknown capability: {item}")
        if item not in names:
            names.append(item)
    record = {
        "version": DISCLOSURE_VERSION,
        "provider": provider,
        "capabilities": names,
        "acked_at": _now_iso(),
    }
    return json.dumps(record, sort_keys=True)


def _validated_capabilities(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise SettingsError("capabilities must be an object")
    settings: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or name not in SETTING_BY_CAPABILITY:
            raise SettingsError(f"unknown capability: {name}")
        enabled = _require_bool(value, f"capabilities.{name}")
        settings[SETTING_BY_CAPABILITY[name]] = "true" if enabled else "false"
    return settings


def update(store: JudgmentSettingsStore, body: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a settings write. Does not echo the key and does not set env."""

    if not isinstance(body, Mapping):
        raise SettingsError("request body must be an object")
    unknown = set(body) - _ALLOWED_UPDATE_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise SettingsError(f"unknown field: {names}")

    # Validate the complete request before any side effect. A 400 must not
    # silently acknowledge disclosure, enable outbound data, or clear a key.
    settings: dict[str, str] = {}
    if "acknowledge" in body:
        settings[SETTING_DISCLOSURE_ACK] = _validated_acknowledge(body["acknowledge"])
    if "enabled" in body:
        enabled = _require_bool(body["enabled"], "enabled")
        settings[SETTING_MASTER] = "true" if enabled else "false"
    if "capabilities" in body:
        settings.update(_validated_capabilities(body["capabilities"]))

    clear = (
        _require_bool(body["clear_api_key"], "clear_api_key")
        if "clear_api_key" in body
        else False
    )
    key = ""
    if "api_key" in body:
        key = _require_str(body["api_key"], "api_key").strip()
        if key and clear:
            raise SettingsError("cannot set api_key and clear_api_key together")

    for name, value in settings.items():
        store.set_setting(name, value)
    if clear or key:
        store.set_secret_setting(SECRET_NAME, "" if clear else key, scope=SECRET_SCOPE)

    return {"ok": True}


def _probe_public(result: object) -> dict[str, Any]:
    if isinstance(result, JudgmentResult):
        return {
            "status": result.status,
            "error_code": result.error_code,
            "latency_ms": int(result.latency_ms),
            "model": result.model,
        }
    if isinstance(result, Mapping):
        latency = result.get("latency_ms", 0)
        try:
            latency_ms = int(latency)
        except (TypeError, ValueError):
            latency_ms = 0
        model = result.get("model")
        return {
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "latency_ms": latency_ms,
            "model": model if isinstance(model, str) else None,
        }
    status_value = getattr(result, "status", None)
    error_code = getattr(result, "error_code", None)
    latency_ms = getattr(result, "latency_ms", 0)
    model = getattr(result, "model", None)
    try:
        latency_int = int(latency_ms)
    except (TypeError, ValueError):
        latency_int = 0
    return {
        "status": status_value,
        "error_code": error_code,
        "latency_ms": latency_int,
        "model": model if isinstance(model, str) else None,
    }


def test_connection(service_provider: Any) -> dict[str, Any]:
    """Call ``JudgmentService.probe()`` and return only public connection facts."""

    result = service_provider.probe()
    return _probe_public(result)


def probe_connection(
    cfg: Config, store: JudgmentSettingsStore | None
) -> dict[str, Any]:
    """Build the host service (deferred import) and probe it.

    The import stays inside the function to keep the gateway's import graph
    flat, not because the module might be missing: W1-B merged it, so an
    ImportError here is a real defect and must not be reported as a tidy
    ``unconfigured``.
    """

    from openai4s.host.judgment import JudgmentService

    service = JudgmentService(
        cfg_provider=resolve_settings_config(cfg, store), store_provider=store
    )
    return test_connection(service)


__all__ = (
    "ENV_API_KEY",
    "SECRET_NAME",
    "SECRET_SCOPE",
    "SettingsError",
    "TYPESAFE_HOST",
    "egress_report",
    "key_is_configured",
    "probe_connection",
    "resolve_settings_config",
    "status",
    "test_connection",
    "update",
)
