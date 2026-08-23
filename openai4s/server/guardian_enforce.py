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
from collections.abc import Mapping, Sequence
from typing import Any

from openai4s.host.files import is_credential_path

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


# Permission targets are not uniformly paths: glob/grep target their pattern,
# and web_download targets its domain. Only inspect the argument that the file
# tool will actually resolve or open.
_FILE_PATH_ARGUMENTS = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "glob": "path",
    "grep": "path",
    "list_dir": "path",
    "web_download": "path",
    "save_artifact": "path",
    "materialise_artifact": "filename",
}
_DIRECT_PATH_TARGET_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "save_artifact",
    }
)
# These tools write to a path that is not their permission target. If their
# canonical arguments do not expose that path, an unattended reviewer cannot
# safely infer it from the domain/version target and must fail closed.
_PATH_REQUIRED_FOR_REVIEW = frozenset({"web_download", "materialise_artifact"})
# ``grep`` discovers and opens files only after approval. A base directory is
# not enough to apply the unattended basename tier to every eventual read, so
# its data-dependent file set needs a human review.
_DYNAMIC_FILE_READ_TOOLS = frozenset({"grep"})


def _file_path_argument(
    tool: str,
    canonical_arguments: Any,
    *,
    target: str,
) -> str | None:
    key = _FILE_PATH_ARGUMENTS.get(tool)
    if key is None:
        return None
    arguments = canonical_arguments
    if isinstance(arguments, (list, tuple)):
        arguments = arguments[0] if arguments else None
    if isinstance(arguments, Mapping):
        value = arguments.get(key)
        if value not in (None, ""):
            return str(value)
    # These tools use their path itself as the permission target, so it is a
    # safe fail-closed fallback when canonical arguments are missing/malformed.
    # Never do this for glob/grep (pattern targets) or web_download (domain).
    if tool in _DIRECT_PATH_TARGET_TOOLS and target:
        return target
    return None


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
        configured = str(getattr(auto, "approvals_reviewer", "") or "")
        # A real ``AutoModeConfig`` distinguishes its built-in ``user`` default
        # from an operator selection. The default must not mask the legacy
        # headless ``OPENAI4S_UNATTENDED_APPROVAL=auto_review`` escape hatch;
        # a durable per-conversation selection still wins above. Lightweight
        # config doubles have no explicitness metadata, so their value is the
        # only expressed selection and remains authoritative.
        explicit_fields = getattr(auto, "deployment_explicit_fields", None)
        if (
            explicit_fields is None
            or bool(getattr(auto, "enabled", False))
            or ("approvals_reviewer" in explicit_fields or "preset" in explicit_fields)
        ):
            selection = configured
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


def denial_circuit_open(
    history: Sequence[bool],
    *,
    config: Any | None = None,
) -> bool:
    """Return whether durable Guardian decisions have opened the breaker.

    ``True`` entries are policy denials and ``False`` entries are successful
    Guardian approvals.  Structural refusals are intentionally absent from the
    sequence, matching :class:`_DenialCircuit`: a tool outside the unattended
    allowlist is not evidence of a denial *loop*.  Computing from the durable
    permission-request history makes restart a read, not a budget reset.
    """

    consecutive_limit = _budget(
        config, "guardian_consecutive_denial_limit", DEFAULT_CONSECUTIVE_DENIAL_LIMIT
    )
    window_size = _budget(config, "guardian_window_size", DEFAULT_WINDOW_SIZE)
    window_limit = _budget(
        config, "guardian_window_denial_limit", DEFAULT_WINDOW_DENIAL_LIMIT
    )
    bounded = [bool(value) for value in history][-window_size:]
    consecutive = 0
    for denied in reversed(bounded):
        if not denied:
            break
        consecutive += 1
    return consecutive >= consecutive_limit or sum(bounded) >= window_limit


