"""Stage 8 official Notebook path and host-side cross-language lineage."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifacts import ArtifactManager
from openai4s.server.evidence_snapshot import (
    collect_turn_evidence,
    freeze_evidence_snapshot,
)
from openai4s.server.notebook_lineage import (
    bind_cell_lineage,
    official_notebook_enabled,
)
from openai4s.storage.snapshots import WorkspaceCAS
from openai4s.store import get_store


def test_stage8_flag_makes_notebook_official_without_the_old_developer_switch():
    off = Config(roadmap_features=RoadmapFeatureFlags())
    on = Config(roadmap_features=RoadmapFeatureFlags(stage8_live_notebook_lineage=True))
    assert official_notebook_enabled(off) is False
    assert official_notebook_enabled(on) is True


def test_runtime_observation_is_the_only_production_read_set(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "table.csv").write_text("x\n", encoding="utf-8")

    class Store:
        @staticmethod
        def version_for_path(*_args, **_kwargs):
            return None

        @staticmethod
        def list_artifacts(*_args, **_kwargs):
            return []

    assert (
        bind_cell_lineage(
            Store(),
            workspace=workspace,
            artifacts=[],
            root_frame_id="root",
            project_id="project",
            producing_cell_id="cell",
            observed_reads=[],
        )
        == []
    )
    assert bind_cell_lineage(
        Store(),
        workspace=workspace,
        artifacts=[],
        root_frame_id="root",
        project_id="project",
        producing_cell_id="cell",
        observed_reads=["table.csv"],
    ) == ["table.csv"]


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
        artifacts=r_capture.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-r",
        observed_reads=[],
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
        artifacts=py_capture.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-py",
        observed_reads=["table.csv"],
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
    second_version = second.artifacts[0]["version_id"]

    before = manager.snapshot(workspace)
    (workspace / "table.csv").write_text("value\n1\n2\n3\n", encoding="utf-8")
    third = manager.capture(
        session, 3, "cell-3", before, lambda event: None, language="python"
    )
    bind_cell_lineage(
        store,
        workspace=workspace,
        artifacts=third.artifacts,
        root_frame_id=root,
        project_id="default",
        producing_cell_id="cell-3",
        observed_reads=["table.csv"],
    )
    third_version = third.artifacts[0]["version_id"]
    assert second_version != first_version
    assert third_version not in {first_version, second_version}
    inputs = store.lineage_inputs(third_version)
    assert {item["version_id"] for item in inputs} == {second_version}
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


def test_cell_lineage_is_recorded_without_the_stage8_flag(tmp_path):
    """ART-14: the read -> version mapping sat behind the Stage 8 flag, and the
    gated binding *returned* ``[]``, which the Cell stored as its read set. With
    the flag off (the default) every observed read was erased, so provenance
    showed "no recorded inputs" even for ``read_csv('upload.csv').to_csv('x.csv')``.
    """
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    assert cfg.roadmap_features.stage8_live_notebook_lineage is False
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        store = runner.store
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
        (workspace / "upload.csv").write_text("v\n1\n", encoding="utf-8")
        uploaded = manager.capture(
            session, 1, "cell-1", {}, lambda event: None, language="python"
        )

        before = manager.snapshot(workspace)
        (workspace / "x.csv").write_text("v\n1\n", encoding="utf-8")
        derived = manager.capture(
            session, 2, "cell-2", before, lambda event: None, language="python"
        )
        derived.files_read = ["upload.csv"]  # what the worker observed

        reads = runner._bind_notebook_lineage(  # noqa: SLF001
            session, None, before, derived, "cell-2"
        )

        assert reads == ["upload.csv"]
        inputs = store.lineage_inputs(derived.artifacts[0]["version_id"])
        assert {item["version_id"] for item in inputs} == {
            uploaded.artifacts[0]["version_id"]
        }
    finally:
        runner.close()


# ---------------------------------------------------------------------------
# NV-07 · Notebook lineage when a Cell rewrites an Artifact with identical bytes
# ---------------------------------------------------------------------------
#
# Sample: Artifact ``a.csv`` already has V1 (cell-1) and V2 (cell-2).  A Cell
# reads ``a.csv``, writes the same bytes back, and also writes ``b.csv`` (VB).
# Every case below runs the real capture path (``ArtifactManager.capture`` ->
# ``register_file`` -> ``record_cell_artifact``) and then ``bind_cell_lineage``
# exactly as ``CellRunService`` orders them: capture first, bind after.
#
# Reproduced on main@53e161b0:
#
# * Trusted capture (Stage 1), same bytes: ``record_cell_artifact`` reuses V2
#   (``head_checksum_reused``), but ``register_file`` returns only the version
#   id, so the binder counts V2 as this Cell's output, excludes it from read
#   resolution and falls back to the next-newest version.  Result: VB <- V1
#   (wrong) and a new V1 -> V2 edge from this Cell on a historical version.
# * Restore V1 as V3, then the same bytes: V3 is reused and excluded the same
#   way; the fallback lands on V2, whose bytes were not on disk.  Result:
#   VB <- V2 and the restored V3 gains a second input V2 beside its restore
#   edge from V1.
# * Checkpoint revert to V1's bytes (the head stays V2), then the same bytes:
#   a new V3 is created; the fallback again picks V2.  Result: VB <- V2 and
#   V3 <- V2 although the Cell read V1's bytes.
# * Already right: a different-content overwrite (V3 <- V2, VB <- V2), a
#   same-Cell merge (the merged version is this Cell's own, so the fallback to
#   the pre-Cell head is the correct input), and untrusted capture (identical
#   bytes become a new version, VB <- V2).
#
# The wrong cases are pinned below with ``xfail(strict=True)`` asserting the
# correct expectation; when they start passing, strict mode fails the run so
# the marker is removed in the same change.
#
# Acceptance design for the future fix (no API, schema or new file needed):
#
# 1. Hand the capture kind from capture to binding.  ``record_cell_artifact``
#    already returns ``capture_kind`` (``version_created`` /
#    ``same_cell_merge`` / ``head_checksum_reused``) and ``version_created``;
#    ``register_file`` must carry them into each ``CaptureResult.artifacts``
#    entry, and ``bind_cell_lineage`` must read them.
# 2. Only versions this Cell created (``version_created``, ``same_cell_merge``)
#    are outputs: they are excluded from read resolution and receive input
#    edges.  A ``head_checksum_reused`` version is history: it is not excluded
#    from read resolution and receives no new lineage edge (the Cell's own
#    facts live in its capture observation, which already exists).
# 3. A read links to the Artifact's head at Cell start only when the bytes the
#    Cell started from are verifiably that version's (for example a digest
#    taken at Cell start equals its checksum).  Today's "next-newest version
#    not in the outputs" only coincides with that head when rules 1-2 hold and
#    the workspace was not reverted underneath it.  When the read cannot be
#    proven -- a path can be rewritten mid-Cell, so a filename alone cannot pin
#    the bytes -- the read stays in ``files_read`` and no edge is recorded
#    (shown as unlinked).  The pre-Cell snapshot carries no digest outside
#    WSL/overlay filesystems, so rule 3 needs new evidence at Cell start; that
#    is why this item is preparation only.
#
# Acceptance bar: every NV-07 xfail below passes with its marker removed; the
# passing NV-07 cases (and ``test_overwrite_maps_to_the_previous_version``)
# stay green; no historical version gains a self-loop or a new edge; nothing
# falls back to an older version by guess; existing version rows are not
# rewritten.  Rules 1-2 alone turn the capture-kind, same-content and restore
# xfails green; the revert xfail also needs rule 3.

_NV07_A1 = "v\n1\n"
_NV07_A2 = "v\n1\n2\n"


@pytest.fixture
def nv07_world(tmp_path):
    """Build one session world per call: store, real ArtifactManager, workspace."""

    opened = []

    def make(*, trusted: bool = True):
        base = (tmp_path / f"world-{len(opened)}").resolve()
        cfg = Config(data_dir=base / "data")
        store = get_store(cfg.db_path)
        root = store.new_frame(kind="turn", project_id="default", status="ready")
        # The managed workspace root, so ArtifactManager.restore accepts the path.
        workspace = cfg.data_dir / "agent-workspaces" / root
        workspace.mkdir(parents=True)
        manager = ArtifactManager(
            data_dir=cfg.data_dir,
            store=store,
            workspace_for=lambda _frame: workspace,
            broadcast=lambda *_args: None,
            guess_content_type=lambda _name: "text/csv",
            checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
            trusted_delivery=trusted,
        )
        _generation(store, root, "python", sys.executable, "base")
        world = SimpleNamespace(
            store=store,
            manager=manager,
            workspace=workspace,
            root=root,
            session=SimpleNamespace(
                root_frame_id=root, project_id="default", workspace=workspace
            ),
        )
        opened.append(world)
        return world

    yield make
    for world in opened:
        world.store.close()


def _nv07_cell(world, index, cell_id, writes, reads=(), *, capture_passes=1):
    """One Cell: write files, capture them, then bind the observed reads."""

    before = world.manager.snapshot(world.workspace)
    for name, text in writes.items():
        (world.workspace / name).write_text(text, encoding="utf-8")
    artifacts = []
    for _ in range(capture_passes):
        artifacts += world.manager.capture(
            world.session, index, cell_id, before, lambda event: None
        ).artifacts
    files_read = bind_cell_lineage(
        world.store,
        workspace=world.workspace,
        artifacts=artifacts,
        root_frame_id=world.root,
        project_id="default",
        producing_cell_id=cell_id,
        observed_reads=list(reads),
    )
    return {item["filename"]: item for item in artifacts}, files_read


def _nv07_a_with_two_versions(world):
    first, _ = _nv07_cell(world, 1, "cell-1", {"a.csv": _NV07_A1})
    second, _ = _nv07_cell(world, 2, "cell-2", {"a.csv": _NV07_A2})
    return first["a.csv"]["version_id"], second["a.csv"]["version_id"]


def _nv07_inputs(store, version_id, **kwargs):
    return {item["version_id"] for item in store.lineage_inputs(version_id, **kwargs)}


def test_nv07_trusted_same_content_rewrite_reuses_v2_without_touching_it(
    nv07_world,
):
    """The capture half of the sample, right today: an observation on V2."""

    world = nv07_world()
    v1, v2 = _nv07_a_with_two_versions(world)
    v2_row = world.store.version_meta(v2)

    out, files_read = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A2, "b.csv": "n\n2\n"}, ["a.csv"]
    )

    artifact_id = out["a.csv"]["artifact_id"]
    assert out["a.csv"]["version_id"] == v2
    assert out["b.csv"]["version_id"] not in {v1, v2}
    assert files_read == ["a.csv"]
    assert [row["version_id"] for row in world.store.list_versions(artifact_id)] == [
        v2,
        v1,
    ]
    assert world.store.version_meta(v2) == v2_row
    observed = world.store.list_artifact_capture_observations(artifact_id=artifact_id)
    assert [
        (row["producing_cell_id"], row["capture_kind"], row["version_id"])
        for row in observed
    ] == [
        ("cell-1", "version_created", v1),
        ("cell-2", "version_created", v2),
        ("cell-3", "head_checksum_reused", v2),
    ]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="NV-07: register_file does not hand capture_kind to the capture result",
)
def test_nv07_capture_result_says_which_version_was_reused(nv07_world):
    world = nv07_world()
    _nv07_a_with_two_versions(world)

    out, _ = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A2, "b.csv": "n\n2\n"}, ["a.csv"]
    )

    assert out["a.csv"].get("capture_kind") == "head_checksum_reused"
    assert out["b.csv"].get("capture_kind") == "version_created"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="NV-07: a reused V2 is excluded as this Cell's output; the read falls back to V1",
)
def test_nv07_same_content_rewrite_links_b_to_the_version_read(nv07_world):
    world = nv07_world()
    v1, v2 = _nv07_a_with_two_versions(world)
    v2_inputs = world.store.lineage_inputs(v2)

    out, files_read = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A2, "b.csv": "n\n2\n"}, ["a.csv"]
    )

    b = out["b.csv"]["version_id"]
    assert files_read == ["a.csv"]
    # V2 (the head the Cell started from) when provable, otherwise unlinked.
    assert _nv07_inputs(world.store, b) <= {v2}
    assert v1 not in _nv07_inputs(world.store, b)
    # V2 is history: no new edge from this Cell, no V2 -> V2 self-loop.
    assert world.store.lineage_inputs(v2, producing_cell_id="cell-3") == []
    assert world.store.lineage_inputs(v2) == v2_inputs


def test_nv07_different_content_overwrite_links_both_outputs_to_v2(nv07_world):
    world = nv07_world()
    v1, v2 = _nv07_a_with_two_versions(world)

    out, files_read = _nv07_cell(
        world, 3, "cell-3", {"a.csv": "v\n1\n2\n3\n", "b.csv": "n\n3\n"}, ["a.csv"]
    )

    v3, b = out["a.csv"]["version_id"], out["b.csv"]["version_id"]
    assert v3 not in {v1, v2}
    assert files_read == ["a.csv"]
    assert _nv07_inputs(world.store, v3) == {v2}
    assert _nv07_inputs(world.store, b) == {v2}
    assert world.store.lineage_inputs(v2) == []


def test_nv07_same_cell_merge_keeps_the_pre_cell_head_as_input(nv07_world):
    """The fallback is right here: the merged version is this Cell's own."""

    world = nv07_world()
    v1, v2 = _nv07_a_with_two_versions(world)

    out, files_read = _nv07_cell(
        world,
        3,
        "cell-3",
        {"a.csv": "v\n1\n2\n3\n", "b.csv": "n\n3\n"},
        ["a.csv"],
        capture_passes=2,
    )

    v3, b = out["a.csv"]["version_id"], out["b.csv"]["version_id"]
    assert v3 not in {v1, v2}
    assert len(world.store.list_versions(out["a.csv"]["artifact_id"])) == 3
    observed = world.store.list_artifact_capture_observations(version_id=v3)
    assert [(row["producing_cell_id"], row["capture_kind"]) for row in observed] == [
        ("cell-3", "same_cell_merge")
    ]
    assert files_read == ["a.csv"]
    assert _nv07_inputs(world.store, v3) == {v2}
    assert _nv07_inputs(world.store, b) == {v2}
    assert world.store.lineage_inputs(v2) == []


