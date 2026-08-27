"""Direct contracts for versioned workspace artifact capture."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.host_dispatch import HostDispatcher
from openai4s.kernel import Kernel
from openai4s.server.artifacts import (
    CONTENT_FINGERPRINT_ENV,
    ArtifactManager,
    ArtifactOperationError,
    artifact_receipt_map,
)
from openai4s.storage.artifact_observations import (
    CAPTURE_KIND_HEAD_CHECKSUM_REUSED,
)
from openai4s.store import get_store


class ArtifactHarness:
    def __init__(self, tmp_path: Path, *, trusted_delivery: bool = False) -> None:
        cfg = Config(
            data_dir=tmp_path / "data",
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            roadmap_features=RoadmapFeatureFlags(
                stage1_trusted_delivery=trusted_delivery
            ),
        )
        self.cfg = cfg
        self.store = get_store(cfg.db_path)
        self.frame_id = self.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        self.workspace = cfg.data_dir / "agent-workspaces" / self.frame_id
        self.workspace.mkdir(parents=True)
        self.broadcasts: list[tuple[str, dict]] = []
        self.environment_calls = 0
        self.manager = ArtifactManager(
            data_dir=cfg.data_dir,
            store=self.store,
            workspace_for=lambda frame_id: self.workspace,
            broadcast=lambda frame_id, event: self.broadcasts.append((frame_id, event)),
            guess_content_type=lambda name: (
                "text/csv" if name.endswith(".csv") else "application/octet-stream"
            ),
            checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
            trusted_delivery=trusted_delivery,
        )
        self.session = SimpleNamespace(
            root_frame_id=self.frame_id,
            project_id="default",
            workspace=self.workspace,
        )

    def count_environment_captures(self) -> None:
        """Freezing once per capture is the invariant, not once per file.

        Previously counted through an injected `environment_snapshot` port;
        the snapshot now comes from the kernel generation, so the counter
        wraps the method that must stay called exactly once.
        """
        original = self.manager.capture_environment

        def counting(*args, **kwargs):
            self.environment_calls += 1
            return original(*args, **kwargs)

        self.manager.capture_environment = counting


def test_register_freezes_version_before_emitting_event(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.csv"
    observed = []

    def emit(event):
        version_id = event["artifact"]["version_id"]
        meta = harness.store.version_meta(version_id)
        snapshot = Path(meta["snapshot_path"])
        observed.append((version_id, snapshot.read_bytes()))

    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(harness.session, path, "cell-1", emit)
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(harness.session, path, "cell-2", emit)

    assert first["artifact_id"] == second["artifact_id"]
    assert observed == [
        (first["version_id"], b"ALPHA"),
        (second["version_id"], b"BETA"),
    ]
    assert (
        Path(
            harness.store.version_meta(first["version_id"])["snapshot_path"]
        ).read_bytes()
        == b"ALPHA"
    )


def test_trusted_register_freezes_verified_snapshot_before_record(
    tmp_path, monkeypatch
):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "verified.csv"
    path.write_bytes(b"a,b\n1,2\n")
    observed = []
    original = harness.store.record_cell_artifact

    def record_after_freeze(**fields):
        snapshot = Path(fields["snapshot_path"])
        raw = snapshot.read_bytes()
        observed.append((snapshot, fields.copy(), raw))
        assert fields["reuse_matching_head"] is True
        assert fields["size_bytes"] == len(raw)
        assert fields["checksum"] == hashlib.sha256(raw).hexdigest()
        return original(**fields)

    monkeypatch.setattr(harness.store, "record_cell_artifact", record_after_freeze)
    result = harness.manager.register_file(
        harness.session, path, "cell-trusted", lambda event: None
    )

    assert len(observed) == 1
    snapshot, _fields, raw = observed[0]
    assert snapshot.is_file()
    assert raw == b"a,b\n1,2\n"
    assert harness.store.version_meta(result["version_id"])["snapshot_path"] == str(
        snapshot
    )
    assert result["version_id"]


def test_trusted_same_bytes_reuse_head_but_record_each_cell_capture(tmp_path):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "same.csv"
    events = []
    path.write_bytes(b"value\n42\n")

    first = harness.manager.register_file(
        harness.session, path, "cell-1", events.append
    )
    second = harness.manager.register_file(
        harness.session, path, "cell-2", events.append
    )

    assert second["version_id"] == first["version_id"]
    assert second["artifact_id"] == first["artifact_id"]
    assert len(harness.store.list_versions(first["artifact_id"])) == 1
    observations = harness.store.list_artifact_capture_observations(
        version_id=first["version_id"]
    )
    assert [row["producing_cell_id"] for row in observations] == ["cell-1", "cell-2"]
    assert observations[-1]["capture_kind"] == CAPTURE_KIND_HEAD_CHECKSUM_REUSED
    # The second pre-freeze was not selected by COALESCE and is removed; only
    # the head's actual immutable snapshot remains.
    snapshots = list(harness.manager.versions_dir().iterdir())
    assert snapshots == [
        Path(harness.store.version_meta(first["version_id"])["snapshot_path"])
    ]


def test_parent_rewrite_after_child_capture_keeps_parent_production(tmp_path):
    """A child claim excludes only the exact unchanged live-file identity."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    child_frame_id = harness.store.new_frame(
        parent_id=harness.frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    path = harness.workspace / "shared.txt"
    parent_before = harness.manager.snapshot(harness.workspace)
    path.write_text("child bytes", encoding="utf-8")
    child = harness.manager.register_file(
        harness.session,
        path,
        "child-cell",
        lambda _event: None,
        producer_frame_id=child_frame_id,
    )
    assert child is not None
    harness.manager.claim_delegated_artifacts([child], workspace=harness.workspace)

    # The parent genuinely writes different bytes after the child returns.
    # Its fingerprint no longer matches the one-shot exclusion claim, so this
    # must remain a real parent version rather than being suppressed.
    path.write_text("parent bytes are different", encoding="utf-8")
    parent = harness.manager.capture(
        harness.session,
        1,
        "parent-cell",
        parent_before,
        lambda _event: None,
        language="python",
    )

    assert len(parent.artifacts) == 1
    versions = harness.store.list_versions(child["artifact_id"])
    assert len(versions) == 2
    head = harness.store.version_meta(parent.artifacts[0]["version_id"])
    assert head["frame_id"] == harness.frame_id
    assert head["producing_cell_id"] == "parent-cell"


