"""literature.screen and literature.claim templates.

Questions are English. Each ``instructions`` names the state field it reads
because question keys are not sent to the model. Numeric comparison and quote
location stay in the literature-review sidecar; Jev only judges semantics.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from openai4s.judgment.registry import get_template, register_template
from openai4s.judgment.types import Answer, Choice, Noul, Question

CLAIM_CONF = 0.8
SCREEN_RELEVANT = 0.45
SCREEN_EVIDENCE = 0.55
SCREEN_CONTRADICT = 0.70
FUZZY_MIN = 0.9
TEMPLATE_VERSION = "1"

TEMPLATE_ID_SCREEN = "literature.screen"
TEMPLATE_ID_CLAIM = "literature.claim"
PURPOSE = "literature_check"


def policy_version() -> str:
    """Cache-key component. Threshold edits must miss the in-process LRU."""

    return (
        f"{TEMPLATE_VERSION}:"
        f"claim_conf={CLAIM_CONF:.2f}:"
        f"rel={SCREEN_RELEVANT:.2f}:"
        f"ev={SCREEN_EVIDENCE:.2f}:"
        f"contra={SCREEN_CONTRADICT:.2f}"
    )


POLICY_VERSION = policy_version()


def _noul_value(answers: Mapping[str, Answer], key: str) -> float | None:
    answer = answers.get(key)
    if answer is None or answer.kind != "noul":
        return None
    try:
        return float(answer.value)
    except (TypeError, ValueError):
        return None


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _screen_questions(params: Mapping[str, Any]) -> Mapping[str, Question]:
    questions: dict[str, Question] = {
        "relevant": Noul(
            instructions=(
                "Does the passage in state.passage address the subject of the "
                "question in state.question?"
            ),
            criteria={
                "true": (
                    "The passage is about the same topic as state.question, "
                    "even if it does not fully answer it."
                ),
                "false": (
                    "The passage is off-topic for state.question or only "
                    "shares incidental wording."
                ),
            },
        ),
        "has_evidence": Noul(
            instructions=(
                "Does the passage in state.passage contain evidence needed to "
                "answer the question in state.question — data, results, or an "
                "explicit statement — rather than only background or opinion?"
            ),
            criteria={
                "true": (
                    "The passage states data, results, or a claim that could "
                    "be used to answer state.question."
                ),
                "false": (
                    "The passage does not contain usable evidence for "
                    "state.question."
                ),
            },
        ),
    }
    if _truthy(params.get("has_hypothesis")):
        questions["contradicts_hypothesis"] = Noul(
            instructions=(
                "Does the passage in state.passage contradict the hypothesis "
                "in state.hypothesis? Answer yes only when the passage states "
                "or directly implies the opposite of that hypothesis."
            ),
            criteria={
                "true": (
                    "The passage states or implies that state.hypothesis is " "false."
                ),
                "false": (
                    "The passage does not contradict state.hypothesis; it may "
                    "support it, be silent, or be unrelated."
                ),
            },
        )
    return questions


def _screen_policy(
    answers: Mapping[str, Answer], params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    needed = ["relevant", "has_evidence"]
    if _truthy(params.get("has_hypothesis")):
        needed.append("contradicts_hypothesis")
    for key in needed:
        value = _noul_value(answers, key)
        if value is None or not (0.0 <= value <= 1.0):
            return "uncertain"
    return "ok"


def _claim_questions(_params: Mapping[str, Any]) -> Mapping[str, Question]:
    return {
        "relation": Choice(
            instructions=(
                "How does the section in state.section relate to the claim in "
                "state.claim? Judge only the semantic relationship. Do not "
                "compare numbers or units; those are checked in code."
            ),
            options={
                "supports": (
                    "The section states the claim or directly implies that "
                    "the claim is true."
                ),
                "contradicts": (
                    "The section states the opposite of the claim or implies "
                    "that the claim is false."
                ),
                "insufficient": (
                    "The section does not address what the claim asserts, "
                    "either way."
                ),
            },
        )
    }


def _claim_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    answer = answers.get("relation")
    if answer is None or answer.kind != "choice":
        return "uncertain"
    if str(answer.value) not in {"supports", "contradicts", "insufficient"}:
        return "uncertain"
    confidence = answer.confidence
    if confidence is None:
        return "uncertain"
    try:
        if float(confidence) < CLAIM_CONF:
            return "uncertain"
    except (TypeError, ValueError):
        return "uncertain"
    return "ok"


def _register() -> None:
    try:
        get_template(TEMPLATE_ID_SCREEN)
        return
    except KeyError:
        pass
    shared = policy_version()
    register_template(
        TEMPLATE_ID_SCREEN,
        TEMPLATE_VERSION,
        _screen_questions,
        _screen_policy,
        purpose=PURPOSE,
        policy_version=shared,
    )
    register_template(
        TEMPLATE_ID_CLAIM,
        TEMPLATE_VERSION,
        _claim_questions,
        _claim_policy,
        purpose=PURPOSE,
        policy_version=shared,
    )


_register()


__all__ = [
    "CLAIM_CONF",
    "FUZZY_MIN",
    "POLICY_VERSION",
    "PURPOSE",
    "SCREEN_CONTRADICT",
    "SCREEN_EVIDENCE",
    "SCREEN_RELEVANT",
    "TEMPLATE_ID_CLAIM",
    "TEMPLATE_ID_SCREEN",
    "TEMPLATE_VERSION",
    "policy_version",
]
