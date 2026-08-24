"""Offline characterization of the supported ``openai4s`` CLI surface."""

from __future__ import annotations

import importlib
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
        def __init__(self, *, cfg, verbose):
            calls.append(("construct", (cfg, verbose)))

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
        ("construct", (cfg, False)),
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
        def __init__(self, *, cfg, verbose):
            del cfg, verbose

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
        def __init__(self, *, cfg, verbose):
            calls.append(("construct", (cfg, verbose)))

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
    assert calls == [("construct", (cfg, True)), ("run", "legacy task")]
    assert "final: done" in capsys.readouterr().out


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
    assert "did not become ready" in output.err


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
