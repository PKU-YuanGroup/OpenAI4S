from __future__ import annotations

from openai4s.agent.actions import CodeCell
from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.tools.native import ToolSpec


class _Model:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def complete(self, messages, on_delta):
        self.calls.append([dict(message) for message in messages])
        return ModelReply(content="```python\nprint(42)\n```")


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, action, reply, state):
        self.calls += 1
        assert action == CodeCell("python", "print(42)\n")
        return ExecutionOutcome(observation="42")


def test_external_sampler_uses_prepared_observation_without_model_call() -> None:
    model = _Model()
    executor = _Executor()
    engine = AgentEngine(model, executor, max_turns=2)
    state = RunState([{"role": "user", "content": "calculate"}], max_turns=2)

    observation = engine.prepare_decision(state)
    step = engine.step(
        state,
        ModelReply(
            content="```python\nprint(42)\n```",
            extra={"policy_version": "vllm-test", "token_ids": [4, 2]},
        ),
        observation=observation,
    )

    assert step.observation == observation
    assert step.reply is not None
    assert step.reply.extra["token_ids"] == [4, 2]
    assert step.action == CodeCell("python", "print(42)\n")
    assert step.outcome is not None
    assert model.calls == []
    assert executor.calls == 1


def test_step_stops_at_max_turns_before_sampling_or_execution() -> None:
    model = _Model()
    executor = _Executor()
    engine = AgentEngine(model, executor, max_turns=1)
    state = RunState([], max_turns=1, turn=1)

    step = engine.step(state, ModelReply(content="ignored"))

    assert step.stop_reason == "max_turns"
    assert step.terminal
    assert model.calls == []
    assert executor.calls == 0


def test_prepare_tools_returns_detached_provider_neutral_schemas() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}

    class _ExecutorWithTools(_Executor):
        def policy_tools(self, messages):
            assert messages[0]["content"] == "calculate"
            return (ToolSpec("inspect", "Inspect data", schema),)

    engine = AgentEngine(_Model(), _ExecutorWithTools(), max_turns=1)
    state = RunState([{"role": "user", "content": "calculate"}], max_turns=1)
    tools = engine.prepare_tools(state)

    assert tools == (
        {
            "name": "inspect",
            "description": "Inspect data",
            "input_schema": schema,
            "strict": False,
        },
    )
    tools[0]["input_schema"]["properties"]["value"]["type"] = "number"  # type: ignore[index]
    assert schema["properties"]["value"]["type"] == "string"
