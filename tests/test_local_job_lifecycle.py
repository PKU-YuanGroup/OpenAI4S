"""The half of a local job's life that nothing owned: its end.

`test_local_jobs.py` covers cancellation and the process group; this file covers
what P0-3 asks for beyond that, and every item here was missing rather than
weak.

**No deadline.** The comment on `MAX_ACTIVE_JOBS` said so outright -- "a real
process tree with no deadline". Eight of them could hold the machine forever,
because the only capacity ever released was the capacity a job chose to release
by exiting.

**The output cap was applied after the allocation it existed to prevent.** The
read loop was `for line in proc.stdout`, i.e. `readline()`, which allocates
until it finds a newline. A job printing a gigabyte with no newline in it had
already materialised that gigabyte in the daemon before `append` could drop any
of it, and the counters were characters on a text-mode pipe while every other
budget in the project is bytes.

**Nothing closed the manager.** No `close`, nothing wired into `server_close`.
The worker threads are daemon threads and the children are in their own process
groups, so daemon shutdown left them running, reparented, with their stdout
pipes closed -- and, because the registry was purely in-memory, with no record
that they had ever existed. A user who submitted a four-hour run came back to an
empty Jobs panel.

**The exception text was the user's.** `job.append(f"\\n[job error] {e}\\n")` put
`str(e)` into a log served by `GET /compute/jobs/<id>`, and `submit` returned
an `OSError`'s message -- which carries the absolute path it failed on, and with
it the account's username -- straight to the caller.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from openai4s import jobs as jobs_mod
from openai4s.jobs import MAX_JOB_DEADLINE_S, JobManager

TERMINAL = ("done", "failed", "cancelled", "timeout", "abandoned")


def _manager(tmp_path) -> JobManager:
    return JobManager(root=tmp_path / "jobs")


def _wait(job_id, manager, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = manager.get(job_id)
        if row and row.get("status") in TERMINAL:
            return row
        time.sleep(0.05)
    return manager.get(job_id)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- the deadline -----------------------------------------------------------


def test_a_job_that_outlives_its_deadline_is_stopped_and_named_timeout(tmp_path):
    """The process must be gone, and the state must not borrow another's name.

    `failed` would claim the job's own command failed and `cancelled` would
    claim somebody meant to stop it. Neither happened: the daemon ended it
    because it ran out of time, which P0-4 requires be its own terminal state.
    """
    manager = _manager(tmp_path)
    submitted = manager.submit("sleep 120", deadline_s=1.0)
    assert "error" not in submitted, submitted

    row = _wait(submitted["id"], manager, timeout=30)

    assert row["status"] == "timeout", row
    assert "deadline" in row["output"]
    job = manager._jobs[submitted["id"]]
    deadline = time.time() + 10
    while _alive(job._proc.pid if job._proc else None) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(job._proc.pid if job._proc else None), "the child survived"


def test_a_job_finishing_inside_its_deadline_is_untouched(tmp_path):
    """The timer must not turn a normal completion into an expiry."""
    manager = _manager(tmp_path)
    submitted = manager.submit("echo fine", deadline_s=30.0)

    row = _wait(submitted["id"], manager)

    assert row["status"] == "done", row
    assert row["exit_code"] == 0
    assert "fine" in row["output"]


def test_a_deadline_past_the_ceiling_is_refused_before_anything_spawns(tmp_path):
    """A refusal that has already started the process is not a limit.

    The count is asserted rather than the message: `submit` returning an error
    dict while a `Job` sits in the registry is the shape this whole file is
    about.
    """
    manager = _manager(tmp_path)

    refused = manager.submit("sleep 120", deadline_s=MAX_JOB_DEADLINE_S + 1)

    assert refused["code"] == "job_bad_deadline", refused
    assert manager.list() == []


def test_the_route_default_is_a_deadline_not_an_unbounded_run(tmp_path):
    """Omitting `deadline_s` must not restore the old unbounded behaviour."""
    manager = _manager(tmp_path)
    submitted = manager.submit("echo hi")

    assert submitted["deadline_s"] == jobs_mod.DEFAULT_JOB_DEADLINE_S
    _wait(submitted["id"], manager)


# --- the output budget ------------------------------------------------------


def test_output_with_no_newline_is_bounded_as_it_is_read(tmp_path, monkeypatch):
    """The producer side, measured -- not the buffer that survives it.

    Asserting only on `retained_bytes` cannot tell this fix from the bug: the
    old code also ended up with a small buffer, having first allocated the whole
    blob to get there. So the sizes handed to `append_bytes` are recorded, and
    the largest one is what the read loop actually asked the kernel for. Under
    `readline()` on a four-megabyte line that number is four megabytes.
    """
    seen: list[int] = []
    original = jobs_mod.Job.append_bytes

    def recording(self, chunk):
        seen.append(len(chunk))
        return original(self, chunk)

    monkeypatch.setattr(jobs_mod.Job, "append_bytes", recording)

    manager = _manager(tmp_path)
    submitted = manager.submit(
        "python3 -c \"import sys; sys.stdout.write('x' * (4 * 1024 * 1024))\"",
        kind="bash",
        deadline_s=60.0,
    )
    row = _wait(submitted["id"], manager, timeout=60)

    assert row["status"] == "done", row
    assert seen, "the read loop never delivered a chunk"
    assert max(seen) <= jobs_mod._READ_CHUNK_BYTES, (
        f"one read allocated {max(seen)} bytes; the chunk bound is "
        f"{jobs_mod._READ_CHUNK_BYTES}"
    )
    assert row["seen_bytes"] >= 4 * 1024 * 1024
    assert row["retained_bytes"] <= jobs_mod._MAX_OUTPUT_BYTES
    assert row["truncated"] is True
    assert row["dropped_bytes"] == row["seen_bytes"] - row["retained_bytes"]


def test_a_truncated_log_does_not_start_mid_character(tmp_path):
    """Dropping on a read boundary must not corrupt what is kept.

    The cut lands inside a multi-byte sequence roughly two times in three for
    three-byte characters, and a replacement character at the head of every
    truncated log is damage the daemon did, not damage the job did.
    """
    manager = _manager(tmp_path)
    submitted = manager.submit(
        "python3 -c \"import sys; sys.stdout.write('\\u4e2d' * 400000)\"",
        deadline_s=60.0,
    )
    row = _wait(submitted["id"], manager, timeout=60)

    assert row["status"] == "done", row
    assert row["truncated"] is True
    body = row["output"].split("\n", 1)[1]
    assert body, "nothing was kept"
    assert not body.startswith("�"), "the retained tail begins mid-character"
    assert body.rstrip("�").endswith("中")


# --- server-owned close -----------------------------------------------------


def test_close_stops_live_jobs_reaps_their_threads_and_refuses_new_ones(tmp_path):
    manager = _manager(tmp_path)
    submitted = manager.submit("sleep 120", deadline_s=300.0)
    job = manager._jobs[submitted["id"]]
    for _ in range(200):
        if job._proc is not None:
            break
        time.sleep(0.02)
    pid = job._proc.pid

    report = manager.close()

    assert report["ok"], report
    assert submitted["id"] in report["stopped"]
    deadline = time.time() + 10
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), "close left the process tree running"
    assert job._thread is not None and not job._thread.is_alive(), "thread not reaped"
    assert manager.get(submitted["id"])["status"] in TERMINAL

    refused = manager.submit("echo late")
    assert refused["code"] == "job_manager_closed", refused


def test_close_is_idempotent(tmp_path):
    manager = _manager(tmp_path)
    assert manager.close()["closed"] is True
    assert manager.close()["closed"] is True


def test_the_http_server_close_closes_the_jobs_manager(tmp_path, monkeypatch):
    """Wired to the real server, because the closure was the whole bug.

    `_jobs_mgr` lived inside `make_handler` and nothing outside could reach it,
    so a test that calls `JobManager.close()` directly would pass against a
    daemon that still orphans every job on the way out. This drives
    `_GatewayHTTPServer.server_close`, which is the callable shutdown actually
    goes through.
    """
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI4S_PORT", "0")
    from openai4s.config import get_config
    from openai4s.server.gateway import build_app_server

    cfg = get_config()
    cfg.port = 0
    httpd = build_app_server(cfg)
    try:
        manager = httpd.RequestHandlerClass.jobs_manager
        assert isinstance(manager, JobManager)
        submitted = manager.submit("sleep 120", deadline_s=300.0)
        job = manager._jobs[submitted["id"]]
        for _ in range(200):
            if job._proc is not None:
                break
            time.sleep(0.02)
        pid = job._proc.pid
    finally:
        httpd.server_close()

    deadline = time.time() + 10
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), "server_close left a local job running"
    assert manager.submit("echo late").get("code") == "job_manager_closed"


# --- the receipt that survives the daemon -----------------------------------


def test_a_job_the_previous_daemon_was_running_comes_back_abandoned(tmp_path):
    """A restart must name it, and must not revive it.

    The registry was in-memory only, so the previous behaviour was neither: the
    job simply vanished, and a user could not tell "finished and pruned" from
    "the daemon died holding it". The second manager reads the receipt the first
    one wrote and reports `abandoned` -- terminal, with no process attached,
    which is the honest description of a process tree whose supervisor is gone.
    """
    first = _manager(tmp_path)
    submitted = first.submit("sleep 120", deadline_s=300.0)
    job = first._jobs[submitted["id"]]
    for _ in range(200):
        if job._proc is not None:
            break
        time.sleep(0.02)
    pid = job._proc.pid
    try:
        # No `close()`: this is the daemon being killed, not shut down.
        second = JobManager(root=tmp_path / "jobs")

        row = second.get(submitted["id"])

        assert row["status"] == "abandoned", row
        assert second._jobs[submitted["id"]]._proc is None, "it was revived"
        assert "did not" in row["output"] or "not retained" in row["output"]
    finally:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def test_a_cleanly_finished_job_leaves_no_receipt_behind(tmp_path):
    """Otherwise the receipts directory grows without bound."""
    first = _manager(tmp_path)
    submitted = first.submit("echo done")
    _wait(submitted["id"], first)

    second = JobManager(root=tmp_path / "jobs")

    assert second.get(submitted["id"]) == {"error": "job not found"}
    assert list((tmp_path / "jobs" / "receipts").glob("*.json")) == []


def test_a_corrupt_receipt_does_not_stop_the_daemon_starting(tmp_path):
    root = tmp_path / "jobs"
    (root / "receipts").mkdir(parents=True)
    (root / "receipts" / "job-broken.json").write_text("{not json", encoding="utf-8")
    (root / "receipts" / "job-live.json").write_text(
        json.dumps({"id": "job-live", "status": "running", "command": "sleep 1"}),
        encoding="utf-8",
    )

    manager = JobManager(root=root)

    assert manager.get("job-live")["status"] == "abandoned"
    assert manager.get("job-broken") == {"error": "job not found"}


# --- no raw exception text on a public surface ------------------------------

CANARY_PATH = "/Users/canary/Documents/embargoed-grant.csv"


def test_a_failure_inside_the_runner_never_reaches_the_job_log(tmp_path, monkeypatch):
    """`GET /compute/jobs/<id>` returns this log. `str(e)` may not be in it."""

    def exploding(*args, **kwargs):
        raise PermissionError(13, "Permission denied", CANARY_PATH)

    monkeypatch.setattr(jobs_mod, "build_kernel_environment", exploding)
    manager = _manager(tmp_path)
    submitted = manager.submit("echo hi")

    row = _wait(submitted["id"], manager)

    assert row["status"] == "failed", row
    assert CANARY_PATH not in json.dumps(row, default=str)
    assert "PermissionError" not in row["output"]
    assert submitted["id"] in row["output"], "the log must still be traceable"


def test_an_unwritable_workspace_is_refused_without_quoting_the_path(
    tmp_path, monkeypatch
):
    """`submit`'s return value goes to the client, so it carries no `strerror`."""
    real_mkdir = Path.mkdir

    def selective(self, *args, **kwargs):
        if "jobs" in str(self):
            raise OSError(13, "Permission denied", CANARY_PATH)
        return real_mkdir(self, *args, **kwargs)

    manager = _manager(tmp_path)
    monkeypatch.setattr(Path, "mkdir", selective)

    refused = manager.submit("echo hi")

    assert refused["code"] == "job_workspace_unavailable", refused
    assert CANARY_PATH not in json.dumps(refused, default=str)
    assert manager.list() == []
