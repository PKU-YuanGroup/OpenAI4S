"""Stage 8 official Notebook path and host-side cross-language lineage."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifacts import ArtifactManager
from openai4s.server.evidence_snapshot import (
    collect_turn_evidence,
    freeze_evidence_snapshot,
)
from openai4s.server.notebook_lineage import (
    bind_cell_lineage,
    infer_read_paths,
    official_notebook_enabled,
)
from openai4s.store import get_store


def test_stage8_flag_makes_notebook_official_without_the_old_developer_switch():
    off = Config(roadmap_features=RoadmapFeatureFlags())
    on = Config(roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True))
    assert official_notebook_enabled(off) is False
    assert official_notebook_enabled(on) is True


def test_infer_reads_named_existing_files_and_ignores_urls():
    workspace = Path("/tmp/ws-stage8")
    before = {
        str(workspace / "table.csv"): (1, 2, 3),
        str(workspace / "notes.md"): (1, 2, 3),
    }
    source = 'df = read.csv("table.csv")\n# ignore https://example.com/a.csv\n'
    assert infer_read_paths(workspace, before, source, files_written=["out.json"]) == [
        "table.csv"
    ]


def _generation(store, root, language, interpreter, env_name):
    return store.create_kernel_generation(
        root_frame_id=root,
        branch_id=root,
        language=language,
        environment={
            "runtime": language,
            "interpreter": interpreter,
            "environment_name": env_name,
        },
        bootstrap={"status": "ok" if language == "python" else "not_applicable"},
        state="active",
    )


def test_r_csv_to_python_json_creates_version_lineage_and_two_env_snapshots(
    tmp_path,
):
    cfg = Config(
        data_dir=tmp_path / "data",
        roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True),
    )
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="default", status="ready")
    workspace = cfg.data_dir / "ws"
    workspace.mkdir(parents=True)
    manager = ArtifactManager(
        data_dir=cfg.data_dir,
        store=store,
        workspace_for=lambda _frame: workspace,
        broadcast=lambda *_args: None,
        guess_content_type=lambda name: (
            "text/csv" if name.endswith(".csv") else "application/json"
        ),
        checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    session = SimpleNamespace(
        root_frame_id=root, project_id="default", workspace=workspace
    )
    _generation(store, root, "r", "/usr/bin/Rscript", "r-mini")
    _generation(store, root, "python", sys.executable, "base")

    before_r = manager.snapshot(workspace)
    (workspace / "table.csv").write_text("value\n1\n2\n", encoding="utf-8")
    r_capture = manager.capture(
        session,
        1,
        "cell-r",
        before_r,
        lambda event: None,
        language="r",
    )
    bind_cell_lineage(
        store,
        workspace=workspace,
        before=before_r,
        source='write.csv(df, "table.csv")',
        files_written=r_capture.files_written,
        artifacts=r_capture.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-r",
    )

    before_py = manager.snapshot(workspace)
    (workspace / "table.json").write_text('{"n": 2}\n', encoding="utf-8")
    py_capture = manager.capture(
        session,
        2,
        "cell-py",
        before_py,
        lambda event: None,
        language="python",
    )
    reads = bind_cell_lineage(
        store,
        workspace=workspace,
        before=before_py,
        source='import json\njson.dump(open("table.csv").read(), open("table.json","w"))',
        files_written=py_capture.files_written,
        artifacts=py_capture.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-py",
    )

    assert reads == ["table.csv"]
    csv_version = r_capture.artifacts[0]["version_id"]
    json_version = py_capture.artifacts[0]["version_id"]
    inputs = store.lineage_inputs(json_version)
    assert csv_version in {item["version_id"] for item in inputs}

    csv_env = store.get_env_snapshot(store.version_meta(csv_version)["env_snapshot_id"])
    json_env = store.get_env_snapshot(
        store.version_meta(json_version)["env_snapshot_id"]
    )
    assert csv_env["kind"] == "r"
    assert csv_env["interpreter"] == "/usr/bin/Rscript"
    assert json_env["kind"] == "python"
    assert json_env["interpreter"] == sys.executable
    assert csv_env["generation_id"] != json_env["generation_id"]

    snapshot = freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": root,
                "branch_id": root,
                "turn_id": "turn-1",
                "execution_id": "exec-1",
            },
            "user_request": "R csv then Python json",
            "candidate_answer": "table.json",
            "artifacts": [
                {
                    "artifact_id": py_capture.artifacts[0]["artifact_id"],
                    "filename": "table.json",
                    "version_id": json_version,
                    "checksum": "ab" * 32,
                    "exists": True,
                }
            ],
            "cells": [
                {
                    "cell_id": "cell-py",
                    "files_read": ["table.csv"],
                    "read_versions": [csv_version],
                }
            ],
            "lineage": [
                {
                    "input_version_id": csv_version,
                    "output_version_id": json_version,
                }
            ],
        }
    )
    refs = {item["ref_id"] for item in snapshot["evidence_refs"]}
    assert f"art:{csv_version}" in refs
    assert f"lineage:{csv_version}->{json_version}" in refs

    collected = collect_turn_evidence(
        store,
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        execution_id="exec-1",
        user_request="R csv then Python json",
        candidate_answer="table.json",
    )
    collected_refs = {item["ref_id"] for item in collected["evidence_refs"]}
    assert f"lineage:{csv_version}->{json_version}" in collected_refs
    store.close()


def test_overwrite_maps_to_the_previous_version(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True),
    )
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="default", status="ready")
    workspace = cfg.data_dir / "ws"
    workspace.mkdir(parents=True)
    manager = ArtifactManager(
        data_dir=cfg.data_dir,
        store=store,
        workspace_for=lambda _frame: workspace,
        broadcast=lambda *_args: None,
        guess_content_type=lambda _name: "text/csv",
        checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    session = SimpleNamespace(
        root_frame_id=root, project_id="default", workspace=workspace
    )
    _generation(store, root, "python", sys.executable, "base")
    (workspace / "table.csv").write_text("value\n1\n", encoding="utf-8")
    first = manager.capture(
        session, 1, "cell-1", {}, lambda event: None, language="python"
    )
    first_version = first.artifacts[0]["version_id"]

    before = manager.snapshot(workspace)
    (workspace / "table.csv").write_text("value\n1\n2\n", encoding="utf-8")
    second = manager.capture(
        session, 2, "cell-2", before, lambda event: None, language="python"
    )
    bind_cell_lineage(
        store,
        workspace=workspace,
        before=before,
        source='df.to_csv("table.csv")',
        files_written=second.files_written,
        artifacts=second.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-2",
    )
    second_version = second.artifacts[0]["version_id"]
    assert second_version != first_version
    inputs = store.lineage_inputs(second_version)
    assert {item["version_id"] for item in inputs} == {first_version}
    store.close()


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def test_stage8_flag_opens_repl_routes_without_the_developer_switch(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True),
    )
    assert cfg.notebook_repl is False
    assert official_notebook_enabled(cfg) is True
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    fid = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    hits = []
    ticket = SimpleNamespace(
        job_id="job-stage8",
        execution_id="repl-stage8",
        execution_owner={"kind": "user_repl", "id": "repl-stage8"},
        wait_result=lambda: {"cell": {"cell_index": 1}},
    )
    runner.submit_repl = lambda rfid, pid, code, **kwargs: (
        hits.append((rfid, pid, code, kwargs)) or ticket
    )
    runner.start_kernel = lambda *args, **kwargs: {"ok": True, "state": "running"}
    runner.stop_kernel = lambda *args, **kwargs: {"ok": True}
    runner.restart_kernel = lambda *args, **kwargs: {"ok": True}
    runner.set_env = lambda *args, **kwargs: {"ok": True}
    runner.interrupt_kernel = lambda *args, **kwargs: {"ok": True}

    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies: list[tuple] = []
    handler._query = lambda: {}
    handler._body = lambda: {
        "code": "print(1)",
        "language": "python",
        "execution_id": "repl-stage8",
        "owner": {"kind": "user_repl", "id": "repl-stage8"},
    }
    handler._json = lambda value, code=200: replies.append((code, value))

    handler._api("POST", f"/frames/{fid}/kernel/execute")
    assert replies[-1][0] == 202
    assert hits and hits[0][2] == "print(1)"
    for action in ("env", "restart", "stop", "start", "interrupt"):
        handler._api("POST", f"/frames/{fid}/kernel/{action}")
        assert replies[-1][0] != 403
    runner.close()


def test_user_repl_reads_agent_namespace_on_the_shared_generation(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        fid = runner.store.new_frame(kind="turn", project_id="default", status="ready")
        st = runner._state(fid, "default")
        with runner._session_execution(
            st,
            owner="agent",
            owner_id="agent-1",
            execution_id="agent-1",
            language="python",
            reason="agent cell",
        ):
            info = runner._execute_and_log(
                st, "agent_marker = 91", "agent", lambda event: None
            )
        assert info["result"].get("error") is None
        result = runner.run_repl(fid, "default", "print(agent_marker)")
        assert result["cell"]["status"] == "ok"
        assert result["cell"]["stdout"].strip() == "91"
        assert result["cell"]["generation_id"]
        assert result["cell"]["generation_id"] == info["generation_id"]
        inspected = runner.variables.inspect(fid, "python")
        names = {item.get("name") for item in inspected.get("variables") or []}
        assert "agent_marker" in names
    finally:
        runner.close()
