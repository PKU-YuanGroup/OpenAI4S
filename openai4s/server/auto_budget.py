"""Atomic Auto Mode budget admission and persistent progress circuit.

Every model / review / repair / Python-R Cell / native-tool sink reserves a
stable ``admission_id`` in one ``BEGIN IMMEDIATE`` transaction before the
action starts. A reservation is committed from durable facts, released only
when the action clearly never started, and otherwise kept ``consumed`` /
``unknown`` until a receipt is reconciled. Guardian fields are projected from
Guardian's own durable history; this module never copies those counters.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

from openai4s.config import GUARDIAN_BUDGET_FIELDS, AutoModeBudgets
from openai4s.server.guardian_enforce import (
    DEFAULT_CONSECUTIVE_DENIAL_LIMIT,
    DEFAULT_WINDOW_DENIAL_LIMIT,
    DEFAULT_WINDOW_SIZE,
)
from openai4s.server.guardian_enforce import _budget as _guardian_ceiling
from openai4s.storage.auto_mode import AutoBudgetDenied

CONSUMERS = frozenset(
    {
        "model",
        "review",
        "repair",
        "repair_turn",
        "extra_cell",
        "native_tool",
        "token",
        "repeated_finding",
    }
)
DURABLE_DELTA_KINDS = frozenset(
    {
        "artifact_version",
        "plan",
        "checkpoint",
        "evidence",
        "remote_receipt",
        "completion_delivery",
    }
)
#: Each consumer names the ONE method whose source contains its
#: ``reserve(... consumer="<name>" ...)`` call. `inspect_budget_wiring()`
#: checks for that literal, not for the substring ``auto_budget``: the
#: substring test was satisfied by a comment, a helper name, or an unrelated
#: `self._auto_budget()` in the same method, which is how `repair_turn` could
#: be registered against `AutoRepairService.run` -- a method that never
#: reserves it -- and still read as wired.
SINK_REGISTRY = {
    "review": (
        "openai4s.server.scientific_review",
        "ScientificReviewService.evaluate",
    ),
    "repair": ("openai4s.server.auto_repair", "AutoRepairService.run"),
    "model": (
        "openai4s.server.gateway",
        "SessionRunner._invoke_model_with_auto_budget",
    ),
    "extra_cell": ("openai4s.server.gateway", "SessionRunner._loop"),
    "native_tool": (
        "openai4s.server.gateway",
        "SessionRunner._invoke_control_with_auto_budget",
    ),
    "repeated_finding": ("openai4s.server.auto_repair", "AutoRepairService.run"),
    "repair_turn": ("openai4s.server.auto_repair", "RepairTurnAdmission.admit_turn"),
    "token": (
        "openai4s.server.scientific_review",
        "ScientificReviewService.evaluate",
    ),
}
#: Consumers with a published limit that nothing currently reserves against,
#: and why. An empty mapping means every CONSUMERS member is in
#: SINK_REGISTRY. `inspect_budget_wiring()` treats every key here as a
#: missing sink -- naming an unwired field is not GA-ready, it is a
#: fail-closed inventory of known holes.
UNWIRED_CONSUMERS: dict[str, str] = {}
FIELD_AUTHORITIES = {
    **{
        name: "auto_budget"
        for name in (item.name for item in fields(AutoModeBudgets))
        if name not in GUARDIAN_BUDGET_FIELDS
    },
    **{name: "guardian" for name in GUARDIAN_BUDGET_FIELDS},
}
TERMINAL_USER_TRUTH = {
    "budget_exhausted": "Paused · Budget exhausted",
    "loop_detected": "Paused/Blocked · Loop detected",
    "budget_measurement_unavailable": "无法验证 token 预算",
}
_COMPLETION_STATUSES = frozenset(
    {"verified", "completed", "completed_with_issues", "pass"}
)


def execution_action_group(base: Any, invocation_id: Any = None) -> str:
    """Identify one sink invocation within a broader action/model group.

    Provider-native call IDs are stable across response replay, which makes a
    replay hit the same single-use admission. Sibling calls have different IDs;
    sinks without a provider ID receive a fresh nonce for every attempt.
    """

    prefix = str(base or "action")
    suffix = str(invocation_id or uuid.uuid4().hex)
    return f"{prefix}:{suffix}"


def canonical_action_fingerprint(
    *,
    kind: str,
    name: str = "",
    arguments: Any = None,
    source: str | None = None,
) -> str:
    """Canonical same-action fingerprint: kind+tool+args, or Cell source digest."""

    if source is not None:
        payload: dict[str, Any] = {
            "kind": str(kind),
            "source_sha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
        }
    else:
        payload = {
            "kind": str(kind),
            "name": str(name or ""),
            "arguments": _canonical_arguments(arguments),
        }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def finding_set_digest(fingerprints: Any) -> str:
    values = sorted(
        {str(item) for item in (fingerprints or ()) if str(item or "").strip()}
    )
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verifiable_token_usage(usage: Any) -> int | None:
    """Return a non-negative token total only when the adapter usage is exact."""

    if not isinstance(usage, Mapping):
        return None
    prompt = usage.get("prompt_tokens")
    if type(prompt) is not int:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if type(completion) is not int:
        completion = usage.get("output_tokens")
    total = usage.get("total_tokens")
    if (
        type(prompt) is int
        and type(completion) is int
        and prompt >= 0
        and completion >= 0
    ):
        return prompt + completion
    if type(total) is int and total >= 0:
        return total
    return None


def token_upper_bound(
    adapter_cfg: Any,
    *,
    messages: Any = None,
    tools: Any = None,
    max_tokens: int | None = None,
) -> int | None:
    """Return a pre-provider upper bound for prompt plus completion tokens.

    ``max_tokens`` bounds only provider output and therefore cannot be used as
    a total-spend reservation. Adapters may expose an audited bound directly.
    Otherwise, for the exact JSON request, UTF-8 bytes upper-bound ordinary
    tokenizer tokens; the per-node allowance covers provider wire wrappers and
    chat control tokens. The per-attempt value is multiplied by the transport's
    audited attempt ceiling. Non-JSON request content fails closed before
    provider spend. Model-catalog context sizes are deliberately not used: a
    provider default is not proof about the exact configured endpoint.
    """

    def value(name: str) -> Any:
        if isinstance(adapter_cfg, Mapping):
            return adapter_cfg.get(name)
        return getattr(adapter_cfg, name, None)

    total = value("total_token_upper_bound")
    if type(total) is int and total > 0:
        return total
    attempts = value("provider_attempt_upper_bound")
    if attempts is None:
        # Exact production transport ceiling: first request + two bounded
        # retries. Reserving one prompt for a three-attempt transport would not
        # be a hard spend ceiling after an ambiguous/lost response.
        from openai4s.llm.transport import DEFAULT_MAX_ATTEMPTS

        attempts = DEFAULT_MAX_ATTEMPTS
    if type(attempts) is not int or attempts <= 0:
        return None
    prompt = value("input_token_upper_bound")
    completion = max_tokens if max_tokens is not None else value("max_tokens")
    if (
        type(prompt) is int
        and prompt >= 0
        and type(completion) is int
        and completion > 0
    ):
        return (prompt + completion) * attempts
    if messages is None or type(completion) is not int or completion <= 0:
        return None
    request = {"messages": messages, "tools": tools or []}
    try:
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None

    def nodes(item: Any) -> int:
        if isinstance(item, Mapping):
            return 1 + sum(nodes(child) for child in item.values())
        if isinstance(item, (list, tuple)):
            return 1 + sum(nodes(child) for child in item)
        return 1

    request_bound = (
        len(encoded) + (64 * nodes(request)) + 1024 + completion
    ) * attempts
    return request_bound


def inspect_budget_wiring() -> dict[str, Any]:
    """Return the authority/sink inventory Stage 12 GA refuses to skip."""

    missing_authorities = [
        name
        for name in (item.name for item in fields(AutoModeBudgets))
        if name not in FIELD_AUTHORITIES
    ]
    duplicate_authorities = [
        name
        for name, authority in FIELD_AUTHORITIES.items()
        if not authority or authority not in {"auto_budget", "guardian"}
    ]
    missing_sinks: list[str] = []
    for consumer, (module_name, qualname) in SINK_REGISTRY.items():
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - inventory is fail-closed
            missing_sinks.append(consumer)
            continue
        target: Any = module
        for part in qualname.split("."):
            target = getattr(target, part, None)
            if target is None:
                break
        if target is None:
            missing_sinks.append(consumer)
            continue
        try:
            source = inspect.getsource(target)
        except (OSError, TypeError):
            missing_sinks.append(consumer)
            continue
        # Structural: the registered method must itself reserve THIS consumer.
        if not re.search(
            r'consumer\s*=\s*["\']' + re.escape(consumer) + r'["\']', source
        ):
            missing_sinks.append(consumer)
    # A named-unwired consumer is still a missing sink. Publishing the
    # hole is not the same as wiring it; Stage 12 must refuse GA until
    # the key is deleted from this mapping because a real sink exists.
    for consumer in UNWIRED_CONSUMERS:
        if consumer not in missing_sinks:
            missing_sinks.append(consumer)
    for consumer in sorted(CONSUMERS - set(SINK_REGISTRY) - set(UNWIRED_CONSUMERS)):
        if consumer not in missing_sinks:
            missing_sinks.append(consumer)
    return {
        "field_authorities": dict(FIELD_AUTHORITIES),
        "missing_authorities": missing_authorities,
        "duplicate_authorities": duplicate_authorities,
        "sinks": sorted(SINK_REGISTRY),
        "missing_sinks": missing_sinks,
        "sink_bypass_count": len(missing_sinks),
        "ga_ready": not missing_authorities
        and not duplicate_authorities
        and not missing_sinks,
    }


def user_truth_for(reason: str | None) -> str | None:
    if not reason:
        return None
    return TERMINAL_USER_TRUTH.get(str(reason))


def is_completion_disguise(status: Any, reason: Any) -> bool:
    """True when a budget/loop terminal would be presented as completion."""

    if str(reason or "") not in TERMINAL_USER_TRUTH:
        return False
    return str(status or "") in _COMPLETION_STATUSES


class AutoBudgetAdmission:
    """Host-side admission envelope over the durable Auto Budget repository."""

    def __init__(self, store: Any, budgets: AutoModeBudgets | None = None) -> None:
        self.store = store
        self.budgets = budgets

    def available(self) -> bool:
        return callable(getattr(self.store, "reserve_auto_mode_budget", None))

    def token_phase_active(self, run_id: str) -> bool:
        """Whether this run has reached the phase where token ceilings bind.

        Before the extra phase a run has no frozen token limit, so an adapter
        that cannot state a prompt-plus-completion ceiling costs nothing: the
        turn proceeds and no field is being enforced against it. After it,
        the same silence means the limit cannot be enforced, and the run
        fails closed.

        Both admission sites ask this. The Web turn loop gated its token
        reservation on the predicate while the review path did not, so an
        adapter without a usable ceiling was tolerated on one path and
        paused the whole run on the other -- same missing capability, two
        answers, depending only on which sink reached it first.
        """

        if not run_id:
            return False
        raw = self.store.project_auto_mode_budget(run_id)
        if not isinstance(raw, Mapping):
            return False
        state = raw.get("state") if isinstance(raw.get("state"), Mapping) else {}
        return bool(
            raw.get("tokens_frozen")
            or int(state.get("review_rounds") or 0)
            or int(state.get("repair_rounds") or 0)
        )

    def ensure_state(
        self,
        run_id: str,
        *,
        root_run_id: str | None = None,
        initial_turn_tokens: int = 0,
    ) -> dict[str, Any] | None:
        if not callable(getattr(self.store, "ensure_auto_mode_budget_state", None)):
            return None
        return self.store.ensure_auto_mode_budget_state(
            run_id,
            root_run_id=root_run_id,
            initial_turn_tokens=initial_turn_tokens,
        )

    def reserve(
        self,
        *,
        run_id: str,
        admission_id: str,
        consumer: str,
        action_group_id: str,
        amount: int = 1,
        action_sha256: str | None = None,
        enforce_field_limit: bool = True,
        token_upper_bound: int | None = None,
    ) -> dict[str, Any]:
        if consumer not in CONSUMERS:
            raise ValueError("invalid Auto Budget consumer")
        result = self.store.reserve_auto_mode_budget(
            run_id=run_id,
            admission_id=admission_id,
            consumer=consumer,
            action_group_id=action_group_id,
            amount=amount,
            action_sha256=action_sha256,
            enforce_field_limit=enforce_field_limit,
            token_upper_bound=token_upper_bound,
        )
        # Repository replay is useful for crash inspection/reconciliation, but
        # an execution envelope must never treat an old reservation as fresh
        # authority to invoke a provider, tool, Cell, or repair a second time.
        if not bool(result.get("created")):
            raise AutoBudgetDenied(
                "loop_detected",
                "Auto Budget admission was already used",
            )
        return result

    def commit(self, admission_id: str, *, committed_amount: int) -> dict[str, Any]:
        return self.store.commit_auto_mode_budget(
            admission_id, committed_amount=committed_amount
        )

    def release(self, admission_id: str, *, started: bool) -> dict[str, Any]:
        return self.store.release_auto_mode_budget(admission_id, started=started)

    def mark_unknown(self, admission_id: str) -> dict[str, Any]:
        return self.store.mark_auto_mode_budget_unknown(admission_id)

    def reconcile(self, admission_id: str, *, committed_amount: int) -> dict[str, Any]:
        return self.store.reconcile_auto_mode_budget(
            admission_id, committed_amount=committed_amount
        )

    def record_delta(self, run_id: str, *, kind: str, cursor: str) -> dict[str, Any]:
        if kind not in DURABLE_DELTA_KINDS:
            raise ValueError("invalid Auto Budget durable delta kind")
        return self.store.record_auto_mode_budget_delta(
            run_id, kind=kind, cursor=cursor
        )

    def freeze_initial_tokens(self, run_id: str, tokens: int) -> dict[str, Any]:
        multiplier = None
        if self.budgets is not None:
            multiplier = self.budgets.extra_token_multiplier
        return self.store.freeze_auto_mode_budget_initial_tokens(
            run_id, tokens, extra_token_multiplier=multiplier
        )

    def trip(
        self, run_id: str, *, reason: str, field: str | None = None
    ) -> dict[str, Any]:
        return self.store.trip_auto_mode_budget_circuit(
            run_id, reason=reason, field=field
        )

    def fail_measurement(self, run_id: str, admission_id: str | None = None) -> None:
        if admission_id:
            self.mark_unknown(admission_id)
        self.trip(
            run_id,
            reason="budget_measurement_unavailable",
            field="extra_token_multiplier",
        )

    def project_usage(
        self,
        run_id: str,
        *,
        root_frame_id: str | None = None,
    ) -> dict[str, Any]:
        raw = None
        if callable(getattr(self.store, "project_auto_mode_budget", None)):
            raw = self.store.project_auto_mode_budget(run_id)
        if not isinstance(raw, Mapping):
            return {
                "legacy": True,
                "budget_usage": {},
                "circuit": {
                    "state": "closed",
                    "reason": None,
                    "last_delta_cursor": None,
                },
            }
        state = raw.get("state") if isinstance(raw.get("state"), Mapping) else {}
        budgets = raw.get("budgets") if isinstance(raw.get("budgets"), Mapping) else {}
        reservations = [
            item
            for item in (raw.get("reservations") or [])
            if isinstance(item, Mapping)
        ]
        usage = self._field_usage(
            state,
            budgets,
            reservations,
            tokens_frozen=bool(raw.get("tokens_frozen")),
            root_frame_id=root_frame_id,
        )
        reason = raw.get("circuit_reason")
        circuit_state = "tripped" if reason else "closed"
        return {
            "legacy": False,
            "budget_usage": usage,
            "circuit": {
                "state": circuit_state,
                "reason": reason,
                "last_delta_cursor": state.get("last_delta_cursor"),
            },
        }

    def _field_usage(
        self,
        state: Mapping[str, Any],
        budgets: Mapping[str, Any],
        reservations: list[Mapping[str, Any]],
        *,
        tokens_frozen: bool,
        root_frame_id: str | None,
    ) -> dict[str, Any]:
        def meter(
            *,
            limit: float | int,
            used: float | int,
            reserved: float | int,
            authority: str,
        ) -> dict[str, Any]:
            remaining = max(0, float(limit) - float(used) - float(reserved))
            if type(limit) is int and type(used) is int and type(reserved) is int:
                remaining_value: float | int = int(remaining)
            else:
                remaining_value = remaining
            return {
                "limit": limit,
                "used": used,
                "reserved": reserved,
                "remaining": remaining_value,
                "exhausted": remaining_value <= 0 and float(limit) >= 0,
                "authority": authority,
            }

        def _int(name: str, default: int = 0) -> int:
            value = budgets.get(name, default)
            try:
                number = int(value)
            except (TypeError, ValueError):
                return default
            if isinstance(value, bool) or number < 0:
                return default
            return number

        def _reserved(consumer: str) -> int:
            return sum(
                int(item.get("reserved_amount") or 0)
                for item in reservations
                if item.get("consumer") == consumer and item.get("state") == "reserved"
            )

        review_limit = _int("max_review_rounds")
        repair_limit = _int("max_repair_rounds")
        repair_turn_limit = _int("repair_turns_per_round")
        extra_cell_limit = _int("max_extra_cells")
        wall_limit = _int("wall_time_s")
        same_limit = _int("same_action_no_delta_limit")
        progress_limit = _int("no_progress_turn_limit")
        finding_limit = _int("repeated_finding_limit")
        started_at = int(state.get("started_at") or 0)
        now = int(state.get("started_at") or 0)
        if callable(getattr(self.store, "_auto_mode", None)):
            clock = getattr(self.store._auto_mode, "_clock_ms", None)
            if callable(clock):
                now = int(clock())
        elapsed = max(0, (now - started_at) // 1000) if started_at else 0
        token_limit = int(state.get("computed_extra_token_limit") or 0)
        token_used = sum(
            int(item.get("committed_amount") or item.get("reserved_amount") or 0)
            for item in reservations
            if item.get("consumer") == "token"
            and item.get("state") in {"committed", "consumed", "unknown"}
        )
        token_reserved = _reserved("token")
        finding_used = sum(
            int(item.get("committed_amount") or item.get("reserved_amount") or 0)
            for item in reservations
            if item.get("consumer") == "repeated_finding"
            and item.get("state") != "released"
        )
        usage = {
            "max_review_rounds": meter(
                limit=review_limit,
                used=int(state.get("review_rounds") or 0),
                reserved=_reserved("review"),
                authority="auto_budget",
            ),
            "max_repair_rounds": meter(
                limit=repair_limit,
                used=int(state.get("repair_rounds") or 0),
                reserved=_reserved("repair"),
                authority="auto_budget",
            ),
            "repair_turns_per_round": meter(
                limit=repair_turn_limit,
                used=int(state.get("repair_turns") or 0),
                reserved=_reserved("repair_turn"),
                authority="auto_budget",
            ),
            "max_extra_cells": meter(
                limit=extra_cell_limit,
                used=int(state.get("extra_cells") or 0),
                reserved=_reserved("extra_cell"),
                authority="auto_budget",
            ),
            "wall_time_s": meter(
                limit=wall_limit,
                used=min(elapsed, wall_limit) if wall_limit else elapsed,
                reserved=0,
                authority="auto_budget",
            ),
            "extra_token_multiplier": meter(
                limit=token_limit if tokens_frozen else 0,
                used=token_used,
                reserved=token_reserved,
                authority="auto_budget",
            ),
            "repeated_finding_limit": meter(
                limit=finding_limit,
                used=finding_used,
                reserved=_reserved("repeated_finding"),
                authority="auto_budget",
            ),
            "same_action_no_delta_limit": meter(
                limit=same_limit,
                used=int(state.get("same_action_streak") or 0),
                reserved=0,
                authority="auto_budget",
            ),
            "no_progress_turn_limit": meter(
                limit=progress_limit,
                used=int(state.get("no_progress_turns") or 0),
                reserved=0,
                authority="auto_budget",
            ),
        }
        if not tokens_frozen:
            usage["extra_token_multiplier"]["exhausted"] = False
        usage.update(self._guardian_usage(root_frame_id))
        return usage

    def _guardian_usage(self, root_frame_id: str | None) -> dict[str, dict[str, Any]]:
        history = self._guardian_history(root_frame_id)
        budgets = self.budgets
        consecutive_limit = _guardian_ceiling(
            type("cfg", (), {"auto_mode": type("am", (), {"budgets": budgets})()})(),
            "guardian_consecutive_denial_limit",
            DEFAULT_CONSECUTIVE_DENIAL_LIMIT,
        )
        window_size = _guardian_ceiling(
            type("cfg", (), {"auto_mode": type("am", (), {"budgets": budgets})()})(),
            "guardian_window_size",
            DEFAULT_WINDOW_SIZE,
        )
        window_limit = _guardian_ceiling(
            type("cfg", (), {"auto_mode": type("am", (), {"budgets": budgets})()})(),
            "guardian_window_denial_limit",
            DEFAULT_WINDOW_DENIAL_LIMIT,
        )
        window = list(history)[-window_size:]
        consecutive = 0
        for denied in reversed(window):
            if not denied:
                break
            consecutive += 1
        window_denials = sum(1 for item in window if item)
        timeout_limit = int(budgets.guardian_timeout_s) if budgets is not None else 90

        def guardian_meter(limit: int, used: int) -> dict[str, Any]:
            remaining = max(0, limit - used)
            return {
                "limit": limit,
                "used": used,
                "reserved": 0,
                "remaining": remaining,
                "exhausted": remaining <= 0,
                "authority": "guardian",
            }

        return {
            "guardian_timeout_s": guardian_meter(timeout_limit, 0),
            "guardian_consecutive_denial_limit": guardian_meter(
                consecutive_limit, consecutive
            ),
            "guardian_window_size": guardian_meter(window_size, len(window)),
            "guardian_window_denial_limit": guardian_meter(
                window_limit, window_denials
            ),
        }

    def _guardian_history(self, root_frame_id: str | None) -> list[bool]:
        if not root_frame_id or not callable(
            getattr(self.store, "list_permission_requests", None)
        ):
            return []
        try:
            from openai4s.permissions import guardian_denial_history

            return list(guardian_denial_history(self.store, root_frame_id))
        except Exception:  # noqa: BLE001 - projection must not fail the GET
            return []


def _canonical_arguments(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_arguments(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_arguments(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, bool) or not isinstance(value, float):
            return value
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    return str(value)


__all__ = [
    "CONSUMERS",
    "DURABLE_DELTA_KINDS",
    "FIELD_AUTHORITIES",
    "SINK_REGISTRY",
    "UNWIRED_CONSUMERS",
    "TERMINAL_USER_TRUTH",
    "AutoBudgetAdmission",
    "AutoBudgetDenied",
    "canonical_action_fingerprint",
    "finding_set_digest",
    "inspect_budget_wiring",
    "is_completion_disguise",
    "token_upper_bound",
    "user_truth_for",
    "verifiable_token_usage",
]
