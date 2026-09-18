"""Explicit code modes in the headless CLI (`openai4s run --mode ...`).

`reusable_pipeline` / `codebase_change` completion demands `test_evidence`
whose producing cell holds a Host-authorized `host.bash` receipt for the exact
command. A headless run has no approval channel and the shipped posture denies
unattended approvals, so that receipt could never be written: every run looped
to `max_turns` while the model guessed cell ids it had never been shown. These
pin the three repairs -- a narrow, exact-command opt-in, a contract the model
can actually learn (the cell id in the Observation, `host.bash` as the runner,
the fields as keyword arguments), and a refusal before the first model call
when nothing could ever authorize the runner.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shlex
import sys
from types import SimpleNamespace

import pytest

from openai4s.agent.task_modes import TaskMode, task_mode_prompt
from openai4s.config import Config, LLMConfig

TEST_COMMAND = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
    "from convert import convert; assert convert('3') == 6"
)

_CELL_ID_LINE = re.compile(r"\[cell id: ([^\]\s]+)\]")


def _cli():
    return importlib.import_module("openai4s.cli.main")


def _cfg(tmp_path, *, max_turns: int = 4) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=max_turns,
    )


def _args(**overrides) -> SimpleNamespace:
    values = {
        "task": "Build a reusable conversion pipeline module with a test.",
        "json": True,
        "verbose": False,
        "mode": "reusable_pipeline",
        "auto": False,
        "allow_test_command": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _ScriptedChat:
    """A provider double that records every call and may compute a reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, cfg, **kwargs):
        del cfg, kwargs
        self.calls.append([dict(message) for message in messages])
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if callable(reply):
            reply = reply(messages)
        return {"content": reply, "usage": {}}


def _json_payload(out: str) -> dict:
    return json.loads(out[out.index("{") :])


_WRITE_AND_TEST = (
    "```python\n"
    "from pathlib import Path\n"
    "Path('convert.py').write_text(\n"
    "    'def convert(value):\\n    return int(value) * 2\\n',\n"
    "    encoding='utf-8',\n"
    ")\n"
    "host.save_artifact('convert.py', 'convert.py')\n"
    f"test_result = host.bash({TEST_COMMAND!r})\n"
    "assert test_result['exit_code'] == 0, test_result\n"
    "print('convert smoke passed')\n"
    "```"
)


def _submit_naming_the_observed_cell(messages) -> str:
    """Name the cell exactly the way the prompt tells a model to: by reading
    the id off the previous cell's Observation -- never a hidden query."""

    observation = str(messages[-1].get("content") or "")
    match = _CELL_ID_LINE.search(observation)
    cell_id = match.group(1) if match else "no-cell-id-in-the-observation"
    return (
        "```python\n"
        "host.submit_output(\n"
        "    {'summary': 'convert.py owns the conversion helper.'},\n"
        "    ['Wrote convert.py and ran its smoke test through host.bash'],\n"
        "    source_files=[{'path': 'convert.py'}],\n"
        "    entry_points=['convert.py'],\n"
        "    architecture_summary='convert.py owns the numeric conversion helper.',\n"
        f"    test_evidence=[{{'command': {TEST_COMMAND!r}, "
        f"'producing_cell_id': {cell_id!r}}}],\n"
        ")\n"
        "```"
    )


def test_explicit_code_mode_completes_headless_with_a_preauthorized_test_command(
    tmp_path, monkeypatch, capsys
):
    """The live failure, offline: default deny posture, no channel. With the
    exact test command pre-authorized, the run writes the module, tests it
    through host.bash, reads the cell id off the Observation, and submits."""

    from openai4s.agent import loop as loop_mod

    assert os.environ.get("OPENAI4S_UNATTENDED_APPROVAL") == "deny"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    chat = _ScriptedChat(
        [_WRITE_AND_TEST, _submit_naming_the_observed_cell, "Stopping."]
    )
    monkeypatch.setattr(loop_mod, "chat", chat)

    status = cli.cmd_run(_args(allow_test_command=[TEST_COMMAND]))
    payload = _json_payload(capsys.readouterr().out)

    assert status == 0
    assert payload["stop_reason"] == "submitted", payload
    evidence = payload["submitted_output"]["test_evidence"][0]
    assert evidence["command"] == TEST_COMMAND
    # The id the model named is the durable producing cell the Host verified.
    from openai4s.store import get_store

    row = get_store(cfg.db_path).cell_detail(evidence["producing_cell_id"])
    assert row is not None and row["status"] == "ok"
    assert "convert smoke passed" in (row["stdout"] or "")


