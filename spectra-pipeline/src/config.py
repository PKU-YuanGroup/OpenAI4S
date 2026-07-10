"""Pipeline configuration + config search space.

A ``config`` is a plain dict that fully determines one pass of the pipeline
(preprocessing -> matching -> unmixing -> diagnosis). The outer loop searches
over the space defined by ``SEARCH_SPACE`` and keeps the best-scoring config.
"""
from __future__ import annotations

import copy


# ---------------------------------------------------------------------------
# Common wavenumber grid (cm^-1). Every library and target spectrum is
# resampled onto this grid so the NNLS reference matrix stays aligned.
# ---------------------------------------------------------------------------
GRID_MIN = 150.0
GRID_MAX = 1400.0
GRID_STEP = 2.0


# ---------------------------------------------------------------------------
# Default pipeline config. The loop mutates a copy of this.
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    # --- despike (cosmic-ray removal) ---
    "despike_enabled": True,
    "despike_threshold": 7.0,       # modified z-score threshold
    "despike_window": 5,            # neighbourhood for replacement

    # --- denoise (smoothing) ---
    "denoise_method": "savgol",     # {"savgol", "gaussian", "none"}
    "denoise_window": 7,            # odd; savgol window / gaussian ~sigma proxy
    "savgol_poly": 3,

    # --- baseline correction ---
    "baseline_method": "asls",      # {"asls", "airpls", "poly", "none"}
    "baseline_lam": 1e5,            # asls/airpls smoothness
    "baseline_p": 0.01,             # asls asymmetry
    "baseline_poly_order": 5,       # for poly

    # --- normalisation ---
    "normalise_method": "area",     # {"area", "max", "l2"}

    # --- candidate selection (library matching) ---
    "selection_method": "greedy",   # {"greedy" (OMP-style), "topk"}
    "match_metric": "pearson",      # correlation metric for ranking
    "top_k": 8,                     # topk: keep K hits; greedy: max components
    "corr_threshold": 0.3,          # candidate must exceed this correlation
    "greedy_min_gain": 0.01,        # greedy: min relative-residual drop to keep a component

    # --- unmixing (NNLS) ---
    "fraction_threshold": 0.05,     # drop components below this fraction, refit
}


# ---------------------------------------------------------------------------
# Search space for the outer loop. Each key maps to a list of candidate values.
# ---------------------------------------------------------------------------
SEARCH_SPACE = {
    "despike_threshold": [5.0, 7.0, 10.0],
    "denoise_method": ["savgol", "gaussian"],
    "denoise_window": [5, 7, 11, 15],
    "baseline_method": ["asls", "airpls", "poly"],
    "baseline_lam": [1e4, 1e5, 1e6],
    "baseline_p": [0.001, 0.01, 0.05],
    "baseline_poly_order": [3, 5, 7],
    "normalise_method": ["area", "max", "l2"],
    "selection_method": ["greedy", "topk"],
    "top_k": [5, 8, 12, 20],
    "corr_threshold": [0.2, 0.3, 0.4],
    "greedy_min_gain": [0.005, 0.01, 0.02, 0.04],
    "fraction_threshold": [0.03, 0.05, 0.1],
}


def default_config() -> dict:
    """Return a fresh copy of the default config."""
    return copy.deepcopy(DEFAULT_CONFIG)
