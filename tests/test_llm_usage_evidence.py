"""Metering must retain provider evidence before display defaults are filled."""

import json
import threading
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

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
    "attested,total",
    [
        (None, None),  # the reply carried no usage at all
        (True, 10),  # the adapter attested the counters it received
        (False, None),  # the same counters, unattested -- refused, not charged
    ],
)
@pytest.mark.parametrize("malformed", [False, True])
def test_reviewer_projection_and_parse_errors_preserve_original_usage(
    wire, attested, total, malformed
):
    """The injected ``chat_call`` is this path's attestation seam: ``review_evidence``
    and ``review_snapshot`` call it directly, never through ``llm.chat``. So what
    the projection must carry through both the success return and the parse-error
    return is the adapter's *evidence*, not its display keys -- an attested
    measurement stays chargeable, and the identical counters from an unattested
    adapter stay refused."""

    from openai4s.review import review_evidence
    from openai4s.scientific_reviewer import review_snapshot

    invoke = review_evidence if wire == "review" else review_snapshot
    counters = {"input_tokens": 7, "output_tokens": 3}
    if attested is None:
        raw = {}
    elif attested:
        raw = llm.normalize_usage(counters, "chatgpt")
        # Paired negative: the very same counters as a bare dict are refused, so
        # the assertion below turns on the attestation and not on the key names.
        assert measured_total(dict(counters)) is None
    else:
        raw = dict(counters)

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
    if attested is not None:
        assert usage["input_tokens"] == 7  # the display survives the refusal


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


def test_cli_frame_meter_charges_what_the_web_frame_meter_charges():
    """``Agent._record_frame_usage``'s docstring says it charges "as the Web
    loop does". It did not: the Web sibling (``gateway.add_usage``) calls
    ``measured_usage`` first, while this one read the public display keys --
    so one non-final streamed reply charged the frame 1200/340 on the CLI and
    0/0 on the Web, and this same run's Action Ledger recorded it as
    ``llm_*_tokens_unknown`` two lines later."""

    from types import SimpleNamespace

    from openai4s.agent.loop import Agent

    charged = []

    class _Store:
        def add_frame_tokens(self, frame_id, *, input_tokens, output_tokens):
            charged.append((frame_id, input_tokens, output_tokens))

    agent = object.__new__(Agent)
    agent._owns_frame = True
    agent.frame_id = "frame-1"
    agent.dispatcher = SimpleNamespace(store=_Store())

    unmeasured = llm.normalize_usage(
        RawUsage({"prompt_tokens": 1200, "completion_tokens": 340}, final=False),
        "chatgpt",
    )
    assert unmeasured["prompt_tokens"] == 1200  # the display still says so
    agent._record_frame_usage(unmeasured)

    measured = llm.normalize_usage(
        RawUsage({"prompt_tokens": 7, "completion_tokens": 3}, final=True),
        "chatgpt",
    )
    agent._record_frame_usage(measured)

    assert charged == [("frame-1", 0, 0), ("frame-1", 7, 3)]


@pytest.mark.parametrize(
    "strip",
    [
        pytest.param(dict, id="dict()"),
        pytest.param(lambda u: {**u}, id="spread"),
        pytest.param(lambda u: json.loads(json.dumps(u)), id="json-round-trip"),
        pytest.param(lambda u: {k: v for k, v in u.items()}, id="comprehension"),
    ],
)
def test_a_copy_that_lost_its_evidence_is_refused_not_recertified(strip):
    """``dict(x)`` on a dict subclass builds a plain dict through CPython's
    concrete fast path -- there is no dunder to intercept it -- so these four
    copies CANNOT carry provenance. That is exactly why a meter refuses a
    plain mapping instead of re-deriving counters from one: the old key scan
    could not tell a stripped copy from a raw provider payload, and certified
    counters the evidence had refused. Refusing costs an ``*_unknown`` row;
    trusting costs a silent overrun of the Auto Mode budget and the quota
    ledger."""

    unmeasured = llm.normalize_usage(
        RawUsage({"prompt_tokens": 1200, "completion_tokens": 340}, final=False),
        "chatgpt",
    )
    assert measured_total(unmeasured) is None
    stripped = strip(unmeasured)
    assert stripped["total_tokens"] == 1540  # the public shape is unchanged
    assert measured_total(stripped) is None
    assert verifiable_token_usage(stripped) is None
    # copy_usage used to *mint* evidence for an evidence-less input, which
    # laundered a stripped copy back into an audited-looking value.
    assert measured_total(copy_usage(stripped)) is None


