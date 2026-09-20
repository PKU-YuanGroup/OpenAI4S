"""Each parse_response rule has a legal case and an illegal case."""

from __future__ import annotations

from typing import Any

import pytest

from openai4s.judgment.port import BackendError
from openai4s.judgment.types import Answer, Choice, Noul, Score
from openai4s.judgment.validate import parse_response

NOUL = Noul(instructions="Does state.text convey urgency?")
CHOICE = Choice(
    instructions="Which team should handle state.text?",
    options={"billing": "Payments", "technical": "Bugs", "sales": "Pricing"},
)
SCORE = Score(
    instructions="How frustrated is the customer in state.text?",
    levels=("Calm", "Frustrated", "Very angry"),
)
QUESTIONS = {"is_urgent": NOUL, "department": CHOICE, "frustration": SCORE}


def _noul_answer(value: float = 0.5) -> dict[str, Any]:
    return {"type": "noul", "noul": value}


def _choice_answer(
    selected: str = "billing",
    probabilities: dict[str, float] | None = None,
    confidence: float = 0.81,
) -> dict[str, Any]:
    if probabilities is None:
        probabilities = {"billing": 0.88, "technical": 0.12, "sales": 0.0}
    return {
        "type": "choice",
        "choice": selected,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _score_answer(
    score: float = 1.05,
    probabilities: dict[str, float] | None = None,
    legend: dict[str, str] | None = None,
    confidence: float = 0.92,
) -> dict[str, Any]:
    if probabilities is None:
        probabilities = {"0": 0.0, "1": 0.95, "2": 0.05}
    if legend is None:
        legend = {"0": "Calm", "1": "Frustrated", "2": "Very angry"}
    return {
        "type": "score",
        "score": score,
        "legend": legend,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _payload(
    *,
    answers: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    model: str = "jev-1.13.0",
) -> dict[str, Any]:
    if answers is None:
        answers = {
            "is_urgent": _noul_answer(),
            "department": _choice_answer(),
            "frustration": _score_answer(),
        }
    if usage is None:
        usage = {"input_tokens": 296, "output_tokens": 20}
    return {"model": model, "answers": answers, "usage": usage}


def _invalid(payload: dict[str, Any], questions: dict[str, Any] = QUESTIONS) -> None:
    with pytest.raises(BackendError) as caught:
        parse_response(payload, questions)
    assert caught.value.code == "invalid_response"


def test_valid_three_types_round_trip() -> None:
    reply = parse_response(_payload(), QUESTIONS)
    assert reply.model == "jev-1.13.0"
    assert reply.usage == {"input_tokens": 296, "output_tokens": 20}
    assert reply.fake is False
    urgent = reply.answers["is_urgent"]
    assert isinstance(urgent, Answer)
    assert urgent.kind == "noul"
    assert urgent.value == 0.5
    assert urgent.probabilities is None
    assert urgent.confidence is None
    department = reply.answers["department"]
    assert department.kind == "choice"
    assert department.value == "billing"
    assert department.probabilities == {
        "billing": 0.88,
        "technical": 0.12,
        "sales": 0.0,
    }
    assert department.confidence == 0.81
    frustration = reply.answers["frustration"]
    assert frustration.kind == "score"
    assert frustration.value == 1.05
    assert frustration.probabilities == {"0": 0.0, "1": 0.95, "2": 0.05}


def test_missing_answer_id_is_invalid() -> None:
    payload = _payload()
    del payload["answers"]["department"]
    _invalid(payload)


def test_every_requested_id_is_required_when_present() -> None:
    reply = parse_response(
        {
            "model": "jev-1.13.0",
            "answers": {"is_urgent": _noul_answer(0.95)},
            "usage": {"input_tokens": 1, "output_tokens": 0},
        },
        {"is_urgent": NOUL},
    )
    assert set(reply.answers) == {"is_urgent"}


def test_extra_answer_id_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["bonus"] = _noul_answer()
    _invalid(payload)


def test_wrong_answer_type_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["is_urgent"] = _choice_answer()
    _invalid(payload)


def test_matching_types_are_accepted() -> None:
    reply = parse_response(_payload(), QUESTIONS)
    assert reply.answers["is_urgent"].kind == "noul"
    assert reply.answers["department"].kind == "choice"
    assert reply.answers["frustration"].kind == "score"


def test_choice_selected_must_belong_to_options() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(selected="legal")
    _invalid(payload)


def test_choice_selected_option_is_accepted() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(selected="technical")
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["department"].value == "technical"


def test_choice_probability_keys_must_equal_options() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"billing": 1.0, "technical": 0.0}
    )
    _invalid(payload)


