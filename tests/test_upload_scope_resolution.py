"""Uploading into a session outside the `default` project was impossible.

`ArtifactManager.upload` read `payload.get("project_id") or "default"`, so a
request that named a `frame_id` and no project asserted `"default"` on the
caller's behalf. `artifact_write_scope` treats a non-None `project_id` as an
assertion about the producer frame's project and refuses when the two disagree,
which is correct — so every upload into a session belonging to a real project
was refused for naming a project the client had never mentioned.

And the refusal was a `ValueError` nothing caught: it reached the dispatcher's
catch-all and came back as `500 internal_error`, which tells a client that the
server broke rather than that its scope was wrong. A scope conflict is the
caller's, and P0-4 requires it carry a stable status a client can act on.

Found by uploading a file through the running daemon, not by reading the code —
the `or "default"` reads as a harmless default until you notice what the
resolver does with a value that is present.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import artifacts as artifacts_mod
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifacts import ArtifactOperationError


class _Hub:
    def emitter(self, root_frame_id):
        def emit(event):
            del event

        return emit

    def broadcast(self, root_frame_id, event):
        del root_frame_id, event

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id


@pytest.fixture
def runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    made = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    yield made
    made.close()


def _payload(frame_id: str | None, body: bytes, **extra) -> dict:
    out = {
        "filename": "table.tsv",
        "content_base64": base64.b64encode(body).decode("ascii"),
    }
    if frame_id is not None:
        out["frame_id"] = frame_id
    out.update(extra)
    return out


def _reopen_artifacts(runner):
    current = runner.artifacts
    return type(current)(
        data_dir=current.data_dir,
        store=current.store,
        workspace_for=current.workspace_for,
        broadcast=current.broadcast,
        guess_content_type=current.guess_content_type,
        checksum=current.checksum,
        trusted_delivery=current.trusted_delivery,
    )


def _nested_text_artifact(runner, frame_id: str, data: bytes = b"old bytes\n"):
    target = runner.workspace_for(frame_id) / "results" / "notes.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    record = runner.store.save_artifact(
        path=str(target),
        filename="results/notes.txt",
        content_type="text/plain; charset=utf-8",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        frame_id=frame_id,
        project_id="default",
    )
    runner.artifacts.write_version_snapshot(
        record["version_id"], record["filename"], data=data
    )
    return record, target


def test_an_upload_into_a_real_project_does_not_assert_default(runner):
    """The defect, on the shape a client actually sends: frame, no project."""
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )

    saved = runner.artifacts.upload(_payload(frame_id, b"a\tb\n1\t2\n"))

    assert saved["artifact_id"]
    stored = runner.store.get_artifact(saved["artifact_id"])
    # The project comes from the frame, which is the only place that knows it.
    assert stored["project_id"] == "proj_science"


def test_an_upload_with_no_frame_still_lands_in_default(runner):
    """Removing the default must not move the frameless case."""
    saved = runner.artifacts.upload(_payload(None, b"loose bytes\n"))

    stored = runner.store.get_artifact(saved["artifact_id"])
    assert stored["project_id"] == "default"


def test_a_frameless_upload_accepts_an_equivalent_data_dir_spelling(tmp_path):
    """Journal validation must not compare one resolved spelling with one lexical.

    macOS exposes ``/var`` through ``/private/var``.  ``TemporaryDirectory`` can
    hand the capture gate the first spelling while ``Path.resolve()`` produces
    the second, and resolving only the expected upload root made every
    frameless upload fail as an apparent frame escape.  A local symlink gives
    every POSIX CI host the same pair of equivalent spellings.
    """

    real = tmp_path / "real-data"
    real.mkdir()
    alias = tmp_path / "data-alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    cfg = Config(
        data_dir=alias,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    made = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        saved = made.artifacts.upload(_payload(None, b"aliased bytes\n"))
        artifact = made.store.get_artifact(saved["artifact_id"])
        assert artifact["project_id"] == "default"
        assert (alias / "uploads" / "table.tsv").read_bytes() == b"aliased bytes\n"
    finally:
        made.close()


def test_a_second_frameless_upload_versions_the_same_exact_scope(runner):
    first = runner.artifacts.upload(_payload(None, b"first\n"))
    second = runner.artifacts.upload(_payload(None, b"second\n"))

    assert second["artifact_id"] == first["artifact_id"]
    versions = runner.store.list_versions(first["artifact_id"])
    assert len(versions) == 2
    assert runner.store.get_artifact(first["artifact_id"])["checksum"] == (
        hashlib.sha256(b"second\n").hexdigest()
    )


def test_a_frameless_unchanged_edit_keeps_the_upload_live_path(runner):
    saved = runner.artifacts.upload(_payload(None, b"same\n"))
    artifact_id = saved["artifact_id"]
    before = runner.store.get_artifact(artifact_id)
    live = Path(before["path"])

    result = runner.artifacts.edit(artifact_id, "same\n")

    assert result["unchanged"] is True
    assert result["version_id"] == before["latest_version_id"]
    assert live == runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    assert live.read_bytes() == b"same\n"
    assert runner.store.get_artifact(artifact_id)["path"] == str(live)
    assert len(runner.store.list_versions(artifact_id)) == 1
    assert not (runner.workspace_for("default") / "table.tsv").exists()


def test_a_frameless_changed_edit_versions_in_the_upload_namespace(runner):
    saved = runner.artifacts.upload(_payload(None, b"first\n"))
    artifact_id = saved["artifact_id"]
    first_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    live = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"

    result = runner.artifacts.edit(artifact_id, "second\n")

    assert result["unchanged"] is False
    assert result["version_id"] != first_version_id
    assert live.read_bytes() == b"second\n"
    current = runner.store.get_artifact(artifact_id)
    assert current["path"] == str(live)
    assert current["latest_version_id"] == result["version_id"]
    assert len(runner.store.list_versions(artifact_id)) == 2
    first = runner.store.version_meta(first_version_id)
    assert Path(first["snapshot_path"]).read_bytes() == b"first\n"
    assert not (runner.workspace_for("default") / "table.tsv").exists()


def test_a_frameless_restore_stays_in_the_upload_namespace(runner):
    first = runner.artifacts.upload(_payload(None, b"first\n"))
    first_version_id = runner.store.get_artifact(first["artifact_id"])[
        "latest_version_id"
    ]
    runner.artifacts.upload(_payload(None, b"second\n"))
    live = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"

    restored = runner.artifacts.restore(first["artifact_id"], first_version_id)

    assert restored["ok"] is True
    assert restored["restored_from_version_id"] == first_version_id
    assert live.read_bytes() == b"first\n"
    current = runner.store.get_artifact(first["artifact_id"])
    assert current["path"] == str(live)
    assert current["latest_version_id"] == restored["version_id"]
    assert not (runner.workspace_for("default") / "table.tsv").exists()


@pytest.mark.parametrize("follower", ["edit", "restore"])
def test_exact_artifact_writers_serialize_edit_and_restore(
    runner, monkeypatch, follower
):
    first = runner.artifacts.upload(_payload(None, b"alpha\n"))
    artifact_id = first["artifact_id"]
    source_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    runner.artifacts.upload(_payload(None, b"beta\n"))
    live = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"

    first_staged = threading.Event()
    release_first = threading.Event()
    follower_started = threading.Event()
    follower_staged = threading.Event()
    stage_calls: list[str] = []
    errors: list[BaseException] = []
    original_stage = runner.artifacts._stage_version_bytes_pinned

    def gate_stage(filename, data):
        stage_calls.append(filename)
        if len(stage_calls) == 1:
            first_staged.set()
            assert release_first.wait(5), "timed out releasing first exact writer"
        else:
            follower_staged.set()
        return original_stage(filename, data)

    monkeypatch.setattr(runner.artifacts, "_stage_version_bytes_pinned", gate_stage)

    def first_restore():
        try:
            result = runner.artifacts.restore(artifact_id, source_version_id)
            assert result["ok"] is True
        except BaseException as error:
            errors.append(error)

    def follow():
        follower_started.set()
        try:
            if follower == "edit":
                result = runner.artifacts.edit(artifact_id, "gamma\n")
                assert result["unchanged"] is False
            else:
                result = runner.artifacts.restore(artifact_id, source_version_id)
                assert result["ok"] is True
        except BaseException as error:
            errors.append(error)

    first_thread = threading.Thread(target=first_restore)
    follower_thread = threading.Thread(target=follow)
    first_thread.start()
    assert first_staged.wait(5), "first exact writer never reached staging"
    follower_thread.start()
    assert follower_started.wait(5), "follower writer did not start"
    assert not follower_staged.wait(0.2), "exact Artifact writers overlapped"
    release_first.set()
    first_thread.join(5)
    follower_thread.join(5)

    assert not first_thread.is_alive()
    assert not follower_thread.is_alive()
    assert errors == []
    assert follower_staged.is_set()
    expected = b"gamma\n" if follower == "edit" else b"alpha\n"
    assert live.read_bytes() == expected
    head = runner.store.get_artifact(artifact_id)
    metadata = runner.store.version_meta(head["latest_version_id"])
    assert metadata["checksum"] == hashlib.sha256(expected).hexdigest()
    assert Path(metadata["snapshot_path"]).read_bytes() == expected


def test_frameless_restore_journal_recovery_keeps_the_committed_inode(
    runner, monkeypatch
):
    first = runner.artifacts.upload(_payload(None, b"first\n"))
    source_version_id = runner.store.get_artifact(first["artifact_id"])[
        "latest_version_id"
    ]
    runner.artifacts.upload(_payload(None, b"second\n"))
    real_unlink = Path.unlink

    def leave_journal(self, *args, **kwargs):
        if self.name.startswith(".upload-v-") and self.name.endswith(".json"):
            raise OSError("simulated crash before restore cleanup")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", leave_journal)
    restored = runner.artifacts.restore(first["artifact_id"], source_version_id)
    monkeypatch.undo()
    live = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    expected_head = runner.store.get_artifact(first["artifact_id"])["latest_version_id"]
    assert restored["ok"] is True
    assert list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))

    _reopen_artifacts(runner)

    assert live.read_bytes() == b"first\n"
    assert (
        runner.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == expected_head
    )
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))


def test_restore_recovery_rolls_back_a_same_byte_published_inode_swap(
    runner, monkeypatch
):
    first = runner.artifacts.upload(_payload(None, b"alpha\n"))
    artifact_id = first["artifact_id"]
    source_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    runner.artifacts.upload(_payload(None, b"beta\n"))
    runner.store._conn.execute(
        "UPDATE artifacts SET filename=?,content_type=? WHERE artifact_id=?",
        ("renamed-current.txt", "text/x-current", artifact_id),
    )
    runner.store._conn.commit()
    previous_head = copy.deepcopy(runner.store.get_artifact(artifact_id))
    previous_versions = copy.deepcopy(runner.store.list_versions(artifact_id))
    real_unlink = Path.unlink

    def leave_journal(self, *args, **kwargs):
        if self.name.startswith(".upload-v-") and self.name.endswith(".json"):
            raise OSError("simulated crash after restore commit")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", leave_journal)
    restored = runner.artifacts.restore(artifact_id, source_version_id)
    monkeypatch.undo()
    assert restored["ok"] is True
    live = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    parked = live.with_name("parked-published-restore.tsv")
    decoy = live.with_name("same-byte-published-restore.tsv")
    decoy.write_bytes(b"alpha\n")
    live.rename(parked)
    decoy.rename(live)

    _reopen_artifacts(runner)

    assert runner.store.get_artifact(artifact_id) == previous_head
    assert runner.store.list_versions(artifact_id) == previous_versions
    assert live.read_bytes() == b"beta\n"
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    parked.unlink()


def test_a_frameless_edit_refuses_a_replaced_upload_directory(runner, tmp_path):
    saved = runner.artifacts.upload(_payload(None, b"first\n"))
    uploads = runner.cfg.data_dir.resolve() / "uploads"
    detached = tmp_path / "detached-uploads"
    outside = tmp_path / "outside"
    outside.mkdir()
    uploads.rename(detached)
    try:
        uploads.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        detached.rename(uploads)
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ArtifactOperationError) as refused:
        runner.artifacts.edit(saved["artifact_id"], "second\n")

    assert refused.value.code == 500
    assert refused.value.message == "write failed"
    assert (detached / "table.tsv").read_bytes() == b"first\n"
    assert not list(outside.iterdir())


def test_unchanged_exact_edit_rejects_a_same_byte_final_name_swap(runner, monkeypatch):
    saved = runner.artifacts.upload(_payload(None, b"same\n"))
    artifact_id = saved["artifact_id"]
    before = copy.deepcopy(runner.store.get_artifact(artifact_id))
    target = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    parked = target.with_name("parked-original.tsv")
    decoy = target.with_name("same-byte-decoy.tsv")
    decoy.write_bytes(b"same\n")
    original = artifacts_mod._PinnedUploadFile.verified_bytes
    reads = 0

    def swap_after_first_read(pinned, *args, **kwargs):
        nonlocal reads
        data = original(pinned, *args, **kwargs)
        if pinned.path == target:
            reads += 1
            if reads == 1:
                target.rename(parked)
                decoy.rename(target)
        return data

    monkeypatch.setattr(
        artifacts_mod._PinnedUploadFile,
        "verified_bytes",
        swap_after_first_read,
    )
    with pytest.raises(ArtifactOperationError) as refused:
        runner.artifacts.edit(artifact_id, "same\n")
    monkeypatch.undo()

    assert refused.value.code == 500
    assert runner.store.get_artifact(artifact_id) == before
    assert len(runner.store.list_versions(artifact_id)) == 1
    assert target.read_bytes() == b"same\n"
    parked.unlink()


def test_changed_exact_edit_rolls_back_a_same_byte_pre_publish_name_swap(
    runner, monkeypatch
):
    saved = runner.artifacts.upload(_payload(None, b"old\n"))
    artifact_id = saved["artifact_id"]
    before = copy.deepcopy(runner.store.get_artifact(artifact_id))
    before_versions = copy.deepcopy(runner.store.list_versions(artifact_id))
    target = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    parked = target.with_name("parked-old.tsv")
    decoy = target.with_name("same-byte-old-decoy.tsv")
    decoy.write_bytes(b"old\n")
    real_replace = artifacts_mod._PinnedUploadDirectory.replace
    swapped = False

    def swap_before_backup(directory, source, destination):
        nonlocal swapped
        if source == target and destination.name.endswith(".backup") and not swapped:
            swapped = True
            target.rename(parked)
            decoy.rename(target)
        return real_replace(directory, source, destination)

    monkeypatch.setattr(
        artifacts_mod._PinnedUploadDirectory,
        "replace",
        swap_before_backup,
    )
    with pytest.raises(ArtifactOperationError) as refused:
        runner.artifacts.edit(artifact_id, "new\n")
    monkeypatch.undo()

    assert refused.value.code == 500
    assert runner.store.get_artifact(artifact_id) == before
    assert runner.store.list_versions(artifact_id) == before_versions
    assert target.read_bytes() == b"old\n"
    assert not list(target.parent.glob("*.part"))
    assert not list(target.parent.glob(".*.upload-*.backup"))
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    parked.unlink()


def test_prior_head_freeze_stays_bound_when_the_final_name_is_swapped(
    runner, monkeypatch
):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    target = runner.workspace_for(frame_id) / "legacy.txt"
    target.write_bytes(b"legacy\n")
    first = runner.store.save_artifact(
        path=str(target),
        filename=target.name,
        content_type="text/plain",
        size_bytes=len(b"legacy\n"),
        checksum=hashlib.sha256(b"legacy\n").hexdigest(),
        frame_id=frame_id,
        project_id="default",
    )
    parked = target.with_name("parked-legacy.txt")
    decoy = target.with_name("same-byte-legacy-decoy.txt")
    decoy.write_bytes(b"legacy\n")
    original = runner.artifacts.write_version_snapshot

    def freeze_then_swap(*args, **kwargs):
        result = original(*args, **kwargs)
        target.rename(parked)
        decoy.rename(target)
        return result

    monkeypatch.setattr(
        runner.artifacts,
        "write_version_snapshot",
        freeze_then_swap,
    )
    with pytest.raises(ArtifactOperationError):
        runner.artifacts.edit(first["artifact_id"], "new\n")
    monkeypatch.undo()

    current = runner.store.get_artifact(first["artifact_id"])
    assert current["latest_version_id"] == first["version_id"]
    meta = runner.store.version_meta(first["version_id"])
    assert Path(meta["snapshot_path"]).read_bytes() == b"legacy\n"
    assert target.read_bytes() == b"legacy\n"
    assert not list(target.parent.glob("*.part"))
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    parked.unlink()


def test_prior_head_snapshot_failure_refuses_before_publish_and_restart(
    runner, monkeypatch
):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    target = runner.workspace_for(frame_id) / "legacy.txt"
    target.write_bytes(b"legacy\n")
    first = runner.store.save_artifact(
        path=str(target),
        filename=target.name,
        content_type="text/plain",
        size_bytes=len(b"legacy\n"),
        checksum=hashlib.sha256(b"legacy\n").hexdigest(),
        frame_id=frame_id,
        project_id="default",
    )
    before = copy.deepcopy(runner.store.get_artifact(first["artifact_id"]))
    before_versions = copy.deepcopy(runner.store.list_versions(first["artifact_id"]))

    # `write_version_snapshot` historically swallowed OSError.  A no-op is the
    # externally visible result of that failure and must not be enough to let
    # the live publication/journal begin.
    monkeypatch.setattr(
        runner.artifacts,
        "write_version_snapshot",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ArtifactOperationError) as refused:
        runner.artifacts.edit(first["artifact_id"], "new\n")
    monkeypatch.undo()

    assert refused.value.code == 500
    assert runner.store.get_artifact(first["artifact_id"]) == before
    assert runner.store.list_versions(first["artifact_id"]) == before_versions
    assert target.read_bytes() == b"legacy\n"
    assert not list(target.parent.glob("*.part"))
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))

    # With no transaction journal and the old truth intact, normal startup is
    # immediately usable rather than fail-closed on an impossible rollback.
    _reopen_artifacts(runner)
    assert runner.store.get_artifact(first["artifact_id"]) == before
    assert target.read_bytes() == b"legacy\n"


def test_restore_rolls_back_a_same_byte_post_publish_name_swap(runner, monkeypatch):
    first = runner.artifacts.upload(_payload(None, b"alpha\n"))
    artifact_id = first["artifact_id"]
    source_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    runner.artifacts.upload(_payload(None, b"beta\n"))
    runner.store._conn.execute(
        "UPDATE artifacts SET filename=?,content_type=? WHERE artifact_id=?",
        ("renamed-current.txt", "text/x-current", artifact_id),
    )
    runner.store._conn.commit()
    before = copy.deepcopy(runner.store.get_artifact(artifact_id))
    before_versions = copy.deepcopy(runner.store.list_versions(artifact_id))
    target = runner.cfg.data_dir.resolve() / "uploads" / "table.tsv"
    parked = target.with_name("parked-restored-stage.tsv")
    decoy = target.with_name("same-byte-restored-decoy.tsv")
    decoy.write_bytes(b"alpha\n")
    original = runner.store.record_artifact_restore

    def swap_after_publish(**fields):
        publish = fields["publish"]

        def wrapped_publish(version_id, published_artifact_id):
            snapshot = publish(version_id, published_artifact_id)
            target.rename(parked)
            decoy.rename(target)
            return snapshot

        return original(**{**fields, "publish": wrapped_publish})

    monkeypatch.setattr(runner.store, "record_artifact_restore", swap_after_publish)
    refused = runner.artifacts.restore(artifact_id, source_version_id)
    monkeypatch.undo()

    assert refused == {"error": "restore failed", "code": "restore_failed"}
    assert runner.store.get_artifact(artifact_id) == before
    assert runner.store.list_versions(artifact_id) == before_versions
    assert target.read_bytes() == b"beta\n"
    assert not list(target.parent.glob("*.part"))
    assert not list(target.parent.glob(".*.upload-*.backup"))
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    parked.unlink()


def test_a_project_that_really_disagrees_is_still_refused(runner):
    """The check is right; only the invented value was wrong.

    A caller that names a project *and* a frame from a different one is making a
    claim that cannot be satisfied, and it must not be resolved by silently
    preferring one of the two.
    """
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )

    with pytest.raises(ArtifactOperationError) as caught:
        runner.artifacts.upload(_payload(frame_id, b"x\n", project_id="proj_other"))

    assert caught.value.code == 409, caught.value.code
    assert "project_id" in caught.value.message
    # Refused before anything is written: no artifact, no stray part file.
    assert runner.store.list_artifacts(frame_id) == []
    workspace = runner.workspace_for(frame_id)
    assert not list(workspace.glob("*.part"))
    assert not (workspace / "table.tsv").exists()


def test_the_route_answers_a_scope_conflict_as_a_conflict_not_a_500(runner):
    """Driven through the real handler, because the status is the defect.

    A direct call could assert the exception type and still leave the route
    answering `500 internal_error` — which is what it did, because nothing
    between the repository and the dispatcher's catch-all knew this was the
    caller's error.
    """
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    handler_class = gateway_mod.make_handler(runner.cfg, runner.hub, runner)
    handler = object.__new__(handler_class)
    handler._correlation_id = "req-upload"
    handler._last_status = 0
    handler.headers = {}
    handler._query = lambda: {}
    handler._body = lambda: _payload(frame_id, b"x\n", project_id="proj_other")
    seen: list[tuple[object, int]] = []
    handler._json = lambda value, code=200: seen.append((value, code))

    from openai4s.server.errors import GatewayError, gateway_error_payload

    try:
        handler._api("POST", "/uploads")
    except GatewayError as error:
        # What the dispatcher does with a raised GatewayError, reproduced here
        # rather than re-derived: `_api` raises, `_route` converts.
        seen.append((gateway_error_payload(error), error.code))

    assert seen, "the route answered nothing"
    body, status = seen[-1]
    assert status == 409, (status, body)
    assert "internal error" not in json.dumps(body, default=str)


def test_a_five_thousand_row_upload_is_stored_whole(runner, tmp_path):
    """The size this route is asked for in practice, end to end.

    P1-A's exit criteria name a 5001-row, 101-column table; it is worth one
    assertion that the upload path carries it rather than only the small
    fixtures every other test uses.
    """
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    header = "\t".join(f"col{i}" for i in range(101))
    rows = "\n".join(
        "\t".join(str(r * 101 + c) for c in range(101)) for r in range(5001)
    )
    body = (header + "\n" + rows + "\n").encode("utf-8")

    saved = runner.artifacts.upload(_payload(frame_id, body))

    stored = runner.store.get_artifact(saved["artifact_id"])
    assert stored["size_bytes"] == len(body)
    landed = Path(runner.workspace_for(frame_id)) / "table.tsv"
    assert landed.read_bytes() == body


# --- the atomic boundary -----------------------------------------------------


def test_a_snapshot_that_cannot_be_written_leaves_no_version_behind(
    runner, monkeypatch
):
    """The order was DB-then-snapshot, through a call that swallows `OSError`.

    So a snapshot the filesystem refused produced a *committed* version with a
    NULL `snapshot_path` and no frozen bytes — and the upload returned success.
    `ArtifactRestoreService.verified_snapshot_bytes` refuses precisely that
    version, so what the route handed back was an artifact no restore could
    ever read, with a checksum describing bytes that were nowhere.

    Staging the bytes first moves the failure to before the row exists, which
    is the only place it can happen without leaving something behind.
    """
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    before = len(runner.store.list_artifacts(frame_id))

    def refuse(_filename, _data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runner.artifacts, "_stage_version_bytes_pinned", refuse)

    with pytest.raises(Exception):
        runner.artifacts.upload(_payload(frame_id, b"a\tb\n"))
    monkeypatch.undo()
    # Nothing became visible: no artifact, no version, no live file, no stage.
    assert len(runner.store.list_artifacts(frame_id)) == before
    workspace = runner.workspace_for(frame_id)
    assert not (workspace / "table.tsv").exists()
    assert not list(workspace.glob("*.part"))


def test_every_committed_version_has_its_frozen_bytes(runner):
    """The invariant the comment claimed and the code did not enforce."""
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )

    saved = runner.artifacts.upload(_payload(frame_id, b"first\n"))
    runner.artifacts.upload(_payload(frame_id, b"second\n"))

    versions = runner.store.list_versions(saved["artifact_id"])
    assert len(versions) == 2, versions
    for version in versions:
        # `version_meta`, not `list_versions`: the listing does not project
        # `snapshot_path`, and this is the accessor
        # `ArtifactRestoreService.verified_snapshot_bytes` reads, so it is the
        # one whose answer decides whether a restore can happen.
        meta = runner.store.version_meta(version["version_id"])
        snapshot = (meta or {}).get("snapshot_path")
        assert snapshot, f"version {version['version_id']} has no snapshot path"
        assert Path(snapshot).is_file(), f"{snapshot} is not on disk"
        # The name is the version's, not the pending one it was staged under.
        assert Path(snapshot).name.startswith(version["version_id"])
        assert not Path(snapshot).name.startswith(".pending-")


def test_a_failed_upload_does_not_leave_a_pending_snapshot_behind(runner, monkeypatch):
    """A stage that outlives its failure is a slow disk leak."""
    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    real_save = runner.store.commit_artifact_upload

    def explode(**kwargs):
        del kwargs
        raise RuntimeError("database is locked")

    runner.store.commit_artifact_upload = explode
    try:
        with pytest.raises(ArtifactOperationError) as caught:
            runner.artifacts.upload(_payload(frame_id, b"x\n"))
        assert caught.value.code == 500
    finally:
        runner.store.commit_artifact_upload = real_save

    assert not list(runner.artifacts.versions_dir().glob(".pending-*"))
    assert not list(runner.workspace_for(frame_id).glob("*.part"))


def test_pending_snapshot_name_swap_cannot_commit_attacker_bytes(runner, monkeypatch):
    """The pending inode stays held across promotion and final-name proof."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    raw = b"GOOD snapshot bytes\n"
    evil = b"EVIL snapshot bytes\n"
    assert len(raw) == len(evil)
    original = runner.artifacts._promote_version_stage

    def swap_pending(stage, final, *, size_bytes, checksum):
        attacker = stage.path.with_name(stage.path.name + ".attacker")
        with artifacts_mod._PinnedUploadFile.create(stage.directory, attacker) as held:
            held.write(evil)
            held.verified_bytes(named_as=attacker)
        stage.directory.replace(attacker, stage.path)
        return original(
            stage,
            final,
            size_bytes=size_bytes,
            checksum=checksum,
        )

    monkeypatch.setattr(runner.artifacts, "_promote_version_stage", swap_pending)
    with pytest.raises(ArtifactOperationError) as failure:
        runner.artifacts.upload(_payload(frame_id, raw))
    assert failure.value.code == 500

    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == []
    assert not (runner.workspace_for(frame_id) / "table.tsv").exists()
    versions = runner.artifacts.versions_dir()
    assert not list(versions.glob(".pending-*"))
    assert not list(versions.glob(".upload-*.json"))
    assert not list(versions.glob("v-*__table.tsv"))


