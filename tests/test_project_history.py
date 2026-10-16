"""Portable project history is bounded, inert, immutable and retained on delete."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from openai4s import project_history as history
from openai4s.config import Config
from openai4s.project_history import ProjectHistoryService
from openai4s.server.session_domain import SessionDomainService
from openai4s.store import get_store


def _setup(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    cfg = Config(data_dir=tmp_path / "daemon")
    store = get_store(cfg.db_path)
    project = store.create_project(
        name="Research",
        description="Data",
        context="Keep units",
        folder_path=str(source.resolve()),
    )
    pid = project["project_id"]
    sid = store.new_frame(
        project_id=pid, kind="turn", name="An experiment", model="model-example"
    )
    workspace = cfg.data_dir / "agent-workspaces" / sid
    workspace.mkdir(parents=True)
    store.add_message(root_frame_id=sid, role="user", content="Analyze the data")
    store.add_message(root_frame_id=sid, role="assistant", content="The mean is 4.")
    store.log_cell(
        frame_id=sid,
        root_frame_id=sid,
        project_id=pid,
        code="print(4)",
        result={"id": "cell-example", "stdout": "4\n", "stderr": ""},
    )
    (workspace / "result.csv").write_text("value\n3\n5\n")
    return source, cfg, store, pid, sid, workspace, ProjectHistoryService(store, cfg)


def test_archive_records_real_text_code_files_settings_and_secure_modes(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    store.set_setting("llm_api_key", "not-an-exportable-value")
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    snapshot = service.read_session(pid, sid)
    assert snapshot["state"] == "ready", snapshot
    assert [row["content"] for row in snapshot["messages"]] == [
        "Analyze the data",
        "The mean is 4.",
    ]
    assert snapshot["cells"][0]["code"] == "print(4)"
    assert snapshot["cells"][0]["stdout"] == "4\n"
    assert snapshot["settings"]["project"]["context"] == "Keep units"
    assert snapshot["settings"]["session"]["model"] == "model-example"
    assert snapshot["read_only"] is snapshot["untrusted"] is True
    assert "llm_api_key" not in json.dumps(snapshot)
    archived = service.read_file(
        pid, sid, result["revision_id"], "workspace/result.csv"
    )
    assert archived["data"] == (workspace / "result.csv").read_bytes()
    assert (
        snapshot["files"][0]["sha256"] == hashlib.sha256(archived["data"]).hexdigest()
    )
    assert (source / ".openai4s" / ".gitignore").read_text() == "*\n"
    assert stat.S_IMODE((source / ".openai4s").stat().st_mode) == 0o700
    assert stat.S_IMODE((source / ".openai4s/settings.json").stat().st_mode) == 0o600
    assert service.status(pid)["last_saved_at"] == result["last_saved_at"]
    assert service.list_sessions(pid)["sessions"][0]["message_count"] == 2


def test_changed_files_and_messages_create_revisions_with_shared_objects(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    first = service.sync_session(sid)
    unchanged = service.sync_session(sid)
    assert unchanged["state"] == "unchanged", unchanged
    assert unchanged["revision_id"] == first["revision_id"]
    store.add_message(root_frame_id=sid, role="user", content="Repeat")
    second = service.sync_session(sid)
    assert second["revision_id"] != first["revision_id"]

    def file_objects(saved):
        files = service.read_session(pid, sid, saved["revision_id"])["files"]
        return {row["sha256"] for row in files}

    assert file_objects(second) == file_objects(first)
    (workspace / "result.csv").write_text("value\n9\n")
    third = service.sync_session(sid)
    assert third["revision_id"] != second["revision_id"]
    assert len(file_objects(first) | file_objects(third)) == 2
    old = service.read_file(pid, sid, first["revision_id"], "workspace/result.csv")
    new = service.read_file(pid, sid, third["revision_id"], "workspace/result.csv")
    assert old["data"] == b"value\n3\n5\n" and new["data"] == b"value\n9\n"
    assert len(service.read_session(pid, sid)["revisions"]) == 3


def _archive_files(source):
    return {
        str(path.relative_to(source)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (source / ".openai4s").rglob("*")
        if path.is_file()
    }


def test_unchanged_session_writes_nothing_even_when_its_frame_moves(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    first = service.sync_session(sid)
    assert first["state"] == "saved", first
    before = _archive_files(source)
    stamp = store.get_frame(sid)["updated_at"]
    time.sleep(0.005)
    # A status change bumps the frame's updated_at; nothing archived moved.
    store.update_frame(sid, status="done")
    assert store.get_frame(sid)["updated_at"] != stamp
    again = service.sync_session(sid)
    assert again["state"] == "unchanged", again
    assert again["revision_id"] == first["revision_id"]
    assert _archive_files(source) == before
    assert len(service.read_session(pid, sid)["revisions"]) == 1


def test_long_session_archive_grows_linearly(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    session = source / ".openai4s/sessions" / sid

    def archived():
        return sum(
            path.stat().st_size
            for path in (source / ".openai4s").rglob("*")
            if path.is_file()
        )

    sizes = []
    for turn in range(80):
        store.add_message(
            root_frame_id=sid, role="user", content=f"Question {turn}: " + "q" * 300
        )
        store.add_message(
            root_frame_id=sid, role="assistant", content=f"Answer {turn}: " + "a" * 900
        )
        store.log_cell(
            frame_id=sid,
            root_frame_id=sid,
            project_id=pid,
            code=f"value = {turn}",
            result={"id": f"turn-{turn}", "stdout": "o" * 300 + "\n", "stderr": ""},
        )
        assert service.sync_session(sid)["state"] == "saved"
        sizes.append(archived())
    first, last = sizes[19] - sizes[0], sizes[79] - sizes[60]
    conversation = sum(
        (session / name).stat().st_size for name in ("messages.jsonl", "cells.jsonl")
    )
    # A full copy per revision made the last quarter of this session grow
    # about six times faster than the first, to ~70x the conversation; shared
    # chunks keep each quarter's growth about even.
    assert last < 2.5 * first, (first, last)
    assert sizes[-1] < 12 * conversation, (sizes[-1], conversation)
    snapshot = service.read_session(pid, sid)
    assert len(snapshot["messages"]) == 162 and len(snapshot["cells"]) == 81
    assert len(snapshot["revisions"]) == 80


def test_reads_do_not_wait_behind_a_running_save(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    saved = service.sync_session(sid)
    started, release = threading.Event(), threading.Event()
    original = service._snapshot

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_snapshot", slow)
    store.add_message(root_frame_id=sid, role="user", content="slow")
    saver = threading.Thread(target=service.sync_session, args=(sid,))
    saver.start()
    try:
        assert started.wait(5)
        began = time.monotonic()
        assert service.read_session(pid, sid)["revision_id"] == saved["revision_id"]
        assert service.list_sessions(pid)["sessions"][0]["session_id"] == sid
        assert service.status(pid)["enabled"] is True
        assert time.monotonic() - began < 2
    finally:
        release.set()
        saver.join(10)
    assert service.read_session(pid, sid)["messages"][-1]["content"] == "slow"


def test_clean_database_rebind_reads_archive_after_live_project_is_deleted(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"
    store.delete_project(pid)
    assert (source / ".openai4s/sessions" / sid).is_dir()
    store.close()
    other_cfg = Config(data_dir=tmp_path / "clean-daemon")
    clean = get_store(other_cfg.db_path)
    other_pid = clean.create_project(name="Rebound", folder_path=str(source.resolve()))[
        "project_id"
    ]
    reopened = ProjectHistoryService(clean, other_cfg)
    assert reopened.list_sessions(other_pid)["sessions"][0]["session_id"] == sid
    snapshot = reopened.read_session(other_pid, sid)
    assert snapshot["state"] == "ready" and snapshot["untrusted"] is True
    assert clean.get_frame(sid) is None
    assert (
        reopened.read_file(other_pid, sid, None, "workspace/result.csv")["data"]
        == b"value\n3\n5\n"
    )


@pytest.mark.parametrize(
    "target", [".openai4s", ".openai4s/objects", ".openai4s/sessions"]
)
def test_metadata_directory_symlinks_never_write_outside_project(tmp_path, target):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = source / target
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    result = service.sync_session(sid)
    assert result["state"] == "error", result
    assert list(outside.iterdir()) == []


def test_tampering_and_hardlinks_are_refused_without_replacing_old_revision(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    saved = service.sync_session(sid)
    revision = saved["revision_id"]
    digest = service.read_session(pid, sid)["files"][0]["sha256"]
    object_file = source / ".openai4s/objects" / digest
    object_file.write_text("tampered")
    assert (
        service.read_file(pid, sid, revision, "workspace/result.csv")["state"]
        == "error"
    )
    assert service.sync_session(sid)["state"] == "error"
    object_file.unlink()
    outside = tmp_path / "hardlinked"
    outside.write_text("preserve")
    os.link(outside, object_file)
    assert service.sync_session(sid)["state"] == "error"
    assert outside.read_text() == "preserve"
    manifest = (
        source / ".openai4s/sessions" / sid / "revisions" / revision / "manifest.json"
    )
    manifest.write_text("{}")
    assert service.read_session(pid, sid)["status"] == 409


def test_secret_links_special_files_and_large_files_have_explicit_omissions(
    tmp_path, monkeypatch
):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    (workspace / ".env").write_text("do-not-archive")
    (workspace / "outside-link").symlink_to(tmp_path / "private")
    outside = tmp_path / "private"
    outside.write_text("private")
    os.link(outside, workspace / "hardlink")
    os.mkfifo(workspace / "pipe")
    (workspace / "big.bin").write_bytes(b"a" * 128)
    monkeypatch.setattr(history, "MAX_FILE_BYTES", 64)
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    snapshot = service.read_session(pid, sid)
    assert [row["path"] for row in snapshot["files"]] == ["workspace/result.csv"]
    assert len(snapshot["omissions"]) == 5
    assert "secret_path" in {item["reason"] for item in snapshot["omissions"]}


def test_artifact_versions_are_preserved_beside_unregistered_workspace_files(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    for index, content in enumerate((b"first", b"second")):
        snapshot = cfg.data_dir / "artifact-versions" / f"saved-{index}.txt"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(content)
        store.record_cell_artifact(
            path=str(workspace / "report.txt"),
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            producing_cell_id=f"cell-{index}",
            frame_id=sid,
            root_frame_id=sid,
            project_id=pid,
            snapshot_path=str(snapshot),
        )
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    files = service.read_session(pid, sid)["files"]
    artifacts = [row for row in files if row["kind"] == "artifact"]
    assert len(artifacts) == 2
    assert {
        service.read_file(pid, sid, None, row["path"])["data"] for row in artifacts
    } == {b"first", b"second"}


def test_read_errors_do_not_change_successful_save_status_and_io_failure_does(
    tmp_path, monkeypatch
):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    saved = service.sync_session(sid)
    assert service.read_session(pid, "../escape")["status"] == 400
    assert service.read_session(pid, sid, "0" * 64)["status"] == 404
    assert service.status(pid)["state"] == "saved"
    original = history._Tree.write

    def fail_index(self, path, data, **kwargs):
        if path.endswith("/index.json"):
            raise OSError("simulated disk failure")
        return original(self, path, data, **kwargs)

    monkeypatch.setattr(history._Tree, "write", fail_index)
    store.add_message(root_frame_id=sid, role="user", content="New")
    assert service.sync_session(sid)["state"] == "error"
    assert service.status(pid)["state"] == "error"
    assert service.read_session(pid, sid)["revision_id"] == saved["revision_id"]
    monkeypatch.setattr(history._Tree, "write", original)
    assert service.sync_session(sid)["state"] == "saved"


def test_unbound_remote_projects_write_nothing_and_manual_project_save_includes_sessions(
    tmp_path,
):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    cfg.team_mode = True
    assert service.sync_session(sid)["state"] == "skipped"
    assert not (source / ".openai4s").exists()
    cfg.team_mode = False
    store.update_project(pid, folder_path=None)
    assert service.sync_project(pid)["state"] == "skipped"
    assert not (source / ".openai4s").exists()
    store.update_project(pid, folder_path=str(source.resolve()))
    assert service.sync_project(pid, include_sessions=True)["state"] == "saved"
    assert service.read_session(pid, sid)["state"] == "ready"


def _fork(store, cfg, sid, workspace):
    def selected_workspace(_root, selected):
        path = workspace if selected == sid else cfg.data_dir / "branches" / selected
        path.mkdir(parents=True, exist_ok=True)
        return path

    domain = SessionDomainService(
        store, data_dir=cfg.data_dir, workspace=selected_workspace
    )
    checkpoint = domain.create_checkpoint(sid, reason="branch base")
    branch = domain.fork_branch(sid, from_checkpoint_id=checkpoint["checkpoint_id"])
    return domain, checkpoint, branch


def test_branch_history_preserves_shared_prefix_without_mixing_sibling_tails(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    domain, checkpoint, branch = _fork(store, cfg, sid, workspace)
    child = branch["branch_id"]
    store.add_message(
        root_frame_id=sid, branch_id=sid, role="user", content="root only"
    )
    store.log_cell(
        frame_id=sid,
        root_frame_id=sid,
        project_id=pid,
        code="root_only = 2",
        result={"id": "root-tail", "stdout": "", "stderr": ""},
    )
    store.add_message(
        root_frame_id=sid, branch_id=child, role="user", content="child only"
    )
    original = service.sync_session(sid, workspace=workspace, branch_id=sid)
    assert original["state"] == "saved", original
    domain.publish_activation(
        sid,
        branch_id=child,
        checkpoint_id=checkpoint["checkpoint_id"],
        expected_current_branch_id=sid,
    )
    child_workspace = cfg.data_dir / "child-workspace"
    child_workspace.mkdir()
    (child_workspace / "child.txt").write_text("child workspace")
    child_saved = service.sync_session(sid, workspace=child_workspace, branch_id=child)
    assert child_saved["state"] == "saved", child_saved
    snapshot = service.read_session(pid, sid)
    assert snapshot["branch_id"] == child
    assert [row["content"] for row in snapshot["messages"]] == [
        "Analyze the data",
        "The mean is 4.",
        "child only",
    ]
    assert all(row["message_id"] and row["branch_id"] for row in snapshot["messages"])
    assert [row["code"] for row in snapshot["cells"]] == ["print(4)"]
    assert [row["path"] for row in snapshot["files"]] == ["workspace/child.txt"]
    old = service.read_session(pid, sid, original["revision_id"])
    assert old["branch_id"] == sid
    assert old["messages"][-1]["content"] == "root only"
    assert old["cells"][-1]["code"] == "root_only = 2"
    assert {row["branch_id"] for row in snapshot["revisions"]} == {sid, child}


def test_branch_switch_during_snapshot_and_stale_workspace_do_not_publish(
    tmp_path, monkeypatch
):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    domain, checkpoint, branch = _fork(store, cfg, sid, workspace)
    saved = service.sync_session(sid)
    original = service._snapshot

    def switch_branch(*args, **kwargs):
        result = original(*args, **kwargs)
        domain.publish_activation(
            sid,
            branch_id=branch["branch_id"],
            checkpoint_id=checkpoint["checkpoint_id"],
            expected_current_branch_id=sid,
        )
        return result

    monkeypatch.setattr(service, "_snapshot", switch_branch)
    failed = service.sync_session(sid, workspace=workspace, branch_id=sid)
    assert failed["code"] == "project_history_session_changed", failed
    assert service.read_session(pid, sid)["revision_id"] == saved["revision_id"]
    assert (
        service.sync_session(sid, workspace=workspace, branch_id=sid)["code"]
        == "project_history_session_changed"
    )
    assert service.sync_session(sid)["code"] == "project_history_workspace_required"


def test_archive_storage_cap_keeps_prior_revision_readable(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    saved = service.sync_session(sid)
    used = sum(
        path.stat().st_size
        for path in (source / ".openai4s").rglob("*")
        if path.is_file()
    )
    monkeypatch.setattr(history, "MAX_ARCHIVE_BYTES", used + 64)
    (workspace / "result.csv").write_bytes(b"x" * 128)
    result = service.sync_session(sid)
    assert result["state"] == "error" and "storage limit" in result["error"]
    assert service.read_session(pid, sid)["revision_id"] == saved["revision_id"]
    assert (
        service.read_file(pid, sid, None, "workspace/result.csv")["data"]
        == b"value\n3\n5\n"
    )


def test_workspace_scan_bounds_directories_and_records_omission(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    for index in range(50):
        (workspace / f"directory-{index}").mkdir()
    monkeypatch.setattr(history, "MAX_SCAN_ENTRIES", 25)
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    assert {"path": "workspace", "reason": "file_count_limit"} in result["omissions"]


def test_omitted_file_does_not_spend_the_snapshot_budget(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    (workspace / "result.csv").unlink()
    (workspace / "a-large.bin").write_bytes(b"x" * 200)
    (workspace / "b-small.txt").write_text("small\n")
    monkeypatch.setattr(history, "MAX_SESSION_BYTES", 100)
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    assert result["omissions"] == [
        {"path": "workspace/a-large.bin", "reason": "session_size_limit"}
    ]
    files = service.read_session(pid, sid)["files"]
    assert [row["path"] for row in files] == ["workspace/b-small.txt"]


def test_artifact_checksum_mismatch_is_a_named_omission(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    snapshot = cfg.data_dir / "artifact-versions" / "saved.txt"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"changed after capture")
    store.record_cell_artifact(
        path=str(workspace / "report.txt"),
        filename="report.txt",
        content_type="text/plain",
        size_bytes=8,
        checksum=hashlib.sha256(b"captured").hexdigest(),
        producing_cell_id="cell-mismatch",
        frame_id=sid,
        root_frame_id=sid,
        project_id=pid,
        snapshot_path=str(snapshot),
    )
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    reasons = {item["reason"] for item in result["omissions"]}
    assert "artifact_checksum_mismatch" in reasons
    assert "unsafe_missing_or_oversized_file" not in reasons


def test_transient_status_read_error_is_not_remembered(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"
    original = history._Tree.names
    calls = {"count": 0}

    def flaky(self, path, limit=history.MAX_REVISIONS):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("transient")
        return original(self, path, limit)

    monkeypatch.setattr(history._Tree, "names", flaky)
    assert service.status(pid)["state"] == "error"
    recovered = service.status(pid)
    assert recovered["enabled"] is True
    assert recovered["state"] == "saved" and recovered["error"] is None


def test_configured_secret_values_never_reach_the_portable_archive(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    configured = "custom-secret-without-provider-prefix-123456"
    agent_plan = "custom-agent-plan-secret-654321"
    store.set_setting("llm_api_key", configured)
    store.set_setting("agent_plan_key", agent_plan)
    store.update_project(pid, context=f"Use {agent_plan} for the agent plan")
    store.update_frame(sid, name=f"Run with {configured}")
    store.add_message(root_frame_id=sid, role="user", content=f"My key is {configured}")
    store.log_cell(
        frame_id=sid,
        root_frame_id=sid,
        project_id=pid,
        code="print(key)",
        result={"id": "cell-secret", "stdout": f"{agent_plan}\n", "stderr": ""},
    )
    (workspace / "configured.txt").write_text(f"key={configured}\n")
    (workspace / "binary.bin").write_bytes(
        b"\x00\xff" + agent_plan.encode("utf-8") + b"\x00"
    )
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    omitted = {item["path"]: item["reason"] for item in result["omissions"]}
    assert omitted["workspace/configured.txt"] == "credential_content"
    assert omitted["workspace/binary.bin"] == "credential_content"
    snapshot = service.read_session(pid, sid)
    assert "[REDACTED]" in snapshot["messages"][-1]["content"]
    assert "[REDACTED]" in snapshot["title"]
    archived = [
        path.read_bytes()
        for path in (source / ".openai4s").rglob("*")
        if path.is_file()
    ]
    assert archived
    for secret in (configured, agent_plan):
        assert not any(secret.encode("utf-8") in data for data in archived), secret


def test_snapshot_race_with_a_turn_is_not_remembered_as_the_save_state(
    tmp_path, monkeypatch
):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"

    def moved_on(*_args):
        raise history.ProjectHistoryError(
            "session branch or project folder changed; save again",
            409,
            history.CHANGED_CODE,
        )

    monkeypatch.setattr(service, "_check_snapshot_guard", moved_on)
    store.add_message(root_frame_id=sid, role="user", content="mid-turn")
    assert service.sync_session(sid)["code"] == history.CHANGED_CODE
    assert service.status(pid)["state"] == "saved"


def test_unchanged_files_and_archived_objects_are_not_read_again(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"
    original = history._Tree.read
    reads = []

    def counting(self, path, limit=history.MAX_METADATA_BYTES):
        reads.append(path)
        return original(self, path, limit)

    monkeypatch.setattr(history._Tree, "read", counting)
    assert service.sync_session(sid)["state"] in {"saved", "unchanged"}
    assert "result.csv" not in reads
    assert not any(path.startswith(".openai4s/objects/") for path in reads)
    (workspace / "result.csv").write_text("value\n7\n")
    assert service.sync_session(sid)["state"] == "saved"
    assert "result.csv" in reads


def test_revision_picker_does_not_reread_every_manifest(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    for content in ("first", "second", "third"):
        store.add_message(root_frame_id=sid, role="user", content=content)
        assert service.sync_session(sid)["state"] == "saved"
    calls = []
    original = service._read_revision
    monkeypatch.setattr(
        service,
        "_read_revision",
        lambda tree, session, revision: calls.append(revision)
        or original(tree, session, revision),
    )
    snapshot = service.read_session(pid, sid)
    assert len(snapshot["revisions"]) == 3
    assert len(calls) == 1


def test_uncommitted_session_and_stray_entries_do_not_hide_the_archive(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"
    sessions = source / ".openai4s/sessions"
    # A first save that published payloads but never reached index.json, and
    # a Finder file, are not archived sessions.
    (sessions / "interrupted-session" / "revisions" / ("a" * 64)).mkdir(parents=True)
    (sessions / ".DS_Store").write_bytes(b"\0")
    listed = service.list_sessions(pid)
    assert listed["error"] is None, listed
    assert [row["session_id"] for row in listed["sessions"]] == [sid]
    status = service.status(pid)
    assert status["enabled"] is True and status["sessions_count"] == 1


def test_interrupted_revision_without_manifest_is_completed_and_skipped(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    first = service.sync_session(sid)
    assert first["state"] == "saved", first
    store.add_message(root_frame_id=sid, role="user", content="Next")
    second = service.sync_session(sid)
    revision = source / ".openai4s/sessions" / sid / "revisions" / second["revision_id"]
    # Interrupted after the payloads were linked, before the manifest.
    (revision / "manifest.json").unlink()
    assert service.read_session(pid, sid, first["revision_id"])["state"] == "ready"
    retried = service.sync_session(sid)
    assert retried["state"] in {"saved", "unchanged"}, retried
    assert retried["revision_id"] == second["revision_id"]
    snapshot = service.read_session(pid, sid)
    assert snapshot["state"] == "ready", snapshot
    assert len(snapshot["revisions"]) == 2


def test_partial_unlisted_revision_does_not_hide_readable_ones(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    saved = service.sync_session(sid)
    partial = source / ".openai4s/sessions" / sid / "revisions" / ("b" * 64)
    partial.mkdir()
    (partial / "messages.jsonl").write_bytes(b"")
    snapshot = service.read_session(pid, sid)
    assert snapshot["state"] == "ready", snapshot
    assert [row["revision_id"] for row in snapshot["revisions"]] == [
        saved["revision_id"]
    ]


def test_user_edited_gitignore_and_corrupt_index_do_not_block_saving(tmp_path):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    assert service.sync_session(sid)["state"] == "saved"
    (source / ".openai4s/.gitignore").write_text("!sessions/\n")
    (source / ".openai4s/sessions" / sid / "index.json").write_text("{not json")
    store.add_message(root_frame_id=sid, role="user", content="Again")
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    assert (source / ".openai4s/.gitignore").read_text() == "!sessions/\n"
    assert service.list_sessions(pid)["sessions"][0]["message_count"] == 3


def test_artifact_snapshots_under_a_symlinked_data_dir_are_archived(tmp_path):
    real = tmp_path / "real-daemon"
    real.mkdir()
    (tmp_path / "daemon").symlink_to(real, target_is_directory=True)
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    snapshot = cfg.data_dir / "artifact-versions" / "saved.txt"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"captured")
    store.record_cell_artifact(
        path=str(workspace / "report.txt"),
        filename="report.txt",
        content_type="text/plain",
        size_bytes=8,
        checksum=hashlib.sha256(b"captured").hexdigest(),
        producing_cell_id="cell-linked",
        frame_id=sid,
        root_frame_id=sid,
        project_id=pid,
        snapshot_path=str(snapshot),
    )
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    assert not result["omissions"], result["omissions"]
    files = service.read_session(pid, sid)["files"]
    assert [row["kind"] for row in files].count("artifact") == 1


def test_record_cap_keeps_the_newest_conversation(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    store.add_message(root_frame_id=sid, role="user", content="newest question")
    for index in (5, 6):
        store.log_cell(
            frame_id=sid,
            root_frame_id=sid,
            project_id=pid,
            code=f"print({index})",
            result={"id": f"cell-{index}", "stdout": f"{index}\n", "stderr": ""},
        )
    monkeypatch.setattr(history, "MAX_RECORDS", 2)
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    reasons = {(item["path"], item["reason"]) for item in result["omissions"]}
    assert {("messages", "record_count_limit"), ("cells", "record_count_limit")} <= (
        reasons
    )
    snapshot = service.read_session(pid, sid)
    assert [row["content"] for row in snapshot["messages"]] == [
        "The mean is 4.",
        "newest question",
    ]
    assert [row["code"] for row in snapshot["cells"]] == ["print(5)", "print(6)"]


def test_artifact_versions_are_read_without_a_query_per_version(tmp_path, monkeypatch):
    source, cfg, store, pid, sid, workspace, service = _setup(tmp_path)
    for index, content in enumerate((b"first", b"second")):
        snapshot = cfg.data_dir / "artifact-versions" / f"bulk-{index}.txt"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(content)
        store.record_cell_artifact(
            path=str(workspace / "report.txt"),
            filename="report.txt",
            content_type="text/plain",
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            producing_cell_id=f"cell-bulk-{index}",
            frame_id=sid,
            root_frame_id=sid,
            project_id=pid,
            snapshot_path=str(snapshot),
        )

    def per_version(*_args, **_kwargs):
        raise AssertionError("history must not query each Artifact version")

    monkeypatch.setattr(store, "version_meta", per_version)
    monkeypatch.setattr(store, "list_versions", per_version)
    result = service.sync_session(sid)
    assert result["state"] == "saved", result
    files = service.read_session(pid, sid)["files"]
    assert [row["kind"] for row in files].count("artifact") == 2
