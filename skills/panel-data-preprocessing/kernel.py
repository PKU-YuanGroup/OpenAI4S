"""Explicit panel construction and exploratory event screening, using stdlib."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

_RESERVED = {"_period_index", "_source_rows"}
_IMPUTED = "_imputed_columns"
_FREQUENCIES = {"year", "quarter", "month", "day", "integer"}
_AGGREGATIONS = {"sum", "mean", "min", "max"}


def _missing(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, float) and math.isnan(value))
    )


def _number(value: Any, column: str) -> float | None:
    if _missing(value):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{column}: boolean is not a numeric measurement")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column}: invalid numeric value {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{column}: non-finite numeric value {value!r}")
    return number


def _period(value: Any, frequency: str) -> tuple[int, str]:
    if frequency not in _FREQUENCIES:
        raise ValueError(f"unsupported frequency: {frequency!r}")
    if isinstance(value, bool) or _missing(value):
        raise ValueError(f"invalid {frequency} time: {value!r}")
    text = value.isoformat() if isinstance(value, date) else str(value).strip()
    if frequency == "integer":
        if not re.fullmatch(r"-?\d+", text):
            raise ValueError(f"integer time requires whole-number labels: {text!r}")
        index = int(text)
        return index, str(index)
    if re.fullmatch(r"\d{4}", text) and frequency == "year":
        year = int(text)
        date(year, 1, 1)
        return year, f"{year:04d}"
    quarter = re.fullmatch(r"(\d{4})-?Q([1-4])", text, re.I)
    if quarter and frequency == "quarter":
        year, q = map(int, quarter.groups())
        date(year, 1, 1)
        return year * 4 + q - 1, f"{year:04d}Q{q}"
    if re.fullmatch(r"\d{4}-\d{2}", text):
        if frequency not in {"year", "quarter", "month"}:
            raise ValueError("day frequency requires an ISO date, not a month")
        parsed = date.fromisoformat(text + "-01")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        parsed = date.fromisoformat(text)
    else:
        raise ValueError(f"ambiguous or unsupported time label: {text!r}")
    if frequency == "year":
        return parsed.year, f"{parsed.year:04d}"
    if frequency == "quarter":
        q = (parsed.month - 1) // 3 + 1
        return parsed.year * 4 + q - 1, f"{parsed.year:04d}Q{q}"
    if frequency == "month":
        return parsed.year * 12 + parsed.month - 1, parsed.strftime("%Y-%m")
    return parsed.toordinal(), parsed.isoformat()


def _period_label(index: int, frequency: str) -> str:
    if frequency in {"year", "integer"}:
        return str(index)
    if frequency == "quarter":
        year, q = divmod(index, 4)
        return f"{year:04d}Q{q + 1}"
    if frequency == "month":
        year, month = divmod(index, 12)
        return f"{year:04d}-{month + 1:02d}"
    return date.fromordinal(index).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _entity(value: Any) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or _missing(value):
        raise ValueError("entity IDs must be non-empty strings or integers")
    return value


def read_csv(path: str | Path) -> list[dict[str, str]]:
    """Read UTF-8 CSV without inference; preserve IDs such as '001'."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        if (
            not columns
            or len(columns) != len(set(columns))
            or any(not c for c in columns)
        ):
            raise ValueError("CSV requires unique, non-empty column headers")
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError("CSV row width does not match its header")
        return rows


def _clean_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError("rows must be a non-empty sequence of mappings")
    if any(not isinstance(r, Mapping) for r in rows):
        raise ValueError("every row must be a mapping")
    if any(not isinstance(c, str) or not c for r in rows for c in r):
        raise ValueError("column names must be non-empty strings")
    if any("_imputed_columns" in r for r in rows):
        raise ValueError("input contains reserved cleaning metadata _imputed_columns")
    return [dict(r) for r in rows]


def _clean_columns(columns: Sequence[str], name: str) -> list[str]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
        raise ValueError(f"{name} must be a sequence of unique column names")
    values = list(columns)
    if any(not isinstance(c, str) or not c for c in values) or len(values) != len(
        set(values)
    ):
        raise ValueError(f"{name} must contain unique non-empty column names")
    return values


def _clean_scalar(value: Any, column: str) -> Any:
    if (
        not isinstance(value, (str, int, float, bool))
        or isinstance(value, float)
        and not math.isfinite(value)
    ):
        raise ValueError(f"{column}: category/group values must be finite JSON scalars")
    return value


def _clean_tokens(value: Any, column: str) -> list[str]:
    if _missing(value):
        return []
    if not isinstance(value, str):
        raise ValueError(f"{column}: text encoding requires strings or missing values")
    return re.findall(r"\w+", value.casefold(), flags=re.UNICODE)


def _clean_group(
    row: Mapping[str, Any], columns: Sequence[str]
) -> tuple[str, list[Any]]:
    values = []
    for column in columns:
        if column not in row or _missing(row[column]):
            raise ValueError(
                f"{column}: grouping values must be present and non-missing"
            )
        values.append(_clean_scalar(row[column], column))
    return _canonical(values), values


