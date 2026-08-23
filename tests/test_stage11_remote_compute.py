"""Stage 11 durable remote-compute Go/No-Go."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import threading
import types

import pytest

from openai4s.compute import registry
from openai4s.compute.manager import ComputeManager
from openai4s.compute.stage11 import (
    harvest_artifact_receipts,
    harvest_source,
    official_stage11_enabled,
)
from openai4s.compute.states import SUCCEEDED, TIMED_OUT, UNKNOWN
from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifacts import ArtifactOperationError
from openai4s.store import get_store
from openai4s.tools.registry import execute_tool_call


def test_stage11_flag_defaults_off():
    assert official_stage11_enabled(Config()) is False
    assert official_stage11_enabled(
        Config(
            roadmap_features=RoadmapFeatureFlags(stage11_durable_remote_compute=True)
        )
    )


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = io.BytesIO()

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    registry.add_host("lab", data_dir=tmp_path)
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
        roadmap_features=RoadmapFeatureFlags(stage11_durable_remote_compute=True),
    )


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _gateway_cfg(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage11_durable_remote_compute=True),
    )


class _HarvestManager:
    def __init__(self, workspace, job_id, *, input_versions=None):
        self.workspace = workspace
        self.job_id = job_id
        self.input_versions = list(input_versions or [])
        self.calls = 0

    def has_any_provider(self):
        return True

    def result(self, spec):
        self.calls += 1
        assert spec["job_id"] == self.job_id
        target = self.workspace / "hpc" / self.job_id / "model.pdb"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"ATOM\n"
        target.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        return {
            "status": "succeeded",
            "job_id": self.job_id,
            "provider": "byoc:fake",
            "receipt": f"receipt-{self.job_id}",
            "remote_environment": "fake-a100",
            "input_versions": list(self.input_versions),
            "output_files": [str(target)],
            "artifact_manifest": [
                {"path": "model.pdb", "size": len(payload), "sha256": checksum}
            ],
        }


class _DuplicateHarvestManager(_HarvestManager):
    def result(self, spec):
        payload = super().result(spec)
        payload["output_files"] = payload["output_files"] * 2
        payload["artifact_manifest"] = payload["artifact_manifest"] * 2
        return payload


def _source(store, version_id):
    source = store.version_meta(version_id)["source"]
    return json.loads(source) if isinstance(source, str) else source


def _seed_input_version(runner, frame_id, filename):
    saved = runner.artifacts.upload(
        {
            "frame_id": frame_id,
            "filename": filename,
            "content_text": "remote input\n",
        }
    )
    artifact = runner.store.get_artifact(saved["artifact_id"])
    assert artifact is not None
    return artifact["latest_version_id"]


def test_native_result_binds_source_before_artifact_event(tmp_path):
    """Real control Tool -> dispatcher -> Gateway capture, not a stamper unit."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    input_version_id = _seed_input_version(runner, frame_id, "native-input.txt")
    manager = _HarvestManager(
        state.workspace, "job-native", input_versions=[input_version_id]
    )
    dispatcher._compute = manager
    events = []
    sources_at_emit = []
    lineage_at_emit = []

    def emit(event):
        events.append(event)
        if event.get("type") == "artifact_created":
            version_id = event["artifact"]["version_id"]
            sources_at_emit.append(_source(runner.store, version_id))
            lineage_at_emit.append(
                [item["version_id"] for item in runner.store.lineage_inputs(version_id)]
            )

    try:
        observation, ok = runner._invoke_control_with_artifacts(
            state,
            {
                "id": "call-native-harvest",
                "name": "compute_result",
                "arguments": {
                    "provider": "byoc:fake",
                    "job_id": "job-native",
                },
            },
            emit,
            lambda: execute_tool_call(
                dispatcher,
                {
                    "name": "compute_result",
                    "arguments": {
                        "provider": "byoc:fake",
                        "job_id": "job-native",
                    },
                },
            ),
        )
        assert ok is True
        assert "succeeded" in observation
        assert manager.calls == 1
        artifact = runner.store.artifact_by_filename(
            "hpc/job-native/model.pdb", frame_id, strict=True
        )
        assert artifact is not None
        assert len(sources_at_emit) == 1
        source = sources_at_emit[0]
        assert source["job_id"] == "job-native"
        assert source["provider"] == "byoc:fake"
        assert source["receipt"] == "receipt-job-native"
        assert source["input_versions"] == [input_version_id]
        assert source["checksums"]["hpc/job-native/model.pdb"] == artifact["checksum"]
        assert lineage_at_emit == [[input_version_id]]
        created = next(event for event in events if event["type"] == "artifact_created")
        assert created["artifact"]["version_id"] == artifact["latest_version_id"]

        def cached_result(spec):
            manager.calls += 1
            target = state.workspace / "hpc" / spec["job_id"] / "model.pdb"
            checksum = hashlib.sha256(target.read_bytes()).hexdigest()
            return {
                "status": "succeeded",
                "cached": True,
                "job_id": spec["job_id"],
                "provider": "byoc:fake",
                "receipt": "receipt-job-native",
                "input_versions": [input_version_id],
                "output_files": [str(target)],
                "artifact_manifest": [{"path": "model.pdb", "sha256": checksum}],
            }

        manager.result = cached_result
        repeated_events = []
        _observation, repeated_ok = runner._invoke_control_with_artifacts(
            state,
            {
                "id": "call-native-harvest-again",
                "name": "compute_result",
                "arguments": {
                    "provider": "byoc:fake",
                    "job_id": "job-native",
                },
            },
            repeated_events.append,
            lambda: execute_tool_call(
                dispatcher,
                {
                    "name": "compute_result",
                    "arguments": {
                        "provider": "byoc:fake",
                        "job_id": "job-native",
                    },
                },
            ),
        )
        assert repeated_ok is True
        assert not any(
            event.get("type") == "artifact_created" for event in repeated_events
        )
        assert [
            item["version_id"]
            for item in runner.store.lineage_inputs(artifact["latest_version_id"])
        ] == [input_version_id]
        edge_count = runner.store._conn.execute(  # noqa: SLF001 - exact edge count
            "SELECT COUNT(*) FROM lineage_edges WHERE input_version_id=? "
            "AND output_version_id=?",
            (input_version_id, artifact["latest_version_id"]),
        ).fetchone()[0]
        assert edge_count == 1
    finally:
        runner.close()


