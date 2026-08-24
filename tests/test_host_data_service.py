"""Direct contracts for store-backed host data capabilities."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.host.data as data_mod
from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.host.data import (
    HostDataService,
    kernel_artifact_input_dir,
    rank_artifacts,
)
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
    team_mode: bool = False,
):
    actual_store = store or FakeStore()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        team_mode=team_mode,
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


def _real_service(
    tmp_path: Path,
    *,
    trusted_delivery: bool,
    team_mode: bool = False,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Config(
        data_dir=tmp_path / "data",
        team_mode=team_mode,
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


def _team_artifact_source(
    tmp_path: Path,
    *,
    version_id: str = "v-owned",
    payload: bytes = b"exact-version-bytes",
    snapshot: bool = True,
):
    service, store, workspace, config = _service(tmp_path, team_mode=True)
    config.data_dir.mkdir(parents=True)
    if snapshot:
        source = config.data_dir / "artifacts" / f"{version_id}.bin"
        source.parent.mkdir()
    else:
        source = workspace / f"{version_id}.bin"
    source.write_bytes(payload)
    store.metadata[version_id] = {
        "version_id": version_id,
        "artifact_id": "a-1",
        "filename": f"{version_id}.bin",
        "path": str(source),
        "snapshot_path": str(source) if snapshot else None,
        "checksum": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    store.paths[version_id] = str(source)
    return service, store, workspace, config, source


def test_team_artifact_path_stages_current_and_historical_exact_bytes(tmp_path):
    service, store, workspace, config, current_source = _team_artifact_source(
        tmp_path,
        version_id="v-current",
        payload=b"current-live-version",
        snapshot=False,
    )
    historical = config.data_dir / "artifact-versions" / "v-history.bin"
    historical.parent.mkdir()
    historical.write_bytes(b"historical-frozen-version")
    store.metadata["v-history"] = {
        "version_id": "v-history",
        "artifact_id": "a-1",
        "filename": "history.bin",
        "path": str(current_source),
        "snapshot_path": str(historical),
        "checksum": hashlib.sha256(b"historical-frozen-version").hexdigest(),
        "size_bytes": len(b"historical-frozen-version"),
    }

    current_path = Path(service.artifact_path("v-current"))
    history_path = Path(service.artifact_path("v-history"))
    expected_root = kernel_artifact_input_dir(config.data_dir, "frame-1")

    assert current_path.parent == expected_root
    assert history_path.parent == expected_root
    assert current_path.read_bytes() == b"current-live-version"
    assert history_path.read_bytes() == b"historical-frozen-version"
    assert current_path != current_source
    assert history_path != historical
    assert not current_path.is_symlink()
    assert current_path.stat().st_nlink == 1
    assert expected_root.stat().st_mode & 0o777 == 0o700
    # The remote-compute path keeps the strict snapshot-only rule and resolves
    # to the same verified session copy rather than weakening to the live path.
    assert Path(service.artifact_snapshot_path("v-history")) == history_path
    with pytest.raises(FileNotFoundError, match="no frozen snapshot"):
        service.artifact_snapshot_path("v-current")
    assert service.provenance_resolve_path(str(current_path)) == "v-current"
    assert service.provenance_resolve_path(str(history_path)) == "v-history"
    assert not any(call[0] == "record_cell_artifact" for call in store.calls)
    assert not any(expected_root.is_relative_to(path) for path in (workspace,))


def test_team_artifact_path_is_stable_and_concurrently_idempotent(tmp_path):
    service, _store, _workspace, _config, _source = _team_artifact_source(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(service.artifact_path, ["v-owned"] * 24))

    assert len(set(paths)) == 1
    target = Path(paths[0])
    first_identity = (target.stat().st_dev, target.stat().st_ino)
    assert service.artifact_path("v-owned") == paths[0]
    assert (target.stat().st_dev, target.stat().st_ino) == first_identity
    assert target.read_bytes() == b"exact-version-bytes"


def test_team_artifact_path_repeated_hit_does_not_consume_session_quota(
    tmp_path,
    monkeypatch,
):
    service, store, _workspace, config, _source = _team_artifact_source(
        tmp_path,
        payload=b"quota-one",
    )
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_SESSION_MAX_BYTES", 9)
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_SESSION_MAX_FILES", 2)
    first = service.artifact_path("v-owned")

    assert service.artifact_path("v-owned") == first

    second_source = config.data_dir / "artifacts" / "quota-two.bin"
    second_source.write_bytes(b"X")
    store.metadata["v-two"] = {
        "version_id": "v-two",
        "artifact_id": "a-1",
        "filename": "quota-two.bin",
        "path": str(second_source),
        "snapshot_path": str(second_source),
        "checksum": hashlib.sha256(b"X").hexdigest(),
        "size_bytes": 1,
    }

    with pytest.raises(OSError, match="quota exceeded for this session"):
        service.artifact_path("v-two")

    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    assert [path.name for path in stage.iterdir()] == [Path(first).name]


def test_team_artifact_path_enforces_daemon_wide_quota_across_sessions(
    tmp_path,
    monkeypatch,
):
    service, store, _workspace, config, _source = _team_artifact_source(
        tmp_path,
        payload=b"global-one",
    )
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_GLOBAL_MAX_BYTES", 10)
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_SESSION_MAX_BYTES", 100)
    service.artifact_path("v-owned")

    store.scope = {
        "frame_id": "frame-2",
        "root_frame_id": "frame-2",
        "project_id": "default",
    }
    store.artifacts_by_id["a-2"] = {
        "artifact_id": "a-2",
        "root_frame_id": "frame-2",
        "project_id": "default",
    }
    second_source = config.data_dir / "artifacts" / "global-two.bin"
    second_source.write_bytes(b"Y")
    store.metadata["v-global-two"] = {
        "version_id": "v-global-two",
        "artifact_id": "a-2",
        "filename": "global-two.bin",
        "path": str(second_source),
        "snapshot_path": str(second_source),
        "checksum": hashlib.sha256(b"Y").hexdigest(),
        "size_bytes": 1,
    }

    with pytest.raises(OSError, match="global Artifact input staging quota"):
        service.artifact_path("v-global-two")

    assert list(kernel_artifact_input_dir(config.data_dir, "frame-2").iterdir()) == []


def test_team_artifact_path_fails_closed_without_root_scope(tmp_path):
    service, store, _workspace, config, _source = _team_artifact_source(tmp_path)
    store.scope = {"frame_id": "frame-1", "root_frame_id": "", "project_id": ""}
    store.artifacts_by_id["a-1"].update(root_frame_id="", project_id="")

    with pytest.raises(RuntimeError, match="staging scope is unavailable"):
        service.artifact_path("v-owned")

    assert not (config.data_dir / "kernel-artifact-inputs").exists()


def test_team_artifact_path_preserves_disk_free_reserve(tmp_path, monkeypatch):
    service, _store, _workspace, config, _source = _team_artifact_source(
        tmp_path,
        payload=b"reserve",
    )
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_MIN_FREE_BYTES", 10)
    monkeypatch.setattr(data_mod, "_ARTIFACT_INPUT_MAX_FREE_RESERVE", 10)
    monkeypatch.setattr(
        data_mod.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=84, free=16),
    )

    with pytest.raises(OSError, match="exhaust reserved disk space"):
        service.artifact_path("v-owned")

    assert list(kernel_artifact_input_dir(config.data_dir, "frame-1").iterdir()) == []


def test_team_artifact_path_denies_foreign_version_before_staging(tmp_path):
    service, store, _workspace, config, _source = _team_artifact_source(tmp_path)
    foreign = config.data_dir / "artifacts" / "foreign.bin"
    foreign.write_bytes(b"foreign-secret")
    store.artifacts_by_id["a-foreign"] = {
        "artifact_id": "a-foreign",
        "root_frame_id": "frame-other",
        "project_id": "other-project",
    }
    store.metadata["v-foreign"] = {
        "version_id": "v-foreign",
        "artifact_id": "a-foreign",
        "filename": "foreign.bin",
        "path": str(foreign),
        "snapshot_path": str(foreign),
        "checksum": hashlib.sha256(b"foreign-secret").hexdigest(),
        "size_bytes": len(b"foreign-secret"),
    }

    with pytest.raises(KeyError, match="current session"):
        service.artifact_path("v-foreign")

    assert not kernel_artifact_input_dir(config.data_dir, "frame-1").exists()


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_team_artifact_path_safely_replaces_preexisting_cache_alias(
    tmp_path,
    alias_kind,
):
    service, _store, _workspace, _config, _source = _team_artifact_source(tmp_path)
    target = Path(service.artifact_path("v-owned"))
    target.unlink()
    outside = tmp_path / f"outside-{alias_kind}.bin"
    outside.write_bytes(b"must-not-change")
    if alias_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    returned = Path(service.artifact_path("v-owned"))

    assert returned == target
    assert returned.read_bytes() == b"exact-version-bytes"
    assert outside.read_bytes() == b"must-not-change"
    assert not returned.is_symlink()
    assert returned.stat().st_nlink == 1


def test_team_artifact_path_refuses_symlinked_cache_parent(tmp_path):
    service, _store, _workspace, config, _source = _team_artifact_source(tmp_path)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    parent = config.data_dir / "kernel-artifact-inputs"
    parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        service.artifact_path("v-owned")

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_team_artifact_path_refuses_aliased_snapshot_source(tmp_path, alias_kind):
    service, _store, _workspace, config, source = _team_artifact_source(tmp_path)
    exact = config.data_dir / "artifacts" / f"exact-{alias_kind}.bin"
    source.replace(exact)
    if alias_kind == "symlink":
        source.symlink_to(exact)
    else:
        os.link(exact, source)

    with pytest.raises(OSError):
        service.artifact_path("v-owned")

    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    assert list(stage.iterdir()) == []


def test_team_artifact_path_detects_snapshot_name_swap_during_stream(
    tmp_path,
    monkeypatch,
):
    payload = b"A" * (1024 * 1024 + 4096)
    service, _store, _workspace, config, source = _team_artifact_source(
        tmp_path,
        payload=payload,
    )
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    detached = source.with_suffix(".detached")
    native_read = os.read
    swapped = False

    def swap_name_after_first_read(descriptor, count):
        nonlocal swapped
        chunk = native_read(descriptor, count)
        opened = os.fstat(descriptor)
        if chunk and not swapped and (opened.st_dev, opened.st_ino) == source_identity:
            swapped = True
            source.rename(detached)
            source.write_bytes(payload)
        return chunk

    monkeypatch.setattr("openai4s.host.data.os.read", swap_name_after_first_read)

    with pytest.raises(OSError, match="changed during input staging"):
        service.artifact_path("v-owned")

    assert swapped is True
    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    assert list(stage.iterdir()) == []


def test_team_artifact_path_does_not_follow_cache_parent_swap(tmp_path, monkeypatch):
    service, _store, _workspace, config, _source = _team_artifact_source(tmp_path)
    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    moved = stage.with_name(f"{stage.name}-moved")
    outside = tmp_path / "outside-race"
    outside.mkdir()
    native_read = os.read
    swapped = False

    def swap_cache_parent_after_source_open(descriptor, count):
        nonlocal swapped
        chunk = native_read(descriptor, count)
        if chunk and not swapped and stage.is_dir():
            swapped = True
            stage.rename(moved)
            stage.symlink_to(outside, target_is_directory=True)
        return chunk

    monkeypatch.setattr(
        "openai4s.host.data.os.read",
        swap_cache_parent_after_source_open,
    )

    with pytest.raises(OSError):
        service.artifact_path("v-owned")

    assert swapped is True
    assert list(outside.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_team_artifact_path_rejects_tampered_snapshot(tmp_path):
    service, _store, _workspace, config, source = _team_artifact_source(tmp_path)
    source.write_bytes(b"other-version-bytes")  # same size, different digest

    with pytest.raises(OSError, match="checksum verification failed"):
        service.artifact_path("v-owned")

    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    assert stage.is_dir()
    assert list(stage.iterdir()) == []


def test_team_artifact_path_bounds_oversized_source_read_to_recorded_size(
    tmp_path,
    monkeypatch,
):
    service, _store, _workspace, config, source = _team_artifact_source(
        tmp_path,
        payload=b"recorded",
    )
    source.write_bytes(b"X" * (4 * 1024 * 1024))
    native_read = os.read
    bytes_read = 0

    def observe_read(descriptor, count):
        nonlocal bytes_read
        chunk = native_read(descriptor, count)
        bytes_read += len(chunk)
        return chunk

    monkeypatch.setattr("openai4s.host.data.os.read", observe_read)

    with pytest.raises(OSError, match="size verification failed"):
        service.artifact_path("v-owned")

    assert bytes_read == len(b"recorded") + 1
    stage = kernel_artifact_input_dir(config.data_dir, "frame-1")
    assert list(stage.iterdir()) == []


def test_single_user_artifact_path_keeps_direct_path_compatibility(tmp_path):
    service, store, _workspace, _config = _service(tmp_path, team_mode=False)
    direct = tmp_path / "legacy-direct.bin"
    direct.write_bytes(b"legacy")
    store.metadata["v-direct"] = {
        "version_id": "v-direct",
        "artifact_id": "a-1",
        "snapshot_path": str(direct),
    }
    store.paths["v-direct"] = str(direct)

    assert service.artifact_path("v-direct") == str(direct)
    assert service.artifact_snapshot_path("v-direct") == str(direct)
    assert not (tmp_path / "data" / "kernel-artifact-inputs").exists()


def test_real_team_store_stages_each_immutable_version_in_session_root(tmp_path):
    service, store, workspace, config, frame_id = _real_service(
        tmp_path,
        trusted_delivery=True,
        team_mode=True,
    )
    source = workspace / "evolving.csv"
    try:
        source.write_bytes(b"generation,score\n1,0.4\n")
        first = service.save_artifact({"path": source.name})
        source.write_bytes(b"generation,score\n2,0.9\n")
        second = service.save_artifact({"path": source.name})

        first_path = Path(service.artifact_path(first["version_id"]))
        second_path = Path(service.artifact_path(second["version_id"]))
        scope = store.resolve_frame_scope(frame_id)
        expected_root = kernel_artifact_input_dir(
            config.data_dir,
            scope["root_frame_id"],
        )

        assert first["version_id"] != second["version_id"]
        assert first_path.parent == second_path.parent == expected_root
        assert first_path.read_bytes() == b"generation,score\n1,0.4\n"
        assert second_path.read_bytes() == b"generation,score\n2,0.9\n"
        assert Path(service.artifact_snapshot_path(first["version_id"])) == first_path
        assert service.provenance_resolve_path(str(first_path)) == first["version_id"]
    finally:
        store.close()


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
