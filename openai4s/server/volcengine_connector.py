"""Volcengine account projection and OpenAI4S model provisioning."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from openai4s.server.volcengine_arkcli import (
    _PROFILE_NAME,
    ArkCliBridge,
    ArkCliError,
    _named,
    _normalize_device_code,
    _rows,
    _text,
)

_CACHE_TTL_S = 60.0
_REGION = re.compile(r"^[a-z0-9-]{2,64}$")
_CHOICE_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class ProvisioningMaterial:
    """Private handoff from Ark CLI to the model-profile secret boundary."""

    # repr=False: the default dataclass repr would print the raw key into any
    # log or traceback that formats this object.
    api_key: str = field(repr=False)
    plan_key: str
    plan_name: str
    profile_name: str
    model: str
    region: str
    account_name: str


def _public_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "logged_in": bool(_named(payload, "logged_in", "loggedIn", "LoggedIn")),
        "auth_method": _text(
            _named(payload, "auth_method", "authMethod", "AuthMethod"), 32
        ),
        "name": _text(_named(payload, "name", "Name"), 120),
        "project_name": _text(
            _named(payload, "project_name", "projectName", "ProjectName"), 120
        ),
        "region": _text(_named(payload, "region", "Region"), 64),
        "is_root": bool(_named(payload, "is_root", "isRoot", "IsRoot")),
        "sso_expired": bool(_named(payload, "sso_expired", "ssoExpired", "SsoExpired")),
    }


def _public_plans(plans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for plan in plans[:8]:
        key = _text(plan.get("key"), 64).lower()
        if key not in {
            "agent-plan",
            "agent-plan-team",
            "coding-plan",
            "coding-plan-team",
        }:
            continue
        status = _text(plan.get("status"), 32)
        projected.append(
            {
                "key": key,
                "name": _text(plan.get("name"), 100),
                "scope": _text(plan.get("scope"), 24),
                "tier": _text(plan.get("tier"), 32),
                "status": status,
                "available": not bool(plan.get("error"))
                and status.lower() in {"effective", "running"},
            }
        )
    return projected


def _usage_error_code(value: Any) -> str:
    lowered = str(value or "").lower()
    # ``.lower()`` does not affect the Chinese seat wording (未分配席位).
    if "no seat" in lowered or "seat bound" in lowered or "席位" in lowered:
        return "seat_required"
    if "accessdenied" in lowered or "access denied" in lowered:
        return "access_denied"
    if "notlogin" in lowered or "auth" in lowered:
        return "authentication_required"
    return "unavailable" if lowered else ""


def _public_usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in _rows(payload, "items", "Items")[:8]:
        periods: list[dict[str, Any]] = []
        raw_periods = _named(item, "periods", "Periods")
        if isinstance(raw_periods, list):
            for period in raw_periods[:8]:
                if not isinstance(period, Mapping):
                    continue
                row: dict[str, Any] = {
                    "label": _text(period.get("label"), 24),
                    "reset_at": _text(period.get("reset_at"), 64),
                }
                for field in ("used", "total", "percent"):
                    value = period.get(field)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        row[field] = max(0, value)
                periods.append(row)
        error_code = _usage_error_code(item.get("error"))
        items.append(
            {
                "product": _text(
                    _named(item, "product", "key", "plan", "Product"), 64
                ).lower(),
                "name": _text(_named(item, "name", "Name"), 100),
                "scope": _text(_named(item, "scope", "Scope"), 24),
                "tier": _text(_named(item, "tier", "Tier"), 32),
                "subscribed": bool(_named(item, "subscribed", "Subscribed")),
                "periods": periods,
                "error_code": error_code,
            }
        )
    return {"items": items}


def _profile_key_state(profile: Mapping[str, Any]) -> str:
    probed = _text(profile.get("_key_state"), 32)
    if probed:
        return probed
    raw_count = _named(
        profile,
        "api_key_count",
        "apiKeyCount",
        "ApiKeyCount",
        "key_count",
        "keyCount",
    )
    if isinstance(raw_count, bool):
        return "ready" if raw_count else "key_missing"
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        has_key = _named(profile, "has_api_key", "hasApiKey", "HasApiKey")
        if isinstance(has_key, bool):
            return "ready" if has_key else "key_missing"
        return "key_check_required"
    return "ready" if count > 0 else "key_missing"


def _decorate_plan_access(
    plans: Sequence[Mapping[str, Any]],
    profiles: Sequence[Mapping[str, Any]],
    *,
    profiles_known: bool,
) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for plan in plans:
        row = dict(plan)
        matches = [
            profile
            for profile in profiles
            if _text(
                _named(profile, "type", "Type", "profile_type", "profileType"),
                64,
            ).lower()
            == row.get("key")
        ]
        if not profiles_known:
            key_state = "key_check_required"
        elif not matches:
            key_state = "profile_missing"
        elif len(matches) > 1:
            key_state = "profile_ambiguous"
        else:
            key_state = _profile_key_state(matches[0])
        row["key_state"] = key_state
        if len(matches) == 1 and matches[0].get("_key_choices"):
            row["key_choices"] = list(matches[0]["_key_choices"])
        row["has_api_key"] = (
            True
            if key_state == "ready"
            else False if key_state == "key_missing" else None
        )
        decorated.append(row)
    return decorated


def _plan_usage_rows(
    usage: Mapping[str, Any], plan_key: str
) -> list[Mapping[str, Any]]:
    """Rows that speak for one plan: tagged rows win, and an untagged
    aggregate row counts only when the plan has no tagged row of its own
    (otherwise one shared row would exhaust or seat-block every plan)."""

    rows = [item for item in usage.get("items", []) if isinstance(item, Mapping)]
    tagged = [
        item for item in rows if _text(item.get("product"), 64).lower() == plan_key
    ]
    if tagged:
        return tagged
    return [item for item in rows if not _text(item.get("product"), 64)]


def _quota_exhausted(usage: Mapping[str, Any], plan_key: str) -> bool:
    for item in _plan_usage_rows(usage, plan_key):
        for period in item.get("periods", []):
            if not isinstance(period, Mapping):
                continue
            percent = period.get("percent")
            used = period.get("used")
            total = period.get("total")
            if isinstance(percent, (int, float)) and percent >= 100:
                return True
            if (
                isinstance(used, (int, float))
                and isinstance(total, (int, float))
                and total > 0
                and used >= total
            ):
                return True
    return False


def _access_projection(
    plans: Sequence[Mapping[str, Any]],
    usage: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    *,
    profiles_error: str = "",
) -> dict[str, Any]:
    if profiles_error:
        return {
            "state": "check_failed",
            "plan_key": "",
            "has_api_key": None,
            "error_code": profiles_error,
        }
    available = [plan for plan in plans if plan.get("available")]
    if len(available) > 1:
        return {
            "state": "plan_choice_required",
            "plan_key": "",
            "has_api_key": None,
            "error_code": "",
        }
    if len(available) == 1:
        plan = available[0]
        plan_key = str(plan.get("key") or "")
        usage_rows = _plan_usage_rows(usage, plan_key)
        if any(item.get("error_code") == "seat_required" for item in usage_rows):
            state = "seat_required"
        else:
            state = str(plan.get("key_state") or "key_check_required")
            if state in {"ready", "key_check_required"} and _quota_exhausted(
                usage, plan_key
            ):
                state = "quota_exhausted"
        return {
            "state": state,
            "plan_key": plan_key,
            "has_api_key": plan.get("has_api_key"),
            "error_code": "",
        }
    platform_profiles = [
        profile
        for profile in profiles
        if _text(
            _named(profile, "type", "Type", "profile_type", "profileType"), 64
        ).lower()
        == "platform"
    ]
    if len(platform_profiles) > 1:
        return {
            "state": "check_failed",
            "plan_key": "platform",
            "has_api_key": None,
            "error_code": "ark_profile_ambiguous",
        }
    key_choice_profiles = [
        profile
        for profile in platform_profiles
        if _profile_key_state(profile) == "key_choice_required"
    ]
    if len(key_choice_profiles) == 1:
        profile = key_choice_profiles[0]
        projection = {
            "state": "key_choice_required",
            "plan_key": "platform",
            "has_api_key": True,
            "key_choices": list(profile.get("_key_choices", [])),
            "error_code": "",
        }
        # Surface a pending endpoint choice alongside the key choice; without
        # it a multi-key + multi-endpoint account can never submit both and
        # configure loops between the two 409s forever.
        if _text(profile.get("_endpoint_state"), 32) == "endpoint_choice_required":
            projection["endpoint_choices"] = list(profile.get("_endpoint_choices", []))
        return projection
    failed_profiles = [
        profile
        for profile in platform_profiles
        if _profile_key_state(profile) == "key_check_failed"
    ]
    if len(failed_profiles) == 1:
        return {
            "state": "key_check_failed",
            "plan_key": "platform",
            "has_api_key": None,
            "error_code": _text(failed_profiles[0].get("_key_error"), 64),
        }
    missing_key_profiles = [
        profile
        for profile in platform_profiles
        if _profile_key_state(profile) == "key_missing"
    ]
    if len(missing_key_profiles) == 1:
        return {
            "state": "key_missing",
            "plan_key": "platform",
            "has_api_key": False,
            "error_code": "",
        }
    ready_profiles = [
        profile
        for profile in platform_profiles
        if _profile_key_state(profile) == "ready"
    ]
    if len(ready_profiles) == 1:
        profile = ready_profiles[0]
        endpoint_state = _text(profile.get("_endpoint_state"), 32)
        if endpoint_state == "ready":
            return {
                "state": "platform_ready",
                "plan_key": "platform",
                "has_api_key": True,
                "endpoint_choice": _text(profile.get("_endpoint_choice"), 64),
                "error_code": "",
            }
        if endpoint_state == "endpoint_choice_required":
            return {
                "state": "endpoint_choice_required",
                "plan_key": "platform",
                "has_api_key": True,
                "endpoint_choices": list(profile.get("_endpoint_choices", [])),
                "error_code": "",
            }
        if endpoint_state == "endpoint_check_failed":
            return {
                "state": "endpoint_check_failed",
                "plan_key": "platform",
                "has_api_key": True,
                "error_code": _text(profile.get("_endpoint_error"), 64),
            }
        return {
            "state": "platform_endpoint_required",
            "plan_key": "platform",
            "has_api_key": True,
            "error_code": "",
        }
    if plans:
        return {
            "state": "plan_inactive",
            "plan_key": "",
            "has_api_key": None,
            "error_code": "",
        }
    return {
        "state": "no_plan",
        "plan_key": "",
        "has_api_key": False,
        "error_code": "",
    }


class VolcengineConnectorService:
    """Cache read projections and own the one in-flight SSO process."""

    def __init__(
        self,
        bridge: ArkCliBridge | None = None,
        *,
        cache_ttl_s: float = _CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.bridge = bridge or ArkCliBridge()
        self.cache_ttl_s = max(0.0, float(cache_ttl_s))
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._scan_in_flight: threading.Event | None = None
        self._cached_at = float("-inf")
        self._cached: dict[str, Any] | None = None
        # Raw probed profile rows from the latest scan (internal fields
        # included), so provisioning can reuse the inventories that scan
        # already paid for instead of re-spawning the CLI for each of them.
        self._probed_profiles: list[dict[str, Any]] = []
        self._login: dict[str, Any] = {"state": "idle"}
        self._key_choice_ids: dict[tuple[str, str], str] = {}
        self._key_choices: dict[str, tuple[str, Any]] = {}
        self._endpoint_choice_ids: dict[tuple[str, str], str] = {}
        self._endpoint_choices: dict[str, tuple[str, str]] = {}

    def _remember_key_choice(self, profile_name: str, key_id: Any) -> str:
        pair = (profile_name, json.dumps(key_id, sort_keys=True, separators=(",", ":")))
        with self._lock:
            existing = self._key_choice_ids.get(pair)
            if existing:
                return existing
            choice_id = uuid.uuid4().hex
            self._key_choice_ids[pair] = choice_id
            self._key_choices[choice_id] = (profile_name, key_id)
            if len(self._key_choices) > 128:
                oldest = next(iter(self._key_choices))
                old_profile, old_key_id = self._key_choices.pop(oldest)
                old_pair = (
                    old_profile,
                    json.dumps(old_key_id, sort_keys=True, separators=(",", ":")),
                )
                self._key_choice_ids.pop(old_pair, None)
            return choice_id

    def _remember_endpoint_choice(self, profile_name: str, endpoint_id: str) -> str:
        pair = (profile_name, endpoint_id)
        with self._lock:
            existing = self._endpoint_choice_ids.get(pair)
            if existing:
                return existing
            choice_id = uuid.uuid4().hex
            self._endpoint_choice_ids[pair] = choice_id
            self._endpoint_choices[choice_id] = pair
            if len(self._endpoint_choices) > 128:
                oldest = next(iter(self._endpoint_choices))
                old_pair = self._endpoint_choices.pop(oldest)
                self._endpoint_choice_ids.pop(old_pair, None)
            return choice_id

    def _probe_profile_keys(
        self, profiles: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve key readiness without exposing cloud key identifiers."""

        projected: list[dict[str, Any]] = []
        for raw_profile in profiles[:12]:
            profile = dict(raw_profile)
            profile_type = _text(
                _named(profile, "type", "Type", "profile_type", "profileType"), 64
            ).lower()
            name = _text(_named(profile, "name", "Name"), 160)
            if profile_type not in {
                "platform",
                "agent-plan",
                "agent-plan-team",
                "coding-plan",
                "coding-plan-team",
            } or not _PROFILE_NAME.fullmatch(name):
                projected.append(profile)
                continue
            try:
                candidates = self.bridge.api_key_inventory(name)
            except ArkCliError as error:
                profile["_key_state"] = "key_check_failed"
                profile["_key_error"] = error.code
                projected.append(profile)
                continue
            profile["_key_candidates"] = [dict(item) for item in candidates]
            selected = [item for item in candidates if item.get("selected")]
            if len(selected) == 1 or len(candidates) == 1:
                profile["_key_state"] = "ready"
            elif not candidates:
                profile["_key_state"] = "key_missing"
            else:
                profile["_key_state"] = "key_choice_required"
                profile["_key_choices"] = [
                    {
                        "id": self._remember_key_choice(name, item["id"]),
                        "name": _text(item.get("name"), 80),
                        "suffix": _text(item.get("suffix"), 8),
                    }
                    for item in candidates[:20]
                ]
            if profile_type == "platform" and candidates:
                try:
                    endpoints = self.bridge.endpoint_inventory(name)
                except ArkCliError as error:
                    profile["_endpoint_state"] = "endpoint_check_failed"
                    profile["_endpoint_error"] = error.code
                else:
                    profile["_endpoints"] = [dict(item) for item in endpoints]
                    selected_endpoints = [
                        item for item in endpoints if item.get("selected")
                    ]
                    if len(selected_endpoints) == 1 or len(endpoints) == 1:
                        chosen = (
                            selected_endpoints[0]
                            if len(selected_endpoints) == 1
                            else endpoints[0]
                        )
                        profile["_endpoint_state"] = "ready"
                        profile["_endpoint_choice"] = self._remember_endpoint_choice(
                            name, str(chosen["id"])
                        )
                    elif not endpoints:
                        profile["_endpoint_state"] = "endpoint_missing"
                    else:
                        profile["_endpoint_state"] = "endpoint_choice_required"
                        profile["_endpoint_choices"] = [
                            {
                                "id": self._remember_endpoint_choice(
                                    name, str(item["id"])
                                ),
                                "name": _text(item.get("name"), 100),
                                "suffix": str(item["id"])[-8:],
                            }
                            for item in endpoints[:20]
                        ]
            projected.append(profile)
        return projected

    def _public_login(self) -> dict[str, Any]:
        with self._lock:
            return {
                key: value
                for key, value in self._login.items()
                if key
                in {
                    "state",
                    "login_id",
                    "authorize_url",
                    "expires_at",
                    "error_code",
                    "error_detail",
                    "method",
                    "phase",
                }
            }

    def connection(self, *, force: bool = False) -> dict[str, Any]:
        while True:
            with self._lock:
                now = self._clock()
                if (
                    not force
                    and self._cached is not None
                    and now - self._cached_at < self.cache_ttl_s
                ):
                    return {
                        **self._cached,
                        "login": self._public_login(),
                        "cached": True,
                    }
                waiter = self._scan_in_flight
                if waiter is None:
                    self._scan_in_flight = owner = threading.Event()
                    break
            # Single-flight: another thread is already running the CLI scan.
            # Its result is brand new, which is fresh enough even for a forced
            # caller — running a second identical scan would only double the
            # subprocess spawns and control-plane calls.
            waiter.wait(timeout=300.0)
            with self._lock:
                if self._cached is not None:
                    return {
                        **self._cached,
                        "login": self._public_login(),
                        "cached": True,
                    }
            force = False
        try:
            return self._scan()
        finally:
            with self._lock:
                self._scan_in_flight = None
            owner.set()

    def _scan(self) -> dict[str, Any]:
        with self._lock:
            self._probed_profiles = []
        availability = self.bridge.availability()
        if not availability["installed"]:
            payload = {
                **availability,
                "state": "not_installed",
                "identity": {},
                "plans": [],
                "usage": {"items": []},
                "access": {"state": "unavailable"},
            }
            return self._cache(payload)
        try:
            identity = _public_identity(self.bridge.whoami())
        except ArkCliError as error:
            payload = {
                **availability,
                "state": "error",
                "identity": {},
                "plans": [],
                "usage": {"items": []},
                "access": {"state": "check_failed", "error_code": error.code},
                "error_code": error.code,
            }
            return self._cache(payload)
        if not identity["logged_in"]:
            payload = {
                **availability,
                "state": "expired" if identity["sso_expired"] else "disconnected",
                "identity": identity,
                "plans": [],
                "usage": {"items": []},
                "access": {"state": "unavailable"},
            }
            return self._cache(payload)

        def leg(call: Callable[[], Any], fallback: Any) -> tuple[Any, str]:
            try:
                return call(), ""
            except ArkCliError as error:
                return fallback, error.code

        # The three legs are independent CLI conversations already isolated by
        # their own error codes; running them concurrently makes the scan's
        # wall time the max of the legs instead of their sum.
        with ThreadPoolExecutor(max_workers=3) as pool:
            plans_future = pool.submit(
                leg, lambda: _public_plans(self.bridge.plans()), []
            )
            usage_future = pool.submit(
                leg, lambda: _public_usage(self.bridge.usage()), {"items": []}
            )
            profiles_future = pool.submit(
                leg, lambda: self._probe_profile_keys(self.bridge.profiles()), []
            )
        plans, plans_error = plans_future.result()
        usage, usage_error = usage_future.result()
        profiles, profiles_error = profiles_future.result()
        with self._lock:
            self._probed_profiles = [dict(profile) for profile in profiles]
        plans = _decorate_plan_access(
            plans,
            profiles,
            profiles_known=not bool(profiles_error),
        )
        payload = {
            **availability,
            "state": "connected",
            "identity": identity,
            "plans": plans,
            "usage": usage,
            "access": _access_projection(
                plans,
                usage,
                profiles,
                profiles_error=profiles_error,
            ),
            "plans_error": plans_error,
            "usage_error": usage_error,
            "profiles_error": profiles_error,
        }
        return self._cache(payload)

    def _cache(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._cached = dict(payload)
            self._cached_at = self._clock()
            return {**self._cached, "login": self._public_login(), "cached": False}

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._cached_at = float("-inf")

    def refresh(self) -> dict[str, Any]:
        """Recheck live Ark resources once and replace the cached projection."""

        # api_key_inventory and endpoint_inventory both query the control plane
        # directly.  A second profile refresh plus a second full connection scan
        # only doubled the browser wait without making this projection fresher.
        self.invalidate()
        return self.connection(force=True)

    def start_device_login(self) -> dict[str, Any]:
        now = int(self._wall_clock())
        with self._lock:
            state = self._login.get("state")
            expires_at = self._login.get("expires_at")
            still_valid = not isinstance(expires_at, int) or expires_at > now
            if state == "connecting" or (state == "awaiting_code" and still_valid):
                return self._public_login()
            # Claim the flow inside this lock scope: without the placeholder,
            # two concurrent starts both pass the idle check and both spawn
            # `arkcli auth login`, and an expired awaiting_code login would be
            # handed out forever.
            login_id = "volc-login-" + uuid.uuid4().hex[:12]
            self._login = {
                "state": "connecting",
                "login_id": login_id,
                "method": "browser_oauth",
                "phase": "starting",
            }
        try:
            result = self.bridge.login_device_start()
        except ArkCliError:
            # Release the claim: the route reports the failure, and the panel
            # renders it from the response; a stuck "connecting" would block
            # every later start.
            with self._lock:
                if self._login.get("login_id") == login_id:
                    self._login = {"state": "idle"}
            raise
        with self._lock:
            if (
                self._login.get("login_id") == login_id
                and self._login.get("state") == "connecting"
            ):
                self._login = {
                    "state": "awaiting_code",
                    "login_id": login_id,
                    "authorize_url": result["authorize_url"],
                    "expires_at": int(self._wall_clock()) + result["expires_in_sec"],
                    "method": "browser_oauth",
                    "phase": "authorization",
                }
        return self._public_login()

    def complete_device_login(self, code: Any) -> dict[str, Any]:
        cancel_event = threading.Event()
        with self._lock:
            if self._login.get("state") != "awaiting_code":
                raise ArkCliError(
                    "device_login_not_pending", "No device login is waiting for a code"
                )
            login_id = self._login.get("login_id")
            authorize_url = str(self._login.get("authorize_url") or "")
            # Validate before the state transition: a purely local paste-format
            # rejection must not destroy the still-valid pending authorization.
            normalized = _normalize_device_code(code, authorize_url)
            self._login = {
                "state": "connecting",
                "login_id": login_id,
                "method": "browser_oauth",
                "phase": "token_exchange",
                "_cancel": cancel_event,
            }
        try:
            self.bridge.login_device_complete(normalized, cancel_event=cancel_event)
            snapshot = self.connection(force=True)
            if snapshot.get("state") != "connected":
                raise ArkCliError(
                    "volcengine_login_incomplete",
                    "Volcengine login did not produce a connected account",
                )
        except ArkCliError as error:
            with self._lock:
                # Compare-and-set: a cancel that raced this exchange already
                # owns the state, and "cancelled" must not become "failed".
                if (
                    self._login.get("login_id") == login_id
                    and self._login.get("state") == "connecting"
                ):
                    self._login = {
                        "state": "failed",
                        "login_id": login_id,
                        "error_code": error.code,
                        "error_detail": _text(error.message, 240),
                        "method": "browser_oauth",
                    }
            raise
        with self._lock:
            # Same compare-and-set: an acknowledged cancellation must not flip
            # to "succeeded" after the CLI ran to completion anyway.
            if (
                self._login.get("login_id") == login_id
                and self._login.get("state") == "connecting"
            ):
                self._login = {
                    "state": "succeeded",
                    "login_id": login_id,
                    "method": "browser_oauth",
                }
        return self._public_login()

    def cancel_login(self) -> dict[str, Any]:
        with self._lock:
            login_id = self._login.get("login_id")
            cancel_event = self._login.get("_cancel")
            self._login = {"state": "cancelled", "login_id": login_id}
        if isinstance(cancel_event, threading.Event):
            # Actually stop the in-flight `arkcli auth login --code` run; the
            # runner kills the subprocess when this event is set.
            cancel_event.set()
        return self._public_login()

    def logout(self) -> dict[str, Any]:
        self.cancel_login()
        self.bridge.logout()
        self.invalidate()
        with self._lock:
            self._login = {"state": "idle"}
        return self.connection(force=True)

    def _probed_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(profile) for profile in self._probed_profiles]

    @staticmethod
    def _select_probed_key(profile_row: Mapping[str, Any] | None) -> Any:
        """Pick the key id the latest scan would auto-select, mirroring the
        bridge's own selection so no second inventory round trip is needed."""

        candidates = (profile_row or {}).get("_key_candidates")
        if not isinstance(candidates, list):
            # No probe data (live-fallback path); the bridge re-inventories.
            return None
        selected = [item for item in candidates if item.get("selected")]
        if len(selected) == 1:
            return selected[0]["id"]
        if len(candidates) == 1:
            return candidates[0]["id"]
        if not candidates:
            raise ArkCliError(
                "ark_key_missing",
                "No usable API key exists for the selected Ark profile",
            )
        raise ArkCliError(
            "ark_key_choice_required",
            "Choose which Ark API key OpenAI4S should use",
        )

    def provisioning_material(
        self,
        plan_key: Any = None,
        key_choice: Any = None,
        endpoint_choice: Any = None,
    ) -> ProvisioningMaterial:
        snapshot = self.connection(force=True)
        if snapshot.get("state") != "connected":
            raise ArkCliError(
                "volcengine_not_connected", "Connect a Volcengine account first"
            )
        # The forced scan above just probed every profile's key and endpoint
        # inventories; reuse those rows instead of re-spawning the CLI for
        # data that is seconds old. An empty snapshot (probe failure) falls
        # back to the live calls.
        probed = self._probed_snapshot()
        requested = _text(plan_key, 64).lower()

        choice = _text(key_choice, 64).lower()
        key_id = None
        if choice:
            if not _CHOICE_ID.fullmatch(choice):
                raise ArkCliError(
                    "ark_key_choice_invalid", "The selected Ark API key is invalid"
                )
            with self._lock:
                resolved_key = self._key_choices.get(choice)
            if resolved_key is None:
                raise ArkCliError(
                    "ark_key_choice_invalid",
                    "The selected Ark API key is no longer available",
                )

        if requested == "platform":
            rows = probed or [dict(profile) for profile in self.bridge.profiles()]
            profiles = [
                profile
                for profile in rows
                if _text(
                    _named(profile, "type", "Type", "profile_type", "profileType"),
                    64,
                ).lower()
                == "platform"
                and _PROFILE_NAME.fullmatch(_text(_named(profile, "name", "Name"), 160))
            ]
            if not profiles:
                raise ArkCliError(
                    "ark_profile_missing", "Ark CLI has no platform profile"
                )
            if len(profiles) > 1:
                raise ArkCliError(
                    "ark_profile_ambiguous", "Ark CLI has multiple platform profiles"
                )
            profile_row = profiles[0]
            profile_name = _text(_named(profile_row, "name", "Name"), 160)
            if choice:
                if resolved_key[0] != profile_name:
                    raise ArkCliError(
                        "ark_key_choice_invalid",
                        "The selected Ark API key is no longer available",
                    )
                key_id = resolved_key[1]

            endpoints = profile_row.get("_endpoints")
            if not isinstance(endpoints, list):
                endpoints = self.bridge.endpoint_inventory(profile_name)
            requested_endpoint = _text(endpoint_choice, 64).lower()
            selected_endpoint: dict[str, Any] | None = None
            if requested_endpoint:
                if not _CHOICE_ID.fullmatch(requested_endpoint):
                    raise ArkCliError(
                        "ark_endpoint_choice_invalid",
                        "The selected Ark endpoint is invalid",
                    )
                with self._lock:
                    resolved_endpoint = self._endpoint_choices.get(requested_endpoint)
                if resolved_endpoint is None or resolved_endpoint[0] != profile_name:
                    raise ArkCliError(
                        "ark_endpoint_choice_invalid",
                        "The selected Ark endpoint is no longer available",
                    )
                selected_endpoint = next(
                    (
                        item
                        for item in endpoints
                        if item.get("id") == resolved_endpoint[1]
                    ),
                    None,
                )
                if selected_endpoint is None:
                    raise ArkCliError(
                        "ark_endpoint_choice_invalid",
                        "The selected Ark endpoint is no longer available",
                    )
            else:
                defaults = [item for item in endpoints if item.get("selected")]
                if len(defaults) == 1:
                    selected_endpoint = defaults[0]
                elif len(endpoints) == 1:
                    selected_endpoint = endpoints[0]
                elif not endpoints:
                    raise ArkCliError(
                        "ark_endpoint_missing",
                        "No invocable Ark endpoint exists for this Project",
                    )
                else:
                    raise ArkCliError(
                        "ark_endpoint_choice_required",
                        "Choose which Ark endpoint OpenAI4S should use",
                    )
            if key_id is None:
                key_id = self._select_probed_key(profile_row)
            key = self.bridge.api_key(profile_name, key_id)
            identity = snapshot.get("identity", {})
            region = _text(identity.get("region"), 64).lower()
            if not _REGION.fullmatch(region):
                region = "cn-beijing"
            return ProvisioningMaterial(
                api_key=key,
                plan_key="platform",
                plan_name="Platform Endpoint",
                profile_name=profile_name,
                model=str(selected_endpoint["id"]),
                region=region,
                account_name=_text(identity.get("name"), 120),
            )

        available = [plan for plan in snapshot.get("plans", []) if plan["available"]]
        if requested:
            selected = next(
                (plan for plan in available if plan["key"] == requested), None
            )
            if selected is None:
                raise ArkCliError(
                    "plan_not_available", "Selected Ark plan is not available"
                )
        elif len(available) == 1:
            selected = available[0]
        elif not available:
            raise ArkCliError("plan_required", "No active Ark plan is available")
        else:
            raise ArkCliError("plan_choice_required", "Choose which Ark plan to use")
        profile_row: dict[str, Any] | None = None
        if probed:
            matches = [
                row
                for row in probed
                if _text(
                    _named(row, "type", "Type", "profile_type", "profileType"), 64
                ).lower()
                == selected["key"]
                and _PROFILE_NAME.fullmatch(_text(_named(row, "name", "Name"), 160))
            ]
            if not matches:
                raise ArkCliError(
                    "ark_profile_missing",
                    "Ark CLI has no profile for the selected plan",
                )
            if len(matches) > 1:
                raise ArkCliError(
                    "ark_profile_ambiguous",
                    "Ark CLI has multiple profiles for the selected plan",
                )
            profile_row = matches[0]
            profile_name = _text(_named(profile_row, "name", "Name"), 160)
        else:
            profile_name = self.bridge.profile_for_plan(selected["key"])
        model = self.bridge.default_model(profile_name)
        if choice:
            if resolved_key[0] != profile_name:
                raise ArkCliError(
                    "ark_key_choice_invalid",
                    "The selected Ark API key is no longer available",
                )
            key_id = resolved_key[1]
        if key_id is None:
            key_id = self._select_probed_key(profile_row)
        key = self.bridge.api_key(profile_name, key_id)
        identity = snapshot.get("identity", {})
        region = _text(identity.get("region"), 64).lower()
        if not _REGION.fullmatch(region):
            region = "cn-beijing"
        return ProvisioningMaterial(
            api_key=key,
            plan_key=selected["key"],
            plan_name=selected["name"] or selected["key"],
            profile_name=profile_name,
            model=model,
            region=region,
            account_name=_text(identity.get("name"), 120),
        )


__all__ = ["ProvisioningMaterial", "VolcengineConnectorService"]
