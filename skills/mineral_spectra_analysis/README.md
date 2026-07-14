# Mineral Spectra Analysis Skill

This progressive-disclosure Skill describes a Raman mixed-mineral workflow: preprocess once, iteratively match/subtract residual peaks against a reference library, unmix with NNLS, diagnose reliability, and optionally evaluate synthetic cases with hidden truth.

The sidecar contains the numerical pipeline but uses optional numpy/scipy/pybaselines/matplotlib and may download/cache RRUFF data when allowed. Its current library/pipeline is explicitly prototype-oriented; a high synthetic score does not establish real-sample mineral identification.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Main recipe for observable inputs, dependency/data preparation, one-time preprocessing, blind residual loop, NNLS, diagnostics, reports, synthetic evaluation separation, outputs, and interpretation limits. |
| [`kernel.py`](kernel.py) | Optional sidecar: checks optional dependencies/config; parses/downloads/builds an aligned RRUFF library; reads/resamples spectra; despikes, denoises, baseline-corrects and normalizes; detects/matches peaks; ranks references; performs NNLS reconstruction; runs blind-loop diagnostics; renders figures/reports; computes truth metrics; and generates/saves synthetic benchmark cases. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | Committed synthetic case inputs, hidden truth, recorded blind-analysis output, derived report, and a stdlib report rebuilder. |
