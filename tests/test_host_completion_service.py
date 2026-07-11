"""Direct contracts for the sole Code-as-Action completion signal."""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.host.completion import (
    CompletionService,
    validate_completion_bullets,
    validate_output_schema,
)
from openai4s.host_dispatch import HostDispatcher


def test_success_commits_structured_output_and_clear_resets_state():
    service = CompletionService()
    output = {"artifacts": ["model.pt"], "metrics": {"accuracy": 0.93}}
    bullets = ["Generated the model", "Measured its accuracy"]

    assert service.last_output is None
    assert service.submit(
        {"output": output, "completion_bullets": bullets}
    ) == {"status": "ok"}
    assert service.last_output == {
        "output": output,
        "completion_bullets": bullets,
    }
    assert service.last_output["output"] is output
    assert service.last_output["completion_bullets"] is bullets

    service.clear()
    assert service.last_output is None


@pytest.mark.parametrize(
    "bullets, expected",
    [
        (None, "completion_bullets must be a list of 1-4 items"),
        ([], "completion_bullets must be a list of 1-4 items"),
        (("Computed it",), "completion_bullets must be a list of 1-4 items"),
        (["Did it"] * 5, "completion_bullets must be a list of 1-4 items"),
        ([""], "each completion bullet must be a non-empty string"),
        ([1], "each completion bullet must be a non-empty string"),
        (
            ["Run the analysis"],
            "completion bullet 'Run the analysis' must start with a past-tense verb "
            "(e.g. 'Computed...', 'Saved...')",
        ),
    ],
)
def test_invalid_bullets_soft_fail_without_replacing_prior_completion(
    bullets, expected
):
    service = CompletionService()
    previous = {
        "output": {"answer": 42},
        "completion_bullets": ["Computed the answer"],
    }
    service.last_output = previous

    result = service.submit({"output": {"answer": 0}, "completion_bullets": bullets})

    assert result == {"error": expected}
    assert service.last_output is previous


def test_irregular_and_suffix_past_tense_rules_remain_case_sensitive_to_words():
    assert validate_completion_bullets(["Made the dataset"]) is None
    assert validate_completion_bullets(["SAVED the model"]) is None
    assert validate_completion_bullets(["Computed the score"]) is None
    assert "past-tense verb" in validate_completion_bullets(
        ["Computed, then saved the score"]
    )


@pytest.mark.parametrize(
    "output, schema, expected",
    [
        ({"x": 1}, {"type": "object", "required": ["x"]}, None),
        ({}, {"type": "object", "required": ["x"]}, "missing required field 'x'"),
        ([], {"type": "object"}, "output must be an object"),
        ([], {"type": "array"}, None),
        ({}, {"type": "array"}, "output must be an array"),
        ("ok", {"type": "string"}, None),
        (1, {"type": "string"}, "output must be a string"),
        (1.5, {"type": "number"}, None),
        (True, {"type": "number"}, None),
        ("1", {"type": "number"}, "output must be a number"),
        (None, {"type": "unknown"}, None),
        (None, "not-a-schema", None),
    ],
)
def test_minimal_output_schema_contract(output, schema, expected):
    result = validate_output_schema(output, schema)
    if expected is None:
        assert result is None
    else:
        assert expected in result


def test_schema_failure_is_soft_and_success_is_the_only_commit():
    service = CompletionService()
    schema = {"type": "object", "required": ["artifact"]}

    failed = service.submit(
        {
            "output": {"metric": 0.93},
            "completion_bullets": ["Computed the metric"],
            "output_schema": schema,
        }
    )
    assert failed == {"error": "output missing required field 'artifact'"}
    assert service.last_output is None

    succeeded = service.submit(
        {
            "output": {"artifact": "prediction.csv"},
            "completion_bullets": ["Saved the prediction"],
            "output_schema": schema,
        }
    )
    assert succeeded == {"status": "ok"}
    assert service.last_output["output"] == {"artifact": "prediction.csv"}

    with pytest.raises(AttributeError):
        service.submit("not-a-spec")
    assert service.last_output["output"] == {"artifact": "prediction.csv"}


def test_dispatcher_last_output_remains_bidirectionally_compatible(tmp_path):
    dispatcher = HostDispatcher(Config(data_dir=tmp_path))
    assert isinstance(dispatcher._completion_service, CompletionService)
    assert dispatcher.last_output is None

    dispatcher.last_output = {"stale": True}
    assert dispatcher._completion_service.last_output == {"stale": True}
    dispatcher.last_output = None
    assert dispatcher._completion_service.last_output is None

    result = dispatcher._m_submit_output(
        {
            "output": {"answer": 42},
            "completion_bullets": ["Computed the answer"],
        }
    )
    assert result == {"status": "ok"}
    assert dispatcher.last_output == {
        "output": {"answer": 42},
        "completion_bullets": ["Computed the answer"],
    }
