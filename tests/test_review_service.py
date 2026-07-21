"""Direct contracts for Reviewer orchestration outside the gateway."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from openai4s.config import LLMConfig
from openai4s.server.reviews import ReviewPorts, ReviewService


class FakeStore:
    def __init__(self) -> None:
        self.settings = {}
        self.profiles = []
        self.frames = {"frame": {"frame_id": "frame", "status": "ready"}}
        self.artifacts = []
        self.artifact_paths = {}
        self.cells = []
        self.steps = []
        self.messages = []
        self.tokens = []
        self.message_reads = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def list_model_profiles(self):
        return self.profiles

    def add_step(self, **fields):
        self.steps.append(dict(fields))

    def update_step(self, step_id, **fields):
        step = next(item for item in self.steps if item["step_id"] == step_id)
        step.update(fields)

    def list_artifacts(self, filters):
        assert filters == {"root_frame_id": "frame"}
        return self.artifacts

    def resolve_artifact_path(self, artifact_id):
        return self.artifact_paths.get(artifact_id)

    def list_cells(self, root_frame_id):
        assert root_frame_id == "frame"
        return self.cells

    def step_count(self, root_frame_id):
        assert root_frame_id == "frame"
        return len(self.steps)

    def list_steps(self, frame_id, *, start=0, limit=200):
        assert frame_id == "frame"
        return self.steps[start : start + limit]

    def add_frame_tokens(self, frame_id, *, input_tokens, output_tokens):
        self.tokens.append((frame_id, input_tokens, output_tokens))

    def get_frame(self, frame_id):
        return self.frames.get(frame_id)

    def update_frame(self, frame_id, **fields):
        self.frames.setdefault(frame_id, {"frame_id": frame_id}).update(fields)

    def message_count(self, root_frame_id):
        assert root_frame_id == "frame"
        return len(self.messages)

    def list_messages(self, root_frame_id, *, start=0, limit=1000):
        assert root_frame_id == "frame"
        self.message_reads.append((start, limit))
        return self.messages[start : start + limit]


class FakeState:
    def __init__(self, root_frame_id="frame") -> None:
        self.root_frame_id = root_frame_id
        self.cancel = threading.Event()
        self.dispatcher = SimpleNamespace(last_output={"answer": "submitted"})

    @contextmanager
    def execution_barrier(self):
        self.cancel.clear()
        yield


class FakeJob:
    def __init__(self, job_id, root_frame_id) -> None:
        self.job_id = job_id
        self.root_frame_id = root_frame_id
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.finished_at = None
        self.thread = None

    def finish(self, result=None, error=None):
        self.result = result
        self.error = error
        self.finished_at = 1000.0
        self.done.set()

    def wait_result(self):
        assert self.done.wait(2)
        if self.result is not None:
            return self.result
        return {"error": self.error}


class BusyError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ImmediateThreads:
    def __init__(self) -> None:
        self.created = []

    def __call__(self, *, target, name, daemon):
        record = {"target": target, "name": name, "daemon": daemon}
        self.created.append(record)

        class ImmediateThread:
            def start(inner_self):
                target()

        return ImmediateThread()


class HeldThreads:
    def __init__(self) -> None:
        self.created = []

    def __call__(self, *, target, name, daemon):
        record = {
            "target": target,
            "name": name,
            "daemon": daemon,
            "started": False,
        }
        self.created.append(record)

        class HeldThread:
            def start(inner_self):
                record["started"] = True

        return HeldThread()

    def run(self, index=0):
        self.created[index]["target"]()


def _service(
    store=None,
    *,
    state=None,
    review_box=None,
    providers_box=None,
    run_reviewer=None,
    thread_factory=None,
    jobs=None,
    now=None,
):
    actual_store = store or FakeStore()
    actual_state = state or FakeState()
    reviews = review_box or {
        "call": lambda _evidence, cfg: {
            "verdict": "pass",
            "summary": "No issues found",
            "issues": [],
            "usage": {},
            "model": cfg.model,
        }
    }
    provider_registry = providers_box or {"value": {}}
    events = []
    job_map = jobs if jobs is not None else {}
    config = LLMConfig(
        provider="deepseek",
        api_key="worker-key",
        model="worker-model",
        timeout_s=60,
    )

    def emitter_for(root_frame_id):
        assert root_frame_id == "frame"

        def emit(event):
            events.append(dict(event))

        return emit

    ports = ReviewPorts(
        state_for=lambda root_frame_id, _project_id: (
            actual_state
            if root_frame_id == actual_state.root_frame_id
            else pytest.fail("unexpected frame")
        ),
        emitter_for=emitter_for,
        llm_config_for=lambda _state: config,
        review_evidence=lambda evidence, cfg: reviews["call"](evidence, cfg),
        providers=lambda: provider_registry["value"],
        clean_api_key=lambda value: str(value or "").strip(),
        # A profile's api_key holds a broker reference once migrated; this
        # double stands in for the resolution the gateway wires in. Required
        # rather than defaulted on purpose — a port that quietly fell back to
        # the raw field would send the reference to the provider as a key.
        resolve_profile_key=lambda profile: str(profile.get("api_key") or "").strip(),
        job_factory=FakeJob,
        busy_error=BusyError,
        run_reviewer=run_reviewer,
        thread_factory=thread_factory,
        now=now,
    )
    service = ReviewService(
        store=actual_store,
        lock=threading.Lock(),
        jobs=job_map,
        ports=ports,
    )
    return service, actual_store, actual_state, events, job_map, reviews


def test_auto_setting_precedence_and_late_model_provider_resolution():
    store = FakeStore()
    providers = {"value": {}}
    service, _store, state, _events, _jobs, _reviews = _service(
        store,
        providers_box=providers,
    )

    store.settings["auto_review_enabled"] = " yes "
    assert service.auto_enabled("frame") is True
    store.settings["review:auto:frame"] = "OFF"
    assert service.auto_enabled("frame") is False

    store.settings["reviewer_model"] = "global-reviewer"
    store.settings["review:model:frame"] = "__agent__"
    agent_config = service.llm_config(state)
    assert agent_config.provider == "deepseek"
    assert agent_config.model == "worker-model"
    assert agent_config.timeout_s == 45

    store.settings["review:model:frame"] = "remote-reviewer"
    providers["value"] = {
        "gemini": {"model": "remote-reviewer"},
    }
    remote_config = service.llm_config(state)
    assert remote_config.provider == "gemini"
    assert remote_config.model == "remote-reviewer"
    assert remote_config.base_url == LLMConfig(provider="gemini").base_url
    assert remote_config.api_key == LLMConfig(provider="gemini").api_key

    store.profiles = [
        {
            "provider": "claude",
            "model": "profile-reviewer",
            "base_url": " https://review.example ",
            "api_key": " review-key ",
        }
    ]
    store.settings["review:model:frame"] = "profile-reviewer"
    profile_config = service.llm_config(state)
    assert profile_config.provider == "claude"
    assert profile_config.base_url == "https://review.example"
    assert profile_config.api_key == "review-key"


def test_artifact_excerpt_preserves_type_extension_limit_and_decode(tmp_path):
    readable = tmp_path / "report.bin"
    readable.write_bytes((b"a" * 7999) + b"\xff" + b"tail")
    markdown = tmp_path / "report.MD"
    markdown.write_text("markdown", encoding="utf-8")
    binary = tmp_path / "figure.png"
    binary.write_bytes(b"png")

    excerpt = ReviewService.artifact_excerpt(
        {"path": str(readable), "content_type": "APPLICATION/JSON"}
    )
    assert excerpt is not None
    assert len(excerpt) == 8000
    assert excerpt.endswith("�")
    assert ReviewService.artifact_excerpt({"path": str(markdown)}) == "markdown"
    assert ReviewService.artifact_excerpt({"path": str(binary)}) is None
    assert (
        ReviewService.artifact_excerpt({"path": str(tmp_path / "missing.txt")}) is None
    )


def test_run_builds_evidence_persists_usage_and_streams_exact_steps(tmp_path):
    store = FakeStore()
    old_path = tmp_path / "old.txt"
    old_path.write_text("old", encoding="utf-8")
    report_path = tmp_path / "report.md"
    report_path.write_text("# Report", encoding="utf-8")
    store.artifacts = [
        {
            "artifact_id": "old",
            "filename": "old.txt",
            "content_type": "text/plain",
            "latest_version_id": "v1",
            "path": str(old_path),
        },
        {
            "artifact_id": "new",
            "filename": "report.md",
            "content_type": "text/markdown",
            "size_bytes": 8,
            "latest_version_id": "v2",
            "path": str(report_path),
        },
    ]
    store.cells = [
        {"cell_index": 1, "code": "old"},
        {
            "cell_index": 2,
            "code": "write_report()",
            "stdout": "done",
            "status": "ok",
            "files_written": ["report.md"],
        },
    ]
    store.steps = [
        {
            "step_id": "search",
            "kind": "search",
            "title": "Find evidence",
            "status": "done",
            "summary": "1 source",
            "input": {"query": "evidence"},
            "output": {"count": 1},
        }
    ]
    captured = {}
    review_box = {}
    threads = ImmediateThreads()
    service, _store, state, events, _jobs, reviews = _service(
        store,
        review_box=review_box,
        thread_factory=threads,
    )

    def review(evidence, cfg):
        captured["evidence"] = evidence
        return {
            "verdict": "pass",
            "summary": "No issues found",
            "issues": [],
            "usage": {"input_tokens": 11, "output_tokens": 3},
            "model": cfg.model,
        }

    # Replace the provider after service construction: the port is late-bound.
    reviews["call"] = review
    result = service.run(
        state,
        events.append,
        user_text="Create a report",
        assistant_text="Report created",
        artifact_versions_before={"old": "v1"},
        cell_count_before=1,
        step_count_before=0,
    )

    assert result is not None and result["reviewed_artifacts"] == ["new"]
    evidence = captured["evidence"]
    assert evidence["submitted_output"] == {"answer": "submitted"}
    assert evidence["changed_artifacts"] == [
        {
            "artifact_id": "new",
            "filename": "report.md",
            "content_type": "text/markdown",
            "size_bytes": 8,
            "latest_version_id": "v2",
            "exists": True,
            "excerpt": "# Report",
        }
    ]
    assert [cell["cell_index"] for cell in evidence["execution"]] == [2]
    assert evidence["tool_evidence"][0]["kind"] == "search"
    assert store.tokens == [("frame", 11, 3)]
    review_step = next(step for step in store.steps if step["kind"] == "review")
    assert review_step["status"] == "done"
    assert [event["status"] for event in events] == ["running", "done"]
    assert threads.created[0]["name"] == "openai4s-review-call-frame"
    assert threads.created[0]["daemon"] is True


def test_run_honors_pre_provider_cancel_without_starting_provider():
    threads = ImmediateThreads()
    service, store, state, events, _jobs, _reviews = _service(thread_factory=threads)
    state.cancel.set()

    assert (
        service.run(
            state,
            events.append,
            user_text="request",
            assistant_text="answer",
            artifact_versions_before={},
            cell_count_before=0,
        )
        is None
    )

    assert threads.created == []
    assert store.steps[0]["status"] == "cancelled"
    assert store.steps[0]["output"] == {
        "verdict": "cancelled",
        "provider_call": "not_started",
    }
    assert events[-1]["summary"] == "Review cancelled"


def test_run_provider_error_is_a_nonfatal_unavailable_step():
    threads = ImmediateThreads()
    review_box = {
        "call": lambda *_args: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        )
    }
    service, store, state, events, _jobs, _reviews = _service(
        review_box=review_box,
        thread_factory=threads,
    )

    result = service.run(
        state,
        events.append,
        user_text="request",
        assistant_text="answer",
        artifact_versions_before={},
        cell_count_before=0,
    )

    assert result is None
    assert store.steps[0]["status"] == "error"
    assert store.steps[0]["output"] == {
        "error": "provider unavailable",
        "verdict": "unavailable",
    }
    assert events[-1]["status"] == "error"
    assert service.call_inflight("frame") is False


def test_cancelled_provider_finishes_asynchronously_and_blocks_duplicates():
    started = threading.Event()
    release = threading.Event()

    def slow_review(_evidence, _cfg):
        started.set()
        assert release.wait(3)
        return {"verdict": "pass", "summary": "No issues found", "usage": {}}

    service, store, state, events, _jobs, _reviews = _service(
        review_box={"call": slow_review}
    )
    result_box = {}
    outer = threading.Thread(
        target=lambda: result_box.setdefault(
            "result",
            service.run(
                state,
                events.append,
                user_text="request",
                assistant_text="answer",
                artifact_versions_before={},
                cell_count_before=0,
            ),
        )
    )
    outer.start()
    assert started.wait(1)
    state.cancel.set()
    outer.join(1)

    assert not outer.is_alive()
    assert result_box["result"] is None
    assert service.call_inflight("frame") is True
    assert store.steps[0]["output"]["provider_call"] == "finishing"

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and service.call_inflight("frame"):
        time.sleep(0.01)
    assert service.call_inflight("frame") is False
    assert store.steps[0]["output"]["provider_call"] == "finished"
    assert events[-1]["summary"] == "Review cancelled"


def test_submit_manual_review_preserves_status_tail_and_job_pruning():
    store = FakeStore()
    store.frames["frame"]["status"] = "failed"
    store.messages = [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "analysis"},
        {"role": "assistant", "content": "final"},
    ]
    captured = {}

    def manual_runner(_state, _emit, **kwargs):
        captured.update(kwargs)
        return {"verdict": "pass"}

    old = FakeJob("old", "other")
    old.done.set()
    old.finished_at = 1
    fresh = FakeJob("fresh", "other")
    fresh.done.set()
    fresh.finished_at = 900
    jobs = {"old": old, "fresh": fresh}
    threads = ImmediateThreads()
    service, _store, _state, events, job_map, _reviews = _service(
        store,
        run_reviewer=manual_runner,
        thread_factory=threads,
        jobs=jobs,
        now=lambda: 1000,
    )

    job = service.submit("frame", "default")
    result = job.wait_result()

    assert result == {
        "status": "completed",
        "frame_id": "frame",
        "review": {"verdict": "pass"},
    }
    assert captured["mode"] == "manual"
    assert captured["user_text"] == "latest"
    assert captured["assistant_text"] == "analysis\n\nfinal"
    assert store.message_reads == [(0, 1000)]
    assert store.frames["frame"]["status"] == "failed"
    assert [event["status"] for event in events] == ["processing", "failed"]
    assert "old" not in job_map and "fresh" in job_map
    assert threads.created[0]["name"] == "openai4s-review-frame"
    assert threads.created[0]["daemon"] is True


def test_submit_reserves_cancel_before_state_and_rejects_duplicate():
    state = FakeState()
    threads = HeldThreads()
    service, _store, _state, events, _jobs, _reviews = _service(
        state=state,
        run_reviewer=lambda *_args, **_kwargs: None,
        thread_factory=threads,
    )

    job = service.submit("frame", "default")
    assert service.call_inflight("frame") is True
    with pytest.raises(BusyError) as busy:
        service.submit("frame", "default")
    assert busy.value.code == 409
    assert str(busy.value) == "a previous review call is still finishing"

    service.cancel("frame")
    threads.run()

    assert job.wait_result()["status"] == "cancelled"
    assert service.call_inflight("frame") is False
    assert [event["status"] for event in events] == ["processing", "ready"]


def test_submit_thread_start_failure_cleans_reservation_and_job():
    class FailingThreads:
        def __call__(self, **_kwargs):
            class FailingThread:
                def start(inner_self):
                    raise RuntimeError("cannot start")

            return FailingThread()

    service, _store, _state, _events, jobs, _reviews = _service(
        run_reviewer=lambda *_args, **_kwargs: None,
        thread_factory=FailingThreads(),
    )

    with pytest.raises(RuntimeError, match="cannot start"):
        service.submit("frame", "default")
    assert service.call_inflight("frame") is False
    assert jobs == {}