def test_capture_detects_same_length_rewrite_that_restores_mtime(tmp_path):
    """A writer-controlled timestamp cannot hide changed scientific bytes."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "preserved-time.txt"
    path.write_text("AAAA", encoding="utf-8")
    original_mtime = path.stat().st_mtime_ns
    before = harness.manager.snapshot(harness.workspace)

    path.write_text("BBBB", encoding="utf-8")
    os.utime(path, ns=(original_mtime, original_mtime))
    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-preserved-time",
        before,
        lambda _event: None,
        language="python",
    )

    assert [artifact["filename"] for artifact in captured.artifacts] == [
        "preserved-time.txt"
    ]
    version = harness.store.version_meta(captured.artifacts[0]["version_id"])
    assert Path(version["snapshot_path"]).read_text(encoding="utf-8") == "BBBB"
    assert version["producing_cell_id"] == "cell-preserved-time"


def test_trusted_register_rejects_mid_freeze_rewrite_with_restored_mtime(
    tmp_path, monkeypatch
):
    """A same-size rewrite during the descriptor stream leaves no claim."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "mid-freeze.bin"
    original = b"A" * (1024 * 1024 + 4096)
    replacement = b"B" * len(original)
    path.write_bytes(original)
    source_stat = path.stat()
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
            with path.open("r+b", buffering=0) as stream:
                stream.write(replacement)
                os.fsync(stream.fileno())
            os.utime(
                path,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
        return chunk

    monkeypatch.setattr(
        "openai4s.server.artifacts.os.read", rewrite_after_first_source_read
    )

    with pytest.raises(ArtifactOperationError, match="snapshot freeze failed"):
        harness.manager.register_file(
            harness.session,
            path,
            "cell-mid-freeze",
            lambda _event: None,
        )

    assert mutated is True
    assert path.stat().st_size == len(original)
    assert path.stat().st_mtime_ns == source_stat.st_mtime_ns
    assert (
        harness.store.list_artifacts(
            {"root_frame_id": harness.frame_id, "project_id": "default"}
        )
        == []
    )
    assert harness.store.list_artifact_capture_observations() == []
    assert (
        harness.store._conn.execute(  # noqa: SLF001 - assert no hidden version row
            "SELECT COUNT(*) FROM artifact_versions"
        ).fetchone()[0]
        == 0
    )
    assert list(harness.manager.versions_dir().iterdir()) == []


def test_child_claim_survives_every_nested_ancestor_sweep(tmp_path):
    """A grandchild must not be reassigned first to its parent, then root."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    child_frame_id = harness.store.new_frame(
        parent_id=harness.frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    grandchild_frame_id = harness.store.new_frame(
        parent_id=child_frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    path = harness.workspace / "nested.txt"
    root_before = harness.manager.snapshot(harness.workspace)
    child_before = harness.manager.snapshot(harness.workspace)
    path.write_text("grandchild bytes", encoding="utf-8")
    grandchild = harness.manager.register_file(
        harness.session,
        path,
        "grandchild-cell",
        lambda _event: None,
        producer_frame_id=grandchild_frame_id,
    )
    assert grandchild is not None
    harness.manager.claim_delegated_artifacts([grandchild], workspace=harness.workspace)

    child_capture = harness.manager.capture(
        harness.session,
        1,
        "child-cell",
        child_before,
        lambda _event: None,
        language="python",
        producer_frame_id=child_frame_id,
    )
    root_capture = harness.manager.capture(
        harness.session,
        1,
        "root-cell",
        root_before,
        lambda _event: None,
        language="python",
    )

    assert child_capture.artifacts == []
    assert root_capture.artifacts == []
    assert harness.manager._delegated_claims == {}
    versions = harness.store.list_versions(grandchild["artifact_id"])
    assert len(versions) == 1
    metadata = harness.store.version_meta(grandchild["version_id"])
    assert metadata["frame_id"] == grandchild_frame_id
    observations = harness.store.list_artifact_capture_observations(
        version_id=grandchild["version_id"]
    )
    assert [(row["frame_id"], row["producing_cell_id"]) for row in observations] == [
        (grandchild_frame_id, "grandchild-cell")
    ]


def test_failed_child_capture_cannot_be_laundered_by_parent_sweep(
    tmp_path, monkeypatch
):
    """Trusted capture failure is sticky for the exact unchanged child bytes."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    child_frame_id = harness.store.new_frame(
        parent_id=harness.frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    hooks = harness.manager.delegated_cell_hooks(
        harness.session, child_frame_id, lambda _event: None
    )
    action = SimpleNamespace(language="python")
    token = hooks.before(action)
    path = harness.workspace / "uncaptured-child.txt"
    path.write_text("child bytes", encoding="utf-8")
    original_register = harness.manager.register_file
    monkeypatch.setattr(
        harness.manager,
        "register_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected child capture fault")
        ),
    )

    with pytest.raises(RuntimeError, match="injected child capture fault"):
        hooks.after(action, token, {"id": "child-cell"})

    monkeypatch.setattr(harness.manager, "register_file", original_register)
    with pytest.raises(
        ArtifactOperationError, match="delegated artifact capture failed"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "parent-cell",
            token.before,
            lambda _event: None,
            language="python",
        )
    assert (
        harness.store.artifact_by_filename(
            "uncaptured-child.txt", harness.frame_id, strict=True
        )
        is None
    )


def test_receipt_set_is_fully_verified_before_any_artifact_is_published(tmp_path):
    """A later bad receipt cannot leave a committed prefix or event behind."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    before = harness.manager.snapshot(harness.workspace)
    first = harness.workspace / "first.txt"
    second = harness.workspace / "second.txt"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    events = []
    receipts = artifact_receipt_map(
        [
            {
                "filename": first.name,
                "checksum": hashlib.sha256(first.read_bytes()).hexdigest(),
                "source": {"kind": "test", "ordinal": 1},
            },
            {
                "filename": second.name,
                "checksum": "0" * 64,
                "source": {"kind": "test", "ordinal": 2},
            },
        ]
    )

    with pytest.raises(
        ArtifactOperationError, match="receipt did not match captured bytes"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "cell-receipts",
            before,
            events.append,
            artifact_receipts=receipts,
        )

    assert events == []
    assert (
        harness.store.artifact_by_filename(first.name, harness.frame_id, strict=True)
        is None
    )
    assert (
        harness.store.artifact_by_filename(second.name, harness.frame_id, strict=True)
        is None
    )


@pytest.mark.parametrize("invalid_input", ["foreign", "absent"])
def test_receipt_lineage_set_is_scoped_before_any_artifact_is_published(
    tmp_path, invalid_input
):
    """Receipt N cannot publish an earlier prefix with invalid lineage."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=False)
    if invalid_input == "foreign":
        foreign_frame = harness.store.new_frame(
            kind="turn", project_id="foreign-project", status="ready"
        )
        foreign_path = tmp_path / "foreign-input.txt"
        foreign_path.write_bytes(b"foreign\n")
        foreign = harness.store.save_artifact(
            path=str(foreign_path),
            filename=foreign_path.name,
            content_type="text/plain",
            size_bytes=foreign_path.stat().st_size,
            checksum=hashlib.sha256(foreign_path.read_bytes()).hexdigest(),
            frame_id=foreign_frame,
        )
        invalid_version_id = foreign["version_id"]
    else:
        invalid_version_id = "v-absent"

    before = harness.manager.snapshot(harness.workspace)
    first = harness.workspace / "first.txt"
    second = harness.workspace / "second.txt"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    receipts = artifact_receipt_map(
        [
            {
                "filename": first.name,
                "checksum": hashlib.sha256(first.read_bytes()).hexdigest(),
                "source": {
                    "kind": "remote_compute",
                    "input_versions": [],
                },
            },
            {
                "filename": second.name,
                "checksum": hashlib.sha256(second.read_bytes()).hexdigest(),
                "source": {
                    "kind": "remote_compute",
                    "input_versions": [invalid_version_id],
                },
            },
        ]
    )
    events = []

    with pytest.raises(
        ArtifactOperationError, match="receipt lineage evidence is invalid"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "cell-invalid-lineage",
            before,
            events.append,
            artifact_receipts=receipts,
        )

    assert events == []
    for path in (first, second):
        assert (
            harness.store.artifact_by_filename(path.name, harness.frame_id, strict=True)
            is None
        )
    assert list(harness.manager.versions_dir().iterdir()) == []


