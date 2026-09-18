"""Offline characterization of the supported ``openai4s`` CLI surface."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _cli_module():
    return importlib.import_module("openai4s.cli.main")


def test_console_and_module_entrypoints_target_the_same_main():
    package_cli = importlib.import_module("openai4s.cli")
    module_cli = _cli_module()
    module_entry = importlib.import_module("openai4s.__main__")

    assert package_cli.main is module_cli.main
    assert module_entry.main is module_cli.main

    # Parse only the relevant TOML section so this stays Python 3.10 compatible
    # without adding a TOML dependency to the stdlib-only project.
    section = None
    scripts: dict[str, str] = {}
    for raw in (_REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section == "[project.scripts]" and "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            scripts[name.strip()] = value.strip().strip('"').strip("'")
    assert scripts.get("openai4s") == "openai4s.cli:main"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["serve"], {"cmd": "serve", "no_open": False}),
        (["serve", "--no-open"], {"cmd": "serve", "no_open": True}),
        (
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
                "--no-browser",
                "--detached",
            ],
            {
                "cmd": "serve",
                "host": "127.0.0.1",
                "port": 8080,
                "no_open": True,
                "detached": True,
            },
        ),
        (["status"], {"cmd": "status"}),
        (["stop"], {"cmd": "stop", "force": False}),
        (["stop", "--force"], {"cmd": "stop", "force": True}),
        (["url"], {"cmd": "url"}),
        (
            ["run", "analyze data"],
            {"cmd": "run", "task": "analyze data", "json": False, "verbose": False},
        ),
        (
            ["run", "analyze data", "--json", "--verbose"],
            {"cmd": "run", "task": "analyze data", "json": True, "verbose": True},
        ),
        (
            ["run", "analyze data", "-v"],
            {"cmd": "run", "task": "analyze data", "json": False, "verbose": True},
        ),
        (
            ["init", "--provider", "claude", "--non-interactive"],
            {
                "cmd": "init",
                "provider": "claude",
                "model": None,
                "base_url": None,
                "api_key_stdin": False,
                "clear_api_key": False,
                "non_interactive": True,
                "json": False,
            },
        ),
        (["setup"], {"cmd": "setup", "only": None, "dry_run": False}),
        (
            ["setup", "--only", "r", "--dry-run"],
            {"cmd": "setup", "only": "r", "dry_run": True},
        ),
        (
            ["jupyter", "describe", "--json"],
            {"cmd": "jupyter", "jupyter_action": "describe", "json": True},
        ),
        (
            ["jupyter", "export", "/tmp/specs", "--language", "r"],
            {
                "cmd": "jupyter",
                "jupyter_action": "export",
                "language": "r",
                "output": Path("/tmp/specs"),
                "replace": False,
            },
        ),
        (
            ["jupyter", "install", "--prefix", "/tmp/prefix", "--replace"],
            {
                "cmd": "jupyter",
                "jupyter_action": "install",
                "language": "all",
                "prefix": Path("/tmp/prefix"),
                "replace": True,
            },
        ),
    ],
)
def test_subcommands_and_arguments_parse_compatibly(argv, expected):
    args = _cli_module().build_parser().parse_args(argv)
    for name, value in expected.items():
        assert getattr(args, name) == value


@pytest.mark.parametrize("name", ["python", "phylo", "r", "struct"])
def test_setup_only_accepts_each_documented_environment(name):
    args = _cli_module().build_parser().parse_args(["setup", "--only", name])
    assert args.only == name


@pytest.mark.parametrize(
    ("argv", "expected_fragment"),
    [
        (["serve", "--help"], "--no-open"),
        (["serve", "--help"], "--no-browser"),
        (["serve", "--help"], "--detached"),
        (["serve", "--help"], "--port"),
        (["stop", "--help"], "--force"),
        (["run", "--help"], "--json"),
        (["run", "--help"], "--verbose"),
        (["init", "--help"], "--api-key-stdin"),
        (["setup", "--help"], "--only"),
        (["setup", "--help"], "--dry-run"),
        (["benchmark", "--help"], "--acceptance"),
        (["jupyter", "describe", "--help"], "--json"),
        (["jupyter", "export", "--help"], "--language"),
        (["jupyter", "install", "--help"], "--prefix"),
    ],
)
def test_subcommand_help_advertises_supported_options(argv, expected_fragment, capsys):
    with pytest.raises(SystemExit) as stopped:
        _cli_module().main(argv)
    assert stopped.value.code == 0
    assert expected_fragment in capsys.readouterr().out


def test_root_help_lists_every_supported_subcommand_through_python_m():
    proc = subprocess.run(
        [sys.executable, "-m", "openai4s", "--help"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        "{serve,status,doctor,verify-package,diagnostics,stop,url,run,init,setup,"
        "benchmark,env,jupyter,share,cluster,user,relay}" in proc.stdout
    )
    for command in (
        "serve",
        "status",
        # Environments are a transaction, and a transaction nobody can drive
        # from the command line is one nobody uses.
        "env",
        # A benchmark nobody can run is a directory of fixtures.
        "benchmark",
        # The command for someone whose daemon will not start: if it is not in
        # --help, it does not exist to the person who needs it.
        "doctor",
        # A recipient verifying an evidence package has no daemon and no docs
        # open; a command absent from --help may as well not exist.
        "verify-package",
        # A support command has to be discoverable from --help, or the user in
        # trouble hand-collects files instead and shares whatever they grab.
        "diagnostics",
        "stop",
        "url",
        "run",
        "init",
        "setup",
        "jupyter",
        "share",
        # Team-mode accounts are managed on the server, daemon or not; an
        # admin who cannot find `user` in --help cannot bootstrap login.
        "user",
        # Batch jobs: the surface a researcher reaches for when the work is
        # too long to sit in front of.
        "cluster",
        "relay",
    ):
        assert command in proc.stdout


def test_root_version_prints_the_declared_package_version(capsys):
    from openai4s import __version__

    with pytest.raises(SystemExit) as stopped:
        _cli_module().main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"openai4s {__version__}"


def test_cli_rejects_unknown_commands_and_missing_run_task(capsys):
    for argv in (["unknown"], ["run"]):
        with pytest.raises(SystemExit) as stopped:
            _cli_module().main(argv)
        assert stopped.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_serve_rejects_invalid_ports(port, capsys):
    with pytest.raises(SystemExit) as stopped:
        _cli_module().main(["serve", "--port", port])
    assert stopped.value.code == 2
    capsys.readouterr()


def test_url_command_is_offline_and_returns_success(monkeypatch, capsys):
    module = _cli_module()
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: SimpleNamespace(host="127.0.0.1", port=9876),
    )

    assert module.main(["url"]) == 0
    assert capsys.readouterr().out.strip() == "http://127.0.0.1:9876/"


def _recorded_daemon_config(tmp_path, *, host="172.25.100.5", port=9876, pid=4321):
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        pidfile=tmp_path / "openai4s.pid",
        statefile=tmp_path / "daemon.json",
    )
    config.pidfile.write_text(str(pid), encoding="utf-8")
    config.statefile.write_text(
        json.dumps(
            {
                "pid": pid,
                "pid_start": "daemon-start",
                "host": host,
                "port": port,
            }
        ),
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize(
    ("recorded_host", "url_host"),
    [
        ("172.25.100.5", "172.25.100.5"),
        ("0.0.0.0", "localhost"),
        ("", "localhost"),
        ("::", "localhost"),
        ("::1", "[::1]"),
    ],
)
def test_url_uses_the_live_recorded_endpoint_without_losing_url_semantics(
    tmp_path, monkeypatch, capsys, recorded_host, url_host
):
    from openai4s.server import local_auth

    module = _cli_module()
    config = _recorded_daemon_config(tmp_path, host=recorded_host)
    token = local_auth.load_or_mint(tmp_path)

    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module, "_daemon_alive", lambda _cfg, _pid: True)
    monkeypatch.setattr(module, "_process_start_token", lambda _pid: "daemon-start")

    assert module.cmd_url(SimpleNamespace()) == 0
    assert capsys.readouterr().out.strip() == (f"http://{url_host}:9876/?token={token}")


def test_url_ignores_a_recorded_endpoint_when_the_pid_is_not_live(
    tmp_path, monkeypatch, capsys
):
    module = _cli_module()
    config = _recorded_daemon_config(tmp_path)

    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module, "_daemon_alive", lambda _cfg, _pid: False)

    assert module.cmd_url(SimpleNamespace()) == 0
    assert capsys.readouterr().out.strip() == "http://127.0.0.1:8760/"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"pid": 4322, "host": "172.25.100.5", "port": 9876},
            id="stale-pid",
        ),
        pytest.param(
            {
                "pid": 4321,
                "pid_start": "stale-start",
                "host": "172.25.100.5",
                "port": 9876,
            },
            id="reused-pid",
        ),
        pytest.param(
            {
                "pid": 4321,
                "pid_start": None,
                "host": "172.25.100.5",
                "port": 9876,
            },
            id="missing-start-token",
        ),
        pytest.param(
            {"pid": True, "host": "172.25.100.5", "port": 9876}, id="bool-pid"
        ),
        pytest.param({"pid": 4321, "host": None, "port": 9876}, id="host-type"),
        pytest.param({"pid": 4321, "host": "bad host", "port": 9876}, id="host-space"),
        pytest.param(
            {"pid": 4321, "host": "http://remote", "port": 9876},
            id="host-url",
        ),
        pytest.param({"pid": 4321, "host": "[::1]", "port": 9876}, id="host-brackets"),
        pytest.param(
            {"pid": 4321, "host": "127.0.0.1", "port": "9876"},
            id="port-type",
        ),
        pytest.param({"pid": 4321, "host": "127.0.0.1", "port": True}, id="bool-port"),
        pytest.param({"pid": 4321, "host": "127.0.0.1", "port": 0}, id="port-zero"),
        pytest.param({"pid": 4321, "host": "127.0.0.1", "port": 65536}, id="port-high"),
        pytest.param("not-json", id="malformed-json"),
    ],
)
def test_recorded_endpoint_rejects_stale_or_invalid_state(
    tmp_path, monkeypatch, payload
):
    module = _cli_module()
    config = SimpleNamespace(statefile=tmp_path / "daemon.json")
    if isinstance(payload, str):
        content = payload
    else:
        content = json.dumps({"pid_start": "daemon-start", **payload})
    config.statefile.write_text(content, encoding="utf-8")
    monkeypatch.setattr(module, "_process_start_token", lambda _pid: "daemon-start")

    assert module._recorded_endpoint(config, 4321) is None


#: What `sysctl kern.proc.pid.<pid>` hands back on LP64 macOS for a running
#: process that started at 1789000000.123456: ``kp_proc.p_starttime`` (a
#: ``struct timeval``) opens the record, ``kp_proc.p_stat`` sits at byte 36.
def _darwin_kinfo_record(*, seconds=1_789_000_000, micros=123_456, p_stat=2):
    import struct

    record = bytearray(648)
    struct.pack_into("<qi", record, 0, seconds, micros)
    record[36] = p_stat
    return bytes(record)


def _as_darwin(module, monkeypatch, record):
    """Take the macOS branch on any host: no procfs, and this kinfo record."""
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_proc_stat_fields", lambda _pid: None)
    # raising=False: before the fix nothing reads a kinfo record for a start
    # token, and the test must fail on the URL, not on a missing attribute.
    monkeypatch.setattr(
        module, "_darwin_kinfo_proc", lambda _pid: record, raising=False
    )


def test_a_darwin_start_token_is_the_process_start_time(monkeypatch):
    """UPG5-05. macOS has no procfs, so there was no start token at all, and a
    daemon recorded `pid_start: null` -- which `_recorded_endpoint` rightly
    refuses, sending `url` and `status` to the default port."""
    module = _cli_module()
    _as_darwin(module, monkeypatch, _darwin_kinfo_record())

    assert module._process_start_token(4321) == "1789000000.123456"

    monkeypatch.setattr(module, "_darwin_kinfo_proc", lambda _pid: None)
    assert module._process_start_token(4321) is None


def _live_darwin_daemon(tmp_path, *, port=9734):
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        pidfile=tmp_path / "openai4s.pid",
        statefile=tmp_path / "daemon.json",
    )
    config.pidfile.write_text(str(os.getpid()), encoding="utf-8")
    config.statefile.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "pid_start": "1789000000.123456",
                "host": "127.0.0.1",
                "port": port,
            }
        ),
        encoding="utf-8",
    )
    return config


def test_url_on_macos_names_the_port_the_daemon_was_started_on(
    tmp_path, monkeypatch, capsys
):
    from openai4s.server import local_auth

    module = _cli_module()
    config = _live_darwin_daemon(tmp_path)
    token = local_auth.load_or_mint(tmp_path)
    _as_darwin(module, monkeypatch, _darwin_kinfo_record())
    monkeypatch.setattr(module, "get_config", lambda: config)

    assert module.cmd_url(SimpleNamespace()) == 0
    assert capsys.readouterr().out.strip() == (f"http://127.0.0.1:9734/?token={token}")


def test_status_on_macos_probes_the_port_the_daemon_was_started_on(
    tmp_path, monkeypatch, capsys
):
    module = _cli_module()
    config = _live_darwin_daemon(tmp_path)
    _as_darwin(module, monkeypatch, _darwin_kinfo_record())
    monkeypatch.setattr(module, "get_config", lambda: config)
    opened = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"status":"ok","model":"demo"}'

    def open_daemon(request, *, timeout):
        opened.append(request)
        if ":9734/" not in request:
            raise module.urllib.error.URLError("connection refused")
        return Response()

    monkeypatch.setattr(module, "_open_daemon", open_daemon)

    assert module.cmd_status(SimpleNamespace()) == 0
    assert opened == ["http://127.0.0.1:9734/health"]
    assert "at http://127.0.0.1:9734/" in capsys.readouterr().out


def test_a_reused_pid_on_macos_still_falls_back_to_the_callers_config(
    tmp_path, monkeypatch, capsys
):
    """The start token exists to keep a reused pid from steering the token URL
    to whatever the stale record names; the macOS token must do that too."""
    module = _cli_module()
    config = _live_darwin_daemon(tmp_path, port=9734)
    _as_darwin(module, monkeypatch, _darwin_kinfo_record(micros=654_321))
    monkeypatch.setattr(module, "get_config", lambda: config)

    assert module._daemon_alive(config, os.getpid()) is False
    assert module.cmd_url(SimpleNamespace()) == 0
    assert capsys.readouterr().out.strip().startswith("http://127.0.0.1:8760/")


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the real macOS sysctl")
def test_the_darwin_start_token_is_read_from_the_real_kernel():
    """Read from the live kernel, so the record offset is proven rather than
    asserted: stable for this process, different for a child started later."""
    module = _cli_module()

    mine = module._process_start_token(os.getpid())
    assert mine and mine == module._process_start_token(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        theirs = module._process_start_token(child.pid)
        assert theirs and theirs != mine
    finally:
        child.kill()
        child.wait()
    assert module._process_start_token(child.pid) is None


def test_stage1_run_allows_control_only_agent_before_any_readiness_probe(
    tmp_path, monkeypatch, capsys
):
    from openai4s import agent as agent_module
    from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage1_trusted_delivery=True),
    )
    calls: list[tuple[str, object]] = []

    class Agent:
        def __init__(self, *, cfg, verbose, task_mode=None):
            calls.append(("construct", (cfg, verbose, task_mode)))

        def run(self, task):
            calls.append(("run", task))
            return {
                "stop_reason": "submitted",
                "submitted_output": {
                    "output": {"summary": "control-only completion"},
                    "completion_bullets": ["Answered without a science runtime"],
                },
                "final_message": "control-only completion",
            }

    def forbidden_readiness(**_kwargs):
        raise AssertionError("cmd_run probed readiness before action routing")

    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(agent_module, "Agent", Agent)
    monkeypatch.setattr(
        "openai4s.kernel.readiness.standard_profile_readiness",
        forbidden_readiness,
    )

    status = module.cmd_run(
        SimpleNamespace(task="analyze data", json=True, verbose=False)
    )

    assert status == 0
    assert calls == [
        ("construct", (cfg, False, None)),
        ("run", "analyze data"),
    ]
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["stop_reason"] == "submitted"
    assert payload["final_message"] == "control-only completion"


def test_stage1_run_projects_typed_first_cell_readiness_refusal(
    tmp_path, monkeypatch, capsys
):
    from openai4s import agent as agent_module
    from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
    from openai4s.kernel.readiness import EnvironmentReadinessError

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage1_trusted_delivery=True),
    )
    readiness = {
        "state": "needs_repair",
        "ready": False,
        "missing_packages": {"python": ["numpy"], "r": ["r-base"]},
        "missing_environments": [],
        "remediation": {
            "plan_argv": ["openai4s", "env", "plan", "python", "r", "--repair"],
            "apply_argv": ["openai4s", "env", "apply", "python", "r", "--repair"],
        },
    }

    class Agent:
        def __init__(self, *, cfg, verbose, task_mode=None):
            del cfg, verbose, task_mode

        def run(self, task):
            del task
            raise EnvironmentReadinessError(readiness)

    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(agent_module, "Agent", Agent)

    status = module.cmd_run(
        SimpleNamespace(task="run a Cell", json=True, verbose=False)
    )

    assert status == 2
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["code"] == "environment_not_ready"
    assert payload["standard_profile_readiness"] == readiness
    assert "python: numpy" in payload["error"]
    assert "openai4s env apply python r --repair" in payload["error"]


def test_flag_off_run_preserves_agent_execution_without_readiness_probe(
    tmp_path, monkeypatch, capsys
):
    from openai4s import agent as agent_module
    from openai4s.config import Config, LLMConfig

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    calls: list[tuple[str, object]] = []

    class Agent:
        def __init__(self, *, cfg, verbose, task_mode=None):
            calls.append(("construct", (cfg, verbose, task_mode)))

        def run(self, task):
            calls.append(("run", task))
            return {
                "stop_reason": "submitted",
                "submitted_output": None,
                "final_message": "done",
            }

    def forbidden_readiness(**_kwargs):
        raise AssertionError("flag-off CLI performed the Stage 1 readiness probe")

    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(agent_module, "Agent", Agent)
    monkeypatch.setattr(
        "openai4s.kernel.readiness.standard_profile_readiness",
        forbidden_readiness,
    )

    status = module.cmd_run(
        SimpleNamespace(task="legacy task", json=False, verbose=True)
    )

    assert status == 0
    assert calls == [("construct", (cfg, True, None)), ("run", "legacy task")]
    assert "final: done" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stop_reason", ["max_turns", "no_progress", "cancelled", "a_future_reason", None]
)
@pytest.mark.parametrize("as_json", [True, False])
def test_run_that_did_not_complete_exits_non_zero_with_its_result_intact(
    tmp_path, monkeypatch, capsys, stop_reason, as_json
):
    """Only ``submitted`` is a completion, and the exit status must say so.

    Every Agent terminal used to fall through to ``return 0``, so a script
    checking ``$?`` read a run that hit ``max_turns`` or tripped the progress
    circuit as a success while the Action Ledger recorded it as failed. The
    code is not 2 -- that already means a refusal (readiness, a future schema,
    a usage error) -- and an unknown stop reason fails closed.
    """
    from openai4s import agent as agent_module
    from openai4s.config import Config, LLMConfig

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )

    class Agent:
        def __init__(self, *, cfg, verbose, task_mode=None):
            del cfg, verbose, task_mode

        def run(self, task):
            del task
            return {
                "stop_reason": stop_reason,
                "submitted_output": None,
                "final_message": "",
            }

    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(agent_module, "Agent", Agent)

    status = module.cmd_run(
        SimpleNamespace(task="never completes", json=as_json, verbose=False)
    )

    assert status == 3
    assert getattr(module, "RUN_NOT_COMPLETED_EXIT", None) == status
    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out)["stop_reason"] == stop_reason
    else:
        assert f"=== stop_reason: {stop_reason} ===" in out


def _scripted_cli_run(tmp_path, monkeypatch, replies, *, max_turns):
    """Drive the real ``main(["run", ...])`` and real Agent on a scripted chat."""
    import openai4s.agent.loop as loop_mod
    from openai4s.config import Config, LLMConfig

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=max_turns,
    )
    pending = list(replies)

    def chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        return {
            "content": pending.pop(0) if pending else "Still thinking.",
            "reasoning": None,
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            "finish_reason": "stop",
            "raw": {},
        }

    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(loop_mod, "chat", chat)
    return module, cfg


def _only_frame_row(cfg):
    import sqlite3

    with sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT status, input_tokens, output_tokens FROM frames"
        ).fetchall()
    assert len(rows) == 1, rows
    return rows[0]


def test_real_cli_run_hitting_max_turns_exits_3_and_closes_its_frame_failed(
    tmp_path, monkeypatch, capsys
):
    module, cfg = _scripted_cli_run(
        tmp_path, monkeypatch, ["Still thinking."] * 4, max_turns=2
    )

    status = module.main(["run", "--json", "loop until the turn cap"])

    assert status == 3
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "max_turns"
    assert _only_frame_row(cfg) == ("failed", 22, 6)


def test_real_cli_run_that_submits_exits_0_and_closes_its_frame_done(
    tmp_path, monkeypatch, capsys
):
    module, cfg = _scripted_cli_run(
        tmp_path,
        monkeypatch,
        ["```python\nhost.submit_output({'summary': 'ok'}, ['Computed it'])\n```"],
        max_turns=3,
    )

    status = module.main(["run", "--json", "submit once"])

    assert status == 0
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "submitted"
    assert _only_frame_row(cfg) == ("done", 11, 3)


@pytest.mark.parametrize("task", ["", "   ", "\n\t "])
@pytest.mark.parametrize("as_json", [True, False])
def test_a_blank_task_is_refused_before_any_config_store_or_model_call(
    tmp_path, monkeypatch, capsys, task, as_json
):
    """`openai4s run ""` used to spend provider calls on nothing: the model
    answered a capabilities blurb, was nudged, and finalized with exit 0."""

    import openai4s.agent.loop as loop_mod
    from openai4s.config import Config, LLMConfig

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    calls = []
    monkeypatch.setattr(module, "get_config", lambda: calls.append("config") or cfg)
    monkeypatch.setattr(
        loop_mod, "chat", lambda *a, **k: calls.append("chat") or {"content": "hi"}
    )

    argv = ["run", task] + (["--json"] if as_json else [])
    status = module.main(argv)

    captured = capsys.readouterr()
    assert status == 2
    assert calls == []
    assert not cfg.db_path.exists()
    if as_json:
        payload = json.loads(captured.out)
        assert payload["code"] == "empty_task"
        assert "task" in payload["error"]
    else:
        assert captured.err.startswith("error: ")


def test_run_help_documents_the_exit_status_table(capsys):
    with pytest.raises(SystemExit) as stopped:
        _cli_module().main(["run", "--help"])
    assert stopped.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "exit status:" in text
    for fragment in (
        '0 the run completed (stop_reason "submitted")',
        "2 refused",
        "3 the run ended without completing",
    ):
        assert fragment in text


def _run_refusal_codes() -> list[str]:
    """Every literal refusal code `cmd_run` can hand to `_run_refusal`."""

    import inspect
    import re

    source = inspect.getsource(_cli_module().cmd_run)
    return sorted(set(re.findall(r'"code":\s*"([a-z_]+)"', source)))


def test_every_exit_2_refusal_code_is_in_the_documented_exit_table(capsys):
    """The exit table said 2 meant a usage error, readiness or a newer schema,
    while `cmd_run` also refused an empty task and an explicit code mode whose
    test runner nothing could authorize. A new refusal code must be named in
    every place the table is written, or it drifts again."""

    codes = _run_refusal_codes()
    assert {
        "empty_task",
        "invalid_allow_test_command",
        "code_mode_test_runner_unauthorized",
    } <= set(codes)

    with pytest.raises(SystemExit):
        _cli_module().main(["run", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    exit_2 = help_text[help_text.index("2 refused") : help_text.index("3 the run")]

    table_row = next(
        line
        for line in (_REPO / "docs" / "configuration.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("| 2 |")
    )
    cli_dir = _REPO / "openai4s" / "cli"
    readme_rows = {
        name: next(
            line
            for line in (cli_dir / name).read_text(encoding="utf-8").splitlines()
            if line.startswith(prefix)
        )
        for name, prefix in (
            ("README.md", "- `run` exits 0"),
            ("README_zh.md", "- `run` 只有在"),
        )
    }
    for code in codes:
        assert code in exit_2, ("run --help", code)
        assert code in table_row, ("docs/configuration.md", code)
        for name, row in readme_rows.items():
            assert code in row, (name, code)
    # RFD-3: `main()` refuses a database it will not open -- one from a newer
    # release, or one whose upgrade failed and was rolled back -- with exit 2
    # and these codes, and every table that lists the exit-2 refusals says so.
    from openai4s.storage.migrations import FutureSchemaError

    # `migration_failed` is pinned by the --json refusal tests below.
    store_codes = ("future_schema", "migration_failed")
    assert FutureSchemaError.code == store_codes[0]
    for code in store_codes:
        assert code in exit_2, ("run --help", code)
        assert code in table_row, ("docs/configuration.md", code)
        for name, row in readme_rows.items():
            assert code in row, (name, code)
    assert "backup" in exit_2 and "backup" in table_row
    assert (
        "backup" in readme_rows["README.md"] and "备份" in readme_rows["README_zh.md"]
    )


def test_the_cli_readme_halves_carry_the_same_operational_contract():
    """A hand-merge kept both the old Chinese bullet (`status` prints the
    `?token=` URL) and its replacement (it prints the plain origin), so the
    Chinese page contradicted itself, the English page and `cmd_status`."""

    cli_dir = _REPO / "openai4s" / "cli"

    def contract_bullets(name: str, heading: str) -> list[str]:
        text = (cli_dir / name).read_text(encoding="utf-8")
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        return [line for line in section.splitlines() if line.startswith("- ")]

    english = contract_bullets("README.md", "## Operational contract")
    chinese = contract_bullets("README_zh.md", "## 运维契约")
    assert len(english) == len(chinese), (len(english), len(chinese))
    token_bullets = [line for line in chinese if "?token=" in line]
    assert len(token_bullets) == 1, token_bullets
    assert "`serve`、`status`、`url`" not in token_bullets[0]
    assert "`status`" in token_bullets[0] and "不带 token" in token_bullets[0]


def test_the_upgrade_guide_names_the_longer_stop_wait():
    """0.2.x `stop` gave up (or, with --force, sent SIGKILL) after about 5s;
    0.3.0 waits up to --timeout, 30s by default, first."""

    module = _cli_module()
    assert module.STOP_TIMEOUT_S == 30.0
    for name in ("upgrading.md", "upgrading_zh.md"):
        text = " ".join((_REPO / "docs" / name).read_text(encoding="utf-8").split())
        # The section-4 bullet, not the backup step that also says `stop`.
        start = text.index("* **`openai4s stop`")
        window = text[start : start + 700]
        assert "--timeout" in window, name
        assert "30" in window, name
        assert "--force" in window, name

    # UPG5-06. `cmd_stop` prints `shutting down…` only once the SIGTERM grace
    # has passed with the daemon still alive; an idle daemon stops in well
    # under a second and prints only "daemon stopped". "While it waits" read
    # as a line every stop prints.
    grace = f"{module.TERM_GRACE_S:g}"
    english = " ".join((_REPO / "docs" / "upgrading.md").read_text("utf-8").split())
    chinese = " ".join((_REPO / "docs" / "upgrading_zh.md").read_text("utf-8").split())
    stop_en = english[english.index("* **`openai4s stop`") :][:700]
    stop_zh = chinese[chinese.index("* **`openai4s stop`") :][:400]
    assert "prints a `shutting down…` line while it waits" not in stop_en
    assert f"still running after the first {grace}s" in stop_en
    assert "`daemon stopped`" in stop_en
    assert "等待期间打印一行" not in stop_zh
    assert f"过了最初 {grace} 秒仍未退出" in stop_zh
    assert "`daemon stopped`" in stop_zh


def test_daemon_health_ignores_environment_proxies_for_a_wsl_nat_host(monkeypatch):
    module = _cli_module()
    config = SimpleNamespace(host="172.25.100.5", port=8760)
    handlers = []
    opened = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"status":"ok"}'

    class Opener:
        @staticmethod
        def open(request, timeout):
            opened.append((request, timeout))
            return Response()

    def build_opener(*items):
        handlers.extend(items)
        return Opener()

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(module.urllib.request, "build_opener", build_opener)

    assert module._health_ready(config) is True
    assert opened == [("http://172.25.100.5:8760/health", 1)]
    proxy = next(
        item
        for item in handlers
        if isinstance(item, module.urllib.request.ProxyHandler)
    )
    assert proxy.proxies == {}


def test_detached_serve_starts_the_foreground_command_in_a_new_session(
    tmp_path, monkeypatch, capsys
):
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    module = _cli_module()
    logs = tmp_path / "logs"
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        logs_dir=logs,
        pidfile=tmp_path / "daemon.pid",
        ensure_dirs=lambda: logs.mkdir(parents=True, exist_ok=True),
    )
    launched = {}

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        config.pidfile.write_text("4321", encoding="utf-8")
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "_health_ready", lambda _cfg: True)
    monkeypatch.setattr(module, "_url", lambda _cfg: "http://127.0.0.1:8760/?ready")

    args = SimpleNamespace(no_open=True)
    assert module._cmd_serve_detached(args, config) == 0
    assert launched["command"][-1] == "--no-browser"
    assert launched["kwargs"]["start_new_session"] is True
    assert launched["kwargs"]["stdin"] is module.subprocess.DEVNULL
    assert "daemon started (pid 4321)" in capsys.readouterr().out


def test_detached_serve_stops_a_child_that_never_becomes_ready(
    tmp_path, monkeypatch, capsys
):
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    module = _cli_module()
    logs = tmp_path / "logs"
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        logs_dir=logs,
        pidfile=tmp_path / "daemon.pid",
        ensure_dirs=lambda: logs.mkdir(parents=True, exist_ok=True),
    )
    calls = []

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def terminate():
            calls.append("terminate")

        @staticmethod
        def wait(timeout):
            calls.append(("wait", timeout))
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_k: Process())
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), config) == 1
    assert calls == ["terminate", ("wait", 5)]
    assert "did not become ready" in capsys.readouterr().err


def test_detached_serve_ready_timeout_is_overridable(tmp_path, monkeypatch, capsys):
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    module = _cli_module()
    logs = tmp_path / "logs"
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        logs_dir=logs,
        pidfile=tmp_path / "daemon.pid",
        ensure_dirs=lambda: logs.mkdir(parents=True, exist_ok=True),
    )

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def terminate():
            return None

        @staticmethod
        def wait(timeout):
            return 0

    monkeypatch.setenv("OPENAI4S_DETACHED_READY_TIMEOUT", "5")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_k: Process())
    # The second tick is far past 5s but well inside the 60s default: only the
    # override can make the loop give up here.
    ticks = iter((0.0, 6.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), config) == 1
    assert "within 5s" in capsys.readouterr().err


def test_detached_serve_does_not_accept_an_unrelated_healthy_daemon(
    tmp_path, monkeypatch, capsys
):
    import os

    if os.name != "posix":
        pytest.skip("detached server sessions are a POSIX/WSL feature")

    module = _cli_module()
    logs = tmp_path / "logs"
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8760,
        data_dir=tmp_path,
        logs_dir=logs,
        pidfile=tmp_path / "daemon.pid",
        ensure_dirs=lambda: logs.mkdir(parents=True, exist_ok=True),
    )
    polls = iter((None, 98, 98))

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return next(polls)

        @staticmethod
        def terminate():
            raise AssertionError("an exited child must not be signalled")

    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(module, "_health_ready", lambda _cfg: True)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), config) == 1
    output = capsys.readouterr()
    assert "daemon started" not in output.out
    # The child exited while the unrelated daemon answered: that is what is
    # reported, not a readiness wait that never ran out.
    assert "exited with status 98 before it became ready" in output.err


def test_detached_cleanup_escalates_to_kill_and_reaps():
    module = _cli_module()
    calls = []

    class Process:
        @staticmethod
        def poll():
            return None

        @staticmethod
        def terminate():
            calls.append("terminate")

        @staticmethod
        def kill():
            calls.append("kill")

        @staticmethod
        def wait(timeout):
            calls.append(("wait", timeout))
            if calls.count(("wait", timeout)) == 1:
                raise module.subprocess.TimeoutExpired("serve", timeout)
            return 0

    module._cleanup_failed_detached_child(Process())

    assert calls == ["terminate", ("wait", 5), "kill", ("wait", 5)]


def test_detached_cleanup_does_not_signal_an_already_exited_child():
    module = _cli_module()

    class Process:
        @staticmethod
        def poll():
            return 1

        @staticmethod
        def terminate():
            raise AssertionError("an exited child must not be signalled")

    module._cleanup_failed_detached_child(Process())


def test_status_reports_the_local_data_dir_without_trusting_health(monkeypatch, capsys):
    module = _cli_module()
    config = SimpleNamespace(
        host="127.0.0.1",
        port=9876,
        data_dir=Path("/trusted/local-data"),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"status":"ok","model":"demo","data_dir":"/leaked"}'

    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module, "_read_pid", lambda cfg: 123)
    monkeypatch.setattr(module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        module,
        "_open_daemon",
        lambda *args, **kwargs: Response(),
    )

    assert module.cmd_status(SimpleNamespace()) == 0
    output = capsys.readouterr().out
    assert "model    : demo" in output
    assert "data_dir : /trusted/local-data" in output
    assert "/leaked" not in output


def test_status_probes_and_reports_the_live_recorded_endpoint(
    tmp_path, monkeypatch, capsys
):
    module = _cli_module()
    config = _recorded_daemon_config(tmp_path, host="172.25.100.5", port=9123)
    opened = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"status":"ok","model":"demo"}'

    def open_daemon(request, *, timeout):
        opened.append((request, timeout))
        return Response()

    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module, "_daemon_alive", lambda _cfg, _pid: True)
    monkeypatch.setattr(module, "_process_start_token", lambda _pid: "daemon-start")
    monkeypatch.setattr(module, "_open_daemon", open_daemon)

    assert module.cmd_status(SimpleNamespace()) == 0
    assert opened == [("http://172.25.100.5:9123/health", 3)]
    output = capsys.readouterr().out
    assert "at http://172.25.100.5:9123/" in output
    assert "127.0.0.1:8760" not in output


def test_status_never_prints_the_access_token_and_points_at_url(
    tmp_path, monkeypatch, capsys
):
    """`status` is a health question, and its output lands in CI and support logs.

    It printed `http://host:port/?token=<the full access token>` with no flag,
    while the release pipeline treats that very bootstrap URL as a credential
    that must not reach a log. The tokenized URL is what `openai4s url` is for.
    """
    from openai4s.server import local_auth

    module = _cli_module()
    config = _recorded_daemon_config(tmp_path, host="127.0.0.1", port=9222)
    token = local_auth.load_or_mint(tmp_path)
    assert token and local_auth.read_token(tmp_path) == token

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"status":"ok","model":"demo"}'

    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(module, "_daemon_alive", lambda _cfg, _pid: True)
    monkeypatch.setattr(module, "_process_start_token", lambda _pid: "daemon-start")
    monkeypatch.setattr(module, "_open_daemon", lambda *_a, **_k: Response())

    assert module.cmd_status(SimpleNamespace()) == 0
    output = capsys.readouterr().out
    assert token not in output
    assert "token=" not in output
    assert "daemon: running (pid 4321) at http://127.0.0.1:9222/\n" in output
    assert "openai4s url" in output

    # The command that exists to hand a person the working URL still does.
    assert module.main(["url"]) == 0
    assert capsys.readouterr().out.strip() == (f"http://127.0.0.1:9222/?token={token}")


@pytest.mark.skipif(os.name != "posix", reason="SIGINT dispositions are POSIX")
def test_ctrl_c_during_a_run_stops_this_agent_s_cell_and_still_exits():
    """Both halves of what a terminal Ctrl-C used to do, restored.

    The kernel worker now runs in its own session, so a group-wide SIGINT no
    longer reaches it. That is the point -- a stray Ctrl-C must not end every
    cell a daemon is running -- and it takes something real away from the CLI,
    where `Agent.run` executes cells on this very thread: before, the signal
    reached both this process (whose default handler raised KeyboardInterrupt
    out of the run) and the worker (whose handler ended the cell). Restoring
    one half without the other would be a behaviour change arriving through a
    signal handler.
    """

    cli = _cli_module()
    calls = []
    agent = SimpleNamespace(
        interrupt_foreground=lambda: (calls.append("interrupt"), True)[1]
    )

    before = signal.getsignal(signal.SIGINT)
    with cli._foreground_cell_interrupt(agent):
        handler = signal.getsignal(signal.SIGINT)
        assert handler is not before, "no handler was installed"
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)

    assert calls == ["interrupt"], "the running cell was not interrupted"
    assert signal.getsignal(signal.SIGINT) is before, "the disposition leaked"


@pytest.mark.skipif(os.name != "posix", reason="SIGINT dispositions are POSIX")
@pytest.mark.parametrize(
    "interrupt_foreground",
    [
        pytest.param(lambda: False, id="nothing_to_interrupt"),
        pytest.param(
            lambda: (_ for _ in ()).throw(RuntimeError("worker gone")),
            id="interrupt_raises",
        ),
    ],
)
def test_ctrl_c_exits_even_when_the_cell_cannot_be_interrupted(interrupt_foreground):
    """The exit is unconditional. A run that survived Ctrl-C because no worker
    happened to be up, or because interrupting one raised, would be a new
    behaviour nobody asked for -- and the user's second Ctrl-C would be their
    only way out."""

    cli = _cli_module()
    agent = SimpleNamespace(interrupt_foreground=interrupt_foreground)

    before = signal.getsignal(signal.SIGINT)
    with cli._foreground_cell_interrupt(agent):
        handler = signal.getsignal(signal.SIGINT)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) is before


@pytest.mark.parametrize("detached", [False, True])
def test_serve_future_schema_fails_before_directories_singleton_or_spawn(
    tmp_path, monkeypatch, capsys, detached
):
    import sqlite3

    import openai4s.config as config_module
    from openai4s.storage.migrations import SCHEMA_VERSION

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = config_module.Config()
    with sqlite3.connect(cfg.db_path) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    calls = []
    monkeypatch.setattr(
        config_module.Config, "ensure_dirs", lambda *_: calls.append("dirs")
    )
    monkeypatch.setattr(
        module, "_acquire_singleton", lambda *_: calls.append("singleton")
    )
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *_a, **_kw: calls.append("spawn")
    )
    assert module.cmd_serve(SimpleNamespace(detached=detached)) == 2
    assert calls == []
    assert config_module._CONFIG is None
    assert "future_schema" in capsys.readouterr().err


def test_serve_formal_open_future_schema_error_clears_only_owned_state(
    tmp_path, monkeypatch, capsys
):
    import openai4s.config as config_module
    import openai4s.server as server
    from openai4s.storage.migrations import SCHEMA_VERSION, FutureSchemaError

    module = _cli_module()
    cfg = config_module.Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    monkeypatch.setattr(module, "get_config", lambda **_: cfg)
    monkeypatch.setattr(module, "_acquire_singleton", lambda *_: True)
    cleared = []
    monkeypatch.setattr(module, "_clear_state", lambda _cfg, **kw: cleared.append(kw))

    def reject(_cfg):
        raise FutureSchemaError(SCHEMA_VERSION + 1, SCHEMA_VERSION)

    monkeypatch.setattr(server, "build_server", reject)
    assert module.cmd_serve(SimpleNamespace(detached=False, host=None, port=None)) == 2
    assert cleared == [{"only_if_owned_by": os.getpid()}]
    assert "future_schema" in capsys.readouterr().err


def _older_store_whose_upgrade_fails(data_dir, monkeypatch):
    """A real v31 database whose step-32 migration fails, as a broken upgrade
    does: the Store rolls back, keeps the backup, and raises MigrationError."""
    import sqlite3

    from openai4s.store import Store

    db = Path(data_dir) / "openai4s.db"
    Store(db).close()
    with sqlite3.connect(str(db)) as conn:
        conn.execute("DROP INDEX IF EXISTS ix_artifacts_project_created")
        conn.execute("DELETE FROM schema_migrations WHERE version>=32")
        conn.execute("PRAGMA user_version = 31")

    def fail(_self, _connection):
        raise RuntimeError("no such column: profile_id")

    monkeypatch.setattr(Store, "_apply_artifact_browse_index", fail)
    return db


def test_serve_reports_a_failed_upgrade_in_one_line_naming_the_backup(
    tmp_path, monkeypatch, capsys
):
    """UPG3-04. The refusal for a *newer* database was one line and exit 2; a
    failed upgrade of an *older* one escaped as a ~45-line traceback whose only
    useful sentence -- rolled back, and where the backup is -- came last."""
    import openai4s.config as config_module

    module = _cli_module()
    cfg = config_module.Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    db = _older_store_whose_upgrade_fails(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "get_config", lambda **_: cfg)
    monkeypatch.setattr(module, "_acquire_singleton", lambda *_: True)
    cleared = []
    monkeypatch.setattr(module, "_clear_state", lambda _cfg, **kw: cleared.append(kw))

    assert module.cmd_serve(SimpleNamespace(detached=False, host=None, port=None)) == 2

    err = capsys.readouterr().err
    assert "Traceback" not in err
    # Startup may print its machine-dependent package notices first; the
    # failure itself is one line, and the last.
    errors = [line for line in err.splitlines() if line.startswith("error:")]
    assert len(errors) == 1, err
    assert err.rstrip().splitlines()[-1] == errors[0]
    assert errors[0].startswith("error: migration to version")
    assert "rolled back and remains at version 31" in errors[0]
    backup = db.with_name("openai4s.db.v31.bak")
    assert backup.exists() and str(backup) in errors[0]
    assert cleared == [{"only_if_owned_by": os.getpid()}]


def test_run_reports_a_failed_upgrade_in_one_line_naming_the_backup(
    tmp_path, monkeypatch, capsys
):
    """The same failure from `openai4s run`, through the real entry point."""
    import openai4s.config as config_module

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    config_module.Config().ensure_dirs()
    db = _older_store_whose_upgrade_fails(tmp_path, monkeypatch)

    assert module.main(["run", "say hello"]) == 2

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "error: migration to version" in err
    assert str(db.with_name("openai4s.db.v31.bak")) in err


def test_detached_child_future_schema_is_reported_without_startup_timeout(
    tmp_path, monkeypatch, capsys
):
    from openai4s.config import Config
    from openai4s.storage.migrations import SCHEMA_VERSION, FutureSchemaError

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    log = cfg.logs_dir / "app.out"
    log.write_text("old log text must not be returned\n")

    class Process:
        pid = 4321

        def poll(self):
            return 2

    def spawn(_command, **kwargs):
        diagnostic = FutureSchemaError(SCHEMA_VERSION + 1, SCHEMA_VERSION)
        kwargs["stdout"].write(
            f"error: {diagnostic}\nprivate diagnostic tail\n".encode()
        )
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 2
    error = capsys.readouterr().err
    assert "future_schema" in error and str(SCHEMA_VERSION + 1) in error
    assert "did not become ready" not in error
    assert "private diagnostic" not in error and "old log" not in error


@pytest.mark.skipif(os.name != "posix", reason="detached sessions require POSIX")
def test_real_detached_child_rejects_future_schema_with_explicit_parent_error(
    tmp_path, monkeypatch, capsys
):
    import sqlite3

    from openai4s.config import Config
    from openai4s.storage.migrations import SCHEMA_VERSION

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")
    with sqlite3.connect(cfg.db_path) as conn:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = cfg.db_path.read_bytes()
    # Exercise the actual spawned foreground command. This bypasses only the
    # parent's already-tested preflight, as a version race would do.
    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 2
    error = capsys.readouterr().err
    assert "future_schema" in error and "did not become ready" not in error
    assert cfg.db_path.read_bytes() == before
    assert not cfg.pidfile.exists() and not cfg.statefile.exists()


def _detached_child_that_exits(module, monkeypatch, *, output: str, status: int):
    """A detached child that writes ``output`` to its log and has exited."""

    class Process:
        pid = 4321

        def poll(self):
            return status

        def terminate(self):
            raise AssertionError("an exited child must not be signalled")

    def spawn(_command, **kwargs):
        kwargs["stdout"].write(output.encode())
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)


def test_detached_child_failed_upgrade_is_reported_in_one_line_naming_the_backup(
    tmp_path, monkeypatch, capsys
):
    """LR4-1 / UPG5-04. The foreground `serve` answers a failed upgrade with
    one `error:` line naming the kept backup and exit 2. The detached parent
    recognised only `future_schema`, so the same child -- dead after a second
    -- was reported as "did not become ready within 60s" with exit 1."""
    from openai4s.config import Config

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    (cfg.logs_dir / "app.out").write_text("old log text must not be returned\n")
    backup = cfg.db_path.with_name("openai4s.db.v27.bak")
    backup.write_bytes(b"kept")
    _detached_child_that_exits(
        module,
        monkeypatch,
        status=2,
        output=(
            "some startup notice\n"
            "error: migration to version 32 failed at step 32: there is already "
            "a table named ix_artifacts_project_created. The database was rolled "
            "back and remains at version 27; re-running is safe. A pre-upgrade "
            "backup is at /somewhere/else/openai4s.db.v27.bak.\n"
            "private diagnostic tail\n"
        ),
    )

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 2

    err = capsys.readouterr().err
    errors = [line for line in err.splitlines() if line.startswith("error:")]
    assert len(errors) == 1, err
    assert errors[0].startswith("error: migration to version 32 failed at step 32")
    assert "rolled back and remains at version 27" in errors[0]
    assert str(backup) in errors[0]
    assert str(cfg.logs_dir / "app.out") in errors[0]
    assert "did not become ready" not in err
    # Built from the numbers, never echoed: not the SQLite detail, not a path
    # the log claims, not the rest of the log.
    assert "already a table" not in err and "/somewhere/else" not in err
    assert "private diagnostic" not in err and "old log" not in err


def test_detached_child_failed_upgrade_names_no_backup_that_does_not_exist(
    tmp_path, monkeypatch, capsys
):
    from openai4s.config import Config

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    _detached_child_that_exits(
        module,
        monkeypatch,
        status=2,
        output=(
            "error: migration to version 32 failed at step 30: disk I/O error. "
            "The database was rolled back and remains at version 29; re-running "
            "is safe.\n"
        ),
    )

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 2

    err = capsys.readouterr().err
    assert "error: migration to version 32 failed at step 30" in err
    assert "backup" not in err


def test_detached_child_that_cannot_bind_is_reported_with_its_own_line(
    tmp_path, monkeypatch, capsys
):
    import errno

    from openai4s.config import Config

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    line = module._bind_failure_message(OSError(errno.EADDRINUSE, "in use"), cfg)
    _detached_child_that_exits(
        module, monkeypatch, status=1, output=f"{line}\nprivate diagnostic tail\n"
    )

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 1

    err = capsys.readouterr().err
    assert line in err
    assert "did not become ready" not in err and "private diagnostic" not in err


@pytest.mark.parametrize(("status", "expected"), [(1, 1), (2, 2), (-9, 1)])
def test_detached_child_that_exits_early_is_not_reported_as_a_timeout(
    tmp_path, monkeypatch, capsys, status, expected
):
    """An unrecognised early exit names the exit and the log, never a wait
    that did not happen; a refusal's status 2 stays 2."""
    from openai4s.config import Config

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    _detached_child_that_exits(
        module, monkeypatch, status=status, output="private diagnostic tail\n"
    )

    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == expected

    err = capsys.readouterr().err
    assert "within 60s" not in err and "did not become ready" not in err
    assert "before it became ready" in err
    assert str(cfg.logs_dir / "app.out") in err
    assert "private diagnostic" not in err


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _v31_database_whose_upgrade_fails_in_any_process(db_path: Path) -> None:
    """A real v31 database that step 32 cannot upgrade in *any* process: the
    name its index needs is taken by a table, so no monkeypatch is involved
    and a spawned child fails the same way."""
    import sqlite3

    from openai4s.store import Store

    Store(db_path).close()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DROP INDEX IF EXISTS ix_artifacts_project_created")
        conn.execute("CREATE TABLE ix_artifacts_project_created(x)")
        conn.execute("DELETE FROM schema_migrations WHERE version>=32")
        conn.execute("PRAGMA user_version = 31")


