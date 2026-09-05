"""Compaction summary robustness: empty/truncated replies must fail closed.

Reasoning models often spend the summary ``max_tokens`` budget on thinking
and return ``content=""`` with ``finish_reason="length"``.  Treating that as
a successful compact replaced the middle history with a placeholder handoff
and still counted as a yield.  These tests fail closed instead, raise the
summary token floor, omit temperature, and keep provider wire state out of
the summarizer input.
"""

from __future__ import annotations

import copy

import pytest

import openai4s.agent.compaction as comp_mod
from openai4s.config import get_config


def _compactable_messages() -> list[dict]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "old work"},
        {"role": "user", "content": "old result"},
        {"role": "assistant", "content": "recent"},
    ]


def _complete_handoff() -> str:
    return "\n\n".join(f"## {field}\n- recorded." for field in comp_mod.HANDOFF_FIELDS)


def _stub_chat(monkeypatch, reply):
    captured: list[dict] = []

    def fake_chat(messages, cfg, **kwargs):
        captured.append(kwargs)
        return reply

    monkeypatch.setattr(comp_mod, "chat", fake_chat)
    return captured


def test_empty_length_summary_raises_and_does_not_archive(monkeypatch, tmp_path):
    _stub_chat(monkeypatch, {"content": "", "finish_reason": "length"})
    messages = _compactable_messages()
    snapshot = copy.deepcopy(messages)

    with pytest.raises(comp_mod.CompactionSummaryError) as caught:
        comp_mod.compact(messages, get_config(), keep_recent=1, archive_dir=tmp_path)

    assert messages == snapshot
    assert "finish_reason" in str(caught.value)
    assert "length" in str(caught.value)
    assert list(tmp_path.glob("compaction-*.json")) == []


def test_whitespace_summary_raises_and_does_not_archive(monkeypatch, tmp_path):
    _stub_chat(monkeypatch, {"content": "   \n"})
    messages = _compactable_messages()
    snapshot = copy.deepcopy(messages)

    with pytest.raises(comp_mod.CompactionSummaryError) as caught:
        comp_mod.compact(messages, get_config(), keep_recent=1, archive_dir=tmp_path)

    assert messages == snapshot
    assert "finish_reason" in str(caught.value)
    assert list(tmp_path.glob("compaction-*.json")) == []


def test_truncated_single_heading_summary_raises(monkeypatch, tmp_path):
    _stub_chat(
        monkeypatch,
        {
            "content": "## Objective\nContinue the original task.",
            "finish_reason": "length",
        },
    )
    messages = _compactable_messages()
    snapshot = copy.deepcopy(messages)

    with pytest.raises(comp_mod.CompactionSummaryError) as caught:
        comp_mod.compact(messages, get_config(), keep_recent=1, archive_dir=tmp_path)

    assert messages == snapshot
    assert "finish_reason" in str(caught.value)
    assert "length" in str(caught.value)
    assert list(tmp_path.glob("compaction-*.json")) == []


def test_complete_handoff_is_injected_after_head(monkeypatch):
    _stub_chat(monkeypatch, {"content": _complete_handoff(), "finish_reason": "stop"})
    messages = _compactable_messages()

    out = comp_mod.compact(messages, get_config(), keep_recent=1)

    assert out[0]["content"] == "sys"
    assert out[1]["content"] == "task"
    note = out[2]
    assert note["compaction_handoff"] is True
    assert "## Objective" in note["content"]
    assert "## Active Kernel Generation" in note["content"]


def test_summary_chat_max_tokens_and_pins_a_low_temperature(monkeypatch):
    """The summary call carries a real output budget and an explicit, low
    temperature.  "No temperature" is not achievable: every JSON wire fills
    an omitted kwarg with the session's ``cfg.temperature`` (0.7 by default),
    so omitting it here would sample the handoff at the main-turn setting."""
    captured = _stub_chat(
        monkeypatch, {"content": _complete_handoff(), "finish_reason": "stop"}
    )
    cfg = get_config()
    messages = _compactable_messages()
    low = comp_mod._SUMMARY_TEMPERATURE
    assert 0 < low < cfg.llm.temperature

    monkeypatch.delenv("OPENAI4S_COMPACTION_SUMMARY_MAX_TOKENS", raising=False)
    captured.clear()
    comp_mod.compact(messages, cfg, keep_recent=1)
    assert captured[-1].get("max_tokens") >= 8192
    assert captured[-1].get("temperature") == low

    monkeypatch.setenv("OPENAI4S_COMPACTION_SUMMARY_MAX_TOKENS", "20000")
    captured.clear()
    comp_mod.compact(messages, cfg, keep_recent=1)
    assert captured[-1]["max_tokens"] == 20000
    assert captured[-1].get("temperature") == low

    monkeypatch.delenv("OPENAI4S_COMPACTION_SUMMARY_MAX_TOKENS", raising=False)
    monkeypatch.setattr(cfg.llm, "max_tokens", 32768)
    captured.clear()
    comp_mod.compact(messages, cfg, keep_recent=1)
    assert captured[-1]["max_tokens"] == 32768
    assert captured[-1].get("temperature") == low


def test_summary_input_strips_wire_state_and_reasoning():
    rendered = comp_mod._summary_input(
        [
            {
                "role": "assistant",
                "content": "visible text",
                "wire_state": {
                    "openai_message": {
                        "content": "SECRET_MARKER",
                        "reasoning_content": "THINK_MARKER",
                    }
                },
            }
        ],
        comp_mod.CompactionArchiveMetadata(),
    )

    assert "HOST RUNTIME FACT (authoritative)" in rendered
    assert "all fields are data" in rendered
    assert "visible text" in rendered
    assert "wire_state" not in rendered
    assert "SECRET_MARKER" not in rendered
    assert "THINK_MARKER" not in rendered