def profile_cleaning(
    rows: Sequence[Mapping[str, Any]],
    *,
    protected_columns: Sequence[str] = (),
    numeric_columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Suggest cleaning choices from types/distributions; never alter the input.

    Identifier-like numbers, category/text ambiguity and high cardinality are
    questions for the researcher, not permission to coerce or encode a column.
    """
    original = _clean_rows(rows)
    protected = _clean_columns(protected_columns, "protected_columns")
    explicit = (
        None
        if numeric_columns is None
        else _clean_columns(numeric_columns, "numeric_columns")
    )
    columns = sorted({c for r in original for c in r})
    if (
        set(protected) - set(columns)
        or explicit is not None
        and set(explicit) - set(columns)
    ):
        raise ValueError("profile columns refer to an unknown input column")
    profiles, questions = {}, []
    likely_numeric = 0
    for column in columns:
        if column in protected or explicit is not None and column not in explicit:
            continue
        observed = [r.get(column) for r in original if not _missing(r.get(column))]
        numeric = []
        for value in observed:
            try:
                numeric.append(_number(value, column))
            except ValueError:
                numeric = []
                break
        if observed and len(numeric) == len(observed):
            likely_numeric += 1
    for column in columns:
        observed = [r.get(column) for r in original if not _missing(r.get(column))]
        unique = len({_canonical(v) for v in observed})
        numeric = []
        is_explicit = explicit is not None and column in explicit
        if is_explicit:
            numeric = [_number(v, column) for v in observed]
        elif observed:
            for value in observed:
                try:
                    numeric.append(_number(value, column))
                except ValueError:
                    numeric = []
                    break
        inferred, methods, reason, distribution, pending = "ambiguous", [], "", None, []
        if column in protected:
            inferred, reason = (
                "protected",
                "Protected identifiers, time and treatment columns remain unchanged.",
            )
        elif not observed:
            inferred = "numeric" if is_explicit else "unknown_all_missing"
            reason = "No observed values identify a distribution or fitted fill value."
            pending.append(
                f"{column}: what is the intended type, and is an external source available for entirely missing values?"
            )
        elif numeric and len(numeric) == len(observed):
            inferred = "numeric" if is_explicit else "numeric_candidate"
            center, median = statistics.mean(numeric), statistics.median(numeric)
            sd = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
            q1, _, q3 = (
                statistics.quantiles(numeric, n=4, method="inclusive")
                if len(numeric) > 1
                else [numeric[0]] * 3
            )
            iqr = q3 - q1
            outliers = sum(v < q1 - 1.5 * iqr or v > q3 + 1.5 * iqr for v in numeric)
            distribution = {
                "n": len(numeric),
                "mean": center,
                "median": median,
                "sd": sd,
                "min": min(numeric),
                "max": max(numeric),
                "q1": q1,
                "q3": q3,
                "iqr": iqr,
                "iqr_outlier_count": outliers,
            }
            if any(not math.isfinite(v) for v in distribution.values()):
                raise ValueError(f"profile statistics overflow: {column}")
            robust = outliers > 0 or sd > 0 and abs(center - median) > 0.3 * sd
            methods = ["median", "mean"] if robust else ["mean", "median"]
            if likely_numeric > 1:
                methods.append("knn")
            reason = (
                "Median deserves review because of asymmetric values or IQR extremes; no observations are deleted."
                if robust
                else "Mean or median may suit a reasonably symmetric distribution; choose a scientifically justified missingness model."
            )
            if "knn" in methods:
                reason += " KNN is an option only with meaningful numeric similarity features and enough observed fit donors."
            if not is_explicit:
                pending.append(
                    f"{column}: confirm this is a measurement, not a numeric identifier, code or date; specify units before numeric conversion."
                )
            if any(
                isinstance(v, str) and re.fullmatch(r"0\d+", v.strip())
                for v in observed
            ):
                pending.append(
                    f"{column}: leading-zero strings may be identifiers; preserve them unless the researcher confirms a numeric measurement."
                )
        elif all(isinstance(v, bool) for v in observed):
            inferred, methods = "boolean", ["onehot", "label"]
            reason = "Boolean values may be categories or treatment indicators; confirm their role."
            pending.append(
                f"{column}: is this a protected treatment flag or an explanatory category?"
            )
        elif all(isinstance(v, str) for v in observed):
            lengths = [len(_clean_tokens(v, column)) for v in observed]
            text_like = statistics.median(lengths) >= 4
            inferred = "text_candidate" if text_like else "categorical_candidate"
            methods = (
                ["count", "tfidf"] if text_like else ["onehot", "label", "ordinal"]
            )
            reason = "Long strings suggest text features; short repeated strings suggest categories. The interpretation requires researcher confirmation."
            pending.append(
                f"{column}: are these nominal categories, ordered categories, free text or identifiers?"
            )
            if unique > 50 or unique / len(observed) > 0.8:
                pending.append(
                    f"{column}: high cardinality may indicate an identifier or require vocabulary/feature limits; should this column be encoded?"
                )
            if any(re.search(r"[\u3400-\u9fff]", v) for v in observed):
                pending.append(
                    f"{column}: Chinese text must be segmented upstream; Unicode word tokenization does not segment Chinese sentences."
                )
        else:
            reason = (
                "Mixed or structured values require an explicit type/recoding decision."
            )
            pending.append(
                f"{column}: specify a scalar numeric/category/text representation; no automatic coercion will be applied."
            )
        questions.extend(pending)
        profiles[column] = {
            "inferred_type": inferred,
            "missing_count": len(original) - len(observed),
            "unique_count": unique,
            "distribution": distribution,
            "recommended_methods": methods,
            "recommendation_reason": reason,
            "questions": pending,
        }
    return {
        "schema_version": 1,
        "source_records_sha256": _digest(original),
        "row_count": len(original),
        "protected_columns": protected,
        "columns": profiles,
        "questions": questions,
        "decision_required": {
            "basis": ["researcher", "data_profile"],
            "rationale": "Record why the chosen methods suit variable types, distributions and the research design.",
        },
        "limitations": [
            "Recommendations do not modify data or establish that missingness is ignorable.",
            "A profile of validation/post-event values must not select training methods; profile only the intended calibration sample.",
        ],
    }


def fit_cleaner(
    rows: Sequence[Mapping[str, Any]],
    *,
    fit_rows: Sequence[int],
    numeric_columns: Sequence[str],
    imputations: Mapping[str, str] | None = None,
    categorical_encodings: Mapping[str, str | Mapping[str, Any]] | None = None,
    text_encodings: Mapping[str, str] | None = None,
    protected_columns: Sequence[str] = (),
    group_by: Sequence[str] = (),
    knn_features: Sequence[str] | None = None,
    n_neighbors: int = 5,
    weights: str = "uniform",
    unknown_categories: str = "error",
    text_max_features: int = 1000,
    max_generated_features: int = 1000,
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Fit explicitly chosen cleaning rules on explicit 1-based source positions.

    All donors, scales, means, categories and token statistics use fit rows
    only. No imputation or encoding method is inferred from the input.
    """
    original = _clean_rows(rows)
    columns = {c for r in original for c in r}
    numeric = _clean_columns(numeric_columns, "numeric_columns")
    protected = _clean_columns(protected_columns, "protected_columns")
    groups = _clean_columns(group_by, "group_by")
    features = (
        numeric[:]
        if knn_features is None
        else _clean_columns(knn_features, "knn_features")
    )
    if set(numeric + protected + groups) - columns:
        raise ValueError("cleaning refers to an unknown input column")
    if set(features) - set(numeric):
        raise ValueError("knn_features must be explicitly selected numeric_columns")
    if (
        isinstance(fit_rows, (str, bytes))
        or not isinstance(fit_rows, Sequence)
        or not fit_rows
    ):
        raise ValueError("fit_rows must be a non-empty sequence of 1-based row indices")
    if any(
        isinstance(p, bool) or not isinstance(p, int) or p < 1 or p > len(original)
        for p in fit_rows
    ) or len(fit_rows) != len(set(fit_rows)):
        raise ValueError("fit_rows must contain unique valid 1-based row indices")
    positions = sorted(fit_rows)
    if (
        not isinstance(decision, Mapping)
        or decision.get("basis") not in {"researcher", "data_profile"}
        or not isinstance(decision.get("rationale"), str)
        or not decision["rationale"].strip()
    ):
        raise ValueError(
            "decision requires basis researcher/data_profile and a non-empty rationale"
        )
    chosen_decision = {
        "basis": decision["basis"],
        "rationale": decision["rationale"].strip(),
    }
    for value, name in [
        (n_neighbors, "n_neighbors"),
        (text_max_features, "text_max_features"),
        (max_generated_features, "max_generated_features"),
    ]:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if text_max_features > 10000:
        raise ValueError("text_max_features may not exceed 10000")
    if max_generated_features > 10000:
        raise ValueError("max_generated_features may not exceed 10000")
    if weights not in {"uniform", "distance"}:
        raise ValueError("weights must be uniform or distance")
    if unknown_categories not in {"error", "indicator"}:
        raise ValueError("unknown_categories must be error or indicator")
    for supplied, name in [
        (imputations, "imputations"),
        (categorical_encodings, "categorical_encodings"),
        (text_encodings, "text_encodings"),
    ]:
        if supplied is not None and not isinstance(supplied, Mapping):
            raise ValueError(f"{name} must be a mapping")
    impute, categorical, texts = (
        dict(imputations or {}),
        dict(categorical_encodings or {}),
        dict(text_encodings or {}),
    )
    if set(impute) - set(numeric) or any(
        method not in {"mean", "median", "knn"} for method in impute.values()
    ):
        raise ValueError(
            "imputations must map selected numeric columns to mean/median/knn"
        )
    if set(categorical) & set(texts) or (set(categorical) | set(texts)) & set(numeric):
        raise ValueError("numeric, categorical and text selections must be disjoint")
    if (set(categorical) | set(texts)) - columns:
        raise ValueError("encoding refers to an unknown input column")
    if set(protected) & (set(numeric) | set(categorical) | set(texts)):
        raise ValueError(
            "protected columns cannot be numerically converted, imputed or encoded"
        )
    if set(groups) & (set(impute) | set(categorical) | set(texts)):
        raise ValueError("group_by columns cannot themselves be imputed or encoded")
    converted = [
        {**row, **{c: _number(row.get(c), c) for c in numeric}} for row in original
    ]
    fitted_groups: dict[str, dict[str, Any]] = {}
    for position in positions:
        row = converted[position - 1]
        key, values = _clean_group(row, groups)
        bucket = fitted_groups.setdefault(key, {"group_values": values, "donors": []})
        bucket["donors"].append(
            {"row": position, "values": {c: row[c] for c in numeric}}
        )
    for bucket in fitted_groups.values():
        means, medians, scales, population_sds = {}, {}, {}, {}
        for column in numeric:
            values = [
                d["values"][column]
                for d in bucket["donors"]
                if d["values"][column] is not None
            ]
            means[column] = statistics.mean(values) if values else None
            medians[column] = statistics.median(values) if values else None
            sd = statistics.pstdev(values) if values else None
            population_sds[column], scales[column] = sd, sd if sd else 1.0
            if any(
                v is not None and not math.isfinite(v)
                for v in [means[column], medians[column], sd]
            ):
                raise ValueError(f"fit statistics overflow: {column}")
        bucket.update(
            means=means, medians=medians, scales=scales, population_sds=population_sds
        )
    category_models, text_models, generated = {}, {}, {}

    def add_column(name: str, detail: dict[str, Any]) -> None:
        if (
            name in columns
            or name in generated
            or name in _RESERVED
            or name == "_imputed_columns"
        ):
            raise ValueError(f"generated column collision: {name}")
        if len(generated) >= max_generated_features:
            raise ValueError(
                "generated feature schema exceeds max_generated_features; explicitly revise the encoding/limit"
            )
        generated[name] = detail

    for column, specification in sorted(categorical.items()):
        observed = {
            _canonical(_clean_scalar(original[p - 1][column], column)): original[p - 1][
                column
            ]
            for p in positions
            if not _missing(original[p - 1].get(column))
        }
        if isinstance(specification, str):
            method = specification
            if method not in {"onehot", "label"}:
                raise ValueError(
                    "categorical methods must be onehot/label or an explicit ordinal order"
                )
            values = [observed[key] for key in sorted(observed)]
        elif (
            isinstance(specification, Mapping)
            and specification.get("method") == "ordinal"
        ):
            method = "ordinal"
            order = specification.get("order")
            if (
                isinstance(order, (str, bytes))
                or not isinstance(order, Sequence)
                or not order
            ):
                raise ValueError("ordinal encoding requires a non-empty explicit order")
            values = [_clean_scalar(v, column) for v in order]
            keys = [_canonical(v) for v in values]
            if (
                len(keys) != len(set(keys))
                or any(_missing(v) for v in values)
                or set(observed) - set(keys)
            ):
                raise ValueError(
                    "ordinal order must contain unique non-missing values covering fit categories"
                )
        else:
            raise ValueError("invalid categorical encoding specification")
        mapping = {_canonical(v): i for i, v in enumerate(values)}
        output = []
        if method == "onehot":
            for i, value in enumerate(values):
                name = f"{column}__cat_{i}"
                add_column(
                    name,
                    {
                        "source_column": column,
                        "kind": "category",
                        "category": value,
                        "index": i,
                    },
                )
                output.append(name)
        else:
            name = f"{column}__code"
            add_column(
                name,
                {"source_column": column, "kind": "category_code", "method": method},
            )
            output.append(name)
        missing = f"{column}__missing"
        add_column(missing, {"source_column": column, "kind": "missing_indicator"})
        output.append(missing)
        unknown = None
        if unknown_categories == "indicator":
            unknown = f"{column}__unknown"
            add_column(unknown, {"source_column": column, "kind": "unknown_indicator"})
            output.append(unknown)
        category_models[column] = {
            "method": method,
            "categories": values,
            "mapping": mapping,
            "output_columns": output,
            "missing_column": missing,
            "unknown_column": unknown,
        }
    for column, method in sorted(texts.items()):
        if method not in {"count", "tfidf"}:
            raise ValueError("text encodings must be count or tfidf")
        documents = [
            _clean_tokens(original[p - 1].get(column), column) for p in positions
        ]
        counts, document_counts = Counter(t for doc in documents for t in doc), Counter(
            t for doc in documents for t in set(doc)
        )
        vocabulary = sorted(
            sorted(counts, key=lambda t: (-counts[t], t))[:text_max_features]
        )
        idf = {
            token: math.log((1 + len(documents)) / (1 + document_counts[token])) + 1
            for token in vocabulary
        }
        output = []
        for i, token in enumerate(vocabulary):
            name = f"{column}__text_{i}"
            add_column(
                name,
                {"source_column": column, "kind": "text", "token": token, "index": i},
            )
            output.append(name)
        missing = f"{column}__missing"
        add_column(missing, {"source_column": column, "kind": "missing_indicator"})
        text_models[column] = {
            "method": method,
            "vocabulary": vocabulary,
            "idf": idf,
            "document_frequency": {t: document_counts[t] for t in vocabulary},
            "n_documents": len(documents),
            "output_columns": output,
            "missing_column": missing,
        }
    model = {
        "schema_version": 1,
        "method": "panel-data-preprocessing/cleaner-v1",
        "config": {
            "numeric_columns": numeric,
            "imputations": impute,
            "categorical_encodings": categorical,
            "text_encodings": texts,
            "protected_columns": protected,
            "group_by": groups,
            "knn_features": features,
            "n_neighbors": n_neighbors,
            "weights": weights,
            "unknown_categories": unknown_categories,
            "text_max_features": text_max_features,
            "max_generated_features": max_generated_features,
        },
        "decision": chosen_decision,
        "fit_rows": positions,
        "fit_source_records_sha256": _digest([original[p - 1] for p in positions]),
        "source_records_sha256": _digest(original),
        "numeric": {"groups": fitted_groups},
        "categorical": category_models,
        "text": text_models,
        "generated_columns": generated,
        "limitations": [
            "All fitted statistics use explicit fit rows; choosing that calibration set is part of the research design.",
            "Imputation assumes meaningful similarity/missingness and does not recreate observed outcomes.",
            "Group-specific fits never borrow target values from an unseen group.",
            "Label codes do not imply an order; use an explicit ordinal order only for ordered categories.",
            "Unicode word tokenization needs upstream segmentation for Chinese text.",
        ],
    }
    model["model_sha256"] = _digest(model)
    json.dumps(model, ensure_ascii=False, allow_nan=False)
    return model


def transform_cleaner(
    rows: Sequence[Mapping[str, Any]], model: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a fitted cleaner without updating donors/scales/vocabulary.

    Numeric distances always use original observed recipient features, so one
    filled variable cannot become an unrecorded feature for another fill.
    """
    original = _clean_rows(rows)
    if (
        not isinstance(model, Mapping)
        or model.get("method") != "panel-data-preprocessing/cleaner-v1"
        or model.get("schema_version") != 1
    ):
        raise ValueError("unsupported cleaning model")
    if model.get("model_sha256") != _digest(
        {k: v for k, v in model.items() if k != "model_sha256"}
    ):
        raise ValueError("cleaning model hash mismatch")
    columns = {c for r in original for c in r}
    collision = columns.intersection(model["generated_columns"])
    if collision:
        raise ValueError(f"generated column collision: {sorted(collision)[0]}")
    config, filled, unresolved = model["config"], [], []
    numeric = config["numeric_columns"]
    converted = [{**r, **{c: _number(r.get(c), c) for c in numeric}} for r in original]
    output, unknown_counts, oov = [], Counter(), {}
    all_oov = {c: Counter() for c in model["text"]}
    oov_rows = Counter()
    for position, row in enumerate(converted, 1):
        result = dict(row)
        key, _ = _clean_group(row, config["group_by"])
        bucket = model["numeric"]["groups"].get(key)
        changed = []
        for column in numeric:
            if row[column] is not None:
                continue
            method = config["imputations"].get(column)
            donors = (
                []
                if bucket is None
                else [d for d in bucket["donors"] if d["values"][column] is not None]
            )
            problem = (
                "no_imputation_selected"
                if method is None
                else (
                    "no_fitted_group"
                    if bucket is None
                    else "no_fit_target_values" if not donors else None
                )
            )
            if problem:
                unresolved.append(
                    {
                        "row": position,
                        "column": column,
                        "reason": problem,
                        "fit_source_records_sha256": model["fit_source_records_sha256"],
                    }
                )
                continue
            evidence = {
                "row": position,
                "column": column,
                "method": method,
                "fit_source_records_sha256": model["fit_source_records_sha256"],
            }
            if method in {"mean", "median"}:
                value = bucket["means" if method == "mean" else "medians"][column]
                evidence["donor_rows"] = [d["row"] for d in donors]
            else:
                features = [c for c in config["knn_features"] if c != column]
                neighbors = []
                for donor in donors:
                    common = [
                        c
                        for c in features
                        if row[c] is not None and donor["values"][c] is not None
                    ]
                    if not common:
                        continue
                    components = []
                    for feature in common:
                        difference = row[feature] - donor["values"][feature]
                        scale = bucket["scales"][feature]
                        component = (
                            difference / scale
                            if math.isfinite(difference)
                            else row[feature] / scale - donor["values"][feature] / scale
                        )
                        components.append(component)
                    # hypot evaluates the same square-root distance without
                    # overflowing an intermediate square or underflowing tiny
                    # but nonzero distances into false exact matches.
                    distance = math.hypot(*components) * math.sqrt(
                        len(features) / len(common)
                    )
                    if not math.isfinite(distance):
                        raise ValueError(f"KNN distance overflow: {column}")
                    neighbors.append((distance, donor["row"], donor, common))
                neighbors.sort(key=lambda entry: (entry[0], entry[1]))
                selected = neighbors[: config["n_neighbors"]]
                if not selected:
                    value = bucket["means"][column]
                    evidence.update(
                        method="fitted_mean_fallback",
                        reason="no_common_dimensions",
                        donor_rows=[d["row"] for d in donors],
                    )
                else:
                    if config["weights"] == "distance" and selected[0][0] == 0:
                        selected = [entry for entry in selected if entry[0] == 0]
                    if config["weights"] == "uniform" or selected[0][0] == 0:
                        value = statistics.mean(
                            entry[2]["values"][column] for entry in selected
                        )
                    else:
                        relative = [selected[0][0] / entry[0] for entry in selected]
                        value = sum(
                            entry[2]["values"][column] * weight
                            for entry, weight in zip(selected, relative)
                        ) / sum(relative)
                    evidence["donor_rows"] = [entry[1] for entry in selected]
                    evidence["distance_details"] = [
                        {
                            "row": entry[1],
                            "distance": entry[0],
                            "common_features": entry[3],
                        }
                        for entry in selected
                    ]
            if value is None or not math.isfinite(value):
                raise ValueError(f"imputation overflow: {column}")
            result[column] = value
            changed.append(column)
            evidence["value"] = value
            filled.append(evidence)
        for column, fitted in model["categorical"].items():
            raw = row.get(column)
            missing = _missing(raw)
            code = (
                None
                if missing
                else fitted["mapping"].get(_canonical(_clean_scalar(raw, column)))
            )
            unknown = not missing and code is None
            if unknown and config["unknown_categories"] == "error":
                raise ValueError(f"{column}: unknown category {raw!r}")
            if unknown:
                unknown_counts[column] += 1
            if fitted["method"] == "onehot":
                for i, name in enumerate(
                    fitted["output_columns"][: len(fitted["categories"])]
                ):
                    result[name] = int(code == i)
            else:
                result[f"{column}__code"] = code
            result[fitted["missing_column"]] = int(missing)
            if fitted["unknown_column"] is not None:
                result[fitted["unknown_column"]] = int(unknown)
        for column, fitted in model["text"].items():
            raw = row.get(column)
            counts = Counter(_clean_tokens(raw, column))
            vocabulary = fitted["vocabulary"]
            unknown = {
                t: count for t, count in counts.items() if t not in fitted["idf"]
            }
            all_oov[column].update(unknown)
            oov_rows[column] += bool(unknown)
            values = [counts[token] for token in vocabulary]
            if fitted["method"] == "tfidf":
                values = [
                    count * fitted["idf"][token]
                    for count, token in zip(values, vocabulary)
                ]
                norm = math.sqrt(sum(value * value for value in values))
                if norm:
                    values = [value / norm for value in values]
            result.update(zip(fitted["output_columns"], values))
            result[fitted["missing_column"]] = int(_missing(raw))
        if changed:
            result["_imputed_columns"] = changed
        output.append(result)
    for column, counts in all_oov.items():
        oov[column] = {
            "token_count": sum(counts.values()),
            "unique_token_count": len(counts),
            "row_count": oov_rows[column],
        }
    report = {
        "schema_version": 1,
        "source_records_sha256": _digest(original),
        "fit_source_records_sha256": model["fit_source_records_sha256"],
        "cleaned_records_sha256": _digest(output),
        "model_sha256": model["model_sha256"],
        "before_missing": {
            c: sum(_missing(r.get(c)) for r in original)
            for c in sorted(
                columns | set(numeric) | set(model["categorical"]) | set(model["text"])
            )
        },
        "after_missing": {
            c: sum(_missing(r.get(c)) for r in output)
            for c in sorted(
                columns | set(numeric) | set(model["categorical"]) | set(model["text"])
            )
        },
        "filled": filled,
        "unresolved": unresolved,
        "encoded_columns": model["generated_columns"],
        "oov_counts": oov,
        "unknown_category_counts": {c: unknown_counts[c] for c in model["categorical"]},
        "decision": model["decision"],
        "fit_rows": model["fit_rows"],
        "limitations": model["limitations"],
    }
    return {"rows": output, "model": model, "report": report}


def clean_data(rows: Sequence[Mapping[str, Any]], **fit_kwargs: Any) -> dict[str, Any]:
    """Fit explicit cleaning choices, then transform; never select methods."""
    return transform_cleaner(rows, fit_cleaner(rows, **fit_kwargs))


def prepare_panel(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    numeric_columns: Sequence[str],
    frequency: str,
    wide_columns: Mapping[str, Mapping[str, str]] | None = None,
    aggregations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build long entity-period rows with explicit conversion and source positions.

    wide_columns maps metric -> {period label: input column}. Duplicate
    entity-period cells require one explicit reducer per numeric column;
    all remaining columns must agree. No missing observations are created.
    """
    if frequency not in _FREQUENCIES:
        raise ValueError(f"unsupported frequency: {frequency!r}")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError("rows must be a non-empty sequence of mappings")
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("every row must be a mapping")
    if any(not isinstance(key, str) for row in rows for key in row):
        raise ValueError("column names must be strings")
    numeric = list(numeric_columns)
    if isinstance(numeric_columns, str) or len(numeric) != len(set(numeric)):
        raise ValueError("numeric_columns must contain unique column names")
    if (
        not entity
        or not time
        or entity == time
        or (_RESERVED | {_IMPUTED}).intersection([entity, time, *numeric])
    ):
        raise ValueError("entity, time and reserved column names must be distinct")
    if {entity, time}.intersection(numeric):
        raise ValueError("entity/time columns cannot be numeric measurements")
    if any(_RESERVED.intersection(row) for row in rows):
        raise ValueError("input contains reserved lineage columns")
    for raw in rows:
        flags = raw.get(_IMPUTED, [])
        if (
            isinstance(flags, (str, bytes))
            or not isinstance(flags, Sequence)
            or any(not isinstance(c, str) or c not in raw for c in flags)
            or len(flags) != len(set(flags))
            or {entity, time}.intersection(flags)
        ):
            raise ValueError("invalid imputed-column metadata")
    reducers = dict(aggregations or {})
    if reducers and (
        set(reducers) != set(numeric)
        or any(r not in _AGGREGATIONS for r in reducers.values())
    ):
        raise ValueError(
            "aggregations must specify sum/mean/min/max for every numeric column"
        )
    expanded: list[dict[str, Any]] = []
    wide_specs: dict[int, tuple[str, dict[str, str]]] = {}
    wide_sources: set[str] = set()
    if wide_columns is not None:
        if not wide_columns or set(wide_columns) != set(numeric):
            raise ValueError("wide_columns must map every numeric metric")
        common_periods: set[int] | None = None
        for metric, mapping in wide_columns.items():
            indices = set()
            for label, source_column in mapping.items():
                index, normalized = _period(label, frequency)
                if index in indices or source_column in wide_sources:
                    raise ValueError("wide mapping repeats a period or source column")
                indices.add(index)
                wide_sources.add(source_column)
                wide_specs.setdefault(index, (normalized, {}))[1][
                    metric
                ] = source_column
            if not indices or (
                common_periods is not None and common_periods != indices
            ):
                raise ValueError(
                    "wide metrics must have the same non-empty period coverage"
                )
            common_periods = indices
        if {entity, time}.intersection(wide_sources):
            raise ValueError("wide source columns cannot be entity/time columns")
    for position, raw in enumerate(rows, 1):
        _entity(raw.get(entity))
        if wide_columns is None:
            if time not in raw or any(column not in raw for column in numeric):
                raise ValueError(f"source row {position}: required column missing")
            index, normalized = _period(raw[time], frequency)
            cells = [(index, normalized, dict(raw))]
        else:
            if time in raw or set(numeric).intersection(raw):
                raise ValueError("wide input collides with output time/metric columns")
            if not wide_sources.issubset(raw):
                raise ValueError(f"source row {position}: wide source column missing")
            static = {
                k: v for k, v in raw.items() if k not in wide_sources | {_IMPUTED}
            }
            cells = [
                (
                    index,
                    label,
                    {
                        **static,
                        **{m: raw[c] for m, c in sources.items()},
                        **(
                            {
                                _IMPUTED: sorted(
                                    {
                                        m
                                        for m, c in sources.items()
                                        if c in raw.get(_IMPUTED, [])
                                    }
                                    | (set(raw.get(_IMPUTED, [])) - wide_sources)
                                )
                            }
                            if raw.get(_IMPUTED)
                            else {}
                        ),
                    },
                )
                for index, (label, sources) in sorted(wide_specs.items())
            ]
        for index, label, cell in cells:
            if _IMPUTED in cell and not cell[_IMPUTED]:
                del cell[_IMPUTED]
            cell[time] = label
            for column in numeric:
                cell[column] = _number(cell[column], column)
            cell["_period_index"] = index
            cell["_source_rows"] = [position]
            expanded.append(cell)
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in expanded:
        grouped[(_canonical(row[entity]), row["_period_index"])].append(row)
    result = []
    collapsed = 0
    for key, group in sorted(grouped.items()):
        output = dict(group[0])
        if len(group) > 1:
            if not reducers:
                raise ValueError(
                    f"duplicate entity-period {key}; explicit aggregations required"
                )
            static_columns = (
                set().union(*(r.keys() for r in group))
                - set(numeric)
                - _RESERVED
                - {_IMPUTED}
            )
            for column in static_columns:
                if any(
                    _canonical(r.get(column)) != _canonical(group[0].get(column))
                    for r in group[1:]
                ):
                    raise ValueError(f"conflicting non-aggregated column: {column}")
            for column in numeric:
                values = [r[column] for r in group if r[column] is not None]
                reducer = reducers[column]
                output[column] = (
                    {"sum": sum, "mean": statistics.mean, "min": min, "max": max}[
                        reducer
                    ](values)
                    if values
                    else None
                )
                if output[column] is not None and not math.isfinite(output[column]):
                    raise ValueError(f"aggregation overflow: {column}")
            output["_source_rows"] = sorted(
                {i for r in group for i in r["_source_rows"]}
            )
            imputed = sorted({c for r in group for c in r.get(_IMPUTED, [])})
            if imputed:
                output[_IMPUTED] = imputed
            collapsed += len(group) - 1
        result.append(output)
    config = {
        "entity": entity,
        "time": time,
        "numeric_columns": numeric,
        "frequency": frequency,
        "wide_columns": wide_columns,
        "aggregations": reducers,
    }
    return {
        "schema_version": 1,
        "config": config,
        "rows": result,
        "transformation": {
            "source_row_count": len(rows),
            "expanded_row_count": len(expanded),
            "collapsed_row_count": collapsed,
            "output_row_count": len(result),
            "source_records_sha256": _digest(list(rows)),
        },
    }


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "std": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def describe_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    """Describe observed cells and calendar gaps without balancing or imputing."""
    config, rows = panel["config"], panel["rows"]
    entity, time = config["entity"], config["time"]
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_period: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_entity[_canonical(row[entity])].append(row)
        by_period[row["_period_index"]].append(row)
    first, last = min(by_period), max(by_period)
    span = last - first + 1
    coverage = []
    for group in by_entity.values():
        indices = sorted(r["_period_index"] for r in group)
        coverage.append(
            {
                "entity": group[0][entity],
                "observations": len(group),
                "first_period": _period_label(indices[0], config["frequency"]),
                "last_period": _period_label(indices[-1], config["frequency"]),
                "internal_missing_periods": indices[-1] - indices[0] + 1 - len(indices),
                "late_entry_periods": indices[0] - first,
                "early_exit_periods": last - indices[-1],
            }
        )
    columns = {}
    for column in config["numeric_columns"]:
        values = [r[column] for r in rows if r[column] is not None]
        columns[column] = {**_summary(values), "missing": len(rows) - len(values)}
    timeline = []
    for index, group in sorted(by_period.items()):
        timeline.append(
            {
                "time": group[0][time],
                "period_index": index,
                "entity_count": len(group),
                "entity_set_sha256": _digest(
                    sorted(_canonical(r[entity]) for r in group)
                ),
                "metrics": {
                    c: _summary([r[c] for r in group if r[c] is not None])
                    for c in config["numeric_columns"]
                },
            }
        )
    return {
        "audit": {
            "entity_count": len(by_entity),
            "observed_period_count": len(by_period),
            "calendar_period_count": span,
            "row_count": len(rows),
            "missing_entity_periods": len(by_entity) * span - len(rows),
            "balanced_calendar_grid": len(rows) == len(by_entity) * span,
            "balanced_observed_grid": len(rows) == len(by_entity) * len(by_period),
            "coverage": coverage,
        },
        "columns": columns,
        "timeline": timeline,
        "interpretation": "Pooled row summaries and changing-composition period means are descriptive, not causal effects.",
    }


def _pre_window_statistics(
    values: list[float], min_correlation_periods: int
) -> dict[str, Any]:
    """Describe a complete pre-window; residual noise does not replace level SD."""
    n = len(values)
    mean, median = statistics.mean(values), statistics.median(values)
    sd, variance = statistics.stdev(values), statistics.variance(values)
    centered = [v - mean for v in values]
    x = [i - (n - 1) / 2 for i in range(n)]
    slope = sum(a * b for a, b in zip(x, centered)) / sum(a * a for a in x)
    residuals = [v - slope * a for v, a in zip(centered, x)]
    residual_sd = math.sqrt(sum(r * r for r in residuals) / (n - 2)) if n > 2 else None
    acf = None
    if n >= min_correlation_periods:
        residual_mean = statistics.mean(residuals)
        residuals = [r - residual_mean for r in residuals]
        denominator = sum(r * r for r in residuals)
        if denominator > 0:
            acf = sum(a * b for a, b in zip(residuals, residuals[1:])) / denominator
    result = {
        "n": n,
        "variance": variance,
        "sd": sd,
        "mad": statistics.median(abs(v - median) for v in values),
        "linear_slope": slope,
        "trend_span": abs(slope) * (n - 1),
        "residual_sd": residual_sd,
        "lag1_autocorrelation": acf,
        "mean_noise_iid": sd / math.sqrt(n),
    }
    if any(not math.isfinite(v) for v in result.values() if v is not None):
        raise ValueError("window statistics overflow")
    return result


def assess_windows(
    panel: Mapping[str, Any],
    *,
    window: int = 3,
    window_options: Sequence[int] | None = None,
    noise_tolerance: float | Mapping[str, float] | None = None,
    min_noise_periods: int = 5,
    min_correlation_periods: int = 8,
    autocorrelation_threshold: float = 0.5,
    trend_residual_ratio: float = 0.5,
    treatment: str | None = None,
) -> dict[str, Any]:
    """Audit coverage and pre-only variability before exploratory screening.

    An anchor is the first post period. Recommendations compare inspected
    windows at that same anchor; post values affect only missingness/coverage.
    Mean-noise advice assumes independent stable noise and does not select a
    significant event, change the screen, or supply a power calculation.
    """
    for value, name, minimum in [
        (window, "window", 2),
        (min_noise_periods, "min_noise_periods", 2),
        (min_correlation_periods, "min_correlation_periods", 3),
    ]:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if (
        not math.isfinite(autocorrelation_threshold)
        or not 0 < autocorrelation_threshold <= 1
    ):
        raise ValueError("autocorrelation_threshold must be in (0, 1]")
    if not math.isfinite(trend_residual_ratio) or not 0 < trend_residual_ratio < 1:
        raise ValueError("trend_residual_ratio must be in (0, 1)")
    config, rows = panel["config"], panel["rows"]
    entity, time = config["entity"], config["time"]
    has_imputation_flags = any(_IMPUTED in r for r in rows)
    if window_options is None:
        maximum_rows = max(Counter(_canonical(r[entity]) for r in rows).values())
        upper = min(max(2 * window, min_noise_periods), max(window, maximum_rows))
        if upper - window >= 64:
            raise ValueError(
                "default window sweep exceeds 64 sizes; supply bounded window_options"
            )
        options = list(range(window, upper + 1))
    else:
        if isinstance(window_options, (str, bytes)) or not isinstance(
            window_options, Sequence
        ):
            raise ValueError("window_options must be a sequence of integers >= 2")
        if any(
            isinstance(w, bool) or not isinstance(w, int) or w < 2
            for w in window_options
        ):
            raise ValueError("window_options must contain integers >= 2")
        options = sorted({window, *window_options})
        if len(options) > 64:
            raise ValueError("window_options may inspect at most 64 distinct sizes")
    metrics = [c for c in config["numeric_columns"] if c != treatment]
    tolerances: dict[str, float | None] = {c: None for c in metrics}
    if noise_tolerance is not None:
        supplied = (
            noise_tolerance
            if isinstance(noise_tolerance, Mapping)
            else {c: noise_tolerance for c in metrics}
        )
        if any(c not in metrics for c in supplied):
            raise ValueError("noise_tolerance refers to an unknown outcome metric")
        for metric, value in supplied.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    "noise_tolerance must be finite and positive in metric units"
                )
            tolerances[metric] = float(value)
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_canonical(row[entity])].append(row)
    assessments, recommendations = [], []
    for _, unsorted in sorted(groups.items()):
        group = sorted(unsorted, key=lambda r: r["_period_index"])
        indices = [r["_period_index"] for r in group]
        first, last = indices[0], indices[-1]
        for metric in metrics:
            for row in group:
                anchor = row["_period_index"]
                local = []
                for width in options:
                    pre = group[
                        bisect_left(indices, anchor - width) : bisect_left(
                            indices, anchor
                        )
                    ]
                    post = group[
                        bisect_left(indices, anchor) : bisect_left(
                            indices, anchor + width
                        )
                    ]
                    pre_missing = sum(r[metric] is None for r in pre)
                    post_missing = sum(r[metric] is None for r in post)
                    pre_imputed = sum(metric in r.get(_IMPUTED, []) for r in pre)
                    post_imputed = sum(metric in r.get(_IMPUTED, []) for r in post)
                    pre_shortfall = min(width, max(0, first - (anchor - width)))
                    post_shortfall = min(width, max(0, anchor + width - 1 - last))
                    coverage = {
                        "pre_observed_periods": len(pre),
                        "post_observed_periods": len(post),
                        "pre_nonmissing": len(pre) - pre_missing,
                        "post_nonmissing": len(post) - post_missing,
                        "pre_missing_periods": width - len(pre),
                        "post_missing_periods": width - len(post),
                        "pre_missing_values": pre_missing,
                        "post_missing_values": post_missing,
                        "pre_history_shortfall": pre_shortfall,
                        "post_history_shortfall": post_shortfall,
                        "pre_internal_gap_periods": width - len(pre) - pre_shortfall,
                        "post_internal_gap_periods": width - len(post) - post_shortfall,
                    }
                    if has_imputation_flags:
                        coverage.update(
                            pre_imputed_values=pre_imputed,
                            post_imputed_values=post_imputed,
                        )
                    reasons = [
                        reason
                        for key, reason in [
                            ("pre_history_shortfall", "insufficient_pre_history"),
                            ("post_history_shortfall", "insufficient_post_history"),
                            ("pre_internal_gap_periods", "calendar_gap_pre"),
                            ("post_internal_gap_periods", "calendar_gap_post"),
                            ("pre_missing_values", "missing_values_pre"),
                            ("post_missing_values", "missing_values_post"),
                        ]
                        if coverage[key]
                    ]
                    if pre_imputed:
                        reasons.append("imputed_values_pre")
                    if post_imputed:
                        reasons.append("imputed_values_post")
                    stats = None
                    tolerance = tolerances[metric]
                    status = "pre_data_incomplete"
                    if len(pre) == width and not pre_missing and not pre_imputed:
                        stats = _pre_window_statistics(
                            [r[metric] for r in pre], min_correlation_periods
                        )
                        ratio = (
                            stats["mean_noise_iid"] / tolerance
                            if tolerance is not None
                            else None
                        )
                        if ratio is not None and not math.isfinite(ratio):
                            raise ValueError("window noise ratio overflow")
                        stats["noise_to_tolerance_ratio"] = ratio
                        if tolerance is None:
                            status = "tolerance_not_configured"
                        elif (
                            stats["trend_span"] > tolerance
                            and stats["residual_sd"] is not None
                            and stats["residual_sd"]
                            < trend_residual_ratio * stats["sd"]
                        ):
                            status = "review_trend"
                        elif (
                            stats["lag1_autocorrelation"] is not None
                            and abs(stats["lag1_autocorrelation"])
                            >= autocorrelation_threshold
                        ):
                            status = "review_serial_dependence"
                        elif width < min_noise_periods:
                            status = "noise_baseline_short"
                        else:
                            status = (
                                "high_noise_under_iid"
                                if ratio > 1
                                else "within_tolerance_under_iid"
                            )
                    assessment = {
                        "entity": row[entity],
                        "metric": metric,
                        "time": row[time],
                        "period_index": anchor,
                        "window": width,
                        "eligible": not reasons,
                        "reasons": reasons,
                        "coverage": coverage,
                        "pre_statistics": stats,
                        "noise_status": status,
                        "pre_source_rows": sorted(
                            {i for r in pre for i in r["_source_rows"]}
                        ),
                        "post_source_rows": sorted(
                            {i for r in post for i in r["_source_rows"]}
                        ),
                    }
                    local.append(assessment)
                    assessments.append(assessment)
                requested = next(a for a in local if a["window"] == window)
                larger = [a for a in local if a["window"] > window and a["eligible"]]
                suggestion = None
                action = requested["noise_status"]
                if not requested["eligible"]:
                    action, reason = "coverage_insufficient", "; ".join(
                        requested["reasons"]
                    )
                elif action == "noise_baseline_short":
                    available = [
                        a
                        for a in larger
                        if a["window"] >= min_noise_periods
                        and a["noise_status"]
                        in {"high_noise_under_iid", "within_tolerance_under_iid"}
                    ]
                    if available:
                        action, suggestion = (
                            "consider_larger_for_baseline",
                            available[0]["window"],
                        )
                    reason = "Short pre baseline: variance is displayed but no quantitative noise-based recommendation is reliable."
                elif action == "high_noise_under_iid":
                    available = [
                        a
                        for a in larger
                        if a["noise_status"] == "within_tolerance_under_iid"
                    ]
                    if available:
                        action, suggestion = (
                            "consider_larger_under_iid",
                            available[0]["window"],
                        )
                        reason = "Smallest inspected larger complete window meeting the mean-noise target, conditional on independent stable noise."
                    else:
                        action, reason = (
                            "no_inspected_window_meets_tolerance",
                            "No inspected larger complete window meets the target without trend/dependence/short-baseline review; collect more history or revise the exploratory plan.",
                        )
                elif action == "within_tolerance_under_iid":
                    action, reason = (
                        "retain_current_under_iid",
                        "Requested window meets the mean-noise target under independent stable noise; this is not proof of independence or event-detection power.",
                    )
                elif action == "tolerance_not_configured":
                    reason = "Set a positive mean-noise tolerance in metric units to judge whether variation is excessive."
                elif action == "review_trend":
                    reason = "Pre-window drift dominates residual variation; inspect trend rather than automatically widening."
                elif action == "review_serial_dependence":
                    reason = "Strong residual lag-1 correlation; the IID mean-noise approximation may be misleading."
                else:
                    reason = (
                        "Complete pre-window data are required to assess variability."
                    )
                recommendations.append(
                    {
                        "entity": row[entity],
                        "metric": metric,
                        "time": row[time],
                        "requested_window": window,
                        "action": action,
                        "suggested_window": suggestion,
                        "reason": reason,
                    }
                )
    summaries = []
    for width in options:
        inspected = [a for a in assessments if a["window"] == width]
        eligible_pairs = {
            (_canonical(a["entity"]), a["metric"]) for a in inspected if a["eligible"]
        }
        summaries.append(
            {
                "window": width,
                "entity_metric_count": len(groups) * len(metrics),
                "anchor_count": len(inspected),
                "eligible_anchor_count": sum(a["eligible"] for a in inspected),
                "eligible_entity_metric_count": len(eligible_pairs),
                "no_eligible_entity_metric_count": len(groups) * len(metrics)
                - len(eligible_pairs),
                "blocked_by_reason": dict(
                    Counter(reason for a in inspected for reason in a["reasons"])
                ),
                "noise_status_counts": dict(
                    Counter(a["noise_status"] for a in inspected)
                ),
                "eligible_noise_status_counts": dict(
                    Counter(a["noise_status"] for a in inspected if a["eligible"])
                ),
            }
        )
    return {
        "schema_version": 1,
        "source_records_sha256": panel["transformation"]["source_records_sha256"],
        "config": {
            "requested_window": window,
            "window_options": options,
            "default_sweep_data_bound": "maximum observed periods within any entity; requested window always retained",
            "maximum_inspected_sizes": 64,
            "noise_tolerance": tolerances,
            "noise_tolerance_source": (
                "explicit" if noise_tolerance is not None else "unconfigured"
            ),
            "min_noise_periods": min_noise_periods,
            "min_correlation_periods": min_correlation_periods,
            "autocorrelation_threshold": autocorrelation_threshold,
            "trend_residual_ratio": trend_residual_ratio,
            "recommendation_scope": "advisory only; smallest suitable inspected larger window at same first-post anchor",
        },
        "summary": summaries,
        "assessments": assessments,
        "recommendations": recommendations,
        "limitations": [
            "Mean-noise SD/sqrt(n) assumes independent stable noise; it is neither a confidence interval nor a power calculation.",
            "Short baseline and correlation thresholds are review heuristics, not statistical guarantees. Unassessed or weak correlation does not prove independence.",
            "Noise uses pre values only; post values enter only through coverage/missingness. Windows are never automatically changed.",
            "The existing event screen uses change/pre SD, not change/SE. Lower mean noise need not improve its score.",
            "Baseline trend, earlier shocks and seasonality can contaminate wider windows; inspect recommendations before comparing specifications.",
        ],
    }


