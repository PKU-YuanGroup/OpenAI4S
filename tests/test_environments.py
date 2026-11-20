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

#: Every environment variable ``E._env_roots()`` merges into its scan list.
#: ``_env_roots`` merges *all* sources — OPENAI4S_ENV_ROOTS does not
#: short-circuit — so a developer machine that exports e.g. MAMBA_ROOT_PREFIX
#: (micromamba's shell hook always does) would otherwise leak real conda envs
#: into these assertions.
_ENV_ROOT_SOURCES = (
    "OPENAI4S_ENV_ROOTS",
    "OPENAI4S_ENV_HIDE",
    "OPENAI4S_DEFAULT_ENV",
    "CONDA_ENVS_DIRS",
    "CONDA_ENVS_PATH",
    "MAMBA_ROOT_PREFIX",
    "CONDA_PREFIX",
)


@pytest.fixture(autouse=True)
def _isolate_env_discovery(tmp_path, monkeypatch):
    """Neutralize every discovery source so each test sees only what it sets up.

    Also rescans the (real) envs afterwards so the global cache never leaks a
    test's fake roots into a later test.
    """
    for var in _ENV_ROOT_SOURCES:
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "isolated-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(E.sys, "base_prefix", str(tmp_path / "no-such-system-python"))
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
    monkeypatch.setenv("CONDA_PREFIX", str(conda_base))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")

    envs = E.discover_environments(force=True)

    assert E.get_environment("torch").root == expected
    assert conda_base.name not in {environment.name for environment in envs}


def test_activated_named_conda_prefix_discovers_sibling_envs(tmp_path, monkeypatch):
    envs_root = tmp_path / "portable-conda" / "envs"
    active = _make_py_env(envs_root, "active")
    expected = _make_py_env(envs_root, "analysis", packages=("numpy", "pandas"))
    monkeypatch.setenv("CONDA_PREFIX", str(active))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "active")

    E.discover_environments(force=True)

    assert E.get_environment("active").root == active
    assert E.get_environment("analysis").root == expected


def test_daemon_venv_base_prefix_discovers_conda_envs_without_activation(
    tmp_path, monkeypatch
):
    conda_base = tmp_path / "portable-conda"
    expected = _make_py_env(conda_base / "envs", "science", packages=("pandas",))
    monkeypatch.setattr(E.sys, "base_prefix", str(conda_base))

    E.discover_environments(force=True)

    assert E.get_environment("science").root == expected


def test_custom_envs_dirs_prefix_discovers_siblings(tmp_path, monkeypatch):
    """``conda config --add envs_dirs /data/myenvs`` (or ``conda create -p``)
    puts the active prefix under a directory that is *not* named ``envs``."""
    myenvs = tmp_path / "myenvs"
    active = _make_py_env(myenvs, "proj")
    expected = _make_py_env(myenvs, "sibling", packages=("numpy", "pandas"))
    monkeypatch.setenv("CONDA_PREFIX", str(active))

    E.discover_environments(force=True)

    assert E.get_environment("proj").root == active
    assert E.get_environment("sibling").root == expected


def test_base_install_prefix_does_not_offer_its_own_parent(tmp_path, monkeypatch):
    """A base install has ``envs/`` — its parent must never become a root."""
    conda_base = tmp_path / "opt" / "miniconda3"
    (conda_base / "conda-meta").mkdir(parents=True)
    expected = _make_py_env(conda_base / "envs", "torch")
    _make_py_env(tmp_path / "opt", "unrelated")  # sibling of the base install
    monkeypatch.setenv("CONDA_PREFIX", str(conda_base))

    E.discover_environments(force=True)

    assert E.get_environment("torch").root == expected
    assert E.get_environment("unrelated") is None
    assert E.get_environment("miniconda3") is None


