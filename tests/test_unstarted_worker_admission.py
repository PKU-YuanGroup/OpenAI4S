"""A worker that never started must not hold the session forever.

`4477209` made a turn release its ticket when the *turn* fails. It does not
cover the turn that never began: `Thread.start()` can raise — a thread limit, a
memory ceiling, an interpreter shutting down — and at that moment the ticket is
already submitted, the job is already in `_jobs`, and (for a plan) the row is
already compare-and-swapped into `executing`.

What that leaves behind, measured by injecting a real failure into
`Thread.start`:

* an idle plan rolled its claim back but left the un-started ticket owning the
  session, in `_jobs`, and in the coordinator's maps;
* a plan queued behind a running turn stayed in the queue forever — nothing
  ever admits it, because the thing that would have finished never ran;
* a direct message or REPL left an active owner and an unfinished job, so
  `is_running` answered True for a session with nothing running.

The fix has one subtlety worth stating: the ordinary `cancel` is wrong here. It
reads `ticket.state` and *then* acts, so the FIFO can admit the ticket in
between — and its RUNNING branch asks the runtime to interrupt a thread that
does not exist. `abort_unstarted` never reads the state: it asks the
coordinator to cancel the exact queued ticket, and only if that says "not
queued" does it fail the exact active one. The coordinator arbitrates, and the
maps are cleared by the terminal event either branch emits.
"""

from __future__ import annotations

import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id):
        return self.events.append

    def broadcast(self, root_frame_id, event):
        self.events.append(event)

    def has_subscriber(self, root_frame_id):
        return False

    def is_running(self, root_frame_id):
        return False


class _StartFailed(RuntimeError):
    """What a real `Thread.start` raises when it cannot get a thread."""


@pytest.fixture
def runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=2,
    )
    made = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    yield made
    made.close()


@pytest.fixture
def frame_id(runner):
    return runner.store.new_frame(kind="turn", project_id="proj", status="ready")


@pytest.fixture
def break_worker_start(monkeypatch):
    """Fail `Thread.start` for openai4s worker threads only.

    Selective on purpose. Breaking every thread start would take the store, the
    hub and the coordinator's own machinery down with it, and the resulting
    wreckage would pass any assertion about "nothing was left behind".
    """
    real_start = threading.Thread.start
    broken: list[str] = []

    def start(self):
        name = str(getattr(self, "name", ""))
        if name.startswith(("openai4s-turn-", "openai4s-plan-", "openai4s-repl-")):
            broken.append(name)
            raise _StartFailed("cannot allocate a worker thread")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    return broken


def _draft(runner, frame_id, status="draft"):
    return runner.store.create_plan(
        frame_id=frame_id,
        project_id="proj",
        title="p",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "s", "detail": "d", "deliverables": []}],
        status=status,
    )


def _plan_status(runner, frame_id):
    return (runner.store.get_plan_by_frame(frame_id) or {}).get("status")


def assert_session_is_idle(runner, frame_id, *, where: str) -> None:
    """Nothing owns it, nothing is queued, nothing is tracked, nothing runs."""
    state = runner.executions.snapshot(frame_id)
    assert not state.get("owner"), f"{where}: an un-started ticket still owns it"
    assert not state.get("queue"), f"{where}: a queued ticket was left behind"
    assert state.get("active_count") in (0, None), where
    assert not [
        job for job in runner._jobs.values() if job.root_frame_id == frame_id
    ], f"{where}: an un-started job is still tracked"
    assert runner.is_running(frame_id) is False, f"{where}: is_running is stuck True"
    # The coordinator's own maps, not just its projection.
    assert not runner.executions._tickets, f"{where}: a ticket map entry leaked"
    assert not runner.executions._positions, f"{where}: a queue position leaked"


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "submit, claimed_from",
    [("submit_plan_approval", "draft"), ("submit_plan_resume", "paused")],
)
def test_a_plan_whose_worker_never_started_leaves_nothing_behind(
    runner, frame_id, break_worker_start, submit, claimed_from
):
    """Approve claims `draft`, resume claims `paused`, and the rollback has to
    return the row to the state its own route can claim again -- putting an
    approved plan back to `paused` would strand it just as surely."""
    _draft(runner, frame_id, status=claimed_from)
    plan = runner.store.get_plan_by_frame(frame_id)
    runner.store.compare_and_set_plan_status(
        plan["plan_id"], expected=claimed_from, new_status="executing"
    )

    with pytest.raises(_StartFailed):
        getattr(runner, submit)(frame_id, "proj", None, claimed_plan_id=plan["plan_id"])

    assert break_worker_start, "the injection never fired"
    assert_session_is_idle(runner, frame_id, where=submit)
    assert _plan_status(runner, frame_id) == claimed_from


