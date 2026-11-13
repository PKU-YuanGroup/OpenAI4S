from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from openai4s.agent.models import ModelReply
from openai4s.rl.client import (
    AsyncRolloutClient,
    RemoteRolloutWorker,
    RolloutClient,
    RolloutClientClosed,
    RolloutProtocolError,
    RolloutRemoteError,
    RolloutTimeoutError,
    RolloutTransportError,
)
from openai4s.rl.models import Episode, Trajectory

pytestmark = pytest.mark.stubbed_backend

SERVER = textwrap.dedent("""
    import json
    import sys
    import time

    mode = sys.argv[1]
    log_path = sys.argv[2] if len(sys.argv) > 2 else None

    EPISODE = {
        "episode_id": "remote-episode",
        "task": "remote task",
        "session_id": "remote-session",
        "created_at": 1,
        "policy_name": "trainer",
        "policy_version": "v1",
        "parent_episode_id": None,
        "status": "completed",
        "metadata": {},
    }
    TRAJECTORY = {
        "schema_version": "openai4s.science-trajectory.v1",
        "episode": EPISODE,
        "observations": [],
        "decisions": [],
        "segments": [],
        "execution_events": [],
        "rewards": [],
        "costs": [],
    }

    def log(raw):
        if log_path:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(raw + "\\n")

    def send(payload):
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
        sys.stdout.flush()

    def ok(request_id, result):
        send({"jsonrpc": "2.0", "id": request_id, "ok": True, "result": result})

    def error(request_id, code):
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "ok": False,
                "error": {"code": code, "message": "fixture failure"},
            }
        )

    step_count = 0
    for raw in sys.stdin:
        log(raw.rstrip("\\n"))
        if mode == "eof":
            break
        if mode == "bad-json":
            sys.stdout.write("not-json\\n")
            sys.stdout.flush()
            continue
        request = json.loads(raw)
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if mode == "mismatched-id":
            request_id = request_id + 100
        if mode == "remote-error":
            error(request_id, "state_error")
            continue
        if mode == "timeout":
            time.sleep(5)
            continue
        if mode == "slow-step" and method == "step":
            time.sleep(0.3)

        if method == "reset":
            ok(
                request_id,
                {
                    "episode": EPISODE,
                    "turn": 0,
                    "max_turns": 3,
                    "observation": [{"role": "user", "content": "start"}],
                    "tools": [{"name": "first"}],
                    "terminal": False,
                    "status": "running",
                },
            )
        elif method == "observe":
            ok(
                request_id,
                {
                    "episode": EPISODE,
                    "turn": 0,
                    "max_turns": 3,
                    "observation": [{"role": "user", "content": "start"}],
                    "tools": [{"name": "first"}],
                    "terminal": False,
                    "status": "running",
                },
            )
        elif method == "step":
            step_count += 1
            if step_count == 1:
                ok(
                    request_id,
                    {
                        "episode": EPISODE,
                        "turn": 1,
                        "observation": params.get("observation", []),
                        "action": {"type": "native_tool_batch", "calls": []},
                        "outcome": None,
                        "completion": None,
                        "stop_reason": None,
                        "terminal": False,
                        "state": {"turn": 1, "max_turns": 3},
                        "status": "running",
                        "next_observation": [
                            {"role": "user", "content": "after-step"}
                        ],
                        "next_tools": [{"name": "second"}],
                    },
                )
            else:
                ok(
                    request_id,
                    {
                        "episode": EPISODE,
                        "turn": 2,
                        "observation": [],
                        "action": {"type": "finalize", "call": {}},
                        "outcome": None,
                        "completion": {"done": True},
                        "stop_reason": "done",
                        "terminal": True,
                        "state": {"turn": 2, "max_turns": 3},
                        "status": "terminal",
                    },
                )
        elif method == "export":
            ok(
                request_id,
                {
                    "episode_id": "remote-episode",
                    "format": "json",
                    "schema_version": "openai4s.science-trajectory.v1",
                    "sha256": "fixture",
                    "content": json.dumps(TRAJECTORY),
                },
            )
        elif method == "release":
            ok(request_id, {"status": "terminal", "episode": EPISODE})
        elif method in {"rewards", "costs"}:
            ok(request_id, [])
        elif method in {"recover", "result", "step_result"}:
            ok(
                request_id,
                {
                    "request_id": params.get("request_id"),
                    "step_id": params.get("step_id"),
                    "state": "completed",
                },
            )
        else:
            error(request_id, "method_not_found")
    """)