def test_receipt_prefreeze_race_publishes_no_partial_artifacts(tmp_path, monkeypatch):
    """A rewrite while the receipt set freezes fails before row/event one."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=False)
    before = harness.manager.snapshot(harness.workspace)
    first = harness.workspace / "first.txt"
    second = harness.workspace / "second.txt"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    receipts = artifact_receipt_map(
        [
            {
                "filename": first.name,
                "checksum": hashlib.sha256(first.read_bytes()).hexdigest(),
                "source": {"kind": "test", "ordinal": 1},
            },
            {
                "filename": second.name,
                "checksum": hashlib.sha256(second.read_bytes()).hexdigest(),
                "source": {"kind": "test", "ordinal": 2},
            },
        ]
    )
    original_freeze = harness.manager.freeze_capture_snapshot

    def freeze_then_race(filename, source_path):
        frozen = original_freeze(filename, source_path)
        if filename == first.name:
            second.write_bytes(b"kernel-thread-rewrite\n")
        return frozen

    monkeypatch.setattr(harness.manager, "freeze_capture_snapshot", freeze_then_race)
    events = []
    with pytest.raises(
        ArtifactOperationError, match="receipt did not match captured bytes"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "cell-raced-receipts",
            before,
            events.append,
            artifact_receipts=receipts,
        )

    assert events == []
    assert (
        harness.store.list_artifacts(
            {"root_frame_id": harness.frame_id, "project_id": "default"}
        )
        == []
    )
    assert harness.store.list_artifact_capture_observations() == []
    assert (
        harness.store._conn.execute(  # noqa: SLF001 - no hidden partial row
            "SELECT COUNT(*) FROM artifact_versions"
        ).fetchone()[0]
        == 0
    )
    assert list(harness.manager.versions_dir().iterdir()) == []


def test_receipts_reuse_prefrozen_bytes_when_trusted_delivery_is_off(
    tmp_path, monkeypatch
):
    """Stage 10/11 evidence stays immutable independently of Stage 1."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=False)
    before = harness.manager.snapshot(harness.workspace)
    first = harness.workspace / "first.txt"
    second = harness.workspace / "second.txt"
    first_bytes = b"first\n"
    second_bytes = b"second\n"
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)
    receipts = artifact_receipt_map(
        [
            {
                "filename": first.name,
                "checksum": hashlib.sha256(first_bytes).hexdigest(),
                "source": {"kind": "test", "ordinal": 1},
            },
            {
                "filename": second.name,
                "checksum": hashlib.sha256(second_bytes).hexdigest(),
                "source": {"kind": "test", "ordinal": 2},
            },
        ]
    )
    original_register = harness.manager.register_file

    def register_then_race(session, path, cell_id, emit, *args, **kwargs):
        result = original_register(session, path, cell_id, emit, *args, **kwargs)
        if path.name == first.name:
            second.write_bytes(b"late-kernel-thread-rewrite\n")
        return result

    monkeypatch.setattr(harness.manager, "register_file", register_then_race)
    events = []
    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-prefrozen-receipts",
        before,
        events.append,
        artifact_receipts=receipts,
    )

    assert len(captured.artifacts) == 2
    assert len(events) == 2
    second_artifact = next(
        item for item in captured.artifacts if item["filename"] == second.name
    )
    second_meta = harness.store.version_meta(second_artifact["version_id"])
    second_snapshot = Path(second_meta["snapshot_path"])
    assert second_snapshot.read_bytes() == second_bytes
    assert second_meta["checksum"] == hashlib.sha256(second_bytes).hexdigest()
    assert second.read_bytes() == b"late-kernel-thread-rewrite\n"


