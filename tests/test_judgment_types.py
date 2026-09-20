"""Contract tests for judgment question/answer types and the NullBackend port."""

from __future__ import annotations

import json

import pytest

from openai4s.judgment.port import (
    BACKEND_ERROR_CODES,
    BackendError,
    NullBackend,
)
from openai4s.judgment.types import (
    Answer,
    BackendReply,
    Choice,
    JudgmentResult,
    Noul,
    Score,
)


def test_noul_rejects_empty_instructions() -> None:
    with pytest.raises(ValueError, match="instructions"):
        Noul(instructions="")
    with pytest.raises(ValueError, match="instructions"):
        Noul(instructions="   ")


def test_noul_criteria_must_be_true_and_false() -> None:
    with pytest.raises(ValueError, match="true"):
        Noul(instructions="Is this true?", criteria={"true": "yes"})
    with pytest.raises(ValueError, match="true"):
        Noul(
            instructions="Is this true?",
            criteria={"true": "yes", "false": "no", "maybe": "huh"},
        )
    ok = Noul(
        instructions="Is this true of state.text?",
        criteria={"false": "no", "true": "yes"},
    )
    assert ok.criteria == {"true": "yes", "false": "no"}


def test_noul_to_api_omits_missing_criteria() -> None:
    bare = Noul(instructions="Does state.text ask for a computation?")
    assert bare.to_api() == {
        "type": "noul",
        "instructions": "Does state.text ask for a computation?",
    }
    with_criteria = Noul(
        instructions="Does state.text ask for a computation?",
        criteria={"true": "wants action", "false": "explanation only"},
    )
    assert with_criteria.to_api() == {
        "type": "noul",
        "instructions": "Does state.text ask for a computation?",
        "criteria": {"true": "wants action", "false": "explanation only"},
    }


def test_choice_option_count_bounds() -> None:
    with pytest.raises(ValueError, match="2 and 255"):
        Choice(instructions="pick", options={"only": "one"})
    Choice(
        instructions="pick one of state.request",
        options={"a": "A", "b": "B"},
    )
    Choice(
        instructions="pick one of state.request",
        options={f"opt{i:03d}": f"d{i}" for i in range(255)},
    )
    with pytest.raises(ValueError, match="2 and 255"):
        Choice(
            instructions="pick one of state.request",
            options={f"opt{i:03d}": f"d{i}" for i in range(256)},
        )


def test_choice_rejects_empty_option_name() -> None:
    with pytest.raises(ValueError, match="option names"):
        Choice(instructions="pick", options={"": "empty", "b": "B"})
    with pytest.raises(ValueError, match="option names"):
        Choice(instructions="pick", options={"  ": "ws", "b": "B"})


def test_choice_to_api_preserves_string_and_object_descriptions() -> None:
    question = Choice(
        instructions="Which Skill fits state.request?",
        options={
            "single-cell-rna-analysis": "End-to-end scRNA-seq.",
            "none_of_these": {"label": "none", "note": "prose is enough"},
        },
    )
    assert question.to_api() == {
        "type": "choice",
        "instructions": "Which Skill fits state.request?",
        "criteria": {
            "single-cell-rna-analysis": "End-to-end scRNA-seq.",
            "none_of_these": {"label": "none", "note": "prose is enough"},
        },
    }


def test_score_level_count_bounds() -> None:
    with pytest.raises(ValueError, match="2 and 10"):
        Score(instructions="rate", levels=("only",))
    Score(instructions="rate state.text", levels=("low", "high"))
    Score(
        instructions="rate state.text",
        levels=tuple(f"L{i}" for i in range(10)),
    )
    with pytest.raises(ValueError, match="2 and 10"):
        Score(
            instructions="rate state.text",
            levels=tuple(f"L{i}" for i in range(11)),
        )


def test_score_rejects_empty_instructions_and_levels() -> None:
    with pytest.raises(ValueError, match="instructions"):
        Score(instructions="", levels=("a", "b"))
    with pytest.raises(ValueError, match="levels"):
        Score(instructions="rate", levels=("ok", "  "))


def test_score_to_api_uses_criteria_list() -> None:
    question = Score(
        instructions="How well does the Skill fit state.request?",
        levels=("poor", "ok", "excellent"),
    )
    assert question.to_api() == {
        "type": "score",
        "instructions": "How well does the Skill fit state.request?",
        "criteria": ["poor", "ok", "excellent"],
    }


def test_judgment_result_to_dict_is_json_serializable() -> None:
    result = JudgmentResult(
        status="ok",
        purpose="skill_suggest",
        template_id="skills.suggest",
        template_version="1",
        policy_version="1",
        provider="typesafe",
        model="jev-1.13.0",
        calibrated=True,
        state_sha256="abc",
        answers={
            "best": Answer(
                kind="choice",
                value="none_of_these",
                probabilities={"a": 0.2, "none_of_these": 0.8},
                confidence=0.7,
            ),
            "gate": Answer(kind="noul", value=0.4),
        },
        usage={"input_tokens": 12, "output_tokens": 0},
        latency_ms=40,
        cache_hit=False,
        error_code=None,
    )
    payload = result.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert json.loads(encoded)["status"] == "ok"
    assert payload["answers"]["gate"]["probabilities"] is None
    assert payload["answers"]["best"]["value"] == "none_of_these"


def test_backend_reply_request_id_defaults_to_none() -> None:
    reply = BackendReply(
        answers={"q": {"noul": 0.5}},
        usage={"input_tokens": 1, "output_tokens": 0},
        model="jev-1.13.0",
    )
    assert reply.request_id is None
    assert reply.answers["q"]["noul"] == 0.5


def test_null_backend_always_raises_disabled() -> None:
    backend = NullBackend()
    with pytest.raises(BackendError) as excinfo:
        backend.evaluate(
            state={"text": "hello"},
            questions={"q": Noul(instructions="Is this a greeting?")},
            model="jev-1.13.0",
            timeout=3.0,
        )
    assert excinfo.value.code == "disabled"


@pytest.mark.parametrize("code", sorted(BACKEND_ERROR_CODES))
def test_backend_error_accepts_declared_codes(code: str) -> None:
    err = BackendError(code, "detail")
    assert err.code == code
    assert "detail" in str(err)


def test_backend_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="invalid backend error code"):
        BackendError("nope", "x")
