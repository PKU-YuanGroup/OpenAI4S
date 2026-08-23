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
import subprocess
import sys
import textwrap
import threading
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


def test_the_two_snapshots_have_private_inodes(project):
    """A writable alias must not be able to rewrite two version identities."""
    _cfg, store, service, _ws, _mine, _theirs, source = project

    source_snapshot = Path(store.version_meta(source["version_id"])["snapshot_path"])
    record = service.materialise_artifact({"version_id": source["version_id"]})
    new_snapshot = Path(store.version_meta(record["version_id"])["snapshot_path"])

    assert new_snapshot.stat().st_ino != source_snapshot.stat().st_ino
    assert new_snapshot.stat().st_nlink == source_snapshot.stat().st_nlink == 1


def test_materialise_refuses_same_length_snapshot_tamper_before_any_write(project):
    """The source row's checksum, not a pathname precheck, selects the bytes."""

    cfg, store, service, workspace, mine, _theirs, source = project
    source_snapshot = Path(store.version_meta(source["version_id"])["snapshot_path"])
    original = source_snapshot.read_bytes()
    tampered = b"X" * len(original)
    assert tampered != original
    source_snapshot.write_bytes(tampered)
    before_names = {
        path.name for path in (Path(cfg.data_dir) / "artifact-versions").iterdir()
    }

    from openai4s.artifact_restore import ArtifactRestoreRefused

    with pytest.raises(ArtifactRestoreRefused, match="checksum verification failed"):
        service.materialise_artifact({"version_id": source["version_id"]})

    assert store.list_artifacts({"root_frame_id": mine}) == []
    assert not (workspace / "cohort.csv").exists()
    assert {
        path.name for path in (Path(cfg.data_dir) / "artifact-versions").iterdir()
    } == before_names


def test_materialise_rejects_a_pending_snapshot_name_swap(project, monkeypatch):
    """Materialise uses the upload writer's held pending snapshot inode."""

    cfg, store, service, workspace, mine, _theirs, source = project
    versions_dir = Path(cfg.data_dir) / "artifact-versions"
    before_names = {path.name for path in versions_dir.iterdir()}
    manager = service._default_artifact_manager()
    original = manager._promote_version_stage

    def swap_pending(stage, final, *, size_bytes, checksum):
        from openai4s.server.artifacts import _PinnedUploadFile

        attacker_path = stage.path.with_name(stage.path.name + ".evil")
        with _PinnedUploadFile.create(stage.directory, attacker_path) as attacker:
            attacker.write(b"X" * size_bytes)
        stage.directory.replace(attacker_path, stage.path)
        return original(
            stage,
            final,
            size_bytes=size_bytes,
            checksum=checksum,
        )

    monkeypatch.setattr(manager, "_promote_version_stage", swap_pending)
    from openai4s.server.artifacts import ArtifactOperationError

    with pytest.raises(ArtifactOperationError):
        service.materialise_artifact({"version_id": source["version_id"]})

    assert store.list_artifacts({"root_frame_id": mine}) == []
    assert not (workspace / "cohort.csv").exists()
    assert {path.name for path in versions_dir.iterdir()} == before_names


def test_materialise_rejects_a_live_stage_name_swap_before_publication(
    project, monkeypatch
):
    """Renaming a same-size alien stage never publishes a successful row."""

    cfg, store, service, workspace, mine, _theirs, source = project
    from openai4s.server.artifacts import (
        _PinnedUploadDirectory,
        _PinnedUploadFile,
    )

    versions_dir = Path(cfg.data_dir) / "artifact-versions"
    before_names = {path.name for path in versions_dir.iterdir()}
    source_bytes = Path(store.version_meta(source["version_id"])["snapshot_path"])
    expected_size = len(source_bytes.read_bytes())
    real_replace = _PinnedUploadDirectory.replace
    swapped = False

    def replace_stage_with_alien(self, staged, destination):
        nonlocal swapped
        if not swapped and staged.name.endswith(".part"):
            attacker_path = self.path / f".{staged.name}.evil"
            attacker = _PinnedUploadFile.create(self, attacker_path)
            try:
                attacker.write(b"X" * expected_size)
            finally:
                attacker.close()
            real_replace(self, attacker_path, staged)
            swapped = True
        real_replace(self, staged, destination)

    monkeypatch.setattr(_PinnedUploadDirectory, "replace", replace_stage_with_alien)
    from openai4s.server.artifacts import ArtifactOperationError

    with pytest.raises(ArtifactOperationError):
        service.materialise_artifact({"version_id": source["version_id"]})

    assert swapped
    assert store.list_artifacts({"root_frame_id": mine}) == []
    assert not (workspace / "cohort.csv").exists()
    assert list(workspace.iterdir()) == []
    assert {path.name for path in versions_dir.iterdir()} == before_names


