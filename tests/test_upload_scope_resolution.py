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


def test_a_second_frameless_upload_versions_the_same_exact_scope(runner):
    first = runner.artifacts.upload(_payload(None, b"first\n"))
    second = runner.artifacts.upload(_payload(None, b"second\n"))

    assert second["artifact_id"] == first["artifact_id"]
    versions = runner.store.list_versions(first["artifact_id"])
    assert len(versions) == 2
    assert runner.store.get_artifact(first["artifact_id"])["checksum"] == (
        hashlib.sha256(b"second\n").hexdigest()
    )


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

    monkeypatch.setattr(runner.artifacts, "stage_version_bytes", refuse)

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
            "promote_version_bytes",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("promotion fault")),
        )
    else:
        real_replace = artifacts_mod.os.replace

        def fail_live_replace(source, destination):
            source_path = Path(source)
            destination_path = Path(destination)
            if destination_path == target and source_path.name.endswith(".part"):
                raise OSError("live replace fault")
            return real_replace(source, destination)

        monkeypatch.setattr(artifacts_mod.os, "replace", fail_live_replace)

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
    payload = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "version_id": version_id,
        "frame_id": frame_id,
        "previous_version_id": prior.get("latest_version_id") if prior else None,
        "previous_updated_at": prior.get("updated_at") if prior else None,
        "target": str(target),
        "staged": str(staged),
        "pending": str(pending),
        "final": str(final),
        "backup": str(backup),
        **runner.artifacts._describe_upload_live(target),
        "size_bytes": len(new_bytes),
        "checksum": hashlib.sha256(new_bytes).hexdigest(),
    }
    runner.artifacts._write_upload_journal(journal, payload)
    if crash_point in {"snapshot-published", "live-published"}:
        runner.artifacts.promote_version_bytes(version_id, target.name, pending)
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

    monkeypatch.setattr(runner.artifacts, "promote_version_bytes", interrupt)
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
