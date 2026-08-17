"""Direct contracts for store-backed host data capabilities."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.host.data import HostDataService, rank_artifacts
from openai4s.store import get_store


class FakeStore:
    def __init__(self) -> None:
        self.calls = []
        self.artifact_rows = []
        self.query_rows = []
        self.paths = {}
        self.path_scopes: list = []
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

    # `visible_to_user_id` is recorded rather than ignored: these doubles are
    # how the tests below assert that the host path *passes* a scope at all,
    # which is the thing it did not do.
    def frame_detail(self, frame_id, *, page, page_size, visible_to_user_id=None):
        self.calls.append(
            ("frame_detail", frame_id, page, page_size, visible_to_user_id)
        )
        return self.frame_details.get(frame_id)

    def search_frames(self, pattern, *, project_id, limit, visible_to_user_id=None):
        self.calls.append(
            ("search_frames", pattern, project_id, limit, visible_to_user_id)
        )
        return [{"frame_id": "search"}]

    def browse_frames(
        self, *, project_id, status, roots_only, limit, visible_to_user_id=None
    ):
        self.calls.append(
            ("browse_frames", project_id, status, roots_only, limit, visible_to_user_id)
        )
        return [{"frame_id": "browse"}]

    def producing_cell_for_version(self, version_id):
        return {"code": "answer = 42"}

    def lineage_inputs(self, version_id):
        return [{"version_id": "v-input"}]

    def lineage_edges_for(self, version_id, direction):
        self.calls.append(("lineage_edges_for", version_id, direction))
        return self.edges.get(version_id, [])

    def version_for_path(self, path, *, root_frame_id, project_id):
        # Required, not defaulted, so this fake cannot keep accepting the
        # unscoped call that production can no longer make -- which is how a
        # fake comes to certify a signature the real store has dropped.
        self.path_scopes.append((path, root_frame_id, project_id))
        return self.paths.get(path)


def _service(
    tmp_path: Path,
    store: FakeStore | None = None,
    *,
    trusted_delivery: bool = False,
):
    actual_store = store or FakeStore()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(
        artifacts_dir=tmp_path / "artifacts",
        roadmap_features=SimpleNamespace(
            stage1_trusted_delivery=trusted_delivery,
        ),
    )

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


def _real_service(tmp_path: Path, *, trusted_delivery: bool):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Config(
        data_dir=tmp_path / "data",
        roadmap_features=RoadmapFeatureFlags(
            stage1_trusted_delivery=trusted_delivery,
        ),
    )
    store = get_store(config.db_path)
    frame_id = store.new_frame(project_id="science", status="ready")
    workspace_root = workspace.resolve()

    def resolve(path, *, must_exist=False):
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        result = candidate.resolve()
        if result != workspace_root and workspace_root not in result.parents:
            raise ValueError(f"path escapes the workspace: {path}")
        if must_exist and not result.exists():
            raise FileNotFoundError(result)
        return result

    service = HostDataService(
        store=store,
        config=config,
        frame_id=frame_id,
        resolve_path=resolve,
    )
    return service, store, workspace, config, frame_id


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


def test_flag_off_provenance_record_preserves_legacy_record_shape(tmp_path):
    service, store, workspace, config = _service(tmp_path, trusted_delivery=False)
    source = workspace / "legacy-result.bin"
    source.write_bytes(b"legacy-bytes")

    result = service.provenance_record(
        {
            "path": source.name,
            "filename": "published.bin",
            "content_type": "application/octet-stream",
            "producing_cell_id": "cell-legacy",
        }
    )

    assert result == store.version
    record = next(call for call in store.calls if call[0] == "record_cell_artifact")
    assert record[1] == {
        "path": str(source),
        "filename": "published.bin",
        "content_type": "application/octet-stream",
        "size_bytes": len(b"legacy-bytes"),
        "checksum": hashlib.sha256(b"legacy-bytes").hexdigest(),
        "producing_cell_id": "cell-legacy",
        "frame_id": "frame-1",
        "input_version_ids": [],
    }
    assert not config.artifacts_dir.exists()


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_flag_off_real_host_capture_keeps_legacy_response_and_no_observation(
    tmp_path, operation
):
    service, store, workspace, _config, _frame_id = _real_service(
        tmp_path,
        trusted_delivery=False,
    )
    source = workspace / "legacy-real.dat"
    source.write_bytes(b"legacy-real-bytes")

    try:
        if operation == "save_artifact":
            result = service.save_artifact(
                {
                    "path": source.name,
                    "execution_cell_id": "cell-legacy",
                }
            )
        else:
            result = service.provenance_record(
                {
                    "path": source.name,
                    "producing_cell_id": "cell-legacy",
                }
            )

        assert set(result) == {
            "artifact_id",
            "version_id",
            "filename",
            "path",
            "content_type",
            "size_bytes",
            "checksum",
            "created_at",
        }
        assert (
            store.list_artifact_capture_observations(version_id=result["version_id"])
            == []
        )
        metadata = store.version_meta(result["version_id"])
        if operation == "save_artifact":
            assert Path(metadata["snapshot_path"]).read_bytes() == b"legacy-real-bytes"
        else:
            assert metadata["snapshot_path"] is None
            assert result["path"] == str(source)
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_trusted_host_capture_freezes_exact_bytes_before_the_store_call(
    tmp_path, monkeypatch, operation
):
    service, store, workspace, config = _service(
        tmp_path,
        trusted_delivery=True,
    )
    source = workspace / "result.dat"
    payload = b"trusted-exact-bytes\x00\xff"
    source.write_bytes(payload)
    original_record = store.record_cell_artifact
    observed = []

    def inspect_record(**fields):
        snapshot = Path(fields["snapshot_path"])
        snapshot_bytes = snapshot.read_bytes()
        observed.append((snapshot, dict(fields)))
        assert snapshot_bytes == payload
        assert fields["size_bytes"] == len(snapshot_bytes)
        assert fields["checksum"] == hashlib.sha256(snapshot_bytes).hexdigest()
        assert fields["reuse_matching_head"] is True
        store.metadata[store.version["version_id"]] = {
            "snapshot_path": str(snapshot),
        }
        return original_record(**fields)

    monkeypatch.setattr(store, "record_cell_artifact", inspect_record)
    spec = {
        "path": source.name,
        "filename": "published.dat",
        "content_type": "application/octet-stream",
    }
    if operation == "save_artifact":
        spec["execution_cell_id"] = "cell-trusted"
        result = service.save_artifact(spec)
    else:
        spec["producing_cell_id"] = "cell-trusted"
        result = service.provenance_record(spec)

    assert result["version_id"] == store.version["version_id"]
    assert len(observed) == 1
    snapshot, fields = observed[0]
    assert snapshot.parent == config.artifacts_dir
    assert snapshot.is_file()
    source.write_bytes(b"later-mutable-workspace-bytes")
    assert snapshot.read_bytes() == payload
    if operation == "save_artifact":
        assert fields["reuse_policy"] == "provisional"
    else:
        assert "reuse_policy" not in fields


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_trusted_host_capture_reuses_head_bytes_but_audits_each_cell(
    tmp_path, operation
):
    service, store, workspace, config, frame_id = _real_service(
        tmp_path,
        trusted_delivery=True,
    )
    source = workspace / "same.dat"
    payload = b"same-scientific-result"
    source.write_bytes(payload)

    try:
        if operation == "save_artifact":
            first = service.save_artifact(
                {
                    "path": source.name,
                    "execution_cell_id": "cell-first",
                }
            )
            second = service.save_artifact(
                {
                    "path": source.name,
                    "execution_cell_id": "cell-second",
                }
            )
        else:
            first = service.provenance_record(
                {
                    "path": source.name,
                    "producing_cell_id": "cell-first",
                }
            )
            second = service.provenance_record(
                {
                    "path": source.name,
                    "producing_cell_id": "cell-second",
                }
            )

        assert first["version_id"] == second["version_id"]
        artifact = store.artifact_by_filename(source.name, frame_id, strict=True)
        assert artifact is not None
        assert len(store.list_versions(artifact["artifact_id"])) == 1
        observations = store.list_artifact_capture_observations(
            version_id=first["version_id"]
        )
        assert [row["producing_cell_id"] for row in observations] == [
            "cell-first",
            "cell-second",
        ]
        assert observations[0]["capture_kind"] == "version_created"
        assert observations[1]["capture_kind"] == "head_checksum_reused"

        metadata = store.version_meta(first["version_id"])
        snapshot = Path(metadata["snapshot_path"])
        assert metadata["checksum"] == hashlib.sha256(payload).hexdigest()
        assert snapshot.read_bytes() == payload
        source.write_bytes(b"mutable-workspace-after-capture")
        assert snapshot.read_bytes() == payload
        assert list(config.artifacts_dir.iterdir()) == [snapshot]
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_trusted_host_freeze_fault_never_reaches_the_store_or_leaves_bytes(
    tmp_path, monkeypatch, operation
):
    service, store, workspace, config = _service(
        tmp_path,
        trusted_delivery=True,
    )
    source = workspace / "freeze-fault.dat"
    source.write_bytes(b"must-not-be-claimed")

    def fail_fsync(_descriptor):
        raise OSError("injected freeze fault")

    monkeypatch.setattr("openai4s.host.data.os.fsync", fail_fsync)
    spec = {"path": source.name, "producing_cell_id": "cell-fault"}
    if operation == "save_artifact":
        spec = {"path": source.name, "execution_cell_id": "cell-fault"}
        with pytest.raises(OSError, match="injected freeze fault"):
            service.save_artifact(spec)
    else:
        result = service.provenance_record(spec)
        assert result == {"error": f"prov_record: {source.name}: injected freeze fault"}

    assert not any(call[0] == "record_cell_artifact" for call in store.calls)
    assert config.artifacts_dir.is_dir()
    assert list(config.artifacts_dir.iterdir()) == []


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_trusted_host_rejects_mid_freeze_rewrite_with_restored_mtime(
    tmp_path, monkeypatch, operation
):
    service, store, workspace, config, frame_id = _real_service(
        tmp_path,
        trusted_delivery=True,
    )
    source = workspace / "mid-freeze.dat"
    original = b"A" * (1024 * 1024 + 4096)
    replacement = b"B" * len(original)
    source.write_bytes(original)
    source_stat = source.stat()
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    native_read = os.read
    mutated = False

    def rewrite_after_first_source_read(descriptor, size):
        nonlocal mutated
        chunk = native_read(descriptor, size)
        descriptor_stat = os.fstat(descriptor)
        if (
            chunk
            and not mutated
            and (descriptor_stat.st_dev, descriptor_stat.st_ino) == source_identity
        ):
            mutated = True
            with source.open("r+b", buffering=0) as stream:
                stream.write(replacement)
                os.fsync(stream.fileno())
            os.utime(
                source,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        return chunk

    monkeypatch.setattr("openai4s.host.data.os.read", rewrite_after_first_source_read)

    try:
        if operation == "save_artifact":
            with pytest.raises(OSError, match="changed during snapshot freeze"):
                service.save_artifact(
                    {
                        "path": source.name,
                        "execution_cell_id": "cell-mid-freeze",
                    }
                )
        else:
            result = service.provenance_record(
                {
                    "path": source.name,
                    "producing_cell_id": "cell-mid-freeze",
                }
            )
            assert result == {
                "error": (
                    f"prov_record: {source.name}: "
                    "artifact source changed during snapshot freeze"
                )
            }

        assert mutated is True
        assert source.stat().st_size == len(original)
        assert source.stat().st_mtime_ns == source_stat.st_mtime_ns
        assert (
            store.list_artifacts({"root_frame_id": frame_id, "project_id": "science"})
            == []
        )
        assert store.list_artifact_capture_observations() == []
        assert (
            store._conn.execute(  # noqa: SLF001 - assert no hidden version row
                "SELECT COUNT(*) FROM artifact_versions"
            ).fetchone()[0]
            == 0
        )
        assert config.artifacts_dir.is_dir()
        assert list(config.artifacts_dir.iterdir()) == []
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["save_artifact", "provenance_record"])
def test_trusted_host_store_fault_removes_prefreeze_and_persists_nothing(
    tmp_path, operation
):
    service, store, workspace, config, frame_id = _real_service(
        tmp_path,
        trusted_delivery=True,
    )
    source = workspace / "store-fault.dat"
    source.write_bytes(b"must-not-survive")
    store._conn.execute(
        "CREATE TRIGGER fail_host_capture_observation BEFORE INSERT "
        "ON artifact_capture_observations "
        "BEGIN SELECT RAISE(ABORT, 'injected store fault'); END"
    )
    store._conn.commit()
    try:
        spec = {"path": source.name, "producing_cell_id": "cell-fault"}
        if operation == "save_artifact":
            spec = {"path": source.name, "execution_cell_id": "cell-fault"}
            call = service.save_artifact
        else:
            call = service.provenance_record
        with pytest.raises(sqlite3.IntegrityError, match="injected store fault"):
            call(spec)

        assert list(config.artifacts_dir.iterdir()) == []
        assert (
            store.list_artifacts({"root_frame_id": frame_id, "project_id": "science"})
            == []
        )
        assert store.list_artifact_capture_observations() == []
        assert (
            store._conn.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0]
            == 0
        )
    finally:
        store.close()


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
