"""Deterministic evaluation of recorded retrosynthesis backend responses."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from openai4s.config import get_config

DEFAULT_CASES_PATH = Path(__file__).with_name("retrosynthesis_backend_cases.json")


def _backend_module():
    skills_dir = str(get_config().skills_dir)
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    return importlib.import_module("retrosynthesis_planning.external_backends")


def load_backend_replay_cases(
    path: str | Path = DEFAULT_CASES_PATH,
) -> list[dict[str, Any]]:
    """Load strict, versioned recorded backend responses for offline scoring."""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("retrosynthesis backend case file must contain an object")
    if payload.get("schema_version") != 1:
        raise ValueError(
            f"unsupported backend case schema_version {payload.get('schema_version')!r}"
        )
    if set(payload) != {"schema_version", "cases"}:
        raise ValueError("backend case file contains unsupported top-level fields")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("backend case file must contain a non-empty cases list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, Mapping):
            raise ValueError(f"backend case {index} must be an object")
        if set(case) != {"id", "response", "expect"}:
            raise ValueError(f"backend case {index} has unsupported fields")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"backend case {index}.id must be a non-empty string")
        if case_id in seen:
            raise ValueError(f"duplicate backend case id {case_id!r}")
        seen.add(case_id)
        response = case.get("response")
        expect = case.get("expect")
        if not isinstance(response, Mapping) or not isinstance(expect, Mapping):
            raise ValueError(
                f"backend case {case_id!r} response/expect must be objects"
            )
        normalized.append(
            {"id": case_id, "response": dict(response), "expect": dict(expect)}
        )
    return normalized


def _case_passed(
    normalized: Mapping[str, Any], expect: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected_ok = expect.get("ok")
    if not isinstance(expected_ok, bool):
        errors.append("expect.ok must be a boolean")
    elif normalized.get("ok") is not expected_ok:
        errors.append(f"expected ok={expected_ok}, got {normalized.get('ok')}")

    if expected_ok:
        prediction_count = expect.get("prediction_count")
        if isinstance(prediction_count, bool) or not isinstance(prediction_count, int):
            errors.append("expect.prediction_count must be an integer")
        elif len(normalized.get("predictions") or []) != prediction_count:
            errors.append(
                f"expected {prediction_count} predictions, got "
                f"{len(normalized.get('predictions') or [])}"
            )
        expected_provenance = expect.get("provenance_status")
        if expected_provenance is not None and (
            normalized.get("provenance_status") != expected_provenance
        ):
            errors.append(
                f"expected provenance {expected_provenance!r}, got "
                f"{normalized.get('provenance_status')!r}"
            )
    else:
        expected_code = expect.get("error_code")
        actual_code = (normalized.get("error") or {}).get("code")
        if expected_code != actual_code:
            errors.append(f"expected error code {expected_code!r}, got {actual_code!r}")
    return not errors, errors


def evaluate_backend_replays(
    cases: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score recorded responses without loading a model, GPU, or network."""
    module = _backend_module()
    case_list = list(cases) if cases is not None else load_backend_replay_cases()
    results: list[dict[str, Any]] = []
    total_predictions = 0
    scored_predictions = 0
    success_cases = 0
    complete_provenance = 0

    for case in case_list:
        case_id = str(case.get("id") or "")
        errors: list[str] = []
        normalized: dict[str, Any] | None = None
        try:
            normalized = module.normalize_external_backend_response(
                case.get("response")
            )
        except Exception as exc:
            errors.append(f"schema validation failed: {type(exc).__name__}: {exc}")
        if normalized is not None:
            passed, expectation_errors = _case_passed(
                normalized, case.get("expect") or {}
            )
            errors.extend(expectation_errors)
            canonical = json.dumps(
                normalized,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            if normalized.get("ok"):
                success_cases += 1
                if normalized.get("provenance_status") == "complete":
                    complete_provenance += 1
                predictions = normalized.get("predictions") or []
                total_predictions += len(predictions)
                scored_predictions += sum(
                    prediction.get("score") is not None for prediction in predictions
                )
        else:
            passed = False
            digest = None
        results.append(
            {
                "id": case_id,
                "passed": passed and not errors,
                "errors": errors,
                "normalized_sha256": digest,
            }
        )

    passed_count = sum(result["passed"] for result in results)
    case_count = len(results)
    return {
        "schema_version": 1,
        "case_count": case_count,
        "passed": passed_count,
        "failed": case_count - passed_count,
        "accuracy": passed_count / case_count if case_count else 0.0,
        "success_case_count": success_cases,
        "complete_provenance_rate": (
            complete_provenance / success_cases if success_cases else 0.0
        ),
        "prediction_count": total_predictions,
        "scored_prediction_rate": (
            scored_predictions / total_predictions if total_predictions else 0.0
        ),
        "cases": results,
    }


__all__ = [
    "DEFAULT_CASES_PATH",
    "evaluate_backend_replays",
    "load_backend_replay_cases",
]