def test_duplicate_delegated_receipt_is_rejected_and_claimed_failed(tmp_path):
    """One final file cannot consume two child Host-call receipts."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    child_frame_id = harness.store.new_frame(
        parent_id=harness.frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    hooks = harness.manager.delegated_cell_hooks(
        harness.session, child_frame_id, lambda _event: None
    )
    action = SimpleNamespace(language="python")
    token = hooks.before(action)
    path = harness.workspace / "duplicate-child.txt"
    path.write_bytes(b"child\n")
    receipt = {
        "filename": path.name,
        "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": {"kind": "test"},
    }

    with pytest.raises(ArtifactOperationError, match="claimed more than once"):
        hooks.after(
            action,
            token,
            {
                "id": "child-cell",
                "_openai4s_artifact_receipts": [receipt, dict(receipt)],
            },
        )

    assert (
        harness.store.artifact_by_filename(path.name, harness.frame_id, strict=True)
        is None
    )
    with pytest.raises(
        ArtifactOperationError, match="delegated artifact capture failed"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "parent-cell",
            token.before,
            lambda _event: None,
            language="python",
        )


def test_delegated_claim_capacity_fails_closed_instead_of_evicting(
    tmp_path, monkeypatch
):
    """Losing an old claim at the cap would make its bytes become parent's."""

    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    monkeypatch.setattr(harness.manager, "_DELEGATED_CLAIM_MAX", 1)
    child_frame_id = harness.store.new_frame(
        parent_id=harness.frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    parent_before = harness.manager.snapshot(harness.workspace)

    captured = []
    for index in range(2):
        path = harness.workspace / f"child-{index}.txt"
        path.write_text(f"child {index}", encoding="utf-8")
        artifact = harness.manager.register_file(
            harness.session,
            path,
            f"child-cell-{index}",
            lambda _event: None,
            producer_frame_id=child_frame_id,
        )
        assert artifact is not None
        captured.append(artifact)

    harness.manager.claim_delegated_artifacts(
        [captured[0]], workspace=harness.workspace
    )
    with pytest.raises(
        ArtifactOperationError, match="delegated artifact claim capacity exceeded"
    ):
        harness.manager.claim_delegated_artifacts(
            [captured[1]], workspace=harness.workspace
        )

    # Both versions are already child-owned. The critical assertion is that an
    # enclosing sweep cannot continue after exact exclusion state overflowed.
    with pytest.raises(
        ArtifactOperationError, match="delegated artifact claim capacity exceeded"
    ):
        harness.manager.capture(
            harness.session,
            1,
            "parent-cell",
            parent_before,
            lambda _event: None,
            language="python",
        )
    for artifact in captured:
        observations = harness.store.list_artifact_capture_observations(
            version_id=artifact["version_id"]
        )
        assert len(observations) == 1
        assert observations[0]["frame_id"] == child_frame_id
        assert observations[0]["producing_cell_id"].startswith("child-cell-")

    # Capacity loss is scoped to the affected session workspace. A single
    # pathological session must not brick Artifact capture for the daemon.
    other_frame = harness.store.new_frame(
        kind="turn", project_id="default", status="ready"
    )
    other_workspace = harness.cfg.data_dir / "agent-workspaces" / other_frame
    other_workspace.mkdir(parents=True)
    other_session = SimpleNamespace(
        root_frame_id=other_frame,
        project_id="default",
        workspace=other_workspace,
    )
    other_before = harness.manager.snapshot(other_workspace)
    (other_workspace / "independent.txt").write_text("safe", encoding="utf-8")
    independent = harness.manager.capture(
        other_session,
        1,
        "other-cell",
        other_before,
        lambda _event: None,
        language="python",
    )
    assert [item["filename"] for item in independent.artifacts] == ["independent.txt"]


def test_trusted_record_fault_removes_unreferenced_prefreeze(tmp_path, monkeypatch):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "fault.csv"
    path.write_bytes(b"x\n1\n")
    frozen_paths = []

    def fail_record(**fields):
        frozen = Path(fields["snapshot_path"])
        assert frozen.is_file()
        frozen_paths.append(frozen)
        raise RuntimeError("injected record failure")

    monkeypatch.setattr(harness.store, "record_cell_artifact", fail_record)
    emitted = []
    with pytest.raises(RuntimeError, match="injected record failure"):
        harness.manager.register_file(
            harness.session, path, "cell-fault", emitted.append
        )

    assert emitted == []
    assert frozen_paths and not frozen_paths[0].exists()
    assert list(harness.manager.versions_dir().iterdir()) == []


def test_trusted_snapshot_verification_failure_never_records_or_leaves_bytes(
    tmp_path, monkeypatch
):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    path = harness.workspace / "mismatch.csv"
    path.write_bytes(b"x\n1\n")
    monkeypatch.setattr(harness.manager, "checksum", lambda _path: "0" * 64)

    with pytest.raises(ArtifactOperationError, match="snapshot freeze failed"):
        harness.manager.register_file(
            harness.session, path, "cell-mismatch", lambda event: None
        )

    assert (
        harness.store.artifact_by_filename(path.name, harness.frame_id, strict=True)
        is None
    )
    assert list(harness.manager.versions_dir().iterdir()) == []


def test_capture_finalizes_provenance_version_without_duplicating_it(tmp_path):
    harness = ArtifactHarness(tmp_path)
    source = harness.workspace / "input.txt"
    source.write_text("science")
    source_record = harness.store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=7,
        checksum="source",
        frame_id=harness.frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)
    events = []

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        first_result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text.upper())\n",
            cell_id="cell-derived-1",
        )
        assert first_result["error"] is None
        output = harness.store.artifact_by_filename(
            "derived.txt", harness.frame_id, strict=True
        )
        assert output is not None
        provenance_version = output["latest_version_id"]
        assert len(harness.store.list_versions(output["artifact_id"])) == 1

        first_capture = harness.manager.capture(
            harness.session,
            1,
            "cell-derived-1",
            before,
            events.append,
            language="python",
        )

        assert first_capture.artifacts[0]["version_id"] == provenance_version
        assert events[0]["artifact"]["version_id"] == provenance_version
        assert events[0]["producing_cell_id"] == "cell-derived-1"
        assert events[0]["artifact"]["producing_cell_id"] == "cell-derived-1"
        output = harness.store.get_artifact(output["artifact_id"])
        assert output["latest_version_id"] == provenance_version
        assert len(harness.store.list_versions(output["artifact_id"])) == 1
        metadata = harness.store.version_meta(provenance_version)
        assert metadata["env_snapshot_id"] is not None
        assert Path(metadata["snapshot_path"]).read_text() == "SCIENCE"
        assert harness.store.lineage_inputs(provenance_version) == [
            {
                "version_id": source_record["version_id"],
                "filename": "input.txt",
                "path": str(source),
            }
        ]

        before_second = harness.manager.snapshot(harness.workspace)
        second_result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text.lower())\n",
            cell_id="cell-derived-2",
        )
        assert second_result["error"] is None
        second_capture = harness.manager.capture(
            harness.session,
            2,
            "cell-derived-2",
            before_second,
            events.append,
            language="python",
        )

    assert second_capture.artifacts[0]["artifact_id"] == output["artifact_id"]
    second_version = second_capture.artifacts[0]["version_id"]
    assert second_version != provenance_version
    assert len(harness.store.list_versions(output["artifact_id"])) == 2
    assert Path(
        harness.store.resolve_artifact_path(provenance_version)
    ).read_text() == ("SCIENCE")
    assert Path(harness.store.resolve_artifact_path(second_version)).read_text() == (
        "science"
    )
    assert (
        len(
            harness.store.list_artifacts(
                {"root_frame_id": harness.frame_id, "filename": "derived.txt"}
            )
        )
        == 1
    )


