"""Frame/root/project ownership contracts for artifacts and Web sessions."""

from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server.gateway import SessionRunner
from openai4s.store import Store, get_store


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        pass


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def test_child_frames_and_artifacts_inherit_root_project_scope(tmp_path):
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="project-science")
    child = store.new_frame(parent_id=root, kind="delegate")
    grandchild = store.new_frame(
        parent_id=child,
        project_id="wrong-project",
        kind="delegate",
    )

    assert store.get_frame(child)["project_id"] == "project-science"
    assert store.get_frame(grandchild)["project_id"] == "project-science"
    assert store.resolve_frame_scope(grandchild) == {
        "frame_id": grandchild,
        "root_frame_id": root,
        "project_id": "project-science",
    }

    record = store.save_artifact(
        path="/workspace/result.csv",
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="result",
        producing_cell_id="cell-child",
        frame_id=grandchild,
    )
    artifact = store.get_artifact(record["artifact_id"])
    version = store.version_meta(record["version_id"])

    assert artifact["root_frame_id"] == root
    assert artifact["project_id"] == "project-science"
    assert version["frame_id"] == grandchild


def test_root_capture_finalizes_child_provenance_without_changing_producer(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-science")
    child = store.new_frame(parent_id=root, kind="delegate")
    path = tmp_path / "result.csv"
    path.write_text("x\n1\n")

    provenance = store.record_cell_artifact(
        path=str(path),
        filename="result.csv",
        content_type=None,
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-child",
        frame_id=child,
    )
    capture = store.record_cell_artifact(
        path=str(path),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-child",
        frame_id=root,
        root_frame_id=root,
        project_id="project-science",
    )

    assert capture["artifact_id"] == provenance["artifact_id"]
    assert capture["version_id"] == provenance["version_id"]
    artifact = store.get_artifact(provenance["artifact_id"])
    metadata = store.version_meta(provenance["version_id"])
    assert artifact["root_frame_id"] == root
    assert artifact["project_id"] == "project-science"
    assert metadata["frame_id"] == child


def test_artifact_rejects_version_from_different_root(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    first_root = store.new_frame(kind="turn", project_id="project-one")
    second_root = store.new_frame(kind="turn", project_id="project-two")
    record = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="one",
        frame_id=first_root,
    )

    with pytest.raises(ValueError, match="different root frame"):
        store.save_artifact(
            path="/other/result.txt",
            filename="result.txt",
            content_type="text/plain",
            size_bytes=3,
            checksum="two",
            frame_id=second_root,
            artifact_id=record["artifact_id"],
        )

    assert len(store.list_versions(record["artifact_id"])) == 1


def test_existing_artifact_without_explicit_scope_inherits_its_owner(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-existing")
    first = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="one",
        frame_id=root,
    )

    second = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="two",
        artifact_id=first["artifact_id"],
    )

    artifact = store.get_artifact(first["artifact_id"])
    assert artifact["project_id"] == "project-existing"
    assert artifact["root_frame_id"] == root
    assert artifact["latest_version_id"] == second["version_id"]


def test_known_producer_rejects_conflicting_explicit_root(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-one")
    child = store.new_frame(parent_id=root, kind="delegate")
    other = store.new_frame(kind="turn", project_id="project-two")

    with pytest.raises(ValueError, match="conflicts with producer frame"):
        store.save_artifact(
            path="/workspace/result.txt",
            filename="result.txt",
            content_type="text/plain",
            size_bytes=3,
            checksum="bad-scope",
            frame_id=child,
            root_frame_id=other,
        )

    assert store.list_artifacts({"root_frame_id": other}) == []


def test_unknown_frame_keeps_legacy_scope_fallback(tmp_path):
    store = get_store(_config(tmp_path).db_path)

    orphan = store.new_frame(parent_id="missing-parent", kind="delegate")
    assert store.get_frame(orphan)["root_frame_id"] == orphan

    record = store.save_artifact(
        path="/legacy/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="legacy",
        frame_id="legacy-frame",
        project_id="legacy-project",
    )

    artifact = store.get_artifact(record["artifact_id"])
    assert artifact["root_frame_id"] == "legacy-frame"
    assert artifact["project_id"] == "legacy-project"


def test_store_migration_repairs_historical_child_scope(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="project-migrated")
    child = store.new_frame(parent_id=root, kind="delegate")
    record = store.save_artifact(
        path="/workspace/old.txt",
        filename="old.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="old",
        frame_id=child,
    )
    store._conn.execute(
        "UPDATE frames SET project_id='default' WHERE frame_id=?",
        (child,),
    )
    store._conn.execute(
        "UPDATE artifacts SET project_id='default',root_frame_id=? "
        "WHERE artifact_id=?",
        (child, record["artifact_id"]),
    )
    # Make the simulation of "historical" faithful. The rows above are hand-made
    # to look like data written by a version that predates project_id
    # inheritance, so the database they live in has to look that old too — the
    # repair is a one-time migration, not a healer that re-runs on every open.
    # (It used to re-run every open only because there was no version to know
    # better; that meant a full-table UPDATE over frames and artifacts on every
    # single Store construction. Real databases still get repaired: the upgrade
    # path is v0 -> run the baseline, which includes this repair -> stamp v1.)
    store._conn.execute("PRAGMA user_version = 0")
    store._conn.commit()
    store.close()

    reopened = Store(cfg.db_path)
    try:
        assert reopened.get_frame(child)["project_id"] == "project-migrated"
        artifact = reopened.get_artifact(record["artifact_id"])
        assert artifact["project_id"] == "project-migrated"
        assert artifact["root_frame_id"] == root
    finally:
        reopened.close()


def test_web_state_requires_root_id_and_uses_root_project_as_authority(tmp_path):
    cfg = _config(tmp_path)
    runner = SessionRunner(cfg, _Hub())
    root = runner.store.new_frame(kind="turn", project_id="project-web")
    child = runner.store.new_frame(parent_id=root, kind="delegate")

    with pytest.raises(ValueError, match="require a root frame id"):
        runner._state(child, "wrong-project")

    state = runner._state(root, "wrong-project")

    assert state.root_frame_id == root
    assert state.project_id == "project-web"
    assert state.workspace == runner.workspace_for(root)
