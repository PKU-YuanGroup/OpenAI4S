"""Ground-truth evaluation — run ONCE after the blind search loop, never during
it. This mirrors a scientist who has no answer key while tuning the analysis and
only checks against the truth at the very end.

Kept separate from ``metrics.py`` (which holds the *spectral* similarity metrics
used inside the blind fit) and from ``loop.py`` (the blind search itself).
"""
from __future__ import annotations

import numpy as np

from .loop import PipelineResult


def component_prf(true_names, pred_names) -> dict:
    """Precision / recall / F1 on the set of identified components."""
    t, p = set(true_names), set(pred_names)
    tp = len(t & p)
    precision = tp / len(p) if p else 0.0
    recall = tp / len(t) if t else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def fraction_mae(true_map: dict, pred_map: dict) -> float:
    """Mean absolute error of fractions over the union of components."""
    keys = set(true_map) | set(pred_map)
    if not keys:
        return 0.0
    return float(np.mean([abs(true_map.get(k, 0.0) - pred_map.get(k, 0.0)) for k in keys]))


def evaluate(result: PipelineResult, truth: dict) -> dict:
    """Ground-truth evaluation, run ONCE after the loop (never during search).

    ``truth`` is a plain dict with ``true_names`` / ``true_fractions`` as loaded
    from a case's ``truth.json``.
    """
    prf = component_prf(truth["true_names"], list(result.fractions))
    mae = fraction_mae(truth["true_fractions"], result.fractions)
    return {**prf, "fraction_mae": mae}
