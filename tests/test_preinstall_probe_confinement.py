"""The provenance freeze probe launches a foreign interpreter, so confine it.

``freeze_for`` runs a *selected environment's* interpreter to read its package
list — and for an artifact from a sandboxed kernel, that interpreter and its
startup hooks are attacker-influenced. `-I` only isolates Python's own path and
environment handling; it does nothing to stop the executable, a `.pth` file, or
a sitecustomize hook from reading the daemon's credentials out of the
environment, touching daemon files, or reaching the network. The probe must run
under the same scrubbed child environment and OS boundary a kernel cell gets.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from openai4s.kernel import preinstall


def test_the_probe_environment_carries_no_daemon_credentials(monkeypatch):
    """The scrubbed child env is what removes the credential vector, whether or
    not an OS sandbox is available."""
    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", "sk-secret-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-should-not-leak")

    argv, env, sandbox = preinstall._confined_probe(
        [sys.executable, "-I", "-c", "pass"], os.getcwd()
    )
    try:
        assert "OPENAI4S_CLAUDE_API_KEY" not in env, env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        for key, value in env.items():
            assert "should-not-leak" not in str(value), key
    finally:
        if sandbox is not None:
            sandbox.close()


def test_a_foreign_interpreter_probe_cannot_read_a_daemon_secret(tmp_path, monkeypatch):
    """End to end: a real secret in the daemon env must not be visible to the
    probe the daemon launches to freeze another interpreter."""
    monkeypatch.setenv("OPENAI4S_CLAUDE_API_KEY", "sk-live-secret")

    # A fake "foreign" interpreter: a wrapper that prints the environment as if
    # it were the freeze output. It stands in for a hostile startup hook.
    spy = tmp_path / "bin" / "python"
    spy.parent.mkdir(parents=True)
    spy.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os
            leaked = {k: v for k, v in os.environ.items() if "secret" in v.lower()}
            print(json.dumps([{"name": k, "version": "1"} for k in leaked]))
            """),
        encoding="utf-8",
    )
    spy.chmod(0o755)

    result = preinstall.freeze_for(str(spy), timeout=30)
    # The spy printed only env entries whose value contained the secret; if the
    # scrub worked there are none, so the freeze is an empty list.
    assert (
        result == []
    ), f"the probe leaked a daemon secret to a foreign interpreter: {result}"


def test_the_probe_still_freezes_a_normal_interpreter(tmp_path):
    """The confinement must not break the legitimate case."""
    spy = tmp_path / "bin" / "python"
    spy.parent.mkdir(parents=True)
    spy.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        'print(json.dumps([{"name": "numpy", "version": "1.26.0"}]))\n',
        encoding="utf-8",
    )
    spy.chmod(0o755)

    result = preinstall.freeze_for(str(spy), timeout=30)
    assert result == [{"name": "numpy", "version": "1.26.0"}]


def test_the_probe_source_no_longer_runs_the_interpreter_bare():
    """A guard against a regression that drops the confinement wiring."""
    source = Path(preinstall.__file__).read_text("utf-8")
    assert "_confined_probe(" in source
    assert "build_kernel_environment" in source
    assert "create_kernel_sandbox" in source


def test_enforce_mode_fails_closed_when_no_boundary_can_be_built(monkeypatch, tmp_path):
    """Under enforce, a probe whose OS boundary cannot be established must not
    silently launch the foreign interpreter unconfined."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "enforce")

    from openai4s.security import sandbox as sandbox_mod

    def refuse(*_a, **_k):
        raise sandbox_mod.SandboxUnavailableError("no backend on this host")

    monkeypatch.setattr(sandbox_mod, "create_kernel_sandbox", refuse)

    with pytest.raises(Exception) as error:
        preinstall.run_confined_probe([sys.executable, "-c", "pass"], timeout=10)
    assert "backend" in str(error.value) or "boundary" in str(error.value).lower()


def test_enforce_failure_does_not_leak_the_probe_workspace(monkeypatch):
    """When `_confined_probe` raises under enforce, the temp workspace created a
    line earlier must still be removed. Building the probe outside the try/finally
    leaked one `openai4s-probe-*` dir per probe on every enforce host without a
    working backend — a common CI/hardened configuration."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "enforce")

    from openai4s.security import sandbox as sandbox_mod

    monkeypatch.setattr(
        sandbox_mod,
        "create_kernel_sandbox",
        lambda *_a, **_k: (_ for _ in ()).throw(
            sandbox_mod.SandboxUnavailableError("no backend on this host")
        ),
    )

    created: list[str] = []
    real_mkdtemp = preinstall.tempfile.mkdtemp

    def recording_mkdtemp(*a, **k):
        path = real_mkdtemp(*a, **k)
        created.append(path)
        return path

    monkeypatch.setattr(preinstall.tempfile, "mkdtemp", recording_mkdtemp)

    with pytest.raises(Exception):
        preinstall.run_confined_probe([sys.executable, "-c", "pass"], timeout=10)

    assert created, "the probe never created its workspace"
    for path in created:
        assert not os.path.exists(path), f"leaked probe workspace: {path}"


