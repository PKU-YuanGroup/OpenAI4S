"""Stage 7 Guardian enforcement for unattended ``ask`` resolutions.

Only ``allow_once`` is ever issued, and only when EVERY precondition holds:
the action is not ``dangerous``, no hard deny applies, the tool AND its
side-effect class are both on the explicit allowlist below, the durable action
digest matches exactly, the durable audit row already exists, and the denial
circuit for this conversation is closed. Anything else -- including "we could
not tell" -- denies. Guardian still cannot create a standing allow.

The allowlist is deliberately read-only. An operator who genuinely needs an
unattended write, network call, or shell command is expected to establish a
narrow standing policy *before* the run, which `PermissionBroker.gate` consults
first; the model is never the thing that widens its own authority mid-run.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from typing import Any

from openai4s.server.guardian_shadow import assess_shadow, exact_action_envelope

#: Tools an unattended Guardian may auto-approve. Read-only surfaces only.
ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "read_text_file",
        "list_dir",
        "glob_files",
        "content_search",
        "get_artifact_metadata",
        "list_artifacts",
        "list_artifact_versions",
        "lineage_get",
        "lineage_graph",
        "query",
        "query_schema",
    }
)

#: Side-effect classes an unattended Guardian may auto-approve. A tool must
#: satisfy BOTH this and :data:`ALLOWED_TOOLS`; an empty/unknown class denies,
#: because an action whose effect we cannot name is not one we can bound.
ALLOWED_SIDE_EFFECTS = frozenset({"read", "read_only", "none"})

#: Plan defaults. Overridden per-run by ``config.auto_mode`` when supplied.
DEFAULT_CONSECUTIVE_DENIAL_LIMIT = 3
DEFAULT_WINDOW_SIZE = 50
DEFAULT_WINDOW_DENIAL_LIMIT = 10


class _DenialCircuit:
    """Per-conversation denial counters. Opening the circuit is terminal.

    Explicit policy denials and infrastructure failures are counted separately,
    per the plan: a timeout does not prove the action was dangerous, but a run
    of them must still terminate rather than retry forever.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive: dict[str, int] = {}
        self._window: dict[str, list[bool]] = {}
        self._open: set[str] = set()

    def is_open(self, key: str) -> bool:
        with self._lock:
            return key in self._open

    def record(
        self,
        key: str,
        *,
        denied: bool,
        consecutive_limit: int,
        window_size: int,
        window_limit: int,
    ) -> bool:
        """Record one decision. Returns True when the circuit is now open."""

        with self._lock:
            window = self._window.setdefault(key, [])
            window.append(denied)
            if len(window) > window_size:
                del window[: len(window) - window_size]
            if denied:
                self._consecutive[key] = self._consecutive.get(key, 0) + 1
            else:
                # One non-denial resets the consecutive count, but never the
                # window: a run that alternates allow/deny still terminates.
                self._consecutive[key] = 0
            if (
                self._consecutive.get(key, 0) >= consecutive_limit
                or sum(1 for item in window if item) >= window_limit
            ):
                self._open.add(key)
            return key in self._open

    def reset(self, key: str) -> None:
        with self._lock:
            self._consecutive.pop(key, None)
            self._window.pop(key, None)
            self._open.discard(key)


_CIRCUIT = _DenialCircuit()


def circuit() -> _DenialCircuit:
    """The process-wide denial circuit (exposed for tests and for reset)."""

    return _CIRCUIT


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def feature_enabled(config: Any | None = None) -> bool:
    if config is not None:
        flags = getattr(config, "roadmap_features", None)
        if flags is not None:
            return bool(getattr(flags, "stage7_guardian_enforcement", False))
    return _flag(os.environ.get("OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", ""))


