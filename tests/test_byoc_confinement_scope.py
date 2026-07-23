"""What the boundary hands back is as load-bearing as what it denies.

Three findings, one theme — a check that answered an easier question than the
one it was standing in for:

  * ``runtime_read_paths`` allowed the helper package's *parent*, which in a
    source or editable install is the repository root. The home denial was then
    reopened over everything beneath it, so a provider shim — which by design
    also has the network — could read an untracked ``.env``, ``.git``, and any
    unrelated source or data sitting there;
  * ``available()`` asked whether the backend binary was installed. On a Linux
    host with unprivileged user namespaces or mounts disabled, ``bwrap`` is
    installed and confines nothing, so the enforce gate proceeded and the real
    invocation died before the helper started — reported as an indeterminate
    remote operation rather than a host that cannot sandbox;
  * ``doctor`` had its own opinion and contradicted the runtime's, reporting
    unconditionally that no OS boundary existed and advising the user to weaken
    ``enforce`` to ``auto``.

The macOS tests below establish a real boundary with ``sandbox-exec`` and try
to read real files through it. A profile-string assertion would have passed
against the broken code — the string was right, and it allowed the repository.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from openai4s.security import byoc_confinement as bc

_REPO = Path(bc.__file__).resolve().parent.parent.parent

macos_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="Seatbelt is the macOS backend"
)

_READ_PROBE = r"""
import json, os, sys
out = {}


def t(key, fn):
    try:
        out[key] = fn() or "OK"
    except PermissionError:
        out[key] = "DENIED"
    except FileNotFoundError:
        out[key] = "ABSENT"
    except Exception as exc:
        out[key] = "ERR:" + type(exc).__name__


repo = sys.argv[1]
t("repo_list", lambda: os.listdir(repo) and "READABLE")
t("repo_file", lambda: open(os.path.join(repo, "pyproject.toml"), "rb").read() and "READABLE")
t("git_dir", lambda: os.listdir(os.path.join(repo, ".git")) and "READABLE")
t("helper_pkg", lambda: os.listdir(sys.argv[2]) and "READABLE")
print(json.dumps(out))
"""


@pytest.fixture(autouse=True)
def _fresh_self_test():
    bc.reset_self_test_cache()
    yield
    bc.reset_self_test_cache()


# --------------------------------------------------------------------------
# the repository is not the helper's to read
# --------------------------------------------------------------------------


def test_the_allowance_names_the_package_not_the_tree_it_sits_in():
    allowed = bc.runtime_read_paths()
    assert str(_REPO) not in allowed, (
        "the repository root was allow-listed back over the home denial, so "
        "an untracked .env and .git were readable from inside the boundary"
    )
    assert bc.helper_package_dir() in allowed
    assert bc.helper_package_dir().endswith("openai4s_compute_provider")


@macos_only
def test_a_confined_process_cannot_read_the_repository(tmp_path):
    """The leak, tried for real, in the install this test is running from."""
    if not str(_REPO).startswith(str(Path.home())):
        pytest.skip("this install is not under the user's home")
    stage = tmp_path / "stage"
    stage.mkdir()
    profile = bc.build_profile(stage)
    argv = [
        "sandbox-exec",
        "-p",
        profile,
        sys.executable,
        "-I",
        "-c",
        _READ_PROBE,
        str(_REPO),
        bc.helper_package_dir(),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    assert result["repo_list"] == "DENIED", result
    assert result["repo_file"] == "DENIED", result
    assert result["git_dir"] in ("DENIED", "ABSENT"), result
    # ...and the one thing it does need is still there.
    assert result["helper_pkg"] == "READABLE", result


@macos_only
def test_the_helper_still_loads_its_own_package_under_the_boundary(tmp_path):
    """The allowance was there for a reason; the reason has to still work.

    Invoked with no arguments the entrypoint imports its package and *then*
    fails to unpack argv — so a ValueError proves the import got through, and
    an ImportError would prove the tightening broke the helper.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    helper_main = str(Path(bc.helper_package_dir()) / "__main__.py")
    argv = bc.wrap([sys.executable, "-I", helper_main], stage)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)

    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert "ImportError" not in proc.stderr, proc.stderr
    assert (
        "ValueError" in proc.stderr
    ), f"the entrypoint did not get past its own import: {proc.stderr}"


def test_the_entrypoint_does_not_put_its_parent_on_the_path():
    source = (Path(bc.helper_package_dir()) / "__main__.py").read_text("utf-8")
    assert "sys.path.insert(0, os.path.dirname(_here))" not in source, (
        "putting the parent on sys.path is what forced the repository root to "
        "be readable: a package import lists the directory it searches"
    )


