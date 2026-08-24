from __future__ import annotations

import pytest

from openai4s.kernel import InterruptDelivery
from openai4s.kernel.lazy import LazyKernel


class _Kernel:
    def __init__(self) -> None:
        self.cells: list[tuple[str, str]] = []
        self.closed = False
        self.generation = 7

    def execute(self, code: str, *, origin: str) -> dict:
        self.cells.append((origin, code))
        return {"stdout": code, "error": None}

    def is_alive(self) -> bool:
        return not self.closed

    def inspect_variables(self, *, limit=200) -> dict:
        return {"variables": [], "limit": limit}

    def interrupt(self) -> None:
        self.cells.append(("system", "interrupt"))

    def shutdown(self) -> None:
        self.closed = True


def test_context_without_code_never_creates_a_worker():
    created: list[_Kernel] = []

    with LazyKernel(lambda: created.append(_Kernel()) or created[-1]) as lazy:
        assert lazy.spawned is False
        assert lazy.generation is None
        assert lazy.is_alive() is False

    assert created == []


def test_variable_inspection_preserves_lazy_no_spawn_contract():
    created: list[_Kernel] = []
    lazy = LazyKernel(lambda: created.append(_Kernel()) or created[-1])

    with pytest.raises(RuntimeError, match="has not been started"):
        lazy.inspect_variables()
    assert created == [] and lazy.spawned is False

    lazy.execute("one", origin="agent")
    assert lazy.inspect_variables(limit=9) == {"variables": [], "limit": 9}
    assert len(created) == 1
    lazy.shutdown()


def test_first_cell_bootstraps_once_reuses_and_detaches_worker():
    created: list[_Kernel] = []
    published: list[_Kernel | None] = []

    def factory() -> _Kernel:
        kernel = _Kernel()
        created.append(kernel)
        return kernel

    lazy = LazyKernel(
        factory,
        bootstrap=lambda kernel: kernel.execute("bootstrap", origin="system"),
        publish=published.append,
    )

    first = lazy.execute("one", origin="agent")
    second = lazy.execute("two", origin="agent")

    assert first["stdout"] == "one" and second["stdout"] == "two"
    assert len(created) == 1
    assert created[0].cells == [
        ("system", "bootstrap"),
        ("agent", "one"),
        ("agent", "two"),
    ]
    assert lazy.generation == 7
    lazy.shutdown()
    assert created[0].closed is True
    assert published == [created[0], None]


def test_bootstrap_failure_does_not_publish_a_broken_worker():
    kernel = _Kernel()
    published: list[_Kernel | None] = []

    def fail(_kernel: _Kernel) -> None:
        raise RuntimeError("bootstrap failed")

    lazy = LazyKernel(lambda: kernel, bootstrap=fail, publish=published.append)

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        lazy.execute("never", origin="agent")

    assert lazy.spawned is False
    assert kernel.closed is True
    assert published == [kernel, None]


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        pytest.param(InterruptDelivery(True, "local-process"), True, id="delivered"),
        pytest.param(
            InterruptDelivery(False, "sandbox", "no pinned command identity"),
            False,
            id="reached_nobody",
        ),
        pytest.param(None, True, id="no_claim_either_way"),
    ],
)
def test_interrupt_reports_the_kernel_s_verdict_not_that_it_was_called(
    verdict, expected
):
    """`return True` after `kernel.interrupt()` meant "we made the call".

    Its caller — `Agent.interrupt_foreground`, and through it the CLI's Ctrl-C
    and `stop_child` — reads it as "the cell was stopped". That is the same gap
    `Kernel.interrupt` closes one layer down, and forwarding is what stops it
    being reopened here. `None` is a kernel double making no claim, and keeps
    the answer this method gave before there was a verdict to forward.
    """

    calls = []

    class _Interruptible(_Kernel):
        def interrupt(self):
            calls.append("interrupt")
            return verdict

    lazy = LazyKernel(lambda: _Interruptible())
    lazy.execute("x = 1", origin="agent")  # force the worker into existence

    assert lazy.interrupt() is expected
    assert calls == ["interrupt"]


def test_interrupting_a_kernel_that_was_never_started_is_false():
    """No worker, nothing stopped — and starting one to interrupt it would be
    the opposite of what a lazy kernel is for."""

    lazy = LazyKernel(lambda: pytest.fail("a stop must not spawn a worker"))
    assert lazy.interrupt() is False
