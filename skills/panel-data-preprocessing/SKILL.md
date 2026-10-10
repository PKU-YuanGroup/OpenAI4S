---
name: panel-data-preprocessing
description: Preprocess raw or wide tabular records into entity-time panel data, audit calendar gaps and sample coverage, summarize numeric outcomes, and visually inspect abrupt persistent changes alongside known event dates. Use for panel data preparation, exploratory event discovery, 面板数据预处理 and 数据突变; candidate dates are not causal identification.
origin: openai4s
category: empirical-analysis
capabilities:
  network:
    mode: none
    domains: []
---

# Panel data preparation and visual event discovery

Confirm the entity ID, time column, frequency, measurement columns and their
units. Preserve raw input. IDs remain strings when read from CSV (including
leading zeros); only explicitly selected measurements are converted to numbers.
Combine multiple ID columns into a documented composite ID before calling.
Do not infer a time frequency from the outcome or silently deduplicate,
impute, winsorize, balance a panel, or aggregate exposure indicators.
When cleaning is requested, choose and record a method before panel construction;
cleaning is optional and never runs simply because missing values exist.

The sidecar is stdlib-only except for optional diagnostic plots. Load
`figure-style` when preparing presentation figures; the diagnostic renderer
below is for exploratory visual inspection, not a publication layout.

## Choose cleaning from the data and research design

First identify numeric measurements, nominal/ordered categories and text.
Use `profile_cleaning` to inspect types, missingness, distributions and method
suggestions. For decisions that learn from a baseline/training subset, profile
that subset: future outcomes must not determine the cleaning plan. A profile
describes a candidate method; it neither changes data nor establishes a missingness
mechanism. If coding meaning, a valid fitting period or the intended method is
unclear, ask the researcher before applying it. Otherwise choose from the stated
data types and research design, recording `decision={"basis": "data_profile",
"rationale": "..."}`. A researcher-specified method uses `basis="researcher"`.

- Continuous numeric data: mean is a simple option for an appropriate stable
  distribution; median is a robust option for skew/outliers. Neither recovers
  imputation uncertainty. Choose KNN only when explicit numeric similarity
  features and eligible donor groups have a scientific justification; it is not
  the default for missing data.
- Nominal categories: one-hot features avoid a numeric rank assumption. Label
  codes are identifiers, not distances or natural ordering. Ordered categories
  require an explicit domain order; do not invent one from lexical sorting.
- Text: count or TF-IDF creates a basic lexical feature matrix. This is not a
  semantic embedding. The Unicode word tokenizer does not segment Chinese;
  provide consistently segmented text first, documenting that transformation.

Every fit requires explicit, non-empty **1-based input row positions** in
`fit_rows`. Select eligible training/pre-event rows using the research design;
there is no default full-data fit. Means, medians, category dictionaries, text
vocabularies/IDF and KNN scaling/donors are learned only from these rows. Keep
IDs, time, treatment states and other design keys in `protected_columns`.
The integrated panel workflow automatically protects its entity/time/treatment
columns. Protect any additional design columns explicitly.

For wide data, one input row can contain measurements from both before and
after an event. Row selection alone cannot establish a pre-event fit: restrict
the temporal role of source columns and KNN features, or explicitly reshape
to long data first and then select eligible pre-event rows. Record that choice.

```python
from importlib import import_module

prep = import_module("panel-data-preprocessing.kernel")
toy = [
    {"firm_id": "001", "quarter": "2022Q1", "sales": "10", "size": "100",
     "sector": "retail", "review": "fast delivery"},
    {"firm_id": "002", "quarter": "2022Q1", "sales": "14", "size": "120",
     "sector": "service", "review": "reliable delivery"},
    {"firm_id": "001", "quarter": "2022Q2", "sales": "", "size": "110",
     "sector": "retail", "review": "fast response"},
]
fit_rows = [1, 2]  # explicit baseline input positions for this small example
profile = prep.profile_cleaning(
    [toy[i - 1] for i in fit_rows],
    numeric_columns=["sales", "size"],
    protected_columns=["firm_id", "quarter"],
)
print(profile["columns"])
cleaning = {
    "fit_rows": fit_rows,
    "numeric_columns": ["sales", "size"],
    "imputations": {"sales": "median"},
    "categorical_encodings": {"sector": "onehot"},
    "text_encodings": {"review": "tfidf"},
    "unknown_categories": "indicator",
    "decision": {"basis": "researcher",
                 "rationale": "Use a simple robust baseline fill and nominal/lexical covariates; fit only the two baseline records."},
}
cleaned = prep.clean_data(
    toy, protected_columns=["firm_id", "quarter"], **cleaning,
)
print(cleaned["report"])
# Alternatively run cleaning once inside the panel workflow, using raw toy rows.
cleaned_panel = prep.preprocess_panel(
    toy, entity="firm_id", time="quarter", frequency="quarter",
    numeric_columns=["sales"], cleaning=cleaning,
    window=2, window_options=[2], noise_tolerance={"sales": 0.5},
)
```

