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

import os
import subprocess
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


def test_a_package_pip_would_read_as_an_option_is_refused_before_anything_runs(
    monkeypatch,
):
    """Each name is one argv element after `install`: `--index-url=…` or
    `-r file` would change where everything installs from, not add a package.
    The agent's env_create passes its strings straight through, so the shared
    install path refuses them, with nothing started."""
    spy = SpyRun()
    monkeypatch.setattr(preinstall.subprocess, "run", spy)
    monkeypatch.setattr(
        preinstall, "freeze_for", lambda _python: [{"name": "x", "version": "1"}]
    )
    for hostile in (
        ["--index-url=https://evil.example/simple"],
        ["pandas", "-r", "requirements.txt"],
        ["-e", "git+https://evil.example/pkg.git"],
        ["numpy\n--target=/tmp/x"],
    ):
        result = preinstall.install(
            hostile, python=sys.executable, preserve_installed=True
        )
        assert result["ok"] is False, hostile
        assert result["installed"] == []
        assert "pip would read it as an option" in result["failed"][0]["error"]
    assert spy.commands == []


def test_ordinary_requirement_specifiers_still_reach_pip(monkeypatch):
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: True)
    monkeypatch.setattr(preinstall.subprocess, "run", spy)
    specs = ["pandas", "numpy==2.0.1", "scikit-learn>=1.4", "lifelines[all]"]
    result = preinstall.install(specs)
    assert result["ok"] is True
    assert spy.commands and spy.commands[0][-len(specs) :] == specs


def test_an_install_gets_the_kernels_scrubbed_env_not_the_daemons(monkeypatch):
    """An install runs a foreign interpreter (its .pth files and sitecustomize
    first) and the build backends of what it fetches. They get the kernel's
    scrubbed child env plus pip/uv settings and proxies, never the daemon's
    provider keys, and a fresh working directory instead of the daemon's."""
    seen: dict = {}

    def run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cwd_existed"] = os.path.isdir(kwargs["cwd"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preinstall, "_has_pip", lambda: True)
    monkeypatch.setattr(preinstall.subprocess, "run", run)
    monkeypatch.setenv("ARK_API_KEY", "not-a-real-provider-key")
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "not-a-real-provider-key-2")
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")

    ok, _log = preinstall._pip_install(["tabulate"])

    assert ok is True
    env = seen["env"]
    assert "ARK_API_KEY" not in env and "OPENAI4S_LLM_API_KEY" not in env
    assert not any("not-a-real-provider-key" in value for value in env.values())
    assert env["PIP_INDEX_URL"] == "https://mirror.example/simple"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert "PYTHONPATH" not in env
    assert seen["cwd"] != os.getcwd() and seen["cwd_existed"]
    assert not os.path.exists(seen["cwd"]), "the install workdir was left behind"
    assert seen["stdin"] is subprocess.DEVNULL


def test_the_pip_probe_on_a_foreign_interpreter_runs_confined(monkeypatch):
    """`_target_has_pip` runs the kernel env's interpreter, whose `.pth` files
    and sitecustomize run first. It goes through the confined probe (scrubbed
    env, OS boundary) like `freeze_for`, never a bare subprocess with the
    daemon's environment."""
    probes: list[list[str]] = []

    def confined(argv, *, timeout, stop=None):
        probes.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="pip 25.0", stderr="")

    def bare(*_args, **_kwargs):
        raise AssertionError("the pip probe ran unconfined")

    monkeypatch.setattr(preinstall, "run_confined_probe", confined)
    monkeypatch.setattr(preinstall.subprocess, "run", bare)

    assert preinstall._target_has_pip("/envs/lab/bin/python") is True
    assert probes == [["/envs/lab/bin/python", "-m", "pip", "--version"]]


def test_an_agent_install_takes_only_index_package_names(monkeypatch):
    """A path, `file:`/VCS/HTTP URL or PEP 508 direct reference is an install
    source whose build backend would run as the daemon user outside the kernel
    sandbox; the agent's installs (`registry_only`) refuse them before anything
    runs, while index names with extras and versions go through."""
    spy = SpyRun()
    monkeypatch.setattr(preinstall, "_has_pip", lambda: True)
    monkeypatch.setattr(preinstall.subprocess, "run", spy)
    for source in (
        "/tmp/evil",
        "./evil",
        "file:///tmp/evil",
        "git+https://github.com/example/evil.git",
        "evil @ https://evil.example/evil-1.0.tar.gz",
        "https://evil.example/evil-1.0.tar.gz",
        "evil-1.0-py3-none-any.whl",
        'evil; python_version<"3.13"',
    ):
        result = preinstall.install([source], registry_only=True)
        assert result["ok"] is False, source
        assert "takes package names from the index" in result["failed"][0]["error"]
    assert spy.commands == []

    names = ["pandas", "numpy==2.0.1", "scikit-learn>=1.4,<2", "lifelines[all]"]
    result = preinstall.install(names, registry_only=True)
    assert result["ok"] is True
    assert spy.commands and spy.commands[0][-len(names) :] == names