def test_foreground_python_host_rpc_harvest_binds_exact_cell_receipt(tmp_path):
    """A real worker Host-RPC returns before the enclosing Cell captures."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    input_version_id = _seed_input_version(runner, frame_id, "cell-input.txt")
    manager = _HarvestManager(
        state.workspace, "job-cell", input_versions=[input_version_id]
    )
    dispatcher._compute = manager
    events = []
    sources_at_emit = []
    lineage_at_emit = []

    def emit(event):
        events.append(event)
        if event.get("type") == "artifact_created":
            version_id = event["artifact"]["version_id"]
            sources_at_emit.append(_source(runner.store, version_id))
            lineage_at_emit.append(
                [item["version_id"] for item in runner.store.lineage_inputs(version_id)]
            )

    try:
        executed = runner._execute_and_log(
            state,
            "result = host._call('compute_result', "
            "[{'provider': 'byoc:fake', 'job_id': 'job-cell'}])\n"
            "print(result['status'])",
            "agent",
            emit,
            stream=True,
        )
        assert executed["result"]["error"] is None
        assert "succeeded" in executed["result"]["stdout"]
        assert manager.calls == 1
        assert executed["files_written"] == ["hpc/job-cell/model.pdb"]
        assert len(sources_at_emit) == 1
        source = sources_at_emit[0]
        assert source["job_id"] == "job-cell"
        assert source["receipt"] == "receipt-job-cell"
        assert source["input_versions"] == [input_version_id]
        artifact = runner.store.artifact_by_filename(
            "hpc/job-cell/model.pdb", frame_id, strict=True
        )
        assert artifact is not None
        assert source["checksums"]["hpc/job-cell/model.pdb"] == artifact["checksum"]
        assert lineage_at_emit == [[input_version_id]]
    finally:
        runner.close()


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("delete", "did not match a changed workspace file"),
        ("rename", "did not match a changed workspace file"),
        ("restore", "receipt did not match captured bytes"),
    ],
)
def test_foreground_host_rpc_cannot_drop_or_retarget_its_receipt(
    tmp_path, mutation, expected_error
):
    """A successful Host call is not a successful Cell without exact capture."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    manager = _HarvestManager(state.workspace, "job-mutated")
    dispatcher._compute = manager
    target = state.workspace / "hpc" / "job-mutated" / "model.pdb"
    if mutation == "restore":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"BASELINE\n")
    mutations = {
        "delete": 'os.remove("hpc/job-mutated/model.pdb")',
        "rename": (
            'os.rename("hpc/job-mutated/model.pdb", ' '"hpc/job-mutated/renamed.pdb")'
        ),
        "restore": 'open("hpc/job-mutated/model.pdb", "wb").write(b"BASELINE\\n")',
    }
    events = []
    code = (
        "import os\n"
        "result = host._call('compute_result', "
        "[{'provider': 'byoc:fake', 'job_id': 'job-mutated'}])\n"
        f"{mutations[mutation]}\n"
        "print(result['status'])"
    )

    try:
        with pytest.raises(ArtifactOperationError, match=expected_error):
            runner._execute_and_log(
                state,
                code,
                "agent",
                events.append,
                stream=True,
            )
        assert manager.calls == 1
        assert not any(event.get("type") == "artifact_created" for event in events)
        assert (
            runner.store.artifact_by_filename(
                "hpc/job-mutated/model.pdb", frame_id, strict=True
            )
            is None
        )
        assert (
            runner.store.artifact_by_filename(
                "hpc/job-mutated/renamed.pdb", frame_id, strict=True
            )
            is None
        )
    finally:
        runner.close()


