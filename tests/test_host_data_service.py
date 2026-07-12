"""Direct contracts for store-backed host data capabilities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.host.data import HostDataService, rank_artifacts


class FakeStore:
    def __init__(self) -> None:
        self.calls = []
        self.artifact_rows = []
        self.query_rows = []
        self.paths = {}
        self.version = {
            "version_id": "v-abcdef123456",
            "artifact_id": "a-1",
        }
        self.metadata = {}
        self.frame_details = {}
        self.edges = {}

    def query(self, sql, *, params=None, limit=None, timeout_s=5.0):
        self.calls.append(("query", sql, params, limit, timeout_s))
        return self.query_rows

    def schema(self):
        return {"frames": ["frame_id"]}

    def list_artifacts(self, filters=None):
        self.calls.append(("list_artifacts", filters))
        return list(self.artifact_rows)

    def resolve_artifact_path(self, ident):
        return self.paths.get(ident)

    def record_cell_artifact(self, **fields):
        self.calls.append(("record_cell_artifact", fields))
        return dict(self.version)

    def version_meta(self, version_id):
        self.calls.append(("version_meta", version_id))
        return self.metadata.get(version_id)

    def set_version_snapshot(self, version_id, snapshot_path):
        self.calls.append(("set_version_snapshot", version_id, snapshot_path))

    def set_priority(self, artifact_id, priority):
        self.calls.append(("set_priority", artifact_id, priority))

    def frame_detail(self, frame_id, *, page, page_size):
        self.calls.append(("frame_detail", frame_id, page, page_size))
        return self.frame_details.get(frame_id)

    def search_frames(self, pattern, *, project_id, limit):
        self.calls.append(("search_frames", pattern, project_id, limit))
        return [{"frame_id": "search"}]

    def browse_frames(self, *, project_id, status, roots_only, limit):
        self.calls.append(("browse_frames", project_id, status, roots_only, limit))
        return [{"frame_id": "browse"}]

    def producing_cell_for_version(self, version_id):
        return {"code": "answer = 42"}

    def lineage_inputs(self, version_id):
        return [{"version_id": "v-input"}]

    def lineage_edges_for(self, version_id, direction):
        self.calls.append(("lineage_edges_for", version_id, direction))
        return self.edges.get(version_id, [])

    def version_for_path(self, path):
        return self.paths.get(path)


def _service(tmp_path: Path, store: FakeStore | None = None):
    actual_store = store or FakeStore()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(artifacts_dir=tmp_path / "artifacts")

    def resolve(path, *, must_exist=False):
        result = (workspace / path).resolve()
        if must_exist and not result.exists():
            raise FileNotFoundError(result)
        return result

    service = HostDataService(
        store=actual_store,
        config=config,
        frame_id=lambda: "frame-1",
        resolve_path=resolve,
    )
    return service, actual_store, workspace, config


def test_query_projection_and_schema_keep_store_contract(tmp_path):
    service, store, _workspace, _config = _service(tmp_path)
    store.query_rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    assert service.query(
        {"sql": "SELECT a,b", "params": [1], "limit": 9, "df": True}
    ) == {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]}
    assert store.calls == [("query", "SELECT a,b", [1], 9, 5.0)]
    assert service.query_schema() == {"frames": ["frame_id"]}


def test_artifact_search_keeps_filter_mutation_and_ranking(tmp_path):
    service, store, _workspace, _config = _service(tmp_path)
    store.artifact_rows = [
        {"filename": "protein_scores.csv", "content_type": "text/csv", "priority": 0},
        {"filename": "protein_notes.txt", "content_type": "text/plain", "priority": 2},
        {"filename": "unrelated.png", "content_type": "image/png", "priority": 0},
    ]
    filters = {"search": "protein", "project_id": "p1"}

    result = service.artifacts(filters)

    assert filters == {"project_id": "p1"}
    assert store.calls == [("list_artifacts", {"project_id": "p1"})]
    assert result["count"] == 2
    assert [row["filename"] for row in result["artifacts"]] == [
        "protein_notes.txt",
        "protein_scores.csv",
    ]
    assert all("_score" in row for row in result["artifacts"])


def test_rank_artifacts_never_mutates_source_rows():
    rows = [{"filename": "result.csv", "priority": 1}]

    ranked = rank_artifacts(rows, "result")

    assert "_score" not in rows[0]
    assert ranked[0]["_score"] == 5.75


def test_save_artifact_copies_snapshot_and_preserves_record_shape(tmp_path):
    service, store, workspace, config = _service(tmp_path)
    source = workspace / "raw result.txt"
    source.write_text("science", encoding="utf-8")
    store.metadata["v-abcdef123456"] = {"snapshot_path": None}

    result = service.save_artifact(
        {
            "path": source.name,
            "filename": "final result.txt",
            "content_type": "text/plain",
            "execution_cell_id": "cell-7",
            "input_version_ids": ["v-input"],
            "priority": 3,
        }
    )

    snapshot = Path(result["path"])
    assert snapshot.parent == config.artifacts_dir
    assert snapshot.name.endswith("__final_result.txt")
    assert snapshot.read_text(encoding="utf-8") == "science"
    record = next(call for call in store.calls if call[0] == "record_cell_artifact")
    fields = record[1]
    assert fields == {
        "path": str(source),
        "filename": "final result.txt",
        "content_type": "text/plain",
        "size_bytes": 7,
        "checksum": hashlib.sha256(b"science").hexdigest(),
        "producing_cell_id": "cell-7",
        "frame_id": "frame-1",
        "snapshot_path": str(snapshot),
        "input_version_ids": ["v-input"],
        "reuse_policy": "provisional",
    }
    assert ("set_priority", "a-1", 3) in store.calls
    assert result["artifact_id"] == "a-1"


def test_frames_modes_validate_before_store_access(tmp_path):
    service, store, _workspace, _config = _service(tmp_path)

    with pytest.raises(ValueError, match="invalid status"):
        service.frames({"status": "typo"})
    assert store.calls == []

    store.frame_details["f1"] = {"frame_id": "f1"}
    assert service.frames({"frame_id": "f1", "page": 2, "page_size": 7}) == {
        "frame_id": "f1"
    }
    assert (
        service.frames({"pattern": "protein", "project_id": "all"})["mode"] == "search"
    )
    assert service.frames({"status": "done", "roots_only": False}) == {
        "mode": "browse",
        "frames": [{"frame_id": "browse"}],
    }


def test_lineage_projection_and_bounded_graph(tmp_path):
    service, store, _workspace, _config = _service(tmp_path)
    store.metadata["v-root"] = {
        "artifact_id": "a-root",
        "filename": "result.csv",
        "checksum": "sum",
        "frame_id": "f1",
        "producing_cell_id": "c1",
    }
    store.edges = {"v-root": ["v-a", "v-b"], "v-a": ["v-c"]}

    assert service.lineage_get("v-root") == {
        "version_id": "v-root",
        "artifact_id": "a-root",
        "filename": "result.csv",
        "checksum": "sum",
        "frame_id": "f1",
        "producing_cell_id": "c1",
        "code": "answer = 42",
        "inputs": [{"version_id": "v-input"}],
        "extraction_pending": False,
    }
    assert service.lineage_graph(
        {"version_id": "v-root", "direction": "down", "max_depth": 1}
    ) == {
        "root": "v-root",
        "nodes": ["v-a", "v-b", "v-root"],
        "edges": [
            {"from": "v-root", "to": "v-a", "direction": "down"},
            {"from": "v-root", "to": "v-b", "direction": "down"},
        ],
    }


def test_provenance_soft_failure_and_dynamic_store_provider(tmp_path):
    first = FakeStore()
    second = FakeStore()
    current = {"store": first}
    service = HostDataService(
        store=lambda: current["store"],
        config=SimpleNamespace(artifacts_dir=tmp_path / "artifacts"),
        frame_id=None,
        resolve_path=lambda path, **_kwargs: Path(path),
    )

    current["store"] = second
    assert service.query_schema() == {"frames": ["frame_id"]}
    assert service.provenance_record({"path": str(tmp_path / "missing")}) == {
        "error": f"prov_record: no such output file: {tmp_path / 'missing'}"
    }
    assert first.calls == []


@pytest.mark.parametrize("version_id", ["short", "v-not-hex", "{{artifact:x}}"])
def test_artifact_marker_rejects_untrusted_ids(tmp_path, version_id):
    service, *_ = _service(tmp_path)

    with pytest.raises(ValueError, match="not a valid version id"):
        service.artifact_marker(version_id)
