"""Which backup web-search engines run, and in what order.

Doubao (the Agent Plan key) is not in this list: when it is configured it always
answers first, and `host/search.py` decides what happens when its quota runs
out. Behind it sit the engines `webtools.web_search` knows by id — ``tavily``,
``you`` (You.com), opt-in ``parallel`` and ``builtin`` (the keyless DuckDuckGo/Bing/Mojeek chain) —
which the user can reorder and switch on or off.

One rule is enforced here and in the UI alike: at least one engine must stay
*usable*, meaning on and able to answer. Tavily counts only with a key; the
other engines need none. Without the rule a user could switch every engine off and
every search would come back empty with no hint why. Removing a key is held to
the same rule as switching its engine off.

The stored setting is a JSON object under ``search_engines``. An install that
never saved one gets the default order with the legacy engines on — the same chain
`web_search` walked before the setting existed, so an upgrade changes nothing.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from openai4s.webtools import AVAILABLE_SEARCH_ENGINES
from openai4s.webtools import SEARCH_ENGINES as DEFAULT_SEARCH_ENGINES

#: Serializes a key write with the mirror into the environment: a search that
#: read a key just before it was cleared must not put it back afterwards.
_KEY_LOCK = threading.Lock()

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
    "parallel": "https://mcp.parallel.ai/mcp",
}
#: You.com answers keyed requests from a different host.
_YOU_KEYED_ENDPOINT = "https://ydc-index.io/v1/search"


class SearchSettingsError(ValueError):
    """A submitted engine configuration is malformed or leaves nothing usable."""


def _default() -> dict[str, Any]:
    return {
        "order": list(AVAILABLE_SEARCH_ENGINES),
        "enabled": {
            engine: engine in DEFAULT_SEARCH_ENGINES
            for engine in AVAILABLE_SEARCH_ENGINES
        },
    }


def load(store) -> dict[str, Any]:
    """The stored order and on/off map, repaired to cover every known engine.

    A row written by another version may name an engine this one dropped or
    miss one it added; unknown ids are discarded and missing ones appended in
    default order. Parallel is opt-in; missing legacy engines stay switched on.
    """

    raw = store.get_setting(SETTING_KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    order = [e for e in stored.get("order") or [] if e in AVAILABLE_SEARCH_ENGINES]
    order = list(dict.fromkeys(order))
    order += [e for e in AVAILABLE_SEARCH_ENGINES if e not in order]
    enabled_raw = stored.get("enabled")
    enabled_raw = enabled_raw if isinstance(enabled_raw, dict) else {}
    enabled = {
        e: (
            enabled_raw[e]
            if isinstance(enabled_raw.get(e), bool)
            else e in DEFAULT_SEARCH_ENGINES
        )
        for e in AVAILABLE_SEARCH_ENGINES
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


def usable(store, engine: str, enabled: dict[str, bool], has=None) -> bool:
    """On and able to answer. ``has`` stands in for `has_key`, so `update` can
    ask what a pending key change would leave."""
    if not enabled.get(engine):
        return False
    return (has or has_key)(store, engine) if engine == "tavily" else True


def save(
    store, *, order: Any, enabled: Any, has=None, persist: bool = True
) -> dict[str, Any]:
    """Validate and persist a new configuration; returns what was stored.

    With ``persist=False`` nothing is written and the result is what would be
    stored; ``has`` is passed on to `usable`.
    """

    if (
        isinstance(order, list)
        and sorted(str(e) for e in order) == sorted(DEFAULT_SEARCH_ENGINES)
        and isinstance(enabled, dict)
    ):
        current = load(store)
        order = list(order)
        # Keep Parallel behind every engine that outranked it, not at its old
        # index, so a legacy reorder cannot lift it past one of them.
        stored = current["order"]
        above = set(stored[: stored.index("parallel")])
        at = max((i + 1 for i, e in enumerate(order) if e in above), default=0)
        order.insert(at, "parallel")
        enabled = {**enabled, "parallel": current["enabled"]["parallel"]}
    if (
        not isinstance(order, list)
        or not all(isinstance(e, str) for e in order)
        or sorted(order) != sorted(AVAILABLE_SEARCH_ENGINES)
    ):
        raise SearchSettingsError(
            f"order must list each of {', '.join(AVAILABLE_SEARCH_ENGINES)} exactly once"
        )
    if not isinstance(enabled, dict) or not all(
        isinstance(enabled.get(e), bool) for e in AVAILABLE_SEARCH_ENGINES
    ):
        raise SearchSettingsError("enabled must map every engine to true or false")
    config = {
        "order": list(order),
        "enabled": {e: enabled[e] for e in AVAILABLE_SEARCH_ENGINES},
    }
    if not any(
        usable(store, e, config["enabled"], has) for e in AVAILABLE_SEARCH_ENGINES
    ):
        raise SearchSettingsError(
            "at least one search engine must stay on and usable "
            "(Tavily counts only with an API key)"
        )
    if persist:
        store.set_setting(SETTING_KEY, json.dumps(config))
    return config


def engine_chain(store) -> tuple[str, ...]:
    """The engines `web_search` should try, in order (switched-on ones only)."""

    config = load(store)
    return tuple(e for e in config["order"] if config["enabled"][e])


def choose_primary(
    store, engine: str, *, has=None, persist: bool = True
) -> dict[str, Any]:
    """Make ``engine`` the first backup and switch it on (setup's one choice).

    ``builtin`` as the choice means "keyless engines only": the keyed and
    third-party engines are switched off. Any other choice leaves the rest of
    the list as it was. ``has`` and ``persist`` are passed on to `save`.
    """

    if engine not in AVAILABLE_SEARCH_ENGINES:
        raise SearchSettingsError(f"unknown search engine {engine!r}")
    config = load(store)
    order = [engine] + [e for e in config["order"] if e != engine]
    enabled = dict(config["enabled"])
    enabled[engine] = True
    if engine == "builtin":
        enabled = {e: e == "builtin" for e in AVAILABLE_SEARCH_ENGINES}
    return save(store, order=order, enabled=enabled, has=has, persist=persist)


def update(
    store,
    *,
    engine: str = "tavily",
    api_key: Any = None,
    clear_api_key: bool = False,
    primary: Any = None,
    reorder: bool = False,
    order: Any = None,
    enabled: Any = None,
) -> None:
    """Apply one settings write, all or nothing (the settings route's).

    A write may set or clear ``engine``'s key, make ``primary`` the first
    backup, and (``reorder``) store a new ``order`` and ``enabled`` map. All of
    it is checked against the configuration the write would leave before
    anything is stored, so a refused write changes nothing -- the route used to
    clear the key first and then refuse the order that came with it.

    Removing a key is held to the one-usable-engine rule like switching the
    engine off: when the engine needs its key and nothing else is usable, the
    removal is refused and the user switches another engine on first. The
    alternatives were worse: an empty chain that answers every search with
    "none reached", or switching on engines the user had turned off, which
    would send the query to providers they did not choose.
    """

    if engine not in ENGINE_KEYS:
        raise SearchSettingsError(
            f"engine must be one that takes an API key ({', '.join(ENGINE_KEYS)})"
        )
    key = ""
    if not clear_api_key and api_key is not None:
        if not isinstance(api_key, str):
            raise SearchSettingsError("api_key must be a string")
        key = api_key.strip()
    if any(ch in key for ch in ("\r", "\n", "\x00")):
        # The secret broker refuses a line break with an error of its own, which
        # this module's callers do not answer as a refusal; NUL cannot enter
        # the environment at all.
        raise SearchSettingsError("an API key must not contain a line break or NUL")
    changes_key = clear_api_key or bool(key)

    def has(store_, other: str) -> bool:
        return bool(key) if other == engine else has_key(store_, other)

    check = has if changes_key else None
    config = None
    if primary is not None:
        config = choose_primary(store, str(primary), has=check, persist=False)
    if reorder:
        config = save(store, order=order, enabled=enabled, has=check, persist=False)
    if config is None and clear_api_key:
        now = load(store)["enabled"]
        if not any(usable(store, e, now, has) for e in AVAILABLE_SEARCH_ENGINES):
            raise SearchSettingsError(
                "removing this key would leave no usable search engine; "
                "switch another engine on first"
            )
    if changes_key:
        set_key(store, engine, key)
    if config is not None:
        store.set_setting(SETTING_KEY, json.dumps(config))


#: First-run setup's one search decision: Doubao, or a backup engine first.
SETUP_CHOICES = ("doubao",) + AVAILABLE_SEARCH_ENGINES


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
    if choice not in AVAILABLE_SEARCH_ENGINES:
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
    with _KEY_LOCK:
        store.set_secret_setting(setting, key, scope=SECRET_SCOPE)
        if key:
            os.environ[env] = key
        else:
            os.environ.pop(env, None)


def load_keys_into_env(store) -> None:
    """Expose stored keys to `webtools`, unless the environment already sets
    them (an explicit export wins).

    `has_key` counts a key the broker holds; `webtools` sends only what is in
    the environment. The daemon mirrors at start, and every search mirrors
    again (`HostDispatcher._search_web`), so the two agree in any process --
    `openai4s run`, which never ran the daemon's start-up, a keychain that was
    locked then, and an operator-injected key that a clear could not unset.
    """

    for setting, env in ENGINE_KEYS.values():
        with _KEY_LOCK:
            # Stripped, as `has_key` reads it: a blank variable is no key and
            # must not hide the stored one.
            if (os.environ.get(env) or "").strip():
                continue
            try:
                value = (store.get_secret_setting(setting) or "").strip()
            except Exception:  # noqa: BLE001 - a search must not fail on one key
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