def test_explicit_save_merges_provenance_and_capture_into_one_complete_version(
    tmp_path,
):
    harness = ArtifactHarness(tmp_path)
    source = harness.workspace / "input.txt"
    source.write_text("science")
    source_record = harness.store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=7,
        checksum="source",
        frame_id=harness.frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)
    events = []

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('manual.csv', 'w') as handle:\n"
            "    handle.write(text.upper())\n"
            "saved = host.save_artifact(\n"
            "    'manual.csv', 'published/result.csv',\n"
            "    content_type='application/x-science',\n"
            f"    input_version_ids=['{source_record['version_id']}'],\n"
            "    producing_cell_id='declared-producer',\n"
            "    priority=2,\n"
            ")\n"
            "print(saved['version_id'])\n"
            "print(saved['path'])\n",
            cell_id="cell-explicit",
        )
        assert result["error"] is None
        saved_version, returned_path = result["stdout"].splitlines()
        before_capture = harness.store.version_meta(saved_version)
        capture = harness.manager.capture(
            harness.session,
            1,
            "cell-explicit",
            before,
            events.append,
            language="python",
        )

    artifact = harness.store.artifact_by_filename(
        "published/result.csv", harness.frame_id, strict=True
    )
    assert artifact is not None
    assert artifact["priority"] == 2
    assert artifact["latest_version_id"] == saved_version
    assert len(harness.store.list_versions(artifact["artifact_id"])) == 1
    assert capture.files_written == ["manual.csv"]
    assert capture.artifacts[0]["version_id"] == saved_version
    assert capture.artifacts[0]["filename"] == "published/result.csv"
    assert events[0]["artifact"]["filename"] == "published/result.csv"
    metadata = harness.store.version_meta(saved_version)
    assert metadata["producing_cell_id"] == "cell-explicit"
    assert metadata["content_type"] == "application/x-science"
    assert artifact["content_type"] == "application/x-science"
    assert capture.artifacts[0]["content_type"] == "application/x-science"
    assert metadata["path"] == str(harness.workspace / "manual.csv")
    assert metadata["snapshot_path"] == before_capture["snapshot_path"]
    assert returned_path == metadata["snapshot_path"]
    assert Path(metadata["snapshot_path"]).read_text() == "SCIENCE"
    assert metadata["env_snapshot_id"] is not None
    assert harness.store.lineage_inputs(saved_version) == [
        {
            "version_id": source_record["version_id"],
            "filename": "input.txt",
            "path": str(source),
        }
    ]
    assert (
        harness.store.artifact_by_filename("manual.csv", harness.frame_id, strict=True)
        is None
    )


def test_trusted_provenance_record_reuses_same_bytes_across_cells(tmp_path):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    source = harness.workspace / "source.txt"
    source.write_text("same")
    harness.store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=4,
        checksum=hashlib.sha256(b"same").hexdigest(),
        frame_id=harness.frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        first = kernel.execute(
            "text = open('source.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text)\n",
            cell_id="cell-provenance-first",
        )
        second = kernel.execute(
            "text = open('source.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text)\n",
            cell_id="cell-provenance-second",
        )

    assert first["error"] is None and second["error"] is None
    artifact = harness.store.artifact_by_filename(
        "derived.txt", harness.frame_id, strict=True
    )
    assert len(harness.store.list_versions(artifact["artifact_id"])) == 1
    observations = harness.store.list_artifact_capture_observations(
        artifact_id=artifact["artifact_id"]
    )
    assert [row["producing_cell_id"] for row in observations] == [
        "cell-provenance-first",
        "cell-provenance-second",
    ]


def test_repeated_explicit_saves_remain_versions_and_capture_adds_no_third(tmp_path):
    harness = ArtifactHarness(tmp_path)
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        result = kernel.execute(
            "open('repeat.txt', 'w').write('same')\n"
            "first = host.save_artifact('repeat.txt')\n"
            "second = host.save_artifact('repeat.txt')\n"
            "print(first['version_id'])\n"
            "print(second['version_id'])\n",
            cell_id="cell-repeat",
        )
        assert result["error"] is None
        first_version, second_version = result["stdout"].splitlines()
        capture = harness.manager.capture(
            harness.session,
            1,
            "cell-repeat",
            before,
            lambda event: None,
            language="python",
        )

    artifact = harness.store.artifact_by_filename(
        "repeat.txt", harness.frame_id, strict=True
    )
    assert first_version != second_version
    assert artifact["latest_version_id"] == second_version
    assert capture.artifacts[0]["version_id"] == second_version
    versions = harness.store.list_versions(artifact["artifact_id"])
    assert {version["version_id"] for version in versions} == {
        first_version,
        second_version,
    }
    assert all(
        Path(
            harness.store.version_meta(version["version_id"])["snapshot_path"]
        ).is_file()
        for version in versions
    )


def test_trusted_explicit_save_reuses_same_bytes_across_cells_with_observations(
    tmp_path,
):
    harness = ArtifactHarness(tmp_path, trusted_delivery=True)
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        first = kernel.execute(
            "open('repeat.txt', 'w').write('same')\n"
            "print(host.save_artifact('repeat.txt')['version_id'])\n",
            cell_id="cell-explicit-first",
        )
        second = kernel.execute(
            "open('repeat.txt', 'w').write('same')\n"
            "print(host.save_artifact('repeat.txt')['version_id'])\n",
            cell_id="cell-explicit-second",
        )

    assert first["error"] is None and second["error"] is None
    assert first["stdout"].strip() == second["stdout"].strip()
    artifact = harness.store.artifact_by_filename(
        "repeat.txt", harness.frame_id, strict=True
    )
    assert len(harness.store.list_versions(artifact["artifact_id"])) == 1
    observations = harness.store.list_artifact_capture_observations(
        artifact_id=artifact["artifact_id"]
    )
    assert [row["producing_cell_id"] for row in observations] == [
        "cell-explicit-first",
        "cell-explicit-second",
    ]
    assert observations[-1]["capture_kind"] == CAPTURE_KIND_HEAD_CHECKSUM_REUSED


def test_protect_latest_backfills_live_bytes_for_legacy_version(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "legacy.txt"
    path.write_bytes(b"ALPHA")
    legacy = harness.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=5,
        checksum=hashlib.sha256(b"ALPHA").hexdigest(),
        producing_cell_id="cell-legacy",
        frame_id=harness.frame_id,
        project_id="default",
    )

    harness.manager.protect_latest(harness.session)

    metadata = harness.store.version_meta(legacy["version_id"])
    assert Path(metadata["snapshot_path"]).read_bytes() == b"ALPHA"


def test_restore_backfills_legacy_latest_before_broadcast(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "report.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )

    # Simulate a pre-snapshot version: latest points at the mutable live path.
    path.write_bytes(b"BETA")
    legacy = harness.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=4,
        checksum=hashlib.sha256(b"BETA").hexdigest(),
        producing_cell_id="cell-2",
        frame_id=harness.frame_id,
        project_id="default",
        artifact_id=first["artifact_id"],
    )
    checked_during_broadcast = []

    def broadcast(frame_id, event):
        legacy_meta = harness.store.version_meta(legacy["version_id"])
        checked_during_broadcast.append(
            (
                path.read_bytes(),
                Path(legacy_meta["snapshot_path"]).read_bytes(),
                harness.store.get_artifact(first["artifact_id"])["latest_version_id"],
            )
        )

    harness.manager.broadcast = broadcast
    result = harness.manager.restore(first["artifact_id"], first["version_id"])

    assert result["ok"] is True
    restored_version_id = result["version_id"]
    assert restored_version_id not in {
        first["version_id"],
        legacy["version_id"],
    }
    assert result["restored_from_version_id"] == first["version_id"]
    assert checked_during_broadcast == [(b"ALPHA", b"BETA", restored_version_id)]
    assert harness.store.lineage_edges_for(restored_version_id, "up") == [
        first["version_id"]
    ]