@pytest.mark.parametrize("existing", [False, True], ids=["first", "new-version"])
@pytest.mark.parametrize("fault", ["promotion", "live-replace"])
def test_upload_publish_fault_restores_exact_previous_state(
    runner, monkeypatch, existing, fault
):
    """Every filesystem fault rolls back head, version, event, and live bytes."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    events = []
    artifact_id = None
    if existing:
        saved = runner.artifacts.upload(
            _payload(frame_id, b"old bytes\n"),
            broadcast=lambda _root, event: events.append(event),
        )
        artifact_id = saved["artifact_id"]

    target = runner.workspace_for(frame_id) / "table.tsv"
    before_artifacts = copy.deepcopy(
        runner.store.list_artifacts({"root_frame_id": frame_id})
    )
    before_versions = (
        copy.deepcopy(runner.store.list_versions(artifact_id)) if artifact_id else []
    )
    before_events = list(events)
    before_live = target.read_bytes() if target.exists() else None
    before_snapshots = set(runner.artifacts.versions_dir().iterdir())

    if fault == "promotion":
        monkeypatch.setattr(
            runner.artifacts,
            "_promote_version_stage",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("promotion fault")),
        )
    else:
        real_replace = artifacts_mod._PinnedUploadDirectory.replace

        def fail_live_replace(directory, source, destination):
            if destination == target and source.name.endswith(".part"):
                raise OSError("live replace fault")
            return real_replace(directory, source, destination)

        monkeypatch.setattr(
            artifacts_mod._PinnedUploadDirectory,
            "replace",
            fail_live_replace,
        )

    with pytest.raises(ArtifactOperationError) as caught:
        runner.artifacts.upload(
            _payload(frame_id, b"new bytes\n"),
            broadcast=lambda _root, event: events.append(event),
        )
    assert caught.value.code == 500
    monkeypatch.undo()

    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before_artifacts
    if artifact_id:
        assert runner.store.list_versions(artifact_id) == before_versions
    assert events == before_events
    assert (target.read_bytes() if target.exists() else None) == before_live
    assert set(runner.artifacts.versions_dir().iterdir()) == before_snapshots
    assert not list(target.parent.glob("*.part"))
    assert not list(target.parent.glob(".*.upload-*.backup"))

    # Reopening runs the same recovery pass used at daemon startup. A normal
    # API failure has already closed the transaction, so this is idempotent and
    # cannot change the recovered truth.
    _reopen_artifacts(runner)
    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before_artifacts
    assert (target.read_bytes() if target.exists() else None) == before_live


@pytest.mark.parametrize("existing", [False, True], ids=["first", "new-version"])
def test_lost_commit_response_is_compensated_before_api_failure(
    runner, monkeypatch, existing
):
    """Even an ambiguous post-commit exception is restored before returning 500."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    if existing:
        runner.artifacts.upload(_payload(frame_id, b"old bytes\n"))
    target = runner.workspace_for(frame_id) / "table.tsv"
    before = copy.deepcopy(runner.store.list_artifacts({"root_frame_id": frame_id}))
    before_live = target.read_bytes() if target.exists() else None
    original = runner.store.commit_artifact_upload

    def commit_then_lose_response(**fields):
        original(**fields)
        raise OSError("commit response lost")

    monkeypatch.setattr(
        runner.store, "commit_artifact_upload", commit_then_lose_response
    )
    with pytest.raises(ArtifactOperationError) as caught:
        runner.artifacts.upload(_payload(frame_id, b"new bytes\n"))
    assert caught.value.code == 500
    monkeypatch.undo()

    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before
    assert (target.read_bytes() if target.exists() else None) == before_live
    _reopen_artifacts(runner)
    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before


