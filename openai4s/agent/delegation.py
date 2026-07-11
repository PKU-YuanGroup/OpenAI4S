"""Delegation + supervision/steering.

`host.delegate(request, wait=...)` spawns sub-agents that run the same
Code-as-Action loop. Faithful features:

  leafness......... a sub-agent's depth is tracked; once depth reaches
                    MAX_DEPTH it becomes a leaf (allow_delegate=False) so
                    recursion is bounded. We support MULTI-LEVEL delegation
                    up to MAX_DEPTH (not just single-level).
  wait=True / False..... blocking returns results directly; async returns child
                         handles ({child_id, name, status}) to collect later.
  supervision... children / collect / stop_child / send_message
                 / delegation_stats. Communication topology is limited
                 to DIRECT parent<->child only.
  output_schema......... when set, each child must submit_output matching it;
                         a mismatch marks the child failed.
  frames................ every delegate spawns a child frame under the parent
                         frame, and prints a `[delegate] frame_id=...` steer
                         line so the transcript shows the subtree.

Caps: FANOUT_CAP (per call), SESSION_CAP (whole session). A list request
dispatches all children concurrently (up to FANOUT_CAP); no extra throttle.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from openai4s.config import Config

FANOUT_CAP = 48
SESSION_CAP = 1000
MAX_DEPTH = 4  # leafness bound: children beyond this cannot delegate


class DelegationError(RuntimeError):
    pass


class _Child:
    """A tracked sub-agent (running or finished)."""

    __slots__ = (
        "child_id",
        "name",
        "spec",
        "status",
        "result",
        "future",
        "stop_event",
        "inbox",
        "error",
    )

    def __init__(self, child_id: str, name: str | None, spec: dict):
        self.child_id = child_id
        self.name = name
        self.spec = spec
        self.status = "pending"  # pending|running|done|failed|stopped
        self.result: dict | None = None
        self.future: Future | None = None
        self.stop_event = threading.Event()
        self.inbox: list[str] = []  # messages steered in from the parent
        self.error: str | None = None

    def snapshot(self) -> dict:
        return {
            "child_id": self.child_id,
            "name": self.name,
            "status": self.status,
            "output": (self.result or {}).get("output"),
            "error": self.error,
        }


class DelegationRunner:
    """Wired into the host dispatcher as `delegate_fn`. One per (agent, frame)."""

    def __init__(
        self,
        cfg: Config,
        child_max_turns: int | None = None,
        depth: int = 0,
        parent_frame_id: str | None = None,
        store: Any | None = None,
    ):
        self.cfg = cfg
        self.child_max_turns = child_max_turns
        self.depth = depth
        self.parent_frame_id = parent_frame_id
        self.store = store
        self._spawned = 0
        self._lock = threading.Lock()
        self._children: dict[str, _Child] = {}
        self._seq = 0
        self._pool = ThreadPoolExecutor(max_workers=FANOUT_CAP)

    # --- capacity --------------------------------------------------------
    def _reserve(self, n: int) -> None:
        with self._lock:
            if self._spawned + n > SESSION_CAP:
                raise DelegationError(
                    f"session spawn cap reached ({SESSION_CAP}); "
                    f"already spawned {self._spawned}, requested {n}"
                )
            self._spawned += n

    def _new_child_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"child-{self.depth}-{self._seq}"

    # --- child execution -------------------------------------------------
    def _run_one(self, child: _Child) -> dict:
        from openai4s.agent.loop import Agent

        child.status = "running"
        if child.stop_event.is_set():
            child.status = "stopped"
            child.result = {
                "stop_reason": "stopped",
                "output": None,
                "completion_bullets": [],
            }
            return child.result

        spec = child.spec
        task = _spec_to_task(spec)
        # leafness: a child at MAX_DEPTH-1 spawns leaves that cannot delegate.
        child_depth = self.depth + 1
        can_delegate = child_depth < MAX_DEPTH
        # child frame under the parent, printed as a steer line.
        child_frame_id = None
        if self.store is not None:
            child_frame_id = self.store.new_frame(
                parent_id=self.parent_frame_id,
                kind="delegate",
                name=spec.get("name") or child.child_id,
                model=self.cfg.llm.model,
                depth=child_depth,
            )
            print(
                f"[delegate] frame_id={child_frame_id} "
                f"child={child.child_id} depth={child_depth} "
                f"leaf={not can_delegate}"
            )

        try:
            agent = Agent(
                cfg=self.cfg,
                max_turns=self.child_max_turns or self.cfg.max_turns,
                verbose=False,
                use_skills=True,
                allow_delegate=can_delegate,
                frame_id=child_frame_id,
                delegate_depth=child_depth,
            )
            if child.inbox:
                task += "\n\nSteering messages from parent:\n" + "\n".join(
                    f"- {m}" for m in child.inbox
                )
            result = agent.run(task)
        except Exception as e:  # noqa: BLE001
            child.status = "failed"
            child.error = str(e)
            child.result = {
                "stop_reason": "error",
                "output": None,
                "completion_bullets": [],
                "error": str(e),
            }
            if self.store is not None and child_frame_id:
                self.store.update_frame(child_frame_id, status="failed")
            return child.result

        submitted = result.get("submitted_output") or {}
        out = {
            "child_id": child.child_id,
            "name": spec.get("name"),
            "stop_reason": result.get("stop_reason"),
            "output": submitted.get("output"),
            "completion_bullets": submitted.get("completion_bullets", []),
            "final_message": result.get("final_message"),
            "frame_id": child_frame_id,
        }
        # output_schema enforcement
        schema = spec.get("output_schema")
        if schema is not None:
            from openai4s.host.completion import validate_output_schema

            verr = validate_output_schema(out["output"], schema)
            if verr:
                child.status = "failed"
                child.error = f"output_schema violation: {verr}"
                out["error"] = child.error
                child.result = out
                if self.store is not None and child_frame_id:
                    self.store.update_frame(child_frame_id, status="failed")
                return out

        child.status = "done"
        child.result = out
        if self.store is not None and child_frame_id:
            self.store.update_frame(child_frame_id, status="done")
        return out

    # --- dispatcher entrypoint ------------------------------------------
    def __call__(self, spec: dict) -> Any:
        request = spec.get("request")
        wait = spec.get("wait", True)
        if isinstance(request, list):
            items, is_list = request, True
        else:
            items, is_list = [request], False

        if len(items) > FANOUT_CAP:
            raise DelegationError(
                f"delegate fanout {len(items)} exceeds cap {FANOUT_CAP}; "
                f"split into multiple waves"
            )
        self._reserve(len(items))

        children: list[_Child] = []
        for it in items:
            cspec = _normalize_item(it, spec)
            child = _Child(self._new_child_id(), cspec.get("name"), cspec)
            self._children[child.child_id] = child
            children.append(child)

        if not wait:
            # async: kick off, return handles immediately for later collect.
            for c in children:
                c.future = self._pool.submit(self._run_one, c)
            handles = [c.snapshot() for c in children]
            return handles if is_list else handles[0]

        # blocking: run all concurrently, gather results.
        if len(children) == 1:
            results = [self._run_one(children[0])]
        else:
            futs = [self._pool.submit(self._run_one, c) for c in children]
            results = [f.result() for f in futs]
        return results if is_list else results[0]

    # --- steering surface ---------------------------------------
    def children(self) -> list[dict]:
        return [c.snapshot() for c in self._children.values()]

    def collect(self, spec: dict) -> Any:
        child_ids = spec.get("child_ids")
        timeout = spec.get("timeout")
        targets = (
            list(self._children.values())
            if not child_ids
            else [self._children[i] for i in child_ids if i in self._children]
        )
        out = []
        for c in targets:
            if c.future is not None:
                try:
                    c.future.result(timeout=timeout)
                except Exception as e:  # noqa: BLE001
                    c.status = "failed"
                    c.error = str(e)
            out.append(c.result or c.snapshot())
        return out

    def stop_child(self, child_id: str) -> dict:
        c = self._children.get(child_id)
        if c is None:
            raise KeyError(f"no such child {child_id!r}")
        c.stop_event.set()
        if c.status in ("pending", "running"):
            c.status = "stopped"
        return c.snapshot()

    def send_message(self, spec: dict) -> dict:
        # direct parent->child only (topology constraint)
        child_id = spec["child_id"]
        c = self._children.get(child_id)
        if c is None:
            raise KeyError(f"no such child {child_id!r}")
        c.inbox.append(spec.get("message", ""))
        return {"ok": True, "child_id": child_id, "queued": len(c.inbox)}

    def delegation_stats(self) -> dict:
        stats = {
            "total": len(self._children),
            "running": 0,
            "done": 0,
            "failed": 0,
            "stopped": 0,
            "pending": 0,
            "spawned_session": self._spawned,
            "depth": self.depth,
        }
        for c in self._children.values():
            stats[c.status] = stats.get(c.status, 0) + 1
        return stats


def _normalize_item(item: Any, parent_spec: dict) -> dict:
    if isinstance(item, str):
        return {
            "request": item,
            "task": parent_spec.get("task"),
            "name": parent_spec.get("name"),
            "context_summary": parent_spec.get("context_summary"),
            "output_schema": parent_spec.get("output_schema"),
        }
    if isinstance(item, dict):
        d = dict(item)
        d.setdefault("task", parent_spec.get("task"))
        d.setdefault("context_summary", parent_spec.get("context_summary"))
        d.setdefault("output_schema", parent_spec.get("output_schema"))
        return d
    raise DelegationError(
        f"delegate: each request item must be str or dict, got {type(item).__name__}"
    )


def _spec_to_task(spec: dict) -> str:
    parts: list[str] = []
    if spec.get("task"):
        parts.append(str(spec["task"]))
    req = spec.get("request")
    if isinstance(req, str):
        parts.append(req)
    elif isinstance(req, dict):
        if req.get("task"):
            parts.append(str(req["task"]))
        elif req.get("prompt"):
            parts.append(str(req["prompt"]))
        else:
            parts.append(str(req))
    if spec.get("context_summary"):
        parts.append(f"\nContext from the parent agent:\n{spec['context_summary']}")
    return "\n".join(p for p in parts if p).strip() or "(no task provided)"
