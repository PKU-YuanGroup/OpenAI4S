# Estimators and inference

## Traditional contrast

For each included entity, form d_i = mean(Y_i,post) - mean(Y_i,pre) using the
explicit requested periods. Fit d_i = a + b D_i + u_i; b is the treated mean
change minus the control mean change. Complete requested observations and an
unchanging entity sample prevent row availability from silently setting weights.
The pre/post choice still determines the substantive estimand.

For X with N included entities, K fitted coefficients and G assignment clusters,
cluster score s_c = sum_i in c X_i u_i. The CR1 sandwich is

```
V = [G/(G-1)] [(N-1)/(N-K)] (X'X)^(-1) [sum_c s_c s_c'] (X'X)^(-1).
```

Pointwise intervals and two-sided p-values use Student-t with G-1 degrees of
freedom. This is an asymptotic cluster approximation with a conventional small
sample correction, not a guarantee with few clusters. Even estimable designs
with modest cluster counts can
under-cover or over-reject. Inspect finite-sample calibration evidence rather
than treating the support floor as a reliability threshold. Inference is unavailable
when the design lacks residual degrees of freedom or independent support in the
relevant arms/cells. A zero residual variance does not prove certainty. The
implementation records the reason when uncertainty cannot be calculated.
The aggregation unit here is an entity change, not every original panel row;
the correction need not match a row-level fixed-effects regression's correction.

Serially dependent panel data require attention to assignment clustering:
[Bertrand, Duflo and Mullainathan](https://www.nber.org/papers/w8841) show why
conventional DiD standard errors and placebo rejection frequencies can mislead.
The covariance convention is independently comparable to
[statsmodels cov_cluster](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_cluster.html).

## Event study and placebo

At period t relative to reference r, use entity contrast Y_it - Y_ir and the
same treated/control contrast on a sample complete for all requested periods.
Joint cluster score vectors retain the covariance induced by a shared baseline
and shared units. Pointwise intervals do not control familywise error. A
pre-period diagnostic is an assessment of the supplied design; nonrejection
does not verify counterfactual parallel trends. See the official
[DiD pre-testing vignette](https://bcallaway11.github.io/did/articles/pre-testing.html).

A fake-time analysis freezes real group assignment and confines both windows
to genuine untreated periods. A fake-group analysis splits only never-treated
assignment clusters. These are different diagnostics. Sampling arbitrary fake
groups does not reproduce the actual policy assignment mechanism and therefore
does not produce a formal randomization-test p-value for the original effect.

## Group-time ATT

For treated adoption cohort g, base b=g-1 and target t, contrast means of
Y_it-Y_ib between that cohort and the explicitly eligible comparison units.
Never-treated controls have cohort 0; not-yet-treated controls must be untreated
at both contrast periods and cannot belong to the target cohort. Use a common
sample complete for the requested periods. This version returns post-treatment cells only; requested pre-periods support
the complete sample and cohort baselines. It does not implement staggered
pre-treatment pseudo-ATT tests. Post-treatment cells identify ATT(g,t) only under the declared
unconditional parallel-trends, no-anticipation and absorbing-treatment assumptions.

Dynamic aggregates weight supported cells at the same t-g by observed cohort
sizes. The overall aggregate documents which cohort/time cells and weights enter.
Different horizons can include different cohorts. Carry joint influence scores
through aggregation to include covariances from reused treated units and controls;
cohort sizes and weights are conditioned on the observed design. This implementation
uses an analytic cluster approximation and cannot be described as the full
conditional/doubly robust estimator or multiplier-bootstrap inference in
[Callaway and Sant'Anna](https://arxiv.org/abs/1803.09015v4) or the official
[att_gt API](https://bcallaway11.github.io/did/reference/att_gt.html).

## Triple differences

For fixed binary subgroup S, fit entity changes with saturated regressors
[1,D,S,D×S]. The D×S coefficient equals DiD(S=1)-DiD(S=0); its clustered
covariance uses the single joint regression, retaining correlation across cells.
Four cells with independent assignment support are necessary. Identification
needs the appropriate unconditional DDD restriction; two null subgroup pre-tests
alone do not establish it. The supported design has one common adoption date.

Do not extend this algebra automatically to conditional or staggered DDD.
[Better Understanding Triple Differences Estimators](https://arxiv.org/abs/2505.09942)
describes failures of conventional differences-between-DiDs when identification
depends on conditioning and of some staggered comparison strategies.

## Evidence boundary

Point estimates, clustered sampling uncertainty, pre-period diagnostics and
placebos answer different questions. None verifies exogeneity, no interference,
an ignorable missing-data mechanism, or absence of post-treatment sample selection.
An explicit complete-case restriction defines the analyzed sample and introduces
additional assumptions; an imputed outcome is not a newly observed outcome.
Preserve every requested result and distinguish an unsupported estimate from a
scientifically credible null result.
