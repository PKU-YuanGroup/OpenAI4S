"""Regression contracts for CompactionPolicy failure-breaker and usage calibration."""

from __future__ import annotations

from types import SimpleNamespace

import openai4s.agent.compaction as comp_mod
import openai4s.agent.runtime as runtime
from openai4s.agent.compaction import estimate_context
from openai4s.agent.models import ModelReply, RunState
from openai4s.agent.runtime import CompactionPolicy
from openai4s.llm.capabilities import normalize_usage

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


# ---------------------------------------------------------------------------
# Review follow-ups: a torn host blob is not a compaction failure, an
# Anthropic cached prompt still calibrates, the skip log names the breaker
# that tripped, and a failing archive sink trips it.
# ---------------------------------------------------------------------------


def _complete_handoff() -> str:
    return "\n\n".join(f"## {field}\n- ok." for field in comp_mod.HANDOFF_FIELDS)


def _rounds(count: int, fill: str) -> list[dict]:
    messages: list[dict] = []
    for index in range(count):
        messages.append(
            {"role": "assistant", "content": f"```python\nprint({index})\n```"}
        )
        messages.append({"role": "user", "content": f"[Observation]\n{index}\n{fill}"})
    return messages


def test_a_torn_archive_blob_does_not_trip_the_breaker(monkeypatch, tmp_path):
    """A truncated ``<sha>.json`` under the host compaction directory used to
    fail every externalization *and* every compaction of that history (the
    policy re-externalized on its way into ``compact()``), so the breaker
    opened in two turns while the oversized output stayed in context and a
    fresh run against the same data dir tripped identically."""
    big = "[Observation]\n" + ("q" * 20_000)
    digest = comp_mod.hashlib.sha256(
        comp_mod._json_text(big).encode("utf-8")
    ).hexdigest()
    torn = tmp_path / "blobs" / digest[:2] / f"{digest}.json"
    torn.parent.mkdir(parents=True)
    torn.write_text('{"sha256": "', "utf-8")
    monkeypatch.setattr(
        comp_mod,
        "chat",
        lambda *a, **k: {"content": _complete_handoff(), "finish_reason": "stop"},
    )
    cfg = _cfg()
    cfg.compaction_dir = str(tmp_path)
    cfg.llm = SimpleNamespace(
        provider="deepseek", model=None, base_url=None, max_tokens=4096
    )
    logs: list[str] = []
    policy = CompactionPolicy(
        cfg, context_budget_provider=lambda _s: _BUDGET, log=logs.append
    )
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "```python\nprint('big')\n```"},
        {"role": "user", "content": big},
        *_rounds(12, "y" * 4_000),
    ]
    state = RunState(messages)
    assert estimate_context(messages).total > _trigger_tokens()

    for _ in range(3):
        policy.prepare(state)

    assert policy.failure_streak == 0
    assert policy.circuit_open is False
    assert any(line.startswith("[compacted]") for line in logs), logs
    assert not any("durable archive failed" in line for line in logs), logs
    assert comp_mod.load_archived_content(tmp_path, digest) == big


def test_an_anthropic_cached_prompt_still_calibrates(monkeypatch):
    """Anthropic's ``input_tokens`` is the uncached remainder only.  With 93%
    of a prompt cached the ratio collapsed to the 0.5 floor and compaction
    fired at 1.5x the window, after the provider had rejected the request.
    ``normalize_usage`` folds the cache counters back in, so the canonical
    count is the whole prompt on every wire."""
    calls: list[int] = []

    def shrink(messages, cfg, **kwargs):
        del cfg, kwargs
        calls.append(len(messages))
        return [{"role": "user", "content": "short"}]

    monkeypatch.setattr(runtime, "compact", shrink)
    messages = _messages_near_tokens(max(1, _trigger_tokens() // 2))
    policy = _policy()
    state = RunState(messages)
    policy.prepare(state)
    sent = state.metadata["context_estimate_sent"]
    actual = 4 * sent
    cached = int(actual * 0.93)
    state.last_reply = ModelReply(
        usage=normalize_usage(
            {
                "input_tokens": actual - cached,
                "cache_read_input_tokens": cached,
                "output_tokens": 1,
            },
            "claude",
        )
    )
    policy.prepare(state)

    assert calls == [len(messages)]
    assert state.metadata["context_estimate_calibration"] == 4.0


def test_the_skip_log_names_the_breaker_that_tripped(monkeypatch):
    """One low-yield compaction followed by two failures opens the breaker on
    the failure path; every later skip used to report "1 low-yield attempts",
    a count that could not have opened it."""
    steps = iter(["low_yield", "fail", "fail", "skip", "skip"])

    def scripted(messages, cfg, **kwargs):
        del cfg, kwargs
        if next(steps) == "fail":
            raise RuntimeError("summary 4xx")
        return list(messages)  # no gain: a low-yield attempt

    monkeypatch.setattr(runtime, "compact", scripted)
    logs: list[str] = []
    policy = _policy(log=logs.append)
    state = RunState(_messages_with_total_at_least(_trigger_tokens() + 1))
    for _ in range(5):
        policy.prepare(state)

    assert policy.circuit_open is True
    assert policy.low_yield_streak == 1 and policy.failure_streak == 2
    skips = [line for line in logs if "circuit breaker open after" in line]
    assert len(skips) == 2
    assert all("2 consecutive failures" in line for line in skips), skips
    assert not any("low-yield" in line for line in skips), skips
    assert state.metadata["compaction_circuit_reason"] == "2 consecutive failures"


def test_a_failing_archive_sink_trips_the_breaker(monkeypatch):
    """The streak was zeroed right after ``compact()`` returned, before the
    deferred sink replay, so a sink that kept failing never opened the
    breaker and bought a fresh summary (a real LLM call) every turn."""
    summaries: list[int] = []

    def compact_with_record(messages, cfg, **kwargs):
        del cfg
        summaries.append(len(messages))
        kwargs["archive_sink"]({"handoff": "h", "compacted_messages": []})
        return [{"role": "user", "content": "short"}]

    def sink(_payload):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(runtime, "compact", compact_with_record)
    policy = _policy(archive_sink=sink)
    state = RunState(_messages_with_total_at_least(_trigger_tokens() + 1))
    for _ in range(4):
        policy.prepare(state)

    assert policy.circuit_open is True
    assert policy.circuit_reason == "2 consecutive failures"
    assert len(summaries) == 2, "the breaker did not stop the summaries"
    assert state.metadata["compaction_circuit_open"] is True


def test_a_cancelled_compaction_is_not_a_failure(monkeypatch):
    """Stop pressed mid-compaction returns the live context untouched and
    leaves the breaker alone: a cancellation is not a consecutive failure."""

    def cancelled(messages, cfg, **kwargs):
        del messages, cfg
        assert kwargs["should_cancel"]() is True
        raise comp_mod.CompactionCancelled("run cancelled between summary chunks")

    monkeypatch.setattr(runtime, "compact", cancelled)
    logs: list[str] = []
    policy = _policy(should_cancel=lambda: True, log=logs.append)
    state = RunState(_messages_with_total_at_least(_trigger_tokens() + 1))

    prepared = policy.prepare(state)

    assert list(prepared) == state.messages
    assert policy.failure_streak == 0 and policy.circuit_open is False
    assert any(line.startswith("[compaction cancelled]") for line in logs), logs
