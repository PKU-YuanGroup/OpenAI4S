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
import json
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
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

    # Injected at a level both the old and the new order share: any write into
    # the versions directory fails. Patching `stage_version_bytes` would only
    # exist in the new code and could not tell the two apart.
    versions_dir = runner.artifacts.versions_dir().resolve()
    real_write_bytes = Path.write_bytes

    def refuse(self, data, *a, **k):
        try:
            inside = self.resolve().parent == versions_dir
        except OSError:  # pragma: no cover - parent always resolvable here
            inside = False
        if inside:
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data, *a, **k)

    monkeypatch.setattr(Path, "write_bytes", refuse)

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
    real_save = runner.store.save_artifact

    def explode(**kwargs):
        del kwargs
        raise RuntimeError("database is locked")

    runner.store.save_artifact = explode
    try:
        with pytest.raises(RuntimeError):
            runner.artifacts.upload(_payload(frame_id, b"x\n"))
    finally:
        runner.store.save_artifact = real_save

    assert not list(runner.artifacts.versions_dir().glob(".pending-*"))
    assert not list(runner.workspace_for(frame_id).glob("*.part"))