def test_explicit_code_mode_refuses_before_any_model_call_when_bash_is_unauthorizable(
    tmp_path, monkeypatch, capsys
):
    """No channel, unattended deny, no allow rule: host.bash can never be
    authorized, so the run must say so up front instead of spending every
    turn it has on a contract it cannot meet."""

    from openai4s.agent import loop as loop_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    monkeypatch.setattr(cli, "get_config", lambda: _cfg(tmp_path, max_turns=2))
    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)

    status = cli.cmd_run(_args(mode="codebase_change"))
    payload = _json_payload(capsys.readouterr().out)

    assert chat.calls == []
    assert status == 2
    assert payload["code"] == "code_mode_test_runner_unauthorized"
    assert "--allow-test-command" in payload["error"]
    assert "host.bash" in payload["error"]


def _frame_statuses(cfg) -> list[str]:
    """Every frame row's status, read without opening a Store."""

    import sqlite3

    if not cfg.db_path.exists():
        return []
    with sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute("SELECT status FROM frames")]


@pytest.mark.parametrize("mode", ["codebase_change", "reusable_pipeline"])
def test_a_preflight_refusal_closes_the_frame_the_agent_opened(
    tmp_path, monkeypatch, capsys, mode
):
    """The Agent opens its root turn frame before the preflight can refuse, and
    only `Agent.run` used to close it. A refused run never reaches `run`, so
    its row stayed `processing` forever: a phantom in-progress turn for a run
    that did no work."""

    from openai4s.agent import loop as loop_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    cfg = _cfg(tmp_path, max_turns=2)
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)

    status = cli.cmd_run(_args(mode=mode))
    payload = _json_payload(capsys.readouterr().out)

    assert status == 2 and chat.calls == []
    assert payload["code"] == "code_mode_test_runner_unauthorized"
    assert _frame_statuses(cfg) == ["failed"]


def test_a_preflight_that_raises_still_closes_the_frame(tmp_path, monkeypatch):
    from openai4s.agent import loop as loop_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    cfg = _cfg(tmp_path, max_turns=2)
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)

    def broken(self):
        raise RuntimeError("the permission store is unreadable")

    monkeypatch.setattr(loop_mod.Agent, "code_mode_preflight_refusal", broken)

    with pytest.raises(RuntimeError, match="unreadable"):
        cli.cmd_run(_args(mode="codebase_change"))

    assert chat.calls == []
    assert _frame_statuses(cfg) == ["failed"]


def test_closing_an_unrun_frame_never_writes_a_frame_the_agent_does_not_own(
    tmp_path,
):
    from openai4s.agent import loop as loop_mod

    owner = _agent(tmp_path, task_mode="codebase_change")
    store = owner.dispatcher.store
    borrowed = loop_mod.Agent(
        cfg=_cfg(tmp_path),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        dispatcher=owner.dispatcher,
        frame_id=owner.frame_id,
    )

    borrowed.close_unrun_frame("failed")
    assert store.get_frame(owner.frame_id)["status"] == "processing"
    owner.close_unrun_frame("failed")
    assert store.get_frame(owner.frame_id)["status"] == "failed"


def test_the_refusal_is_plain_text_without_json(tmp_path, monkeypatch, capsys):
    from openai4s.agent import loop as loop_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    monkeypatch.setattr(cli, "get_config", lambda: _cfg(tmp_path, max_turns=2))
    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)

    status = cli.cmd_run(_args(json=False))

    captured = capsys.readouterr()
    assert status == 2 and chat.calls == []
    assert captured.err.startswith("error: ")
    assert "--allow-test-command" in captured.err


@pytest.mark.parametrize(
    "argv_mode, command, message",
    [
        (None, TEST_COMMAND, "--mode reusable_pipeline or --mode codebase_change"),
        ("analysis_run", TEST_COMMAND, "--mode reusable_pipeline"),
        ("codebase_change", "python -m pytest; true", "mask"),
        ("codebase_change", "   ", "non-empty"),
    ],
)
def test_allow_test_command_is_validated_before_anything_runs(
    tmp_path, monkeypatch, capsys, argv_mode, command, message
):
    from openai4s.agent import loop as loop_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = _cli()
    monkeypatch.setattr(cli, "get_config", lambda: _cfg(tmp_path, max_turns=2))
    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)

    status = cli.cmd_run(_args(mode=argv_mode, allow_test_command=[command]))
    payload = _json_payload(capsys.readouterr().out)

    assert status == 2 and chat.calls == []
    assert payload["code"] == "invalid_allow_test_command"
    assert message in payload["error"]


