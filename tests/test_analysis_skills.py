"""Executable contracts for bundled data and model analysis Skills."""

from __future__ import annotations

import importlib
import sys

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader

SKILLS = ("audit-dataset", "evaluate-model", "plan-ml-experiment")


@pytest.fixture(scope="module", autouse=True)
def _skills_on_path():
    path = str(get_config().skills_dir)
    sys.path.insert(0, path)
    yield
    sys.path.remove(path)


def _kernel(name: str):
    return importlib.import_module(f"{name}.kernel")


@pytest.mark.parametrize("name", SKILLS)
def test_analysis_skill_is_discoverable_and_compiles(name):
    skill = SkillLoader().discover()[name]
    assert skill.origin == "openai4s"
    assert skill.read_only is True
    assert skill.has_kernel is True
    assert skill.sidecar_gate() == {"ok": True, "error": None}


@pytest.mark.parametrize(
    "query, expected",
    [
        ("audit missing duplicates patient split leakage", "audit-dataset"),
        ("bootstrap confidence interval ROC AUC regression metrics", "evaluate-model"),
        ("reproducible grouped chronological train test split", "plan-ml-experiment"),
    ],
)
def test_analysis_skill_is_retrievable(query, expected):
    hits = SkillLoader().search(query, limit=3)
    assert expected in [hit["name"] for hit in hits]


def test_audit_rows_finds_duplicates_types_and_group_leakage():
    audit_rows = _kernel("audit-dataset").audit_rows
    rows = [
        {"id": "a", "patient": 1, "split": "train", "label": 0, "value": 3.0},
        {"id": "b", "patient": 1, "split": "test", "label": 1, "value": ""},
        {"id": "b", "patient": 2, "split": "test", "label": 1, "value": "bad"},
        {"id": "b", "patient": 2, "split": "test", "label": 1, "value": "bad"},
    ]
    result = audit_rows(
        rows,
        target="label",
        id_columns=("id",),
        group_columns=("patient",),
        split_column="split",
    )
    assert result["duplicate_rows"] == 1
    assert result["duplicate_ids"]["count"] == 2
    assert result["columns"]["value"]["missing"] == 1
    assert result["columns"]["value"]["types"] == {"float": 1, "str": 2}
    assert result["split_leakage"]["patient"] == {"count": 1, "examples": [1]}


def test_binary_metrics_include_tie_aware_auc_and_undefined_ratios():
    metrics = _kernel("evaluate-model")
    result = metrics.binary_classification_metrics(
        [0, 0, 1, 1], scores=[0.1, 0.4, 0.4, 0.9], threshold=0.5
    )
    assert result["accuracy"] == 0.75
    assert result["precision"] == 1.0
    assert result["recall"] == 0.5
    assert result["roc_auc"] == pytest.approx(0.875)

    no_positive_predictions = metrics.binary_classification_metrics(
        [0, 1], predictions=[0, 0]
    )
    assert no_positive_predictions["precision"] is None
    assert no_positive_predictions["f1"] is None


def test_regression_metrics_and_bootstrap_are_deterministic():
    metrics = _kernel("evaluate-model")
    result = metrics.regression_metrics([1, 2, 3], [1, 2, 4])
    assert result["mae"] == pytest.approx(1 / 3)
    assert result["rmse"] == pytest.approx((1 / 3) ** 0.5)
    assert result["r2"] == pytest.approx(0.5)
    first = metrics.bootstrap_ci([1, 2, 3, 4], resamples=100, seed=7)
    second = metrics.bootstrap_ci([1, 2, 3, 4], resamples=100, seed=7)
    assert first == second
    assert first["lower"] <= first["estimate"] <= first["upper"]


def test_planning_splits_preserve_rows_groups_and_time_order(tmp_path):
    plan = _kernel("plan-ml-experiment")
    random_split = plan.random_split(20, fractions=(0.6, 0.2, 0.2), seed=4)
    assert sorted(sum(random_split.values(), [])) == list(range(20))
    assert {name: len(values) for name, values in random_split.items()} == {
        "train": 12,
        "validation": 4,
        "test": 4,
    }

    groups = ["a", "a", "b", "b", "c", "d", "e", "f"]
    grouped = plan.grouped_split(groups, seed=8)
    assert sorted(sum(grouped.values(), [])) == list(range(len(groups)))
    owners = {
        group: {
            split
            for split, indices in grouped.items()
            for index in indices
            if groups[index] == group
        }
        for group in set(groups)
    }
    assert all(len(splits) == 1 for splits in owners.values())

    chronological = plan.chronological_split([5, 1, 4, 2, 3], fractions=(0.6, 0.2, 0.2))
    assert chronological == {"train": [1, 3, 4], "validation": [2], "test": [0]}

    data = tmp_path / "data.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    manifest = plan.experiment_manifest(
        {"learning_rate": 0.1},
        data_paths=[data],
        seeds=[3],
        code_revision="abc123",
    )
    assert len(manifest["config_fingerprint"]) == 64
    assert len(manifest["data"][0]["sha256"]) == 64
    assert manifest["code_revision"] == "abc123"
