"""Scientific/data contracts for panel preparation and event exploration."""

from __future__ import annotations

import builtins
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from harness.evals.panel_data import DEFAULT_CASES_PATH, evaluate_panel_cases
from openai4s.skills_loader import SkillLoader

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prep():
    spec = importlib.util.spec_from_file_location(
        "panel_preprocessing_test", ROOT / "skills/panel-data-preprocessing/kernel.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(values, times=None):
    return [
        {"id": "001", "t": t, "y": y}
        for t, y in zip(times or range(len(values)), values)
    ]


def _run(prep, rows, **kwargs):
    config = {
        "entity": "id",
        "time": "t",
        "numeric_columns": ["y"],
        "frequency": "integer",
    }
    return prep.preprocess_panel(rows, **{**config, **kwargs})


def test_skill_discovery_and_search():
    loader = SkillLoader()
    skill = loader.discover()["panel-data-preprocessing"]
    assert skill.read_only and skill.has_kernel
    assert skill.sidecar_gate() == {"ok": True, "error": None}
    for query in (
        "panel data preprocessing event discovery",
        "panel abrupt persistent changes",
    ):
        assert "panel-data-preprocessing" in [
            r["name"] for r in loader.search(query, limit=3)
        ]


def test_wide_rows_preserve_ids_missingness_and_source_positions(prep):
    raw = [{"id": "001", "a": "3.5", "b": "", "industry": "IT"}]
    before = copy.deepcopy(raw)
    panel = _run(
        prep,
        raw,
        frequency="quarter",
        wide_columns={"y": {"2023Q4": "a", "2024Q1": "b"}},
    )
    assert raw == before
    assert [r["t"] for r in panel["rows"]] == ["2023Q4", "2024Q1"]
    assert [r["y"] for r in panel["rows"]] == [3.5, None]
    assert [r["_source_rows"] for r in panel["rows"]] == [[1], [1]]
    assert panel["analysis"]["columns"]["y"]["missing"] == 1


def test_month_aggregation_requires_decision_and_preserves_missing_sums(prep):
    rows = [
        {"id": "001", "t": "2024-01-02", "y": "2"},
        {"id": "001", "t": "2024-01-25", "y": "3"},
        {"id": "001", "t": "2024-02-01", "y": ""},
        {"id": "001", "t": "2024-02-20", "y": None},
    ]
    with pytest.raises(ValueError, match="duplicate entity-period"):
        _run(prep, rows, frequency="month")
    result = _run(prep, rows, frequency="month", aggregations={"y": "sum"})
    assert [r["y"] for r in result["rows"]] == [5, None]
    assert [r["_source_rows"] for r in result["rows"]] == [[1, 2], [3, 4]]
    assert result["transformation"]["collapsed_row_count"] == 2


def test_conflicting_static_columns_are_not_silently_collapsed(prep):
    rows = [
        {"id": "001", "t": 1, "y": 2, "state": 0},
        {"id": "001", "t": 1, "y": 3, "state": 1},
    ]
    with pytest.raises(ValueError, match="conflicting non-aggregated column: state"):
        _run(prep, rows, aggregations={"y": "sum"})


@pytest.mark.parametrize("value", ["bad", "NaN", float("inf"), True])
def test_bad_measurements_refuse_instead_of_coercing(prep, value):
    with pytest.raises(ValueError):
        _run(prep, _rows([value]))


@pytest.mark.parametrize(
    "frequency,value",
    [
        ("quarter", "2024Q5"),
        ("day", "2024-01"),
        ("month", "01/02/2024"),
        ("year", "2024-02-30"),
        ("integer", 1.5),
    ],
)
def test_ambiguous_or_invalid_periods_refuse(prep, frequency, value):
    with pytest.raises(ValueError):
        _run(prep, _rows([1], [value]), frequency=frequency)


def test_full_calendar_and_observed_grid_balance_are_distinct(prep):
    rows = _rows([1, 2], [0, 2]) + [
        {"id": "002", "t": 0, "y": 3},
        {"id": "002", "t": 2, "y": 4},
    ]
    result = _run(prep, rows)
    audit = result["analysis"]["audit"]
    assert audit["balanced_observed_grid"] is True
    assert audit["balanced_calendar_grid"] is False
    assert audit["missing_entity_periods"] == 2
    assert len(result["rows"]) == 4
    assert all(c["internal_missing_periods"] == 1 for c in audit["coverage"])


def test_composition_change_is_visible_and_not_a_within_entity_event(prep):
    rows = _rows([1] * 8) + [{"id": "002", "t": t, "y": 100} for t in range(4, 8)]
    result = _run(prep, rows)
    points = result["analysis"]["timeline"]
    assert points[3]["metrics"]["y"]["mean"] == 1
    assert points[4]["metrics"]["y"]["mean"] == 50.5
    assert points[3]["entity_set_sha256"] != points[4]["entity_set_sha256"]
    assert result["events"]["candidates"] == []


@pytest.mark.parametrize("direction", [1, -1])
def test_persistent_shift_localization_and_independent_known_date(prep, direction):
    result = _run(
        prep,
        _rows([10] * 4 + [10 + 5 * direction] * 4),
        known_events=[{"label": "Launch", "time": 3, "source": "Independent record"}],
    )
    known, candidate = result["events"]["candidates"]
    assert known["time"] == "3" and known["source"] == "Independent record"
    assert candidate["time"] == "4" and candidate["change"] == 5 * direction
    assert candidate["source_rows"] == [2, 3, 4, 5, 6, 7]
    assert candidate["score"] is None
    assert candidate["causal_status"] == "exploratory"
    assert (
        _run(prep, _rows([10] * 4 + [10 + 5 * direction] * 4), min_abs_change=6)[
            "events"
        ]["candidates"]
        == []
    )


@pytest.mark.parametrize(
    "values,times",
    [
        ([5, 5, 5, 30, 5, 5, 5, 5], None),
        (list(range(10)), None),
        ([5, 5, None, 5, 15, 15, 15, 15], None),
        ([5] * 4 + [15] * 4, [0, 1, 2, 3, 7, 8, 9, 10]),
    ],
)
def test_spikes_linear_trends_and_missing_windows_do_not_become_events(
    prep, values, times
):
    assert _run(prep, _rows(values, times))["events"]["candidates"] == []


def test_censored_exposure_and_reversals_keep_their_meaning(prep):
    rows = [
        {"id": "001", "t": 0, "y": 1, "state": 1},
        {"id": "001", "t": 1, "y": 1, "state": 0},
        {"id": "001", "t": 2, "y": 1, "state": None},
        {"id": "001", "t": 3, "y": 1, "state": 1},
    ]
    result = _run(prep, rows, treatment="state")
    assert [e["kind"] for e in result["events"]["candidates"]] == [
        "left_censored_exposure",
        "treatment_reversal",
        "interval_censored_transition",
    ]
    assert result["events"]["missing_treatment_states"] == 1
    rows[2]["state"] = "yes"
    with pytest.raises(ValueError):
        _run(prep, rows, treatment="state")


def test_csv_width_headers_and_id_preservation(prep, tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("id,t,y\n001,0,2\n", encoding="utf-8-sig")
    assert prep.read_csv(source)[0]["id"] == "001"
    for text in ("id,id\n1,2\n", "id,t,y\n1,2\n", "id,t\n1,2,3\n"):
        source.write_text(text)
        with pytest.raises(ValueError):
            prep.read_csv(source)


def test_bundle_hashes_and_source_mapping_are_verifiable(prep, tmp_path):
    rows = _rows([5] * 4 + [15] * 4)
    result = _run(prep, rows)
    outputs = prep.write_outputs(result, tmp_path / "results")
    manifest = json.loads(Path(outputs["manifest.json"]).read_text())
    for name, info in manifest["outputs"].items():
        assert (
            hashlib.sha256(Path(outputs[name]).read_bytes()).hexdigest()
            == info["sha256"]
        )
    with Path(outputs["panel.csv"]).open(newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert exported[0]["id"] == "001"
    assert json.loads(exported[0]["_source_rows"]) == [1]
    assert result["manifest"] == _run(prep, rows)["manifest"]
    changed = copy.deepcopy(rows)
    changed[0]["y"] = 6
    assert (
        result["manifest"]["source_records_sha256"]
        != _run(prep, changed)["manifest"]["source_records_sha256"]
    )
    with pytest.raises(FileExistsError):
        prep.write_outputs(result, tmp_path / "results")


def test_plot_dependency_is_optional_and_fails_explicitly(prep, monkeypatch, tmp_path):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    result = _run(prep, _rows([5] * 8))
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match="require matplotlib"):
        prep.render_diagnostics(result, tmp_path)


def test_replay_scores_real_results_and_refusals(tmp_path):
    report = evaluate_panel_cases()
    assert report["production_backed"] is True
    assert report["total"] == 22 and report["failed"] == 0
    record = json.loads(DEFAULT_CASES_PATH.read_text())
    case = record["cases"][0]
    case.pop("expected")
    case["expected_error"] = "duplicate entity-period"
    record["cases"] = [case]
    tape = tmp_path / "cases.json"
    tape.write_text(json.dumps(record))
    assert evaluate_panel_cases(tape)["failed"] == 1
    record["cases"] = []
    tape.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="non-empty cases"):
        evaluate_panel_cases(tape)
