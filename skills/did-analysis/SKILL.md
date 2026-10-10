---
name: did-analysis
description: Analyze researcher-defined panel interventions with traditional difference-in-differences first, dynamic event studies, fake-time and fake-group placebo checks, and design-specific staggered group-time ATT or triple differences. Audit observed data, comparison groups and assignment clusters before estimation; export assumptions, uncertainty and every prespecified result.
capabilities:
  network:
    mode: none
    domains: []
---

# Difference-in-differences analysis

Use for an empirical intervention study with panel outcomes and an independently
documented treatment assignment. Begin with traditional DiD, then inspect dynamics
and placebo results, and choose extensions from the design. Call the reusable
`kernel.py` sidecar; do not invent estimator APIs or substitute a plot for an estimate.

## 1. Establish the research design

Before estimation, obtain the entity, ordered time, measured outcome, observed
binary exposure or adoption cohort, comparison group, assignment cluster, event
source, and proposed pre/post periods. Ask the researcher when these are missing
or ambiguous. Record substantive, nonempty `research_design` entries:

- `event_source`: independent policy/rollout record, its date and provenance.
- `treatment_assignment`: why these units receive treatment, and the assignment level.
- `parallel_trends`: the proposed identifying argument and plausible threats.

These are researcher declarations, not verification that assumptions hold.
Also discuss anticipation, spillovers, concurrent interventions, selection and
attrition. Do not derive the causal treatment date from an outcome-selected
breakpoint. The `panel-data-preprocessing` Skill supplies exploratory candidates
and window/noise audits; independent evidence must establish an intervention.
Freeze the comparison sample, windows, placebo choices and aggregation before
seeing their effect estimates. A variance-driven window suggestion is advisory;
document any design change and retain the original requested result.

Use string IDs, one row per entity/period, integer periods (or the upstream
`_period_index` column), finite outcomes and an explicit cluster column. Preserve
original outcomes and `_imputed_columns` from preprocessing. Filled outcome cells
cannot become observed causal evidence. Default `missing_policy="error"` refuses
incomplete requested samples; `"drop_entity"` is an explicit complete-case choice
with losses recorded. Never silently fill, collapse duplicates, balance a panel
or infer treatment from encoded category numbers. Covariate adjustment is outside
this version; encoded features do not automatically enter its regression.

For calendar data, pass the preprocessing output **in memory**, retaining
list-valued imputation/source-row metadata. CSV export serializes these lists;
`did.read_csv("panel.csv")` strictly decodes `_source_rows` and `_imputed_columns`
from JSON while retaining ordinary IDs and periods as strings. When using another
CSV reader, decode those reserved lists explicitly instead of dropping markers.
Carry the preprocessing transformation/configuration in the research record:

```python
from importlib import import_module
host.load_skill("panel-data-preprocessing")
prep = import_module("panel-data-preprocessing.kernel")
panel = prep.prepare_panel(
    raw_rows, entity="firm", time="month", frequency="month",
    numeric_columns=["sales"],
)
calendar = {r["month"]: r["_period_index"] for r in panel["rows"]}
# After obtaining the researcher's design, add its upstream provenance.
design["preprocessing_metadata"] = {
    "transformation": panel["transformation"], "config": panel["config"],
    "calendar": calendar,
}
# Use time="_period_index", treatment_time=calendar["2025-07"],
# and calendar-mapped pre/post periods in the estimator below.
```

When cleaning, also protect cluster, cohort and subgroup columns explicitly.
The causal estimator's source hash identifies its received records; retained
upstream hashes identify raw-to-panel transformations, not the original file
bytes unless independently hashed. `research_design` accepts these additional
JSON-compatible metadata fields without treating them as identifying proof.

## 2. Traditional DiD first

For a shared adoption date, the `treatment` column is **observed exposure**:
zero before adoption and one thereafter for treated units; controls stay zero.
It is not a time-invariant treated-group label. All supplied histories are checked
for alternate adoption dates, reversals and inconsistent cluster assignments.
Keep the full exposure history when estimating a pre-treatment placebo.

Load the Skill in OpenAI4S, then import its permitted package:

```python
host.load_skill("did-analysis")
from importlib import import_module
did = import_module("did-analysis.kernel")

# rows is the original/observed long panel, not an outcome-filled copy.
design = {
    "event_source": "Documented rollout begins in integer period 7; source recorded in the study log.",
    "treatment_assignment": "The institution assigned rollout at the region level; controls never adopted.",
    "parallel_trends": "The proposed comparison has similar prior outcome dynamics; examine competing region-specific changes.",
}
common = dict(
    entity="firm", time="period", outcome="sales", treatment="exposed",
    treatment_time=7, cluster="region", research_design=design,
    missing_policy="error",
)
baseline = did.traditional_did(
    rows, **common, pre_periods=[5, 6], post_periods=[7, 8],
)
print(baseline)
```

The estimand is the equally weighted difference between treated and control
entities' changes in their specified pre/post period means. This is the
traditional two-group/two-period contrast after collapsing periods, with
cluster-robust CR1 covariance and Student-t reference using cluster degrees of
freedom. It is not an arbitrary row-weighted multi-period TWFE regression.
Report the estimate, interval, cluster counts, sample loss and inference status.
Too few independent treated/control clusters retain a point estimate but cannot
produce reliable sidecar inference. Many observations in one policy region
do not create many independent assignments. See [method notes](method-notes.md)
for formulas, support rules and interpretation limits.

## 3. Dynamic event study

```python
dynamics = did.event_study(
    rows, **common, periods=[3, 4, 5, 6, 7, 8, 9], reference_period=6,
)
```

Each coefficient is the treated-control outcome difference in that period minus
the difference in the explicit pre-treatment reference period. Use one complete
entity sample across requested periods and inspect the reported joint covariance.
Plot coefficients and pointwise intervals with the reference and adoption marked.
Inspect prior effects and any available joint pre-period diagnostic. Failure to
reject pre-trends does not establish parallel trends; selection after a pre-test
can also affect inference. Periodwise intervals are not simultaneous bands.
Report anticipation or differential prior trends as threats, not an invitation
to remove inconvenient periods.

## 4. Placebo checks (伪 DiD / 安慰剂)

```python
fake_date = did.placebo_time(
    rows, **common, fake_treatment_time=4,
    pre_periods=[2, 3], post_periods=[4, 5],
)
fake_group = did.placebo_group(
    rows, **common, pre_periods=[5, 6], post_periods=[7, 8],
    fake_treated_entities=prespecified_never_treated_entities,
)
distribution = did.placebo_distribution(
    rows, **common, pre_periods=[5, 6], post_periods=[7, 8],
    seed=20261010, draws=100, fake_cluster_count=4,
)
```

For fake dates, **both windows must be strictly before the real treatment date**.
Retain the real eventually-treated group; a placebo cannot contain genuine
post-treatment observations. For fake groups, use genuine never-treated controls
only and move whole assignment clusters. Require comparison support after the
split. Keep all requested dates, assignments, failures and results, rather than
selecting a nonsignificant subset. A nonzero placebo can signal incompatible
prior trends, confounding or an unsuitable comparison; it does not diagnose the
cause by itself. A null placebo does not validate identification.

The random fake-group distribution records every sampled assignment and seed.
Its descriptive tail fraction is not a calibrated randomization p-value for the
observational treatment: the true assignment mechanism and exchangeability have
not been established. Do not present it as such. Any formal randomization test
requires a separately justified assignment mechanism and null hypothesis.

## 5. Choose a derivative from the assignment structure

### Staggered adoption

First explain the traditional contrast and why a pooled shared-date estimator
does not fit this design. A prespecified single-cohort versus never-treated
traditional contrast may be a baseline; do not report a naive pooled TWFE effect
as the primary causal result. Then estimate supported group-time contrasts:

```python
staggered = did.staggered_did(
    rows, entity="firm", time="period", outcome="sales", cohort="adoption_period",
    periods=[1, 2, 3, 4, 5, 6, 7, 8, 9], cluster="region",
    research_design=design, control_group="never_treated",
    missing_policy="error",
)
```

`cohort` is a time-invariant integer adoption period; **0 means never treated**.
The unadjusted ATT(g,t) uses g-1 as baseline and either never-treated or
`"not_yet_treated"` controls unexposed at the relevant periods. State absorbing
treatment, no anticipation and unconditional parallel trends. Inspect cohort
support, changing comparison membership and dynamic cohort weights. Aggregates
use joint cluster influence covariance, including shared controls; do not average
cell standard errors or pretend cells are independent.

