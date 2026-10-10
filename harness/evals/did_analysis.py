"""Offline prespecified DiD replays against the actual bundled sidecar."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_CASES_PATH = Path(__file__).with_name("did_analysis_cases.json")
OPERATIONS = {
    "traditional_did",
    "event_study",
    "placebo_time",
    "placebo_group",
    "placebo_distribution",
    "triple_difference",
    "staggered_did",
}


def _kernel():
    path = Path(__file__).resolve().parents[2] / "skills/did-analysis/kernel.py"
    spec = importlib.util.spec_from_file_location("did_analysis_eval_kernel", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("DiD sidecar is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _matches(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_matches(a, e) for a, e in zip(actual, expected))
        )
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return math.isfinite(actual) and math.isclose(
            actual, expected, rel_tol=1e-10, abs_tol=1e-10
        )
    return actual == expected


def evaluate_did_cases(path: str | Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = record.get("cases")
    datasets = record.get("datasets")
    if record.get("version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("DiD replay requires version 1 and nonempty cases")
    if not isinstance(datasets, dict) or not datasets:
        raise ValueError("DiD replay requires declared datasets")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("case IDs must be unique nonempty strings")
    module = _kernel()
    outcomes = []
    for case in cases:
        operation = case.get("operation")
        if operation not in OPERATIONS:
            raise ValueError(f"unknown DiD operation: {operation}")
        if case.get("dataset") not in datasets:
            raise ValueError("case refers to an undeclared dataset")
        expected, error = case.get("expected"), case.get("expected_error")
        if (expected is None) == (error is None):
            raise ValueError("declare exactly one expected result or refusal")
        try:
            rows = datasets[case["dataset"]]
            actual = getattr(module, operation)(rows, **case["kwargs"])
            json.dumps(actual, allow_nan=False)
            passed = error is None and _matches(actual, expected)
            detail = "" if passed else "declared outcome did not match"
        except Exception as exc:
            passed = (
                isinstance(exc, ValueError)
                and isinstance(error, str)
                and error in str(exc)
            )
            detail = "" if passed else f"{type(exc).__name__}: {exc}"
        outcomes.append({"case_id": case["id"], "passed": passed, "detail": detail})
    passed = sum(c["passed"] for c in outcomes)
    return {
        "version": 1,
        "production_backed": True,
        "scope": "actual DiD sidecar; prespecified synthetic truth and refusals; no model/kernel or causal identification claim",
        "total": len(outcomes),
        "passed": passed,
        "failed": len(outcomes) - passed,
        "cases": outcomes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate_did_cases(args.cases)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"DiD replay: {report['passed']}/{report['total']} passed")
        for case in report["cases"]:
            if not case["passed"]:
                print(f"{case['case_id']}: {case['detail']}")
    return int(report["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
