---
name: is-survey-measurement
description: Evaluate Information Systems survey constructs with explicit item coding, reflective measurement models, ordinal or continuous CFA/SEM, model-based reliability, and measurement invariance. Record sample losses and validity limits before comparing groups or interpreting structural paths.
origin: openai4s
category: research-methodology
license: MIT
capabilities:
  network:
    mode: none
    domains: []
---

# IS survey measurement

Use for Information Systems (IS / 信息系统) survey research involving constructs
such as technology acceptance, perceived usefulness, trust, or organizational
capability. Adapted from AlterLab's MIT-licensed SEM & Psychometrics Skill;
[UPSTREAM.json](UPSTREAM.json) records the exact source and adaptation.
The local analysis examples make no outbound network calls.

This is a measurement-analysis recipe, not a validated questionnaire or an
automatic declaration that a scale is valid. It needs the researcher's item
wording, response anchors, provenance and intended construct definitions.
Missing instrument information blocks interpretation even when a model fits.

## Choose the measurement question

1. Record the construct, population, unit of analysis, respondent, language,
   collection wave, instrument source/version, item wording and response anchors.
   Record consent and the permitted uses of the supplied participant data.
2. Distinguish reflective indicators from formative components. The CFA example
   below assumes reflective indicators. Do not drop formative components to raise
   internal consistency or fit a reflective model merely because the software can.
3. Map each item to one proposed construct. Specify its allowed codes, missing
   codes, reverse direction and scoring rule from the instrument, not from
   correlations. Do not reverse an item just to improve alpha.
4. Separate exploratory factor analysis from confirmation. A structure selected
   after inspecting these responses remains exploratory until evaluated on
   independent data. Use sample-size/power simulation tailored to the actual
   model, loadings, categories and missingness; no universal observations-per-item
   rule establishes adequacy.
5. Decide whether items are ordinal or defensibly continuous; name the estimator
   and missing-data strategy before fitting. Inspect sparse categories, floor/ceiling
   effects, repeated respondents, site clusters, weights and nonresponse. The
   simple independent-respondent example does not account for complex surveys,
   clustered organizations or repeated observations.

Reuse `audit-dataset` for generic data checks, `literature-review` for instrument
and citation retrieval, and `is-research-design` for construct/theory alignment.
Treatment effects belong in `did-analysis` when its design assumptions apply.
Do not create another literature-search or causal-estimation workflow here.

Read the local method notes before reliability or group comparisons. From a
Python Cell (or use the native `read_skill_file` tool):

```python
print(host.skills.read("is-survey-measurement", "method-notes.md"))
```

## Preserve raw items and audit scoring

The following is an **example mapping**, not an established Trust/Usefulness
instrument. Replace it with the documented instrument and confirm direction and
missing codes before running. `survey.csv` must be a real, researcher-supplied
CSV in the workspace with one independent respondent per row. Do not fabricate
responses to make the analysis complete. Keep direct identifiers out of exports.

The recipe fails on missing items unless the researcher explicitly chooses
`complete_case` for this analysis. Complete-case analysis can be biased; it is
not a default recommendation. Save the missingness audit before fitting and
discuss alternative missing-data models when appropriate.

