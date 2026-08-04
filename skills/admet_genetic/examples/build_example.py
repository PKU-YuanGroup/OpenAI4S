#!/usr/bin/env python
"""Rebuild the committed ADMET optimization example from recorded results.

This script does not run the GA or ADMET-AI. It validates the recorded CSV
files, rebuilds the self-contained dashboard, and writes an audit report.

    python skills/admet_genetic/examples/build_example.py
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from admet_genetic.kernel import render_optimization_history  # noqa: E402

SOURCE_FILES = (
    "seed_molecules.csv",
    "candidates_final.csv",
    "generation_log.csv",
    "generation_summary.csv",
    "config.yaml",
    "optimization_dashboard.html",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _markdown(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def load_example(root: Path = HERE) -> dict[str, Any]:
    missing = [name for name in SOURCE_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"example source files missing: {missing}")
    with (root / "config.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping")
    return {
        "seeds": _read_csv(root / "seed_molecules.csv"),
        "finals": _read_csv(root / "candidates_final.csv"),
        "log": _read_csv(root / "generation_log.csv"),
        "summary": _read_csv(root / "generation_summary.csv"),
        "config": config,
    }


def validate_example(data: dict[str, Any]) -> None:
    rows = data["log"]
    summaries = data["summary"]
    if not rows:
        raise ValueError("generation_log.csv is empty")
    if len(data["seeds"]) != sum(row["operation"] == "seed" for row in rows):
        raise ValueError("seed_molecules.csv does not match generation-0 seed records")

    for row in rows:
        expected_pass = _passes_configured_filters(row, data["config"]["filters"])
        if _as_bool(row["passes_filters"]) != expected_pass:
            raise ValueError(
                f"{row['molecule_id']} passes_filters does not match config.yaml"
            )
        expected_score = _composite_score(row, data["config"])
        if not math.isclose(
            float(row["total_score"]),
            expected_score,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                f"{row['molecule_id']} total_score does not match config.yaml"
            )

    by_generation: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        by_generation.setdefault(int(row["generation"]), []).append(row)

    observed = sorted(generation for generation in by_generation if generation > 0)
    declared = sorted(int(row["generation"]) for row in summaries)
    if observed != declared:
        raise ValueError(
            f"generation_summary.csv generations {declared} do not match log {observed}"
        )

    cumulative_best = max(float(row["total_score"]) for row in by_generation[0])
    for summary in sorted(summaries, key=lambda row: int(row["generation"])):
        generation = int(summary["generation"])
        records = by_generation[generation]
        scores = [float(row["total_score"]) for row in records]
        expected = {
            "generated": len(records),
            "best_score": max(scores),
            "mean_score": sum(scores) / len(scores),
            "pass_count": sum(_as_bool(row["passes_filters"]) for row in records),
        }
        cumulative_best = max(cumulative_best, expected["best_score"])
        expected["population_best"] = cumulative_best
        for key, value in expected.items():
            actual = float(summary[key])
            if not math.isclose(actual, float(value), rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(
                    f"generation {generation} {key}: summary={actual}, log={value}"
                )


def _passes_configured_filters(row: dict[str, str], filters: dict[str, Any]) -> bool:
    minimums = {"MW": "mw_min", "LogP": "logp_min", "TPSA": "tpsa_min"}
    maximums = {
        "MW": "mw_max",
        "LogP": "logp_max",
        "TPSA": "tpsa_max",
        "HBD": "hbd_max",
        "HBA": "hba_max",
        "RotatableBonds": "rotb_max",
        "sa_score": "sa_score_max",
    }
    for column, key in minimums.items():
        if key in filters and float(row[column]) < float(filters[key]):
            return False
    for column, key in maximums.items():
        if key in filters and float(row[column]) > float(filters[key]):
            return False
    if "qed_min" in filters and float(row["qed"]) < float(filters["qed_min"]):
        return False
    risk_count = len([flag for flag in row["admet_risk_flags"].split(";") if flag])
    if "risk_flags_max" in filters and risk_count > int(filters["risk_flags_max"]):
        return False
    return True


def _composite_score(row: dict[str, str], config: dict[str, Any]) -> float:
    filters = config["filters"]
    scoring = config["scoring"]
    window_checks = [
        float(filters["mw_min"]) <= float(row["MW"]) <= float(filters["mw_max"]),
        float(filters["logp_min"]) <= float(row["LogP"]) <= float(filters["logp_max"]),
        float(filters["tpsa_min"]) <= float(row["TPSA"]) <= float(filters["tpsa_max"]),
        float(row["HBD"]) <= float(filters["hbd_max"]),
        float(row["HBA"]) <= float(filters["hba_max"]),
        float(row["RotatableBonds"]) <= float(filters["rotb_max"]),
    ]
    property_component = sum(window_checks) / len(window_checks)
    sa_component = max(0.0, min(1.0, (10.0 - float(row["sa_score"])) / 9.0))
    return (
        float(scoring["admet_weight"]) * float(row["admet_score"])
        + float(scoring["qed_weight"]) * float(row["qed"])
        + float(scoring["sa_weight"]) * sa_component
        + float(scoring["property_weight"]) * property_component
    )


def select_final_candidates(
    rows: list[dict[str, str]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Select passing children that improve on their best ancestral seed."""
    by_id = {row["molecule_id"]: row for row in rows}
    filters = config.get("filters", {})

    @lru_cache(maxsize=None)
    def ancestral_seeds(molecule_id: str) -> tuple[str, ...]:
        row = by_id[molecule_id]
        if row["operation"] == "seed":
            return (molecule_id,)
        parent_ids = [row["parent"]] if row["parent"] else []
        if row["parents"]:
            parent_ids.extend(item for item in row["parents"].split(";") if item)
        seeds: list[str] = []
        for parent_id in parent_ids:
            if parent_id not in by_id:
                raise ValueError(f"{molecule_id} references unknown parent {parent_id}")
            seeds.extend(ancestral_seeds(parent_id))
        return tuple(dict.fromkeys(seeds))

    qualifying = []
    for row in rows:
        if row["operation"] == "seed":
            continue
        if not _as_bool(row["passes_filters"]) or _as_bool(row["admet_failed"]):
            continue
        if not _passes_configured_filters(row, filters):
            continue
        seed_ids = ancestral_seeds(row["molecule_id"])
        if not seed_ids:
            raise ValueError(f"{row['molecule_id']} has no traceable seed ancestor")
        baseline = max(float(by_id[seed_id]["total_score"]) for seed_id in seed_ids)
        score = float(row["total_score"])
        if score <= baseline:
            continue
        candidate = dict(row)
        candidate.update(
            {
                "baseline_seed_ids": ";".join(seed_ids),
                "baseline_total_score": baseline,
                "delta_total_vs_baseline": score - baseline,
            }
        )
        qualifying.append(candidate)

    selected = []
    represented_lineages = set()
    for candidate in sorted(
        qualifying, key=lambda row: float(row["total_score"]), reverse=True
    ):
        lineage = candidate["baseline_seed_ids"]
        if lineage in represented_lineages:
            continue
        represented_lineages.add(lineage)
        selected.append(candidate)
    return selected


