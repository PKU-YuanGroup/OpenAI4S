"""Session shutdown must not leak independent background kernels."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

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
        (0, threading.get_ident(), 0),
        (1, threading.get_ident(), 1),
        ("one", threading.get_ident(), 0),
    ],
)
def test_malformed_capture_state_fails_closed_before_admission(
    captures, owner, backgrounds
):
    admission = TrustedCaptureCoordinator(enabled=True)
    admission._captures = captures
    admission._capture_owner = owner
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
