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
import sys
import textwrap
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
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, os
            leaked = {k: v for k, v in os.environ.items() if "secret" in v.lower()}
            print(json.dumps([{"name": k, "version": "1"} for k in leaked]))
            """
        ),
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
