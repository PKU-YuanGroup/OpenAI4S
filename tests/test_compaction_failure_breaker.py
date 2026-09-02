"""Regression contracts for CompactionPolicy failure-breaker and usage calibration."""

from __future__ import annotations

from types import SimpleNamespace

import openai4s.agent.runtime as runtime
from openai4s.agent.compaction import estimate_context
from openai4s.agent.models import ModelReply, RunState
from openai4s.agent.runtime import CompactionPolicy

_BUDGET = 10_000
_TRIGGER_RATIO = 0.75


def _trigger_tokens() -> int:
    return int(_BUDGET * _TRIGGER_RATIO)


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        compaction_dir="archive",
        context_window_tokens=_BUDGET,
        compaction_trigger_ratio=_TRIGGER_RATIO,
    )


def _policy(**kwargs) -> CompactionPolicy:
    return CompactionPolicy(
        _cfg(),
        context_budget_provider=lambda _state: _BUDGET,
        **kwargs,
    )


def _messages_with_total_at_least(min_tokens: int) -> list[dict]:
    content = "x" * max(4, min_tokens * 4)
    messages = [{"role": "user", "content": content}]
    while estimate_context(messages).total < min_tokens:
        messages[0]["content"] += "x" * 256
    return messages


def _messages_near_tokens(target: int) -> list[dict]:
    messages = _messages_with_total_at_least(max(1, target))
    # Shrink from above so the estimate sits close to ``target`` without
    # undershooting (the framing overhead is a few tokens).
    content = messages[0]["content"]
    while len(content) > 4 and (
        estimate_context([{"role": "user", "content": content[:-4]}]).total >= target
    ):
        content = content[:-4]
    messages[0]["content"] = content
    return messages


def _grow_to_total(messages: list[dict], min_tokens: int) -> None:
    while estimate_context(messages).total < min_tokens:
        messages[0]["content"] += "z" * 256


def _raise_compact(*_args, **_kwargs):
    raise RuntimeError("summary 4xx")


def test_consecutive_compaction_failures_open_the_circuit(monkeypatch):
    calls: list[int] = []

    def boom(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        calls.append(1)
        raise RuntimeError("summary 4xx")

    monkeypatch.setattr(runtime, "compact", boom)
    policy = _policy()
    state = RunState(_messages_with_total_at_least(_trigger_tokens() + 1))

    policy.prepare(state)
    policy.prepare(state)
    policy.prepare(state)

    assert len(calls) == 2
    assert state.metadata["compaction_circuit_open"] is True
    assert state.metadata["compaction_failure_streak"] == 2
    assert policy.circuit_open is True
    assert policy.failure_streak == 2


def test_failure_circuit_reopens_after_context_growth(monkeypatch):
    calls: list[int] = []

    def boom(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        calls.append(1)
        raise RuntimeError("summary 4xx")

    monkeypatch.setattr(runtime, "compact", boom)
    policy = _policy()
    messages = _messages_with_total_at_least(_trigger_tokens() + 1)
    state = RunState(messages)

    policy.prepare(state)
    policy.prepare(state)
    policy.prepare(state)
    assert len(calls) == 2
    open_total = policy.circuit_open_total
    assert open_total > 0

    needed = int(open_total * policy.circuit_retry_growth)
    _grow_to_total(messages, needed)
    assert estimate_context(messages).total >= needed

    policy.prepare(state)

    assert len(calls) == 3


def test_successful_compact_clears_failure_streak(monkeypatch):
    calls: list[int] = []

    def flaky(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("summary 4xx")
        return [{"role": "user", "content": "short"}]

    monkeypatch.setattr(runtime, "compact", flaky)
    policy = _policy()
    state = RunState(_messages_with_total_at_least(_trigger_tokens() + 1))

    policy.prepare(state)
    assert policy.failure_streak == 1
    assert policy.circuit_open is False

    policy.prepare(state)

    assert len(calls) == 2
    assert policy.failure_streak == 0
    assert policy.circuit_open is False
    assert state.metadata["compaction_failure_streak"] == 0
    assert state.metadata["compaction_circuit_open"] is False


def test_usage_calibration_can_trigger_compaction(monkeypatch):
    calls: list[int] = []

    def shrink(messages, cfg, **kwargs):
        del cfg, kwargs
        calls.append(len(messages))
        return [{"role": "user", "content": "short"}]

    monkeypatch.setattr(runtime, "compact", shrink)
    trigger = _trigger_tokens()
    messages = _messages_near_tokens(max(1, trigger // 2))
    estimate = estimate_context(messages).total
    assert estimate <= trigger
    assert estimate * 4 > trigger

    policy = _policy()
    state = RunState(messages)
    policy.prepare(state)
    assert calls == []
    sent = state.metadata["context_estimate_sent"]
    assert sent > 0

    state.last_reply = ModelReply(usage={"input_tokens": 4 * sent})
    policy.prepare(state)
    assert len(calls) == 1

    calls.clear()
    uncalibrated = _policy()
    plain = RunState(_messages_near_tokens(max(1, trigger // 2)))
    uncalibrated.prepare(plain)
    uncalibrated.prepare(plain)
    assert calls == []


def test_calibration_ratio_is_clamped(monkeypatch):
    monkeypatch.setattr(runtime, "compact", _raise_compact)
    trigger = _trigger_tokens()
    half = _messages_near_tokens(max(1, trigger // 2))

    high_policy = _policy()
    high_state = RunState(list(half))
    high_policy.prepare(high_state)
    sent = high_state.metadata["context_estimate_sent"]
    assert sent > 0
    high_state.last_reply = ModelReply(usage={"input_tokens": 100 * sent})
    high_policy.prepare(high_state)
    assert high_state.metadata["context_estimate_calibration"] == 8.0

    low_policy = _policy()
    low_state = RunState(list(half))
    low_policy.prepare(low_state)
    sent = low_state.metadata["context_estimate_sent"]
    assert sent > 0
    low_state.last_reply = ModelReply(usage={"input_tokens": sent / 100})
    low_policy.prepare(low_state)
    assert low_state.metadata["context_estimate_calibration"] == 0.5
