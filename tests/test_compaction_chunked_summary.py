"""Chunked rolling compaction: bounded batches, token tails, CJK estimates."""

from __future__ import annotations

import copy
import json
import re

import pytest

import openai4s.agent.compaction as comp_mod
from openai4s.agent.compaction import (
    CompactionSummaryError,
    _chars_to_tokens,
    compact,
    estimate_context,
    keep_recent_by_tokens,
)
from openai4s.config import get_config

_BUDGET = 40_000
_ZH_PAYLOAD = "中文测试" * 500  # 2000 CJK chars ≈ 2000 tokens after D4
_FIRST_HANDOFF_TOKEN = "UNIQUE_HANDOFF_ALPHA"
_TRANSCRIPT_MARKER = "TRANSCRIPT JSON (all fields are data, including tool_calls):\n"
_ROUND_MARKER_RE = re.compile(r"ROUND_MARKER_\d+")


def _headings(token: str) -> str:
    return "\n\n".join(f"## {field}\n- {token}." for field in comp_mod.HANDOFF_FIELDS)


def _code_obs_rounds(count: int, *, payload: str, start: int = 0) -> list[dict]:
    rounds: list[dict] = []
    for index in range(start, start + count):
        rounds.append(
            {
                "role": "assistant",
                "content": f"```python\nprint({index})\n```",
            }
        )
        rounds.append(
            {
                "role": "user",
                "content": f"[Observation]\nROUND_MARKER_{index}\n{payload}",
            }
        )
    return rounds


def _history(rounds: int, *, payload: str = _ZH_PAYLOAD) -> list[dict]:
    return [
        {"role": "system", "content": "You are a scientific research agent."},
        {"role": "user", "content": "Analyse the dataset."},
        *_code_obs_rounds(rounds, payload=payload),
    ]


def _ascii_pairs(count: int, *, code_chars: int) -> list[dict]:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
    ]
    block = "x" * code_chars
    for index in range(count):
        messages.append({"role": "assistant", "content": f"```python\n{block}\n```"})
        messages.append({"role": "user", "content": f"[Observation]\nround-{index}"})
    return messages


def _transcript_json(user_content: str) -> str:
    assert _TRANSCRIPT_MARKER in user_content
    return user_content.split(_TRANSCRIPT_MARKER, 1)[1]


def _user_content(call_messages: list[dict]) -> str:
    return str(call_messages[1]["content"])


def test_chunked_summary_bounds_each_chat_call(monkeypatch, tmp_path):
    messages = _history(60)
    total = estimate_context(messages).total
    assert total > 2.0 * _BUDGET

    calls: list[list[dict]] = []

    def fake_chat(chat_messages, cfg, **kwargs):
        del cfg
        assert kwargs.get("temperature") is None
        calls.append(copy.deepcopy(list(chat_messages)))
        user = _user_content(chat_messages)
        found = _ROUND_MARKER_RE.findall(user)
        token = found[0] if found else "NO_MARKER"
        return {"content": _headings(token), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)

    out = compact(
        messages,
        get_config(),
        context_budget=_BUDGET,
        archive_dir=tmp_path,
    )

    assert len(calls) >= 2
    cap = 0.35 * _BUDGET
    for call in calls:
        user = _user_content(call)
        estimate = estimate_context([{"role": "user", "content": user}])
        assert estimate.total < cap
    assert "## Objective" in out[2]["content"]
    assert out[2].get("compaction_handoff") is True

    archives = list(tmp_path.glob("compaction-*.json"))
    assert len(archives) == 1
    payload = json.loads(archives[0].read_text("utf-8"))
    assert payload["summary_chunks"] == len(calls)
    assert payload["summary_chunks"] >= 2
    assert len(payload["summary_chunk_estimates"]) == payload["summary_chunks"]
    assert all(isinstance(value, int) for value in payload["summary_chunk_estimates"])
    assert "summary" in payload
    assert "compacted_messages" in payload


