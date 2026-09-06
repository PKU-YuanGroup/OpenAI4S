"""Cross-lane contracts for the two Wave 2 changes that met in one file.

Lane D rewrote ``compact`` and Lane E rewrote ``externalize_large_outputs``
in regions git merged without reporting a conflict.  ``compact`` calls the
function Lane E changed, so the retrieval channel reaches a direct
``compact`` caller only when the workspace is threaded through — a seam
that neither lane's own tests could cover, because neither lane's tree
contained the other's change.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai4s.agent.compaction as comp_mod
import openai4s.agent.runtime as runtime
from openai4s.agent.compaction import compact
from openai4s.agent.models import RunState
from openai4s.agent.runtime import CompactionPolicy

_BUDGET = 10_000
_TRIGGER_RATIO = 0.75
_FILL = "k" * 20_000


def _cfg(compaction_dir) -> SimpleNamespace:
    return SimpleNamespace(
        compaction_dir=compaction_dir,
        context_window_tokens=_BUDGET,
        compaction_trigger_ratio=_TRIGGER_RATIO,
        llm=SimpleNamespace(max_tokens=8192),
    )


def _handoff() -> str:
    return "\n\n".join(f"## {field}\n- done." for field in comp_mod.HANDOFF_FIELDS)


def test_compact_threads_the_workspace_into_externalize(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(
        comp_mod,
        "chat",
        lambda *_args, **_kwargs: {"content": _handoff(), "finish_reason": "stop"},
    )

    projected = compact(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {"role": "user", "content": "[Observation]\n" + _FILL},
        ],
        _cfg(tmp_path / "compaction"),
        archive_dir=tmp_path / "compaction",
        workspace=workspace,
    )

    archived = next(
        message
        for message in projected
        if isinstance(message.get("content_archive"), dict)
    )
    ref = archived["content_archive"]["workspace_ref"]
    blob = workspace / ref
    assert blob.exists(), "compact() dropped the workspace on the way to externalize"
    assert json.loads(blob.read_text("utf-8"))["content"].endswith(_FILL[-32:])
    assert ref in str(archived["content"])


def test_policy_threads_the_workspace_into_compact(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    recorded: dict = {}

    def _record(messages, _cfg_arg, **kwargs):
        recorded.update(kwargs)
        return list(messages[:1])

    monkeypatch.setattr(runtime, "compact", _record)
    policy = CompactionPolicy(
        _cfg(tmp_path / "compaction"),
        context_budget_provider=lambda _state: _BUDGET,
        workspace_provider=lambda _state: str(workspace),
    )

    state = RunState([{"role": "user", "content": "y" * (_BUDGET * 4)}])
    policy.prepare(state)

    assert recorded, "compaction never ran, so the seam was not exercised"
    assert recorded["workspace"] == str(workspace)