def test_nv07_untrusted_same_content_rewrite_is_a_new_version_linked_to_v2(
    nv07_world,
):
    world = nv07_world(trusted=False)
    v1, v2 = _nv07_a_with_two_versions(world)

    out, files_read = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A2, "b.csv": "n\n2\n"}, ["a.csv"]
    )

    v3, b = out["a.csv"]["version_id"], out["b.csv"]["version_id"]
    assert v3 not in {v1, v2}
    assert files_read == ["a.csv"]
    assert _nv07_inputs(world.store, v3) == {v2}
    assert _nv07_inputs(world.store, b) == {v2}


def _nv07_restore_v1(world):
    v1, v2 = _nv07_a_with_two_versions(world)
    artifact_id = world.store.version_meta(v1)["artifact_id"]
    restored = world.manager.restore(artifact_id, v1)
    return v1, v2, restored["version_id"]


def test_nv07_restore_then_same_content_rewrite_reuses_the_restored_head(
    nv07_world,
):
    world = nv07_world()
    v1, v2, v3 = _nv07_restore_v1(world)
    assert v3 not in {v1, v2}
    assert (world.workspace / "a.csv").read_text(encoding="utf-8") == _NV07_A1
    assert _nv07_inputs(world.store, v3) == {v1}

    out, _ = _nv07_cell(
        world, 4, "cell-4", {"a.csv": _NV07_A1, "b.csv": "n\n1\n"}, ["a.csv"]
    )

    assert out["a.csv"]["version_id"] == v3
    observed = world.store.list_artifact_capture_observations(version_id=v3)
    assert [(row["producing_cell_id"], row["capture_kind"]) for row in observed] == [
        ("cell-4", "head_checksum_reused")
    ]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="NV-07: the reused restored head is excluded; the read falls back to V2",
)
def test_nv07_restore_then_same_content_rewrite_links_b_to_the_restored_head(
    nv07_world,
):
    world = nv07_world()
    _v1, v2, v3 = _nv07_restore_v1(world)
    v3_inputs = world.store.lineage_inputs(v3)

    out, files_read = _nv07_cell(
        world, 4, "cell-4", {"a.csv": _NV07_A1, "b.csv": "n\n1\n"}, ["a.csv"]
    )

    b = out["b.csv"]["version_id"]
    assert files_read == ["a.csv"]
    # V2's bytes were never on disk during this Cell.
    assert _nv07_inputs(world.store, b) <= {v3}
    assert v2 not in _nv07_inputs(world.store, b)
    # The restored version keeps exactly its restore edge from V1.
    assert world.store.lineage_inputs(v3) == v3_inputs