def test_a_plan_queued_behind_a_running_turn_is_not_stranded(
    runner, frame_id, break_worker_start, monkeypatch
):
    """The queued case is the one a state-reading cancel gets wrong.

    Owner A holds the session, so the new ticket is QUEUED rather than active.
    Nothing will ever admit it, because the worker that would have finished it
    never ran -- and every later turn waits behind it.
    """
    owner = runner.executions.submit(frame_id, owner="agent", owner_id="owner-a")
    assert runner.executions.snapshot(frame_id).get("owner")

    _draft(runner, frame_id)
    plan = runner.store.get_plan_by_frame(frame_id)
    runner.store.compare_and_set_plan_status(
        plan["plan_id"], expected="draft", new_status="executing"
    )

    with pytest.raises(_StartFailed):
        runner.submit_plan_approval(
            frame_id, "proj", None, claimed_plan_id=plan["plan_id"]
        )

    state = runner.executions.snapshot(frame_id)
    assert (state.get("owner") or {}).get("execution_id") == owner.execution_id
    assert state.get("queue") == [], f"the un-started plan stayed queued: {state}"
    assert not [job for job in runner._jobs.values() if job.root_frame_id == frame_id]
    assert _plan_status(runner, frame_id) == "draft"


# --------------------------------------------------------------------------
# direct submissions
# --------------------------------------------------------------------------


def test_a_message_whose_worker_never_started_leaves_nothing_behind(
    runner, frame_id, break_worker_start
):
    with pytest.raises(_StartFailed):
        runner.submit_message(frame_id, "proj", "do the thing")

    assert break_worker_start, "the injection never fired"
    assert_session_is_idle(runner, frame_id, where="submit_message")


def test_a_plan_revision_whose_worker_never_started_leaves_nothing_behind(
    runner, frame_id, break_worker_start
):
    with pytest.raises(_StartFailed):
        runner.submit_plan_revision(frame_id, "proj", None)

    assert break_worker_start, "the injection never fired"
    assert_session_is_idle(runner, frame_id, where="submit_plan_revision")


def test_a_repl_whose_worker_never_started_leaves_nothing_behind(
    runner, frame_id, break_worker_start
):
    with pytest.raises(_StartFailed):
        runner.submit_repl(frame_id, "proj", "1 + 1", language="python")

    assert break_worker_start, "the injection never fired"
    assert_session_is_idle(runner, frame_id, where="submit_repl")


def test_the_failure_reaches_the_caller_rather_than_a_202(
    runner, frame_id, break_worker_start
):
    """A 202 says "accepted, it is running". Returning one for a turn that
    never started is the one answer the caller cannot recover from: it will
    wait for a terminal event that nobody will ever emit."""
    with pytest.raises(_StartFailed) as caught:
        runner.submit_message(frame_id, "proj", "do the thing")
    assert "cannot allocate a worker thread" in str(caught.value)


def test_a_queued_worker_that_never_started_reads_as_cancelled_not_failed(
    runner, frame_id, break_worker_start
):
    """`fail()` would also release a queued ticket, so releasing it is not what
    the queued branch is for -- the terminal status is.

    A turn that never began did not *fail*: nothing ran, nothing threw, and a
    `failed` terminal tells a watching client the work was attempted and went
    wrong. `cancelled` says what happened. The distinction is the difference
    between a user retrying and a user filing a bug.
    """
    runner.executions.submit(frame_id, owner="agent", owner_id="owner-a")
    _draft(runner, frame_id)
    plan = runner.store.get_plan_by_frame(frame_id)
    runner.store.compare_and_set_plan_status(
        plan["plan_id"], expected="draft", new_status="executing"
    )

    with pytest.raises(_StartFailed):
        runner.submit_plan_approval(
            frame_id, "proj", None, claimed_plan_id=plan["plan_id"]
        )

    terminal = [
        event
        for event in runner.hub.events
        if event.get("type") == "execution_state"
        and event.get("status") in {"cancelled", "failed"}
    ]
    assert terminal, "no terminal event was emitted for the un-started ticket"
    assert terminal[-1]["status"] == "cancelled", terminal[-1]


# --------------------------------------------------------------------------
# every cleanup obligation, including when the cleanup itself faults
# --------------------------------------------------------------------------


class _HostileStart(RuntimeError):
    """A start failure whose message cannot be rendered.

    `coordinator._error_text` does `str(error)` to build the ticket's failure
    text. If that raises, the release raises with it — and the ticket it was
    releasing stays owned. Nothing on a cleanup path may depend on formatting
    an exception it did not author.
    """

    rendered = False

    def __str__(self) -> str:  # type: ignore[override]
        type(self).rendered = True
        raise ValueError("this exception refuses to be rendered")


@pytest.fixture
def break_worker_start_with(monkeypatch):
    """Fail worker-thread starts with a caller-supplied exception."""

    def install(factory):
        real_start = threading.Thread.start

        def start(self):
            name = str(getattr(self, "name", ""))
            if name.startswith(("openai4s-turn-", "openai4s-plan-", "openai4s-repl-")):
                raise factory()
            return real_start(self)

        monkeypatch.setattr(threading.Thread, "start", start)

    return install


