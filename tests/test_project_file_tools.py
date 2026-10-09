"""Project source authority, recursive reads, binary imports and permission gates."""

import hashlib
import os

import pytest

from openai4s.config import Config
from openai4s.host_dispatch import HostDispatcher
from openai4s.sdk.host import _Host, decode_args
from openai4s.server.session_package import session_import_quarantine_key
from openai4s.tools import get_tool


def configured(tmp_path):
    source = tmp_path / "project"
    (source / "nested/data").mkdir(parents=True)
    (source / "nested/data/values.csv").write_text("sample,value\na,3\nb,5\n")
    (source / "nested/bytes.bin").write_bytes(b"\x00\xff\x80input\n")
    dispatcher = HostDispatcher(
        Config(data_dir=tmp_path / "state"), workspace=tmp_path / "workspace"
    )
    project = dispatcher.store.create_project(name="Research", folder_path=str(source))
    dispatcher.frame_id = dispatcher.store.new_frame(project_id=project["project_id"])
    return dispatcher, source, project["project_id"]


def test_recursive_reads_are_separate_from_workspace(tmp_path):
    dispatcher, source, _pid = configured(tmp_path)
    assert dispatcher("project_list_dir", [{}])["entries"][0]["name"] == "nested"
    assert dispatcher("project_glob", [{"pattern": "**/*.csv"}])["matches"] == [
        "nested/data/values.csv"
    ]
    result = dispatcher("project_grep", [{"pattern": "a,3", "include": "*.csv"}])
    assert result["count"] == 1
    assert result["matches"][0]["file"] == "nested/data/values.csv"
    assert (
        dispatcher(
            "project_read_file",
            [{"path": "nested/data/values.csv", "offset": 1, "limit": 1}],
        )["content"]
        == "a,3"
    )
    assert not (dispatcher._workspace() / "nested").exists()
    assert (source / "nested/data/values.csv").read_text().endswith("b,5\n")


def test_binary_import_preserves_source_and_records_exact_foreground_receipt(tmp_path):
    dispatcher, source, _pid = configured(tmp_path)
    payload = (source / "nested/bytes.bin").read_bytes()
    with dispatcher.bind_artifact_receipt_scope() as receipts:
        result = dispatcher(
            "project_import_file", [{"source_path": "nested/bytes.bin"}]
        )
    assert result["path"] == "project-inputs/nested/bytes.bin"
    assert (dispatcher._workspace() / result["path"]).read_bytes() == payload
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["bytes"] == len(payload)
    assert result["source"]["kind"] == "local_project"
    assert receipts == [
        {
            "filename": result["path"],
            "checksum": result["sha256"],
            "source": result["source"],
        }
    ]
    assert (source / "nested/bytes.bin").read_bytes() == payload


def test_native_import_commits_provenance(tmp_path):
    dispatcher, _source, _pid = configured(tmp_path)
    committed = []

    def commit(receipts):
        committed.extend(receipts)
        return [
            {
                "artifact_id": "artifact",
                "version_id": "version",
                "filename": receipts[0]["filename"],
            }
        ]

    with dispatcher.bind_native_artifact_committer(commit):
        result = dispatcher(
            "project_import_file",
            [{"source_path": "nested/data/values.csv", "path": "inputs.csv"}],
        )
    assert result["artifact"]["version_id"] == "version"
    assert committed[0]["source"]["source_path"] == "nested/data/values.csv"


