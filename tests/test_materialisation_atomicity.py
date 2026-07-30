"""Materialisation and upload must not destroy bytes before deciding to refuse.

Both paths write the live target directly, and both run checks that can still
refuse *after* that write. The consequences differ in kind.

**Materialisation hardlinks the live name to an immutable snapshot.** The
docstring justifies the link with "a version snapshot is immutable by contract",
which is true of the snapshot *names* and false of the live name it also links.
So the borrowing session's writable working file shares an inode with the source
session's frozen bytes:

    live and SOURCE snapshot same inode: True
    source snapshot changed after one ordinary write: True
    source row checksum now describes bytes that are gone: True

That is another project member's provenance silently rewritten by an analysis
doing nothing unusual, and `write_version_snapshot` will never re-freeze it — it
returns early when the snapshot file exists. In a system whose whole claim is
immutable versions with checksums, this is the checksum describing bytes that no
longer exist.

**Both paths destroy the previous live file before their refusals.**
Materialisation `unlink()`s any existing same-name live file with no logging,
then runs the "already belongs to this session" and "different scope" checks
inside the transaction; its rollback removes only the new snapshot, never
restoring what it clobbered. Upload truncates the live file and *then* resolves
the DB scope, so a `project_id` mismatch leaves the previous version's row naming
a path whose bytes are now the rejected upload's.

The invariant these tests assert is the one the plan states: after an induced
failure, old live bytes, the Artifact head, checksum, version count, lineage edges
and event count are all unchanged.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.store import get_store


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _service(cfg, store, frame_id, workspace: Path):
    from openai4s.host.data import HostDataService

    workspace.mkdir(parents=True, exist_ok=True)

    def _resolve(path, must_exist=False):
        target = (workspace / path).resolve()
        if must_exist and not target.exists():
            raise FileNotFoundError(target)
        return target

    return HostDataService(
        store=store, config=cfg, frame_id=lambda: frame_id, resolve_path=_resolve
    )


def _seed(cfg, store, root_frame_id, project_id, filename, payload):
    versions = Path(cfg.data_dir) / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_id = f"v-{uuid.uuid4().hex[:12]}"
    snapshot = versions / f"{version_id}__{filename}"
    snapshot.write_bytes(payload)
    return store.record_cell_artifact(
        path=str(snapshot),
        filename=filename,
        content_type="text/csv",
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        producing_cell_id=None,
        frame_id=root_frame_id,
        root_frame_id=root_frame_id,
        project_id=project_id,
        snapshot_path=str(snapshot),
    )


@pytest.fixture
def project(tmp_path):
    """Two sibling sessions in one project -- the case D3 materialisation is for."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    mine = store.new_frame(kind="turn", project_id="p1")
    theirs = store.new_frame(kind="turn", project_id="p1")
    source = _seed(cfg, store, theirs, "p1", "cohort.csv", b"authoritative,bytes\n")
    workspace = tmp_path / "ws"
    service = _service(cfg, store, mine, workspace)
    try:
        yield cfg, store, service, workspace, mine, theirs, source
    finally:
        store.close()


def _counts(store, artifact_id, version_id):
    """The invariants a failed write must leave alone."""
    artifact = store.get_artifact(artifact_id)
    return {
        "head": artifact["latest_version_id"],
        "versions": len(store.list_versions(artifact_id)),
        "checksum": store.version_meta(version_id)["checksum"],
        "lineage": len(store.lineage_inputs(version_id)),
    }


# --- the inode-sharing corruption -------------------------------------------


def test_the_live_file_never_shares_an_inode_with_a_snapshot(project):
    """The worst of the six: one ordinary write through the live name rewrote the
    source session's immutable bytes, and no later `write_version_snapshot` can
    re-freeze them because it returns early when the file exists."""
    cfg, store, service, workspace, _mine, _theirs, source = project

    source_snapshot = Path(store.version_meta(source["version_id"])["snapshot_path"])
    before = source_snapshot.read_bytes()

    record = service.materialise_artifact({"version_id": source["version_id"]})
    live = workspace / "cohort.csv"
    new_snapshot = Path(store.version_meta(record["version_id"])["snapshot_path"])

    assert (
        live.stat().st_ino != source_snapshot.stat().st_ino
    ), "the live file is a hardlink to the SOURCE session's immutable snapshot"
    assert (
        live.stat().st_ino != new_snapshot.stat().st_ino
    ), "the live file is a hardlink to its own immutable snapshot"

    # And the proof that it matters: an ordinary write must not reach either.
    live.write_text("OVERWRITTEN BY THE BORROWING SESSION\n", encoding="utf-8")
    assert (
        source_snapshot.read_bytes() == before
    ), "writing the borrowed working file rewrote another session's frozen bytes"
    assert (
        hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
        == store.version_meta(source["version_id"])["checksum"]
    ), "the source version's checksum no longer describes its bytes"


