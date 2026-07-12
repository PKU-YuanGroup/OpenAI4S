"""Direct DTO contracts for execution-log and artifact-lineage views."""

from __future__ import annotations

import hashlib

import pytest

from openai4s.server.execution_views import ExecutionViewService


class _Store:
    def __init__(self):
        self.cells = {}
        self.artifacts = {}
        self.versions = {}
        self.inputs = {}
        self.cell_details = {}
        self.cursor_checkpoints = {}

    def list_cells(self, root_frame_id):
        return self.cells.get(root_frame_id, [])

    def session_checkpoint_source_map(self, root_frame_id, *, source_kind):
        return self.cursor_checkpoints.get((root_frame_id, source_kind), {})

    def get_artifact(self, artifact_id):
        return self.artifacts.get(artifact_id)

    def version_meta(self, version_id):
        return self.versions.get(version_id)

    def lineage_inputs(self, version_id):
        return self.inputs.get(version_id, [])

    def cell_detail(self, producing_cell_id):
        return self.cell_details.get(producing_cell_id)


def _service(store, calls=None):
    def format_timestamp(value):
        if calls is not None:
            calls.append(value)
        return f"time:{value}" if value is not None else None

    return ExecutionViewService(store=store, format_timestamp=format_timestamp)


def test_execution_log_keeps_order_defaults_and_first_seen_kernels():
    store = _Store()
    store.cells["frame"] = [
        {
            "cell_index": 2,
            "state_revision": 9,
            "generation_id": "generation-python-9",
            "kernel_id": None,
            "language": None,
            "code": None,
            "stdout": None,
            "stderr": None,
            "error": None,
            "status": None,
            "figures": None,
            "files_written": None,
            "files_read": None,
            "cpu_s": None,
            "peak_rss_kb": None,
            "interrupted": True,
        },
        {
            "cell_index": 1,
            "kernel_id": "r-kernel",
            "language": "r",
            "code": "mean(x)",
            "stdout": "2\n",
            "stderr": "warning",
            "error": "",
            "status": "ok",
            "figures": ["plot.png"],
            "files_written": ["result.csv"],
            "files_read": ["input.csv"],
            "cpu_s": 1.25,
            "peak_rss_kb": 4096,
        },
        {"cell_index": 3, "kernel_id": None, "code": "pass"},
        {
            "cell_index": 4,
            "kernel_id": "python",
            "status": "interrupted",
            "cpu_s": 0.0,
            "peak_rss_kb": 0,
        },
    ]
    store.cursor_checkpoints[("frame", "cell")] = {"legacy-cell-2": "cp-cell-2"}

    payload = _service(store).execution_log("frame")

    assert payload["kernels"] == ["python", "r-kernel"]
    assert [entry["cell_index"] for entry in payload["entries"]] == [2, 1, 3, 4]
    first, second, _third, interrupted = payload["entries"]
    assert set(first) == {
        "cell_index",
        "state_revision",
        "generation_id",
        "producing_cell_id",
        "fork_checkpoint_id",
        "kernel_id",
        "language",
        "origin",
        "source",
        "code_hash",
        "visibility",
        "pin",
        "replay_policy",
        "variable_reads",
        "variable_writes",
        "variable_deletes",
        "mutation_uncertain",
        "stale",
        "stale_reasons",
        "stdout",
        "stderr",
        "error",
        "status",
        "figures",
        "files_written",
        "files_read",
        "cpu_seconds",
        "peak_rss_kb",
        "attempt_group_id",
        "attempt",
        "revision_of",
        "is_latest_attempt",
        "attempt_count",
    }
    assert first == {
        "producing_cell_id": "legacy-cell-2",
        "fork_checkpoint_id": "cp-cell-2",
        "cell_index": 2,
        "state_revision": 9,
        "generation_id": "generation-python-9",
        "kernel_id": "python",
        "language": "python",
        "origin": None,
        "source": "",
        "code_hash": hashlib.sha256(b"").hexdigest(),
        "visibility": "scientific",
        "pin": False,
        "replay_policy": "conditional",
        "variable_reads": [],
        "variable_writes": [],
        "variable_deletes": [],
        "mutation_uncertain": False,
        "stale": False,
        "stale_reasons": [],
        "stdout": "",
        "stderr": "",
        "error": "",
        "status": "ok",
        "figures": [],
        "files_written": [],
        "files_read": [],
        "cpu_seconds": None,
        "peak_rss_kb": None,
        "attempt_group_id": "legacy-cell-2",
        "attempt": 1,
        "revision_of": None,
        "is_latest_attempt": True,
        "attempt_count": 1,
    }
    assert second["source"] == "mean(x)"
    assert second["cpu_seconds"] == 1.25
    assert second["peak_rss_kb"] == 4096
    assert interrupted["status"] == "interrupted"
    assert interrupted["cpu_seconds"] == 0.0
    assert interrupted["peak_rss_kb"] == 0


