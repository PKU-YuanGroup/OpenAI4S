"""Offline, bounded replications using the repository's actual panel skill."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def dump(value, path):
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def table(rows, path):
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_skill():
    path = REPO / "skills/panel-data-preprocessing/kernel.py"
    spec = importlib.util.spec_from_file_location("did_experiment_panel_skill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load panel skill")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources():
    sources = json.loads((ROOT / "inputs/sources.json").read_text())
    for record in sources["datasets"].values():
        for kind in ("binary", "csv"):
            path = ROOT / "inputs" / record[f"{kind}_filename"]
            if (
                hashlib.sha256(path.read_bytes()).hexdigest()
                != record[f"{kind}_sha256"]
            ):
                raise ValueError(f"Input checksum mismatch: {path.name}")
    return sources


def check_reference(actual, expected, tolerance, name):
    error = abs(actual - expected)
    return {
        "name": name,
        "actual": actual,
        "published_rounded": expected,
        "absolute_error": error,
        "rounding_tolerance": tolerance,
        "passed": error <= tolerance,
    }


def screen_summary(result):
    candidates = result["events"]["candidates"]
    return {
        "counts_by_kind": dict(Counter(c["kind"] for c in candidates)),
        "numeric_candidate_count": sum(
            c["kind"] == "level_shift_candidate" for c in candidates
        ),
        "numeric_candidates": [
            c for c in candidates if c["kind"] == "level_shift_candidate"
        ],
        "configuration": result["events"]["config"],
    }


def event_alignment(result, sensitivity, group_column, directory):
    """Compare dates descriptively; alignment is not a causal validation score."""
    entity = result["config"]["entity"]
    time = result["config"]["time"]
    metric = result["config"]["numeric_columns"][0]
    units = defaultdict(dict)
    for row in result["rows"]:
        units[row[entity]][int(row[time])] = row
    last = max(t for group in units.values() for t in group)
    cohorts = {
        u: int(next(iter(group.values()))[group_column]) for u, group in units.items()
    }
    records, summary = [], []
    for screen in sensitivity:
        window = screen["configuration"]["window"]
        eligible = {
            u
            for u, group in units.items()
            if 0 < cohorts[u] <= last
            and all(
                t in group and group[t][metric] is not None
                for t in range(cohorts[u] - window, cohorts[u] + window)
            )
        }
        exact_units = set()
        for candidate in screen["numeric_candidates"]:
            unit = candidate["entity"]
            g = cohorts[unit]
            treated = 0 < g <= last
            exact = treated and int(candidate["time"]) == g
            if exact:
                exact_units.add(unit)
            records.append(
                {
                    "window": window,
                    "entity": unit,
                    "candidate_time": candidate["time"],
                    "known_first_treatment": g if treated else None,
                    "date_matches_treatment": exact,
                    "change": candidate["change"],
                    "boundary_innovation": candidate["boundary_innovation"],
                }
            )
        summary.append(
            {
                "window": window,
                "numeric_candidates": screen["numeric_candidate_count"],
                "treated_units_with_full_windows_at_adoption": len(eligible),
                "candidate_dates_exactly_matching_adoption": len(exact_units),
                "interpretation": "Raw outcome can change for reasons other than treatment; date disagreement is not by itself a false positive.",
            }
        )
    if records:
        table(records, directory / "event_alignment.csv")
    dump(summary, directory / "event_alignment.json")
    return summary


def real_data(prep, directory, reference):
    raw = prep.read_csv(ROOT / "inputs/mpdta.csv")
    rows = []
    for row in raw:
        g, t = int(row["first.treat"]), int(row["year"])
        rows.append({**row, "exposed": int(g != 0 and t >= g)})
    # 'treat' is ever-treated membership in this source, not current exposure.
    events = [
        {
            "label": f"Minimum wage cohort {g}",
            "time": str(g),
            "source": reference["source"]
            + "#an-example-with-real-data; first.treat in pinned mpdta",
        }
        for g in (2004, 2006, 2007)
    ]
    options = {
        "entity": "countyreal",
        "time": "year",
        "frequency": "year",
        "numeric_columns": ["lemp"],
        "treatment": "exposed",
        "known_events": events,
        "threshold": 3.0,
        "min_abs_change": 0.05,
    }
    result = prep.preprocess_panel(rows, window=3, **options)
    if not result["analysis"]["audit"]["balanced_calendar_grid"]:
        raise ValueError("This replication requires a balanced calendar grid")
    prep.write_outputs(result, directory)
    units = defaultdict(dict)
    for row in result["rows"]:
        units[row["countyreal"]][int(row["year"])] = row
    groups = {
        unit: int(next(iter(series.values()))["first.treat"])
        for unit, series in units.items()
    }
    for unit, series in units.items():
        if {int(r["first.treat"]) for r in series.values()} != {groups[unit]}:
            raise ValueError("First treatment year must be constant within county")
    cohorts = sorted(set(groups.values()) - {0})
    counts = Counter(groups.values())
    controls = [u for u in units if groups[u] == 0]
    estimates = []
    for g in cohorts:
        treated = [u for u in units if groups[u] == g]
        for t in range(2004, 2008):
            # did defaults to varying base for pre-treatment pseudo-ATT,
            # and the last untreated period for post-treatment ATT.
            base = t - 1 if t < g else g - 1

            def changes(members):
                return [units[u][t]["lemp"] - units[u][base]["lemp"] for u in members]

            att = statistics.mean(changes(treated)) - statistics.mean(changes(controls))
            estimates.append(
                {
                    "group": g,
                    "time": t,
                    "event_time": t - g,
                    "base_period": base,
                    "att": att,
                    "n_treated": len(treated),
                    "n_never_treated": len(controls),
                    "status": "pre_period_pseudo_att" if t < g else "post_period_att",
                }
            )
    dynamic = []
    for e in sorted({a["event_time"] for a in estimates}):
        members = [a for a in estimates if a["event_time"] == e]
        total = sum(a["n_treated"] for a in members)
        dynamic.append(
            {
                "event_time": e,
                "att": sum(a["att"] * a["n_treated"] for a in members) / total,
                "n_treated_support": total,
                "cohorts": ";".join(str(a["group"]) for a in members),
            }
        )
    overall = statistics.mean(a["att"] for a in dynamic if a["event_time"] >= 0)
    checks = [
        check_reference(
            a["att"],
            reference["group_time"][f'{a["group"]}:{a["time"]}'],
            reference["tolerance"],
            f'cs:group={a["group"]}:time={a["time"]}',
        )
        for a in estimates
    ]
    checks += [
        check_reference(
            a["att"],
            reference["dynamic"][str(a["event_time"])],
            reference["tolerance"],
            f'cs:event={a["event_time"]}',
        )
        for a in dynamic
    ]
    checks.append(
        check_reference(
            overall,
            reference["overall_dynamic_att"],
            reference["tolerance"],
            "cs:overall_dynamic_att",
        )
    )
    table(estimates, directory / "group_time_att.csv")
    table(dynamic, directory / "dynamic_att.csv")
    subset = [sorted(u for u in units if groups[u] == g)[0] for g in [0, *cohorts]]
    prep.render_diagnostics(result, directory, entities=subset, metrics=["lemp"])
    sensitivity = [
        screen_summary(prep.preprocess_panel(rows, window=w, **options)) for w in (2, 3)
    ]
    dump(sensitivity, directory / "screening_sensitivity.json")
    alignment = event_alignment(result, sensitivity, "first.treat", directory)
    return result, {
        "data_type": "public real-data subset",
        "audit": result["analysis"]["audit"],
        "cohort_sizes": dict(counts),
        "overall_dynamic_att": overall,
        "checks": checks,
        "screening": screen_summary(result),
        "screening_sensitivity": [
            {
                "window": x["configuration"]["window"],
                "numeric_candidate_count": x["numeric_candidate_count"],
            }
            for x in sensitivity
        ],
        "event_alignment": alignment,
        "trajectory_subset_selection": "Smallest county ID in never-treated and each treatment cohort, selected independently of outcomes",
        "selected_entities": subset,
        "scope": reference["scope"],
        "limitations": [
            "Only point estimates reproduced. No bootstrap, confidence bands, clustering inference or causal policy conclusion.",
            "Original paper uses a different, larger empirical sample; this is its authors' worked example.",
            "Five periods cannot supply two three-period windows. Two-period screening is exploratory and noisy.",
            "Group/event-time supports vary; negative event times use varying pre-period bases, not a universal -1 normalization.",
        ],
    }


def within_ols(np, y, x, ids, times):
    """FWL balanced-panel projection; refuse unsupported missing/unbalanced data."""
    unique_ids, i = np.unique(ids, return_inverse=True)
    unique_times, t = np.unique(times, return_inverse=True)
    n_id, n_time = len(unique_ids), len(unique_times)
    if len(y) != n_id * n_time or len(set(zip(i.tolist(), t.tolist()))) != len(y):
        raise ValueError(
            "Within projection is valid here only for a complete balanced grid"
        )

    def demean(z):
        z = np.asarray(z, dtype=float)
        if z.ndim == 1:
            z = z[:, None]
        mean_id = np.zeros((n_id, z.shape[1]))
        mean_time = np.zeros((n_time, z.shape[1]))
        np.add.at(mean_id, i, z)
        np.add.at(mean_time, t, z)
        return z - mean_id[i] / n_time - mean_time[t] / n_id + z.mean(axis=0)

    yw = demean(y)[:, 0]
    xw = demean(x)
    coef, _, rank, _ = np.linalg.lstsq(xw, yw, rcond=None)
    if rank != x.shape[1]:
        raise ValueError("Event-study design is rank deficient")
    residual = yw - xw @ coef
    return coef, math.sqrt(float(residual @ residual) / len(y)), xw, yw


def simulation(prep, directory, reference, np):
    raw = prep.read_csv(ROOT / "inputs/base_stagg.csv")
    rows = []
    for row in raw:
        g, t = int(row["year_treated"]), int(row["year"])
        rows.append({**row, "exposed": int(g <= 10 and t >= g)})
    # Actual sentinel is 10000, despite some prose in fixest docs saying 1000.
    result = prep.preprocess_panel(
        rows,
        entity="id",
        time="year",
        frequency="integer",
        numeric_columns=["y"],
        treatment="exposed",
        window=3,
        threshold=3.0,
        min_abs_change=1.0,
    )
    prep.write_outputs(result, directory)
    data = result["rows"]
    ids = np.array([r["id"] for r in data])
    times = np.array([int(r["year"]) for r in data])
    cohorts = np.array([int(r["year_treated"]) for r in data])
    relative = np.array([int(r["time_to_treatment"]) for r in data])
    outcome = np.array([r["y"] for r in data])
    x1 = np.array([float(r["x1"]) for r in data])
    truth = np.array([float(r["treatment_effect_true"]) for r in data])
    active = cohorts <= times.max()
    if not np.array_equal(relative[active], times[active] - cohorts[active]):
        raise ValueError("Relative period inconsistent with cohort date")
    if not np.array_equal(
        np.array([int(r["treated"]) for r in data]), active.astype(int)
    ):
        raise ValueError("Unexpected definition of ever-treated membership")
    for unit in np.unique(ids):
        if len(np.unique(cohorts[ids == unit])) != 1:
            raise ValueError("Treatment cohort must be constant within unit")
    periods = sorted(set(relative[active].tolist()) - {-1})
    twfe_x = np.column_stack(
        [x1, *[(active & (relative == e)).astype(float) for e in periods]]
    )
    twfe_beta, twfe_rmse, _, _ = within_ols(np, outcome, twfe_x, ids, times)
    pairs = [
        (g, e)
        for g in sorted(set(cohorts[active].tolist()))
        for e in sorted(set(relative[cohorts == g].tolist()))
        if e != -1
    ]
    sa_x = np.column_stack(
        [x1, *[((cohorts == g) & (relative == e)).astype(float) for g, e in pairs]]
    )
    sa_beta, sa_rmse, _, _ = within_ols(np, outcome, sa_x, ids, times)
    cohort_rows = [
        {
            "cohort": g,
            "event_time": e,
            "att": float(sa_beta[k + 1]),
            "cell_observations": int(np.sum((cohorts == g) & (relative == e))),
        }
        for k, (g, e) in enumerate(pairs)
    ]
    estimates = []
    for k, e in enumerate(periods):
        members = [c for c in cohort_rows if c["event_time"] == e]
        total = sum(c["cell_observations"] for c in members)
        estimates.append(
            {
                "event_time": e,
                "twfe": float(twfe_beta[k + 1]),
                "sun_abraham": sum(c["att"] * c["cell_observations"] for c in members)
                / total,
                "true_effect": float(np.mean(truth[active & (relative == e)])),
                "treated_observations_support": total,
                "cohort_count": len(members),
            }
        )
    post = [c for c in cohort_rows if c["event_time"] >= 0]
    denominator = sum(c["cell_observations"] for c in post)
    overall = sum(c["att"] * c["cell_observations"] for c in post) / denominator
    true_overall = float(np.mean(truth[active & (relative >= 0)]))
    checks = [
        check_reference(
            float(sa_beta[0]), reference["x1"], reference["tolerance"], "sa:x1"
        ),
        check_reference(
            overall, reference["overall_att"], reference["tolerance"], "sa:overall_att"
        ),
        check_reference(sa_rmse, reference["rmse"], reference["tolerance"], "sa:rmse"),
        check_reference(
            true_overall, reference["true_overall_att"], 1e-12, "sa:true_overall_att"
        ),
    ]

    def rmse(name):
        return math.sqrt(
            statistics.mean((a[name] - a["true_effect"]) ** 2 for a in estimates)
        )

    table(cohort_rows, directory / "cohort_event_att.csv")
    table(estimates, directory / "dynamic_comparison.csv")
    # Known simulation adoption dates are retained per selected entity.
    subset = [sorted(set(ids[cohorts == g].tolist()))[0] for g in [10000, 2, 5, 8]]
    prep.render_diagnostics(result, directory, entities=subset, metrics=["y"])
    sensitivity = [
        screen_summary(
            prep.preprocess_panel(
                rows,
                entity="id",
                time="year",
                frequency="integer",
                numeric_columns=["y"],
                treatment="exposed",
                window=w,
                threshold=3.0,
                min_abs_change=1.0,
            )
        )
        for w in (2, 3)
    ]
    dump(sensitivity, directory / "screening_sensitivity.json")
    alignment = event_alignment(result, sensitivity, "year_treated", directory)
    candidates = screen_summary(result)["numeric_candidates"]
    review_ids = list(dict.fromkeys(c["entity"] for c in candidates))[:4]
    if review_ids:
        prep.render_diagnostics(
            result, directory / "candidate-review", entities=review_ids, metrics=["y"]
        )
        dump(
            {
                "selection": "First four distinct entities in chronologically sorted numeric candidates; exploratory, outcome-selected sample",
                "selected_entities": review_ids,
            },
            directory / "candidate-review/selection.json",
        )
    return result, {
        "data_type": "public fixed simulation draw; no newly generated data",
        "audit": result["analysis"]["audit"],
        "checks": checks,
        "overall_att": overall,
        "true_overall_att": true_overall,
        "x1": float(sa_beta[0]),
        "regression_rmse": sa_rmse,
        "effect_curve_rmse": {"twfe": rmse("twfe"), "sun_abraham": rmse("sun_abraham")},
        "scope": reference["scope"],
        "screening": screen_summary(result),
        "screening_sensitivity": [
            {
                "window": x["configuration"]["window"],
                "numeric_candidate_count": x["numeric_candidate_count"],
            }
            for x in sensitivity
        ],
        "event_alignment": alignment,
        "selected_entities": subset,
        "trajectory_subset_selection": "Smallest string ID in never-treated and cohorts 2, 5, 8, independent of outcomes",
        "normalization": "Never-treated controls; event time -1 omitted; all other observed event times included. Cohort-specific interactions aggregated by supported cell sample shares.",
        "limitations": [
            "One fixed simulation draw, not a Monte Carlo accuracy claim or the paper's empirical application.",
            "Only point estimates and residual RMSE reproduced; no confidence intervals or significance conclusions.",
            "Effect-curve RMSE weights each nonreference event time equally; overall ATT weights treated post-period observations.",
            "Numeric changes in raw y include x1 and time effects; visual candidates need not equal treatment dates.",
        ],
    }


def plot_comparison(root):
    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError("Experiment figures require matplotlib") from exc
    with (root / "sun-abraham/dynamic_comparison.csv").open() as handle:
        sim = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(handle)]
    with (root / "callaway-santanna/dynamic_att.csv").open() as handle:
        real = [
            {k: float(r[k]) for k in ("event_time", "att")}
            for r in csv.DictReader(handle)
        ]
    figure = Figure(figsize=(12, 4.7), layout="constrained")
    left, right = figure.subplots(1, 2)
    left.axhline(0, color="#999", linewidth=0.8)
    left.axvline(-0.5, color="#999", linestyle="--", linewidth=0.8)
    left.plot(
        [r["event_time"] for r in real], [r["att"] for r in real], "o-", color="#216781"
    )
    left.set(
        title="Real data: C&S dynamic estimates",
        xlabel="Event time (years)",
        ylabel="ATT in log employment",
    )
    left.text(
        0.02,
        0.04,
        "Pre-period estimates use varying bases.\nCohort support changes with event time.",
        transform=left.transAxes,
        fontsize=8,
    )
    for key, label, color, marker in [
        ("true_effect", "Known simulation truth", "#333333", "s"),
        ("twfe", "TWFE event study", "#bb5774", "o"),
        ("sun_abraham", "Sun-Abraham IW", "#216781", "^"),
    ]:
        right.plot(
            [r["event_time"] for r in sim],
            [r[key] for r in sim],
            marker=marker,
            color=color,
            label=label,
            linewidth=1.2,
            markersize=4,
        )
    right.axhline(0, color="#999", linewidth=0.8)
    right.axvline(-0.5, color="#999", linestyle="--", linewidth=0.8)
    right.set(
        title="Fixed simulation: staggered treatment",
        xlabel="Event time (periods)",
        ylabel="Effect on y",
    )
    right.legend(fontsize=8)
    figure.suptitle(
        "DiD worked-example replications | point estimates only, no inference"
    )
    FigureCanvasAgg(figure)
    figure.savefig(root / "comparison.png", dpi=180)
    with matplotlib.rc_context({"svg.hashsalt": "openai4s-did-replication"}):
        figure.savefig(root / "comparison.svg", metadata={"Date": None})


def canonicalize_svgs(root):
    """Meet the repository whitespace gate without changing figure content."""
    for path in root.rglob("*.svg"):
        path.write_text(
            "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n",
            encoding="utf-8",
        )
    # The renderer's initial hashes precede this explicit formatting step.
    # Preserve its selections/configuration and record hashes of final files.
    for path in root.rglob("visualization.json"):
        record = json.loads(path.read_text())
        for name, entry in record["outputs"].items():
            entry["sha256"] = hashlib.sha256(
                (path.parent / name).read_bytes()
            ).hexdigest()
        record["postprocessing"] = (
            "SVG line trailing whitespace removed to satisfy repository formatting gate; figure geometry and plotted data unchanged"
        )
        dump(record, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    try:
        import matplotlib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Use a scientific Python environment with numpy and matplotlib"
        ) from exc
    output = args.output.resolve()
    matplotlib.rcParams["svg.hashsalt"] = "openai4s-did-replication"
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            "Choose a new empty output directory; runs never overwrite results"
        )
    sources = check_sources()
    prep, skill_hash = load_skill()
    references = json.loads((ROOT / "reference_values.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    for name in ("callaway-santanna", "sun-abraham"):
        (output / name).mkdir()
    _, real = real_data(
        prep, output / "callaway-santanna", references["callaway_santanna"]
    )
    _, sim = simulation(prep, output / "sun-abraham", references["sun_abraham"], np)
    checks = real["checks"] + sim["checks"]
    result = {
        "replication_scope": "Two bounded official worked-example replications, not full-paper empirical replications",
        "checks_passed": sum(c["passed"] for c in checks),
        "checks_total": len(checks),
        "callaway_santanna": real,
        "sun_abraham": sim,
    }
    dump(result, output / "results.json")
    plot_comparison(output)
    canonicalize_svgs(output)
    dump(
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "skill_kernel_sha256": skill_hash,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "reference_values_sha256": hashlib.sha256(
                (ROOT / "reference_values.json").read_bytes()
            ).hexdigest(),
            "input_csv_sha256": {
                k: v["csv_sha256"] for k, v in sources["datasets"].items()
            },
            "network_during_analysis": False,
            "random_seed": None,
            "randomness": "None; analysis uses two fixed public input files",
            "point_estimate_checks": "Published decimal rounding intervals; no inference reproduction claimed",
        },
        output / "environment.json",
    )
    print(
        json.dumps(
            {
                "checks_passed": result["checks_passed"],
                "checks_total": len(checks),
                "cs_overall_dynamic_att": real["overall_dynamic_att"],
                "sa_att": sim["overall_att"],
                "sa_true_att": sim["true_overall_att"],
                "effect_curve_rmse": sim["effect_curve_rmse"],
            },
            indent=2,
        )
    )
    if not all(c["passed"] for c in checks):
        raise RuntimeError("Published reference check failed; inspect results.json")


if __name__ == "__main__":
    main()