def unattended_file_deny_reason(
    *,
    tool: str,
    target: str,
    canonical_arguments: Any = None,
    resolved_file_path: str | None = None,
    resolved_file_is_credential: bool = False,
) -> str | None:
    """Return the unattended file fence reason, independent of rule scope.

    Default workspace rules intentionally allow routine file tools. This fence
    must therefore run before an ``allow`` rule as well as while resolving an
    ``ask``; otherwise the default policy bypasses the Guardian entirely.
    """
    file_path = _file_path_argument(
        tool,
        canonical_arguments,
        target=target,
    )
    # A successful Host resolution is workspace-relative and authoritative.
    # Falling back to a raw absolute spelling would reapply credential-shaped
    # parent segments above an explicitly trusted workspace root.
    reviewed_path = resolved_file_path if resolved_file_path is not None else file_path
    if resolved_file_is_credential or (
        reviewed_path is not None and is_credential_path(reviewed_path)
    ):
        return "unattended credential policy denied access to a credential path"
    if tool in _PATH_REQUIRED_FOR_REVIEW and file_path is None:
        return "unattended file policy denied access without a reviewable path"
    if tool in _DYNAMIC_FILE_READ_TOOLS:
        return "unattended file policy denied data-dependent file search"
    return None


def decide_unattended(
    payload: Mapping[str, Any],
    *,
    canonical_arguments: Any = None,
    resolved_file_path: str | None = None,
    resolved_file_is_credential: bool = False,
    config: Any | None = None,
    approvals_reviewer: str | None = None,
    expected_digest: str | None = None,
    recomputed_digest: str | None = None,
    hard_deny: bool = False,
    audit_persisted: bool = False,
    interactive: bool = False,
    circuit_key: str | None = None,
    denial_history: Sequence[bool] | None = None,
    decision_meta: dict[str, bool] | None = None,
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
            # "user" means a HUMAN decides. When one is reachable, say nothing
            # and let the approval card do its job. When one is not, this is a
            # denial rather than a hand-back: returning None headless drops to
            # the legacy path, where OPENAI4S_UNATTENDED_APPROVAL=allow approves
            # everything -- which made the safe default more permissive than
            # opting in, and let a quarantined session auto-approve `curl | sh`.
            if interactive:
                return None
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
    if decision_meta is not None:
        decision_meta.clear()
        decision_meta.update(structural=False, circuit_open=False)
    if denial_history is not None:
        already_open = denial_circuit_open(denial_history, config=config)
    else:
        already_open = bool(key and _CIRCUIT.is_open(key))
    if already_open:
        if decision_meta is not None:
            decision_meta["circuit_open"] = True
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

        if decision_meta is not None:
            decision_meta["structural"] = bool(structural)

        if key and not structural:
            if denial_history is not None:
                opened = denial_circuit_open(
                    [*denial_history, not allow], config=config
                )
            else:
                opened = _CIRCUIT.record(
                    key,
                    denied=not allow,
                    consecutive_limit=consecutive_limit,
                    window_size=window_size,
                    window_limit=window_limit,
                )
            if decision_meta is not None:
                decision_meta["circuit_open"] = bool(opened)
            if opened and not allow:
                return False, f"{message}; guardian circuit open"
        return allow, message

    tool = str(payload.get("tool") or "")
    side_effect = str(payload.get("side_effect_class") or "")
    dangerous = bool(payload.get("dangerous"))
    file_deny_reason = unattended_file_deny_reason(
        tool=tool,
        target=str(payload.get("target") or ""),
        canonical_arguments=(
            canonical_arguments
            if canonical_arguments is not None
            else payload.get("input")
        ),
        resolved_file_path=resolved_file_path,
        resolved_file_is_credential=resolved_file_is_credential,
    )

    if file_deny_reason is not None:
        # This is a deterministic file policy, not evidence of a denial loop.
        # The broker only calls this branch headlessly; with an attached human
        # the same policy match remains an audited approval card.
        return settle(False, file_deny_reason, structural=True)
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