def test_parallel_dispatchers_share_one_materialise_writer(project, monkeypatch):
    """Web, delegated and background-compatible callers linearise by data dir."""

    cfg, store, web_service, workspace, mine, _theirs, first = project
    second_root = store.new_frame(kind="turn", project_id="p1")
    second = _seed(
        cfg,
        store,
        second_root,
        "p1",
        "cohort.csv",
        b"BBBB,source\n",
    )
    delegated_service = _service(cfg, store, mine, workspace)
    web_manager = web_service._default_artifact_manager()
    delegated_manager = delegated_service._default_artifact_manager()
    assert web_manager is delegated_manager
    web_service.set_artifact_restorer(
        None,
        materialise=web_manager.materialise_version,
        writer=web_manager.writer_transaction,
    )

    staged = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    original_stage = web_manager._stage_version_bytes_pinned

    def pause_first_writer(filename, data):
        result = original_stage(filename, data)
        staged.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(web_manager, "_stage_version_bytes_pinned", pause_first_writer)
    outcomes = []

    def run(service, version_id, *, announce=None, finished=None):
        if announce is not None:
            announce.set()
        try:
            outcomes.append(
                ("success", service.materialise_artifact({"version_id": version_id}))
            )
        except BaseException as error:
            outcomes.append(("error", error))
        finally:
            if finished is not None:
                finished.set()

    first_thread = threading.Thread(
        target=run, args=(web_service, first["version_id"]), daemon=True
    )
    first_thread.start()
    assert staged.wait(5)
    second_thread = threading.Thread(
        target=run,
        args=(delegated_service, second["version_id"]),
        kwargs={"announce": second_started, "finished": second_finished},
        daemon=True,
    )
    second_thread.start()
    assert second_started.wait(5)
    assert not second_finished.wait(0.05), "the second dispatcher bypassed the writer"
    release.set()
    first_thread.join(5)
    second_thread.join(5)
    assert not first_thread.is_alive() and not second_thread.is_alive()

    successes = [value for kind, value in outcomes if kind == "success"]
    errors = [value for kind, value in outcomes if kind == "error"]
    assert len(successes) == 1
    assert len(errors) == 1 and isinstance(errors[0], FileExistsError)
    artifacts = store.list_artifacts({"root_frame_id": mine})
    assert len(artifacts) == 1
    head = store.version_meta(artifacts[0]["latest_version_id"])
    live = workspace / "cohort.csv"
    assert head["checksum"] == hashlib.sha256(live.read_bytes()).hexdigest()
    assert Path(head["snapshot_path"]).read_bytes() == live.read_bytes()
    assert not list(workspace.glob("*.part"))
    assert not list((Path(cfg.data_dir) / "artifact-versions").glob(".upload-*.json"))
    assert not list((Path(cfg.data_dir) / "artifact-versions").glob(".pending-*"))


