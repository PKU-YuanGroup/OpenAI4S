"""Session shutdown must not leak independent background kernels."""

from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from openai4s.kernel import InterruptDelivery
from openai4s.kernel import background as background_mod
from openai4s.kernel.background import BackgroundExecutor
from openai4s.server.errors import GatewayError
from openai4s.server.trusted_capture import TrustedCaptureCoordinator


class _HungKernel:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.interrupt_calls = 0
        self.kill_calls = 0
        self.shutdown_calls = 0

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin, on_chunk
        self.entered.set()
        self.release.wait(2)
        raise RuntimeError("worker exited")

    def interrupt(self):
        self.interrupt_calls += 1

    def kill_worker(self):
        self.kill_calls += 1
        self.release.set()

    def shutdown(self):
        self.shutdown_calls += 1


def test_shutdown_interrupts_then_kills_hung_background_workers():
    kernel = _HungKernel()
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    launched = executor.launch("hang()")
    assert kernel.entered.wait(1)

    assert executor.shutdown(timeout_per_job=0.01) == 1
    assert kernel.interrupt_calls == 1
    assert kernel.kill_calls == 1
    assert kernel.shutdown_calls == 1
    assert executor.peek(launched["exec_id"])["status"] == "failed"
    with pytest.raises(RuntimeError, match="closed"):
        executor.launch("print('late')")


def test_background_admission_lease_spans_the_worker_lifetime():
    """A returned exec id is still an active writer until its thread exits."""

    kernel = _HungKernel()
    admission = TrustedCaptureCoordinator(enabled=True)
    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=admission.background,
    )
    launched = executor.launch("hang()")
    assert kernel.entered.wait(1)

    with pytest.raises(GatewayError) as failure:
        with admission.capture():
            raise AssertionError("capture must not overlap a background worker")
    assert failure.value.error_code == "trusted_capture_busy"

    kernel.release.set()
    job = executor._get(launched["exec_id"])
    assert job._thread is not None
    job._thread.join(1)
    assert not job._thread.is_alive()
    assert executor.peek(launched["exec_id"])["done"] is True
    executor.shutdown(timeout_per_job=1)
    with admission.capture():
        pass


def test_background_writer_gate_spans_lifetime_when_capture_is_disabled():
    """Stage 1 controls capture, not workspace-writer exclusion."""

    kernel = _HungKernel()
    admission = TrustedCaptureCoordinator(enabled=False)
    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=admission.background,
    )
    launched = executor.launch("hang()")
    assert kernel.entered.wait(1)

    # Capture remains the flag-off compatibility no-op.
    with admission.capture():
        pass
    with pytest.raises(GatewayError) as failure:
        with admission.external_mutation():
            raise AssertionError("external mutation must not overlap background")
    assert failure.value.error_code == "trusted_capture_busy"

    kernel.release.set()
    job = executor._get(launched["exec_id"])
    assert job._thread is not None
    job._thread.join(1)
    assert not job._thread.is_alive()
    executor.shutdown(timeout_per_job=1)
    with admission.external_mutation():
        pass


def test_malformed_background_admission_refuses_before_kernel_creation():
    spawned = False

    def kernel_factory():
        nonlocal spawned
        spawned = True
        raise AssertionError("malformed admission must fail before spawn")

    executor = BackgroundExecutor(
        kernel_factory,
        dispatcher=None,
        lifetime_factory=lambda: object(),
    )

    with pytest.raises(RuntimeError, match="admission is unavailable"):
        executor.launch("print('must not run')")
    assert spawned is False
    assert executor.list_jobs() == []


def test_background_thread_start_failure_releases_capture_admission(monkeypatch):
    kernel = _HungKernel()
    admission = TrustedCaptureCoordinator(enabled=True)

    class CannotStart:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def start(self):
            raise RuntimeError("injected thread start failure")

    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=admission.background,
    )
    monkeypatch.setattr(
        background_mod,
        "threading",
        SimpleNamespace(Thread=CannotStart, Lock=threading.Lock),
    )

    with pytest.raises(RuntimeError, match="injected thread start failure"):
        executor.launch("print('must not run')")
    assert kernel.shutdown_calls == 1
    assert executor.list_jobs() == []
    with admission.capture():
        pass


def test_background_base_exception_has_terminal_public_state_and_reuses_slot():
    admission = TrustedCaptureCoordinator(enabled=True)

    class InterruptingKernel:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            raise KeyboardInterrupt("private worker detail")

        def shutdown(self):
            self.shutdown_calls += 1

    class SuccessfulKernel:
        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            return {"stdout": "ok"}

        def shutdown(self):
            pass

    interrupted = InterruptingKernel()
    kernels = iter((interrupted, SuccessfulKernel()))
    executor = BackgroundExecutor(
        lambda: next(kernels),
        dispatcher=None,
        lifetime_factory=admission.background,
    )
    executor.MAX_ACTIVE_JOBS = 1

    first_id = executor.launch("interrupt()")["exec_id"]
    first = executor._get(first_id)
    assert first._thread is not None
    first._thread.join(1)

    assert not first._thread.is_alive()
    assert executor.peek(first_id) == {
        "exec_id": first_id,
        "status": "failed",
        "done": True,
        "stdout": "",
        "interrupted": False,
        "error": "background execution failed",
        "started_at": first.started_at,
        "ended_at": first.ended_at,
        "persistent": False,
    }
    assert interrupted.shutdown_calls == 1
    # The lifetime is released and the status no longer consumes the only
    # process slot, so both foreground capture and a later launch can proceed.
    with admission.capture():
        pass
    second_id = executor.launch("print('ok')")["exec_id"]
    second = executor._get(second_id)
    assert second._thread is not None
    second._thread.join(1)
    assert executor.peek(second_id)["status"] == "done"


def test_background_shutdown_base_exception_still_releases_admission():
    admission = TrustedCaptureCoordinator(enabled=True)

    class BrokenShutdownKernel:
        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            return {"stdout": "completed before cleanup"}

        def shutdown(self):
            raise KeyboardInterrupt("private cleanup detail")

    executor = BackgroundExecutor(
        BrokenShutdownKernel,
        dispatcher=None,
        lifetime_factory=admission.background,
    )
    exec_id = executor.launch("print('done')")["exec_id"]
    job = executor._get(exec_id)
    assert job._thread is not None
    job._thread.join(1)

    assert not job._thread.is_alive()
    result = executor.peek(exec_id)
    assert result["status"] == "failed"
    assert result["done"] is True
    assert result["error"] == "background execution cleanup failed"
    with admission.capture():
        pass


def test_executor_shutdown_does_not_skip_a_job_blocked_in_cleanup():
    """Terminal status is not public until cleanup and its lease both finish."""

    cleanup_entered = threading.Event()
    cleanup_release = threading.Event()
    lifetime_released = threading.Event()

    class CleanupBlockingKernel:
        def __init__(self) -> None:
            self.interrupt_calls = 0
            self.kill_calls = 0

        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            return {"stdout": "execution already returned"}

        def shutdown(self):
            cleanup_entered.set()
            if not cleanup_release.wait(2):
                raise AssertionError("executor shutdown did not unblock cleanup")

        def interrupt(self):
            self.interrupt_calls += 1

        def kill_worker(self):
            self.kill_calls += 1
            cleanup_release.set()

    class Lifetime:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            lifetime_released.set()

    kernel = CleanupBlockingKernel()
    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=Lifetime,
    )
    exec_id = executor.launch("print('done')")["exec_id"]
    assert cleanup_entered.wait(1)

    # execute() has returned, but cleanup and the external-admission lifetime
    # are still live. Session shutdown must continue to treat this as running.
    before = executor.peek(exec_id)
    assert before["status"] == "running"
    assert before["done"] is False
    assert before["ended_at"] is None
    assert lifetime_released.is_set() is False

    assert executor.shutdown(timeout_per_job=0.1) == 1
    assert kernel.interrupt_calls == 1
    assert kernel.kill_calls == 1
    assert lifetime_released.is_set() is True
    after = executor.peek(exec_id)
    assert after["status"] == "done"
    assert after["done"] is True
    assert after["ended_at"] is not None