def test_choice_probability_keys_match_options() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"sales": 0.2, "billing": 0.5, "technical": 0.3}
    )
    reply = parse_response(payload, QUESTIONS)
    assert set(reply.answers["department"].probabilities or {}) == {
        "billing",
        "technical",
        "sales",
    }


def test_choice_probability_outside_unit_interval_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"billing": 1.2, "technical": -0.1, "sales": -0.1}
    )
    _invalid(payload)


def test_choice_probability_endpoints_are_accepted() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"billing": 1.0, "technical": 0.0, "sales": 0.0}
    )
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["department"].probabilities["billing"] == 1.0


def test_choice_probability_sum_within_tolerance() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"billing": 0.4, "technical": 0.3, "sales": 0.3005}
    )
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["department"].value == "billing"


def test_choice_probability_sum_outside_tolerance_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(
        probabilities={"billing": 0.5, "technical": 0.5, "sales": 0.002}
    )
    _invalid(payload)


def test_choice_confidence_in_unit_interval() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(confidence=0.0)
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["department"].confidence == 0.0
    payload["answers"]["department"] = _choice_answer(confidence=1.0)
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["department"].confidence == 1.0


def test_choice_confidence_out_of_range_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(confidence=1.01)
    _invalid(payload)


def test_noul_endpoints_are_accepted() -> None:
    payload = _payload()
    payload["answers"]["is_urgent"] = _noul_answer(0.0)
    assert parse_response(payload, QUESTIONS).answers["is_urgent"].value == 0.0
    payload["answers"]["is_urgent"] = _noul_answer(1.0)
    assert parse_response(payload, QUESTIONS).answers["is_urgent"].value == 1.0


def test_noul_out_of_range_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["is_urgent"] = _noul_answer(1.01)
    _invalid(payload)
    payload["answers"]["is_urgent"] = _noul_answer(-0.01)
    _invalid(payload)


def test_noul_bool_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["is_urgent"] = {"type": "noul", "noul": True}
    _invalid(payload)


def test_score_in_range_is_accepted() -> None:
    payload = _payload()
    payload["answers"]["frustration"] = _score_answer(score=0.0)
    assert parse_response(payload, QUESTIONS).answers["frustration"].value == 0.0
    payload["answers"]["frustration"] = _score_answer(score=2.0)
    assert parse_response(payload, QUESTIONS).answers["frustration"].value == 2.0


def test_score_out_of_range_is_invalid() -> None:
    payload = _payload()
    payload["answers"]["frustration"] = _score_answer(score=2.01)
    _invalid(payload)
    payload["answers"]["frustration"] = _score_answer(score=-0.01)
    _invalid(payload)


def test_score_probability_keys_are_level_indices() -> None:
    payload = _payload()
    payload["answers"]["frustration"] = _score_answer(
        probabilities={"0": 0.2, "1": 0.8}
    )
    _invalid(payload)


def test_score_probability_keys_zero_through_n_minus_one() -> None:
    payload = _payload()
    payload["answers"]["frustration"] = _score_answer(
        probabilities={"0": 0.2, "1": 0.3, "2": 0.5}
    )
    reply = parse_response(payload, QUESTIONS)
    assert set(reply.answers["frustration"].probabilities or {}) == {"0", "1", "2"}


def test_score_legend_must_match_requested_levels() -> None:
    payload = _payload()
    payload["answers"]["frustration"] = _score_answer(
        legend={"0": "Calm", "1": "Annoyed", "2": "Very angry"}
    )
    _invalid(payload)


def test_score_legend_matches_requested_levels() -> None:
    payload = _payload()
    reply = parse_response(payload, QUESTIONS)
    assert reply.answers["frustration"].kind == "score"


def test_usage_fields_are_non_negative_integers() -> None:
    payload = _payload(usage={"input_tokens": 0, "output_tokens": 0})
    reply = parse_response(payload, QUESTIONS)
    assert reply.usage == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 1, "output_tokens": -1},
        {"input_tokens": 1.5, "output_tokens": 0},
        {"input_tokens": True, "output_tokens": 0},
        {"input_tokens": 1},
        "nope",
        None,
    ],
)
def test_usage_invalid_shapes_are_rejected(usage: object) -> None:
    payload = _payload()
    payload["usage"] = usage
    _invalid(payload)


def test_one_bad_answer_discards_the_whole_payload() -> None:
    payload = _payload()
    payload["answers"]["department"] = _choice_answer(selected="nope")
    _invalid(payload)


def test_non_object_payload_is_invalid() -> None:
    with pytest.raises(BackendError) as caught:
        parse_response(["not", "an", "object"], QUESTIONS)  # type: ignore[arg-type]
    assert caught.value.code == "invalid_response"