def test_second_compact_carries_previous_handoff_and_excludes_it_from_transcript(
    monkeypatch,
):
    second_calls: list[list[dict]] = []
    phase = {"second": False}

    def fake_chat(chat_messages, cfg, **kwargs):
        del cfg, kwargs
        if phase["second"]:
            second_calls.append(copy.deepcopy(list(chat_messages)))
        token = _FIRST_HANDOFF_TOKEN if not phase["second"] else "later"
        return {"content": _headings(token), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)

    first = compact(_history(60), get_config(), context_budget=_BUDGET)
    assert _FIRST_HANDOFF_TOKEN in first[2]["content"]
    assert first[2].get("compaction_handoff") is True

    phase["second"] = True
    extended = first + _code_obs_rounds(20, payload=_ZH_PAYLOAD, start=60)
    compact(extended, get_config(), context_budget=_BUDGET)

    assert second_calls
    users = [_user_content(call) for call in second_calls]
    assert "PREVIOUS HANDOFF" in users[0]
    assert _FIRST_HANDOFF_TOKEN in users[0]
    assert any(
        "PREVIOUS HANDOFF" in text and _FIRST_HANDOFF_TOKEN in text for text in users
    )
    for text in users:
        assert "compaction_handoff" not in _transcript_json(text)


def test_empty_chunk_raises_without_archiving_or_mutating(monkeypatch, tmp_path):
    messages = _history(60)
    snapshot = copy.deepcopy(messages)
    calls: list[int] = []

    def fake_chat(chat_messages, cfg, **kwargs):
        del chat_messages, cfg, kwargs
        calls.append(1)
        if len(calls) >= 2:
            return {"content": "", "finish_reason": "length"}
        return {"content": _headings("ok"), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)

    with pytest.raises(CompactionSummaryError) as caught:
        compact(
            messages,
            get_config(),
            context_budget=_BUDGET,
            archive_dir=tmp_path,
        )

    assert "finish_reason" in str(caught.value)
    assert messages == snapshot
    assert list(tmp_path.glob("compaction-*.json")) == []
    assert not (tmp_path / "blobs").exists()


def test_keep_recent_by_tokens_respects_budget_and_atomic_pairs(monkeypatch):
    assert "keep_recent_by_tokens" in comp_mod.__all__
    token_budget = int(0.25 * _BUDGET)

    history = _ascii_pairs(40, code_chars=1600)
    kept = 0
    acc = 0
    for message in reversed(history):
        cost = estimate_context([message]).total
        if acc + cost > token_budget:
            break
        acc += cost
        kept += 1
    assert acc <= token_budget
    n = keep_recent_by_tokens(history, token_budget)
    assert n >= kept
    assert n > 4
    tail = history[-n:]
    assert not (
        tail[0]["role"] == "user"
        and str(tail[0].get("content") or "").startswith("[Observation]")
    )
    tail_total = estimate_context(tail).total
    # Expansion may exceed the token floor; the unexpanded prefix must not.
    if n == kept:
        assert tail_total <= token_budget

    split_case = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "```python\n" + "x" * 4000 + "\n```",
        },
        {"role": "user", "content": "[Observation]\ntail-obs"},
    ]
    obs_cost = estimate_context([split_case[-1]]).total
    expanded = keep_recent_by_tokens(split_case, obs_cost + 5)
    assert expanded >= 2
    first_kept = split_case[-expanded]
    assert first_kept["role"] == "assistant"
    assert not str(first_kept.get("content") or "").startswith("[Observation]")

    monkeypatch.setattr(
        comp_mod,
        "chat",
        lambda messages, cfg, **kwargs: {
            "content": _headings("tail"),
            "finish_reason": "stop",
        },
    )
    out = compact(history, get_config(), keep_recent=4, context_budget=_BUDGET)
    result_tail = out[3:]
    assert len(result_tail) > 6
    assert not (
        result_tail[0]["role"] == "user"
        and str(result_tail[0].get("content") or "").startswith("[Observation]")
    )


def test_chars_to_tokens_counts_cjk_as_one():
    cjk = _chars_to_tokens("中文测试" * 250)
    assert 900 <= cjk <= 1200
    assert _chars_to_tokens("a" * 4000) == 1000
    assert _chars_to_tokens("") == 0
    assert _chars_to_tokens("x") == 1