@pytest.mark.parametrize("existing", [False, True], ids=["first", "new-version"])
def test_upload_head_cas_preserves_a_concurrent_writer(runner, monkeypatch, existing):
    """A Cell write that wins admission is never erased by upload rollback."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    artifact_id = None
    if existing:
        artifact_id = runner.artifacts.upload(_payload(frame_id, b"old bytes\n"))[
            "artifact_id"
        ]
    target = runner.workspace_for(frame_id) / "table.tsv"
    original = runner.store.commit_artifact_upload
    raced = {}

    def race_then_commit(**fields):
        target.write_bytes(b"racer bytes\n")
        raced.update(
            runner.store.save_artifact(
                path=str(target),
                filename=target.name,
                content_type="text/tab-separated-values",
                size_bytes=len(b"racer bytes\n"),
                checksum=hashlib.sha256(b"racer bytes\n").hexdigest(),
                frame_id=frame_id,
                project_id="proj_science",
                artifact_id=artifact_id,
            )
        )
        return original(**fields)

    monkeypatch.setattr(runner.store, "commit_artifact_upload", race_then_commit)
    with pytest.raises(ArtifactOperationError) as caught:
        runner.artifacts.upload(_payload(frame_id, b"upload bytes\n"))
    assert caught.value.code == 500
    monkeypatch.undo()

    assert target.read_bytes() == b"racer bytes\n"
    stored = runner.store.get_artifact(raced["artifact_id"])
    assert stored["latest_version_id"] == raced["version_id"]
    assert stored["checksum"] == raced["checksum"]
    assert not list(target.parent.glob("*.part"))
    assert not list(runner.artifacts.versions_dir().glob(".pending-*"))
    assert not list(runner.artifacts.versions_dir().glob(".upload-*.json"))


@pytest.mark.parametrize("existing", [False, True], ids=["first", "new-version"])
@pytest.mark.parametrize(
    "crash_point", ["prepared", "snapshot-published", "live-published"]
)
def test_startup_recovers_every_durable_upload_journal_stage(
    runner, existing, crash_point
):
    """A process death before SQLite commit restores the exact prior truth."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    prior = None
    if existing:
        saved = runner.artifacts.upload(_payload(frame_id, b"old bytes\n"))
        prior = runner.store.get_artifact(saved["artifact_id"])
    target = runner.workspace_for(frame_id) / "table.tsv"
    before = copy.deepcopy(runner.store.list_artifacts({"root_frame_id": frame_id}))
    before_live = target.read_bytes() if target.exists() else None
    before_snapshots = set(runner.artifacts.versions_dir().iterdir())

    version_id = "v-crashprepared"
    artifact_id = prior["artifact_id"] if prior else "a-crashprepared"
    staged = target.with_name(f"{target.name}.deadbeef.part")
    pending = runner.artifacts.versions_dir() / f".pending-{'a' * 32}__{target.name}"
    final = runner.artifacts.versions_dir() / f"{version_id}__{target.name}"
    backup = target.with_name(f".{target.name}.upload-{version_id}.backup")
    journal = runner.artifacts.versions_dir() / f".upload-{version_id}.json"
    new_bytes = b"crash bytes\n"
    runner.artifacts._write_durable_upload_file(staged, new_bytes)
    runner.artifacts._write_durable_upload_file(pending, new_bytes)
    parent = target.parent.stat()
    payload = {
        "schema_version": 4,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "frame_id": frame_id,
        "previous_version_id": prior.get("latest_version_id") if prior else None,
        "previous_updated_at": prior.get("updated_at") if prior else None,
        "previous_filename": prior.get("filename") if prior else None,
        "previous_content_type": prior.get("content_type") if prior else None,
        "target": str(target),
        "staged": str(staged),
        "pending": str(pending),
        "final": str(final),
        "backup": str(backup),
        "target_parent_dev": int(parent.st_dev),
        "target_parent_ino": int(parent.st_ino),
        "published_dev": int(staged.stat().st_dev),
        "published_ino": int(staged.stat().st_ino),
        "final_dev": int(pending.stat().st_dev),
        "final_ino": int(pending.stat().st_ino),
        **runner.artifacts._describe_upload_live(target),
        "size_bytes": len(new_bytes),
        "checksum": hashlib.sha256(new_bytes).hexdigest(),
    }
    runner.artifacts._write_upload_journal(journal, payload)
    if crash_point in {"snapshot-published", "live-published"}:
        artifacts_mod.os.replace(pending, final)
    if crash_point == "live-published":
        if payload["had_live"]:
            artifacts_mod.os.replace(target, backup)
        artifacts_mod.os.replace(staged, target)

    _reopen_artifacts(runner)
    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before
    assert (target.read_bytes() if target.exists() else None) == before_live
    assert set(runner.artifacts.versions_dir().iterdir()) == before_snapshots
    assert not journal.exists()
    # Recovery is idempotent after it consumes the journal.
    _reopen_artifacts(runner)
    assert runner.store.list_artifacts({"root_frame_id": frame_id}) == before