def test_a_malformed_sandbox_config_propagates_even_under_auto(monkeypatch):
    """A misconfiguration is not an availability failure. Under `auto`, a missing
    backend degrades to the scrubbed env, but a bad *setting* (a typo in
    OPENAI4S_KERNEL_ALLOW_RAW_NETWORK) must fail closed — never silently launch
    the foreign interpreter unconfined behind the typo."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "auto")

    from openai4s.security import sandbox as sandbox_mod

    def misconfigured(*_a, **_k):
        raise sandbox_mod.SandboxConfigurationError(
            "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK must be one of: true, false"
        )

    monkeypatch.setattr(sandbox_mod, "create_kernel_sandbox", misconfigured)

    with pytest.raises(sandbox_mod.SandboxConfigurationError):
        preinstall.run_confined_probe([sys.executable, "-c", "pass"], timeout=10)


def test_auto_mode_still_runs_when_no_boundary_is_available(monkeypatch):
    """The degrade path: `auto` runs the probe with the scrubbed env even when
    no OS boundary can be built."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "auto")

    from openai4s.security import sandbox as sandbox_mod

    monkeypatch.setattr(
        sandbox_mod,
        "create_kernel_sandbox",
        lambda *_a, **_k: (_ for _ in ()).throw(
            sandbox_mod.SandboxUnavailableError("none")
        ),
    )
    proc = preinstall.run_confined_probe(
        [sys.executable, "-c", "print('ok')"], timeout=10
    )
    assert proc.returncode == 0
    assert b"ok" in proc.stdout


def test_listing_a_foreign_env_probes_its_version_confined(monkeypatch):
    """Codex P1: `Environment.python_version()` started a *foreign* interpreter
    with an unsandboxed subprocess, so a generation's malicious `.pth` or
    sitecustomize ran on merely listing environments. A foreign interpreter must
    go through the confined probe; our own is read in-process."""
    from openai4s.kernel.environments import Environment

    calls: list[list[str]] = []

    def fake_probe(argv, *, timeout):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"3.11.7\n", b"")

    monkeypatch.setattr("openai4s.kernel.preinstall.run_confined_probe", fake_probe)
    # A raw subprocess.run of a foreign interpreter would be the bug.
    monkeypatch.setattr(
        "openai4s.kernel.environments.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("a foreign interpreter was run unconfined")
        ),
    )

    foreign = Environment(
        name="gen",
        language="python",
        root=Path("/opt/generations/env-x/prefix"),
        python="/opt/generations/env-x/prefix/bin/python",
        rscript=None,
    )
    assert foreign.python_version() == "3.11.7"
    assert calls, "the foreign interpreter was not probed through the confined path"
    assert calls[0][0] == "/opt/generations/env-x/prefix/bin/python"


def test_listing_our_own_env_reads_the_version_in_process(monkeypatch):
    """The daemon's own interpreter is trusted and read without any subprocess —
    neither a raw run nor a confined probe."""
    from openai4s.kernel.environments import Environment

    monkeypatch.setattr(
        "openai4s.kernel.preinstall.run_confined_probe",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("our own interpreter should not be probed")
        ),
    )
    ours = Environment(
        name="base",
        language="python",
        root=Path(sys.prefix),
        python=sys.executable,
        rscript=None,
        is_conda=False,
    )
    import platform

    assert ours.python_version() == platform.python_version()


