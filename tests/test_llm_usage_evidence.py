"""Metering must retain provider evidence before display defaults are filled."""

import json
import threading
from contextlib import closing
from dataclasses import replace

import pytest

from openai4s import llm
from openai4s.agent.models import ModelReply
from openai4s.agent.runtime import ChatModel
from openai4s.config import LLMConfig
from openai4s.llm.models import LLMDeadlineExceeded, LLMError
from openai4s.llm.usage import RawUsage, copy_usage, measured_total, measured_usage
from openai4s.server.auto_budget import token_upper_bound, verifiable_token_usage

pytestmark = pytest.mark.stubbed_backend


def cfg(provider="chatgpt"):
    return LLMConfig(provider=provider, api_key="test-key", model="test-model")


@pytest.mark.parametrize("bad", [None, False, -1, 1.5, "0", "17", float("nan")])
def test_normalization_defaults_are_not_metering_evidence(bad):
    usage = llm.normalize_usage(
        {"prompt_tokens": bad, "completion_tokens": 0}, "chatgpt"
    )
    assert usage["completion_tokens"] == 0  # public compatibility
    assert measured_total(usage) is None
    assert (
        verifiable_token_usage(ModelReply.from_mapping({"usage": usage}).usage) is None
    )
    assert (
        measured_total(copy_usage(usage, {"input_tokens": 0, "output_tokens": 0}))
        is None
    )
    cost = llm.CostMetadata(input_per_million=1, output_per_million=2, currency="USD")
    assert llm.calculate_usage_cost_usd(usage, cost) is None


@pytest.mark.parametrize(
    "raw,total",
    [
        ({}, None),
        ({"prompt_tokens": 0, "completion_tokens": 0}, 0),
        ({"prompt_tokens": 7, "completion_tokens": 3}, 10),
    ],
)
def test_real_facade_keeps_usage_evidence_and_json_shape(monkeypatch, raw, total):
    monkeypatch.setattr(
        llm.transport,
        "post_json",
        lambda *_args: {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": raw,
        },
    )
    usage = llm.chat([{"role": "user", "content": "hello"}], cfg())["usage"]
    assert measured_total(usage) == total
    assert usage.evidence.raw == raw
    assert set(json.loads(json.dumps(usage))) == {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_read",
        "cache_write",
        "reasoning_tokens",
    }


@pytest.mark.parametrize("final_usage", [False, True])
def test_anthropic_initial_output_zero_is_not_a_finished_measurement(
    monkeypatch, final_usage
):
    def stream(_url, _body, _headers, _timeout, event):
        event(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 9, "output_tokens": 0}},
            }
        )
        if final_usage:
            event(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 4},
                }
            )
        raise LLMDeadlineExceeded("fixture deadline")

    monkeypatch.setattr(llm.transport, "post_sse", stream)
    with pytest.raises(LLMDeadlineExceeded) as caught:
        llm.chat(
            [{"role": "user", "content": "hello"}],
            cfg("claude"),
            on_delta=lambda _: None,
        )
    assert measured_total(caught.value.usage) == (13 if final_usage else None)


