"""The one place that answers "which model will this install actually use?".

The daemon's process config is only the base. What a turn really sends is that
config plus the Customize → Models settings held in the store, plus whatever
model the session itself selected. Two callers need that answer — the runtime
that sends the request, and `doctor`, which exists to tell a user whether
sending will work — and they had two separate implementations of it.

Only one of them was right. `doctor` read `cfg.llm` alone, so an installation
whose model was configured entirely through the UI (the documented path: the
daemon boots with no key) was diagnosed `model FAIL` while working perfectly.
Diagnostics that disagree with the runtime are worse than no diagnostics: they
send people to fix something that is not broken.

So the resolution lives here, once, and both call it.
"""
from __future__ import annotations

import dataclasses
import ipaddress
from typing import Any
from urllib.parse import urlsplit

#: The settings Customize → Models writes. `llm_api_key` is deliberately absent:
#: after migration that row holds a broker reference rather than a key, so it
#: must be read through `get_secret_setting`, never `get_setting`.
_PLAIN_SETTINGS = ("llm_model", "llm_base_url", "llm_provider")


def is_loopback_endpoint(base_url: Any) -> bool:
    """Whether a base URL points at a server on this machine.

    Ollama, LM Studio, vLLM and llama.cpp all speak the OpenAI-compatible wire
    on loopback and all of them authenticate by being unreachable from anywhere
    else. Demanding an API key from them is demanding a credential that does
    not exist, which is why `doctor` reported a working local setup as a
    failure.

    Loopback specifically, not "local-looking": a hostname that merely resolves
    to 127.0.0.1 today is not a stable authorisation story, so only literal
    loopback addresses count.
    """
    text = str(base_url or "").strip()
    if not text:
        return False
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def store_overrides(store: Any) -> dict[str, str]:
    """The Customize → Models settings, read defensively.

    A store that cannot be read yields no overrides rather than an exception:
    both callers are on paths that must still produce an answer.
    """
    if store is None:
        return {}
    values: dict[str, str] = {}
    try:
        for key in _PLAIN_SETTINGS:
            value = store.get_setting(key)
            if value:
                values[key] = str(value)
    except Exception:  # noqa: BLE001 - an unreadable store means no overrides
        return {}
    try:
        # Through the broker, not get_setting: after migration the row holds a
        # reference, and handing that to a provider as an API key fails auth in
        # a way that looks exactly like a bad key.
        secret = store.get_secret_setting("llm_api_key")
        if secret:
            values["llm_api_key"] = str(secret)
    except Exception:  # noqa: BLE001
        pass
    return values


def resolve_llm_config(
    base: Any, store: Any = None, *, model_override: str | None = None
) -> Any:
    """base config + Customize → Models overrides + the session's chosen model.

    When the PROVIDER is overridden the base provider's concrete base_url,
    model and key must NOT be inherited, or requests go to the wrong endpoint
    under the wrong credential. Clearing them lets ``LLMConfig.__post_init__``
    re-resolve the new provider's own defaults.
    """
    from openai4s.server.model_profiles import clean_api_key

    settings = store_overrides(store)
    chosen = model_override or settings.get("llm_model")
    over: dict[str, Any] = {}
    api_key = clean_api_key(settings.get("llm_api_key"))
    if api_key:
        over["api_key"] = api_key
    if settings.get("llm_base_url"):
        over["base_url"] = settings["llm_base_url"]
    if chosen:
        over["model"] = chosen
    provider = settings.get("llm_provider")
    if provider and provider != getattr(base, "provider", None):
        over["provider"] = provider
        # Re-resolve the NEW provider's key too unless a real runtime key was
        # supplied; otherwise `replace` carries the previous provider's
        # resolved key into the new provider.
        over.setdefault("api_key", "")
        over.setdefault("base_url", "")
        over.setdefault("model", "")
    if not over:
        return base
    try:
        return dataclasses.replace(base, **over)
    except Exception:  # noqa: BLE001 - an unusable override is not a crash
        return base


__all__ = ["is_loopback_endpoint", "resolve_llm_config", "store_overrides"]
