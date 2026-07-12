"""Direct contracts for in-kernel host.llm requests."""

from __future__ import annotations

import pytest

import openai4s.host_dispatch as dispatcher_module
from openai4s.config import Config, LLMConfig
from openai4s.host.llm import LLMService
from openai4s.host_dispatch import HostDispatcher


def _config(tmp_path):
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(
            provider="deepseek",
            api_key="test-key",
            model="science-model",
        ),
    )


def test_single_call_forwards_exact_options_and_normalizes_content(tmp_path):
    config = _config(tmp_path)
    calls = []

    def chat(messages, llm_config, **options):
        calls.append((messages, llm_config, options))
        return {"content": "analysis result"}

    service = LLMService(config, chat_call=chat)
    messages = [{"role": "user", "content": "analyze"}]
    result = service.complete(
        {
            "messages": messages,
            "max_tokens": 123,
            "temperature": 0.2,
        }
    )

    assert result == "analysis result"
    assert calls == [
        (
            messages,
            config.llm,
            {"max_tokens": 123, "temperature": 0.2},
        )
    ]

    assert service.complete({"messages": None}) == "analysis result"
    assert calls[-1][0] == []
    service.chat_call = lambda *_a, **_kw: {}
    assert service.complete({}) == ""


def test_batch_preserves_order_and_bounds_requested_concurrency(tmp_path):
    records = []

    class Executor:
        def __init__(self, max_workers):
            records.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def map(self, function, items):
            return [function(item) for item in items]

    config = _config(tmp_path)
    service = LLMService(
        config,
        chat_call=lambda messages, _cfg, **_kw: {"content": messages[0]["content"]},
        fanout_cap=3,
        executor_factory=Executor,
    )
    batch = [
        {"messages": [{"role": "user", "content": value}]}
        for value in ("a", "b", "c", "d")
    ]

    assert service.complete({"batch": batch, "max_concurrency": 9}) == [
        "a",
        "b",
        "c",
        "d",
    ]
    assert records == [3]

    assert service.complete({"batch": batch[:2], "max_concurrency": -4}) == [
        "a",
        "b",
    ]
    assert records == [3, 1]
    assert service.complete({"batch": None}) == []
    assert records == [3, 1]


def test_fanout_cap_and_one_call_are_late_bound(tmp_path):
    cap = {"value": 4}
    calls = []
    workers = []

    class Executor:
        def __init__(self, max_workers):
            workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def map(self, function, items):
            return [function(item) for item in items]

    service = LLMService(
        _config(tmp_path),
        one_call=lambda spec: calls.append(spec["value"]) or spec["value"],
        fanout_cap=lambda: cap["value"],
        executor_factory=Executor,
    )
    batch = [{"value": value} for value in (1, 2, 3)]
    assert service.complete({"batch": batch}) == [1, 2, 3]
    assert workers == [3]

    cap["value"] = 1
    assert service.complete({"batch": batch}) == [1, 2, 3]
    assert workers == [3, 1]
    assert calls == [1, 2, 3, 1, 2, 3]


def test_model_projection_reads_live_configuration(tmp_path):
    config = _config(tmp_path)
    service = LLMService(config, chat_call=lambda *_a, **_kw: {})

    assert service.current_model() == "science-model"
    assert service.list_models() == [
        {
            "id": "science-model",
            "context_window": config.context_window_tokens,
            "default": True,
        }
    ]

    config.llm.model = "new-model"
    config.context_window_tokens = 9876
    assert service.current_model() == "new-model"
    assert service.list_models()[0] == {
        "id": "new-model",
        "context_window": 9876,
        "default": True,
    }

    replacement = _config(tmp_path)
    replacement.llm.model = "replacement-model"
    dynamic = {"config": config}
    dynamic_service = LLMService(
        lambda: dynamic["config"],
        chat_call=lambda *_a, **_kw: {},
    )
    dynamic["config"] = replacement
    assert dynamic_service.current_model() == "replacement-model"


def test_dispatcher_keeps_late_chat_and_private_one_call_compatibility(
    tmp_path, monkeypatch
):
    dispatcher = HostDispatcher(_config(tmp_path))
    calls = []

    def fake_chat(messages, config, **options):
        calls.append((messages, config, options))
        return {"content": "patched"}

    monkeypatch.setattr(dispatcher_module, "chat", fake_chat)
    assert dispatcher._m_llm({"messages": []}) == "patched"
    assert calls[0][1] is dispatcher.cfg.llm

    monkeypatch.setattr(
        dispatcher,
        "_one_llm",
        lambda spec: f"override:{spec['value']}",
    )
    dispatcher.LLM_FANOUT_CAP = 1
    assert dispatcher._m_llm(
        {"batch": [{"value": "a"}, {"value": "b"}], "max_concurrency": 9}
    ) == ["override:a", "override:b"]

    replacement = _config(tmp_path)
    replacement.llm.model = "replacement-model"
    dispatcher.cfg = replacement
    assert dispatcher._m_current_model() == "replacement-model"


def test_invalid_concurrency_type_keeps_hard_failure(tmp_path):
    service = LLMService(
        _config(tmp_path),
        chat_call=lambda *_a, **_kw: {"content": "ok"},
    )
    with pytest.raises(TypeError):
        service.complete({"batch": [{}], "max_concurrency": "many"})


def test_single_and_batch_item_failures_remain_hard(tmp_path):
    def fail(messages, _config, **_options):
        raise RuntimeError(messages[0]["content"])

    service = LLMService(_config(tmp_path), chat_call=fail)
    with pytest.raises(RuntimeError, match="single failed"):
        service.complete({"messages": [{"role": "user", "content": "single failed"}]})
    with pytest.raises(RuntimeError, match="batch failed"):
        service.complete(
            {"batch": [{"messages": [{"role": "user", "content": "batch failed"}]}]}
        )