def test_fresh_base_install_without_envs_dir_does_not_scan_its_parent(
    tmp_path, monkeypatch
):
    """A base install on which no env has been created yet still has ``pkgs/``.

    Classifying it as a named environment would add ``$HOME`` (the parent of
    ``~/miniconda3``) to the scan list, offering every home-directory folder —
    and the base install itself — as a selectable environment.
    """
    home = tmp_path / "home"
    conda_base = home / "miniconda3"
    (conda_base / "bin").mkdir(parents=True)
    (conda_base / "bin" / "python").symlink_to(sys.executable)
    (conda_base / "conda-meta").mkdir()
    (conda_base / "pkgs").mkdir()  # root-prefix marker; no envs/ yet
    _make_py_env(home, "Projects")  # unrelated home-directory folder
    monkeypatch.setenv("CONDA_PREFIX", str(conda_base))

    E.discover_environments(force=True)

    assert E.get_environment("Projects") is None
    assert E.get_environment("miniconda3") is None


def test_conda_envs_path_splits_on_pathsep(tmp_path, monkeypatch):
    first = _make_py_env(tmp_path / "a", "alpha")
    second = _make_py_env(tmp_path / "b", "beta")
    monkeypatch.setenv(
        "CONDA_ENVS_PATH", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    )

    E.discover_environments(force=True)

    assert E.get_environment("alpha").root == first
    assert E.get_environment("beta").root == second


def test_conda_envs_dirs_is_honoured(tmp_path, monkeypatch):
    """CONDA_ENVS_DIRS is canonical; CONDA_ENVS_PATH is the deprecated alias."""
    expected = _make_py_env(tmp_path / "roots", "gamma")
    monkeypatch.setenv("CONDA_ENVS_DIRS", str(tmp_path / "roots"))

    E.discover_environments(force=True)

    assert E.get_environment("gamma").root == expected


def test_relative_conda_envs_entries_are_ignored(tmp_path, monkeypatch):
    """A relative root would resolve against the daemon's nondeterministic cwd."""
    _make_py_env(tmp_path / "roots", "delta")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONDA_ENVS_DIRS", "roots")

    E.discover_environments(force=True)

    assert E.get_environment("delta") is None


def test_mamba_root_prefix_discovers_envs(tmp_path, monkeypatch):
    mamba_root = tmp_path / "micromamba"
    expected = _make_py_env(mamba_root / "envs", "epsilon")
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(mamba_root))

    E.discover_environments(force=True)

    assert E.get_environment("epsilon").root == expected


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


@pytest.mark.parametrize(
    "layout",
    [
        "lib/python3.13/site-packages",  # CPython venv / conda env
        "lib64/python3.11/site-packages",  # RHEL multilib
        "lib/pypy3.10/site-packages",  # PyPy — 'python*' would miss this
        "lib/python3/dist-packages",  # Debian/Ubuntu system Python
        "lib/python3.11/dist-packages",  # Debian versioned
        "Lib/site-packages",  # Windows
    ],
)
def test_package_scan_resolves_real_layouts(tmp_path, layout):
    env = tmp_path / "env"
    installed = env / layout / "demo-1.dist-info"
    installed.mkdir(parents=True)
    (installed / "METADATA").write_text("Name: demo\nVersion: 1\n", "utf-8")
    # decoy: a nested toolchain directory must stay out of the bounded scan
    decoy = env / "lib" / "native" / "cache" / "ghost-1.dist-info"
    decoy.mkdir(parents=True)
    (decoy / "METADATA").write_text("Name: ghost\nVersion: 1\n", "utf-8")

    packages = pkgscan.collect_packages(env, language="python")

    assert "demo" in packages, f"{layout} did not resolve"
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

    # The probe package must be one no environment (including the daemon's own
    # venv, which discovery also surfaces) can ever have installed: scanpy is
    # no longer safe here because `uv sync --extra singlecell` puts it in the
    # dev venv.
    r = disp._m_env_list({"packages": ["biotite", "pandas", "nonexistent-probe-pkg"]})
    assert r["current"] == "struct"
    envs = {e["name"]: e for e in r["environments"]}
    assert "biotite" in envs["struct"]["has"]
    assert "pandas" in envs["python"]["has"]
    assert r["missing"] == ["nonexistent-probe-pkg"]  # in no env → needs install
    # runnable on the channel it serves: an R-only env runs ```r cells
    assert envs["r"]["runnable"] is True

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


