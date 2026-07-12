"""Compatibility contracts for the host workspace file boundary."""

from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.tools import get_tool_by_host_method


def _dispatcher(tmp_path: Path, frame_id: str | None = "frame-1") -> HostDispatcher:
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return HostDispatcher(cfg=cfg, frame_id=frame_id)


def test_workspace_follows_frame_id_assigned_after_construction(tmp_path):
    dispatcher = _dispatcher(tmp_path, frame_id=None)
    default_workspace = dispatcher._workspace()

    dispatcher.frame_id = "frame-late"
    result = dispatcher("write_file", [{"path": "result.txt", "content": "late-bound"}])

    assert default_workspace.name == "default"
    assert result["path"] == "result.txt"
    assert dispatcher._workspace().name == "frame-late"
    assert (dispatcher._workspace() / "result.txt").read_text() == "late-bound"
    assert not (default_workspace / "result.txt").exists()


def test_dispatcher_envelope_calls_registered_file_tool_class(tmp_path, monkeypatch):
    dispatcher = _dispatcher(tmp_path)
    tool = get_tool_by_host_method("list_dir")
    assert tool is not None
    seen = []

    def execute(_self, context, arguments):
        seen.append((context, arguments))
        return {"path": ".", "count": 0, "entries": []}

    monkeypatch.setattr(type(tool), "execute", execute)

    result = dispatcher("list_dir", [{"path": "."}])

    assert result == {"path": ".", "count": 0, "entries": []}
    assert seen == [(dispatcher._tool_context, {"path": "."})]
    assert seen[0][0].workspace() == dispatcher._workspace()


def test_workspace_service_keeps_legacy_operation_facade(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    service = dispatcher._files

    written = service.write_file({"path": "compat.txt", "content": "hello"})
    read = service.read_file({"path": "compat.txt"})

    assert written == {"path": "compat.txt", "bytes": 5}
    assert read["content"] == "hello"
    for method in ("edit_file", "glob", "grep", "list_dir"):
        assert callable(getattr(service, method))


def test_resolve_confines_parent_absolute_and_symlink_paths(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")

    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": "../../outside/secret.txt"}])

    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": str(outside / "secret.txt")}])

    (workspace / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="path escapes the workspace"):
        dispatcher("read_file", [{"path": "linked/secret.txt"}])

    absolute_inside = workspace / "inside.txt"
    result = dispatcher(
        "write_file", [{"path": str(absolute_inside), "content": "inside"}]
    )
    assert result["path"] == "inside.txt"


def test_read_file_preserves_text_window_and_binary_shapes(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / "lines.txt").write_text("one\ntwo\nthree\n")
    (workspace / "binary.bin").write_bytes(b"\xff\x00")

    text = dispatcher("read_file", [{"path": "lines.txt", "offset": -4, "limit": -1}])
    binary = dispatcher("read_file", [{"path": "binary.bin"}])

    assert text == {
        "path": "lines.txt",
        "total_lines": 3,
        "offset": 0,
        "content": "one",
        "truncated": True,
    }
    assert binary == {
        "path": "binary.bin",
        "binary": True,
        "size_bytes": 2,
        "content": "",
    }


def test_edit_file_keeps_single_key_errors_and_replace_all_behavior(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    target = dispatcher._workspace() / "notes.txt"
    target.write_text("same\nsame\n")

    missing = dispatcher(
        "edit_file",
        [{"path": "notes.txt", "old_string": "missing", "new_string": "new"}],
    )
    duplicate = dispatcher(
        "edit_file",
        [{"path": "notes.txt", "old_string": "same", "new_string": "new"}],
    )
    replaced = dispatcher(
        "edit_file",
        [
            {
                "path": "notes.txt",
                "old_string": "same",
                "new_string": "new",
                "replace_all": True,
            }
        ],
    )

    assert missing == {"error": "edit_file: old_string not found"}
    assert set(duplicate) == {"error"}
    assert "not unique (2 matches)" in duplicate["error"]
    assert replaced == {"path": "notes.txt", "replaced": 2}
    assert target.read_text() == "new\nnew\n"


def test_glob_and_grep_filter_secret_files_but_keep_normal_results(tmp_path):
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    (workspace / ".env").write_text("TOKEN=NEEDLE")
    (workspace / "private.pem").write_text("NEEDLE")
    (workspace / "notes.txt").write_text("NEEDLE")

    globbed = dispatcher("glob", [{"pattern": "*"}])
    grepped = dispatcher("grep", [{"pattern": "NEEDLE"}])

    assert globbed["matches"] == ["notes.txt"]
    assert [(hit["file"], hit["line"]) for hit in grepped["matches"]] == [
        ("notes.txt", 1)
    ]


def test_list_dir_missing_directory_keeps_soft_fail_shape(tmp_path):
    dispatcher = _dispatcher(tmp_path)

    result = dispatcher("list_dir", [{"path": "missing"}])

    assert result == {"error": "list_dir: no such directory: missing"}
