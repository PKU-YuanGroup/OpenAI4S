"""The compute-jobs routes must give a domain failure an HTTP status.

Every refusal from `JobManager` arrives as a soft ``{"error": ...}`` dict,
and the routes serialized all of them as 200 — so an unknown job read as a
successful lookup, a refused cancel as a cancel that landed, and a refused
submit as an accepted one. `api()` in the web client only throws on
non-2xx, so the workbench showed all three as success: a job that did not
exist rendered "output empty", and a refused submit disappeared silently.

The projection is the one `_skill_result_status` applies to the skill
surface (`_soft_failure_status`): keyed on the stable `code` through
`jobs.JOB_FAILURE_STATUS`, never on the message, and an error whose code is
missing or unmapped is 400 rather than 200.

- `job_not_found` (unknown id on read or cancel) → 404.
- `job_cancel_failed` → 500: the job exists and is still running. Deciding
  404 from the mere presence of `error` answered this one "no such job".
- Client input (`job_empty_command`, `job_bad_command`, `job_bad_kind`,
  `job_bad_deadline`, `job_bad_cwd`, `job_cwd_escape`) → 400, `job_capacity` → 429,
  `job_workspace_unavailable` → 500, `job_manager_closed` → 503.

Every refusal test here fails if its table entry is removed: the route
falls back to the 400 default, or, before this projection, to a 200
carrying the same body.
"""

from __future__ import annotations

import math
import os
import re
import signal
import time
from pathlib import Path

import pytest

from openai4s import jobs as jobs_mod
from openai4s.config import Config, LLMConfig
from openai4s.jobs import JOB_FAILURE_STATUS
from openai4s.server import gateway as gateway_mod


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def route(tmp_path):
    """Call one compute-jobs route, one fresh handler per request.

    The handler class -- and with it the `JobManager` -- is built once and
    closed afterwards: `runner.close()` does not own the manager, so without
    the explicit `close()` a real job outlived its test.
    """
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)

    def call(method, path, body=None):
        handler = object.__new__(handler_cls)
        handler._query = lambda: {}
        handler._body = lambda: dict(body or {})
        seen: list[tuple[dict, int]] = []
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._api(method, path)
        return seen[-1]

    call.manager = handler_cls.jobs_manager
    try:
        yield call
    finally:
        handler_cls.jobs_manager.close()
        runner.close()


def test_reading_a_missing_job_is_a_404(route):
    assert route("GET", "/compute/jobs/job-that-never-ran") == (
        {"error": "job not found", "code": "job_not_found"},
        404,
    )


def test_cancelling_a_missing_job_is_a_404(route):
    assert route("POST", "/compute/jobs/job-that-never-ran/cancel") == (
        {"error": "job not found", "code": "job_not_found"},
        404,
    )


def test_submitting_an_empty_command_is_a_400(route):
    assert route("POST", "/compute/jobs", {"command": "   "}) == (
        {"error": "empty command", "code": "job_empty_command"},
        400,
    )


def test_submitting_a_bad_deadline_is_a_400(route):
    body, code = route("POST", "/compute/jobs", {"command": "x", "deadline_s": "soon"})
    assert code == 400
    assert body["code"] == "job_bad_deadline"


@pytest.mark.parametrize("deadline", ["nan", math.nan, "inf", -1])
def test_a_non_finite_deadline_is_refused_not_run(route, deadline):
    """NaN compares False against both bounds, so it used to be accepted: the
    Timer fired at once and the row carried a bare `NaN` that `JSON.parse`
    rejects, which blanked the whole Jobs list in the workbench."""
    body, code = route(
        "POST", "/compute/jobs", {"command": "echo hi", "deadline_s": deadline}
    )
    assert (code, body["code"]) == (400, "job_bad_deadline"), body
    assert route.manager.list() == []


def test_submitting_an_escaping_cwd_is_a_400(route):
    assert route("POST", "/compute/jobs", {"command": "x", "cwd": "../.."}) == (
        {"error": "cwd escapes the jobs root", "code": "job_cwd_escape"},
        400,
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"command": 123}, "job_bad_command"),
        ({"command": ["ls"]}, "job_bad_command"),
        ({"command": "echo hi", "cwd": 5}, "job_bad_cwd"),
        ({"command": "echo hi", "cwd": ["a"]}, "job_bad_cwd"),
        ({"command": "echo hi", "cwd": "a\x00b"}, "job_bad_cwd"),
        # A mistyped kind used to run the command under bash instead.
        ({"command": "import os", "kind": "Python"}, "job_bad_kind"),
        ({"command": "echo hi", "kind": "R"}, "job_bad_kind"),
    ],
)
def test_a_mistyped_field_is_a_400_not_a_crash(route, body, expected):
    """`.strip()` on a number and `os.path.join` on a list raised out of the
    route, which the dispatcher answers 500 with a traceback."""
    answer, code = route("POST", "/compute/jobs", body)
    assert (code, answer["code"]) == (400, expected), answer
    assert route.manager.list() == []


