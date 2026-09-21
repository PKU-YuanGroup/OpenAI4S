"""LlmBackend: mocked chat(), no network, never a silent TypeSafe fallback."""

from __future__ import annotations

import json
from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags, LLMConfig
from openai4s.host.judgment import JudgmentService
from openai4s.judgment.llm_backend import LlmBackend, distribution_confidence
from openai4s.judgment.port import BackendError
from openai4s.judgment.registry import PROBE_TEMPLATE_ID
from openai4s.judgment.types import Answer, Choice, Noul, Score
from openai4s.judgment.typesafe import TypeSafeBackend
from openai4s.llm.models import MissingCredentialError

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

NOUL = Noul(instructions="Does state.text convey urgency?")
CHOICE = Choice(
    instructions="Which team should handle state.text?",
    options={"billing": "Payments", "technical": "Bugs", "sales": "Pricing"},
)
SCORE = Score(
    instructions="How frustrated is the customer in state.text?",
    levels=("Calm", "Frustrated", "Very angry"),
)
QUESTIONS: dict[str, Noul | Choice | Score] = {
    "is_urgent": NOUL,
    "department": CHOICE,
    "frustration": SCORE,
}
STATE = {"text": "The invoice is wrong and I want a refund now."}


class MemoryStore:
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return default


@pytest.fixture(autouse=True)
def _clear_judgment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _llm_cfg() -> LLMConfig:
    return LLMConfig(provider="deepseek", api_key="test-key", model="science-model")


def _cfg(tmp_path: Any, **flag_kw: Any) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=_llm_cfg(),
        experimental_judgment=ExperimentalJudgmentFlags(master=True, **flag_kw),
    )


def _backend(
    tmp_path: Any,
    chat_call: Any,
    *,
    usage: list[Any] | None = None,
    flags: dict[str, Any] | None = None,
) -> LlmBackend:
    return LlmBackend(
        _cfg(tmp_path, **(flags or {})),
        usage_sink=(usage.append if usage is not None else None),
        chat_call=chat_call,
    )


def _valid_content() -> str:
    return json.dumps(
        {
            "answers": {
                "is_urgent": {"noul": 0.91},
                "department": {
                    "probabilities": {
                        "billing": 0.88,
                        "technical": 0.12,
                        "sales": 0.0,
                    }
                },
                "frustration": {"probabilities": {"0": 0.0, "1": 0.95, "2": 0.05}},
            }
        }
    )


def _chat_reply(content: object, **extra: Any) -> dict[str, Any]:
    payload = {
        "content": content,
        "model": extra.pop("model", "science-model"),
        "usage": extra.pop("usage", {"input_tokens": 11, "output_tokens": 7}),
    }
    payload.update(extra)
    return payload


def test_three_question_types_parse(tmp_path: Any) -> None:
    calls: list[dict[str, Any]] = []

    def chat(messages: list[dict[str, Any]], cfg: LLMConfig, **options: Any) -> dict:
        calls.append({"messages": messages, "cfg": cfg, "options": options})
        return _chat_reply(_valid_content())

    reply = _backend(tmp_path, chat).evaluate(
        state=STATE, questions=QUESTIONS, model="jev-1.13.0", timeout=3.0
    )
    assert reply.model == "science-model"
    assert reply.usage == {"input_tokens": 11, "output_tokens": 7}
    urgent = reply.answers["is_urgent"]
    assert isinstance(urgent, Answer)
    assert urgent.kind == "noul"
    assert urgent.value == pytest.approx(0.91)
    department = reply.answers["department"]
    assert department.kind == "choice"
    assert department.value == "billing"
    assert department.probabilities == {
        "billing": 0.88,
        "technical": 0.12,
        "sales": 0.0,
    }
    assert department.confidence == pytest.approx(
        distribution_confidence(department.probabilities or {}, 3)
    )
    frustration = reply.answers["frustration"]
    assert frustration.kind == "score"
    assert frustration.value == pytest.approx(1.05)
    assert frustration.probabilities == {"0": 0.0, "1": 0.95, "2": 0.05}
    assert calls and calls[0]["options"]["temperature"] == 0.0
    assert calls[0]["cfg"].model == "science-model"


def test_non_json_output_is_invalid_response(tmp_path: Any) -> None:
    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        return _chat_reply("the invoice looks urgent, billing should handle it")

    with pytest.raises(BackendError) as caught:
        _backend(tmp_path, chat).evaluate(
            state=STATE, questions=QUESTIONS, model="jev-1.13.0", timeout=3.0
        )
    assert caught.value.code == "invalid_response"


def test_probabilities_not_summing_to_one_is_invalid(tmp_path: Any) -> None:
    content = json.dumps(
        {
            "answers": {
                "is_urgent": {"noul": 0.5},
                "department": {
                    "probabilities": {
                        "billing": 0.5,
                        "technical": 0.5,
                        "sales": 0.5,
                    }
                },
                "frustration": {"probabilities": {"0": 0.5, "1": 0.5, "2": 0.0}},
            }
        }
    )

    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        return _chat_reply(content)

    with pytest.raises(BackendError) as caught:
        _backend(tmp_path, chat).evaluate(
            state=STATE, questions=QUESTIONS, model="jev-1.13.0", timeout=3.0
        )
    assert caught.value.code == "invalid_response"


