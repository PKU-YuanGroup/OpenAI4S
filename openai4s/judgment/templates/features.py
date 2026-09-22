"""features.custom: caller-supplied Noul/Score questions for text featurization.

Questions are English. Each ``instructions`` names the state field it reads
(``state.text``) because question keys are not sent to the model. Specs come
from the caller; this module validates them. Kernel code must pass them as
``specs=`` — ``host.judge`` rejects a top-level ``questions`` argument.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from openai4s.judgment.registry import get_template, register_template
from openai4s.judgment.types import Answer, Noul, Question, Score

TEMPLATE_ID = "features.custom"
TEMPLATE_VERSION = "1"
PURPOSE = "text_features"

MAX_QUESTIONS = 24
MAX_QUESTION_CHARS = 400
MAX_STATE_CHARS = 8000
MAX_SCORE_LEVELS = 10
ALLOWED_KINDS = ("noul", "score")

DEFAULT_NOUL_CRITERIA = {
    "true": "The text in state.text states this or clearly implies it.",
    "false": "The text in state.text gives no indication of this.",
}


def policy_version() -> str:
    """Cache-key component. Limit edits must miss the in-process LRU."""

    return (
        f"{TEMPLATE_VERSION}:"
        f"max_q={MAX_QUESTIONS}:"
        f"max_instr={MAX_QUESTION_CHARS}:"
        f"max_state={MAX_STATE_CHARS}"
    )


POLICY_VERSION = policy_version()

ALLOW_CUSTOM = {
    "kinds": ALLOWED_KINDS,
    "max_questions": MAX_QUESTIONS,
    "max_question_chars": MAX_QUESTION_CHARS,
    "max_state_chars": MAX_STATE_CHARS,
}


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _specs_from_params(params: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw = params.get("specs", params.get("items"))
    if raw is None:
        raise ValueError("features.custom requires params.specs")
    if isinstance(raw, Mapping):
        items: list[Mapping[str, Any]] = []
        for key, spec in raw.items():
            mapping = dict(_as_mapping(spec, f"specs[{key!r}]"))
            mapping.setdefault("id", str(key))
            items.append(mapping)
        return items
    if isinstance(raw, (list, tuple)):
        items = []
        for index, spec in enumerate(raw):
            mapping = dict(_as_mapping(spec, f"specs[{index}]"))
            items.append(mapping)
        return items
    raise ValueError("features.custom params.specs must be a list or object")


def _question_id(spec: Mapping[str, Any], index: int, taken: set[str]) -> str:
    raw = spec.get("id")
    if isinstance(raw, str) and raw.strip():
        candidate = raw.strip()
    else:
        candidate = f"q{index + 1}"
    if candidate in taken:
        raise ValueError(f"features.custom duplicate question id: {candidate!r}")
    taken.add(candidate)
    return candidate


def _kind(spec: Mapping[str, Any]) -> str:
    raw = spec.get("kind") or spec.get("type")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("features.custom each spec needs kind 'noul' or 'score'")
    kind = raw.strip().lower()
    if kind in {"presence"}:
        kind = "noul"
    if kind in {"intensity"}:
        kind = "score"
    if kind not in ALLOWED_KINDS:
        raise ValueError(
            "features.custom allows only noul and score questions, " f"not {kind!r}"
        )
    return kind


def _instructions(spec: Mapping[str, Any]) -> str:
    raw = spec.get("instructions") or spec.get("question")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("features.custom instructions must be a non-empty string")
    text = raw.strip()
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(
            "features.custom instructions exceed " f"{MAX_QUESTION_CHARS} characters"
        )
    return text


def _noul_criteria(spec: Mapping[str, Any]) -> dict[str, str] | None:
    raw = spec.get("criteria")
    if raw is None:
        return dict(DEFAULT_NOUL_CRITERIA)
    mapping = _as_mapping(raw, "noul criteria")
    return {str(key): str(value) for key, value in mapping.items()}


def _score_levels(spec: Mapping[str, Any]) -> tuple[str, ...]:
    raw = spec.get("levels")
    if raw is None:
        raw = spec.get("criteria")
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ValueError("features.custom score specs need a levels list")
    levels = tuple(str(item) for item in raw)
    if len(levels) > MAX_SCORE_LEVELS:
        raise ValueError(
            f"features.custom Score allows at most {MAX_SCORE_LEVELS} levels"
        )
    return levels


def _build_one(
    spec: Mapping[str, Any], index: int, taken: set[str]
) -> tuple[str, Question]:
    qid = _question_id(spec, index, taken)
    kind = _kind(spec)
    instructions = _instructions(spec)
    if kind == "noul":
        return qid, Noul(instructions=instructions, criteria=_noul_criteria(spec))
    return qid, Score(instructions=instructions, levels=_score_levels(spec))


def build_questions(params: Mapping[str, Any]) -> Mapping[str, Question]:
    """Turn caller specs into typed questions, or raise ValueError."""

    specs = _specs_from_params(params)
    if not specs:
        raise ValueError("features.custom requires at least one question")
    if len(specs) > MAX_QUESTIONS:
        raise ValueError(f"features.custom allows at most {MAX_QUESTIONS} questions")
    taken: set[str] = set()
    questions: dict[str, Question] = {}
    for index, spec in enumerate(specs):
        qid, question = _build_one(spec, index, taken)
        questions[qid] = question
    return questions


def _features_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    if not answers:
        return "uncertain"
    for answer in answers.values():
        if answer.kind == "noul":
            try:
                value = float(answer.value)
            except (TypeError, ValueError):
                return "uncertain"
            if not (0.0 <= value <= 1.0):
                return "uncertain"
        elif answer.kind == "score":
            if answer.probabilities is None:
                return "uncertain"
            try:
                float(answer.value)
            except (TypeError, ValueError):
                return "uncertain"
        else:
            return "uncertain"
    return "ok"


def _register() -> None:
    try:
        get_template(TEMPLATE_ID)
        return
    except KeyError:
        pass
    register_template(
        TEMPLATE_ID,
        TEMPLATE_VERSION,
        build_questions,
        _features_policy,
        purpose=PURPOSE,
        allow_custom=ALLOW_CUSTOM,
        policy_version=POLICY_VERSION,
    )


_register()


__all__ = [
    "ALLOWED_KINDS",
    "ALLOW_CUSTOM",
    "DEFAULT_NOUL_CRITERIA",
    "MAX_QUESTION_CHARS",
    "MAX_QUESTIONS",
    "MAX_SCORE_LEVELS",
    "MAX_STATE_CHARS",
    "POLICY_VERSION",
    "PURPOSE",
    "TEMPLATE_ID",
    "TEMPLATE_VERSION",
    "build_questions",
    "policy_version",
]
