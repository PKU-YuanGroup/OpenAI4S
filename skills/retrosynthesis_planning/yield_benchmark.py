"""Distribution-shift-aware reaction-yield protocol for Scenario 6."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .benchmark_common import (
    BenchmarkProtocolError,
    finite_number,
    require_exact_fields,
    require_text,
    sha256_json,
)
from .single_step_benchmark import normalize_precursor_set, rdkit_canonicalize

Canonicalizer = Callable[[str], str]
SPLITS = ("random_test", "mff_test1", "mff_test2", "mff_test3", "mff_test4")
DOMAIN_STATUSES = frozenset({"matched", "uncertain", "out_of_domain", "screening_only"})
INPUT_FIELDS = frozenset({"reaction_id", "split", "reactants", "reagents", "product"})
PREDICTION_FIELDS = frozenset(
    {
        "reaction_id",
        "predicted_yield_percent",
        "interval_lower",
        "interval_upper",
        "domain_status",
    }
)
REFERENCE_FIELDS = frozenset({"reaction_id", "yield_percent"})


def _components(
    value: Any, *, field: str, allow_empty: bool, canonicalizer: Canonicalizer
) -> str:
    if allow_empty and value in (None, ""):
        return ""
    return ".".join(
        normalize_precursor_set(
            require_text(value, field=field), canonicalizer=canonicalizer
        )
    )


def validate_yield_inputs(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
    require_all_splits: bool = True,
) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_signatures: set[tuple[str, str, str]] = set()
    observed_splits: set[str] = set()
    for index, row in enumerate(rows, start=1):
        require_exact_fields(row, INPUT_FIELDS, field=f"yield input {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        split = require_text(row["split"], field="split")
        if split not in SPLITS:
            raise BenchmarkProtocolError(f"unsupported yield split {split!r}")
        reactants = _components(
            row["reactants"],
            field="reactants",
            allow_empty=False,
            canonicalizer=canonicalizer,
        )
        reagents = _components(
            row["reagents"],
            field="reagents",
            allow_empty=True,
            canonicalizer=canonicalizer,
        )
        product = require_text(
            canonicalizer(require_text(row["product"], field="product")),
            field="canonical product",
        )
        signature = (reactants, reagents, product)
        if reaction_id in seen or signature in seen_signatures:
            raise BenchmarkProtocolError(
                "yield IDs and reaction structures must be unique"
            )
        seen.add(reaction_id)
        seen_signatures.add(signature)
        observed_splits.add(split)
        result.append(
            {
                "reaction_id": reaction_id,
                "split": split,
                "reactants": reactants,
                "reagents": reagents,
                "product": product,
            }
        )
    if not result:
        raise BenchmarkProtocolError("yield inputs must not be empty")
    if require_all_splits and observed_splits != set(SPLITS):
        raise BenchmarkProtocolError(
            f"yield benchmark must contain all frozen splits; missing {sorted(set(SPLITS) - observed_splits)}"
        )
    return tuple(result)


def normalize_yield_outputs(
    inputs: Sequence[Mapping[str, str]],
    predictions: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    input_by_id = {row["reaction_id"]: row for row in inputs}
    prediction_by_id: dict[str, Mapping[str, Any]] = {}
    for index, prediction in enumerate(predictions, start=1):
        require_exact_fields(
            prediction, PREDICTION_FIELDS, field=f"yield prediction {index}"
        )
        reaction_id = require_text(prediction["reaction_id"], field="reaction_id")
        if reaction_id not in input_by_id or reaction_id in prediction_by_id:
            raise BenchmarkProtocolError(
                f"unknown or duplicate reaction {reaction_id!r}"
            )
        prediction_by_id[reaction_id] = prediction
    if set(prediction_by_id) != set(input_by_id):
        raise BenchmarkProtocolError("yield output must cover every input")

    result: list[dict[str, Any]] = []
    for reaction_id, input_row in input_by_id.items():
        prediction = prediction_by_id[reaction_id]
        estimate = finite_number(
            prediction["predicted_yield_percent"],
            field="predicted_yield_percent",
            allow_none=False,
        )
        lower = finite_number(prediction["interval_lower"], field="interval_lower")
        upper = finite_number(prediction["interval_upper"], field="interval_upper")
        if (lower is None) != (upper is None):
            raise BenchmarkProtocolError(
                "yield interval endpoints must both be present or both be null"
            )
        if lower is not None and upper is not None and lower > upper:
            raise BenchmarkProtocolError(
                "interval_lower must not exceed interval_upper"
            )
        status = require_text(prediction["domain_status"], field="domain_status")
        if status not in DOMAIN_STATUSES:
            raise BenchmarkProtocolError(f"unsupported domain_status {status!r}")
        result.append(
            {
                **input_row,
                "predicted_yield_percent": estimate,
                "interval_lower": lower,
                "interval_upper": upper,
                "domain_status": status,
                "prediction_outside_physical_range": estimate is not None
                and not 0.0 <= estimate <= 100.0,
            }
        )
    return tuple(result)


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left)
        * sum((b - right_mean) ** 2 for b in right)
    )
    return numerator / denominator if denominator else None


def _group_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    errors = [float(row["predicted"]) - float(row["observed"]) for row in rows]
    observed = [float(row["observed"]) for row in rows]
    predicted = [float(row["predicted"]) for row in rows]
    mean_observed = sum(observed) / len(observed)
    residual_sum = sum(error * error for error in errors)
    total_sum = sum((value - mean_observed) ** 2 for value in observed)
    intervals = [row for row in rows if row["lower"] is not None]
    top_count = max(1, math.ceil(len(rows) * 0.1))
    true_top = set(
        sorted(range(len(rows)), key=lambda i: observed[i], reverse=True)[:top_count]
    )
    predicted_top = set(
        sorted(range(len(rows)), key=lambda i: predicted[i], reverse=True)[:top_count]
    )
    precision = len(true_top & predicted_top) / top_count
    return {
        "count": len(rows),
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": math.sqrt(residual_sum / len(errors)),
        "r2": 1.0 - residual_sum / total_sum if total_sum else None,
        "spearman": _pearson(_average_ranks(observed), _average_ranks(predicted)),
        "top_decile_precision": precision,
        "top_decile_enrichment": precision / (top_count / len(rows)),
        "interval_count": len(intervals),
        "interval_coverage": (
            sum(
                float(row["lower"]) <= float(row["observed"]) <= float(row["upper"])
                for row in intervals
            )
            / len(intervals)
            if intervals
            else None
        ),
        "mean_interval_width": (
            sum(float(row["upper"]) - float(row["lower"]) for row in intervals)
            / len(intervals)
            if intervals
            else None
        ),
        "out_of_range_prediction_rate": sum(
            not 0.0 <= value <= 100.0 for value in predicted
        )
        / len(rows),
    }


def evaluate_yield_predictions(
    predictions: Sequence[Mapping[str, Any]],
    reference_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    # The non-empty invariant is enforced on the normalize side by every
    # validate_* helper; without it here the metric divisions below reduce
    # to a bare ZeroDivisionError instead of a protocol refusal.
    if not predictions:
        raise BenchmarkProtocolError("yield predictions must not be empty")
    references: dict[str, float] = {}
    for index, row in enumerate(reference_rows, start=1):
        require_exact_fields(row, REFERENCE_FIELDS, field=f"yield reference {index}")
        reaction_id = require_text(row["reaction_id"], field="reaction_id")
        observed = finite_number(
            row["yield_percent"], field="yield_percent", allow_none=False
        )
        if (
            reaction_id in references
            or observed is None
            or not 0.0 <= observed <= 100.0
        ):
            raise BenchmarkProtocolError(
                "reference yields require unique IDs and values in [0, 100]"
            )
        references[reaction_id] = observed
    if {row["reaction_id"] for row in predictions} != set(references):
        raise BenchmarkProtocolError("prediction and reference reactions must match")

    scored: list[dict[str, Any]] = []
    by_split: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    for prediction in predictions:
        row = {
            "reaction_id": prediction["reaction_id"],
            "split": prediction["split"],
            "observed": references[prediction["reaction_id"]],
            "predicted": prediction["predicted_yield_percent"],
            "lower": prediction["interval_lower"],
            "upper": prediction["interval_upper"],
            "domain_status": prediction["domain_status"],
        }
        scored.append(row)
        by_split[row["split"]].append(row)
    nonempty = {split: _group_metrics(rows) for split, rows in by_split.items() if rows}
    ood = [row for row in scored if row["split"].startswith("mff_test")]
    ood_groups = [
        metrics for split, metrics in nonempty.items() if split.startswith("mff_test")
    ]
    result = {
        "schema_version": 1,
        "scenario_id": "reaction_yield_distribution_shift_v1",
        "reaction_count": len(scored),
        "overall": _group_metrics(scored),
        "by_split": nonempty,
        "macro_ood_mae": (
            sum(metrics["mae"] for metrics in ood_groups) / len(ood_groups)
            if ood_groups
            else None
        ),
        "worst_group_mae": max(metrics["mae"] for metrics in nonempty.values()),
        "ood_pooled": _group_metrics(ood) if ood else None,
        "domain_status_counts": {
            status: sum(row["domain_status"] == status for row in scored)
            for status in sorted(DOMAIN_STATUSES)
        },
        "reactions": scored,
        "caveat": "Yield labels are dataset-specific reported outcomes, not universal intrinsic reaction constants.",
    }
    result["result_sha256"] = sha256_json(result)
    return result


__all__ = [
    "BenchmarkProtocolError",
    "DOMAIN_STATUSES",
    "SPLITS",
    "evaluate_yield_predictions",
    "normalize_yield_outputs",
    "validate_yield_inputs",
]
