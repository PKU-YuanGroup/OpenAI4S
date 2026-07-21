"""Evidence-review orchestration for web sessions.

The service owns Reviewer-specific state and sequencing.  The gateway remains
the adapter for HTTP, WebSocket buffering, normal turn execution, and its
concrete ``MessageJob`` / ``GatewayError`` types.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Protocol


class ReviewStore(Protocol):
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        ...

    def list_model_profiles(self) -> list[dict]:
        ...

    def add_step(self, **fields: Any) -> None:
        ...

    def update_step(self, step_id: str, **fields: Any) -> None:
        ...

    def list_artifacts(self, filters: dict) -> list[dict]:
        ...

    def resolve_artifact_path(self, artifact_id: str) -> str | None:
        ...

    def list_cells(self, root_frame_id: str) -> list[dict]:
        ...

    def step_count(self, root_frame_id: str) -> int:
        ...

    def list_steps(
        self,
        frame_id: str,
        *,
        start: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        ...

    def add_frame_tokens(
        self,
        frame_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        ...

    def get_frame(self, frame_id: str) -> dict | None:
        ...

    def update_frame(self, frame_id: str, **fields: Any) -> None:
        ...

    def message_count(self, root_frame_id: str) -> int:
        ...

    def list_messages(
        self,
        root_frame_id: str,
        *,
        start: int = 0,
        limit: int = 1000,
    ) -> list[dict]:
        ...


class ReviewState(Protocol):
    root_frame_id: str
    cancel: threading.Event
    dispatcher: Any

    def execution_barrier(self):
        ...


class ReviewJob(Protocol):
    job_id: str
    done: threading.Event
    finished_at: float | None
    thread: Any

    def finish(
        self,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        ...


EventSink = Callable[[dict], None]
ReviewStoreProvider = Callable[[], ReviewStore]


@dataclass(frozen=True)
class ReviewPorts:
    """Late-bound gateway providers used by :class:`ReviewService`."""

    state_for: Callable[[str, str], ReviewState]
    emitter_for: Callable[[str], EventSink]
    llm_config_for: Callable[[ReviewState], Any]
    review_evidence: Callable[[dict, Any], dict]
    providers: Callable[[], Mapping[str, dict]]
    clean_api_key: Callable[[Any], str]
    # A profile's api_key field holds a broker reference once migrated.
    # Reading it raw would send that reference to the provider as if it
    # were a key, failing auth in a way that looks like a bad key.
    resolve_profile_key: Callable[[Mapping[str, Any]], str]
    job_factory: Callable[[str, str], ReviewJob]
    busy_error: Callable[[int, str], Exception]
    run_reviewer: Callable[..., dict | None] | None = None
    review_config_for: Callable[[ReviewState], Any] | None = None
    artifact_excerpt: Callable[[dict], str | None] | None = None
    thread_factory: Callable[..., Any] | None = None
    event_factory: Callable[[], threading.Event] | None = None
    now: Callable[[], float] | None = None


class ReviewService:
    """Build evidence, run constrained reviews, and coordinate manual jobs."""

    _RESTORABLE_FRAME_STATUSES = frozenset(
        {"ready", "done", "failed", "cancelled", "completed", "success"}
    )

    def __init__(
        self,
        *,
        store: ReviewStore | ReviewStoreProvider,
        lock: Any,
        jobs: MutableMapping[str, ReviewJob],
        ports: ReviewPorts,
    ) -> None:
        self._store_source = store
        self.lock = lock
        self.jobs = jobs
        self.ports = ports
        self.operations: dict[str, threading.Event] = {}
        self.provider_calls: dict[str, threading.Event] = {}

    @property
    def store(self) -> ReviewStore:
        source = self._store_source
        return source() if callable(source) else source

    def _event(self) -> threading.Event:
        factory = self.ports.event_factory or threading.Event
        return factory()

    def _thread(self, **kwargs: Any):
        factory = self.ports.thread_factory or threading.Thread
        return factory(**kwargs)

    def _now(self) -> float:
        return (self.ports.now or time.time)()

    def auto_enabled(self, root_frame_id: str) -> bool:
        value = self.store.get_setting(f"review:auto:{root_frame_id}")
        if value is None:
            value = self.store.get_setting("auto_review_enabled", "0")
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def llm_config(self, state: ReviewState):
        """Resolve the Reviewer model while retaining the agent-model sentinel."""
        config = self.ports.llm_config_for(state)
        local_model = self.store.get_setting(f"review:model:{state.root_frame_id}")
        if local_model == "__agent__":
            model = None
        else:
            model = local_model
        if local_model is None and not model:
            model = self.store.get_setting("reviewer_model")
        overrides: dict = {"timeout_s": min(float(config.timeout_s), 45.0)}
        model = (model or "").strip()
        if model:
            profile = next(
                (
                    item
                    for item in self.store.list_model_profiles()
                    if str(item.get("model") or "").strip() == model
                ),
                None,
            )
            provider = str((profile or {}).get("provider") or "").strip()
            if not provider:
                provider = next(
                    (
                        name
                        for name, spec in self.ports.providers().items()
                        if str(spec.get("model") or "").strip() == model
                    ),
                    "",
                )
            overrides["model"] = model
            if provider and provider != config.provider:
                overrides["provider"] = provider
                overrides["base_url"] = str(
                    (profile or {}).get("base_url") or ""
                ).strip()
                overrides["api_key"] = self.ports.resolve_profile_key(profile or {})
            elif profile and profile.get("base_url"):
                overrides["base_url"] = str(profile["base_url"]).strip()
            if profile and self.ports.resolve_profile_key(profile):
                overrides["api_key"] = self.ports.resolve_profile_key(profile)
        try:
            return dataclasses.replace(config, **overrides)
        except Exception:  # noqa: BLE001 - preserve the agent config fallback
            return config

    @staticmethod
    def artifact_excerpt(artifact: dict) -> str | None:
        path = artifact.get("path")
        if not path or not Path(path).is_file():
            return None
        filename = str(artifact.get("filename") or path).lower()
        content_type = str(artifact.get("content_type") or "").lower()
        readable = (
            content_type.startswith("text/")
            or content_type
            in {
                "application/json",
                "application/xml",
                "application/javascript",
            }
            or filename.endswith((".md", ".txt", ".csv", ".tsv", ".json", ".py", ".r"))
        )
        if not readable:
            return None
        try:
            data = Path(path).read_bytes()[:8_000]
        except OSError:
            return None
        return data.decode("utf-8", errors="replace")

    def run(
        self,
        state: ReviewState,
        emit: EventSink,
        *,
        user_text: str,
        assistant_text: str,
        artifact_versions_before: dict[str, str | None],
        cell_count_before: int,
        step_count_before: int = 0,
        mode: str = "auto",
    ) -> dict | None:
        """Persist and stream one constrained Reviewer step."""
        root_frame_id = state.root_frame_id
        step_id = f"review-{uuid.uuid4().hex[:12]}"
        config_provider = self.ports.review_config_for or self.llm_config
        config = config_provider(state)
        self.store.add_step(
            step_id=step_id,
            frame_id=root_frame_id,
            kind="review",
            title="Reviewer",
            input={"mode": mode, "model": config.model or None},
            status="running",
        )
        emit(
            {
                "type": "step",
                "frame_id": root_frame_id,
                "step_id": step_id,
                "kind": "review",
                "title": "Reviewer",
                "input": {"mode": mode, "model": config.model or None},
                "status": "running",
            }
        )
        try:
            artifacts = self.store.list_artifacts({"root_frame_id": root_frame_id})
            changed = []
            changed_total = 0
            for artifact in artifacts:
                artifact_id = artifact.get("artifact_id") or artifact.get("id")
                if not artifact_id:
                    continue
                latest = artifact.get("latest_version_id")
                if (
                    artifact_id in artifact_versions_before
                    and artifact_versions_before[artifact_id] == latest
                ):
                    continue
                changed_total += 1
                if len(changed) >= 64:
                    continue
                resolved_path = artifact.get(
                    "path"
                ) or self.store.resolve_artifact_path(artifact_id)
                artifact_with_path = {**artifact, "path": resolved_path}
                item = {
                    "artifact_id": artifact_id,
                    "filename": artifact.get("filename"),
                    "content_type": artifact.get("content_type"),
                    "size_bytes": artifact.get("size_bytes"),
                    "latest_version_id": latest,
                    "exists": bool(resolved_path and Path(resolved_path).is_file()),
                }
                excerpt = (
                    (self.ports.artifact_excerpt or self.artifact_excerpt)(
                        artifact_with_path
                    )
                    if len(changed) < 12
                    else None
                )
                if excerpt:
                    item["excerpt"] = excerpt
                changed.append(item)

            cells = self.store.list_cells(root_frame_id)[cell_count_before:]
            execution = []
            for cell in cells[-24:]:
                execution.append(
                    {
                        "cell_index": cell.get("cell_index"),
                        "source": str(cell.get("code") or "")[:5_000],
                        "stdout": str(cell.get("stdout") or "")[:4_000],
                        "stderr": str(cell.get("stderr") or "")[:2_000],
                        "error": str(cell.get("error") or "")[:2_000],
                        "status": cell.get("status"),
                        "files_written": cell.get("files_written") or [],
                        "files_read": cell.get("files_read") or [],
                    }
                )

            tool_evidence = []
            tool_start = max(
                step_count_before,
                self.store.step_count(root_frame_id) - 200,
            )
            for step in self.store.list_steps(
                root_frame_id,
                start=tool_start,
                limit=200,
            )[-32:]:
                if step.get("kind") == "review":
                    continue
                tool_evidence.append(
                    {
                        "kind": step.get("kind"),
                        "title": step.get("title"),
                        "status": step.get("status"),
                        "summary": step.get("summary"),
                        "input": step.get("input"),
                        "output": step.get("output"),
                    }
                )

            evidence = {
                "user_request": user_text[:16_000],
                "final_answer": assistant_text[:24_000],
                "submitted_output": getattr(
                    getattr(state, "dispatcher", None),
                    "last_output",
                    None,
                ),
                "execution": execution,
                "tool_evidence": tool_evidence,
                "changed_artifacts": changed,
                "changed_artifact_count": changed_total,
                "omitted_artifact_count": max(0, changed_total - len(changed)),
            }
            if state.cancel.is_set():
                output = {
                    "verdict": "cancelled",
                    "provider_call": "not_started",
                }
                self.store.update_step(
                    step_id,
                    status="cancelled",
                    output=output,
                    summary="Review cancelled",
                )
                emit(
                    {
                        "type": "step_update",
                        "frame_id": root_frame_id,
                        "step_id": step_id,
                        "status": "cancelled",
                        "output": output,
                        "summary": "Review cancelled",
                    }
                )
                return None

            review_done = self._event()
            review_cancelled = self._event()
            review_box: dict = {}
            with self.lock:
                previous = self.provider_calls.get(root_frame_id)
                if previous is not None and not previous.is_set():
                    raise RuntimeError("a previous review call is still finishing")
                self.provider_calls[root_frame_id] = review_done

            def invoke_review() -> None:
                try:
                    review_box["result"] = self.ports.review_evidence(
                        evidence,
                        config,
                    )
                except Exception as review_error:  # noqa: BLE001
                    review_box["error"] = review_error
                finally:
                    review_done.set()
                    with self.lock:
                        if self.provider_calls.get(root_frame_id) is review_done:
                            self.provider_calls.pop(root_frame_id, None)
                    if review_cancelled.is_set():
                        finished_output = {
                            "verdict": "cancelled",
                            "provider_call": "finished",
                        }
                        try:
                            self.store.update_step(
                                step_id,
                                status="cancelled",
                                output=finished_output,
                                summary="Review cancelled",
                            )
                            emit(
                                {
                                    "type": "step_update",
                                    "frame_id": root_frame_id,
                                    "step_id": step_id,
                                    "status": "cancelled",
                                    "output": finished_output,
                                    "summary": "Review cancelled",
                                }
                            )
                        except Exception:  # noqa: BLE001 - best-effort cleanup
                            pass

            self._thread(
                target=invoke_review,
                name=f"openai4s-review-call-{root_frame_id}",
                daemon=True,
            ).start()
            while not review_done.wait(0.2):
                if state.cancel.is_set():
                    review_cancelled.set()
                    output = {
                        "verdict": "cancelled",
                        "provider_call": "finishing",
                    }
                    self.store.update_step(
                        step_id,
                        status="cancelled",
                        output=output,
                        summary=("Review cancelled · provider request finishing"),
                    )
                    emit(
                        {
                            "type": "step_update",
                            "frame_id": root_frame_id,
                            "step_id": step_id,
                            "status": "cancelled",
                            "output": output,
                            "summary": (
                                "Review cancelled · provider request finishing"
                            ),
                        }
                    )
                    return None

            if review_box.get("error") is not None:
                raise review_box["error"]
            result = review_box["result"]
            result["reviewed_artifacts"] = [
                artifact["artifact_id"] for artifact in changed
            ]
            usage = result.get("usage") or {}
            self.store.add_frame_tokens(
                root_frame_id,
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
            )
            summary = result.get("summary") or "No issues found"
            self.store.update_step(
                step_id,
                status="done",
                output=result,
                summary=summary,
            )
            emit(
                {
                    "type": "step_update",
                    "frame_id": root_frame_id,
                    "step_id": step_id,
                    "status": "done",
                    "output": result,
                    "summary": summary,
                }
            )
            return result
        except Exception as error:  # noqa: BLE001 - review never fails main work
            output = {"error": str(error)[:500], "verdict": "unavailable"}
            self.store.update_step(
                step_id,
                status="error",
                output=output,
                summary="Review unavailable",
            )
            emit(
                {
                    "type": "step_update",
                    "frame_id": root_frame_id,
                    "step_id": step_id,
                    "status": "error",
                    "output": output,
                    "summary": "Review unavailable",
                }
            )
            return None

    def cancel(self, root_frame_id: str) -> None:
        """Reserve cancellation even before the session state is available."""
        with self.lock:
            self.cancel_locked(root_frame_id)

    def cancel_locked(self, root_frame_id: str) -> None:
        """Set a pending operation's signal while the shared lock is held."""
        pending = self.operations.get(root_frame_id)
        if pending is not None:
            pending.set()

    def call_inflight(self, root_frame_id: str) -> bool:
        """Return whether an operation or uncancellable provider call remains."""
        with self.lock:
            if root_frame_id in self.operations:
                return True
            provider_call = self.provider_calls.get(root_frame_id)
            return bool(provider_call is not None and not provider_call.is_set())

    def submit(self, root_frame_id: str, project_id: str) -> ReviewJob:
        """Run an on-demand Reviewer without adding conversation messages."""
        job = self.ports.job_factory(
            f"review-job-{uuid.uuid4().hex[:12]}",
            root_frame_id,
        )
        operation_cancel = self._event()
        with self.lock:
            provider_call = self.provider_calls.get(root_frame_id)
            if root_frame_id in self.operations or (
                provider_call is not None and not provider_call.is_set()
            ):
                raise self.ports.busy_error(
                    409,
                    "a previous review call is still finishing",
                )
            done = [
                job_id
                for job_id, existing in self.jobs.items()
                if existing.done.is_set()
                and (self._now() - (existing.finished_at or 0)) > 300
            ]
            for job_id in done:
                self.jobs.pop(job_id, None)
            self.operations[root_frame_id] = operation_cancel
            self.jobs[job.job_id] = job

        def target() -> None:
            emit = self.ports.emitter_for(root_frame_id)
            frame_status = "ready"
            job_result: dict | None = None
            job_error: str | None = None
            try:
                state = self.ports.state_for(root_frame_id, project_id)
                with state.execution_barrier():
                    current_frame = self.store.get_frame(root_frame_id) or {}
                    frame_status = str(current_frame.get("status") or "ready")
                    if frame_status not in self._RESTORABLE_FRAME_STATUSES:
                        frame_status = "ready"
                        self.store.update_frame(
                            root_frame_id,
                            status=frame_status,
                        )
                    if operation_cancel.is_set():
                        state.cancel.set()
                    emit(
                        {
                            "type": "frame_update",
                            "frame_id": root_frame_id,
                            "status": "processing",
                        }
                    )
                    branch_reader = getattr(self.store, "list_branch_messages", None)
                    active_branch = getattr(self.store, "active_session_branch", None)
                    if callable(branch_reader) and callable(active_branch):
                        messages = branch_reader(
                            root_frame_id,
                            branch_id=active_branch(root_frame_id),
                            limit=None,
                        )[-1000:]
                    else:
                        message_count = self.store.message_count(root_frame_id)
                        messages = self.store.list_messages(
                            root_frame_id,
                            start=max(0, message_count - 1000),
                            limit=1000,
                        )
                    last_user = max(
                        (
                            index
                            for index, message in enumerate(messages)
                            if message.get("role") == "user"
                        ),
                        default=-1,
                    )
                    user_text = (
                        str(messages[last_user].get("content") or "")
                        if last_user >= 0
                        else ""
                    )
                    assistant_text = "\n\n".join(
                        str(message.get("content") or "")
                        for message in messages[last_user + 1 :]
                        if message.get("role") == "assistant"
                    ).strip()
                    reviewer = self.ports.run_reviewer or self.run
                    result = reviewer(
                        state,
                        emit,
                        user_text=user_text,
                        assistant_text=assistant_text,
                        artifact_versions_before={},
                        cell_count_before=0,
                        step_count_before=0,
                        mode="manual",
                    )
                    job_status = "cancelled" if state.cancel.is_set() else "completed"
                    job_result = {
                        "status": job_status,
                        "frame_id": root_frame_id,
                        "review": result,
                    }
            except Exception as error:  # noqa: BLE001
                job_error = str(error)
            finally:
                try:
                    emit(
                        {
                            "type": "frame_update",
                            "frame_id": root_frame_id,
                            "status": frame_status,
                        }
                    )
                except Exception:
                    pass
                with self.lock:
                    if self.operations.get(root_frame_id) is operation_cancel:
                        self.operations.pop(root_frame_id, None)
                job.finish(result=job_result, error=job_error)

        thread = self._thread(
            target=target,
            name=f"openai4s-review-{root_frame_id}",
            daemon=True,
        )
        job.thread = thread
        try:
            thread.start()
        except Exception:
            with self.lock:
                if self.operations.get(root_frame_id) is operation_cancel:
                    self.operations.pop(root_frame_id, None)
                self.jobs.pop(job.job_id, None)
            raise
        return job


__all__ = ["ReviewPorts", "ReviewService"]
