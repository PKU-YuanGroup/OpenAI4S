"""Multi-component unmixing via non-negative least squares."""
from __future__ import annotations

import numpy as np
from scipy.optimize import nnls

from .data import Library


def unmix(processed: np.ndarray, lib: Library, cand_idx: np.ndarray, config: dict):
    """NNLS fit on candidate columns; drop tiny fractions and refit.

    Returns dict: names -> fraction (normalised to sum 1), plus the raw
    coefficient vector and the final candidate index set actually used.
    """
    idx = np.asarray(cand_idx)
    A = lib.A[:, idx]
    coef, _ = nnls(A, processed)

    total = coef.sum()
    if total <= 0:
        return {}, idx, np.zeros_like(coef)

    frac = coef / total
    keep = frac >= config["fraction_threshold"]
    if keep.sum() == 0:
        keep[np.argmax(frac)] = True

    # refit on kept components
    idx2 = idx[keep]
    A2 = lib.A[:, idx2]
    coef2, _ = nnls(A2, processed)
    total2 = coef2.sum() or 1.0
    frac2 = coef2 / total2

    names = [lib.names[j] for j in idx2]
    fractions = {names[i]: float(frac2[i]) for i in range(len(names))}
    return fractions, idx2, coef2


def reconstruct(lib: Library, idx: np.ndarray, coef: np.ndarray) -> np.ndarray:
    """Reconstructed (area-normalised) spectrum from fitted coefficients."""
    if len(idx) == 0:
        return np.zeros(lib.A.shape[0])
    recon = lib.A[:, np.asarray(idx)] @ np.asarray(coef)
    s = recon.sum()
    return recon / s if s > 0 else recon
