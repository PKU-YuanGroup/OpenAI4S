# IS research Skill development record

[中文主入口](skill-development_zh.md)

The implemented contributions are [panel-data-preprocessing](../skills/panel-data-preprocessing/SKILL.md)
and [did-analysis](../skills/did-analysis/SKILL.md).
It turns an explicitly documented cleaning decision and raw records into a
traceable panel, then assesses event-window readiness and supports visual
exploration of persistent changes. Its scientific execution is the reusable
standard-library `kernel.py` sidecar, with matplotlib imported only for figures.
The separate DiD Skill receives an explicit intervention design after preparation,
estimates effects and records diagnostic/identification boundaries. Earlier bounded
public-data replication archives retain their original scope and hashes.

## Implemented capability

| Stage | Implemented behavior | Research boundary |
| --- | --- | --- |
| Data profile | Types, missing counts, cardinality, numeric distribution summaries and method suggestions/questions. | Suggestions require a type/design decision; a profile never transforms data or proves missingness is ignorable. |
| Numeric cleaning | Explicit mean, median or KNN fills from selected fit rows; optional group-specific donors/statistics. | No automatic KNN, no use of later rows as fit donors, no invented value for all-missing fitted targets. |
| Category/text encoding | One-hot, label or explicit ordinal codes; count/TF-IDF lexical features with a fit-only vocabulary. | Original columns remain; label codes have no natural ordering, and lexical features are not semantic embeddings. |
| Panel construction | CSV string IDs, explicit long/wide conversion, period normalization and explicit duplicate aggregation. | No implicit frequency, deduplication, missing-period creation, balancing or treatment aggregation. |
| Descriptive audit | Missingness, observed-grid/calendar balance, gaps, late entry/early exit, numeric summaries and changing sample coverage. | Filled values may enter summaries and are identified; pooled summaries are not causal effects. |
| Window review | Complete observed pre/post windows, baseline variance/trend/correlation review and conditional widening advice. | Filled outcomes are excluded; mean-noise advice assumes stable independent noise and is not power or significance. |
| Event exploration | Separate supplied dates, exposure transitions and within-entity persistent numeric change candidates. | Outcome-selected candidates do not identify exogenous treatment dates. |
| Visual/export evidence | Faceted trajectories, distinct filled-value markers, coverage/readiness plots, source rows, reusable models and record/configuration/output hashes. | Inspect raw and transformed values; a lower filled-data variance does not add observed evidence. |

## Method selection before transformation

Choose variable roles and the eligible fitting sample before calling a cleaner.
Use `profile_cleaning` on the intended calibration sample when its output
informs methods. A profile of future outcomes must not choose the training
method. Types that could be numeric IDs, ordinal categories or free text, and
an unclear fitting period, require a question to the researcher. Clear types
and a justified design allow a documented `data_profile` decision; an explicit
researcher instruction uses `researcher`. Every decision requires a substantive
`rationale` instead of an unexplained default.

| Data/role | Candidate method | What determines the choice |
| --- | --- | --- |
| Continuous measurement with a suitable stable distribution | Mean fill | Research design, fitted distribution and a defensible missingness assumption. |
| Skewed measurement or notable extremes | Median fill | A robust baseline option; keep extremes and inspect their meaning rather than automatically deleting them. |
| Numeric covariates with meaningful local similarity | KNN fill | Declare valid numeric distance features, units/scaling and eligible donor groups; inspect fitted-group mean fallback and unresolved cells. |
| Nominal categories | One-hot; label only when appropriate for its downstream use | One-hot avoids rank assumptions; a numeric label identifies a category and should not automatically become a continuous regressor. |
| Ordered categories | Explicit ordinal order | Researcher/domain-defined levels, including any levels defined before fitting; lexical sort is not an order definition. |
| Natural-language text | Count or TF-IDF | A lexical analysis goal, consistent tokenization and a fit-only vocabulary; Chinese needs upstream segmentation. |

The profile's distribution suggestions are heuristics, not a statistical test
or a validated automatic method chooser. Filling is optional: unselected
numeric columns keep missing values. The functions do not silently winsorize,
drop rows, correct units, segment Chinese or infer a causal missingness model.

## Fit-only cleaning API

Load the Skill sidecar through the OpenAI4S Skill bootstrap as in its recipe:

