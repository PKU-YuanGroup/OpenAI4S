"""Skill semantic suggestion: allowlist, disabled, explicit skip, wrapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from openai4s.config import Config, ExperimentalJudgmentFlags
from openai4s.host.judgment import JudgmentService
from openai4s.host.skills import SkillService
from openai4s.judgment.port import BackendError
from openai4s.judgment.registry import get_template, unregister_template
from openai4s.judgment.templates import skills as skill_templates
from openai4s.judgment.types import Answer, BackendReply, Choice, Noul, Question
from openai4s.tools.registry import format_tool_result
from openai4s.tools.skills import SearchSkillsTool

_JUDGMENT_ENV = (
    "OPENAI4S_EXPERIMENTAL_JUDGMENT",
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


class ScriptedBackend:
    """Offline JudgmentBackend with Choice + Noul answers."""

    def __init__(
        self,
        *,
        noul: float = 0.9,
        noul_by_id: dict[str, float] | None = None,
        error: BackendError | None = None,
        prefer_bioskills: bool = False,
    ) -> None:
        self.noul = noul
        self.noul_by_id = dict(noul_by_id or {})
        self.error = error
        self.prefer_bioskills = prefer_bioskills
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
            {
                "state": state,
                "questions": dict(questions),
                "model": model,
                "timeout": timeout,
            }
        )
        if self.error is not None:
            raise self.error
        answers: dict[str, Answer] = {}
        for qid, question in questions.items():
            if isinstance(question, Noul):
                value = self.noul_by_id.get(qid, self.noul)
                answers[qid] = Answer(kind="noul", value=float(value))
                continue
            if isinstance(question, Choice):
                names = list(question.options)
                if (
                    self.prefer_bioskills
                    and qid == "best_skill"
                    and skill_templates.BIOSKILLS_ID in question.options
                ):
                    pick = skill_templates.BIOSKILLS_ID
                else:
                    pick = names[0]
                answers[qid] = _choice_answer(names, pick)
                continue
            raise TypeError(f"unexpected question {qid}")
        return BackendReply(
            answers=answers,
            usage={"input_tokens": 8, "output_tokens": 0},
            model=model,
        )


def _choice_answer(names: list[str], pick: str) -> Answer:
    if pick not in names:
        pick = names[0]
    if len(names) == 1:
        probs = {pick: 1.0}
    else:
        leftover = 0.4
        rest = [name for name in names if name != pick]
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


def _write_skill(root: Path, directory: str, name: str, description: str) -> None:
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\norigin: openai4s\n---\n"
        f"# {name}\n{description}\nFollow the documented procedure.\n",
        encoding="utf-8",
    )


def _tiny_skills(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "alpha-skill", "alpha", "Curated clustering helper")
    _write_skill(skills, "beta-skill", "beta", "Curated plotting helper")
    bios = skills / "bioskills"
    bios.mkdir()
    (bios / "COLLECTION.json").write_text(
        json.dumps(
            {
                "id": "bioskills",
                "prompt_line": "bioSkills collection: {count} recipes.",
            }
        ),
        encoding="utf-8",
    )
    _write_skill(
        bios,
        "bio-single-cell-demo",
        "bio-single-cell-demo",
        "Single-cell annotation recipe",
    )
    _write_skill(
        bios,
        "bio-hi-c-demo",
        "bio-hi-c-demo",
        "Hi-C contact matrix recipe",
    )
    return skills


def _cfg(tmp_path: Path, skills_dir: Path, **flag_kw: Any) -> Config:
    cfg = Config(
        data_dir=tmp_path / "data",
        skills_dir=skills_dir,
        experimental_judgment=ExperimentalJudgmentFlags(
            master=True, skill_suggest=True, **flag_kw
        ),
    )
    cfg.ensure_dirs()
    return cfg


def _service(
    tmp_path: Path,
    backend: ScriptedBackend,
    *,
    skills_dir: Path | None = None,
    cfg: Config | None = None,
) -> SkillService:
    skills_dir = skills_dir or _tiny_skills(tmp_path)
    cfg = cfg or _cfg(tmp_path, skills_dir)
    judgment = JudgmentService(
        cfg,
        lambda: None,
        backend_factory=lambda: backend,
    )
    return SkillService(cfg, judgment_service=judgment)


class FakeRuntime:
    def __init__(self, rows: Any, semantic: Any) -> None:
        self.rows = rows
        self.semantic = semantic
        self.calls: list[tuple[str, Any]] = []

    def invoke(self, method: str, *arguments: Any) -> Any:
        spec = arguments[0] if arguments else None
        self.calls.append((method, spec))
        if method == "search_skills":
            return self.rows
        if method == "suggest_skills":
            return self.semantic
        raise RuntimeError(f"unexpected method {method}")


def _choice_option_names(backend: ScriptedBackend) -> set[str]:
    names: set[str] = set()
    for call in backend.calls:
        for question in call["questions"].values():
            if isinstance(question, Choice):
                names.update(question.options)
    return names


def test_templates_are_registered() -> None:
    for template_id in (
        skill_templates.TEMPLATE_ID,
        skill_templates.TEMPLATE_ID_BIO,
        skill_templates.TEMPLATE_ID_FIT,
    ):
        template = get_template(template_id)
        assert template.purpose == skill_templates.PURPOSE
        assert template.policy_version == skill_templates.POLICY_VERSION
        assert "gate=" in template.policy_version


def test_policy_version_tracks_thresholds() -> None:
    original = skill_templates.policy_version()
    assert f"gate={skill_templates.GATE:.2f}" in original
    assert f"fit={skill_templates.FIT:.2f}" in original
    old = skill_templates.GATE
    try:
        skill_templates.GATE = 0.99
        assert skill_templates.policy_version() != original
        assert "gate=0.99" in skill_templates.policy_version()
    finally:
        skill_templates.GATE = old


def test_policy_version_is_in_the_cache_key(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = _service(tmp_path, backend)
    query = {"query": "cluster these cells"}
    first = service.suggest(query)
    assert first["semantic_status"] in {"ok", "uncertain"}
    n_calls = len(backend.calls)
    second = service.suggest(query)
    assert second["semantic_status"] == first["semantic_status"]
    assert len(backend.calls) == n_calls

    old = skill_templates.GATE
    try:
        for template_id in (
            skill_templates.TEMPLATE_ID,
            skill_templates.TEMPLATE_ID_BIO,
            skill_templates.TEMPLATE_ID_FIT,
        ):
            unregister_template(template_id)
        skill_templates.GATE = 0.99
        skill_templates._register()
        service.suggest(query)
        assert len(backend.calls) > n_calls
        assert "gate=0.99" in get_template(skill_templates.TEMPLATE_ID).policy_version
    finally:
        skill_templates.GATE = old
        for template_id in (
            skill_templates.TEMPLATE_ID,
            skill_templates.TEMPLATE_ID_BIO,
            skill_templates.TEMPLATE_ID_FIT,
        ):
            try:
                unregister_template(template_id)
            except ValueError:
                pass
        skill_templates._register()


def test_allowlist_is_applied_before_candidates(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = _service(tmp_path, backend)
    service.set_allowed_skills(["alpha"])
    result = service.suggest({"query": "cluster single cell data"})
    option_names = _choice_option_names(backend)
    assert "beta" not in option_names
    assert "bio-single-cell-demo" not in option_names
    assert "bio-hi-c-demo" not in option_names
    assert skill_templates.BIOSKILLS_ID not in option_names
    for suggestion in result["semantic_suggestions"]:
        assert suggestion["name"] == "alpha"


def test_disabled_skills_are_not_candidates(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = _service(tmp_path, backend)
    service.loader.discover()
    service.loader.set_enabled("beta", False)
    service.suggest({"query": "make a plot"})
    option_names = _choice_option_names(backend)
    assert "beta" not in option_names
    assert "alpha" in option_names


def test_explicit_name_skips_judgment(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = _service(tmp_path, backend)
    exact = service.suggest({"query": "alpha"})
    assert exact["semantic_status"] == "skipped_explicit"
    assert exact["semantic_suggestions"] == []
    assert backend.calls == []
    styled = service.suggest({"query": 'please load_skill("alpha") first'})
    assert styled["semantic_status"] == "skipped_explicit"
    assert backend.calls == []


def test_unavailable_keeps_lexical_results_and_status() -> None:
    rows = [{"name": "alpha", "description": "cluster", "doc": "short"}]
    semantic = {
        "semantic_status": "unavailable",
        "semantic_suggestions": [],
        "error_code": "timeout",
    }
    runtime = FakeRuntime(rows, semantic)
    tool = SearchSkillsTool()
    out = tool.execute(runtime, {"query": "cluster", "limit": 5})
    assert out["results"] == rows
    assert out["semantic_status"] == "unavailable"
    assert out["semantic_suggestions"] == []


def test_search_skills_disabled_matches_lexical_list_bytes() -> None:
    rows = [
        {"name": "alpha", "description": "cluster", "doc": "body"},
        {"name": "beta", "description": "plot", "doc": "body"},
    ]
    runtime = FakeRuntime(
        rows,
        {
            "semantic_status": "disabled",
            "semantic_suggestions": [{"name": "should-not-appear"}],
        },
    )
    tool = SearchSkillsTool()
    out = tool.execute(runtime, {"query": "cluster", "limit": 5})
    fitted = tool.fit_to_budget(rows)
    assert out == fitted
    assert isinstance(out, list)
    assert json.dumps(out, sort_keys=True) == json.dumps(fitted, sort_keys=True)


def test_chinese_query_enters_semantic_path(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    service = _service(tmp_path, backend)
    query = "帮我对这批单细胞数据做细胞类型注释"
    lexical = service.search({"query": query, "limit": 5})
    assert lexical == []
    result = service.suggest({"query": query})
    assert result["semantic_status"] in {"ok", "uncertain"}
    assert backend.calls, "pure Chinese query must reach the judgment backend"
    assert result["requests"] >= 1
    assert result["requests"] <= skill_templates.MAX_REQUESTS


def test_requests_capped_at_three(tmp_path: Path) -> None:
    backend = ScriptedBackend(prefer_bioskills=True)
    catalog = skill_templates.SuggestCatalog(
        curated=(
            skill_templates.SkillCandidate(
                "alpha", "clustering", "1", None, "alpha body " * 40
            ),
        ),
        collections=(
            skill_templates.SkillCandidate(
                "bioskills", "bio collection", "1", "bioskills", ""
            ),
        ),
        areas=(
            skill_templates.AreaBucket(
                "single",
                "single-cell",
                "single-cell recipes",
                ("bio-single-cell-demo",),
            ),
            skill_templates.AreaBucket(
                "hi", "Hi-C", "Hi-C recipes", ("bio-hi-c-demo",)
            ),
        ),
        by_name={
            "alpha": skill_templates.SkillCandidate(
                "alpha", "clustering", "1", None, "alpha body " * 40
            ),
            "bio-single-cell-demo": skill_templates.SkillCandidate(
                "bio-single-cell-demo",
                "annotation",
                "1",
                "bioskills",
                "single-cell body " * 40,
            ),
            "bio-hi-c-demo": skill_templates.SkillCandidate(
                "bio-hi-c-demo", "hic", "1", "bioskills", "hic body " * 40
            ),
        },
    )
    cfg = _cfg(tmp_path, _tiny_skills(tmp_path))
    judgment = JudgmentService(cfg, lambda: None, backend_factory=lambda: backend)
    result = skill_templates.suggest(
        request="annotate these cells",
        catalog=catalog,
        run=judgment.run,
    )
    assert len(backend.calls) <= 3
    assert result["requests"] <= 3
    assert result["requests"] == len(backend.calls)
    for call in backend.calls:
        for question in call["questions"].values():
            if isinstance(question, Choice):
                assert 2 <= len(question.options) <= 255


def test_choice_options_capped_at_255(tmp_path: Path) -> None:
    options = {f"skill-{index:03d}": "x" * (index + 1) for index in range(300)}
    capped, truncated = skill_templates.cap_choice_options(
        options,
        none_key=skill_templates.NONE_OF_THESE,
        none_desc="none",
    )
    assert truncated is True
    assert len(capped) == 255
    assert skill_templates.NONE_OF_THESE in capped
    catalog = skill_templates.SuggestCatalog(
        curated=tuple(
            skill_templates.SkillCandidate(
                f"skill-{index:03d}", "desc", "1", None, "body"
            )
            for index in range(300)
        ),
        collections=(),
        areas=(),
        by_name={
            f"skill-{index:03d}": skill_templates.SkillCandidate(
                f"skill-{index:03d}", "desc", "1", None, "body"
            )
            for index in range(300)
        },
    )

    class CaptureBackend(ScriptedBackend):
        pass

    backend = CaptureBackend()
    cfg = Config(
        data_dir=tmp_path / "data",
        experimental_judgment=ExperimentalJudgmentFlags(
            master=True, skill_suggest=True
        ),
    )
    judgment = JudgmentService(cfg, lambda: None, backend_factory=lambda: backend)
    skill_templates.suggest(
        request="do the analysis",
        catalog=catalog,
        run=judgment.run,
    )
    assert backend.calls
    best = backend.calls[0]["questions"]["best_skill"]
    assert isinstance(best, Choice)
    assert len(best.options) <= 255


def test_low_gate_recommends_nothing(tmp_path: Path) -> None:
    backend = ScriptedBackend(noul=0.05)
    service = _service(tmp_path, backend)
    result = service.suggest({"query": "what is a p-value"})
    assert result["semantic_suggestions"] == []
    assert result["requests"] == 1


def test_search_skills_wraps_when_enabled_and_fits_budget() -> None:
    rows = [{"name": "alpha", "description": "cluster", "doc": "short"}]
    suggestions = [
        {
            "name": "alpha",
            "p_fit": 0.83,
            "choice_prob": 0.61,
            "confidence": 0.74,
            "stage": "curated",
            "reason_fields": ["description", "skill_md_head"],
            "template_version": skill_templates.TEMPLATE_VERSION,
        }
    ]
    runtime = FakeRuntime(
        rows,
        {
            "semantic_status": "ok",
            "semantic_suggestions": suggestions,
        },
    )
    tool = SearchSkillsTool()
    out = tool.execute(runtime, {"query": "cluster", "limit": 5})
    assert out["results"] == rows
    assert out["semantic_status"] == "ok"
    assert out["semantic_suggestions"] == suggestions
    rendered = format_tool_result(tool, out)
    assert rendered.startswith("[Tool: search_skills]")
    assert "alpha" in rendered


def test_semantic_reason_fields_are_dropped_first() -> None:
    tool = SearchSkillsTool(output_limit=400)
    payload = {
        "results": [{"name": "alpha", "doc": "x" * 50}],
        "semantic_status": "ok",
        "semantic_suggestions": [
            {
                "name": "alpha",
                "p_fit": 0.9,
                "choice_prob": 0.8,
                "confidence": 0.9,
                "stage": "curated",
                "reason_fields": ["description", "skill_md_head"],
                "template_version": "1",
            }
        ],
    }
    fitted = tool._fit_semantic(payload)
    assert fitted["semantic_status"] == "ok"
    suggestions = fitted["semantic_suggestions"]
    if suggestions:
        assert suggestions[0].get("reason_fields") == []


def test_suggest_does_not_run_when_flag_off(tmp_path: Path) -> None:
    backend = ScriptedBackend()
    skills_dir = _tiny_skills(tmp_path)
    cfg = Config(
        data_dir=tmp_path / "data",
        skills_dir=skills_dir,
        experimental_judgment=ExperimentalJudgmentFlags(
            master=False, skill_suggest=True
        ),
    )
    cfg.ensure_dirs()
    service = SkillService(
        cfg,
        judgment_service=JudgmentService(
            cfg, lambda: None, backend_factory=lambda: backend
        ),
    )
    result = service.suggest({"query": "cluster cells"})
    assert result["semantic_status"] == "disabled"
    assert backend.calls == []


def test_is_explicit_request_helpers() -> None:
    names = {"alpha", "single-cell-rna-analysis"}
    assert skill_templates.is_explicit_request("alpha", names)
    assert skill_templates.is_explicit_request(
        "load_skill('single-cell-rna-analysis')", names
    )
    assert skill_templates.is_explicit_request("host.load_skill(alpha)", names)
    assert not skill_templates.is_explicit_request("please cluster these cells", names)
