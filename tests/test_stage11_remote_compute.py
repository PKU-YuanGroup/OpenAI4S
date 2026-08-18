"""Stage 11 durable remote-compute Go/No-Go."""

from __future__ import annotations

import io
import json
import subprocess
import types

from openai4s.compute import registry
from openai4s.compute.manager import ComputeManager
from openai4s.compute.stage11 import (
    harvest_source,
    official_stage11_enabled,
    stamp_harvest_artifacts,
)
from openai4s.compute.states import SUCCEEDED, TIMED_OUT, UNKNOWN
from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.store import get_store


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


def test_harvest_artifact_names_receipt_input_versions_and_checksum(tmp_path):
    store = get_store(tmp_path / "db.sqlite")
    root = store.new_frame(kind="turn", project_id="default", status="ready")
    path = tmp_path / "out.txt"
    path.write_text("remote-bytes\n", encoding="utf-8")
    saved = store.save_artifact(
        path=str(path),
        filename="out.txt",
        content_type="text/plain",
        size_bytes=13,
        checksum="ab" * 32,
        frame_id=root,
        project_id="default",
    )
    stamped = stamp_harvest_artifacts(
        store,
        [
            {
                "version_id": saved["version_id"],
                "filename": "out.txt",
                "checksum": "ab" * 32,
            }
        ],
        {
            "job_id": "job-stage11",
            "receipt": "sbx-99",
            "provider": "ssh:lab",
            "input_versions": ["v-input"],
        },
    )
    assert stamped == 1
    source = store.version_meta(saved["version_id"]).get("source")
    if isinstance(source, str):
        source = json.loads(source)
    assert source["kind"] == "remote_compute"
    assert source["job_id"] == "job-stage11"
    assert source["receipt"] == "sbx-99"
    assert source["input_versions"] == ["v-input"]
    assert source["checksums"]["out.txt"] == "ab" * 32
    assert (
        harvest_source({"job_id": "j", "alias": "lab"})["remote_environment"] == "lab"
    )
    store.close()
