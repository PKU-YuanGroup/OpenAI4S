"""Pure-stdlib structural audits for row-oriented tabular data."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def _missing(value: object, *, empty_strings_missing: bool) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return empty_strings_missing and isinstance(value, str) and not value.strip()


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _stable(value: object) -> str:
    """Return a deterministic representation suitable for equality counts."""
    try:
        # default=repr lets unserializable leaves (datetime, Decimal, UUID) pass
        # through json.dumps so sort_keys still canonicalizes dict key order.
        # A bare repr(value) fallback preserves insertion order, so two
        # byte-identical rows with differing key order would miss as duplicates.
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=True,
            default=repr,
        )
    except (TypeError, ValueError):
        return repr(value)


def _check_columns(columns: Sequence[str], available: set[str], label: str) -> None:
    missing = sorted(set(columns) - available)
    if missing:
        raise ValueError(f"unknown {label} column(s): {', '.join(missing)}")


def audit_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    target: str | None = None,
    id_columns: Sequence[str] = (),
    group_columns: Sequence[str] = (),
    split_column: str | None = None,
    empty_strings_missing: bool = True,
    example_limit: int = 10,
) -> dict[str, Any]:
    """Audit a sequence of mappings without pandas or numpy.

    Leakage is reported when the same non-missing ID or group key appears in
    more than one split. Results contain only JSON-compatible values.
    """
    if example_limit < 0:
        raise ValueError("example_limit must be non-negative")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TypeError("rows must be a sequence of mappings")
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("every row must be a mapping")

    columns = sorted({str(key) for row in rows for key in row})
    available = set(columns)
    _check_columns(id_columns, available, "ID")
    _check_columns(group_columns, available, "group")
    if target is not None:
        _check_columns((target,), available, "target")
    if split_column is not None:
        _check_columns((split_column,), available, "split")

    column_report: dict[str, dict[str, Any]] = {}
    for column in columns:
        observed = [row.get(column) for row in rows]
        present = [
            value
            for value in observed
            if not _missing(value, empty_strings_missing=empty_strings_missing)
        ]
        types = Counter(_type_name(value) for value in present)
        column_report[column] = {
            "missing": len(observed) - len(present),
            "missing_fraction": (
                (len(observed) - len(present)) / len(rows) if rows else 0.0
            ),
            "types": dict(sorted(types.items())),
            "unique_non_missing": len({_stable(value) for value in present}),
        }

    row_counts = Counter(_stable(dict(row)) for row in rows)
    duplicate_rows = sum(count - 1 for count in row_counts.values() if count > 1)

    duplicate_id_count = 0
    duplicate_id_examples: list[list[Any]] = []
    if id_columns:
        id_counts: Counter[tuple[str, ...]] = Counter()
        id_values: dict[tuple[str, ...], list[Any]] = {}
        for row in rows:
            values = [row.get(column) for column in id_columns]
            if any(
                _missing(value, empty_strings_missing=empty_strings_missing)
                for value in values
            ):
                continue
            key = tuple(_stable(value) for value in values)
            id_counts[key] += 1
            id_values.setdefault(key, values)
        duplicate_id_count = sum(count - 1 for count in id_counts.values() if count > 1)
        duplicate_id_examples = [
            id_values[key] for key, count in id_counts.items() if count > 1
        ][:example_limit]

    target_counts: dict[str, int] | None = None
    if target is not None:
        target_counts = dict(
            sorted(
                Counter(
                    _stable(row.get(target))
                    for row in rows
                    if not _missing(
                        row.get(target), empty_strings_missing=empty_strings_missing
                    )
                ).items()
            )
        )

    leakage: dict[str, dict[str, Any]] = {}
    if split_column is not None:
        for column in (*id_columns, *group_columns):
            key_splits: defaultdict[str, set[str]] = defaultdict(set)
            raw_values: dict[str, Any] = {}
            for row in rows:
                value = row.get(column)
                split = row.get(split_column)
                if _missing(
                    value, empty_strings_missing=empty_strings_missing
                ) or _missing(split, empty_strings_missing=empty_strings_missing):
                    continue
                key = _stable(value)
                raw_values.setdefault(key, value)
                key_splits[key].add(_stable(split))
            leaked = [key for key, splits in key_splits.items() if len(splits) > 1]
            leakage[column] = {
                "count": len(leaked),
                "examples": [raw_values[key] for key in leaked[:example_limit]],
            }

    return {
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": column_report,
        "duplicate_rows": duplicate_rows,
        "duplicate_ids": {
            "count": duplicate_id_count,
            "examples": duplicate_id_examples,
        },
        "target_counts": target_counts,
        "split_leakage": leakage,
    }