def test_external_mutation_is_reentrant_but_exclusive_with_writers():
    admission = TrustedCaptureCoordinator(enabled=True)

    with admission.external_mutation():
        with admission.external_mutation():
            pass
        with pytest.raises(GatewayError) as capture_failure:
            with admission.capture():
                raise AssertionError("capture must not enter a mutation lifetime")
        with pytest.raises(GatewayError) as background_failure:
            with admission.background():
                raise AssertionError("background must not enter a mutation lifetime")

    with admission.capture():
        with pytest.raises(GatewayError) as mutation_during_capture:
            with admission.external_mutation():
                raise AssertionError("mutation must not enter a capture lifetime")
    with admission.background():
        with pytest.raises(GatewayError) as mutation_during_background:
            with admission.external_mutation():
                raise AssertionError("mutation must not enter a background lifetime")

    assert {
        capture_failure.value.error_code,
        background_failure.value.error_code,
        mutation_during_capture.value.error_code,
        mutation_during_background.value.error_code,
    } == {"trusted_capture_busy"}


def test_external_and_background_writers_remain_exclusive_when_capture_is_disabled():
    admission = TrustedCaptureCoordinator(enabled=False)

    with admission.external_mutation():
        # Capture is deliberately still absent before Stage 1 rollout.
        with admission.capture():
            pass
        with pytest.raises(GatewayError) as background_failure:
            with admission.background():
                raise AssertionError("background must not overlap mutation")

    with admission.background():
        with admission.capture():
            pass
        with pytest.raises(GatewayError) as mutation_failure:
            with admission.external_mutation():
                raise AssertionError("mutation must not overlap background")

    assert background_failure.value.error_code == "trusted_capture_busy"
    assert mutation_failure.value.error_code == "trusted_capture_busy"


def test_external_mutation_rejects_a_different_owner_thread():
    admission = TrustedCaptureCoordinator(enabled=True)
    entered = threading.Event()
    release = threading.Event()
    owner_errors: list[BaseException] = []

    def hold_mutation() -> None:
        try:
            with admission.external_mutation():
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test did not release mutation owner")
        except BaseException as error:
            owner_errors.append(error)

    owner = threading.Thread(target=hold_mutation)
    owner.start()
    try:
        assert entered.wait(1)
        with pytest.raises(GatewayError) as failure:
            with admission.external_mutation():
                raise AssertionError("contending mutation must not enter")
        assert failure.value.error_code == "trusted_capture_busy"
    finally:
        release.set()
        owner.join(2)
    assert not owner.is_alive()
    assert owner_errors == []


def test_external_mutation_release_corruption_poisons_all_later_admission():
    admission = TrustedCaptureCoordinator(enabled=True)

    with pytest.raises(ValueError, match="primary failure"):
        with admission.external_mutation():
            admission._mutations = 0  # fault injection under the owning thread
            raise ValueError("primary failure")

    for lease in (
        admission.capture,
        admission.background,
        admission.external_mutation,
    ):
        with pytest.raises(GatewayError) as failure:
            with lease():
                raise AssertionError("a poisoned coordinator must refuse")
        assert failure.value.error_code == "trusted_capture_unavailable"


def test_capture_release_corruption_does_not_mask_and_permanently_fails_closed():
    admission = TrustedCaptureCoordinator(enabled=True)

    with pytest.raises(ValueError, match="primary failure"):
        with admission.capture():
            admission._captures = 0  # fault injection: impossible under its lock
            raise ValueError("primary failure")

    with pytest.raises(GatewayError) as capture_failure:
        with admission.capture():
            raise AssertionError("a poisoned coordinator must not admit capture")
    with pytest.raises(GatewayError) as background_failure:
        with admission.background():
            raise AssertionError("a poisoned coordinator must not admit background")
    with pytest.raises(GatewayError) as mutation_failure:
        with admission.external_mutation():
            raise AssertionError("a poisoned coordinator must not admit mutation")
    assert capture_failure.value.error_code == "trusted_capture_unavailable"
    assert background_failure.value.error_code == "trusted_capture_unavailable"
    assert mutation_failure.value.error_code == "trusted_capture_unavailable"


@pytest.mark.parametrize(
    ("captures", "owner", "backgrounds"),
    [
        (1, None, 0),
        (0, "current_thread", 0),
        (1, "current_thread", 1),
        ("one", "current_thread", 0),
    ],
    ids=(
        "capture-without-owner",
        "owner-without-capture",
        "capture-overlaps-background",
        "non-integer-capture-count",
    ),
)
def test_malformed_capture_state_fails_closed_before_admission(
    captures, owner, backgrounds
):
    admission = TrustedCaptureCoordinator(enabled=True)
    admission._captures = captures
    admission._capture_owner = (
        threading.get_ident() if owner == "current_thread" else owner
    )
    admission._backgrounds = backgrounds

    with pytest.raises(GatewayError) as first:
        with admission.capture():
            raise AssertionError("malformed state must not admit capture")
    with pytest.raises(GatewayError) as second:
        with admission.background():
            raise AssertionError("poisoned state must not admit background")

    assert first.value.error_code == "trusted_capture_unavailable"
    assert second.value.error_code == "trusted_capture_unavailable"


def test_a_background_cell_buffer_does_not_grow_without_bound():
    """`_buf` was an unbounded list, and `stdout_so_far` re-joined all of it.

    A long-running background cell is exactly the case where that matters: the
    agent polls `exec_peek` to watch progress, so a chatty job grew the
    daemon's memory for the life of the job *and* made each poll more expensive
    than the last. Nothing evicted, nothing capped, no marker.

    The worker now bounds its own stream, so this is a backstop -- but it is
    the buffer's own contract that was missing, and a buffer that is only safe
    because of what feeds it is one refactor from being unsafe again.

    Head-capped rather than ring-buffered on purpose: `exec_peek` and the final
    response then truncate at the same point, instead of showing two different
    prefixes of the same cell.
    """
    from openai4s.kernel.background import MAX_PEEK_CHARS, _BackgroundJob

    job = _BackgroundJob("bg-1", "print('x')")

    job._on_chunk("small")
    assert job.stdout_so_far() == "small"

    job._on_chunk("y" * (MAX_PEEK_CHARS * 2))
    seen = job.stdout_so_far()
    assert len(seen) <= MAX_PEEK_CHARS + len("\n...(truncated at N characters)") + 8
    assert seen.count("...(truncated at") == 1

    # Still one marker after further writes, not one per chunk.
    job._on_chunk("z" * 1000)
    assert job.stdout_so_far().count("...(truncated at") == 1


class _UninterruptibleKernel(_HungKernel):
    """A kernel whose stop request reaches nobody, and says so.

    It releases the cell anyway, so the executor's five-second join returns at
    once. What is under test is that the delivery verdict reaches the caller's
    report, not how long a genuinely stuck cell keeps running.
    """

    def interrupt(self):
        self.interrupt_calls += 1
        self.release.set()
        return InterruptDelivery(
            False, "sandbox", "bubblewrap did not provide a pinned command identity"
        )


def test_a_stop_that_reached_nobody_is_reported_to_the_caller():
    """`status` cannot carry this. "running" cannot distinguish "still
    unwinding" from "that request did nothing and repeating it will do nothing
    either", and a cell that then fails for its own reasons reports "failed"
    with the dropped stop nowhere in the answer. The sandbox already knew which
    it was and printed the diagnosis to stderr, where the agent that asked for
    the stop cannot read it."""

    kernel = _UninterruptibleKernel()
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    launched = executor.launch("hang()")
    try:
        assert kernel.entered.wait(1)
        report = executor.interrupt(launched["exec_id"])
        assert kernel.interrupt_calls == 1
        assert "pinned command identity" in report["interrupt_undelivered"]
    finally:
        kernel.release.set()
        executor.shutdown(timeout_per_job=1.0)


def test_a_delivered_stop_adds_no_undelivered_note():
    """The note must appear only when the stop really did not land, or it is
    noise that trains its reader to ignore it."""

    class _StoppableKernel(_HungKernel):
        def interrupt(self):
            self.interrupt_calls += 1
            self.release.set()
            return InterruptDelivery(True, "local-process")

    kernel = _StoppableKernel()
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    launched = executor.launch("hang()")
    try:
        assert kernel.entered.wait(1)
        report = executor.interrupt(launched["exec_id"])
        assert "interrupt_undelivered" not in report
    finally:
        kernel.release.set()
        executor.shutdown(timeout_per_job=1.0)


def test_a_kernel_that_makes_no_delivery_claim_is_not_reported_as_failed():
    """`None` is "no claim either way". Reading it as "not delivered" would
    manufacture a failure out of an absent answer -- the same dishonesty this
    change exists to remove, pointed the other way."""

    kernel = _HungKernel()  # its interrupt() returns None
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    launched = executor.launch("hang()")
    try:
        assert kernel.entered.wait(1)
        kernel.release.set()
        report = executor.interrupt(launched["exec_id"])
        assert "interrupt_undelivered" not in report
    finally:
        kernel.release.set()
        executor.shutdown(timeout_per_job=1.0)