def _nv07_revert_to_v1(world, cas_root):
    """Two versions, then the workspace transition a checkpoint revert applies.

    ``SessionBranching.revert_and_continue`` restores workspace bytes through
    ``WorkspaceCAS.restore``; it does not move Artifact heads, so ``a.csv``
    holds V1's bytes while its head stays V2.
    """

    cas = WorkspaceCAS(cas_root)
    first, _ = _nv07_cell(world, 1, "cell-1", {"a.csv": _NV07_A1})
    tree_v1 = cas.capture(world.workspace)["tree_id"]
    second, _ = _nv07_cell(world, 2, "cell-2", {"a.csv": _NV07_A2})
    tree_v2 = cas.capture(world.workspace)["tree_id"]
    applied = cas.restore(tree_v1, world.workspace, baseline_tree_id=tree_v2)
    return first["a.csv"]["version_id"], second["a.csv"]["version_id"], applied


def test_nv07_revert_keeps_the_head_and_the_rewrite_is_a_new_version(
    nv07_world, tmp_path
):
    world = nv07_world()
    v1, v2, applied = _nv07_revert_to_v1(world, tmp_path / "cas")
    assert applied["applied"] is True
    assert (world.workspace / "a.csv").read_text(encoding="utf-8") == _NV07_A1
    artifact_id = world.store.version_meta(v1)["artifact_id"]
    assert world.store.get_artifact(artifact_id)["latest_version_id"] == v2

    out, _ = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A1, "b.csv": "n\n1\n"}, ["a.csv"]
    )

    v3 = out["a.csv"]["version_id"]
    assert v3 not in {v1, v2}
    assert (
        world.store.version_meta(v3)["checksum"]
        == world.store.version_meta(v1)["checksum"]
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="NV-07: after a revert the bytes read are V1's, but the read falls back to V2",
)
def test_nv07_revert_then_rewrite_never_links_to_the_unread_head(nv07_world, tmp_path):
    world = nv07_world()
    v1, v2, _applied = _nv07_revert_to_v1(world, tmp_path / "cas")

    out, files_read = _nv07_cell(
        world, 3, "cell-3", {"a.csv": _NV07_A1, "b.csv": "n\n1\n"}, ["a.csv"]
    )

    v3, b = out["a.csv"]["version_id"], out["b.csv"]["version_id"]
    assert files_read == ["a.csv"]
    # The Cell read V1's bytes: V1 when provable, otherwise unlinked, never V2.
    assert v2 not in _nv07_inputs(world.store, b)
    assert _nv07_inputs(world.store, b) <= {v1}
    assert _nv07_inputs(world.store, v3) <= {v1}
