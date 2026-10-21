"""Offline prespecified DiD replays against the actual bundled sidecar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .sidecar_replay import load_sidecar, replay_cli, replay_report, run_case

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
    return load_sidecar("did-analysis")


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
        outcomes.append(
            run_case(
                module,
                case,
                datasets[case["dataset"]],
                mismatch_detail="declared outcome did not match",
            )
        )
    return replay_report(
        outcomes,
        scope="actual DiD sidecar; prespecified synthetic truth and refusals; no model/kernel or causal identification claim",
    )


def main() -> int:
    return replay_cli(evaluate_did_cases, DEFAULT_CASES_PATH, __doc__, "DiD")


if __name__ == "__main__":
    raise SystemExit(main())
