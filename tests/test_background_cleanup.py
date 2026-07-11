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