def test_env_list_reports_an_r_only_env_runnable_on_the_r_channel(
    tmp_path, monkeypatch
):
    """`env_list` must agree with `env_use` and with where R cells really run.

    `runnable` was `interpreter is not None`, and `interpreter` is the *Python*
    that hosts the notebook kernel -- None for every R-only environment. So the
    model was told the one environment its ```r cells ran in could not run
    anything, while `env_use` accepted it and `resolve_r_interpreter` picked
    its Rscript. A model taking the field at its word refused to use R. The
    package recommendation skipped R environments the same way, so a request
    for R packages never pointed at the environment that had them.

    `Environment.to_dict` keeps its narrower meaning: it backs the Notebook's
    Python-kernel picker, where an R-only environment is correctly disabled.
    """
    roots = tmp_path / "envs"
    _make_py_env(roots, "python", packages=("numpy",))
    r_env = _make_r_env(roots, "rlang")
    description = r_env / "lib" / "R" / "library" / "probeRplot" / "DESCRIPTION"
    description.parent.mkdir(parents=True)
    description.write_text("Package: probeRplot\nVersion: 1.0\n", "utf-8")
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "d"))
    E.discover_environments(force=True)
    disp = build_dispatcher()

    listed = disp._m_env_list({"packages": ["probeRplot"]})

    envs = {e["name"]: e for e in listed["environments"]}
    assert envs["rlang"]["language"] == "r"
    assert envs["rlang"]["runnable"] is True
    assert envs["python"]["runnable"] is True
    assert envs["rlang"]["has"] == ["probeRplot"]
    assert listed["recommend"] == "rlang"
    assert disp._m_env_use({"name": "rlang"})["ok"] is True
    assert E.get_environment("rlang").to_dict()["runnable"] is False


def test_env_use_rescans_after_operator_creates_a_conda_environment(
    tmp_path, monkeypatch
):
    roots = tmp_path / "envs"
    roots.mkdir()
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(roots))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "d"))
    E.discover_environments(force=True)
    created = _make_py_env(roots, "reactiont5", packages=("transformers",))

    disp = build_dispatcher()
    switched = {}
    disp.on_env_switch = lambda name: switched.__setitem__("name", name)

    result = disp._m_env_use({"name": "reactiont5"})

    assert result["ok"] is True
    assert result["env"]["name"] == "reactiont5"
    assert switched == {"name": "reactiont5"}
    assert E.get_environment("reactiont5").root == created


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
    # An R-only env (ENV-6) retargets the session's R cells and starts nothing;
    # it used to be refused, so no R environment could be chosen at all.
    picked = runner.set_env("f1", "r")
    assert picked.get("ok") is True and picked["language"] == "r"
    assert runner.list_environments("f1")["current_r"] == "r"
    assert runner.kernel_status("f1")["alive"] is False


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


# --- ENV-2 / ENV-4: the user's own conda environments ------------------------
#
# Every discovered conda env was reported ``builtin``, so a personal research
# environment read as part of OpenAI4S, the agent switched the kernel into it
# without asking, and R cells ran in the first personal env that shipped
# Rscript (r-mini, without survival) while PATH had a complete R.


def test_the_base_description_lists_what_is_installed_instead_of_a_promise(tmp_path):
    """`host.env.list` described the default kernel as "启动即预装
    numpy/pandas/scipy/matplotlib/联网栈" on every install. The reviewed one
    had no networking stack and no openpyxl; the line is now read off the
    interpreter's own packages."""
    root = _make_py_env(tmp_path, "daemon-venv", packages=("numpy", "openpyxl"))
    base = E.Environment(
        name="base", language="python", root=root, python=sys.executable, is_conda=False
    )

    assert base.description() == "内置默认内核：已安装 numpy, openpyxl"
    for promised in ("scipy", "matplotlib", "联网栈", "预装"):
        assert promised not in base.description()

    bare = E.Environment(
        name="base",
        language="python",
        root=_make_py_env(tmp_path, "bare-venv", packages=()),
        python=sys.executable,
        is_conda=False,
    )
    assert bare.description() == "内置默认内核：未检测到常用科学计算包"


