"""`write_file` could destroy the old bytes without producing the new ones.

`edit_file` stages its rewrite beside the target and `os.replace`s it, and says
why in a comment: `write_text` "left a half-written file". `write_file`, in the
same directory, still called `path.write_text(content)` — which truncates first
and writes second, so a failure in between leaves a truncated file where a
complete one used to be and the previous contents are gone.

An overwrite that can lose the old content without committing the new one is
the one outcome this tool must not have, and it is not a hypothetical: the
content comes from a model, so the write can be megabytes, and the workspace is
the user's own directory.

Fault-injected at the write itself rather than at the tool, so the test
describes a disk that stopped rather than a function that was mocked out.
"""

from __future__ import annotations

import os

import pytest

from openai4s.host.files import WorkspaceFileService
from openai4s.tools.write_file import WriteFileTool


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    service = WorkspaceFileService(
        data_dir=tmp_path / "data",
        frame_id=lambda: "atomic-write-test",
        workspace=lambda: root,
    )
    service.root = root
    return service


def test_a_failed_overwrite_leaves_the_previous_contents(workspace, monkeypatch):
    """The defect: truncate-then-write loses the old file on any failure."""
    target = workspace.root / "protocol.md"
    target.write_text("the protocol that must survive\n", encoding="utf-8")
    real_fdopen = os.fdopen

    def failing(*args, **kwargs):
        handle = real_fdopen(*args, **kwargs)
        if "w" in str(kwargs.get("mode", "")) or (
            len(args) > 1 and "w" in str(args[1])
        ):
            handle.close()
            raise OSError(28, "No space left on device")
        return handle

    monkeypatch.setattr("openai4s.tools.write_file.os.fdopen", failing)

    with pytest.raises(OSError):
        WriteFileTool().execute(
            workspace, {"path": "protocol.md", "content": "x" * 4096}
        )

    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "the protocol that must survive\n"


def test_a_failed_write_leaves_no_stage_behind(workspace, monkeypatch):
    """A staged file that outlives its failure is a slow leak in the workspace."""
    target = workspace.root / "notes.txt"
    target.write_text("before\n", encoding="utf-8")

    def refuse(_self, _staged):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("openai4s.host.files.SecureWorkspaceParent.publish", refuse)

    with pytest.raises(OSError):
        WriteFileTool().execute(workspace, {"path": "notes.txt", "content": "after\n"})

    assert target.read_text(encoding="utf-8") == "before\n"
    assert not list(workspace.root.glob(".openai4s-*.write")), "a stage was left behind"


def test_a_successful_write_still_replaces_the_content(workspace):
    result = WriteFileTool().execute(
        workspace, {"path": "out.csv", "content": "a,b\n1,2\n"}
    )

    assert result["path"] == "out.csv"
    assert result["bytes"] == len("a,b\n1,2\n".encode("utf-8"))
    assert (workspace.root / "out.csv").read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert not list(workspace.root.glob(".openai4s-*.write"))


def test_an_overwrite_keeps_the_targets_permissions(workspace):
    """`mkstemp` creates 0600; without `copymode` an overwrite tightens the file."""
    target = workspace.root / "shared.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o644)

    WriteFileTool().execute(workspace, {"path": "shared.txt", "content": "new\n"})

    assert target.stat().st_mode & 0o777 == 0o644
    assert target.read_text(encoding="utf-8") == "new\n"


def test_a_new_file_is_created_without_a_pre_existing_target(workspace):
    """`copymode` must be skipped when there is nothing to copy from."""
    WriteFileTool().execute(
        workspace, {"path": "nested/fresh.txt", "content": "hello\n"}
    )

    assert (workspace.root / "nested" / "fresh.txt").read_text("utf-8") == "hello\n"
