"""Prebuilt-environment registry + kernel env selection.

Uses fake conda-env directories under a temp OPENAI4S_ENV_ROOTS so the tests
are deterministic and do not depend on the developer's installed conda envs.
"""
import os
import sys
from pathlib import Path

import pytest

from openai4s import pkgscan
from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher
from openai4s.kernel import environments as E
from openai4s.server import gateway as gateway_mod


@pytest.fixture(autouse=True)
def _reset_env_cache():
    """Rescan the (real) envs after each test so the global cache never leaks a
    test's fake OPENAI4S_ENV_ROOTS into a later test."""
    yield
    E.discover_environments(force=True)


def _make_py_env(root: Path, name: str, packages=("numpy",)) -> Path:
    env = root / name
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "python").symlink_to(
        sys.executable
    )  # real interp → version probe works
    meta = env / "conda-meta"
    meta.mkdir()
    for p in packages:
        (meta / f"{p}-1.0-0.json").write_text('{"name": "%s"}' % p, "utf-8")
    return env


def _make_r_env(root: Path, name: str) -> Path:
    env = root / name
    (env / "bin").mkdir(parents=True)
    rs = env / "bin" / "Rscript"
    rs.write_text("#!/bin/sh\necho R\n", "utf-8")
    os.chmod(rs, 0o755)
    return env


# --- registry -------------------------------------------------------------


def test_base_env_always_present():
    E.discover_environments(force=True)
    base = E.get_environment("base")
    assert base is not None
    assert base.language == "python"
    assert base.interpreter == sys.executable
    assert base.is_conda is False
    assert base.bin_dir is None  # base never mangles PATH


def test_default_env_name_is_offered():
    assert E.default_env_name() in {e.name for e in E.discover_environments(force=True)}


def test_conda_base_prefix_discovers_its_envs_not_the_base_parent(
    tmp_path, monkeypatch
):
    conda_base = tmp_path / "portable-conda"
    (conda_base / "bin").mkdir(parents=True)
    (conda_base / "bin" / "python").symlink_to(sys.executable)
    expected = _make_py_env(conda_base / "envs", "torch", packages=("pandas", "torch"))
    monkeypatch.delenv("OPENAI4S_ENV_ROOTS", raising=False)
    monkeypatch.delenv("CONDA_ENVS_PATH", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(conda_base))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(E.sys, "base_prefix", str(tmp_path / "system-python"))

    envs = E.discover_environments(force=True)

    assert E.get_environment("torch").root == expected
    assert conda_base.name not in {environment.name for environment in envs}


