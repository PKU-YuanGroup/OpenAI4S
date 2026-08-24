"""On-demand installs must work in a pip-less, uv-managed environment.

`setup.sh` builds the daemon's own `.venv` with uv, and uv virtualenvs ship
without a `pip` module by default. `_pip_install` unconditionally ran
``sys.executable -m pip install …``, so on such an interpreter every
``host.env.create(...)`` failed with "No module named pip" — with a perfectly
capable `uv` binary sitting on PATH. Detection and fallback:

* pip importable → keep the historical ``python -m pip`` command exactly;
* no pip, uv on PATH → ``uv pip install --python <sys.executable> …``, which
  targets the same interpreter the kernel runs under;
* neither → fail with a message that names both remedies, instead of the bare
  interpreter error.

A tool-selection failure must not silently switch tools: when pip exists but
the install itself fails (network, resolver), the result stays a pip failure.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from openai4s.kernel import preinstall


class SpyRun:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.commands: list[list[str]] = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        return SimpleNamespace(
            returncode=self._returncode, stdout="", stderr=self._stderr
        )


def test_pip_present_keeps_the_historical_command(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: True)
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, _log = preinstall._pip_install(["tabulate"])

    assert ok is True
    assert spy.commands == [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--break-system-packages",
            "--disable-pip-version-check",
            "--no-input",
            "tabulate",
        ]
    ]


def test_missing_pip_falls_back_to_uv(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: False)
    monkeypatch.setattr(
        preinstall.shutil, "which", lambda name: "/opt/uv" if name == "uv" else None
    )
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, _log = preinstall._pip_install(["pandas", "statsmodels"])

    assert ok is True
    assert spy.commands == [
        [
            "/opt/uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "pandas",
            "statsmodels",
        ]
    ]


def test_uv_fallback_carries_the_upgrade_flag(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: False)
    monkeypatch.setattr(preinstall.shutil, "which", lambda name: "/opt/uv")
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    preinstall._pip_install(["numpy"], upgrade=True)

    assert "--upgrade" in spy.commands[0]
    assert spy.commands[0].index("--upgrade") < spy.commands[0].index("numpy")


def test_neither_pip_nor_uv_names_both_remedies(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: False)
    monkeypatch.setattr(preinstall.shutil, "which", lambda name: None)
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, log = preinstall._pip_install(["pandas"])

    assert ok is False
    assert spy.commands == []  # nothing to run — fail before launching anything
    assert "pip" in log and "uv" in log


def test_a_pip_failure_with_pip_present_does_not_switch_tools(monkeypatch):
    """When pip exists and fails, retrying with uv would only blur attribution
    (the resolver or the network is broken either way)."""

    spy = SpyRun(returncode=1, stderr="ResolutionImpossible")
    monkeypatch.setattr(preinstall, "_has_pip", lambda: True)
    monkeypatch.setattr(preinstall.shutil, "which", lambda name: "/opt/uv")
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, log = preinstall._pip_install(["pandas"])

    assert ok is False
    assert "ResolutionImpossible" in log
    assert len(spy.commands) == 1
    assert spy.commands[0][:3] == [sys.executable, "-m", "pip"]


def test_install_reports_the_uv_result_through_the_structured_shape(monkeypatch):
    """`install()` is what `host.env.create` ultimately calls; its result shape
    (ok/installed/failed/log) must be preserved over the fallback path."""

    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: False)
    monkeypatch.setattr(preinstall.shutil, "which", lambda name: "/opt/uv")
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    result = preinstall.install(["pandas"])

    assert result["ok"] is True
    assert result["installed"] == ["pandas"]
    assert result["failed"] == []