def test_execution_log_hides_protocol_only_completion_but_keeps_mixed_cell():
    store = _Store()
    store.cells["frame"] = [
        {
            "cell_index": 1,
            "kernel_id": "python",
            "language": "python",
            "code": "host.submit_output({'ok': True}, ['Completed it'])",
            "stdout": "{'status': 'ok'}\n",
            "status": "ok",
        },
        {
            "cell_index": 2,
            "kernel_id": "python",
            "language": "python",
            "code": (
                "score = compute_score()\n"
                "host.submit_output({'score': score}, ['Computed the score'])"
            ),
            "stdout": "0.93\n",
            "status": "ok",
        },
    ]

    payload = _service(store).execution_log("frame")

    assert [entry["cell_index"] for entry in payload["entries"]] == [2]
    assert "compute_score" in payload["entries"][0]["source"]
    assert _service(store).execution_log("missing") == {
        "kernels": [],
        "entries": [],
    }


def test_execution_log_projects_consecutive_failed_retries_without_dropping_rows():
    store = _Store()
    store.cells["frame"] = [
        {
            "producing_cell_id": "cell-1",
            "cell_index": 1,
            "kernel_id": "python",
            "language": "python",
            "origin": "agent",
            "code": "calculate()",
            "status": "error",
            "error": "NameError",
        },
        {
            "producing_cell_id": "cell-2",
            "cell_index": 2,
            "kernel_id": "python",
            "language": "python",
            "origin": "agent",
            "code": "repair_and_calculate()",
            "status": "error",
            "error": "ValueError",
        },
        {
            "producing_cell_id": "cell-3",
            "cell_index": 3,
            "kernel_id": "python",
            "language": "python",
            "origin": "agent",
            "code": "repair_again()",
            "status": "ok",
        },
        {
            "producing_cell_id": "cell-4",
            "cell_index": 4,
            "kernel_id": "python",
            "language": "python",
            "origin": "agent",
            "code": "new_analysis_step()",
            "status": "ok",
        },
    ]

    entries = _service(store).execution_log("frame")["entries"]

    # This endpoint remains a lossless projection of all physical attempts.
    assert [entry["producing_cell_id"] for entry in entries] == [
        "cell-1",
        "cell-2",
        "cell-3",
        "cell-4",
    ]
    assert [entry["attempt_group_id"] for entry in entries[:3]] == [
        "cell-1",
        "cell-1",
        "cell-1",
    ]
    assert [entry["attempt"] for entry in entries[:3]] == [1, 2, 3]
    assert [entry["revision_of"] for entry in entries[:3]] == [
        None,
        "cell-1",
        "cell-2",
    ]
    assert [entry["attempt_count"] for entry in entries[:3]] == [3, 3, 3]
    assert [entry["is_latest_attempt"] for entry in entries[:3]] == [
        False,
        False,
        True,
    ]
    assert entries[3]["attempt_group_id"] == "cell-4"
    assert entries[3]["attempt_count"] == 1


