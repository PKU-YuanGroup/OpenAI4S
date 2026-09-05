"""Retrievable large-output archives: workspace blobs and Artifact guidance.

Oversized tool/observation text used to be replaced by a preview plus a
compaction-dir hash the kernel could not open. These contracts pin the
workspace-relative copy, the Artifact read-back line, and the absence of
absolute paths in the model-visible marker.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.agent.compaction as comp_mod
import openai4s.agent.runtime as runtime
from openai4s.agent.compaction import externalize_large_outputs
from openai4s.agent.models import RunState
from openai4s.agent.runtime import CompactionPolicy
from openai4s.kernel import Kernel

# Recorded from externalize_large_outputs(..., workspace=None) on the Wave 1
# baseline, before the retrieval channel existed.
_BASELINE_FILL = "Q" * 20_000
_BASELINE_OBSERVATION = "[Observation]\n" + _BASELINE_FILL
_BASELINE_SHA256 = "5169e1528ce264ed3c7e31823b21002166ebcb34bd5d2b86feeaa262d96008a6"
_BASELINE_ARCHIVE_REF = (
    "blobs/51/5169e1528ce264ed3c7e31823b21002166ebcb34bd5d2b86feeaa262d96008a6.json"
)
_BASELINE_PREVIEW = "[Observation] " + ("Q" * 753) + "…"
_BASELINE_NONE_WORKSPACE_CONTENT = (
    "[Large output archived]\n"
    f"sha256: {_BASELINE_SHA256}\n"
    f"archive_ref: {_BASELINE_ARCHIVE_REF}\n"
    "original_chars: 20014\n"
    f"preview: {_BASELINE_PREVIEW}"
)
_BASELINE_NONE_WORKSPACE_ARCHIVE = {
    "sha256": _BASELINE_SHA256,
    "archive_ref": _BASELINE_ARCHIVE_REF,
    "original_chars": 20014,
}


def _observation_messages(body: str) -> list[dict]:
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "user", "content": body},
    ]


def _assert_marker_has_no_host_paths(text: str, archive: dict, tmp_path: Path) -> None:
    rendered = text + repr(archive)
    assert str(tmp_path) not in text
    assert str(tmp_path) not in repr(archive)
    home = str(Path.home())
    assert home not in rendered
    assert "$HOME" not in rendered
    assert os.path.expanduser("~") not in rendered


def test_workspace_blob_is_retrievable_without_absolute_paths(tmp_path):
    original = "[Observation]\n" + ("n" * 20_000)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projected = externalize_large_outputs(
        _observation_messages(original),
        tmp_path / "compaction",
        workspace=workspace,
    )
    message = projected[2]
    archive = message["content_archive"]
    digest = archive["sha256"]
    ref = archive["workspace_ref"]
    text = message["content"]

    assert ref == f".openai4s/context/{digest}.json"
    assert Path(ref).name == f"{digest}.json"
    assert ".openai4s/context/" + digest + ".json" in text
    assert "read the full text" in text
    _assert_marker_has_no_host_paths(text, archive, tmp_path)

    previous = os.getcwd()
    try:
        os.chdir(workspace)
        loaded = json.load(open(ref))["content"]
    finally:
        os.chdir(previous)
    assert loaded == original


def test_workspace_none_matches_pre_retrieval_baseline(tmp_path):
    projected = externalize_large_outputs(
        _observation_messages(_BASELINE_OBSERVATION),
        tmp_path,
        workspace=None,
    )
    message = projected[2]
    assert message["content"] == _BASELINE_NONE_WORKSPACE_CONTENT
    assert message["content_archive"] == _BASELINE_NONE_WORKSPACE_ARCHIVE
    assert "workspace_ref" not in message["content_archive"]
    assert "read the full text" not in message["content"]
    assert not (tmp_path / ".openai4s" / "context").exists()


def test_identical_content_writes_the_workspace_blob_once(tmp_path):
    original = "[Observation]\n" + ("k" * 20_000)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    messages = _observation_messages(original)
    first = externalize_large_outputs(
        messages, tmp_path / "compaction", workspace=workspace
    )
    ref = first[2]["content_archive"]["workspace_ref"]
    path = workspace / ref
    context_files = list((workspace / ".openai4s" / "context").glob("*.json"))
    assert context_files == [path]
    os.utime(path, (0, 0))

    second = externalize_large_outputs(
        messages, tmp_path / "compaction", workspace=workspace
    )
    assert second[2]["content_archive"]["workspace_ref"] == ref
    assert list((workspace / ".openai4s" / "context").glob("*.json")) == [path]
    assert path.stat().st_mtime == 0
    assert json.loads(path.read_text(encoding="utf-8"))["content"] == original


def test_compaction_policy_forwards_workspace_from_provider(monkeypatch):
    captured: list[object] = []

    def fake_externalize(messages, archive_dir, **kwargs):
        captured.append(kwargs.get("workspace"))
        return list(messages)

    monkeypatch.setattr(runtime, "externalize_large_outputs", fake_externalize)
    cfg = SimpleNamespace(
        compaction_dir="archive",
        context_window_tokens=128_000,
        compaction_trigger_ratio=0.75,
    )
    state = RunState([{"role": "user", "content": "hi"}])

    CompactionPolicy(
        cfg,
        workspace_provider=lambda _s: "/some/ws",
    ).prepare(state)
    assert captured == ["/some/ws"]

    def boom(_state):
        raise RuntimeError("workspace unavailable")

    CompactionPolicy(cfg, workspace_provider=boom).prepare(state)
    assert captured == ["/some/ws", None]


def test_artifact_archiver_marker_includes_host_artifact_path():
    # Same-session context Artifacts are saved before the marker is built, so
    # host.artifact_path(version_id) resolves without materialise_artifact.
    projected = externalize_large_outputs(
        [{"role": "tool", "content": "x" * 20_000}],
        None,
        artifact_archiver=lambda _c, _m, _a: {
            "artifact_id": "a-context",
            "version_id": "v-context",
        },
    )
    text = projected[0]["content"]
    assert "host.artifact_path('" in text
    assert "v-context" in text
    assert "open(host.artifact_path('v-context')).read()" in text


def _marker_python_snippet(text: str) -> tuple[str, str]:
    """The exact Python one-liner the marker hands the model, split at the
    imports, so the test runs the model's instructions verbatim rather than
    a hand-written equivalent that happens to import what the marker omits."""
    match = re.search(r"Python: (.+?); R: ", text)
    assert match, text
    imports, expression = match.group(1).split("; ", 1)
    return imports, expression


def test_kernel_reads_externalized_workspace_blob(tmp_path):
    original = "[Observation]\n" + ("z" * 20_000)
    projected = externalize_large_outputs(
        _observation_messages(original),
        tmp_path / "compaction",
        workspace=tmp_path,
    )
    imports, expression = _marker_python_snippet(projected[2]["content"])
    with Kernel(cwd=str(tmp_path)) as kernel:
        # A cell that changed directory can still follow the marker: the
        # read-back is anchored on OPENAI4S_WORKSPACE, not the cwd.
        result = kernel.execute(
            f"import os\nos.chdir('/')\n{imports}\nprint(({expression})[:40])"
        )
    assert result["error"] is None
    assert result["stdout"].strip() == original[:40]


# ---------------------------------------------------------------------------
# Review follow-ups: the digest path is written atomically, a torn or planted
# file is handled per root, a symlink loop costs only the kernel copy, and a
# user's own prompt is never mistaken for an output.
# ---------------------------------------------------------------------------


def _torn_dump(prefix_chars: int = 200):
    """A ``json.dump`` that writes a prefix and then hits ENOSPC."""

    def dump(payload, handle, **kwargs):
        handle.write(json.dumps(payload, **kwargs)[:prefix_chars])
        raise OSError(28, "No space left on device")

    return dump


def test_a_torn_write_leaves_no_file_at_the_digest_path(tmp_path, monkeypatch):
    """A crash or ENOSPC mid-write used to leave a truncated ``<sha>.json`` at
    the content-addressed path; every later externalization then rejected it
    as unreadable, forever, and on the host archive that repeat failure
    tripped the compaction breaker.  The bytes now land by rename only."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    messages = _observation_messages("[Observation]\n" + ("t" * 20_000))
    real_dump = json.dump
    monkeypatch.setattr(json, "dump", _torn_dump())
    with pytest.raises(OSError):
        externalize_large_outputs(messages, tmp_path / "archive", workspace=workspace)
    monkeypatch.setattr(json, "dump", real_dump)

    for root in (tmp_path / "archive", workspace / ".openai4s"):
        assert not list(root.rglob("*.json")), "a torn blob was left behind"
        assert not list(root.rglob("*.tmp")), "a temp file was left behind"

    projected = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    archive = projected[2]["content_archive"]
    assert "archive_ref" in archive and "workspace_ref" in archive
    restored = comp_mod.load_archived_content(tmp_path / "archive", archive["sha256"])
    assert restored == messages[2]["content"]