def test_evidence_survives_every_copy_python_can_intercept():
    """The counterpart: the refusal above must not swallow a real
    measurement, and every channel that DOES route through a dunder is
    sealed."""

    import copy as copy_mod
    import pickle

    from openai4s.llm.usage import UsageMetrics

    measured = llm.normalize_usage(
        RawUsage({"prompt_tokens": 1200, "completion_tokens": 340}, final=True),
        "chatgpt",
    )
    assert measured_total(measured) == 1540
    for clone in (
        measured.copy(),
        copy_mod.copy(measured),
        copy_mod.deepcopy(measured),
        pickle.loads(pickle.dumps(measured)),
        measured | {},
    ):
        assert type(clone) is UsageMetrics
        assert measured_total(clone) == 1540


def test_asdict_neither_crashes_nor_inverts_the_trust_bit():
    """``dataclasses.asdict`` rebuilds a dict field as ``type(obj)(pairs)``.
    A Mapping-only constructor made that raise for ``UsageMetrics`` and, worse,
    silently hand ``RawUsage`` an EMPTY dict marked final=True -- counters lost
    and the trust bit inverted to "measured" in one step."""

    import dataclasses

    @dataclasses.dataclass
    class Box:
        usage: dict

    rebuilt = dataclasses.asdict(
        Box(usage=RawUsage({"prompt_tokens": 5}, final=False))
    )["usage"]
    assert dict(rebuilt) == {"prompt_tokens": 5}  # was {} -- counters lost
    assert rebuilt.final is False  # was True -- trust inverted
    assert measured_total(rebuilt) is None

    metrics = llm.normalize_usage(
        {"prompt_tokens": 7, "completion_tokens": 3}, "chatgpt"
    )
    # Was TypeError: __init__() missing 1 required positional argument.
    assert measured_total(dataclasses.asdict(Box(usage=metrics))["usage"]) is None


def test_measuring_a_verdict_twice_is_the_identity():
    """The one production path that meters twice -- ``_canonical_usage`` hands
    its result to ``record_session_llm_usage``, which measures again -- used to
    depend on the trusting key scan for its second pass. The verdict carries
    its own type instead."""

    measured = llm.normalize_usage(
        RawUsage({"prompt_tokens": 7, "completion_tokens": 3}, final=True), "chatgpt"
    )
    once = measured_usage(measured)
    assert measured_usage(once) is once
    assert measured_total(once) == 10


def test_a_call_that_never_left_the_process_is_not_billable():
    """`client.py` reads `state.attempts and not state.sent` while the transport
    itself uses the honest `not self.sent`. A failure raised BEFORE the first
    attempt is counted -- `check_send` finds the total deadline already spent,
    and `deadline_error` does not set the flag itself -- therefore reported as
    *started*. Since a failed call's `error.usage` is attested-but-empty,
    `record_session_llm_usage` turns that into two `llm_*_unknown` rows, which
    `check_quota` converts into a hard refusal for the whole window -- and
    nothing anywhere can clear them. A request that never reached the wire
    must not be able to do that."""

    import time as time_mod

    from openai4s.llm.transport import CallState

    state = CallState(total_timeout_s=600.0)
    state.deadline = time_mod.monotonic() - 1.0  # already spent
    assert state.attempts == 0 and state.sent is False

    class _Probe:
        call_state = state

        def __call__(self) -> bool:
            return False

    state.should_cancel = _Probe()
    with pytest.raises(LLMDeadlineExceeded) as caught:
        llm.chat(
            [{"role": "user", "content": "hi"}],
            cfg(),
            should_cancel=state.should_cancel,
        )
    assert caught.value.llm_not_started is True
    # The attested-but-empty usage is what would otherwise have been charged.
    assert measured_usage(caught.value.usage) == {}


def _owned(tmp_path):
    """A store with one owned session, so the governance ledger is live."""
    from openai4s.store import Store

    store = Store(tmp_path / "meter.db")
    user = store.team.create_user(
        username="meter", password="fake-password", role="member"
    )
    root = store.new_frame(kind="turn", project_id="p")
    store.team.set_session_owner(root, user["id"], project_id="p")
    return store, root, user["id"]