`clean_data` and `transform_cleaner` return `rows`, `model` and `report`.
Original input rows are not mutated; original category/text columns remain
alongside generated numeric feature columns. Only the panel's explicitly
listed `numeric_columns` are outcome measurements for summaries and scans;
encoding features are retained as covariates and are not automatically scanned.
Cleaning may include additional numeric covariates, but its numeric list must
cover every original outcome source column (including wide-format sources).
For wide data, use source-column names in `imputations`; the panel builder
propagates their imputation flags to the corresponding long-format metric.

Reuse a fitted model without fitting on later data:

```python
import json

model = prep.fit_cleaner(
    toy, protected_columns=["firm_id", "quarter"], **cleaning,
)
restored_model = json.loads(json.dumps(model))
later = prep.transform_cleaner(toy[2:], restored_model)
```

Imputation strategies are `mean`, `median` and `knn`; unselected numeric columns
retain missing values. For KNN, explicitly select meaningful `knn_features`;
when omitted, the declared numeric columns form the recorded default feature
set, excluding the target being filled from its distance calculation. KNN uses
missing-aware distances with fit-only numeric standardization, and restricts
donors to explicit `group_by` groups when supplied. Group keys must be present
and non-missing; they cannot themselves be imputed or encoded.
`n_neighbors=5` and `weights="uniform"`
are configurable (`"distance"` also supported). Fewer valid donors reduce the
neighbor count; no common feature dimensions triggers a recorded fitted-group
mean fallback. An unseen fitted group or entirely missing fit column remains
unresolved, with the reason recorded; do not cross groups or manufacture zeros.

Category methods are `"onehot"`, `"label"` or
`{"method": "ordinal", "order": ["low", "medium", "high"]}`.
Unknown categories raise by default; `unknown_categories="indicator"` records
them through `column__unknown`, separate from `column__missing`. One-hot feature
names are `column__cat_0`, etc.; label/ordinal use `column__code` and preserve
missing codes as missing. Text methods are `"count"` and `"tfidf"`, with a
fit-only vocabulary bounded by `text_max_features=1000` by default.
`max_generated_features=1000` bounds all generated columns together, including
indicators; both feature limits accept positive integers up to 10,000. Exceeding
the total schema budget fails rather than silently changing methods or columns.
Encoding maps, vocabulary, IDF, unknown-category and out-of-vocabulary counts
are retained in the model/report. An explicit ordinal order may include levels
not observed in fit rows when the researcher defines them in advance.

The `_imputed_columns` marker identifies each filled numeric value. Default
window assessment and event screening exclude windows containing filled
outcomes; `imputed_values_pre` / `imputed_values_post` distinguish those
exclusions from absent periods and still-missing cells. Pre-window noise is
not calculated from filled outcomes. Descriptive statistics may include filled
values and carry an explicit cleaning explanation: these are summaries of the
transformed data, not added observations. Inspect the cleaning report and
compare unfilled data before deciding on any subsequent estimation design.
Use raw rows for a single fit, or reuse the frozen model: the cleaner rejects
input carrying `_imputed_columns` rather than silently treating earlier fills
as fresh donor observations.

