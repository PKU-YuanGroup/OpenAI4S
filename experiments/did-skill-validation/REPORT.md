# Synthetic DiD validation report

[中文报告](REPORT_zh.md)

The actual DiD Skill's calculations agree with independent statsmodels/SciPy calculations for traditional DiD, event-study covariance and joint pretrend tests, triple differences, whole-cluster placebos, and both supported staggered control definitions. **This arithmetic agreement does not establish nominal 95% confidence coverage.** With 24 assignment clusters, the prespecified null simulation rejects zero in 13/128 replicates (10.16%, Monte Carlo Wilson interval 6.03–16.60%) and covers the true effect in 115/128 (89.84%, interval 83.40–93.97%). These calibration limitations are retained without seed retuning.

## Design and independence

`prespecification.json` was written before generating results. There are 48 entities in 24 independent assignment clusters, two entities per cluster, and eight periods 0–7. Clusters 0–11 adopt at period 4 in the common-date panels. Each panel's untreated outcome combines an entity intercept, a common period trend, a stationary cluster AR(1) shock, and independent entity-period noise. Treatment is observed absorbing exposure 0/1. Assignment, event date, subgroup and sample are specified from the DGP, never from observed outcome changes.

The saved examples use seed 20261010. The simulation uses exactly seeds 910000–910127, 128 replicates for each of four scenarios; all 512 complete panels are retained in `inputs/monte_carlo_unit_outcomes.csv`. Matching seeds share untreated shocks across the constant-effect, null and violated-pretrend scenarios. Their comparison uses common random numbers: equal constant/null errors are a paired comparison, **not 256 independent replicates**. No seeds, clusters or rates were selected after seeing estimates.

The runner imports and executes the actual sidecar's `traditional_did`, `event_study`, `placebo_time`, `placebo_group`, `placebo_distribution`, `triple_difference`, `staggered_did`, `analyze_did`, `write_outputs` and optional `render_diagnostics`. The verifier imports neither runner nor Skill numerical functions. It independently fits statsmodels OLS to saved entity post-minus-pre changes, with regressors `[1,T]` or `[1,T,S,T*S]` for DDD. Its CR1 correction is `G/(G-1) × (N-1)/(N-K)`, where N counts entities and K is 2 or 4. This reproduces the specified change-regression inference; an expanded panel fixed-effects fit can have the same coefficient but a different finite-sample correction. The cluster covariance API's small-sample option is documented by [statsmodels](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_cluster.html). The archived environment uses statsmodels 0.14.5, numpy 2.3.5 and SciPy 1.16.3.

The verifier constructs cross-equation covariance from independently computed cluster influence vectors, checks all event/staggered cells and linear aggregation variances, and uses [SciPy Student-t](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html) and [F](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.f.html) survival functions to check pointwise and joint inference. Every Monte Carlo coefficient, interval and rejection decision is recalculated from saved panels. Only deterministic numerical comparisons, source/output integrity, finite JSON, LF CSV and replay determine the audit result. Coverage and rejection proportions have no pass threshold.

## Saved example results

These intervals are pointwise asymptotic cluster intervals, not simultaneous bands or a claim of exact finite-sample validity.

| Prespecified scenario | Assigned effect | Estimate | Nominal 95% interval | Joint pretrend p |
| --- | ---: | ---: | --- | ---: |
| Constant effect | 2.000 | 2.096773 | [1.494501, 2.699044] | 0.289411 |
| Null | 0.000 | 0.096773 | [-0.505499, 0.699044] | 0.289411 |
| Violated pretrends | 2.000 | 3.496773 | [2.894501, 4.099044] | 0.000214 |
| Strong clustered noise | 2.000 | 2.236059 | [1.275931, 3.196186] | 0.369752 |

The violated scenario adds an untreated treated-group trend of 0.35 per period. The post-period mean date is 5.5 and pre-period mean date is 1.5, so its population DiD bias relative to the assigned treatment effect is 0.35 × 4 = 1.4. The reported coefficient is then a biased causal contrast even when a conventional test rejects zero.