def test_retry_projection_does_not_cross_runtime_or_non_agent_boundaries():
    store = _Store()
    store.cells["frame"] = [
        {
            "producing_cell_id": "py-error",
            "kernel_id": "python",
            "language": "python",
            "status": "error",
        },
        {
            "producing_cell_id": "r-error",
            "kernel_id": "r",
            "language": "r",
            "status": "error",
        },
        {
            "producing_cell_id": "user-cell",
            "kernel_id": "r",
            "language": "r",
            "origin": "user",
            "status": "ok",
        },
    ]

    entries = _service(store).execution_log("frame")["entries"]

    assert [entry["attempt_group_id"] for entry in entries] == [
        "py-error",
        "r-error",
        "user-cell",
    ]


def test_stale_projection_invalidates_only_old_value_consumers_transitively():
    store = _Store()
    store.cells["frame"] = [
        {
            "producing_cell_id": "producer-x",
            "cell_index": 1,
            "state_revision": 1,
            "code": "x = 1",
        },
        {
            "producing_cell_id": "consumer-x",
            "cell_index": 2,
            "state_revision": 2,
            "code": "y = x + 1",
        },
        {
            "producing_cell_id": "consumer-y",
            "cell_index": 3,
            "state_revision": 3,
            "code": "z = y * 2",
        },
        {
            "producing_cell_id": "independent",
            "cell_index": 4,
            "state_revision": 4,
            "code": "label = 'independent'",
        },
        {
            "producing_cell_id": "replacement-x",
            "cell_index": 5,
            "state_revision": 5,
            "code": "x = 10",
        },
    ]

    entries = _service(store).execution_log("frame")["entries"]
    projected = {
        entry["producing_cell_id"]: (
            entry["stale"],
            entry["stale_reasons"],
        )
        for entry in entries
    }

    assert projected["producer-x"] == (False, [])
    assert projected["consumer-x"][0] is True
    assert projected["consumer-y"][0] is True
    assert projected["consumer-x"][1] == projected["consumer-y"][1]
    assert "variable 'x'" in projected["consumer-x"][1][0]
    assert "replacement-x" in projected["consumer-x"][1][0]
    assert projected["independent"] == (False, [])
    assert projected["replacement-x"] == (False, [])


def test_notebook_projection_hides_unpinned_non_scientific_cells():
    store = _Store()
    store.cells["frame"] = [
        {
            "producing_cell_id": "scientific",
            "code": "result = 1",
            "visibility": "scientific",
        },
        {
            "producing_cell_id": "scratch-hidden",
            "code": "probe = 2",
            "visibility": "scratch",
        },
        {
            "producing_cell_id": "recovery-pinned",
            "code": "restored = True",
            "visibility": "recovery",
            "pin": True,
            "replay_policy": "safe",
        },
    ]

    entries = _service(store).execution_log("frame")["entries"]

    assert [entry["producing_cell_id"] for entry in entries] == [
        "scientific",
        "recovery-pinned",
    ]
    assert entries[1]["visibility"] == "recovery"
    assert entries[1]["pin"] is True
    assert entries[1]["replay_policy"] == "safe"


def test_lineage_merges_reads_deduplicates_and_filters_only_dependency_inputs():
    store = _Store()
    store.artifacts["artifact"] = {
        "artifact_id": "artifact",
        "filename": "result.csv",
        "latest_version_id": "version-2",
        "created_at": 10,
    }
    store.versions["version-2"] = {
        "version_id": "version-2",
        "producing_cell_id": "cell-2",
        "created_at": 20,
    }
    store.inputs["version-2"] = [
        {"filename": "legacy.csv", "version_id": "ignored"},
        {"filename": None, "path": "/data/raw.csv", "version_id": "raw-v"},
        {"filename": None, "path": None, "version_id": "ghost-v"},
        {"filename": "written.txt"},
    ]
    store.cell_details["cell-2"] = {
        "cell_index": 4,
        "kernel_id": None,
        "language": None,
        "status": None,
        "code": "write_results()",
        "files_written": ["written.txt"],
        "files_read": ["legacy.csv", "result.csv", "written.txt"],
    }
    timestamps = []

    payload = _service(store, timestamps).artifact_lineage("artifact")

    assert timestamps == [20]
    assert payload["artifact_id"] == "artifact"
    assert payload["filename"] == "result.csv"
    assert [interaction["kind"] for interaction in payload["interactions"]] == [
        "cell",
        "save",
    ]
    expected_cell = {
        "cell_index": 4,
        "kernel_id": "python",
        "language": "python",
        "exit_status": "ok",
        "source": "write_results()",
        "files_written": ["written.txt"],
        "files_read": [
            "legacy.csv",
            "result.csv",
            "written.txt",
            "/data/raw.csv",
            "ghost-v",
        ],
    }
    for key, value in expected_cell.items():
        assert payload["interactions"][0][key] == value
    assert payload["interactions"][1] == {"kind": "save", "at": "time:20"}
    assert payload["dependency_mappings"] == {
        "inputs": ["legacy.csv", "/data/raw.csv", "ghost-v"]
    }