@pytest.mark.parametrize("existing", [False, True], ids=["first", "new-version"])
def test_startup_finishes_committed_upload_journal_cleanup(
    runner, monkeypatch, existing
):
    """A crash after commit keeps the verified new truth and cleans idempotently."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    if existing:
        runner.artifacts.upload(_payload(frame_id, b"old bytes\n"))
    real_unlink = Path.unlink

    def leave_journal(self, *args, **kwargs):
        if self.name.startswith(".upload-v-") and self.name.endswith(".json"):
            raise OSError("process stopped before journal cleanup")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", leave_journal)
    saved = runner.artifacts.upload(_payload(frame_id, b"committed bytes\n"))
    monkeypatch.undo()
    journals = list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    assert len(journals) == 1
    committed = copy.deepcopy(runner.store.get_artifact(saved["artifact_id"]))

    _reopen_artifacts(runner)
    assert runner.store.get_artifact(saved["artifact_id"]) == committed
    assert (runner.workspace_for(frame_id) / "table.tsv").read_bytes() == (
        b"committed bytes\n"
    )
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    _reopen_artifacts(runner)
    assert runner.store.get_artifact(saved["artifact_id"]) == committed


def test_recovery_rolls_back_same_byte_final_snapshot_inode_swap(runner, monkeypatch):
    """Journal identity binds committed immutable bytes, not only their hash."""

    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    real_unlink = Path.unlink

    def leave_journal(self, *args, **kwargs):
        if self.name.startswith(".upload-v-") and self.name.endswith(".json"):
            raise OSError("simulated process stop")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", leave_journal)
    saved = runner.artifacts.upload(_payload(frame_id, b"committed bytes\n"))
    monkeypatch.undo()
    journal = next(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    payload = json.loads(journal.read_text("utf-8"))
    final = Path(payload["final"])
    replacement = final.with_name(final.name + ".replacement")
    replacement.write_bytes(final.read_bytes())
    artifacts_mod.os.replace(replacement, final)

    _reopen_artifacts(runner)
    assert runner.store.get_artifact(saved["artifact_id"]) is None
    assert not (runner.workspace_for(frame_id) / "table.tsv").exists()
    assert not journal.exists()


def test_upload_keyboard_interrupt_closes_the_transaction(runner, monkeypatch):
    """BaseException gets the same exact rollback before it propagates."""

    runner.store.create_project(name="Science", project_id="proj_science")
    frame_id = runner.store.new_frame(
        kind="turn", project_id="proj_science", status="ready"
    )
    saved = runner.artifacts.upload(_payload(frame_id, b"old bytes\n"))
    target = runner.workspace_for(frame_id) / "table.tsv"
    before = copy.deepcopy(runner.store.get_artifact(saved["artifact_id"]))

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.artifacts, "_promote_version_stage", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.artifacts.upload(_payload(frame_id, b"new bytes\n"))
    monkeypatch.undo()

    assert runner.store.get_artifact(saved["artifact_id"]) == before
    assert target.read_bytes() == b"old bytes\n"
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    _reopen_artifacts(runner)
    assert runner.store.get_artifact(saved["artifact_id"]) == before


def test_symlinked_upload_journal_fails_closed_without_following_it(runner, tmp_path):
    outside = tmp_path / "outside-journal.json"
    outside.write_text("{}", encoding="utf-8")
    journal = runner.artifacts.versions_dir() / ".upload-v-malicious.json"
    journal.symlink_to(outside)

    with pytest.raises(
        RuntimeError, match="artifact upload recovery could not be verified"
    ):
        _reopen_artifacts(runner)

    assert journal.is_symlink()
    assert outside.read_text("utf-8") == "{}"


def test_nested_exact_artifact_recovery_finishes_committed_cleanup(runner, monkeypatch):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    first, target = _nested_text_artifact(runner, frame_id)
    real_unlink = Path.unlink

    def leave_journal(self, *args, **kwargs):
        if self.name.startswith(".upload-v-") and self.name.endswith(".json"):
            raise OSError("process stopped before nested journal cleanup")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", leave_journal)
    edited = runner.artifacts.edit(first["artifact_id"], "committed bytes\n")
    monkeypatch.undo()
    journals = list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    assert len(journals) == 1
    committed = copy.deepcopy(runner.store.get_artifact(first["artifact_id"]))

    _reopen_artifacts(runner)

    assert runner.store.get_artifact(first["artifact_id"]) == committed
    assert committed["latest_version_id"] == edited["version_id"]
    assert target.read_bytes() == b"committed bytes\n"
    assert not list(runner.artifacts.versions_dir().glob(".upload-v-*.json"))
    assert not list(target.parent.glob("*.part"))
    assert not list(target.parent.glob(".*.backup"))


def test_nested_exact_artifact_recovery_refuses_a_swapped_parent(runner, tmp_path):
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    prior, target = _nested_text_artifact(runner, frame_id)
    prior_head = runner.store.get_artifact(prior["artifact_id"])
    new_bytes = b"uncommitted bytes\n"
    version_id = "v-nestedcrash"
    staged = target.with_name(f"{target.name}.deadbeef.part")
    pending = runner.artifacts.versions_dir() / f".pending-{'b' * 32}__{target.name}"
    final = runner.artifacts.versions_dir() / f"{version_id}__{target.name}"
    backup = target.with_name(f".{target.name}.upload-{version_id}.backup")
    journal = runner.artifacts.versions_dir() / f".upload-{version_id}.json"
    runner.artifacts._write_durable_upload_file(staged, new_bytes)
    runner.artifacts._write_durable_upload_file(pending, new_bytes)
    parent_metadata = target.parent.stat()
    payload = {
        "schema_version": 4,
        "artifact_id": prior["artifact_id"],
        "version_id": version_id,
        "frame_id": frame_id,
        "previous_version_id": prior_head["latest_version_id"],
        "previous_updated_at": prior_head["updated_at"],
        "previous_filename": prior_head["filename"],
        "previous_content_type": prior_head["content_type"],
        "target": str(target),
        "staged": str(staged),
        "pending": str(pending),
        "final": str(final),
        "backup": str(backup),
        "target_parent_dev": int(parent_metadata.st_dev),
        "target_parent_ino": int(parent_metadata.st_ino),
        "published_dev": int(staged.stat().st_dev),
        "published_ino": int(staged.stat().st_ino),
        "final_dev": int(pending.stat().st_dev),
        "final_ino": int(pending.stat().st_ino),
        **runner.artifacts._describe_upload_live(target),
        "size_bytes": len(new_bytes),
        "checksum": hashlib.sha256(new_bytes).hexdigest(),
    }
    runner.artifacts._write_upload_journal(journal, payload)
    parked = target.parent.with_name("parked-results")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.write_bytes(b"OUTSIDE_CANARY\n")
    target.parent.rename(parked)
    try:
        target.parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        parked.rename(target.parent)
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(
        RuntimeError, match="artifact upload recovery could not be verified"
    ):
        _reopen_artifacts(runner)

    assert outside_target.read_bytes() == b"OUTSIDE_CANARY\n"
    assert (parked / target.name).read_bytes() == b"old bytes\n"
    assert (parked / staged.name).read_bytes() == new_bytes
    assert journal.exists()
    assert (
        runner.store.get_artifact(prior["artifact_id"])["latest_version_id"]
        == prior_head["latest_version_id"]
    )
