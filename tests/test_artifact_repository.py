"""Direct contracts for artifact, version, environment, and lineage storage."""

from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.storage.artifacts import ArtifactRepository
from openai4s.store import get_store


def _repository(tmp_path, **overrides):
    store = get_store(Config(data_dir=tmp_path).db_path)
    ticks = itertools.count(1000)
    repository = ArtifactRepository(
        store._conn,
        store._lock,
        clock_ms=overrides.pop("clock_ms", lambda: next(ticks)),
        get_frame=overrides.pop(
            "get_frame", lambda frame_id: store.get_frame(frame_id)
        ),
        resolve_frame_scope=overrides.pop(
            "resolve_frame_scope",
            lambda frame_id, **kwargs: store.resolve_frame_scope(frame_id, **kwargs),
        ),
        **overrides,
    )
    return store, repository


def _save(repository, path: Path, **overrides):
    values = {
        "path": str(path),
        "filename": path.name,
        "content_type": "text/plain",
        "size_bytes": 4,
        "checksum": "hash",
    }
    values.update(overrides)
    return repository.save_artifact(**values)


def test_save_scopes_versions_and_exact_ownership_errors(tmp_path):
    store, repository = _repository(tmp_path)
    assert repository._connection is store._conn
    assert repository._lock is store._lock
    root = store.new_frame(kind="turn", project_id="science", status="ready")
    child = store.new_frame(
        parent_id=root,
        project_id="ignored",
        kind="delegate",
        status="ready",
    )
    first = _save(
        repository,
        tmp_path / "result.txt",
        frame_id=child,
        producing_cell_id="cell-1",
        is_user_upload=True,
        priority=2,
        snapshot_path="/snap/one",
    )

    assert first["created_at"] == 1000
    artifact = repository.get_artifact(first["artifact_id"])
    assert artifact["root_frame_id"] == root
    assert artifact["project_id"] == "science"
    assert artifact["is_user_upload"] == 1
    assert artifact["priority"] == 2
    assert repository.version_meta(first["version_id"])["frame_id"] == child
    assert repository.artifact_by_filename(
        "result.txt", root, strict=True
    )["artifact_id"] == first["artifact_id"]
    assert repository.artifact_by_filename(
        "result.txt", "wrong-root", strict=True
    ) is None
    assert repository.artifact_by_filename(
        "result.txt", "wrong-root"
    )["artifact_id"] == first["artifact_id"]

    second = _save(
        repository,
        tmp_path / "result.txt",
        filename="renamed.txt",
        artifact_id=first["artifact_id"],
    )
    assert second["created_at"] == 1001
    current = repository.get_artifact(first["artifact_id"])
    assert current["root_frame_id"] == root
    assert current["project_id"] == "science"
    assert current["filename"] == "result.txt"
    assert current["latest_version_id"] == second["version_id"]

    with pytest.raises(KeyError, match="no such artifact 'missing'"):
        _save(repository, tmp_path / "missing", artifact_id="missing")
    other = store.new_frame(kind="turn", project_id="other", status="ready")
    with pytest.raises(
        ValueError, match="artifact belongs to a different root frame"
    ):
        _save(
            repository,
            tmp_path / "wrong-root",
            artifact_id=first["artifact_id"],
            frame_id=other,
        )
    with pytest.raises(ValueError, match="project_id conflicts with producer frame"):
        _save(
            repository,
            tmp_path / "wrong-project",
            frame_id=child,
            project_id="other",
        )

    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM artifact_versions WHERE artifact_id=?",
            (first["artifact_id"],),
        ).fetchone() == (2,)


def test_late_bound_scope_getters_and_execute_callbacks_are_observable(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    calls = []
    ports = {
        "get_frame": lambda _frame_id: None,
        "resolve": lambda frame_id, **kwargs: {
            "frame_id": frame_id,
            "root_frame_id": "root-a",
            "project_id": "project-a",
        },
        "get_artifact": lambda artifact_id: {
            "artifact_id": artifact_id,
            "source": "callback",
        },
        "write_scope": lambda **kwargs: (True, "write-root-a", "write-project-a"),
        "execute": lambda sql, params: calls.append((sql, params)),
    }
    repository = ArtifactRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 77,
        get_frame=lambda frame_id: ports["get_frame"](frame_id),
        resolve_frame_scope=lambda frame_id, **kwargs: ports["resolve"](
            frame_id, **kwargs
        ),
        resolve_artifact_write_scope=lambda **kwargs: ports["write_scope"](**kwargs),
        get_artifact=lambda artifact_id: ports["get_artifact"](artifact_id),
        execute=lambda sql, params: ports["execute"](sql, params),
    )
    assert repository.artifact_write_scope(
        frame_id=None,
        root_frame_id="requested",
        project_id=None,
    ) == (True, "requested", "project-a")
    ports["resolve"] = lambda frame_id, **kwargs: {
        "frame_id": frame_id,
        "root_frame_id": "root-b",
        "project_id": "project-b",
    }
    assert repository.artifact_write_scope(
        frame_id=None,
        root_frame_id=None,
        project_id=None,
    ) == (False, "root-b", "project-b")

    saved = _save(repository, tmp_path / "late-bound.txt")
    stored = repository.list_artifacts({"artifact_id": saved["artifact_id"]})[0]
    assert (stored["root_frame_id"], stored["project_id"]) == (
        "write-root-a",
        "write-project-a",
    )
    ports["write_scope"] = lambda **kwargs: (
        True,
        "write-root-b",
        "write-project-b",
    )
    other = _save(repository, tmp_path / "late-bound-2.txt")
    stored = repository.list_artifacts({"artifact_id": other["artifact_id"]})[0]
    assert (stored["root_frame_id"], stored["project_id"]) == (
        "write-root-b",
        "write-project-b",
    )

    repository.set_priority("artifact-x", "3")
    assert calls == [
        (
            "UPDATE artifacts SET priority=?,updated_at=? WHERE artifact_id=?",
            (3, 77, "artifact-x"),
        )
    ]
    assert repository.set_priority("artifact-y", 4) == {
        "artifact_id": "artifact-y",
        "source": "callback",
    }


