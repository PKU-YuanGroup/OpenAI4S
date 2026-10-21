"""Leakage, encoding and observed-data contracts for optional panel cleaning."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DECISION = {"basis": "researcher", "rationale": "Prespecified training-only cleaning."}


@pytest.fixture(scope="module")
def prep():
    spec = importlib.util.spec_from_file_location(
        "panel_cleaning_test", ROOT / "skills/panel-data-preprocessing/kernel.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fit(prep, rows, **settings):
    defaults = {
        "fit_rows": [1, 2],
        "numeric_columns": ["y"],
        "decision": DECISION,
    }
    return prep.fit_cleaner(rows, **{**defaults, **settings})


def _transform(prep, rows, **settings):
    return prep.transform_cleaner(rows, _fit(prep, rows, **settings))


def _feature(model, source, *, token=None, category=None, kind=None):
    return next(
        name
        for name, info in model["generated_columns"].items()
        if info["source_column"] == source
        and (token is None or info.get("token") == token)
        and (category is None or info.get("category") == category)
        and (kind is None or info.get("kind") == kind)
    )


def _numeric_group(model, values=()):
    return next(
        group
        for group in model["numeric"]["groups"].values()
        if group["group_values"] == list(values)
    )


def test_profile_proposes_actions_without_mutating_or_filling_data(prep):
    rows = [
        {"id": "001", "y": 0, "category": "A", "description": "first text"},
        {"id": "002", "y": None, "category": "B", "description": None},
    ]
    original = copy.deepcopy(rows)
    profile = prep.profile_cleaning(
        rows, protected_columns=["id"], numeric_columns=["y"]
    )
    assert rows == original
    assert {"mean", "median"}.issubset(profile["columns"]["y"]["recommended_methods"])
    assert profile["columns"]["id"]["recommended_methods"] == []
    assert "researcher" in profile["decision_required"]["basis"]
    assert rows[1]["y"] is None


def test_profile_does_not_count_protected_numeric_ids_or_times_as_knn_features(prep):
    rows = [
        {"id": "001", "t": 0, "y": 2},
        {"id": "002", "t": 1, "y": 4},
    ]
    profile = prep.profile_cleaning(
        rows, protected_columns=["id", "t"], numeric_columns=["y"]
    )
    assert "knn" not in profile["columns"]["y"]["recommended_methods"]
    assert profile["columns"]["id"]["recommended_methods"] == []
    assert profile["columns"]["t"]["recommended_methods"] == []


@pytest.mark.parametrize("method,expected", [("mean", 7), ("median", 3)])
def test_mean_and_median_use_only_explicit_fit_rows(prep, method, expected):
    rows = [{"y": value} for value in [0, 3, 18, 1000000, None]]
    original = copy.deepcopy(rows)
    result = _transform(prep, rows, fit_rows=[1, 2, 3], imputations={"y": method})
    assert result["rows"][4]["y"] == expected
    assert result["rows"][4]["_imputed_columns"] == ["y"]
    assert result["rows"][0]["y"] == 0
    assert all("_imputed_columns" not in row for row in result["rows"][:4])
    assert result["report"]["filled"][0]["row"] == 5
    assert rows == original


def test_grouped_imputation_does_not_borrow_other_groups_or_unseen_targets(prep):
    rows = [
        {"g": "A", "y": 0},
        {"g": "A", "y": 10},
        {"g": "B", "y": None},
        {"g": "A", "y": None},
        {"g": "B", "y": None},
        {"g": "C", "y": None},
    ]
    result = _transform(
        prep,
        rows,
        fit_rows=[1, 2, 3],
        imputations={"y": "mean"},
        group_by=["g"],
    )
    assert result["rows"][3]["y"] == 5
    assert result["rows"][4]["y"] is None
    assert result["rows"][5]["y"] is None
    reasons = {(item["row"], item["reason"]) for item in result["report"]["unresolved"]}
    assert (5, "no_fit_target_values") in reasons
    assert (6, "no_fitted_group") in reasons


def test_no_imputation_choice_preserves_missingness(prep):
    rows = [{"y": 0}, {"y": 2}, {"y": None}]
    result = _transform(prep, rows)
    assert result["rows"] == rows
    assert result["report"]["filled"] == []
    assert result["report"]["unresolved"][0]["reason"] == "no_imputation_selected"


@pytest.mark.parametrize("fit_rows", [[], [0], [4], [1, 1], [True], [1.5]])
def test_fit_selection_refuses_empty_invalid_duplicate_and_boolean_positions(
    prep, fit_rows
):
    with pytest.raises(ValueError):
        _fit(prep, [{"y": 0}, {"y": 2}, {"y": None}], fit_rows=fit_rows)


def test_fit_selection_and_decision_are_required(prep):
    rows = [{"y": 0}, {"y": 2}]
    with pytest.raises((TypeError, ValueError)):
        prep.fit_cleaner(rows, numeric_columns=["y"], decision=DECISION)
    with pytest.raises((TypeError, ValueError)):
        prep.fit_cleaner(rows, fit_rows=[1, 2], numeric_columns=["y"])


@pytest.mark.parametrize(
    "decision",
    [
        {},
        {"basis": "guess", "rationale": "Unspecified source."},
        {"basis": "researcher", "rationale": ""},
    ],
)
def test_cleaning_decision_requires_supported_basis_and_rationale(prep, decision):
    with pytest.raises(ValueError):
        _fit(prep, [{"y": 0}, {"y": 2}], decision=decision)


@pytest.mark.parametrize("value", ["invalid", "NaN", float("inf"), True])
def test_invalid_numeric_measurements_refuse_instead_of_becoming_missing(prep, value):
    with pytest.raises(ValueError):
        _fit(prep, [{"y": 0}, {"y": value}, {"y": None}])


def test_heldout_targets_categories_and_words_do_not_change_fitted_transform(prep):
    rows = [
        {"x": 0, "y": 10, "color": "red", "text": "apple pear"},
        {"x": 2, "y": 30, "color": "blue", "text": "pear"},
        {"x": 1, "y": None, "color": "red", "text": "apple apricot"},
    ]
    changed = copy.deepcopy(rows)
    changed[2].update(y=1000000, color="secret-category", text="secret-token")
    settings = {
        "numeric_columns": ["x", "y"],
        "imputations": {"y": "knn"},
        "knn_features": ["x"],
        "categorical_encodings": {"color": "onehot"},
        "text_encodings": {"text": "tfidf"},
    }
    first, second = _fit(prep, rows, **settings), _fit(prep, changed, **settings)
    assert first["fit_source_records_sha256"] == second["fit_source_records_sha256"]
    assert first["numeric"] == second["numeric"]
    assert first["categorical"] == second["categorical"]
    assert first["text"] == second["text"]
    assert first["generated_columns"] == second["generated_columns"]
    assert (
        prep.transform_cleaner(rows, first)["rows"]
        == prep.transform_cleaner(rows, second)["rows"]
    )
    assert [donor["row"] for donor in _numeric_group(first)["donors"]] == [1, 2]


@pytest.mark.parametrize("weights,expected", [("uniform", 20), ("distance", 15)])
def test_knn_hand_checked_neighbor_weights(prep, weights, expected):
    rows = [{"x": 0, "y": 10}, {"x": 2, "y": 30}, {"x": 0.5, "y": None}]
    result = _transform(
        prep,
        rows,
        numeric_columns=["x", "y"],
        imputations={"y": "knn"},
        knn_features=["x"],
        n_neighbors=2,
        weights=weights,
    )
    assert result["rows"][2]["y"] == pytest.approx(expected)
    assert result["report"]["filled"][0]["donor_rows"] == [1, 2]


def test_knn_uses_available_donors_when_fewer_than_requested(prep):
    rows = [{"x": 0, "y": 10}, {"x": 2, "y": 30}, {"x": 1, "y": None}]
    result = _transform(
        prep,
        rows,
        numeric_columns=["x", "y"],
        imputations={"y": "knn"},
        knn_features=["x"],
        n_neighbors=20,
    )
    assert result["rows"][2]["y"] == 20
    assert result["report"]["filled"][0]["donor_rows"] == [1, 2]


def test_knn_zero_distance_donors_exclude_nonzero_distance_weights(prep):
    rows = [
        {"x": 0, "y": 10},
        {"x": 0, "y": 30},
        {"x": 2, "y": 1000},
        {"x": 0, "y": None},
    ]
    result = _transform(
        prep,
        rows,
        fit_rows=[1, 2, 3],
        numeric_columns=["x", "y"],
        imputations={"y": "knn"},
        knn_features=["x"],
        n_neighbors=3,
        weights="distance",
    )
    assert result["rows"][3]["y"] == 20
    assert result["report"]["filled"][0]["donor_rows"] == [1, 2]


def test_knn_standardization_is_invariant_to_individual_feature_units(prep):
    rows = [
        {"x": 0, "z": 0, "y": 0},
        {"x": 10, "z": 2, "y": 100},
        {"x": 1, "z": 1.5, "y": None},
    ]
    scaled = [{**row, "z": row["z"] * 100} for row in rows]
    settings = {
        "numeric_columns": ["x", "z", "y"],
        "imputations": {"y": "knn"},
        "knn_features": ["x", "z"],
        "n_neighbors": 1,
    }
    first, second = _transform(prep, rows, **settings), _transform(
        prep, scaled, **settings
    )
    assert first["rows"][2]["y"] == 0
    assert second["rows"][2]["y"] == 0
    assert (
        first["report"]["filled"][0]["donor_rows"]
        == second["report"]["filled"][0]["donor_rows"]
        == [1]
    )


def test_knn_does_not_reuse_filled_predictors_or_newly_predicted_targets(prep):
    rows = [
        {"x": 0, "y": 0},
        {"x": 10, "y": 100},
        {"x": None, "y": None},
        {"x": 5, "y": None},
    ]
    result = _transform(
        prep,
        rows,
        numeric_columns=["x", "y"],
        imputations={"x": "mean", "y": "knn"},
        knn_features=["x"],
        n_neighbors=1,
    )
    assert result["rows"][2]["x"] == 5
    assert result["rows"][2]["y"] == 50
    fallback = next(
        item
        for item in result["report"]["filled"]
        if item["row"] == 3 and item["column"] == "y"
    )
    assert fallback["method"] == "fitted_mean_fallback"
    assert fallback["reason"] == "no_common_dimensions"
    fourth = next(
        item
        for item in result["report"]["filled"]
        if item["row"] == 4 and item["column"] == "y"
    )
    assert fourth["donor_rows"] == [1]
    assert result["rows"][3]["y"] == 0


def test_knn_missing_predictor_dimensions_are_used_without_filling_them(prep):
    rows = [
        {"x": 0, "z": 0, "y": 10},
        {"x": 10, "z": 2, "y": 30},
        {"x": 1, "z": None, "y": None},
    ]
    result = _transform(
        prep,
        rows,
        numeric_columns=["x", "z", "y"],
        imputations={"y": "knn"},
        knn_features=["x", "z"],
        n_neighbors=1,
    )
    assert result["rows"][2]["y"] == 10
    assert result["rows"][2]["z"] is None
    assert result["rows"][2]["_imputed_columns"] == ["y"]


def test_knn_donor_pool_respects_explicit_groups(prep):
    rows = [
        {"g": "A", "x": 0, "y": 10},
        {"g": "B", "x": 1, "y": 1000},
        {"g": "A", "x": 1, "y": None},
    ]
    result = _transform(
        prep,
        rows,
        numeric_columns=["x", "y"],
        group_by=["g"],
        imputations={"y": "knn"},
        knn_features=["x"],
        n_neighbors=1,
    )
    assert result["rows"][2]["y"] == 10
    assert result["report"]["filled"][0]["donor_rows"] == [1]


def test_onehot_preserves_typed_categories_and_original_values(prep):
    rows = [{"y": 0, "color": value} for value in [1, "1", True]]
    result = _transform(
        prep, rows, fit_rows=[1, 2, 3], categorical_encodings={"color": "onehot"}
    )
    names = [
        name
        for name, info in result["model"]["generated_columns"].items()
        if "category" in info
    ]
    assert len(names) == 3
    vectors = [tuple(row[name] for name in names) for row in result["rows"]]
    assert len(set(vectors)) == 3
    assert all(sum(vector) == 1 for vector in vectors)
    assert [row["color"] for row in result["rows"]] == [1, "1", True]


def test_unknown_categories_refuse_by_default_but_indicator_is_explicit(prep):
    rows = [{"y": 0, "color": "A"}, {"y": 2, "color": "B"}, {"y": None, "color": "C"}]
    with pytest.raises(ValueError):
        _transform(prep, rows, categorical_encodings={"color": "onehot"})
    result = _transform(
        prep,
        rows,
        categorical_encodings={"color": "onehot"},
        unknown_categories="indicator",
    )
    assert result["rows"][2]["color"] == "C"
    assert result["rows"][2]["color__unknown"] == 1
    assert result["rows"][2]["color__missing"] == 0
    assert result["report"]["unknown_category_counts"]["color"] == 1


def test_missing_category_is_distinct_from_an_unknown_category(prep):
    rows = [{"y": 0, "color": "A"}, {"y": 2, "color": "B"}, {"y": None, "color": None}]
    result = _transform(
        prep,
        rows,
        categorical_encodings={"color": "onehot"},
        unknown_categories="indicator",
    )
    assert result["rows"][2]["color__missing"] == 1
    assert result["rows"][2]["color__unknown"] == 0
    assert result["report"]["unknown_category_counts"]["color"] == 0


def test_label_encoding_mapping_is_stable_under_training_row_order(prep):
    rows = [{"y": 0, "color": "B"}, {"y": 2, "color": "A"}]
    first = _fit(prep, rows, categorical_encodings={"color": "label"})
    second = _fit(prep, list(reversed(rows)), categorical_encodings={"color": "label"})
    assert (
        prep.transform_cleaner(rows, first)["rows"]
        == prep.transform_cleaner(rows, second)["rows"]
    )
    assert set(
        row["color__code"] for row in prep.transform_cleaner(rows, first)["rows"]
    ) == {0, 1}


def test_ordinal_encoding_requires_explicit_domain_order(prep):
    rows = [
        {"y": 0, "grade": "high"},
        {"y": 2, "grade": "low"},
        {"y": 1, "grade": "middle"},
    ]
    with pytest.raises(ValueError):
        _fit(prep, rows, categorical_encodings={"grade": "ordinal"})
    result = _transform(
        prep,
        rows,
        categorical_encodings={
            "grade": {"method": "ordinal", "order": ["low", "middle", "high"]}
        },
    )
    assert [row["grade__code"] for row in result["rows"]] == [2, 0, 1]


def test_count_text_encoding_handles_missing_text_and_audits_out_of_vocabulary(prep):
    rows = [
        {"y": 0, "text": "Apple apple, pear"},
        {"y": 2, "text": "pear"},
        {"y": 1, "text": None},
        {"y": 1, "text": "new unseen"},
    ]
    result = _transform(prep, rows, text_encodings={"text": "count"})
    apple, pear = _feature(result["model"], "text", token="apple"), _feature(
        result["model"], "text", token="pear"
    )
    assert result["rows"][0][apple] == 2
    assert result["rows"][0][pear] == 1
    assert result["rows"][2][apple] == result["rows"][2][pear] == 0
    assert result["rows"][2]["text__missing"] == 1
    assert result["rows"][3]["text__missing"] == 0
    assert result["report"]["oov_counts"]["text"] == {
        "token_count": 2,
        "unique_token_count": 2,
        "row_count": 1,
    }
    assert result["rows"][0]["text"] == rows[0]["text"]


def test_tfidf_matches_smoothed_fit_only_idf_and_l2_normalization(prep):
    rows = [
        {"y": 0, "text": "apple pear"},
        {"y": 2, "text": "pear"},
        {"y": 1, "text": "apple new"},
    ]
    result = _transform(prep, rows, text_encodings={"text": "tfidf"})
    apple, pear = _feature(result["model"], "text", token="apple"), _feature(
        result["model"], "text", token="pear"
    )
    apple_idf = math.log(3 / 2) + 1
    norm = math.sqrt(apple_idf**2 + 1)
    assert result["rows"][0][apple] == pytest.approx(apple_idf / norm)
    assert result["rows"][0][pear] == pytest.approx(1 / norm)
    assert result["rows"][1][pear] == 1
    assert result["rows"][2][apple] == 1


def test_text_feature_cap_is_fitted_and_deterministic(prep):
    rows = [
        {"y": 0, "text": "apple apple pear"},
        {"y": 2, "text": "pear banana"},
        {"y": 1, "text": "secret secret secret"},
    ]
    first = _fit(prep, rows, text_encodings={"text": "count"}, text_max_features=1)
    second = _fit(
        prep, copy.deepcopy(rows), text_encodings={"text": "count"}, text_max_features=1
    )
    assert first == second
    assert (
        len([info for info in first["generated_columns"].values() if "token" in info])
        == 1
    )
    assert all(
        info.get("token") != "secret" for info in first["generated_columns"].values()
    )


def test_generated_features_refuse_collisions_with_original_columns(prep):
    rows = [
        {"y": 0, "color": "A", "color__cat_0": "existing"},
        {"y": 2, "color": "B", "color__cat_0": "original"},
    ]
    with pytest.raises(ValueError):
        _fit(prep, rows, categorical_encodings={"color": "onehot"})


def test_generated_schema_limit_counts_missing_and_unknown_indicators(prep):
    rows = [{"y": 0, "color": "A"}, {"y": 2, "color": "B"}]
    with pytest.raises(ValueError):
        _fit(
            prep,
            rows,
            categorical_encodings={"color": "onehot"},
            max_generated_features=2,
        )
    model = _fit(
        prep,
        rows,
        categorical_encodings={"color": "onehot"},
        max_generated_features=3,
    )
    assert len(model["generated_columns"]) == 3
    with pytest.raises(ValueError):
        _fit(
            prep,
            rows,
            categorical_encodings={"color": "onehot"},
            unknown_categories="indicator",
            max_generated_features=3,
        )


def test_generated_schema_limit_applies_to_combined_encoders(prep):
    rows = [
        {"y": 0, "color": "A", "text": "apple pear"},
        {"y": 2, "color": "B", "text": "pear"},
    ]
    with pytest.raises(ValueError):
        _fit(
            prep,
            rows,
            categorical_encodings={"color": "onehot"},
            text_encodings={"text": "count"},
            max_generated_features=5,
        )


@pytest.mark.parametrize("setting", ["text_max_features", "max_generated_features"])
@pytest.mark.parametrize("value", [0, 10001, True])
def test_invalid_feature_limits_refuse_instead_of_allocating(prep, setting, value):
    with pytest.raises(ValueError):
        _fit(prep, [{"y": 0}, {"y": 2}], **{setting: value})


@pytest.mark.parametrize(
    "settings",
    [
        {"imputations": {"id": "mean"}},
        {"numeric_columns": ["id", "y"]},
        {"categorical_encodings": {"id": "onehot"}},
        {"text_encodings": {"id": "count"}},
    ],
)
def test_protected_columns_refuse_requested_transformations(prep, settings):
    rows = [{"id": "001", "y": 0}, {"id": "002", "y": 2}]
    with pytest.raises(ValueError):
        _fit(prep, rows, protected_columns=["id"], **settings)


def test_saved_model_roundtrip_preserves_transform_and_hashes(prep):
    rows = [{"y": 0, "color": "A"}, {"y": 2, "color": "B"}, {"y": None, "color": "A"}]
    model = _fit(
        prep, rows, imputations={"y": "mean"}, categorical_encodings={"color": "onehot"}
    )
    restored = json.loads(json.dumps(model, allow_nan=False))
    first, second = prep.transform_cleaner(rows, model), prep.transform_cleaner(
        rows, restored
    )
    assert first == second
    assert first["report"]["model_sha256"] == model["model_sha256"]
    assert (
        first["report"]["source_records_sha256"]
        != first["report"]["cleaned_records_sha256"]
    )


def test_convenience_cleaner_matches_explicit_fit_then_transform(prep):
    rows = [{"y": 0}, {"y": 2}, {"y": None}]
    settings = {
        "fit_rows": [1, 2],
        "numeric_columns": ["y"],
        "imputations": {"y": "mean"},
        "decision": DECISION,
    }
    expected = prep.transform_cleaner(rows, prep.fit_cleaner(rows, **settings))
    assert prep.clean_data(rows, **settings) == expected


def test_recleaning_cannot_erase_existing_imputation_metadata(prep):
    rows = [{"y": 0}, {"y": 2}, {"y": None}]
    settings = {
        "fit_rows": [1, 2],
        "numeric_columns": ["y"],
        "imputations": {"y": "mean"},
        "decision": DECISION,
    }
    result = prep.clean_data(rows, **settings)
    assert result["rows"][2]["_imputed_columns"] == ["y"]
    with pytest.raises(ValueError, match="_imputed_columns"):
        prep.clean_data(result["rows"], **settings)
    with pytest.raises(ValueError, match="_imputed_columns"):
        prep.transform_cleaner(result["rows"], result["model"])


def test_panel_integration_filled_outcomes_do_not_make_event_windows_observed(prep):
    rows = [
        {"id": "001", "t": t, "y": value}
        for t, value in enumerate([5, 5, 5, 5, 5, None] + [15] * 6)
    ]
    original = copy.deepcopy(rows)
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        window=3,
        window_options=[3],
        cleaning={
            "fit_rows": [1, 2],
            "imputations": {"y": "mean"},
            "decision": DECISION,
        },
    )
    assessment = next(
        entry
        for entry in result["window_assessment"]["assessments"]
        if entry["time"] == "6"
    )
    assert assessment["coverage"]["pre_nonmissing"] == 3
    assert assessment["coverage"]["pre_imputed_values"] == 1
    assert assessment["eligible"] is False
    assert "imputed_values_pre" in assessment["reasons"]
    assert assessment["pre_statistics"] is None
    assert not any(
        event["kind"] == "level_shift_candidate"
        for event in result["events"]["candidates"]
    )
    assert (
        result["manifest"]["raw_source_records_sha256"]
        == result["cleaning"]["report"]["source_records_sha256"]
    )
    assert rows == original


def test_imputed_post_values_are_reported_separately_from_pre_values(prep):
    rows = [
        {"id": "001", "t": t, "y": value}
        for t, value in enumerate([5] * 4 + [None] + [15] * 7)
    ]
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        window=3,
        window_options=[3],
        cleaning={
            "fit_rows": [1, 2],
            "imputations": {"y": "mean"},
            "decision": DECISION,
        },
    )
    assessment = next(
        entry
        for entry in result["window_assessment"]["assessments"]
        if entry["time"] == "3"
    )
    assert assessment["coverage"]["pre_imputed_values"] == 0
    assert assessment["coverage"]["post_imputed_values"] == 1
    assert "imputed_values_post" in assessment["reasons"]
    assert assessment["pre_statistics"] is not None


def test_wide_input_maps_imputation_markers_to_only_the_affected_panel_cell(prep):
    rows = [
        {"id": "001", "a": 5, "b": None, "c": 15, "d": 15},
        {"id": "002", "a": 5, "b": 5, "c": 5, "d": 5},
    ]
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        wide_columns={"y": {"0": "a", "1": "b", "2": "c", "3": "d"}},
        cleaning={
            "fit_rows": [2],
            "imputations": {"b": "mean"},
            "decision": DECISION,
        },
    )
    affected = next(
        row for row in result["rows"] if row["id"] == "001" and row["t"] == "1"
    )
    assert affected["y"] == 5
    assert affected["_imputed_columns"] == ["y"]
    assert affected["_source_rows"] == [1]
    assert all(
        not row.get("_imputed_columns") for row in result["rows"] if row is not affected
    )


def test_duplicate_aggregation_unions_imputed_markers_and_source_positions(prep):
    rows = [
        {"id": "001", "t": 0, "y": None},
        {"id": "001", "t": 0, "y": 4},
        {"id": "001", "t": 1, "y": 3},
        {"id": "001", "t": 2, "y": 5},
    ]
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="t",
        numeric_columns=["y"],
        frequency="integer",
        aggregations={"y": "sum"},
        cleaning={
            "fit_rows": [2],
            "imputations": {"y": "mean"},
            "decision": DECISION,
        },
    )
    aggregated = result["rows"][0]
    assert aggregated["y"] == 8
    assert aggregated["_imputed_columns"] == ["y"]
    assert aggregated["_source_rows"] == [1, 2]


@pytest.mark.parametrize("column", ["id", "t", "state"])
def test_panel_cleaning_cannot_override_automatic_protected_columns(prep, column):
    rows = [{"id": "001", "t": t, "state": int(t >= 3), "y": 5} for t in range(8)]
    with pytest.raises(ValueError):
        prep.preprocess_panel(
            rows,
            entity="id",
            time="t",
            numeric_columns=["y"],
            frequency="integer",
            treatment="state",
            cleaning={
                "fit_rows": [1, 2],
                "categorical_encodings": {column: "onehot"},
                "protected_columns": [],
                "decision": DECISION,
            },
        )


def test_cleaning_exports_are_conditional_and_checksums_cover_both_records(
    prep, tmp_path
):
    rows = [{"id": "001", "t": t, "y": None if t == 4 else 5} for t in range(8)]
    settings = {
        "entity": "id",
        "time": "t",
        "numeric_columns": ["y"],
        "frequency": "integer",
    }
    unchanged = prep.preprocess_panel(rows, **settings)
    clean = prep.preprocess_panel(
        rows,
        **settings,
        cleaning={
            "fit_rows": [1, 2],
            "imputations": {"y": "mean"},
            "decision": DECISION,
        },
    )
    original_outputs = prep.write_outputs(unchanged, tmp_path / "original")
    clean_outputs = prep.write_outputs(clean, tmp_path / "cleaned")
    assert "cleaning_report.json" not in original_outputs
    assert "cleaning_model.json" not in original_outputs
    assert (
        clean["manifest"]["raw_source_records_sha256"]
        == unchanged["manifest"]["source_records_sha256"]
    )
    manifest = json.loads(Path(clean_outputs["manifest.json"]).read_text())
    for name, expected in [
        ("cleaning_model.json", clean["cleaning"]["model"]),
        ("cleaning_report.json", clean["cleaning"]["report"]),
    ]:
        path = Path(clean_outputs[name])
        assert json.loads(path.read_text()) == expected
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == manifest["outputs"][name]["sha256"]
        )