The DDD example adds a common treated/post effect of 0.7 and a subgroup-specific increment of 1.5, plus a shared subgroup time trend and a static treated/subgroup difference. The latter nuisance components cancel from the saturated triple contrast. Estimated DDD is 1.585101, interval [1.194531, 1.975671], against truth 1.5. Subgroups share assignment clusters; the independent check retains their covariance instead of adding two subgroup variances as if independent.

The fake-time placebo uses the genuine treatment assignment but pre-periods 0–1, fake onset 2 and placebo post-periods 2–3, all before real onset 4. Its estimate is 0.110263 (p=0.579066). The fake-group placebo uses only genuinely never-treated entities, chooses all entities in six control clusters, and estimates -0.112745 (p=0.824207). All 99 seeded distribution draws also assign whole never-treated clusters. The fraction of absolute placebo estimates exceeding the real estimate is 0/99; **this is a descriptive tail fraction, not a randomization p-value**, since assignment exchangeability is not established. These selected example null diagnostics do not establish parallel trends.

The staggered panel assigns six clusters to cohort 3, six to cohort 5 and twelve to never treatment. Cohort-3 effects are `1 + 0.3 × event_time`; cohort-5 effects are `3 + 0.5 × event_time`. Unconditional group-time comparisons use baseline g-1 and either never-treated or not-yet-treated controls. The aggregate weights each supported group-time cell by cohort size. Five post cells for cohort 3 and three for cohort 5 imply weights 5/8 and 3/8 at the cohort level and an assigned aggregate effect of 2.3125. Estimates are 2.601763 [2.163077, 3.040449] with never-treated controls and 2.574352 [2.135794, 3.012909] with not-yet-treated controls.

Dynamic truth is 2.0, 2.4, 2.8, 1.9, 2.2 for event times 0–4. Event times 3–4 contain only cohort 3, so the apparent decline between 2 and 3 reflects changing support rather than a decline within either cohort. The saved results disclose supported cohorts, eligible controls, cell weights and full joint covariance. This is an unconditional mean-change version; it does not reproduce conditional/doubly robust Callaway–Sant'Anna scores, multiplier bootstrap or simultaneous bands.

## Prespecified Monte Carlo results

Each row uses 128 replicates. Bias is relative to the assigned treatment effect, including in the deliberately unidentified violated-pretrend scenario. Bias MCSE is the empirical standard deviation of estimation errors divided by sqrt(128). Coverage intervals are Wilson intervals for the Monte Carlo proportions, not confidence intervals for a treatment effect.

| Scenario | Bias | Bias MCSE | RMSE | Mean SE | Coverage count | Coverage rate; MC 95% interval |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Constant effect | 0.033995 | 0.027387 | 0.310504 | 0.273338 | 115/128 | 89.84%; [83.40%, 93.97%] |
| Null | 0.033995 | 0.027387 | 0.310504 | 0.273338 | 115/128 | 89.84%; [83.40%, 93.97%] |
| Violated pretrends | 1.433995 | 0.027387 | 1.466833 | 0.273338 | 0/128 | 0.00%; [0.00%, 2.91%] |
| Strong clustered noise | 0.027881 | 0.045081 | 0.508807 | 0.493044 | 124/128 | 96.88%; [92.24%, 98.78%] |

| Scenario | Reject zero count | Reject zero rate; MC 95% interval | Reject joint pretrends count | Pretrend rejection rate; MC 95% interval |
| --- | ---: | --- | ---: | --- |
| Constant effect | 128/128 | 100.00%; [97.09%, 100.00%] | 13/128 | 10.16%; [6.03%, 16.60%] |
| Null | 13/128 | 10.16%; [6.03%, 16.60%] | 13/128 | 10.16%; [6.03%, 16.60%] |
| Violated pretrends | 128/128 | 100.00%; [97.09%, 100.00%] | 62/128 | 48.44%; [39.95%, 57.01%] |
| Strong clustered noise | 126/128 | 98.44%; [94.48%, 99.57%] | 13/128 | 10.16%; [6.03%, 16.60%] |