class _DegradedBackgroundKernel:
    def __init__(self) -> None:
        self.shutdown_calls = 0
        self.executed = 0

    @property
    def sandbox_status(self) -> dict:
        return {
            "mode": "off",
            "state": "disabled",
            "backend": None,
            "enforced": False,
            "self_test_passed": None,
            "network_policy": "not_enforced",
        }

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin, on_chunk
        self.executed += 1
        return {"stdout": "ok", "error": None}

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_allowlist_refuses_a_degraded_background_kernel_before_its_thread(
    monkeypatch,
):
    """Allowlist refusal happens after spawn and before the worker thread."""

    from openai4s.egress import EgressBoundaryUnavailable

    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    kernel = _DegradedBackgroundKernel()
    exits: list[int] = []

    class _Life:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            exits.append(1)
            return False

    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=lambda: _Life(),
    )
    with pytest.raises(EgressBoundaryUnavailable) as failure:
        executor.launch("print(1)")
    assert failure.value.code == "egress_boundary_unavailable"
    assert executor._jobs == {}
    assert kernel.shutdown_calls == 1
    assert kernel.executed == 0
    assert exits == [1]

    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)
    second = executor.launch("print(2)")
    assert list(executor._jobs) == [second["exec_id"]]
    job = executor._get(second["exec_id"])
    assert job._thread is not None
    job._thread.join(2)
    executor.shutdown(timeout_per_job=1.0)


def test_allowlist_treats_a_missing_background_posture_as_unproven(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")

    class _Bare:
        def __init__(self) -> None:
            self.closed = False

        def shutdown(self) -> None:
            self.closed = True

        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            raise AssertionError("an unproven kernel must not run")

    from openai4s.egress import EgressBoundaryUnavailable

    kernel = _Bare()
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    with pytest.raises(EgressBoundaryUnavailable) as failure:
        executor.launch("print(1)")
    assert failure.value.code == "egress_boundary_unavailable"
    assert executor._jobs == {}
    assert kernel.closed is True


def test_background_thread_records_a_boundary_refusal(monkeypatch):
    """A mode flip after launch is still the stable code, not a generic failure."""

    from openai4s.egress import EGRESS_BOUNDARY_UNAVAILABLE, EgressBoundaryUnavailable

    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)

    class _FlippingKernel:
        def execute(self, code, origin="agent", on_chunk=None):
            del code, origin, on_chunk
            raise EgressBoundaryUnavailable(
                {
                    "code": EGRESS_BOUNDARY_UNAVAILABLE,
                    "reason": "mode flipped while the job was queued",
                }
            )

        def shutdown(self) -> None:
            return None

        def interrupt(self):
            return None

    kernel = _FlippingKernel()
    executor = BackgroundExecutor(lambda: kernel, dispatcher=None)
    launched = executor.launch("print(1)")
    job = executor._get(launched["exec_id"])
    assert job._thread is not None
    job._thread.join(2)
    peeked = executor.peek(launched["exec_id"])
    assert peeked["done"] is True
    assert peeked["error"].startswith("egress_boundary_unavailable:")
    assert "mode flipped while the job was queued" in peeked["error"]
    assert peeked["error"] != "background execution failed"
    executor.shutdown(timeout_per_job=1.0)


def test_host_exec_background_raises_the_boundary_code(tmp_path, monkeypatch):
    from openai4s.config import Config
    from openai4s.egress import EgressBoundaryUnavailable
    from openai4s.host_dispatch import build_dispatcher

    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    dispatcher = build_dispatcher(Config(data_dir=tmp_path / "data"))
    try:
        dispatcher.background_kernel_factory = _DegradedBackgroundKernel
        with pytest.raises(EgressBoundaryUnavailable) as failure:
            dispatcher._m_exec_background({"code": "print(1)"})
        assert failure.value.code == "egress_boundary_unavailable"
        assert str(failure.value).startswith("egress_boundary_unavailable:")
        assert dispatcher._bg_executor is not None
        assert dispatcher._bg_executor._jobs == {}
    finally:
        dispatcher.store.close()


# --- persistent receipts (v34) ---------------------------------------------


def _cfg_store(tmp_path):
    from openai4s.config import Config
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path / "data")
    return cfg, get_store(cfg.db_path)


def _root(store):
    return store.new_frame(kind="turn", project_id="default", status="ready")


def _dispatcher(cfg, root, instance, factory):
    from openai4s.host_dispatch import build_dispatcher

    dispatcher = build_dispatcher(cfg, frame_id=root)
    dispatcher.durable_background = True
    dispatcher.daemon_instance = instance
    dispatcher.background_kernel_factory = factory
    return dispatcher


class _OkKernel:
    def __init__(self, chunks=(), result=None, generation=None):
        self.chunks = chunks
        self.result = result or {"stdout": "ok", "error": None}
        self.shutdown_calls = 0
        self.executed = 0
        if generation is not None:
            self.authorization_generation = generation

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin
        self.executed += 1
        if on_chunk is not None:
            for chunk in self.chunks:
                on_chunk(chunk)
        return dict(self.result)

    def shutdown(self):
        self.shutdown_calls += 1

    def interrupt(self):
        return None


class _WaitKernel:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.chunks = ()

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin
        if on_chunk is not None:
            for chunk in self.chunks:
                on_chunk(chunk)
        self.entered.set()
        self.release.wait(2)
        return {"stdout": "", "error": None}

    def shutdown(self):
        self.release.set()

    def interrupt(self):
        self.release.set()
        return None

    def kill_worker(self):
        self.release.set()


def _join(dispatcher, exec_id):
    job = dispatcher._bg_executor._get(exec_id)
    assert job._thread is not None
    job._thread.join(2)
    assert not job._thread.is_alive()
    return job


def _raw(store, exec_id):
    row = store._conn.execute(
        "SELECT * FROM background_exec_receipts WHERE exec_id=?",
        (exec_id,),
    ).fetchone()
    assert row is not None
    return row


def test_receipt_exists_before_the_kernel_and_stores_only_the_digest(tmp_path):
    import hashlib

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    code = "print('secret-source-é')"
    seen = {}

    def factory():
        row = store._conn.execute("SELECT * FROM background_exec_receipts").fetchone()
        assert row is not None
        seen["row"] = dict(row)
        return _OkKernel(chunks=("out-é",), generation="kernel:real-gen")

    dispatcher = _dispatcher(cfg, root, "daemon-a", factory)
    try:
        launched = dispatcher._m_exec_background({"code": code, "origin": "agent"})
        assert launched["persistent"] is True
        assert launched["status"] == "running"
        _join(dispatcher, launched["exec_id"])
        during = seen["row"]
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        assert during["status"] == "launching"
        assert during["code_sha256"] == digest
        assert during["code_chars"] == len(code)
        assert during["env_generation"] is None
        assert during["root_frame_id"] == root
        blob = " ".join(str(value or "") for value in during.values())
        assert "secret-source" not in blob
        assert code not in blob
        receipt = dispatcher._bg().receipts.get(launched["exec_id"])
        assert receipt["status"] == "done"
        assert receipt["output"] == "out-é"
        assert receipt["ended_at"] is not None
        assert receipt["env_generation"] == "kernel:real-gen"
        peeked = dispatcher._m_exec_peek(launched["exec_id"])
        assert peeked["persistent"] is True
        assert peeked["stdout"] == "out-é"
        assert peeked["status"] == "done"
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_begin_failure_refuses_the_launch_before_any_kernel(tmp_path):
    calls = {"n": 0}

    class _Boom:
        def begin(self, **_kwargs):
            raise sqlite3.OperationalError("disk full")

    def factory():
        calls["n"] += 1
        raise AssertionError("kernel_factory ran after a failed receipt")

    executor = BackgroundExecutor(factory, dispatcher=None, receipts=_Boom())
    with pytest.raises(RuntimeError, match="background receipt could not be recorded"):
        executor.launch("print(1)")
    assert calls["n"] == 0
    assert executor.list_jobs() == []