def test_an_unstarted_job_is_terminalised_not_merely_forgotten(
    runner, frame_id, break_worker_start, monkeypatch
):
    """Popping the job from `_jobs` is not finishing it.

    `wait_result()` blocks on `job.done`, which only `finish` sets. A caller
    already waiting -- the `wait:true` branch of the message route is exactly
    that -- would wait on an event nobody will ever set, for a job the registry
    has already discarded.

    Observed through a spy on `finish` rather than by racing a thread to grab
    the job: the job is registered and popped within one call, so a poller
    would pass or fail on scheduling.
    """
    calls: list = []
    real_finish = gateway_mod.MessageJob.finish

    def spy(self, result=None, error=None):
        calls.append((self.job_id, result, error))
        return real_finish(self, result=result, error=error)

    monkeypatch.setattr(gateway_mod.MessageJob, "finish", spy)

    finished: list = []
    real_abort = runner._abort_unstarted_job

    def capture(job, ticket, error, **kwargs):
        real_abort(job, ticket, error, **kwargs)
        finished.append(job)

    monkeypatch.setattr(runner, "_abort_unstarted_job", capture)

    with pytest.raises(_StartFailed):
        runner.submit_message(frame_id, "proj", "do the thing")

    assert calls, "the un-started job was never finished, only forgotten"
    assert calls[-1][2], "the job was woken with no failure recorded"
    assert finished and finished[0].done.is_set(), "a waiter would still block"
    assert finished[0].error == runner.UNSTARTED_WORKER_MESSAGE


def test_a_start_failure_that_cannot_be_rendered_still_releases(
    runner, frame_id, break_worker_start_with
):
    """The abort reason must not be built by formatting the original."""
    _HostileStart.rendered = False
    break_worker_start_with(_HostileStart)

    with pytest.raises(_HostileStart):
        runner.submit_message(frame_id, "proj", "do the thing")

    assert_session_is_idle(runner, frame_id, where="hostile __str__")


def test_the_original_start_exception_instance_is_what_propagates(
    runner, frame_id, break_worker_start_with
):
    """A cleanup that replaces the failure hides why the turn never began."""
    sentinel = _StartFailed("the exact instance")
    break_worker_start_with(lambda: sentinel)

    with pytest.raises(_StartFailed) as caught:
        runner.submit_message(frame_id, "proj", "do the thing")
    assert caught.value is sentinel


def test_a_job_cleanup_fault_still_releases_the_ticket(
    runner, frame_id, break_worker_start, monkeypatch
):
    """Each obligation is independent; one failing must not skip the others."""

    class _Hostile(dict):
        def pop(self, *args, **kwargs):
            raise RuntimeError("the registry refused")

    monkeypatch.setattr(runner, "_jobs", _Hostile(runner._jobs))
    with pytest.raises(_StartFailed):
        runner.submit_message(frame_id, "proj", "do the thing")

    state = runner.executions.snapshot(frame_id)
    assert not state.get("owner"), "a job-cleanup fault stranded the ticket"
    assert not runner.executions._tickets


def test_a_plan_rollback_fault_still_releases_the_ticket_and_job(
    runner, frame_id, break_worker_start, monkeypatch
):
    _draft(runner, frame_id)
    plan = runner.store.get_plan_by_frame(frame_id)
    runner.store.compare_and_set_plan_status(
        plan["plan_id"], expected="draft", new_status="executing"
    )

    def boom(*args, **kwargs):
        raise RuntimeError("the plan row refused")

    monkeypatch.setattr(runner, "_settle_claimed_plan", boom)
    with pytest.raises(_StartFailed):
        runner.submit_plan_approval(
            frame_id, "proj", None, claimed_plan_id=plan["plan_id"]
        )

    state = runner.executions.snapshot(frame_id)
    assert not state.get("owner"), "a plan-rollback fault stranded the ticket"
    assert not [j for j in runner._jobs.values() if j.root_frame_id == frame_id]


def test_a_hub_emit_fault_does_not_leak_the_coordinator_maps(runner, frame_id):
    """`_on_core_event` popped `_tickets`/`_positions` *after* handing the
    event to an external sink. A sink that raises therefore leaked both, and
    the leak outlives the turn it belonged to."""
    ticket = runner.executions.submit(frame_id, owner="agent", owner_id="emit-fault")

    def boom(root_frame_id, event):
        raise RuntimeError("the socket went away")

    runner.hub.broadcast = boom  # type: ignore[method-assign]
    runner.hub.emitter = lambda root_frame_id: boom  # type: ignore[method-assign]

    try:
        runner.executions.abort_unstarted(ticket, RuntimeError("never started"))
    except Exception:
        pass

    assert (
        ticket.execution_id not in runner.executions._tickets
    ), "a failing event sink leaked the ticket map"
    assert ticket.execution_id not in runner.executions._positions
