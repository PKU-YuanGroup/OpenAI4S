"""The kernel routes, moved verbatim out of `Handler._api`.

First slice of the decomposition. `_api` is one method of ~2,100 lines and 261
branches; this is 220 of them, and the group was chosen because it is the only
one that could be *checked*: it owns eleven of the repo's frozen response
shapes, while `memory`, `permissions`, `connectors` and `compute` own none. A
smaller, less entangled group would have been easier and would have proved
nothing.

Nothing here was rewritten. The order of the branches, the
`store.get_frame(fid) or {}` fallback on the ten permissive routes, the
duplicate `store.get_frame` in `kernel/variables`, and the six `notebook_repl`
gates with `install` deliberately left ungated are all as they were. The only
edits are the dedent and the `return True`.

TWO POSITION DEPENDENCIES, written down because a 2,100-line if-chain is
exactly where this kind of thing hides:

* The call site must stay after the `frame_mutation` guard. That guard is the
  only write-protection on the seven mutating routes here, including the
  arbitrary-code-execution endpoint: a quarantined imported session answers 423
  because of it, and nothing in this module re-checks.
* The call site must stay after the `workbench` guard, which is what makes
  `GET /frames/{id}/execution` return 404 for an unknown session. That handler
  has no frame lookup of its own -- unlike its sibling `kernel/variables`,
  which does. Giving it one would remove this dependency; that is a behaviour
  question, so it is deliberately not bundled into a pure move.

The return value is tri-state on purpose. True means a response was emitted;
False means the path matched but the method arm did not fire, and the chain
must continue to its 404. `return bool(regex_matched)` would silently swallow
twelve wrong-method 404s into an empty response.
"""
from __future__ import annotations

import re
from typing import Any

from openai4s.server.errors import GatewayError


def handle(self, method: str, sub: str, q: dict, runner: Any, store: Any) -> bool:
    """Answer a kernel route, or report that this group does not own it.

    `q` is not optional decoration: `kernel/variables` reads the requested
    language from it, on one line out of 220. An earlier reading of this
    block's dependencies missed it, and the resulting signature raised
    NameError on that route alone -- including on the default path, since
    `q.get` is evaluated before the "python" fallback applies.
    """
    m = re.fullmatch(r"/frames/([^/]+)/execution", sub)
    if m and method == "GET":
        self._json(runner.executions.snapshot(m.group(1)))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/execute", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        pid = f.get("project_id") or "default"
        body = self._body()
        code = body.get("code") or ""
        language = str(body.get("language") or "python").lower()
        if language not in {"python", "r"}:
            self._json({"error": "language must be python or r"}, 400)
            return True
        requested_execution_id = body.get("execution_id")
        if requested_execution_id and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
            str(requested_execution_id),
        ):
            self._json({"error": "invalid execution_id"}, 400)
            return True
        job = runner.submit_repl(
            fid,
            pid,
            code,
            language=language,
            execution_id=(
                str(requested_execution_id) if requested_execution_id else None
            ),
        )
        if body.get("wait") is True:
            self._json(job.wait_result())
            return True
        snapshot = runner.executions.snapshot(fid)
        queued = next(
            (
                item
                for item in snapshot.get("queue", [])
                if item.get("execution_id") == job.execution_id
            ),
            snapshot.get("owner")
            if (snapshot.get("owner") or {}).get("execution_id") == job.execution_id
            else None,
        )
        self._json(
            {
                "status": "accepted",
                "frame_id": fid,
                "job_id": job.job_id,
                "execution_id": job.execution_id,
                "owner": job.execution_owner,
                "queue_position": (queued or {}).get("queue_position"),
            },
            202,
        )
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/restart", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        pid = f.get("project_id") or "default"
        self._json(runner.restart_kernel(fid, pid))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/stop", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        self._json(runner.stop_kernel(fid, f.get("project_id") or "default"))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/interrupt", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        body = self._body()
        owner = body.get("owner") or body.get("owner_kind")
        owner_kind = owner.get("kind") if isinstance(owner, dict) else owner
        owner_id = owner.get("id") if isinstance(owner, dict) else body.get("owner_id")
        if not body.get("execution_id") or not owner_kind or not owner_id:
            self._json(
                {
                    "ok": False,
                    "frame_id": m.group(1),
                    "error": ("execution_id, owner.kind, and owner.id are required"),
                    "reason": ("execution_id, owner.kind, and owner.id are required"),
                },
                400,
            )
            return True
        kwargs = {
            "execution_id": body.get("execution_id"),
            "owner": owner,
            "owner_id": str(owner_id),
        }
        self._json(runner.interrupt_kernel(m.group(1), **kwargs))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/start", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        self._json(runner.start_kernel(fid, f.get("project_id") or "default"))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/variables", sub)
    if m and method == "GET":
        fid = m.group(1)
        frame = store.get_frame(fid)
        if frame is None:
            raise GatewayError(404, "session not found")
        if (frame.get("root_frame_id") or fid) != fid:
            raise GatewayError(
                409,
                "variable inspection requires the current root session",
            )
        language = str((q.get("language") or ["python"])[0]).lower()
        if language not in {"python", "r"}:
            self._json({"error": "language must be python or r"}, 400)
            return True
        self._json(runner.variables.inspect(fid, language))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel", sub)
    if m and method == "GET":
        self._json(runner.kernel_status(m.group(1)))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/status", sub)
    if m and method == "GET":
        fid = m.group(1)
        self._json(
            {
                "frame_id": fid,
                "running": runner.is_running(fid),
                "kernel": runner.kernel_status(fid),
            }
        )
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/install", sub)
    if m and method == "POST":
        # NOT gated by notebook_repl: prebuilt-env package install is a
        # separate Customize → Compute affordance, not the code REPL, and
        # the global /kernel/install route is ungated too.
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        pid = f.get("project_id") or "default"
        b = self._body()
        pkgs = b.get("packages") or ([b["package"]] if b.get("package") else [])
        self._json(
            runner.install_packages(
                pkgs,
                root_frame_id=fid,
                project_id=pid,
                restart=b.get("restart", True),
            )
        )
        return True
    # prebuilt-environment selection for this session's kernel
    m = re.fullmatch(r"/frames/([^/]+)/environments", sub)
    if m and method == "GET":
        self._json(runner.list_environments(m.group(1)))
        return True
    m = re.fullmatch(r"/frames/([^/]+)/kernel/env", sub)
    if m and method == "POST":
        if not runner.cfg.notebook_repl:
            self._json(
                {
                    "error": "notebook REPL is disabled; send a message to resume the agent"
                },
                403,
            )
            return True
        fid = m.group(1)
        f = store.get_frame(fid) or {}
        pid = f.get("project_id") or "default"
        b = self._body()
        name = b.get("env") or b.get("name") or ""
        self._json(runner.set_env(fid, name, pid))
        return True
    return False
