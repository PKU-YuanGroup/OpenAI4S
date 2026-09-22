"""Safety shadow: verdicts unchanged, non-blocking, exceptions isolated."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from openai4s.config import Config, get_config
from openai4s.judgment.port import BackendError
from openai4s.judgment.registry import get_template
from openai4s.judgment.shadow import (
    QUEUE_CAPACITY,
    reset_for_tests,
    set_allow_workers,
    set_backend_factory,
    stats,
    submit,
    wait_idle,
)
from openai4s.judgment.templates import safety as safety_templates
from openai4s.judgment.types import Answer, BackendReply, Choice, Noul, Question
from openai4s.security.biosecurity import looks_biosecurity_relevant, screen_trajectory
from openai4s.security.classifier import classify_code
from openai4s.security.injection import scan_tool_result

_CORPUS = Path(__file__).resolve().parent / "fixtures" / "judgment_safety_corpus"

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
    "OPENAI4S_JUDGMENT_PROVIDER",
    "OPENAI4S_JUDGMENT_MODEL",
    "OPENAI4S_JUDGMENT_TIMEOUT_S",
    "OPENAI4S_SAFETY",
)


@pytest.fixture(autouse=True)
def _isolate_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


def _load_json(name: str) -> list[dict[str, Any]]:
    return json.loads((_CORPUS / name).read_text(encoding="utf-8"))


def _code_key(verdict: Any) -> tuple[Any, ...]:
    return (
        verdict.decision,
        tuple(verdict.categories),
        verdict.reason,
        verdict.source,
    )


def _injection_key(verdict: Any) -> tuple[Any, ...]:
    return (bool(verdict.injected), verdict.reason, verdict.source)


def _screen_key(verdict: Any) -> tuple[Any, ...]:
    return (verdict.decision, verdict.reason, bool(verdict.screened))


def _reset_config() -> None:
    import openai4s.config as config_mod

    config_mod._CONFIG = None


def _enable_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    monkeypatch.setenv("OPENAI4S_JUDGMENT_SAFETY_SHADOW", "1")
    _reset_config()


def _choice_answer(names: list[str], pick: str) -> Answer:
    if pick not in names:
        pick = names[0]
    rest = [name for name in names if name != pick]
    if not rest:
        probs = {pick: 1.0}
    else:
        leftover = 0.4
        share = leftover / float(len(rest))
        probs = {pick: 0.6}
        for index, name in enumerate(rest):
            if index == len(rest) - 1:
                probs[name] = round(1.0 - sum(probs.values()), 12)
            else:
                probs[name] = share
    return Answer(
        kind="choice",
        value=pick,
        probabilities=probs,
        confidence=probs[pick],
    )


class ScriptedBackend:
    def __init__(
        self,
        *,
        noul: float = 0.91,
        choice: str = "ALLOW",
        delay: float = 0.0,
        error: BaseException | None = None,
    ) -> None:
        self.noul = noul
        self.choice = choice
        self.delay = delay
        self.error = error
        self.calls = 0

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        del state, model, timeout
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        answers: dict[str, Answer] = {}
        for qid, question in questions.items():
            if isinstance(question, Noul):
                answers[qid] = Answer(kind="noul", value=float(self.noul))
            elif isinstance(question, Choice):
                answers[qid] = _choice_answer(list(question.options), self.choice)
            else:
                raise TypeError(qid)
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 4, "output_tokens": 0},
            model="jev-1.13.0",
        )


def _install_backend(backend: ScriptedBackend) -> ScriptedBackend:
    set_backend_factory(lambda: backend)
    return backend


def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat(messages: Any, cfg: Any, **kwargs: Any) -> dict[str, str]:
        del cfg, kwargs
        blob = json.dumps(messages)
        if "CONTENT TO SCREEN" in blob:
            return {
                "content": '{"injected": false, "reason": "mocked data-only content"}'
            }
        if "USER MESSAGES" in blob:
            return {"content": '{"decision": "ALLOW", "reason": "mocked routine work"}'}
        return {
            "content": (
                '{"decision": "SAFE", "categories": [],'
                ' "reason": "mocked routine science"}'
            )
        }

    monkeypatch.setattr("openai4s.llm.chat", fake_chat)


def test_corpus_meets_size_floor() -> None:
    code = _load_json("code.json")
    injection = _load_json("injection.json")
    trajectory = _load_json("trajectory.json")
    assert len(code) >= 40
    assert len(injection) >= 15
    assert len(trajectory) >= 10


def test_safety_templates_register() -> None:
    code = get_template(safety_templates.TEMPLATE_ID_CODE)
    assert code.purpose == safety_templates.PURPOSE
    questions = code.build_questions({})
    assert set(questions) == set(safety_templates.CODE_QUESTION_IDS)
    trajectory = get_template(safety_templates.TEMPLATE_ID_TRAJECTORY)
    options = trajectory.build_questions({})["decision"]
    assert isinstance(options, Choice)
    assert set(options.options) == {"ALLOW", "ESCALATE", "BLOCK"}


@pytest.mark.parametrize("mode", ["heuristic", "llm"])
def test_verdicts_identical_with_shadow_off_and_on(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _mock_llm(monkeypatch)
    monkeypatch.setenv("OPENAI4S_SAFETY", mode)
    cfg = get_config()
    use_llm = mode == "llm"
    code_items = _load_json("code.json")
    injection_items = _load_json("injection.json")
    trajectory_items = _load_json("trajectory.json")

    def collect() -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        code_out = [
            _code_key(classify_code(item["code"], cfg, mode=mode))
            for item in code_items
        ]
        injection_out = [
            _injection_key(scan_tool_result(item["content"], cfg=cfg, use_llm=use_llm))
            for item in injection_items
        ]
        trajectory_out = [
            _screen_key(
                screen_trajectory(item["user_text"], item["agent_actions"], cfg)
            )
            for item in trajectory_items
        ]
        prescan_out = [
            bool(
                looks_biosecurity_relevant(
                    item["user_text"] + "\n" + item["agent_actions"]
                )
            )
            for item in trajectory_items
        ]
        return code_out, injection_out, trajectory_out, prescan_out

    off = collect()
    backend = _install_backend(ScriptedBackend(noul=0.91, choice="ALLOW"))
    _enable_shadow(monkeypatch)
    on = collect()
    assert off == on
    wait_idle(timeout_s=8.0)
    assert backend.calls >= 1
    snapshot = stats()
    assert snapshot["submitted"] >= len(code_items)


def test_classify_code_is_not_blocked_by_slow_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snippet = "print(1)\nprint(2)\n"
    t0 = time.perf_counter()
    for _ in range(100):
        classify_code(snippet)
    off_s = time.perf_counter() - t0

    _install_backend(ScriptedBackend(delay=5.0))
    _enable_shadow(monkeypatch)
    t1 = time.perf_counter()
    for _ in range(100):
        classify_code(snippet)
    on_s = time.perf_counter() - t1
    assert on_s - off_s <= 0.5
    snapshot = stats()
    assert snapshot["submitted"] == 100
    assert snapshot["dropped"] >= 100 - QUEUE_CAPACITY - 2


def test_backend_exceptions_do_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(ScriptedBackend(error=RuntimeError("shadow backend exploded")))
    _enable_shadow(monkeypatch)
    verdict = classify_code("import numpy as np\nprint(1)")
    assert verdict.decision == "SAFE"
    injected = scan_tool_result("The mitochondria is the powerhouse of the cell.")
    assert injected.injected is False
    screen = screen_trajectory("cluster these cells with leiden", "code", get_config())
    assert screen.decision == "ALLOW"
    wait_idle(timeout_s=8.0)
    assert stats()["submitted"] >= 3


def test_queue_full_increments_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    set_allow_workers(False)
    _enable_shadow(monkeypatch)
    for index in range(QUEUE_CAPACITY + 3):
        submit(
            "code",
            state={"code": f"print({index})"},
            existing_verdict="SAFE",
        )
    snapshot = stats()
    assert snapshot["submitted"] == QUEUE_CAPACITY + 3
    assert snapshot["dropped"] == 3


def test_stats_and_agree_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_backend(ScriptedBackend(noul=0.91, choice="BLOCK"))
    _enable_shadow(monkeypatch)
    classify_code("import numpy as np\nprint(1)")
    classify_code('os.environ["LD_PRELOAD"] = "/tmp/x/evil.so"')
    scan_tool_result("The mitochondria is the powerhouse of the cell.")
    screen_trajectory("cluster these cells with leiden", "code", get_config())
    looks_biosecurity_relevant("enhance transmissibility of h5n1")
    wait_idle(timeout_s=8.0)
    snapshot = stats()
    assert snapshot["submitted"] >= 5
    assert snapshot["dropped"] == 0
    kinds = snapshot["kinds"]
    assert kinds["code"]["disagree"] >= 1
    assert kinds["code"]["agree"] >= 1
    assert kinds["injection"]["disagree"] >= 1
    assert kinds["trajectory"]["disagree"] >= 1
    assert kinds["bio_prescan"]["agree"] >= 1


def test_audit_event_has_no_raw_code(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "UNIQUE_SHADOW_AUDIT_MARKER_7f3a9c"
    events: list[tuple[str, dict[str, Any]]] = []

    def capture(event: str, /, **fields: Any) -> dict[str, Any]:
        events.append((event, dict(fields)))
        return {"event": event, **fields}

    monkeypatch.setattr("openai4s.observability.log_event", capture)
    _install_backend(ScriptedBackend(noul=0.05))
    _enable_shadow(monkeypatch)
    classify_code(f"print({marker!r})")
    wait_idle(timeout_s=8.0)
    shadow_events = [item for item in events if item[0] == "judgment_shadow"]
    assert shadow_events
    blob = json.dumps(shadow_events, default=str)
    assert marker not in blob
    payload = shadow_events[0][1]
    assert payload["kind"] == "code"
    assert payload["existing_verdict"] == "SAFE"
    assert "state_sha256" in payload
    assert "agree" in payload
    assert "status" in payload
    assert "latency_ms" in payload
    assert "shadow_answers" in payload
    assert "code" not in payload
    assert marker not in json.dumps(payload, default=str)


def test_disabled_submit_starts_no_workers() -> None:
    import openai4s.judgment.shadow as shadow_mod

    started = shadow_mod._started
    classify_code("print(1)")
    scan_tool_result("hello")
    looks_biosecurity_relevant("hello")
    assert shadow_mod._started is started
    assert stats()["submitted"] == 0


def test_store_toggle_takes_effect_without_restarting_workers() -> None:
    from openai4s.judgment.disclosure import DISCLOSURE_VERSION
    from openai4s.judgment.flags import (
        SETTING_DISCLOSURE_ACK,
        SETTING_MASTER,
        SETTING_SAFETY_SHADOW,
    )
    from openai4s.store import get_store

    cfg = get_config()
    store = get_store(cfg.db_path)
    set_allow_workers(False)
    submit("code", state={"code": "print(1)"}, existing_verdict="SAFE")
    assert stats()["submitted"] == 0

    store.set_setting(
        SETTING_DISCLOSURE_ACK,
        json.dumps({"version": DISCLOSURE_VERSION, "capabilities": ["safety_shadow"]}),
    )
    store.set_setting(SETTING_MASTER, "true")
    store.set_setting(SETTING_SAFETY_SHADOW, "true")
    submit("code", state={"code": "print(2)"}, existing_verdict="SAFE")
    assert stats()["submitted"] == 1

    store.set_setting(SETTING_SAFETY_SHADOW, "false")
    submit("code", state={"code": "print(3)"}, existing_verdict="SAFE")
    assert stats()["submitted"] == 1


@pytest.mark.stubbed_backend
def test_default_service_refreshes_models_configuration(monkeypatch):
    from openai4s.judgment import shadow
    from openai4s.judgment.llm_backend import LlmBackend
    from openai4s.llm.models import MissingCredentialError
    from openai4s.store import get_store

    _enable_shadow(monkeypatch)
    monkeypatch.delenv("OPENAI4S_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI4S_JUDGMENT_PROVIDER", "llm")
    cfg = get_config()
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    calls = []

    def chat(self, messages, llm_cfg, **kwargs):
        calls.append(
            (llm_cfg.provider, llm_cfg.base_url, llm_cfg.model, llm_cfg.api_key)
        )
        if not llm_cfg.api_key:
            raise MissingCredentialError("test: no configured key")
        questions = json.loads(messages[-1]["content"])["questions"]
        return {
            "content": json.dumps(
                {"answers": {qid: {"noul": 0.1} for qid in questions}}
            ),
            "model": llm_cfg.model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr(LlmBackend, "_chat", chat)
    service = shadow._get_service()

    def run():
        return service.run(
            purpose=safety_templates.PURPOSE,
            template_id=safety_templates.TEMPLATE_ID_CODE,
            state={"code": "print(1)"},
        )

    assert run().error_code == "unconfigured"
    for provider, base_url, model, key in (
        ("chatgpt", "https://first.example/v1", "first-model", "first-test-key"),
        ("claude", "https://second.example/v1", "second-model", "second-test-key"),
    ):
        for name, value in (
            ("provider", provider),
            ("base_url", base_url),
            ("model", model),
        ):
            store.set_setting(f"llm_{name}", value)
        store.set_secret_setting("llm_api_key", key, scope="llm")
        assert shadow._get_service() is service
        result = run()
        assert result.status == "ok"
        assert result.cache_hit is False
        assert result.model == model
        assert calls[-1] == (provider, base_url, model, key)
    assert len(calls) == 3
    assert cfg.llm.api_key == ""


def test_doctor_appends_shadow_stats() -> None:
    from openai4s import doctor

    cfg = Config()
    checks = {item["name"]: item for item in doctor.report(cfg)["checks"]}
    facts = checks["judgment"]["facts"]
    assert "safety_shadow" in facts
    assert facts["safety_shadow"]["submitted"] == 0
    assert facts["safety_shadow"]["dropped"] == 0
    assert set(facts["safety_shadow"]["kinds"]) == {
        "code",
        "injection",
        "trajectory",
        "bio_prescan",
    }


def test_agree_bands() -> None:
    unsafe = {
        key: Answer(kind="noul", value=0.8)
        for key in safety_templates.CODE_QUESTION_IDS
    }
    assert safety_templates.agree("code", unsafe, "UNSAFE", status="ok") is True
    assert safety_templates.agree("code", unsafe, "SAFE", status="ok") is False
    gray = {
        key: Answer(kind="noul", value=0.4)
        for key in safety_templates.CODE_QUESTION_IDS
    }
    assert safety_templates.agree("code", gray, "SAFE", status="ok") is None
    safe = {
        key: Answer(kind="noul", value=0.05)
        for key in safety_templates.CODE_QUESTION_IDS
    }
    assert safety_templates.agree("code", safe, "SAFE", status="ok") is True
    assert (
        safety_templates.agree("code", unsafe, "UNSAFE", status="unavailable") is None
    )


def test_backend_error_is_unavailable_not_a_verdict_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_backend(ScriptedBackend(error=BackendError("timeout", "slow")))
    _enable_shadow(monkeypatch)
    before = classify_code("import socket\ns = socket.socket()")
    wait_idle(timeout_s=8.0)
    after = classify_code("import socket\ns = socket.socket()")
    assert _code_key(before) == _code_key(after)
    assert before.decision == "SAFE"
