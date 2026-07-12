"""Native Artifact lifecycle contracts and immutable restore safety."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.host.data import HostDataService
from openai4s.host_dispatch import HostDispatcher
from openai4s.store import get_store
from openai4s.tools.artifacts import (
    GetArtifactMetadataTool,
    ListArtifactVersionsTool,
    RestoreArtifactVersionTool,
)
from openai4s.tools.registry import get_tool


class ArtifactControlHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.config = Config(data_dir=tmp_path / "data")
        self.config.artifacts_dir.mkdir(parents=True)
        self.store = get_store(self.config.db_path)
        self.root_frame_id = self.store.new_frame(
            kind="turn",
            project_id="science",
            status="ready",
        )
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        self.service = self.service_for(self.root_frame_id)

    def service_for(self, frame_id: str) -> HostDataService:
        def resolve(path: str, *, must_exist: bool = False) -> Path:
            candidate = Path(path)
            target = (
                candidate if candidate.is_absolute() else self.workspace / candidate
            ).resolve()
            target.relative_to(self.workspace.resolve())
            if must_exist and not target.exists():
                raise FileNotFoundError(target)
            return target

        return HostDataService(
            store=self.store,
            config=self.config,
            frame_id=frame_id,
            resolve_path=resolve,
        )

    def two_versions(self) -> tuple[dict, dict, Path]:
        live = self.workspace / "result.txt"
        live.write_bytes(b"alpha")
        source_snapshot = self.config.artifacts_dir / "source-alpha"
        source_snapshot.write_bytes(b"alpha")
        first = self.store.save_artifact(
            path=str(live),
            filename="result.txt",
            content_type="text/plain",
            size_bytes=5,
            checksum=hashlib.sha256(b"alpha").hexdigest(),
            frame_id=self.root_frame_id,
            snapshot_path=str(source_snapshot),
        )

        live.write_bytes(b"beta")
        current_snapshot = self.config.artifacts_dir / "current-beta"
        current_snapshot.write_bytes(b"beta")
        second = self.store.save_artifact(
            path=str(live),
            filename="result.txt",
            content_type="text/plain",
            size_bytes=4,
            checksum=hashlib.sha256(b"beta").hexdigest(),
            frame_id=self.root_frame_id,
            artifact_id=first["artifact_id"],
            snapshot_path=str(current_snapshot),
        )
        return first, second, live


def test_artifact_lifecycle_tools_own_schema_policy_and_behavior():
    assert isinstance(get_tool("get_artifact_metadata"), GetArtifactMetadataTool)
    assert isinstance(get_tool("list_artifact_versions"), ListArtifactVersionsTool)
    restore = get_tool("restore_artifact_version")
    assert isinstance(restore, RestoreArtifactVersionTool)
    assert restore.requires_approval is True
    assert restore.read_only is False
    assert restore.dangerous is True
    assert restore.side_effect_class == "high_risk"
    assert (
        restore.permission_target({"artifact_id": "a-1", "version_id": "v-1"})
        == "a-1@v-1"
    )
    assert restore.resource_keys({"artifact_id": "a-1", "version_id": "v-1"}) == (
        "artifact:a-1",
        "artifact_version:v-1",
        "workspace:a-1",
    )

    calls = []

    class Runtime:
        def invoke(self, method, *arguments):
            calls.append((method, arguments))
            return {"ok": True}

    restore.execute(Runtime(), {"artifact_id": "a-1", "version_id": "v-1"})
    assert calls == [
        (
            "restore_artifact_version",
            ({"artifact_id": "a-1", "version_id": "v-1"},),
        )
    ]


def test_metadata_and_version_list_are_exact_scoped_and_path_free(tmp_path):
    harness = ArtifactControlHarness(tmp_path)
    first, second, _live = harness.two_versions()

    metadata = harness.service.artifact_metadata(
        {
            "artifact_id": first["artifact_id"],
            "version_id": first["version_id"],
        }
    )
    assert metadata["artifact"]["latest_version_id"] == second["version_id"]
    assert metadata["version"]["version_id"] == first["version_id"]
    assert metadata["version"]["is_latest"] is False
    assert metadata["version"]["snapshot_available"] is True
    assert "path" not in metadata["version"]
    assert "snapshot_path" not in metadata["version"]

    versions = harness.service.artifact_versions({"artifact_id": first["artifact_id"]})
    assert versions["count"] == 2
    assert versions["latest_version_id"] == second["version_id"]
    assert [item["version_id"] for item in versions["versions"]] == [
        second["version_id"],
        first["version_id"],
    ]
    assert all(item["snapshot_available"] for item in versions["versions"])

    other = harness.store.new_frame(kind="turn", project_id="other", status="ready")
    foreign_service = harness.service_for(other)
    with pytest.raises(PermissionError, match="outside the current session"):
        foreign_service.artifact_metadata({"artifact_id": first["artifact_id"]})
    with pytest.raises(PermissionError, match="outside the current session"):
        foreign_service.artifact_versions({"artifact_id": first["artifact_id"]})


def test_restore_copies_verified_snapshot_to_fresh_version_and_lineage(tmp_path):
    harness = ArtifactControlHarness(tmp_path)
    first, second, live = harness.two_versions()
    source_before = harness.store.version_meta(first["version_id"])

    restored = harness.service.restore_artifact_version(
        {
            "artifact_id": first["artifact_id"],
            "version_id": first["version_id"],
        }
    )

    assert restored["ok"] is True
    assert restored["snapshot_verified"] is True
    assert restored["restored_from_version_id"] == first["version_id"]
    assert restored["version_id"] not in {
        first["version_id"],
        second["version_id"],
    }
    assert live.read_bytes() == b"alpha"
    assert harness.store.version_meta(first["version_id"]) == source_before
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == restored["version_id"]
    )
    new_metadata = harness.store.version_meta(restored["version_id"])
    assert Path(new_metadata["snapshot_path"]).parent == (harness.config.artifacts_dir)
    assert Path(new_metadata["snapshot_path"]).read_bytes() == b"alpha"
    assert harness.store.lineage_edges_for(restored["version_id"], "up") == [
        first["version_id"]
    ]
    assert len(harness.store.list_versions(first["artifact_id"])) == 3


def test_dispatcher_keeps_restore_behind_approval_and_audits_call(tmp_path):
    harness = ArtifactControlHarness(tmp_path)
    first, _second, _live = harness.two_versions()
    harness.store.set_permission_rule(
        scope="conversation",
        scope_id=harness.root_frame_id,
        tool="restore_artifact_version",
        pattern="*",
        decision="allow",
    )
    dispatcher = HostDispatcher(
        harness.config,
        frame_id=harness.root_frame_id,
        workspace=harness.workspace,
    )

    result = dispatcher(
        "restore_artifact_version",
        [
            {
                "artifact_id": first["artifact_id"],
                "version_id": first["version_id"],
            }
        ],
    )

    assert result["ok"] is True
    row = harness.store._conn.execute(
        "SELECT method,ok,args_preview FROM host_call_log "
        "WHERE method='restore_artifact_version' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row["method"] == "restore_artifact_version"
    assert row["ok"] == 1
    assert first["artifact_id"] in row["args_preview"]


def test_restore_rejects_untrusted_or_corrupt_snapshots_without_mutation(tmp_path):
    harness = ArtifactControlHarness(tmp_path)
    first, second, live = harness.two_versions()
    source = harness.store.version_meta(first["version_id"])
    Path(source["snapshot_path"]).write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="checksum verification failed"):
        harness.service.restore_artifact_version(
            {
                "artifact_id": first["artifact_id"],
                "version_id": first["version_id"],
            }
        )
    assert live.read_bytes() == b"beta"
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == second["version_id"]
    )
    assert len(harness.store.list_versions(first["artifact_id"])) == 2

    outside = tmp_path / "outside-snapshot"
    outside.write_bytes(b"alpha")
    harness.store.set_version_snapshot(first["version_id"], str(outside))
    with pytest.raises(PermissionError, match="outside trusted storage"):
        harness.service.restore_artifact_version(
            {
                "artifact_id": first["artifact_id"],
                "version_id": first["version_id"],
            }
        )
    assert live.read_bytes() == b"beta"


def test_restore_refuses_workspace_drift_and_rolls_back_store_failure(
    tmp_path, monkeypatch
):
    harness = ArtifactControlHarness(tmp_path)
    first, second, live = harness.two_versions()
    live.write_bytes(b"external edit")
    with pytest.raises(RuntimeError, match="unversioned changes"):
        harness.service.restore_artifact_version(
            {
                "artifact_id": first["artifact_id"],
                "version_id": first["version_id"],
            }
        )
    assert live.read_bytes() == b"external edit"
    assert len(harness.store.list_versions(first["artifact_id"])) == 2

    live.write_bytes(b"beta")
    snapshots_before = set(harness.config.artifacts_dir.iterdir())

    def fail_restore(**fields):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(harness.store, "record_artifact_restore", fail_restore)
    with pytest.raises(RuntimeError, match="database unavailable"):
        harness.service.restore_artifact_version(
            {
                "artifact_id": first["artifact_id"],
                "version_id": first["version_id"],
            }
        )
    assert live.read_bytes() == b"beta"
    assert set(harness.config.artifacts_dir.iterdir()) == snapshots_before
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == second["version_id"]
    )