def test_lineage_unknown_and_save_only_shapes_use_artifact_timestamp():
    store = _Store()
    service = _service(store)
    assert service.artifact_lineage("missing") == {
        "artifact_id": "missing",
        "filename": None,
        "interactions": [],
        "dependency_mappings": {"inputs": []},
    }

    store.artifacts["upload"] = {
        "artifact_id": "upload",
        "filename": "upload.txt",
        "latest_version_id": None,
        "created_at": 30,
    }
    assert service.artifact_lineage("upload") == {
        "artifact_id": "upload",
        "filename": "upload.txt",
        "interactions": [{"kind": "save", "at": "time:30"}],
        "dependency_mappings": {"inputs": []},
    }

    store.artifacts["no-cell"] = {
        "artifact_id": "no-cell",
        "filename": "orphan.txt",
        "latest_version_id": "orphan-version",
        "created_at": 40,
    }
    store.versions["orphan-version"] = {"created_at": None}
    store.inputs["orphan-version"] = [{"version_id": "input-only"}]
    assert service.artifact_lineage("no-cell")["interactions"] == [
        {"kind": "save", "at": "time:40"}
    ]
    assert service.artifact_lineage("no-cell")["dependency_mappings"] == {
        "inputs": ["input-only"]
    }

    store.artifacts["no-meta"] = {
        "artifact_id": "no-meta",
        "filename": "no-meta.txt",
        "latest_version_id": "missing-version",
        "created_at": 50,
    }
    store.inputs["missing-version"] = [{"filename": "edge-only.txt"}]
    no_meta = service.artifact_lineage("no-meta")
    assert no_meta["interactions"] == [{"kind": "save", "at": "time:50"}]
    assert no_meta["dependency_mappings"] == {"inputs": ["edge-only.txt"]}

    store.artifacts["minimal-cell"] = {
        "artifact_id": "minimal-cell",
        "filename": "minimal.txt",
        "latest_version_id": "minimal-version",
        "created_at": 60,
    }
    store.versions["minimal-version"] = {"producing_cell_id": "minimal-producer"}
    store.cell_details["minimal-producer"] = {
        "cell_index": 7,
        "status": "failed",
    }
    minimal = service.artifact_lineage("minimal-cell")["interactions"][0]
    assert minimal == {
        "kind": "cell",
        "cell_index": 7,
        "kernel_id": "python",
        "language": "python",
        "exit_status": "failed",
        "source": "",
        "files_written": [],
        "files_read": [],
    }


def test_view_store_and_formatter_errors_propagate():
    class FailingStore(_Store):
        def list_cells(self, root_frame_id):
            raise RuntimeError(f"cannot read {root_frame_id}")

    with pytest.raises(RuntimeError, match="cannot read frame"):
        _service(FailingStore()).execution_log("frame")

    store = _Store()
    store.artifacts["artifact"] = {
        "artifact_id": "artifact",
        "filename": "result.txt",
        "latest_version_id": None,
        "created_at": 10,
    }

    def fail_timestamp(_value):
        raise ValueError("bad timestamp")

    service = ExecutionViewService(store=store, format_timestamp=fail_timestamp)
    with pytest.raises(ValueError, match="bad timestamp"):
        service.artifact_lineage("artifact")