def test_openai_finish_continues_to_final_usage_then_stops(monkeypatch):
    seen = []

    def stream(_url, _body, _headers, _timeout, event):
        assert (
            event({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
            is not True
        )
        seen.append("finish")
        assert (
            event(
                {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 2}}
            )
            is True
        )
        seen.append("usage")

    monkeypatch.setattr(llm.transport, "post_sse", stream)
    reply = llm.chat(
        [{"role": "user", "content": "hello"}], cfg(), on_delta=lambda _: None
    )
    assert seen == ["finish", "usage"]
    assert measured_total(reply["usage"]) == 13


@pytest.mark.parametrize("known", [False, True])
def test_failed_late_call_accounts_once_after_new_turn_clears_stop(known):
    entered, release, accounted = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    stop = threading.Event()
    replies = []

    class Cancel:
        def cancelled(self):
            return stop.is_set()

    def provider(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        error = LLMDeadlineExceeded("old call deadline")
        error.usage = llm.normalize_usage(
            RawUsage({"prompt_tokens": 7, "completion_tokens": 2}, final=known),
            "chatgpt",
        )
        raise error

    def account(reply):
        replies.append(reply)
        accounted.set()

    model = ChatModel(
        cfg(),
        provider,
        cancellation=Cancel(),
        abandoned_reply=account,
        drain_cancelled_stream=True,
    )
    result = []
    owner = threading.Thread(
        target=lambda: result.append(model.complete([], lambda _: None))
    )
    owner.start()
    assert entered.wait(1)
    stop.set()
    owner.join(1)
    assert not owner.is_alive()
    assert result[0]["finish_reason"] == "cancelled"
    stop.clear()
    release.set()
    assert accounted.wait(1)
    assert len(replies) == 1
    assert measured_total(replies[0]["usage"]) == (9 if known else None)


def test_total_timeout_env_validation_and_dataclass_inheritance(monkeypatch):
    monkeypatch.setenv("OPENAI4S_LLM_TOTAL_TIMEOUT", "42")
    parent = cfg()
    assert parent.total_timeout_s == 42
    assert replace(parent, model="child").total_timeout_s == 42
    monkeypatch.setenv("OPENAI4S_LLM_TOTAL_TIMEOUT", "nan")
    with pytest.raises(ValueError):
        cfg()


def test_metering_a_hostile_usage_object_cannot_fail_the_call_it_meters(tmp_path):
    """Every failure in ``record_session_llm_usage`` is swallowed -- including
    reading the evidence off the caller's usage object, which is metering work
    like the rest and used to sit above the ``try``."""

    from openai4s.storage.governance import record_session_llm_usage
    from openai4s.store import Store

    class Hostile(dict):
        def items(self):
            raise TypeError("counters are not readable")

    with closing(Store(tmp_path / "hostile.db")) as store:
        user = store.team.create_user(
            username="meter", password="fake-password", role="member"
        )
        root = store.new_frame(kind="turn", project_id="p")
        store.team.set_session_owner(root, user["id"], project_id="p")
        record_session_llm_usage(store, root, Hostile({"input_tokens": 1}))


def test_responses_without_wire_output_cap_has_no_budget_ceiling():
    responses = next(
        name for name, spec in llm.PROVIDERS.items() if spec["wire"] == "responses"
    )
    assert (
        token_upper_bound(
            cfg(responses), messages=[{"role": "user", "content": "hi"}], max_tokens=5
        )
        is None
    )


@pytest.mark.parametrize("known", [False, True])
def test_team_quota_never_treats_missing_measurement_as_free(tmp_path, known):
    from openai4s.storage.governance import QuotaExceeded, record_session_llm_usage
    from openai4s.store import Store

    with closing(Store(tmp_path / "usage.db")) as store:
        user = store.team.create_user(
            username="meter", password="fake-password", role="member"
        )
        root = store.new_frame(kind="turn", project_id="p")
        store.team.set_session_owner(root, user["id"], project_id="p")
        store.governance.set_quota(
            scope="user",
            scope_id=user["id"],
            kind="llm_output_tokens",
            limit_amount=100,
            window="day",
        )
        raw = {"prompt_tokens": 0, "completion_tokens": 0} if known else {}
        usage = llm.normalize_usage(raw, "chatgpt")
        record_session_llm_usage(store, root, usage)
        if known:
            store.governance.check_quota(
                user_id=user["id"], project_id="p", kind="llm_output_tokens"
            )
        else:
            with pytest.raises(QuotaExceeded, match="unknown"):
                store.governance.check_quota(
                    user_id=user["id"], project_id="p", kind="llm_output_tokens"
                )
            rows = store.governance.usage_summary(user_id=user["id"])
            assert {row["kind"] for row in rows} == {
                "llm_input_tokens_unknown",
                "llm_output_tokens_unknown",
            }
            assert all(row["total"] == 1 and row["events"] == 1 for row in rows)


@pytest.mark.parametrize("kind", ["deadline", "size"])
def test_local_resource_errors_use_safe_bilingual_projection(kind):
    from openai4s.llm.models import LLMResponseTooLarge, llm_failure_code
    from openai4s.server.gateway import SessionRunner

    error = (LLMDeadlineExceeded if kind == "deadline" else LLMResponseTooLarge)(
        "private upstream body"
    )
    assert llm_failure_code(error) == (
        "llm_deadline_exceeded" if kind == "deadline" else "llm_response_too_large"
    )
    for language in ("zh", "en"):
        friendly = SessionRunner._friendly_error(error, language=language)
        assert friendly and "private" not in friendly


@pytest.mark.parametrize("wire", ["review", "scientific"])
@pytest.mark.parametrize(
    "raw,total", [({}, None), ({"input_tokens": 7, "output_tokens": 3}, 10)]
)
@pytest.mark.parametrize("malformed", [False, True])
def test_reviewer_projection_and_parse_errors_preserve_original_usage(
    wire, raw, total, malformed
):
    from openai4s.review import review_evidence
    from openai4s.scientific_reviewer import review_snapshot

    invoke = review_evidence if wire == "review" else review_snapshot

    def chat_call(*_args, **_kwargs):
        return {
            "content": (
                "invalid"
                if malformed
                else json.dumps(
                    {"verdict": "pass", "summary": "ok", "issues": [], "findings": []}
                )
            ),
            "usage": raw,
        }

    if malformed:
        with pytest.raises(Exception) as caught:
            invoke({}, cfg(), chat_call=chat_call)
        usage = caught.value.usage
    else:
        usage = invoke({}, cfg(), chat_call=chat_call)["usage"]
    assert measured_total(usage) == total


@pytest.mark.parametrize("cache", [None, "bad", -1, False])
def test_invalid_anthropic_cache_cannot_certify_zero_input(cache):
    usage = llm.normalize_usage(
        {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": cache},
        "claude",
    )
    assert measured_total(usage) is None
    assert "input_tokens" not in measured_usage(usage)


def test_renormalizing_a_cache_folding_wire_does_not_fold_twice():
    """``input_excludes_cache`` folds cache tokens into ``input_tokens``. The
    idempotence guard has to return before ``result`` is derived, or a second
    pass folds them again and the public dict stops agreeing with its own
    evidence (and with its own ``total_tokens``)."""

    first = llm.normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 7,
        },
        "claude",
    )
    assert first["input_tokens"] == 607 and first["total_tokens"] == 617
    second = llm.normalize_usage(first, "claude")
    assert dict(second) == dict(first)
    assert measured_usage(second) == measured_usage(first)


def test_a_later_alias_still_measures_a_counter_its_first_path_could_not():
    """``_token_value`` falls through an unusable path to the next alias; the
    metering loop must too, or the public dict and the counters disagree about
    one payload."""

    usage = llm.normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": None,
            "cache_read_tokens": 42,
            "cache_creation_input_tokens": 0,
        },
        "claude",
    )
    assert usage["cache_read"] == 42
    assert measured_usage(usage)["cache_read"] == 42
    assert measured_total(usage) == 162