def test_a_full_job_table_is_a_429(route, monkeypatch):
    monkeypatch.setattr(jobs_mod, "MAX_ACTIVE_JOBS", 0)
    body, code = route("POST", "/compute/jobs", {"command": "echo hi"})
    assert (code, body["code"]) == (429, "job_capacity"), body


@pytest.mark.stubbed_backend
def test_an_unwritable_workspace_is_a_500(route, monkeypatch):
    real_mkdir = Path.mkdir

    def refuse(self, *args, **kwargs):
        if "compute-jobs" in str(self):
            raise OSError(13, "Permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse)
    body, code = route("POST", "/compute/jobs", {"command": "echo hi"})
    assert (code, body["code"]) == (500, "job_workspace_unavailable"), body


def test_a_closed_manager_is_a_503(route):
    route.manager.close()
    body, code = route("POST", "/compute/jobs", {"command": "echo hi"})
    assert (code, body["code"]) == (503, "job_manager_closed"), body


@pytest.mark.stubbed_backend
def test_a_cancel_that_cannot_stop_a_live_job_is_not_a_404(route, monkeypatch):
    """The job exists and is still running; the stop did not end it.

    Signal *delivery* is the stub -- the same seam
    `test_local_jobs.test_a_cancel_that_cannot_stop_the_job_reports_failure`
    uses -- so the process is real and really is still running when cancel
    answers. Answered 404 `not_found`, a client stopped tracking it.
    """
    submitted, code = route(
        "POST", "/compute/jobs", {"command": "sleep 120", "kind": "bash"}
    )
    assert code == 200, submitted
    job = route.manager._jobs[submitted["id"]]
    assert _wait_for(lambda: job._proc is not None)
    try:
        monkeypatch.setattr(
            "openai4s.execution.process_group.signal_group",
            lambda proc, pgid, sig: None,
        )
        monkeypatch.setattr("openai4s.execution.process_group.TERM_GRACE_S", 0.2)

        body, code = route("POST", f"/compute/jobs/{submitted['id']}/cancel")
        assert code == 500, body
        assert body["code"] == "job_cancel_failed"
        assert body["ok"] is False and body["status"] == "running"

        row, code = route("GET", f"/compute/jobs/{submitted['id']}")
        assert code == 200 and row["status"] == "running"
    finally:
        monkeypatch.undo()
        try:
            os.killpg(os.getpgid(job._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def test_an_error_with_an_unmapped_code_is_never_a_200():
    """A refusal added to the manager without a table entry must still fail."""
    status = gateway_mod._soft_failure_status
    assert status({"error": "x"}, JOB_FAILURE_STATUS) == 400
    assert status({"error": "x", "code": "job_brand_new"}, JOB_FAILURE_STATUS) == 400
    assert status({"ok": True, "status": "cancelled"}, JOB_FAILURE_STATUS) == 200


def test_every_code_the_manager_returns_has_a_status():
    source = Path(jobs_mod.__file__).read_text(encoding="utf-8")
    returned = set(re.findall(r'"code": "(job_[a-z_]+)"', source))
    assert returned, "the scan found no codes; the pattern no longer matches"
    assert returned <= set(JOB_FAILURE_STATUS), returned - set(JOB_FAILURE_STATUS)


def test_a_real_job_reads_and_cancels_with_200(route):
    submitted, code = route(
        "POST",
        "/compute/jobs",
        {"command": "import time; time.sleep(30)", "kind": "python"},
    )
    assert code == 200, submitted
    job_id = submitted["id"]
    try:
        row, code = route("GET", f"/compute/jobs/{job_id}")
        assert code == 200
        assert row["id"] == job_id and row["status"] in ("queued", "running")

        stopped, code = route("POST", f"/compute/jobs/{job_id}/cancel")
        assert code == 200
        assert stopped == {"ok": True, "status": "cancelled"}

        # Read the terminal row too: the frozen [ok] shape must cover both
        # the running and the finished state (exit_code/finished_at are
        # null in one and set in the other).
        row, code = route("GET", f"/compute/jobs/{job_id}")
        assert code == 200
        assert row["status"] == "cancelled"
    finally:
        # Never leave a real process behind, whatever the asserts did.
        # Cancel on a terminal job is idempotent.
        route("POST", f"/compute/jobs/{job_id}/cancel")
