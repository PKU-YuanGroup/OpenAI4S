"""D6: explicit null == absent == default, identically through both doors.

The SDK door (``host.delegate``) has always dropped top-level ``None`` values
at the wire codec; the native door (``delegate_task``) schema-validated the
provider's literal ``null`` and refused it, and a ``None`` nested inside a
fan-out request item could clobber an inherited value. Both doors now agree:
a null key is the same as an omitted key, everywhere.
"""

from __future__ import annotations

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import DelegationRunner, _normalize_item
from openai4s.config import get_config
from openai4s.sdk.host import decode_args, encode_args
from openai4s.tools.delegation import DelegateTaskTool


class _RecordingRuntime:
    """Stands in for the ControlToolContext: records the dispatched spec."""

    def __init__(self):
        self.calls = []

    def invoke(self, method, spec=None):
        self.calls.append((method, spec))
        return {"ok": True}


def _sdk_door_spec(spec: dict) -> dict:
    """What the kernel-wire door hands the tool: encode → decode → execute."""
    decoded = decode_args(encode_args([dict(spec)]))[0]
    runtime = _RecordingRuntime()
    DelegateTaskTool().execute(runtime, decoded)
    return runtime.calls[0][1]


def _native_door_spec(spec: dict) -> dict:
    """What the engine door hands the tool after schema validation."""
    tool = DelegateTaskTool()
    error = tool.validation_error(dict(spec))
    assert error is None, f"native door refused the spec: {error}"
    runtime = _RecordingRuntime()
    tool.execute(runtime, dict(spec))
    return runtime.calls[0][1]


@pytest.mark.parametrize(
    "spec",
    [
        {"request": "work"},  # omitted keys
        {"request": "work", "steps": None, "name": None, "retries": None},  # nulls
        {"request": "work", "steps": 3, "retries": 1},  # explicit values
    ],
    ids=["omitted", "explicit-null", "explicit-value"],
)
def test_both_doors_produce_identical_effective_config(spec):
    assert _sdk_door_spec(spec) == _native_door_spec(spec)


def test_explicit_null_equals_absent_on_both_doors():
    omitted = {"request": "work"}
    nulled = {
        "request": "work",
        "name": None,
        "context_summary": None,
        "output_schema": None,
        "steps": None,
        "max_steps": None,
        "max_turns": None,
        "permissions": None,
        "capabilities": None,
        "unrestricted": None,
        "require_artifacts": None,
        "retries": None,
    }
    expected = {"request": "work", "wait": True}
    assert _sdk_door_spec(omitted) == expected
    assert _sdk_door_spec(nulled) == expected
    assert _native_door_spec(omitted) == expected
    assert _native_door_spec(nulled) == expected


def test_native_schema_accepts_new_fields_and_still_forbids_unknown_keys():
    tool = DelegateTaskTool()
    assert (
        tool.validation_error(
            {"request": "work", "require_artifacts": ["out.csv", "fig_*"], "retries": 2}
        )
        is None
    )
    # Unknown keys stay refused; a null unknown key is the same as absent.
    error = tool.validation_error({"request": "work", "bogus": 1})
    assert error is not None and "bogus" in error
    assert tool.validation_error({"request": "work", "bogus": None}) is None


def test_nested_item_null_no_longer_clobbers_inherited_keys():
    parent = {
        "request": ["ignored"],
        "output_schema": {"type": "object", "required": ["x"]},
        "steps": 4,
        "permissions": {"bash": "deny"},
    }
    child = _normalize_item(
        {"request": "sub-task", "output_schema": None, "steps": None}, parent
    )
    assert child["output_schema"] == {"type": "object", "required": ["x"]}
    assert child["steps"] == 4
    assert child["permissions"] == {"bash": "deny"}
    # A real nested value still overrides its inherited counterpart.
    override = _normalize_item({"request": "sub-task", "steps": 2}, parent)
    assert override["steps"] == 2


def test_a_nested_null_turn_budget_runs_with_the_default(monkeypatch):
    """The end-to-end consequence: a present-but-None steps in a fan-out item
    used to raise DelegationError instead of falling back to the default."""
    observed = []

    def fake_run(self, task):
        observed.append(self.max_turns)
        return {
            "stop_reason": "submitted",
            "submitted_output": {
                "output": {"ok": True},
                "completion_bullets": ["Completed the sub-task"],
            },
            "final_message": None,
            "turns": 1,
        }

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)
    runner = DelegationRunner(get_config(), child_max_turns=5)
    try:
        result = runner({"request": [{"request": "sub-task", "steps": None}]})
    finally:
        runner.close()

    assert result[0]["task_status"] == "completed"
    assert observed == [5]
