"""Agent loop + delegation + compaction tests, with the LLM mocked offline."""
from pathlib import Path

import pytest

import openai4s.agent.compaction as comp_mod
import openai4s.agent.delegation as deleg_mod
import openai4s.agent.loop as loop_mod
from openai4s.agent import Agent
from openai4s.agent.delegation import DelegationError, DelegationRunner
from openai4s.config import get_config


class ScriptedLLM:
    """Returns queued replies in order; each call pops one."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def __call__(self, messages, cfg, **kw):
        self.calls.append(messages)
        content = (
            self._replies.pop(0)
            if self._replies
            else ("```python\nhost.submit_output({}, ['Finished the task'])\n```")
        )
        return {
            "content": content,
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }


def _tool_block(json_body: str) -> str:
    """Build a fenced ```tool block the way tests build ```python cells:
    three backticks + 'tool' + newline + the JSON + newline + three backticks."""
    return "```" + "tool\n" + json_body + "\n" + "```"


def test_code_as_action_cycle(monkeypatch):
    scripted = ScriptedLLM(
        [
            "Let me compute it.\n```python\nprint(6 * 7)\n```",
            "```python\nhost.submit_output({'answer': 42}, ['Computed the answer'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    agent = Agent(use_skills=False, allow_delegate=False)
    result = agent.run("compute 6*7 and submit")
    # Completion is signalled through host.submit_output, not a text convention.
    assert result["stop_reason"] == "submitted"
    assert result["submitted_output"]["output"] == {"answer": 42}
    # 2 assistant turns happened
    assert len(scripted.calls) == 2


def test_cli_save_artifact_resolves_relative_to_actual_kernel_cwd(
    monkeypatch, tmp_path
):
    scripted = ScriptedLLM(
        [
            "```python\n"
            "open('cli-result.txt', 'w').write('science')\n"
            "saved = host.save_artifact('cli-result.txt')\n"
            "print(saved['version_id'])\n"
            "```",
            "```python\n"
            "host.submit_output({'saved': True}, ['Saved the CLI artifact'])\n"
            "```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    monkeypatch.chdir(tmp_path)

    agent = Agent(use_skills=False, allow_delegate=False, max_turns=3)
    result = agent.run("write and save a relative artifact")

    assert result["stop_reason"] == "submitted"
    artifact = agent.dispatcher.store.artifact_by_filename(
        "cli-result.txt", agent.frame_id, strict=True
    )
    assert artifact is not None
    metadata = agent.dispatcher.store.version_meta(artifact["latest_version_id"])
    assert metadata["path"] == str(tmp_path / "cli-result.txt")
    assert Path(metadata["snapshot_path"]).read_text() == "science"


def test_no_code_block_nudge(monkeypatch):
    scripted = ScriptedLLM(
        [
            "I think the answer is 42.",  # no code -> nudge
            "```python\nhost.submit_output({'a': 1}, ['Answered the question'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    result = Agent(use_skills=False, allow_delegate=False).run("hi")
    assert result["stop_reason"] == "submitted"


def test_submit_output_soft_fail_does_not_complete(monkeypatch):
    """host.submit_output with invalid completion_bullets soft-fails (the
    dispatcher returns {'error': ...} → RuntimeError in the cell) and the task
    does NOT end; a subsequent valid submit_output is what completes it."""
    scripted = ScriptedLLM(
        [
            "```python\n"
            "try:\n"
            "    host.submit_output({'a': 1}, [])\n"
            "except RuntimeError as e:\n"
            "    print('SOFT-FAIL:', e)\n"
            "```",
            "```python\nhost.submit_output({'a': 1}, ['Computed the answer'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    agent = Agent(use_skills=False, allow_delegate=False, max_turns=4)
    result = agent.run("submit twice")

    # the invalid submit did not stop the loop — the valid one did
    assert result["stop_reason"] == "submitted"
    assert len(scripted.calls) == 2
    assert result["submitted_output"]["output"] == {"a": 1}
    assert result["submitted_output"]["completion_bullets"] == ["Computed the answer"]
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    assert any(
        "SOFT-FAIL:" in o and "completion_bullets must be a list of 1-4 items" in o
        for o in obs
    )


def test_max_turns_stop(monkeypatch):
    # never calls submit_output -> should stop at max_turns
    scripted = ScriptedLLM(["```python\nx = 1\n```"] * 10)
    monkeypatch.setattr(loop_mod, "chat", scripted)
    agent = Agent(use_skills=False, allow_delegate=False, max_turns=3)
    result = agent.run("loop forever")
    assert result["stop_reason"] == "max_turns"


# ---- R execution channel (```r) -------------------------------------------


class _FakeRKernel:
    """Stands in for the persistent R kernel in loop tests (no R needed)."""

    def __init__(self):
        self.cells = []
        self.down = False

    def is_alive(self):
        return not self.down

    def execute(self, code, origin="agent", on_chunk=None):
        self.cells.append(code)
        return {
            "stdout": "[1] 42\n",
            "stderr": "",
            "error": None,
            "interrupted": False,
            "trace": {"error_lineno": None, "error_call": None},
            "usage": {},
        }

    def shutdown(self):
        self.down = True


def test_r_cell_routes_to_r_kernel_and_is_non_terminal(monkeypatch):
    """An ```r cell runs on the (lazily spawned) R kernel, its observation is
    fed back, and — R being an analysis channel with no host object — the task
    still completes only through a python host.submit_output cell. The R
    kernel is shut down with the run."""
    import openai4s.kernel.r_kernel as rk_mod

    fake = _FakeRKernel()
    spawns = []

    def fake_spawn(**kw):
        spawns.append(kw)
        return fake

    monkeypatch.setattr(rk_mod, "spawn_r_kernel", fake_spawn)
    scripted = ScriptedLLM(
        [
            "R first.\n```r\nx <- 42\nprint(x)\n```",
            "```python\nhost.submit_output({'a': 1}, ['Analyzed in R'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    result = Agent(use_skills=False, allow_delegate=False, max_turns=4).run("use R")

    assert result["stop_reason"] == "submitted"
    assert fake.cells == ["x <- 42\nprint(x)\n"]
    assert len(spawns) == 1  # lazy: spawned exactly once, on first ```r cell
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    assert any("[1] 42" in o for o in obs)
    assert fake.down  # run-scoped lifecycle


def test_r_cell_without_r_soft_fails_into_observation(monkeypatch):
    """No R interpreter -> the ```r cell yields an ERROR observation (never a
    crash), and the model can fall back to python and still finish."""
    import openai4s.kernel.r_kernel as rk_mod

    def no_r(**kw):
        raise RuntimeError("no R interpreter available: build the 'r' env")

    monkeypatch.setattr(rk_mod, "spawn_r_kernel", no_r)
    scripted = ScriptedLLM(
        [
            "```r\n1 + 1\n```",
            "```python\nhost.submit_output({'a': 1}, ['Fell back to python'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)
    result = Agent(use_skills=False, allow_delegate=False, max_turns=4).run("try R")

    assert result["stop_reason"] == "submitted"
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    assert any("R kernel unavailable" in o for o in obs)


# ---- ReAct tool surface (```tool) ----------------------------------------


def test_react_tool_call_then_submit(monkeypatch):
    """Happy ReAct path: a ```tool turn runs a read-only tool through the REAL
    HostDispatcher (whose workspace is a per-test tmp dir), its result is fed
    back as ONE '[Tool Results]' observation, and the loop CONTINUES to the next
    turn (it does not nudge or end) until a later python cell submits output."""
    scripted = ScriptedLLM(
        [
            # `list_dir` runs cleanly offline: the dispatcher auto-creates the
            # workspace dir and lists it (empty here) — no network, no fixtures.
            "Let me look around first.\n"
            + _tool_block('{"name": "list_dir", "arguments": {"path": "."}}'),
            "```python\nhost.submit_output({}, ['done'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    result = Agent(use_skills=False, allow_delegate=False, max_turns=4).run(
        "list the workspace, then submit"
    )

    # completion still flows ONLY through host.submit_output
    assert result["stop_reason"] == "submitted"
    # the tool result came back as one observation Turn, tagged [Tool Results]
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    assert any(o.startswith("[Tool Results]") for o in obs)
    assert any("[Tool: list_dir]" in o for o in obs)
    # the tool observation was fed back and the loop continued (>=2 chat calls):
    # it neither nudged nor ended on the tool turn.
    assert len(scripted.calls) >= 2


def test_react_malformed_tool_block_surfaces_error(monkeypatch):
    """Malformed ReAct path: a ```tool block with invalid JSON is surfaced as a
    '[Tool error]' observation (the loop does not crash), and a later python
    cell still completes the task."""
    scripted = ScriptedLLM(
        [
            _tool_block("{not valid json,}"),
            "```python\nhost.submit_output({}, ['done'])\n```",
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    result = Agent(use_skills=False, allow_delegate=False, max_turns=4).run(
        "bad tool, then submit"
    )

    assert result["stop_reason"] == "submitted"
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    # the parse error was fed back, not raised
    assert any("[Tool error]" in o for o in obs)


def test_code_cell_wins_over_embedded_tool_fence(monkeypatch):
    """Fence-collision guard: a ```python cell whose body QUOTES a ```tool block
    (e.g. writing docs about the tool syntax) runs the CELL — the embedded tool
    is never executed and the turn is not hijacked into a tool turn."""
    doc = (
        "```python\n"
        "readme = '''\nUsage example:\n"
        + _tool_block('{"name": "bash", "arguments": {"command": "echo pwned"}}')
        + "\n'''\nprint('wrote', len(readme), 'chars')\n"
        "host.submit_output({'readme': readme}, ['documented'])\n"
        "```"
    )
    scripted = ScriptedLLM([doc])
    monkeypatch.setattr(loop_mod, "chat", scripted)

    result = Agent(use_skills=False, allow_delegate=False, max_turns=1).run(
        "write the docs and submit"
    )

    assert result["stop_reason"] == "submitted"
    assert len(scripted.calls) == 1  # the embedded fence did not truncate/error
    assert '"name": "bash"' in result["submitted_output"]["output"]["readme"]
    obs = [t["content"] for t in result["transcript"] if t["role"] == "observation"]
    # the embedded ```tool must NOT have been executed as a tool call
    assert not any("[Tool Results]" in o for o in obs)
    assert not any("[Tool: bash]" in o for o in obs)
    assert not any("ERROR" in o for o in obs)


def test_four_backtick_python_fence_is_complete_and_wins_over_inner_tool():
    outer = "`" * 4
    reply = (
        outer
        + "python\nreadme = '''\n"
        + _tool_block('{"name": "bash", "arguments": {"command": "echo pwned"}}')
        + "\n'''\nhost.submit_output({'readme': readme}, ['done'])\n"
        + outer
    )
    code = loop_mod._extract_code(reply)
    assert code is not None
    compile(code, "<four-backtick-cell>", "exec")
    assert "host.submit_output" in code
    assert loop_mod.parse_tool_calls(reply) == ([], [])


# ---- compaction ----------------------------------------------------------


def test_estimate_tokens_monotonic():
    small = [{"role": "user", "content": "x"}]
    big = [{"role": "user", "content": "x" * 4000}]
    assert comp_mod.estimate_tokens(big) > comp_mod.estimate_tokens(small)


def test_should_compact_uses_window(monkeypatch):
    cfg = get_config()
    # ~1000 tokens of content
    msgs = [{"role": "user", "content": "x" * 4000}] * 10
    # Tiny window -> should compact; huge window -> should not.
    monkeypatch.setattr(cfg, "context_window_tokens", 100)
    monkeypatch.setattr(cfg, "compaction_trigger_ratio", 0.75)
    assert comp_mod.should_compact(msgs, cfg) is True
    monkeypatch.setattr(cfg, "context_window_tokens", 10_000_000)
    assert comp_mod.should_compact(msgs, cfg) is False


def test_compact_shrinks_and_preserves_head(monkeypatch):
    monkeypatch.setattr(comp_mod, "chat", ScriptedLLM(["SUMMARY TEXT"]))
    msgs = (
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        + [{"role": "assistant", "content": f"a{i}"} for i in range(6)]
        + [{"role": "user", "content": f"o{i}"} for i in range(6)]
    )
    out = comp_mod.compact(msgs, get_config(), keep_recent=4)
    assert len(out) < len(msgs)
    assert out[0]["content"] == "sys"  # system preserved
    assert out[1]["content"] == "task"  # original task preserved
    assert "SUMMARY TEXT" in out[2]["content"]  # summary injected
    assert out[-1]["content"] == "o5"  # most recent kept verbatim


# ---- delegation ----------------------------------------------------------


def test_delegate_fanout_cap():
    runner = DelegationRunner(get_config())
    with pytest.raises(DelegationError):
        runner({"request": ["t"] * (deleg_mod.FANOUT_CAP + 1)})


def test_delegate_single_and_list(monkeypatch):
    # Stub the leaf Agent.run so no real LLM/kernel is used.
    def fake_run(self, task):
        return {
            "stop_reason": "final",
            "submitted_output": {
                "output": {"echo": task},
                "completion_bullets": ["ok"],
            },
            "final_message": "FINAL",
        }

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)

    runner = DelegationRunner(get_config())
    one = runner({"request": "do X"})
    assert isinstance(one, dict)
    assert one["output"] == {"echo": "do X"}

    many = runner({"request": ["A", "B", "C"]})
    assert isinstance(many, list) and len(many) == 3
    assert {m["output"]["echo"] for m in many} == {"A", "B", "C"}


def test_delegate_session_cap(monkeypatch):
    def fake_run(self, task):
        return {"stop_reason": "final", "submitted_output": None, "final_message": None}

    monkeypatch.setattr(loop_mod.Agent, "run", fake_run)

    runner = DelegationRunner(get_config())
    runner._spawned = deleg_mod.SESSION_CAP  # pretend we're at the cap
    with pytest.raises(DelegationError):
        runner({"request": "one more"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