def test_raw_total_includes_provider_reasoning_and_renormalizing_retains_unknown():
    usage = llm.normalize_usage(
        {
            "promptTokenCount": 30,
            "candidatesTokenCount": 11,
            "thoughtsTokenCount": 4,
            "totalTokenCount": 45,
        },
        "gemini",
    )
    assert measured_total(usage) == 45
    unknown = llm.normalize_usage({}, "chatgpt")
    assert measured_total(llm.normalize_usage(unknown, "chatgpt")) is None


@pytest.mark.parametrize(
    "provider,body",
    [
        (
            "chatgpt",
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        ),
        ("claude", {"usage": {"input_tokens": 7, "output_tokens": 3}}),
        (
            "gemini",
            {
                "candidates": [],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
            },
        ),
    ],
)
def test_malformed_json_reply_keeps_successfully_received_usage(
    monkeypatch, provider, body
):
    monkeypatch.setattr(llm.transport, "post_json", lambda *_args: body)
    with pytest.raises(LLMError) as caught:
        llm.chat([{"role": "user", "content": "hello"}], cfg(provider))
    assert measured_total(caught.value.usage) == 10
    assert not caught.value.llm_not_started


def test_pending_accounting_keeps_scope_bounded_even_after_network_finishes(
    monkeypatch,
):
    from openai4s.agent import runtime

    pending = []
    budget = runtime._DetachedCallBudget(128, per_scope_limit=4)
    monkeypatch.setattr(runtime, "_PROVIDER_CALL_BUDGET", budget)
    monkeypatch.setattr(
        runtime._LATE_ACCOUNTING,
        "submit",
        lambda sink, reply: pending.append((sink, reply)),
    )
    calls = []

    def opaque_provider(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError("legacy provider failed after send")

    model = ChatModel(
        cfg(), opaque_provider, call_scope="old", abandoned_reply=lambda _: None
    )
    for _ in range(4):
        with pytest.raises(TimeoutError):
            model.complete([], lambda _: None)
    assert budget.outstanding("old") == 4
    with pytest.raises(RuntimeError, match="still billing"):
        model.complete([], lambda _: None)
    assert len(calls) == 4
    for sink, reply in pending:
        sink(reply)
    assert budget.outstanding("old") == 0


def test_pre_send_denial_creates_no_unknown_accounting():
    from openai4s.storage.governance import QuotaExceeded

    charged = []

    def deny(*_args, **_kwargs):
        raise QuotaExceeded(
            "quota", scope="user", kind="llm_output_tokens", window="day"
        )

    model = ChatModel(cfg(), deny, abandoned_reply=charged.append)
    with pytest.raises(QuotaExceeded):
        model.complete([], lambda _: None)
    assert charged == []


@pytest.mark.parametrize("details", ["malformed", {"cached_tokens": "bad"}])
def test_ledger_cost_keeps_invalid_cache_evidence(monkeypatch, details):
    from types import SimpleNamespace

    from openai4s.agent.ledger import RuntimeActionLedger

    cost = llm.CostMetadata(
        input_per_million=1,
        output_per_million=2,
        cache_read_per_million=0.1,
        currency="USD",
    )
    monkeypatch.setattr(
        "openai4s.agent.ledger.get_model_capabilities",
        lambda *_args: SimpleNamespace(cost=cost),
    )
    usage = llm.normalize_usage(
        {"prompt_tokens": 10, "completion_tokens": 0, "prompt_tokens_details": details},
        "chatgpt",
    )
    ledger = RuntimeActionLedger(object(), "root", "turn", provider="chatgpt")
    counters, charge = ledger._reply_accounting(
        ModelReply.from_mapping({"usage": usage})
    )
    assert counters["input_tokens"] == 10
    assert charge is None


def test_config_repr_never_prints_credentials():
    config = LLMConfig(provider="ark", api_key="test-private-value")
    assert "test-private-value" not in repr(config)