def test_integer_kernel_generation_is_not_stored_as_env_generation(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)

    class _Gen(_OkKernel):
        generation = 0

    dispatcher = _dispatcher(cfg, root, "daemon-a", _Gen)
    try:
        launched = dispatcher._m_exec_background({"code": "x = 1"})
        _join(dispatcher, launched["exec_id"])
        assert _raw(store, launched["exec_id"])["env_generation"] is None
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_restart_reads_an_unfinished_job_as_outcome_unknown_and_does_not_replay(
    tmp_path,
):
    from openai4s.storage.background_execs import OUTCOME_UNKNOWN_ERROR

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _WaitKernel()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return kernel

    original = _dispatcher(cfg, root, "daemon-a", factory)
    restarted_calls = {"n": 0}
    restarted = _dispatcher(
        cfg,
        root,
        "daemon-b",
        lambda: restarted_calls.__setitem__("n", restarted_calls["n"] + 1),
    )
    try:
        launched = original._m_exec_background({"code": "while True: pass"})
        assert kernel.entered.wait(2)
        peeked = restarted._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "outcome_unknown"
        assert peeked["done"] is True
        assert peeked["persistent"] is True
        assert peeked["source"] == "receipt"
        assert peeked["error"] == OUTCOME_UNKNOWN_ERROR
        assert calls["n"] == 1
        assert restarted_calls["n"] == 0
        assert restarted._bg().list_jobs() == []
    finally:
        kernel.release.set()
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_effective_status_does_not_rewrite_a_foreign_running_row(tmp_path):
    """Read-time derivation, with prune deliberately not called."""

    from openai4s.storage.background_execs import BackgroundExecReceiptRepository

    _cfg, store = _cfg_store(tmp_path)
    repo = BackgroundExecReceiptRepository(
        store._conn, store._lock, clock_ms=lambda: 50
    )
    try:
        repo.begin(
            exec_id="exec-foreign",
            root_frame_id="root-a",
            frame_id="root-a",
            owner_user_id=None,
            daemon_instance="old-daemon",
            origin="agent",
            code_sha256="ab" * 32,
            code_chars=3,
        )
        seen = repo.get(
            "exec-foreign", root_frame_id="root-a", current_instance="new-daemon"
        )
        assert seen is not None
        assert seen["status"] == "outcome_unknown"
        assert (
            store._conn.execute(
                "SELECT status FROM background_exec_receipts WHERE exec_id=?",
                ("exec-foreign",),
            ).fetchone()["status"]
            == "launching"
        )
    finally:
        store.close()


def test_a_finished_receipt_survives_restart_on_peek_and_list(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    original = _dispatcher(
        cfg,
        root,
        "daemon-a",
        lambda: _OkKernel(chunks=("kept-output",)),
    )
    try:
        first = original._m_exec_background({"code": "print('one')"})
        _join(original, first["exec_id"])
        second = original._m_exec_background({"code": "print('two')"})
        _join(original, second["exec_id"])
        calls = {"n": 0}
        restarted = _dispatcher(
            cfg,
            root,
            "daemon-b",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )
        peeked = restarted._m_exec_peek(first["exec_id"])
        assert peeked["status"] == "done"
        assert peeked["stdout"] == "kept-output"
        assert peeked["source"] == "receipt"
        assert peeked["code_sha256"]
        listed = restarted._m_exec_list()
        by_id = {item["exec_id"]: item for item in listed}
        assert by_id[first["exec_id"]]["stdout"] == "kept-output"
        assert by_id[second["exec_id"]]["stdout"] == "kept-output"
        assert listed[0]["exec_id"] == second["exec_id"]
        assert calls["n"] == 0
        assert restarted._bg().list_jobs() == []
    finally:
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_another_session_cannot_read_the_receipt(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    other = _root(store)
    original = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("mine",)))
    try:
        launched = original._m_exec_background({"code": "print('mine')"})
        _join(original, launched["exec_id"])
        # Positive control: a fresh dispatcher for the same session reads the
        # receipt, so the refusal below is scope, not a missing row.
        same = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel())
        owned = same._m_exec_peek(launched["exec_id"])
        assert owned["source"] == "receipt"
        assert owned["stdout"] == "mine"
        stranger = _dispatcher(cfg, other, "daemon-a", lambda: _OkKernel())
        with pytest.raises(KeyError):
            stranger._m_exec_peek(launched["exec_id"])
        assert launched["exec_id"] not in {
            item["exec_id"] for item in stranger._m_exec_list()
        }
        assert (
            store.background_exec_receipts.get(
                launched["exec_id"],
                root_frame_id=other,
                current_instance="daemon-a",
            )
            is None
        )
    finally:
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_persisted_output_is_capped_without_shrinking_the_memory_peek(tmp_path):
    from openai4s.storage.background_execs import (
        MAX_PERSISTED_OUTPUT_BYTES,
        truncation_marker,
    )

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    chunk = "a" * 300_000
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=(chunk,)))
    try:
        launched = dispatcher._m_exec_background({"code": "print('lots')"})
        _join(dispatcher, launched["exec_id"])
        peeked = dispatcher._m_exec_peek(launched["exec_id"])
        assert peeked["stdout"] == chunk
        assert "truncated at" not in peeked["stdout"]
        receipt = _raw(store, launched["exec_id"])
        assert receipt["output_truncated"] == 1
        assert receipt["output_bytes"] <= MAX_PERSISTED_OUTPUT_BYTES
        assert truncation_marker(MAX_PERSISTED_OUTPUT_BYTES) in receipt["output"]
        assert len(receipt["output"].encode("utf-8")) <= MAX_PERSISTED_OUTPUT_BYTES
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_flush_writes_a_bounded_head_while_the_job_is_still_running(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _WaitKernel()
    kernel.chunks = ("z" * 64,)
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    dispatcher._bg()
    dispatcher._bg_executor.FLUSH_BYTES = 16
    dispatcher._bg_executor.FLUSH_INTERVAL_MS = 60_000
    try:
        launched = dispatcher._m_exec_background({"code": "stream"})
        assert kernel.entered.wait(2)
        receipt = _raw(store, launched["exec_id"])
        assert receipt["status"] == "running"
        assert receipt["output"] == "z" * 64
        assert receipt["output_truncated"] == 0
    finally:
        kernel.release.set()
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


class _QuietKernel(_WaitKernel):
    """Prints its chunks, then stays silent until released."""

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin
        if on_chunk is not None:
            for chunk in self.chunks:
                on_chunk(chunk)
        self.entered.set()
        self.release.wait(30)
        return {"stdout": "", "error": None}


def test_a_quiet_tail_reaches_the_receipt_without_a_later_chunk(tmp_path):
    """The interval flush used to run only when the next chunk arrived.

    A burst followed by silence left its tail in memory only, and a crash
    during the silence lost it. A deferred flush now writes it while the
    job is still running.
    """

    import time

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _QuietKernel()
    kernel.chunks = ("Loading data...\n", "epoch 1: loss=0.9\n")
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    dispatcher._bg()
    dispatcher._bg_executor.FLUSH_BYTES = 1 << 20
    dispatcher._bg_executor.FLUSH_INTERVAL_MS = 50
    expected = "Loading data...\nepoch 1: loss=0.9\n"
    try:
        launched = dispatcher._m_exec_background({"code": "stream"})
        assert kernel.entered.wait(2)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _raw(store, launched["exec_id"])["output"] == expected:
                break
            time.sleep(0.02)
        receipt = _raw(store, launched["exec_id"])
        assert receipt["output"] == expected
        assert receipt["status"] == "running"
    finally:
        kernel.release.set()
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_a_failing_output_write_backs_off_and_says_so(tmp_path):
    """A full disk used to be retried on every chunk, silently.

    After one failed write the flush waits a whole interval before trying
    again, and ``exec_peek`` reports that the receipt may be behind.
    """

    from openai4s.storage.background_execs import BoundBackgroundReceipts

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    attempts: list[str] = []

    class _FullDisk(BoundBackgroundReceipts):
        def save_output(self, exec_id, text, truncated):
            attempts.append(exec_id)
            raise sqlite3.OperationalError("database or disk is full")

    kernel = _QuietKernel()
    kernel.chunks = tuple("x" * 64 for _ in range(200))
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    dispatcher._bg()
    inner = dispatcher._bg_executor.receipts
    dispatcher._bg_executor.receipts = _FullDisk(
        inner._repository,
        root_frame_id=inner._root_frame_id,
        frame_id=inner._frame_id,
        daemon_instance=inner._daemon_instance,
        owner_user_id=inner._owner_user_id,
    )
    dispatcher._bg_executor.FLUSH_BYTES = 16
    dispatcher._bg_executor.FLUSH_INTERVAL_MS = 60_000
    try:
        launched = dispatcher._m_exec_background({"code": "stream"})
        assert kernel.entered.wait(2)
        assert len(attempts) == 1
        peeked = dispatcher._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "running"
        assert peeked["receipt_degraded"] is True
    finally:
        kernel.release.set()
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_prune_drops_expired_terminals_and_clears_oldest_output_first(tmp_path):
    from openai4s.storage.background_execs import (
        OUTCOME_UNKNOWN_ERROR,
        BackgroundExecReceiptRepository,
    )

    _cfg, store = _cfg_store(tmp_path)
    repo = BackgroundExecReceiptRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 10_000,
        max_output_bytes=80,
        max_total_output_bytes=100,
        terminal_ttl_ms=1_000,
    )

    def start(exec_id, daemon):
        repo.begin(
            exec_id=exec_id,
            root_frame_id="root-a",
            frame_id="root-a",
            owner_user_id=None,
            daemon_instance=daemon,
            origin="agent",
            code_sha256="cd" * 32,
            code_chars=1,
        )

    try:
        start("exec-old", "daemon-a")
        repo.finish(
            "exec-old",
            status="done",
            error=None,
            interrupted=False,
            ended_at=9_500,
            output="A" * 80,
            truncated=False,
        )
        start("exec-new", "daemon-a")
        repo.finish(
            "exec-new",
            status="done",
            error=None,
            interrupted=False,
            ended_at=9_800,
            output="B" * 80,
            truncated=False,
        )
        start("exec-expired", "daemon-a")
        repo.finish(
            "exec-expired",
            status="failed",
            error="old",
            interrupted=False,
            ended_at=100,
            output="Z" * 10,
            truncated=False,
        )
        start("exec-live", "daemon-a")
        repo.mark_running("exec-live", 8_000)
        repo.save_output("exec-live", "C" * 40, False)
        # Same-instance non-terminal rows now read as outcome_unknown unless
        # this process still holds the job. This row is the held one.
        background_mod._claim_live("exec-live")
        start("exec-foreign", "daemon-b")
        report = repo.prune(10_000, current_instance="daemon-a")
        assert report["deleted"] == 1
        assert report["marked_unknown"] == 1
        assert (
            repo.get(
                "exec-expired", root_frame_id="root-a", current_instance="daemon-a"
            )
            is None
        )
        live = repo.get(
            "exec-live", root_frame_id="root-a", current_instance="daemon-a"
        )
        assert live is not None
        assert live["status"] == "running"
        assert live["output"] == "C" * 40
        old = repo.get("exec-old", root_frame_id="root-a", current_instance="daemon-a")
        new = repo.get("exec-new", root_frame_id="root-a", current_instance="daemon-a")
        assert old is not None and new is not None
        assert old["output"] == ""
        assert old["output_truncated"] == 1
        assert old["status"] == "done"
        assert new["output"] == ""
        foreign = repo.get(
            "exec-foreign", root_frame_id="root-a", current_instance="daemon-a"
        )
        assert foreign is not None
        assert foreign["status"] == "outcome_unknown"
        assert foreign["error"] == OUTCOME_UNKNOWN_ERROR
        assert (
            repo.get("exec-live", root_frame_id="root-a", current_instance="daemon-a")[
                "status"
            ]
            == "running"
        )
    finally:
        background_mod._forget_live("exec-live")
        store.close()


