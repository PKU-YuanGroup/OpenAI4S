"""Single pass of the pipeline: config -> full result (blind, no ground truth)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import diagnose as diag
from . import matching, unmix
from .data import Library
from .preprocess import preprocess


@dataclass
class PipelineResult:
    processed: np.ndarray
    recon: np.ndarray
    candidate_names: list
    fractions: dict                      # name -> fraction
    used_idx: np.ndarray
    used_coef: np.ndarray
    diagnostics: dict
    support: dict = field(default_factory=dict)   # name -> [peak positions]


def run_pipeline(spectrum: np.ndarray, lib: Library, config: dict,
                 with_support: bool = False) -> PipelineResult:
    """Preprocess -> match -> select candidates -> NNLS unmix -> diagnose."""
    processed = preprocess(spectrum, config)

    cand_idx, cand_names, _ = matching.select_candidates(processed, lib, config)
    fractions, used_idx, used_coef = unmix.unmix(processed, lib, cand_idx, config)
    recon = unmix.reconstruct(lib, used_idx, used_coef)

    diagnostics = diag.diagnose(processed, recon, lib.grid, config)

    support = {}
    if with_support:
        for name in fractions:
            support[name] = diag.supporting_peaks(processed, lib, name, lib.grid)

    return PipelineResult(
        processed=processed, recon=recon, candidate_names=cand_names,
        fractions=fractions, used_idx=used_idx, used_coef=used_coef,
        diagnostics=diagnostics, support=support,
    )