def _ledger(store, user_id):
    return {
        row["kind"]: row["total"]
        for row in store.governance.usage_summary(user_id=user_id)
    }


def test_host_llm_charges_the_session_and_is_gated(tmp_path):
    """`host.llm` returned `response["content"]` and dropped the reply, so a
    cell's own LLM spend reached no frame counter, no governance ledger and no
    quota -- the widest of the four unmetered ports, at up to LLM_FANOUT_CAP
    billed requests per call."""

    from openai4s.host.llm import LLMService
    from openai4s.storage.governance import (
        QuotaExceeded,
        enforce_session_llm_quota,
        record_session_llm_usage,
    )

    with closing(_owned(tmp_path)[0]) as store:
        root = store.new_frame(kind="turn", project_id="p")
        user = store.team.get_user_by_username("meter")
        store.team.set_session_owner(root, user["id"], project_id="p")

        service = LLMService(
            SimpleNamespace(llm=cfg()),
            chat_call=lambda *_a, **_k: {
                "content": "ok",
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 40, "completion_tokens": 5}, "chatgpt"
                ),
            },
            quota_gate=lambda **kw: enforce_session_llm_quota(store, root, **kw),
            usage_sink=lambda usage: record_session_llm_usage(store, root, usage),
        )
        assert service.one({"messages": [{"role": "user", "content": "hi"}]}) == "ok"
        assert _ledger(store, user["id"])["llm_input_tokens"] == 40

        # ...and the same port now answers to the quota it used to bypass.
        store.governance.set_quota(
            scope="user",
            scope_id=user["id"],
            kind="llm_input_tokens",
            limit_amount=10,
            window="day",
        )
        with pytest.raises(QuotaExceeded):
            service.one({"messages": [{"role": "user", "content": "again"}]})


def test_the_context_summarizer_charges_the_session(tmp_path):
    """A summarizer call never becomes an action, so the Action Ledger never
    carried it to the quota ledger and `add_usage` is scoped to the turn.
    Compaction is the daemon's largest burst; it was entirely free."""

    from openai4s.agent import compaction as comp_mod
    from openai4s.storage.governance import record_session_llm_usage

    with closing(_owned(tmp_path)[0]) as store:
        root = store.new_frame(kind="turn", project_id="p")
        user = store.team.get_user_by_username("meter")
        store.team.set_session_owner(root, user["id"], project_id="p")
        charged = []

        reply = {
            "content": "## Summary\nthings happened",
            "usage": llm.normalize_usage(
                {"prompt_tokens": 900, "completion_tokens": 30}, "chatgpt"
            ),
        }
        # `_summary_chunk` reaches the wire through the module-level `chat`.
        import unittest.mock as mock

        with mock.patch.object(comp_mod, "chat", lambda *_a, **_k: reply):
            summary = comp_mod._summary_chunk(
                [{"role": "user", "content": "x"}],
                SimpleNamespace(llm=cfg()),
                256,
                None,
                None,
                lambda: charged.append("gated"),
                lambda usage: record_session_llm_usage(store, root, usage),
            )
        assert "things happened" in summary
        assert charged == ["gated"]  # the gate ran BEFORE the request
        assert _ledger(store, user["id"])["llm_input_tokens"] == 900


def test_session_titling_is_visible_but_cannot_close_a_window(tmp_path):
    """Titling is daemon overhead on ordinary user traffic. It must be counted,
    and it must NOT be able to refuse a member's next turn: a cosmetic
    64-token call writing a lockout-grade `*_unknown` row would brick a quota
    window on every new session against a provider that reports no usage."""

    from openai4s.server.titles import SessionTitleService
    from openai4s.storage.governance import QuotaExceeded, record_session_llm_usage

    with closing(_owned(tmp_path)[0]) as store:
        root = store.new_frame(kind="turn", project_id="p")
        user = store.team.get_user_by_username("meter")
        store.team.set_session_owner(root, user["id"], project_id="p")
        store.governance.set_quota(
            scope="user",
            scope_id=user["id"],
            kind="llm_input_tokens",
            limit_amount=10_000,
            window="day",
        )
        service = SessionTitleService(
            store=store,
            broadcast=lambda *_a: None,
            chat_call=lambda *_a, **_k: {"content": "A title", "usage": {}},
            usage_sink=lambda frame, usage: record_session_llm_usage(
                store, frame, usage, unmeasured_kind="llm_overhead_unmeasured"
            ),
        )
        assert service.summarize("hello there", cfg(), root) == "A title"
        rows = _ledger(store, user["id"])
        assert rows["llm_overhead_unmeasured"] >= 1  # visible
        assert "llm_input_tokens_unknown" not in rows  # and not lockout-grade
        store.governance.check_quota(
            user_id=user["id"], project_id="p", kind="llm_input_tokens"
        )


