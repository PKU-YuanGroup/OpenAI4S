"""JudgmentService: flags, cache, batching, truncation, metering, audit."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags
from openai4s.host.judgment import (
    SETTING_AUDIT_RAW_STATE,
    STATE_TOKEN_BUDGET,
    JudgmentService,
    JudgmentServiceResult,
)
from openai4s.judgment.port import BACKEND_ERROR_CODES, BackendError
from openai4s.judgment.registry import (
    PROBE_TEMPLATE_ID,
    register_template,
    unregister_template,
)
from openai4s.judgment.types import Answer, BackendReply, Noul, Question

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
    "OPENAI4S_JUDGMENT_LITERATURE",
    "OPENAI4S_JUDGMENT_TEXT_FEATURES",
    "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
    "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
    "OPENAI4S_JUDGMENT_PROVIDER",
    "OPENAI4S_JUDGMENT_MODEL",
    "OPENAI4S_JUDGMENT_TIMEOUT_S",
)


class MemoryStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)


class FakeBackend:
    def __init__(
        self,
        *,
        noul: float = 0.91,
        error: BackendError | None = None,
        delay: float = 0.0,
    ) -> None:
        self.noul = noul
        self.error = error
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.current = 0
        self.max_current = 0

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        with self._lock:
            self.current += 1
            self.max_current = max(self.max_current, self.current)
            self.calls.append(
                {
                    "state": state,
                    "questions": dict(questions),
                    "model": model,
                    "timeout": timeout,
                }
            )
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            answers: dict[str, Answer] = {}
            for qid, question in questions.items():
                if isinstance(question, Noul):
                    answers[qid] = Answer(kind="noul", value=self.noul)
                else:
                    answers[qid] = Answer(kind="noul", value=self.noul)
            return BackendReply(
                answers=answers,
                usage={"input_tokens": 12, "output_tokens": 0},
                model=model,
            )
        finally:
            with self._lock:
                self.current -= 1


@pytest.fixture(autouse=True)
def _clear_judgment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _cfg(tmp_path: Any, **flag_kw: Any) -> Config:
    return Config(
        data_dir=tmp_path,
        experimental_judgment=ExperimentalJudgmentFlags(master=True, **flag_kw),
    )


def _service(
    tmp_path: Any,
    backend: FakeBackend,
    *,
    store: MemoryStore | None = None,
    usage: list[Any] | None = None,
    audit: list[Any] | None = None,
    flags: dict[str, Any] | None = None,
    cfg_box: dict[str, Config] | None = None,
    max_concurrency: int = 4,
) -> JudgmentService:
    cfg = _cfg(tmp_path, **(flags or {}))
    if cfg_box is not None:
        cfg_box["cfg"] = cfg

        def cfg_provider() -> Config:
            return cfg_box["cfg"]

    else:
        cfg_provider = cfg
    return JudgmentService(
        cfg_provider,
        lambda: store if store is not None else MemoryStore(),
        backend_factory=lambda: backend,
        usage_sink=(usage.append if usage is not None else None),
        audit_sink=(audit.append if audit is not None else None),
        max_concurrency=max_concurrency,
    )


def _run(service: JudgmentService, state: object | None = None, **kwargs: Any) -> Any:
    return service.run(
        purpose=kwargs.get("purpose", "probe"),
        template_id=kwargs.get("template_id", PROBE_TEMPLATE_ID),
        state={"text": "hello"} if state is None else state,
        params=kwargs.get("params"),
        scope=kwargs.get("scope"),
        ignore_capability=kwargs.get("ignore_capability", False),
    )


def test_disabled_does_not_call_backend(tmp_path: Any) -> None:
    backend = FakeBackend()
    service = JudgmentService(
        Config(
            data_dir=tmp_path,
            experimental_judgment=ExperimentalJudgmentFlags(master=False),
        ),
        lambda: MemoryStore(),
        backend_factory=lambda: backend,
    )
    result = _run(service)
    assert result.status == "disabled"
    assert result.answers == {}
    assert result.error_code is None
    assert backend.calls == []


def test_probe_ignores_sub_capability_but_requires_master(tmp_path: Any) -> None:
    backend = FakeBackend()
    off = JudgmentService(
        Config(
            data_dir=tmp_path,
            experimental_judgment=ExperimentalJudgmentFlags(master=False),
        ),
        lambda: MemoryStore(),
        backend_factory=lambda: backend,
    )
    disabled = off.probe()
    assert disabled.status == "disabled"
    assert backend.calls == []

    on = _service(tmp_path, backend, flags={"skill_suggest": False})
    result = on.probe()
    assert result.status == "ok"
    assert len(backend.calls) == 1
    assert backend.calls[0]["state"] == {"text": "hello"}


@pytest.mark.parametrize("code", sorted(BACKEND_ERROR_CODES))
def test_backend_error_maps_to_unavailable(tmp_path: Any, code: str) -> None:
    backend = FakeBackend(error=BackendError(code, code))
    result = _run(_service(tmp_path, backend))
    assert result.status == "unavailable"
    assert result.error_code == code
    assert result.answers == {}
    assert len(backend.calls) == 1


def test_policy_ok_and_uncertain_branches(tmp_path: Any) -> None:
    template_id = "test.service.policy"
    unregister_template(template_id)

    def questions(_params: Mapping[str, Any]) -> dict[str, Noul]:
        return {"alive": Noul(instructions="Is this a clear yes?")}

    def policy(answers: Mapping[str, Answer], _params: Mapping[str, Any]) -> str:
        return "ok" if float(answers["alive"].value) >= 0.8 else "uncertain"

    register_template(template_id, "1", questions, policy, purpose="probe")
    try:
        ok = _run(
            _service(tmp_path, FakeBackend(noul=0.9)),
            template_id=template_id,
        )
        assert ok.status == "ok"
        assert ok.answers["alive"].value == 0.9
        uncertain = _run(
            _service(tmp_path, FakeBackend(noul=0.2)),
            template_id=template_id,
        )
        assert uncertain.status == "uncertain"
        assert uncertain.answers["alive"].value == 0.2
    finally:
        unregister_template(template_id)


def test_cache_hit_and_each_key_part_misses(tmp_path: Any) -> None:
    backend = FakeBackend()
    box: dict[str, Config] = {}
    service = _service(tmp_path, backend, cfg_box=box)
    state = {"text": "same"}
    first = _run(service, state)
    second = _run(service, state)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(backend.calls) == 1

    box["cfg"] = _cfg(tmp_path, provider="llm")
    assert _run(service, state).cache_hit is False
    assert len(backend.calls) == 2

    box["cfg"] = _cfg(tmp_path, provider="llm", model="jev-other")
    assert _run(service, state).cache_hit is False
    assert len(backend.calls) == 3

    other_state = {"text": "other"}
    assert _run(service, other_state).cache_hit is False
    assert _run(service, other_state).cache_hit is True
    assert len(backend.calls) == 4

    assert (
        _run(service, other_state, params={"candidate_versions": "v1"}).cache_hit
        is False
    )
    assert (
        _run(service, other_state, params={"candidate_versions": "v2"}).cache_hit
        is False
    )
    assert (
        _run(service, other_state, params={"candidate_versions": "v1"}).cache_hit
        is True
    )
    assert len(backend.calls) == 6

    assert (
        _run(
            service, other_state, params={"candidate_versions": "v2"}, scope="s1"
        ).cache_hit
        is False
    )
    assert (
        _run(
            service, other_state, params={"candidate_versions": "v2"}, scope="s2"
        ).cache_hit
        is False
    )
    assert len(backend.calls) == 8

    template_a = "test.service.cache.a"
    template_b = "test.service.cache.b"

    def questions(_params: Mapping[str, Any]) -> dict[str, Noul]:
        return {"alive": Noul(instructions="Is this cached?")}

    def policy(_answers: Mapping[str, Answer], _params: Mapping[str, Any]) -> str:
        return "ok"

    unregister_template(template_a)
    unregister_template(template_b)
    register_template(template_a, "1", questions, policy, purpose="probe")
    register_template(template_b, "1", questions, policy, purpose="probe")
    try:
        assert _run(service, state, template_id=template_a).cache_hit is False
        assert _run(service, state, template_id=template_a).cache_hit is True
        assert _run(service, state, template_id=template_b).cache_hit is False
        assert len(backend.calls) == 10
    finally:
        unregister_template(template_a)
        unregister_template(template_b)

    versioned = "test.service.cache.ver"
    unregister_template(versioned)
    register_template(versioned, "1", questions, policy, purpose="probe")
    try:
        assert _run(service, state, template_id=versioned).cache_hit is False
        unregister_template(versioned)
        register_template(versioned, "2", questions, policy, purpose="probe")
        assert _run(service, state, template_id=versioned).cache_hit is False
        assert len(backend.calls) == 12
        unregister_template(versioned)
        register_template(
            versioned, "2", questions, policy, purpose="probe", policy_version="p2"
        )
        assert _run(service, state, template_id=versioned).cache_hit is False
        assert len(backend.calls) == 13
    finally:
        unregister_template(versioned)


def test_run_many_batches_same_state(tmp_path: Any) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    state = {"text": "shared"}
    results = service.run_many(
        [
            {"purpose": "probe", "template_id": PROBE_TEMPLATE_ID, "state": state},
            {"purpose": "probe", "template_id": PROBE_TEMPLATE_ID, "state": state},
            {"purpose": "probe", "template_id": PROBE_TEMPLATE_ID, "state": state},
        ]
    )
    assert [item.status for item in results] == ["ok", "ok", "ok"]
    assert len(backend.calls) == 1
    assert len(backend.calls[0]["questions"]) == 3


def test_run_many_concurrency_cap(tmp_path: Any) -> None:
    backend = FakeBackend(delay=0.05)
    service = _service(tmp_path, backend, max_concurrency=4)
    requests = [
        {
            "purpose": "probe",
            "template_id": PROBE_TEMPLATE_ID,
            "state": {"text": str(index)},
        }
        for index in range(6)
    ]
    results = service.run_many(requests)
    assert [item.status for item in results] == ["ok"] * 6
    assert len(backend.calls) == 6
    assert backend.max_current <= 4
    assert backend.max_current >= 2


def test_truncation_mark(tmp_path: Any) -> None:
    backend = FakeBackend()
    service = _service(tmp_path, backend)
    huge = "x" * (STATE_TOKEN_BUDGET * 4 + 200)
    result = _run(service, {"text": huge})
    assert isinstance(result, JudgmentServiceResult)
    assert result.truncated is True
    assert result.to_dict()["truncated"] is True
    sent = backend.calls[0]["state"]
    encoded = json.dumps(sent, sort_keys=True, default=str, separators=(",", ":"))
    assert len(encoded) <= STATE_TOKEN_BUDGET * 4
    small = _run(_service(tmp_path, FakeBackend()), {"text": "hi"})
    assert small.truncated is False


def test_usage_sink_called(tmp_path: Any) -> None:
    backend = FakeBackend()
    usage: list[Any] = []
    result = _run(_service(tmp_path, backend, usage=usage))
    assert result.status == "ok"
    assert usage == [{"input_tokens": 12, "output_tokens": 0}]
    usage.clear()
    disabled = JudgmentService(
        Config(
            data_dir=tmp_path,
            experimental_judgment=ExperimentalJudgmentFlags(master=False),
        ),
        lambda: MemoryStore(),
        backend_factory=lambda: backend,
        usage_sink=usage.append,
    )
    assert _run(disabled).status == "disabled"
    assert usage == []


def test_audit_event_omits_raw_state(tmp_path: Any) -> None:
    backend = FakeBackend()
    events: list[Any] = []
    secret = {"text": "raw-state-must-not-be-audited", "note": "keep-private"}
    result = _run(_service(tmp_path, backend, audit=events), secret)
    assert result.status == "ok"
    assert events
    dumped = json.dumps(events, default=str)
    assert "raw-state-must-not-be-audited" not in dumped
    assert "keep-private" not in dumped
    assert "state" not in events[0]
    assert events[0]["template_id"] == PROBE_TEMPLATE_ID
    assert events[0]["status"] == "ok"
    assert events[0]["state_sha256"] == result.state_sha256
    assert "alive" in events[0]["probabilities"]


def test_audit_raw_state_opt_in(tmp_path: Any) -> None:
    backend = FakeBackend()
    events: list[Any] = []
    store = MemoryStore({SETTING_AUDIT_RAW_STATE: "true"})
    state = {"text": "visible-when-opted-in"}
    _run(_service(tmp_path, backend, store=store, audit=events), state)
    assert events[0]["state"] == {"text": "visible-when-opted-in"}


def test_unknown_template_is_value_error(tmp_path: Any) -> None:
    service = _service(tmp_path, FakeBackend())
    with pytest.raises(ValueError, match="unknown template"):
        service.run(purpose="probe", template_id="nope", state={"text": "x"})


def test_dispatch_unknown_template_is_soft_error(tmp_path: Any) -> None:
    service = _service(tmp_path, FakeBackend())
    result = service.dispatch({"template": "missing.template", "state": {"text": "x"}})
    assert set(result) == {"error"}
    assert "unknown template" in result["error"]


def test_dispatch_four_statuses_are_data(tmp_path: Any) -> None:
    backend = FakeBackend()
    enabled = _service(tmp_path, backend)
    payload = enabled.dispatch({"template": PROBE_TEMPLATE_ID, "state": {"text": "hi"}})
    assert payload["status"] == "ok"
    assert "error" not in payload
    disabled = JudgmentService(
        Config(
            data_dir=tmp_path,
            experimental_judgment=ExperimentalJudgmentFlags(master=False),
        ),
        lambda: MemoryStore(),
        backend_factory=lambda: backend,
    )
    closed = disabled.dispatch({"template": PROBE_TEMPLATE_ID, "state": {"text": "hi"}})
    assert closed["status"] == "disabled"
    assert "error" not in closed


def test_custom_questions_rejected_without_allow_custom(tmp_path: Any) -> None:
    service = _service(tmp_path, FakeBackend())
    with pytest.raises(ValueError, match="does not allow custom questions"):
        service.run(
            purpose="probe",
            template_id=PROBE_TEMPLATE_ID,
            state={"text": "hi"},
            params={"questions": {"x": "y"}},
        )
