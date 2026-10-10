# DiD arXiv replications

[中文说明](README_zh.md)

Two bounded official worked-example replications using the actual [panel preprocessing Skill](../../skills/panel-data-preprocessing/SKILL.md): Callaway–Sant'Anna on public county employment data and Sun–Abraham on a public fixed simulation draw. **24/24 reference checks pass.** The full papers' empirical applications and inference are outside the current scope. Read [the report](REPORT.md) for methods, limits and the event-discovery findings.

## Run offline

From the repository root, use a scientific Python environment with Python 3.13, numpy 2.3.5 and matplotlib 3.10.9. The saved CSVs make R, pyreadr, pandas, model credentials and network access unnecessary for normal analysis. Independent validation additionally needs statsmodels 0.14.5. Pinned analysis versions are in `requirements-analysis.txt`; transitive dependencies and fonts can differ across machines, so image byte identity is guaranteed only in the recorded environment.

```bash
python experiments/did-arxiv-replication/run_experiment.py --output /tmp/did-new-run
python experiments/did-arxiv-replication/verify_experiment.py --results /tmp/did-new-run
```

Choose a new empty output directory; the runner refuses to overwrite existing artifacts. This checkout already includes the verified `results/` run. Verification performs independent regressions and a full rerun in a temporary directory, then writes verification.json to the selected results directory. This report is regenerated; the analysis artifacts are preserved.

The saved `results/` were refreshed on 2026-10-10 by executing the current Skill and then the full independent verifier: 24/24 reference checks and 48/48 artifact comparisons pass. The verifier still rejects a different Skill source hash. All existing estimate, panel and candidate CSVs and existing figures are byte-identical to the earlier run; ten window-audit/figure files are now included. The report records the previous and current source hashes and the scope of this refresh. The separate `window-review/` was also rerun twice with the current Skill; all five generated files reproduced exactly.

Fetching is a separate, explicit online step if input files need restoring:

```bash
uv run --no-project --with pyreadr==0.5.7 --with pandas==3.0.3 python experiments/did-arxiv-replication/fetch_inputs.py
```

The original acquisition versions in `inputs/sources.json` take precedence if an environment differs. Existing input data are preserved; changed CSVs or provenance are refused.

## Files and directories

| File or directory | Purpose |
| --- | --- |
| `fetch_inputs.py` | Acquire pinned public R data and save lossless CSV conversion/provenance; explicit online operation. |
| `run_experiment.py` | Actual Skill calls, panel audits, exploratory plots, two DiD estimators and published-reference checks. |
| `verify_experiment.py` | Independent explicit-FE/change regressions, input integrity and exact artifact comparison after rerun. |
| `reference_values.json` | Small manually transcribed set of official point estimates, source URLs, scope and decimal rounding tolerances. |
| `requirements-analysis.txt` | Pinned scientific packages; no changes to core dependencies. |
| `REPORT.md` | English experiment report and interpretation. |
| `REPORT_zh.md` | Chinese experiment report and interpretation. |
| `inputs/` | Original public inputs, CSVs, GPL-3 notice and sources. |
| `results/` | Verified processed panels, figures, estimates, environment and checks. |
| `window-review/` | Separate offline follow-up auditing pre-screening coverage and pre-window variability; preserves original replications. |

## Review

The overview is `results/comparison.png`. The simulation's `results/sun-abraham/candidate-review/` highlights the distinction between exposure dates and numerical changes. The short real panel cannot support the default two three-period windows; a two-period sensitivity screen yields many more candidates. Both specifications are retained and none determines a DiD treatment date. No confidence intervals or causal policy conclusions are claimed.

The [window review](window-review/README.md) separately compares data sufficiency and variability advice on both preserved inputs, with a clearly labeled constructed example of widening versus trend review.
