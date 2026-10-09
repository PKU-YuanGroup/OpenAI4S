"""Coalesced, non-blocking saves of durable Web sessions to project archives."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable


class ProjectHistoryAutosave:
    """One lazy writer; producers enqueue identities without touching the DB.

    Streaming text is deliberately absent. A durable mutation queues one root,
    repeated mutations collapse into one batch, and a mutation arriving while
    a batch runs remains queued for the next pass. Flush waits for an exact
    queue generation rather than guessing that an empty queue means saved.
    """

    EVENTS = frozenset(
        {
            "frame_update",
            "artifact_created",
            "artifact_deleted",
            "cell_result",
            "cell_recorded",
            "cell_update",
            "branch_activated",
            "session_reverted",
            "session_forked",
            "review_completed",
            "auto_run_terminal",
            "completion_delivery_updated",
        }
    )
    _STATUS_PREFIX = "project_history:autosave:"

    def __init__(
        self,
        service: Any,
        store: Any,
        *,
        workspace_for: Callable[[str], Path],
        placement_for: Callable[[str], tuple[Path, str]] | None = None,
        debounce_seconds: float = 0.25,
    ) -> None:
        self.service = service
        self.store = store
        self._workspace_for = workspace_for
        self._placement_for = placement_for
        self._debounce = max(0.0, debounce_seconds)
        self._condition = threading.Condition()
        self._pending: dict[tuple[str, str], int] = {}
        self._workspaces: dict[str, tuple[Path, str]] = {}
        self._issued = 0
        self._settled = 0
        self._due = 0.0
        self._immediate = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._results: dict[str, dict[str, Any]] = {}
        self._project_results: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, dict[str, Any]] = {}
        # Initial discovery is one cheap query during composition; no worker
        # or filesystem save exists on installs with no bound project folders.
        try:
            self._enabled = {
                str(project["project_id"])
                for project in store.list_projects()
                if project.get("folder_path")
            }
        except Exception:
            self._enabled = set()

    def _enqueue(self, kind: str, identity: str, *, immediate: bool = False) -> int:
        with self._condition:
            if self._stopping or not self._enabled:
                return 0
            self._issued += 1
            if not self._pending:
                self._due = time.monotonic() + self._debounce
            self._pending[(kind, identity)] = self._issued
            self._immediate = self._immediate or immediate
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="openai4s-project-history",
                    daemon=True,
                )
                try:
                    self._thread.start()
                except Exception as error:
                    failure = self._safe_error(error)
                    self._thread = None
                    self._settled = self._issued
                    for pending_kind, pending_id in self._pending:
                        if pending_kind == "session":
                            self._results[pending_id] = failure
                    for pid in self._enabled:
                        self._project_results[pid] = failure
                        self._errors[pid] = {
                            **failure,
                            "failed_at": int(time.time() * 1000),
                        }
                    self._pending.clear()
            self._condition.notify_all()
            return self._issued

    def project_changed(self, project_id: str, folder_path: str | None) -> None:
        with self._condition:
            if folder_path:
                self._enabled.add(project_id)
            else:
                self._enabled.discard(project_id)
        if folder_path:
            self._enqueue("project", project_id)

    def schedule_session(self, root_frame_id: str) -> None:
        if root_frame_id:
            self._enqueue("session", root_frame_id)

    def schedule_project(self, project_id: str) -> None:
        self._enqueue("project", project_id)

    def observe(self, root_frame_id: str, event: dict) -> None:
        if event.get("type") in self.EVENTS:
            self.schedule_session(root_frame_id)

    def schedule_all(self) -> None:
        self._enqueue("all", "")

    def _safe_error(self, error: BaseException) -> dict[str, Any]:
        # Do not copy arbitrary OS exception strings or file contents into
        # settings/status. The service itself supplies safe detailed errors.
        return {
            "state": "error",
            "error": f"Project history autosave failed ({type(error).__name__})",
        }

    def _remember(
        self,
        project_id: str,
        result: dict[str, Any],
        *,
        component: str,
        complete: bool = False,
    ) -> None:
        """Only a successful retry of the failed component clears its error."""
        with self._condition:
            previous = self._errors.get(project_id)
            if previous is None:
                try:
                    raw = self.store.get_setting(self._STATUS_PREFIX + project_id)
                    parsed = json.loads(raw) if raw else {}
                    previous = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    previous = {}
            failures = dict(previous.get("failures") or {})
            if previous.get("state") == "error" and not failures:
                failures["project"] = {
                    key: value for key, value in previous.items() if key != "failures"
                }
            if result.get("state") == "error":
                failures[component] = {
                    "state": "error",
                    "error": str(
                        result.get("error") or "Project history autosave failed"
                    ),
                    "failed_at": int(time.time() * 1000),
                }
            elif result.get("state") in {"saved", "unchanged"}:
                if complete:
                    failures.clear()
                else:
                    failures.pop(component, None)
            if failures:
                first = next(iter(failures.values()))
                state = {**first, "failures": failures}
            else:
                state = {"state": str(result.get("state") or "saved")}
            self._errors[project_id] = state
            try:
                self.store.set_setting(
                    self._STATUS_PREFIX + project_id, json.dumps(state)
                )
            except Exception:
                # Keep the in-memory result when the DB itself is unavailable.
                pass

    def clear_error(self, project_id: str) -> None:
        """Clear after an explicit, successful full-project save only."""
        self._remember(
            project_id, {"state": "saved"}, component="project", complete=True
        )

    def status(self, project_id: str) -> dict[str, Any]:
        base = dict(self.service.status(project_id))
        if not base.get("enabled"):
            return base
        with self._condition:
            failure = self._errors.get(project_id)
        if failure is None:
            try:
                raw = self.store.get_setting(self._STATUS_PREFIX + project_id)
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict) and parsed.get("state") == "error":
                    failure = parsed
            except Exception:
                pass
        if failure and failure.get("state") == "error":
            base.update(
                {key: value for key, value in failure.items() if key != "failures"}
            )
        return base

    def _run_batch(
        self, keys: tuple[tuple[str, str], ...], workspaces: dict[str, tuple[Path, str]]
    ) -> None:
        projects: dict[str, set[str]] = {}
        complete_projects: set[str] = set()
        for kind, identity in keys:
            if kind == "all":
                for project in self.store.list_projects():
                    if project.get("folder_path"):
                        pid = str(project["project_id"])
                        complete_projects.add(pid)
                        projects.setdefault(pid, set()).update(
                            self.store.project_session_ids(pid)
                        )
            elif kind == "project":
                complete_projects.add(identity)
                projects.setdefault(identity, set()).update(
                    self.store.project_session_ids(identity)
                )
            elif kind == "settings":
                projects.setdefault(identity, set())
            else:
                frame = self.store.get_frame(identity)
                if frame is not None:
                    pid = str(frame.get("project_id") or "")
                    projects.setdefault(pid, set()).add(identity)
                else:
                    with self._condition:
                        self._results[identity] = {"state": "skipped"}
        for pid, roots in projects.items():
            project = self.store.get_project(pid)
            if project is None or not project.get("folder_path"):
                with self._condition:
                    self._project_results[pid] = {"state": "skipped"}
                    for root in roots:
                        self._results[root] = {"state": "skipped"}
                continue
            try:
                result = dict(self.service.sync_project(pid))
            except Exception as error:
                result = self._safe_error(error)
            self._remember(pid, result, component="settings")
            first_error = result if result.get("state") == "error" else None
            for root in sorted(roots):
                try:
                    placement = workspaces.get(root)
                    if placement is None:
                        if self._placement_for is not None:
                            workspace, branch_id = self._placement_for(root)
                        else:
                            branch_id = self.store.active_session_branch(root)
                            workspace = self._workspace_for(root)
                    else:
                        workspace, branch_id = placement
                    saved = dict(
                        self.service.sync_session(
                            root, workspace=workspace, branch_id=branch_id
                        )
                    )
                except Exception as error:
                    saved = self._safe_error(error)
                self._remember(pid, saved, component="session:" + root)
                with self._condition:
                    self._results[root] = saved
                if saved.get("state") == "error" and first_error is None:
                    first_error = saved
            outcome = first_error or result
            with self._condition:
                # Each root keeps its own outcome: a sibling's failure must not
                # veto deleting a session whose own snapshot was published.
                self._project_results[pid] = outcome
            if (
                pid in complete_projects
                and first_error is None
                and all(
                    self._results[root].get("state") in {"saved", "unchanged"}
                    for root in roots
                )
            ):
                self._remember(pid, outcome, component="project", complete=True)

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if not self._pending and self._stopping:
                    return
                delay = self._due - time.monotonic()
                if delay > 0 and not self._immediate and not self._stopping:
                    self._condition.wait(delay)
                    continue
                pending = self._pending
                self._pending = {}
                workspaces = self._workspaces
                self._workspaces = {}
                self._immediate = False
            try:
                self._run_batch(tuple(pending), workspaces)
            except Exception as error:
                failure = self._safe_error(error)
                with self._condition:
                    projects = tuple(self._enabled)
                    for kind, identity in pending:
                        if kind == "session":
                            self._results[identity] = failure
                    for pid in projects:
                        self._project_results[pid] = failure
                for project in projects:
                    self._remember(project, failure, component="project")
            finally:
                with self._condition:
                    self._settled = max(self._settled, max(pending.values()))
                    self._condition.notify_all()

    def _wait(self, sequence: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._settled < sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def flush_session(
        self,
        root_frame_id: str,
        *,
        workspace: Path | None = None,
        branch_id: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        with self._condition:
            if workspace is not None:
                self._workspaces[root_frame_id] = (
                    workspace,
                    branch_id or self.store.active_session_branch(root_frame_id),
                )
            sequence = self._enqueue("session", root_frame_id, immediate=True)
        if not sequence:
            return {"state": "skipped"}
        if not self._wait(sequence, timeout):
            failure = {
                "state": "error",
                "error": "Project history autosave timed out; changes are still pending",
            }
            frame = self.store.get_frame(root_frame_id)
            if frame:
                self._remember(
                    str(frame.get("project_id") or ""),
                    failure,
                    component="session:" + root_frame_id,
                )
            return failure
        with self._condition:
            return dict(self._results.get(root_frame_id) or {"state": "skipped"})

    def close(
        self,
        *,
        timeout: float = 10.0,
        workspaces: dict[str, tuple[Path, str]] | None = None,
    ) -> None:
        with self._condition:
            self._workspaces.update(workspaces or {})
            sequence = self._enqueue("all", "", immediate=True)
        finished = self._wait(sequence, timeout)
        with self._condition:
            self._stopping = True
            projects = tuple(self._enabled)
            self._condition.notify_all()
        if not finished:
            for pid in projects:
                self._remember(
                    pid,
                    {
                        "state": "error",
                        "error": "Project history autosave did not finish before daemon shutdown",
                    },
                    component="project",
                )

    def flush_project_settings(
        self, project_id: str, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        sequence = self._enqueue("settings", project_id, immediate=True)
        if not sequence:
            return {"state": "skipped"}
        if not self._wait(sequence, timeout):
            failure = {
                "state": "error",
                "error": "Project history autosave timed out; changes are still pending",
            }
            self._remember(project_id, failure, component="settings")
            return failure
        with self._condition:
            return dict(self._project_results.get(project_id) or {"state": "skipped"})

    def flush_project(
        self, project_id: str, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Save settings and every live session through their actual workspaces."""
        sequence = self._enqueue("project", project_id, immediate=True)
        if not sequence:
            return {"state": "skipped"}
        if not self._wait(sequence, timeout):
            failure = {
                "state": "error",
                "error": "Project history autosave timed out; changes are still pending",
            }
            self._remember(project_id, failure, component="project")
            return failure
        with self._condition:
            return dict(self._project_results.get(project_id) or {"state": "skipped"})