def test_restore_carries_the_retrieval_provenance_forward(tmp_path):
    """A restored version must keep the source row's retrieval envelope — the URL,
    timestamp and response hash that say where the bytes came from.

    `record_artifact_restore` inserted the new latest row copying only
    `env_snapshot_id`, so a retrieval-backed version looked *unsourced* after a
    restore in latest-version exports and evidence checks, even though the
    historical row it was restored from was fully sourced.
    """
    harness = ArtifactHarness(tmp_path)
    snap = harness.workspace / ".snap-alpha"
    snap.write_bytes(b"ALPHA")
    envelope = {
        "url": "https://example.org/data.csv",
        "retrieved_at": 1_700_000_000,
        "sha256": hashlib.sha256(b"ALPHA").hexdigest(),
    }
    first = harness.store.save_artifact(
        path=str(harness.workspace / "data.csv"),
        filename="data.csv",
        content_type="text/csv",
        size_bytes=5,
        checksum=hashlib.sha256(b"ALPHA").hexdigest(),
        producing_cell_id="cell-1",
        frame_id=harness.frame_id,
        project_id="default",
        snapshot_path=str(snap),
        source=envelope,
    )
    # A newer version becomes latest, so the sourced row is a historical one.
    second = harness.store.save_artifact(
        path=str(harness.workspace / "data.csv"),
        filename="data.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum=hashlib.sha256(b"BETA").hexdigest(),
        producing_cell_id="cell-2",
        frame_id=harness.frame_id,
        project_id="default",
        artifact_id=first["artifact_id"],
    )

    restored = harness.store.record_artifact_restore(
        artifact_id=first["artifact_id"],
        source_version_id=first["version_id"],
        expected_latest_version_id=second["version_id"],
        version_id="v-restored-provenance",
        path=str(harness.workspace / "data.csv"),
        snapshot_path=str(snap),
        size_bytes=5,
        checksum=hashlib.sha256(b"ALPHA").hexdigest(),
        frame_id=harness.frame_id,
    )

    restored_meta = harness.store.version_meta(restored["version_id"])
    source_meta = harness.store.version_meta(first["version_id"])
    assert restored_meta["source"], "restored version lost its retrieval envelope"
    assert restored_meta["source"] == source_meta["source"]
    assert "example.org/data.csv" in restored_meta["source"]


def test_restore_rejects_corrupt_snapshot_and_workspace_drift(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(
        harness.session, path, "cell-2", lambda event: None
    )

    source = harness.store.version_meta(first["version_id"])
    Path(source["snapshot_path"]).write_bytes(b"tampered")
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "checksum verification failed" in result["error"]
    assert path.read_bytes() == b"BETA"
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == second["version_id"]
    )
    assert len(harness.store.list_versions(first["artifact_id"])) == 2

    outside = tmp_path / "outside-snapshot"
    outside.write_bytes(b"ALPHA")
    harness.store.set_version_snapshot(first["version_id"], str(outside))
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "outside trusted storage" in result["error"]
    assert path.read_bytes() == b"BETA"

    Path(source["snapshot_path"]).write_bytes(b"ALPHA")
    harness.store.set_version_snapshot(first["version_id"], source["snapshot_path"])
    path.write_bytes(b"external edit")
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "unversioned changes" in result["error"]
    assert path.read_bytes() == b"external edit"
    assert len(harness.store.list_versions(first["artifact_id"])) == 2


def test_restore_expected_latest_cas_rolls_back_live_and_new_snapshot(
    tmp_path, monkeypatch
):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(
        harness.session, path, "cell-2", lambda event: None
    )
    snapshots_before = set(harness.manager.versions_dir().iterdir())
    original_record = harness.store.record_artifact_restore
    raced = {}

    def race_then_record(**fields):
        race_path = harness.workspace / "race.txt"
        race_path.write_bytes(b"GAMMA")
        race_snapshot = harness.manager.versions_dir() / "race-gamma"
        race_snapshot.write_bytes(b"GAMMA")
        raced.update(
            harness.store.save_artifact(
                path=str(race_path),
                filename="result.txt",
                content_type="text/plain",
                size_bytes=5,
                checksum=hashlib.sha256(b"GAMMA").hexdigest(),
                frame_id=harness.frame_id,
                artifact_id=first["artifact_id"],
                snapshot_path=str(race_snapshot),
            )
        )
        return original_record(**fields)

    monkeypatch.setattr(harness.store, "record_artifact_restore", race_then_record)
    result = harness.manager.restore(first["artifact_id"], first["version_id"])

    assert "changed concurrently" in result["error"]
    assert path.read_bytes() == b"BETA"
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == raced["version_id"]
    )
    assert harness.store.version_meta(second["version_id"])["checksum"] == (
        hashlib.sha256(b"BETA").hexdigest()
    )
    assert len(harness.store.list_versions(first["artifact_id"])) == 3
    assert harness.store.lineage_edges_for(first["version_id"], "down") == []
    added_snapshots = set(harness.manager.versions_dir().iterdir()) - snapshots_before
    assert added_snapshots == {harness.manager.versions_dir() / "race-gamma"}


def test_python_capture_uses_one_environment_and_orders_figure_first(tmp_path):
    harness = ArtifactHarness(tmp_path)
    before = harness.manager.snapshot(harness.workspace)
    (harness.workspace / "table.csv").write_text("x\n1\n")
    remote_calls = 0
    events = []

    def run_system_cell(code):
        assert "matplotlib" in code
        (harness.workspace / "figure_cell1_1.png").write_bytes(b"PNG")
        return {"stdout": '__OSFIGS__["figure_cell1_1.png"]\n'}

    def drain_remote():
        nonlocal remote_calls
        remote_calls += 1
        return [{"provider": "gpu-test", "job_id": "job-1"}]

    def emit(event):
        version = harness.store.version_meta(event["artifact"]["version_id"])
        assert Path(version["snapshot_path"]).is_file()
        events.append(event)

    harness.count_environment_captures()
    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-1",
        before,
        emit,
        language="python",
        run_system_cell=run_system_cell,
        drain_remote_provenance=drain_remote,
    )

    assert harness.environment_calls == remote_calls == 1
    assert captured.figures == ["figure_cell1_1.png"]
    assert captured.files_written == ["table.csv"]
    assert [item["filename"] for item in captured.artifacts] == [
        "figure_cell1_1.png",
        "table.csv",
    ]
    assert [event["artifact"]["filename"] for event in events] == [
        "figure_cell1_1.png",
        "table.csv",
    ]
    env_ids = {
        harness.store.version_meta(item["version_id"])["env_snapshot_id"]
        for item in captured.artifacts
    }
    assert len(env_ids) == 1
    snapshot = harness.store.get_env_snapshot(env_ids.pop())
    assert snapshot["remote"] == [{"provider": "gpu-test", "job_id": "job-1"}]