def test_a_failed_terminal_write_stays_unknown_after_restart(tmp_path):
    from openai4s.storage.background_execs import BoundBackgroundReceipts

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)

    class _BoomFinish(BoundBackgroundReceipts):
        def finish(self, *args, **kwargs):
            raise RuntimeError("receipt finish failed")

    kernel = _OkKernel(chunks=("should-not-count-as-success",))
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    try:
        dispatcher._bg()
        inner = dispatcher._bg_executor.receipts
        dispatcher._bg_executor.receipts = _BoomFinish(
            inner._repository,
            root_frame_id=inner._root_frame_id,
            frame_id=inner._frame_id,
            daemon_instance=inner._daemon_instance,
            owner_user_id=inner._owner_user_id,
        )
        launched = dispatcher._m_exec_background({"code": "print('ok')"})
        _join(dispatcher, launched["exec_id"])
        peeked = dispatcher._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "done"
        assert peeked["error"] is None
        assert peeked["stdout"] == "should-not-count-as-success"
        assert peeked["receipt_degraded"] is True
        raw_status = _raw(store, launched["exec_id"])["status"]
        assert raw_status in {"launching", "running"}
        restarted = _dispatcher(
            cfg,
            root,
            "daemon-b",
            lambda: (_ for _ in ()).throw(AssertionError("replayed")),
        )
        again = restarted._m_exec_peek(launched["exec_id"])
        assert again["status"] == "outcome_unknown"
        assert again["status"] != "done"
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_shutdown_joins_a_terminal_receipt_waiting_for_the_store_lock(
    tmp_path, monkeypatch
):
    """Normal exit must not discard a completed cell's pending SQLite write."""

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _WaitKernel()
    kernel.chunks = ("last-output-before-exit",)
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    executor = dispatcher._bg()
    # Keep the small output in memory until the terminal receipt writes it.
    executor.FLUSH_INTERVAL_MS = 60_000
    finish_entered = threading.Event()
    join_entered = threading.Event()
    shutdown_returned = threading.Event()
    stopped = []
    shutdown_thread = None
    finish = executor.receipts.finish

    def observed_finish(*args, **kwargs):
        finish_entered.set()
        return finish(*args, **kwargs)

    monkeypatch.setattr(executor.receipts, "finish", observed_finish)
    try:
        exec_id = executor.launch("print('last-output-before-exit')")["exec_id"]
        assert kernel.entered.wait(1)
        job = executor._get(exec_id)
        assert job._thread is not None
        join = job._thread.join

        def observed_join(*args, **kwargs):
            join_entered.set()
            return join(*args, **kwargs)

        monkeypatch.setattr(job._thread, "join", observed_join)

        def shutdown():
            stopped.append(executor.shutdown(timeout_per_job=2))
            shutdown_returned.set()

        with store._lock:
            kernel.release.set()
            assert finish_entered.wait(1)
            before = executor.peek(exec_id)
            assert before["status"] == "running"
            assert before["done"] is False
            assert before["ended_at"] is None
            assert _raw(store, exec_id)["output"] == ""
            shutdown_thread = threading.Thread(target=shutdown)
            shutdown_thread.start()
            # This observes shutdown's join directly, without racing a sleep
            # against the receipt writer or relying on a timeout to pass.
            assert join_entered.wait(1)
            assert not shutdown_returned.is_set()

        shutdown_thread.join(2)
        assert not shutdown_thread.is_alive()
        assert shutdown_returned.is_set()
        assert stopped == [1]
        assert not job._thread.is_alive()
        assert executor.peek(exec_id)["status"] == "done"
        assert _raw(store, exec_id)["status"] == "done"
        store.close()

        # A fresh connection and dispatcher must see committed output; an
        # in-memory terminal snapshot cannot satisfy this assertion.
        cfg, store = _cfg_store(tmp_path)
        restarted = _dispatcher(cfg, root, "daemon-b", _OkKernel)
        receipt = restarted._m_exec_peek(exec_id)
        assert receipt["status"] == "done"
        assert receipt["stdout"] == "last-output-before-exit"
        assert receipt["source"] == "receipt"
    finally:
        kernel.release.set()
        if shutdown_thread is not None:
            shutdown_thread.join(3)
        executor.shutdown(timeout_per_job=1)
        store.close()


