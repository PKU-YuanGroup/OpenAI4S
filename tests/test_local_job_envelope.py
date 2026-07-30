"""A local job runs with the daemon's secrets, a login shell, and no ceiling.

`JobManager` is reachable from the Compute → Jobs panel (`POST /compute/jobs`).
Five of the envelope's requirements are simply absent, and the first is the one
that matters most:

**The child inherits the daemon's environment verbatim.** `Popen` is called with
no `env=`, so every provider API key the daemon holds is handed to every local
job. The project already owns the fix and uses it everywhere else --
`build_kernel_environment` rebuilds a child env from a strict allowlist, and is
called from `kernel/manager.py`, `tools/dynamic.py` and `kernel/preinstall.py`.
It is not called from `jobs.py`. The architecture doc's claim that "each worker
environment is rebuilt from a strict allowlist rather than copied from the
daemon, so provider/API/cloud secrets do not cross" is true of the kernel and
false of this path.

**`bash -lc` is a login shell.** It sources the user's profile, so the job's
behaviour depends on a dotfile the daemon never saw, and a `.bash_profile` that
exports or prints changes what the job does and what its log contains.

**No capacity, no deadline.** Nothing counts running jobs before spawning a
thread and a process, and nothing ever stops one: a local job can run forever.

The cwd confinement is lexical (`normpath`), so a symlink inside the jobs root
resolves outside it after the check has passed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from openai4s.jobs import JobManager


def _manager(tmp_path) -> JobManager:
    return JobManager(root=tmp_path / "jobs")


def _wait(job_id, manager, timeout=30):
    """Poll to a terminal state and return the receipt WITH its output."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = manager.get(job_id)
        if row and row.get("status") in ("done", "failed", "cancelled", "timeout"):
            return row
        time.sleep(0.05)
    return manager.get(job_id)


# --- the credential leak ----------------------------------------------------


def test_a_local_job_does_not_inherit_the_daemons_provider_keys(tmp_path, monkeypatch):
    """The defect, asserted on what the child can actually read.

    Not "an allowlist function is called" -- the job prints the variable, and the
    value must not come back.
    """
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "sk-daemon-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-daemon-secret")
    manager = _manager(tmp_path)

    submitted = manager.submit(
        'echo "leak:${OPENAI4S_LLM_API_KEY:-none}:${TAVILY_API_KEY:-none}"'
    )
    row = _wait(submitted["id"], manager)

    log = str(row.get("output") or "")
    assert "sk-daemon-secret" not in log, "the daemon's provider key reached the job"
    assert "tvly-daemon-secret" not in log
    assert "leak:none:none" in log


def test_a_local_job_still_gets_the_variables_it_needs(tmp_path):
    """The allowlist must not be so tight the job cannot run: PATH and HOME are
    what a shell needs to be a shell."""
    manager = _manager(tmp_path)
    submitted = manager.submit('echo "path:${PATH:+yes}:home:${HOME:+yes}"')
    row = _wait(submitted["id"], manager)
    log = str(row.get("output") or "")
    assert "path:yes:home:yes" in log


# --- the login shell --------------------------------------------------------


def test_the_job_shell_is_not_a_login_shell(tmp_path):
    """`bash -lc` sources the user's profile, so what a job does depends on a
    dotfile the daemon never saw. Asserted through behaviour: a login shell sets
    `$0` to `-bash`."""
    manager = _manager(tmp_path)
    submitted = manager.submit("shopt -q login_shell && echo LOGIN || echo NOLOGIN")
    row = _wait(submitted["id"], manager)
    assert "NOLOGIN" in str(row.get("output") or "")


# --- capacity ---------------------------------------------------------------


def test_active_capacity_is_claimed_before_a_process_exists(tmp_path):
    """Nothing counted running jobs, so a loop over the route spawned a thread and
    a process per call with no ceiling. The refusal has to come before the spawn,
    or the capacity check is a report rather than a limit."""
    from openai4s import jobs as jobs_mod

    manager = _manager(tmp_path)
    limit = jobs_mod.MAX_ACTIVE_JOBS
    accepted = [manager.submit("sleep 5") for _ in range(limit)]
    assert all("id" in row for row in accepted), accepted

    refused = manager.submit("sleep 5")
    assert "error" in refused, f"the {limit + 1}th job was accepted: {refused}"
    assert "id" not in refused

    for row in accepted:
        manager.cancel(row["id"])


def test_a_finished_job_frees_its_slot(tmp_path):
    """A cap that never releases is a cap that breaks the feature."""
    from openai4s import jobs as jobs_mod

    manager = _manager(tmp_path)
    for _ in range(jobs_mod.MAX_ACTIVE_JOBS):
        row = manager.submit("true")
        _wait(row["id"], manager)
    assert "id" in manager.submit("true")


# --- cwd confinement --------------------------------------------------------


def test_a_symlink_inside_the_jobs_root_cannot_escape_it(tmp_path):
    """`normpath` is lexical: it resolves `..` in the string, not symlinks on
    disk, so a link inside the root passed the `commonpath` test and the process
    ran outside it."""
    manager = _manager(tmp_path)
    root = Path(manager.root)
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "escape")

    result = manager.submit("pwd", cwd="escape")
    assert "error" in result, f"a symlinked cwd escaped the jobs root: {result}"


def test_a_normal_subdirectory_is_still_allowed(tmp_path):
    manager = _manager(tmp_path)
    result = manager.submit("pwd", cwd="work/sub")
    assert "id" in result, result


# --- the receipt ------------------------------------------------------------


def test_the_job_reports_what_it_kept_and_what_it_dropped(tmp_path):
    """A truncated log that does not say so is a log a reader trusts."""
    manager = _manager(tmp_path)
    submitted = manager.submit(
        "python3 -c \"print('x' * 200)\" ; " * 1 + "for i in $(seq 1 5000); do echo "
        "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; done"
    )
    row = _wait(submitted["id"], manager, timeout=60)

    for field in ("truncated", "seen_chars", "retained_chars", "dropped_chars"):
        assert field in row, f"the job receipt does not report {field}"
    assert row["seen_chars"] >= row["retained_chars"]
    assert row["dropped_chars"] == row["seen_chars"] - row["retained_chars"]
    if row["truncated"]:
        assert row["dropped_chars"] > 0