def test_a_torn_file_at_the_digest_path_is_repaired(tmp_path):
    """Garbage at ``<sha>.json`` (a torn write from a pre-fix build, or junk a
    cell left there) is not a blob and endorses nothing: it is replaced with
    the right bytes on both roots instead of poisoning every later
    externalization of that content."""
    workspace = tmp_path / "ws"
    messages = _observation_messages("[Observation]\n" + ("r" * 20_000))
    honest = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    archive = honest[2]["content_archive"]
    host_path = tmp_path / "archive" / archive["archive_ref"]
    ws_path = workspace / archive["workspace_ref"]
    for path in (host_path, ws_path):
        path.write_text('{"sha256": "', "utf-8")

    projected = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    assert projected[2]["content_archive"] == archive
    for path in (host_path, ws_path):
        assert json.loads(path.read_text("utf-8"))["content"] == messages[2]["content"]


def test_a_foreign_blob_is_replaced_only_under_the_host_archive(tmp_path):
    """A well-formed blob holding different content is a planted file in the
    agent-writable workspace (left alone, not referenced) but a host-owned
    inconsistency under the compaction directory (replaced)."""
    workspace = tmp_path / "ws"
    messages = _observation_messages("[Observation]\n" + ("f" * 20_000))
    honest = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    archive = honest[2]["content_archive"]
    host_path = tmp_path / "archive" / archive["archive_ref"]
    ws_path = workspace / archive["workspace_ref"]
    foreign = json.dumps({"sha256": archive["sha256"], "content": "planted"})
    host_path.write_text(foreign, "utf-8")
    ws_path.write_text(foreign, "utf-8")

    projected = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    again = projected[2]["content_archive"]
    assert again["archive_ref"] == archive["archive_ref"]
    assert "workspace_ref" not in again
    assert json.loads(host_path.read_text("utf-8"))["content"] == (
        messages[2]["content"]
    )
    assert ws_path.read_text("utf-8") == foreign, "the planted file was rewritten"