def test_a_probe_is_attributed_to_its_operator_and_can_refuse_nothing(tmp_path):
    """A probe has no session. Attributing it to the person who pressed the
    button keeps the spend visible; `llm_probe_*` is outside
    ENFORCED_QUOTA_KINDS so a failed probe can never write the row that
    refuses the next probe -- the diagnostic would go dark exactly when an
    operator is fixing what broke it."""

    from openai4s.storage.governance import (
        ENFORCED_QUOTA_KINDS,
        record_principal_llm_usage,
    )

    with closing(_owned(tmp_path)[0]) as store:
        user = store.team.get_user_by_username("meter")
        record_principal_llm_usage(
            store,
            user["id"],
            llm.normalize_usage(
                {"prompt_tokens": 12, "completion_tokens": 4}, "chatgpt"
            ),
            prefix="llm_probe",
        )
        rows = _ledger(store, user["id"])
        assert rows["llm_probe_input_tokens"] == 12
        assert rows["llm_probe_output_tokens"] == 4
        assert "llm_probe_input_tokens" not in ENFORCED_QUOTA_KINDS
        with pytest.raises(ValueError):
            store.governance.set_quota(
                scope="user",
                scope_id=user["id"],
                kind="llm_probe_input_tokens",
                limit_amount=1,
                window="day",
            )


def _reply(prompt_tokens, completion_tokens):
    return {
        "content": '{"decision": "UNSAFE", "categories": [6], "reason": "x"}',
        "usage": llm.normalize_usage(
            {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "chatgpt",
        ),
    }


def test_the_three_security_screeners_charge_the_session(tmp_path, monkeypatch):
    """Each pre-execution screener is a real billed provider call the member
    never asked for, and all three projected `content` out of the reply and
    dropped the rest -- no frame counter, no ledger, no budget.

    They are metered and deliberately NOT gated. A quota refusal here would
    not save the tokens; it would run the cell, or hand the model the tool
    output, with the screen switched off.

    And because they cannot be refused, their spend must not be able to refuse
    anything either: it records `llm_screening_*`, which is visible in
    `/team/usage` and outside `ENFORCED_QUOTA_KINDS`. A charge that can refuse
    but cannot be refused would deny the member their next turn for spend they
    never requested -- and for the injection scan, spend an attacker chose the
    size of.
    """
    from openai4s.security import classify_code, scan_tool_result, screen_trajectory
    from openai4s.storage.governance import record_screening_llm_usage

    store, root, user_id = _owned(tmp_path)
    with closing(store):

        def sink(usage):
            record_screening_llm_usage(store, root, usage)

        security = SimpleNamespace(safety_mode="llm", biosecurity=True)
        config = SimpleNamespace(llm=cfg(), security=security)

        monkeypatch.setattr(
            "openai4s.llm.chat", lambda *_a, **_k: _reply(40, 5), raising=False
        )
        verdict = classify_code(
            "import socket\ns = socket.socket()", config, usage_sink=sink
        )
        assert verdict.source == "llm", verdict
        assert _ledger(store, user_id)["llm_screening_input_tokens"] == 40

        monkeypatch.setattr(
            "openai4s.llm.chat",
            lambda *_a, **_k: {
                "content": '{"injected": false}',
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 7, "completion_tokens": 1}, "chatgpt"
                ),
            },
            raising=False,
        )
        scan_tool_result(
            "the search returned three papers on mitochondria",
            cfg=config,
            use_llm=True,
            usage_sink=sink,
        )
        assert _ledger(store, user_id)["llm_screening_input_tokens"] == 47

        monkeypatch.setattr(
            "openai4s.llm.chat",
            lambda *_a, **_k: {
                "content": '{"decision": "ALLOW", "reason": "ok"}',
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 3, "completion_tokens": 1}, "chatgpt"
                ),
            },
            raising=False,
        )
        screen_trajectory(
            "enhance transmissibility of h5n1", "code", config, usage_sink=sink
        )
        totals = _ledger(store, user_id)
        assert totals["llm_screening_input_tokens"] == 50, totals
        # The consequence, not just the kind name: none of it can refuse.
        assert "llm_input_tokens" not in totals, totals
        store.governance.set_quota(
            scope="user",
            scope_id=user_id,
            kind="llm_input_tokens",
            limit_amount=1,
            window="day",
        )
        store.governance.check_quota(
            user_id=user_id, project_id="p", kind="llm_input_tokens"
        )


