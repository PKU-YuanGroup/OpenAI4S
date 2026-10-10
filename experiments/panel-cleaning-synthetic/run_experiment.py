"""Fixed-seed offline experiment using the actual panel preprocessing Skill."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = 20261007
SCENARIOS = (
    "quiet",
    "jump_up",
    "jump_down",
    "noisy_jump",
    "linear_trend",
    "single_spike",
    "seasonal",
    "late_entry",
)
NUMERIC = [
    "activity",
    "capacity",
    "spend",
    "correlated_metric",
    "signal_x",
    "signal_z",
]
FIELDS = [
    "entity",
    "period",
    "scenario",
    *NUMERIC,
    "region",
    "plan",
    "channel",
    "comment",
    "treated",
]
PROTECTED = ["entity", "period", "scenario", "treated"]
SELECTED_ENTITIES = [f"{i:03d}" for i in range(1, 33, 4)]
SOURCE_FILES = [
    "experiments/panel-cleaning-synthetic/run_experiment.py",
    "skills/panel-data-preprocessing/kernel.py",
    "skills/panel-data-preprocessing/SKILL.md",
]
SCREEN = {
    "window": 3,
    "window_options": [3, 5, 7, 9],
    "threshold": 3.0,
    "min_abs_change": 2.0,
    "noise_tolerance": {"activity": 0.5},
    "min_noise_periods": 5,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
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


def _module() -> Any:
    path = ROOT / "skills/panel-data-preprocessing/kernel.py"
    spec = importlib.util.spec_from_file_location("synthetic_panel_skill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the actual panel Skill sidecar")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index(
    path: Path, title: str, title_zh: str, description: str, description_zh: str
) -> None:
    files = sorted(
        p.name
        for p in path.iterdir()
        if p.is_file() and p.name not in {"README.md", "README_zh.md"}
    )
    children = sorted(p.name for p in path.iterdir() if p.is_dir())
    english = [
        f"# {title}",
        "",
        "[中文说明](README_zh.md)",
        "",
        description,
        "",
        "## Files",
        "",
        "| Entry | Purpose |",
        "| --- | --- |",
    ]
    chinese = [
        f"# {title_zh}",
        "",
        "[English](README.md)",
        "",
        description_zh,
        "",
        "## 文件",
        "",
        "| 条目 | 用途 |",
        "| --- | --- |",
    ]
    for name in files:
        purpose = (
            "Recorded runtime/source/output checksums."
            if name.endswith("manifest.json") or name == "environment.json"
            else "Preserved generated data, diagnostic or analysis artifact; meanings are recorded by the experiment report."
        )
        purpose_zh = (
            "记录运行环境、源或产物校验和。"
            if name.endswith("manifest.json") or name == "environment.json"
            else "保留的生成数据、诊断或分析产物；具体含义见实验报告。"
        )
        english.append(f"| `{name}` | {purpose} |")
        chinese.append(f"| `{name}` | {purpose_zh} |")
    for name in children:
        english.append(f"| `{name}/` | Independent preserved Skill output bundle. |")
        chinese.append(f"| `{name}/` | 独立保留的 Skill 输出包。 |")
    for name, lines in [("README.md", english), ("README_zh.md", chinese)]:
        (path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _generate(
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    data_rng = random.Random(seed)
    mask_rng = random.Random(seed ^ 0x5A17)
    schema_rng = random.Random(seed ^ 0xC0DE)
    truth, raw, structural, gaps, masks, events = [], [], [], [], [], []
    probabilities = {
        "activity": 0.07,
        "capacity": 0.10,
        "spend": 0.10,
        "correlated_metric": 0.10,
        "signal_x": 0.02,
        "signal_z": 0.02,
    }
    effects = {"jump_up": 8.0, "jump_down": -7.0, "noisy_jump": 5.0}
    for ordinal in range(1, 33):
        entity, scene = f"{ordinal:03d}", SCENARIOS[(ordinal - 1) // 4]
        baseline = 20 + data_rng.gauss(0, 1.5)
        capacity_center = 100 + data_rng.gauss(0, 10)
        target_center = 30 + data_rng.gauss(0, 3)
        if scene in effects:
            effect = effects[scene]
            events.append(
                {
                    "truth_id": f"{scene}:{entity}:25",
                    "entity": entity,
                    "scenario": scene,
                    "metric": "activity",
                    "time": "25",
                    "period_index": 25,
                    "effect": effect,
                    "direction": "up" if effect > 0 else "down",
                    "source": "inputs/generation.json:prespecified scenario registry",
                }
            )
        for period in range(1, 49):
            noise_sd = (
                3.0
                if scene == "noisy_jump"
                else 0.3 if scene == "linear_trend" else 0.4
            )
            deterministic = effects.get(scene, 0.0) if period >= 25 else 0.0
            if scene == "linear_trend":
                deterministic = 0.2 * period
            elif scene == "single_spike" and period == 25:
                deterministic = 8.0
            elif scene == "seasonal":
                deterministic = 3.0 * math.sin(2 * math.pi * period / 8)
            signal_x, signal_z = data_rng.gauss(0, 1), data_rng.gauss(0, 1)
            region = ["north", "east", "south", "west"][(ordinal - 1) % 4]
            if period >= 35 and schema_rng.random() < 0.12:
                region = "new_region"
            comment = [
                "稳定 服务 区域 产品 反馈",
                "稳定 产品 交付 用户 反馈",
                "stable service regional product feedback",
                "reliable product delivery customer feedback",
            ][data_rng.randrange(4)]
            if period >= 35 and schema_rng.random() < 0.12:
                comment += " 量子 推荐 hyperpersonalised"
            row = {
                "entity": entity,
                "period": str(period),
                "scenario": scene,
                "activity": baseline + deterministic + data_rng.gauss(0, noise_sd),
                "capacity": capacity_center + data_rng.gauss(0, 4),
                "spend": data_rng.lognormvariate(3.0, 0.9),
                "correlated_metric": target_center
                + 2.0 * signal_x
                - 1.5 * signal_z
                + data_rng.gauss(0, 0.2),
                "signal_x": signal_x,
                "signal_z": signal_z,
                "region": region,
                "plan": data_rng.choice(["bronze", "silver", "gold"]),
                "channel": data_rng.choice(["web", "mobile", "api"]),
                "comment": comment,
                "treated": int(scene in effects and period >= 25),
            }
            truth.append(row)
            dropped = mask_rng.random() < 0.025
            missing = [
                column
                for column, probability in probabilities.items()
                if mask_rng.random() < probability
            ]
            key = {"entity": entity, "period": str(period), "scenario": scene}
            if scene == "late_entry" and period < 9:
                structural.append(
                    {**key, "reason": "prespecified_late_entry_before_period9"}
                )
                continue
            if scene != "late_entry" and dropped:
                gaps.append(
                    {**key, "reason": "independent_bernoulli_calendar_deletion"}
                )
                continue
            recorded = dict(row)
            for column in missing:
                recorded[column] = None
                masks.append(
                    {
                        **key,
                        "column": column,
                        "raw_row": len(raw) + 1,
                        "split": "fit" if period <= 16 else "heldout",
                    }
                )
            raw.append(recorded)
    generation = {
        "schema_version": 1,
        "seed": seed,
        "rng_seeds": {
            "data": seed,
            "missingness": seed ^ 0x5A17,
            "category_drift": seed ^ 0xC0DE,
        },
        "parameters": {
            "n_entities": 32,
            "n_periods": 48,
            "entities_per_scenario": 4,
            "scenarios": list(SCENARIOS),
            "persistent_event_period": 25,
            "persistent_effects": effects,
            "activity_noise_sd": {
                scene: (
                    3.0
                    if scene == "noisy_jump"
                    else 0.3 if scene == "linear_trend" else 0.4
                )
                for scene in SCENARIOS
            },
            "linear_slope": 0.2,
            "spike_size": 8.0,
            "seasonal_amplitude": 3.0,
            "seasonal_period": 8,
            "capacity_within_entity_sd": 4.0,
            "spend_log_mean": 3.0,
            "spend_log_sd": 0.9,
            "correlated_metric_formula": "entity_offset + 2*signal_x - 1.5*signal_z + N(0,0.2^2)",
            "calendar_delete_probability": 0.025,
            "cell_mask_probabilities": probabilities,
            "late_entry_first_observed_period": 9,
            "fit_period_max": 16,
            "new_category_start_period": 35,
            "new_category_probability": 0.12,
            "new_text_probability": 0.12,
        },
        "mechanisms": {
            "cell_missingness": "Artificial MCAR: independent Bernoulli masks, separate RNG from numeric DGP, retained rows only scored.",
            "calendar_deletion": "Independent Bernoulli deletion within seven non-late-entry scenarios; all mask draws precede retention decisions.",
            "structural_missingness": "Four late-entry entities are deterministically unobserved before period9; separate from MCAR masks.",
            "new_categories_text": "Artificial schema/vocabulary changes after period35; not numeric outcome events.",
        },
        "persistent_events": events,
        "structural_absence": structural,
        "calendar_deleted_records": gaps,
        "masked_cells": masks,
        "counts": {
            "truth_rows": len(truth),
            "raw_rows": len(raw),
            "structural_absence": len(structural),
            "calendar_deleted_rows": len(gaps),
            "masked_cells": len(masks),
        },
        "evaluation_policy": {
            "truth_usage": "Evaluation only after fitting/method choice; truth values never supplied to cleaners or profiles.",
            "primary_screen": SCREEN,
            "event_date_tolerances": [0, 1],
            "require_direction_agreement": True,
            "seed_threshold_tuning": "None; single fixed design/draw.",
            "event_matching": "Same entity and activity; one-to-one exact/nearest/earliest; one true persistent event per affected entity.",
        },
    }
    return truth, raw, generation


def _pearson(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    if len(pairs) < 2:
        return {"n": len(pairs), "pearson": None}
    x_mean, y_mean = statistics.mean(x for x, _ in pairs), statistics.mean(
        y for _, y in pairs
    )
    cov = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    scale = math.sqrt(
        sum((x - x_mean) ** 2 for x, _ in pairs)
        * sum((y - y_mean) ** 2 for _, y in pairs)
    )
    return {"n": len(pairs), "pearson": cov / scale if scale else None}


def _fit_correlations(raw: list[dict[str, str]], fit_rows: list[int]) -> dict[str, Any]:
    result = {}
    for feature in ["signal_x", "signal_z"]:
        groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for position in fit_rows:
            row = raw[position - 1]
            if row[feature] and row["correlated_metric"]:
                groups[row["entity"]].append(
                    (float(row[feature]), float(row["correlated_metric"]))
                )
        pairs = [pair for values in groups.values() for pair in values]
        centered = []
        by_entity = {}
        for entity, values in sorted(groups.items()):
            by_entity[entity] = _pearson(values)
            mx, my = statistics.mean(x for x, _ in values), statistics.mean(
                y for _, y in values
            )
            centered.extend((x - mx, y - my) for x, y in values)
        result[feature] = {
            "pooled": _pearson(pairs),
            "within_entity_demeaned": _pearson(centered),
            "by_entity": by_entity,
        }
    return result


def _cleaning_plan(
    module: Any, raw: list[dict[str, str]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fit_rows = [i for i, row in enumerate(raw, 1) if int(row["period"]) <= 16]
    fit_sample = [raw[i - 1] for i in fit_rows]
    profile = module.profile_cleaning(
        fit_sample, numeric_columns=NUMERIC, protected_columns=PROTECTED
    )
    correlations = _fit_correlations(raw, fit_rows)
    rx = correlations["signal_x"]["within_entity_demeaned"]["pearson"]
    rz = correlations["signal_z"]["within_entity_demeaned"]["pearson"]
    rationale = (
        "Known variable roles and pre16 raw profile guide distinct methods: "
        "capacity is a stable continuous measurement with approximately symmetric within-entity variation, so use mean; "
        "spend is a nonnegative heavy-tailed measurement, so use median for a robust baseline; "
        f"correlated_metric has meaningful numeric similarity features with fit-only within-entity Pearson r(signal_x)={rx:.6f}, r(signal_z)={rz:.6f}, "
        "so use KNN on these two features, with same-entity donors/scales. "
        "Region is nominal onehot, plan has prespecified bronze/silver/gold order, channel labels are category identifiers; "
        "comments are presegmented lexical text using fit-only TFIDF. "
        "Activity and numeric predictors remain unfilled in the primary analysis. No truth or heldout values choose these methods."
    )
    cleaning = {
        "fit_rows": fit_rows,
        "numeric_columns": NUMERIC,
        "protected_columns": PROTECTED,
        "imputations": {
            "capacity": "mean",
            "spend": "median",
            "correlated_metric": "knn",
        },
        "categorical_encodings": {
            "region": "onehot",
            "plan": {"method": "ordinal", "order": ["bronze", "silver", "gold"]},
            "channel": "label",
        },
        "text_encodings": {"comment": "tfidf"},
        "group_by": ["entity"],
        "knn_features": ["signal_x", "signal_z"],
        "n_neighbors": 5,
        "weights": "distance",
        "unknown_categories": "indicator",
        "text_max_features": 1000,
        "max_generated_features": 1000,
        "decision": {"basis": "data_profile", "rationale": rationale},
    }
    decisions = {
        "schema_version": 1,
        "fit_rows": fit_rows,
        "fit_criterion": "Raw input row period<=16; selected before any evaluation.",
        "fit_source_records_sha256": profile["source_records_sha256"],
        "fit_counts_by_entity": dict(
            sorted(Counter(r["entity"] for r in fit_sample).items())
        ),
        "fit_correlations": correlations,
        "imputations": cleaning["imputations"],
        "decision": cleaning["decision"],
        "configuration": cleaning,
        "profile_evidence": {c: profile["columns"][c] for c in NUMERIC},
        "profile_questions_resolution": "Variable roles are explicitly defined by this synthetic design; entity/time/scenario/treatment are protected, plan ordering is declared in advance, and comment tokens are segmented before generation. Profile suggestions do not prove MCAR; masks are independently prescribed.",
        "grouping_scope": "Numeric donor/statistic/scaling fits are within entity; categorical/text dictionaries pool only selected baseline rows.",
        "filled_outcome_sensitivity": "Same fit sample/method plan, additionally mean-fill activity; windows containing filled activity remain excluded and screen settings stay fixed.",
        "screen_fixed": SCREEN,
    }
    return cleaning, profile, decisions


def _stats(errors: list[float]) -> dict[str, Any]:
    return {
        "n_error": len(errors),
        "mae": statistics.mean(abs(e) for e in errors) if errors else None,
        "rmse": math.sqrt(statistics.mean(e * e for e in errors)) if errors else None,
        "bias": statistics.mean(errors) if errors else None,
    }


def _error_rows(
    case: str,
    column: str,
    method: str,
    raw: list[dict[str, str]],
    transformed: list[dict[str, Any]],
    truth: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    records = []
    for position, (source, output) in enumerate(zip(raw, transformed), 1):
        if source[column]:
            continue
        value = output.get(column)
        actual = float(truth[(source["entity"], source["period"])][column])
        records.append(
            {
                "case": case,
                "column": column,
                "method": method,
                "entity": source["entity"],
                "period": source["period"],
                "source_row": position,
                "split": "fit" if int(source["period"]) <= 16 else "heldout",
                "truth": actual,
                "prediction": value,
                "error": value - actual if value is not None else None,
                "filled": value is not None,
            }
        )
    return records


def _imputation_summary(records: list[dict[str, Any]], method: str) -> dict[str, Any]:
    report = {"method": method}
    for split in ["overall", "fit", "heldout"]:
        selected = (
            records
            if split == "overall"
            else [r for r in records if r["split"] == split]
        )
        errors = [r["error"] for r in selected if r["filled"]]
        report[split] = {
            "n_masked": len(selected),
            "n_filled": len(errors),
            "fill_rate": len(errors) / len(selected) if selected else None,
            **_stats(errors),
        }
    return report


def _knn_comparison(
    module: Any,
    raw: list[dict[str, str]],
    cleaning: dict[str, Any],
    main: dict[str, Any],
    truth: dict[tuple[str, str], dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row_sets, errors = {"knn": main["rows"]}, []
    main_lookup = {(r["entity"], str(r["period"])): r for r in main["rows"]}
    row_sets["knn"] = [main_lookup[(r["entity"], r["period"])] for r in raw]
    for method in ["mean", "median"]:
        options = {
            **cleaning,
            "imputations": {"correlated_metric": method},
            "categorical_encodings": {},
            "text_encodings": {},
        }
        options["decision"] = {
            "basis": "data_profile",
            "rationale": "Prespecified evaluation-only comparator: same target masks, baseline fit rows and entity-specific observed target donors; does not choose the primary method.",
        }
        row_sets[method] = module.clean_data(raw, **options)["rows"]
    reports, by_method = {}, {}
    for method, rows in row_sets.items():
        records = _error_rows(
            f"target_comparison_{method}", "correlated_metric", method, raw, rows, truth
        )
        errors.extend(records)
        reports[method] = _imputation_summary(records, method)
        by_method[method] = {(r["entity"], r["period"]): r for r in records}
    common = set.intersection(
        *(
            set(key for key, value in records.items() if value["filled"])
            for records in by_method.values()
        )
    )
    common_report = {}
    for split in ["overall", "fit", "heldout"]:
        keys = sorted(
            common
            if split == "overall"
            else {key for key in common if by_method["knn"][key]["split"] == split}
        )
        common_report[split] = {
            "n_common": len(keys),
            "methods": {
                method: _stats([records[key]["error"] for key in keys])
                for method, records in by_method.items()
            },
        }
    filled = [
        r
        for r in main["cleaning"]["report"]["filled"]
        if r["column"] == "correlated_metric"
    ]
    feature_coverage = Counter()
    for record in filled:
        row = raw[record["row"] - 1]
        names = [f for f in ["signal_x", "signal_z"] if row[f]]
        feature_coverage[
            "both" if len(names) == 2 else names[0] if names else "none"
        ] += 1
    return {
        "scope": {
            "column": "correlated_metric",
            "fit_rows": cleaning["fit_rows"],
            "group_by": ["entity"],
            "knn_features": cleaning["knn_features"],
            "weights": cleaning["weights"],
            "n_neighbors": 5,
            "comparison_role": "Evaluation only, same retained raw target masks and fit donor range; truth never selects the main method.",
        },
        "methods": reports,
        "common_filled": common_report,
        "fit_donor_counts_by_entity": {
            bucket["group_values"][0]: sum(
                d["values"]["correlated_metric"] is not None for d in bucket["donors"]
            )
            for bucket in main["cleaning"]["model"]["numeric"]["groups"].values()
        },
        "knn_usage": {
            "filled_method_counts": dict(Counter(r["method"] for r in filled)),
            "fitted_mean_fallback_count": sum(
                r["method"] == "fitted_mean_fallback" for r in filled
            ),
            "recipient_feature_coverage": dict(feature_coverage),
            "neighbor_count_distribution": dict(
                sorted(
                    Counter(
                        len(r["distance_details"])
                        for r in filled
                        if "distance_details" in r
                    ).items()
                )
            ),
            "shared_feature_count_distribution": dict(
                sorted(
                    Counter(
                        len(d["common_features"])
                        for r in filled
                        for d in r.get("distance_details", [])
                    ).items()
                )
            ),
        },
    }, errors


def _events(result: dict[str, Any], registry: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        {**r, "candidate_id": f"numeric-{i:04d}"}
        for i, r in enumerate(
            [
                r
                for r in result["events"]["candidates"]
                if r["kind"] == "level_shift_candidate" and r["metric"] == "activity"
            ],
            1,
        )
    ]
    assessments = {
        (r["entity"], r["time"]): r
        for r in result["window_assessment"]["assessments"]
        if r["window"] == 3 and r["metric"] == "activity"
    }
    eligibility = []
    for truth in registry:
        assessment = assessments.get((truth["entity"], truth["time"]))
        eligibility.append(
            {
                "truth_id": truth["truth_id"],
                "entity": truth["entity"],
                "time": truth["time"],
                "eligible": assessment["eligible"] if assessment else False,
                "reasons": (
                    assessment["reasons"] if assessment else ["anchor_record_absent"]
                ),
                "coverage": assessment["coverage"] if assessment else None,
            }
        )
    eligible_ids = {r["truth_id"] for r in eligibility if r["eligible"]}
    report = {
        "numeric_candidates": candidates,
        "truth_events": registry,
        "eligibility_at_truth": eligibility,
        "candidate_kind_counts": dict(
            Counter(r["kind"] for r in result["events"]["candidates"])
        ),
        "scenario_candidate_counts": {
            scene: sum(
                c["entity"] in {f"{i:03d}" for i in range(j * 4 + 1, j * 4 + 5)}
                for c in candidates
            )
            for j, scene in enumerate(SCENARIOS)
        },
        "matching_policy": "Same entity/activity, sign agreement required, one-to-one matching; exact then nearest then earliest date. Truth registry has one persistent event per affected entity. Known dates/exposure records excluded from numeric counts. No TN after internal overlap suppression.",
    }
    for key, radius in [("exact", 0), ("tolerance_1", 1)]:
        used, matches = set(), []
        for truth in registry:
            possibilities = [
                c
                for c in candidates
                if c["candidate_id"] not in used
                and c["entity"] == truth["entity"]
                and abs(c["period_index"] - truth["period_index"]) <= radius
                and c["change"] * truth["effect"] > 0
            ]
            if possibilities:
                candidate = min(
                    possibilities,
                    key=lambda c: (
                        abs(c["period_index"] - truth["period_index"]),
                        c["period_index"],
                        c["candidate_id"],
                    ),
                )
                used.add(candidate["candidate_id"])
                matches.append(
                    {
                        "truth_id": truth["truth_id"],
                        "candidate_id": candidate["candidate_id"],
                        "entity": truth["entity"],
                        "truth_time": truth["time"],
                        "candidate_time": candidate["time"],
                        "date_error": candidate["period_index"] - truth["period_index"],
                        "direction_agrees": True,
                        "truth_anchor_eligible": truth["truth_id"] in eligible_ids,
                    }
                )
        matched_truth = {m["truth_id"] for m in matches}
        eligible_tp = len(matched_truth & eligible_ids)
        report[key] = {
            "tp": len(matches),
            "fp": len(candidates) - len(matches),
            "fn": len(registry) - len(matches),
            "precision": len(matches) / len(candidates) if candidates else None,
            "recall": len(matches) / len(registry) if registry else None,
            "matches": matches,
            "unmatched_truth": [
                r for r in registry if r["truth_id"] not in matched_truth
            ],
            "unmatched_candidates": [
                c for c in candidates if c["candidate_id"] not in used
            ],
            "eligible_truth_recovery": {
                "n_eligible": len(eligible_ids),
                "tp": eligible_tp,
                "fn": len(eligible_ids) - eligible_tp,
                "recall": eligible_tp / len(eligible_ids) if eligible_ids else None,
                "matched_ineligible_truth": [
                    m for m in matches if not m["truth_anchor_eligible"]
                ],
            },
        }
    return report


def _normalize_bundle(path: Path) -> None:
    for artifact in path.iterdir():
        if artifact.suffix == ".svg":
            artifact.write_text(
                "\n".join(
                    line.rstrip()
                    for line in artifact.read_text(encoding="utf-8").splitlines()
                )
                + "\n",
                encoding="utf-8",
            )
        elif artifact.suffix == ".csv":
            artifact.write_text(
                artifact.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
            )
    for name in ["manifest.json", "visualization.json"]:
        document = json.loads((path / name).read_text(encoding="utf-8"))
        for artifact in document["outputs"]:
            document["outputs"][artifact]["sha256"] = _sha(path / artifact)
        if name == "visualization.json":
            document["selection_basis"] = (
                "Prespecified first entity in each of eight DGP scenarios:001,005,009,013,017,021,025,029; no candidate- or truth-score selection. Excluded IDs are retained in the existing field."
            )
        _json(path / name, document)


def _overview(evaluation: dict[str, Any], main: dict[str, Any], path: Path) -> str:
    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError("Optional experimental figures require matplotlib") from exc
    figure = Figure(figsize=(13, 9), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    before = main["cleaning"]["report"]["before_missing"]
    after = main["cleaning"]["report"]["after_missing"]
    ax = axes[0, 0]
    x = list(range(len(NUMERIC)))
    ax.bar(
        [i - 0.2 for i in x],
        [before[c] for c in NUMERIC],
        0.4,
        label="Raw missing",
        color="#8a9dad",
    )
    ax.bar(
        [i + 0.2 for i in x],
        [after[c] for c in NUMERIC],
        0.4,
        label="Main: still missing",
        color="#2b6a9c",
    )
    ax.set(
        xticks=x,
        xticklabels=["activity", "capacity", "spend", "target", "x", "z"],
        ylabel="Retained-row cells",
        title="A. Covariates cleaned; activity stays observed/missing",
    )
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    methods = evaluation["knn_comparison"]["methods"]
    labels = ["knn", "mean", "median"]
    ax.bar(
        range(3),
        [methods[m]["heldout"]["mae"] for m in labels],
        color=["#2b6a9c", "#a28353", "#8771a8"],
    )
    for i, method in enumerate(labels):
        value = methods[method]["heldout"]
        ax.text(
            i, value["mae"] + 0.04, f"n={value['n_error']}", ha="center", fontsize=9
        )
    ax.set(
        xticks=range(3),
        xticklabels=labels,
        ylabel="MAE (target units)",
        title="B. Same held-out target masks and fit donor scope",
    )
    ax = axes[1, 0]
    summaries = main["window_assessment"]["summary"]
    ax.bar(
        range(len(summaries)),
        [r["eligible_anchor_count"] for r in summaries],
        color="#4d8d72",
    )
    for i, row in enumerate(summaries):
        ax.text(
            i,
            row["eligible_anchor_count"] + 6,
            str(row["eligible_anchor_count"]),
            ha="center",
            fontsize=9,
        )
    ax.set(
        xticks=range(4),
        xticklabels=[r["window"] for r in summaries],
        xlabel="Pre/post periods per window",
        ylabel="Eligible originally observed anchors",
        title="C. Wider windows need more unfilled observations",
    )
    ax.text(
        0.98,
        0.95,
        "Screen fixed at w=3\nNoise minimum = 5 pre periods",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9,
    )
    ax = axes[1, 1]
    events = evaluation["events"]["main"]
    for group_index, scene in enumerate(SCENARIOS):
        entities = {f"{i:03d}" for i in range(group_index * 4 + 1, group_index * 4 + 5)}
        known = [e for e in events["truth_events"] if e["entity"] in entities]
        candidates = [
            c for c in events["numeric_candidates"] if c["entity"] in entities
        ]
        for e in known:
            offset = (int(e["entity"]) - group_index * 4 - 2.5) * 0.06
            ax.scatter(
                e["period_index"],
                group_index + offset,
                marker="|",
                s=190,
                color="#444444",
                zorder=2,
            )
        for c in candidates:
            offset = (int(c["entity"]) - group_index * 4 - 2.5) * 0.06
            ax.scatter(
                c["period_index"],
                group_index + offset,
                marker="o",
                s=23,
                facecolors="none",
                edgecolors="#bc3d6c",
                zorder=3,
            )
    ax.scatter(
        [], [], marker="|", s=160, color="#444444", label="12 persistent DGP events"
    )
    ax.scatter(
        [],
        [],
        marker="o",
        s=23,
        facecolors="none",
        edgecolors="#bc3d6c",
        label="All returned activity candidates",
    )
    ax.set(
        yticks=range(8),
        yticklabels=SCENARIOS,
        xlim=(1, 48),
        xlabel="Period",
        title="D. Known persistent events and all numeric candidates",
    )
    ax.legend(loc="upper left", fontsize=8)
    ax.invert_yaxis()
    figure.suptitle(
        f"Fixed-seed panel cleaning experiment · seed {evaluation['design']['seed']}",
        fontsize=15,
    )
    figure.savefig(
        path / "overview.png",
        dpi=160,
        metadata={"Software": "OpenAI4S synthetic panel experiment"},
    )
    figure.savefig(
        path / "overview.svg",
        metadata={"Date": None, "Creator": "OpenAI4S synthetic panel experiment"},
    )
    svg = path / "overview.svg"
    svg.write_text(
        "\n".join(
            line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    return matplotlib.__version__


def run(output: Path, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    output = output.resolve()
    for directory in [output / "inputs", output / "results"]:
        if directory.exists() and any(
            p.is_file() and p.name not in {"README.md", "README_zh.md"}
            for p in directory.rglob("*")
        ):
            raise FileExistsError(
                "Existing inputs/results artifacts are preserved; choose a fresh output directory"
            )
    source_hashes = {name: _sha(ROOT / name) for name in SOURCE_FILES}
    module = _module()
    inputs, results = output / "inputs", output / "results"
    inputs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    truth, generated_raw, generation = _generate(seed)
    _csv(inputs / "truth.csv", truth, FIELDS)
    _csv(inputs / "raw.csv", generated_raw, FIELDS)
    raw = module.read_csv(inputs / "raw.csv")
    generation["inputs"] = {
        name: {
            "sha256": _sha(inputs / name),
            "parsed_records_sha256": _record_sha(module.read_csv(inputs / name)),
        }
        for name in ["raw.csv", "truth.csv"]
    }
    _json(inputs / "generation.json", generation)
    cleaning, profile, decisions = _cleaning_plan(module, raw)
    _json(results / "fit_profile.json", profile)
    _json(results / "method_decisions.json", decisions)
    known = [
        {
            "entity": e["entity"],
            "time": e["time"],
            "label": f"DGP {e['scenario']}",
            "source": e["source"],
        }
        for e in generation["persistent_events"]
    ]
    basic = {
        "entity": "entity",
        "time": "period",
        "frequency": "integer",
        "numeric_columns": ["activity"],
        "treatment": "treated",
        "known_events": known,
        **SCREEN,
    }
    variants = {}
    try:
        import matplotlib
    except ImportError as exc:
        raise RuntimeError(
            "Experimental diagnostic figures require optional matplotlib"
        ) from exc
    with matplotlib.rc_context(
        {
            "svg.hashsalt": "openai4s-panel-cleaning-synthetic-v1",
            "font.family": "DejaVu Sans",
        }
    ):
        for name in ["main", "filled-outcome"]:
            plan = {**cleaning, "imputations": dict(cleaning["imputations"])}
            if name == "filled-outcome":
                plan["imputations"]["activity"] = "mean"
                plan["decision"] = {
                    "basis": "data_profile",
                    "rationale": cleaning["decision"]["rationale"]
                    + " Prespecified sensitivity only: additionally mean-fill activity from pre16 same-entity data, retaining imputation markers and excluding filled windows.",
                }
            result = module.preprocess_panel(raw, cleaning=plan, **basic)
            module.write_outputs(result, results / name)
            module.render_diagnostics(
                result, results / name, entities=SELECTED_ENTITIES, metrics=["activity"]
            )
            _normalize_bundle(results / name)
            _index(
                results / name,
                f"{name} Skill outputs",
                f"{name} Skill 输出",
                "Actual Skill exports with all returned candidates. Eight plotted entities were fixed before generation; 24 excluded entities remain in visualization.json. Filled outcomes are distinct markers and cannot make windows eligible.",
                "实际 Skill 导出全部返回候选。八个绘图实体在生成前固定；24 个未展示实体仍记录在 visualization.json。填补结果使用独立标记，不能使窗口变为有效观测。",
            )
            variants[name] = result
        # Truth values first enter evaluation here; the earlier read only hashes provenance.
        truth_lookup = {
            (r["entity"], r["period"]): r for r in module.read_csv(inputs / "truth.csv")
        }
        imputation, error_rows = {"main": {}, "filled-outcome": {}}, []
        for name, columns in [
            (
                "main",
                {"capacity": "mean", "spend": "median", "correlated_metric": "knn"},
            ),
            ("filled-outcome", {"activity": "mean"}),
        ]:
            lookup = {(r["entity"], r["period"]): r for r in variants[name]["rows"]}
            ordered = [lookup[(r["entity"], r["period"])] for r in raw]
            for column, method in columns.items():
                records = _error_rows(
                    f"{name}_{column}_{method}",
                    column,
                    method,
                    raw,
                    ordered,
                    truth_lookup,
                )
                imputation[name][column] = _imputation_summary(records, method)
                error_rows.extend(records)
        comparison, comparator_errors = _knn_comparison(
            module, raw, cleaning, variants["main"], truth_lookup
        )
        error_rows.extend(comparator_errors)
        event_reports = {
            name: _events(result, generation["persistent_events"])
            for name, result in variants.items()
        }
        baseline = module.preprocess_panel(raw, **basic)

        def numeric(result: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                r
                for r in result["events"]["candidates"]
                if r["kind"] == "level_shift_candidate"
            ]

        def eligibility(result: dict[str, Any]) -> dict[tuple[str, str, int], bool]:
            return {
                (r["entity"], r["time"], r["window"]): r["eligible"]
                for r in result["window_assessment"]["assessments"]
            }

        main = variants["main"]
        main_lookup = {(r["entity"], r["period"]): r for r in main["rows"]}
        main_ordered = [main_lookup[(r["entity"], r["period"])] for r in raw]
        restored = module.transform_cleaner(
            raw, json.loads(json.dumps(main["cleaning"]["model"]))
        )
        invariants = {
            "source_rows_unchanged": raw == module.read_csv(inputs / "raw.csv"),
            "fit_rows_pre16_only": all(
                int(raw[i - 1]["period"]) <= 16 for i in cleaning["fit_rows"]
            ),
            "main_activity_never_imputed": not any(
                "activity" in r.get("_imputed_columns", []) for r in main["rows"]
            ),
            "main_observed_activity_unchanged": all(
                (
                    r["activity"] is None
                    if not source["activity"]
                    else r["activity"] == float(source["activity"])
                )
                for source, r in zip(raw, main_ordered)
            ),
            "main_candidates_equal_uncleaned": numeric(main) == numeric(baseline),
            "filled_activity_candidates_equal_main": numeric(variants["filled-outcome"])
            == numeric(main),
            "filled_activity_eligibility_equal_main": eligibility(
                variants["filled-outcome"]
            )
            == eligibility(main),
            "encoding_features_not_scanned": main["config"]["numeric_columns"]
            == ["activity"]
            and all(
                c.get("metric", "activity") == "activity"
                for c in main["events"]["candidates"]
            ),
            "restored_model_matches_transformation": _record_sha(restored["rows"])
            == main["cleaning"]["report"]["cleaned_records_sha256"],
            "all_1536_truth_rows_preserved": len(truth_lookup) == 1536,
            "all_12_persistent_events_preserved": len(generation["persistent_events"])
            == 12,
        }
        if not all(invariants.values()):
            raise AssertionError(f"Experiment invariant failed: {invariants}")
        evaluation = {
            "schema_version": 1,
            "design": {
                "seed": seed,
                "screen": SCREEN,
                "fit_period_max": 16,
                "fit_rows": cleaning["fit_rows"],
                "evaluated_event_metric": "activity",
                "selected_entities": SELECTED_ENTITIES,
                "truth_use": "Evaluation after methods/models fixed; never in fitting, profiling, thresholds or seed selection.",
                "direction_agreement_required": True,
            },
            "missingness": {
                **generation["counts"],
                "masked_cells_by_column_and_split": {
                    c: dict(
                        Counter(
                            r["split"]
                            for r in generation["masked_cells"]
                            if r["column"] == c
                        )
                    )
                    for c in NUMERIC
                },
            },
            "imputation": imputation,
            "knn_comparison": comparison,
            "events": event_reports,
            "cleaning": {
                name: {
                    "before_missing": result["cleaning"]["report"]["before_missing"],
                    "after_missing": result["cleaning"]["report"]["after_missing"],
                    "filled_cells": len(result["cleaning"]["report"]["filled"]),
                    "unresolved_cells": len(result["cleaning"]["report"]["unresolved"]),
                    "encoded_column_count": len(
                        result["cleaning"]["report"]["encoded_columns"]
                    ),
                    "unknown_category_counts": result["cleaning"]["report"][
                        "unknown_category_counts"
                    ],
                    "oov_counts": result["cleaning"]["report"]["oov_counts"],
                }
                for name, result in variants.items()
            },
            "window_assessments": {
                name: {
                    "config": result["window_assessment"]["config"],
                    "summary": result["window_assessment"]["summary"],
                    "recommendation_counts": dict(
                        Counter(
                            r["action"]
                            for r in result["window_assessment"]["recommendations"]
                        )
                    ),
                }
                for name, result in variants.items()
            },
            "invariants": invariants,
            "limitations": [
                "One fixed synthetic draw, not Monte Carlo performance or causal inference.",
                "MCAR masks are artificially defined; real-data missingness needs a research design.",
                "Imputation errors use retained deliberately masked cells only; absent records cannot be filled.",
                "Fit and heldout errors are separate; only heldout assesses predictions outside fit rows.",
                "Window3 is prespecified and below the five-period noise baseline minimum; inspected larger windows do not change event screening.",
                "False candidates here mean unmatched persistent step events, not proof that all observed patterns are scientifically false.",
                "Only existing Skill-returned candidates after its internal overlap suppression are counted; all are retained.",
            ],
        }
        _json(results / "evaluation.json", evaluation)
        _csv(
            results / "imputation_errors.csv",
            error_rows,
            [
                "case",
                "column",
                "method",
                "entity",
                "period",
                "source_row",
                "split",
                "truth",
                "prediction",
                "error",
                "filled",
            ],
        )
        matplotlib_version = _overview(evaluation, main, results)
    current_hashes = {name: _sha(ROOT / name) for name in SOURCE_FILES}
    if current_hashes != source_hashes:
        raise RuntimeError(
            "Experiment sources changed during execution; rerun in a fresh output directory"
        )
    _json(
        results / "environment.json",
        {
            "python": platform.python_version(),
            "matplotlib": matplotlib_version,
            "source_sha256": source_hashes,
            "generation_seed": seed,
            "plot_rc": {
                "svg.hashsalt": "openai4s-panel-cleaning-synthetic-v1",
                "font.family": "DejaVu Sans",
            },
            "evaluation_truth_usage": evaluation["design"]["truth_use"],
        },
    )
    _index(
        inputs,
        "Synthetic inputs",
        "合成输入",
        "raw.csv is the only fitting/analysis input; truth.csv preserves all generated measurements, including measurement noise, used only for post-fit evaluation. generation.json separates structural absence, independent row deletion and cell masks.",
        "raw.csv 是唯一拟合／分析输入；truth.csv 保留包括测量噪声的完整生成测量，仅用于拟合完成后的评估。generation.json 分别记录结构性缺失、独立删行和单元格遮挡。",
    )
    # Mention the final manifest before hashing the generated directory index.
    _json(results / "artifact_manifest.json", {})
    _index(
        results,
        "Synthetic experiment outputs",
        "合成实验输出",
        "Both branches use the actual Skill with one fixed screen. evaluation.json keeps imputation sample counts, shared-mask comparisons, all numeric candidates and unconditional/eligible-event recovery. verification.json may later record independent checks and is excluded from the run manifest.",
        "两套分支以固定筛选配置执行实际 Skill。evaluation.json 保留插补样本数、相同遮挡比较、全部数值候选和无条件／合格真事件恢复。后续 verification.json 可保存独立验证，不计入运行 manifest。",
    )
    # The optional verifier file needs its direct-file marker before it exists.
    for name in ["README.md", "README_zh.md"]:
        with (results / name).open("a", encoding="utf-8") as handle:
            handle.write(
                "\nOptional independent verification: `verification.json`.\n"
                if name == "README.md"
                else "\n可选独立验证：`verification.json`。\n"
            )
    artifacts = {
        str(path.relative_to(output)): {
            "sha256": _sha(path),
            "bytes": path.stat().st_size,
        }
        for base in [inputs, results]
        for path in sorted(base.rglob("*"))
        if path.is_file()
        and path.name not in {"artifact_manifest.json", "verification.json"}
    }
    _json(
        results / "artifact_manifest.json",
        {
            "schema_version": 1,
            "excluded": ["results/artifact_manifest.json", "results/verification.json"],
            "artifacts": artifacts,
            "source_sha256": source_hashes,
        },
    )
    return evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    evaluation = run(args.output, args.seed)
    print(
        json.dumps(
            {
                "seed": args.seed,
                "missingness": evaluation["missingness"],
                "events": {
                    name: {
                        kind: {
                            key: data[kind][key]
                            for key in ["tp", "fp", "fn", "precision", "recall"]
                        }
                        for kind in ["exact", "tolerance_1"]
                    }
                    for name, data in evaluation["events"].items()
                },
                "invariants": evaluation["invariants"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