def test_activated_named_conda_prefix_discovers_sibling_envs(tmp_path, monkeypatch):
    envs_root = tmp_path / "portable-conda" / "envs"
    active = _make_py_env(envs_root, "active")
    expected = _make_py_env(envs_root, "analysis", packages=("numpy", "pandas"))
    monkeypatch.delenv("OPENAI4S_ENV_ROOTS", raising=False)
    monkeypatch.delenv("CONDA_ENVS_PATH", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", str(active))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "active")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(E.sys, "base_prefix", str(tmp_path / "system-python"))

    E.discover_environments(force=True)

    assert E.get_environment("active").root == active
    assert E.get_environment("analysis").root == expected


def test_daemon_venv_base_prefix_discovers_conda_envs_without_activation(
    tmp_path, monkeypatch
):
    conda_base = tmp_path / "portable-conda"
    expected = _make_py_env(conda_base / "envs", "science", packages=("pandas",))
    monkeypatch.delenv("OPENAI4S_ENV_ROOTS", raising=False)
    monkeypatch.delenv("CONDA_ENVS_PATH", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(E.sys, "base_prefix", str(conda_base))

    E.discover_environments(force=True)

    assert E.get_environment("science").root == expected


def test_package_scan_is_bounded_to_site_packages(tmp_path):
    env = tmp_path / "env"
    installed = env / "lib" / "python3.13" / "site-packages" / "demo-1.dist-info"
    installed.mkdir(parents=True)
    (installed / "METADATA").write_text("Name: demo\nVersion: 1\n", "utf-8")
    unrelated = env / "lib" / "native" / "cache" / "ghost-1.dist-info"
    unrelated.mkdir(parents=True)
    (unrelated / "METADATA").write_text("Name: ghost\nVersion: 1\n", "utf-8")

    packages = pkgscan.collect_packages(env, language="python")

    assert "demo" in packages
    assert "ghost" not in packages


def test_discover_fake_python_env(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_py_env(roots, "sci", packages=("numpy", "biotite"))
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    envs = E.discover_environments(force=True)
    names = {e.name for e in envs}
    assert "sci" in names and "base" in names
    sci = E.get_environment("sci")
    assert sci.language == "python"
    assert sci.interpreter is not None
    assert sci.is_conda is True
    assert sci.bin_dir.endswith("/bin")
    assert sci.has_package("biotite")
    assert sci.has_package("Biotite")  # normalized match
    assert not sci.has_package("scanpy")


def test_r_only_env_not_runnable(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_r_env(roots, "rlang")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    E.discover_environments(force=True)
    env = E.get_environment("rlang")
    assert env is not None
    assert env.language == "r"
    assert env.interpreter is None  # cannot host the Python kernel
    assert env.to_dict()["runnable"] is False


def test_hidden_envs_are_dropped(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_py_env(roots, "internal")
    _make_py_env(roots, "secret")
    _make_py_env(roots, "keep")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    monkeypatch.setenv("OPENAI4S_ENV_HIDE", "secret,internal")
    names = {e.name for e in E.discover_environments(force=True)}
    assert "internal" not in names
    assert "secret" not in names
    assert "keep" in names


def test_default_env_override(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_py_env(roots, "python")
    _make_py_env(roots, "chosen")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    monkeypatch.setenv("OPENAI4S_DEFAULT_ENV", "chosen")
    E.discover_environments(force=True)  # pick up the fake roots
    assert E.default_env_name() == "chosen"
    monkeypatch.delenv("OPENAI4S_DEFAULT_ENV")
    assert E.default_env_name() == "python"  # falls back to the "python" env


# --- host dispatcher tools ------------------------------------------------


def test_dispatcher_env_list_and_use(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_py_env(roots, "struct", packages=("biotite", "numpy"))
    _make_py_env(roots, "python", packages=("pandas", "numpy"))
    _make_r_env(roots, "r")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "d"))
    E.discover_environments(force=True)

    disp = build_dispatcher()
    disp.active_env_bin = str(roots / "struct" / "bin")  # pretend kernel runs in struct
    switched = {}
    disp.on_env_switch = lambda n: switched.__setitem__("name", n)

    r = disp._m_env_list({"packages": ["biotite", "pandas", "scanpy"]})
    assert r["current"] == "struct"
    envs = {e["name"]: e for e in r["environments"]}
    assert "biotite" in envs["struct"]["has"]
    assert "pandas" in envs["python"]["has"]
    assert r["missing"] == ["scanpy"]  # in no env → truly needs install
    assert envs["r"]["runnable"] is False

    u = disp._m_env_use({"name": "python"})
    assert u["ok"] is True and switched["name"] == "python"

    assert "error" in disp._m_env_use({"name": "nope"})
    # an R-only env is no longer refused: it retargets the ```r channel (the
    # persistent R kernel) and leaves the python kernel untouched
    u_r = disp._m_env_use({"name": "r"})
    assert u_r["ok"] is True
    assert disp.active_r_env == "r"
    assert switched["name"] == "r"  # gateway applies via the pending-env path
    assert "```r" in u_r["note"]


# --- gateway wiring -------------------------------------------------------


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, rid):
        def emit(ev):
            ev.setdefault("root_frame_id", rid)
            self.events.append(ev)

        return emit


def _runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    return gateway_mod.SessionRunner(cfg, _Hub())


def test_gateway_list_environments(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_py_env(roots, "python")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    E.discover_environments(force=True)
    runner = _runner(tmp_path / "data")
    out = runner.list_environments(None)
    names = {e["name"] for e in out["environments"]}
    assert "base" in names and "python" in names
    assert out["default"] in names
    assert out["current"] in names


def test_gateway_set_env_rejects_bad(tmp_path, monkeypatch):
    roots = tmp_path / "envs"
    _make_r_env(roots, "r")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    E.discover_environments(force=True)
    runner = _runner(tmp_path / "data")
    assert "error" in runner.set_env("f1", "does-not-exist")
    assert "error" in runner.set_env("f1", "r")  # R-only → rejected before spawn


def test_resolve_env_falls_back_to_base(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_DEFAULT_ENV", "base")
    E.discover_environments(force=True)
    runner = _runner(tmp_path / "data")
    st = gateway_mod.SessionState("f1", "default", tmp_path / "ws")
    st.env_name = "no-such-env"
    env = runner._resolve_env(st)
    assert env.name == "base"
    assert st.env_name == "base"  # falls back and records it
    summ = runner._env_summary(st)
    assert summ["name"] == "base" and summ["language"] == "python"