def test_a_symlink_loop_at_the_context_dir_costs_only_the_kernel_copy(tmp_path):
    """``Path.resolve`` raises RuntimeError, not OSError, for a symlink loop on
    CPython 3.10-3.12, and a cell can plant one at ``.openai4s/context``.
    It must cost the workspace copy, not the whole turn's externalization."""
    workspace = tmp_path / "ws"
    (workspace / ".openai4s").mkdir(parents=True)
    os.symlink("context", workspace / ".openai4s" / "context")
    messages = _observation_messages("[Observation]\n" + ("l" * 20_000))

    projected = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    archive = projected[2]["content_archive"]
    assert "archive_ref" in archive and "workspace_ref" not in archive


def test_a_users_own_prompt_after_a_cancelled_cell_stays_inline(tmp_path):
    """A Stop-cancelled cell leaves the assistant's code reply with no
    observation, so the user's NEXT message directly follows a code action.
    A pasted prompt or a pinned figure is the user's request, never an
    output to preview away."""
    paste = "please look at this log:\n" + ("line\n" * 5_000)
    figure = [
        {"type": "text", "text": "what is in this plot?"},
        {"type": "image", "data": "A" * 30_000, "mime": "image/png"},
    ]
    for content in (paste, figure):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "```python\nprint(1)\n```"},
            {"role": "user", "content": content},
        ]
        projected = externalize_large_outputs(
            messages, tmp_path / "archive", workspace=tmp_path / "ws"
        )
        assert projected[3]["content"] == content
        assert "content_archive" not in projected[3]
    assert not list((tmp_path / "archive").rglob("*.json"))