def test_the_two_snapshots_may_still_share_an_inode(project):
    """The optimisation the docstring is actually about is kept: snapshot-to-
    snapshot sharing is safe because both names are immutable by contract, so a
    materialised multi-gigabyte dataset still costs a directory entry."""
    _cfg, store, service, _ws, _mine, _theirs, source = project

    source_snapshot = Path(store.version_meta(source["version_id"])["snapshot_path"])
    record = service.materialise_artifact({"version_id": source["version_id"]})
    new_snapshot = Path(store.version_meta(record["version_id"])["snapshot_path"])

    assert new_snapshot.stat().st_ino == source_snapshot.stat().st_ino, (
        "the snapshot-to-snapshot hardlink was replaced by a copy; a materialised "
        "dataset now costs its own bytes"
    )


# --- a refusal must not have destroyed anything -----------------------------


def test_a_same_session_materialise_leaves_the_existing_live_file_intact(project):
    """The refusal lives inside the transaction; the `unlink()` happens before it.

    Reachable from one cell call, or from a Web `@name#v-<id>` reference to a
    version in the same session.
    """
    cfg, store, service, workspace, mine, _theirs, _source = project
    own = _seed(cfg, store, mine, "p1", "mine.csv", b"my,own,version\n")

    workspace.mkdir(parents=True, exist_ok=True)
    live = workspace / "mine.csv"
    live.write_text("work in progress I have not saved\n", encoding="utf-8")
    before_bytes = live.read_bytes()
    before = _counts(store, own["artifact_id"], own["version_id"])

    with pytest.raises((ValueError, KeyError)):
        service.materialise_artifact({"version_id": own["version_id"]})

    assert live.exists(), "the refusal deleted the caller's live file"
    assert live.read_bytes() == before_bytes, "the refusal replaced the live bytes"
    assert _counts(store, own["artifact_id"], own["version_id"]) == before


def test_a_successful_materialise_does_not_silently_destroy_a_same_name_file(project):
    """There is no same-name conflict check on this path at all -- the existing
    live file is unlinked on the *success* path too, with no logging and no
    snapshot backfill, so an unsaved working file disappears without a word."""
    cfg, store, service, workspace, mine, _theirs, source = project
    workspace.mkdir(parents=True, exist_ok=True)
    live = workspace / "cohort.csv"
    live.write_text("unsaved analysis I would like to keep\n", encoding="utf-8")

    with pytest.raises((ValueError, FileExistsError, KeyError)):
        service.materialise_artifact({"version_id": source["version_id"]})

    assert live.read_text(encoding="utf-8") == (
        "unsaved analysis I would like to keep\n"
    ), "a same-name live file was destroyed rather than the call refused"


def test_a_same_name_materialise_can_be_asked_for_explicitly(project):
    """Refusing by default must still leave a way to do it, or the capability is
    unusable whenever a name repeats. An explicit `filename` is that way."""
    cfg, store, service, workspace, _mine, _theirs, source = project
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "cohort.csv").write_text("mine\n", encoding="utf-8")

    record = service.materialise_artifact(
        {"version_id": source["version_id"], "filename": "borrowed-cohort.csv"}
    )
    assert (workspace / "borrowed-cohort.csv").is_file()
    assert (workspace / "cohort.csv").read_text(encoding="utf-8") == "mine\n"
    assert record["version_id"]


def test_a_failed_db_commit_restores_the_previous_live_file(project, monkeypatch):
    """Rollback was one-sided: it removed the new snapshot and never restored the
    file it clobbered, so after any transaction failure the DB was consistent and
    the filesystem contradicted it."""
    cfg, store, service, workspace, _mine, _theirs, source = project
    workspace.mkdir(parents=True, exist_ok=True)
    # Deliberately NOT pre-created: a colliding name is refused before any
    # mutation now, so the interesting failure is the one *after* the files are
    # written -- where rollback used to remove the snapshot and leave the live
    # file, i.e. a consistent DB contradicted by the filesystem.
    live = workspace / "borrowed.csv"
    assert not live.exists()

    def explode(**kwargs):
        raise RuntimeError("disk full during commit")

    monkeypatch.setattr(store, "materialise_artifact_version", explode)

    with pytest.raises(RuntimeError):
        service.materialise_artifact(
            {"version_id": source["version_id"], "filename": "borrowed.csv"}
        )

    assert (
        not live.exists()
    ), "a rolled-back materialise left a live file no version row names"
    versions_dir = Path(cfg.data_dir) / "artifact-versions"
    orphans = [
        p.name for p in versions_dir.glob("*") if p.name.endswith("__borrowed.csv")
    ]
    assert orphans == [], f"a rolled-back materialise left snapshots behind: {orphans}"


def test_a_colliding_name_is_refused_before_anything_is_written(project):
    """The refusal has to precede the mutation, not merely exist: the old code
    refused inside the transaction, after `live.unlink()`."""
    cfg, store, service, workspace, _mine, _theirs, source = project
    workspace.mkdir(parents=True, exist_ok=True)
    live = workspace / "cohort.csv"
    live.write_text("unsaved work\n", encoding="utf-8")

    versions_dir = Path(cfg.data_dir) / "artifact-versions"
    before_snapshots = {p.name for p in versions_dir.glob("*")}

    with pytest.raises(FileExistsError, match="filename="):
        service.materialise_artifact({"version_id": source["version_id"]})

    assert live.read_text(encoding="utf-8") == "unsaved work\n"
    assert {
        p.name for p in versions_dir.glob("*")
    } == before_snapshots, "the refusal still wrote a snapshot"
    assert sorted(p.name for p in workspace.iterdir()) == [
        "cohort.csv"
    ], "the refusal left staging debris in the workspace"


