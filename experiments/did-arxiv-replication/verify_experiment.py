"""Independently verify estimation and rerun all saved artifacts offline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_estimation(results):
    try:
        import numpy as np
        import statsmodels.api as sm
    except ImportError as exc:
        raise RuntimeError(
            "Independent verification needs numpy and statsmodels"
        ) from exc
    raw = read(ROOT / "inputs/base_stagg.csv")
    ids = np.array([r["id"] for r in raw])
    times = np.array([int(r["year"]) for r in raw])
    cohorts = np.array([int(r["year_treated"]) for r in raw])
    relative = times - cohorts
    cells = read(results / "sun-abraham/cohort_event_att.csv")
    # Explicit least-squares dummy-variable model, independently of the
    # runner's two-way demeaning/FWL implementation.
    x = np.column_stack(
        [
            np.ones(len(raw)),
            *[(ids == i).astype(float) for i in np.unique(ids)[1:]],
            *[(times == t).astype(float) for t in np.unique(times)[1:]],
            np.array([float(r["x1"]) for r in raw]),
            *[
                (
                    (cohorts == int(c["cohort"])) & (relative == int(c["event_time"]))
                ).astype(float)
                for c in cells
            ],
        ]
    )
    y = np.array([float(r["y"]) for r in raw])
    fit = sm.OLS(y, x).fit()
    target = np.array([float(c["att"]) for c in cells])
    max_error = float(np.max(np.abs(fit.params[-len(cells) :] - target)))
    real = read(ROOT / "inputs/mpdta.csv")
    units = {}
    for row in real:
        units.setdefault(row["countyreal"], {})[int(row["year"])] = row
    errors = []
    for cell in read(results / "callaway-santanna/group_time_att.csv"):
        g, t, base = (int(cell[k]) for k in ("group", "time", "base_period"))
        selected = [
            group
            for group in units.values()
            if int(next(iter(group.values()))["first.treat"]) in (0, g)
        ]
        treatment = np.array(
            [int(next(iter(group.values()))["first.treat"]) == g for group in selected],
            dtype=float,
        )
        difference = np.array(
            [float(group[t]["lemp"]) - float(group[base]["lemp"]) for group in selected]
        )
        coefficient = (
            sm.OLS(difference, np.column_stack([np.ones(len(selected)), treatment]))
            .fit()
            .params[1]
        )
        errors.append(abs(float(coefficient) - float(cell["att"])))
    return {
        "sun_abraham_explicit_fe_vs_within_projection_max_coefficient_error": max_error,
        "sun_abraham_coefficients_checked": len(cells),
        "callaway_santanna_change_regression_vs_difference_in_means_max_error": max(
            errors
        ),
        "callaway_santanna_cells_checked": len(errors),
        "tolerance": 1e-9,
        "passed": max(max_error, max(errors)) <= 1e-9,
        "statsmodels_version": sm.__version__,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    results = args.results.resolve()
    original = json.loads((results / "results.json").read_text())
    environment = json.loads((results / "environment.json").read_text())
    if environment["runner_sha256"] != sha(ROOT / "run_experiment.py"):
        raise ValueError("Saved results came from a different runner version")
    if environment["skill_kernel_sha256"] != sha(
        ROOT.parents[1] / "skills/panel-data-preprocessing/kernel.py"
    ):
        raise ValueError("Saved results came from a different skill version")
    independent = independent_estimation(results)
    rerun = Path(tempfile.mkdtemp(prefix="openai4s-did-rerun-")) / "results"
    env = {
        **os.environ,
        "MPLCONFIGDIR": os.environ.get(
            "MPLCONFIGDIR", str(rerun.parent / "matplotlib")
        ),
    }
    subprocess.run(
        [sys.executable, str(ROOT / "run_experiment.py"), "--output", str(rerun)],
        env=env,
        check=True,
        timeout=180,
    )

    def eligible(path):
        return (
            path.suffix in {".json", ".csv", ".png", ".svg"}
            and path.name != "verification.json"
        )

    original_files = {
        p.relative_to(results): p
        for p in results.rglob("*")
        if p.is_file() and eligible(p)
    }
    rerun_files = {
        p.relative_to(rerun): p for p in rerun.rglob("*") if p.is_file() and eligible(p)
    }
    mismatches = [
        str(p)
        for p in sorted(set(original_files) | set(rerun_files))
        if p not in original_files
        or p not in rerun_files
        or sha(original_files[p]) != sha(rerun_files[p])
    ]
    source = json.loads((ROOT / "inputs/sources.json").read_text())
    input_checks = [
        sha(ROOT / "inputs" / r[f"{kind}_filename"]) == r[f"{kind}_sha256"]
        for r in source["datasets"].values()
        for kind in ("binary", "csv")
    ]
    report = {
        "verdict": (
            "REPRODUCIBLE"
            if independent["passed"] and not mismatches and all(input_checks)
            else "MISMATCH"
        ),
        "classification": "Deterministic fixed-data analysis in the recorded environment; exact byte comparison, no timing comparison",
        "published_reference_checks": {
            "passed": original["checks_passed"],
            "total": original["checks_total"],
        },
        "independent_estimation": independent,
        "input_checksum_checks_passed": sum(input_checks),
        "input_checksum_checks_total": len(input_checks),
        "rerun_files_checked": len(original_files),
        "mismatched_artifacts": mismatches,
        "rerun_output": str(rerun),
        "scope": "Reproducibility of our bounded worked-example replications, not verification of the entire papers or inferential results",
    }
    (results / "verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if report["verdict"] != "REPRODUCIBLE":
        raise RuntimeError("Verification failed")


if __name__ == "__main__":
    main()