def test_a_failing_meter_cannot_downgrade_a_screening_verdict(monkeypatch):
    """The screeners are wrapped in a blanket `except Exception` that fails
    open. Calling a sink inside it means a metering bug silently turns a real
    UNSAFE into SAFE -- the screen would look like it had run and passed."""
    from openai4s.security import classify_code

    def _explode(_usage):
        raise RuntimeError("the ledger is on fire")

    monkeypatch.setattr(
        "openai4s.llm.chat", lambda *_a, **_k: _reply(1, 1), raising=False
    )
    verdict = classify_code(
        "import socket\ns = socket.socket()",
        SimpleNamespace(llm=cfg(), security=SimpleNamespace(safety_mode="llm")),
        usage_sink=_explode,
    )
    assert verdict.decision == "UNSAFE", verdict
    assert verdict.source == "llm", verdict


def test_the_v2_reviewer_charges_the_session_and_is_gated(tmp_path):
    """V1 (`review_evidence`) has had a gate and a meter since its port was
    introduced. V2 runs after every completed turn, again on the bounded retry
    and once per auto-repair round, and had neither: its tokens settled the
    Auto Mode reservation table -- a per-run ceiling -- and stopped there,
    while the per-user window `check_quota` reads stayed blind to them."""
    from openai4s.server.scientific_review import ScientificReviewService
    from openai4s.storage.governance import (
        enforce_session_llm_quota,
        record_session_llm_usage,
    )

    store, root, user_id = _owned(tmp_path)
    with closing(store):
        service = ScientificReviewService(
            store=None,
            config=SimpleNamespace(),
            quota_gate=lambda frame_id: enforce_session_llm_quota(store, frame_id),
            usage_sink=lambda frame_id, usage: record_session_llm_usage(
                store, frame_id, usage
            ),
        )
        snapshot = {
            "identity": {"root_frame_id": root, "branch_id": root, "turn_id": "t"},
            "user_request": "do the thing",
            "candidate_answer": "did the thing",
            "artifacts": [],
        }
        reviewer_cfg = replace(cfg(), model="reviewer-model", max_tokens=1800)
        result = service.evaluate(
            snapshot,
            result_review_mode="review_only",
            agent_cfg=SimpleNamespace(llm=replace(cfg(), model="agent-model")),
            reviewer_cfg=reviewer_cfg,
            allow_same_model=True,
            chat_call=lambda *_a, **_k: {
                "content": '{"verdict": "pass", "summary": "ok", "findings": []}',
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 90, "completion_tokens": 10}, "chatgpt"
                ),
            },
        )
        assert result.get("reason") != "quota_exceeded", result
        assert _ledger(store, user_id)["llm_input_tokens"] == 90

        # ...and the same reviewer now answers to the quota it used to bypass,
        # as a terminal the user can read -- not as `reviewer_inference_failed`,
        # which would say the Reviewer is broken when the team is out of quota.
        store.governance.set_quota(
            scope="user",
            scope_id=user_id,
            kind="llm_input_tokens",
            limit_amount=10,
            window="day",
        )
        refused = service.evaluate(
            snapshot,
            result_review_mode="review_only",
            agent_cfg=SimpleNamespace(llm=replace(cfg(), model="agent-model")),
            reviewer_cfg=reviewer_cfg,
            allow_same_model=True,
            chat_call=lambda *_a, **_k: pytest.fail("the gate let the call through"),
        )
        assert refused["reason"] == "quota_exceeded", refused
        assert refused["summary"] == "Paused · Team quota exhausted", refused


