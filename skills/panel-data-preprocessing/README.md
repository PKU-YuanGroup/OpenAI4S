# Panel Data Preprocessing

[中文说明](README_zh.md)

Convert explicit entity/time mappings or wide measurement columns into long
panel records, check coverage, summarize data and inspect abrupt persistent
changes visually. Supplied event dates and binary exposure transitions are
kept separate from exploratory numeric candidates. No causal effect is estimated.

## Optional cleaning before panel construction

Start with `profile_cleaning` to inspect types, missingness and distributions;
resolve ambiguous measurement or category meaning with the researcher. Choose
an appropriate method from the data profile or an explicit researcher decision,
and record `decision.basis` and `decision.rationale`. KNN is an available option,
not the default. Every fitted cleaner requires explicit 1-based `fit_rows`;
statistics, donors, categories and text vocabularies are learned only from that
selected training/baseline subset.

`clean_data` supports mean/median/KNN numeric imputation, one-hot/label/explicit
ordinal category encoding and count/TF-IDF lexical text features. Its pure-stdlib
implementation preserves original input and category/text columns, protects
ID/time/treatment keys, retains unresolved missing values and marks actual
fills with `_imputed_columns`. Chinese text needs documented upstream segmentation;
count/TF-IDF is not a semantic embedding. `fit_cleaner` and `transform_cleaner`
allow JSON model reuse without refitting on future records.

`preprocess_panel(..., cleaning={...})` runs explicit cleaning before the panel
builder and saves `cleaning_model.json` plus `cleaning_report.json`, including
method decisions and original/processed record hashes. Encoded features remain
covariates unless explicitly selected as outcome measurements. Window checks
and numerical event screening exclude filled outcomes; descriptive summaries
may include them and record that interpretation. See `SKILL.md` for executable
examples, donor groups, fallback rules and fit/transform boundaries.

## Window readiness

Before screening, `assess_windows(panel, window=3, noise_tolerance=...)`
checks each entity/metric for complete, consecutive pre/post windows and
reports baseline variance, trend and noise. A positive tolerance in measurement
units allows conditional advice on larger windows; omitted tolerances use a
positive `min_abs_change` when called through the screening workflow. Short
baselines, trend and serial dependence receive separate review states.
The five-period noise minimum is a review heuristic; averaging precision
assumes independent, stable noise and is not a detection-power calculation.
Default inspected widths are bounded by observed rows per entity while
retaining the requested width. At most 64 distinct widths may be inspected,
including the request; larger default sweeps require explicit bounded
`window_options`.

`preprocess_panel` runs this audit before screening and retains
`window_assessment` plus `analysis.window_assessment_summary`.
`window_assessment.json` and `window_assessment.csv` preserve coverage,
baseline diagnostics and recommendations; optional `window-readiness.png`
and `window-readiness.svg` visualize full-panel readiness. Suggested windows
are advisory and do not change the configured screen. See `SKILL.md` for
the API, per-metric budgets, assumptions and same-anchor comparison rules.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install panel-data-preprocessing --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. npm 0.2.0 does not include this recipe. Use the
GitHub installation command above.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/panel-data-preprocessing
python3 -m zipfile -c panel-data-preprocessing.zip panel-data-preprocessing
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/panel-data-preprocessing/` out
(`unzip main.zip 'OpenAI4S-main/skills/panel-data-preprocessing/*'` unpacks only
that directory). If you already run OpenAI4S there is nothing to install — the
wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Runnable fit-only cleaning, preprocessing, pre-screen window/noise assessment and visual review recipes, method-selection rules, screening formula and interpretation boundaries. |
| [`kernel.py`](kernel.py) | Stdlib profiling/cleaning/encoding with reusable models, CSV ingestion, explicit wide-to-long/aggregation, source-row and imputation mapping, calendar audits, summaries, window readiness, event screening and result exports; optional lazy matplotlib diagnostic plots. |

The offline fixture replay is `python -m harness.evals.panel_data --json`.
It exercises this actual sidecar without a model or a kernel. Optional plots
require matplotlib in the scientific environment; the preparation and replay
do not. This is an in-memory first version, not a streaming ETL engine.
