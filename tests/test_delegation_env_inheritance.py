"""Delegated children inherit the parent session's selected environment.

Three confirmed defects, one inheritance chain:

  * a delegated child's kernels always spawned on ``sys.executable`` — the
    daemon's own venv — whatever environment the parent session had selected;
  * ``host.env.use`` inside a child (or the CLI Agent) returned ``ok: true``
    with a note while switching nothing, so the next cell silently ran on the
    old interpreter;
  * child kernels never registered a ``kernel_generations`` row, so a child
    artifact's environment provenance degraded to the "assumed" daemon
    fallback — coincidentally right only while defect one persisted.

These tests execute real cells in real workers wherever the claim is about an
interpreter: asserting on a resolved path would have passed against the broken
code too.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent import Agent
from openai4s.agent.delegation import DelegationRunner
from openai4s.agent.models import KernelEnvSpec
from openai4s.config import Config, LLMConfig, get_config
from openai4s.kernel import environments as envmod
from openai4s.server import gateway as gateway_mod
from openai4s.server.artifacts import ArtifactManager
from openai4s.store import get_store


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


@pytest.fixture(autouse=True)
def _fresh_discovery():
    envmod.invalidate_cache()
    yield
    envmod.invalidate_cache()


def _fake_env(root: Path, name: str) -> Path:
    """A conda-shaped env whose bin/python really is an interpreter."""
    bin_dir = root / name / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(sys.executable)
    return root / name


def _env_spec(env_dir: Path) -> KernelEnvSpec:
    return KernelEnvSpec(
        python=str(env_dir / "bin" / "python"),
        env_root=str(env_dir),
        env_name=env_dir.name,
    )


def _report_cell() -> str:
    return (
        "```python\n"
        "import sys\n"
        "host.submit_output({'exe': sys.executable}, ['reported the interpreter'])\n"
        "```"
    )


# --------------------------------------------------------------------------
# D2: the parent's selection reaches child (and nested-child) kernels
# --------------------------------------------------------------------------


def test_child_cell_runs_the_parent_selected_interpreter(monkeypatch, tmp_path):
    env_dir = _fake_env(tmp_path / "envs", "sci")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scripted = ScriptedLLM([_report_cell()])
    monkeypatch.setattr(loop_mod, "chat", scripted)

    runner = DelegationRunner(get_config(), workspace=workspace, env=_env_spec(env_dir))
    try:
        result = runner({"request": "report the interpreter"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    assert result["output"]["exe"] == str(env_dir / "bin" / "python"), (
        "the child kernel ran on the daemon interpreter instead of the "
        "parent session's selected environment"
    )


def test_nested_child_inherits_through_the_delegation_tree(monkeypatch, tmp_path):
    """A depth-1 runner is exactly the nested runner a delegated child builds
    in Agent.__post_init__; the env must travel with the tree, not per call."""
    env_dir = _fake_env(tmp_path / "envs", "sci")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scripted = ScriptedLLM([_report_cell()])
    monkeypatch.setattr(loop_mod, "chat", scripted)

    runner = DelegationRunner(
        get_config(), depth=1, workspace=workspace, env=_env_spec(env_dir)
    )
    try:
        result = runner({"request": "report the interpreter"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    assert result["output"]["exe"] == str(env_dir / "bin" / "python")


def test_delegating_agent_threads_env_to_its_nested_runner(tmp_path):
    """A child Agent hands its own env to the runner it builds for
    grandchildren, so the whole delegation subtree stays on one selection."""
    spec = KernelEnvSpec(python="/fake/bin/python", env_name="fake")
    agent = Agent(use_skills=False, workspace=tmp_path, env=spec)
    try:
        assert agent._delegation_runner is not None
        assert agent._delegation_runner.env is spec
    finally:
        agent._delegation_runner.close()


def test_all_three_kernel_construction_sites_honor_the_env(monkeypatch, tmp_path):
    """Foreground LazyKernel, background factory, and the R channel pin."""
    captured: list[dict] = []

    class FakeKernel:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.generation = 1
            self.python = kwargs.get("python")
            self.env_root = kwargs.get("env_root")
            self.env_name = kwargs.get("env_name")
            self.cwd = kwargs.get("cwd")
            self.mode = kwargs.get("mode", "repl")
            self.argv = None
            self.pid = None

        def execute(self, code, **kw):
            return {"id": "c1", "stdout": "", "stderr": "", "error": None}

        def is_alive(self):
            return True

        def interrupt(self):
            return None

        def shutdown(self):
            return None

    monkeypatch.setattr(loop_mod, "Kernel", FakeKernel)
    scripted = ScriptedLLM(["```python\nprint('hi')\n```"])
    monkeypatch.setattr(loop_mod, "chat", scripted)
    spec = KernelEnvSpec(
        python="/sel/bin/python",
        env_root="/sel",
        env_name="sel",
        r_env="r-sci",
    )
    agent = Agent(
        cfg=get_config(),
        max_turns=1,
        use_skills=False,
        allow_delegate=False,
        workspace=tmp_path,
        env=spec,
    )
    agent.run("run one cell")

    assert captured, "the foreground cell never constructed a kernel"
    foreground = captured[0]
    assert foreground["python"] == "/sel/bin/python"
    assert foreground["env_root"] == "/sel"
    assert foreground["env_name"] == "sel"

    background = agent.dispatcher.background_kernel_factory()
    assert isinstance(background, FakeKernel)
    assert captured[-1]["python"] == "/sel/bin/python"
    assert captured[-1]["env_root"] == "/sel"
    assert captured[-1]["env_name"] == "sel"

    # The R channel honors the inherited pin through the existing
    # active_r_env respawn mechanism.
    assert agent.dispatcher.active_r_env == "r-sci"


# --------------------------------------------------------------------------
# D2 Web wiring: gateway resolves the session env and (re)stamps the runner
# --------------------------------------------------------------------------


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def test_wire_delegation_stamps_and_restamps_the_session_env(monkeypatch, tmp_path):
    roots = tmp_path / "envs"
    _fake_env(roots, "sci")
    _fake_env(roots, "sci2")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    envmod.discover_environments(force=True)

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=2,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    st = runner._state(frame_id, "default")
    st.desired_env = "sci"
    try:
        runner._ensure_runtime(st)
        delegated = st.delegation_runner
        assert delegated is not None
        assert (
            delegated.env is not None
        ), "the Web gateway wired delegation without the session environment"
        assert delegated.env.env_name == "sci"
        assert delegated.env.python == str(roots / "sci" / "bin" / "python")
        assert delegated.env.env_root == str(roots / "sci")

        # Per-turn re-stamp follows the workspace re-stamp pattern: the tree,
        # budget, and running children survive; future children follow the
        # newly selected environment.
        st.desired_env = "sci2"
        st.env_name = None
        runner._wire_delegation(st)
        assert st.delegation_runner is delegated
        assert delegated.env.env_name == "sci2"
        assert delegated.env.python == str(roots / "sci2" / "bin" / "python")
    finally:
        runner.close()


# --------------------------------------------------------------------------
# D3: a child kernel registers a durable generation the provenance can name
# --------------------------------------------------------------------------


def test_child_artifact_environment_resolves_the_child_generation(
    monkeypatch, tmp_path
):
    env_dir = _fake_env(tmp_path / "envs", "sci")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scripted = ScriptedLLM([_report_cell()])
    monkeypatch.setattr(loop_mod, "chat", scripted)

    cfg = get_config()
    store = get_store(cfg.db_path)
    parent_frame = store.new_frame(kind="turn", status="ready")
    runner = DelegationRunner(
        cfg,
        parent_frame_id=parent_frame,
        store=store,
        workspace=workspace,
        env=_env_spec(env_dir),
    )
    try:
        result = runner({"request": "report the interpreter"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    child_frame = result["frame_id"]
    assert child_frame

    generation = store.latest_kernel_generation(child_frame, "python")
    assert (
        generation is not None
    ), "the child kernel spawned without registering a kernel generation"
    environment = generation["environment"]
    assert environment["interpreter"] == str(env_dir / "bin" / "python")
    assert environment["environment_name"] == "sci"
    assert environment["environment_root"] == str(env_dir)
    assert environment["runtime"] == "python"
    # The run finished, so the row was closed rather than left dangling live.
    assert generation["ended_at"] is not None

    manager = ArtifactManager(
        data_dir=tmp_path / "artifacts",
        store=store,
        workspace_for=lambda _frame: workspace,
        broadcast=lambda _frame, _event: None,
        guess_content_type=lambda _name: "text/plain",
        checksum=lambda _path: "x",
    )
    snapshot = store.get_env_snapshot(
        manager.capture_environment(None, root_frame_id=child_frame)
    )
    assert snapshot["generation_id"] == generation["generation_id"], (
        "a delegated child artifact still borrows the daemon environment "
        "instead of the child kernel's own generation"
    )
    assert snapshot["interpreter"] == str(env_dir / "bin" / "python")
    assert snapshot["environment_name"] == "sci"
    assert (
        snapshot.get("provenance") is None
    ), "a real generation-backed snapshot must not carry the 'assumed' label"


# --------------------------------------------------------------------------
# D4: env_use is honest, and a child switch is real
# --------------------------------------------------------------------------


def test_env_use_without_a_switch_callback_is_an_error():
    from openai4s.tools.env_use import EnvUseTool

    runtime = SimpleNamespace(
        active_env_bin=None, active_r_env=None, on_env_switch=None
    )
    result = EnvUseTool().execute(runtime, {"name": "base"})

    assert (
        "ok" not in result
    ), "a runtime that cannot switch environments reported a successful switch"
    assert "not available" in result["error"]
    assert "base" in result["error"]  # names the environment the kernel stays on


def test_child_env_use_switches_the_next_cells_interpreter(monkeypatch, tmp_path):
    roots = tmp_path / "envs"
    env_dir = _fake_env(roots, "sci")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    envmod.discover_environments(force=True)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    scripted = ScriptedLLM(
        [
            "```python\n"
            "import sys\n"
            "open('first.txt', 'w').write(sys.executable)\n"
            "result = host.env.use('sci')\n"
            "print('switch ok:', result['ok'])\n"
            "```",
            _report_cell(),
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    runner = DelegationRunner(get_config(), workspace=workspace)
    try:
        result = runner({"request": "switch env then report"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    # The first cell ran on the default interpreter; the switch applied at the
    # next cell boundary, never mid-cell.
    assert (workspace / "first.txt").read_text() == sys.executable
    assert result["output"]["exe"] == str(env_dir / "bin" / "python"), (
        "host.env.use reported ok but the next cell still ran on the old " "interpreter"
    )


def test_env_use_of_an_unknown_env_errors_without_switching(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scripted = ScriptedLLM(
        [
            "```python\n"
            "try:\n"
            "    host.env.use('no-such-env-anywhere')\n"
            "except RuntimeError as error:\n"
            "    open('refused.txt', 'w').write(str(error))\n"
            "```",
            _report_cell(),
        ]
    )
    monkeypatch.setattr(loop_mod, "chat", scripted)

    runner = DelegationRunner(get_config(), workspace=workspace)
    try:
        result = runner({"request": "try a bogus env"})
    finally:
        runner.close()

    assert result["stop_reason"] == "submitted"
    refused = (workspace / "refused.txt").read_text()
    assert "unknown environment" in refused
    assert (
        result["output"]["exe"] == sys.executable
    ), "an unknown environment must not change the kernel interpreter"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
