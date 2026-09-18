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
    assert service.submit({"output": output, "completion_bullets": bullets}) == {
        "status": "ok"
    }
    assert service.last_output == {
        "output": output,
        "completion_bullets": bullets,
    }
    assert service.last_output["output"] is output
    assert service.last_output["completion_bullets"] is bullets

    service.clear()
    assert service.last_output is None


def test_declared_task_status_is_validated_and_committed():
    """D5: an optional honest status rides the completion; the undeclared
    two-key CompletionRecord shape is untouched (asserted by the exact
    equality in the success test above)."""
    service = CompletionService()
    spec = {
        "output": {"summary": "partially done"},
        "completion_bullets": ["Completed the first half"],
        "task_status": "partial",
    }
    assert service.submit(spec) == {"status": "ok"}
    assert service.last_output == {
        "output": {"summary": "partially done"},
        "completion_bullets": ["Completed the first half"],
        "task_status": "partial",
    }

    previous = service.last_output
    for bad in ("done", "almost", 1, True, ""):
        result = service.submit(
            {
                "output": {},
                "completion_bullets": ["Computed it"],
                "task_status": bad,
            }
        )
        assert set(result) == {"error"}, f"accepted invalid task_status {bad!r}"
        assert "task_status" in result["error"]
        assert service.last_output is previous


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


def test_completion_bullets_accept_cjk_completed_action_phrases():
    assert validate_completion_bullets(["撰写了完整报告"]) is None
    assert validate_completion_bullets(["已完成真实数据分析", "生成了结果表"]) is None


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


# --- irregular past tenses --------------------------------------------------
#
# The English tense guard accepted a first word ending in "ed" or one of a short
# list of irregular forms. "Wrote" was listed; "Overwrote", "Rewrote", "Took",
# "Cut", "Reset" and "Undid" were not, so a correct bullet raised inside
# host.submit_output after the Cell's file write had already happened, and
# finalize_response refused the same wording. Both doors share the validator.

IRREGULAR_PAST_BULLETS = [
    "Overwrote measurements.csv with header x,y,z",
    "Rewrote the loader",
    "Took three replicate measurements",
    "Cut the reads to 100 bp",
    "Reset the random seed",
    "Undid the normalization",
    "Redid the alignment",
    "Rebuilt the index",
    "Did a sensitivity sweep",
    "Gave each sample an identifier",
    "Brought the tables into one frame",
    "Lost no rows during the join",
    "Knew the reference build from the header",
    "Grew the training set",
    "Hid the internal columns",
    "Overrode the default threshold",
    "Upheld the original labels",
    "Mistook nothing: rechecked every ID",
    "Unset the proxy variable",
    "Froze the environment lockfile",
    "Spent the budget on two runs",
]

NOT_PAST_BULLETS = [
    "Will compute the score",
    "Computing the score",
    "Plan to save the model",
    "Compute the score",
    "Output the table",
    "Present the results",
    "Rerun the analysis",
    "Outlet flow was measured",
]


@pytest.mark.parametrize("bullet", IRREGULAR_PAST_BULLETS)
def test_irregular_and_prefixed_past_tense_bullets_are_accepted_by_both_doors(
    bullet,
):
    from openai4s.agent.finalize import validate_finalize_arguments

    assert validate_completion_bullets([bullet]) is None
    assert CompletionService().submit(
        {"output": {"rows": 3}, "completion_bullets": [bullet]}
    ) == {"status": "ok"}
    assert (
        validate_finalize_arguments({"summary": "x", "completion_bullets": [bullet]})
        is None
    )


@pytest.mark.parametrize("bullet", NOT_PAST_BULLETS)
def test_future_progressive_imperative_and_prefix_false_friends_stay_refused(
    bullet,
):
    from openai4s.agent.finalize import validate_finalize_arguments

    assert "past-tense verb" in validate_completion_bullets([bullet])
    assert "past-tense verb" in validate_finalize_arguments(
        {"summary": "x", "completion_bullets": [bullet]}
    )


@pytest.mark.parametrize(
    "bullet", ["Overwrote x.csv", "Rewrote the report", "Rebuilt the index"]
)
def test_newly_accepted_write_verbs_still_need_execution_evidence(bullet):
    """Accepting the wording must not open a zero-execution completion claim."""
    from openai4s.agent.finalize import reconcile_completion_claims

    arguments = {"summary": "x", "completion_bullets": [bullet]}
    refusal = reconcile_completion_claims(arguments, {"cells": 0, "tool_calls": 0})
    assert refusal is not None and bullet in refusal
    assert reconcile_completion_claims(arguments, {"cells": 1, "tool_calls": 0}) is None
