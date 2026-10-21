# DiD worked-example replication report

## Scope and sources

Two arXiv methods were implemented and checked against public official examples:

- [Callaway & Sant'Anna, arXiv:1803.09015v4](https://arxiv.org/abs/1803.09015v4), using the authors' [did real-data example](https://bcallaway11.github.io/did/articles/did-basics.html). `mpdta` is a subset, not the complete empirical application in the paper: 500 counties, five years (2003–2007), 2,500 rows. Never-treated counties: 309; treatment cohorts 2004/2006/2007: 20/40/131.
- [Sun & Abraham, arXiv:1804.05785v2](https://arxiv.org/abs/1804.05785v2), implementing the Section 4 interaction-weighted estimator on the [official fixest example](https://lrberge.github.io/fixest/articles/fixest_walkthrough.html). `base_stagg` is a fixed simulated dataset, not the paper's empirical data: 95 individuals, 10 periods, 950 rows; 50 never-treated individuals and five in each cohort 2–10.

Original R binaries and their 17-digit CSV conversions are saved in `inputs/`. Fixed repository commits, input hashes, conversion versions and paper links appear in `inputs/sources.json`. No new random draws were generated. Both source packages declare GPL-3; a license copy accompanies the data. The normal analysis is offline.

## What the skill actually did

The runner imports the actual `skills/panel-data-preprocessing/kernel.py`; preprocessing and plots are not reimplemented in the experiment. It calls `read_csv`, `preprocess_panel`, `write_outputs` and `render_diagnostics`.

Entity IDs remain strings. Annual/integer frequency and numeric outcomes are explicit. Both panels pass complete-calendar audits with no missing measurements, duplicated entity-periods, imputation or row deletion. Source-row mappings and output hashes are exported.

The original `treat` and `treated` columns encode ever-treated membership. We derive current exposure as `first_treatment > 0 AND period >= first_treatment`, with the simulation's never-treated sentinel explicitly handled. `base_stagg` actually uses 10000 for `year_treated` and -1000 for its relative-time sentinel; some prose in its documentation says 1000. The raw values are preserved.

The main trajectory subsets are chosen by ID and cohort, independently of outcomes. An additional simulation candidate-review figure deliberately selects the first four distinct candidate entities (33, 65, 26, 28); its selection is documented and does not represent the full sample. Orange lines show exposure records; pink lines show outcome-based candidates. Sample coverage plots always use all entities.

## Estimates and independent verification

For the unconditional C&S example, group-time ATT is the difference between the treated cohort's mean outcome change and the never-treated group's mean change. The post-treatment base is `g-1`; the pre-treatment pseudo-ATT base is `t-1`, matching the official varying-base example. Dynamic estimates weight supported cohort cells by cohort size. The reported overall dynamic ATT averages the four post-treatment event-time estimates equally; it is not the simple average over all treated observations.

For Sun–Abraham, we estimate `y ~ x1 + cohort × relative-time indicators + unit FE + period FE`. The never-treated cohort and relative period -1 are reference categories. A balanced-panel within projection implements the fixed effects. Cohort-event coefficients are aggregated using supported cell sample shares; overall ATT weights treated post-period observations. The TWFE comparison uses common relative-period indicators, `x1` and the same fixed effects and reference period.

| Check | Our result | Published or known reference | Outcome |
| --- | ---: | ---: | --- |
| C&S: group 2004, year 2006 | -0.137259 | -0.1373 | Within four-decimal rounding |
| C&S: overall dynamic ATT | -0.0772398215 | -0.0772 | Within four-decimal rounding |
| Sun–Abraham: overall ATT | -1.1337494488 | -1.133749 | Within six-decimal rounding |
| Sun–Abraham: x1 coefficient | 0.9946783109 | 0.994678 | Within six-decimal rounding |
| Simulation true overall ATT | -1.0000000000 | -1 | Exact to numeric tolerance |

All **24 published/known reference checks** pass: 12 C&S group-time cells, seven dynamic cells, overall dynamic ATT, and four simulation quantities. Full-precision values and decimal rounding tolerances are saved in `results/results.json` and `reference_values.json`.

Across the 17 nonreference event times in this one simulation draw, equal-event-time curve RMSE against known truth is **1.146828 for TWFE** and **0.340291 for Sun–Abraham**. This is a descriptive comparison on one fixed example, not a Monte Carlo performance result.

`verify_experiment.py` uses statsmodels with explicit unit and time dummies, independently of the main within projection: all 81 Sun–Abraham cohort-event coefficients agree with maximum absolute error **5.42e-14**. Twelve separate two-period change regressions agree with the C&S difference-in-means calculations within **1.42e-15**. A full rerun in the recorded environment yields **48/48 identical artifact hashes**, including CSV, JSON, PNG and SVG files. See `results/verification.json`.

Only point estimates and the simulation regression RMSE are reproduced. Bootstrap inference, confidence bands, significance tests and the papers' full empirical applications remain outside this experiment's scope. The real-data outcome is in log-employment units; its dynamic aggregation changes cohort support across event time.

## What visual event discovery revealed

Exploratory settings were fixed before reviewing candidates: threshold 3, practical minimum change 0.05 log units for the real data and 1 outcome unit for the simulation. We retained both window choices rather than selecting one after looking at the results.

| Data | Periods per pre/post window | Numeric candidates | Treated units with complete windows at adoption | Candidates exactly on adoption dates |
| --- | ---: | ---: | ---: | ---: |
| Real employment panel | 3 | 0 | 0 | 0 |
| Real employment panel | 2 | 180 | 40 | 6 |
| Fixed simulation | 3 | 13 | 25 | 2 |
| Fixed simulation | 2 | 96 | 35 | 14 |

Five annual observations cannot provide two three-period windows. Reducing the window to two makes the screen much more sensitive to noise: its pre-window increment MAD has only one increment and is necessarily zero. Small baseline variance can also inflate the change/SD score. The screen provides exploratory candidates, not calibrated significance or exact treatment dates. A date that differs from adoption can represent a real outcome change from another cause; it is not automatically a false positive.

In the candidate-review plots, raw trajectories contain covariate movements, period effects and noise as well as treatment effects. A change can precede adoption or emerge during a recovery. This is why treatment dates in both DiD models come from the original treatment-cohort variables, never from detected outcome changes.

These results suggest three useful follow-ups for the skill: report window eligibility before screening; show cohort trajectories alongside individual trajectories; and add a separately specified residual-change screen to explore covariate/common-time movements. Such follow-ups need their own method and validation rather than silently changing the present heuristic.

## Current-source refresh — 2026-10-10

The saved results now come from a fresh execution of the unchanged runner
against panel sidecar SHA-256
`e0fd9da1b7a36183ee3cf6da07501531f0d9bf38da7c2d56b217ab0e31fdda3d`.
The preceding run recorded
`f6548f003f6006962efc098981e6fbb6d8b5abaa571247386eb1a2a32e13b359`,
which predates window assessment. The independent verifier executed another
complete run: all 48 analysis files match byte for byte, all four original
input checksums match, and the 24 reference checks and independent regression
errors above are unchanged. The temporary verification path is explicitly
redacted in the published report; no numerical evidence is redacted.

All existing estimate, panel, candidate and event-alignment CSVs, together with
all existing PNG/SVG figures, are byte-identical to the preceding run. The ten
additional files contain window assessments (CSV/JSON for each dataset) and
three PNG/SVG readiness-figure pairs. Existing JSON summaries, configuration
and visualization manifests now include those diagnostics. No treatment date,
estimator, input, sample, point estimate, candidate or threshold was changed.

The separate `window-review/` was also executed twice against the current
sidecar; all five generated files match exactly between the two runs. Its
synthetic CSV, audit JSON and two figures are byte-identical to its preceding
archive. Only its environment record changes the Skill source hash from
`1a5a5aa2e7c25eeaf9aacc595ae48192ea53f6c34d574e7394fee8df0d3225a7`
to the current hash above. This refresh establishes compatibility with the
current implementation; it adds no inferential or causal validity claim.