```python
import csv
import hashlib
import io
import json
from pathlib import Path

# EXAMPLE ONLY: confirm this mapping against the supplied questionnaire.
items = {
    "q1": {"construct": "Trust", "min": 1, "max": 5, "reverse": False},
    "q2": {"construct": "Trust", "min": 1, "max": 5, "reverse": False},
    "q3": {"construct": "Trust", "min": 1, "max": 5, "reverse": True},
    "q4": {"construct": "Usefulness", "min": 1, "max": 5, "reverse": False},
    "q5": {"construct": "Usefulness", "min": 1, "max": 5, "reverse": False},
    "q6": {"construct": "Usefulness", "min": 1, "max": 5, "reverse": True},
}
missing_codes = {"", "NA"}  # Confirm the source's codes, including any numeric sentinels.
missing_policy = "error"  # Change only to an explicitly justified "complete_case".
if missing_policy not in {"error", "complete_case"}:
    raise ValueError("Declare a supported missing-data policy")
source = Path("survey.csv")
source_bytes = source.read_bytes()
reader = csv.DictReader(io.StringIO(source_bytes.decode("utf-8-sig")))
if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
    raise ValueError("Missing or duplicate column names")
if not set(items).issubset(reader.fieldnames):
    raise ValueError("The confirmed questionnaire items are absent from survey.csv")
raw_rows = list(reader)
if not raw_rows:
    raise ValueError("No respondent records")

scored, excluded_rows = [], []
missing_counts = {item: 0 for item in items}
category_counts = {item: {} for item in items}
for row_number, raw in enumerate(raw_rows, start=1):
    if None in raw or any(raw[item] is None for item in items):
        raise ValueError(f"Malformed CSV record {row_number}")
    row = {"source_row": row_number}
    for item, spec in items.items():
        value = raw[item].strip()
        if value in missing_codes:
            missing_counts[item] += 1
            row[item] = None
            continue
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"Unrecognized code at row {row_number}, item {item}") from exc
        # This example is for contiguous, integer-valued Likert categories.
        if not number.is_integer() or not spec["min"] <= number <= spec["max"]:
            raise ValueError(f"Out-of-range/noninteger code at row {row_number}, item {item}")
        number = int(number)
        category_counts[item][number] = category_counts[item].get(number, 0) + 1
        row[item] = spec["min"] + spec["max"] - number if spec["reverse"] else number
    if any(row[item] is None for item in items):
        excluded_rows.append(row_number)
    else:
        scored.append(row)

audit = {
    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    "source_rows": len(raw_rows), "complete_rows": len(scored),
    "missing_policy": missing_policy, "incomplete_source_rows": excluded_rows,
    "item_missing_counts": missing_counts, "raw_category_counts": category_counts,
    "item_specification": items, "missing_codes": sorted(missing_codes),
    "status": "blocked_missing_items" if excluded_rows and missing_policy == "error" else "ready_for_review",
}
print(json.dumps(audit, indent=2))
output_dir = Path("measurement-analysis")
output_dir.mkdir(exist_ok=False)  # Choose a fresh directory; never overwrite an earlier analysis.
(output_dir / "scoring-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
if audit["status"] == "blocked_missing_items":
    raise ValueError("Missing items: audit saved; agree a missing-data strategy before fitting")
if not scored:
    raise ValueError("No complete respondent records remain")
with (output_dir / "scored-items.csv").open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=["source_row", *items])
    writer.writeheader()
    writer.writerows(scored)
```

Inspect the audit and generic `audit-dataset` output before accepting the sample.
`source_row` is a record index, not proof of respondent independence. A retained
sample is not automatically representative. Reverse coding preserves order
according to the known scale endpoints; observed sample minima/maxima must never
replace those endpoints. The raw source file is untouched.

## Fit an ordinal CFA in the R kernel

Use a fenced `r` Cell in OpenAI4S's independent R kernel. R has **no `host`
singleton** and does not share Python variables; the workspace files above are
the explicit handoff. The selected R environment must already contain `lavaan`.
If R or the package is unavailable, report that prerequisite and keep the audit;
do not claim model estimates or silently install packages inside this recipe.