def test_an_exception_that_never_reached_the_provider_is_not_charged():
    """`llm_not_started` is set by exactly two things -- `llm.chat` and
    `ReviewError` -- so a caller whose try wraps more than the wire charged for
    calls that never happened. A `KeyboardInterrupt` during assembly, or a
    `TypeError` from building the request, arrived with no flag and no usage
    and was metered as a completed call with no counters.

    `llm.chat` attaches `usage` on every raise that reached the provider and on
    none that did not, so that attribute is the positive evidence."""
    from openai4s.llm.usage import charge_call

    charged = []

    charge_call(charged.append, KeyboardInterrupt())
    charge_call(charged.append, TypeError("still assembling the request"))
    assert charged == []

    reached = RuntimeError("provider answered, then the parse failed")
    reached.usage = llm.normalize_usage(
        {"prompt_tokens": 12, "completion_tokens": 2}, "chatgpt"
    )
    charge_call(charged.append, reached)
    assert [measured_usage(u)["input_tokens"] for u in charged] == [12]


def _fanout_service(store, root, *, chat, cap=32):
    from concurrent.futures import ThreadPoolExecutor

    from openai4s.host.llm import LLMService
    from openai4s.storage.governance import (
        enforce_session_llm_quota,
        record_session_llm_usage,
    )

    return LLMService(
        SimpleNamespace(llm=replace(cfg(), max_tokens=64)),
        chat_call=chat,
        fanout_cap=cap,
        executor_factory=lambda **kwargs: ThreadPoolExecutor(**kwargs),
        quota_gate=lambda **kw: enforce_session_llm_quota(store, root, **kw),
        usage_sink=lambda usage: record_session_llm_usage(store, root, usage),
    )


def test_a_fanout_cannot_outrun_the_quota_it_is_gated_by(tmp_path):
    """The gate reads a ledger that is written only when a call RETURNS, so
    every worker of a fan-out asked the same pre-spend question and got the
    same yes. Measured before this: a 100-token window, 40 tokens a call --
    where a serialised caller is refused on its fourth -- passed all 32 items
    and recorded 1280 tokens, 12.8x the limit.

    The missing fact was never "what will this call cost"; the ledger records
    that a moment later. It is "what is already promised and not yet
    recorded", which is exactly what a fan-out hides."""
    import threading

    from openai4s.storage.governance import QuotaExceeded

    store, root, user_id = _owned(tmp_path)
    with closing(store):
        store.governance.set_quota(
            scope="user",
            scope_id=user_id,
            kind="llm_input_tokens",
            limit_amount=100,
            window="day",
        )
        started = threading.Barrier(2, timeout=5)

        def chat(_messages, _llm_cfg, **_kwargs):
            # Hold every worker inside the provider call at once, which is the
            # window the ledger cannot describe.
            try:
                started.wait()
            except threading.BrokenBarrierError:
                pass
            return {
                "content": "ok",
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 40, "completion_tokens": 1}, "chatgpt"
                ),
            }

        service = _fanout_service(store, root, chat=chat)
        batch = [{"messages": [{"role": "user", "content": "x"}]} for _ in range(32)]
        with pytest.raises(QuotaExceeded):
            service.complete({"batch": batch})
        charged = _ledger(store, user_id).get("llm_input_tokens", 0)
        assert charged <= 100, charged
        # Nothing is left promised once the batch unwinds, or the window would
        # shrink for the rest of the daemon's life.
        assert service._inflight_input == 0.0
        assert service._inflight_output == 0.0


def test_the_gate_does_not_throttle_a_fanout_that_fits(tmp_path):
    """The paired positive, and the one that matters for every ordinary run:
    these bounds carry a wire allowance and the transport's retry ceiling, so
    charging a call for its OWN projection would price one 40-token request at
    ~3.8k and let a 100-token window admit nothing at all. Far from a limit the
    promises are noise, and a fan-out must be untouched."""
    store, root, user_id = _owned(tmp_path)
    with closing(store):
        store.governance.set_quota(
            scope="user",
            scope_id=user_id,
            kind="llm_input_tokens",
            limit_amount=1_000_000,
            window="day",
        )
        calls = []

        def chat(_messages, _llm_cfg, **_kwargs):
            calls.append(1)
            return {
                "content": "ok",
                "usage": llm.normalize_usage(
                    {"prompt_tokens": 40, "completion_tokens": 1}, "chatgpt"
                ),
            }

        service = _fanout_service(store, root, chat=chat)
        batch = [{"messages": [{"role": "user", "content": "x"}]} for _ in range(32)]
        assert len(service.complete({"batch": batch})) == 32
        assert len(calls) == 32
        assert _ledger(store, user_id)["llm_input_tokens"] == 1280
        assert service._inflight_input == 0.0