def discover_events(
    panel: Mapping[str, Any],
    *,
    treatment: str | None = None,
    known_events: Sequence[Mapping[str, Any]] = (),
    window: int = 3,
    threshold: float = 3.0,
    min_abs_change: float = 0.0,
    window_options: Sequence[int] | None = None,
    noise_tolerance: float | Mapping[str, float] | None = None,
    min_noise_periods: int = 5,
    min_correlation_periods: int = 8,
    autocorrelation_threshold: float = 0.5,
    trend_residual_ratio: float = 0.5,
) -> dict[str, Any]:
    """Screen within-entity persistent level changes and exposure transitions.

    The score is a change / pre-window sample SD heuristic, never a p-value.
    Each numeric window requires contiguous, non-missing observations; at
    least two thirds of the post-window must support the change direction.
    Nearby overlapping candidates are reduced to the strongest score/change.
    """
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError("window must be an integer >= 2")
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be finite and positive")
    if not math.isfinite(min_abs_change) or min_abs_change < 0:
        raise ValueError("min_abs_change must be finite and non-negative")
    config, rows = panel["config"], panel["rows"]
    entity, time, frequency = config["entity"], config["time"], config["frequency"]
    if treatment in {entity, time, _IMPUTED, *_RESERVED}:
        raise ValueError("treatment must be a separate binary column")
    if treatment is not None and any(treatment not in r for r in rows):
        raise ValueError("treatment column missing")
    if treatment in config["aggregations"]:
        raise ValueError("treatment states cannot be aggregated as numeric outcomes")
    if treatment is not None and any(treatment in r.get(_IMPUTED, []) for r in rows):
        raise ValueError("treatment states cannot be imputed")
    effective_tolerance = noise_tolerance
    if effective_tolerance is None and min_abs_change > 0:
        effective_tolerance = min_abs_change
    window_assessment = assess_windows(
        panel,
        window=window,
        window_options=window_options,
        noise_tolerance=effective_tolerance,
        min_noise_periods=min_noise_periods,
        min_correlation_periods=min_correlation_periods,
        autocorrelation_threshold=autocorrelation_threshold,
        trend_residual_ratio=trend_residual_ratio,
        treatment=treatment,
    )
    if noise_tolerance is None and effective_tolerance is not None:
        window_assessment["config"]["noise_tolerance_source"] = "min_abs_change"
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_canonical(row[entity])].append(row)
    events: list[dict[str, Any]] = []
    for event in known_events:
        if not isinstance(event.get("label"), str) or not event["label"].strip():
            raise ValueError("known event requires a label")
        if not isinstance(event.get("source"), str) or not event["source"].strip():
            raise ValueError("known event requires a source reference")
        index, label = _period(event.get("time"), frequency)
        event_entity = event.get("entity")
        if event_entity is not None and _canonical(_entity(event_entity)) not in groups:
            raise ValueError("known event refers to an unknown entity")
        events.append(
            {
                "kind": "documented_event",
                "entity": event_entity,
                "time": label,
                "period_index": index,
                "label": event["label"],
                "source": event["source"],
                "source_rows": [],
                "causal_status": "unverified",
                "observed_period": any(
                    r["_period_index"] == index
                    and (event_entity is None or r[entity] == event_entity)
                    for r in rows
                ),
            }
        )
    missing_states = 0
    for key, unsorted in sorted(groups.items()):
        group = sorted(unsorted, key=lambda r: r["_period_index"])
        if treatment is not None:
            previous = None
            for row in group:
                value = row[treatment]
                if _missing(value):
                    missing_states += 1
                    continue
                if isinstance(value, bool):
                    state = int(value)
                else:
                    state = _number(value, treatment)
                if state not in (0, 1):
                    raise ValueError("treatment must be binary 0/1 or missing")
                if previous is None and state == 1:
                    events.append(
                        {
                            "kind": "left_censored_exposure",
                            "entity": row[entity],
                            "time": row[time],
                            "period_index": row["_period_index"],
                            "source_rows": row["_source_rows"],
                            "causal_status": "unverified",
                        }
                    )
                elif previous is not None and previous[1] != state:
                    before, old_state = previous
                    exact = row["_period_index"] - before["_period_index"] == 1
                    events.append(
                        {
                            "kind": (
                                (
                                    "treatment_adoption"
                                    if state == 1
                                    else "treatment_reversal"
                                )
                                if exact
                                else "interval_censored_transition"
                            ),
                            "entity": row[entity],
                            "time": row[time],
                            "period_index": row["_period_index"],
                            "from_state": old_state,
                            "to_state": state,
                            "previous_observed_time": before[time],
                            "source_rows": sorted(
                                set(before["_source_rows"] + row["_source_rows"])
                            ),
                            "causal_status": "unverified",
                        }
                    )
                previous = row, state
        for metric in config["numeric_columns"]:
            if metric == treatment:
                continue
            candidates = []
            for split in range(window, len(group) - window + 1):
                before, after = (
                    group[split - window : split],
                    group[split : split + window],
                )
                combined = before + after
                if any(
                    r[metric] is None or metric in r.get(_IMPUTED, []) for r in combined
                ):
                    continue
                if any(
                    b["_period_index"] - a["_period_index"] != 1
                    for a, b in zip(combined, combined[1:])
                ):
                    continue
                pre = [r[metric] for r in before]
                post = [r[metric] for r in after]
                baseline = statistics.mean(pre)
                delta = statistics.mean(post) - baseline
                sd = statistics.stdev(pre)
                if not math.isfinite(delta) or not math.isfinite(sd):
                    raise ValueError(f"event screening overflow: {metric}")
                if abs(delta) <= min_abs_change:
                    continue
                score = abs(delta) / sd if sd else None
                if score is not None and score < threshold:
                    continue
                direction = 1 if delta > 0 else -1
                increments = [b - a for a, b in zip(pre, pre[1:])]
                typical_increment = statistics.median(increments)
                increment_mad = statistics.median(
                    abs(v - typical_increment) for v in increments
                )
                innovation = post[0] - pre[-1] - typical_increment
                tolerance = 1e-12 * max(1.0, *(abs(v) for v in pre + post))
                if direction * innovation <= max(
                    min_abs_change, threshold * 1.4826 * increment_mad, tolerance
                ):
                    continue
                support_cutoff = max(min_abs_change / 2, threshold * sd / 2)
                support = sum(direction * (v - baseline) > support_cutoff for v in post)
                if support < math.ceil(2 * window / 3):
                    continue
                candidates.append(
                    {
                        "kind": "level_shift_candidate",
                        "entity": group[split][entity],
                        "time": group[split][time],
                        "period_index": group[split]["_period_index"],
                        "metric": metric,
                        "pre_mean": baseline,
                        "post_mean": statistics.mean(post),
                        "change": delta,
                        "score": score,
                        "boundary_innovation": innovation,
                        "score_basis": (
                            "pre_window_sample_sd" if sd else "zero_baseline_variation"
                        ),
                        "post_support": support,
                        "window": window,
                        "source_rows": sorted(
                            {i for r in combined for i in r["_source_rows"]}
                        ),
                        "causal_status": "exploratory",
                    }
                )
            selected: list[dict[str, Any]] = []
            for candidate in sorted(
                candidates,
                key=lambda c: (
                    -(c["score"] if c["score"] is not None else float("inf")),
                    -abs(c["change"]),
                    c["period_index"],
                ),
            ):
                if all(
                    abs(candidate["period_index"] - other["period_index"]) >= window
                    for other in selected
                ):
                    selected.append(candidate)
            events.extend(selected)
    events.sort(
        key=lambda e: (
            e["period_index"],
            e["kind"] != "documented_event",
            _canonical(e.get("entity")),
            e["kind"],
            e.get("metric", ""),
            e.get("label", ""),
        )
    )
    return {
        "candidates": events,
        "window_assessment": window_assessment,
        "missing_treatment_states": missing_states,
        "config": {
            "treatment": treatment,
            "window": window,
            "threshold": threshold,
            "min_abs_change": min_abs_change,
            "known_events": list(known_events),
            "window_assessment": window_assessment["config"],
        },
        "limitations": [
            "Screening is exploratory; scores are not p-values or causal effects.",
            "Trend, seasonality and multiple screening comparisons are not adjusted.",
            "Do not select a DiD treatment date from the outcome being tested; corroborate independently and validate on fresh data.",
            "First observed exposure and transitions across missing periods do not establish exact adoption dates.",
        ],
    }