@pytest.mark.skipif(os.name != "posix", reason="detached sessions require POSIX")
def test_real_detached_child_failed_upgrade_names_the_backup_and_exits_2(
    tmp_path, monkeypatch, capsys
):
    import sqlite3
    import time

    from openai4s.config import Config

    module = _cli_module()
    cfg = Config(data_dir=tmp_path)
    cfg.port = _free_port()
    cfg.ensure_dirs()
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI4S_NO_OPEN", "1")
    monkeypatch.setenv("OPENAI4S_DETACHED_READY_TIMEOUT", "30")
    _v31_database_whose_upgrade_fails_in_any_process(cfg.db_path)

    started = time.monotonic()
    assert module._cmd_serve_detached(SimpleNamespace(no_open=True), cfg) == 2
    assert time.monotonic() - started < 30

    err = capsys.readouterr().err
    backup = cfg.db_path.with_name("openai4s.db.v31.bak")
    assert backup.exists()
    assert "error: migration to version" in err and str(backup) in err
    assert "did not become ready" not in err and "already a table" not in err
    with sqlite3.connect(str(cfg.db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 31
    assert not cfg.pidfile.exists() and not cfg.statefile.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "hello", "--json"],
        ["init", "--non-interactive", "--json"],
        ["user", "list", "--json"],
    ],
)
def test_a_future_schema_refusal_prints_its_code_under_json(
    tmp_path, monkeypatch, capsys, argv
):
    """RFD-2. The exit-2 tables say `--json` prints the refusal's code. The
    Store refusals are raised inside the Store open and reach `main()`, which
    wrote only the stderr line: a script calling json.loads on stdout got an
    empty string for exactly this refusal."""
    import sqlite3

    import openai4s.config as config_module
    from openai4s.storage.migrations import SCHEMA_VERSION

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = config_module.Config()
    cfg.ensure_dirs()
    with sqlite3.connect(cfg.db_path) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = cfg.db_path.read_bytes()

    assert module.main(argv) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["code"] == "future_schema"
    assert f"database schema {SCHEMA_VERSION + 1}" in payload["error"]
    # The operator's one line stays where the upgrade guide says it is.
    assert "error: [future_schema]" in captured.err
    assert "Traceback" not in captured.err
    assert cfg.db_path.read_bytes() == before