def test_foreground_host_rpc_rejects_duplicate_receipts_before_capture(tmp_path):
    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    manager = _DuplicateHarvestManager(state.workspace, "job-cell-duplicate")
    dispatcher._compute = manager
    events = []

    try:
        with pytest.raises(RuntimeError, match="receipt evidence is invalid"):
            runner._execute_and_log(
                state,
                "result = host._call('compute_result', "
                "[{'provider': 'byoc:fake', 'job_id': 'job-cell-duplicate'}])\n"
                "print(result['status'])",
                "agent",
                events.append,
                stream=True,
            )
        assert manager.calls == 1
        assert not any(event.get("type") == "artifact_created" for event in events)
        assert (
            runner.store.artifact_by_filename(
                "hpc/job-cell-duplicate/model.pdb", frame_id, strict=True
            )
            is None
        )
    finally:
        runner.close()


def test_duplicate_native_harvest_receipts_fail_before_any_artifact_event(tmp_path):
    """Two Host receipts cannot both bind the same final filename."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)

    manager = _DuplicateHarvestManager(state.workspace, "job-duplicate")
    dispatcher._compute = manager
    events = []
    try:
        with pytest.raises(RuntimeError, match="trusted Artifact capture failed"):
            runner._invoke_control_with_artifacts(
                state,
                {
                    "id": "call-duplicate-harvest",
                    "name": "compute_result",
                    "arguments": {
                        "provider": "byoc:fake",
                        "job_id": "job-duplicate",
                    },
                },
                events.append,
                lambda: execute_tool_call(
                    dispatcher,
                    {
                        "name": "compute_result",
                        "arguments": {
                            "provider": "byoc:fake",
                            "job_id": "job-duplicate",
                        },
                    },
                ),
            )
        assert manager.calls == 1
        assert not any(event.get("type") == "artifact_created" for event in events)
        assert (
            runner.store.artifact_by_filename(
                "hpc/job-duplicate/model.pdb", frame_id, strict=True
            )
            is None
        )
    finally:
        runner.close()


def test_background_thread_cannot_borrow_foreground_receipt_scope(tmp_path):
    """The no-scope refusal happens before a provider can publish bytes."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    manager = _HarvestManager(state.workspace, "job-background")
    dispatcher._compute = manager
    answers = []

    def background_call():
        answers.append(
            dispatcher(
                "compute_result",
                [{"provider": "byoc:fake", "job_id": "job-background"}],
            )
        )

    try:
        with dispatcher.bind_artifact_receipt_scope() as foreground_receipts:
            thread = threading.Thread(target=background_call)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert manager.calls == 0
        assert foreground_receipts == []
        assert answers == [
            {
                "error": "Artifact-producing Host call requires a foreground "
                "capture scope"
            }
        ]
        assert not (state.workspace / "hpc" / "job-background").exists()
    finally:
        runner.close()


def test_refresh_captures_bytes_before_rejecting_malformed_provenance(
    tmp_path, monkeypatch
):
    """A bad receipt cannot skip partial-harvest Artifact registration."""

    runner = gateway_mod.SessionRunner(
        _gateway_cfg(tmp_path), _Hub(), start_idle_sweeper=False
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    workspace = runner.active_workspace_for(frame_id)

    class _MalformedManager:
        def result(self, spec):
            target = workspace / "hpc" / spec["job_id"] / "result.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("harvested\n", encoding="utf-8")
            return {
                "status": "succeeded",
                "job_id": spec["job_id"],
                "provider": "byoc:fake",
                "receipt": "receipt-malformed",
                "output_files": [str(target)],
                "artifact_manifest": [{"path": "result.txt", "sha256": "not-a-sha256"}],
            }

    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *args, **kwargs: types.SimpleNamespace(compute=_MalformedManager()),
    )

    try:
        with pytest.raises(gateway_mod.GatewayError) as refused:
            runner.refresh_compute_task(frame_id, "job-malformed")
        assert refused.value.error_code == "harvest_provenance_invalid"
        artifact = runner.store.artifact_by_filename(
            "hpc/job-malformed/result.txt", frame_id, strict=True
        )
        assert artifact is not None
        assert _source(runner.store, artifact["latest_version_id"]) is None
    finally:
        runner.close()


