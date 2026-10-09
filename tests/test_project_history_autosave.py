"""Autosave runs independently of viewers and preserves archives before deletion."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.server.gateway import GatewayError, SessionRunner, WSHub
from openai4s.server.project_history_autosave import ProjectHistoryAutosave


class MemoryStore:
    def __init__(self, attached=True):
        self.project = {
            "project_id": "project",
            "folder_path": "/source" if attached else None,
        }
        self.frames = {
            "one": {"frame_id": "one", "project_id": "project"},
            "two": {"frame_id": "two", "project_id": "project"},
        }
        self.settings = {}

    def list_projects(self):
        return [self.project]

    def get_project(self, _pid):
        return self.project

    def project_session_ids(self, _pid):
        return list(self.frames)

    def get_frame(self, root):
        return self.frames.get(root)

    def active_session_branch(self, root):
        return root

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_setting(self, key):
        return self.settings.get(key)


class RecordingService:
    def __init__(self):
        self.calls = []
        self.projects = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_once = False
        self.fail = False

    def sync_project(self, pid):
        self.projects.append(pid)
        return {"state": "saved"}

    def sync_session(self, root, *, workspace=None, branch_id=None):
        self.calls.append((root, workspace))
        self.started.set()
        if self.block_once:
            self.block_once = False
            assert self.release.wait(3)
        if self.fail:
            raise OSError("private path must not leak")
        return {"state": "saved"}

    def status(self, _pid, **_kwargs):
        return {"enabled": True, "state": "saved", "last_saved_at": 0}


def fake_autosave(*, debounce=0.0, attached=True):
    store, service = MemoryStore(attached), RecordingService()
    autosave = ProjectHistoryAutosave(
        service,
        store,
        workspace_for=lambda root: Path("/workspace") / root,
        debounce_seconds=debounce,
    )
    return autosave, service, store


@pytest.mark.stubbed_backend
def test_debounces_each_project_and_never_saves_text_tokens():
    autosave, service, _store = fake_autosave(debounce=5)
    try:
        for _ in range(100):
            autosave.observe("one", {"type": "text_chunk", "chunk": "token"})
        assert autosave._thread is None
        for _ in range(100):
            autosave.observe("one", {"type": "artifact_created"})
        assert autosave.flush_session("one")["state"] == "saved"
        assert service.projects == ["project"]
        assert service.calls == [("one", Path("/workspace/one"))]
    finally:
        autosave.close()


@pytest.mark.stubbed_backend
def test_dirty_update_during_save_gets_a_later_generation_and_no_parallel_writer():
    autosave, service, _store = fake_autosave()
    service.block_once = True
    try:
        autosave.schedule_session("one")
        assert service.started.wait(2)
        autosave.schedule_session("one")
        service.release.set()
        assert autosave.flush_session("one")["state"] == "saved"
        assert len(service.calls) == 2
    finally:
        service.release.set()
        autosave.close()


@pytest.mark.stubbed_backend
def test_unexpected_failure_is_visible_and_can_be_retried_or_cleared():
    autosave, service, store = fake_autosave()
    try:
        service.fail = True
        assert autosave.flush_session("one")["state"] == "error"
        status = autosave.status("project")
        assert status["state"] == "error"
        assert "private path" not in status["error"]
        assert store.settings
        service.fail = False
        autosave.clear_error("project")
        assert autosave.status("project")["state"] == "saved"
        assert autosave.flush_session("one")["state"] == "saved"
    finally:
        service.fail = False
        autosave.close()


@pytest.mark.stubbed_backend
def test_failed_session_stays_failed_after_other_session_settings_and_restart():
    autosave, service, store = fake_autosave()
    try:
        service.fail = True
        assert autosave.flush_session("one")["state"] == "error"
        service.fail = False
        assert autosave.flush_session("two")["state"] == "saved"
        assert autosave.flush_project_settings("project")["state"] == "saved"
        # A newer disk timestamp belongs to the successful component only.
        service.status = lambda pid, **_kwargs: {
            "enabled": True,
            "state": "saved",
            "last_saved_at": 10**20,
        }
        assert autosave.status("project")["state"] == "error"
        # A freshly composed writer must preserve unresolved failures even
        # when the old writer no longer has their in-memory state.
        reopened = ProjectHistoryAutosave(
            service, store, workspace_for=lambda root: Path("/workspace") / root
        )
        assert reopened.status("project")["state"] == "error"
        assert reopened.flush_session("two")["state"] == "saved"
        assert reopened.status("project")["state"] == "error"
        assert reopened.flush_session("one")["state"] == "saved"
        assert reopened.status("project")["state"] == "saved"
        reopened.close()
    finally:
        service.fail = False
        autosave.close()


@pytest.mark.stubbed_backend
def test_sibling_failure_does_not_become_another_sessions_result():
    autosave, service, _store = fake_autosave(debounce=0.2)
    original = service.sync_session

    def fail_two(root, *, workspace=None, branch_id=None):
        if root == "two":
            return {"state": "error", "error": "session two failed"}
        return original(root, workspace=workspace, branch_id=branch_id)

    service.sync_session = fail_two
    try:
        # Both roots land in one debounced batch, as when a turn in `two`
        # ends just before `one` is deleted.
        autosave.schedule_session("two")
        assert autosave.flush_session("one")["state"] == "saved"
        assert autosave.status("project")["state"] == "error"
    finally:
        autosave.close()


@pytest.mark.stubbed_backend
def test_startup_and_shutdown_sweep_all_sessions_with_actual_workspace():
    autosave, service, _store = fake_autosave()
    autosave.schedule_all()
    assert autosave.flush_project("project")["state"] == "saved"
    assert {root for root, _workspace in service.calls} == {"one", "two"}
    service.calls.clear()
    autosave.close()
    assert {root for root, _workspace in service.calls} == {"one", "two"}


@pytest.mark.stubbed_backend
def test_without_attached_folder_no_writer_thread_starts():
    autosave, service, _store = fake_autosave(attached=False)
    autosave.schedule_all()
    autosave.schedule_session("one")
    autosave.close()
    assert autosave._thread is None
    assert service.calls == []


@pytest.mark.stubbed_backend
def test_archive_placement_pins_branch_of_live_workspace_during_activation():
    runner = object.__new__(SessionRunner)
    old = SimpleNamespace(workspace=Path("/live/old"), branch_id="old")
    runner._existing_state = lambda root: old
    runner.store = MemoryStore()
    runner.store.active_session_branch = lambda root: "new"
    # The DB pointer may already name the candidate while the old runtime is
    # still installed. The archive service must see old and reject the drift.
    assert runner._project_history_placement("one") == (Path("/live/old"), "old")


def runner_with_project(tmp_path):
    cfg = Config(data_dir=tmp_path / "state")
    hub = WSHub()
    runner = SessionRunner(cfg, hub, start_idle_sweeper=False)
    folder = tmp_path / "research"
    folder.mkdir()
    pid = runner.store.create_project(name="Research", folder_path=str(folder))[
        "project_id"
    ]
    root = runner.create_session(pid)
    assert runner.project_history_autosave.flush_session(root)["state"] in {
        "saved",
        "unchanged",
    }
    return runner, hub, folder, pid, root


def wait_for_message(runner, pid, root, content):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        saved = runner.project_history.read_session(pid, root)
        if any(row.get("content") == content for row in saved.get("messages", [])):
            return saved
        time.sleep(0.01)
    raise AssertionError("durable mutation was not automatically archived")


def test_server_event_saves_without_browser_and_execution_exit_saves_without_event(
    tmp_path,
):
    runner, hub, _folder, pid, root = runner_with_project(tmp_path)
    try:
        runner.store.add_message(
            root_frame_id=root, role="user", content="no browser connected"
        )
        hub.broadcast(
            root, {"type": "frame_update", "frame_id": root, "status": "done"}
        )
        wait_for_message(runner, pid, root, "no browser connected")
        state = runner._state(root, pid)
        with runner._session_execution(
            state, owner="user", owner_id="test-edit", reason="edit"
        ):
            runner.store.add_message(
                root_frame_id=root,
                role="assistant",
                content="durable execution completed",
            )
        wait_for_message(runner, pid, root, "durable execution completed")
    finally:
        runner.close()


def test_delete_session_archives_last_bytes_before_private_workspace_cleanup(tmp_path):
    runner, _hub, folder, pid, root = runner_with_project(tmp_path)
    workspace = runner.active_workspace_for(root)
    (workspace / "result.txt").write_text("final output")
    runner.store.add_message(root_frame_id=root, role="assistant", content="last reply")
    try:
        runner.delete_session(root)
        assert runner.store.get_frame(root) is None
        assert not workspace.exists()
        archived = runner.project_history.read_session(pid, root)
        assert any(row["content"] == "last reply" for row in archived["messages"])
        assert (folder / ".openai4s" / "sessions" / root / "index.json").is_file()
    finally:
        runner.close()


def test_failed_delete_retains_all_project_sessions_and_workspace(tmp_path):
    runner, _hub, folder, pid, root = runner_with_project(tmp_path)
    other = runner.create_session(pid)
    workspace = runner.active_workspace_for(root)
    (workspace / "result.txt").write_text("keep")
    moved = tmp_path / "disconnected"
    folder.rename(moved)
    try:
        with pytest.raises(GatewayError, match="retained"):
            runner.delete_project(pid)
        assert runner.store.get_frame(root) is not None
        assert runner.store.get_frame(other) is not None
        assert (workspace / "result.txt").read_text() == "keep"
        assert not folder.exists()
    finally:
        moved.rename(folder)
        runner.close()


def test_shutdown_saves_existing_conversation_and_restart_catches_unsaved_rows(
    tmp_path,
):
    runner, _hub, _folder, pid, root = runner_with_project(tmp_path)
    runner.store.add_message(
        root_frame_id=root, role="user", content="saved at shutdown"
    )
    runner.close()
    assert any(
        row["content"] == "saved at shutdown"
        for row in runner.project_history.read_session(pid, root)["messages"]
    )
    runner.store.add_message(
        root_frame_id=root, role="user", content="recovered after restart"
    )
    reopened = SessionRunner(runner.cfg, WSHub())
    try:
        wait_for_message(reopened, pid, root, "recovered after restart")
    finally:
        reopened.close()


def test_unsaveable_folder_refuses_delete_before_dropping_the_runtime(
    tmp_path, monkeypatch
):
    runner, _hub, folder, _pid, root = runner_with_project(tmp_path)
    dropped = []
    original = runner.drop_session
    monkeypatch.setattr(
        runner,
        "drop_session",
        lambda root_frame_id, **kwargs: dropped.append(root_frame_id)
        or original(root_frame_id, **kwargs),
    )
    moved = tmp_path / "disconnected"
    folder.rename(moved)
    try:
        with pytest.raises(GatewayError, match="runtime were retained"):
            runner.delete_session(root)
        assert dropped == []
        assert runner.store.get_frame(root) is not None
    finally:
        moved.rename(folder)
        runner.close()


def test_archive_limit_refusal_names_the_way_out(tmp_path, monkeypatch):
    from openai4s import project_history

    runner, _hub, _folder, _pid, root = runner_with_project(tmp_path)
    monkeypatch.setattr(project_history, "MAX_SCAN_ENTRIES", 1)
    try:
        with pytest.raises(GatewayError, match="unlink the project folder"):
            runner.delete_session(root)
        assert runner.store.get_frame(root) is not None
    finally:
        runner.close()


@pytest.mark.stubbed_backend
def test_transient_session_change_is_reported_but_not_remembered():
    autosave, service, _store = fake_autosave()
    service.sync_session = lambda root, **_kwargs: {
        "state": "error",
        "error": "session branch or project folder changed; save again",
        "code": "project_history_session_changed",
    }
    try:
        assert autosave.flush_session("one")["state"] == "error"
        assert autosave.status("project")["state"] == "saved"
    finally:
        autosave.close()
