"""text-features skill: custom template limits, featurize shape, test-split isolation."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags, LLMConfig
from openai4s.host.judgment import JudgmentService
from openai4s.host_dispatch import HostDispatcher
from openai4s.judgment.registry import get_template
from openai4s.judgment.templates import features as feat_templates
from openai4s.judgment.types import Answer, BackendReply, Noul, Question, Score
from openai4s.kernel import Kernel
from openai4s.skills_loader import SkillLoader

_REPO = Path(__file__).resolve().parents[1]
_KERNEL_PATH = _REPO / "skills" / "text-features" / "kernel.py"

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
    "OPENAI4S_JUDGMENT_TEXT_FEATURES",
    "OPENAI4S_JUDGMENT_LITERATURE",
    "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
    "OPENAI4S_JUDGMENT_PROVIDER",
    "OPENAI4S_JUDGMENT_MODEL",
    "OPENAI4S_JUDGMENT_TIMEOUT_S",
    "OPENAI4S_TYPESAFE_API_KEY",
    "OPENAI4S_JUDGMENT_FAKE_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clear_judgment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _JUDGMENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _load_kernel():
    spec = importlib.util.spec_from_file_location("text_features_kernel", _KERNEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kernel_mod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(_REPO / "skills"))
    return _load_kernel()


class FakeHost:
    def __init__(
        self,
        results: list[dict[str, Any]] | dict[str, Any] | None = None,
        *,
        llm_text: str = "",
    ) -> None:
        self.calls: list[tuple[str, object, dict[str, Any]]] = []
        self.llm_calls: list[str] = []
        self.llm_text = llm_text
        if results is None:
            self._results: list[dict[str, Any]] = []
            self._repeat_last = False
        elif isinstance(results, list):
            self._results = list(results)
            self._repeat_last = False
        else:
            self._results = [results]
            self._repeat_last = True

    def judge(self, template: str, state: object, **params: Any) -> dict[str, Any]:
        self.calls.append((template, state, dict(params)))
        if not self._results:
            return {"status": "unavailable", "answers": {}, "error_code": "exhausted"}
        if self._repeat_last or len(self._results) == 1:
            return dict(self._results[0])
        return dict(self._results.pop(0))

    def llm(self, request: str, **_kwargs: Any) -> str:
        self.llm_calls.append(request)
        return self.llm_text


def _noul_answer(value: float) -> dict[str, Any]:
    return {
        "kind": "noul",
        "value": value,
        "probabilities": None,
        "confidence": None,
    }


def _score_answer(
    probabilities: Mapping[str, float], *, confidence: float = 0.9
) -> dict[str, Any]:
    expected = sum(int(key) * float(p) for key, p in probabilities.items())
    return {
        "kind": "score",
        "value": expected,
        "probabilities": dict(probabilities),
        "confidence": confidence,
    }


def _ok_result(answers: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "answers": dict(answers),
        "template_id": feat_templates.TEMPLATE_ID,
        "template_version": feat_templates.TEMPLATE_VERSION,
        "purpose": "text_features",
        "usage": {"input_tokens": 4, "output_tokens": 0},
    }


def _noul_spec(
    qid: str, instructions: str = "Does state.text mention a p-value?"
) -> dict[str, Any]:
    return {"id": qid, "kind": "noul", "instructions": instructions}


def _score_spec(
    qid: str,
    *,
    levels: tuple[str, ...] = ("low", "mid", "high"),
    instructions: str = "How strong is the claim in state.text?",
) -> dict[str, Any]:
    return {
        "id": qid,
        "kind": "score",
        "instructions": instructions,
        "levels": list(levels),
    }


class ScriptedBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        self.calls.append(
            {"state": state, "questions": dict(questions), "model": model}
        )
        answers: dict[str, Answer] = {}
        for qid, question in questions.items():
            if isinstance(question, Noul):
                answers[qid] = Answer(kind="noul", value=0.61)
                continue
            if isinstance(question, Score):
                n_levels = len(question.levels)
                mid = (n_levels - 1) // 2
                probs = {str(i): (1.0 if i == mid else 0.0) for i in range(n_levels)}
                answers[qid] = Answer(
                    kind="score",
                    value=float(mid),
                    probabilities=probs,
                    confidence=1.0,
                )
                continue
            raise TypeError(qid)
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 6, "output_tokens": 0},
            model=model,
        )


def test_features_custom_is_registered() -> None:
    template = get_template(feat_templates.TEMPLATE_ID)
    assert template.purpose == "text_features"
    assert template.version == feat_templates.TEMPLATE_VERSION
    spec = template.allow_custom
    assert spec is not None
    assert spec.kinds == ("noul", "score")
    assert spec.max_questions == 24
    assert spec.max_question_chars == 400
    assert spec.max_state_chars == 8000
    questions = dict(
        template.build_questions({"specs": [_noul_spec("a"), _score_spec("b")]})
    )
    assert set(questions) == {"a", "b"}
    assert isinstance(questions["a"], Noul)
    assert isinstance(questions["b"], Score)


def test_features_custom_rejects_kind_count_length_and_state(tmp_path: Path) -> None:
    template = get_template(feat_templates.TEMPLATE_ID)
    with pytest.raises(ValueError, match="noul and score"):
        template.build_questions(
            {
                "specs": [
                    {
                        "id": "x",
                        "kind": "choice",
                        "instructions": "Pick one",
                        "options": {"a": "A", "b": "B"},
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="at most 24"):
        template.build_questions(
            {
                "specs": [
                    _noul_spec(f"q{i}", instructions=f"Is fact {i} in state.text?")
                    for i in range(25)
                ]
            }
        )
    with pytest.raises(ValueError, match="400"):
        template.build_questions(
            {"specs": [_noul_spec("long", instructions="x" * 401)]}
        )
    with pytest.raises(ValueError, match="levels"):
        template.build_questions(
            {
                "specs": [
                    {
                        "id": "s",
                        "kind": "score",
                        "instructions": "Grade state.text",
                        "levels": [f"L{i}" for i in range(11)],
                    }
                ]
            }
        )

    backend = ScriptedBackend()
    service = JudgmentService(
        Config(
            data_dir=tmp_path / ".data",
            llm=LLMConfig(provider="deepseek", api_key="test-only"),
            experimental_judgment=ExperimentalJudgmentFlags(
                master=True, text_features=True
            ),
        ),
        None,
        backend_factory=lambda: backend,
    )
    oversize = service.dispatch(
        {
            "template": feat_templates.TEMPLATE_ID,
            "state": {"id": "r1", "text": "t" * 8001},
            "params": {"specs": [_noul_spec("a")]},
        }
    )
    assert set(oversize) == {"error"}
    assert "8000" in oversize["error"]
    choice = service.dispatch(
        {
            "template": feat_templates.TEMPLATE_ID,
            "state": {"id": "r1", "text": "ok"},
            "params": {
                "specs": [
                    {
                        "id": "x",
                        "kind": "choice",
                        "instructions": "Pick",
                        "options": {"a": "A", "b": "B"},
                    }
                ]
            },
        }
    )
    assert set(choice) == {"error"}


def test_featurize_column_shape_nan_and_score_normalization(kernel_mod) -> None:
    levels = ("a", "b", "c", "d", "e")
    questions = [
        _noul_spec("has_pvalue"),
        _score_spec("strength", levels=levels),
    ]
    # Expected level 2 of 0..4 → 0.5; mass on 0 and 4 equally → sd_norm 0.5.
    mixed = {str(i): (0.5 if i in (0, 4) else 0.0) for i in range(5)}
    host = FakeHost(
        [
            _ok_result(
                {
                    "has_pvalue": _noul_answer(0.8),
                    "strength": _score_answer(mixed),
                }
            ),
            {
                "status": "unavailable",
                "answers": {},
                "error_code": "timeout",
            },
        ]
    )
    kernel_mod.tf_sdk = lambda: host
    table = kernel_mod.featurize(
        [
            {"id": "ok", "text": "p = 0.01, strong effect"},
            {"id": "miss", "text": "no judge"},
        ],
        questions,
        text_field="text",
        id_field="id",
    )
    assert table["ids"] == ["ok", "miss"]
    assert set(table["columns"]) == {"has_pvalue", "strength", "strength_sd"}
    assert table["columns"]["has_pvalue"][0] == pytest.approx(0.8)
    assert table["columns"]["strength"][0] == pytest.approx(0.5)
    assert table["columns"]["strength_sd"][0] == pytest.approx(0.5)
    assert math.isnan(table["columns"]["has_pvalue"][1])
    assert math.isnan(table["columns"]["strength"][1])
    assert math.isnan(table["columns"]["strength_sd"][1])
    assert table["unavailable_count"] == 1
    assert table["row_status"] == ["ok", "unavailable"]
    sources = {row["column"]: row for row in table["feature_sources"]}
    assert sources["has_pvalue"]["instructions"]
    assert sources["has_pvalue"]["template_version"] == feat_templates.TEMPLATE_VERSION
    assert host.calls[0][0] == feat_templates.TEMPLATE_ID
    assert "specs" in host.calls[0][2]


def test_test_split_is_not_judged_until_frozen(kernel_mod) -> None:
    questions = [_noul_spec("mentions_trial")]
    host = FakeHost(_ok_result({"mentions_trial": _noul_answer(0.7)}))
    kernel_mod.tf_sdk = lambda: host
    rows = [
        {"id": f"dev-{i}", "text": f"dev row {i}", "label": i % 2, "group": i % 2}
        for i in range(8)
    ] + [
        {"id": f"test-{i}", "text": f"test row {i}", "label": i % 2, "group": 10 + i}
        for i in range(8)
    ]
    seen: list[list[Any]] = []
    original = kernel_mod.featurize

    def wrapped(batch, q, **kwargs):
        seen.append([row.get("id") for row in batch])
        return original(batch, q, **kwargs)

    kernel_mod.featurize = wrapped
    kernel_mod.propose_questions = lambda *a, **k: questions
    result = kernel_mod.run_feature_study(
        rows,
        target="label",
        split_by="group",
        rounds=1,
        text_field="text",
        id_field="id",
        n_questions=1,
        task_description="Predict label from text",
    )
    assert result["status"] == "ok"
    test_ids = set(result["split"]["test"])
    dev_ids = set(result["split"]["dev"])
    assert test_ids
    assert dev_ids.isdisjoint(test_ids)
    first_test_batch = next(
        (i for i, batch in enumerate(seen) if set(batch) & test_ids), None
    )
    assert first_test_batch is not None
    for batch in seen[:first_test_batch]:
        assert set(batch).isdisjoint(test_ids)
        assert set(batch) <= dev_ids | {None}
    freeze_at = next(
        i
        for i, call in enumerate(host.calls)
        if isinstance(call[1], dict) and call[1].get("id") in test_ids
    )
    before = [
        call[1].get("id")
        for call in host.calls[:freeze_at]
        if isinstance(call[1], dict)
    ]
    assert set(before).isdisjoint(test_ids)


def test_disabled_featurize_does_not_raise(kernel_mod) -> None:
    host = FakeHost({"status": "disabled", "answers": {}, "error_code": None})
    kernel_mod.tf_sdk = lambda: host
    table = kernel_mod.featurize(
        [{"id": "a", "text": "secret note"}],
        [_noul_spec("q")],
        text_field="text",
        id_field="id",
    )
    assert table["status"] == "disabled"
    assert math.isnan(table["columns"]["q"][0])


def test_text_features_skill_is_discovered_by_loader() -> None:
    loader = SkillLoader()
    skill = loader.get("text-features")
    assert skill is not None
    assert skill.has_kernel
    assert skill.sidecar_gate()["ok"] is True
    assert skill.origin == "openai4s"
    names = set(loader.discover())
    assert "text-features" in names


def test_text_features_skill_capability_frontmatter() -> None:
    loader = SkillLoader()
    skill = loader.get("text-features")
    assert skill is not None
    assert skill.network.mode == "host_only"
    assert "api.typesafe.ai" in skill.network.domains
    body = (_REPO / "skills" / "text-features" / "SKILL.md").read_text(encoding="utf-8")
    assert "human gold standard" in body.lower() or "人工金标准" in body
    assert "United States" in body or "美国" in body


def test_service_features_custom_round_trip(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = JudgmentService(
        Config(
            data_dir=tmp_path / ".data",
            llm=LLMConfig(provider="deepseek", api_key="test-only"),
            experimental_judgment=ExperimentalJudgmentFlags(
                master=True, text_features=True
            ),
        ),
        None,
        backend_factory=lambda: backend,
    )
    result = service.run(
        purpose="text_features",
        template_id=feat_templates.TEMPLATE_ID,
        state={"id": "r1", "text": "The trial reported p=0.02."},
        params={"specs": [_noul_spec("has_p"), _score_spec("tone")]},
    )
    assert result.status == "ok"
    assert set(result.answers) == {"has_p", "tone"}
    assert result.answers["has_p"].kind == "noul"
    assert result.answers["tone"].kind == "score"


def test_kernel_cell_featurize_with_fake_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.providers.typesafe_fake import start_fake

    url, stop = start_fake(port=0)
    try:
        monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
        monkeypatch.setenv("OPENAI4S_JUDGMENT_TEXT_FEATURES", "1")
        monkeypatch.setenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", url)
        monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", "fake-key-DO-NOT-LEAK")
        cfg = Config(
            data_dir=tmp_path / ".data",
            llm=LLMConfig(provider="deepseek", api_key="test-only"),
            experimental_judgment=ExperimentalJudgmentFlags(
                master=True, text_features=True
            ),
        )
        dispatcher = HostDispatcher(cfg, workspace=tmp_path)
        cell = (
            "import sys, json, math\n"
            "sys.modules['host'] = host\n"
            + _KERNEL_PATH.read_text(encoding="utf-8")
            + "\n"
            + "rows = [\n"
            + "    {'id': 'r1', 'text': 'Significant reduction, p=0.01.'},\n"
            + "    {'id': 'r2', 'text': 'No difference was observed.'},\n"
            + "]\n"
            + "questions = [\n"
            + "    {'id': 'has_pvalue', 'kind': 'noul',\n"
            + "     'instructions': 'Does state.text mention a p-value?'},\n"
            + "    {'id': 'certainty', 'kind': 'score',\n"
            + "     'instructions': 'How certain is the finding in state.text?',\n"
            + "     'levels': ['none', 'low', 'mid', 'high', 'certain']},\n"
            + "]\n"
            + "out = featurize(rows, questions, text_field='text', id_field='id')\n"
            + "summary = {\n"
            + "    'status': out['status'],\n"
            + "    'ids': out['ids'],\n"
            + "    'columns': sorted(out['columns']),\n"
            + "    'unavailable_count': out['unavailable_count'],\n"
            + "    'has_pvalue': out['columns']['has_pvalue'],\n"
            + "    'certainty': out['columns']['certainty'],\n"
            + "    'certainty_sd': out['columns']['certainty_sd'],\n"
            + "    'template_version': out['template_version'],\n"
            + "}\n"
            + "print(json.dumps(summary, sort_keys=True))\n"
        )
        with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
            result = kernel.execute(cell)
    finally:
        stop()
    assert result["error"] is None, result
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["status"] == "ok", payload
    assert payload["ids"] == ["r1", "r2"]
    assert payload["columns"] == ["certainty", "certainty_sd", "has_pvalue"]
    assert payload["unavailable_count"] == 0
    assert payload["has_pvalue"] == [0.5, 0.5]
    assert payload["certainty"] == [0.5, 0.5]
    assert payload["certainty_sd"] == [0.0, 0.0]
    assert payload["template_version"] == feat_templates.TEMPLATE_VERSION