def test_a_failed_upgrade_refusal_prints_its_code_under_json(
    tmp_path, monkeypatch, capsys
):
    import openai4s.config as config_module

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    config_module.Config().ensure_dirs()
    db = _older_store_whose_upgrade_fails(tmp_path, monkeypatch)

    assert module.main(["run", "say hello", "--json"]) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["code"] == "migration_failed"
    assert payload["error"].startswith("migration to version")
    assert str(db.with_name("openai4s.db.v31.bak")) in payload["error"]
    assert "error: migration to version" in captured.err


def test_a_store_refusal_without_json_prints_nothing_on_stdout(
    tmp_path, monkeypatch, capsys
):
    import openai4s.config as config_module

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    config_module.Config().ensure_dirs()
    _older_store_whose_upgrade_fails(tmp_path, monkeypatch)

    assert module.main(["run", "say hello"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: migration to version" in captured.err


@pytest.mark.parametrize(
    "argv", [["user", "list"], ["init", "--non-interactive", "--json"]]
)
def test_store_opening_commands_refuse_a_future_schema_without_a_traceback(
    tmp_path, monkeypatch, capsys, argv
):
    """`serve` already rendered the refusal; every other Store opener crashed."""
    import sqlite3

    import openai4s.config as config_module
    from openai4s.storage.migrations import SCHEMA_VERSION

    module = _cli_module()
    monkeypatch.setattr(config_module, "_CONFIG", None)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = config_module.Config()
    cfg.ensure_dirs()
    with sqlite3.connect(cfg.db_path) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = cfg.db_path.read_bytes()
    assert module.main(argv) == 2
    captured = capsys.readouterr()
    assert "future_schema" in captured.err
    assert "Traceback" not in captured.err
    assert cfg.db_path.read_bytes() == before
