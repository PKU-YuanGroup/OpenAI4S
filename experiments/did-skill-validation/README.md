# Synthetic DiD Skill validation

[中文说明](README_zh.md)

This offline experiment executes the actual `skills/did-analysis/kernel.py` sidecar and independently checks its arithmetic against statsmodels and SciPy. Six prespecified data-generating processes cover a known effect, a null, violated pretrends, correlated cluster noise, triple differences and heterogeneous staggered adoption. The report retains finite-cluster calibration problems instead of turning statistical rates into pass gates.

## Reproduce

Use an analysis Python environment with the optional packages in `requirements-analysis.txt`. The OpenAI4S core receives no dependency. Preserved `inputs/` and `results/` are immutable: the runner refuses an existing output directory's input or result subdirectory; the verifier refuses an existing audit report.

```bash
python experiments/did-skill-validation/verify_experiment.py --replay --report /tmp/did-independent-audit.json
python experiments/did-skill-validation/run_experiment.py --output /tmp/did-new-bundle --plots
python experiments/did-skill-validation/verify_experiment.py --bundle /tmp/did-new-bundle --replay
```

Choose fresh `/tmp` paths on subsequent runs. `--plots` executes the Skill's guarded optional matplotlib renderer. A replay runs the actual Skill again in a temporary directory and compares every preserved deterministic artifact, including the SVG and PNG when requested. Byte comparison assumes the recorded Python/package/font environment; independent numerical comparison uses explicit tolerances. Source files are hashed before execution and checked again before a manifest is finalized.

## Contents

| Entry | Purpose |
| --- | --- |
| `prespecification.json` | DGPs, assignment clusters, event dates, estimands, fixed seeds, simulation size and inference scope set before running. |
| `requirements-analysis.txt` | Versions used for independent numerical analysis and optional plotting. |
| `run_experiment.py` | Generate and save inputs, execute the actual Skill APIs and pipeline, preserve requested refusals, and freeze checksums. |
| `verify_experiment.py` | Independently recompute regressions, full joint covariance, t/F inference, all Monte Carlo decisions, artifact hashes and replay. |
| `REPORT.md` | Scientific findings, calibration limitations and exact replication scope. |
| `REPORT_zh.md` | Chinese scientific report. |
| `inputs/` | Six long-form example panels plus all 512 simulated panels in a lossless wide CSV. |
| `results/` | Skill results, pipeline exports/diagnostics, retained refusals, Monte Carlo summaries, environment and independent audit. |

The historical `experiments/did-arxiv-replication/` archive is not an input and is not modified. This experiment supplies synthetic validation, not an empirical causal claim or a full conditional/doubly robust Callaway–Sant'Anna bootstrap replication.

The current artifact manifest SHA-256 is `38e7d5a81574814b47d534afde687c897880eb7886c4af1888cc490ebac57994`. A complete rerun after the Skill's no-network frontmatter update reproduced all 45 deterministic artifacts unchanged. The final verifier against the restored repository bundle passed **12,237 checks with zero failures**. The prior local input/result bundle and comparison record remain in a local archive outside the repository; see `REPORT.md` for the current source/audit hashes and refresh provenance.
