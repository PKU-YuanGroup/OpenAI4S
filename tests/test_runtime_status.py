"""What each language's interpreter is described as (SET-3 / ENV-3).

Settings → Compute and the runtime menu show the Python and the R a session
actually uses. The description must name an environment and where it comes
from, never a path, and must keep one shape whether or not the host has R.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.kernel import environments as envmod
from openai4s.kernel import preinstall, r_kernel, runtime_status
from openai4s.server import gateway as gateway_mod


def _env(name: str, tmp_path: Path, **overrides) -> envmod.Environment:
    fields = dict(
        name=name,
        language="python",
        root=tmp_path / name,
        python=None,
        rscript=None,
        is_conda=True,
        builtin=False,
    )
    fields.update(overrides)
    return envmod.Environment(**fields)


def _fake_rscript(tmp_path: Path) -> str:
    rscript = tmp_path / "lab_env" / "bin" / "Rscript"
    rscript.parent.mkdir(parents=True)
    rscript.write_text("#!/bin/sh\n")
    return str(rscript)


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    runtime_status._R_PROBES.clear()
    yield
    runtime_status._R_PROBES.clear()


def _probe_answers(monkeypatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv, *, timeout, stop=None):
        calls.append(list(argv))
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(preinstall, "run_confined_probe", run)
    return calls


def test_environment_kind_says_where_an_environment_comes_from(tmp_path):
    base = _env("base", tmp_path, is_conda=False, builtin=True)
    managed = _env("python", tmp_path, builtin=True)
    local = _env("work", tmp_path, builtin=False)

    assert runtime_status.environment_kind(base) == "builtin"
    assert runtime_status.environment_kind(managed) == "managed"
    assert runtime_status.environment_kind(local) == "local"


def test_python_runtime_names_the_environment_in_use():
    row = runtime_status.python_runtime("base")

    assert row["available"] is True
    assert row["environment"] == "base"
    assert row["kind"] == "builtin"
    assert row["version"] == platform.python_version()


def test_r_runtime_describes_the_default_resolution_without_a_path(
    tmp_path, monkeypatch
):
    rscript = _fake_rscript(tmp_path)
    lab = _env("lab_env", tmp_path, language="r", rscript=rscript)
    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [lab])
    monkeypatch.setattr(r_kernel, "resolve_r_interpreter", lambda env=None: rscript)
    calls = _probe_answers(
        monkeypatch,
        "version=4.4.1\npackage=ggplot2=FALSE\npackage=dplyr=TRUE\n"
        "package=jsonlite=TRUE\n",
    )

    row = runtime_status.r_runtime(None)

    assert row == {
        "available": True,
        "selected": False,
        "environment": "lab_env",
        "kind": "local",
        "version": "4.4.1",
        "checked": True,
        "packages": [
            {"name": "ggplot2", "available": False},
            {"name": "dplyr", "available": True},
            {"name": "jsonlite", "available": True},
        ],
    }
    assert str(tmp_path) not in json.dumps(row)
    assert calls and calls[0][0] == rscript and "--vanilla" in calls[0]


def test_r_on_path_belongs_to_no_environment(tmp_path, monkeypatch):
    rscript = _fake_rscript(tmp_path)
    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [])
    monkeypatch.setattr(r_kernel, "resolve_r_interpreter", lambda env=None: rscript)
    _probe_answers(
        monkeypatch,
        "version=4.5.0\npackage=ggplot2=TRUE\npackage=dplyr=TRUE\n"
        "package=jsonlite=TRUE\n",
    )

    row = runtime_status.r_runtime(None)

    assert (row["environment"], row["kind"], row["version"]) == ("", "local", "4.5.0")
    assert all(package["available"] for package in row["packages"])


def test_r_runtime_keeps_one_shape_when_no_r_exists(monkeypatch):
    monkeypatch.setattr(r_kernel, "resolve_r_interpreter", lambda env=None: None)

    row = runtime_status.r_runtime(None)

    assert row["available"] is False and row["checked"] is False
    assert (row["environment"], row["kind"], row["version"]) == ("", "", "")
    assert [package["name"] for package in row["packages"]] == list(
        runtime_status.R_KEY_PACKAGES
    )
    # The same keys, each with the same type, as a host that has R.
    assert {key: type(value) for key, value in row.items()} == {
        "available": bool,
        "selected": bool,
        "environment": str,
        "kind": str,
        "version": str,
        "checked": bool,
        "packages": list,
    }


def test_an_unreadable_probe_is_unchecked_not_missing_packages(tmp_path, monkeypatch):
    rscript = _fake_rscript(tmp_path)
    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [])
    monkeypatch.setattr(r_kernel, "resolve_r_interpreter", lambda env=None: rscript)

    def refuse(argv, *, timeout, stop=None):
        raise RuntimeError("the sandbox could not be built")

    monkeypatch.setattr(preinstall, "run_confined_probe", refuse)

    row = runtime_status.r_runtime(None)

    assert row["available"] is True and row["checked"] is False
    assert row["version"] == ""


def test_a_complete_probe_is_cached_and_an_incomplete_one_is_asked_again(
    tmp_path, monkeypatch
):
    rscript = _fake_rscript(tmp_path)
    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [])
    monkeypatch.setattr(r_kernel, "resolve_r_interpreter", lambda env=None: rscript)
    missing = _probe_answers(
        monkeypatch,
        "version=4.4.1\npackage=ggplot2=FALSE\npackage=dplyr=TRUE\n"
        "package=jsonlite=TRUE\n",
    )
    clock = [1000.0]
    monkeypatch.setattr(runtime_status.time, "monotonic", lambda: clock[0])

    runtime_status.r_runtime(None)
    runtime_status.r_runtime(None)
    assert len(missing) == 1  # within the minute: not asked again

    clock[0] += runtime_status._R_PROBE_TTL_S + 1
    complete = _probe_answers(
        monkeypatch,
        "version=4.4.1\npackage=ggplot2=TRUE\npackage=dplyr=TRUE\n"
        "package=jsonlite=TRUE\n",
    )
    assert runtime_status.r_runtime(None)["packages"][0]["available"] is True
    clock[0] += 10 * runtime_status._R_PROBE_TTL_S
    runtime_status.r_runtime(None)
    assert len(complete) == 1  # every key package loads: held


def test_install_reports_the_versions_it_installed(monkeypatch):
    monkeypatch.setattr(
        preinstall, "_pip_install", lambda names, **kwargs: (True, "installed")
    )

    result = preinstall.install(["pytest", "no-such-project-for-this-test>=1"])

    assert result["ok"] is True
    from importlib.metadata import version

    # Read back from the installed metadata; a name pip did not leave behind
    # is left out rather than guessed.
    assert result["installed_versions"] == [
        {"name": "pytest", "version": version("pytest")}
    ]


def test_install_reports_no_versions_for_another_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        preinstall, "_pip_install", lambda names, **kwargs: (True, "installed")
    )
    other = tmp_path / "work" / "bin" / "python"
    other.parent.mkdir(parents=True)
    other.write_text("")

    result = preinstall.install(["pytest"], python=str(other))

    assert result["ok"] is True and result["installed_versions"] == []


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


@pytest.mark.stubbed_backend
def test_environment_listings_say_what_each_language_runs_in(tmp_path, monkeypatch):
    """Both listings carry `runtimes`; the session one follows its R choice."""
    seen: list[str | None] = []

    def r_runtime(env_name):
        seen.append(env_name)
        return {"available": False, "selected": bool(env_name)}

    monkeypatch.setattr(runtime_status, "r_runtime", r_runtime)
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    replies: list[tuple[int, dict]] = []
    handler._query = lambda: {}
    handler._body = lambda: {}
    handler._json = lambda value, code=200: replies.append((code, value))

    handler._api("GET", "/environments")

    code, body = replies[-1]
    assert code == 200
    assert body["runtimes"]["python"]["environment"] == body["current"]
    assert body["runtimes"]["python"]["kind"] in ("builtin", "managed", "local")
    assert body["runtimes"]["r"] == {"available": False, "selected": False}
    assert seen == [None]

    frame_id = runner.store.new_frame(kind="turn", project_id="work", status="ready")
    state = runner._state(frame_id, "work")
    state.r_env_name = "lab_env"
    handler._api("GET", f"/frames/{frame_id}/environments")

    assert replies[-1][1]["current_r"] == "lab_env"
    assert seen[-1] == "lab_env"
