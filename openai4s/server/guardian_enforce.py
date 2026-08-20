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
from collections.abc import Mapping
from typing import Any

#: Tools an unattended Guardian may auto-approve, named the way the gate
#: actually sees them: HOST METHOD names from ``GATEABLE_TOOLS``, not the
#: control-tool names. `PermissionBroker.gate` is called with the host method,
#: so an entry like ``glob_files`` or ``query`` matches nothing and is a comment
#: pretending to be policy. These four are the entire read-only surface that
#: reaches the gate.
ALLOWED_TOOLS = frozenset({"read_file", "list_dir", "glob", "grep"})

#: Side-effect classes an unattended Guardian may auto-approve. A tool must
#: satisfy BOTH this and :data:`ALLOWED_TOOLS`; an empty/unknown class denies,
#: because an action whose effect we cannot name is not one we can bound.
#:
#: ``read_only`` is the only value production emits for a read. It is NOT
#: sufficient on its own, which is why the tool allowlist above exists:
#: `web_fetch` and `web_search` are also classified ``read_only`` even though
#: they leave the machine, so a side-effect-only rule would auto-approve
#: outbound network calls.
ALLOWED_SIDE_EFFECTS = frozenset({"read_only"})

#: Plan defaults. Overridden per-run by ``config.auto_mode`` when supplied.
DEFAULT_CONSECUTIVE_DENIAL_LIMIT = 3
DEFAULT_WINDOW_SIZE = 50
DEFAULT_WINDOW_DENIAL_LIMIT = 10


class _DenialCircuit:
    """Per-conversation denial counters. Opening the circuit is terminal.

    One counter, not two. The plan asks for explicit policy denials and
    infrastructure failures to be counted separately -- a timeout does not
    prove an action was dangerous -- and this does not do that: `record` takes
    a single `denied` flag. Saying so is better than a docstring that describes
    a split the code never made. Splitting them needs an infra-failure signal
    the enforce path does not yet produce, since every denial it can currently
    reach is a policy decision.

    In-memory and process-global, so it also does not survive a restart.
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


def auto_review_requested(
    config: Any | None = None, approvals_reviewer: str | None = None
) -> bool:
    """Whether this run asked for Guardian adjudication of ``ask`` decisions.

    The durable per-conversation selection wins whenever there IS one: a session
    that session-import quarantine or the legacy ``review:auto:*`` migration
    pinned to ``user`` must not be auto-approved merely because the daemon
    process was started with the environment variable set. An empty selection
    means nobody recorded one, and only then does the environment decide.
    """

    selection = str(approvals_reviewer or "")
    if not selection and config is not None:
        auto = getattr(config, "auto_mode", None)
        selection = str(getattr(auto, "approvals_reviewer", "") or "")
    if selection:
        return selection == "auto_review"
    return os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower() == (
        "auto_review"
    )


def _budget(config: Any | None, name: str, fallback: int) -> int:
    """A configured Guardian ceiling, clamped so it can only TIGHTEN.

    The plan is explicit that these thresholds "不能由普通 project setting 无限
    放宽": a setting that raised `guardian_consecutive_denial_limit` to 1000
    would disable the breaker while still looking configured. Anything at or
    below the default is honoured; anything above it is the default.
    """

    auto = getattr(config, "auto_mode", None) if config is not None else None
    budgets = getattr(auto, "budgets", None) if auto is not None else None
    try:
        value = int(getattr(budgets, name, fallback) or fallback)
    except (TypeError, ValueError):
        return fallback
    if value <= 0:
        return fallback
    return min(value, fallback)


def decide_unattended(
    payload: Mapping[str, Any],
    *,
    config: Any | None = None,
    approvals_reviewer: str | None = None,
    expected_digest: str | None = None,
    recomputed_digest: str | None = None,
    hard_deny: bool = False,
    audit_persisted: bool = False,
    circuit_key: str | None = None,
) -> tuple[bool, str] | None:
    """Return (allow, message) or None to keep the legacy unattended path.

    ``expected_digest`` is the ``action_digest`` stored on the request row;
    ``recomputed_digest`` is that same envelope hashed again from the row's own
    fields. They are required to be equal, and both must be present. Taking a
    single digest and merely checking it is non-empty would bind the approval
    to nothing -- any string would do -- which is why the equality lives here,
    with the decision, rather than at the call site where a later edit could
    quietly drop it.
    """

    if not feature_enabled(config):
        return None
    if not auto_review_requested(config, approvals_reviewer):
        # A RECORDED "user" is a human decision that a human decides. Returning
        # None here handed the call to the legacy path, where
        # OPENAI4S_UNATTENDED_APPROVAL=allow approves everything -- so the safe
        # default was strictly more permissive than opting in, and an imported
        # session pinned to "user" by quarantine still auto-approved
        # `curl | sh`. An absent selection is different: nobody recorded one,
        # so the operator's environment remains the only expressed intent.
        if str(approvals_reviewer or "") == "user":
            return False, "this conversation requires a human approver"
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

    def settle(
        allow: bool, message: str, *, structural: bool = False
    ) -> tuple[bool, str]:
        """Record the decision and return it.

        ``structural`` marks a refusal that is a static property of the action
        -- "this tool is not on the unattended allowlist" -- rather than
        evidence of an agent pushing against policy. Counting those opened the
        circuit almost immediately: only four gate-reaching tools are
        allowlisted, so ordinary progress through the other twenty-two tripped
        a breaker meant for a denial LOOP, and the conversation then refused
        even the reads it had been allowing.
        """

        if key and not structural:
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
    if not expected_digest or not recomputed_digest:
        return settle(False, "no durable action digest to bind the approval to")
    if expected_digest != recomputed_digest:
        # The stored envelope does not hash to what its own fields say it
        # should. Something rewrote the row, or the canonicalization moved
        # under it; either way this is not the action anyone approved.
        return settle(False, "action digest mismatch")
    if not audit_persisted:
        return settle(False, "approval audit is not durable yet")
    if tool not in ALLOWED_TOOLS or side_effect not in ALLOWED_SIDE_EFFECTS:
        return settle(
            False,
            f"{tool or 'action'} is not on the unattended allowlist; "
            "establish a narrow standing policy before the run",
            structural=True,
        )

    return settle(True, "guardian allow_once for exact action " + expected_digest[:12])
