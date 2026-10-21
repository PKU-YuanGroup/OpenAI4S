"""Shared offline replay mechanics for the empirical science sidecars."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def load_sidecar(skill: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "skills" / skill / "kernel.py"
    spec = importlib.util.spec_from_file_location(
        skill.replace("-", "_") + "_eval_kernel", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{skill} sidecar is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matches(actual: Any, expected: Any) -> bool:
    """Match declared dictionary subsets and lists; tolerate finite float roundoff."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(matches(a, e) for a, e in zip(actual, expected))
        )
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return math.isfinite(actual) and math.isclose(
            actual, expected, rel_tol=1e-10, abs_tol=1e-10
        )
    return actual == expected


def run_case(
    module: ModuleType,
    case: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    mismatch_detail: str,
) -> dict[str, Any]:
    expected, error = case.get("expected"), case.get("expected_error")
    try:
        actual = getattr(module, case["operation"])(rows, **case["kwargs"])
        json.dumps(actual, allow_nan=False)
        passed = error is None and matches(actual, expected)
        detail = "" if passed else mismatch_detail
    except Exception as exc:
        passed = (
            isinstance(exc, ValueError) and isinstance(error, str) and error in str(exc)
        )
        detail = "" if passed else f"{type(exc).__name__}: {exc}"
    return {"case_id": case["id"], "passed": passed, "detail": detail}


def replay_report(outcomes: list[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    passed = sum(case["passed"] for case in outcomes)
    return {
        "version": 1,
        "production_backed": True,
        "scope": scope,
        "total": len(outcomes),
        "passed": passed,
        "failed": len(outcomes) - passed,
        "cases": outcomes,
    }


def replay_cli(
    evaluate: Callable[[Path], dict[str, Any]],
    default_cases: Path,
    description: str | None,
    label: str,
) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--cases", type=Path, default=default_cases)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.cases)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        for case in report["cases"]:
            print(
                f"{'PASS' if case['passed'] else 'FAIL'} {case['case_id']} {case['detail']}"
            )
        print(f"{report['passed']}/{report['total']} {label} replays passed")
    return int(report["failed"] > 0)