The null false-rejection rate and constant-effect coverage indicate undercoverage in this specific modest-cluster DGP; matching a software reference does not repair it. The stronger-noise DGP's 124/128 coverage is a separate finite simulation result, not proof of general calibration. The pretrend test detects only 62/128 deliberately violated designs, leaving 66/128 non-rejections despite bias around 1.4. Conversely, it rejects 13/128 designs with true parallel trends. Neither non-rejection nor rejection alone is a design verdict. Monte Carlo rate MCSEs and Wilson intervals, including nonzero intervals for observed 0/128 or 128/128 outcomes, are preserved in the JSON summary.

## Refusals, reproducibility and scope

`results/pipeline/` preserves an actual ordered baseline → event-study → time/group/distribution placebos → derived DDD workflow. This full workflow uses the constant-effect panel, whose subgroup-specific DDD truth is zero; the separate known-1.5 DDD example and heterogeneous staggered example have their own exported diagnostic bundles in `results/triple-difference/` and `results/staggered-never/`. `results/refusals/` requests an invalid fake date after real adoption and a fake group containing genuinely treated entities. Both diagnostics are retained as refused with explicit reasons, while the later distribution and DDD still execute. A refusal has no fabricated coefficient in the exported CSV.

`results/artifact_manifest.json` binds source hashes and all deterministic input/output artifacts; `results/verification.json` records the independent checks and byte replay. The verifier report is intentionally outside that manifest because it is written after the audit. CSVs use LF; JSON rejects nonfinite numbers. The runner refuses existing input/result directories and the verifier refuses an existing audit file. Source hashes are captured before execution and rechecked before freezing the manifest. Plot metadata has no timestamp and SVG IDs use a fixed salt.

The final repository-context independent audit passed **12,237 deterministic checks with zero failures**, using absolute and relative numerical tolerances of 1e-9. The largest absolute numerical discrepancy was 4.3053e-12. All **45 preserved deterministic artifacts** and the artifact manifest reproduced byte for byte in a fresh run, including three PNGs and three SVGs. The plots were also visually inspected. The estimator/recipe hashes bound by that run are:

| Frozen source or audit artifact | SHA-256 |
| --- | --- |
| `skills/did-analysis/kernel.py` | `b075a9b05b17fdd15938d5f387e50df53583b62e88e0bd4855dcbff6cbae4706` |
| `skills/did-analysis/SKILL.md` | `3be8ae1fd74c9e80b61228947f6c294ddaca16994425f2cad2f5e677d2fcc553` |
| `skills/did-analysis/method-notes.md` | `1e09628216d116b080c53357ab91ebac8e5d4f8bc050f217c1009937936e5ad2` |
| `results/artifact_manifest.json` | `38e7d5a81574814b47d534afde687c897880eb7886c4af1888cc490ebac57994` |
| `results/verification.json` | `5bddf1c24e04e3ed50f1a36b712e489fc392162aebe1237f5220fbec23fcbeb9` |

A later metadata-only update declared `network: {mode: none, domains: []}` in the Skill frontmatter. The prior verified 47-file input/result snapshot was preserved byte for byte in a local archive outside the repository. The actual runner was then rerun with the current source, independently audited and replayed before restoring this refreshed bundle. All 45 deterministic artifacts, including every input CSV, numerical output and plot, were unchanged; the artifact manifest changed only its Skill source hash. The independent reference values and discrepancies were also unchanged. The local comparison record is `metadata-refresh-comparison.json` in the local archive; it records the one fewer finite-JSON context check in the fresh destination, which does not contain the protocol JSON beside the source runner. The final verifier was then executed against the restored repository bundle, including that adjacent protocol JSON, and its actual 12,237-check report is the archived `results/verification.json`. The intermediate fresh-destination report remains separately preserved in the local archive. No estimator, Python source, DGP, seed or statistical result was changed, and historical experiment archives were untouched.

The experiment tests this prespecified balanced unconditional estimator and records its limits. It does not establish real-world identification, calibration for arbitrary cluster sizes, a heterogeneity-safe conventional TWFE estimator, conditional adjustment, bootstrap inference, missing-data validity, or an exact empirical paper replication. No historical replication archive was read or changed. The synthetic truth and adverse simulations are part of the evidence, not grounds for replacing the assigned treatment date, sample or seed list.

Publication note: machine-local archive paths are omitted; numerical checks and artifact hashes are unchanged.
