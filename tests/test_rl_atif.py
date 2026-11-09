from __future__ import annotations

import json

from openai4s.rl import (
    ATIFExporter,
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    TrajectoryRecorder,
)


def test_atif_export_preserves_decision_telemetry_and_source_evidence() -> None:
    recorder = TrajectoryRecorder(Episode("ep-atif", "task", "session", 1))
    recorder.add_observation(
        Observation(
            "obs-0",
            "ep-atif",
            "decision-0",
            [{"role": "user", "content": "measure"}],
            2,
        )
    )
    recorder.add_decision(
        PolicyDecision(
            "decision-0",
            "ep-atif",
            0,
            "obs-0",
            "prompt-hash",
            "vllm",
            "policy-1",
            {
                "type": "native_tool_batch",
                "calls": [
                    {
                        "id": "call-1",
                        "name": "measure",
                        "arguments": {"sample": "x"},
                        "raw_arguments": '{"sample":"x"}',
                    }
                ],
            },
            3,
            token_ids=[11, 12],
            token_logprobs=[-0.1, -0.2],
            loss_mask=[1, 1],
            action_logprob=-0.3,
            assistant_message={"role": "assistant", "content": ""},
        )
    )
    recorder.add_event(
        ExecutionEvent(
            "event-0",
            "ep-atif",
            "execution.completed",
            4,
            payload={"ok": True},
            decision_id="decision-0",
        )
    )
    recorder.add_cost(
        CostRecord(
            "cost-0",
            "ep-atif",
            5,
            prompt_tokens=10,
            completion_tokens=2,
            cost_usd=0.01,
            decision_id="decision-0",
        )
    )

    trajectory = recorder.snapshot()
    exported = ATIFExporter(trajectory, agent_version="test").to_dict()
    assert exported["schema_version"] == "ATIF-v1.8"
    assert [step["step_id"] for step in exported["steps"]] == [1, 2]
    agent_step = exported["steps"][1]
    assert agent_step["tool_calls"][0]["tool_call_id"] == "call-1"
    assert agent_step["observation"]["results"][0]["content"] == '{"ok":true}'
    assert agent_step["metrics"]["completion_token_ids"] == [11, 12]
    assert agent_step["metrics"]["extra"]["loss_mask"] == [1, 1]
    assert agent_step["metrics"]["prompt_tokens"] == 10
    assert agent_step["metrics"]["cost_usd"] == 0.01
    assert exported["extra"]["openai4s"]["costs"][0]["cost_id"] == "cost-0"
    assert ATIFExporter(trajectory, agent_version="test").export_json() == json.dumps(
        exported, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert trajectory.canonical_json() == recorder.export_json()
