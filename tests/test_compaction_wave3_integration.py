"""Cross-wave contract: a restored handoff note equals the one compact() built.

Wave 2 owns the in-memory note, Wave 3 owns rebuilding it from the Action
Ledger, and the archive payload between them carries only the bare handoff
body.  Nothing in either wave's own tests compares the two, so the framing
line that tells the model "this is compacted history" can go missing across
a restart while every lane stays green.
"""

from __future__ import annotations

from types import SimpleNamespace

import openai4s.agent.compaction as comp_mod
from openai4s.agent.compaction import COMPACTION_NOTE_PREFIX, compact
from openai4s.agent.ledger import reduce_action_groups

_BUDGET = 4_000


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        compaction_dir=None,
        context_window_tokens=_BUDGET,
        compaction_trigger_ratio=0.75,
        llm=SimpleNamespace(max_tokens=8192),
    )


def _handoff_body() -> str:
    return "\n\n".join(f"## {field}\n- kept." for field in comp_mod.HANDOFF_FIELDS)


def _history(rounds: int) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    for index in range(rounds):
        messages.append({"role": "assistant", "content": f"```python\np({index})\n```"})
        messages.append({"role": "user", "content": f"[Observation]\nrow-{index}"})
    return messages


def _live_note(monkeypatch) -> dict:
    monkeypatch.setattr(
        comp_mod,
        "chat",
        lambda *_a, **_k: {"content": _handoff_body(), "finish_reason": "stop"},
    )
    result = compact(_history(40), _cfg(), context_budget=_BUDGET)
    return next(m for m in result if m.get("compaction_handoff"))


def _restored_note(handoff_body: str) -> dict:
    groups = [
        {
            "ordinal": 0,
            "kind": "user",
            "assistant_message": {"role": "user", "content": "task"},
        },
        {
            "ordinal": 1,
            "kind": "code",
            "assistant_message": {
                "role": "assistant",
                "content": "```python\np(0)\n```",
            },
            "events": [
                {
                    "type": "observation",
                    "result": {"observation": "[Observation]\nrow-0"},
                }
            ],
        },
        {
            "ordinal": 2,
            "kind": "compaction",
            "assistant_message": None,
            "events": [
                {
                    "type": "compaction",
                    "result": {
                        "handoff": handoff_body,
                        "covered_through_ordinal": 1,
                        "archive_id": "a1",
                    },
                }
            ],
        },
    ]
    return next(m for m in reduce_action_groups(groups) if m.get("compaction_handoff"))


def test_restored_note_matches_the_in_memory_note(monkeypatch):
    live = _live_note(monkeypatch)
    body = live["content"].split(COMPACTION_NOTE_PREFIX, 1)[1]
    restored = _restored_note(body)
    assert restored["content"] == live["content"]
    assert restored["role"] == live["role"] == "system"


def test_restored_note_is_not_double_prefixed():
    body = _handoff_body()
    once = _restored_note(body)["content"]
    twice = _restored_note(once)["content"]
    assert once.count(COMPACTION_NOTE_PREFIX) == 1
    assert twice == once