def test_record_cell_reuses_provisional_version_and_merges_lineage(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    source = _save(
        repository,
        tmp_path / "input.txt",
        frame_id=frame_id,
        checksum="input-hash",
    )
    provisional = repository.record_cell_artifact(
        path=str(tmp_path / "physical.csv"),
        filename="published/result.csv",
        content_type="application/x-science",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        input_version_ids=[source["version_id"], source["version_id"], ""],
        reuse_policy="provisional",
    )
    captured = repository.record_cell_artifact(
        path=str(tmp_path / "physical.csv"),
        filename="physical.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        env_snapshot_id="env-later",
        snapshot_path="/snap/result",
        input_version_ids=[source["version_id"]],
        preserve_filename=True,
        preserve_content_type=True,
    )

    assert captured["artifact_id"] == provisional["artifact_id"]
    assert captured["version_id"] == provisional["version_id"]
    assert captured["created_at"] == provisional["created_at"] == 1001
    assert captured["filename"] == "published/result.csv"
    assert captured["content_type"] == "application/x-science"
    metadata = repository.version_meta(captured["version_id"])
    assert metadata["snapshot_path"] == "/snap/result"
    assert metadata["env_snapshot_id"] == "env-later"
    assert repository.lineage_inputs(captured["version_id"]) == [
        {
            "version_id": source["version_id"],
            "filename": "input.txt",
            "path": str(tmp_path / "input.txt"),
        }
    ]
    assert len(repository.list_versions(captured["artifact_id"])) == 1

    repeated = repository.record_cell_artifact(
        path=str(tmp_path / "physical.csv"),
        filename="published/result.csv",
        content_type="application/x-science",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        reuse_policy="provisional",
    )
    assert repeated["artifact_id"] == captured["artifact_id"]
    assert repeated["version_id"] != captured["version_id"]
    assert len(repository.list_versions(captured["artifact_id"])) == 2

    with pytest.raises(
        ValueError,
        match="unknown cell artifact reuse policy: 'sometimes'",
    ):
        repository.record_cell_artifact(
            path="x",
            filename="x",
            content_type=None,
            size_bytes=0,
            checksum=None,
            producing_cell_id=None,
            frame_id=None,
            reuse_policy="sometimes",
        )


def test_record_cell_rolls_back_the_whole_transaction_on_lineage_failure(tmp_path):
    _store, repository = _repository(tmp_path)
    with pytest.raises(sqlite3.ProgrammingError):
        repository.record_cell_artifact(
            path="/tmp/rollback.txt",
            filename="rollback.txt",
            content_type="text/plain",
            size_bytes=1,
            checksum="x",
            producing_cell_id="cell-bad",
            frame_id=None,
            input_version_ids=[object()],
        )
    assert repository.list_artifacts({"filename": "rollback.txt"}) == []


def test_environment_snapshots_deduplicate_decode_and_bind_versions(tmp_path):
    store, repository = _repository(tmp_path)
    snapshot = {
        "kind": "python",
        "python_version": "3.14",
        "implementation": "CPython",
        "platform": "test",
        "package_count": 99,
        "packages": [{"name": "numpy", "version": "2"}],
        "remote": [{"provider": "gpu", "job": "42"}],
    }
    snapshot_id = repository.upsert_env_snapshot(snapshot)
    assert (
        repository.upsert_env_snapshot(dict(snapshot, package_count=1))
        == snapshot_id
    )
    decoded = repository.get_env_snapshot(snapshot_id)
    assert decoded["created_at"] == 1000
    assert decoded["packages"] == snapshot["packages"]
    assert decoded["remote"] == snapshot["remote"]
    assert decoded["package_count"] == 99

    first = _save(
        repository,
        tmp_path / "env.txt",
        env_snapshot_id=snapshot_id,
    )
    assert repository.env_snapshot_for_artifact(first["artifact_id"]) == decoded
    assert (
        repository.env_snapshot_for_artifact(
            first["artifact_id"], first["version_id"]
        )
        == decoded
    )
    assert repository.env_snapshot_for_artifact("wrong", first["version_id"]) is None

    with store._lock:
        store._conn.execute(
            "UPDATE env_snapshots SET packages_json=?,remote_json=? "
            "WHERE snapshot_id=?",
            ("not-json", "{", snapshot_id),
        )
        store._conn.commit()
    malformed = repository.get_env_snapshot(snapshot_id)
    assert malformed["packages"] == []
    assert malformed["remote"] == []
    assert "packages_json" not in malformed and "remote_json" not in malformed


def test_listing_paths_versions_priority_and_restore_contracts(tmp_path):
    _store, repository = _repository(tmp_path)
    real = tmp_path / "real.txt"
    alias = tmp_path / "alias.txt"
    real.write_text("data")
    alias.symlink_to(real)
    first = _save(
        repository,
        real,
        project_id="science",
        snapshot_path="/snap/first",
    )
    second = _save(
        repository,
        alias,
        filename="real.txt",
        project_id="science",
        artifact_id=first["artifact_id"],
        size_bytes=5,
        checksum="second",
    )

    assert repository.resolve_artifact_path(first["version_id"]) == "/snap/first"
    assert repository.resolve_artifact_path(first["artifact_id"]) == str(alias)
    assert repository.resolve_artifact_path("missing") is None
    assert repository.version_for_path(str(real)) == second["version_id"]
    versions = repository.list_versions(first["artifact_id"])
    assert [version["ordinal"] for version in versions] == [2, 1]
    assert [version["is_latest"] for version in versions] == [True, False]
    assert [version["version_id"] for version in versions] == [
        second["version_id"],
        first["version_id"],
    ]
    assert repository.list_artifacts({"project_id": "science"})[0][
        "artifact_id"
    ] == first["artifact_id"]
    assert repository.list_artifacts({"unknown": "ignored"})

    repository.update_version_path(
        first["version_id"], "/new/path", size_bytes=0, checksum=""
    )
    repository.set_version_snapshot(first["version_id"], "/new/snapshot")
    metadata = repository.version_meta(first["version_id"])
    assert (metadata["path"], metadata["size_bytes"], metadata["checksum"]) == (
        "/new/path",
        0,
        "",
    )
    assert metadata["snapshot_path"] == "/new/snapshot"
    assert repository.set_priority(first["artifact_id"], "-2")["priority"] == -2
    assert repository.set_latest_version(first["artifact_id"], first["version_id"])[
        "latest_version_id"
    ] == first["version_id"]
    assert repository.set_latest_version(first["artifact_id"], "missing") is None


def test_lineage_directions_missing_inputs_and_producing_cell(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    source = _save(repository, tmp_path / "source.txt", frame_id=frame_id)
    output = _save(
        repository,
        tmp_path / "output.txt",
        frame_id=frame_id,
        producing_cell_id="cell-output",
    )
    store.log_cell(
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id="science",
        code="make_output()",
        result={"id": "cell-output", "stdout": "", "error": None},
    )
    repository.add_lineage_edge(
        input_version_id=source["version_id"],
        output_version_id=output["version_id"],
        producing_cell_id="cell-output",
        frame_id=frame_id,
    )
    repository.add_lineage_edge(
        input_version_id="missing-version",
        output_version_id=output["version_id"],
    )

    assert repository.lineage_edges_for(output["version_id"], "up") == [
        source["version_id"],
        "missing-version",
    ]
    assert repository.lineage_edges_for(source["version_id"], "sideways") == [
        output["version_id"]
    ]
    assert repository.lineage_inputs(output["version_id"])[1] == {
        "version_id": "missing-version",
        "filename": None,
        "path": None,
    }
    assert repository.producing_cell_for_version(output["version_id"]) == {
        "code": "make_output()",
        "frame_id": frame_id,
        "producing_cell_id": "cell-output",
    }
    assert repository.producing_cell_for_version(source["version_id"]) is None


def test_rename_delete_cascade_and_shared_path_reclamation(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    shared = str(tmp_path / "shared.txt")
    first = _save(
        repository,
        Path(shared),
        frame_id=frame_id,
        snapshot_path="/snap/first",
    )
    second = _save(
        repository,
        Path(shared),
        filename="other.txt",
        frame_id=frame_id,
        snapshot_path="/snap/second",
    )
    annotation = store.add_annotation(
        root_frame_id=frame_id,
        artifact_id=first["artifact_id"],
        artifact_name="shared.txt",
        rel_x=0.1,
        rel_y=0.2,
        body="review",
    )

    repository.rename_artifact(first["artifact_id"], "renamed.txt")
    assert repository.get_artifact(first["artifact_id"])["filename"] == "renamed.txt"
    assert repository.version_meta(first["version_id"])["filename"] == "renamed.txt"
    stale = set(repository.delete_artifact(first["artifact_id"]))
    assert stale == {"/snap/first"}
    assert store.get_annotation(annotation["annotation_id"]) is None
    assert repository.delete_artifact("missing") == []
    assert set(repository.delete_artifact(second["artifact_id"])) == {
        shared,
        "/snap/second",
    }