Request all periods needed to balance the sample, including each active g-1
baseline. This version returns post-treatment cells only. If observed exposure
is available, pass `treatment="exposed"` to verify it against the declared cohort;
without that argument, absorbing exposure is a recorded cohort assumption.
Overall aggregation weights every supported cell by cohort size, so a cohort
with more observed post-treatment periods receives more overall weight.

This bounded unconditional estimator follows the group-time identification idea
in [Callaway and Sant'Anna](https://arxiv.org/abs/1803.09015v4).
It does not implement their full covariate-adjusted doubly robust estimator,
multiplier bootstrap or simultaneous bands, or the Sun-Abraham estimator.
For those designs, load a suitable scientific environment/package and document
the actual estimator and inference. Naive TWFE lead/lag coefficients can mix
effects under staggered heterogeneous adoption;
[Sun and Abraham](https://arxiv.org/abs/1804.05785v2) explain this problem.

### Triple differences (DDD)

```python
ddd = did.triple_difference(
    rows, **common, subgroup="eligible",
    pre_periods=[5, 6], post_periods=[7, 8],
)
```

`subgroup` is a fixed binary attribute, observed in all four group-by-subgroup
cells. DDD contrasts the two subgroup-specific DiDs, with joint cluster covariance.
Treatment-region status must have the same shared adoption meaning in both
subgroups; define the outcome eligibility mechanism separately. This version is
limited to unconditional common-time 2×2×2 identification. It does not automatically
solve covariate-dependent or staggered DDD; see
[Ortiz-Villavicencio and Sant'Anna](https://arxiv.org/abs/2505.09942).

## 6. Preserve a reviewable analysis

Use `analyze_did(rows, plan=...)` for a prespecified ordered analysis bundle:
traditional first, optional dynamics/placebos, then explicit derivative entries.
Each optional stage is a list of complete keyword configurations; `derived`
entries declare a `method` and its `config`. For example:

```python
plan = {
    "traditional": {**common, "pre_periods": [5, 6], "post_periods": [7, 8]},
    "event_study": [{**common, "periods": [3, 4, 5, 6, 7, 8, 9], "reference_period": 6}],
    "placebo_time": [{**common, "fake_treatment_time": 4,
                      "pre_periods": [2, 3], "post_periods": [4, 5]}],
    "placebo_group": [{**common, "pre_periods": [5, 6], "post_periods": [7, 8],
                       "fake_treated_entities": prespecified_never_treated_entities}],
    "derived": [{"method": "triple_difference", "config": {
        **common, "subgroup": "eligible", "pre_periods": [5, 6], "post_periods": [7, 8],
    }}],
}
bundle = did.analyze_did(rows, plan=plan)
files = did.write_outputs(bundle, "did-results")
figures = did.render_diagnostics(bundle, "did-figures")
```

The baseline must be valid. Later invalid diagnostics are retained with
`status="refused"`, the reason and configuration, and subsequent stages still
execute. Review those refusals; a returned bundle is not evidence that every
requested design was estimable. A staggered pipeline may explicitly set the
traditional stage's `descriptive_staggered_benchmark=True`; its result is labeled
descriptive. Use the staggered derivative for that design's identified estimand.

Use fresh output directories. `write_outputs(result, directory)` exports
`results.json`, `estimates.csv` and `manifest.json`.
`render_diagnostics(result, directory)` creates optional matplotlib figures. The sidecar
refuses to overwrite existing outputs and records input/configuration provenance.
Keep the independent event source, original exposure histories, imputation audit,
requested periods, sample losses, all placebo results and unsupported designs.
Figures supplement numerical results; no plot establishes a causal effect.

The estimator needs only Python's standard library. Figures require matplotlib
in the scientific environment, imported only when called. Large unbalanced
designs, treatment reversals, conditional/DR estimation, matching/weighting,
synthetic DiD, interference models, wild-cluster bootstrap and automatic model
selection are outside this implementation. Explain the relevant boundary and
choose a justified estimator instead of silently substituting this one.
