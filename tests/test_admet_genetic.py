"""Offline tests for the admet_genetic skill."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader


def _import_skill():
    sys.path.insert(0, str(get_config().skills_dir))
    from admet_genetic import kernel  # noqa: PLC0415

    return kernel


def _example_root() -> Path:
    return get_config().skills_dir / "admet_genetic" / "examples"


def _read_csv(name: str) -> list[dict[str, str]]:
    with (_example_root() / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_builder():
    path = _example_root() / "build_example.py"
    spec = importlib.util.spec_from_file_location("admet_genetic_build_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_admet_genetic_skill_is_discovered_and_searchable():
    skills = SkillLoader().discover()
    skill = skills["admet_genetic"]
    assert skill.has_kernel
    assert skill.sidecar_gate() == {"ok": True, "error": None}
    assert "admet_genetic.kernel" in (skill.import_hint or "")

    hits = SkillLoader().search("ADMET genetic molecule optimization lineage")
    assert any(hit["name"] == "admet_genetic" for hit in hits)


def test_admet_aggregation_and_operation_detail():
    kernel = _import_skill()
    config = {
        "positive_keywords": ["hia"],
        "negative_keywords": ["dili"],
        "risk_threshold": 0.5,
    }
    score, flags, mapping = kernel.aggregate_admet_predictions(
        {
            "HIA_Hou": 0.8,
            "DILI": 0.7,
            "DILI_drugbank_approved_percentile": 82.0,
            "unmapped": None,
        },
        config,
    )
    assert score == pytest.approx(0.55)
    assert flags == ["high_DILI"]
    assert mapping == {
        "HIA_Hou": "positive",
        "DILI": "negative",
        "DILI_drugbank_approved_percentile": "ignored",
        "unmapped": "ignored",
    }

    detail = json.loads(
        kernel.operation_detail_json(
            "mutation", "replace_atom", "CCN", ["seed-1"], ["CCO"]
        )
    )
    assert detail == {
        "operation": "mutation",
        "operator_detail": "replace_atom",
        "child_canonical_smiles": "CCN",
        "parent_ids": ["seed-1"],
        "parent_smiles": ["CCO"],
    }


def test_generation_log_lineage_validation():
    kernel = _import_skill()
    valid = pd.DataFrame(
        [
            {
                "molecule_id": "seed-1",
                "smiles": "CCO",
                "generation": 0,
                "operation": "seed",
                "parent": "",
                "parents": "",
            },
            {
                "molecule_id": "child-1",
                "smiles": "CCN",
                "generation": 1,
                "operation": "mutation",
                "parent": "seed-1",
                "parents": "",
            },
            {
                "molecule_id": "child-2",
                "smiles": "CCC",
                "generation": 1,
                "operation": "crossover",
                "parent": "",
                "parents": "seed-1;child-1",
            },
        ]
    )
    kernel.validate_generation_log(valid)

    duplicate = valid.copy()
    duplicate.loc[2, "molecule_id"] = "child-1"
    with pytest.raises(ValueError, match="one-to-one"):
        kernel.validate_generation_log(duplicate)

    bad_mutation = valid.copy()
    bad_mutation.loc[1, "parent"] = ""
    with pytest.raises(ValueError, match="mutation rows"):
        kernel.validate_generation_log(bad_mutation)

    bad_crossover = valid.copy()
    bad_crossover.loc[2, "parents"] = "seed-1"
    with pytest.raises(ValueError, match="crossover rows"):
        kernel.validate_generation_log(bad_crossover)


def test_example_files_and_recorded_counts():
    expected = {
        "seed_molecules.csv",
        "candidates_final.csv",
        "generation_log.csv",
        "generation_summary.csv",
        "config.yaml",
        "optimization_dashboard.html",
        "report.md",
        "build_example.py",
    }
    assert expected <= {path.name for path in _example_root().iterdir()}

    seeds = _read_csv("seed_molecules.csv")
    finals = _read_csv("candidates_final.csv")
    rows = _read_csv("generation_log.csv")
    assert len(seeds) == 12
    assert len(finals) == 4
    assert len(rows) == 108
    assert Counter(row["generation"] for row in rows) == {
        "0": 12,
        "1": 24,
        "2": 24,
        "3": 24,
        "4": 24,
    }
    assert Counter(row["operation"] for row in rows) == {
        "seed": 12,
        "mutation": 78,
        "crossover": 18,
    }
    assert sum(row["passes_filters"] == "True" for row in rows) == 83
    assert [row["molecule_id"] for row in finals] == [
        "GA_g4_0085",
        "GA_g2_0033",
        "GA_g1_0006",
        "GA_g1_0018",
    ]

    for row in rows:
        assert isinstance(json.loads(row["operation_detail"]), dict)
        assert isinstance(json.loads(row["admet_predictions_json"]), dict)


def test_generation_summary_matches_generation_log():
    builder = _load_builder()
    data = builder.load_example()
    builder.validate_example(data)
    builder.validate_final_candidates(data)


def test_build_example_uses_temporary_outputs(tmp_path):
    builder = _load_builder()
    committed_dashboard = (_example_root() / "optimization_dashboard.html").read_bytes()
    committed_candidates = (_example_root() / "candidates_final.csv").read_bytes()
    committed_report = (_example_root() / "report.md").read_bytes()
    dashboard = tmp_path / "dashboard.html"
    candidates = tmp_path / "candidates.csv"
    report = tmp_path / "report.md"

    builder.main(
        [
            "--dashboard-output",
            str(dashboard),
            "--candidates-output",
            str(candidates),
            "--report-output",
            str(report),
        ]
    )

    html = dashboard.read_text(encoding="utf-8")
    markdown = report.read_text(encoding="utf-8")
    assert "GA Optimization History" in html
    assert 'id="generationSlider"' in html
    with candidates.open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 4
    assert "Total generation-log records: 108" in markdown
    assert "0.8841338" in markdown
    assert "not captured" in markdown
    assert "model predictions" in markdown
    assert (
        _example_root() / "optimization_dashboard.html"
    ).read_bytes() == committed_dashboard
    assert (
        _example_root() / "candidates_final.csv"
    ).read_bytes() == committed_candidates
    assert (_example_root() / "report.md").read_bytes() == committed_report


def test_dashboard_payload_escapes_script_and_html_delimiters():
    kernel = _import_skill()
    attack = "</script><script>alert('x')</script>&"
    html = kernel._render_html(  # noqa: SLF001 - security regression at render boundary
        {
            "records": [{"molecule_id": attack}],
            "generations": [0],
            "first_seen": {},
            "plots": {"score": "", "count": ""},
            "meta": {"record_count": 1, "svg_count": 0},
        }
    )
    assert attack not in html
    assert "\\u003c/script\\u003e" in html
    assert "\\u0026" in html
    assert "function escapeHtml(value)" in html


def test_dashboard_colors_population_by_filter_result():
    kernel = _import_skill()
    generated = kernel._render_html(  # noqa: SLF001 - template regression boundary
        {
            "records": [],
            "generations": [0],
            "first_seen": {},
            "plots": {"score": "", "count": ""},
            "meta": {"record_count": 0, "svg_count": 0},
        }
    )
    committed = (_example_root() / "optimization_dashboard.html").read_text(
        encoding="utf-8"
    )
    for html in (generated, committed):
        assert ".mol-card.filter-pass" in html
        assert ".mol-card.filter-fail" in html
        assert "passesFilters ? 'filter-pass' : 'filter-fail'" in html
        assert "selected_next_generation" not in html
