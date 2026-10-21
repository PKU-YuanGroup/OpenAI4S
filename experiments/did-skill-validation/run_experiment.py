"""Execute the actual did-analysis sidecar on prespecified synthetic panels.

The protocol is saved before running the simulation. Its statistical rates are
descriptive and never determine whether a run passes. Existing output bundles
are refused. Independent arithmetic lives in verify_experiment.py.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import random
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROTOCOL = json.loads((HERE / "prespecification.json").read_text(encoding="utf-8"))
FIELDS = [
    "entity",
    "period",
    "cluster",
    "subgroup",
    "cohort",
    "exposure",
    "outcome",
    "untreated_outcome",
    "true_effect",
]
DESIGN = {
    "event_source": "Prespecified synthetic policy onset, not selected from observed outcomes.",
    "treatment_assignment": "Cluster IDs 0-11 treated at period 4; paired entities share assignment and shocks. Staggered cohorts use clusters 0-5 at 3, 6-11 at 5, and 12-23 never treated.",
    "parallel_trends": "Prespecified untreated common trend except the explicitly labelled violated_pretrends stress test. Diagnostics cannot establish this identifying assumption.",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def save_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        format(row[key], ".17g")
                        if isinstance(row.get(key), float)
                        else row.get(key)
                    )
                    for key in fields
                }
            )


def generate(scenario: str, seed: int) -> list[dict[str, Any]]:
    """Saved DGP: cluster AR(1) shocks plus unit and unit-period components."""
    spec = PROTOCOL["scenarios"][scenario]
    rng = random.Random(seed)
    rows = []
    for cluster in range(PROTOCOL["assignment_clusters"]):
        common = rng.gauss(0.0, spec["cluster_sd"])
        shocks = []
        for _ in PROTOCOL["periods"]:
            common = spec["cluster_ar"] * common + rng.gauss(
                0.0, spec["cluster_sd"] * math.sqrt(1.0 - spec["cluster_ar"] ** 2)
            )
            shocks.append(common)
        for within in range(PROTOCOL["entities_per_cluster"]):
            unit = cluster * 2 + within
            intercept = 10.0 + rng.gauss(0.0, 1.0)
            subgroup = within
            treated = cluster < 12
            cohort = (
                (3 if cluster < 6 else 5 if cluster < 12 else 0)
                if scenario == "heterogeneous_staggered"
                else (4 if treated else 0)
            )
            for time in PROTOCOL["periods"]:
                exposed = int(cohort > 0 and time >= cohort)
                untreated = (
                    intercept
                    + 0.25 * time
                    + 0.1 * math.sin(time)
                    + shocks[time]
                    + rng.gauss(0.0, spec["entity_noise_sd"])
                )
                untreated += spec.get("treated_trend", 0.0) * treated * time
                if scenario == "triple_difference":
                    untreated += (
                        spec["subgroup_time_trend"] * subgroup * time
                        + spec["treated_subgroup_static"] * treated * subgroup
                    )
                    effect = exposed * (
                        spec["effect"] + spec["subgroup_effect"] * subgroup
                    )
                elif scenario == "heterogeneous_staggered":
                    effect = (
                        (
                            spec["effect_at_cohort"][str(cohort)]
                            + spec["effect_growth"][str(cohort)] * (time - cohort)
                        )
                        if exposed
                        else 0.0
                    )
                else:
                    effect = spec["effect"] * exposed
                rows.append(
                    {
                        "entity": unit,
                        "period": time,
                        "cluster": cluster,
                        "subgroup": subgroup,
                        "cohort": cohort,
                        "exposure": exposed,
                        "outcome": untreated + effect,
                        "untreated_outcome": untreated,
                        "true_effect": effect,
                    }
                )
    return rows


def load_skill() -> Any:
    path = REPO / "skills/did-analysis/kernel.py"
    spec = importlib.util.spec_from_file_location("did_validation_actual_skill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load actual did-analysis sidecar")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def common() -> dict[str, Any]:
    return dict(
        entity="entity",
        time="period",
        outcome="outcome",
        treatment="exposure",
        cluster="cluster",
        research_design=DESIGN,
        treatment_time=4,
        pre_periods=PROTOCOL["pre_periods"],
        post_periods=PROTOCOL["post_periods"],
    )


def wilson(successes: int, n: int) -> list[float]:
    z = 1.959963984540054
    p = successes / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, center - radius), min(1.0, center + radius)]


def event_config() -> dict[str, Any]:
    return {
        key: value
        for key, value in common().items()
        if key not in {"pre_periods", "post_periods"}
    }


def fake_entities(rows: list[dict[str, Any]]) -> list[str | int]:
    return sorted(
        {row["entity"] for row in rows if int(row["entity"]) in range(24, 36)}
    )


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    errors = [r["estimate"] - r["truth"] for r in records]
    bias = statistics.mean(errors)
    bias_mcse = statistics.stdev(errors) / math.sqrt(n)
    value: dict[str, Any] = {
        "replicates": n,
        "bias": bias,
        "bias_mcse": bias_mcse,
        "bias_mc_95_interval": [
            bias - 1.959963984540054 * bias_mcse,
            bias + 1.959963984540054 * bias_mcse,
        ],
        "rmse": math.sqrt(statistics.mean(e * e for e in errors)),
        "mean_standard_error": statistics.mean(r["standard_error"] for r in records),
    }
    for field in ["covers_truth", "rejects_zero", "rejects_pretrend"]:
        count = sum(r[field] for r in records)
        p = count / n
        value[field] = {
            "count": count,
            "rate": p,
            "mcse": math.sqrt(p * (1 - p) / n),
            "wilson_mc_95_interval": wilson(count, n),
        }
    return value


def index(path: Path, title: str, title_zh: str) -> None:
    files = sorted(
        p.name
        for p in path.iterdir()
        if p.is_file() and p.name not in {"README.md", "README_zh.md"}
    )
    if path.name == "results" and "verification.json" not in files:
        files.append("verification.json")
        files.sort()
    for filename, heading, link, desc in [
        (
            "README.md",
            title,
            "[中文说明](README_zh.md)",
            "Preserved experiment artifacts; consult the experiment report for scientific scope.",
        ),
        (
            "README_zh.md",
            title_zh,
            "[English](README.md)",
            "保留的实验产物；科学范围见实验报告。",
        ),
    ]:
        lines = [
            f"# {heading}",
            "",
            link,
            "",
            desc,
            "",
            "## Files" if filename == "README.md" else "## 文件",
            "",
        ]
        lines += [f"- `{name}`" for name in files]
        lines += [
            f"- `{p.name}/`"
            for p in sorted(path.iterdir())
            if p.is_dir() and p.name != "__pycache__"
        ]
        (path / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument(
        "--plots",
        action="store_true",
        help="Execute the actual Skill's optional matplotlib renderer",
    )
    args = parser.parse_args()
    sources = [
        HERE / "run_experiment.py",
        HERE / "verify_experiment.py",
        HERE / "prespecification.json",
        HERE / "requirements-analysis.txt",
        REPO / "skills/did-analysis/kernel.py",
        REPO / "skills/did-analysis/SKILL.md",
        REPO / "skills/did-analysis/method-notes.md",
    ]
    source_digests = {str(p.relative_to(REPO)): sha(p) for p in sources}
    out = args.output.resolve()
    for name in ["inputs", "results"]:
        if (out / name).exists():
            parser.error(
                f"Refusing to overwrite existing {out / name}; choose a fresh --output directory"
            )
    out.mkdir(parents=True, exist_ok=True)
    inputs, results = out / "inputs", out / "results"
    inputs.mkdir()
    results.mkdir()
    skill = load_skill()
    examples = {}
    for scenario in PROTOCOL["scenarios"]:
        rows = generate(scenario, PROTOCOL["example_seed"])
        save_csv(inputs / f"{scenario}.csv", rows, FIELDS)
        examples[scenario] = skill.read_csv(inputs / f"{scenario}.csv")
    save_json(
        inputs / "generation.json",
        {
            "protocol": PROTOCOL,
            "research_design": DESIGN,
            "rng": "Python random.Random, separate restart per scenario/seed; rows ordered cluster/entity/time",
            "truth_fields": ["untreated_outcome", "true_effect"],
            "selection": "No outcome-selected event date, subgroup or sample",
        },
    )
    outputs = {}
    for scenario in PROTOCOL["monte_carlo_scenarios"]:
        rows = examples[scenario]
        outputs[scenario] = {
            "traditional": skill.traditional_did(rows, **common()),
            "event_study": skill.event_study(
                rows, **event_config(), periods=PROTOCOL["periods"], reference_period=3
            ),
        }
    outputs["triple_difference"] = skill.triple_difference(
        examples["triple_difference"], **common(), subgroup="subgroup"
    )
    for control in ["never_treated", "not_yet_treated"]:
        outputs[f"staggered_{control}"] = skill.staggered_did(
            examples["heterogeneous_staggered"],
            entity="entity",
            time="period",
            outcome="outcome",
            cohort="cohort",
            treatment="exposure",
            periods=PROTOCOL["periods"],
            cluster="cluster",
            research_design=DESIGN,
            control_group=control,
        )
    placebo = PROTOCOL["placebo"]
    outputs["fake_time"] = skill.placebo_time(
        examples["constant_effect"],
        **{
            **common(),
            "pre_periods": placebo["pre_periods"],
            "post_periods": placebo["post_periods"],
        },
        fake_treatment_time=placebo["fake_treatment_time"],
    )
    outputs["fake_group"] = skill.placebo_group(
        examples["constant_effect"],
        **common(),
        fake_treated_entities=fake_entities(examples["constant_effect"]),
    )
    outputs["random_group_distribution"] = skill.placebo_distribution(
        examples["constant_effect"],
        **common(),
        draws=placebo["random_draws"],
        seed=placebo["random_seed"],
        fake_cluster_count=placebo["fake_cluster_count"],
    )
    outputs["pipeline"] = skill.analyze_did(
        examples["constant_effect"],
        plan={
            "traditional": common(),
            "event_study": [
                {
                    **event_config(),
                    "periods": PROTOCOL["periods"],
                    "reference_period": 3,
                }
            ],
            "placebo_time": [
                {
                    **common(),
                    "pre_periods": placebo["pre_periods"],
                    "post_periods": placebo["post_periods"],
                    "fake_treatment_time": placebo["fake_treatment_time"],
                }
            ],
            "placebo_group": [
                {
                    **common(),
                    "fake_treated_entities": fake_entities(examples["constant_effect"]),
                }
            ],
            "placebo_distribution": [
                {
                    **common(),
                    "draws": placebo["random_draws"],
                    "seed": placebo["random_seed"],
                    "fake_cluster_count": placebo["fake_cluster_count"],
                }
            ],
            "derived": [
                {
                    "method": "triple_difference",
                    "config": {**common(), "subgroup": "subgroup"},
                }
            ],
        },
    )
    pipeline_dir = results / "pipeline"
    skill.write_outputs(outputs["pipeline"], pipeline_dir)
    if args.plots:
        skill.render_diagnostics(outputs["pipeline"], pipeline_dir)
    index(pipeline_dir, "Actual Skill pipeline output", "实际 Skill 流程产物")
    outputs["pipeline_with_refusals"] = skill.analyze_did(
        examples["constant_effect"],
        plan={
            "traditional": common(),
            "placebo_time": [{**common(), "fake_treatment_time": 5}],
            "placebo_group": [{**common(), "fake_treated_entities": ["0", "1"]}],
            "placebo_distribution": [
                {
                    **common(),
                    "draws": placebo["random_draws"],
                    "seed": placebo["random_seed"],
                    "fake_cluster_count": placebo["fake_cluster_count"],
                }
            ],
            "derived": [
                {
                    "method": "triple_difference",
                    "config": {**common(), "subgroup": "subgroup"},
                }
            ],
        },
    )
    refusal_dir = results / "refusals"
    skill.write_outputs(outputs["pipeline_with_refusals"], refusal_dir)
    index(refusal_dir, "Retained diagnostic refusals", "保留的诊断拒绝记录")
    for name, result_key in [
        ("triple-difference", "triple_difference"),
        ("staggered-never", "staggered_never_treated"),
    ]:
        diagnostic_dir = results / name
        skill.write_outputs(outputs[result_key], diagnostic_dir)
        if args.plots:
            skill.render_diagnostics(outputs[result_key], diagnostic_dir)
        index(diagnostic_dir, "Actual advanced DiD output", "实际进阶 DiD 产物")
    save_json(results / "skill_outputs.json", outputs)
    records = []
    monte_carlo_inputs = []
    summary = {}
    for scenario in PROTOCOL["monte_carlo_scenarios"]:
        scenario_records = []
        for replicate in range(PROTOCOL["replicate_seeds"]["count"]):
            seed = PROTOCOL["replicate_seeds"]["start"] + replicate
            rows = generate(scenario, seed)
            by_entity: dict[int, dict[str, Any]] = {}
            for raw in rows:
                unit = by_entity.setdefault(
                    raw["entity"],
                    {
                        "scenario": scenario,
                        "seed": seed,
                        "entity": raw["entity"],
                        "cluster": raw["cluster"],
                        "group": int(raw["cohort"] > 0),
                    },
                )
                unit[f"outcome_{raw['period']}"] = raw["outcome"]
            monte_carlo_inputs.extend(by_entity.values())
            fit = skill.traditional_did(rows, **common())
            event = skill.event_study(
                rows, **event_config(), periods=PROTOCOL["periods"], reference_period=3
            )
            est = fit
            truth = PROTOCOL["scenarios"][scenario]["effect"]
            low, high = est["ci_low"], est["ci_high"]
            row = {
                "scenario": scenario,
                "seed": seed,
                "truth": truth,
                "estimate": est["estimate"],
                "standard_error": est["se"],
                "ci_low": low,
                "ci_high": high,
                "covers_truth": low <= truth <= high,
                "rejects_zero": est["p_value"] < 0.05,
                "rejects_pretrend": event["joint_pretrend"]["p_value"] < 0.05,
            }
            scenario_records.append(row)
            records.append(row)
        summary[scenario] = summarize(scenario_records)
        print(f"{scenario}: {len(scenario_records)} replicates", flush=True)
    save_csv(results / "monte_carlo.csv", records, list(records[0]))
    save_csv(
        inputs / "monte_carlo_unit_outcomes.csv",
        monte_carlo_inputs,
        list(monte_carlo_inputs[0]),
    )
    save_json(
        results / "monte_carlo_summary.json",
        {
            "protocol": PROTOCOL["replicate_seeds"],
            "interpretation": "Rates describe finite Monte Carlo precision; no rate is a pass gate. Violated-pretrend coverage is relative to the assigned effect, although identifying assumptions are deliberately false.",
            "scenarios": summary,
        },
    )
    versions = {}
    for package in ["numpy", "scipy", "statsmodels", "matplotlib"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    save_json(
        results / "environment.json",
        {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "optional_analysis_packages": versions,
            "skill_path": "skills/did-analysis/kernel.py",
            "skill_sha256": source_digests["skills/did-analysis/kernel.py"],
            "plots_requested": args.plots,
        },
    )
    index(inputs, "Synthetic input panels", "合成输入面板")
    index(results, "DiD validation results", "DiD 验证结果")
    if source_digests != {str(p.relative_to(REPO)): sha(p) for p in sources}:
        raise RuntimeError(
            "A source changed during execution; this incomplete bundle has no finalized artifact manifest"
        )
    save_json(
        results / "artifact_manifest.json",
        {
            "schema_version": 1,
            "sources": source_digests,
            "artifacts": {
                str(p.relative_to(out)): sha(p)
                for folder in [inputs, results]
                for p in sorted(folder.rglob("*"))
                if p.is_file() and p.name != "artifact_manifest.json"
            },
            "excluded": [
                "results/artifact_manifest.json (self hash)",
                "results/verification.json (written by independent verifier)",
            ],
        },
    )
    index(results, "DiD validation results", "DiD 验证结果")
    # Recompute after the index includes artifact_manifest.json.
    manifest = json.loads(
        (results / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    manifest["artifacts"]["results/README.md"] = sha(results / "README.md")
    manifest["artifacts"]["results/README_zh.md"] = sha(results / "README_zh.md")
    save_json(results / "artifact_manifest.json", manifest)
    print(f"Saved actual Skill outputs to {out}", flush=True)


if __name__ == "__main__":
    main()
