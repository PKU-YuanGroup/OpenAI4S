from __future__ import annotations

import io
import json
import subprocess
import sys

from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome
from openai4s.rl import (
    EnvironmentAdapter,
    RolloutEnvironment,
    RolloutRPC,
    serve_rollout_rpc,
)


class _NeverCalledModel:
    def complete(self, _messages, _on_delta):
        raise AssertionError("the trainer must provide every policy reply")


class _Executor:
    def execute(self, _action, _reply, _state):
        return ExecutionOutcome(observation={"ok": True}, stop_reason="done")


def _request(episode_id: str = "rpc-episode", *, max_turns: int = 1) -> dict:
    return {
        "episode": {
            "episode_id": episode_id,
            "task": "rpc task",
            "session_id": "rpc-session",
            "created_at": 1,
            "policy_name": "external",
            "policy_version": "weights-1",
        },
        "messages": [{"role": "user", "content": "run"}],
        "config": {"temperature": 0.0},
        "budget": {"tokens": 12},
        "max_turns": max_turns,
    }


def _rpc() -> RolloutRPC:
    environment = RolloutEnvironment(
        lambda _reset: AgentEngine(_NeverCalledModel(), _Executor(), max_turns=1)
    )
    return RolloutRPC(EnvironmentAdapter(environment))


def test_rpc_dispatches_episode_and_makes_release_idempotent() -> None:
    rpc = _rpc()
    reset = rpc.dispatch({"id": 1, "method": "reset", "params": _request()})
    assert reset["ok"] is True
    assert reset["result"]["episode"]["episode_id"] == "rpc-episode"

    stepped = rpc.dispatch(
        {
            "id": 2,
            "method": "step",
            "params": {"reply": {"content": "```python\npass\n```"}},
        }
    )
    assert stepped["ok"] is True
    assert stepped["result"]["terminal"] is True

    exported = rpc.dispatch({"id": 3, "method": "export", "params": {}})
    assert exported["ok"] is True
    released = rpc.dispatch({"id": 4, "method": "release"})
    assert released["result"]["status"] == "terminal"
    repeated = rpc.dispatch({"id": 5, "method": "release"})
    assert repeated["result"] == released["result"]


def test_rpc_rejects_bad_requests_without_running_execution() -> None:
    rpc = _rpc()
    assert rpc.dispatch({"method": "observe"})["error"]["code"] == "invalid_request"
    assert rpc.dispatch({"id": 1, "method": "unknown"})["error"]["code"] == (
        "method_not_found"
    )
    assert (
        rpc.dispatch({"id": 2, "method": "reset", "params": []})["error"]["code"]
        == "invalid_params"
    )
    assert rpc.dispatch({"id": 3, "method": "costs"})["error"]["code"] == "state_error"
    assert (
        rpc.dispatch({"id": 4, "method": "step", "params": {"reply": None}})["error"][
            "code"
        ]
        == "invalid_params"
    )
    assert (
        rpc.dispatch({"id": 5, "method": "finish", "params": {"status": "running"}})[
            "error"
        ]["code"]
        == "invalid_params"
    )


def test_rpc_json_lines_reports_parse_and_state_errors_and_continues() -> None:
    request = {"id": 1, "method": "reset", "params": _request("stream-episode")}
    lines = "not-json\n" + json.dumps(request) + "\n"
    output = io.StringIO()
    count = serve_rollout_rpc(_rpc().adapter, io.StringIO(lines), output)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert count == 2
    assert responses[0]["error"]["code"] == "parse_error"
    assert responses[1]["result"]["episode"]["episode_id"] == "stream-episode"


def test_rpc_drains_oversized_line_and_preserves_following_request() -> None:
    request = {"id": 2, "method": "costs", "params": {}}
    input_text = json.dumps({"id": 1, "method": "x", "padding": "x" * 100}) + "\n"
    input_text += json.dumps(request) + "\n"
    output = io.StringIO()
    count = serve_rollout_rpc(
        _rpc().adapter, io.StringIO(input_text), output, max_line_bytes=80
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert count == 2
    assert responses[0]["error"]["code"] == "request_too_large"
    assert responses[1]["error"]["code"] == "state_error"


def test_rpc_serves_a_real_subprocess_stdin_stdout_pipe() -> None:
    script = r"""
import sys
from openai4s.agent.engine import AgentEngine
from openai4s.agent.models import ExecutionOutcome
from openai4s.rl import EnvironmentAdapter, RolloutEnvironment, serve_rollout_rpc

class Model:
    def complete(self, _messages, _on_delta):
        raise AssertionError("policy sampling must stay outside the child")

class Executor:
    def execute(self, _action, _reply, _state):
        return ExecutionOutcome(observation={"ok": True}, stop_reason="done")

environment = RolloutEnvironment(
    lambda _reset: AgentEngine(Model(), Executor(), max_turns=1)
)
serve_rollout_rpc(EnvironmentAdapter(environment), sys.stdin, sys.stdout)
"""
    requests = (
        "\n".join(
            [
                json.dumps({"id": 1, "method": "reset", "params": _request("pipe")}),
                json.dumps(
                    {
                        "id": 2,
                        "method": "step",
                        "params": {"reply": {"content": "```python\npass\n```"}},
                    }
                ),
                json.dumps({"id": 3, "method": "release"}),
            ]
        )
        + "\n"
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        input=requests,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[1]["result"]["terminal"] is True
    assert responses[2]["result"]["status"] == "terminal"