def auto_review_requested(config: Any | None = None) -> bool:
    """Whether this run asked for Guardian adjudication of ``ask`` decisions.

    The durable per-frame selection wins when it is available: a session whose
    ``approvals_reviewer`` is ``user`` -- which is what session import and the
    legacy ``review:auto:*`` migration force -- must not be auto-approved just
    because the daemon process was started with the environment variable set.
    """

    if config is not None:
        auto = getattr(config, "auto_mode", None)
        selection = str(getattr(auto, "approvals_reviewer", "") or "")
        if selection:
            return selection == "auto_review"
    return os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower() == (
        "auto_review"
    )


def _budget(config: Any | None, name: str, fallback: int) -> int:
    auto = getattr(config, "auto_mode", None) if config is not None else None
    budgets = getattr(auto, "budgets", None) if auto is not None else None
    try:
        value = int(getattr(budgets, name, fallback) or fallback)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def decide_unattended(
    payload: Mapping[str, Any],
    *,
    config: Any | None = None,
    expected_digest: str | None = None,
    hard_deny: bool = False,
    audit_persisted: bool = False,
    circuit_key: str | None = None,
) -> tuple[bool, str] | None:
    """Return (allow, message) or None to keep the legacy unattended path.

    ``expected_digest`` is the durable ``action_digest`` of the request row.
    Without it there is nothing binding the approval to a specific action, so
    the absence of a digest is a denial, not a bypass.
    """

    if not feature_enabled(config) or not auto_review_requested(config):
        return None

    key = str(circuit_key or payload.get("frame_id") or "")
    consecutive_limit = _budget(
        config, "guardian_consecutive_denial_limit", DEFAULT_CONSECUTIVE_DENIAL_LIMIT
    )
    window_size = _budget(config, "guardian_window_size", DEFAULT_WINDOW_SIZE)
    window_limit = _budget(
        config, "guardian_window_denial_limit", DEFAULT_WINDOW_DENIAL_LIMIT
    )
    if key and _CIRCUIT.is_open(key):
        return False, "guardian circuit open: blocked_by_guardian"

    def settle(allow: bool, message: str) -> tuple[bool, str]:
        if key:
            opened = _CIRCUIT.record(
                key,
                denied=not allow,
                consecutive_limit=consecutive_limit,
                window_size=window_size,
                window_limit=window_limit,
            )
            if opened and not allow:
                return False, f"{message}; guardian circuit open"
        return allow, message

    tool = str(payload.get("tool") or "")
    side_effect = str(payload.get("side_effect_class") or "")
    dangerous = bool(payload.get("dangerous"))

    if hard_deny:
        return settle(False, "denied by an existing hard policy")
    if dangerous:
        return settle(False, "guardian never auto-approves a dangerous action")
    if not expected_digest:
        return settle(False, "no durable action digest to bind the approval to")
    if not audit_persisted:
        return settle(False, "approval audit is not durable yet")
    if tool not in ALLOWED_TOOLS or side_effect not in ALLOWED_SIDE_EFFECTS:
        return settle(
            False,
            f"{tool or 'action'} is not on the unattended allowlist; "
            "establish a narrow standing policy before the run",
        )

    envelope = exact_action_envelope(
        tool=tool,
        target=str(payload.get("target") or ""),
        canonical_arguments=payload.get("canonical_arguments"),
        side_effect_class=side_effect,
        resource_keys=list(payload.get("resource_keys") or ()),
        dangerous=dangerous,
    )
    assessment = assess_shadow(
        envelope,
        expected_digest=None,
        requested_scope="once",
        hard_deny=hard_deny,
    )
    if (
        assessment.get("outcome") != "shadow_allow"
        or assessment.get("standing_allow") is not False
        or assessment.get("fail_closed")
    ):
        return settle(False, str(assessment.get("rationale") or "guardian denied"))
    return settle(True, "guardian allow_once for exact action " + expected_digest[:12])


def note_deadline_exceeded(started_at: float, budget_s: float = 90.0) -> bool:
    """Whether one Guardian adjudication has outrun its shared deadline."""

    return (time.time() - started_at) > budget_s