def validate_final_candidates(data: dict[str, Any]) -> None:
    expected = select_final_candidates(data["log"], data["config"])
    actual = data["finals"]
    expected_ids = [row["molecule_id"] for row in expected]
    actual_ids = [row["molecule_id"] for row in actual]
    if actual_ids != expected_ids:
        raise ValueError(
            f"candidates_final.csv IDs {actual_ids} do not match selection {expected_ids}"
        )
    for expected_row, actual_row in zip(expected, actual):
        for key in (
            "baseline_seed_ids",
            "baseline_total_score",
            "delta_total_vs_baseline",
        ):
            if key not in actual_row:
                raise ValueError(f"candidates_final.csv is missing {key}")
            if key == "baseline_seed_ids":
                if actual_row[key] != expected_row[key]:
                    raise ValueError(
                        f"{actual_row['molecule_id']} has inconsistent {key}"
                    )
            elif not math.isclose(
                float(actual_row[key]),
                float(expected_row[key]),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{actual_row['molecule_id']} has inconsistent {key}")


def write_final_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        raise ValueError("selection produced no final candidates")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(candidates[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(candidates)


def build_report(data: dict[str, Any]) -> str:
    seeds = data["seeds"]
    finals = data["finals"]
    rows = data["log"]
    summaries = sorted(data["summary"], key=lambda row: int(row["generation"]))
    config = data["config"]
    operations = Counter(row["operation"] for row in rows)
    pass_count = sum(_as_bool(row["passes_filters"]) for row in rows)
    seed_rows = [row for row in rows if int(row["generation"]) == 0]
    child_rows = [row for row in rows if int(row["generation"]) > 0]
    best_seed = max(seed_rows, key=lambda row: float(row["total_score"]))
    best_child = max(child_rows, key=lambda row: float(row["total_score"]))
    risks = Counter(
        flag for row in finals for flag in row["admet_risk_flags"].split(";") if flag
    )

    lines = [
        "# ADMET Genetic Optimization Example Report",
        "",
        "## Run overview",
        "",
        "This report was reconstructed from committed run artifacts. The build "
        "script does not rerun the genetic algorithm or ADMET-AI.",
        "",
        f"- Input seeds: {len(seeds)}",
        f"- Evaluated generation-0 seeds: {len(seed_rows)}",
        "- Invalid and deduplicated input counts: not captured",
        f"- Recorded optimization generations: {len(summaries)}",
        f"- Generated molecules per generation: {summaries[0]['generated']}",
        f"- Total generation-log records: {len(rows)}",
        f"- Mutation records: {operations['mutation']}",
        f"- Crossover records: {operations['crossover']}",
        f"- Records passing filters: {pass_count}",
        f"- Final candidates: {len(finals)}",
        "- Random seed: not captured",
        "- Explicit stop reason: not captured; the artifacts end after generation 4",
        "- Exact dependency versions: not captured",
        "- Final candidate selection: generated molecules that pass a fresh "
        "config-based filter check, have no ADMET failure, and score strictly "
        "above their best ancestral seed; retain the highest-scoring molecule "
        "for each distinct ancestral-seed lineage",
        "",
        "## Configuration",
        "",
        "### Hard filters",
        "",
        "| Setting | Value |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{_markdown(key)}` | {_markdown(value)} |"
        for key, value in config.get("filters", {}).items()
    )
    lines.extend(
        [
            "",
            "### Scoring",
            "",
            "| Setting | Value |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| `{_markdown(key)}` | {_markdown(value)} |"
        for key, value in config.get("scoring", {}).items()
    )
    admet = config.get("admet", {})
    lines.extend(
        [
            "",
            "### ADMET mapping",
            "",
            f"- ADMET-AI requested: `{admet.get('use_admet_ai', 'not captured')}`",
            f"- Risk threshold: `{admet.get('risk_threshold', 'not captured')}`",
            "- Positive endpoint keywords: "
            + ", ".join(f"`{item}`" for item in admet.get("positive_keywords", [])),
            "- Negative endpoint keywords: "
            + ", ".join(f"`{item}`" for item in admet.get("negative_keywords", [])),
            "",
            "## Generation summary",
            "",
            "| Generation | Generated | Best score | Mean score | Passed | Population best |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summaries:
        lines.append(
            f"| {row['generation']} | {row['generated']} | "
            f"{float(row['best_score']):.7f} | {float(row['mean_score']):.7f} | "
            f"{row['pass_count']} | {float(row['population_best']):.7f} |"
        )

    lines.extend(
        [
            "",
            "## Final candidates",
            "",
            "| Molecule ID | Operation | Baseline seed(s) | QED | SA score | ADMET score | Total score | Delta total | Risks |",
            "|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in finals:
        lines.append(
            f"| `{_markdown(row['molecule_id'])}` | {_markdown(row['operation'])} | "
            f"{_markdown(row['baseline_seed_ids'])} | {float(row['qed']):.4f} | "
            f"{float(row['sa_score']):.4f} | {float(row['admet_score']):.4f} | "
            f"{float(row['total_score']):.4f} | "
            f"{float(row['delta_total_vs_baseline']):+.4f} | "
            f"{_markdown(row['admet_risk_flags'] or 'none recorded')} |"
        )

    risk_text = ", ".join(f"`{name}` ({count})" for name, count in risks.items())
    score_delta = float(best_child["total_score"]) - float(best_seed["total_score"])
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"The best generation-0 score was {float(best_seed['total_score']):.7f} "
            f"(`{best_seed['molecule_id']}`). The best generated score was "
            f"{float(best_child['total_score']):.7f} (`{best_child['molecule_id']}`), "
            f"a recorded increase of {score_delta:+.7f}.",
            "",
            f"All {len(finals)} final candidates passed the configured filters, had "
            "successful ADMET evaluation, and improved total score relative to the "
            "best seed in their recorded ancestry. Filters were recalculated from "
            "the recorded properties and `config.yaml` rather than accepted solely "
            "from the log flag. The highest-scoring molecule represents each distinct "
            "ancestral-seed lineage. This deterministic rule does not claim an "
            "unrecorded fingerprint-diversity calculation.",
            "",
            f"Final-candidate risk flags were {risk_text or 'none recorded'}. These flags "
            "are model-derived triage signals, not observed toxicology outcomes.",
            "",
            "## Limitations and next steps",
            "",
            "- All ADMET values are model predictions and have not been experimentally validated.",
            "- The low-level mutation and crossover operators do not establish chemical feasibility.",
            "- SA score is a heuristic and does not guarantee a practical synthesis route.",
            "- The endpoint aggregation and keyword mapping are task-specific heuristics.",
            "- Reproduce the run with captured package versions and a fixed random seed.",
            "- Review top structures for medicinal-chemistry liabilities and scaffold diversity.",
            "- Validate prioritized endpoints with independent models and experimental assays.",
            "- Assess synthetic routes before advancing a candidate.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dashboard-output", type=Path, default=HERE / "optimization_dashboard.html"
    )
    parser.add_argument(
        "--candidates-output", type=Path, default=HERE / "candidates_final.csv"
    )
    parser.add_argument("--report-output", type=Path, default=HERE / "report.md")
    args = parser.parse_args(argv)

    data = load_example()
    validate_example(data)
    data["finals"] = select_final_candidates(data["log"], data["config"])
    write_final_candidates(args.candidates_output, data["finals"])
    render_optimization_history(HERE / "generation_log.csv", args.dashboard_output)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(build_report(data), encoding="utf-8")
    print(
        f"records={len(data['log'])} finals={len(data['finals'])} "
        f"dashboard={args.dashboard_output} report={args.report_output}"
    )


if __name__ == "__main__":
    main()