def test_import_refuses_without_capture_and_before_over_budget_write(tmp_path):
    dispatcher, _source, _pid = configured(tmp_path)
    assert (
        "capture scope"
        in dispatcher("project_import_file", [{"source_path": "nested/bytes.bin"}])[
            "error"
        ]
    )
    target = dispatcher._workspace() / "result.bin"
    target.write_bytes(b"original")
    with dispatcher.bind_artifact_receipt_scope() as receipts:
        with pytest.raises(ValueError, match="max_bytes"):
            dispatcher(
                "project_import_file",
                [
                    {
                        "source_path": "nested/bytes.bin",
                        "path": "result.bin",
                        "max_bytes": 2,
                    }
                ],
            )
    assert receipts == []
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize(
    "method,spec,old_method,target",
    [
        (
            "project_read_file",
            {"path": "nested/data/values.csv"},
            "read_file",
            "nested/data/values.csv",
        ),
        (
            "project_import_file",
            {"source_path": "nested/data/values.csv"},
            "read_file",
            "nested/data/values.csv",
        ),
        (
            "project_import_file",
            {"source_path": "nested/data/values.csv"},
            "write_file",
            "project-inputs/*",
        ),
    ],
)
@pytest.mark.parametrize("decision", ["deny", "ask"])
def test_existing_file_policy_cannot_be_bypassed(
    tmp_path, method, spec, old_method, target, decision
):
    dispatcher, _source, _pid = configured(tmp_path)
    dispatcher.store.set_permission_rule(
        scope="global", tool=old_method, pattern=target, decision=decision
    )
    with dispatcher.bind_artifact_receipt_scope():
        result = dispatcher(method, [spec])
    assert "Permission denied" in result["error"]
    assert not (dispatcher._workspace() / "project-inputs").exists()


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "nested/link.txt", ".env", ".ssh/config", "nested/hardlink.txt"],
)
def test_source_traversal_links_and_secrets_are_refused(tmp_path, path):
    dispatcher, source, _pid = configured(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (source / "nested/link.txt").symlink_to(outside)
    os.link(outside, source / "nested/hardlink.txt")
    (source / ".env").write_text("private")
    (source / ".ssh").mkdir()
    (source / ".ssh/config").write_text("private")
    try:
        result = dispatcher("project_read_file", [{"path": path}])
    except (ValueError, OSError):
        pass
    else:
        assert "error" in result


def test_import_destination_cannot_escape(tmp_path):
    dispatcher, _source, _pid = configured(tmp_path)
    with dispatcher.bind_artifact_receipt_scope():
        with pytest.raises(ValueError):
            dispatcher(
                "project_import_file",
                [{"source_path": "nested/bytes.bin", "path": "../escape.bin"}],
            )
    assert not (tmp_path / "escape.bin").exists()


def test_live_binding_and_quarantine_fail_closed(tmp_path):
    dispatcher, source, pid = configured(tmp_path)
    dispatcher.store.update_project(pid, folder_path=None)
    with pytest.raises(ValueError, match="no linked folder"):
        dispatcher("project_list_dir", [{}])
    dispatcher.store.update_project(pid, folder_path=str(source))
    dispatcher.store.set_setting(
        session_import_quarantine_key(dispatcher.frame_id), "{}"
    )
    with pytest.raises(ValueError, match="quarantined"):
        dispatcher("project_list_dir", [{}])
    dispatcher.store.delete_setting(session_import_quarantine_key(dispatcher.frame_id))
    source.rename(tmp_path / "moved")
    with pytest.raises(ValueError, match="not found"):
        dispatcher("project_list_dir", [{}])
    assert not source.exists()


def test_team_and_custom_workspace_overlap_are_refused(tmp_path):
    dispatcher, source, _pid = configured(tmp_path)
    dispatcher.cfg.team_mode = True
    with pytest.raises(ValueError, match="standalone loopback"):
        dispatcher("project_list_dir", [{}])
    dispatcher.cfg.team_mode = False
    dispatcher.set_workspace(source / "analysis")
    with pytest.raises(ValueError, match="overlap"):
        dispatcher("project_list_dir", [{}])
    assert not (source / "analysis").exists()


@pytest.mark.stubbed_backend
def test_folder_cannot_change_while_permission_is_pending(tmp_path, monkeypatch):
    from openai4s.permissions import broker

    dispatcher, _source, pid = configured(tmp_path)
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    def approve(**_kwargs):
        dispatcher.store.update_project(pid, folder_path=str(replacement))
        return {"allow": True}

    monkeypatch.setattr(broker(), "gate", approve)
    with pytest.raises(ValueError, match="changed during approval"):
        dispatcher("project_list_dir", [{}])


def test_sdk_import_omits_none_and_tools_are_in_active_catalog(tmp_path):
    calls = []
    host = _Host(
        lambda method, args: calls.append((method, decode_args(args)))
        or {"path": "local"}
    )
    assert host.project_import_file("nested/a.csv")["path"] == "local"
    assert calls == [
        (
            "project_import_file",
            [{"source_path": "nested/a.csv", "max_bytes": 268435456}],
        )
    ]
    dispatcher, _source, _pid = configured(tmp_path)
    names = {tool.name for tool in dispatcher.tool_catalog().specs_for([])}
    assert {
        "project_list_dir",
        "project_read_file",
        "project_glob",
        "project_grep",
        "project_import_file",
    } <= names
    tool = get_tool("project_import_file")
    assert tool.writes_files and not tool.read_only
    assert tool.validation_error({"source_path": "a.csv", "max_bytes": 0})


@pytest.mark.parametrize(
    "method,spec",
    [
        ("project_read_file", {"path": "config.json"}),
        (
            "project_import_file",
            {"source_path": "config.json", "path": "ordinary.json"},
        ),
        (
            "project_import_file",
            {"source_path": "nested/data/values.csv", "path": "config.json"},
        ),
    ],
)
def test_unattended_fence_checks_project_source_and_import_destination(
    tmp_path, monkeypatch, method, spec
):
    monkeypatch.setenv("OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT", "1")
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "auto_review")
    dispatcher, source, _pid = configured(tmp_path)
    (source / "config.json").write_text('{"ordinary": "configuration"}')
    with dispatcher.bind_artifact_receipt_scope() as receipts:
        result = dispatcher(method, [spec])
    assert set(result) == {"error"}
    assert "credential path" in result["error"]
    assert receipts == []
    assert not (dispatcher._workspace() / "config.json").exists()
    assert not (dispatcher._workspace() / "ordinary.json").exists()


def test_project_history_does_not_spend_the_search_scan_budget(tmp_path, monkeypatch):
    from openai4s.project_folders import ReadOnlyProjectFiles
    from openai4s.tools.glob_files import GlobFilesTool

    source = tmp_path / "project"
    (source / ".openai4s" / "objects").mkdir(parents=True)
    for index in range(30):
        (source / ".openai4s" / "objects" / f"{index:064x}").write_bytes(b"x")
    (source / "data").mkdir()
    (source / "data" / "values.csv").write_text("a\n1\n")
    opened = []
    original = ReadOnlyProjectFiles.verified_read_opener

    def recording(self):
        opener = original(self)

        def open_file(relative):
            opened.append(str(relative))
            return opener(relative)

        return open_file

    monkeypatch.setattr(ReadOnlyProjectFiles, "verified_read_opener", recording)
    result = GlobFilesTool().execute(
        ReadOnlyProjectFiles(source.resolve()), {"pattern": "**/*"}
    )
    assert result["matches"] == ["data/values.csv"]
    # History entries are skipped before they count against the walk, not
    # opened and refused one by one.
    assert opened and not any(".openai4s" in path for path in opened)
