"""Session shutdown must not leak independent background kernels."""

from __future__ import annotations

import threading

import pytest

from openai4s.kernel.background import BackgroundExecutor


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
