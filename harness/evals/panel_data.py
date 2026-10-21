"""Versioned synthetic panel preprocessing replays against the real sidecar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .sidecar_replay import load_sidecar, replay_cli, replay_report, run_case

DEFAULT_CASES_PATH = Path(__file__).with_name("panel_data_cases.json")


def _kernel():
    return load_sidecar("panel-data-preprocessing")


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
        outcomes.append(
            run_case(
                module,
                case,
                case["rows"],
                mismatch_detail="result did not match the declared outcome",
            )
        )
    return replay_report(
        outcomes,
        scope="real sidecar; synthetic inputs; no LLM/kernel or visual judgment",
    )


def main() -> int:
    return replay_cli(evaluate_panel_cases, DEFAULT_CASES_PATH, __doc__, "panel")


if __name__ == "__main__":
    raise SystemExit(main())
