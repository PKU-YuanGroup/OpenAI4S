"""Direct contracts for artifact, version, environment, and lineage storage."""

from __future__ import annotations

import itertools
import sqlite3
import threading
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.storage.artifact_observations import (
    CAPTURE_KIND_HEAD_CHECKSUM_REUSED,
    CAPTURE_KIND_SAME_CELL_MERGE,
    CAPTURE_KIND_VERSION_CREATED,
)
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
    assert (
        repository.artifact_by_filename("result.txt", root, strict=True)["artifact_id"]
        == first["artifact_id"]
    )
    assert (
        repository.artifact_by_filename("result.txt", "wrong-root", strict=True) is None
    )
    assert (
        repository.artifact_by_filename("result.txt", "wrong-root")["artifact_id"]
        == first["artifact_id"]
    )

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
    with pytest.raises(ValueError, match="artifact belongs to a different root frame"):
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


def test_capture_observations_merge_same_cell_and_keep_producer_lineage(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    first_input = _save(
        repository,
        tmp_path / "input-a.txt",
        frame_id=frame_id,
        checksum="input-a",
    )
    second_input = _save(
        repository,
        tmp_path / "input-b.txt",
        frame_id=frame_id,
        checksum="input-b",
    )
    provisional = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type=None,
        size_bytes=5,
        checksum="result-hash",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        input_version_ids=[first_input["version_id"]],
        reuse_matching_head=True,
    )
    captured = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="result-hash",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        env_snapshot_id="env-cell-1",
        snapshot_path="/snap/result.csv",
        input_version_ids=[
            first_input["version_id"],
            second_input["version_id"],
        ],
        source={"phase": "captured"},
        reuse_matching_head=True,
    )

    assert provisional["version_created"] is True
    assert provisional["capture_kind"] == CAPTURE_KIND_VERSION_CREATED
    assert captured["version_created"] is False
    assert captured["capture_kind"] == CAPTURE_KIND_SAME_CELL_MERGE
    assert captured["observation_id"] == provisional["observation_id"]
    assert captured["observation_ordinal"] == provisional["observation_ordinal"]
    assert captured["ordinal"] == captured["observation_ordinal"]
    observations = repository.list_capture_observations(
        version_id=captured["version_id"]
    )
    assert len(observations) == 1
    assert observations[0]["capture_kind"] == CAPTURE_KIND_SAME_CELL_MERGE
    assert observations[0]["env_snapshot_id"] == "env-cell-1"
    assert observations[0]["source"] == '{"phase":"captured"}'
    assert observations[0]["input_version_ids"] == [
        first_input["version_id"],
        second_input["version_id"],
    ]