def test_the_prompt_asks_for_a_missing_package_instead_of_working_around_it():
    """The reviewed model found no openpyxl, did not ask for it, and parsed the
    .xlsx with zipfile + XML and hand-wrote the ANOVA "to avoid the install
    wait". The Web prompt had told it to: fall back to the stdlib when a
    package is missing, or pip-install through host.bash."""
    from openai4s.agent.loop import SYSTEM_PROMPT

    web = SYSTEM_PROMPT + gateway_mod._GATEWAY_PROMPT_EXTRA

    assert "A missing package is something to ask for, not to work around." in web
    assert "native `env_create` tool" in SYSTEM_PROMPT
    assert 'host.env.create(None, ["pkg"])' in SYSTEM_PROMPT
    assert "Do NOT re-implement the library to avoid that wait" in SYSTEM_PROMPT
    assert "unzipping an .xlsx" in SYSTEM_PROMPT
    assert "hand-coded statistical test" in SYSTEM_PROMPT
    # R has no installer (owner decision): name the package, point at setup.
    assert "R packages cannot be installed from a session." in SYSTEM_PROMPT
    assert "`openai4s setup` builds the managed R environment" in SYSTEM_PROMPT
    assert "Do not silently fall back to base R." in SYSTEM_PROMPT
    # What the Web half used to say instead.
    assert "pip install --break-system-packages" not in web
    assert "matplotlib fallback" not in web
    assert "request the install with `host.env.create(None, [pkg])`" in web
    assert "do not substitute your own parser or statistic" in web


def test_envs_in_the_users_conda_root_are_local_but_product_names_are_not(
    tmp_path, monkeypatch
):
    conda_root = tmp_path / "miniconda3"
    _make_py_env(conda_root / "envs", "lab_env")
    _make_py_env(conda_root / "envs", "python")
    operator_root = tmp_path / "operator-envs"
    _make_py_env(operator_root, "curated")
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(operator_root))

    E.discover_environments(force=True)

    assert E.get_environment("lab_env").builtin is False
    assert E.get_environment("python").builtin is True  # built by `openai4s setup`
    assert E.get_environment("curated").builtin is True  # operator-configured root
    assert E.get_environment("base").builtin is True


def test_env_list_does_not_recommend_the_users_own_environment(tmp_path, monkeypatch):
    """PERM-3. The step card read "4 envs · use <a personal conda env>" and the
    model asked to switch into it: `recommend` was whichever environment had
    the most of the requested packages, the user's own included. A built-in
    environment and an install request come first; a local one is the user's
    to name."""
    conda_root = tmp_path / "miniconda3"
    _make_py_env(
        conda_root / "envs", "lab_env", packages=("pandas", "openpyxl", "statsmodels")
    )
    operator_root = tmp_path / "operator-envs"
    _make_py_env(operator_root, "curated", packages=("pandas", "openpyxl"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(operator_root))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "d"))
    E.discover_environments(force=True)
    disp = build_dispatcher()
    wanted = ["pandas", "openpyxl", "statsmodels", "nonexistent-probe-pkg"]

    listed = disp._m_env_list({"packages": wanted})

    envs = {e["name"]: e for e in listed["environments"]}
    assert envs["lab_env"]["builtin"] is False and envs["curated"]["builtin"] is True
    # The local environment covers more, and is still reported honestly...
    assert envs["lab_env"]["has"] == ["pandas", "openpyxl", "statsmodels"]
    # ...but the recommendation is the best built-in one, plus what to ask for.
    assert listed["recommend"] == "curated"
    assert listed["install"] == ["statsmodels", "nonexistent-probe-pkg"]
    assert listed["missing"] == ["statsmodels", "nonexistent-probe-pkg"]
    assert "only if the user named it" in listed["note"]
    assert "env_create" in listed["note"]
    # The standing "Kernel environment" paragraph gives the same order.
    from openai4s.kernel.constraints import kernel_constraints_note

    standing = kernel_constraints_note(
        posture={"mode": "off", "backend": None, "raw_network": False}
    )
    assert "switch to the built-in environment it recommends" in standing
    assert "builtin=false is the user's own" in standing