The standard-library implementation follows the relevant method concepts,
with its documented preservation and grouping choices; it does not require
scikit-learn or claim identical library defaults. See the primary documentation
on [imputation](https://scikit-learn.org/stable/modules/impute.html),
[category preprocessing](https://scikit-learn.org/stable/modules/preprocessing.html#encoding-categorical-features),
[text features](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction)
and [fitting without data leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

## Raw records to panel and candidate events

```python
from importlib import import_module

prep = import_module("panel-data-preprocessing.kernel")
rows = prep.read_csv("raw.csv")
result = prep.preprocess_panel(
    rows,
    entity="firm_id",
    time="quarter",
    frequency="quarter",
    numeric_columns=["productivity", "sales"],
    treatment="adopted",  # optional separate column: 0/1, bool or missing
    known_events=[
        {"label": "Feature launch", "time": "2023Q2",
         "source": "User-supplied launch log, record 18"},
    ],
    window=3,
    threshold=3.0,
    min_abs_change=0.5,  # outcome units; choose before inspecting candidates
    noise_tolerance={"productivity": 0.25, "sales": 1.0},
)
print(result["analysis"]["audit"])
print(result["window_assessment"]["summary"])
print(result["events"]["candidates"])
files = prep.write_outputs(result, "results/panel-review-01")
```

Frequencies: `year` (YYYY or ISO date/month), `quarter` (YYYYQ1..Q4 or
ISO date/month), `month` (YYYY-MM or ISO date), `day` (YYYY-MM-DD), and
`integer` (whole-number period labels with step one). Ambiguous dates,
timestamps and fuzzy textual periods require an explicit conversion first.
Missing measurements are None, NaN or empty/whitespace values. Document any
other sentinel recoding before calling; non-finite or malformed numbers fail.

For wide data, supply an explicit mapping. Every metric needs the same
period coverage; missing values stay missing rather than creating zeros:

```python
wide = [{"firm_id": "001", "sales_2022": "10", "sales_2023": "25"}]
panel = prep.prepare_panel(
    wide, entity="firm_id", time="year", frequency="year",
    numeric_columns=["sales"],
    wide_columns={"sales": {"2022": "sales_2022", "2023": "sales_2023"}},
)
```

For transaction data, parse dates into the intended frequency and explicitly
choose a reducer for every numeric measurement, e.g.
`aggregations={"sales": "sum", "price": "mean"}`. Allowed reducers:
sum, mean, min, max. All other columns must agree within an entity-period;
conflicts fail. Sum of an entirely missing group remains missing. Use a
domain-specific upstream calculation for weighted means or exposure timing.

## Check window readiness before screening

Report whether each proposed window has enough continuous data before
interpreting candidates. Total rows across entities do not establish enough
time observations within an entity. For a first-post-period anchor, the
numeric screen requires `window` consecutive, non-missing measurements on
each side. The assessment distinguishes absent calendar records from records
with missing measurements or filled outcomes, and reports both pre/post coverage
and exclusions. Filling a cell does not make a screening window fully observed.

Use the standalone audit to review coverage and baseline variation before
running the screen:

```python
panel = prep.prepare_panel(
    rows, entity="firm_id", time="quarter", frequency="quarter",
    numeric_columns=["productivity", "sales"],
)
readiness = prep.assess_windows(
    panel, window=3, window_options=[3, 4, 5, 6],
    noise_tolerance={"productivity": 0.25, "sales": 1.0},
    min_noise_periods=5,
)
print(readiness["summary"])
print(readiness["recommendations"])
```

`preprocess_panel` and `discover_events` accept the same assessment options
and compute the audit before numeric screening. Default inspected windows
are every integer from `window` through
`min(max(2 * window, min_noise_periods), max(window, maximum_rows))`,
inclusive, where `maximum_rows` is the largest observed row count within an
entity. The requested width is always retained, even when it exceeds the
available history. A sweep inspecting more than 64 distinct widths fails
before allocation: supply bounded `window_options`, also limited to 64
distinct widths including the requested one. This bounds the sweep, not the
size of an individual window or the amount of data needed for eligibility.
`noise_tolerance` is a positive scalar or per-metric mapping in
measurement units: the intended budget for the variability of a window mean.
Choose it before inspecting candidates. When it is omitted, a positive
`min_abs_change` supplies a recorded fallback budget; the default zero does
not. A budget is an operational precision target, not statistical power or
an effect-size significance threshold.

The report contains `config`, a per-window `summary`, detailed `assessments`
for every observed anchor/entity/metric/window, and `recommendations` for
the requested window. Each assessment records `eligible`, `reasons`,
`coverage`, `pre_statistics` and `noise_status`. Pre statistics include sample
variance, SD, MAD, linear slope, fitted trend span, residual SD, lag-1
autocorrelation with a default minimum of eight pre observations, conditional
`mean_noise_iid = SD / sqrt(window)` and its ratio to the tolerance.

All numeric noise diagnostics use pre values only. Post values determine
coverage and missingness, not noise or the suggested window. High raw SD can
reflect a smooth trend: with default settings, drift above the budget with
residual SD below half the raw SD prompts trend review. An absolute lag-1 correlation of at least
0.5 prompts dependence review. A shorter baseline leaves autocorrelation
unassessed; a low observed correlation does not establish independence.
The default five-period noise minimum and these review thresholds are
engineering heuristics, not statistical guarantees.
`min_correlation_periods`, `autocorrelation_threshold` and
`trend_residual_ratio` can be configured; their values are recorded.

Recommendations distinguish insufficient coverage, an absent tolerance,
a short noise baseline, trend/dependence review and conditional mean-precision
decisions. A fully covered larger window may be suggested to obtain a longer
baseline (`consider_larger_for_baseline`) or meet the tolerance
(`consider_larger_under_iid`). Suggestions compare the same anchor only and
are limited to the inspected windows. They never change the screen's window.
Review seasonality, changing variance, prior regimes and the event's duration
before accepting a larger window: averaging can also blur a short change.

The `SD / sqrt(window)` calculation assumes independent, stable noise; it is
not a confidence interval or a calibrated guarantee. Existing detection
scores divide the mean change by SD, not this mean-noise estimate, so better
averaging precision does not itself imply higher detection power. See the
[NIST autocorrelation guidance](https://www.itl.nist.gov/div898/handbook/eda/section3/eda331.htm)
and [linear residual SD definition](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/linressd.htm)
for the assumptions and diagnostic distinction.

## Inspect changes visually before naming an event

```python
# Requires matplotlib in the scientific environment. This does not install it.
figures = prep.render_diagnostics(
    result, "results/panel-review-01", entities=["001", "002"],
    metrics=["productivity"],
)
host.view_image("results/panel-review-01/metric-1.png")
host.view_image("results/panel-review-01/sample-coverage.png")
host.view_image("results/panel-review-01/window-readiness.png")
```

The renderer saves PNG and SVG trajectories in separate entity facets, with
known dates and candidate changes marked. Missing measurements and calendar
gaps break lines. Filled outcomes use separate hollow diamonds and break the
observed trajectory; inspect original and filled series together rather than
treating reduced variance as evidence of more real observations.
The coverage plot uses the full panel, even when only a
subset is selected for trajectory review. For more than twelve entities,
select an explicit subset and record why; excluded IDs appear in
`visualization.json`. Do not present the selected entities as the full sample.
The full-panel window-readiness chart compares eligible anchor counts and
noise-assessment states across inspected windows; it does not select a
window or establish that missing observations are ignorable.

Prioritize these visual checks:

- Is the change abrupt and persistent rather than one spike, a smooth trend,
  a recurring seasonal pattern, or a unit/collection-definition change?
- Does it appear within the same entities, and does sample coverage change?
  Period means can change when sample membership changes.
- Does its date align with independent launch/policy/transaction records?
  A supplied event source is recorded, not independently verified.
- Does moving the exploratory window or practical-size threshold materially
  change the candidate? Record those specifications rather than keeping only
  the most convenient one.

## Detection contract and interpretation

The numeric screen compares adjacent pre/post windows within each entity.
Both windows must be consecutive, fully observed and at least two periods
long; a filled outcome is excluded from this observed-data screen. A candidate
needs a window-mean change exceeding `min_abs_change`, a
change/pre-window sample-SD score of at least `threshold` (or zero baseline
variation), an abrupt boundary innovation relative to the median pre-window
increment exceeding `threshold × 1.4826 × increment MAD` and the practical
threshold, and at least two thirds of post-window values supporting the
direction. Nearby overlapping candidates are reduced to the strongest score,
then largest absolute change, with earlier time breaking ties. The innovation
filter also has a small numerical tolerance. Scores are heuristics, not
significance tests; seasonality and nonlinear trends remain review questions.

Exposure transitions have separate kinds: observed adoption/reversal,
interval-censored transition across a gap, and left-censored exposure when
first observed already treated. Neither censoring case supplies an exact
adoption date. Reversals require a design supporting non-absorbing treatment.

Never label an outcome-selected date an exogenous shock. Use discovered
patterns to form hypotheses, seek independent evidence and validate on fresh
data before designing DiD. This Skill performs no causal estimation.

## Deliverables

- `panel.csv`: normalized entity-period records, period indices and 1-based
  input row positions (`_source_rows`); no invented missing cells.
- `summary.json`: full-calendar versus observed-grid balance, within-entity
  gaps, late entry/early exit, missingness, numeric summaries and period counts.
- `candidate_events.csv`: event kinds, dates, source rows and screening evidence.
- `window_assessment.json/.csv`: pre-screen coverage, baseline variation,
  assumptions and advisory recommendations. The JSON preserves the complete
  report; the CSV provides per-anchor/window assessments. The report is also
  available as `result["window_assessment"]`, with per-window counts under
  `result["analysis"]["window_assessment_summary"]`.
- `manifest.json`: source-record/configuration hashes, conversion and
  aggregation decisions, screening and window-assessment settings, tolerance
  sources and output hashes. The source hash identifies parsed records, not
  original file bytes.
- With explicit cleaning: `cleaning_model.json` and `cleaning_report.json`
  preserve the fit-only reusable model, method decisions, filling/encoding
  outcomes and original/processed record hashes. The in-memory result retains
  `result["cleaning"]` with `model` and `report`; the manifest records
  `raw_source_records_sha256` separately from the panel transformation's
  cleaned-input `source_records_sha256`.
- Optional `metric-*.png/.svg`, `sample-coverage.png/.svg` and
  `window-readiness.png/.svg`, plus `visualization.json`: plots,
  selected/excluded IDs and image hashes.

Output helpers refuse to replace existing output files. Review the saved
images, then register the result files with the normal Artifact workflow.
