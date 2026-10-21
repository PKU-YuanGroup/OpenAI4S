"""Which backup web-search engines run, and in what order.

Doubao (the Agent Plan key) is not in this list: when it is configured it always
answers first, and `host/search.py` decides what happens when its quota runs
out. Behind it sit the engines `webtools.web_search` knows by id — ``tavily``,
``you`` (You.com) and ``builtin`` (the keyless DuckDuckGo/Bing/Mojeek chain) —
which the user can reorder and switch on or off.

One rule is enforced here and in the UI alike: at least one engine must stay
*usable*, meaning on and able to answer. Tavily counts only with a key; the
other two need none. Without the rule a user could switch every engine off and
every search would come back empty with no hint why.

The stored setting is a JSON object under ``search_engines``. An install that
never saved one gets the default order with every engine on — the same chain
`web_search` walked before the setting existed, so an upgrade changes nothing.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai4s.webtools import SEARCH_ENGINES

SETTING_KEY = "search_engines"
SECRET_SCOPE = "search"
#: engine id -> (settings credential name, environment variable that wins)
ENGINE_KEYS: dict[str, tuple[str, str]] = {
    "tavily": ("tavily_api_key", "OPENAI4S_TAVILY_API_KEY"),
    "you": ("you_api_key", "YDC_API_KEY"),
}
ENGINE_ENDPOINTS = {
    "tavily": "https://api.tavily.com/search",
    "you": "https://api.you.com/v1/agents/search",
    "builtin": "",
}
#: You.com answers keyed requests from a different host.
_YOU_KEYED_ENDPOINT = "https://ydc-index.io/v1/search"


class SearchSettingsError(ValueError):
    """A submitted engine configuration is malformed or leaves nothing usable."""


def _default() -> dict[str, Any]:
    return {
        "order": list(SEARCH_ENGINES),
        "enabled": {engine: True for engine in SEARCH_ENGINES},
    }


def load(store) -> dict[str, Any]:
    """The stored order and on/off map, repaired to cover every known engine.

    A row written by another version may name an engine this one dropped or
    miss one it added; unknown ids are discarded and missing ones appended in
    default order, switched on, so a newly shipped engine is not silently off.
    """

    raw = store.get_setting(SETTING_KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    order = [e for e in stored.get("order") or [] if e in SEARCH_ENGINES]
    order = list(dict.fromkeys(order))
    order += [e for e in SEARCH_ENGINES if e not in order]
    enabled_raw = stored.get("enabled")
    enabled_raw = enabled_raw if isinstance(enabled_raw, dict) else {}
    enabled = {
        e: enabled_raw[e] if isinstance(enabled_raw.get(e), bool) else True
        for e in SEARCH_ENGINES
    }
    return {"order": order, "enabled": enabled}


def has_key(store, engine: str) -> bool:
    """Whether a credential for ``engine`` is set (environment or broker)."""

    names = ENGINE_KEYS.get(engine)
    if names is None:
        return False
    setting, env = names
    if (os.environ.get(env) or "").strip():
        return True
    try:
        return bool((store.get_secret_setting(setting) or "").strip())
    except Exception:  # noqa: BLE001 - an unreadable broker is not a key
        return False


def usable(store, engine: str, enabled: dict[str, bool]) -> bool:
    if not enabled.get(engine):
        return False
    return has_key(store, engine) if engine == "tavily" else True


def save(store, *, order: Any, enabled: Any) -> dict[str, Any]:
    """Validate and persist a new configuration; returns what was stored."""

    if (
        not isinstance(order, list)
        or not all(isinstance(e, str) for e in order)
        or sorted(order) != sorted(SEARCH_ENGINES)
    ):
        raise SearchSettingsError(
            f"order must list each of {', '.join(SEARCH_ENGINES)} exactly once"
        )
    if not isinstance(enabled, dict) or not all(
        isinstance(enabled.get(e), bool) for e in SEARCH_ENGINES
    ):
        raise SearchSettingsError("enabled must map every engine to true or false")
    config = {
        "order": list(order),
        "enabled": {e: enabled[e] for e in SEARCH_ENGINES},
    }
    if not any(usable(store, e, config["enabled"]) for e in SEARCH_ENGINES):
        raise SearchSettingsError(
            "at least one search engine must stay on and usable "
            "(Tavily counts only with an API key)"
        )
    store.set_setting(SETTING_KEY, json.dumps(config))
    return config


def engine_chain(store) -> tuple[str, ...]:
    """The engines `web_search` should try, in order (switched-on ones only)."""

    config = load(store)
    return tuple(e for e in config["order"] if config["enabled"][e])


def choose_primary(store, engine: str) -> dict[str, Any]:
    """Make ``engine`` the first backup and switch it on (setup's one choice).

    ``builtin`` as the choice means "keyless engines only": the keyed and
    third-party engines are switched off. Any other choice leaves the rest of
    the list as it was.
    """

    if engine not in SEARCH_ENGINES:
        raise SearchSettingsError(f"unknown search engine {engine!r}")
    config = load(store)
    order = [engine] + [e for e in config["order"] if e != engine]
    enabled = dict(config["enabled"])
    enabled[engine] = True
    if engine == "builtin":
        enabled = {e: e == "builtin" for e in SEARCH_ENGINES}
    return save(store, order=order, enabled=enabled)


#: First-run setup's one search decision: Doubao, or a backup engine first.
SETUP_CHOICES = ("doubao",) + SEARCH_ENGINES


def apply_setup_choice(store, choice: str, key: str = "") -> dict[str, Any]:
    """Apply the Web wizard's or ``openai4s init``'s search choice.

    ``doubao`` stores the Agent Plan key (Doubao then answers first; the backup
    list is untouched). Any other choice optionally stores that engine's key
    and makes it the first backup -- Tavily refuses without one.
    """

    key = (key or "").strip()
    if choice == "doubao":
        if not key:
            raise SearchSettingsError("Doubao search needs an Agent Plan key")
        from openai4s import datapro

        datapro.save_agent_plan_key(store, key)
        return project(store)
    if choice not in SEARCH_ENGINES:
        raise SearchSettingsError(
            f"search choice must be one of {', '.join(SETUP_CHOICES)}"
        )
    if key and choice in ENGINE_KEYS:
        set_key(store, choice, key)
    if choice == "tavily" and not has_key(store, "tavily"):
        raise SearchSettingsError("Tavily needs an API key")
    choose_primary(store, choice)
    return project(store)


def set_key(store, engine: str, key: str) -> None:
    """Store (or, with an empty ``key``, clear) an engine's credential and
    mirror it into the environment variable `webtools` reads."""

    setting, env = ENGINE_KEYS[engine]
    key = (key or "").strip()
    store.set_secret_setting(setting, key, scope=SECRET_SCOPE)
    if key:
        os.environ[env] = key
    else:
        os.environ.pop(env, None)


def load_keys_into_env(store) -> None:
    """At daemon start: expose UI-saved keys to `webtools`, unless the
    environment already sets them (an explicit export wins)."""

    for setting, env in ENGINE_KEYS.values():
        if os.environ.get(env):
            continue
        try:
            value = store.get_secret_setting(setting)
        except Exception:  # noqa: BLE001 - startup must not fail on one key
            value = ""
        if value:
            os.environ[env] = value


def project(store) -> dict[str, Any]:
    """Secret-free view for the settings route and first-run setup."""

    from openai4s import datapro

    config = load(store)
    try:
        doubao = bool(datapro.credential_state(store).get("key_configured"))
    except Exception:  # noqa: BLE001 - an unreadable broker is not a key
        doubao = False
    return {
        "doubao_configured": doubao,
        "order": config["order"],
        "engines": [
            {
                "id": engine,
                "enabled": config["enabled"][engine],
                "needs_key": engine == "tavily",
                "accepts_key": engine in ENGINE_KEYS,
                "api_key_configured": has_key(store, engine),
                "usable": usable(store, engine, config["enabled"]),
                "endpoint": (
                    _YOU_KEYED_ENDPOINT
                    if engine == "you" and has_key(store, engine)
                    else ENGINE_ENDPOINTS[engine]
                ),
            }
            for engine in config["order"]
        ],
    }