def test_delegated_materialise_uses_root_workspace_and_child_provenance(project):
    """The producer frame selects provenance, never a child-named workspace."""

    cfg, store, _service_obj, _workspace, parent, _theirs, source = project
    runner = _runner(cfg)
    child = store.new_frame(parent_id=parent, kind="delegate", project_id="p1")
    parent_workspace = runner.active_workspace_for(parent)
    service = _service(cfg, store, child, parent_workspace)
    service.set_artifact_restorer(
        None,
        materialise=runner.artifacts.materialise_version,
        writer=runner.artifacts.writer_transaction,
    )

    record = service.materialise_artifact(
        {
            "version_id": source["version_id"],
            "execution_cell_id": "cell-child-materialise",
        }
    )
    artifact = store.get_artifact(record["artifact_id"])
    version = store.version_meta(record["version_id"])
    live = parent_workspace / "cohort.csv"
    child_workspace = runner.active_workspace_for(child)

    assert artifact["root_frame_id"] == parent
    assert version["frame_id"] == child
    assert version["producing_cell_id"] == "cell-child-materialise"
    assert Path(version["path"]) == live
    assert live.read_bytes() == b"authoritative,bytes\n"
    assert not (child_workspace / "cohort.csv").exists()
    assert [
        item["version_id"] for item in store.lineage_inputs(record["version_id"])
    ] == [source["version_id"]]


def test_delegated_upload_also_separates_workspace_from_producer(tmp_path):
    """The generic upload API applies the same actor/root separation."""

    cfg = _cfg(tmp_path)
    runner = _runner(cfg)
    store = runner.store
    try:
        parent = store.new_frame(kind="turn", project_id="p1")
        child = store.new_frame(parent_id=parent, kind="delegate", project_id="p1")
        record = runner.artifacts.upload(
            {
                "filename": "child-result.txt",
                "frame_id": child,
                "project_id": "p1",
                "content_base64": "Y2hpbGQgYnl0ZXMK",
            }
        )
        artifact = store.get_artifact(record["artifact_id"])
        version = store.version_meta(artifact["latest_version_id"])
        parent_live = runner.active_workspace_for(parent) / "child-result.txt"

        assert artifact["root_frame_id"] == parent
        assert version["frame_id"] == child
        assert Path(version["path"]) == parent_live
        assert parent_live.read_bytes() == b"child bytes\n"
        assert not (runner.active_workspace_for(child) / "child-result.txt").exists()
    finally:
        store.close()


def test_materialise_crash_journal_recovers_and_allows_retry(project):
    """A real process death after pathname publication leaves a recoverable intent."""

    cfg, store, _service_obj, workspace, mine, _theirs, source = project
    child = textwrap.dedent("""
        import os
        import sys
        from pathlib import Path
        from openai4s.config import Config
        from openai4s.host.data import HostDataService
        from openai4s.store import get_store

        data_dir = Path(sys.argv[1])
        workspace = Path(sys.argv[2])
        frame_id = sys.argv[3]
        source_version_id = sys.argv[4]
        store = get_store(Config(data_dir=data_dir).db_path)

        def resolve(path, must_exist=False):
            target = (workspace / path).resolve()
            if must_exist and not target.exists():
                raise FileNotFoundError(target)
            return target

        service = HostDataService(
            store=store,
            config=Config(data_dir=data_dir),
            frame_id=frame_id,
            resolve_path=resolve,
        )
        real_materialise = store.materialise_artifact_version

        def die_after_publish(**kwargs):
            publish = kwargs["publish"]

            def fatal_publish(version_id, artifact_id):
                result = publish(version_id, artifact_id)
                os._exit(73)

            return real_materialise(**{**kwargs, "publish": fatal_publish})

        store.materialise_artifact_version = die_after_publish
        service.materialise_artifact({"version_id": source_version_id})
        """)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(cfg.data_dir),
            str(workspace),
            mine,
            source["version_id"],
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    versions_dir = Path(cfg.data_dir) / "artifact-versions"
    assert (workspace / "cohort.csv").exists()
    assert len(list(versions_dir.glob(".upload-v-*.json"))) == 1
    assert store.list_artifacts({"root_frame_id": mine}) == []

    retry_service = _service(cfg, store, mine, workspace)
    retried = retry_service.materialise_artifact({"version_id": source["version_id"]})
    live = workspace / "cohort.csv"
    head = store.version_meta(retried["version_id"])
    assert live.read_bytes() == b"authoritative,bytes\n"
    assert head["checksum"] == hashlib.sha256(live.read_bytes()).hexdigest()
    assert Path(head["snapshot_path"]).read_bytes() == live.read_bytes()
    assert not list(versions_dir.glob(".upload-v-*.json"))
    assert not list(versions_dir.glob(".pending-*"))
    assert not list(workspace.glob("*.part"))
    assert len(list(versions_dir.glob("v-*__cohort.csv"))) == 2


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

    from openai4s.server.artifacts import ArtifactOperationError

    with pytest.raises(ArtifactOperationError):
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
    from openai4s.server.artifacts import ArtifactOperationError

    with pytest.raises(ArtifactOperationError):
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


