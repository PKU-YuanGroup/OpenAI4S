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


# --- ENV-1: install into the session kernel's interpreter, never move a pin ---
#
# The agent's `host.env.create` used to pip-install into the daemon's own
# interpreter while the step card named the kernel's conda environment, and one
# such install downgraded the daemon's pandas 3.0.3 to 2.3.3. The install now
# targets an explicit interpreter and, for agent installs, pins every package
# that interpreter already has.


def test_a_foreign_interpreter_installs_with_its_own_pip(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_is_this_interpreter", lambda path: False)
    monkeypatch.setattr(preinstall, "_target_has_pip", lambda path: True)
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, _log = preinstall._pip_install(
        ["lifelines"], python="/envs/lab/bin/python", constraints="/tmp/keep.txt"
    )

    assert ok is True
    assert spy.commands == [
        [
            "/envs/lab/bin/python",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--constraint",
            "/tmp/keep.txt",
            "lifelines",
        ]
    ]


def test_a_foreign_interpreter_without_pip_is_targeted_by_uv(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_is_this_interpreter", lambda path: False)
    monkeypatch.setattr(preinstall, "_target_has_pip", lambda path: False)
    monkeypatch.setattr(preinstall.shutil, "which", lambda name: "/opt/uv")
    monkeypatch.setattr(preinstall.subprocess, "run", spy)

    ok, _log = preinstall._pip_install(["lifelines"], python="/envs/lab/bin/python")

    assert ok is True
    assert spy.commands == [
        ["/opt/uv", "pip", "install", "--python", "/envs/lab/bin/python", "lifelines"]
    ]


def test_preserve_installed_pins_every_installed_package(monkeypatch):
    seen: dict = {}

    def fake_pip_install(names, *, upgrade, python, constraints):
        seen["python"] = python
        with open(constraints, encoding="utf-8") as handle:
            seen["pins"] = handle.read().split()
        return True, "ok"

    monkeypatch.setattr(
        preinstall,
        "freeze_for",
        lambda python: [
            {"name": "pandas", "version": "3.0.3"},
            {"name": "numpy", "version": "2.5.0"},
            {"name": "broken-dist", "version": None},
        ],
    )
    monkeypatch.setattr(preinstall, "_pip_install", fake_pip_install)

    result = preinstall.install(
        ["lifelines"], python="/envs/lab/bin/python", preserve_installed=True
    )

    assert result["ok"] is True
    assert result["interpreter"] == "/envs/lab/bin/python"
    assert seen["python"] == "/envs/lab/bin/python"
    assert seen["pins"] == ["numpy==2.5.0", "pandas==3.0.3"]


def test_preserve_installed_refuses_when_the_installed_set_is_unreadable(monkeypatch):
    calls: list = []
    monkeypatch.setattr(preinstall, "freeze_for", lambda python: None)
    monkeypatch.setattr(
        preinstall, "_pip_install", lambda *a, **k: calls.append(a) or (True, "")
    )

    result = preinstall.install(
        ["lifelines"], python="/envs/lab/bin/python", preserve_installed=True
    )

    assert result["ok"] is False
    assert calls == []
    assert "no existing package version changes" in result["failed"][0]["error"]


def test_a_resolver_conflict_is_reported_as_a_refused_version_change(monkeypatch):
    monkeypatch.setattr(
        preinstall,
        "freeze_for",
        lambda python: [{"name": "pandas", "version": "3.0.3"}],
    )
    monkeypatch.setattr(
        preinstall,
        "_pip_install",
        lambda *a, **k: (
            False,
            "× No solution found when resolving dependencies: lifelines "
            "depends on pandas<3 and you require pandas==3.0.3",
        ),
    )

    result = preinstall.install(
        ["lifelines"], python="/envs/lab/bin/python", preserve_installed=True
    )

    assert result["ok"] is False
    error = result["failed"][0]["error"]
    assert "would change the version of a package already installed" in error
    assert "pandas<3" in error
