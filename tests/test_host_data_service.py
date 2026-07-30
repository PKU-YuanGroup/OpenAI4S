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
        #: artifact_id -> row. Scope lives on the parent `artifacts` row, not
        #: on the version, so a version-keyed read has to resolve it. The fake
        #: declared neither this nor `resolve_frame_scope` while the Protocol
        #: requires both, so it could only stand in for the unscoped calls.
        self.artifacts_by_id = {
            "a-root": {
                "artifact_id": "a-root",
                "root_frame_id": "frame-1",
                "project_id": "default",
            },
            "a-1": {
                "artifact_id": "a-1",
                "root_frame_id": "frame-1",
                "project_id": "default",
            },
        }
        self.scope = {
            "frame_id": "frame-1",
            "root_frame_id": "frame-1",
            "project_id": "default",
        }
        #: A lineage input this session really owns. `save_artifact` now resolves
        #: every declared `input_version_ids` entry through the scope check before
        #: it copies anything, so a fake that cannot answer `version_meta` for one
        #: is a fake that cannot stand in for the call at all.
        self.metadata.setdefault("v-input", {"artifact_id": "a-1"})

    def get_artifact(self, artifact_id):
        self.calls.append(("get_artifact", artifact_id))
        return self.artifacts_by_id.get(artifact_id)

    def resolve_frame_scope(self, frame_id):
        self.calls.append(("resolve_frame_scope", frame_id))
        return dict(self.scope)

    def query(self, sql, *, params=None, limit=None, timeout_s=5.0, scope=None):
        # `scope` is what publishes the session-scoped `my_*` views on the real
        # store. It used to be accepted by the SDK and dropped, so the base
        # artifact tables were readable directly across every project.
        self.calls.append(("query", sql, params, limit, timeout_s, scope))
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
    # The scope reaches the store, and it is the session's own rather than
    # anything the caller sent: `spec["scope"]` is deliberately not read, because
    # a value the caller chooses cannot be what confines the caller. It used to be
    # dropped entirely here, so the `my_*` views did not exist and the base
    # artifact tables were readable directly, across every project.
    scope = {
        "frame_id": "frame-1",
        "root_frame_id": "frame-1",
        "project_id": "default",
    }
    assert store.calls == [
        ("resolve_frame_scope", "frame-1"),
        ("query", "SELECT a,b", [1], 9, 5.0, scope),
    ]
    assert service.query_schema() == {"frames": ["frame_id"]}


def test_a_caller_supplied_scope_is_ignored(tmp_path):
    """The SDK accepts `scope=` and it must not be load-bearing.

    If the value the caller passes decided which rows the views expose, the
    confinement would be advisory.
    """
    service, store, _workspace, _config = _service(tmp_path)
    store.query_rows = []

    service.query(
        {"sql": "SELECT 1", "scope": {"root_frame_id": "frame-999", "project_id": "x"}}
    )

    call = next(c for c in store.calls if c[0] == "query")
    assert call[5] == {
        "frame_id": "frame-1",
        "root_frame_id": "frame-1",
        "project_id": "default",
    }


def test_artifact_search_keeps_filter_mutation_and_ranking(tmp_path):
    service, store, _workspace, _config = _service(tmp_path)
    store.artifact_rows = [
        {"filename": "protein_scores.csv", "content_type": "text/csv", "priority": 0},
        {"filename": "protein_notes.txt", "content_type": "text/plain", "priority": 2},
        {"filename": "unrelated.png", "content_type": "image/png", "priority": 0},
    ]
    filters = {"search": "protein", "project_id": "p1"}

    result = service.artifacts(filters)

    # The caller's `project_id` is overwritten by the session's own scope --
    # that confinement has always been the intent (see `artifacts`), but the
    # fake did not implement `resolve_frame_scope`, so the branch never ran and
    # this asserted the unscoped shape.
    assert filters == {"root_frame_id": "frame-1", "project_id": "default"}
    assert ("resolve_frame_scope", "frame-1") in store.calls
    assert (
        "list_artifacts",
        {"root_frame_id": "frame-1", "project_id": "default"},
    ) in store.calls
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
        # Absent from this call, and forwarded as None rather than dropped:
        # the store distinguishes "no retrieval" from "not passed on".
        "source": None,
        "reuse_policy": "provisional",
    }
    assert ("set_priority", "a-1", 3) in store.calls
    assert result["artifact_id"] == "a-1"


def test_save_artifact_forwards_the_retrieval_envelope(tmp_path):
    """`source` is what lets a saved file say what it is evidence of. A hop
    that quietly dropped it would leave the artifact looking computed from
    nothing."""
    service, store, workspace, _config = _service(tmp_path)
    source = workspace / "data.txt"
    source.write_text("science", encoding="utf-8")
    envelope = {
        "database": "uniprot",
        "retrieved_at": 1,
        "response_sha256": "a" * 64,
    }

    service.save_artifact(
        {"path": source.name, "filename": "data.txt", "source": envelope}
    )

    record = next(call for call in store.calls if call[0] == "record_cell_artifact")
    assert record[1]["source"] == envelope


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
    # `v-a -> v-c` exists but is past the depth limit, so the graph is partial
    # and says so. It used to return the same nodes with nothing to indicate
    # that a reachable edge had been left out -- a lineage claim that is wrong
    # rather than incomplete.
    assert service.lineage_graph(
        {"version_id": "v-root", "direction": "down", "max_depth": 1}
    ) == {
        "root": "v-root",
        "nodes": ["v-a", "v-b", "v-root"],
        "edges": [
            {"from": "v-root", "to": "v-a", "direction": "down"},
            {"from": "v-root", "to": "v-b", "direction": "down"},
        ],
        "truncated": True,
    }

    # A walk that reaches the end of the graph makes no such claim.
    assert "truncated" not in service.lineage_graph(
        {"version_id": "v-root", "direction": "down"}
    )


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


def test_view_image_confines_a_caller_supplied_path_to_the_workspace(tmp_path):
    """`host.view_image(path=...)` was an existence oracle for the whole host.

    Every sibling file operation goes through the workspace resolver. This one
    checked `Path(path).exists()` and returned the path, so a kernel cell could
    ask about any absolute path on the machine and read the answer off the
    difference between a result and a `FileNotFoundError` -- `/etc/passwd`,
    `~/.ssh/id_rsa`, a colleague's data directory.

    The `version_id` branch is deliberately not confined: an artifact snapshot
    legitimately lives under the data dir, outside the workspace. Its scope
    check belongs with the other artifact read paths.
    """
    from openai4s.config import Config, LLMConfig
    from openai4s.host_dispatch import HostDispatcher

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = HostDispatcher(cfg=cfg, frame_id="frame-1")
    workspace = dispatcher._workspace()

    inside = workspace / "figure.png"
    inside.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert dispatcher("view_image", [{"path": "figure.png"}])["rendered"] is True

    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="escapes the workspace"):
        dispatcher("view_image", [{"path": str(outside)}])

    # The canary that made this worth fixing: a real host path the caller
    # never had any business naming.
    with pytest.raises(ValueError, match="escapes the workspace"):
        dispatcher("view_image", [{"path": "/etc/passwd"}])

    # A traversal spelled relatively is the same escape.
    with pytest.raises(ValueError, match="escapes the workspace"):
        dispatcher("view_image", [{"path": "../secret.png"}])
