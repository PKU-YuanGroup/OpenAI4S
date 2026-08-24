"""Shared stdlib helpers for independent reaction benchmark protocols."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class BenchmarkProtocolError(ValueError):
    """Raised when a scenario input or frozen output violates its contract."""


def require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkProtocolError(f"{field} must be a non-empty string")
    return value.strip()


def finite_number(value: Any, *, field: str, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        suffix = " or null" if allow_none else ""
        raise BenchmarkProtocolError(f"{field} must be a finite number{suffix}")
    number = float(value)
    if not math.isfinite(number):
        raise BenchmarkProtocolError(f"{field} must be finite")
    return number


def positive_int(value: Any, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BenchmarkProtocolError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise BenchmarkProtocolError(f"{field} must be at most {maximum}")
    return value


def json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise BenchmarkProtocolError(f"{field} must be JSON serializable") from exc


def require_exact_fields(
    value: Mapping[str, Any],
    expected: set[str] | frozenset[str],
    *,
    field: str,
) -> None:
    actual = set(value)
    if actual != set(expected):
        raise BenchmarkProtocolError(
            f"{field} has unsupported fields {sorted(actual - set(expected))} "
            f"or missing fields {sorted(set(expected) - actual)}"
        )


def sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_intermediate_artifact(
    scenario_id: str,
    records: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, auditable scenario trajectory artifact."""

    payload = {
        "schema_version": 1,
        "scenario_id": require_text(scenario_id, field="scenario_id"),
        "records": json_copy(records, field="records"),
        "metadata": json_copy(dict(metadata or {}), field="metadata"),
    }
    payload["trajectory_sha256"] = sha256_json(payload)
    return payload


__all__ = [
    "BenchmarkProtocolError",
    "build_intermediate_artifact",
    "finite_number",
    "json_copy",
    "positive_int",
    "require_exact_fields",
    "require_text",
    "sha256_json",
    "write_json_atomic",
]
