"""Prospective coverage/noise contracts for panel event-window assessment."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prep():
    spec = importlib.util.spec_from_file_location(
        "panel_window_assessment_test",
        ROOT / "skills/panel-data-preprocessing/kernel.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(values, times=None, entity="001"):
    return [
        {"id": entity, "t": t, "y": value}
        for t, value in zip(range(len(values)) if times is None else times, values)
    ]


def _panel(prep, rows, numeric_columns=None):
    return prep.prepare_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"] if numeric_columns is None else numeric_columns,
        frequency="integer",
    )


def _assessment(report, time, window, metric="y", entity="001"):
    return next(
        row
        for row in report["assessments"]
        if row["entity"] == entity
        and row["metric"] == metric
        and row["time"] == str(time)
        and row["window"] == window
    )


def _recommendation(report, time, metric="y", entity="001"):
    return next(
        row
        for row in report["recommendations"]
        if row["entity"] == entity
        and row["metric"] == metric
        and row["time"] == str(time)
    )


def _noisy_rows():
    # The requested window's noise exceeds the budget, while one additional
    # observed baseline period lowers its iid mean-noise estimate enough.
    return _rows([0, 0, 0, 0, 0, 4, -4, 4, -4, 0] + [2] * 6)


def test_sufficient_windows_are_reported_without_changing_rows(prep):
    panel = _panel(prep, _rows([5] * 12))
    original = copy.deepcopy(panel)
    report = prep.assess_windows(panel, window=3, window_options=[4])
    row = _assessment(report, 4, 3)
    assert panel == original
    assert {entry["window"] for entry in report["summary"]} == {3, 4}
    assert row["eligible"] is True and row["reasons"] == []
    assert row["coverage"]["pre_nonmissing"] == 3
    assert row["coverage"]["post_nonmissing"] == 3
    assert row["noise_status"] == "tolerance_not_configured"
    assert row["pre_statistics"]["noise_to_tolerance_ratio"] is None
    assert _recommendation(report, 4)["action"] == "tolerance_not_configured"


def test_five_period_panel_cannot_support_three_period_sides(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([3] * 5)), window=3, noise_tolerance=1
    )
    assert report["assessments"]
    assert not any(row["eligible"] for row in report["assessments"])
    assert all(
        row["action"] == "coverage_insufficient" and row["suggested_window"] is None
        for row in report["recommendations"]
    )


def test_default_inspection_includes_five_period_noise_baseline(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([3] * 10)), window=3, noise_tolerance=1
    )
    assert report["config"]["window_options"] == [3, 4, 5, 6]
    assert _assessment(report, 5, 5)["eligible"] is True
    recommendation = _recommendation(report, 5)
    assert recommendation["action"] == "consider_larger_for_baseline"
    assert recommendation["suggested_window"] == 5


def test_extreme_requested_window_checks_only_that_size_on_small_data(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([3] * 5)), window=10**9, noise_tolerance=1
    )
    assert report["config"]["window_options"] == [10**9]
    assert len(report["assessments"]) == 5
    assert not any(row["eligible"] for row in report["assessments"])
    assert all(
        row["action"] == "coverage_insufficient" for row in report["recommendations"]
    )


def test_extreme_noise_baseline_is_bounded_by_each_entitys_observation_count(prep):
    rows = _rows([3] * 5) + _rows([3] * 5, range(10, 15), entity="002")
    report = prep.assess_windows(_panel(prep, rows), window=3, min_noise_periods=10**9)
    assert report["config"]["window_options"] == [3, 4, 5]
    assert len(report["assessments"]) == 30


def test_explicit_sweep_with_more_than_64_sizes_refuses(prep):
    with pytest.raises(ValueError, match="64"):
        prep.assess_windows(
            _panel(prep, _rows([3] * 5)),
            window=3,
            window_options=list(range(2, 67)),
        )


def test_default_sweep_with_more_than_64_sizes_requests_bounded_options(prep):
    with pytest.raises(ValueError, match="64"):
        prep.assess_windows(
            _panel(prep, _rows([3] * 100)), window=3, min_noise_periods=100
        )


def test_missing_calendar_periods_and_missing_values_are_separate(prep):
    rows = _rows([4, None, 4, 4, 4, 4], [0, 1, 3, 4, 5, 6])
    panel = _panel(prep, rows)
    report = prep.assess_windows(panel, window=3, window_options=[3], noise_tolerance=1)
    row = _assessment(report, 4, 3)
    assert row["eligible"] is False
    assert set(row["reasons"]) == {"calendar_gap_pre", "missing_values_pre"}
    assert row["coverage"]["pre_observed_periods"] == 2
    assert row["coverage"]["pre_missing_periods"] == 1
    assert row["coverage"]["pre_missing_values"] == 1
    assert row["coverage"]["pre_nonmissing"] == 1
    assert row["coverage"]["pre_internal_gap_periods"] == 1
    assert row["coverage"]["pre_history_shortfall"] == 0
    assert row["pre_statistics"] is None
    assert row["noise_status"] == "pre_data_incomplete"
    assert len(panel["rows"]) == len(rows)


def test_entity_entry_shortfall_is_not_an_internal_calendar_gap(prep):
    rows = _rows([2] * 8, range(3, 11)) + _rows([2] * 11, entity="002")
    report = prep.assess_windows(
        _panel(prep, rows), window=3, window_options=[3], noise_tolerance=1
    )
    short = _assessment(report, 4, 3)
    full = _assessment(report, 4, 3, entity="002")
    assert short["coverage"]["pre_history_shortfall"] == 2
    assert short["coverage"]["pre_internal_gap_periods"] == 0
    assert "insufficient_pre_history" in short["reasons"]
    assert "calendar_gap_pre" not in short["reasons"]
    assert short["eligible"] is False and full["eligible"] is True


def test_post_coverage_defects_keep_valid_pre_statistics(prep):
    rows = _rows([2, 2, 2, 2, 2, None, 2], [0, 1, 2, 3, 4, 5, 7])
    report = prep.assess_windows(
        _panel(prep, rows), window=3, window_options=[3], noise_tolerance=1
    )
    row = _assessment(report, 4, 3)
    assert set(row["reasons"]) == {"calendar_gap_post", "missing_values_post"}
    assert row["coverage"]["post_missing_periods"] == 1
    assert row["coverage"]["post_missing_values"] == 1
    assert row["pre_statistics"]["variance"] == 0
    assert _recommendation(report, 4)["action"] == "coverage_insufficient"


def test_high_pre_noise_recommends_only_an_available_larger_window(prep):
    report = prep.assess_windows(
        _panel(prep, _noisy_rows()),
        window=5,
        window_options=[5, 6],
        noise_tolerance=1.5,
    )
    small, large = _assessment(report, 10, 5), _assessment(report, 10, 6)
    assert small["noise_status"] == "high_noise_under_iid"
    assert large["noise_status"] == "within_tolerance_under_iid"
    assert small["pre_statistics"]["mean_noise_iid"] > 1.5
    assert large["pre_statistics"]["mean_noise_iid"] < 1.5
    recommendation = _recommendation(report, 10)
    assert recommendation["action"] == "consider_larger_under_iid"
    assert recommendation["requested_window"] == 5
    assert recommendation["suggested_window"] == 6


def test_unavailable_larger_window_cannot_be_recommended(prep):
    report = prep.assess_windows(
        _panel(prep, _noisy_rows()[:-1]),
        window=5,
        window_options=[5, 6],
        noise_tolerance=1.5,
    )
    assert _assessment(report, 10, 5)["eligible"] is True
    assert _assessment(report, 10, 6)["eligible"] is False
    recommendation = _recommendation(report, 10)
    assert recommendation["action"] == "no_inspected_window_meets_tolerance"
    assert recommendation["suggested_window"] is None


def test_larger_window_cannot_skip_a_gap_to_collect_more_baseline_rows(prep):
    rows = [row for row in _noisy_rows() if row["t"] != 4]
    report = prep.assess_windows(
        _panel(prep, rows),
        window=5,
        window_options=[5, 6],
        noise_tolerance=1.5,
    )
    assert _assessment(report, 10, 5)["eligible"] is True
    larger = _assessment(report, 10, 6)
    assert larger["eligible"] is False
    assert larger["coverage"]["pre_internal_gap_periods"] == 1
    assert larger["pre_statistics"] is None
    assert _recommendation(report, 10)["suggested_window"] is None


def test_large_raw_variance_from_linear_drift_requires_trend_review(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([7 + 20 * t for t in range(14)])),
        window=6,
        window_options=[6],
        noise_tolerance=1,
    )
    row = _assessment(report, 7, 6)
    stats = row["pre_statistics"]
    assert stats["variance"] > 1000
    assert stats["linear_slope"] == pytest.approx(20)
    assert stats["residual_sd"] == pytest.approx(0, abs=1e-10)
    assert row["noise_status"] == "review_trend"
    assert _recommendation(report, 7)["action"] == "review_trend"


def test_zero_variance_has_finite_zero_noise_and_retains_window(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([100] * 12)),
        window=5,
        window_options=[5],
        noise_tolerance=0.01,
    )
    row = _assessment(report, 6, 5)
    for field in ("variance", "sd", "mad", "residual_sd", "mean_noise_iid"):
        assert row["pre_statistics"][field] == 0
    assert row["pre_statistics"]["noise_to_tolerance_ratio"] == 0
    assert row["noise_status"] == "within_tolerance_under_iid"
    assert _recommendation(report, 6)["action"] == "retain_current_under_iid"
    json.dumps(report, allow_nan=False)


def test_short_noise_baseline_requires_more_history_even_when_flat(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([1] * 14)),
        window=3,
        window_options=[3, 5],
        noise_tolerance=1,
        min_noise_periods=5,
    )
    assert _assessment(report, 7, 3)["noise_status"] == "noise_baseline_short"
    recommendation = _recommendation(report, 7)
    assert recommendation["action"] == "consider_larger_for_baseline"
    assert recommendation["suggested_window"] == 5


def test_short_noise_baseline_without_a_larger_option_is_explicit(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([1] * 8)),
        window=3,
        window_options=[3],
        noise_tolerance=1,
        min_noise_periods=5,
    )
    assert _assessment(report, 4, 3)["eligible"] is True
    recommendation = _recommendation(report, 4)
    assert recommendation["action"] == "noise_baseline_short"
    assert recommendation["suggested_window"] is None


def test_serial_dependence_is_flagged_before_iid_recommendations(prep):
    report = prep.assess_windows(
        _panel(prep, _rows([4, -4] * 10)),
        window=10,
        window_options=[10],
        noise_tolerance=10,
    )
    row = _assessment(report, 10, 10)
    assert abs(row["pre_statistics"]["lag1_autocorrelation"]) > 0.7
    assert row["noise_status"] == "review_serial_dependence"
    assert _recommendation(report, 10)["action"] == "review_serial_dependence"


def test_post_outcome_values_do_not_change_prospective_noise_or_recommendation(prep):
    rows = _noisy_rows()
    changed = copy.deepcopy(rows)
    for row in changed:
        if row["t"] >= 10:
            row["y"] = 1000000 * (-1) ** row["t"]
    settings = {"window": 5, "window_options": [5, 6], "noise_tolerance": 1.5}
    before = prep.assess_windows(_panel(prep, rows), **settings)
    after = prep.assess_windows(_panel(prep, changed), **settings)
    for width in (5, 6):
        assert _assessment(before, 10, width) == _assessment(after, 10, width)
    assert _recommendation(before, 10) == _recommendation(after, 10)


def test_noise_recommendation_is_invariant_to_measurement_units(prep):
    original = _noisy_rows()
    scaled = [{**row, "y": 100 * row["y"]} for row in original]
    settings = {"window": 5, "window_options": [5, 6]}
    before = prep.assess_windows(
        _panel(prep, original), noise_tolerance=1.5, **settings
    )
    after = prep.assess_windows(_panel(prep, scaled), noise_tolerance=150, **settings)
    for width in (5, 6):
        small, large = _assessment(before, 10, width), _assessment(after, 10, width)
        assert small["noise_status"] == large["noise_status"]
        assert small["pre_statistics"]["noise_to_tolerance_ratio"] == pytest.approx(
            large["pre_statistics"]["noise_to_tolerance_ratio"]
        )
    assert _recommendation(before, 10)["action"] == _recommendation(after, 10)["action"]
    assert _recommendation(before, 10)["suggested_window"] == 6
    assert _recommendation(after, 10)["suggested_window"] == 6


def test_noise_recommendation_is_invariant_to_a_constant_level_offset(prep):
    original = _noisy_rows()
    shifted = [{**row, "y": row["y"] + 1000000} for row in original]
    settings = {"window": 5, "window_options": [5, 6], "noise_tolerance": 1.5}
    before = prep.assess_windows(_panel(prep, original), **settings)
    after = prep.assess_windows(_panel(prep, shifted), **settings)
    for width in (5, 6):
        assert _assessment(before, 10, width) == _assessment(after, 10, width)
    assert _recommendation(before, 10) == _recommendation(after, 10)


def test_partial_metric_tolerances_do_not_borrow_another_metrics_scale(prep):
    rows = [{**row, "z": row["y"] * 1000} for row in _noisy_rows()]
    report = prep.assess_windows(
        _panel(prep, rows, ["y", "z"]),
        window=5,
        window_options=[5, 6],
        noise_tolerance={"y": 1.5},
    )
    assert _assessment(report, 10, 5)["noise_status"] == "high_noise_under_iid"
    assert (
        _assessment(report, 10, 5, metric="z")["noise_status"]
        == "tolerance_not_configured"
    )
    assert _recommendation(report, 10, metric="z")["suggested_window"] is None


def test_numeric_treatment_state_is_excluded_from_outcome_noise_assessment(prep):
    rows = [{**row, "state": int(row["t"] >= 6)} for row in _rows([3] * 12)]
    report = prep.assess_windows(
        _panel(prep, rows, ["y", "state"]),
        window=5,
        window_options=[5],
        noise_tolerance=1,
        treatment="state",
    )
    assert {row["metric"] for row in report["assessments"]} == {"y"}
    assert {row["metric"] for row in report["recommendations"]} == {"y"}
    assert report["config"]["noise_tolerance"] == {"y": 1}


@pytest.mark.parametrize(
    "settings",
    [
        {"window": 1},
        {"window": True},
        {"window": 2.5},
        {"window_options": [1]},
        {"window_options": [True]},
        {"noise_tolerance": 0},
        {"noise_tolerance": -1},
        {"noise_tolerance": float("inf")},
        {"noise_tolerance": float("nan")},
        {"noise_tolerance": True},
        {"noise_tolerance": {"unknown": 1}},
        {"min_noise_periods": 0},
        {"min_noise_periods": True},
        {"min_noise_periods": 3.5},
    ],
)
def test_invalid_window_diagnostic_settings_refuse(prep, settings):
    with pytest.raises(ValueError):
        prep.assess_windows(_panel(prep, _rows([1] * 12)), **settings)


def test_assessment_integration_keeps_event_candidates_and_requested_window(prep):
    rows = _rows([5] * 8 + [15] * 8)
    panel = _panel(prep, rows)
    expected = prep.discover_events(panel, window=3)["candidates"]
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        window=3,
        window_options=[3, 5],
        noise_tolerance=1,
    )
    assert result["events"]["candidates"] == expected
    assert result["events"]["config"]["window"] == 3
    assert result["window_assessment"]["config"]["requested_window"] == 3
    assert "window_assessment_summary" in result["analysis"]


def test_diagnostic_exports_and_configuration_hashes_are_auditable(prep, tmp_path):
    settings = {
        "entity": "id",
        "time": "t",
        "numeric_columns": ["y"],
        "frequency": "integer",
        "window": 5,
        "window_options": [5, 6],
    }
    rows = _noisy_rows()
    result = prep.preprocess_panel(rows, noise_tolerance=1.5, **settings)
    changed = prep.preprocess_panel(rows, noise_tolerance=2, **settings)
    assert (
        result["manifest"]["source_records_sha256"]
        == changed["manifest"]["source_records_sha256"]
    )
    assert (
        result["manifest"]["configuration_sha256"]
        != changed["manifest"]["configuration_sha256"]
    )
    outputs = prep.write_outputs(result, tmp_path / "bundle")
    assert "window_assessment.json" in outputs
    assert "window_assessment.csv" in outputs
    exported = json.loads(Path(outputs["window_assessment.json"]).read_text())
    assert exported == result["window_assessment"]
    manifest = json.loads(Path(outputs["manifest.json"]).read_text())
    for name in ("window_assessment.json", "window_assessment.csv"):
        assert (
            hashlib.sha256(Path(outputs[name]).read_bytes()).hexdigest()
            == manifest["outputs"][name]["sha256"]
        )
    with pytest.raises(FileExistsError):
        prep.write_outputs(result, tmp_path / "bundle")


def test_practical_change_fallback_is_explicit_but_direct_default_is_unconfigured(prep):
    rows = _rows([5] * 12)
    standalone = prep.assess_windows(_panel(prep, rows))
    assert standalone["config"]["noise_tolerance_source"] == "unconfigured"
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        min_abs_change=0.5,
    )
    assert (
        result["window_assessment"]["config"]["noise_tolerance_source"]
        == "min_abs_change"
    )
    explicit = prep.assess_windows(_panel(prep, rows), noise_tolerance=0.5)
    assert explicit["config"]["noise_tolerance_source"] == "explicit"