def test_interrupt_of_a_receipt_without_a_process_does_not_change_the_row(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    original = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("stay",)))
    try:
        launched = original._m_exec_background({"code": "print('stay')"})
        _join(original, launched["exec_id"])
        before = dict(_raw(store, launched["exec_id"]))
        restarted = _dispatcher(cfg, root, "daemon-b", lambda: _OkKernel())
        report = restarted._m_exec_interrupt(launched["exec_id"])
        assert isinstance(report["interrupt_undelivered"], str)
        assert report["interrupt_undelivered"] == report["reason"]
        assert report["reason"] == (
            "the daemon has restarted; delivery cannot be confirmed"
        )
        assert report["status"] == "done"
        assert report["stdout"] == "stay"
        after = dict(_raw(store, launched["exec_id"]))
        assert after["status"] == before["status"]
        assert after["output"] == before["output"]
        assert after["updated_at"] == before["updated_at"]
        assert after["error"] == before["error"]
    finally:
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_restored_receipts_do_not_block_idle_release(tmp_path):
    from openai4s.server.gateway import SessionRunner
    from openai4s.server.session_recovery import SessionRecoveryService

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    original = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("x",)))
    gateway = SessionRunner.__new__(SessionRunner)
    try:
        launched = original._m_exec_background({"code": "print(1)"})
        _join(original, launched["exec_id"])
        restarted = _dispatcher(cfg, root, "daemon-b", lambda: _OkKernel())
        restarted._bg()
        session = SimpleNamespace(
            dispatcher=restarted,
            root_frame_id=root,
            kernels=SimpleNamespace(
                status=lambda: {"python": {"alive": True, "last_activity_at": 1}}
            ),
        )
        assert gateway._background_active(session) is False
        service = SessionRecoveryService(
            store=store,
            sessions=lambda: [session],
            turn_active=lambda _root: False,
            approval_pending=lambda _root: False,
            background_active=gateway._background_active,
            release_idle=lambda _session, _reason: True,
            background_last_activity_ms=gateway._background_last_activity_ms,
            ttl_s=1,
            clock=lambda: 100.0,
        )
        assert service.blocked(session) is False
        assert service.sweep_once() == [root]

        hung_kernel = _WaitKernel()
        hung = _dispatcher(cfg, root, "daemon-a", lambda: hung_kernel)
        running = hung._m_exec_background({"code": "hang"})
        assert hung_kernel.entered.wait(2)
        hung_session = SimpleNamespace(dispatcher=hung, root_frame_id=root)
        assert gateway._background_active(hung_session) is True
        assert service.blocked(hung_session) is True
        del running
    finally:
        if original._bg_executor is not None:
            original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_cli_background_jobs_stay_in_memory(tmp_path):
    from openai4s.host_dispatch import build_dispatcher

    cfg, store = _cfg_store(tmp_path)
    dispatcher = build_dispatcher(cfg, frame_id=_root(store))
    dispatcher.background_kernel_factory = lambda: _OkKernel(chunks=("cli",))
    try:
        launched = dispatcher._m_exec_background({"code": "print('cli')"})
        assert launched["persistent"] is False
        assert "source" not in launched
        _join(dispatcher, launched["exec_id"])
        peeked = dispatcher._m_exec_peek(launched["exec_id"])
        assert peeked["persistent"] is False
        assert peeked["status"] == "done"
        assert peeked["stdout"] == "cli"
        assert "source" not in peeked
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM background_exec_receipts"
            ).fetchone()[0]
            == 0
        )
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_v34_migration_denies_agent_sql_and_session_delete_removes_receipts(
    tmp_path,
):
    from openai4s.storage.migrations import SCHEMA_VERSION

    cfg, store = _cfg_store(tmp_path)
    try:
        assert store.schema_state()["version"] == SCHEMA_VERSION == 35
        names = {row["version"]: row["name"] for row in store.schema_state()["applied"]}
        assert names[34] == "background_exec_receipts"
        assert "background_exec_receipts" not in store.schema()
        with pytest.raises(PermissionError, match="background_exec_receipts"):
            store.query("SELECT output FROM background_exec_receipts")
        root = _root(store)
        other = _root(store)
        owner = store.team.create_user(
            username="ada", password="test-password-not-real"
        )
        store.team.set_session_owner(root, owner["id"], project_id="default")
        dispatcher = _dispatcher(
            cfg, root, "daemon-a", lambda: _OkKernel(chunks=("z",))
        )
        launched = dispatcher._m_exec_background({"code": "print('z')"})
        _join(dispatcher, launched["exec_id"])
        assert _raw(store, launched["exec_id"])["owner_user_id"] == owner["id"]
        store._conn.execute(
            "UPDATE background_exec_receipts SET frame_id=NULL WHERE exec_id=?",
            (launched["exec_id"],),
        )
        store._conn.commit()
        store.background_exec_receipts.begin(
            exec_id="exec-other",
            root_frame_id=other,
            frame_id=other,
            owner_user_id=None,
            daemon_instance="daemon-a",
            origin="agent",
            code_sha256="ef" * 32,
            code_chars=1,
        )
        store.delete_frame(root)
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM background_exec_receipts WHERE exec_id=?",
                (launched["exec_id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            store.background_exec_receipts.get(
                "exec-other", root_frame_id=other, current_instance="daemon-a"
            )
            is not None
        )
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
    finally:
        store.close()


def test_allowlist_refusal_survives_a_shutdown_error_and_records_launch_failed(
    tmp_path, monkeypatch
):
    from openai4s.egress import EgressBoundaryUnavailable

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")

    class _BoomShutdown(_DegradedBackgroundKernel):
        def shutdown(self):
            self.shutdown_calls += 1
            raise RuntimeError("shutdown exploded")

    kernel = _BoomShutdown()
    dispatcher = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    try:
        with pytest.raises(EgressBoundaryUnavailable) as failure:
            dispatcher._m_exec_background({"code": "print(1)"})
        assert failure.value.code == "egress_boundary_unavailable"
        assert kernel.executed == 0
        assert kernel.shutdown_calls == 1
        receipt = store._conn.execute(
            "SELECT status, error FROM background_exec_receipts"
        ).fetchone()
        assert receipt["status"] == "launch_failed"
        assert str(receipt["error"]).startswith("egress_boundary_unavailable:")
    finally:
        store.close()


def test_allowlist_refusal_survives_a_lifetime_exit_error(tmp_path, monkeypatch):
    from openai4s.egress import EgressBoundaryUnavailable
    from openai4s.kernel.background import BackgroundExecutor
    from openai4s.storage.background_execs import BoundBackgroundReceipts

    _cfg, store = _cfg_store(tmp_path)
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    bound = BoundBackgroundReceipts(
        store.background_exec_receipts,
        root_frame_id="root-a",
        frame_id="root-a",
        daemon_instance="daemon-a",
        owner_user_id=None,
    )

    class _Life:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            raise RuntimeError("lifetime exploded")

    kernel = _DegradedBackgroundKernel()
    executor = BackgroundExecutor(
        lambda: kernel,
        dispatcher=None,
        lifetime_factory=lambda: _Life(),
        receipts=bound,
    )
    with pytest.raises(EgressBoundaryUnavailable) as failure:
        executor.launch("print(1)")
    assert failure.value.code == "egress_boundary_unavailable"
    assert kernel.executed == 0
    row = store.background_exec_receipts.get(
        executor_exec_id(store),
        root_frame_id="root-a",
        current_instance="daemon-a",
    )
    assert row is not None
    assert row["status"] == "launch_failed"
    store.close()


def executor_exec_id(store):
    row = store._conn.execute("SELECT exec_id FROM background_exec_receipts").fetchone()
    assert row is not None
    return row["exec_id"]


def test_finish_does_not_lose_to_a_later_mark_running(tmp_path):
    _cfg, store = _cfg_store(tmp_path)
    repo = store.background_exec_receipts
    try:
        repo.begin(
            exec_id="exec-race",
            root_frame_id="root-a",
            frame_id="root-a",
            owner_user_id=None,
            daemon_instance="daemon-a",
            origin="agent",
            code_sha256="11" * 32,
            code_chars=1,
        )
        repo.finish(
            "exec-race",
            status="done",
            error=None,
            interrupted=False,
            ended_at=20,
            output="final",
            truncated=False,
        )
        repo.mark_running("exec-race", 15)
        repo.save_output("exec-race", "late", False)
        repo.finish(
            "exec-race",
            status="failed",
            error="second writer",
            interrupted=False,
            ended_at=30,
            output="nope",
            truncated=False,
        )
        row = repo.get("exec-race", root_frame_id="root-a", current_instance="daemon-a")
        assert row is not None
        assert row["status"] == "done"
        assert row["output"] == "final"
        assert row["error"] is None
    finally:
        store.close()


class _ExplodingLifetime:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        raise RuntimeError("lifetime exploded")


def _bound(store, instance="daemon-a"):
    from openai4s.storage.background_execs import BoundBackgroundReceipts

    return BoundBackgroundReceipts(
        store.background_exec_receipts,
        root_frame_id="root-a",
        frame_id="root-a",
        daemon_instance=instance,
        owner_user_id=None,
    )


def _join_executor(executor, exec_id):
    job = executor._get(exec_id)
    assert job._thread is not None
    job._thread.join(2)
    assert not job._thread.is_alive()
    return job


