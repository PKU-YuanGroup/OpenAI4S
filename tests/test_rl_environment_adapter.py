from __future__ import annotations

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome, ModelReply
from openai4s.rl import EnvironmentAdapter, RolloutEnvironment


class _NeverCalledModel:
    def complete(self, _messages, _on_delta):
        raise AssertionError("the trainer must provide every policy reply")


class _Executor:
    def __init__(self, stop_reason=None):
        self.stop_reason = stop_reason

    def execute(self, _action, _reply, _state):
        return ExecutionOutcome(
            observation={"ok": True},
            stop_reason=self.stop_reason,
        )


def _request(episode_id="adapter-episode", *, max_turns=2):
    return {
        "episode": {
            "episode_id": episode_id,
            "task": "adapter task",
            "session_id": "adapter-session",
            "created_at": 1,
            "policy_name": "external",
            "policy_version": "weights-1",
        },
        "messages": [{"role": "user", "content": "run"}],
        "config": {"temperature": 0.0},
        "budget": {"tokens": 12},
        "max_turns": max_turns,
    }


def test_environment_adapter_exposes_json_step_and_export_boundary():
    environment = RolloutEnvironment(
        lambda _reset: AgentEngine(
            _NeverCalledModel(), _Executor(stop_reason="done"), max_turns=2
        )
    )
    adapter = EnvironmentAdapter(environment)

    initial = adapter.reset(_request())
    assert initial["status"] == "running"
    assert initial["terminal"] is False
    assert initial["observation"][0]["content"] == "run"
    assert "workspace" not in initial["episode"]["metadata"]["reset"]

    stepped = adapter.step({"content": "```python\nprint(1)\n```"})
    assert stepped["status"] == "terminal"
    assert stepped["terminal"] is True
    assert stepped["action"] == {
        "type": "code_cell",
        "language": "python",
        "code": "print(1)\n",
    }
    assert stepped["outcome"]["observation"] == {"ok": True}

    exported = adapter.export("jsonl")
    assert exported["format"] == "jsonl"
    assert exported["sha256"]
    released = adapter.release()
    assert released["episode"]["episode_id"] == "adapter-episode"
    assert environment.active is False


def test_environment_adapter_terminalizes_the_last_turn_without_a_dummy_reply():
    environment = RolloutEnvironment(
        lambda _reset: AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)
    )
    adapter = EnvironmentAdapter(environment)
    adapter.reset(_request("adapter-max-turns", max_turns=1))

    stepped = adapter.step(ModelReply(content="```python\nprint(1)\n```"))
    assert stepped["status"] == "terminal"
    assert stepped["stop_reason"] == "max_turns"
    assert stepped["episode"]["status"] == "max_turns"


def test_environment_adapter_releases_before_resetting_another_episode():
    environment = RolloutEnvironment(
        lambda _reset: AgentEngine(
            _NeverCalledModel(), _Executor(stop_reason="done"), max_turns=1
        )
    )
    adapter = EnvironmentAdapter(environment)
    adapter.reset(_request("adapter-first", max_turns=1))
    adapter.step({"content": "```python\npass\n```"})
    adapter.release()

    second = adapter.reset(_request("adapter-second", max_turns=1))
    assert second["episode"]["episode_id"] == "adapter-second"
