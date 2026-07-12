"""Offline tests for the mineral_spectra_analysis skill."""

import json
import sys

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader


def _import_skill():
    sys.path.insert(0, str(get_config().skills_dir))
    from mineral_spectra_analysis.kernel import (  # noqa: PLC0415
        LoopOutcome,
        PipelineResult,
        available_dependencies,
        build_markdown_report,
        component_prf,
        default_config,
        evaluate_against_truth,
        fraction_mae,
        mineral_from_filename,
        summarize_outcome,
    )

    return {
        "LoopOutcome": LoopOutcome,
        "PipelineResult": PipelineResult,
        "available_dependencies": available_dependencies,
        "build_markdown_report": build_markdown_report,
        "component_prf": component_prf,
        "default_config": default_config,
        "evaluate_against_truth": evaluate_against_truth,
        "fraction_mae": fraction_mae,
        "mineral_from_filename": mineral_from_filename,
        "summarize_outcome": summarize_outcome,
    }


def _fake_outcome():
    funcs = _import_skill()
    result = funcs["PipelineResult"](
        processed=[0.0, 0.4, 0.0],
        recon=[0.0, 0.38, 0.0],
        candidate_names=["Diopside", "Bertrandite"],
        fractions={"Diopside": 0.62, "Bertrandite": 0.38},
        used_idx=[0, 1],
        used_coef=[0.62, 0.38],
        diagnostics={
            "fit_corr": 0.981,
            "residual_rmse": 0.0005,
            "rel_residual": 0.16,
            "explained_energy": 0.97,
            "n_residual_peaks": 0,
            "residual_peak_positions": [],
            "reliability": "high",
            "hints": [],
        },
        peaks=[180.0, 232.0, 666.0],
        support={"Diopside": [180.0, 666.0], "Bertrandite": [232.0]},
    )
    return funcs["LoopOutcome"](
        best_result=result,
        history=[
            {
                "step": 0,
                "added_component": "Diopside",
                "match_corr": 0.84,
                "rel_residual": 0.54,
                "n_residual_peaks": 12,
                "cumulative_components": ["Diopside"],
            },
            {
                "step": 1,
                "added_component": "Bertrandite",
                "match_corr": 0.79,
                "rel_residual": 0.16,
                "n_residual_peaks": 7,
                "cumulative_components": ["Diopside", "Bertrandite"],
            },
        ],
    )


def test_mineral_spectra_skill_is_discovered():
    skills = SkillLoader().discover()
    assert "mineral_spectra_analysis" in skills
    skill = skills["mineral_spectra_analysis"]
    assert skill.has_kernel
    assert "mineral_spectra_analysis.kernel" in (skill.import_hint or "")
    assert "raman" in skill.description.lower()
    assert "mixed-mineral" in skill.description.lower()


def test_mineral_spectra_skill_is_searchable():
    hits = SkillLoader().search("Raman mixed mineral spectrum NNLS residual peaks")
    assert any(hit["name"] == "mineral_spectra_analysis" for hit in hits)


def test_skill_doc_preserves_blind_pipeline_and_separates_evaluation():
    skill_root = get_config().skills_dir / "mineral_spectra_analysis"
    doc = (skill_root / "SKILL.md").read_text(encoding="utf-8")

    assert "apply global preprocessing exactly once" in doc
    assert "save `clean_spectrum.csv`" in doc
    assert "read `clean_spectrum.csv` back into the loop" in doc
    assert "second-derivative peak detection -> peak-driven" in doc
    assert "Do not read `truth.json` during analysis" in doc
    assert "Only create synthetic cases or read answer keys" in doc
    assert "https://www.rruff.net/zipped_data_files/raman/excellent_oriented.zip" in doc
    assert "spectra_cache/excellent_oriented.zip" in doc