def test_an_in_cell_materialise_makes_one_version_not_two(project):
    """The capture must reuse the row the materialise just wrote.

    `record_cell_artifact` looks for a reusable candidate keyed on
    `v.producing_cell_id = ?`, and `materialise_artifact_version` wrote NULL
    there -- it took no such parameter. So the end-of-cell capture could never
    match, and one `host.materialise_artifact` inside a cell produced two
    versions of identical bytes.

    The second one becomes the artifact head, and the source->target lineage
    edge stays on the first. Measured before this change: two versions, and
    `lineage_inputs(head) == []` -- an approved cross-session copy whose head
    claims it has no inputs, which is the wrong-provenance failure this
    subsystem exists to prevent.

    The identity is plumbed the way `save_artifact` already plumbs it:
    `worker._attach_cell_context` -> the spec -> `HostDataService` ->
    `Store` -> the repository. Four hops, and the `Store` facade was the one
    that had to be remembered separately.
    """
    _cfg_obj, store, service, _workspace, mine, _theirs, seeded = project
    cell_id = "cell-dedup"

    brought = service.materialise_artifact(
        {
            "version_id": seeded["version_id"],
            "filename": "cohort.csv",
            "execution_cell_id": cell_id,
        }
    )

    live = Path(store.version_meta(brought["version_id"])["path"])
    payload = live.read_bytes()
    captured = store.record_cell_artifact(
        path=str(live),
        filename="cohort.csv",
        content_type="text/csv",
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        producing_cell_id=cell_id,
        frame_id=mine,
    )

    assert (
        captured["version_id"] == brought["version_id"]
    ), "the capture forked a second version"
    assert len(store.list_versions(brought["artifact_id"])) == 1

    head = store.get_artifact(brought["artifact_id"])["latest_version_id"]
    inputs = [row["version_id"] for row in store.lineage_inputs(head)]
    assert inputs == [seeded["version_id"]], (
        "the head carries no inputs; the lineage edge was stranded on a "
        "superseded version"
    )


def test_the_worker_gives_a_materialise_the_cell_identity_to_dedup_with():
    """The first hop, which the test above cannot reach.

    That test calls the service directly and passes `execution_cell_id`
    itself, so it proves the service->Store->repository half and nothing about
    where the value comes from. A cell calling `host.materialise_artifact(...)`
    supplies no such field: `worker._attach_cell_context` injects it, and it
    filtered on `save_artifact` alone. Reverting that filter leaves the test
    above green and the product unfixed -- measured, which is why this exists.
    """
    from openai4s.kernel import worker as worker_mod

    previous = worker_mod._ACTIVE_CELL_ID[0]
    worker_mod._ACTIVE_CELL_ID[0] = "cell-from-worker"
    try:
        attached = worker_mod._attach_cell_context(
            "materialise_artifact", [{"version_id": "v-src", "filename": "x.csv"}]
        )
        assert attached[0]["executionCellId"] == "cell-from-worker"

        # And the neighbour it already covered is unchanged.
        saved = worker_mod._attach_cell_context("save_artifact", [{"path": "x.csv"}])
        assert saved[0]["executionCellId"] == "cell-from-worker"

        # A method that writes nothing keeps its args untouched.
        other = worker_mod._attach_cell_context("query", [{"sql": "SELECT 1"}])
        assert "executionCellId" not in other[0]
    finally:
        worker_mod._ACTIVE_CELL_ID[0] = previous
