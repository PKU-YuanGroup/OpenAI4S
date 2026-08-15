"""Offline characterization of the supported ``openai4s`` CLI surface."""

from __future__ import annotations

import importlib
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
