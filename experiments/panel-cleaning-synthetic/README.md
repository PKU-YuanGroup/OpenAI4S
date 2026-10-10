# Random panel Skill experiment

[中文说明](README_zh.md)

A fixed-seed, offline experiment executes the actual
`skills/panel-data-preprocessing/kernel.py` sidecar. It generates random panels,
retains their complete generation truth separately, and tests explicit numeric
cleaning, category/text encoding, panel audits, window readiness and exploratory
event discovery. Read [the results and interpretation](REPORT.md).

## Reproduce

Use Python with matplotlib in a scientific environment. Generation, cleaning
and verification use the standard library; plots require matplotlib. From the
repository root, write a fresh bundle rather than overwrite the archived run:

```bash
python3 experiments/panel-cleaning-synthetic/run_experiment.py --output /tmp/openai4s-panel-random-new
python3 experiments/panel-cleaning-synthetic/verify_experiment.py --experiment /tmp/openai4s-panel-random-new
```

The default seed is `20261007`. Existing generated bundles are refused. The
archived input and output are in this directory; numerical and image byte
comparisons depend on the recorded Python/matplotlib environment. The verifier
recalculates results independently and runs another fresh bundle.

## Design

Thirty-two string entity IDs have 48 underlying periods and eight prespecified
scenarios: quiet, upward shift, downward shift, noisy shift, linear trend,
single spike, seasonality and late entry. Calendar omissions and measurement
missingness are generated independently in this artificial design. Actual raw
row counts are recorded after omissions; the complete truth is not training data.

The cleaner fits only raw rows at periods 1–16. Baseline data profiles and
measurement roles justify mean, median and KNN choices; the comparison with
truth is performed after those decisions. Numeric donors stay within entity.
Category/text schemas use the selected baseline rows across entities. Later
unknown categories and words test transformation with a frozen schema.

The main run preserves missing outcome values. A second run fills the outcome
explicitly to inspect imputation markers and the exclusion of filled outcomes
from event windows. The screen uses window 3, threshold 3, minimum absolute
change 2 and a noise budget of 0.5 outcome units; widths 3/5/7/9 are audited.
Suggestions do not change the configured screen. Known generation event dates
are recorded separately from numeric candidates.

## Files

| File or directory | Purpose |
| --- | --- |
| `run_experiment.py` | Fixed-seed generator, actual Skill calls, truth-based evaluation and diagnostic plots. |
| `verify_experiment.py` | Independent checks of fitting, transformation, windows, matching, hashes and reproduction. |
| `REPORT.md` | English results, audit findings and interpretation. |
| `REPORT_zh.md` | Chinese results, audit findings and interpretation. |
| `inputs/` | Raw records, complete truth and generation settings. |
| `results/` | Baseline profile, decisions, evaluations, environment, overview and both Skill output bundles. |

This is a functional test on one synthetic realization. It does not establish
general detector performance or a causal effect.
