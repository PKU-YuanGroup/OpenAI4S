"""Real kernel worker ``host.judge`` RPC and dispatcher gating decisions."""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags, LLMConfig
from openai4s.host_dispatch import GATEABLE_TOOLS, HostDispatcher
from openai4s.judgment.port import BackendError
from openai4s.judgment.registry import PROBE_TEMPLATE_ID
from openai4s.judgment.types import Answer, BackendReply, Question
from openai4s.kernel import Kernel
from openai4s.sdk.judgment import judge as sdk_judge


class FakeBackend:
    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        answers = {qid: Answer(kind="noul", value=0.88) for qid in questions}
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 4, "output_tokens": 0},
            model=model,
        )


class BoomBackend:
    def evaluate(self, **_kwargs: Any) -> BackendReply:
        raise BackendError("timeout", "boom")


@pytest.fixture(autouse=True)
def _clear_judgment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", raising=False)
    monkeypatch.delenv("OPENAI4S_JUDGMENT_SKILL_SUGGEST", raising=False)


def _dispatcher(tmp_path: Any, backend: Any) -> HostDispatcher:
    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
        experimental_judgment=ExperimentalJudgmentFlags(master=True),
    )
    dispatcher = HostDispatcher(cfg, workspace=tmp_path)
    dispatcher._judgment_service.backend_factory = lambda: backend
    return dispatcher


def test_judge_is_not_gateable_or_screened() -> None:
    assert "judge" not in GATEABLE_TOOLS
    assert "judge" not in HostDispatcher._SCREENED_METHODS


def test_sdk_rejects_direct_questions_list() -> None:
    with pytest.raises(ValueError, match="questions list"):
        sdk_judge(
            lambda _m, _a: None, "system.probe", {"text": "hi"}, questions={"q": {}}
        )


def test_kernel_host_judge_system_probe(tmp_path: Any) -> None:
    dispatcher = _dispatcher(tmp_path, FakeBackend())
    with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "out = host.judge('system.probe', {'text': 'hi'})\n"
            "print(out['status'])\n"
            "print(out['template_id'])\n"
            "print(out['purpose'])"
        )
    assert result["error"] is None, result
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    assert lines[0] == "ok"
    assert lines[1] == PROBE_TEMPLATE_ID
    assert lines[2] == "probe"


def test_kernel_unknown_template_is_runtime_error(tmp_path: Any) -> None:
    dispatcher = _dispatcher(tmp_path, FakeBackend())
    with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "try:\n"
            "    host.judge('no.such.template', {'text': 'x'})\n"
            "    print('not-raised')\n"
            "except RuntimeError as exc:\n"
            "    print('caught')\n"
            "    print('unknown template' in str(exc))"
        )
    assert result["error"] is None, result
    stdout = result["stdout"]
    assert "caught" in stdout
    assert "True" in stdout
    assert "not-raised" not in stdout


def test_kernel_unavailable_is_normal_return(tmp_path: Any) -> None:
    dispatcher = _dispatcher(tmp_path, BoomBackend())
    with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "out = host.judge('system.probe', {'text': 'hi'})\n"
            "print(out['status'])\n"
            "print(out['error_code'])"
        )
    assert result["error"] is None, result
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    assert lines[0] == "unavailable"
    assert lines[1] == "timeout"


def test_kernel_disabled_is_normal_return(tmp_path: Any) -> None:
    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
        experimental_judgment=ExperimentalJudgmentFlags(master=False),
    )
    dispatcher = HostDispatcher(cfg, workspace=tmp_path)
    backend = FakeBackend()
    dispatcher._judgment_service.backend_factory = lambda: backend
    with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "out = host.judge('system.probe', {'text': 'hi'})\n" "print(out['status'])"
        )
    assert result["error"] is None, result
    assert result["stdout"].strip() == "disabled"