def test_a_virtualenv_is_not_this_interpreter(tmp_path):
    """A venv symlinks the base python but selects a different prefix. Treating
    it as this process would freeze the daemon's packages under its id."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    assert not preinstall._is_this_interpreter(str(venv / "bin" / "python")), (
        "a venv shares the base executable but not the prefix, so it is a "
        "distinct interpreter"
    )
    # ...and this process's own interpreter still matches itself.
    assert preinstall._is_this_interpreter(sys.executable)


def test_a_probe_runs_in_its_own_empty_directory_not_the_daemons(tmp_path, monkeypatch):
    """`python -c` without `-I` puts the working directory first on `sys.path`.

    The probe used to inherit the daemon's launch directory -- a directory a
    CLI kernel may write -- so a `json.py` or `platform.py` there answered for
    the stdlib in a version probe or the font-list builder. The probe's cwd is
    the private, empty workspace it creates for itself.
    """

    daemon_cwd = tmp_path / "daemon-cwd"
    daemon_cwd.mkdir()
    (daemon_cwd / "platform.py").write_text(
        "def python_version():\n    return 'forged'\n", encoding="utf-8"
    )
    monkeypatch.chdir(daemon_cwd)

    completed = preinstall.run_confined_probe(
        [
            sys.executable,
            "-c",
            "import json, os, platform\n"
            "print(json.dumps([os.getcwd(), sorted(os.listdir('.')),"
            " platform.python_version()]))",
        ],
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    cwd, entries, version = json.loads(completed.stdout.decode("utf-8"))
    assert os.path.realpath(cwd) != os.path.realpath(daemon_cwd)
    assert os.path.basename(cwd).startswith("openai4s-probe-"), cwd
    assert entries == []
    assert version != "forged"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: A child that leaves its parent's process group, as macOS `system_profiler`'s
#: scan helper does, then writes its pid to the file named by its argument.
_ESCAPED_CHILD = (
    "import os, sys, time; os.setpgid(0, 0); "
    "handle = open(sys.argv[1] + '.tmp', 'w'); handle.write(str(os.getpid())); "
    "handle.close(); os.replace(sys.argv[1] + '.tmp', sys.argv[1]); time.sleep(120)"
)


def _probe_with_an_escaped_child(ready: Path) -> str:
    """A probe program whose work runs in a child that left its process group.

    That is the shape of macOS `system_profiler`, which matplotlib's font
    search runs: it re-spawns itself as a helper in a group of its own, and the
    helper holds the probe's stderr open while it scans.
    """

    return (
        "import subprocess, sys, time\n"
        f"ready = {str(ready)!r}\n"
        f"child = subprocess.Popen([sys.executable, '-c', {_ESCAPED_CHILD!r}, ready])\n"
        "while True:\n"
        "    try:\n"
        "        open(ready).close()\n"
        "        break\n"
        "    except OSError:\n"
        "        time.sleep(0.01)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )


def test_descendants_are_read_from_proc_by_parent_link(monkeypatch, tmp_path):
    """The Linux branch, which a Mac never takes, against a stand-in `/proc`."""

    proc = tmp_path / "proc"
    stats = {
        "self": "7 (python) S 1 7 7",
        "10": "10 (python) S 1 10 10",
        "20": "20 (system_profiler) S 10 10 10",
        # A helper that left the group; `comm` may hold spaces and parens.
        "30": "30 (scan (helper) x) S 20 30 30",
        "40": "40 (unrelated) S 1 40 40",
        "50": "50 (torn",
    }
    for name, line in stats.items():
        (proc / name).mkdir(parents=True)
        (proc / name / "stat").write_text(line + "\n", encoding="ascii")
    (proc / "sys").mkdir()
    monkeypatch.setattr(preinstall, "_PROC_ROOT", str(proc))
    assert sorted(preinstall._descendant_pids(10)) == [20, 30]
    assert preinstall._descendant_pids(40) == []


def _kill_if_alive(pid: int | None) -> None:
    if pid is not None and _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")
def test_a_probe_that_times_out_is_stopped_with_everything_it_started(
    monkeypatch, tmp_path
):
    """A timeout ends the probe's whole tree, not only its leader.

    `subprocess.run` killed only the interpreter on a timeout, so a hung scan's
    helper ran on unowned after the probe had given up on it.
    """

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    program = _probe_with_an_escaped_child(tmp_path / "ready")
    child = None
    try:
        with pytest.raises(subprocess.TimeoutExpired) as timed_out:
            preinstall.run_confined_probe([sys.executable, "-c", program], timeout=5)
        output = timed_out.value.stdout or b""
        assert output.strip(), "the probe never reported its child"
        child = int(output.split()[0])
        deadline = time.monotonic() + 5
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(child), "the probe's child outlived its timeout"
    finally:
        _kill_if_alive(child)


def test_a_stop_before_the_probe_starts_runs_nothing(monkeypatch, tmp_path):
    """A build shutdown reached before its spawn starts no process at all."""

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    monkeypatch.setattr(preinstall.tempfile, "tempdir", str(tmp_path))
    spawned: list = []
    real_popen = preinstall.subprocess.Popen

    def recording_popen(*args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(preinstall.subprocess, "Popen", recording_popen)
    stop = threading.Event()
    stop.set()
    with pytest.raises(preinstall.ProbeStopped):
        preinstall.run_confined_probe(
            [sys.executable, "-c", "pass"], timeout=60, stop=stop
        )
    assert spawned == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")
def test_a_stopped_probe_ends_at_once_with_everything_it_started(monkeypatch, tmp_path):
    """How the daemon ends its font-list build at shutdown, from another thread.

    The probe must not wait for a pipe the escaped helper still holds: that
    wait is what kept the build thread -- and so the removal of its workspace
    -- running past the daemon's exit.
    """

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(preinstall.tempfile, "tempdir", str(temp))
    ready = tmp_path / "ready"
    stop = threading.Event()
    outcome: list = []

    def probe() -> None:
        try:
            preinstall.run_confined_probe(
                [sys.executable, "-c", _probe_with_an_escaped_child(ready)],
                timeout=120,
                stop=stop,
            )
        except BaseException as error:  # noqa: BLE001 - reported to the test
            outcome.append(error)
        else:
            outcome.append(None)

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    helper = None
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "the probe never started its helper"
        stopped_at = time.monotonic()
        stop.set()
        worker.join(10)
        assert not worker.is_alive(), "the stopped probe kept waiting"
        assert time.monotonic() - stopped_at < 5
        assert len(outcome) == 1 and isinstance(outcome[0], preinstall.ProbeStopped)
        assert list(temp.iterdir()) == []
        helper = int(ready.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while _alive(helper) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(helper), "the probe's helper outlived the stop"
    finally:
        stop.set()
        _kill_if_alive(helper)
        worker.join(10)
