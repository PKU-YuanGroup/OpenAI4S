"""Independently audit the random panel experiment and rerun it offline.

Scientific calculations below use only the standard library. The runner is
executed as a subprocess for the reproducibility check; no cleaning, screening,
matching or metric function is imported from the runner or the Skill.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SEED = 20261007
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
NUMERIC = ("activity", "capacity", "spend", "correlated_metric", "signal_x", "signal_z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def number(value: Any) -> float | None:
    return None if value is None or str(value).strip() == "" else float(value)


def flags(row: dict[str, Any]) -> list[str]:
    value = row.get("_imputed_columns")
    return value if isinstance(value, list) else json.loads(value or "[]")


def tokens(value: str | None) -> list[str]:
    return re.findall(r"\w+", (value or "").casefold(), flags=re.UNICODE)


def close(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None or actual == ""
    return (
        actual is not None
        and actual != ""
        and math.isclose(float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-10)
    )


class Audit:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.failures: list[dict[str, str]] = []
        self.details: dict[str, Any] = {}

    def check(self, group: str, condition: Any, context: str) -> None:
        self.counts[group] += 1
        if not condition:
            self.failures.append({"check_group": group, "context": context})

    def equal(self, group: str, actual: Any, expected: Any, context: str) -> None:
        self.check(group, actual == expected, context)

    def numeric(self, group: str, actual: Any, expected: Any, context: str) -> None:
        self.check(group, close(actual, expected), context)


def verify_model(
    audit: Audit, raw: list[dict[str, str]], model: dict[str, Any], name: str
) -> None:
    """Recalculate fit-only statistics, category maps and TF-IDF state."""
    config = model["config"]
    if name in {"main", "filled-outcome"}:
        expected_imputations = {
            "capacity": "mean",
            "spend": "median",
            "correlated_metric": "knn",
        }
        if name == "filled-outcome":
            expected_imputations["activity"] = "mean"
        audit.equal(
            "cleaning_config", config["imputations"], expected_imputations, name
        )
        audit.equal(
            "cleaning_config", set(config["numeric_columns"]), set(NUMERIC), name
        )
        audit.equal(
            "cleaning_config", config["knn_features"], ["signal_x", "signal_z"], name
        )
        audit.equal(
            "cleaning_config", config["categorical_encodings"]["region"], "onehot", name
        )
        audit.equal(
            "cleaning_config", config["categorical_encodings"]["channel"], "label", name
        )
        audit.equal(
            "cleaning_config",
            config["categorical_encodings"]["plan"]["method"],
            "ordinal",
            name,
        )
        audit.equal(
            "cleaning_config", config["text_encodings"], {"comment": "tfidf"}, name
        )
        audit.equal("cleaning_config", config["unknown_categories"], "indicator", name)
    fit_rows = [i for i, row in enumerate(raw, 1) if int(row["period"]) <= 16]
    audit.equal("fit_selection", model["fit_rows"], fit_rows, name)
    audit.equal("fit_selection", config["group_by"], ["entity"], name)
    audit.check(
        "fit_selection",
        {"entity", "period", "scenario", "treated"}.issubset(
            config["protected_columns"]
        ),
        name + " protected design fields",
    )
    audit.equal("record_hashes", model["source_records_sha256"], digest(raw), name)
    audit.equal(
        "record_hashes",
        model["fit_source_records_sha256"],
        digest([raw[i - 1] for i in fit_rows]),
        name + " fit records",
    )
    audit.equal(
        "record_hashes",
        model["model_sha256"],
        digest({k: v for k, v in model.items() if k != "model_sha256"}),
        name + " model",
    )
    grouped: dict[str, list[int]] = defaultdict(list)
    for i in fit_rows:
        grouped[canonical([raw[i - 1]["entity"]])].append(i)
    audit.equal("fit_donors", set(model["numeric"]["groups"]), set(grouped), name)
    for key, positions in grouped.items():
        bucket = model["numeric"]["groups"][key]
        audit.equal(
            "fit_donors", [d["row"] for d in bucket["donors"]], positions, name + key
        )
        audit.equal("fit_donors", bucket["group_values"], json.loads(key), name + key)
        for donor in bucket["donors"]:
            expected = {
                c: number(raw[donor["row"] - 1].get(c))
                for c in config["numeric_columns"]
            }
            audit.equal(
                "fit_donors", donor["values"], expected, name + str(donor["row"])
            )
        for column in config["numeric_columns"]:
            values = [number(raw[i - 1].get(column)) for i in positions]
            values = [v for v in values if v is not None]
            expected = {
                "means": statistics.mean(values) if values else None,
                "medians": statistics.median(values) if values else None,
                "population_sds": statistics.pstdev(values) if values else None,
                "scales": (
                    statistics.pstdev(values)
                    if values and statistics.pstdev(values)
                    else 1.0
                ),
            }
            for statistic, value in expected.items():
                audit.numeric(
                    "fit_statistics",
                    bucket[statistic][column],
                    value,
                    name + key + column + statistic,
                )
    for column, fitted in model["categorical"].items():
        observed = sorted(
            {raw[i - 1][column] for i in fit_rows if raw[i - 1][column].strip()},
            key=canonical,
        )
        declaration = config["categorical_encodings"][column]
        expected = declaration["order"] if fitted["method"] == "ordinal" else observed
        audit.equal("encoding_fit", fitted["categories"], expected, name + column)
        audit.equal(
            "encoding_fit",
            fitted["mapping"],
            {canonical(v): i for i, v in enumerate(expected)},
            name + column + " mapping",
        )
        audit.check(
            "encoding_fit",
            set(observed).issubset(expected),
            name + column + " observed support",
        )
    for column, fitted in model["text"].items():
        documents = [tokens(raw[i - 1].get(column)) for i in fit_rows]
        counts = Counter(t for doc in documents for t in doc)
        document_counts = Counter(t for doc in documents for t in set(doc))
        vocabulary = sorted(
            sorted(counts, key=lambda t: (-counts[t], t))[: config["text_max_features"]]
        )
        audit.equal(
            "encoding_fit",
            fitted["vocabulary"],
            vocabulary,
            name + column + " vocabulary",
        )
        audit.equal(
            "encoding_fit", fitted["n_documents"], len(documents), name + column
        )
        audit.equal(
            "encoding_fit",
            fitted["document_frequency"],
            {t: document_counts[t] for t in vocabulary},
            name + column,
        )
        for token in vocabulary:
            audit.numeric(
                "encoding_fit",
                fitted["idf"][token],
                math.log((1 + len(documents)) / (1 + document_counts[token])) + 1,
                name + token,
            )


def independent_fill(
    raw: list[dict[str, str]], model: dict[str, Any], position: int, column: str
) -> tuple[float, list[int], list[dict[str, Any]], str]:
    """Recompute donor ranking from raw data, including missing-aware distance."""
    config = model["config"]
    row = raw[position - 1]
    fit = [i for i in model["fit_rows"] if raw[i - 1]["entity"] == row["entity"]]
    donors = [i for i in fit if number(raw[i - 1].get(column)) is not None]
    values = [float(raw[i - 1][column]) for i in donors]
    method = config["imputations"][column]
    if method == "mean":
        return statistics.mean(values), donors, [], method
    if method == "median":
        return statistics.median(values), donors, [], method
    features = [c for c in config["knn_features"] if c != column]
    scales = {}
    for feature in features:
        observed = [
            float(raw[i - 1][feature])
            for i in fit
            if number(raw[i - 1].get(feature)) is not None
        ]
        scales[feature] = statistics.pstdev(observed) or 1.0
    ranked = []
    for i in donors:
        common = [
            c
            for c in features
            if number(row.get(c)) is not None and number(raw[i - 1].get(c)) is not None
        ]
        if not common:
            continue
        squared = sum(
            ((float(row[c]) - float(raw[i - 1][c])) / scales[c]) ** 2 for c in common
        )
        distance = math.sqrt(squared * len(features) / len(common))
        ranked.append((distance, i, common))
    ranked.sort(key=lambda item: (item[0], item[1]))
    selected = ranked[: config["n_neighbors"]]
    if not selected:
        return statistics.mean(values), donors, [], "fitted_mean_fallback"
    if config["weights"] == "distance" and selected[0][0] == 0:
        selected = [entry for entry in selected if entry[0] == 0]
    if config["weights"] == "uniform" or selected[0][0] == 0:
        value = statistics.mean(float(raw[i - 1][column]) for _, i, _ in selected)
    else:
        weights = [1 / distance for distance, _, _ in selected]
        value = sum(
            float(raw[i - 1][column]) * weight
            for (_, i, _), weight in zip(selected, weights)
        ) / sum(weights)
    details = [
        {"row": i, "distance": distance, "common_features": common}
        for distance, i, common in selected
    ]
    return value, [i for _, i, _ in selected], details, method


def verify_cleaned(
    audit: Audit,
    raw: list[dict[str, str]],
    panel: list[dict[str, str]],
    model: dict[str, Any],
    report: dict[str, Any],
    name: str,
) -> None:
    """Compare every transformed field, imputation and encoding with raw records."""
    panel_by_key = {(r["entity"], int(r["period"])): r for r in panel}
    audit.equal("preservation", len(panel), len(raw), name + " row count")
    audit.equal("preservation", len(panel_by_key), len(raw), name + " unique keys")
    filled = {(f["row"], f["column"]): f for f in report["filled"]}
    audit.equal(
        "imputation", len(filled), len(report["filled"]), name + " unique fills"
    )
    unknown = Counter()
    oov_count = Counter()
    oov_unique: dict[str, set[str]] = defaultdict(set)
    oov_rows = Counter()
    knn_count = 0
    for i, row in enumerate(raw, 1):
        saved = panel_by_key[(row["entity"], int(row["period"]))]
        audit.equal(
            "preservation", json.loads(saved["_source_rows"]), [i], name + f" row {i}"
        )
        for column in model["config"]["protected_columns"]:
            audit.equal(
                "preservation", saved[column], row[column], name + f" {i} {column}"
            )
        expected_flags = []
        for column in model["config"]["numeric_columns"]:
            observed = number(row.get(column))
            if observed is not None:
                audit.numeric(
                    "preservation", saved[column], observed, name + f" {i} {column}"
                )
                audit.check(
                    "imputation",
                    (i, column) not in filled,
                    name + f" observed {i} {column}",
                )
            elif column in model["config"]["imputations"]:
                value, donors, distances, method = independent_fill(
                    raw, model, i, column
                )
                evidence = filled[(i, column)]
                audit.numeric(
                    "imputation", saved[column], value, name + f" {i} {column}"
                )
                audit.numeric(
                    "imputation",
                    evidence["value"],
                    value,
                    name + f" evidence {i} {column}",
                )
                audit.equal(
                    "imputation",
                    evidence["donor_rows"],
                    donors,
                    name + f" donors {i} {column}",
                )
                audit.equal(
                    "imputation",
                    evidence["method"],
                    method,
                    name + f" method {i} {column}",
                )
                if distances:
                    knn_count += 1
                    audit.equal(
                        "knn_distances",
                        len(evidence["distance_details"]),
                        len(distances),
                        name + str(i),
                    )
                    for saved_distance, expected in zip(
                        evidence["distance_details"], distances
                    ):
                        audit.equal(
                            "knn_distances",
                            saved_distance["row"],
                            expected["row"],
                            name + str(i),
                        )
                        audit.equal(
                            "knn_distances",
                            saved_distance["common_features"],
                            expected["common_features"],
                            name + str(i),
                        )
                        audit.numeric(
                            "knn_distances",
                            saved_distance["distance"],
                            expected["distance"],
                            name + str(i),
                        )
                expected_flags.append(column)
            else:
                audit.equal(
                    "preservation", saved[column], "", name + f" missing {i} {column}"
                )
        audit.equal("imputation_flags", flags(saved), expected_flags, name + str(i))
        for column, fitted in model["categorical"].items():
            audit.equal(
                "preservation", saved[column], row[column], name + f" {i} {column}"
            )
            missing = not row[column].strip()
            code = None if missing else fitted["mapping"].get(canonical(row[column]))
            is_unknown = not missing and code is None
            unknown[column] += is_unknown
            audit.numeric(
                "encoding_transform",
                saved[fitted["missing_column"]],
                int(missing),
                name + str(i),
            )
            if fitted["unknown_column"]:
                audit.numeric(
                    "encoding_transform",
                    saved[fitted["unknown_column"]],
                    int(is_unknown),
                    name + str(i),
                )
            if fitted["method"] == "onehot":
                for j, generated in enumerate(
                    fitted["output_columns"][: len(fitted["categories"])]
                ):
                    audit.numeric(
                        "encoding_transform",
                        saved[generated],
                        int(code == j),
                        name + str(i),
                    )
            else:
                audit.numeric(
                    "encoding_transform", saved[column + "__code"], code, name + str(i)
                )
        for column, fitted in model["text"].items():
            audit.equal(
                "preservation", saved[column], row[column], name + f" {i} {column}"
            )
            counts = Counter(tokens(row[column]))
            oov = {t: n for t, n in counts.items() if t not in fitted["vocabulary"]}
            oov_count[column] += sum(oov.values())
            oov_unique[column].update(oov)
            oov_rows[column] += bool(oov)
            values = [
                (
                    counts[t] * fitted["idf"][t]
                    if fitted["method"] == "tfidf"
                    else counts[t]
                )
                for t in fitted["vocabulary"]
            ]
            norm = math.sqrt(sum(v * v for v in values))
            if fitted["method"] == "tfidf" and norm:
                values = [v / norm for v in values]
            for generated, expected in zip(fitted["output_columns"], values):
                audit.numeric(
                    "encoding_transform", saved[generated], expected, name + str(i)
                )
            audit.numeric(
                "encoding_transform",
                saved[fitted["missing_column"]],
                int(not row[column].strip()),
                name + str(i),
            )
    for column in model["categorical"]:
        audit.equal(
            "encoding_transform",
            report["unknown_category_counts"][column],
            unknown[column],
            name + column,
        )
    for column in model["text"]:
        expected = {
            "token_count": oov_count[column],
            "unique_token_count": len(oov_unique[column]),
            "row_count": oov_rows[column],
        }
        audit.equal(
            "encoding_transform", report["oov_counts"][column], expected, name + column
        )
    audit.details[name + "_knn_fills_recalculated"] = knn_count
    audit.equal(
        "record_hashes",
        report["source_records_sha256"],
        digest(raw),
        name + " cleaning raw",
    )
    audit.equal(
        "record_hashes",
        report["fit_source_records_sha256"],
        model["fit_source_records_sha256"],
        name + " cleaning fit",
    )
    audit.equal(
        "record_hashes",
        report["model_sha256"],
        model["model_sha256"],
        name + " cleaning model",
    )
    reconstructed = []
    for raw_row in raw:
        saved = panel_by_key[(raw_row["entity"], int(raw_row["period"]))]
        row: dict[str, Any] = dict(raw_row)
        for column in model["config"]["numeric_columns"]:
            row[column] = number(saved[column])
        for column, detail in model["generated_columns"].items():
            value = number(saved[column])
            kind = detail["kind"]
            if value is not None and kind != "text":
                value = int(value)
            row[column] = value
        if flags(saved):
            row["_imputed_columns"] = flags(saved)
        reconstructed.append(row)
    audit.equal(
        "record_hashes",
        report["cleaned_records_sha256"],
        digest(reconstructed),
        name + " cleaned records",
    )
    for column, actual in report["before_missing"].items():
        audit.equal(
            "missingness",
            actual,
            sum(not str(r.get(column, "")).strip() for r in raw),
            name + " before " + column,
        )
    for column, actual in report["after_missing"].items():
        audit.equal(
            "missingness",
            actual,
            sum(r.get(column) is None or r.get(column) == "" for r in reconstructed),
            name + " after " + column,
        )


def verify_windows(
    audit: Audit, panel: list[dict[str, str]], assessment: dict[str, Any], name: str
) -> dict[tuple[str, int, int], bool]:
    groups: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in panel:
        groups[row["entity"]][int(row["period"])] = row
    options = [3, 5, 7, 9]
    audit.equal("window_config", assessment["config"]["requested_window"], 3, name)
    audit.equal("window_config", assessment["config"]["window_options"], options, name)
    audit.equal(
        "window_config",
        assessment["config"]["noise_tolerance"],
        {"activity": 0.5},
        name,
    )
    audit.equal(
        "window_eligibility",
        len(assessment["assessments"]),
        len(panel) * len(options),
        name,
    )
    eligibility = {}
    for a in assessment["assessments"]:
        entity, anchor, width = a["entity"], a["period_index"], a["window"]
        rows = groups[entity]
        pre = [rows[t] for t in range(anchor - width, anchor) if t in rows]
        post = [rows[t] for t in range(anchor, anchor + width) if t in rows]
        pre_missing = sum(number(r["activity"]) is None for r in pre)
        post_missing = sum(number(r["activity"]) is None for r in post)
        pre_imputed = sum("activity" in flags(r) for r in pre)
        post_imputed = sum("activity" in flags(r) for r in post)
        first, last = min(rows), max(rows)
        pre_short = min(width, max(0, first - (anchor - width)))
        post_short = min(width, max(0, anchor + width - 1 - last))
        expected = {
            "pre_observed_periods": len(pre),
            "post_observed_periods": len(post),
            "pre_nonmissing": len(pre) - pre_missing,
            "post_nonmissing": len(post) - post_missing,
            "pre_missing_periods": width - len(pre),
            "post_missing_periods": width - len(post),
            "pre_missing_values": pre_missing,
            "post_missing_values": post_missing,
            "pre_history_shortfall": pre_short,
            "post_history_shortfall": post_short,
            "pre_internal_gap_periods": width - len(pre) - pre_short,
            "post_internal_gap_periods": width - len(post) - post_short,
        }
        if "pre_imputed_values" in a["coverage"]:
            expected.update(
                pre_imputed_values=pre_imputed, post_imputed_values=post_imputed
            )
        context = name + f" {entity}:{anchor}:w{width}"
        audit.equal("window_coverage", a["coverage"], expected, context)
        reasons = [
            reason
            for key, reason in (
                ("pre_history_shortfall", "insufficient_pre_history"),
                ("post_history_shortfall", "insufficient_post_history"),
                ("pre_internal_gap_periods", "calendar_gap_pre"),
                ("post_internal_gap_periods", "calendar_gap_post"),
                ("pre_missing_values", "missing_values_pre"),
                ("post_missing_values", "missing_values_post"),
            )
            if expected[key]
        ]
        if pre_imputed:
            reasons.append("imputed_values_pre")
        if post_imputed:
            reasons.append("imputed_values_post")
        audit.equal("window_eligibility", a["reasons"], reasons, context)
        audit.equal("window_eligibility", a["eligible"], not reasons, context)
        eligibility[(entity, anchor, width)] = not reasons
        audit.check(
            "window_noise",
            (a["pre_statistics"] is None)
            == (len(pre) != width or pre_missing > 0 or pre_imputed > 0),
            context,
        )
        if a["pre_statistics"] is not None:
            values = [float(r["activity"]) for r in pre]
            center = statistics.mean(values)
            median = statistics.median(values)
            x = [t - (width - 1) / 2 for t in range(width)]
            slope = sum(t * (v - center) for t, v in zip(x, values)) / sum(
                t * t for t in x
            )
            residuals = [v - center - slope * t for t, v in zip(x, values)]
            residual_sd = math.sqrt(sum(r * r for r in residuals) / (width - 2))
            residual_center = statistics.mean(residuals)
            residuals = [r - residual_center for r in residuals]
            residual_ss = sum(r * r for r in residuals)
            acf = (
                sum(a * b for a, b in zip(residuals, residuals[1:])) / residual_ss
                if width >= assessment["config"]["min_correlation_periods"]
                and residual_ss > 0
                else None
            )
            expected_stats = {
                "n": width,
                "sd": statistics.stdev(values),
                "variance": statistics.variance(values),
                "mad": statistics.median(abs(v - median) for v in values),
                "linear_slope": slope,
                "trend_span": abs(slope) * (width - 1),
                "residual_sd": residual_sd,
                "lag1_autocorrelation": acf,
                "mean_noise_iid": statistics.stdev(values) / math.sqrt(width),
                "noise_to_tolerance_ratio": statistics.stdev(values)
                / math.sqrt(width)
                / 0.5,
            }
            for key, value in expected_stats.items():
                audit.numeric(
                    "window_noise", a["pre_statistics"][key], value, context + key
                )
    for summary in assessment["summary"]:
        selected = [
            a for a in assessment["assessments"] if a["window"] == summary["window"]
        ]
        audit.equal("window_summary", summary["anchor_count"], len(selected), name)
        audit.equal(
            "window_summary",
            summary["eligible_anchor_count"],
            sum(a["eligible"] for a in selected),
            name,
        )
        audit.equal(
            "window_summary",
            summary["blocked_by_reason"],
            dict(Counter(reason for a in selected for reason in a["reasons"])),
            name,
        )
    return eligibility


def verify_candidates(
    audit: Audit,
    panel: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    eligibility: dict[tuple[str, int, int], bool],
    name: str,
) -> None:
    """Recalculate screen evidence for each exported numeric candidate."""
    rows = {(r["entity"], int(r["period"])): r for r in panel}
    for c in candidates:
        anchor = int(c["period_index"])
        entity = c["entity"]
        audit.equal("candidate_screen", c["kind"], "level_shift_candidate", name)
        audit.equal("candidate_screen", c["metric"], "activity", name)
        audit.check(
            "candidate_screen",
            eligibility[(entity, anchor, 3)],
            name + f" {entity}:{anchor}",
        )
        pre = [float(rows[(entity, t)]["activity"]) for t in range(anchor - 3, anchor)]
        post = [float(rows[(entity, t)]["activity"]) for t in range(anchor, anchor + 3)]
        pre_mean = statistics.mean(pre)
        change = statistics.mean(post) - pre_mean
        sd = statistics.stdev(pre)
        direction = 1 if change > 0 else -1
        increments = [b - a for a, b in zip(pre, pre[1:])]
        typical = statistics.median(increments)
        mad = statistics.median(abs(v - typical) for v in increments)
        innovation = post[0] - pre[-1] - typical
        support = sum(direction * (v - pre_mean) > max(1.0, 1.5 * sd) for v in post)
        context = name + f" {entity}:{anchor}"
        for key, value in {
            "pre_mean": pre_mean,
            "post_mean": statistics.mean(post),
            "change": change,
            "score": abs(change) / sd if sd else None,
            "boundary_innovation": innovation,
            "post_support": support,
            "window": 3,
        }.items():
            audit.numeric("candidate_screen", c[key], value, context + key)
        audit.check(
            "candidate_screen",
            abs(change) > 2 and (not sd or abs(change) / sd >= 3),
            context + " thresholds",
        )
        audit.check(
            "candidate_screen",
            direction * innovation
            > max(2, 3 * 1.4826 * mad, 1e-12 * max(1, *(abs(v) for v in pre + post))),
            context + " boundary",
        )
        audit.check("candidate_screen", support >= 2, context + " persistence")


def match_events(
    candidates: list[dict[str, Any]],
    truths: list[dict[str, Any]],
    eligibility: dict[tuple[str, int, int], bool],
    radius: int,
) -> dict[str, Any]:
    """One-to-one matching using entity, metric, direction and date distance.

    The design has one persistent change per affected entity, so matching cannot
    depend on the order in which different entities' truths are visited.
    """
    used: set[str] = set()
    matches = []
    for truth in truths:
        possible = [
            c
            for c in candidates
            if c["candidate_id"] not in used
            and c["entity"] == truth["entity"]
            and c["metric"] == truth["metric"]
            and abs(int(c["period_index"]) - truth["period_index"]) <= radius
            and (float(c["change"]) > 0) == (truth["direction"] == "up")
        ]
        if not possible:
            continue
        possible.sort(
            key=lambda c: (
                abs(int(c["period_index"]) - truth["period_index"]),
                int(c["period_index"]),
                c["candidate_id"],
            )
        )
        candidate = possible[0]
        used.add(candidate["candidate_id"])
        matches.append(
            {
                "truth_id": truth["truth_id"],
                "candidate_id": candidate["candidate_id"],
                "entity": truth["entity"],
                "truth_time": truth["time"],
                "candidate_time": candidate["time"],
                "date_error": int(candidate["period_index"]) - truth["period_index"],
                "direction_agrees": True,
                "truth_anchor_eligible": eligibility.get(
                    (truth["entity"], truth["period_index"], 3), False
                ),
            }
        )
    tp = len(matches)
    matched_truth = {m["truth_id"] for m in matches}
    eligible_truth = [
        t for t in truths if eligibility.get((t["entity"], t["period_index"], 3), False)
    ]
    eligible_tp = sum(m["truth_anchor_eligible"] for m in matches)
    return {
        "tp": tp,
        "fp": len(candidates) - tp,
        "fn": len(truths) - tp,
        "precision": tp / len(candidates) if candidates else None,
        "recall": tp / len(truths) if truths else None,
        "matches": matches,
        "unmatched_truth": [t for t in truths if t["truth_id"] not in matched_truth],
        "unmatched_candidates": [
            c for c in candidates if c["candidate_id"] not in used
        ],
        "eligible_truth_recovery": {
            "n_eligible": len(eligible_truth),
            "tp": eligible_tp,
            "fn": len(eligible_truth) - eligible_tp,
            "recall": eligible_tp / len(eligible_truth) if eligible_truth else None,
            "matched_ineligible_truth": [
                m for m in matches if not m["truth_anchor_eligible"]
            ],
        },
    }


def verify_event_evaluation(
    audit: Audit,
    event_report: dict[str, Any],
    candidate_csv: list[dict[str, str]],
    truths: list[dict[str, Any]],
    eligibility: dict[tuple[str, int, int], bool],
    assessment: dict[str, Any],
    name: str,
) -> None:
    numeric = [c for c in candidate_csv if c["kind"] == "level_shift_candidate"]
    candidates = event_report["numeric_candidates"]
    audit.equal(
        "event_accounting",
        event_report["truth_events"],
        truths,
        name + " truth registry",
    )
    audit.equal(
        "event_accounting",
        len(candidates),
        len(numeric),
        name + " numeric candidate total",
    )
    audit.equal(
        "event_accounting",
        len({c["candidate_id"] for c in candidates}),
        len(candidates),
        name + " unique candidate ids",
    )
    by_anchor = {
        (a["entity"], a["period_index"]): a
        for a in assessment["assessments"]
        if a["window"] == 3
    }
    expected_eligibility = []
    for truth in truths:
        a = by_anchor.get((truth["entity"], truth["period_index"]))
        expected_eligibility.append(
            {
                "truth_id": truth["truth_id"],
                "entity": truth["entity"],
                "time": truth["time"],
                "eligible": a["eligible"] if a else False,
                "reasons": a["reasons"] if a else ["anchor_record_absent"],
                "coverage": a["coverage"] if a else None,
            }
        )
    audit.equal(
        "event_accounting",
        event_report["eligibility_at_truth"],
        expected_eligibility,
        name + " truth eligibility",
    )
    audit.equal(
        "event_accounting",
        event_report["candidate_kind_counts"],
        dict(Counter(c["kind"] for c in candidate_csv)),
        name + " all event kinds",
    )
    documented = [c for c in candidate_csv if c["kind"] == "documented_event"]
    audit.equal(
        "event_accounting",
        {(c["entity"], c["time"]) for c in documented},
        {(t["entity"], t["time"]) for t in truths},
        name + " supplied known dates",
    )
    audit.equal(
        "event_accounting",
        len(documented),
        len(truths),
        name + " known dates separate from numeric detections",
    )
    audit.check(
        "event_accounting",
        all(c["causal_status"] == "unverified" for c in documented),
        name + " known dates remain supplied evidence",
    )
    expected_scenarios = {
        scenario: sum(
            SCENARIOS[(int(c["entity"]) - 1) // 4] == scenario for c in candidates
        )
        for scenario in SCENARIOS
    }
    audit.equal(
        "event_accounting",
        event_report["scenario_candidate_counts"],
        expected_scenarios,
        name + " all scenario candidate totals",
    )
    for saved, observed in zip(candidates, numeric):
        for key, value in observed.items():
            if isinstance(saved.get(key), (list, dict)):
                audit.equal(
                    "event_accounting", saved[key], json.loads(value), name + key
                )
            elif isinstance(saved.get(key), (int, float)) or saved.get(key) is None:
                audit.numeric(
                    "event_accounting", saved.get(key), number(value), name + key
                )
            else:
                audit.equal("event_accounting", saved.get(key), value, name + key)
    for key, radius in (("exact", 0), ("tolerance_1", 1)):
        expected = match_events(candidates, truths, eligibility, radius)
        actual = event_report[key]
        for field in (
            "tp",
            "fp",
            "fn",
            "matches",
            "unmatched_truth",
            "unmatched_candidates",
            "eligible_truth_recovery",
        ):
            audit.equal(
                "event_matching", actual[field], expected[field], name + key + field
            )
        for field in ("precision", "recall"):
            audit.numeric(
                "event_matching", actual[field], expected[field], name + key + field
            )
        audit.equal(
            "event_matching",
            actual["tp"] + actual["fp"],
            len(candidates),
            name + key + " candidate denominator",
        )
        audit.equal(
            "event_matching",
            actual["tp"] + actual["fn"],
            len(truths),
            name + key + " unconditional truth denominator",
        )
    audit.details[name + "_event_recovery"] = {
        key: {
            field: event_report[key][field]
            for field in (
                "tp",
                "fp",
                "fn",
                "precision",
                "recall",
                "eligible_truth_recovery",
            )
        }
        for key in ("exact", "tolerance_1")
    }


def verify_provenance(audit: Audit, experiment: Path) -> None:
    manifest = read_json(experiment / "results/artifact_manifest.json")
    environment = read_json(experiment / "results/environment.json")
    audit.equal(
        "provenance",
        manifest["excluded"],
        ["results/artifact_manifest.json", "results/verification.json"],
        "manifest exclusions",
    )
    audit.equal(
        "provenance",
        manifest["source_sha256"],
        environment["source_sha256"],
        "environment and manifest source identity",
    )
    expected_sources = {
        str((ROOT / "run_experiment.py").relative_to(REPO)),
        "skills/panel-data-preprocessing/kernel.py",
        "skills/panel-data-preprocessing/SKILL.md",
    }
    audit.check(
        "provenance",
        expected_sources.issubset(manifest["source_sha256"]),
        "runner and Skill source identity",
    )
    for relative, expected in manifest["source_sha256"].items():
        path = REPO / relative
        audit.equal("provenance", sha(path), expected, relative)
    audit.equal(
        "provenance", environment["generation_seed"], SEED, "recorded generation seed"
    )
    audit.equal(
        "provenance",
        environment["python"],
        platform.python_version(),
        "recorded Python environment",
    )
    audit.equal(
        "provenance",
        environment["matplotlib"],
        importlib.metadata.version("matplotlib"),
        "recorded matplotlib environment",
    )
    files = deterministic_files(experiment)
    inventory = set(files) - set(manifest["excluded"])
    audit.equal(
        "artifact_manifest",
        set(manifest["artifacts"]),
        inventory,
        "complete generated file inventory",
    )
    for relative, metadata in manifest["artifacts"].items():
        path = experiment / relative
        audit.equal(
            "artifact_manifest", metadata["sha256"], sha(path), relative + " sha256"
        )
        audit.equal(
            "artifact_manifest",
            metadata["bytes"],
            path.stat().st_size,
            relative + " bytes",
        )
    for name in ("main", "filled-outcome"):
        branch = experiment / "results" / name
        exported = read_json(branch / "manifest.json")
        for filename, detail in exported["outputs"].items():
            audit.equal(
                "branch_manifest",
                detail["sha256"],
                sha(branch / filename),
                name + filename,
            )
        visualization = read_json(branch / "visualization.json")
        for filename, detail in visualization["outputs"].items():
            audit.equal(
                "branch_manifest",
                detail["sha256"],
                sha(branch / filename),
                name + filename,
            )
        selected, excluded = (
            visualization["selected_entities"],
            visualization["excluded_entities"],
        )
        audit.equal(
            "visualization_scope",
            set(selected) | set(excluded),
            {f"{i:03d}" for i in range(1, 33)},
            name,
        )
        audit.equal("visualization_scope", set(selected) & set(excluded), set(), name)
        audit.equal(
            "visualization_scope", visualization["selected_metrics"], ["activity"], name
        )
        audit.details[name + "_visualization"] = {
            "selected_entities": selected,
            "excluded_entity_count": len(excluded),
        }


def error_metrics(errors: list[float]) -> dict[str, Any]:
    return {
        "n_error": len(errors),
        "mae": statistics.mean(abs(e) for e in errors) if errors else None,
        "rmse": math.sqrt(statistics.mean(e * e for e in errors)) if errors else None,
        "bias": statistics.mean(errors) if errors else None,
    }


def imputation_metrics(
    raw: list[dict[str, str]],
    truth: dict[tuple[str, str], dict[str, str]],
    predictions: dict[tuple[int, str], float | None],
    column: str,
) -> dict[str, dict[str, Any]]:
    result = {}
    for scope in ("overall", "fit", "heldout"):
        selected = [
            (i, r)
            for i, r in enumerate(raw, 1)
            if number(r[column]) is None
            and (scope == "overall" or (int(r["period"]) <= 16) == (scope == "fit"))
        ]
        filled = [
            (i, r) for i, r in selected if predictions.get((i, column)) is not None
        ]
        errors = [
            float(predictions[(i, column)])
            - float(truth[(r["entity"], r["period"])][column])
            for i, r in filled
        ]
        result[scope] = {
            "n_masked": len(selected),
            "n_filled": len(filled),
            "fill_rate": len(filled) / len(selected) if selected else None,
            **error_metrics(errors),
        }
    return result


def compare_metrics(audit: Audit, actual: Any, expected: Any, context: str) -> None:
    if isinstance(expected, dict):
        audit.equal("truth_metrics", set(actual), set(expected), context + " keys")
        for key, value in expected.items():
            compare_metrics(audit, actual[key], value, context + "." + key)
    elif isinstance(expected, (int, float)) or expected is None:
        audit.numeric("truth_metrics", actual, expected, context)
    else:
        audit.equal("truth_metrics", actual, expected, context)


def verify_imputation_evaluation(
    audit: Audit,
    raw: list[dict[str, str]],
    truth_rows: list[dict[str, str]],
    evaluation: dict[str, Any],
    models: dict[str, dict[str, Any]],
    reports: dict[str, dict[str, Any]],
    error_csv: list[dict[str, str]],
) -> None:
    truth = {(r["entity"], r["period"]): r for r in truth_rows}
    predictions_by_case = {}
    for case in ("main", "filled-outcome"):
        predictions = {
            (f["row"], f["column"]): float(f["value"]) for f in reports[case]["filled"]
        }
        predictions_by_case[case] = predictions
        for column, actual in evaluation["imputation"][case].items():
            expected = {
                "method": models[case]["config"]["imputations"][column],
                **imputation_metrics(raw, truth, predictions, column),
            }
            compare_metrics(audit, actual, expected, case + "." + column)
    method_predictions = {}
    for method in ("knn", "mean", "median"):
        predictions = {}
        for i, row in enumerate(raw, 1):
            if number(row["correlated_metric"]) is not None:
                continue
            if method == "knn":
                value = independent_fill(raw, models["main"], i, "correlated_metric")[0]
            else:
                donors = [
                    float(r["correlated_metric"])
                    for r in raw
                    if r["entity"] == row["entity"]
                    and int(r["period"]) <= 16
                    and number(r["correlated_metric"]) is not None
                ]
                value = (
                    statistics.mean(donors)
                    if method == "mean"
                    else statistics.median(donors)
                )
            predictions[(i, "correlated_metric")] = value
        method_predictions[method] = predictions
        expected = {
            "method": method,
            **imputation_metrics(raw, truth, predictions, "correlated_metric"),
        }
        compare_metrics(
            audit,
            evaluation["knn_comparison"]["methods"][method],
            expected,
            "knn comparison " + method,
        )
    for scope in ("overall", "fit", "heldout"):
        selected = [
            (i, row)
            for i, row in enumerate(raw, 1)
            if number(row["correlated_metric"]) is None
            and (scope == "overall" or (int(row["period"]) <= 16) == (scope == "fit"))
            and all(
                method_predictions[m].get((i, "correlated_metric")) is not None
                for m in method_predictions
            )
        ]
        expected = {"n_common": len(selected), "methods": {}}
        for method, predictions in method_predictions.items():
            errors = [
                float(predictions[(i, "correlated_metric")])
                - float(truth[(row["entity"], row["period"])]["correlated_metric"])
                for i, row in selected
            ]
            expected["methods"][method] = error_metrics(errors)
        compare_metrics(
            audit,
            evaluation["knn_comparison"]["common_filled"][scope],
            expected,
            "common filled " + scope,
        )
    audit.details["imputation_truth_metrics"] = evaluation["imputation"]
    expected_errors = {}
    for case, predictions in predictions_by_case.items():
        columns = evaluation["imputation"][case]
        for i, row in enumerate(raw, 1):
            for column in columns:
                if number(row[column]) is None:
                    method = models[case]["config"]["imputations"][column]
                    expected_errors[
                        (f"{case}_{column}_{method}", method, i, column)
                    ] = predictions.get((i, column))
    for method, predictions in method_predictions.items():
        for (i, column), prediction in predictions.items():
            expected_errors[("target_comparison_" + method, method, i, column)] = (
                prediction
            )
    for row in error_csv:
        position = int(row["source_row"])
        source = raw[position - 1]
        column = row["column"]
        audit.equal("truth_error_rows", row["entity"], source["entity"], str(position))
        audit.equal("truth_error_rows", row["period"], source["period"], str(position))
        audit.equal(
            "truth_error_rows",
            row["split"],
            "fit" if int(source["period"]) <= 16 else "heldout",
            str(position),
        )
        actual_truth = float(truth[(row["entity"], row["period"])][column])
        audit.numeric("truth_error_rows", row["truth"], actual_truth, str(position))
        prediction = number(row["prediction"])
        case = row["case"]
        expected_prediction = expected_errors.get(
            (case, row["method"], position, column)
        )
        audit.numeric(
            "truth_error_rows",
            prediction,
            expected_prediction,
            f"{case}:{position}:{column}",
        )
        audit.numeric(
            "truth_error_rows",
            row["error"],
            prediction - actual_truth if prediction is not None else None,
            str(position),
        )
        audit.equal(
            "truth_error_rows",
            row["filled"].casefold(),
            str(prediction is not None).casefold(),
            str(position),
        )
    audit.equal(
        "truth_error_rows",
        len(error_csv),
        len(expected_errors),
        "all masked evaluation cell rows",
    )
    observed_keys = {
        (r["case"], r["method"], int(r["source_row"]), r["column"]) for r in error_csv
    }
    audit.equal(
        "truth_error_rows",
        len(observed_keys),
        len(error_csv),
        "unique error evaluation cells",
    )
    audit.equal(
        "truth_error_rows",
        observed_keys,
        set(expected_errors),
        "complete error evaluation cell scope",
    )


def verify_inputs(
    audit: Audit,
    raw: list[dict[str, str]],
    truth: list[dict[str, str]],
    generation: dict[str, Any],
) -> None:
    ids = {f"{i:03d}" for i in range(1, 33)}
    audit.equal("generator_design", generation["seed"], SEED, "fixed seed")
    parameters = generation["parameters"]
    prescribed = {
        "n_entities": 32,
        "n_periods": 48,
        "entities_per_scenario": 4,
        "scenarios": list(SCENARIOS),
        "persistent_event_period": 25,
        "persistent_effects": {"jump_up": 8.0, "jump_down": -7.0, "noisy_jump": 5.0},
        "linear_slope": 0.2,
        "spike_size": 8.0,
        "seasonal_amplitude": 3.0,
        "seasonal_period": 8,
        "calendar_delete_probability": 0.025,
        "late_entry_first_observed_period": 9,
        "fit_period_max": 16,
        "cell_mask_probabilities": {
            "activity": 0.07,
            "capacity": 0.10,
            "spend": 0.10,
            "correlated_metric": 0.10,
            "signal_x": 0.02,
            "signal_z": 0.02,
        },
    }
    for key, expected in prescribed.items():
        audit.equal(
            "generator_design", parameters[key], expected, "prespecified " + key
        )
    audit.equal(
        "generator_design", {r["entity"] for r in truth}, ids, "truth string IDs"
    )
    audit.equal("generator_design", {r["entity"] for r in raw}, ids, "raw string IDs")
    audit.equal("generator_design", len(truth), 32 * 48, "complete truth grid")
    truth_by_key = {(r["entity"], r["period"]): r for r in truth}
    raw_by_key = {(r["entity"], r["period"]): r for r in raw}
    audit.equal("generator_design", len(truth_by_key), len(truth), "unique truth grid")
    audit.equal("generator_design", len(raw_by_key), len(raw), "unique raw grid")
    expected_truth_keys = {(entity, str(t)) for entity in ids for t in range(1, 49)}
    audit.equal(
        "generator_design",
        set(truth_by_key),
        expected_truth_keys,
        "periods 1 through 48 per entity",
    )
    expected_structural = {
        (f"{i:03d}", str(t)) for i in range(29, 33) for t in range(1, 9)
    }
    structural = {
        (r["entity"], str(r["period"])) for r in generation["structural_absence"]
    }
    deleted = {
        (r["entity"], str(r["period"])) for r in generation["calendar_deleted_records"]
    }
    audit.equal(
        "calendar",
        structural,
        expected_structural,
        "late-entry absence at periods 1 through 8",
    )
    audit.equal(
        "calendar",
        structural & deleted,
        set(),
        "structural absence separate from calendar deletions",
    )
    audit.check(
        "calendar",
        deleted.issubset(expected_truth_keys),
        "calendar deletions on truth grid",
    )
    audit.equal(
        "calendar",
        set(raw_by_key),
        expected_truth_keys - structural - deleted,
        "observed calendar equals registered omissions",
    )
    masked = {(int(r["raw_row"]), r["column"]): r for r in generation["masked_cells"]}
    expected_missing = {
        (i, column)
        for i, r in enumerate(raw, 1)
        for column in NUMERIC
        if number(r[column]) is None
    }
    audit.equal(
        "missingness", set(masked), expected_missing, "registered numeric missing cells"
    )
    for (i, column), metadata in masked.items():
        row = raw[i - 1]
        audit.equal("missingness", metadata["entity"], row["entity"], f"{i}:{column}")
        audit.equal(
            "missingness", str(metadata["period"]), row["period"], f"{i}:{column}"
        )
        audit.equal(
            "missingness",
            metadata["split"],
            "fit" if int(row["period"]) <= 16 else "heldout",
            f"{i}:{column}",
        )
    for row in truth:
        scenario = SCENARIOS[(int(row["entity"]) - 1) // 4]
        audit.equal("generator_design", row["scenario"], scenario, row["entity"])
        audit.check(
            "generator_design",
            all(
                number(row[c]) is not None and math.isfinite(float(row[c]))
                for c in NUMERIC
            ),
            "finite full truth " + row["entity"] + ":" + row["period"],
        )
        expected_treated = int(
            scenario in {"jump_up", "jump_down", "noisy_jump"}
            and int(row["period"]) >= 25
        )
        audit.equal(
            "generator_design",
            row["treated"],
            str(expected_treated),
            "treatment registry " + row["entity"] + ":" + row["period"],
        )
    for i, row in enumerate(raw, 1):
        original = truth_by_key[(row["entity"], row["period"])]
        for column, value in row.items():
            if (i, column) not in masked:
                audit.equal(
                    "raw_truth_preservation", value, original[column], f"{i}:{column}"
                )
    expected_events = []
    for i in range(5, 17):
        scenario = SCENARIOS[(i - 1) // 4]
        effect = {"jump_up": 8.0, "jump_down": -7.0, "noisy_jump": 5.0}[scenario]
        expected_events.append(
            (f"{i:03d}", scenario, 25, effect, "up" if effect > 0 else "down")
        )
    actual_events = [
        (e["entity"], e["scenario"], e["period_index"], e["effect"], e["direction"])
        for e in generation["persistent_events"]
    ]
    audit.equal(
        "generator_design",
        actual_events,
        expected_events,
        "persistent truth events exclude spike/trend/seasonality",
    )
    audit.details["generated_sample"] = {
        "seed": SEED,
        "truth_rows": len(truth),
        "raw_rows": len(raw),
        "entities": len(ids),
        "structural_absence": len(structural),
        "calendar_deleted_records": len(deleted),
        "numeric_masked_cells": dict(Counter(c for _, c in expected_missing)),
        "fit_rows": sum(int(r["period"]) <= 16 for r in raw),
        "heldout_rows": sum(int(r["period"]) > 16 for r in raw),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=ROOT,
        help="Generated bundle root containing inputs/ and results/",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Verification JSON destination (default results/verification.json)",
    )
    args = parser.parse_args()
    experiment = args.experiment.resolve()
    audit = Audit()
    raw = read_csv(experiment / "inputs/raw.csv")
    truth = read_csv(experiment / "inputs/truth.csv")
    generation = read_json(experiment / "inputs/generation.json")
    evaluation = read_json(experiment / "results/evaluation.json")
    verify_inputs(audit, raw, truth, generation)
    for name, records in (("raw.csv", raw), ("truth.csv", truth)):
        audit.equal(
            "input_hashes",
            generation["inputs"][name]["sha256"],
            sha(experiment / "inputs" / name),
            name + " original file bytes",
        )
        audit.equal(
            "input_hashes",
            generation["inputs"][name]["parsed_records_sha256"],
            digest(records),
            name + " parsed records",
        )
    verify_provenance(audit, experiment)
    baseline = [r for r in raw if int(r["period"]) <= 16]
    profile = read_json(experiment / "results/fit_profile.json")
    audit.equal(
        "fit_profile",
        profile["source_records_sha256"],
        digest(baseline),
        "profile uses baseline raw rows",
    )
    audit.equal(
        "fit_profile", profile["row_count"], len(baseline), "profile baseline count"
    )
    decisions = read_json(experiment / "results/method_decisions.json")
    fit_positions = [i for i, r in enumerate(raw, 1) if int(r["period"]) <= 16]
    audit.equal("fit_profile", decisions["fit_rows"], fit_positions, "method fit rows")
    audit.equal(
        "fit_profile",
        decisions["fit_source_records_sha256"],
        digest(baseline),
        "method fit profile",
    )
    audit.equal(
        "fit_profile",
        decisions["fit_counts_by_entity"],
        dict(Counter(r["entity"] for r in baseline)),
        "method fit group counts",
    )
    audit.equal(
        "evaluation_design",
        evaluation["design"]["fit_rows"],
        fit_positions,
        "evaluation fit scope",
    )
    audit.equal(
        "evaluation_design", evaluation["design"]["seed"], SEED, "evaluation seed"
    )
    audit.equal(
        "evaluation_design",
        evaluation["design"]["screen"],
        generation["evaluation_policy"]["primary_screen"],
        "prespecified screen reused",
    )
    expected_missingness = {
        **generation["counts"],
        "masked_cells_by_column_and_split": {
            c: dict(
                Counter(
                    m["split"] for m in generation["masked_cells"] if m["column"] == c
                )
            )
            for c in NUMERIC
        },
    }
    audit.equal(
        "evaluation_design",
        evaluation["missingness"],
        expected_missingness,
        "evaluation denominators",
    )
    models, reports, panels, assessments, eligibility = {}, {}, {}, {}, {}
    for name in ("main", "filled-outcome"):
        branch = experiment / "results" / name
        models[name] = read_json(branch / "cleaning_model.json")
        reports[name] = read_json(branch / "cleaning_report.json")
        panels[name] = read_csv(branch / "panel.csv")
        assessments[name] = read_json(branch / "window_assessment.json")
        verify_model(audit, raw, models[name], name)
        verify_cleaned(audit, raw, panels[name], models[name], reports[name], name)
        eligibility[name] = verify_windows(audit, panels[name], assessments[name], name)
        event_report = evaluation["events"][name]
        verify_candidates(
            audit,
            panels[name],
            event_report["numeric_candidates"],
            eligibility[name],
            name,
        )
        verify_event_evaluation(
            audit,
            event_report,
            read_csv(branch / "candidate_events.csv"),
            generation["persistent_events"],
            eligibility[name],
            assessments[name],
            name,
        )
        manifest = read_json(branch / "manifest.json")
        audit.equal(
            "record_hashes",
            manifest["raw_source_records_sha256"],
            digest(raw),
            name + " raw manifest",
        )
        audit.equal(
            "record_hashes",
            manifest["source_records_sha256"],
            reports[name]["cleaned_records_sha256"],
            name + " cleaned manifest",
        )
        audit.equal(
            "record_hashes",
            manifest["cleaning_model_sha256"],
            models[name]["model_sha256"],
            name + " fitted model manifest",
        )
        audit.equal(
            "record_hashes",
            manifest["configuration_sha256"],
            digest(
                {
                    "panel": manifest["config"],
                    "events": manifest["event_config"],
                    "cleaning_model_sha256": models[name]["model_sha256"],
                }
            ),
            name + " configuration hash",
        )
        audit.equal(
            "screening_config",
            manifest["config"]["numeric_columns"],
            ["activity"],
            name + " scan only declared outcome",
        )
        for key, expected in {
            "window": 3,
            "threshold": 3,
            "min_abs_change": 2,
            "treatment": "treated",
        }.items():
            audit.equal(
                "screening_config", manifest["event_config"][key], expected, name + key
            )
    audit.equal(
        "branch_invariance",
        eligibility["main"],
        eligibility["filled-outcome"],
        "filling outcomes adds no eligible windows",
    )
    audit.equal(
        "branch_invariance",
        models["main"]["categorical"],
        models["filled-outcome"]["categorical"],
        "frozen categorical schema",
    )
    audit.equal(
        "branch_invariance",
        models["main"]["text"],
        models["filled-outcome"]["text"],
        "frozen text schema",
    )
    audit.equal(
        "branch_invariance",
        evaluation["events"]["main"]["numeric_candidates"],
        evaluation["events"]["filled-outcome"]["numeric_candidates"],
        "outcome filling does not change numeric candidates",
    )
    main_assessment = {
        (a["entity"], a["period_index"], a["window"]): a
        for a in assessments["main"]["assessments"]
    }
    reason_changes = 0
    for a in assessments["filled-outcome"]["assessments"]:
        key = (a["entity"], a["period_index"], a["window"])
        reasons = [
            r.replace("missing_values_", "imputed_values_")
            for r in main_assessment[key]["reasons"]
        ]
        audit.equal(
            "branch_invariance",
            set(a["reasons"]),
            set(reasons),
            str(key) + " missing versus imputed exclusion",
        )
        reason_changes += a["reasons"] != main_assessment[key]["reasons"]
    audit.details["missing_to_imputed_reason_changes"] = reason_changes
    verify_imputation_evaluation(
        audit,
        raw,
        truth,
        evaluation,
        models,
        reports,
        read_csv(experiment / "results/imputation_errors.csv"),
    )
    verify_rerun(audit, experiment, SEED)
    failed_counts = Counter(f["check_group"] for f in audit.failures)
    report = {
        "verdict": "PASS" if not audit.failures else "FAIL",
        "checks_total": sum(audit.counts.values()),
        "checks_passed": sum(audit.counts.values()) - len(audit.failures),
        "check_groups": {
            k: {"total": n, "passed": n - failed_counts[k]}
            for k, n in sorted(audit.counts.items())
        },
        "failures": audit.failures,
        "details": audit.details,
        "verifier_sha256": sha(Path(__file__).resolve()),
        "scope": "Independent arithmetic and invariants for one fixed-seed synthetic realization; no claim of calibrated detection performance, causal identification, or missingness uncertainty.",
    }
    report_path = (args.report or experiment / "results/verification.json").resolve()
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "checks_passed": report["checks_passed"],
                "checks_total": report["checks_total"],
                "failures": report["failures"][:20],
                "report": str(report_path),
                "reproducibility": audit.details.get("reproducibility"),
            },
            indent=2,
        )
    )
    if audit.failures:
        raise SystemExit(1)


def all_files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def deterministic_files(root: Path) -> dict[str, str]:
    """Every generated input/result file, with verification excluded explicitly."""
    result = {}
    for directory in (root / "inputs", root / "results"):
        for p in sorted(directory.rglob("*")):
            if p.is_file() and p != root / "results/verification.json":
                result[str(p.relative_to(root))] = sha(p)
    return result


def verify_rerun(audit: Audit, experiment: Path, seed: int) -> None:
    before = all_files(experiment)
    temporary = Path(
        tempfile.mkdtemp(prefix="openai4s-panel-verify-", dir="/private/tmp")
    )
    fresh = temporary / "bundle"
    env = {**os.environ, "MPLCONFIGDIR": str(temporary / "matplotlib")}
    command = [
        sys.executable,
        str(ROOT / "run_experiment.py"),
        "--seed",
        str(seed),
        "--output",
        str(fresh),
    ]
    completed = subprocess.run(
        command, env=env, text=True, capture_output=True, timeout=180, check=False
    )
    audit.check(
        "fresh_rerun",
        completed.returncode == 0,
        completed.stdout[-2000:] + completed.stderr[-2000:],
    )
    if completed.returncode:
        return
    original_files, fresh_files = deterministic_files(experiment), deterministic_files(
        fresh
    )
    audit.equal(
        "fresh_rerun",
        set(original_files),
        set(fresh_files),
        "generated artifact inventory",
    )
    mismatches = []
    for path in sorted(set(original_files) | set(fresh_files)):
        equal = original_files.get(path) == fresh_files.get(path)
        audit.check("artifact_byte_equality", equal, path)
        if not equal:
            mismatches.append(path)
    refusal_before = all_files(fresh)
    refused = subprocess.run(
        command, env=env, text=True, capture_output=True, timeout=180, check=False
    )
    audit.check("output_refusal", refused.returncode != 0, "repeat output must fail")
    audit.equal(
        "output_refusal",
        all_files(fresh),
        refusal_before,
        "repeat output preserves every existing file",
    )
    audit.equal(
        "output_refusal",
        all_files(experiment),
        before,
        "verification rerun preserves original bundle",
    )
    audit.details["reproducibility"] = {
        "files_compared": len(original_files),
        "file_types": dict(Counter(Path(p).suffix for p in original_files)),
        "mismatched_artifacts": mismatches,
        "rerun_output": str(fresh),
        "scope": "All files in generated inputs/ and results/, including CSV, JSON, PNG, SVG and bilingual README files. Excludes results/verification.json written after generation; root prose and source scripts are outside the runner output. File timestamps are not compared.",
        "repeat_output_returncode": refused.returncode,
    }


if __name__ == "__main__":
    main()