def test_the_helper_package_still_imports_the_new_way():
    """Unconfined, so a failure here is about the loader and nothing else."""
    helper_main = str(Path(bc.helper_package_dir()) / "__main__.py")
    proc = subprocess.run(
        [sys.executable, "-I", helper_main], capture_output=True, text=True, timeout=60
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert "ValueError" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------
# installed is not working
# --------------------------------------------------------------------------


def test_available_runs_a_boundary_self_test_rather_than_a_which(monkeypatch):
    """The bwrap reproduction: the binary is there and confines nothing."""
    calls: list[list[str]] = []

    def failing_probe(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 1, b"", b"bwrap: setting up uid map: Permission denied"
        )

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", failing_probe)

    ok, reason = bc.available()

    assert calls, "available() did not establish a boundary before answering"
    assert ok is False
    assert "did not establish a filesystem boundary" in reason
    assert "Permission denied" in reason


def test_a_self_test_verdict_is_cached_but_not_across_a_change(monkeypatch):
    runs: list[int] = []

    def probe(argv, **kwargs):
        runs.append(1)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", probe)
    monkeypatch.setattr(
        bc.os, "stat", lambda *_a, **_k: types.SimpleNamespace(st_mtime_ns=1)
    )

    assert bc.available()[0] is True
    assert bc.available()[0] is True
    assert len(runs) == 1, "the self-test is meant to be cached"

    # The backend binary changed underneath us: the verdict must not survive it.
    monkeypatch.setattr(
        bc.os, "stat", lambda *_a, **_k: types.SimpleNamespace(st_mtime_ns=2)
    )
    assert bc.available()[0] is True
    assert len(runs) == 2, "a changed backend must invalidate the cached verdict"


def test_a_self_test_that_hangs_is_a_failure_not_a_wait(monkeypatch):
    def hangs(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=bc.SELF_TEST_TIMEOUT_S)

    monkeypatch.setattr(bc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bc.subprocess, "run", hangs)

    ok, reason = bc.available()
    assert ok is False
    assert "self-test" in reason


@macos_only
def test_the_real_backend_passes_its_own_self_test():
    """The control for all of the above, on a host that can actually confine."""
    ok, reason = bc.self_test(force=True)
    assert ok is True, reason
    assert "Seatbelt" in reason


# --------------------------------------------------------------------------
# doctor and the runtime answer from the same place
# --------------------------------------------------------------------------


def test_doctor_reports_the_boundary_that_is_actually_implemented(
    tmp_path, monkeypatch
):
    from openai4s import doctor as doctor_mod

    skills = tmp_path / "skills"
    (skills / "remote-compute-fake").mkdir(parents=True)
    (skills / "remote-compute-fake" / "provider.py").write_text("x", encoding="utf-8")
    (skills / "remote-compute-fake" / "provider.json").write_text(
        '{"id": "fake"}', encoding="utf-8"
    )
    cfg = types.SimpleNamespace(skills_dir=skills, data_dir=tmp_path)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    monkeypatch.setattr(bc, "available", lambda: (True, "macOS Seatbelt"))

    check = doctor_mod._remote(cfg)

    assert check.status != doctor_mod.FAIL, (
        "doctor claimed no OS boundary exists on a host where one is applied, "
        "and told the user to weaken enforce to auto"
    )
    assert check.facts["confinement_state"] == "active"


def test_doctor_still_fails_closed_when_enforce_cannot_be_satisfied(
    tmp_path, monkeypatch
):
    from openai4s import doctor as doctor_mod

    skills = tmp_path / "skills"
    (skills / "remote-compute-fake").mkdir(parents=True)
    (skills / "remote-compute-fake" / "provider.py").write_text("x", encoding="utf-8")
    (skills / "remote-compute-fake" / "provider.json").write_text(
        '{"id": "fake"}', encoding="utf-8"
    )
    cfg = types.SimpleNamespace(skills_dir=skills, data_dir=tmp_path)

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    monkeypatch.setattr(bc, "available", lambda: (False, "bwrap is not on PATH"))

    check = doctor_mod._remote(cfg)
    assert check.status == doctor_mod.FAIL
    assert "bwrap is not on PATH" in check.detail


def test_the_manager_and_doctor_describe_the_same_posture(tmp_path, monkeypatch):
    from openai4s.compute.manager import ComputeManager

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "auto")
    (tmp_path / "skills").mkdir()
    cfg = types.SimpleNamespace(
        data_dir=tmp_path, skills_dir=tmp_path / "skills", db_path=None
    )
    status = ComputeManager(cfg).confinement_status()
    posture = bc.posture("auto")

    assert status["state"] == posture["state"]
    assert status["enforced"] == posture["enforced"]
    assert status["network_isolated"] == posture["network_isolated"]
