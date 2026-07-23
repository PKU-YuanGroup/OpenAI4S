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


# --------------------------------------------------------------------------
# the branch: a fork's artifact must not borrow the root's kernel
# --------------------------------------------------------------------------


def _select_branch(store, root, branch_id):
    """Make ``branch_id`` the session's active branch.

    Written straight to the selection table rather than driven through
    checkpoint activation, because what is under test is what
    ``active_session_branch`` returns — and that reads this row for real.
    """
    with store._lock:
        store._conn.execute(
            "INSERT OR REPLACE INTO session_branch_selection"
            "(root_frame_id,current_branch_id,updated_at) VALUES(?,?,?)",
            (root, branch_id, 1),
        )
        store._conn.commit()


def test_a_forked_branch_artifact_records_its_own_branch_s_kernel(env):
    """The regression.

    Generations are keyed by branch, and the repository defaults an omitted
    ``branch_id`` to ``root_frame_id``. Omitting it here meant a file written
    by a cell on a forked branch was attributed to whatever kernel the *root*
    branch last ran — a different interpreter, a different environment, a
    different package set — and the UI presented that as the artifact's own.
    """
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/root/bin/python", env_name="root-env"
    )
    store.create_kernel_generation(
        root_frame_id=root,
        branch_id="br-fork",
        language="python",
        environment={
            "runtime": "python",
            "interpreter": "/opt/fork/bin/python",
            "environment_name": "fork-env",
        },
        bootstrap={"status": "ok"},
        state="active",
    )
    _select_branch(store, root, "br-fork")

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["interpreter"] == "/opt/fork/bin/python"
    assert snapshot["environment_name"] == "fork-env"


def test_a_fork_with_no_kernel_of_its_own_does_not_borrow_the_root_s(env):
    """Silence beats the wrong answer: with nothing registered on this branch
    the snapshot must say it is assumed, not describe the root's kernel."""
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/root/bin/python", env_name="root-env"
    )
    _select_branch(store, root, "br-empty")

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["interpreter"] != "/opt/root/bin/python"
    assert "assumed" in (snapshot.get("provenance") or "")


def test_the_root_branch_is_unaffected(env):
    """No selection row means the root branch, which is what it always was."""
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/root/bin/python", env_name="root-env"
    )

    snapshot = _snapshot(manager, store, root, "python")

    assert snapshot["interpreter"] == "/opt/root/bin/python"


# --------------------------------------------------------------------------
# the generation: one snapshot row per kernel lifetime
# --------------------------------------------------------------------------


def test_a_restarted_kernel_gets_its_own_snapshot_row(env):
    """`upsert_env_snapshot` never updates an existing row, and the snapshot id
    did not include the generation — so a kernel restarted into an unchanged
    environment resolved to the row already on disk, which kept naming the
    *first* generation. Every artifact from generation 2 then pointed at a
    snapshot recorded as generation 1, and nothing else on the artifact carries
    a generation, so there was no second source that could catch it.
    """
    manager, store, root = env
    first = _generation(
        store, root, "r", interpreter="/usr/bin/Rscript", env_name="r-mini"
    )
    snapshot_one = _snapshot(manager, store, root, "r")

    second = _generation(
        store, root, "r", interpreter="/usr/bin/Rscript", env_name="r-mini"
    )
    snapshot_two = _snapshot(manager, store, root, "r")

    assert first["generation_id"] != second["generation_id"]
    assert snapshot_one["snapshot_id"] != snapshot_two["snapshot_id"]
    assert snapshot_one["generation_id"] == first["generation_id"]
    assert snapshot_two["generation_id"] == second["generation_id"]


def test_the_same_generation_still_reuses_one_row(env):
    """Content addressing is still the point: two artifacts from the same
    kernel share one environment record."""
    manager, store, root = env
    _generation(store, root, "r", interpreter="/usr/bin/Rscript", env_name="r-mini")
    assert (
        _snapshot(manager, store, root, "r")["snapshot_id"]
        == _snapshot(manager, store, root, "r")["snapshot_id"]
    )


# --------------------------------------------------------------------------
# the probe: paid once per generation, not once per artifact
# --------------------------------------------------------------------------