def test_same_instance_peek_is_unknown_after_a_failed_terminal_write(tmp_path):
    """A new dispatcher in this process must not poll a receipt whose write died.

    The row stays non-terminal. The job is no longer in the process-wide live
    set, so the read derives outcome_unknown instead of waiting for a restart.
    """

    from openai4s.storage.background_execs import BoundBackgroundReceipts

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)

    class _BoomFinish(BoundBackgroundReceipts):
        def finish(self, *args, **kwargs):
            raise RuntimeError("receipt finish failed")

    original = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("x",)))
    try:
        original._bg()
        inner = original._bg_executor.receipts
        original._bg_executor.receipts = _BoomFinish(
            inner._repository,
            root_frame_id=inner._root_frame_id,
            frame_id=inner._frame_id,
            daemon_instance=inner._daemon_instance,
            owner_user_id=inner._owner_user_id,
        )
        launched = original._m_exec_background({"code": "print('x')"})
        _join(original, launched["exec_id"])
        assert _raw(store, launched["exec_id"])["status"] in {"launching", "running"}
        fresh = _dispatcher(
            cfg,
            root,
            "daemon-a",
            lambda: (_ for _ in ()).throw(AssertionError("replayed")),
        )
        peeked = fresh._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "outcome_unknown"
        assert peeked["done"] is True
        assert peeked["source"] == "receipt"
        assert _raw(store, launched["exec_id"])["status"] in {"launching", "running"}
    finally:
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_same_instance_peek_stays_running_while_the_job_is_live(tmp_path):
    """The live set is per job. A second dispatcher still sees a running one."""

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _WaitKernel()
    original = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    try:
        launched = original._m_exec_background({"code": "while True: pass"})
        assert kernel.entered.wait(2)
        other = _dispatcher(
            cfg,
            root,
            "daemon-a",
            lambda: (_ for _ in ()).throw(AssertionError("replayed")),
        )
        peeked = other._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "running"
        assert peeked["done"] is False
        assert peeked["source"] == "receipt"
        assert background_mod.background_job_is_live(launched["exec_id"]) is True
    finally:
        kernel.release.set()
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_closed_spawn_records_launch_failed_when_lifetime_exit_raises(tmp_path):
    _cfg, store = _cfg_store(tmp_path)
    holder: dict = {}

    def factory():
        holder["executor"].shutdown(timeout_per_job=0.0)
        return _OkKernel()

    executor = BackgroundExecutor(
        factory,
        dispatcher=None,
        lifetime_factory=_ExplodingLifetime,
        receipts=_bound(store),
    )
    holder["executor"] = executor
    try:
        with pytest.raises(RuntimeError, match="background executor is closed"):
            executor.launch("print(1)")
        row = store._conn.execute(
            "SELECT status, error FROM background_exec_receipts"
        ).fetchone()
        assert row["status"] == "launch_failed"
        assert "closed" in str(row["error"])
    finally:
        store.close()


def test_thread_start_failure_records_launch_failed_when_lifetime_exit_raises(
    tmp_path, monkeypatch
):
    class _BoomThread(threading.Thread):
        def start(self):
            raise RuntimeError("thread refused")

    monkeypatch.setattr(background_mod.threading, "Thread", _BoomThread)
    _cfg, store = _cfg_store(tmp_path)
    executor = BackgroundExecutor(
        lambda: _OkKernel(),
        dispatcher=None,
        lifetime_factory=_ExplodingLifetime,
        receipts=_bound(store),
    )
    try:
        with pytest.raises(RuntimeError, match="lifetime exploded"):
            executor.launch("print(1)")
        row = store._conn.execute(
            "SELECT status, error FROM background_exec_receipts"
        ).fetchone()
        assert row["status"] == "launch_failed"
        assert row["error"] == "background worker thread could not be started"
    finally:
        store.close()


def test_terminal_prune_runs_again_only_after_the_injected_interval(tmp_path):
    from openai4s.kernel.background import PRUNE_INTERVAL_MS
    from openai4s.storage.background_execs import BoundBackgroundReceipts

    background_mod._receipt_prune_last_ms = None
    calls = {"n": 0}

    class _Counting(BoundBackgroundReceipts):
        def prune(self, now):
            calls["n"] += 1
            return super().prune(now)

    clock = {"now": 5_000_000}
    _cfg, store = _cfg_store(tmp_path)
    executor = BackgroundExecutor(
        lambda: _OkKernel(),
        dispatcher=None,
        receipts=_Counting(
            store.background_exec_receipts,
            root_frame_id="root-a",
            frame_id="root-a",
            daemon_instance="daemon-a",
            owner_user_id=None,
        ),
        clock_ms=lambda: clock["now"],
    )

    def finish(code: str) -> None:
        launched = executor.launch(code)
        _join_executor(executor, launched["exec_id"])

    try:
        finish("one")
        assert calls["n"] == 1
        clock["now"] += PRUNE_INTERVAL_MS - 1
        finish("two")
        assert calls["n"] == 1
        clock["now"] += 1
        finish("three")
        assert calls["n"] == 2
    finally:
        background_mod._receipt_prune_last_ms = None
        executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_quota_trim_clears_the_oldest_terminal_rows_in_one_update(tmp_path):
    """Three oldest rows of five, one UPDATE. The per-row loop issued three."""

    from openai4s.storage.background_execs import BackgroundExecReceiptRepository

    _cfg, store = _cfg_store(tmp_path)
    repo = BackgroundExecReceiptRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 10_000,
        max_output_bytes=80,
        max_total_output_bytes=70,
        terminal_ttl_ms=10**12,
    )
    bodies = ("A", "B", "C", "D", "E")
    try:
        for index, body in enumerate(bodies, start=1):
            exec_id = f"exec-q{index}"
            repo.begin(
                exec_id=exec_id,
                root_frame_id="root-a",
                frame_id="root-a",
                owner_user_id=None,
                daemon_instance="daemon-a",
                origin="agent",
                code_sha256="ab" * 32,
                code_chars=1,
            )
            repo.finish(
                exec_id,
                status="done",
                error=None,
                interrupted=False,
                ended_at=1_000 * index,
                output=body * 30,
                truncated=False,
            )
        statements: list[str] = []
        store._conn.set_trace_callback(statements.append)
        try:
            report = repo.prune(10_000, current_instance="daemon-a")
        finally:
            store._conn.set_trace_callback(None)
        clears = [sql for sql in statements if "output=''" in sql]
        assert len(clears) == 1
        assert report["cleared"] == 3
        for index, body in enumerate(bodies, start=1):
            row = repo.get(
                f"exec-q{index}",
                root_frame_id="root-a",
                current_instance="daemon-a",
            )
            assert row is not None
            assert row["status"] == "done"
            if index <= 3:
                assert row["output"] == ""
                assert row["output_truncated"] == 1
            else:
                assert row["output"] == body * 30
    finally:
        store.close()


def test_receipt_interrupt_names_a_missing_handle_in_the_same_daemon(tmp_path):
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    original = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("stay",)))
    try:
        launched = original._m_exec_background({"code": "print('stay')"})
        _join(original, launched["exec_id"])
        same = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel())
        report = same._m_exec_interrupt(launched["exec_id"])
        assert isinstance(report["interrupt_undelivered"], str)
        assert report["interrupt_undelivered"] == report["reason"]
        assert report["reason"] == (
            "this process no longer has a handle for this job; "
            "delivery cannot be confirmed"
        )
        assert report["status"] == "done"
    finally:
        original._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_receipt_daemon_instance_is_the_shared_process_id_when_unset(tmp_path):
    from openai4s.host_dispatch import build_dispatcher
    from openai4s.process_instance import PROCESS_INSTANCE_ID
    from openai4s.server.session_recovery import (
        PROCESS_INSTANCE_ID as recovery_instance,
    )

    assert recovery_instance is PROCESS_INSTANCE_ID
    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    dispatcher = build_dispatcher(cfg, frame_id=root)
    dispatcher.durable_background = True
    dispatcher.background_kernel_factory = lambda: _OkKernel(chunks=("id",))
    try:
        assert getattr(dispatcher, "daemon_instance", None) in (None, "")
        launched = dispatcher._m_exec_background({"code": "print(1)"})
        _join(dispatcher, launched["exec_id"])
        stored = _raw(store, launched["exec_id"])["daemon_instance"]
        assert stored == PROCESS_INSTANCE_ID
        assert stored == recovery_instance
    finally:
        dispatcher._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_a_job_finished_between_the_select_and_the_live_check_reads_terminal(
    tmp_path, monkeypatch
):
    """The reader's row said running; the owner then finished and left.

    The owner leaves the live set only after its terminal write commits, so
    a reader that misses it in the live set re-reads the row and reports
    what was written instead of deriving `outcome_unknown`.
    """

    _cfg, store = _cfg_store(tmp_path)
    repo = store.background_exec_receipts
    repo.begin(
        exec_id="exec-race",
        root_frame_id="root-a",
        frame_id="root-a",
        owner_user_id=None,
        daemon_instance="daemon-a",
        origin="agent",
        code_sha256="ab" * 32,
        code_chars=1,
    )
    repo.mark_running("exec-race", 1_000)
    background_mod._claim_live("exec-race")

    def owner_finishes_then_checks(exec_id: str) -> bool:
        repo.finish(
            exec_id,
            status="done",
            error=None,
            interrupted=False,
            ended_at=2_000,
            output="finished",
            truncated=False,
        )
        background_mod._forget_live(exec_id)
        return False

    monkeypatch.setattr(
        background_mod, "background_job_is_live", owner_finishes_then_checks
    )
    try:
        row = repo.get("exec-race", root_frame_id="root-a", current_instance="daemon-a")
        assert row is not None
        assert row["status"] == "done"
        assert row["output"] == "finished"
    finally:
        background_mod._forget_live("exec-race")
        store.close()