def test_env_list_keeps_an_environment_the_user_already_picked(tmp_path, monkeypatch):
    """A local environment the session already runs in was the user's own
    choice (a UI pick does not ask), so it stays a candidate; and staying put
    is not a recommendation to switch."""
    conda_root = tmp_path / "miniconda3"
    lab = _make_py_env(conda_root / "envs", "lab_env", packages=("pandas", "openpyxl"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "d"))
    E.discover_environments(force=True)
    disp = build_dispatcher()
    disp.active_env_bin = str(lab / "bin")

    listed = disp._m_env_list({"packages": ["openpyxl", "nonexistent-probe-pkg"]})

    assert listed["current"] == "lab_env"
    assert listed["recommend"] is None
    assert listed["install"] == ["nonexistent-probe-pkg"]
    assert listed["missing"] == ["nonexistent-probe-pkg"]


def test_switching_into_a_local_env_needs_approval_by_default(tmp_path, monkeypatch):
    from openai4s.store import Store
    from openai4s.tools.env_use import EnvUseTool

    conda_root = tmp_path / "miniconda3"
    _make_py_env(conda_root / "envs", "work")
    _make_py_env(conda_root / "envs", "python")
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    E.discover_environments(force=True)

    tool = EnvUseTool()
    assert tool.requires_approval is True
    assert tool.permission_target({"name": "work"}) == "local:work"
    assert tool.permission_target("python") == "builtin:python"

    store = Store(tmp_path / "perm.db")
    store.seed_default_permission_rules()
    assert store.resolve_permission(tool="env_use", pattern_input="local:work") == "ask"
    assert (
        store.resolve_permission(tool="env_use", pattern_input="builtin:python")
        == "allow"
    )


def test_the_env_use_step_names_a_local_environment(tmp_path, monkeypatch):
    from openai4s.host_dispatch import _step_begin

    conda_root = tmp_path / "miniconda3"
    _make_py_env(conda_root / "envs", "work")
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    E.discover_environments(force=True)

    kind, title, data = _step_begin("env_use", [{"name": "work"}])

    assert title == "Switching to the local work environment"
    assert data == {"name": "work", "local": True}


def test_r_on_path_wins_over_a_personal_conda_env_with_rscript(tmp_path, monkeypatch):
    from openai4s.kernel.r_kernel import resolve_r_interpreter

    conda_root = tmp_path / "miniconda3"
    _make_r_env(conda_root / "envs", "r-mini")
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    system_r = path_dir / "Rscript"
    system_r.write_text("#!/bin/sh\necho R\n", "utf-8")
    os.chmod(system_r, 0o755)
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(conda_root))
    monkeypatch.setenv("PATH", str(path_dir))
    E.discover_environments(force=True)

    assert resolve_r_interpreter() == str(system_r)

    # The environment `openai4s setup` builds for R still comes first.
    product_r = _make_r_env(conda_root / "envs", "r")
    E.discover_environments(force=True)
    assert resolve_r_interpreter() == str(product_r / "bin" / "Rscript")

    # Without any R on PATH, a discovered env is still better than nothing.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    import shutil

    shutil.rmtree(product_r)
    E.discover_environments(force=True)
    assert resolve_r_interpreter() == str(
        conda_root / "envs" / "r-mini" / "bin" / "Rscript"
    )
