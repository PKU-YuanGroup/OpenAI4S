"""Versioned synthetic panel preprocessing replays against the real sidecar."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

DEFAULT_CASES_PATH = Path(__file__).with_name("panel_data_cases.json")


def _kernel():
    path = (
        Path(__file__).resolve().parents[2]
        / "skills/panel-data-preprocessing/kernel.py"
    )
    spec = importlib.util.spec_from_file_location("panel_data_eval_kernel", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("panel preprocessing sidecar is unavailable")
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
    return actual == expected


def evaluate_panel_cases(path: str | Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = record.get("cases")
    if record.get("version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("panel evaluation requires version 1 and non-empty cases")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("panel evaluation case IDs must be unique and non-empty")
    module = _kernel()
    outcomes = []
    for case in cases:
        operation = case.get("operation")
        if operation not in {"prepare_panel", "preprocess_panel", "clean_data"}:
            raise ValueError(f"unknown evaluation operation: {operation}")
        expected = case.get("expected")
        error = case.get("expected_error")
        if (expected is None) == (error is None):
            raise ValueError("case must declare exactly one result or expected error")
        try:
            actual = getattr(module, operation)(case["rows"], **case["kwargs"])
            passed = error is None and _matches(actual, expected)
            detail = "" if passed else "result did not match the declared outcome"
        except Exception as exc:
            passed = (
                isinstance(exc, ValueError)
                and isinstance(error, str)
                and error in str(exc)
            )
            detail = "" if passed else f"{type(exc).__name__}: {exc}"
        outcomes.append({"case_id": case["id"], "passed": passed, "detail": detail})
    passed = sum(case["passed"] for case in outcomes)
    return {
        "version": 1,
        "production_backed": True,
        "scope": "real sidecar; synthetic inputs; no LLM/kernel or visual judgment",
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
    report = evaluate_panel_cases(args.cases)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for case in report["cases"]:
            print(
                f"{'PASS' if case['passed'] else 'FAIL'} {case['case_id']} {case['detail']}"
            )
        print(f"{report['passed']}/{report['total']} panel replays passed")
    return int(report["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
