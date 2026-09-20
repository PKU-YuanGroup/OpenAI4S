"""Strict TypeSafe System One response validator.

Any single violation raises ``BackendError("invalid_response")``. Nothing is
accepted in part: a payload is either fully well-typed or it is discarded.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from openai4s.judgment.port import BackendError
from openai4s.judgment.types import Answer, BackendReply, Choice, Noul, Question, Score

_PROB_SUM_TOLERANCE = 1e-3


def parse_response(
    payload: dict[str, Any], questions: Mapping[str, Question]
) -> BackendReply:
    """Parse a System One JSON object into a ``BackendReply``.

    ``questions`` is the request map (same ids, same types). The payload must
    answer exactly those ids, with matching types and in-range values.
    """

    if not isinstance(payload, dict):
        raise _invalid("response must be an object")
    if not isinstance(questions, Mapping) or not questions:
        raise _invalid("request questions must be a non-empty mapping")

    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise _invalid("model must be a non-empty string")

    usage = _parse_usage(payload.get("usage"))
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, dict):
        raise _invalid("answers must be an object")

    expected = set(questions)
    got = set(raw_answers)
    if got != expected:
        raise _invalid("answers must match the requested question ids exactly")

    parsed: dict[str, Answer] = {}
    for qid, question in questions.items():
        item = raw_answers[qid]
        if not isinstance(item, dict):
            raise _invalid("each answer must be an object")
        parsed[qid] = _parse_answer(item, question)
    return BackendReply(answers=parsed, usage=usage, model=model.strip())


def _invalid(message: str) -> BackendError:
    return BackendError("invalid_response", message)


def _parse_usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise _invalid("usage must be an object")
    return {
        "input_tokens": _nonneg_int(raw.get("input_tokens"), "input_tokens"),
        "output_tokens": _nonneg_int(raw.get("output_tokens"), "output_tokens"),
    }


def _nonneg_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(f"{field} must be a non-negative integer")
    if value < 0:
        raise _invalid(f"{field} must be a non-negative integer")
    return value


def _unit_interval(value: object, field: str) -> float:
    number = _finite_number(value, field)
    if number < 0.0 or number > 1.0:
        raise _invalid(f"{field} must be in [0, 1]")
    return number


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise _invalid(f"{field} must be a finite number")
    return number


def _parse_answer(item: Mapping[str, Any], question: Question) -> Answer:
    declared = item.get("type")
    if isinstance(question, Noul):
        if declared != "noul":
            raise _invalid("answer type must match the requested noul")
        return Answer(kind="noul", value=_unit_interval(item.get("noul"), "noul"))
    if isinstance(question, Choice):
        if declared != "choice":
            raise _invalid("answer type must match the requested choice")
        return _parse_choice(item, question)
    if isinstance(question, Score):
        if declared != "score":
            raise _invalid("answer type must match the requested score")
        return _parse_score(item, question)
    raise _invalid("unknown question type")


def _parse_choice(item: Mapping[str, Any], question: Choice) -> Answer:
    selected = item.get("choice")
    options = set(question.options)
    if not isinstance(selected, str) or selected not in options:
        raise _invalid("choice must be one of the requested options")
    probabilities = _parse_probabilities(item.get("probabilities"), options)
    confidence = _unit_interval(item.get("confidence"), "confidence")
    return Answer(
        kind="choice",
        value=selected,
        probabilities=probabilities,
        confidence=confidence,
    )


def _parse_score(item: Mapping[str, Any], question: Score) -> Answer:
    n_levels = len(question.levels)
    score = _finite_number(item.get("score"), "score")
    if score < 0.0 or score > float(n_levels - 1):
        raise _invalid("score must be in [0, levels-1]")
    expected_keys = {str(index) for index in range(n_levels)}
    probabilities = _parse_probabilities(item.get("probabilities"), expected_keys)
    legend = item.get("legend")
    if not isinstance(legend, Mapping):
        raise _invalid("score legend must be an object")
    if set(legend) != expected_keys:
        raise _invalid("score legend keys must be '0'..'n-1'")
    for index, level in enumerate(question.levels):
        label = legend.get(str(index))
        if not isinstance(label, str) or label != level:
            raise _invalid("score legend must match the requested levels")
    confidence = _unit_interval(item.get("confidence"), "confidence")
    return Answer(
        kind="score",
        value=score,
        probabilities=probabilities,
        confidence=confidence,
    )


def _parse_probabilities(raw: object, expected_keys: set[str]) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise _invalid("probabilities must be an object")
    if set(raw) != expected_keys:
        raise _invalid("probability keys must match the requested set exactly")
    parsed: dict[str, float] = {}
    for key in expected_keys:
        parsed[key] = _unit_interval(raw[key], "probability")
    total = math.fsum(parsed.values())
    if abs(total - 1.0) > _PROB_SUM_TOLERANCE:
        raise _invalid("probabilities must sum to 1")
    return parsed


__all__ = ["parse_response"]