def test_the_receipt_is_described_as_the_worker_report_it_is():
    """`host.bash` runs inside the Cell's own kernel process, with the Cell's
    PATH, cwd and in-process state, and the worker reports its exit status.
    The rule authorizes an exact command string; the receipt is not proof
    against a Cell that fakes the runner. Every place that described it as
    the Host's own receipt for "exactly this test command" must say so."""

    from pathlib import Path

    from openai4s import prompts

    root = Path(__file__).resolve().parents[1]
    parser = _cli().build_parser()
    run = next(
        action
        for action in parser._subparsers._group_actions[0].choices["run"]._actions
        if "--allow-test-command" in action.option_strings
    )
    flag_help = " ".join(str(run.help).split())
    assert "exact command string" in flag_help
    assert "worker" in flag_help and "not proof" in flag_help

    fragment = " ".join(prompts._TASK_MODE_SHARED_COMPLETION.split())
    assert "its own receipt" not in fragment
    assert "never evidence" not in fragment
    assert "exact command string" in fragment
    assert "exit status" in fragment and "`PATH`" in fragment

    architecture = " ".join(
        (root / "docs" / "architecture.md").read_text(encoding="utf-8").split()
    )
    assert "Host's own successful `host.bash` receipt" not in architecture
    assert "never counts" not in architecture
    assert "kernel worker's own report" in architecture

    cli = root / "openai4s" / "cli"
    english = " ".join((cli / "README.md").read_text(encoding="utf-8").split())
    chinese = "".join((cli / "README_zh.md").read_text(encoding="utf-8").split())
    assert "pre-authorizes exactly that command for" not in english
    assert "exact command string" in english and "not proof" in english
    assert "精确命令字符串" in chinese and "并不能证明" in chinese


def test_the_opt_in_is_parsed_as_a_repeatable_flag():
    parser = _cli().build_parser()
    args = parser.parse_args(
        [
            "run",
            "task",
            "--mode",
            "codebase_change",
            "--allow-test-command",
            "python -m pytest -q",
            "--allow-test-command",
            "python -m unittest",
        ]
    )
    assert args.allow_test_command == ["python -m pytest -q", "python -m unittest"]
    assert parser.parse_args(["run", "task"]).allow_test_command is None


def _agent(tmp_path, **kwargs):
    from openai4s.agent import loop as loop_mod

    return loop_mod.Agent(
        cfg=_cfg(tmp_path),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        **kwargs,
    )


def test_the_opt_in_is_an_exact_command_rule_not_a_glob(tmp_path):
    """A test command with glob metacharacters must authorize itself and
    nothing its pattern could otherwise match."""

    from openai4s.permissions import PermissionBroker

    command = "pytest tests/test_*.py"
    agent = _agent(
        tmp_path, task_mode="codebase_change", allowed_test_commands=(command,)
    )
    agent.authorize_test_commands()
    store = agent.dispatcher.store

    def decision(target: str, root: str | None = None) -> str:
        return store.resolve_permission(
            root_frame_id=root or agent.frame_id,
            project_id="default",
            tool="bash",
            pattern_input=target,
        )

    assert decision(command) == "allow"
    assert decision("pytest tests/test_x.py") == "ask"
    assert decision("pytest tests/test_*.py; rm -rf ~") == "ask"
    # The gate itself still refuses anything else, headless.
    refused = PermissionBroker().gate(
        store=store,
        frame_id=agent.frame_id,
        method="bash",
        target="pytest tests/test_x.py",
    )
    assert refused["allow"] is False
    # Conversation scope: another conversation gains nothing.
    other = store.new_frame(kind="turn")
    assert decision(command, root=other) == "ask"


def test_preflight_tracks_what_could_really_authorize_the_runner(tmp_path, monkeypatch):
    agent = _agent(tmp_path, task_mode="reusable_pipeline")
    assert "--allow-test-command" in (agent.code_mode_preflight_refusal() or "")

    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    assert agent.code_mode_preflight_refusal() is None
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "deny")

    allowed = _agent(
        tmp_path, task_mode="reusable_pipeline", allowed_test_commands=(TEST_COMMAND,)
    )
    assert allowed.code_mode_preflight_refusal() is None

    # An analysis run, or no selection at all, has no runner requirement.
    assert _agent(tmp_path).code_mode_preflight_refusal() is None
    assert (
        _agent(tmp_path, task_mode="analysis_run").code_mode_preflight_refusal() is None
    )