def test_r_capture_never_runs_python_figure_probe(tmp_path):
    harness = ArtifactHarness(tmp_path)
    before = harness.manager.snapshot(harness.workspace)
    (harness.workspace / "Rplots.pdf").write_bytes(b"PDF")

    def forbidden_probe(code):
        raise AssertionError("R capture must not execute a Python system cell")

    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-r",
        before,
        lambda event: None,
        language="r",
        run_system_cell=forbidden_probe,
    )

    assert captured.figures == []
    assert captured.files_written == ["Rplots.pdf"]
    assert [item["filename"] for item in captured.artifacts] == ["Rplots.pdf"]


def test_no_changes_skip_environment_and_remote_provenance(tmp_path):
    harness = ArtifactHarness(tmp_path)
    # Without this the counter is never wrapped, so `environment_calls` stays 0
    # whatever the code does — the assertion below asserted nothing at all.
    # Found by mutating the gate away and watching the test stay green.
    harness.count_environment_captures()
    before = harness.manager.snapshot(harness.workspace)
    remote_calls = 0

    def drain_remote():
        nonlocal remote_calls
        remote_calls += 1
        return [{"job_id": "must-not-outlive-this-cell"}]

    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-empty",
        before,
        lambda event: None,
        language="r",
        drain_remote_provenance=drain_remote,
    )

    assert captured.artifacts == []
    # The environment freeze is still skipped: it lists packages, and there is
    # no artifact here for it to describe.
    assert harness.environment_calls == 0
    # The drain is NOT skipped, and this is the assertion that changed. The
    # fixture above still calls its entry "should-remain-buffered", which is
    # what the old contract wanted — and what made a remote job in a cell that
    # wrote nothing reappear as the provenance of the next cell's artifact.
    # A buffer that survives its own cell is how provenance becomes wrong
    # rather than absent.
    assert remote_calls == 1


def test_snapshot_ignores_hidden_junk_and_nested_git_repositories(tmp_path):
    harness = ArtifactHarness(tmp_path)
    (harness.workspace / "deliverable.txt").write_text("keep")
    (harness.workspace / ".hidden.txt").write_text("ignore")
    junk = harness.workspace / "node_modules"
    junk.mkdir()
    (junk / "dependency.js").write_text("ignore")
    nested = harness.workspace / "cloned-tool"
    (nested / ".git").mkdir(parents=True)
    (nested / "weights.bin").write_bytes(b"ignore")

    snapshot = harness.manager.snapshot(harness.workspace)

    assert set(snapshot) == {str(harness.workspace / "deliverable.txt")}


def test_promote_cell_freezes_code_and_output_as_markdown_artifact(tmp_path):
    harness = ArtifactHarness(tmp_path)
    events: list[dict] = []
    cell = {
        "producing_cell_id": "cell-abc",
        "cell_index": 2,
        "language": "python",
        "source": "print('hi')\ndf.to_csv('out.csv')",
        "stdout": "hi",
        "figures": ["figure_cell2_1.png"],
        "files_written": ["out.csv"],
    }

    meta = harness.manager.promote_cell(harness.session, cell, events.append)

    assert meta is not None
    assert meta["filename"].endswith(".md")
    promoted = list((harness.workspace / "promoted").glob("*.md"))
    assert len(promoted) == 1
    text = promoted[0].read_text("utf-8")
    assert "```python" in text
    assert "print('hi')" in text
    assert "## Output" in text and "hi" in text
    assert "figure_cell2_1.png" in text  # figure reference preserved
    assert "`out.csv`" in text  # produced-file pointer preserved
    # A real artifact_created event fires so the Files panel refreshes.
    assert any(event.get("type") == "artifact_created" for event in events)


def test_promote_cell_reuses_one_artifact_and_versions_on_change(tmp_path):
    harness = ArtifactHarness(tmp_path)
    cell = {"producing_cell_id": "cell-x", "cell_index": 1, "source": "x = 1"}

    first = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    same = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    # Re-promoting the identical cell rewrites the same stable path: one
    # artifact, one file, no duplicate (identical bytes dedupe to one version).
    assert same["artifact_id"] == first["artifact_id"]
    assert same["version_id"] == first["version_id"]
    assert len(list((harness.workspace / "promoted").glob("*.md"))) == 1

    # An edited cell (same id) writes a fresh version of that same artifact.
    cell["source"] = "x = 2"
    changed = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    assert changed["artifact_id"] == first["artifact_id"]
    assert changed["version_id"] != first["version_id"]
    assert len(list((harness.workspace / "promoted").glob("*.md"))) == 1


def test_promote_cell_fences_longer_than_backtick_runs_in_output(tmp_path):
    harness = ArtifactHarness(tmp_path)
    # A cell whose output contains a Markdown fence must not break out of the
    # code block — the surrounding fence has to be longer than any run inside.
    cell = {
        "producing_cell_id": "cell-md",
        "cell_index": 3,
        "source": "print('markdown')",
        "stdout": "```\nnested fence\n```",
    }

    meta = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert meta is not None
    text = list((harness.workspace / "promoted").glob("*.md"))[0].read_text("utf-8")
    assert "````" in text  # output fence grew to 4 backticks around the 3-run body


def test_promote_cell_survives_symlinked_workspace_prefix(tmp_path):
    harness = ArtifactHarness(tmp_path)
    # A workspace reached through a symlinked parent (mirrors /tmp -> /private/tmp
    # on macOS, or a relative OPENAI4S_DATA_DIR): _write_confined_text returns a
    # resolved path while register_file relativizes against the unresolved
    # workspace. If the two diverge, promotion must still succeed rather than
    # raising an uncaught ValueError.
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(real, target_is_directory=True)
    workspace = link / "ws"
    workspace.mkdir()
    session = SimpleNamespace(
        root_frame_id=harness.frame_id,
        project_id="default",
        workspace=workspace,
    )

    meta = harness.manager.promote_cell(
        session,
        {"producing_cell_id": "cell-sym", "cell_index": 7, "source": "x = 1"},
        lambda event: None,
    )

    assert meta is not None
    assert meta["filename"].endswith(".md")
    assert list((workspace / "promoted").glob("*.md"))


def test_promote_cell_rejects_symlinked_output_directory(tmp_path):
    harness = ArtifactHarness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (harness.workspace / "promoted").symlink_to(outside, target_is_directory=True)

    result = harness.manager.promote_cell(
        harness.session,
        {"producing_cell_id": "cell-link", "cell_index": 4, "source": "x = 1"},
        lambda event: None,
    )

    assert result is None
    assert list(outside.iterdir()) == []


def test_promote_cell_rejects_symlinked_output_file(tmp_path):
    harness = ArtifactHarness(tmp_path)
    cell = {"producing_cell_id": "cell-link", "cell_index": 4, "source": "x = 1"}
    first = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    assert first is not None
    target = next((harness.workspace / "promoted").glob("*.md"))
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)

    result = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert result is None
    assert outside.read_text(encoding="utf-8") == "keep"