def test_unknown_choice_option_is_invalid(tmp_path: Any) -> None:
    content = json.dumps(
        {
            "answers": {
                "is_urgent": {"noul": 0.5},
                "department": {
                    "probabilities": {
                        "billing": 0.2,
                        "other_desk": 0.8,
                    }
                },
                "frustration": {"probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}},
            }
        }
    )

    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        return _chat_reply(content)

    with pytest.raises(BackendError) as caught:
        _backend(tmp_path, chat).evaluate(
            state=STATE, questions=QUESTIONS, model="jev-1.13.0", timeout=3.0
        )
    assert caught.value.code == "invalid_response"


def test_charge_call_meters_chat_usage(tmp_path: Any) -> None:
    charged: list[Any] = []
    usage = {"input_tokens": 42, "output_tokens": 9}

    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        return _chat_reply(_valid_content(), usage=usage)

    _backend(tmp_path, chat, usage=charged).evaluate(
        state=STATE, questions=QUESTIONS, model="jev-1.13.0", timeout=3.0
    )
    assert charged == [usage]


def test_llm_provider_result_is_not_calibrated(tmp_path: Any) -> None:
    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        return _chat_reply(json.dumps({"answers": {"alive": {"noul": 0.9}}}))

    metered: list[Any] = []
    backend = LlmBackend(
        _cfg(tmp_path, provider="llm"),
        usage_sink=metered.append,
        chat_call=chat,
    )
    service = JudgmentService(
        _cfg(tmp_path, provider="llm"),
        lambda: MemoryStore(),
        backend_factory=lambda: backend,
        usage_sink=metered.append,
    )
    result = service.run(
        purpose="probe",
        template_id=PROBE_TEMPLATE_ID,
        state={"text": "hello"},
    )
    assert result.status == "ok"
    assert result.calibrated is False
    assert result.provider == "llm"
    assert result.model == "science-model"
    assert metered


def test_typesafe_unavailable_never_calls_chat(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_calls: list[object] = []

    def fake_chat(*_a: object, **_k: object) -> dict[str, Any]:
        chat_calls.append(1)
        return _chat_reply("{}")

    monkeypatch.setattr("openai4s.llm.chat", fake_chat)

    class BoomBackend:
        def evaluate(
            self,
            *,
            state: object,
            questions: Mapping[str, Any],
            model: str,
            timeout: float,
        ) -> None:
            del state, questions, model, timeout
            raise BackendError("unavailable", "typesafe down")

    service = JudgmentService(
        _cfg(tmp_path, provider="typesafe"),
        lambda: MemoryStore(),
        backend_factory=lambda: BoomBackend(),
    )
    result = service.run(
        purpose="probe",
        template_id=PROBE_TEMPLATE_ID,
        state={"text": "hello"},
    )
    assert result.status == "unavailable"
    assert result.error_code == "unavailable"
    assert result.answers == {}
    assert chat_calls == []


def test_default_factory_typesafe_failure_never_calls_chat(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_calls: list[object] = []

    def fake_chat(*_a: object, **_k: object) -> dict[str, Any]:
        chat_calls.append(1)
        return _chat_reply("{}")

    monkeypatch.setattr("openai4s.llm.chat", fake_chat)

    def boom(self: TypeSafeBackend, **_kwargs: object) -> None:
        del self
        raise BackendError("unavailable", "typesafe down")

    monkeypatch.setattr(TypeSafeBackend, "evaluate", boom)
    service = JudgmentService(
        _cfg(tmp_path, provider="typesafe"), lambda: MemoryStore()
    )
    result = service.run(
        purpose="probe",
        template_id=PROBE_TEMPLATE_ID,
        state={"text": "hello"},
    )
    assert result.status == "unavailable"
    assert result.error_code == "unavailable"
    assert chat_calls == []


def test_default_factory_selects_llm_backend_only_when_asked(tmp_path: Any) -> None:
    typesafe_service = JudgmentService(
        _cfg(tmp_path, provider="typesafe"), lambda: MemoryStore()
    )
    typesafe_backend = typesafe_service._default_backend(typesafe_service._flags())
    assert isinstance(typesafe_backend, TypeSafeBackend)
    assert not isinstance(typesafe_backend, LlmBackend)

    llm_service = JudgmentService(_cfg(tmp_path, provider="llm"), lambda: MemoryStore())
    llm_backend = llm_service._default_backend(llm_service._flags())
    assert isinstance(llm_backend, LlmBackend)

    default_flags = ExperimentalJudgmentFlags()
    assert default_flags.provider == "typesafe"


def test_missing_credential_is_unconfigured(tmp_path: Any) -> None:
    def chat(*_a: object, **_k: object) -> dict[str, Any]:
        raise MissingCredentialError("no API key configured")

    with pytest.raises(BackendError) as caught:
        _backend(tmp_path, chat).evaluate(
            state=STATE, questions={"q": NOUL}, model="jev-1.13.0", timeout=3.0
        )
    assert caught.value.code == "unconfigured"


def test_confidence_matches_typesafe_explorer_examples() -> None:
    three = {"a": 0.61, "b": 0.35, "c": 0.04}
    assert distribution_confidence(three, 3) == pytest.approx((3 * 0.61 - 1) / 2)
    peaked = {"a": 0.88, "b": 0.12, "c": 0.0}
    assert distribution_confidence(peaked, 3) == pytest.approx((3 * 0.88 - 1) / 2)
    sure = {"a": 1.0, "b": 0.0, "c": 0.0}
    assert distribution_confidence(sure, 3) == pytest.approx(1.0)
    flat = {"a": 1.0 / 3.0, "b": 1.0 / 3.0, "c": 1.0 / 3.0}
    assert distribution_confidence(flat, 3) == pytest.approx(0.0)