def preprocess_panel(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    numeric_columns: Sequence[str],
    frequency: str,
    wide_columns: Mapping[str, Mapping[str, str]] | None = None,
    aggregations: Mapping[str, str] | None = None,
    treatment: str | None = None,
    known_events: Sequence[Mapping[str, Any]] = (),
    window: int = 3,
    threshold: float = 3.0,
    min_abs_change: float = 0.0,
    window_options: Sequence[int] | None = None,
    noise_tolerance: float | Mapping[str, float] | None = None,
    min_noise_periods: int = 5,
    min_correlation_periods: int = 8,
    autocorrelation_threshold: float = 0.5,
    trend_residual_ratio: float = 0.5,
    cleaning: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run optional explicit cleaning -> panel -> analysis -> event workflow."""
    raw_rows, cleaned = rows, None
    if cleaning is not None:
        if not isinstance(cleaning, Mapping):
            raise ValueError("cleaning must be a mapping of explicit cleaner options")
        settings = dict(cleaning)
        source_numeric = (
            sorted({c for mapping in wide_columns.values() for c in mapping.values()})
            if wide_columns is not None
            else list(numeric_columns)
        )
        selected = settings.pop("numeric_columns", source_numeric)
        if (
            isinstance(selected, (str, bytes))
            or not isinstance(selected, Sequence)
            or not set(source_numeric).issubset(selected)
        ):
            raise ValueError(
                "cleaning numeric_columns must include all source outcomes"
            )
        extra_protected = settings.pop("protected_columns", ())
        if (
            isinstance(extra_protected, (str, bytes))
            or not isinstance(extra_protected, Sequence)
            or any(not isinstance(c, str) for c in extra_protected)
        ):
            raise ValueError("cleaning protected_columns must be column names")
        present = {c for row in rows if isinstance(row, Mapping) for c in row}
        protected = sorted(
            set(extra_protected)
            | (({entity, time} | ({treatment} if treatment else set())) & present)
        )
        cleaned = clean_data(
            rows,
            numeric_columns=selected,
            protected_columns=protected,
            **settings,
        )
        rows = cleaned["rows"]
    panel = prepare_panel(
        rows,
        entity=entity,
        time=time,
        numeric_columns=numeric_columns,
        frequency=frequency,
        wide_columns=wide_columns,
        aggregations=aggregations,
    )
    panel["analysis"] = describe_panel(panel)
    if cleaned is not None:
        panel["cleaning"] = {"model": cleaned["model"], "report": cleaned["report"]}
        panel["analysis"]["cleaning"] = {
            "before_missing": cleaned["report"]["before_missing"],
            "after_missing": cleaned["report"]["after_missing"],
            "filled_cell_count": len(cleaned["report"]["filled"]),
            "unresolved_cell_count": len(cleaned["report"]["unresolved"]),
            "descriptive_statistics": "May include explicitly imputed measurements; event windows require originally observed measurements.",
        }
    panel["events"] = discover_events(
        panel,
        treatment=treatment,
        known_events=known_events,
        window=window,
        threshold=threshold,
        min_abs_change=min_abs_change,
        window_options=window_options,
        noise_tolerance=noise_tolerance,
        min_noise_periods=min_noise_periods,
        min_correlation_periods=min_correlation_periods,
        autocorrelation_threshold=autocorrelation_threshold,
        trend_residual_ratio=trend_residual_ratio,
    )
    panel["window_assessment"] = panel["events"]["window_assessment"]
    panel["analysis"]["window_assessment_summary"] = panel["window_assessment"][
        "summary"
    ]
    panel["manifest"] = {
        "schema_version": 1,
        "method": "panel-data-preprocessing/v1",
        "source_records_sha256": panel["transformation"]["source_records_sha256"],
        "configuration_sha256": _digest(
            {"panel": panel["config"], "events": panel["events"]["config"]}
        ),
        "config": panel["config"],
        "event_config": panel["events"]["config"],
        "transformations": panel["transformation"],
        "limitations": panel["events"]["limitations"],
    }
    if cleaned is not None:
        panel["manifest"].update(
            raw_source_records_sha256=_digest(list(raw_rows)),
            cleaning_model_sha256=cleaned["model"]["model_sha256"],
            cleaning_config=cleaned["model"]["config"],
            cleaning_decision=cleaned["model"]["decision"],
        )
        panel["manifest"]["configuration_sha256"] = _digest(
            {
                "panel": panel["config"],
                "events": panel["events"]["config"],
                "cleaning_model_sha256": cleaned["model"]["model_sha256"],
            }
        )
    return panel


def write_outputs(result: Mapping[str, Any], directory: str | Path) -> dict[str, str]:
    """Export a new result bundle; refuse to replace any existing output file."""
    root = Path(directory)
    names = (
        "panel.csv",
        "summary.json",
        "candidate_events.csv",
        "window_assessment.json",
        "window_assessment.csv",
        "manifest.json",
    )
    if "cleaning" in result:
        names += ("cleaning_report.json", "cleaning_model.json")
    if any((root / name).exists() for name in names):
        raise FileExistsError("output bundle already exists; choose a new directory")
    root.mkdir(parents=True, exist_ok=True)
    tables = {
        "panel.csv": result["rows"],
        "candidate_events.csv": result["events"]["candidates"],
    }
    assessment = result["window_assessment"]
    tables["window_assessment.csv"] = [
        {
            **{k: v for k, v in a.items() if k not in {"coverage", "pre_statistics"}},
            **{f"coverage.{k}": v for k, v in a["coverage"].items()},
            **{f"pre.{k}": v for k, v in (a["pre_statistics"] or {}).items()},
        }
        for a in assessment["assessments"]
    ]
    for name, rows in tables.items():
        fields = sorted({k for row in rows for k in row}) or [
            "kind",
            "entity",
            "time",
            "causal_status",
        ]
        with (root / name).open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        k: _canonical(v) if isinstance(v, (dict, list)) else v
                        for k, v in row.items()
                    }
                )
    with (root / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(
            result["analysis"], handle, ensure_ascii=False, indent=2, allow_nan=False
        )
        handle.write("\n")
    with (root / "window_assessment.json").open("x", encoding="utf-8") as handle:
        json.dump(assessment, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    if "cleaning" in result:
        for filename, key in (
            ("cleaning_report.json", "report"),
            ("cleaning_model.json", "model"),
        ):
            with (root / filename).open("x", encoding="utf-8") as handle:
                json.dump(
                    result["cleaning"][key],
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                handle.write("\n")
    manifest = {
        **result["manifest"],
        "outputs": {
            name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in names
            if name != "manifest.json"
        },
    }
    with (root / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return {name: str((root / name).resolve()) for name in names}


def render_diagnostics(
    result: Mapping[str, Any],
    directory: str | Path,
    *,
    entities: Sequence[str | int] | None = None,
    metrics: Sequence[str] | None = None,
) -> dict[str, str]:
    """Render selected trajectories and full-panel coverage with optional matplotlib."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError(
            "diagnostic plots require matplotlib in the scientific environment"
        ) from exc

    config, rows = result["config"], result["rows"]
    entity = config["entity"]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_canonical(row[entity])].append(row)
    all_ids = [group[0][entity] for group in groups.values()]
    selected = list(all_ids if entities is None else entities)
    keys = [_canonical(v) for v in selected]
    if not selected or len(selected) > 12:
        raise ValueError(
            "select between one and twelve entities explicitly for trajectory review"
        )
    if len(keys) != len(set(keys)) or any(k not in groups for k in keys):
        raise ValueError("selected entities must be unique IDs present in the panel")
    measures = list(config["numeric_columns"] if metrics is None else metrics)
    if (
        not measures
        or len(measures) != len(set(measures))
        or any(m not in config["numeric_columns"] for m in measures)
    ):
        raise ValueError("selected metrics must be unique numeric columns")
    root = Path(directory)
    stems = [f"metric-{i}" for i in range(1, len(measures) + 1)] + ["sample-coverage"]
    if "window_assessment" in result:
        stems.append("window-readiness")
    names = [f"{s}.{ext}" for s in stems for ext in ("png", "svg")] + [
        "visualization.json"
    ]
    if any((root / name).exists() for name in names):
        raise FileExistsError(
            "diagnostic bundle already exists; choose a new directory"
        )
    root.mkdir(parents=True, exist_ok=True)
    first = min(r["_period_index"] for r in rows)
    last = max(r["_period_index"] for r in rows)
    ticks = sorted({r["_period_index"] for r in rows})
    stride = max(1, math.ceil(len(ticks) / 12))
    ticks = ticks[::stride]
    if last not in ticks:
        ticks.append(last)
    outputs: dict[str, str] = {}

    def save(figure: Any, stem: str) -> None:
        FigureCanvasAgg(figure)
        for ext in ("png", "svg"):
            path = root / f"{stem}.{ext}"
            metadata = (
                {"Date": None}
                if ext == "svg"
                else {"Software": "OpenAI4S panel-data-preprocessing"}
            )
            figure.savefig(path, dpi=180, metadata=metadata)
            outputs[path.name] = str(path.resolve())

    for number, metric in enumerate(measures, 1):
        figure = Figure(figsize=(11, max(3, 2.5 * len(selected))), layout="constrained")
        axes = figure.subplots(len(selected), 1, squeeze=False)
        figure.suptitle(f"{metric}: selected entity trajectories (exploratory)")
        for key, label, axis in zip(keys, selected, axes[:, 0]):
            group = sorted(groups[key], key=lambda r: r["_period_index"])
            x, y = [], []
            previous = None
            for row in group:
                if previous is not None and row["_period_index"] - previous > 1:
                    x.append(previous + 1)
                    y.append(float("nan"))
                x.append(row["_period_index"])
                y.append(
                    float("nan")
                    if row[metric] is None or metric in row.get(_IMPUTED, [])
                    else row[metric]
                )
                previous = row["_period_index"]
            axis.plot(x, y, color="#246b8e", marker="o", markersize=3, linewidth=1.3)
            seen: set[str] = set()
            imputed = [
                r
                for r in group
                if metric in r.get(_IMPUTED, []) and r[metric] is not None
            ]
            if imputed:
                axis.scatter(
                    [r["_period_index"] for r in imputed],
                    [r[metric] for r in imputed],
                    marker="D",
                    facecolors="none",
                    edgecolors="#c67c16",
                    label="Imputed value",
                    zorder=3,
                )
                seen.add("Imputed value")
            for event in result["events"]["candidates"]:
                if event["entity"] is not None and _canonical(event["entity"]) != key:
                    continue
                if (
                    event.get("metric", metric) != metric
                    or not first <= event["period_index"] <= last
                ):
                    continue
                kind = event["kind"]
                if kind == "documented_event":
                    color, legend = "#666666", "Known date"
                elif kind == "level_shift_candidate":
                    color, legend = "#bc3d6c", "Numeric candidate"
                elif kind in {
                    "treatment_adoption",
                    "treatment_reversal",
                    "left_censored_exposure",
                    "interval_censored_transition",
                }:
                    color, legend = "#c67c16", "Exposure record"
                else:
                    continue
                axis.axvline(
                    event["period_index"],
                    color=color,
                    linestyle="--",
                    alpha=0.65,
                    label=legend if legend not in seen else None,
                )
                seen.add(legend)
                if kind == "documented_event":
                    axis.text(
                        event["period_index"],
                        0.97,
                        event["label"],
                        transform=axis.get_xaxis_transform(),
                        fontsize=7,
                        rotation=90,
                        va="top",
                        ha="right",
                    )
            axis.set_title(f"{entity} = {label}", loc="left", fontsize=10)
            axis.set_ylabel(metric)
            axis.set_xlim(first - 0.25, last + 0.25)
            axis.set_xticks(
                ticks,
                [_period_label(t, config["frequency"]) for t in ticks],
                rotation=30,
            )
            axis.grid(alpha=0.18)
            if seen:
                axis.legend(loc="lower left", fontsize=7, ncol=3)
        save(figure, f"metric-{number}")

    figure = Figure(figsize=(11, 4), layout="constrained")
    axis = figure.subplots()
    counts = {
        p["period_index"]: p["entity_count"] for p in result["analysis"]["timeline"]
    }
    calendar = list(range(first, last + 1))
    axis.bar(calendar, [counts.get(p, 0) for p in calendar], color="#246b8e")
    axis.set_title("Full-panel sample coverage (all entities)")
    axis.set_ylabel("Observed entities")
    axis.set_xticks(
        ticks, [_period_label(t, config["frequency"]) for t in ticks], rotation=30
    )
    save(figure, "sample-coverage")
    if "window_assessment" in result:
        report = result["window_assessment"]
        summary = report["summary"]
        figure = Figure(figsize=(11, 7), layout="constrained")
        availability, noise = figure.subplots(2, 1)
        windows = [s["window"] for s in summary]
        eligible = [s["eligible_anchor_count"] for s in summary]
        availability.bar(windows, eligible, color="#246b8e")
        availability.set_title("Complete adjacent pre/post windows: full panel")
        availability.set_ylabel("Eligible entity-metric-time anchors")
        availability.set_xticks(windows)
        availability.set_xlabel("Periods on each side")
        bottom = [0] * len(windows)
        for statuses, label, color in [
            (
                {"within_tolerance_under_iid"},
                "Within target (IID assumption)",
                "#4b9570",
            ),
            ({"high_noise_under_iid"}, "Above mean-noise target (IID)", "#bc3d6c"),
            ({"noise_baseline_short"}, "Short variance baseline", "#c67c16"),
            (
                {"review_trend", "review_serial_dependence"},
                "Trend/dependence review",
                "#7d6bb0",
            ),
            ({"tolerance_not_configured"}, "No tolerance configured", "#888888"),
        ]:
            counts = [
                sum(
                    s["eligible_noise_status_counts"].get(status, 0)
                    for status in statuses
                )
                for s in summary
            ]
            noise.bar(windows, counts, bottom=bottom, label=label, color=color)
            bottom = [a + b for a, b in zip(bottom, counts)]
        noise.set_title("Pre-window variability: eligible anchors only")
        noise.set_ylabel("Entity-metric-time anchors")
        noise.set_xticks(windows)
        noise.set_xlabel(
            "Periods on each side; advice never changes screening automatically"
        )
        noise.legend(fontsize=8, ncol=2)
        if not any(eligible):
            availability.text(
                0.5,
                0.5,
                "No inspected window has complete pre/post coverage",
                transform=availability.transAxes,
                ha="center",
                fontsize=10,
            )
        save(figure, "window-readiness")
    manifest = {
        "selected_entities": selected,
        "excluded_entities": [v for v in all_ids if _canonical(v) not in keys],
        "selected_metrics": measures,
        "source_records_sha256": result["transformation"]["source_records_sha256"],
        "config_sha256": _digest(
            {"panel": config, "events": result["events"]["config"]}
        ),
        "outputs": {
            name: {"sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for name, path in outputs.items()
        },
        "interpretation": "Selected trajectories are descriptive. Coverage uses the full sample. Candidate dates do not identify causal effects.",
    }
    path = root / "visualization.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    outputs[path.name] = str(path.resolve())
    return outputs
