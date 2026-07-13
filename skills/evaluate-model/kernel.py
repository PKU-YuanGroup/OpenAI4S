"""Pure-stdlib metrics for common held-out model evaluations."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from typing import Any


def _same_length(*series: Sequence[Any]) -> int:
    lengths = {len(values) for values in series}
    if len(lengths) != 1:
        raise ValueError("all series must have equal length")
    size = lengths.pop()
    if size == 0:
        raise ValueError("series must not be empty")
    return size


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _roc_auc(
    y_true: Sequence[Any], scores: Sequence[float], positive: Any
) -> float | None:
    """Mann-Whitney ROC AUC with average ranks for tied scores."""
    _same_length(y_true, scores)
    labels = [value == positive for value in y_true]
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None

    ranked = sorted(enumerate(scores), key=lambda item: float(item[1]))
    positive_rank_sum = 0.0
    cursor = 0
    while cursor < len(ranked):
        end = cursor + 1
        while end < len(ranked) and ranked[end][1] == ranked[cursor][1]:
            end += 1
        average_rank = ((cursor + 1) + end) / 2.0
        positive_rank_sum += average_rank * sum(
            labels[index] for index, _ in ranked[cursor:end]
        )
        cursor = end
    return (positive_rank_sum - positives * (positives + 1) / 2) / (
        positives * negatives
    )


def binary_classification_metrics(
    y_true: Sequence[Any],
    *,
    predictions: Sequence[Any] | None = None,
    scores: Sequence[float] | None = None,
    threshold: float = 0.5,
    positive: Any = 1,
) -> dict[str, Any]:
    """Return binary confusion-matrix metrics and optional ROC AUC."""
    if predictions is None and scores is None:
        raise ValueError("provide predictions or scores")
    if predictions is not None:
        _same_length(y_true, predictions)
    if scores is not None:
        _same_length(y_true, scores)
        numeric_scores = [float(value) for value in scores]
        if any(not math.isfinite(value) for value in numeric_scores):
            raise ValueError("scores must be finite")
    else:
        numeric_scores = None

    if predictions is None:
        assert numeric_scores is not None
        predicted_positive = [value >= threshold for value in numeric_scores]
    else:
        predicted_positive = [value == positive for value in predictions]
    actual_positive = [value == positive for value in y_true]

    tp = sum(
        actual and predicted
        for actual, predicted in zip(actual_positive, predicted_positive)
    )
    tn = sum(
        not actual and not predicted
        for actual, predicted in zip(actual_positive, predicted_positive)
    )
    fp = sum(
        not actual and predicted
        for actual, predicted in zip(actual_positive, predicted_positive)
    )
    fn = sum(
        actual and not predicted
        for actual, predicted in zip(actual_positive, predicted_positive)
    )
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    balanced_accuracy = (
        (recall + specificity) / 2
        if recall is not None and specificity is not None
        else None
    )
    return {
        "n": len(y_true),
        "threshold": threshold if predictions is None else None,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": (tp + tn) / len(y_true),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "balanced_accuracy": balanced_accuracy,
        "roc_auc": (
            _roc_auc(y_true, numeric_scores, positive)
            if numeric_scores is not None
            else None
        ),
    }


def regression_metrics(
    y_true: Sequence[float], predictions: Sequence[float]
) -> dict[str, float | int | None]:
    """Return MAE, RMSE, bias, and R-squared for finite numeric pairs."""
    size = _same_length(y_true, predictions)
    truth = [float(value) for value in y_true]
    predicted = [float(value) for value in predictions]
    if any(not math.isfinite(value) for value in (*truth, *predicted)):
        raise ValueError("values must be finite")
    errors = [estimate - actual for actual, estimate in zip(truth, predicted)]
    squared_errors = [error * error for error in errors]
    mean_truth = statistics.fmean(truth)
    total_sum_squares = sum((value - mean_truth) ** 2 for value in truth)
    return {
        "n": size,
        "mae": statistics.fmean(abs(error) for error in errors),
        "rmse": math.sqrt(statistics.fmean(squared_errors)),
        "bias": statistics.fmean(errors),
        "r2": (
            1 - sum(squared_errors) / total_sum_squares if total_sum_squares else None
        ),
    }


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic: Callable[[Sequence[float]], float] = statistics.fmean,
    confidence: float = 0.95,
    resamples: int = 2000,
    seed: int = 0,
) -> dict[str, float | int]:
    """Percentile bootstrap interval for independent scalar observations."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    numeric = [float(value) for value in values]
    if any(not math.isfinite(value) for value in numeric):
        raise ValueError("values must be finite")

    rng = random.Random(seed)
    estimates = sorted(
        float(statistic(rng.choices(numeric, k=len(numeric)))) for _ in range(resamples)
    )
    alpha = (1.0 - confidence) / 2.0

    def percentile(probability: float) -> float:
        position = probability * (len(estimates) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return estimates[lower]
        fraction = position - lower
        return estimates[lower] * (1 - fraction) + estimates[upper] * fraction

    return {
        "estimate": float(statistic(numeric)),
        "lower": percentile(alpha),
        "upper": percentile(1.0 - alpha),
        "confidence": confidence,
        "resamples": resamples,
        "seed": seed,
    }
