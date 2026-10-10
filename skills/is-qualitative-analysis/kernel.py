"""Nominal coding agreement, adapted from AlterLab's MIT-licensed icr.py.

See LICENSE, NOTICE.md and UPSTREAM.json for attribution and source hashes.
No file access, model calls, optional libraries, or automatic execution.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import combinations
from typing import Any


def nominal_agreement(
    by_unit: Sequence[Sequence[str | None]], *, coder_names: Sequence[str]
) -> dict[str, Any]:
    """Describe agreement on a unit-by-coder matrix of single nominal labels.

    ``None`` and ``""`` are missing, not categories. Rows must have exactly one
    slot per named coder; a singleton-coded unit contributes no coincidence.
    Labels must be strings (encode numeric category IDs explicitly). Returns
    undefined coefficients as None with reasons, including constant coding.

    This is a point-estimate helper, not a test, CI, quality verdict, semantic
    coder, or support for ordinal, multilabel, or free-segmentation agreement.
    """
    if isinstance(coder_names, (str, bytes)):
        raise ValueError("coder_names must be a sequence of distinct names")
    names = list(coder_names)
    if len(names) < 2 or any(
        not isinstance(name, str) or not name.strip() for name in names
    ):
        raise ValueError("at least two nonblank coder names are required")
    if len(set(names)) != len(names):
        raise ValueError("coder names must be distinct")
    if isinstance(by_unit, (str, bytes)):
        raise ValueError("by_unit must be a matrix, not text")

    rows: list[list[str | None]] = []
    for index, row in enumerate(by_unit):
        if isinstance(row, (str, bytes)) or len(row) != len(names):
            raise ValueError(f"unit {index} must have {len(names)} coder slots")
        checked: list[str | None] = []
        for value in row:
            if value is None or value == "":
                checked.append(None)
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"unit {index}: labels must be nonblank strings")
            else:
                checked.append(value)
        rows.append(checked)

    pairable = [
        [value for value in row if value is not None]
        for row in rows
        if sum(value is not None for value in row) >= 2
    ]
    # Coincidence marginals equal category counts over pairable units. Each
    # unordered coder pair contributes in both directions with weight 1/(m-1).
    marginals: Counter[str] = Counter()
    off_diagonal = 0.0
    agreeing_pairs = total_pairs = 0
    for unit in pairable:
        marginals.update(unit)
        for left, right in combinations(unit, 2):
            total_pairs += 1
            if left == right:
                agreeing_pairs += 1
            else:
                off_diagonal += 2.0 / (len(unit) - 1)
    n = sum(marginals.values())
    denominator = n * n - sum(count * count for count in marginals.values())
    alpha = 1.0 - (n - 1) * off_diagonal / denominator if denominator else None
    alpha_reason = None
    if not pairable:
        alpha_reason = "no_pairable_units"
    elif not denominator:
        alpha_reason = "zero_expected_disagreement"

    kappa = None
    kappa_reason = "requires_exactly_two_coders"
    if len(names) == 2:
        if not pairable:
            kappa_reason = "no_complete_pairs"
        else:
            left_counts = Counter(unit[0] for unit in pairable)
            right_counts = Counter(unit[1] for unit in pairable)
            paired_n = len(pairable)
            # Integer denominator avoids making a constant label look defined
            # due to floating-point rounding in an expected probability of 1.
            expected = sum(
                count * right_counts[label] for label, count in left_counts.items()
            )
            kappa_denominator = paired_n * paired_n - expected
            if kappa_denominator:
                kappa = (agreeing_pairs * paired_n - expected) / kappa_denominator
                kappa_reason = None
            else:
                kappa_reason = "zero_expected_disagreement"

    return {
        "measurement_level": "nominal",
        "matrix_orientation": "units_by_coders",
        "coder_names": names,
        "units_total": len(rows),
        "units_pairable": len(pairable),
        "units_excluded": len(rows) - len(pairable),
        "pairable_unit_fraction": len(pairable) / len(rows) if rows else None,
        "missing_by_coder": {
            name: sum(row[index] is None for row in rows)
            for index, name in enumerate(names)
        },
        "category_counts_pairable": dict(sorted(marginals.items())),
        "pair_count": total_pairs,
        "pairwise_agreement": (agreeing_pairs / total_pairs if total_pairs else None),
        "krippendorff_alpha": alpha,
        "alpha_not_estimable_reason": alpha_reason,
        "cohens_kappa": kappa,
        "kappa_not_estimable_reason": kappa_reason,
        "uncertainty": {
            "status": "not_estimated",
            "reason": "requires_a_design_specific_independent_sampling_unit",
        },
    }