def _server(tmp_path: Path, mode: str = "happy") -> tuple[Path, Path]:
    script = tmp_path / "worker.py"
    script.write_text(SERVER, encoding="utf-8")
    log = tmp_path / "requests.jsonl"
    return script, log


def _argv(script: Path, mode: str, log: Path) -> list[str]:
    return [sys.executable, "-I", str(script), mode, str(log)]


def _episode() -> Episode:
    return Episode(
        episode_id="episode-1",
        task="remote task",
        session_id="session-1",
        created_at=1,
        policy_name="trainer",
        policy_version="v1",
    )


def _request() -> dict:
    return {
        "episode": _episode(),
        "messages": [{"role": "user", "content": "go"}],
        "max_turns": 3,
        "request_id": "req-1",
    }


def _logged(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_client_round_trips_correlated_jsonl_to_real_subprocess(tmp_path: Path) -> None:
    script, log = _server(tmp_path)
    with RolloutClient(_argv(script, "happy", log), timeout=5) as client:
        reset = client.reset(_request())
        assert reset["turn"] == 0
        assert client.observe()["observation"] == [{"role": "user", "content": "start"}]
        first = client.step(
            {"content": "first"},
            step_id="step-1",
            expected_turn=0,
            observation=reset["observation"],
        )
        assert first["status"] == "running"
        second = client.step(
            ModelReply(content="second"), step_id="step-2", expected_turn=1
        )
        assert second["terminal"] is True
        exported = client.export()
        assert exported["format"] == "json"
        assert client.release()["status"] == "terminal"

    requests = _logged(log)
    methods = [item["method"] for item in requests]
    assert methods == ["reset", "observe", "step", "step", "export", "release"]
    assert requests[2]["params"]["step_id"] == "step-1"
    assert requests[2]["params"]["expected_turn"] == 0
    assert requests[3]["params"]["expected_turn"] == 1


def test_client_requires_argv_or_streams_and_never_a_shell_string() -> None:
    with pytest.raises(TypeError):
        RolloutClient("python worker.py")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RolloutClient()
    with pytest.raises(TypeError):
        RolloutClient([sys.executable, 3])  # type: ignore[list-item]


def test_client_validates_step_identity_before_sending() -> None:
    with RolloutClient(streams=(io.StringIO(""), io.StringIO())) as client:
        with pytest.raises(ValueError):
            client.step({"content": "x"}, step_id="", expected_turn=0)
        with pytest.raises(TypeError):
            client.step({"content": "x"}, step_id="s", expected_turn=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            client.step({"content": "x"}, step_id="s", expected_turn=-1)
        with pytest.raises(ValueError):
            client.reset({**_request(), "unknown": 1})


def test_client_eof_is_unknown_and_never_replays(tmp_path: Path) -> None:
    script, log = _server(tmp_path)
    client = RolloutClient(_argv(script, "eof", log), timeout=5)
    try:
        with pytest.raises(RolloutTransportError) as info:
            client.reset(_request())
        assert info.value.state_unknown is True
        assert client.closed is True
        with pytest.raises(RolloutClientClosed):
            client.reset(_request())
    finally:
        client.close()
    assert len(_logged(log)) == 1


@pytest.mark.parametrize("mode", ["bad-json", "mismatched-id"])
def test_client_rejects_bad_frames(tmp_path: Path, mode: str) -> None:
    script, log = _server(tmp_path)
    with RolloutClient(_argv(script, mode, log), timeout=5) as client:
        with pytest.raises(RolloutProtocolError) as info:
            client.reset(_request())
        assert info.value.state_unknown is True


def test_client_timeout_is_unknown_and_closes_the_transport(tmp_path: Path) -> None:
    script, log = _server(tmp_path)
    client = RolloutClient(_argv(script, "timeout", log), timeout=0.25)
    started = time.monotonic()
    try:
        with pytest.raises(RolloutTimeoutError) as info:
            client.reset(_request())
        assert info.value.state_unknown is True
        assert time.monotonic() - started < 3
        assert client.closed is True
        with pytest.raises(RolloutClientClosed):
            client.observe()
    finally:
        client.close()
    assert len(_logged(log)) == 1


def test_client_structured_error_is_not_a_transport_loss(tmp_path: Path) -> None:
    script, log = _server(tmp_path)
    with RolloutClient(_argv(script, "remote-error", log), timeout=5) as client:
        with pytest.raises(RolloutRemoteError) as info:
            client.costs()
        assert info.value.code == "state_error"
        assert info.value.state_unknown is False


def test_remote_worker_samples_then_forwards_stable_step_ids(
    tmp_path: Path,
) -> None:
    script, log = _server(tmp_path)

    class Sampler:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def sample(self, observation, *, tools=()):
            self.calls.append((observation, tools))
            if len(self.calls) == 1:
                return {"content": "first"}
            return ModelReply(content="second")

    sampler = Sampler()

    async def scenario() -> Trajectory:
        worker = RemoteRolloutWorker(
            RolloutClient(_argv(script, "happy", log), timeout=5)
        )
        try:
            return await worker.run_async(_request(), sampler)
        finally:
            await worker.aclose()

    trajectory = asyncio.run(scenario())
    assert isinstance(trajectory, Trajectory)
    assert trajectory.episode.episode_id == "remote-episode"
    assert [call[0] for call in sampler.calls] == [
        ({"role": "user", "content": "start"},),
        ({"role": "user", "content": "after-step"},),
    ]
    assert sampler.calls[0][1] == ({"name": "first"},)
    assert sampler.calls[1][1] == ({"name": "second"},)

    requests = _logged(log)
    steps = [item for item in requests if item["method"] == "step"]
    assert [item["params"]["step_id"] for item in steps] == [
        "req-1:turn-0",
        "req-1:turn-1",
    ]
    assert [item["params"]["expected_turn"] for item in steps] == [0, 1]
    assert steps[0]["params"]["observation"] == [{"role": "user", "content": "start"}]


def test_remote_worker_offloads_a_synchronous_client(tmp_path: Path) -> None:
    script, log = _server(tmp_path)

    class Sampler:
        def sample(self, observation, *, tools=()):
            return {"content": "done"}

    async def scenario() -> int:
        worker = RemoteRolloutWorker(
            RolloutClient(_argv(script, "slow-step", log), timeout=5)
        )
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await worker.run_async(_request(), Sampler())
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
            await worker.aclose()
        return ticks

    assert asyncio.run(scenario()) >= 5


def test_async_client_uses_existing_asyncio_streams(tmp_path: Path) -> None:
    script, log = _server(tmp_path)

    async def scenario() -> None:
        process = await asyncio.create_subprocess_exec(
            *_argv(script, "happy", log),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        client = AsyncRolloutClient(
            process.stdout, process.stdin, timeout=5, close_streams=True
        )
        try:
            reset = await client.reset(_request())
            assert reset["turn"] == 0
            with pytest.raises(ValueError):
                await client.step({"content": "x"}, step_id="", expected_turn=0)
            assert await client.rewards() == []
            assert await client.recover("req-1") == {
                "request_id": "req-1",
                "step_id": None,
                "state": "completed",
            }
        finally:
            await client.aclose()
            if process.returncode is None:
                process.terminate()
                await process.wait()

    asyncio.run(scenario())


def test_real_rpc_worker_persists_results_across_processes(tmp_path):
    script = tmp_path / "worker.py"
    root = Path(__file__).resolve().parents[1]
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(root)!r})
        from pathlib import Path
        from openai4s.agent.engine import AgentEngine
        from openai4s.agent.models import ExecutionOutcome
        from openai4s.config import Config
        from openai4s.store import get_store
        from openai4s.rl import RolloutEnvironment, EnvironmentAdapter, serve_rollout_rpc
        store = get_store(Config(data_dir=Path(sys.argv[1])).db_path)
        class Executor:
            def execute(self, action, reply, state):
                return ExecutionOutcome(observation='executed', stop_reason='done')
        environment = RolloutEnvironment(lambda reset: AgentEngine(None, Executor()), repository=store.rl)
        serve_rollout_rpc(EnvironmentAdapter(environment), sys.stdin, sys.stdout)
        store.close()
    """))
    argv = [sys.executable, "-I", str(script), str(tmp_path / "data")]
    request = {
        "episode": {
            "episode_id": "real-remote",
            "task": "test",
            "session_id": "session",
            "created_at": 1,
        },
        "messages": [{"role": "user", "content": "go"}],
        "request_id": "real-request",
        "max_turns": 1,
    }
    with RolloutClient(argv, timeout=15) as client:
        client.reset(request)
        response = client.step(
            {"content": "```python\npass\n```"}, step_id="turn-0", expected_turn=0
        )
        assert response["terminal"] is True
        original = client.export()
    with RolloutClient(argv, timeout=15) as client:
        assert client.reset(request)["reused"] is True
        assert client.result("real-request") == original
        assert client.step_result("real-request", "turn-0")["response"] == response
        assert client.export() == original