```python
from importlib import import_module
import json

prep = import_module("panel-data-preprocessing.kernel")
rows = [
    {"firm": "001", "period": "1", "adopted": 0,
     "sales": "10", "size": "100", "sector": "retail", "review": "fast delivery"},
    {"firm": "001", "period": "2", "adopted": 0,
     "sales": "12", "size": "120", "sector": "retail", "review": "reliable delivery"},
    {"firm": "001", "period": "3", "adopted": 1,
     "sales": "", "size": "110", "sector": "retail", "review": "fast response"},
]
fit_rows = [1, 2]  # original, 1-based baseline input positions
profile = prep.profile_cleaning(
    [rows[i - 1] for i in fit_rows], numeric_columns=["sales", "size"],
    protected_columns=["firm", "period", "adopted"],
)
cleaning = {
    "fit_rows": fit_rows,
    "numeric_columns": ["sales", "size"],
    "protected_columns": ["firm", "period", "adopted"],
    "imputations": {"sales": "median"},
    "categorical_encodings": {"sector": "onehot"},
    "text_encodings": {"review": "tfidf"},
    "unknown_categories": "indicator",
    "decision": {"basis": "researcher",
                 "rationale": "Fit a simple robust fill and nominal/lexical covariates only on the two pre-adoption records."},
}
cleaned = prep.clean_data(rows, **cleaning)
model = json.loads(json.dumps(cleaned["model"]))
later = prep.transform_cleaner(rows[2:], model)  # no refitting
result = prep.preprocess_panel(
    rows, entity="firm", time="period", frequency="integer",
    numeric_columns=["sales"], treatment="adopted", cleaning=cleaning,
    window=2, window_options=[2], noise_tolerance={"sales": 0.5},
)
print(result["cleaning"]["report"])
print(result["window_assessment"]["summary"])
```

`profile_cleaning` returns `columns` profiles and unresolved `questions`.
`fit_cleaner(rows, *, fit_rows, numeric_columns, ..., decision)` returns a
hash-checked JSON-serializable model. `clean_data` combines fitting and
transformation; `transform_cleaner(rows, model)` returns `rows`, `model` and
`report` without changing donors, statistics, categories or vocabulary. These
standalone and integrated calls illustrate alternatives; fit from raw data,
not from a previous cleaned output carrying `_imputed_columns`.

Fitting rows must be unique, valid, non-empty 1-based original input positions.
There is no default that learns from all rows. Means, medians, numeric scales,
donors, learned category levels and vocabulary/IDF use only these positions.
An ordinal order supplied in advance is a declared schema, distinct from
learned category levels. Keep source data and select training/pre-event rows
using independent research-design information. For a wide table, each selected
row may still contain post-event columns: constrain the temporal role of source
columns and KNN features, or explicitly reshape to long format first and select
pre-event observations there.

For KNN, `knn_features` selects numeric similarity dimensions; the recorded
default is the declared numeric list, excluding each target from its distance.
Fit-only group-specific scaling is used, and distances adjust for the shared
observed dimensions. `group_by` restricts fitting and donors to explicit groups;
group keys must remain observed and cannot be imputed or encoded. Neighbor
counts truncate when fewer valid donors exist. `n_neighbors=5` and
`weights="uniform"` are defaults; distance weighting is available. With no
shared dimensions, a fitted-group mean fallback is recorded. An unseen group
or all-missing fitted target stays unresolved rather than borrowing across
groups. Donor row positions and distance evidence are retained in `report.filled`.

Category encoding preserves original values and missing indicators. One-hot
uses `column__cat_0`, etc.; label/ordinal uses `column__code`. Unknown categories
raise by default, or can be separated by `unknown_categories="indicator"` via
`column__unknown`. Text uses lowercased Unicode word tokens, fit-only lexical
columns `column__text_0`, etc., and missing indicators. TF-IDF uses smoothed IDF
and L2 normalization; out-of-vocabulary counts are recorded without expanding
the fitted vocabulary. `text_max_features=1000` limits each text vocabulary;
`max_generated_features=1000` limits the entire generated schema, including
indicators. Both can be set up to 10,000; exceeding the total schema budget fails.

