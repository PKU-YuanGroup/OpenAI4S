# Measurement model decisions

Adapted from AlterLab's `alterlab-sem-psychometrics` and its
`references/fit_and_invariance.md`; source hashes and changes are recorded in
[UPSTREAM.json](UPSTREAM.json), with the upstream MIT notice in [LICENSE](LICENSE).
These notes cover reflective measurement in IS research, not formative-index
validation or a replacement for the questionnaire's original validation study.

## Fit is diagnostic evidence

Inspect convergence, identification, factor orientation, residuals, improper
solutions and substantive plausibility before global fit indices. Report the
actual estimator, chi-square and degrees of freedom, CFI/TLI, RMSEA with interval
when available, and SRMR. Robust/scaled and ordinary versions are different
quantities; preserve their labels and explain any unavailable result.

Frequently quoted CFI/TLI, RMSEA and SRMR cutoffs came from particular simulation
conditions. They are not universal pass/fail rules across model size, item
categories, loadings, estimator or sample size. Prespecify an assessment approach
appropriate to this design and examine localized misfit, uncertainty and
alternative substantive explanations. An excellent fit does not establish
construct content, predictive usefulness, causal direction or population
generalizability. A just-identified model has no global-fit test to pass.

For a new or adapted scale, assess content coverage and item comprehension with
domain expertise and cognitive/pilot evidence. Translation/back-translation
alone does not establish equivalent measurement. Keep the original instrument
version, modifications and permissions. Use `literature-review` to verify
instrument citations; do not invent a validation history from a familiar scale
name.

## What reliability refers to

State the score (sum, mean, weighted composite, factor score), population, model
and response scale whose reliability is being estimated. Internal consistency
does not establish unidimensionality or validity. Alpha is not a universal lower
bound and cannot be interpreted as true-score reliability without assumptions;
correlated errors and unequal loadings can invalidate simplistic readings.

For one continuous congeneric factor with unit factor variance, a composite
with weights `w`, loadings `lambda`, and residual covariance matrix `Theta` has
model-based reliability:

```text
(w' lambda)^2 / [(w' lambda)^2 + w' Theta w]
```

For unit weights, standardized indicators and uncorrelated errors, this reduces
to `(sum(lambda))^2 / ((sum(lambda))^2 + sum(1 - lambda^2))`. It must not be applied
as a general omega formula for correlated residuals, cross-loadings, multiple
factors, a bifactor hierarchical score, or ordinal observed categories. It also
requires an admissible model and consistently oriented items. A high value for a
redundant item set does not show broad construct coverage.

For the ordinal CFA in the main recipe, an optional R Cell is:

```r
if (!requireNamespace("semTools", quietly = TRUE)) {
  message("Ordinal-scale reliability not computed: optional semTools is unavailable.")
} else {
  # fit must be the converged, admissible lavaan object from SKILL.md.
  reliability <- semTools::compRelSEM(fit, tau.eq = FALSE, ord.scale = TRUE)
  print(reliability)
  writeLines(capture.output(reliability),
             file.path(output_dir, "ordinal-reliability.txt"))
}
```

The `ord.scale` choice distinguishes the categorical observed-response scale
from the underlying latent-response scale. Record which is reported; point
estimates are not confidence intervals. Use a justified resampling/model-based
procedure for uncertainty, respecting any sampling clusters. Do not invent an
interval, clip an inadmissible reliability to [0,1], or quietly substitute alpha
when the planned estimand is unavailable. See the
[semTools compRelSEM documentation](https://rdrr.io/cran/semTools/man/compRelSEM.html).

Convergent/discriminant validity requires substantive argument and multiple
forms of evidence. AVE, factor correlations and HTMT can be diagnostics under
appropriate models; a threshold does not prove distinct constructs. Check
wording overlap, cross-loadings and whether supposedly different constructs
were defined differently in the first place. Preserve contradictory evidence.

## Group and time comparisons

Define which comparison is intended: relationships, latent means, observed
scores or change over time. Specify grouping, group sizes, category support,
anchor items, factor scaling and equality constraints. Evaluate the unconstrained
measurement structure and identification in each group before pooling evidence.

For **continuous reflective indicators**, the usual progression examines the
same factor pattern (configural), equal loadings (metric), equal loadings and
intercepts (scalar), then residual constraints where relevant. Configural fit is
evidence compatible with the proposed pattern, not proof of identical construct
meaning. Comparable latent means need justified anchors/intercept restrictions;
even a passing scalar model does not remove sampling or causal confounding.
Do not equate strict invariance with an automatic license for every observed-score
comparison. For repeated waves, model within-person dependence and justified
item-specific correlated errors; waves are not independent groups.

For **ordinal indicators**, thresholds and latent-response location/scale
identification affect which constraints are meaningful and nested. Binary and
multi-category items need different care. Use the documented ordinal
identification sequence, such as the `semTools::measEq.syntax` approach, and
inspect the generated constraints rather than relabelling continuous scalar
syntax as ordinal invariance. This recipe does not claim to implement a general
automatic ordinal-invariance test. See the
[semTools measEq.syntax documentation](https://rdrr.io/cran/semTools/man/measEq.syntax.html)
and [lavaan multiple-group guide](https://lavaan.ugent.be/tutorial/groups.html).

Compare genuinely nested models on the same analysis sample and compatible
estimators. Use the software's applicable scaled difference test rather than
subtracting robust chi-square values by hand. Consider changes in fit indices,
localized parameter differences, group imbalance and uncertainty together;
fixed delta cutoffs alone do not establish invariance. A poor or underpowered
configural model cannot be rescued by a small delta.

If invariance is unsupported, report the affected comparisons as unsupported.
Partial invariance requires explicitly justified invariant anchors, disclosed
freed parameters and sensitivity analyses; do not free parameters until the
desired group difference appears. A lack of group data means `not assessed`,
not `passed` or `failed`.

## Structural models and method limits

Proceed to prespecified structural paths only after inspecting the measurement
model. Keep measurement restrictions, estimator, sample and structural theory
visible. Changing an arrow in covariance-based SEM can leave equally plausible
models; model fit does not identify causal direction. Longitudinal or experimental
design also requires its own identification argument. Formative constructs,
IRT, EFA, multilevel SEM and complex-survey corrections need distinct plans and
appropriate optional packages; they are not silently supported by this CFA
recipe. Existing data quality, literature and treatment-effect skills retain
their own responsibilities.