def test_promote_cell_embeds_workspace_figures_as_safe_data_urls(tmp_path):
    harness = ArtifactHarness(tmp_path)
    figure = harness.workspace / "figure_cell5_1.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\nfigure-bytes")
    cell = {
        "producing_cell_id": "cell-figure",
        "cell_index": 5,
        "source": "plot()",
        "figures": [figure.name],
    }

    result = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert result is not None
    text = next((harness.workspace / "promoted").glob("*.md")).read_text("utf-8")
    assert f"![{figure.name}](data:image/png;base64," in text
    assert f"]({figure.name})" not in text


def test_imported_session_snapshots_are_inside_trusted_storage(tmp_path):
    """The daemon refused to read a directory it writes itself.

    Session import writes each version's immutable bytes to
    ``<data_dir>/session-imports/<root>/artifacts/`` and points the version row
    at them. Both `ArtifactRestoreService` construction sites listed only
    ``artifacts/`` and ``artifact-versions/`` -- the same two directories, in
    opposite orders -- so `verified_snapshot_bytes` answered every imported
    artifact with "artifact snapshot is outside trusted storage".

    The boundary is containment, not integrity: the bytes are still checked
    against the version row's recorded sha256 and size on every read. Widening
    it to a directory the daemon owns does not weaken what a restore proves.

    Pinned as one shared derivation rather than two lists, because two lists
    maintained by hand is how the third directory came to be written to and not
    readable.
    """
    import hashlib as _hashlib

    from openai4s.artifact_restore import ArtifactRestoreService, trusted_snapshot_roots

    data_dir = tmp_path / "data"
    snapshot = (
        data_dir / "session-imports" / "f-imported" / "artifacts" / "000000-abc.bin"
    )
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    payload = b"imported bytes\n"
    snapshot.write_bytes(payload)
    version = {
        "version_id": "v-1",
        "snapshot_path": str(snapshot),
        "checksum": _hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }

    service = ArtifactRestoreService(
        store=None,
        primary_snapshot_dir=data_dir / "artifact-versions",
        trusted_snapshot_dirs=trusted_snapshot_roots(data_dir),
        resolve_live_path=lambda artifact, current: tmp_path / "live",
    )
    path, data = service.verified_snapshot_bytes(version)
    assert data == payload
    assert path == snapshot.resolve()

    # The integrity check is untouched by the wider boundary.
    tampered = dict(version, checksum="0" * 64)
    with pytest.raises(RuntimeError, match="checksum verification failed"):
        service.verified_snapshot_bytes(tampered)

    # And the boundary still is one: an arbitrary path stays refused.
    outside = tmp_path / "elsewhere.bin"
    outside.write_bytes(payload)
    with pytest.raises(PermissionError, match="outside trusted storage"):
        service.verified_snapshot_bytes(dict(version, snapshot_path=str(outside)))


@pytest.mark.skipif(os.name != "posix", reason="mkfifo and mode bits are POSIX")
def test_snapshot_survives_the_file_types_a_cell_can_leave_behind(
    tmp_path, monkeypatch
):
    """The workspace walk must not block, and must not lose a file it cannot read.

    ``snapshot`` runs on both sides of every Cell over a directory tree the
    agent controls, so its per-entry probe has to answer for whatever is
    there. Two entries are enough to break a naive ``os.open``:

    * a FIFO — ``os.open(fifo, O_RDONLY)`` blocks until a writer appears, and
      the ``S_ISREG`` rejection runs *after* the open, so it cannot save it.
      One ``os.mkfifo`` in a streaming pipeline wedged the Cell boundary
      forever, and the FIFO persists, so every later Cell re-wedged on it.
    * a regular file the daemon cannot read — ``lstat`` needs only search
      permission on the parent, ``open`` needs read permission on the file.
      Dropping it removes it from *both* snapshots, so it can never register
      as changed and one ``chmod 000`` hides a deliverable from capture.

    Asserted with a hard timeout rather than by calling and hoping: a hang is
    the failure mode, and a test that hangs reports nothing.
    """

    import threading

    # Pin the digest on: whether the snapshot reads bytes is a property of the
    # filesystem, and this test is about which entries survive the walk, not
    # about where the digest applies.
    monkeypatch.setenv(CONTENT_FINGERPRINT_ENV, "1")
    harness = ArtifactHarness(tmp_path)
    readable = harness.workspace / "result.csv"
    readable.write_bytes(b"a,b\n1,2\n")
    unreadable = harness.workspace / "locked.csv"
    unreadable.write_bytes(b"x,y\n3,4\n")
    os.chmod(unreadable, 0o000)
    os.mkfifo(harness.workspace / "stream.fifo")
    (harness.workspace / "subdir").mkdir()

    result: dict[str, object] = {}
    worker = threading.Thread(
        target=lambda: result.update(
            snapshot=harness.manager.snapshot(harness.workspace)
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "snapshot() blocked on a non-regular workspace entry"

    snapshot = result["snapshot"]
    assert str(readable) in snapshot
    assert str(unreadable) in snapshot, "an unreadable regular file left provenance"
    assert str(harness.workspace / "stream.fifo") not in snapshot
    assert str(harness.workspace / "subdir") not in snapshot

    # The readable file carries a digest; the unreadable one falls back to the
    # metadata-only identity rather than disappearing.
    assert snapshot[str(readable)][-1] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert snapshot[str(unreadable)][-1] is None

    os.chmod(unreadable, 0o600)


def test_the_content_digest_is_paid_only_where_the_ctime_can_lag(tmp_path, monkeypatch):
    """The read cost is gated on the filesystem, not charged to every platform.

    The digest exists for one defect: WSL's ext4-on-VHD can report the same
    ctime tick after a same-length rewrite whose mtime was restored. The size
    ceiling is per *file*, so leaving the digest unconditional reads the whole
    workspace on both sides of every Cell — ~190-330x the metadata walk — on
    hosts where the kernel-owned ctime is already authoritative.

    Both directions are asserted, because a gate that never opens is the same
    bug as a gate that never closes.
    """

    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.csv"
    path.write_bytes(b"a,b\n1,2\n")
    digest = hashlib.sha256(b"a,b\n1,2\n").hexdigest()

    monkeypatch.setenv(CONTENT_FINGERPRINT_ENV, "0")
    assert harness.manager.snapshot(harness.workspace)[str(path)][-1] is None

    monkeypatch.setenv(CONTENT_FINGERPRINT_ENV, "1")
    assert harness.manager.snapshot(harness.workspace)[str(path)][-1] == digest

    # With no override the answer comes from the probe, and it must be one
    # answer for the whole process: a `before` and an `after` that disagreed
    # would report every bounded file as changed.
    monkeypatch.delenv(CONTENT_FINGERPRINT_ENV, raising=False)
    first = harness.manager.snapshot(harness.workspace)[str(path)]
    second = harness.manager.snapshot(harness.workspace)[str(path)]
    assert first == second
