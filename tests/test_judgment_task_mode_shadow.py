"""Task-mode shadow recording: identity, non-blocking, explicit skip, redaction."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Mapping

import pytest

from openai4s.agent.task_modes import TaskMode, resolve_task_mode
from openai4s.config import Config, ExperimentalJudgmentFlags
from openai4s.host.judgment import JudgmentService
from openai4s.judgment.port import BackendError
from openai4s.judgment.registry import get_template
from openai4s.judgment.task_mode_shadow import (
    QUEUE_CAPACITY,
    bind,
    pause_workers,
    reset_for_tests,
    resume_workers,
    stats,
    submit,
    wait_idle,
)
from openai4s.judgment.templates.task_mode import (
    OPTIONS,
    PURPOSE,
    TEMPLATE_ID,
    shadow_agree,
)
from openai4s.judgment.types import Answer, BackendReply, Choice, Noul, Question

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

# ≥30 requests covering all three modes, zh/en, weak signals, and both-family.
CORPUS: tuple[str, ...] = (
    "Analyze this CSV and plot the distribution of the residuals",
    "Run the ADMET pipeline on these seed molecules and report the top hits",
    "run this code and tell me what the error means",
    "跑一下这段代码，看看为什么报错",
    "用这个管线跑一遍数据，给我一份报告",
    "What does this module do?",
    "Summarise the findings in the attached report",
    "plot a histogram of the expression values",
    "解释一下这份质谱结果",
    "compute the mean of [1, 2, 3]",
    "Build a reusable pipeline I can rerun next week on new samples",
    "Turn this into a repeatable workflow with a CLI entry point",
    "please make the pipeline reproducible so we can re-run it monthly",
    "写一个可复用的分析管线，以后每个月都能重跑",
    "把这套流程工程化成一个可以复用的工作流",
    "package this workflow as a parameterized CLI",
    "make a repeatable entry-point for the analysis pipeline",
    "把这个工作流做成可重复的命令行工具",
    "Refactor the repository so the loaders live in their own module",
    "restructure this codebase and update pyproject accordingly",
    "Read AGENTS.md first, then modularize the package",
    "重构一下这个仓库的模块划分",
    "把源码里的这几个模块拆分开",
    "REFACTOR THE CODEBASE INTO PACKAGES",
    "split the helpers into their own module in this repo",
    "reorganise the source files under the existing package",
    "Refactor the repository into modules and give me a reusable pipeline I can rerun",
    "recode the ordinal columns and rewrite the labels",
    "restructure the packaged-goods sales table",
    "rerun the pipeline with the new seeds",
    "Please refactor my plotting code so the figure is cleaner and split the helpers into their own module",
    "tidy this notebook and add a couple of figures",
    "帮我看看这份数据有没有异常值",
    "工程化这个仓库并拆分模块",
    "build a productionized workflow with an entry point I can re-run",
    "change the codebase: refactor the parsers into a package",
)

assert len(CORPUS) >= 30


class ChoiceBackend:
    def __init__(
        self,
        *,
        pick: str | None = None,
        delay: float = 0.0,
        error: BaseException | None = None,
        confidence: float = 0.91,
    ) -> None:
        self.pick = pick
        self.delay = delay
        self.error = error
        self.confidence = confidence
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        with self._lock:
            self.calls.append(
                {
                    "state": state,
                    "questions": dict(questions),
                    "model": model,
                    "timeout": timeout,
                }
            )
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        answers: dict[str, Answer] = {}
        for qid, question in questions.items():
            if isinstance(question, Choice):
                names = list(question.options)
                pick = self.pick if self.pick in question.options else names[0]
                leftover = 0.06
                rest = [name for name in names if name != pick]
                share = leftover / float(len(rest)) if rest else 0.0
                probs = {pick: round(1.0 - leftover, 6)}
                for name in rest:
                    probs[name] = share
                total = sum(probs.values())
                probs[pick] = round(probs[pick] + (1.0 - total), 6)
                answers[qid] = Answer(
                    kind="choice",
                    value=pick,
                    probabilities=probs,
                    confidence=self.confidence,
                )
            elif isinstance(question, Noul):
                answers[qid] = Answer(kind="noul", value=0.1)
            else:
                raise TypeError(qid)
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 8, "output_tokens": 0},
            model=model,
        )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


def _cfg(tmp_path: Any) -> Config:
    return Config(
        data_dir=tmp_path,
        experimental_judgment=ExperimentalJudgmentFlags(
            master=True, task_mode_shadow=True
        ),
    )


def _service(tmp_path: Any, backend: ChoiceBackend) -> JudgmentService:
    return JudgmentService(
        _cfg(tmp_path),
        lambda: None,
        backend_factory=lambda: backend,
    )


def _enable(tmp_path: Any, backend: ChoiceBackend) -> JudgmentService:
    service = _service(tmp_path, backend)
    bind(service=service, enabled=True)
    return service


def test_template_is_a_structured_three_way_choice() -> None:
    template = get_template(TEMPLATE_ID)
    assert template.purpose == PURPOSE
    questions = template.build_questions({})
    assert set(questions) == {"mode"}
    question = questions["mode"]
    assert isinstance(question, Choice)
    assert tuple(question.options) == OPTIONS
    for name, description in question.options.items():
        assert isinstance(description, dict)
        assert set(description) == {"what", "not_for", "examples"}
        for key in ("what", "not_for", "examples"):
            assert str(description[key]).strip()
    assert "state.request" in question.instructions
    payload = question.to_api()
    assert payload["type"] == "choice"
    assert set(payload["criteria"]) == set(OPTIONS)


def test_shadow_agree_compares_labels() -> None:
    assert shadow_agree("analysis_run", "analysis_run", 0.9) is True
    assert shadow_agree("analysis_run", "codebase_change", 0.9) is False
    assert shadow_agree("analysis_run", None, None) is None
    assert shadow_agree("analysis_run", "not_a_mode", 0.9) is None


def test_default_off_does_not_submit() -> None:
    for text in CORPUS:
        resolve_task_mode(text)
    assert stats()["submitted"] == 0
    assert stats()["dropped"] == 0
    assert stats()["processed"] == 0


def test_returns_match_with_shadow_on_and_off(tmp_path: Any) -> None:
    off = [resolve_task_mode(text) for text in CORPUS]
    backend = ChoiceBackend(pick="analysis_run")
    _enable(tmp_path, backend)
    on = [resolve_task_mode(text) for text in CORPUS]
    assert off == on
    assert [item.value for item in off] == [item.value for item in on]
    assert wait_idle(timeout=5.0)
    assert stats()["submitted"] == len(CORPUS)
    assert len(backend.calls) == len(CORPUS)


def test_slow_backend_does_not_block_the_return(tmp_path: Any) -> None:
    backend = ChoiceBackend(delay=5.0, pick="analysis_run")
    _enable(tmp_path, backend)
    started = time.monotonic()
    mode = resolve_task_mode(
        "Refactor the repository so the loaders live in their own module"
    )
    elapsed = time.monotonic() - started
    assert mode is TaskMode.CODEBASE_CHANGE
    assert elapsed < 0.5
    assert stats()["submitted"] == 1


def test_backend_exceptions_do_not_change_the_return(tmp_path: Any) -> None:
    backend = ChoiceBackend(error=RuntimeError("shadow exploded"))
    _enable(tmp_path, backend)
    text = "Build a reusable pipeline I can rerun next week on new samples"
    assert resolve_task_mode(text) is TaskMode.REUSABLE_PIPELINE
    assert wait_idle(timeout=5.0)
    assert stats()["submitted"] == 1


def test_backend_error_is_isolated(tmp_path: Any) -> None:
    backend = ChoiceBackend(error=BackendError("timeout", "slow"))
    _enable(tmp_path, backend)
    assert resolve_task_mode("plot the residuals") is TaskMode.ANALYSIS_RUN
    assert wait_idle(timeout=5.0)


def test_explicit_selection_is_not_submitted(tmp_path: Any) -> None:
    backend = ChoiceBackend(pick="codebase_change")
    _enable(tmp_path, backend)
    text = "重构这个仓库的模块划分并写一个可复用的管线"
    assert resolve_task_mode(text, explicit="analysis_run") is TaskMode.ANALYSIS_RUN
    assert (
        resolve_task_mode(text, explicit=TaskMode.REUSABLE_PIPELINE)
        is TaskMode.REUSABLE_PIPELINE
    )
    assert wait_idle(timeout=2.0)
    assert stats()["submitted"] == 0
    assert stats()["skipped_explicit"] == 0
    assert backend.calls == []
    submit(request=text, rule_mode="analysis_run", explicit=True)
    assert stats()["skipped_explicit"] == 1
    assert stats()["submitted"] == 0


def test_blank_explicit_still_detects_and_may_submit(tmp_path: Any) -> None:
    backend = ChoiceBackend(pick="analysis_run")
    _enable(tmp_path, backend)
    assert resolve_task_mode("plot the data", explicit="") is TaskMode.ANALYSIS_RUN
    assert wait_idle(timeout=5.0)
    assert stats()["submitted"] == 1


def test_audit_event_has_no_raw_request(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "UNIQUE_TASK_MODE_REQUEST_TOKEN_7f3a9c_do_not_log"
    records: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, /, **fields: Any) -> dict[str, Any]:
        records.append((event, dict(fields)))
        return {"event": event, **fields}

    monkeypatch.setattr("openai4s.observability.log_event", _capture)
    backend = ChoiceBackend(pick="codebase_change")
    _enable(tmp_path, backend)
    text = f"Refactor the repository so the loaders live in their own module {marker}"
    assert resolve_task_mode(text) is TaskMode.CODEBASE_CHANGE
    assert wait_idle(timeout=5.0)
    shadow = [fields for event, fields in records if event == "judgment_shadow"]
    assert shadow
    for fields in shadow:
        dumped = json.dumps(fields, ensure_ascii=False, default=str)
        assert marker not in dumped
        assert "request" not in fields
        assert fields["kind"] == "task_mode"
        assert fields["explicit"] is False
        assert fields["rule_mode"] == "codebase_change"
        assert fields["shadow_choice"] == "codebase_change"
        assert fields["agree"] is True
        assert isinstance(fields["probabilities"], dict)
        assert fields["confidence"] == pytest.approx(0.91)
    for event, fields in records:
        dumped = json.dumps(fields, ensure_ascii=False, default=str)
        assert marker not in dumped, event


def test_queue_full_drops_and_counts(tmp_path: Any) -> None:
    backend = ChoiceBackend(pick="analysis_run")
    _enable(tmp_path, backend)
    pause_workers()
    for index in range(QUEUE_CAPACITY):
        submit(
            request=f"plot series {index}",
            rule_mode="analysis_run",
            explicit=False,
        )
    assert stats()["submitted"] == QUEUE_CAPACITY
    assert stats()["dropped"] == 0
    submit(request="overflow", rule_mode="analysis_run", explicit=False)
    assert stats()["dropped"] == 1
    assert stats()["submitted"] == QUEUE_CAPACITY
    resume_workers()
    assert wait_idle(timeout=10.0)
    assert stats()["processed"] == QUEUE_CAPACITY


def test_capability_off_is_zero_overhead_with_slow_backend(tmp_path: Any) -> None:
    backend = ChoiceBackend(delay=5.0)
    bind(service=_service(tmp_path, backend), enabled=False)
    started = time.monotonic()
    for text in CORPUS[:10]:
        resolve_task_mode(text)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert backend.calls == []
    assert stats()["submitted"] == 0
