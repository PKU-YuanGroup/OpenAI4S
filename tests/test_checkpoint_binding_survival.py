"""What a checkpoint restore quietly threw away.

Migration 12 bound an image annotation to `version_id` + `checksum` because an
annotation naming only the artifact lets a re-plot between the pin and the send
hand the model a different picture while the pin's coordinates still describe
the old one. The checkpoint capture reads the row with `SELECT *`, so both
columns were captured -- and the restore wrote an explicit column list that
did not include them. Every restore therefore unbound every pin in the session
and put the "resolve to latest" behaviour back, on a path whose entire promise
is that it puts the session back the way it was.

The memory rows had the same shape once `updated_at` existed: captured by name
without it, restored without it, so a corrected memory came back expiring on
the clock of the instruction it replaced.

Both are asserted on the columns in the database after a real restore, not on
the snapshot dict -- the snapshot was already right, which is exactly why the
loss was invisible.
"""

from __future__ import annotations

import hashlib

import pytest

from openai4s.server.session_domain import SessionDomainService
from openai4s.store import Store


@pytest.fixture
def session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    made = Store(tmp_path / "openai4s.db")
    made.create_project(name="Bindings", project_id="science")
    root = made.new_frame(project_id="science", kind="turn", status="ready")
    service = SessionDomainService(
        made, data_dir=tmp_path, workspace=lambda _root, _branch: workspace
    )
    yield made, root, service, workspace
    made.close()


def _pinned_figure(store, root, workspace):
    path = workspace / "figure.png"
    path.write_bytes(b"\x89PNG figure")
    return store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="image/png",
        size_bytes=path.stat().st_size,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        frame_id=root,
        root_frame_id=root,
        project_id="science",
    )


def _annotation_row(store, annotation_id):
    return store._conn.execute(
        "SELECT version_id, checksum FROM annotations WHERE annotation_id=?",
        (annotation_id,),
    ).fetchone()


def test_a_restored_annotation_still_names_the_version_it_was_pinned_on(session):
    store, frame_id, service, workspace = session
    artifact = _pinned_figure(store, frame_id, workspace)
    version_id = str(artifact["version_id"])
    checksum = str(artifact["checksum"])
    annotation = store.add_annotation(
        root_frame_id=frame_id,
        artifact_id=artifact["artifact_id"],
        artifact_name="figure.png",
        rel_x=0.5,
        rel_y=0.5,
        body="the peak here",
        version_id=version_id,
        checksum=checksum,
    )
    before = _annotation_row(store, annotation["annotation_id"])
    assert before["version_id"] == version_id, "the fixture never bound the pin"

    checkpoint = service.create_checkpoint(frame_id, reason="bindings")
    # Unbind it the way a re-plot would, then put the session back.
    store._conn.execute(
        "UPDATE annotations SET version_id=NULL, checksum=NULL WHERE annotation_id=?",
        (annotation["annotation_id"],),
    )
    store._conn.commit()

    report = store.restore_checkpoint_state_snapshot(
        checkpoint_id=checkpoint["checkpoint_id"],
        root_frame_id=frame_id,
        project_id="science",
    )

    assert report["applied"] is True, report
    after = _annotation_row(store, annotation["annotation_id"])
    assert after["version_id"] == version_id, (
        "the restore unbound the pin; the send path will resolve it to whatever "
        "version is latest at the time"
    )
    assert after["checksum"] == checksum


def test_a_restored_memory_keeps_the_time_it_was_last_corrected(session):
    store, frame_id, service, workspace = session
    _pinned_figure(store, frame_id, workspace)
    saved = store.add_memory(content="use the old protocol", project_id="science")
    edited = store.update_memory(
        saved["memory_id"], content="use the 2026 protocol", project_id="science"
    )
    assert edited["updated_at"], "the fixture never recorded an edit"

    checkpoint = service.create_checkpoint(frame_id, reason="bindings")
    store._conn.execute("UPDATE memories SET updated_at=NULL")
    store._conn.commit()

    store.restore_checkpoint_state_snapshot(
        checkpoint_id=checkpoint["checkpoint_id"],
        root_frame_id=frame_id,
        project_id="science",
    )

    row = store._conn.execute(
        "SELECT created_at, updated_at FROM memories WHERE memory_id=?",
        (saved["memory_id"],),
    ).fetchone()
    assert row["updated_at"] == edited["updated_at"], (
        "the restore dropped the edit time; retention will expire the "
        "correction on the clock of the thing it replaced"
    )
    assert row["created_at"] == saved["created_at"]


def test_the_snapshot_itself_carries_both_bindings(session):
    """The capture was already right, which is why the loss was invisible."""
    store, frame_id, service, workspace = session
    artifact = _pinned_figure(store, frame_id, workspace)
    store.add_annotation(
        root_frame_id=frame_id,
        artifact_id=artifact["artifact_id"],
        artifact_name="figure.png",
        rel_x=0.1,
        rel_y=0.2,
        body="here",
        version_id=str(artifact["version_id"]),
        checksum=str(artifact["checksum"]),
    )
    saved = store.add_memory(content="remember this", project_id="science")
    store.update_memory(
        saved["memory_id"], content="remember this, v2", project_id="science"
    )

    checkpoint = service.create_checkpoint(frame_id, reason="bindings")
    snapshot = store.get_checkpoint_state_snapshot(
        checkpoint["checkpoint_id"], include_state=True
    )

    state = snapshot["state"]
    assert state["review"]["annotations"][0]["version_id"] == str(
        artifact["version_id"]
    )
    assert state["review"]["annotations"][0]["checksum"] == str(artifact["checksum"])
    assert state["memory"]["entries"][0]["updated_at"] is not None