def test_a_foreign_interpreter_is_frozen_once_per_generation(env, monkeypatch):
    """`freeze_for` launches the target interpreter and enumerates its
    distributions — up to a 20-second wait. Its docstring says callers cache
    per generation; none did, so a persistent kernel writing a figure per cell
    paid the full probe per figure."""
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/other/bin/python", env_name="other"
    )
    calls: list[str] = []

    def counting_freeze(interpreter, *, timeout=20.0):
        calls.append(str(interpreter))
        return [{"name": "numpy", "version": "1.26.0"}]

    monkeypatch.setattr(
        "openai4s.kernel.preinstall.freeze_for", counting_freeze, raising=True
    )

    first = _snapshot(manager, store, root, "python")
    second = _snapshot(manager, store, root, "python")

    assert calls == ["/opt/other/bin/python"], "one probe, not one per artifact"
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["package_count"] == 1


def test_a_failed_probe_is_not_retried_within_the_generation(env, monkeypatch):
    """An interpreter that could not be read will not become readable inside
    the same generation, and re-paying the timeout to rediscover that is the
    worst version of this."""
    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/broken/bin/python", env_name="broken"
    )
    calls: list[str] = []

    def failing_freeze(interpreter, *, timeout=20.0):
        calls.append(str(interpreter))
        return None

    monkeypatch.setattr(
        "openai4s.kernel.preinstall.freeze_for", failing_freeze, raising=True
    )

    snapshot = _snapshot(manager, store, root, "python")
    _snapshot(manager, store, root, "python")

    assert calls == ["/opt/broken/bin/python"]
    assert "could not read distributions" in snapshot["packages_unavailable"]


def test_a_new_generation_probes_again(env, monkeypatch):
    """The cache is scoped to a kernel lifetime because that is the exact span
    over which the answer cannot change. A restart may have installed."""
    manager, store, root = env
    calls: list[str] = []

    def counting_freeze(interpreter, *, timeout=20.0):
        calls.append(str(interpreter))
        return [{"name": "numpy", "version": str(len(calls))}]

    monkeypatch.setattr(
        "openai4s.kernel.preinstall.freeze_for", counting_freeze, raising=True
    )

    _generation(store, root, "python", interpreter="/opt/x/bin/python", env_name="x")
    _snapshot(manager, store, root, "python")
    _generation(store, root, "python", interpreter="/opt/x/bin/python", env_name="x")
    _snapshot(manager, store, root, "python")

    assert len(calls) == 2


# --------------------------------------------------------------------------
# two branches capturing at once
# --------------------------------------------------------------------------


def test_concurrent_captures_on_two_branches_do_not_cross(env, monkeypatch):
    """Branch resolution happens per capture, on whatever thread is capturing.

    A cell finishing on the root branch while another finishes on a fork is the
    ordinary case in a session with a live fork, and the two must not resolve
    each other's kernel — an artifact attributed to the wrong branch's
    interpreter is the exact failure this whole path exists to prevent.
    """
    import threading

    manager, store, root = env
    _generation(
        store, root, "python", interpreter="/opt/root/bin/python", env_name="root-env"
    )
    store.create_kernel_generation(
        root_frame_id=root,
        branch_id="br-fork",
        language="python",
        environment={
            "runtime": "python",
            "interpreter": "/opt/fork/bin/python",
            "environment_name": "fork-env",
        },
        bootstrap={"status": "ok"},
        state="active",
    )

    # Each capture sees the branch that was active for it, which is what a
    # per-session runner supplies.
    branch = threading.local()
    real_active = store.active_session_branch
    monkeypatch.setattr(
        store,
        "active_session_branch",
        lambda frame: getattr(branch, "id", None) or real_active(frame),
        raising=False,
    )

    results: dict[str, str] = {}
    start = threading.Barrier(2)
    lock = threading.Lock()

    def capture(branch_id, label):
        branch.id = branch_id
        start.wait(timeout=5)
        snapshot = _snapshot(manager, store, root, "python")
        with lock:
            results[label] = snapshot["interpreter"]

    threads = [
        threading.Thread(target=capture, args=(root, "root")),
        threading.Thread(target=capture, args=("br-fork", "fork")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results["root"] == "/opt/root/bin/python"
    assert results["fork"] == "/opt/fork/bin/python"