This fits two correlated reflective factors, with no directional causal path.
The model must match the confirmed item mapping. For ordered items `WLSMV` uses
an ordinal model; changing the objective alone without declaring `ordered`
does not turn numeric codes into ordinal indicators. See the
[lavaan categorical-data documentation](https://lavaan.ugent.be/tutorial/cat.html).

```r
if (!requireNamespace("lavaan", quietly = TRUE)) {
  stop("Missing optional R package lavaan in the selected R kernel; CFA was not run.")
}
output_dir <- "measurement-analysis"
data <- read.csv(file.path(output_dir, "scored-items.csv"), check.names = FALSE)
items <- paste0("q", 1:6)
stopifnot(all(items %in% names(data)), nrow(data) > 0L)
if (anyNA(data[items]) || any(!is.finite(as.matrix(data[items])))) {
  stop("The scored analysis sample contains missing or nonfinite items.")
}
if (any(vapply(data[items], function(x) length(unique(x)) < 2L, logical(1)))) {
  stop("At least one item has no observed category variation.")
}
model <- '
  Trust =~ q1 + q2 + q3
  Usefulness =~ q4 + q5 + q6
'
fit <- lavaan::cfa(model, data = data, ordered = items,
                   estimator = "WLSMV", std.lv = TRUE)
saveRDS(fit, file.path(output_dir, "ordinal-cfa.rds"))
writeLines(model, file.path(output_dir, "measurement-model.txt"))
writeLines(capture.output(sessionInfo()), file.path(output_dir, "r-session.txt"))
if (!isTRUE(lavaan::lavInspect(fit, "converged")) ||
    !isTRUE(lavaan::lavInspect(fit, "post.check"))) {
  stop("CFA is nonconverged or inadmissible; inspect saved fit and warnings, not validity claims.")
}
parameters <- lavaan::parameterEstimates(fit, standardized = TRUE, ci = TRUE)
fit_indices <- lavaan::fitMeasures(fit)
write.csv(parameters, file.path(output_dir, "cfa-parameters.csv"), row.names = FALSE)
write.csv(data.frame(index = names(fit_indices), value = as.numeric(fit_indices)),
          file.path(output_dir, "cfa-fit.csv"), row.names = FALSE)
print(summary(fit, standardized = TRUE, fit.measures = TRUE))
```

Retain warnings, estimator, software versions, sample sizes, loadings, residuals,
factor correlations, fit indices and their exact robust/scaled labels. A zero-df
model cannot be assessed by global fit. Do not silently replace failed robust
indices with ordinary ones. Negative variances, singular covariance matrices,
implausible correlations and nonconvergence block substantive interpretation.

For defensibly continuous indicators, a separate prespecified analysis may use
`lavaan::cfa(model, data = data, estimator = "MLR", missing = "fiml")` on the
appropriately coded **unfiltered** data, under documented missingness assumptions.
Do not feed the already complete-case CSV to FIML and claim excluded cases were
recovered. FIML is not available for the ordinal WLSMV example. Report sensitivity
to the estimator and missing-data strategy without selecting the best-looking fit.

## Reliability, validity and invariance

Use [method-notes.md](method-notes.md) to choose the estimand and assumptions
before computing reliability. Alpha alone does not validate a scale, and a
loading-only omega formula is not general enough for every model. If available,
`semTools::compRelSEM(fit, tau.eq = FALSE, ord.scale = TRUE)` can estimate
model-based reliability on the ordinal response scale; check that the fitted
model and software support the intended score. Otherwise report reliability
as **not computed**, with the missing dependency or method stated.

Before country, language, role or time comparisons, specify and evaluate
measurement invariance with the appropriate identification constraints. Ordinal
items require attention to thresholds and latent-response scales; do not copy
the continuous intercept-equality sequence blindly. A single pooled CFA or
separate apparently good group fits does not establish invariance. Record
`not assessed` if a group comparison was not requested or data are unavailable.

SEM structural coefficients, including an indirect path, do not establish
causality from cross-sectional survey covariances. Discuss temporal ordering,
confounding, common-method sources, nonresponse and measurement error. A
single-factor test cannot establish absence of common-method bias. Preserve all
prespecified models and clearly mark post-hoc correlated errors or item deletions
as exploratory instead of repeatedly editing until conventional cutoffs are met.

## Deliver the evidence with its limits

Return a readable measurement report plus the source checksum, exact item and
model specifications, scoring audit, sample exclusions, execution code, runtime
versions and exported fit/parameter tables. Summarize reliability per intended
score, validity evidence and contradictions, invariance tested/untested, and the
comparisons the evidence supports. Distinguish unavailable analysis, inadequate
data and a model rejected by diagnostics. Protect participant identities in any
shared report; do not include raw responses just because an artifact exists.
