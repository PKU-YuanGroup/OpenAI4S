"""An artifact must record the environment that actually produced it.

The snapshot was a zero-argument freeze of the *daemon* process, stamped
``kind: "python"`` whatever had run. So:

  * an artifact written by an R cell carried a Python package list;
  * an artifact written by a Python cell in a selected conda environment
    carried the daemon's packages, not that environment's;
  * and the UI presented both as the kernel's own provenance.

That is the failure mode worth naming: provenance that is *wrong* rather than
absent. Absence gets noticed; a confident wrong answer gets believed. The
kernel generation already recorded the runtime, the interpreter and the
environment name -- nothing read them.
"""
from __future__ import annotations

import sys

import pytest

from openai4s.config import Config
from openai4s.server.artifacts import ArtifactManager
from openai4s.store import get_store


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path)
    store = get_store(cfg.db_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ArtifactManager(
        data_dir=tmp_path,
        store=store,
        workspace_for=lambda _frame: workspace,
        broadcast=lambda _frame, _event: None,
        guess_content_type=lambda _name: "text/plain",
        checksum=lambda _path: "x",
    )
    project = store.create_project(name="provenance")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    return manager, store, root


def _generation(store, root, language, *, interpreter, env_name):
    return store.create_kernel_generation(
        root_frame_id=root,
        branch_id=root,
        language=language,
        environment={
            "runtime": language,
            "interpreter": interpreter,
            "environment_name": env_name,
        },
        bootstrap={"status": "ok" if language == "python" else "not_applicable"},
        state="active",
    )


def _snapshot(manager, store, root, language):
    return store.get_env_snapshot(
        manager.capture_environment(None, root_frame_id=root, language=language)
    )


# --------------------------------------------------------------------------
# the headline: an R artifact stops claiming a Python environment
# --------------------------------------------------------------------------


def test_an_r_artifact_does_not_carry_a_python_package_list(env):
    manager, store, root = env
    _generation(store, root, "r", interpreter="/usr/bin/Rscript", env_name="r-mini")

    snapshot = _snapshot(manager, store, root, "r")

    assert snapshot["kind"] == "r", "the runtime is the kernel's, not the daemon's"
    assert snapshot["interpreter"] == "/usr/bin/Rscript"
    assert snapshot["packages"] == []
    # Absence with a stated reason, not an empty list that reads as
    # "nothing was installed".
    assert "does not apply" in (snapshot["packages_unavailable"] or "")


def test_a_python_artifact_records_its_own_interpreter_and_environment(env):
    manager, store, root = env
    _generation(store, root, "python", interpreter=sys.executable, env_name="base")

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["kind"] == "python"
    assert snapshot["interpreter"] == sys.executable
    assert snapshot["environment_name"] == "base"
    assert snapshot["package_count"] > 0
    assert snapshot["packages_unavailable"] is None


def test_two_runtimes_never_share_one_snapshot_row(env):
    """Deduplication keyed only on the package list collapsed an R kernel and a
    Python one onto the same row whenever both lists happened to match -- and
    which environment produced a result is the whole point of the record."""
    manager, store, root = env
    _generation(store, root, "python", interpreter=sys.executable, env_name="base")
    _generation(store, root, "r", interpreter="/usr/bin/Rscript", env_name="r-mini")

    python_id = manager.capture_environment(None, root_frame_id=root, language="python")
    r_id = manager.capture_environment(None, root_frame_id=root, language="r")

    assert python_id != r_id


def test_the_snapshot_names_the_generation_that_produced_it(env):
    """One artifact, one exact kernel lifetime."""
    manager, store, root = env
    created = _generation(
        store, root, "python", interpreter=sys.executable, env_name="base"
    )

    snapshot = _snapshot(manager, store, root, "python")
    assert snapshot["generation_id"] == created["generation_id"]


# --------------------------------------------------------------------------
# honesty when the environment cannot be read
# --------------------------------------------------------------------------


def test_an_unreadable_interpreter_reports_absence_not_the_daemon_s_packages(env):
    """The load-bearing rule. Falling back to this process's freeze is how the
    original bug looked from the inside: a plausible list, attributed to an
    interpreter it never came from."""
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/nonexistent/python", env_name="ghost"
    )

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["packages"] == []
    assert "/nonexistent/python" in (snapshot["packages_unavailable"] or "")
    # And it must not borrow this process's identity either.
    assert snapshot["python_version"] is None


def test_no_generation_on_record_is_marked_as_assumed(env):
    """A cell that wrote files before any kernel was registered still gets a
    snapshot, but the reader can tell it apart from a measured one."""
    manager, store, root = env

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["generation_id"] is None
    assert snapshot["kind"] == "python"


def test_capture_environment_never_breaks_artifact_saving(env, monkeypatch):
    """Provenance is important; losing the user's file is worse."""
    manager, store, root = env

    def explode(*_a, **_k):
        raise RuntimeError("store is unhappy")

    monkeypatch.setattr(store, "upsert_env_snapshot", explode)
    assert manager.capture_environment(None, root_frame_id=root) is None


# --------------------------------------------------------------------------
# the migration
# --------------------------------------------------------------------------


def test_historical_snapshots_are_not_backfilled_with_a_guess(env):
    """Rows written before this change could only ever have described the
    daemon. Stamping them with the daemon's identity would convert an
    unattributed record into a confidently wrong one."""
    _manager, store, _root = env
    legacy = store.upsert_env_snapshot(
        {
            "kind": "python",
            "python_version": "3.12.0",
            "implementation": "CPython",
            "platform": "test",
            "packages": [{"name": "numpy", "version": "2.0"}],
            "package_count": 1,
        }
    )
    row = store.get_env_snapshot(legacy)
    assert row["generation_id"] is None
    assert row["interpreter"] is None