def test_no_staged_part_file_survives_a_failure(project, monkeypatch):
    """A temporary stage is only an improvement if it is cleaned up; a leftover
    `.part` beside the deliverable is a new kind of confusing artifact."""
    cfg, store, service, workspace, _mine, _theirs, source = project
    workspace.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        store,
        "materialise_artifact_version",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        service.materialise_artifact({"version_id": source["version_id"]})

    leftovers = sorted(p.name for p in workspace.rglob("*") if p.is_file())
    assert leftovers == [], f"the workspace kept staging debris: {leftovers}"


# --- upload ------------------------------------------------------------------


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        self.events.append(event)


def _runner(cfg):
    """A real SessionRunner, which is what wires the ArtifactManager's ports.

    Constructing the manager by hand would mean choosing `workspace_for`,
    `checksum` and `guess_content_type` myself -- exactly the collaborators the
    ordering defect lives between.
    """
    from openai4s.server import gateway as gateway_mod

    return gateway_mod.SessionRunner(cfg, _Hub())


def test_a_rejected_upload_does_not_truncate_the_previous_live_file(tmp_path):
    """`target.write_bytes(raw)` runs before the DB scope resolution, so a
    `project_id` that does not match the frame's leaves the previous version's row
    naming a path whose bytes are now the rejected upload's.

    Client-reachable: `app.js` sends `S.project || undefined` and the handler
    defaults the field to `"default"`, so an upload into a non-default-project
    session with the field omitted takes exactly this branch.
    """
    cfg = _cfg(tmp_path)
    runner = _runner(cfg)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="p1")
        manager = runner.artifacts
        workspace = manager.workspace_for(frame_id)
        workspace.mkdir(parents=True, exist_ok=True)
        live = workspace / "data.csv"
        live.write_bytes(b"the version already registered\n")

        record = manager.upload(
            {
                "filename": "data.csv",
                "frame_id": frame_id,
                "project_id": "p1",
                "content_base64": "Zmlyc3QK",
            }
        )
        head = store.get_artifact(record["artifact_id"])["latest_version_id"]
        before = _counts(store, record["artifact_id"], head)
        first_bytes = live.read_bytes()

        # Now a second upload whose project_id contradicts the frame's.
        with pytest.raises(Exception):
            manager.upload(
                {
                    "filename": "data.csv",
                    "frame_id": frame_id,
                    "project_id": "some-other-project",
                    "content_base64": "c2Vjb25kCg==",
                }
            )

        assert live.read_bytes() == first_bytes, (
            "the rejected upload truncated the live file the committed version "
            "still names"
        )
        assert _counts(store, record["artifact_id"], head) == before
    finally:
        store.close()


def test_a_committed_upload_version_always_has_frozen_bytes(tmp_path):
    """`write_version_snapshot` ran after the commit and swallowed `OSError`, so
    an ENOSPC there left a committed version with `snapshot_path` NULL -- a row
    carrying a checksum for bytes nothing can produce."""
    cfg = _cfg(tmp_path)
    runner = _runner(cfg)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="default")
        manager = runner.artifacts
        record = manager.upload(
            {
                "filename": "notes.txt",
                "frame_id": frame_id,
                "project_id": "default",
                "content_base64": "aGVsbG8K",
            }
        )
        head = store.get_artifact(record["artifact_id"])["latest_version_id"]
        meta = store.version_meta(head)
        assert meta["snapshot_path"], "a committed version has no frozen bytes"
        assert Path(meta["snapshot_path"]).is_file()
        assert (
            hashlib.sha256(Path(meta["snapshot_path"]).read_bytes()).hexdigest()
            == meta["checksum"]
        )
    finally:
        store.close()


def test_a_rejected_upload_writes_nothing_to_disk_at_all(tmp_path, monkeypatch):
    """Staging already protects the previous bytes, so this asserts the *other*
    half of "validate before mutating": a refusal must not have written the
    payload to disk first.

    It matters at size. A 100 MB upload into a session whose project does not
    match spent 100 MB of I/O and disk before being told no, and on a full disk
    the staging write is itself the failure the caller then sees instead of the
    real reason.
    """
    cfg = _cfg(tmp_path)
    runner = _runner(cfg)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="p1")
        manager = runner.artifacts

        writes: list[str] = []
        real = Path.write_bytes

        def spy(self, data):
            writes.append(str(self))
            return real(self, data)

        monkeypatch.setattr(Path, "write_bytes", spy)
        with pytest.raises(Exception):
            manager.upload(
                {
                    "filename": "big.csv",
                    "frame_id": frame_id,
                    "project_id": "some-other-project",
                    "content_base64": "c2Vjb25kCg==",
                }
            )
        assert (
            writes == []
        ), f"the rejected upload wrote to disk before refusing: {writes}"
    finally:
        store.close()