def test_under_the_guardian_only_a_standing_rule_authorizes_the_runner(
    tmp_path, monkeypatch
):
    """--auto hands asks to the Guardian, which never approves a shell
    command; OPENAI4S_UNATTENDED_APPROVAL=allow is not consulted there."""

    from openai4s.agent import loop as loop_mod

    # Exactly what `openai4s run --auto` turns on, read the way it is read:
    # at Config construction.
    for key, value in loop_mod.AUTO_RUN_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    guarded = _cfg(tmp_path)
    agent = loop_mod.Agent(
        cfg=guarded,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        task_mode="codebase_change",
    )
    refusal = agent.code_mode_preflight_refusal()
    assert refusal is not None and "Guardian" in refusal

    agent.allowed_test_commands = (TEST_COMMAND,)
    assert agent.code_mode_preflight_refusal() is None


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_the_fragment_teaches_the_contract_the_host_enforces(mode):
    body = " ".join(task_mode_prompt(mode).split())
    # The runner the receipt comes from.
    assert "host.bash" in body
    # Where the id comes from, and that it is not a counter.
    assert "[cell id:" in body
    # Keyword arguments, not keys inside `output`.
    assert "test_evidence=[" in body
    assert "keyword argument" in body


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_a_detected_fragment_carries_none_of_the_armed_contract_teaching(mode):
    """A DETECTED mode stamps no binding mode, so its Observations carry no
    `[cell id: …]` line and nothing pre-authorizes `host.bash` (the CLI refuses
    `--allow-test-command` without `--mode`; the Web raises an approval card
    per command). Its fragment must not teach a runner and an id that only an
    explicit selection makes real."""

    detected = " ".join(task_mode_prompt(mode, explicit=False).split())
    assert "host.bash" not in detected
    assert "[cell id:" not in detected
    assert "producing_cell_id" not in detected
    # Still the structure guidance, and still honest about being advisory.
    assert "source_files" in detected and "entry_points" in detected
    assert "advisory" in detected


_DETECTED_PIPELINE_TASK = (
    "Build a reusable pipeline script that I can re-run on new CSV files, with tests."
)


@pytest.mark.parametrize(
    ("task_mode", "allowed"),
    [(None, ()), ("reusable_pipeline", (TEST_COMMAND,))],
    ids=["detected", "explicit"],
)
def test_the_mode_fragment_promises_only_what_the_run_keeps(
    monkeypatch, tmp_path, task_mode, allowed
):
    """Bind the request the model reads to what the runtime then does: a
    promised `[cell id: …]` line is one the Observation carries, a named
    `host.bash` runner is one this run could authorize, and a
    `producing_cell_id` is asked for only where an id is shown."""

    from openai4s.agent.task_modes import resolve_task_mode
    from openai4s.permissions import broker

    assert resolve_task_mode(_DETECTED_PIPELINE_TASK) is TaskMode.REUSABLE_PIPELINE
    agent, chat = _fake_kernel_agent(
        monkeypatch,
        tmp_path,
        task_mode=task_mode,
        replies=["```python\nprint('2 passed')\n```", "Stopping."],
    )
    agent.allowed_test_commands = allowed
    agent.run(_DETECTED_PIPELINE_TASK)

    request = " ".join(str(chat.calls[0][1]["content"]).split())
    observation = str(chat.calls[1][-1]["content"])
    assert "[TASK MODE: reusable_pipeline]" in request
    shows_cell_id = _CELL_ID_LINE.search(observation) is not None
    assert ("[cell id:" in request) == shows_cell_id, (request, observation)
    if not shows_cell_id:
        assert "producing_cell_id" not in request
    bash_reachable = broker().approval_reachable(
        store=agent.dispatcher.store,
        frame_id=str(agent.frame_id),
        method="bash",
        side_effect_class="runtime_mutation",
        guardian_config=agent.cfg,
    )
    assert bash_reachable or "host.bash" not in request, request


def _fake_kernel_agent(monkeypatch, tmp_path, *, task_mode, replies):
    from openai4s.agent import loop as loop_mod

    class _Kernel:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            return {"stdout": "2 passed\n", "error": None}

        def shutdown(self):
            pass

    chat = _ScriptedChat(replies)
    monkeypatch.setattr(loop_mod, "Kernel", _Kernel)
    monkeypatch.setattr(loop_mod, "chat", chat)
    agent = loop_mod.Agent(
        cfg=_cfg(tmp_path),
        max_turns=2,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        task_mode=task_mode,
    )
    return agent, chat