`group_by` applies to numeric statistics and donors. Category and text schemas
are shared across the selected fit rows so feature meanings stay consistent
between entities. Count models retain document-frequency/IDF metadata, but
apply unweighted word counts; TF-IDF applies the learned weights.

## Cleaning, panel windows and visual evidence

Cleaning runs before `prepare_panel` when explicitly supplied to
`preprocess_panel(..., cleaning={...})`. The integrated path automatically
protects entity, time and treatment columns. The cleaner can include additional
numeric covariates, but must include every original outcome source column;
wide-format filling names those source columns. Generated covariates remain
in the panel and do not automatically join its outcome list or change screen.

Filled numeric cells carry `_imputed_columns`. Wide-to-long construction maps
source flags to the corresponding metric; duplicate aggregation unions them.
Window assessment records `coverage.pre_imputed_values` and
`coverage.post_imputed_values`, with `imputed_values_pre/post` reasons. An
otherwise covered window containing a filled outcome is ineligible for the
observed-data screen. A filled pre value also prevents numeric noise assessment.
Descriptive statistics may include the transformed values and explicitly state
this scope. Keep that distinction when comparing raw and processed summaries.

The renderer plots observed trajectories with breaks at missing, absent or
filled outcomes; filled cells appear as separate hollow diamonds. Review
original and filled series together. Reduced variance after filling is a
property of a transformation, not proof of enough independent observations.
The full-panel coverage and readiness plots still show the whole sample when
an explicit subset is used for the entity trajectories.

Window advice compares the same anchor using only pre values for variation;
post values determine completeness. A five-period baseline minimum, trend
and residual correlation checks are review heuristics. `SD/sqrt(n)` depends
on stable independent noise, does not establish statistical power and does
not replace the existing change/SD detection score. Larger windows can reduce
precision problems, remove eligible anchors or mix distinct regimes; advice
does not change the configured screening window.

## Exported audit trail

| Artifact or field | Meaning |
| --- | --- |
| `panel.csv` | Normalized records with `_period_index`, source-row positions and any imputation markers; no invented missing-period rows. |
| `summary.json` | Calendar/sample coverage, numeric summaries and explicit cleaning interpretation where configured. |
| `candidate_events.csv` | Exploratory numeric candidates, supplied dates and distinct exposure/censoring kinds. |
| `window_assessment.json` / `.csv` | Complete-window exclusions, pre diagnostics, assumptions and advisory recommendations. |
| `cleaning_model.json` | Optional fit-only model, selected fit rows, decision, fitted parameters/mappings and model hash. |
| `cleaning_report.json` | Optional fills/donors/fallbacks, unresolved cells, missingness, unknown/OOV counts and original/processed record hashes. |
| `manifest.json` | Panel, screen and cleaning settings, source/configuration/model hashes and output checksums. |
| `raw_source_records_sha256` | Manifest identity for original records before cleaning. |
| `transformation.source_records_sha256` | Identity for the records supplied to the panel builder, including explicit cleaning where configured. |
| Optional PNG/SVG and `visualization.json` | Diagnostic plots, selected/excluded entity IDs and image checksums. |

Record hashes identify parsed records, not the bytes of an original CSV.
Preserve original files separately when byte-level provenance is needed.
Output helpers refuse to overwrite an existing artifact bundle. The in-memory
result exposes `cleaning.model/report`, `window_assessment`, and its window
summary under `analysis.window_assessment_summary`.

## Existing public-data experiments

The independent [DiD replication folder](../experiments/did-arxiv-replication/README.md)
preserves public inputs, exact source versions, execution code, figures,
reference checks and a [scope report](../experiments/did-arxiv-replication/REPORT.md).
These are bounded official worked-example point-estimate replications:

| Paper / official example | Preserved input | Verified scope |
| --- | --- | --- |
| [Callaway–Sant'Anna](https://arxiv.org/abs/1803.09015v4) / [did example](https://bcallaway11.github.io/did/articles/did-basics.html) | mpdta: 500 counties × five years, 2,500 rows. | Unconditional method example; overall dynamic ATT −0.077240 matches the rounded official −0.0772. |
| [Sun–Abraham](https://arxiv.org/abs/1804.05785v2) / [fixest example](https://lrberge.github.io/fixest/articles/fixest_walkthrough.html) | base_stagg: 95 individuals × ten periods, 950 rows. | Interaction-weighted method example; ATT −1.133749 matches the official value on the fixed simulation draw. |

The two experiments retain 24 successful reference checks and independent
point-estimate validation; full-paper applications and confidence intervals
are outside their recorded scope. The later [window review](../experiments/did-arxiv-replication/window-review/README.md)
separately audits coverage/variation, preserving its own configuration and
environment record. Its real five-year panel has zero complete three-period
pre/post windows; two periods fit but do not supply a reliable five-period
noise baseline. Its clearly labeled constructed example demonstrates distinct
conditional widening, retention and trend-review branches.

The newly added cleaner does not alter these archived experiment inputs or
results. Cleaning choices for a new study need their own design, fit-sample
definition, audit and validation; a successful old replication does not validate
a new missing-data assumption.

## Random-data functional experiment

The separate [random-panel experiment](../experiments/panel-cleaning-synthetic/README.md)
uses seed `20261007`, 32 string IDs and 48 underlying periods. Eight prespecified
scenarios distinguish persistent shifts, high noise, trend, spikes, seasonality,
quiet series and late entry. Raw calendar/measurement omissions are retained;
the complete generated truth is used only for subsequent evaluation.

It executes the actual Skill with baseline-only method profiles and fitting
(periods 1–16), grouped mean/median/KNN fills, nominal/ordinal/label encoding and
TF-IDF text features. A second explicitly configured outcome-fill run checks
that imputation markers prevent filled values from making event windows eligible.
Fixed windows and thresholds are not changed after inspecting truth or advice.
The [report](../experiments/panel-cleaning-synthetic/REPORT.md) preserves all
numeric candidates, exact and nearby event recovery, false positives, imputation
errors, and unconditional versus eligible-event denominators. This one realization
tests implementation and exposes exploratory limitations; it is not a Monte
Carlo accuracy estimate or DiD identification test.

The archived run contains 1,462 raw rows and 603 numeric masks. Its main cleaner
fills 448 covariate cells and creates 28 encoding columns. Of 12 true persistent
changes, five have complete requested windows and all five are detected; six
additional candidates come from seasonality/noise. Filling the 102 missing
outcomes in a separate comparison leaves window eligibility and candidates
unchanged. Independent checks pass and all 49 generated files reproduce byte
for byte in the recorded environment.

## Preparation boundary

This Skill prepares data and exploratory evidence. It does not infer a valid
missingness mechanism, propagate single-imputation uncertainty, automatically
choose a causal event date or estimate causal effects. Discovered outcome
changes are hypotheses requiring independent event information. Known dates
record a source, with their causal status still unverified.

The current implementation is in memory. KNN comparisons and dense lexical
features have practical scale limits; it is not a streaming ETL or semantic
embedding service. Core dependencies remain standard-library only, with
scientific plotting optional in the analysis environment.

## DiD Skill: traditional analysis, placebos and derivatives

`did-analysis` is a separate callable Skill. The researcher must define an
independent event source, treatment assignment, identifying parallel-trends
argument, entity/time/outcome roles, assignment cluster and requested periods.
These declarations are retained as assumptions; the code cannot verify them.
Default incomplete-sample handling refuses estimation. Explicit complete-case
analysis reports exclusions and does not turn imputed outcomes into observations.

| Analysis stage | Implemented behavior | Boundary |
| --- | --- | --- |
| Traditional DiD | Difference of treated/control entity pre/post mean changes; explicit common adoption and cluster CR1 Student-t inference. | Equal entity weighting and specified windows; no automatic covariate adjustment or general row-level TWFE model. |
| Event study | Period effects relative to one explicit untreated baseline on a shared complete sample, with joint cluster covariance. | Pointwise intervals are not simultaneous bands; nonrejection of prior effects does not prove parallel trends. |
| Fake treatment time | Apply a prespecified earlier date to the real eventually-treated group. | Both windows remain strictly before the real date; preserve every requested result. |
| Fake treatment group | Split genuine never-treated units into whole assignment clusters; optional seeded repeated draws. | Random placebo tail fractions are descriptive, not calibrated randomization p-values for the actual policy. |
| Staggered DiD | Unconditional group-time ATT with g-1 baseline, never/not-yet controls, cohort-size dynamic weights and joint-covariance aggregation. | Absorbing treatment, no anticipation and unconditional identification; not the full conditional/DR C&S estimator or its multiplier bootstrap. |
| Triple differences | Saturated entity-change regression on group, fixed binary subgroup and their interaction, with joint cluster covariance. | Unconditional common-time 2×2×2 design; not general staggered or covariate-dependent DDD. |
| Evidence export | Ordered results, researcher declarations, requested design, sample loss, provenance/checksums and optional plots. | A reliable implementation does not establish a study's causal identification. |

The ordered recipe begins with traditional DiD, then event study and fake-time/
fake-group placebo checks, and selects derivatives from the assignment design.
For staggered adoption, explain why one shared date is unsuitable; a prespecified
single-cohort versus never-treated traditional contrast can be a benchmark.
Do not silently pool staggered cohorts into that estimator. Category/text
encoding from preprocessing remains data preparation; it is not automatically
an adjustment set, and post-treatment covariates can change the causal target.

The [Skill recipe](../skills/did-analysis/SKILL.md) provides executable APIs;
[method notes](../skills/did-analysis/method-notes.md) document formulas and
primary sources. The independent [validation experiment](../experiments/did-skill-validation/README.md)
uses prespecified synthetic truth, clustered errors, null effects, violated prior
trends, DDD and heterogeneous staggered effects. It independently compares
estimates/covariances with scientific reference calculations and records repeated
simulation performance with Monte Carlo uncertainty. It does not overwrite the
earlier public-data archives or claim a full-paper empirical replication.

Offline contracts are in [DiD tests](../tests/test_did_analysis_skill.py):

```bash
uv run pytest tests/test_did_analysis_skill.py
uv run python -m harness.evals.did_analysis --json
```

The 2026-10-10 DiD update passes 90 dedicated offline tests and 12 declared
actual-sidecar replay cases. A separate retailer-month forward trial verifies
the preparation → CSV → DiD handoff, leading-zero IDs, complete-case loss,
preserved refusals and updated figures with 142 independent checks. The archived
experiment passes 12,237 independent statsmodels/SciPy/integrity checks with a
maximum numerical discrepancy of 4.31e-12; all 45 deterministic artifacts and
their manifest reproduce byte for byte in the recorded environment.

The final whole-repository offline regression passes 11,543 tests with 97 skips
and no failures. Formatting, strict core typing, directory documentation,
the 38 PR harness scenarios and both built distribution formats also pass.

Its 512 prespecified simulation panels include 128 replicates per scenario.
With 24 clusters, the null design's 95% intervals cover 115/128 effects (89.84%)
and reject zero 13/128 times (10.16%). The deliberately biased prior-trend design
is detected in only 62/128 pretrend tests. The report retains those limitations
and Monte Carlo intervals; arithmetic agreement is not nominal inference
calibration, and nonrejection does not validate parallel trends.

## Validation and primary references

The 2026-10-07 cleaning update adds profile- or researcher-based method choices
before panel construction. It passes 61 cleaning tests and 22 panel replays;
the full offline suite reports 11,413 passed and 97 skipped. An independent
firm-month trial selected grouped mean fills from pre-launch rows, preserved a
missing outcome, encoded nominal categories and lexical text, and verified
the exported hashes and diagnostic images. These checks establish
implementation behavior, not a particular missingness assumption.

The observable contracts are exercised by [cleaning tests](../tests/test_panel_cleaning.py),
[panel preprocessing tests](../tests/test_panel_data_preprocessing_skill.py)
and [window tests](../tests/test_panel_window_assessment.py).
The [offline panel replay](../harness/evals/panel_data.py) runs the actual
sidecar against declared synthetic cases without a model or network:

```bash
uv run pytest tests/test_panel_cleaning.py tests/test_panel_data_preprocessing_skill.py tests/test_panel_window_assessment.py
uv run python -m harness.evals.panel_data --json
uv run python scripts/check_directory_readmes.py
```

Primary method documentation: [numeric imputation](https://scikit-learn.org/stable/modules/impute.html),
[categorical preprocessing](https://scikit-learn.org/stable/modules/preprocessing.html#encoding-categorical-features),
[lexical text features](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction)
and [avoiding fitting leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
These document method concepts; the Skill's standard-library implementation
has its own explicitly recorded grouping, preservation and fallback behavior.