def test_case1_example_is_documented_and_has_component_types():
    skill_root = get_config().skills_dir / "mineral_spectra_analysis"
    doc = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    examples = skill_root / "examples"
    case_dir = examples / "case1"

    assert "examples/case1/" in doc
    assert "case1_components.json" in doc
    assert "case1_mineral_spectra_report.md" in doc
    assert "examples/build_example.py" in doc
    assert (case_dir / "spectrum.csv").exists()
    assert (case_dir / "truth.json").exists()
    assert (case_dir / "input.png").exists()
    assert (examples / "case1_analysis.json").exists()
    assert (examples / "build_example.py").exists()

    components = json.loads(
        (examples / "case1_components.json").read_text(encoding="utf-8")
    )
    by_name = {item["name"]: item for item in components["components"]}
    assert components["spectrum_type"] == "synthetic dirty mixed-mineral Raman spectrum"
    assert set(by_name) == {"Clinoptilolite-Ca", "Bertrandite", "Diopside"}
    assert all(item["type"] == "mineral phase" for item in by_name.values())
    assert by_name["Diopside"]["true_fraction"] == pytest.approx(0.446399574)

    report = (examples / "case1_mineral_spectra_report.md").read_text(encoding="utf-8")
    assert "Mineral Spectra Example: case1" in report
    assert (
        "Ground truth is shown here only because this is an evaluation example"
        in report
    )
    assert "Clinoptilolite-Ca" in report
    assert "predicted mineral phase" in report
    assert "Precision/Recall/F1" in report


def test_kernel_import_and_pure_helpers_do_not_require_science_stack():
    funcs = _import_skill()

    deps = funcs["available_dependencies"]()
    assert set(deps) == {"numpy", "scipy", "pybaselines", "matplotlib"}
    assert funcs["default_config"]()["top_k"] == 8
    assert (
        funcs["mineral_from_filename"]("Clinoptilolite-Ca__R061111__Processed.txt")
        == "Clinoptilolite-Ca"
    )


def test_report_without_truth_is_blind_and_has_conclusion():
    funcs = _import_skill()
    cfg = funcs["default_config"]()
    report = funcs["build_markdown_report"](
        _fake_outcome(),
        cfg,
        truth=None,
        evaluation=None,
        include_figures=False,
    )

    assert "# 光谱成分识别诊断报告" in report
    assert "全局预处理(一次)" in report
    assert "迭代寻峰-匹配-相减" in report
    assert "Diopside" in report
    assert "Bertrandite" in report
    assert "综合可信度: **HIGH**" in report
    assert "未提供 ground truth" in report
    assert "真实成分" not in report
    assert "clean_spectrum.csv" in report


def test_evaluation_is_explicit_post_loop_contract():
    funcs = _import_skill()
    outcome = _fake_outcome()
    truth = {
        "true_names": ["Diopside", "Bertrandite"],
        "true_fractions": {"Diopside": 0.6, "Bertrandite": 0.4},
    }

    assert funcs["component_prf"](["a", "b"], ["a", "c"]) == pytest.approx(
        {"precision": 0.5, "recall": 0.5, "f1": 0.5}
    )
    assert funcs["fraction_mae"]({"a": 0.6}, {"a": 0.4, "b": 0.2}) == pytest.approx(0.2)

    evaluation = funcs["evaluate_against_truth"](outcome.best_result, truth)
    assert evaluation["f1"] == pytest.approx(1.0)
    assert evaluation["fraction_mae"] == pytest.approx(0.02)

    report = funcs["build_markdown_report"](
        outcome,
        funcs["default_config"](),
        truth=truth,
        evaluation=evaluation,
        include_figures=False,
    )
    assert "与真值对比（循环结束后一次性评估）" in report
    assert "循环过程中未使用" in report
    assert "Precision/Recall/F1" in report


def test_summary_is_serializable_shape():
    funcs = _import_skill()
    summary = funcs["summarize_outcome"](_fake_outcome())

    assert list(summary) == [
        "fractions",
        "candidate_names",
        "support",
        "diagnostics",
        "history",
    ]
    assert summary["fractions"]["Diopside"] == pytest.approx(0.62)
    assert summary["history"][0]["added_component"] == "Diopside"
