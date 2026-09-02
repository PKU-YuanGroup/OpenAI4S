"""Retrievable large-output archives: workspace blobs and Artifact guidance.

Oversized tool/observation text used to be replaced by a preview plus a
compaction-dir hash the kernel could not open. These contracts pin the
workspace-relative copy, the Artifact read-back line, and the absence of
absolute paths in the model-visible marker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

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


def test_kernel_reads_externalized_workspace_blob(tmp_path):
    original = "[Observation]\n" + ("z" * 20_000)
    projected = externalize_large_outputs(
        _observation_messages(original),
        tmp_path / "compaction",
        workspace=tmp_path,
    )
    ref = projected[2]["content_archive"]["workspace_ref"]
    with Kernel(cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "import json\n" f"print(json.load(open({ref!r}))['content'][:40])",
        )
    assert result["error"] is None
    assert result["stdout"].strip() == original[:40]