def test_an_armed_code_mode_observation_names_its_durable_cell(monkeypatch, tmp_path):
    agent, chat = _fake_kernel_agent(
        monkeypatch,
        tmp_path,
        task_mode="codebase_change",
        replies=["```python\nprint('2 passed')\n```", "Stopping."],
    )
    agent.run("move the helpers into their own module")

    observation = str(chat.calls[1][-1]["content"])
    match = _CELL_ID_LINE.search(observation)
    assert match is not None, observation
    assert observation.startswith("[Observation]\n[cell id: ")
    row = agent.dispatcher.store.cell_detail(match.group(1))
    assert row is not None and row["root_frame_id"] == agent.frame_id


def test_an_unarmed_observation_is_byte_for_byte_unchanged(monkeypatch, tmp_path):
    agent, chat = _fake_kernel_agent(
        monkeypatch,
        tmp_path,
        task_mode=None,
        replies=["```python\nprint('2 passed')\n```", "Stopping."],
    )
    agent.run("compute something")

    observation = str(chat.calls[1][-1]["content"])
    assert observation.startswith("[Observation]\nstdout:\n2 passed")
    assert "[cell id:" not in observation


def test_the_web_executor_names_the_cell_only_when_the_contract_is_armed():
    from openai4s.agent.actions import CodeCell
    from openai4s.agent.models import ModelReply, RunState
    from openai4s.server.agent_run import WebActionExecutor, WebEventSink

    def executor(mode):
        dispatcher = SimpleNamespace(binding_task_mode=mode, last_output=None)
        return WebActionExecutor(
            dispatcher=lambda: dispatcher,
            apply_pending=lambda: None,
            execute_cell=lambda action: {
                "id": "cell-web-1",
                "stdout": "2 passed\n",
                "stderr": "",
                "error": None,
            },
            events=WebEventSink(lambda _e: None, "frame-1", [], lambda _u: None),
            prose_nudge="nudge",
            explore_nudge="explore",
        )

    reply = ModelReply(content="```python\nprint(1)\n```")
    armed = executor("reusable_pipeline").execute(
        CodeCell("python", "print(1)\n"), reply, RunState(messages=[])
    )
    plain = executor(None).execute(
        CodeCell("python", "print(1)\n"), reply, RunState(messages=[])
    )
    assert armed.observation.startswith("[Observation]\n[cell id: cell-web-1]\n")
    assert plain.observation.startswith("[Observation]\nstdout:\n")


def test_a_missing_cell_refusal_says_where_the_id_comes_from():
    from openai4s.host.code_evidence import CodeEvidenceContext, _check_test_evidence

    problems: list[str] = []
    _check_test_evidence(
        [{"command": "python -m pytest", "producing_cell_id": "2"}],
        CodeEvidenceContext(cell_lookup=lambda _cell: None, has_cells=lambda: True),
        problems,
    )
    assert problems and "never executed" in problems[0]
    assert "[cell id:" in problems[0]


def test_the_run_tells_the_model_which_exact_commands_are_preauthorized(
    monkeypatch, tmp_path
):
    from openai4s.agent import loop as loop_mod

    chat = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", chat)
    agent = loop_mod.Agent(
        cfg=_cfg(tmp_path),
        max_turns=1,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        task_mode="reusable_pipeline",
        allowed_test_commands=("python -m pytest -q",),
    )
    agent.run("Build a reusable pipeline with tests.")
    request = str(chat.calls[0][1]["content"])
    assert "host.bash('python -m pytest -q')" in request
    assert "Any other host.bash command" in request
    # The note steers other checks into Python, never around the gated,
    # audited runner: it does not recommend spawning a shell from the cell.
    note = request[request.index("Pre-authorized test commands") :]
    assert "subprocess" not in note and "os.system" not in note
    assert "import" in note

    plain = _ScriptedChat(["Stopping."])
    monkeypatch.setattr(loop_mod, "chat", plain)
    loop_mod.Agent(
        cfg=_cfg(tmp_path),
        max_turns=1,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        task_mode="reusable_pipeline",
    ).run("Build a reusable pipeline with tests.")
    assert "Pre-authorized test commands" not in str(plain.calls[0][1]["content"])
