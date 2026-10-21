# Pre-screening window review

[中文说明](README_zh.md)

This offline follow-up uses the actual panel Skill's `assess_windows` to distinguish enough complete observations from a sufficiently informative variability baseline. It reads the existing public inputs, performs no imputation, screens no events and changes no DiD estimates or previous results. Every observed entity-period is a possible first-post **review anchor**; the counts below are calendar positions, not treatment dates or detected events.

## Run and files

From the repository root, use a Python environment with matplotlib for the saved figure:

```bash
MPLCONFIGDIR=/tmp/openai4s-window-mpl python experiments/did-arxiv-replication/window-review/run_review.py --output /tmp/window-review-new
```

The runner verifies public CSV hashes against `../inputs/sources.json`, imports the actual Skill sidecar and refuses to overwrite its output artifacts. Normal execution is offline. `environment.json` records versions and code/output hashes; plot byte equality additionally depends on the runtime and fonts.

| File | Purpose |
| --- | --- |
| `run_review.py` | Reproducible audit of public panels and a clearly labeled constructed example; optional plot imports are guarded. |
| `synthetic.csv` | Preserved 96-row, three-series deterministic illustration; no treatment or event is embedded. |
| `audit.json` | Configurations, coverage counts, variability status counts, deterministic representative assessments/recommendations and fixed synthetic anchor details. |
| `comparison.png` | Four-panel review of coverage, variability statuses, constructed trajectories and conditional window advice. |
| `comparison.svg` | Vector export of the same figure. |
| `environment.json` | Python/matplotlib versions plus script, Skill and output hashes. |

The full API assessment for every anchor can be regenerated from the preserved input and script. The saved audit retains aggregate summaries and the first example of every eligible status/action to avoid duplicating thousands of detailed records.

## Fixed diagnostic assumptions

Public inputs and licenses remain in `../inputs/`; source URLs and hashes are repeated in the audit. Compare mpdta at windows 2 and 3 using a tolerance of **0.05 log-employment units**, and base_stagg at windows 3 and 5 using **1 outcome unit**. These are declared diagnostic assumptions, not thresholds from the papers. A complete pre-window shorter than five periods is marked short for quantitative noise advice.

The displayed `SD / sqrt(n)` estimates mean variation only under stable independent noise. Raw sample variance/SD, MAD, fitted slope, residual SD and sufficiently long lag-one correlation diagnostics are retained. Trend and correlation review take priority over automatic noise interpretation. Window recommendations are advisory and leave the requested screen unchanged. No lower variance, detection power, independent errors or causal validity is guaranteed by a wider window; earlier shocks, drift and seasonality may contaminate it.

## Public-data findings

| Input / window | Complete review anchors | Variability status among complete anchors |
| --- | --- | --- |
| mpdta / 2 | 1,000 / 2,500 | All 1,000 have a short pre baseline. |
| mpdta / 3 | 0 / 2,500 | Five annual observations cannot fit two three-period windows. |
| base_stagg / 3 | 475 / 950 | 371 short baselines; 104 trend reviews. |
| base_stagg / 5 | 95 / 950 | 33 high under IID; 60 within tolerance under IID; 2 trend reviews. |

Increasing the public simulation window from three to five periods reduces eligible anchors from 475 to 95. A lower overall median of the displayed mean-variation approximation (1.001 to 0.909 units) compares different sets of anchors and does **not** demonstrate that widening improves a specific event screen. The API compares recommendations at the same anchor. The real panel cannot provide a complete five-period pre/post baseline at all; no quantitative widening recommendation can solve absent history.

## Constructed illustration

Three 32-period series have baseline 10 and repeat the explicitly saved fluctuations `[-1.3, 0.7, 0.4, -0.9, 1.1]`: quiet and trend use amplitude 0.04, noisy uses amplitude 1.1, and trend adds 0.35 per period. These periodic deterministic patterns are constructed to illustrate branches of the advisory logic, not an IID simulation or an empirical validation. The fixed review anchor is period 17, requested window is five, inspected windows are 3/5/7, and tolerance is 0.5 outcome units.

At that fixed anchor the noisy series' displayed approximation is 0.514 units at five periods and 0.457 at seven: the API conditionally advises considering seven. Quiet retains five (0.019 units), while trend requires trend review instead of enlargement. All three have enough observations for the inspected windows, but their advice differs. No event screen, treatment date or causal estimate is selected using these outcomes.

The 2026-10-10 source refresh reran this audit twice with the current Skill. All five generated files reproduced byte for byte; the prior synthetic data, audit results and figures are unchanged. Only the environment record updates the Skill source hash. See [the refresh record](../REPORT.md#current-source-refresh--2026-10-10).