def test_matching_head_dedup_is_opt_in_and_preserves_version_provenance(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    first_input = _save(
        repository,
        tmp_path / "input-a.txt",
        frame_id=frame_id,
        checksum="input-a",
    )
    second_input = _save(
        repository,
        tmp_path / "input-b.txt",
        frame_id=frame_id,
        checksum="input-b",
    )
    first = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        env_snapshot_id="env-original",
        input_version_ids=[first_input["version_id"]],
        source={"producer": 1},
    )
    default_second = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-2",
        frame_id=frame_id,
        env_snapshot_id="env-default",
        input_version_ids=[first_input["version_id"]],
        source={"producer": 2},
    )
    assert "version_created" not in default_second
    assert "observation_id" not in default_second
    assert default_second["version_id"] != first["version_id"]

    original = repository.version_meta(default_second["version_id"])
    reused = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-3",
        frame_id=frame_id,
        env_snapshot_id="env-cell-3",
        snapshot_path="/snap/cell-3-result.csv",
        input_version_ids=[
            first_input["version_id"],
            second_input["version_id"],
        ],
        source={"producer": 3},
        reuse_matching_head=True,
    )
    repeated_reuse = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-3",
        frame_id=frame_id,
        env_snapshot_id="env-cell-3",
        snapshot_path="/snap/must-not-replace-existing.csv",
        input_version_ids=[first_input["version_id"]],
        source={"producer": 3},
        reuse_matching_head=True,
    )

    assert reused["version_created"] is False
    assert reused["capture_kind"] == CAPTURE_KIND_HEAD_CHECKSUM_REUSED
    assert reused["version_id"] == default_second["version_id"]
    assert repeated_reuse["version_id"] == reused["version_id"]
    assert repeated_reuse["observation_id"] == reused["observation_id"]
    assert repeated_reuse["observation_ordinal"] == reused["observation_ordinal"]
    assert len(repository.list_versions(first["artifact_id"])) == 2
    after = repository.version_meta(reused["version_id"])
    assert after["producing_cell_id"] == original["producing_cell_id"] == "cell-2"
    assert after["frame_id"] == original["frame_id"] == frame_id
    assert after["env_snapshot_id"] == original["env_snapshot_id"] == "env-default"
    assert after["source"] == original["source"] == '{"producer":2}'
    assert after["snapshot_path"] == "/snap/cell-3-result.csv"

    observations = repository.list_capture_observations(
        artifact_id=first["artifact_id"]
    )
    assert [row["ordinal"] for row in observations] == sorted(
        row["ordinal"] for row in observations
    )
    assert len({row["ordinal"] for row in observations}) == len(observations)
    assert observations[-1]["producing_cell_id"] == "cell-3"
    assert observations[-1]["env_snapshot_id"] == "env-cell-3"
    assert observations[-1]["source"] == '{"producer":3}'
    assert observations[-1]["snapshot_path"] == "/snap/cell-3-result.csv"
    assert observations[-1]["input_version_ids"] == [
        first_input["version_id"],
        second_input["version_id"],
    ]
    assert repository.lineage_inputs(reused["version_id"]) == [
        {
            "version_id": first_input["version_id"],
            "filename": "input-a.txt",
            "path": str(tmp_path / "input-a.txt"),
        },
        {
            "version_id": second_input["version_id"],
            "filename": "input-b.txt",
            "path": str(tmp_path / "input-b.txt"),
        },
    ]
    edge_producers = repository._connection.execute(
        "SELECT producing_cell_id,input_version_id FROM lineage_edges "
        "WHERE output_version_id=? ORDER BY producing_cell_id,input_version_id",
        (reused["version_id"],),
    ).fetchall()
    assert [tuple(row) for row in edge_producers] == sorted(
        [
            ("cell-2", first_input["version_id"]),
            ("cell-3", first_input["version_id"]),
            ("cell-3", second_input["version_id"]),
        ]
    )


def test_artifact_names_for_frame_includes_same_byte_capture_observation(tmp_path):
    store, repository = _repository(tmp_path)
    root = store.new_frame(kind="turn", project_id="science", status="ready")
    first_frame = store.new_frame(
        parent_id=root, kind="delegate", project_id="science", status="ready"
    )
    second_frame = store.new_frame(
        parent_id=root, kind="delegate", project_id="science", status="ready"
    )
    original = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-1",
        frame_id=first_frame,
        reuse_matching_head=True,
    )
    reused = repository.record_cell_artifact(
        path=str(tmp_path / "result.csv"),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum="same-bytes",
        producing_cell_id="cell-2",
        frame_id=second_frame,
        reuse_matching_head=True,
    )

    assert reused["version_id"] == original["version_id"]
    assert repository.artifact_names_for_frame(first_frame) == ["result.csv"]
    assert repository.artifact_names_for_frame(second_frame) == ["result.csv"]


def test_matching_head_from_non_cell_producer_reuses_bytes_and_observes_cell(
    tmp_path,
):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    original = _save(
        repository,
        tmp_path / "uploaded.csv",
        frame_id=frame_id,
        producing_cell_id=None,
        checksum="same-uploaded-bytes",
        is_user_upload=True,
        snapshot_path="/snap/uploaded.csv",
    )

    observed = repository.record_cell_artifact(
        path=str(tmp_path / "uploaded.csv"),
        filename="uploaded.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="same-uploaded-bytes",
        producing_cell_id="cell-after-upload",
        frame_id=frame_id,
        env_snapshot_id="env-cell-after-upload",
        snapshot_path="/snap/must-not-replace-upload.csv",
        source={"producer": "cell-after-upload"},
        reuse_matching_head=True,
    )

    assert observed["version_created"] is False
    assert observed["capture_kind"] == CAPTURE_KIND_HEAD_CHECKSUM_REUSED
    assert observed["artifact_id"] == original["artifact_id"]
    assert observed["version_id"] == original["version_id"]
    assert len(repository.list_versions(original["artifact_id"])) == 1
    version = repository.version_meta(original["version_id"])
    assert version["producing_cell_id"] is None
    assert version["snapshot_path"] == "/snap/uploaded.csv"
    observations = repository.list_capture_observations(
        version_id=original["version_id"]
    )
    assert len(observations) == 1
    assert observations[0]["producing_cell_id"] == "cell-after-upload"
    assert observations[0]["env_snapshot_id"] == "env-cell-after-upload"
    assert observations[0]["source"] == '{"producer":"cell-after-upload"}'