def test_restart_reconciles_and_does_not_resubmit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _Proc(0, b"OPENAI4S_JOB 31337 31337\n"),
        raising=True,
    )
    first = ComputeManager(cfg)
    job_id = first.submit(
        {"provider": "ssh:lab", "command": "sleep 600", "idempotency_key": "run-11"}
    )["job_id"]
    calls = []

    def forbidden(*a, **k):
        calls.append(a)
        raise AssertionError("reconcile must not resubmit")

    monkeypatch.setattr(subprocess, "Popen", forbidden, raising=True)
    restarted = ComputeManager(cfg)
    report = restarted.reconcile()
    assert job_id in restarted._jobs
    assert report["count"] == 1
    assert report["recovered"][0]["receipt"] == "31337"
    assert calls == []


def test_cancel_after_restart_hits_the_exact_job(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _Proc(0, b"OPENAI4S_JOB 31337 31337\n"),
        raising=True,
    )
    job_id = ComputeManager(cfg).submit(
        {"provider": "ssh:lab", "command": "sleep 600"}
    )["job_id"]
    seen = {}

    def fake_run(argv, **kw):
        seen["cmd"] = argv[2]
        return _Proc(0)

    monkeypatch.setattr(subprocess, "Popen", fake_run, raising=True)
    out = ComputeManager(cfg).cancel({"job_id": job_id})
    assert out["status"] == "cancelled"
    assert "31337" in seen["cmd"]


def test_unknown_and_timeout_are_not_success():
    assert UNKNOWN != SUCCEEDED
    assert TIMED_OUT != SUCCEEDED


def test_harvest_receipt_names_job_input_versions_and_checksum(tmp_path):
    path = tmp_path / "hpc" / "job-stage11" / "out.txt"
    path.parent.mkdir(parents=True)
    path.write_text("remote-bytes\n", encoding="utf-8")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    receipts = harvest_artifact_receipts(
        {
            "job_id": "job-stage11",
            "receipt": "sbx-99",
            "provider": "ssh:lab",
            "input_versions": ["v-input"],
            "output_files": [str(path)],
            "artifact_manifest": [{"path": "out.txt", "sha256": checksum}],
        },
        workspace=tmp_path,
    )
    assert len(receipts) == 1
    source = receipts[0]["source"]
    assert source["kind"] == "remote_compute"
    assert source["job_id"] == "job-stage11"
    assert source["receipt"] == "sbx-99"
    assert source["input_versions"] == ["v-input"]
    assert source["checksums"]["hpc/job-stage11/out.txt"] == checksum
    assert (
        harvest_source({"job_id": "j", "alias": "lab"})["remote_environment"] == "lab"
    )


def test_real_manager_result_carries_durable_harvest_identity(tmp_path, monkeypatch):
    """The receipt builder receives the real manager result, not a test stub.

    Stage 11 originally tested only a hand-written payload containing fields
    that ``ComputeManager.result`` never returned, so production attached zero
    harvested versions even though the helper test was green.
    """

    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    store.create_compute_job(
        job_id="job-real-result",
        provider="byoc:fake",
        status="running",
        input_versions=["v-input-a", "v-input-b"],
        owner_key=None,
    )
    persisted = store.get_compute_job("job-real-result")
    assert persisted is not None
    assert persisted["input_versions"] == ["v-input-a", "v-input-b"]

    manager = ComputeManager(cfg, store=store)
    manager._jobs["job-real-result"] = {
        "job_id": "job-real-result",
        "provider": "byoc:fake",
        "sandbox_id": "sbx-real",
        "receipt": "sbx-real",
        "status": "running",
        "input_versions": persisted["input_versions"],
    }
    output = tmp_path / "hpc" / "job-real-result" / "out.txt"
    output.parent.mkdir(parents=True)
    output.write_text("remote\n", encoding="utf-8")
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    monkeypatch.setattr(
        manager,
        "_result_byoc",
        lambda _job: {
            "status": "succeeded",
            "output_files": [str(output)],
            "artifact_manifest": [{"path": "out.txt", "sha256": checksum}],
        },
        raising=True,
    )

    result = manager.result({"job_id": "job-real-result"})
    assert result["job_id"] == "job-real-result"
    assert result["provider"] == "byoc:fake"
    assert result["receipt"] == "sbx-real"
    assert result["remote_environment"] == "byoc:fake"
    assert result["input_versions"] == ["v-input-a", "v-input-b"]

    receipts = harvest_artifact_receipts(result, workspace=tmp_path)
    assert len(receipts) == 1
    source = receipts[0]["source"]
    assert source["job_id"] == "job-real-result"
    assert source["receipt"] == "sbx-real"
    assert source["input_versions"] == ["v-input-a", "v-input-b"]
    assert source["checksums"] == {
        "hpc/job-real-result/out.txt": checksum,
    }
    assert receipts[0]["checksum"] == checksum
    assert receipts[0]["filename"] == "hpc/job-real-result/out.txt"
    store.close()