def test_a_receipt_interrupt_from_another_runtime_names_the_holder(tmp_path):
    """Same process, another session runtime: the job is alive elsewhere.

    The receipt reads `running`, so "no longer has a handle" would contradict
    it. The reason says this session cannot deliver the stop.
    """

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)
    kernel = _WaitKernel()
    owner = _dispatcher(cfg, root, "daemon-a", lambda: kernel)
    try:
        launched = owner._m_exec_background({"code": "hold"})
        assert kernel.entered.wait(2)
        other = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel())
        report = other._m_exec_interrupt(launched["exec_id"])
        assert report["status"] == "running"
        assert report["interrupt_undelivered"] == report["reason"]
        assert report["reason"] == (
            "another session runtime in this process holds this job; "
            "this session cannot deliver the stop"
        )
    finally:
        kernel.release.set()
        owner._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()


def test_quota_trim_stops_at_exactly_the_excess(tmp_path):
    """Five 30-byte terminal rows against a 90-byte quota: excess is 60.

    Clearing the two oldest frees exactly 60, so the third keeps its output.
    A trim that only stops once it has freed *more* than the excess clears a
    third row it did not have to.
    """

    from openai4s.storage.background_execs import BackgroundExecReceiptRepository

    _cfg, store = _cfg_store(tmp_path)
    repo = BackgroundExecReceiptRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 10_000,
        max_output_bytes=80,
        max_total_output_bytes=90,
        terminal_ttl_ms=10**12,
    )
    try:
        for index, body in enumerate(("A", "B", "C", "D", "E"), start=1):
            exec_id = f"exec-b{index}"
            repo.begin(
                exec_id=exec_id,
                root_frame_id="root-a",
                frame_id="root-a",
                owner_user_id=None,
                daemon_instance="daemon-a",
                origin="agent",
                code_sha256="ab" * 32,
                code_chars=1,
            )
            repo.finish(
                exec_id,
                status="done",
                error=None,
                interrupted=False,
                ended_at=1_000 * index,
                output=body * 30,
                truncated=False,
            )
        report = repo.prune(10_000, current_instance="daemon-a")
        assert report["cleared"] == 2
        outputs = dict(
            store._conn.execute(
                "SELECT exec_id, output FROM background_exec_receipts"
            ).fetchall()
        )
        assert outputs["exec-b1"] == "" and outputs["exec-b2"] == ""
        assert outputs["exec-b3"] == "C" * 30
    finally:
        store.close()


def test_one_owners_jobs_never_clear_another_owners_output(tmp_path):
    """Team mode shares one Store, so the output quota is per owner.

    Here alice holds 80 bytes and is within the 100-byte quota. bob holds
    120, an excess of 20, so bob's oldest row goes and nothing of alice's
    does. With one table-wide cap the oldest rows overall went first, and
    both of alice's rows went before any of bob's: one member running
    background cells could erase another member's stored output.
    """

    from openai4s.storage.background_execs import BackgroundExecReceiptRepository

    _cfg, store = _cfg_store(tmp_path)
    repo = BackgroundExecReceiptRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 10_000,
        max_output_bytes=80,
        max_total_output_bytes=100,
        terminal_ttl_ms=10**12,
    )
    rows = (
        ("alice", "exec-a1", "A", 1_000),
        ("alice", "exec-a2", "B", 2_000),
        ("bob", "exec-b1", "C", 3_000),
        ("bob", "exec-b2", "D", 4_000),
        ("bob", "exec-b3", "E", 5_000),
    )
    try:
        for owner, exec_id, body, ended_at in rows:
            repo.begin(
                exec_id=exec_id,
                root_frame_id=f"root-{owner}",
                frame_id=f"root-{owner}",
                owner_user_id=owner,
                daemon_instance="daemon-a",
                origin="agent",
                code_sha256="ab" * 32,
                code_chars=1,
            )
            repo.finish(
                exec_id,
                status="done",
                error=None,
                interrupted=False,
                ended_at=ended_at,
                output=body * 40,
                truncated=False,
            )
        report = repo.prune(10_000, current_instance="daemon-a")
        assert report["cleared"] == 1
        outputs = dict(
            store._conn.execute(
                "SELECT exec_id, output FROM background_exec_receipts"
            ).fetchall()
        )
        assert outputs["exec-a1"] == "A" * 40
        assert outputs["exec-a2"] == "B" * 40
        assert outputs["exec-b1"] == ""
        assert outputs["exec-b2"] == "D" * 40
        assert outputs["exec-b3"] == "E" * 40
    finally:
        store.close()


def test_a_failed_launch_whose_receipt_write_fails_reads_unknown(tmp_path):
    """The spawn fails and so does the `launch_failed` write.

    The row stays `launching`. The job must still leave the live set, so a
    reader in this process sees `outcome_unknown` rather than a launch that
    looks like it is still starting.
    """

    from openai4s.storage.background_execs import BoundBackgroundReceipts

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)

    class _BoomFinish(BoundBackgroundReceipts):
        def finish(self, *args, **kwargs):
            raise RuntimeError("receipt finish failed")

    def spawn_fails():
        raise RuntimeError("spawn failed")

    dispatcher = _dispatcher(cfg, root, "daemon-a", spawn_fails)
    try:
        dispatcher._bg()
        inner = dispatcher._bg_executor.receipts
        dispatcher._bg_executor.receipts = _BoomFinish(
            inner._repository,
            root_frame_id=inner._root_frame_id,
            frame_id=inner._frame_id,
            daemon_instance=inner._daemon_instance,
            owner_user_id=inner._owner_user_id,
        )
        with pytest.raises(RuntimeError, match="spawn failed"):
            dispatcher._m_exec_background({"code": "never"})
        exec_id = store._conn.execute(
            "SELECT exec_id FROM background_exec_receipts"
        ).fetchone()[0]
        assert _raw(store, exec_id)["status"] == "launching"
        reader = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel())
        assert reader._m_exec_peek(exec_id)["status"] == "outcome_unknown"
    finally:
        store.close()


def test_a_failing_cleanup_neither_masks_the_launch_error_nor_degrades_a_job(
    tmp_path,
):
    """Cleanup after a terminal write is best effort.

    When it raises after a failed spawn, the caller still sees the spawn's
    own error. When it raises after a job finished, the job's receipt is
    complete, so `exec_peek` does not report `receipt_degraded`.
    """

    from openai4s.storage.background_execs import BoundBackgroundReceipts

    cfg, store = _cfg_store(tmp_path)
    root = _root(store)

    class _BoomPrune(BoundBackgroundReceipts):
        def prune(self, now):
            raise RuntimeError("prune exploded")

    def wrap(dispatcher):
        dispatcher._bg()
        inner = dispatcher._bg_executor.receipts
        dispatcher._bg_executor.receipts = _BoomPrune(
            inner._repository,
            root_frame_id=inner._root_frame_id,
            frame_id=inner._frame_id,
            daemon_instance=inner._daemon_instance,
            owner_user_id=inner._owner_user_id,
        )

    def spawn_fails():
        raise RuntimeError("spawn failed")

    failing = _dispatcher(cfg, root, "daemon-a", spawn_fails)
    ok = _dispatcher(cfg, root, "daemon-a", lambda: _OkKernel(chunks=("done",)))
    try:
        wrap(failing)
        background_mod._receipt_prune_last_ms = None
        with pytest.raises(RuntimeError, match="spawn failed"):
            failing._m_exec_background({"code": "never"})
        wrap(ok)
        background_mod._receipt_prune_last_ms = None
        launched = ok._m_exec_background({"code": "print('done')"})
        _join(ok, launched["exec_id"])
        peeked = ok._m_exec_peek(launched["exec_id"])
        assert peeked["status"] == "done"
        assert "receipt_degraded" not in peeked
    finally:
        background_mod._receipt_prune_last_ms = None
        ok._bg_executor.shutdown(timeout_per_job=1.0)
        store.close()