def test_matching_head_dedup_never_searches_historical_versions(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    first = repository.record_cell_artifact(
        path=str(tmp_path / "history.txt"),
        filename="history.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="old-head",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        reuse_matching_head=True,
    )
    current = repository.record_cell_artifact(
        path=str(tmp_path / "history.txt"),
        filename="history.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="current-head",
        producing_cell_id="cell-2",
        frame_id=frame_id,
        reuse_matching_head=True,
    )
    replay_old_bytes = repository.record_cell_artifact(
        path=str(tmp_path / "history.txt"),
        filename="history.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="old-head",
        producing_cell_id="cell-3",
        frame_id=frame_id,
        reuse_matching_head=True,
    )

    assert current["version_id"] != first["version_id"]
    assert replay_old_bytes["version_created"] is True
    assert replay_old_bytes["version_id"] not in {
        first["version_id"],
        current["version_id"],
    }
    assert len(repository.list_versions(first["artifact_id"])) == 3


def test_concurrent_matching_head_captures_create_one_version_and_two_observations(
    tmp_path,
):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    barrier = threading.Barrier(2)
    records: list[dict] = []
    failures: list[BaseException] = []

    def capture(cell_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            record = repository.record_cell_artifact(
                path=str(tmp_path / "parallel.csv"),
                filename="parallel.csv",
                content_type="text/csv",
                size_bytes=4,
                checksum="same-parallel-bytes",
                producing_cell_id=cell_id,
                frame_id=frame_id,
                snapshot_path=f"/snap/{cell_id}",
                reuse_matching_head=True,
            )
            records.append(record)
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    threads = [
        threading.Thread(target=capture, args=(cell_id,))
        for cell_id in ("cell-parallel-a", "cell-parallel-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(records) == 2
    assert len({record["version_id"] for record in records}) == 1
    artifact_id = records[0]["artifact_id"]
    assert len(repository.list_versions(artifact_id)) == 1
    observations = repository.list_capture_observations(artifact_id=artifact_id)
    assert {row["producing_cell_id"] for row in observations} == {
        "cell-parallel-a",
        "cell-parallel-b",
    }
    assert sorted(row["capture_kind"] for row in observations) == [
        CAPTURE_KIND_HEAD_CHECKSUM_REUSED,
        CAPTURE_KIND_VERSION_CREATED,
    ]


def test_capture_cursor_delta_is_scope_bound_and_returns_exact_version(tmp_path):
    store, repository = _repository(tmp_path)
    root_a = store.new_frame(kind="turn", project_id="science", status="ready")
    root_b = store.new_frame(kind="turn", project_id="science", status="ready")
    first = repository.record_cell_artifact(
        path=str(tmp_path / "a.txt"),
        filename="a.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="a-v1",
        producing_cell_id="cell-a-1",
        frame_id=root_a,
        snapshot_path="/snap/a-v1",
        reuse_matching_head=True,
    )
    cursor = repository.capture_observation_cursor(
        root_frame_id=root_a,
        project_id="science",
    )
    second = repository.record_cell_artifact(
        path=str(tmp_path / "a.txt"),
        filename="a.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="a-v2",
        producing_cell_id="cell-a-2",
        frame_id=root_a,
        snapshot_path="/snap/a-v2",
        reuse_matching_head=True,
    )
    repository.record_cell_artifact(
        path=str(tmp_path / "private-b.txt"),
        filename="private-b.txt",
        content_type="text/plain",
        size_bytes=7,
        checksum="private-b",
        producing_cell_id="cell-b-1",
        frame_id=root_b,
        reuse_matching_head=True,
    )

    assert cursor == first["observation_ordinal"]
    delta = repository.capture_observations_since(
        cursor,
        root_frame_id=root_a,
        project_id="science",
    )
    assert len(delta) == 1
    assert delta[0]["observation_id"] == second["observation_id"]
    assert delta[0]["observation_ordinal"] == second["observation_ordinal"]
    assert delta[0]["artifact_id"] == second["artifact_id"]
    assert delta[0]["version_id"] == second["version_id"]
    assert delta[0]["filename"] == "a.txt"
    assert delta[0]["checksum"] == "a-v2"
    assert delta[0]["size_bytes"] == 3
    assert delta[0]["snapshot_path"] == "/snap/a-v2"
    assert delta[0]["capture_path"] == str(tmp_path / "a.txt")
    assert "private-b" not in repr(delta)
    assert (
        repository.capture_observations_since(
            0,
            root_frame_id=root_a,
            project_id="other-project",
        )
        == []
    )
    with pytest.raises(ValueError, match="project_id is required"):
        repository.capture_observations_since(
            0,
            root_frame_id=root_a,
            project_id="",
        )


def test_observation_failure_rolls_back_version_lineage_and_artifact(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    source = _save(
        repository,
        tmp_path / "input.txt",
        frame_id=frame_id,
        checksum="input",
    )
    repository._connection.execute(
        "CREATE TRIGGER fail_capture_observation BEFORE INSERT "
        "ON artifact_capture_observations "
        "BEGIN SELECT RAISE(ABORT, 'observation failed'); END"
    )
    repository._connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="observation failed"):
        repository.record_cell_artifact(
            path=str(tmp_path / "rolled-back.txt"),
            filename="rolled-back.txt",
            content_type="text/plain",
            size_bytes=1,
            checksum="output",
            producing_cell_id="cell-fail-observation",
            frame_id=frame_id,
            input_version_ids=[source["version_id"]],
            reuse_matching_head=True,
        )

    assert (
        repository.artifact_by_filename("rolled-back.txt", frame_id, strict=True)
        is None
    )
    assert (
        repository._connection.execute(
            "SELECT COUNT(*) FROM artifact_versions WHERE producing_cell_id=?",
            ("cell-fail-observation",),
        ).fetchone()[0]
        == 0
    )
    assert (
        repository._connection.execute(
            "SELECT COUNT(*) FROM lineage_edges WHERE producing_cell_id=?",
            ("cell-fail-observation",),
        ).fetchone()[0]
        == 0
    )


def test_capture_delta_does_not_silently_drop_the_1001st_reused_cell(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    head = repository.record_cell_artifact(
        path=str(tmp_path / "many.txt"),
        filename="many.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-base",
        frame_id=frame_id,
        snapshot_path="/snap/many",
        reuse_matching_head=True,
    )
    cursor = repository.capture_observation_cursor(
        root_frame_id=frame_id,
        project_id="science",
    )
    for ordinal in range(1001):
        reused = repository.record_cell_artifact(
            path=str(tmp_path / "many.txt"),
            filename="many.txt",
            content_type="text/plain",
            size_bytes=4,
            checksum="same",
            producing_cell_id=f"cell-reuse-{ordinal:04d}",
            frame_id=frame_id,
            snapshot_path="/snap/many",
            reuse_matching_head=True,
        )
        assert reused["version_id"] == head["version_id"]

    delta = repository.capture_observations_since(
        cursor,
        root_frame_id=frame_id,
        project_id="science",
    )
    assert len(delta) == 1001
    assert delta[-1]["producing_cell_id"] == "cell-reuse-1000"


def test_reused_head_rolls_back_snapshot_pointer_when_observation_fails(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    source = _save(
        repository,
        tmp_path / "input.txt",
        frame_id=frame_id,
        checksum="input",
    )
    head = repository.record_cell_artifact(
        path=str(tmp_path / "same.txt"),
        filename="same.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-1",
        frame_id=frame_id,
        reuse_matching_head=True,
    )
    artifact_before = repository.get_artifact(head["artifact_id"])
    repository._connection.execute(
        "CREATE TRIGGER fail_reused_capture BEFORE INSERT "
        "ON artifact_capture_observations "
        "WHEN NEW.producing_cell_id='cell-2' "
        "BEGIN SELECT RAISE(ABORT, 'reused observation failed'); END"
    )
    repository._connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="reused observation failed"):
        repository.record_cell_artifact(
            path=str(tmp_path / "same.txt"),
            filename="same.txt",
            content_type="text/plain",
            size_bytes=4,
            checksum="same",
            producing_cell_id="cell-2",
            frame_id=frame_id,
            snapshot_path="/snap/must-roll-back",
            input_version_ids=[source["version_id"]],
            reuse_matching_head=True,
        )

    assert len(repository.list_versions(head["artifact_id"])) == 1
    assert repository.version_meta(head["version_id"])["snapshot_path"] is None
    assert (
        repository.get_artifact(head["artifact_id"])["updated_at"]
        == artifact_before["updated_at"]
    )
    assert [
        row["producing_cell_id"]
        for row in repository.list_capture_observations(artifact_id=head["artifact_id"])
    ] == ["cell-1"]
    assert (
        repository._connection.execute(
            "SELECT COUNT(*) FROM lineage_edges WHERE producing_cell_id='cell-2'"
        ).fetchone()[0]
        == 0
    )


def test_record_cell_rolls_back_the_whole_transaction_on_lineage_failure(tmp_path):
    _store, repository = _repository(tmp_path)
    # The repository now refuses the unsupported identity before binding it;
    # the point of this test remains that no partial output row survives.
    with pytest.raises(TypeError):
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


def test_record_cell_revalidates_lineage_scope_inside_its_savepoint(tmp_path):
    store, repository = _repository(tmp_path)
    owner = store.new_frame(kind="turn", project_id="science", status="ready")
    foreign_frame = store.new_frame(kind="turn", project_id="foreign", status="ready")
    foreign = _save(
        repository,
        tmp_path / "foreign.txt",
        frame_id=foreign_frame,
        checksum="foreign",
    )

    failures = []
    for index, input_version_id in enumerate(
        (foreign["version_id"], "v-absent"), start=1
    ):
        filename = f"refused-{index}.txt"
        with pytest.raises(KeyError) as caught:
            repository.record_cell_artifact(
                path=str(tmp_path / filename),
                filename=filename,
                content_type="text/plain",
                size_bytes=1,
                checksum="x",
                producing_cell_id=f"cell-{index}",
                frame_id=owner,
                input_version_ids=[input_version_id],
            )
        failures.append(str(caught.value).replace(input_version_id, "VERSION"))
        assert repository.list_artifacts({"filename": filename}) == []

    assert failures[0] == failures[1]
    assert (
        repository._connection.execute(
            "SELECT COUNT(*) FROM lineage_edges WHERE input_version_id IN (?,?)",
            (foreign["version_id"], "v-absent"),
        ).fetchone()[0]
        == 0
    )


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
        repository.upsert_env_snapshot(dict(snapshot, package_count=1)) == snapshot_id
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
        repository.env_snapshot_for_artifact(first["artifact_id"], first["version_id"])
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
    assert (
        repository.version_for_path(str(real), root_frame_id=None, project_id="science")
        == second["version_id"]
    )
    versions = repository.list_versions(first["artifact_id"])
    assert [version["ordinal"] for version in versions] == [2, 1]
    assert [version["is_latest"] for version in versions] == [True, False]
    assert [version["version_id"] for version in versions] == [
        second["version_id"],
        first["version_id"],
    ]
    assert (
        repository.list_artifacts({"project_id": "science"})[0]["artifact_id"]
        == first["artifact_id"]
    )
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
    assert (
        repository.set_latest_version(first["artifact_id"], first["version_id"])[
            "latest_version_id"
        ]
        == first["version_id"]
    )
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


def test_explicit_artifact_delete_cleans_observations_and_their_env_refs(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    shared_env = repository.upsert_env_snapshot(
        {
            "kind": "python",
            "generation_id": "generation-shared",
            "packages": [],
            "package_count": 0,
        }
    )
    observed_env = repository.upsert_env_snapshot(
        {
            "kind": "python",
            "generation_id": "generation-observed",
            "packages": [],
            "package_count": 0,
        }
    )
    kept = repository.record_cell_artifact(
        path=str(tmp_path / "kept.txt"),
        filename="kept.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="kept",
        producing_cell_id="cell-kept",
        frame_id=frame_id,
        env_snapshot_id=shared_env,
    )
    target = repository.record_cell_artifact(
        path=str(tmp_path / "target.txt"),
        filename="target.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same-target",
        producing_cell_id="cell-target-1",
        frame_id=frame_id,
        env_snapshot_id=shared_env,
        reuse_matching_head=True,
    )
    reused = repository.record_cell_artifact(
        path=str(tmp_path / "target.txt"),
        filename="target.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same-target",
        producing_cell_id="cell-target-2",
        frame_id=frame_id,
        env_snapshot_id=observed_env,
        reuse_matching_head=True,
    )
    assert reused["version_id"] == target["version_id"]
    assert (
        len(repository.list_capture_observations(artifact_id=target["artifact_id"]))
        == 2
    )

    repository.delete_artifact(target["artifact_id"])

    assert repository.list_capture_observations(artifact_id=target["artifact_id"]) == []
    assert repository.get_env_snapshot(observed_env) is None
    assert repository.get_env_snapshot(shared_env) is not None
    repository.delete_artifact(kept["artifact_id"])
    assert repository.get_env_snapshot(shared_env) is None


def test_generic_env_snapshot_gc_preserves_observation_only_provenance(tmp_path):
    store, repository = _repository(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    original_env = repository.upsert_env_snapshot(
        {"kind": "python", "generation_id": "generation-original", "packages": []}
    )
    observed_env = repository.upsert_env_snapshot(
        {"kind": "python", "generation_id": "generation-observed", "packages": []}
    )
    first = repository.record_cell_artifact(
        path=str(tmp_path / "same.txt"),
        filename="same.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-first",
        frame_id=frame_id,
        env_snapshot_id=original_env,
        snapshot_path="/snap/same",
        reuse_matching_head=True,
    )
    reused = repository.record_cell_artifact(
        path=str(tmp_path / "same.txt"),
        filename="same.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-second",
        frame_id=frame_id,
        env_snapshot_id=observed_env,
        snapshot_path="/snap/discarded-candidate",
        reuse_matching_head=True,
    )

    assert reused["version_id"] == first["version_id"]
    assert repository.version_meta(first["version_id"])["env_snapshot_id"] == (
        original_env
    )
    assert repository.delete_env_snapshots_if_unreferenced([observed_env]) == 0
    assert repository.get_env_snapshot(observed_env) is not None


def test_the_transaction_refuses_a_cross_project_source_on_its_own(tmp_path):
    """Defence in depth, and worded so that the depth is not itself a leak.

    The Host service checks the project bound before calling, and this
    transaction checks it again. Two independent checks is the point -- but
    they must give the *same* refusal, because if they differed then removing
    the outer one would turn the inner one into the disclosure channel both
    exist to close: a caller could tell "another project's version" from "no
    such version" by which sentence came back. That is most of what an
    enumerator wants, and version ids are short enough to grind.

    So this drives the repository directly, with no Host service in front.
    """
    store = get_store(Config(data_dir=tmp_path).db_path)
    theirs_root = store.new_frame(kind="turn", project_id="proj-theirs")
    mine_root = store.new_frame(kind="turn", project_id="proj-mine")
    snapshot = tmp_path / "snap.bin"
    snapshot.write_bytes(b"secret")
    seeded = store.record_cell_artifact(
        path=str(snapshot),
        filename="secret.bin",
        content_type="application/octet-stream",
        size_bytes=6,
        checksum="deadbeef",
        producing_cell_id=None,
        frame_id=theirs_root,
        root_frame_id=theirs_root,
        project_id="proj-theirs",
        snapshot_path=str(snapshot),
    )

    def _materialise(version_id):
        return store.materialise_artifact_version(
            source_version_id=version_id,
            artifact_id="a-new",
            version_id="v-new",
            filename="copy.bin",
            path=str(tmp_path / "copy.bin"),
            snapshot_path=str(tmp_path / "copy.bin"),
            frame_id=mine_root,
            root_frame_id=mine_root,
            project_id="proj-mine",
        )

    with pytest.raises(KeyError) as cross_project:
        _materialise(seeded["version_id"])
    with pytest.raises(KeyError) as absent:
        _materialise("v-000000000000")

    assert str(cross_project.value).replace(seeded["version_id"], "ID") == str(
        absent.value
    ).replace("v-000000000000", "ID")
    # Nothing was written by either refusal.
    assert store.list_artifacts({"root_frame_id": mine_root}) == []
