"""A step's one-line summary also travels as structured parts (CHAT-6).

The host words every step summary in English ("4 envs, 3 missing", "6 results
· you.com", "loaded"), and the Web card translated it by pattern: anything
outside a closed set of templates stayed English in a Chinese UI. The step's
``output`` now carries ``summary_parts`` -- the same facts as copy keys and
values -- which the card words in the UI language; the English ``summary``
stays as it was for the legacy shell and for records stored before.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import _step_end, _step_end_view, build_dispatcher


def test_environment_listing_names_its_counts_as_parts():
    result = {
        "environments": [{"name": n} for n in ("a", "b", "c", "d")],
        "missing": ["x", "y", "z"],
        "recommend": None,
    }
    output, summary, parts = _step_end_view("env_list", "env", result, True)
    assert summary == "4 envs, 3 missing"  # the legacy line is unchanged
    assert parts == [
        {"key": "count", "unit": "env", "n": 4},
        {"key": "missing", "n": 3},
    ]
    # The tool's own result is not mutated by the projection.
    assert "summary_parts" not in result and output is result


def test_a_web_search_keeps_its_source_as_a_verbatim_part():
    result = {
        "results": [{"title": "t", "url": "https://x.test"}] * 6,
        "source": "you.com",
    }
    _output, summary, parts = _step_end_view("web_search", "search", result, True)
    assert summary == "6 results · you.com"
    assert parts == [
        {"key": "count", "unit": "result", "n": 6},
        {"key": "text", "text": "you.com"},
    ]


@pytest.mark.parametrize(
    ("method", "kind", "result", "summary", "parts"),
    [
        (
            "load_skill",
            "skill",
            {"name": "figure-style"},
            "loaded",
            [{"key": "loaded"}],
        ),
        (
            "edit_file",
            "edit",
            {"path": "r.md", "replaced": 1},
            "1 change",
            [{"key": "count", "unit": "change", "n": 1}],
        ),
        (
            "env_setup",
            "env",
            {"ok": True, "installed": [], "failed": []},
            "ready",
            [{"key": "ready"}],
        ),
        (
            "skills_read",
            "skill",
            "x" * 12,
            "12 chars read",
            [{"key": "charsRead", "n": 12}],
        ),
        (
            "read_file",
            "read",
            {"total_lines": 40, "offset": 10, "content": "a\nb"},
            "lines 11–12 of 40",
            [{"key": "lines", "from": 11, "to": 12, "total": 40}],
        ),
        (
            "save_artifact",
            "artifact",
            {"filename": "a.png"},
            "saved",
            [{"key": "saved"}],
        ),
        ("unknown_thing", "tool", {"x": 1}, "done", [{"key": "done"}]),
    ],
)
def test_every_summary_has_parts(method, kind, result, summary, parts):
    _output, legacy, got = _step_end_view(method, kind, result, True)
    assert legacy == summary
    assert got == parts
    # The two-tuple projection is the same line.
    assert _step_end(method, kind, result, True)[1] == summary


def test_a_failure_keeps_its_reason():
    _output, summary, parts = _step_end_view(
        "save_artifact", "artifact", {"error": "no such file: a.png"}, False
    )
    assert summary == "failed: no such file: a.png"
    assert parts == [{"key": "failed", "reason": "no such file: a.png"}]


def test_a_delegation_summary_names_the_task_status():
    _output, summary, parts = _step_end_view(
        "delegate", "delegate", {"task_status": "partial"}, True
    )
    assert summary == "partial"
    assert parts == [{"key": "delegate", "status": "partial"}]


def test_the_streamed_end_step_carries_the_parts(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-root", workspace=workspace)
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    dispatcher("todo_write", [{"todos": [{"content": "a"}, {"content": "b"}]}])

    end = next(step for step in steps if step.get("phase") == "end")
    assert end["summary"] == "2 steps"
    assert end["output"]["summary_parts"] == [{"key": "count", "unit": "step", "n": 2}]
