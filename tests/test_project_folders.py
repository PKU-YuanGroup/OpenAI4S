"""Project folder grants persist, remain read-only and respect the file boundary."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from urllib.parse import quote

import pytest

from openai4s.config import Config
from openai4s.project_folders import (
    PROJECT_ROOTS_ENV,
    ProjectFolderError,
    ReadOnlyProjectFiles,
    allowed_project_roots,
    project_folder_path,
    require_local_project_folders,
    validate_folder_path,
)
from openai4s.server.project_folder_routes import (
    MAX_PREVIEW_BYTES,
    local_folders,
    project_file,
)
from openai4s.store import get_store
from tests.test_project_patch_unknown_id import _call
from tests.test_team_auth_routes import _TeamDaemon


def test_folder_binding_browsing_preview_and_detach_over_http(tmp_path):
    source = tmp_path / "research"
    nested = source / "data" / "nested"
    nested.mkdir(parents=True)
    (nested / "measurements.csv").write_text("sample,value\na,3\nb,5\n")
    (source / ".env").write_text("must stay private")
    (source / "binary.dat").write_bytes(b"\x00\x01")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (source / "escape.txt").symlink_to(outside)
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=False)

    def call(method, path, body=None):
        return _call(daemon.port, method, path, body, token=daemon.token)

    try:
        status, project = call(
            "POST", "/projects", {"name": "Research", "folder_path": str(source)}
        )
        assert status == 200, project
        pid = project["project_id"]
        assert project["folder_path"] == str(source.resolve())
        status, picker = call("GET", f"/local-folders?path={quote(str(tmp_path))}")
        assert status == 200, picker
        assert {entry["name"] for entry in picker["entries"]} >= {"research", "daemon"}
        status, listing = call("GET", f"/projects/{pid}/files")
        assert status == 200, listing
        assert {entry["name"] for entry in listing["entries"]} == {"data", "binary.dat"}
        assert listing["parent_path"] is None
        status, nested_listing = call("GET", f"/projects/{pid}/files?path=data/nested")
        assert status == 200, nested_listing
        assert nested_listing["parent_path"] == "data"
        assert nested_listing["entries"][0]["path"] == "data/nested/measurements.csv"
        status, preview = call(
            "GET", f"/projects/{pid}/file?path=data/nested/measurements.csv"
        )
        assert status == 200, preview
        assert preview["content"] == "sample,value\na,3\nb,5\n"
        assert preview["encoding"] == "utf-8"
        assert preview["truncated"] is False
        for path, expected in (
            ("../outside.txt", 400),
            (".env", 403),
            ("escape.txt", 403),
            ("binary.dat", 415),
        ):
            status, refused = call("GET", f"/projects/{pid}/file?path={quote(path)}")
            assert status == expected, refused
            assert refused.get("request_id")
        status, renamed = call("PATCH", f"/projects/{pid}", {"name": "Renamed"})
        assert status == 200 and renamed["folder_path"] == str(source.resolve())
        status, detached = call("PATCH", f"/projects/{pid}", {"folder_path": None})
        assert status == 200 and detached["folder_path"] is None
        status, refused = call("GET", f"/projects/{pid}/files")
        assert status == 409 and refused["code"] == "no_project_folder"
        assert (nested / "measurements.csv").read_text() == preview["content"]
    finally:
        daemon.close()


def test_folder_binding_rejects_bad_paths_without_creating_project(tmp_path):
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=False)
    try:
        before = daemon.store.count_projects()
        for path in (
            str(tmp_path / "missing"),
            "relative/path",
            7,
            [],
            str(tmp_path / ".ssh"),
        ):
            status, refused = _call(
                daemon.port,
                "POST",
                "/projects",
                {"folder_path": path},
                token=daemon.token,
            )
            assert status in (400, 403, 404), refused
        assert daemon.store.count_projects() == before
        assert not (tmp_path / "missing").exists()
    finally:
        daemon.close()


def test_team_mode_cannot_discover_or_bind_host_folders(tmp_path):
    daemon = _TeamDaemon(tmp_path / "daemon", team_mode=True)
    try:
        status, unbound = _call(
            daemon.port,
            "POST",
            "/projects",
            {"name": "Unbound", "folder_path": ""},
            token=daemon.token,
        )
        assert status == 200 and unbound["folder_path"] is None
        for method, route, body in (
            ("GET", "/local-folders", None),
            ("POST", "/projects", {"folder_path": str(tmp_path)}),
        ):
            status, refused = _call(
                daemon.port, method, route, body, token=daemon.token
            )
            assert status == 403, refused
            assert refused["code"] == "local_project_folders_only"
    finally:
        daemon.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "0.0.0.0"},
        {"trusted_proxy_origins": ("https://lab.example",)},
        {"team_mode": True},
    ],
)
def test_remote_and_team_configuration_cannot_enable_project_folders(tmp_path, kwargs):
    with pytest.raises(ProjectFolderError, match="standalone loopback"):
        require_local_project_folders(Config(data_dir=tmp_path, **kwargs))


def test_project_source_cannot_overlap_daemon_state_or_output_workspace(tmp_path):
    data_dir = tmp_path / "daemon"
    workspace = data_dir / "agent-workspaces" / "session"
    workspace.mkdir(parents=True)
    cfg = Config(data_dir=data_dir)
    for folder in (tmp_path, data_dir, workspace):
        with pytest.raises(ProjectFolderError, match="overlap"):
            validate_folder_path(str(folder), cfg)


def _confine_default_roots(tmp_path, monkeypatch):
    home, scratch = tmp_path / "home", tmp_path / "tmp"
    home.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(scratch))
    monkeypatch.delenv(PROJECT_ROOTS_ENV, raising=False)
    return home, scratch


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directories")
def test_system_directories_are_outside_project_roots():
    for folder in ("/", "/etc", "/usr"):
        with pytest.raises(ProjectFolderError) as refused:
            validate_folder_path(folder)
        assert refused.value.code == "project_folder_outside_roots"
        assert refused.value.status == 403


def test_operator_roots_extend_the_defaults_without_prefix_aliasing(
    tmp_path, monkeypatch
):
    home, _scratch = _confine_default_roots(tmp_path, monkeypatch)
    data = tmp_path / "data"
    (data / "set").mkdir(parents=True)
    (tmp_path / "database").mkdir()
    (home / "notes").mkdir()
    assert validate_folder_path(str(home / "notes")) == str((home / "notes").resolve())
    with pytest.raises(ProjectFolderError, match=PROJECT_ROOTS_ENV):
        validate_folder_path(str(data / "set"))
    monkeypatch.setenv(PROJECT_ROOTS_ENV, os.pathsep.join(["relative", str(data)]))
    assert str(data.resolve()) in allowed_project_roots()
    assert validate_folder_path(str(data / "set")) == str((data / "set").resolve())
    with pytest.raises(ProjectFolderError) as refused:
        validate_folder_path(str(tmp_path / "database"))
    assert refused.value.code == "project_folder_outside_roots"


def test_picker_stops_at_a_project_root(tmp_path, monkeypatch):
    home, _scratch = _confine_default_roots(tmp_path, monkeypatch)
    (home / "research").mkdir()
    at_root = local_folders("")
    assert at_root["path"] == str(home.resolve())
    assert at_root["parent_path"] is None
    assert [entry["name"] for entry in at_root["entries"]] == ["research"]
    inside = local_folders(str(home / "research"))
    assert inside["parent_path"] == str(home.resolve())
    with pytest.raises(ProjectFolderError):
        local_folders(str(tmp_path))


def test_history_directories_cannot_become_project_folders(tmp_path):
    archive = tmp_path / "research" / ".openai4s"
    (archive / "sessions").mkdir(parents=True)
    for folder in (archive, archive / "sessions"):
        with pytest.raises(ProjectFolderError) as refused:
            validate_folder_path(str(folder))
        assert refused.value.code == "project_folder_history"
    assert validate_folder_path(str(tmp_path / "research")) == str(
        (tmp_path / "research").resolve()
    )


def test_missing_or_replaced_folder_is_not_recreated_or_retargeted(tmp_path):
    cfg = Config(data_dir=tmp_path / "daemon")
    store = get_store(cfg.db_path)
    source = tmp_path / "source"
    source.mkdir()
    project = store.create_project(name="source", folder_path=str(source.resolve()))
    service = ReadOnlyProjectFiles(source.resolve())
    source.rmdir()
    with pytest.raises(ProjectFolderError, match="not found"):
        service.workspace()
    assert not source.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    source.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectFolderError, match="changed"):
        project_folder_path(store, cfg, project["project_id"])
    with pytest.raises(ProjectFolderError, match="changed"):
        service.workspace()


def test_preview_is_byte_bounded_and_handles_split_utf8(tmp_path):
    target = tmp_path / "large.txt"
    target.write_bytes(b"a" * (MAX_PREVIEW_BYTES - 1) + "é".encode() + b"tail")
    result = project_file(tmp_path, "large.txt")
    assert result["truncated"] is True
    assert result["content"] == "a" * (MAX_PREVIEW_BYTES - 1)
    assert result["size"] == MAX_PREVIEW_BYTES + 5


def test_preview_rejects_hardlinks_and_special_files(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("ungranted")
    source = tmp_path / "source"
    source.mkdir()
    os.link(outside, source / "alias.txt")
    os.mkfifo(source / "pipe")
    for path in ("alias.txt", "pipe"):
        with pytest.raises((ValueError, OSError)):
            project_file(source, path)


def test_project_history_is_separate_from_agent_input_searches(tmp_path):
    history = tmp_path / ".openai4s" / "sessions"
    history.mkdir(parents=True)
    (history / "private.txt").write_text("a different conversation")
    (tmp_path / "data.txt").write_text("analysis input")
    files = ReadOnlyProjectFiles(tmp_path)
    assert [row["name"] for row in files.list_dir({})["entries"]] == ["data.txt"]
    assert files.glob({"pattern": "**/*.txt"})["matches"] == ["data.txt"]
    for path in (".openai4s", ".openai4s/sessions/private.txt"):
        with pytest.raises(ValueError, match="history"):
            files.open_verified_read(path)
    with pytest.raises(ValueError, match="history"):
        files.resolve(".openai4s")


def test_upgrade_existing_projects_preserves_rows_and_folder_survives_reopen(tmp_path):
    cfg = Config(data_dir=tmp_path / "daemon")
    store = get_store(cfg.db_path)
    project = store.create_project(name="Existing", context="preserve me")
    store.close()
    with sqlite3.connect(cfg.db_path) as conn:
        conn.execute("ALTER TABLE projects DROP COLUMN folder_path")
        conn.execute("DELETE FROM schema_migrations WHERE version=35")
        conn.execute("PRAGMA user_version=34")
    upgraded = get_store(cfg.db_path)
    assert upgraded.get_project(project["project_id"])["context"] == "preserve me"
    assert upgraded.get_project(project["project_id"])["folder_path"] is None
    upgraded.update_project(project["project_id"], folder_path=str(tmp_path.resolve()))
    upgraded.close()
    reopened = get_store(cfg.db_path)
    assert reopened.get_project(project["project_id"])["folder_path"] == str(
        tmp_path.resolve()
    )
