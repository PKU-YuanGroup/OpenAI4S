"""Literature screen/claim helpers: locate, numeric compare, status mapping."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags, LLMConfig
from openai4s.host.judgment import JudgmentService
from openai4s.host_dispatch import HostDispatcher
from openai4s.judgment.registry import get_template
from openai4s.judgment.templates import literature as lit_templates
from openai4s.judgment.types import Answer, BackendReply, Choice, Noul, Question
from openai4s.kernel import Kernel
from openai4s.skills_loader import SkillLoader

_REPO = Path(__file__).resolve().parents[1]
_KERNEL_PATH = _REPO / "skills" / "literature-review" / "kernel.py"

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
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
    spec = importlib.util.spec_from_file_location(
        "literature_review_kernel", _KERNEL_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kernel_mod():
    return _load_kernel()


class FakeHost:
    def __init__(self, results: list[dict[str, Any]] | dict[str, Any]) -> None:
        self.calls: list[tuple[str, object, dict[str, Any]]] = []
        if isinstance(results, list):
            self._results = list(results)
        else:
            self._results = [results]

    def judge(self, template: str, state: object, **params: Any) -> dict[str, Any]:
        self.calls.append((template, state, dict(params)))
        if not self._results:
            return {"status": "unavailable", "answers": {}, "error_code": "exhausted"}
        if len(self._results) == 1:
            return dict(self._results[0])
        return dict(self._results.pop(0))


def _noul_result(
    *,
    relevant: float = 0.9,
    has_evidence: float = 0.85,
    contradicts: float | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    answers = {
        "relevant": {
            "kind": "noul",
            "value": relevant,
            "probabilities": None,
            "confidence": None,
        },
        "has_evidence": {
            "kind": "noul",
            "value": has_evidence,
            "probabilities": None,
            "confidence": None,
        },
    }
    if contradicts is not None:
        answers["contradicts_hypothesis"] = {
            "kind": "noul",
            "value": contradicts,
            "probabilities": None,
            "confidence": None,
        }
    return {
        "status": status,
        "answers": answers,
        "template_version": "1",
        "purpose": "literature_check",
    }


def _choice_result(
    relation: str,
    *,
    confidence: float = 0.93,
    status: str = "ok",
) -> dict[str, Any]:
    others = [
        name for name in ("supports", "contradicts", "insufficient") if name != relation
    ]
    leftover = max(0.0, 1.0 - confidence)
    share = leftover / 2.0 if others else 0.0
    probs = {relation: confidence}
    for name in others:
        probs[name] = share
    return {
        "status": status,
        "answers": {
            "relation": {
                "kind": "choice",
                "value": relation,
                "probabilities": probs,
                "confidence": confidence,
            }
        },
        "template_version": "1",
        "purpose": "literature_check",
    }


def test_templates_are_registered() -> None:
    screen = get_template(lit_templates.TEMPLATE_ID_SCREEN)
    claim = get_template(lit_templates.TEMPLATE_ID_CLAIM)
    assert screen.purpose == "literature_check"
    assert claim.purpose == "literature_check"
    assert screen.version == lit_templates.TEMPLATE_VERSION
    questions = dict(screen.build_questions({}))
    assert set(questions) == {"relevant", "has_evidence"}
    assert all(isinstance(item, Noul) for item in questions.values())
    with_h = dict(screen.build_questions({"has_hypothesis": True}))
    assert "contradicts_hypothesis" in with_h
    claim_q = dict(claim.build_questions({}))
    relation = claim_q["relation"]
    assert isinstance(relation, Choice)
    assert set(relation.options) == {"supports", "contradicts", "insufficient"}


def test_claim_policy_marks_low_confidence_uncertain() -> None:
    template = get_template(lit_templates.TEMPLATE_ID_CLAIM)
    low = Answer(
        kind="choice",
        value="supports",
        probabilities={"supports": 0.5, "contradicts": 0.3, "insufficient": 0.2},
        confidence=0.5,
    )
    high = Answer(
        kind="choice",
        value="supports",
        probabilities={"supports": 0.9, "contradicts": 0.05, "insufficient": 0.05},
        confidence=0.9,
    )
    assert template.policy({"relation": low}, {}) == "uncertain"
    assert template.policy({"relation": high}, {}) == "ok"


def test_normalize_curly_quotes_newlines_and_hyphens(kernel_mod) -> None:
    raw = "HbA1c fell by 1.1\u2013percentage\npoints versus \u201cplacebo\u201d"
    folded = kernel_mod.normalize_literature_text(raw)
    assert '"' in folded
    assert "\n" not in folded
    assert "\u2013" not in folded
    assert folded == 'HbA1c fell by 1.1-percentage points versus "placebo"'


def test_locate_exact_fuzzy_and_not_found(kernel_mod) -> None:
    source = "The trial found that HbA1c fell by 1.1 percentage points versus placebo."
    exact = kernel_mod.locate_claim(
        source, quote="HbA1c fell by 1.1 percentage points versus placebo"
    )
    assert exact is not None
    assert exact["match"] == "exact"
    assert source[exact["span"]["start"] : exact["span"]["end"]]

    curly = kernel_mod.locate_claim(
        "The trial found that HbA1c fell by 1.1 percentage points versus \u201cplacebo\u201d.",
        quote='HbA1c fell by 1.1 percentage points versus "placebo"',
    )
    assert curly is not None
    assert curly["match"] == "exact"

    wrapped = kernel_mod.locate_claim(
        "The trial found that HbA1c fell by 1.1\npercentage points versus placebo.",
        quote="HbA1c fell by 1.1 percentage points versus placebo",
    )
    assert wrapped is not None
    assert wrapped["match"] == "exact"

    fuzzy = kernel_mod.locate_claim(
        source, quote="HbA1c fell by 1.1 percentage points versus placeboe"
    )
    assert fuzzy is not None
    assert str(fuzzy["match"]).startswith("fuzzy:")
    score = float(str(fuzzy["match"]).split(":", 1)[1])
    assert score >= kernel_mod.FUZZY_MIN

    missing = kernel_mod.locate_claim(
        source, quote="completely fabricated quotation that is not in the source"
    )
    assert missing is None


def test_locate_without_quote_uses_keywords(kernel_mod) -> None:
    source = (
        "Introduction. Methods. Results: metformin reduced HbA1c relative to "
        "placebo in adults with type 2 diabetes. Discussion."
    )
    located = kernel_mod.locate_claim(
        source, claim="Metformin reduced HbA1c versus placebo", window=80
    )
    assert located is not None
    assert "metformin" in located["section"].casefold()


def test_quantities_value_unit_and_percent(kernel_mod) -> None:
    same = kernel_mod.compare_quantities(
        "reduced HbA1c by 1.1%", "HbA1c fell by 1.1 percent versus placebo"
    )
    assert same["mismatch"] is False

    value = kernel_mod.compare_quantities(
        "reduced HbA1c by 1.1%", "HbA1c fell by 0.4% versus placebo"
    )
    assert value["mismatch"] is True
    assert value["missing"]

    unit = kernel_mod.compare_quantities(
        "dose was 5 mg daily", "the daily dose was 5 µg"
    )
    assert unit["mismatch"] is True
    assert unit["unit_conflicts"]

    percent = kernel_mod.extract_quantities("a 40% reduction and 12 mg/d")
    units = {item["unit"] for item in percent}
    assert "%" in units
    assert "mg/d" in units


def test_screen_passages_keeps_contradictions(kernel_mod) -> None:
    host = FakeHost(
        [
            _noul_result(relevant=0.9, has_evidence=0.8, contradicts=0.1),
            _noul_result(relevant=0.7, has_evidence=0.6, contradicts=0.92),
        ]
    )
    kernel_mod.lr_sdk = lambda: host
    rows = kernel_mod.screen_passages(
        "Does metformin reduce HbA1c?",
        [
            {
                "text": "Metformin lowered HbA1c.",
                "source_version_id": "v1",
                "locator": "p.1",
            },
            "A later trial found no effect on HbA1c.",
        ],
        hypothesis="Metformin lowers HbA1c",
    )
    assert len(rows) == 2
    assert rows[0]["source_version_id"] == "v1"
    assert rows[0]["locator"] == "p.1"
    assert rows[1]["text"] == "A later trial found no effect on HbA1c."
    assert rows[1]["contradicts_hypothesis"] == pytest.approx(0.92)
    assert {row["status"] for row in rows} == {"ok"}
    assert all(call[2].get("has_hypothesis") is True for call in host.calls)


def test_screen_passages_omits_hypothesis_question(kernel_mod) -> None:
    host = FakeHost(_noul_result())
    kernel_mod.lr_sdk = lambda: host
    rows = kernel_mod.screen_passages("q", ["passage only"])
    assert rows[0]["contradicts_hypothesis"] is None
    assert host.calls[0][2].get("has_hypothesis") in (None, False)
    assert "hypothesis" not in host.calls[0][1]


def test_check_claims_status_mapping(kernel_mod) -> None:
    source = (
        "HbA1c fell by 1.1 percentage points versus placebo in the treated arm. "
        "The primary endpoint was change in HbA1c."
    )
    sources = {"s1": {"text": source, "version_id": "ver-1"}}
    claims = [
        {
            "claim_id": "verified",
            "claim": "HbA1c fell by 1.1 percentage points versus placebo.",
            "quote": "HbA1c fell by 1.1 percentage points versus placebo",
            "source_id": "s1",
        },
        {
            "claim_id": "contradicted",
            "claim": "The trial showed no change in HbA1c.",
            "quote": "HbA1c fell by 1.1 percentage points versus placebo",
            "source_id": "s1",
        },
        {
            "claim_id": "unsupported",
            "claim": "Blood pressure was the primary endpoint.",
            "quote": "The primary endpoint was change in HbA1c.",
            "source_id": "s1",
        },
        {
            "claim_id": "not_found",
            "claim": "A quote that was never written.",
            "quote": "totally fabricated quote 9f3a",
            "source_id": "s1",
        },
        {
            "claim_id": "numeric",
            "claim": "HbA1c fell by 3.4 percentage points versus placebo.",
            "quote": "HbA1c fell by 1.1 percentage points versus placebo",
            "source_id": "s1",
        },
        {
            "claim_id": "uncertain",
            "claim": "HbA1c fell by 1.1 percentage points versus placebo.",
            "quote": "HbA1c fell by 1.1 percentage points versus placebo",
            "source_id": "s1",
        },
    ]
    host = FakeHost(
        [
            _choice_result("supports", confidence=0.93, status="ok"),
            _choice_result("contradicts", confidence=0.95, status="ok"),
            _choice_result("insufficient", confidence=0.91, status="ok"),
            _choice_result("supports", confidence=0.93, status="ok"),
            _choice_result("supports", confidence=0.4, status="uncertain"),
        ]
    )
    kernel_mod.lr_sdk = lambda: host
    rows = {row["claim_id"]: row for row in kernel_mod.check_claims(claims, sources)}
    assert rows["verified"]["status"] == "verified"
    assert rows["verified"]["version_id"] == "ver-1"
    assert rows["verified"]["match"] == "exact"
    assert rows["verified"]["relation"] == "supports"
    assert rows["contradicted"]["status"] == "contradicted"
    assert rows["unsupported"]["status"] == "unsupported"
    assert rows["not_found"]["status"] == "not_found_needs_review"
    assert rows["not_found"]["relation"] is None
    assert rows["numeric"]["status"] == "numeric_mismatch"
    assert rows["numeric"]["relation"] == "supports"
    assert rows["numeric"]["numeric"]["mismatch"] is True
    assert rows["uncertain"]["status"] == "uncertain"
    judged_ids = [call[0] for call in host.calls]
    assert judged_ids.count("literature.claim") == 5
    assert "not_found" not in [
        c[1].get("claim") for c in host.calls if isinstance(c[1], dict)
    ]


def test_disabled_does_not_raise(kernel_mod) -> None:
    host = FakeHost({"status": "disabled", "answers": {}, "template_version": "1"})
    kernel_mod.lr_sdk = lambda: host
    screened = kernel_mod.screen_passages("q", ["passage"])
    assert screened[0]["status"] == "disabled"
    checked = kernel_mod.check_claims(
        [
            {
                "claim_id": "c1",
                "claim": "HbA1c fell by 1.1 percentage points versus placebo.",
                "quote": "HbA1c fell by 1.1 percentage points versus placebo",
                "source_id": "s1",
            }
        ],
        {
            "s1": {
                "text": "HbA1c fell by 1.1 percentage points versus placebo.",
                "version_id": "v",
            }
        },
    )
    assert checked[0]["status"] == "disabled"


def test_existing_helpers_unchanged(kernel_mod) -> None:
    assert kernel_mod.extract_dois("see doi:10.1234/abc.") == ["10.1234/abc"]
    clean = kernel_mod.style_pass("A short review of metformin and HbA1c.")
    assert clean["ok"] is True
    assert callable(kernel_mod.verify_dois)
    assert callable(kernel_mod.crossref_lookup)


def test_literature_review_declares_typesafe_domain() -> None:
    loader = SkillLoader()
    skill = loader.get("literature-review")
    assert skill is not None
    assert skill.network.mode == "host_only"
    assert "api.typesafe.ai" in skill.network.domains
    assert "api.openalex.org" in skill.network.domains


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
                answers[qid] = Answer(kind="noul", value=0.88)
                continue
            if isinstance(question, Choice):
                names = list(question.options)
                pick = "supports" if "supports" in question.options else names[0]
                leftover = 0.07
                rest = [name for name in names if name != pick]
                share = leftover / float(len(rest)) if rest else 0.0
                probs = {pick: 0.93}
                for name in rest:
                    probs[name] = share
                answers[qid] = Answer(
                    kind="choice",
                    value=pick,
                    probabilities=probs,
                    confidence=0.93,
                )
                continue
            raise TypeError(qid)
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 8, "output_tokens": 0},
            model=model,
        )


def test_service_screen_and_claim_round_trip(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
        experimental_judgment=ExperimentalJudgmentFlags(
            master=True, literature_check=True
        ),
    )
    service = JudgmentService(cfg, None, backend_factory=lambda: backend)
    screen = service.run(
        purpose="literature_check",
        template_id="literature.screen",
        state={"question": "Does it work?", "passage": "Yes, it works."},
        params={},
    )
    assert screen.status == "ok"
    assert set(screen.answers) == {"relevant", "has_evidence"}
    screen_h = service.run(
        purpose="literature_check",
        template_id="literature.screen",
        state={
            "question": "Does it work?",
            "hypothesis": "It works",
            "passage": "It failed.",
        },
        params={"has_hypothesis": True},
    )
    assert "contradicts_hypothesis" in screen_h.answers
    claim = service.run(
        purpose="literature_check",
        template_id="literature.claim",
        state={"claim": "It works", "section": "The method works as stated."},
    )
    assert claim.status == "ok"
    assert claim.answers["relation"].value == "supports"


def test_disabled_flag_skips_backend(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
        experimental_judgment=ExperimentalJudgmentFlags(master=False),
    )
    service = JudgmentService(cfg, None, backend_factory=lambda: backend)
    result = service.run(
        purpose="literature_check",
        template_id="literature.claim",
        state={"claim": "x", "section": "y"},
    )
    assert result.status == "disabled"
    assert backend.calls == []


def test_kernel_cell_check_claims_with_fake_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.providers.typesafe_fake import start_fake

    script = {
        "ids": {
            "relation": {
                "type": "choice",
                "choice": "supports",
                "probabilities": {
                    "supports": 0.93,
                    "contradicts": 0.04,
                    "insufficient": 0.03,
                },
                "confidence": 0.93,
            }
        }
    }
    url, stop = start_fake(port=0, script=script)
    try:
        monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
        monkeypatch.setenv("OPENAI4S_JUDGMENT_LITERATURE", "1")
        monkeypatch.setenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", url)
        monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", "fake-key-DO-NOT-LEAK")
        cfg = Config(
            data_dir=tmp_path / ".data",
            llm=LLMConfig(provider="deepseek", api_key="test-only"),
            experimental_judgment=ExperimentalJudgmentFlags(
                master=True, literature_check=True
            ),
        )
        dispatcher = HostDispatcher(cfg, workspace=tmp_path)
        source = "HbA1c fell by 1.1 percentage points versus placebo in adults."
        cell = (
            "import sys\n"
            "sys.modules['host'] = host\n"
            + _KERNEL_PATH.read_text(encoding="utf-8")
            + "\n"
            + "out = check_claims(\n"
            + "    [{'claim_id': 'c1', 'claim': 'HbA1c fell by 1.1 percentage points versus placebo.',"
            + " 'quote': 'HbA1c fell by 1.1 percentage points versus placebo',"
            + " 'source_id': 's1'}],\n"
            + f"    {{'s1': {{'text': {source!r}, 'version_id': 'ver-e2e'}}}},\n"
            + ")\n"
            + "import json as _json\n"
            + "print(_json.dumps(out[0], sort_keys=True, default=str))\n"
        )
        with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
            result = kernel.execute(cell)
    finally:
        stop()
    assert result["error"] is None, result
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["status"] == "verified", payload
    assert payload["relation"] == "supports"
    assert payload["match"] == "exact"
    assert payload["version_id"] == "ver-e2e"


def test_backend_error_does_not_raise_from_helper(kernel_mod) -> None:
    class Boom:
        def judge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("nope")

    kernel_mod.lr_sdk = lambda: Boom()
    rows = kernel_mod.screen_passages("q", ["p"])
    assert rows[0]["status"] == "unavailable"
    checked = kernel_mod.check_claims(
        [
            {
                "claim_id": "c1",
                "claim": "HbA1c fell by 1.1 percentage points versus placebo.",
                "quote": "HbA1c fell by 1.1 percentage points versus placebo",
                "source_id": "s1",
            }
        ],
        {"s1": {"text": "HbA1c fell by 1.1 percentage points versus placebo."}},
    )
    assert checked[0]["status"] == "uncertain"
