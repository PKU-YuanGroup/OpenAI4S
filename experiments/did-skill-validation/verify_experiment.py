"""Independent statsmodels/SciPy audit of saved actual Skill outputs.

No numerical function from the runner or Skill is imported here. Raw examples
and the complete saved Monte Carlo panels provide the statistical inputs.
Optional --replay executes the runner in a fresh directory and compares all
deterministic output bytes; scientific rates are never pass/fail thresholds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import scipy
    import statsmodels.api as sm
    from scipy.stats import f as f_distribution
    from scipy.stats import t as t_distribution
except ImportError as exc:
    raise SystemExit(
        "Independent verification requires the optional analysis dependencies in requirements-analysis.txt"
    ) from exc

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ATOL = 1e-9
RTOL = 1e-9


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"nonfinite JSON constant: {value}")
        ),
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows


class Audit:
    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.failures: list[dict[str, Any]] = []
        self.max_error: dict[str, float] = defaultdict(float)

    def check(self, group: str, condition: bool, context: str) -> None:
        self.counts[group] += 1
        if not condition:
            self.failures.append({"group": group, "context": context})

    def near(self, group: str, actual: Any, expected: Any, context: str) -> None:
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        if a.shape != b.shape:
            self.check(group, False, f"{context}: shape {a.shape} != {b.shape}")
            return
        error = float(np.max(np.abs(a - b))) if a.size else 0.0
        self.max_error[group] = max(self.max_error[group], error)
        self.check(group, bool(np.allclose(a, b, atol=ATOL, rtol=RTOL)), context)


def panel_units(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["entity"])].append(row)
    units = []
    for entity in sorted(grouped):
        rs = grouped[entity]
        first = rs[0]
        units.append(
            {
                "entity": entity,
                "cluster": int(first["cluster"]),
                "group": int(int(first["cohort"]) > 0),
                "cohort": int(first["cohort"]),
                "subgroup": int(first["subgroup"]),
                "outcomes": {int(r["period"]): float(r["outcome"]) for r in rs},
                "truth": {int(r["period"]): float(r["true_effect"]) for r in rs},
            }
        )
    return units


def reference_fit(
    units: list[dict[str, Any]], pre: list[int], post: list[int], *, ddd: bool = False
) -> dict[str, Any]:
    """Independent statsmodels saturated change regression plus score cross-products."""
    change = np.asarray(
        [
            statistics.mean(u["outcomes"][p] for p in post)
            - statistics.mean(u["outcomes"][p] for p in pre)
            for u in units
        ]
    )
    columns = [
        (
            [1.0, u["group"], u["subgroup"], u["group"] * u["subgroup"]]
            if ddd
            else [1.0, u["group"]]
        )
        for u in units
    ]
    x = np.asarray(columns, dtype=float)
    clusters = np.asarray([u["cluster"] for u in units])
    fit = sm.OLS(change, x).fit(
        cov_type="cluster",
        cov_kwds={"groups": clusters, "use_correction": True, "df_correction": True},
        use_t=True,
    )
    coefficient = len(columns[0]) - 1
    # Independent explicit bread/cluster scores establish cross-equation covariance.
    bread = np.linalg.inv(x.T @ x)
    scores = x * np.asarray(fit.resid)[:, None]
    cluster_if = {
        int(cluster): (bread @ scores[clusters == cluster].sum(axis=0))[coefficient]
        for cluster in np.unique(clusters)
    }
    n, k, g = len(units), x.shape[1], len(cluster_if)
    correction = g / (g - 1) * (n - 1) / (n - k)
    estimate, se = float(fit.params[coefficient]), float(fit.bse[coefficient])
    critical = float(t_distribution.ppf(0.975, g - 1))
    return {
        "estimate": estimate,
        "se": se,
        "ci_low": estimate - critical * se,
        "ci_high": estimate + critical * se,
        "p_value": float(2 * t_distribution.sf(abs(estimate / se), g - 1)),
        "coefficients": fit.params.tolist(),
        "covariance": fit.cov_params().tolist(),
        "cluster_if": {
            c: value * math.sqrt(correction) for c, value in cluster_if.items()
        },
        "clusters": g,
        "correction": correction,
    }


def joint_covariance(fits: list[dict[str, Any]]) -> np.ndarray:
    labels = sorted({cluster for fit in fits for cluster in fit["cluster_if"]})
    scores = np.asarray(
        [[fit["cluster_if"].get(cluster, 0.0) for cluster in labels] for fit in fits]
    )
    return scores @ scores.T


def linear_inference(
    estimate: float, variance: float, cluster_count: int
) -> dict[str, float]:
    se = math.sqrt(variance)
    df = cluster_count - 1
    critical = float(t_distribution.ppf(0.975, df))
    return {
        "estimate": estimate,
        "se": se,
        "ci_low": estimate - critical * se,
        "ci_high": estimate + critical * se,
        "p_value": float(2 * t_distribution.sf(abs(estimate / se), df)),
    }


def compare_fit(
    audit: Audit, name: str, actual: dict[str, Any], expected: dict[str, Any]
) -> None:
    for key in ["estimate", "se", "ci_low", "ci_high", "p_value"]:
        audit.near(
            "statsmodels_regression", actual[key], expected[key], f"{name}/{key}"
        )
    if "coefficient_covariance" in actual:
        audit.near(
            "statsmodels_covariance",
            actual["coefficient_covariance"],
            expected["covariance"],
            name,
        )


def event_reference(
    units: list[dict[str, Any]], periods: list[int], reference: int, date: int
) -> dict[str, Any]:
    comparison = [period for period in periods if period != reference]
    fits = [reference_fit(units, [reference], [p]) for p in comparison]
    covariance = joint_covariance(fits)
    indices = [index for index, p in enumerate(comparison) if p < date]
    b = np.asarray([fits[index]["estimate"] for index in indices])
    v = covariance[np.ix_(indices, indices)]
    wald = float(b @ np.linalg.solve(v, b))
    df_den = fits[0]["clusters"] - 1
    return {
        "periods": comparison,
        "fits": fits,
        "covariance": covariance,
        "wald": wald,
        "f_statistic": wald / len(indices),
        "p_value": float(f_distribution.sf(wald / len(indices), len(indices), df_den)),
        "df_num": len(indices),
        "df_den": df_den,
    }


def staggered_reference(
    units: list[dict[str, Any]], periods: list[int], control: str
) -> dict[str, Any]:
    """Direct mean-change influence function; no call into the Skill implementation."""
    cohorts = sorted({u["cohort"] for u in units if u["cohort"] > 0})
    cells = []
    fits = []
    for cohort in cohorts:
        for period in periods:
            if period < cohort:
                continue
            base = cohort - 1
            selected = []
            for unit in units:
                eligible_control = unit["cohort"] == 0 or (
                    control == "not_yet_treated" and unit["cohort"] > max(period, base)
                )
                if unit["cohort"] == cohort or eligible_control:
                    selected.append({**unit, "group": int(unit["cohort"] == cohort)})
            fit = reference_fit(selected, [base], [period])
            fits.append(fit)
            cells.append(
                {
                    "cohort": cohort,
                    "period": period,
                    "event_time": period - cohort,
                    "treated_entities": sum(u["group"] for u in selected),
                    "true_effect": statistics.mean(
                        u["truth"][period] for u in selected if u["group"]
                    ),
                    **{
                        key: fit[key]
                        for key in ["estimate", "se", "ci_low", "ci_high", "p_value"]
                    },
                }
            )
    covariance = joint_covariance(fits)
    weights = np.asarray([c["treated_entities"] for c in cells], dtype=float)
    weights /= weights.sum()
    estimates = np.asarray([c["estimate"] for c in cells])
    aggregate = {
        "estimate": float(weights @ estimates),
        "variance": float(weights @ covariance @ weights),
        "truth": float(weights @ np.asarray([c["true_effect"] for c in cells])),
    }
    aggregate.update(
        linear_inference(
            aggregate["estimate"],
            aggregate["variance"],
            len({c for fit in fits for c in fit["cluster_if"]}),
        )
    )
    dynamics = []
    for event_time in sorted({c["event_time"] for c in cells}):
        indices = [i for i, c in enumerate(cells) if c["event_time"] == event_time]
        dynamic_weights = np.asarray(
            [cells[i]["treated_entities"] for i in indices], dtype=float
        )
        dynamic_weights /= dynamic_weights.sum()
        dynamics.append(
            {
                "event_time": event_time,
                "estimate": float(dynamic_weights @ estimates[indices]),
                "variance": float(
                    dynamic_weights
                    @ covariance[np.ix_(indices, indices)]
                    @ dynamic_weights
                ),
                "truth": float(
                    dynamic_weights
                    @ np.asarray([cells[i]["true_effect"] for i in indices])
                ),
            }
        )
        dynamics[-1].update(
            linear_inference(
                dynamics[-1]["estimate"],
                dynamics[-1]["variance"],
                len({c for index in indices for c in fits[index]["cluster_if"]}),
            )
        )
    return {
        "cells": cells,
        "covariance": covariance,
        "aggregate": aggregate,
        "dynamics": dynamics,
    }


def mc_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    errors = [float(r["estimate"]) - float(r["truth"]) for r in records]
    result: dict[str, Any] = {
        "replicates": n,
        "bias": statistics.mean(errors),
        "bias_mcse": statistics.stdev(errors) / math.sqrt(n),
        "rmse": math.sqrt(statistics.mean(e * e for e in errors)),
        "mean_standard_error": statistics.mean(
            float(r["standard_error"]) for r in records
        ),
    }
    for field in ["covers_truth", "rejects_zero", "rejects_pretrend"]:
        count = sum(r[field] == "True" for r in records)
        p = count / n
        z = 1.959963984540054
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        result[field] = {
            "count": count,
            "rate": p,
            "mcse": math.sqrt(p * (1 - p) / n),
            "wilson_mc_95_interval": [
                max(0.0, center - radius),
                min(1.0, center + radius),
            ],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=HERE)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        help="Choose a fresh audit report path; default is BUNDLE/results/verification.json",
    )
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    report_path = (args.report or bundle / "results/verification.json").resolve()
    if report_path.exists():
        parser.error(
            f"Refusing to overwrite existing audit {report_path}; choose a fresh --report path"
        )
    audit = Audit()
    protocol = read_json(HERE / "prespecification.json")
    outputs = read_json(bundle / "results/skill_outputs.json")
    manifest = read_json(bundle / "results/artifact_manifest.json")
    observed_artifacts = {
        str(path.relative_to(bundle))
        for folder in [bundle / "inputs", bundle / "results"]
        for path in folder.rglob("*")
        if path.is_file()
        and path.name not in {"artifact_manifest.json", "verification.json"}
    }
    audit.check(
        "manifest_completeness",
        set(manifest["artifacts"]) == observed_artifacts,
        "all deterministic inputs and outputs included",
    )
    for relative, digest in manifest["sources"].items():
        audit.check("source_hashes", sha(REPO / relative) == digest, relative)
    for relative, digest in manifest["artifacts"].items():
        audit.check("artifact_hashes", sha(bundle / relative) == digest, relative)
    for path in sorted(bundle.rglob("*")):
        if path.suffix == ".json":
            read_json(path)
            audit.check("finite_json", True, str(path.relative_to(bundle)))
        if path.suffix == ".csv":
            audit.check(
                "csv_lf", b"\r" not in path.read_bytes(), str(path.relative_to(bundle))
            )
    references = {}
    for scenario in protocol["scenarios"]:
        rows = read_csv(bundle / "inputs" / f"{scenario}.csv")
        audit.check(
            "dgp_shape",
            len(rows) == protocol["entities"] * len(protocol["periods"]),
            scenario,
        )
        for row in rows:
            audit.near(
                "dgp_identity",
                float(row["outcome"]),
                float(row["untreated_outcome"]) + float(row["true_effect"]),
                scenario,
            )
            cohort, period = int(row["cohort"]), int(row["period"])
            expected_exposure = int(cohort > 0 and period >= cohort)
            audit.check(
                "dgp_exposure",
                int(row["exposure"]) == expected_exposure,
                f"{scenario}/{row['entity']}/{period}",
            )
            spec = protocol["scenarios"][scenario]
            if scenario == "heterogeneous_staggered":
                expected_effect = (
                    (
                        spec["effect_at_cohort"][str(cohort)]
                        + spec["effect_growth"][str(cohort)] * (period - cohort)
                    )
                    if expected_exposure
                    else 0.0
                )
            elif scenario == "triple_difference":
                expected_effect = expected_exposure * (
                    spec["effect"] + spec["subgroup_effect"] * int(row["subgroup"])
                )
            else:
                expected_effect = expected_exposure * spec["effect"]
            audit.near(
                "dgp_known_effect",
                float(row["true_effect"]),
                expected_effect,
                f"{scenario}/{row['entity']}/{period}",
            )
        units = panel_units(rows)
        if scenario in protocol["monte_carlo_scenarios"]:
            reference = reference_fit(
                units, protocol["pre_periods"], protocol["post_periods"]
            )
            compare_fit(audit, scenario, outputs[scenario]["traditional"], reference)
            event = event_reference(units, protocol["periods"], 3, 4)
            actual = outputs[scenario]["event_study"]
            audit.check(
                "event_periods",
                actual["covariance_periods"] == event["periods"],
                scenario,
            )
            for effect, expected in zip(actual["effects"], event["fits"]):
                compare_fit(
                    audit, f"{scenario}/event/{effect['period']}", effect, expected
                )
            audit.near(
                "event_joint_covariance",
                actual["covariance"],
                event["covariance"],
                scenario,
            )
            for key in ["wald", "f_statistic", "p_value"]:
                audit.near(
                    "pretrend_joint",
                    actual["joint_pretrend"][key],
                    event[key],
                    f"{scenario}/{key}",
                )
            references[scenario] = {
                key: reference[key]
                for key in ["estimate", "se", "ci_low", "ci_high", "p_value"]
            }
            references[scenario]["joint_pretrend_p_value"] = event["p_value"]
        if scenario == "triple_difference":
            reference = reference_fit(
                units, protocol["pre_periods"], protocol["post_periods"], ddd=True
            )
            compare_fit(audit, scenario, outputs[scenario], reference)
            references[scenario] = {
                key: reference[key]
                for key in ["estimate", "se", "ci_low", "ci_high", "p_value"]
            }
        if scenario == "heterogeneous_staggered":
            for control in ["never_treated", "not_yet_treated"]:
                ref = staggered_reference(units, protocol["periods"], control)
                actual = outputs[f"staggered_{control}"]
                audit.near(
                    "staggered_joint_covariance",
                    actual["cell_covariance"],
                    ref["covariance"],
                    control,
                )
                for cell, expected in zip(actual["cells"], ref["cells"]):
                    audit.check(
                        "staggered_cells",
                        (cell["cohort"], cell["period"])
                        == (expected["cohort"], expected["period"]),
                        control,
                    )
                    compare_fit(
                        audit,
                        f"staggered/{control}/{cell['cohort']}/{cell['period']}",
                        {"estimate": cell["estimate"], **cell["inference"]},
                        expected,
                    )
                audit.near(
                    "staggered_aggregate",
                    actual["aggregate"]["estimate"],
                    ref["aggregate"]["estimate"],
                    control,
                )
                audit.near(
                    "staggered_aggregate",
                    actual["aggregate"]["inference"]["se"] ** 2,
                    ref["aggregate"]["variance"],
                    control,
                )
                for dynamic, expected in zip(
                    actual["dynamic_effects"], ref["dynamics"]
                ):
                    compare_fit(
                        audit,
                        f"staggered/{control}/dynamic/{expected['event_time']}",
                        dynamic,
                        expected,
                    )
                    audit.near(
                        "staggered_dynamic",
                        dynamic["estimate"],
                        expected["estimate"],
                        f"{control}/{expected['event_time']}",
                    )
                    audit.near(
                        "staggered_dynamic",
                        dynamic["inference"]["se"] ** 2,
                        expected["variance"],
                        f"{control}/{expected['event_time']}",
                    )
                compare_fit(
                    audit,
                    f"staggered/{control}/aggregate",
                    actual["aggregate"],
                    ref["aggregate"],
                )
                references[f"staggered_{control}"] = {
                    "aggregate": ref["aggregate"],
                    "dynamics": ref["dynamics"],
                    "cells": ref["cells"],
                }
    units = panel_units(read_csv(bundle / "inputs/constant_effect.csv"))
    placebo = protocol["placebo"]
    compare_fit(
        audit,
        "fake_time",
        outputs["fake_time"],
        reference_fit(units, placebo["pre_periods"], placebo["post_periods"]),
    )
    never = [
        {**u, "group": int(u["entity"] in range(24, 36))}
        for u in units
        if u["cohort"] == 0
    ]
    compare_fit(
        audit,
        "fake_group",
        outputs["fake_group"],
        reference_fit(never, protocol["pre_periods"], protocol["post_periods"]),
    )
    distribution = outputs["random_group_distribution"]
    rng = random.Random(placebo["random_seed"])
    pipeline = outputs["pipeline"]
    expected_order = [
        "traditional_did",
        "event_study",
        "placebo_time",
        "placebo_group",
        "placebo_distribution",
        "triple_difference",
    ]
    audit.check(
        "pipeline_order",
        pipeline["execution_order"] == expected_order,
        "frozen analysis stages",
    )
    for index, entry in enumerate(
        [
            outputs["constant_effect"]["traditional"],
            outputs["constant_effect"]["event_study"],
            outputs["fake_time"],
            outputs["fake_group"],
            outputs["random_group_distribution"],
        ]
    ):
        audit.check(
            "pipeline_consistency",
            pipeline["results"][index] == entry,
            expected_order[index],
        )
        audit.check(
            "pipeline_estimated_status",
            pipeline["results"][index]["status"] == "estimated",
            expected_order[index],
        )
    pipeline_manifest = read_json(bundle / "results/pipeline/manifest.json")
    for name, record in pipeline_manifest["outputs"].items():
        audit.check(
            "skill_output_hashes",
            sha(bundle / "results/pipeline" / name) == record["sha256"],
            name,
        )
    audit.check(
        "skill_output_json",
        read_json(bundle / "results/pipeline/results.json") == pipeline,
        "actual output writer",
    )
    pipeline_csv = read_csv(bundle / "results/pipeline/estimates.csv")
    exported_draws = [
        row for row in pipeline_csv if row.get("role") == "fake_group_draw"
    ]
    exported_reference = [
        row for row in pipeline_csv if row.get("role") == "real_treatment_reference"
    ]
    audit.check(
        "placebo_csv_roles",
        len(exported_reference) == 1 and len(exported_draws) == placebo["random_draws"],
        "real comparison and sampled fake effects have distinct roles",
    )
    audit.near(
        "placebo_csv_reference",
        float(exported_reference[0]["estimate"]),
        distribution["real_estimate"],
        "real treatment reference",
    )
    for row, draw in zip(exported_draws, distribution["distribution"]):
        audit.near(
            "placebo_csv_draws", float(row["estimate"]), draw["estimate"], row["draw"]
        )
        audit.check(
            "placebo_csv_draws",
            int(row["draw"]) == draw["draw"]
            and all(row[key] == "" for key in ["se", "ci_low", "ci_high", "p_value"]),
            row["draw"],
        )
        audit.check(
            "placebo_csv_draws",
            json.loads(row["fake_treated_clusters"]) == draw["fake_treated_clusters"]
            and int(row["seed"]) == placebo["random_seed"],
            row["draw"],
        )
    if read_json(bundle / "results/environment.json")["plots_requested"]:
        for folder, result_key in [
            ("pipeline", "pipeline"),
            ("triple-difference", "triple_difference"),
            ("staggered-never", "staggered_never_treated"),
        ]:
            graphic = read_json(bundle / "results" / folder / "visualization.json")
            canonical = json.dumps(
                outputs[result_key], sort_keys=True, ensure_ascii=False, allow_nan=False
            )
            audit.check(
                "plot_result_binding",
                graphic["result_sha256"]
                == hashlib.sha256(canonical.encode()).hexdigest(),
                folder,
            )
            for name, record in graphic["outputs"].items():
                audit.check(
                    "plot_hashes",
                    sha(bundle / "results" / folder / name) == record["sha256"],
                    f"{folder}/{name}",
                )
            if folder == "pipeline":
                event_panel = next(
                    panel
                    for panel in graphic["panels"]
                    if panel["kind"] == "event_study"
                )
                audit.check(
                    "plot_event_dates",
                    event_panel["reference_event_time"] == -1
                    and event_panel["adoption_event_time"] == 0,
                    folder,
                )
                draw_panel = next(
                    panel
                    for panel in graphic["panels"]
                    if panel["kind"] == "placebo_distribution"
                )
                audit.check(
                    "plot_distribution",
                    draw_panel["draws"] == 99
                    and draw_panel["seed"] == placebo["random_seed"],
                    folder,
                )
                audit.near(
                    "plot_distribution",
                    draw_panel["real_reference"],
                    distribution["real_estimate"],
                    folder,
                )
            if folder == "staggered-never":
                panel = next(
                    panel
                    for panel in graphic["panels"]
                    if panel["kind"] == "staggered_did"
                )
                expected_support = [
                    {
                        key: row[key]
                        for key in [
                            "event_time",
                            "supported_cohorts",
                            "cell_indices",
                            "weights",
                        ]
                    }
                    for row in outputs[result_key]["dynamic_effects"]
                ]
                audit.check(
                    "plot_dynamic_support",
                    panel["dynamic_support"] == expected_support,
                    folder,
                )
    refused = outputs["pipeline_with_refusals"]
    audit.check(
        "refusal_retention",
        refused["execution_order"]
        == [
            "traditional_did",
            "placebo_time",
            "placebo_group",
            "placebo_distribution",
            "triple_difference",
        ],
        "all requested stages retained",
    )
    for index in [1, 2]:
        audit.check(
            "refusal_retention",
            refused["results"][index]["status"] == "refused"
            and bool(refused["results"][index]["reason"]),
            f"refused stage {index}",
        )
    for index in [0, 3, 4]:
        audit.check(
            "refusal_continuation",
            refused["results"][index]["status"] == "estimated",
            f"estimated stage {index}",
        )
    refusal_manifest = read_json(bundle / "results/refusals/manifest.json")
    for name, record in refusal_manifest["outputs"].items():
        audit.check(
            "skill_output_hashes",
            sha(bundle / "results/refusals" / name) == record["sha256"],
            f"refusals/{name}",
        )
    refusal_rows = read_csv(bundle / "results/refusals/estimates.csv")
    for row in refusal_rows:
        if row["status"] == "refused":
            audit.check(
                "refusal_export",
                bool(row["reason"]) and row["estimate"] == "",
                row["method"],
            )
    for draw in distribution["distribution"]:
        fake_clusters = {int(cluster) for cluster in draw["fake_treated_clusters"]}
        audit.check(
            "placebo_rng",
            fake_clusters
            == set(rng.sample(list(range(12, 24)), placebo["fake_cluster_count"])),
            str(draw["draw"]),
        )
        audit.check(
            "placebo_cluster_assignment",
            len(fake_clusters) == placebo["fake_cluster_count"]
            and fake_clusters.issubset(set(range(12, 24))),
            str(draw["draw"]),
        )
        fake_units = [{**u, "group": int(u["cluster"] in fake_clusters)} for u in never]
        expected = reference_fit(
            fake_units, protocol["pre_periods"], protocol["post_periods"]
        )
        audit.near(
            "placebo_draws", draw["estimate"], expected["estimate"], str(draw["draw"])
        )
    tail = sum(
        abs(draw["estimate"]) >= abs(distribution["real_estimate"])
        for draw in distribution["distribution"]
    ) / len(distribution["distribution"])
    audit.near(
        "descriptive_tail",
        distribution["descriptive_tail_fraction"],
        tail,
        "tail fraction, not a randomization p value",
    )
    mc_rows = read_csv(bundle / "results/monte_carlo.csv")
    wide_rows = read_csv(bundle / "inputs/monte_carlo_unit_outcomes.csv")
    by_run: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in wide_rows:
        by_run[(row["scenario"], int(row["seed"]))].append(
            {
                "entity": int(row["entity"]),
                "cluster": int(row["cluster"]),
                "group": int(row["group"]),
                "outcomes": {
                    p: float(row[f"outcome_{p}"]) for p in protocol["periods"]
                },
            }
        )
    audit.check(
        "mc_panel_count",
        len(by_run)
        == len(protocol["monte_carlo_scenarios"])
        * protocol["replicate_seeds"]["count"],
        "all prespecified panels saved",
    )
    for key, units in by_run.items():
        audit.check(
            "mc_entity_count",
            len(units) == protocol["entities"]
            and len({unit["entity"] for unit in units}) == protocol["entities"],
            str(key),
        )
    summaries = read_json(bundle / "results/monte_carlo_summary.json")["scenarios"]
    for row in mc_rows:
        units = by_run[(row["scenario"], int(row["seed"]))]
        reference = reference_fit(
            units, protocol["pre_periods"], protocol["post_periods"]
        )
        event = event_reference(units, protocol["periods"], 3, 4)
        for csv_field, reference_field in [
            ("estimate", "estimate"),
            ("standard_error", "se"),
            ("ci_low", "ci_low"),
            ("ci_high", "ci_high"),
        ]:
            audit.near(
                "monte_carlo_regression",
                float(row[csv_field]),
                reference[reference_field],
                f"{row['scenario']}/{row['seed']}/{csv_field}",
            )
        truth = float(row["truth"])
        for key, expected in [
            ("covers_truth", reference["ci_low"] <= truth <= reference["ci_high"]),
            ("rejects_zero", reference["p_value"] < 0.05),
            ("rejects_pretrend", event["p_value"] < 0.05),
        ]:
            audit.check(
                "monte_carlo_decisions",
                (row[key] == "True") == expected,
                f"{row['scenario']}/{row['seed']}/{key}",
            )
    for scenario in protocol["monte_carlo_scenarios"]:
        records = [row for row in mc_rows if row["scenario"] == scenario]
        audit.check(
            "mc_prespecified_seeds",
            sorted(int(row["seed"]) for row in records)
            == list(
                range(
                    protocol["replicate_seeds"]["start"],
                    protocol["replicate_seeds"]["start"]
                    + protocol["replicate_seeds"]["count"],
                )
            ),
            scenario,
        )
        expected = mc_summary(records)
        expected_bias_interval = [
            expected["bias"] - 1.959963984540054 * expected["bias_mcse"],
            expected["bias"] + 1.959963984540054 * expected["bias_mcse"],
        ]
        audit.near(
            "monte_carlo_summary",
            summaries[scenario]["bias_mc_95_interval"],
            expected_bias_interval,
            f"{scenario}/bias_mc_95_interval",
        )
        for key in ["replicates", "bias", "bias_mcse", "rmse", "mean_standard_error"]:
            audit.near(
                "monte_carlo_summary",
                summaries[scenario][key],
                expected[key],
                f"{scenario}/{key}",
            )
        for field in ["covers_truth", "rejects_zero", "rejects_pretrend"]:
            for key in ["count", "rate", "mcse", "wilson_mc_95_interval"]:
                audit.near(
                    "monte_carlo_summary",
                    summaries[scenario][field][key],
                    expected[field][key],
                    f"{scenario}/{field}/{key}",
                )
    if args.replay:
        with tempfile.TemporaryDirectory(prefix="openai4s-did-replay-") as directory:
            replay = Path(directory) / "bundle"
            command = [
                sys.executable,
                str(HERE / "run_experiment.py"),
                "--output",
                str(replay),
            ]
            if read_json(bundle / "results/environment.json")["plots_requested"]:
                command.append("--plots")
            subprocess.run(command, check=True)
            for relative in manifest["artifacts"]:
                audit.check(
                    "replay_bytes",
                    sha(bundle / relative) == sha(replay / relative),
                    relative,
                )
            audit.check(
                "replay_manifest",
                sha(bundle / "results/artifact_manifest.json")
                == sha(replay / "results/artifact_manifest.json"),
                "source and artifact digests",
            )
    result = {
        "schema_version": 1,
        "status": "pass" if not audit.failures else "fail",
        "tolerance": {"absolute": ATOL, "relative": RTOL},
        "comparison": "Independent statsmodels saturated entity-change OLS; CR1 cluster covariance; SciPy t and F tails; joint cluster influence cross-products",
        "statsmodels": sm.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "checks": dict(sorted(audit.counts.items())),
        "maximum_absolute_error": dict(sorted(audit.max_error.items())),
        "failures": audit.failures,
        "independent_references": references,
        "replay_requested": args.replay,
        "monte_carlo_rates_are_pass_gates": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, indent=2, allow_nan=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": sum(audit.counts.values()),
                "failures": len(audit.failures),
                "max_absolute_error": max(audit.max_error.values(), default=0.0),
            },
            allow_nan=False,
        )
    )
    if audit.failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
