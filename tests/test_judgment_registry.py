"""Template registry for the experimental judgment layer."""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from openai4s.judgment.registry import (
    PROBE_PURPOSE,
    PROBE_TEMPLATE_ID,
    CustomQuestionSpec,
    get_template,
    register_template,
    unregister_template,
)
from openai4s.judgment.types import Answer, Noul, Score


def _questions(_params: Mapping[str, Any]) -> dict[str, Noul]:
    return {"q": Noul(instructions="Is state.text a greeting?")}


def _ok_policy(_answers: Mapping[str, Answer], _params: Mapping[str, Any]) -> str:
    return "ok"


def test_builtin_probe_template() -> None:
    template = get_template(PROBE_TEMPLATE_ID)
    assert template.template_id == PROBE_TEMPLATE_ID
    assert template.purpose == PROBE_PURPOSE
    questions = template.build_questions({})
    assert set(questions) == {"alive"}
    assert isinstance(questions["alive"], Noul)
    assert template.allow_custom is None


def test_get_unknown_template_raises() -> None:
    with pytest.raises(KeyError, match="unknown template"):
        get_template("no.such.template")


def test_duplicate_register_raises() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_template(
            PROBE_TEMPLATE_ID,
            "9",
            _questions,
            _ok_policy,
            purpose="probe",
        )


def test_register_get_and_unregister() -> None:
    template_id = "test.registry.unique"
    unregister_template(template_id)
    registered = register_template(
        template_id,
        "3",
        _questions,
        _ok_policy,
        purpose="skill_suggest",
        policy_version="p-7",
    )
    try:
        fetched = get_template(template_id)
        assert fetched is registered
        assert fetched.version == "3"
        assert fetched.policy_version == "p-7"
        assert fetched.purpose == "skill_suggest"
    finally:
        unregister_template(template_id)
    with pytest.raises(KeyError, match="unknown template"):
        get_template(template_id)


def test_cannot_unregister_builtin_probe() -> None:
    with pytest.raises(ValueError, match="system.probe"):
        unregister_template(PROBE_TEMPLATE_ID)
    assert get_template(PROBE_TEMPLATE_ID).template_id == PROBE_TEMPLATE_ID


def test_allow_custom_is_stored() -> None:
    template_id = "test.registry.custom"
    unregister_template(template_id)
    register_template(
        template_id,
        "1",
        _questions,
        _ok_policy,
        purpose="text_features",
        allow_custom={
            "kinds": ("noul", "score"),
            "max_questions": 4,
            "max_question_chars": 120,
            "max_state_chars": 500,
        },
    )
    try:
        spec = get_template(template_id).allow_custom
        assert spec == CustomQuestionSpec(
            kinds=("noul", "score"),
            max_questions=4,
            max_question_chars=120,
            max_state_chars=500,
        )
    finally:
        unregister_template(template_id)


def test_allow_custom_rejects_choice() -> None:
    with pytest.raises(ValueError, match="noul"):
        CustomQuestionSpec(
            kinds=("choice",),
            max_questions=1,
            max_question_chars=10,
            max_state_chars=10,
        )


def test_policy_and_builder_round_trip() -> None:
    template_id = "test.registry.policy"
    unregister_template(template_id)

    def build(params: Mapping[str, Any]) -> dict[str, Score]:
        return {
            "s": Score(
                instructions=str(params.get("hint") or "Rate state.text"),
                levels=("low", "high"),
            )
        }

    def policy(answers: Mapping[str, Answer], _params: Mapping[str, Any]) -> str:
        answer = answers["s"]
        return "ok" if float(answer.value) >= 1 else "uncertain"

    register_template(template_id, "1", build, policy, purpose="text_features")
    try:
        template = get_template(template_id)
        questions = template.build_questions({"hint": "How strong is the claim?"})
        assert questions["s"].instructions == "How strong is the claim?"
        assert (
            template.policy(
                {
                    "s": Answer(
                        kind="score", value=1.2, probabilities={"0": 0.2, "1": 0.8}
                    )
                },
                {},
            )
            == "ok"
        )
    finally:
        unregister_template(template_id)
