"""Hand-derived contracts for the adapted nominal coding-agreement helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "is-qualitative-analysis"
    / "kernel.py"
)
_SPEC = importlib.util.spec_from_file_location("_is_qualitative_kernel", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
nominal_agreement = _MODULE.nominal_agreement


def test_two_coder_point_estimates_have_known_finite_sample_values():
    result = nominal_agreement(
        [["a", "a"], ["a", "b"], ["b", "b"], ["b", "b"]],
        coder_names=["A", "B"],
    )
    # Coincidence marginals are (3, 5), observed off-diagonal is 2:
    # alpha = 1 - 7*2/(64-9-25) = 8/15. Kappa has Po=.75, Pe=.5.
    assert result["krippendorff_alpha"] == pytest.approx(8 / 15)
    assert result["cohens_kappa"] == pytest.approx(0.5)
    assert result["pairwise_agreement"] == pytest.approx(0.75)
    assert result["alpha_not_estimable_reason"] is None
    assert result["kappa_not_estimable_reason"] is None
    assert result["uncertainty"]["status"] == "not_estimated"


def test_missing_coders_use_pairability_without_inventing_categories():
    result = nominal_agreement(
        [
            ["a", "a", "b"],
            ["b", "b", None],
            ["singleton_only", None, ""],
            [None, "", None],
        ],
        coder_names=["A", "B", "C"],
    )
    # First unit contributes two discordant directed coincidences, second
    # contributes none; marginals (2,3) yield 1 - 4*2/(25-4-9) = 1/3.
    assert result["krippendorff_alpha"] == pytest.approx(1 / 3)
    assert result["pairwise_agreement"] == pytest.approx(0.5)
    assert result["category_counts_pairable"] == {"a": 2, "b": 3}
    assert result["units_total"] == 4
    assert result["units_pairable"] == result["units_excluded"] == 2
    assert result["pairable_unit_fraction"] == 0.5
    assert result["missing_by_coder"] == {"A": 1, "B": 2, "C": 3}
    assert result["pair_count"] == 4
    assert result["cohens_kappa"] is None
    assert result["kappa_not_estimable_reason"] == "requires_exactly_two_coders"


@pytest.mark.parametrize("rows", [[], [[None, ""]], [["a", None], [None, "b"]]])
def test_no_pairable_units_report_undefined(rows):
    result = nominal_agreement(rows, coder_names=["A", "B"])
    assert result["krippendorff_alpha"] is None
    assert result["cohens_kappa"] is None
    assert result["pairwise_agreement"] is None
    assert result["alpha_not_estimable_reason"] == "no_pairable_units"
    assert result["kappa_not_estimable_reason"] == "no_complete_pairs"


def test_constant_coding_is_not_perfect_chance_corrected_agreement():
    result = nominal_agreement([["a", "a"]] * 4, coder_names=["A", "B"])
    assert result["pairwise_agreement"] == 1
    assert result["krippendorff_alpha"] is None
    assert result["cohens_kappa"] is None
    assert result["alpha_not_estimable_reason"] == "zero_expected_disagreement"
    assert result["kappa_not_estimable_reason"] == "zero_expected_disagreement"


def test_perfect_agreement_requires_nonconstant_categories():
    result = nominal_agreement([["a", "a"], ["b", "b"]], coder_names=["A", "B"])
    assert result["pairwise_agreement"] == 1
    assert result["krippendorff_alpha"] == 1
    assert result["cohens_kappa"] == 1


def test_systematic_opposition_can_have_negative_alpha():
    result = nominal_agreement([["a", "b"], ["b", "a"]], coder_names=["A", "B"])
    assert result["pairwise_agreement"] == 0
    assert result["krippendorff_alpha"] == pytest.approx(-0.5)
    assert result["cohens_kappa"] == -1


@pytest.mark.parametrize(
    "rows, names",
    [
        ([["a"]], ["A", "B"]),
        ([["a", "a", "a"]], ["A", "B"]),
        (["ab"], ["A", "B"]),
        ("ab", ["A", "B"]),
        ([["a", "a"]], "AB"),
        ([["a"]], ["A"]),
        ([["a", "a"]], ["A", "A"]),
        ([["a", "a"]], ["A", " "]),
        ([[1, 1]], ["A", "B"]),
        ([[float("nan"), "a"]], ["A", "B"]),
        ([["a", " "]], ["A", "B"]),
    ],
)
def test_rejects_ragged_or_ambiguous_coding_data(rows, names):
    with pytest.raises(ValueError):
        nominal_agreement(rows, coder_names=names)
