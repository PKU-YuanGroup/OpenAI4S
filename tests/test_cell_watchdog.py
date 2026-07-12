"""Protocol-neutral contracts for supervised cell timeout recovery."""

from __future__ import annotations

import threading

import pytest

from openai4s.execution import WatchdogPolicy, execute_with_watchdog
from openai4s.kernel import KernelSupervisor


class FakeKernel:
    def __init__(self) -> None:
        self.live = True
        self.interrupt_calls = 0
        self.kill_calls = 0
        self.restart_calls = 0
        self.shutdown_calls = 0
        self.on_interrupt = lambda: None
        self.on_kill = lambda: None

    def is_alive(self) -> bool:
        return self.live

    def interrupt(self) -> None:
        self.interrupt_calls += 1
        self.on_interrupt()

    def kill_worker(self) -> None:
        self.kill_calls += 1
        self.live = False
        self.on_kill()

    def restart(self) -> None:
        self.restart_calls += 1
        self.live = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.live = False


def _lease():
    supervisor = KernelSupervisor()
    kernel = FakeKernel()
    lease = supervisor.ensure("python", "base", lambda: kernel)
    return supervisor, kernel, lease


def test_policy_reads_timeout_dynamically_and_invalid_values_fall_back():
    assert (
        WatchdogPolicy.from_environment({"OPENAI4S_CELL_TIMEOUT": "12.5"}).timeout_s
        == 12.5
    )
    assert (
        WatchdogPolicy.from_environment({"OPENAI4S_CELL_TIMEOUT": "bad"}).timeout_s
        == 900.0
    )
    assert not WatchdogPolicy(timeout_s=0).enabled
    assert not WatchdogPolicy(timeout_s=float("nan")).enabled


def test_fast_result_and_original_exception_pass_through():
    supervisor, kernel, lease = _lease()
    policy = WatchdogPolicy(timeout_s=1, poll_s=0.01)

    assert execute_with_watchdog(
        supervisor, lease, lambda worker: {"pid": id(worker)}, policy=policy
    ) == {"pid": id(kernel)}

    error = ValueError("cell failed")

    def fail(worker):
        raise error

    with pytest.raises(ValueError) as raised:
        execute_with_watchdog(supervisor, lease, fail, policy=policy)
    assert raised.value is error
    assert kernel.interrupt_calls == kernel.kill_calls == 0


def test_permission_pause_freezes_timeout_budget():
    supervisor, kernel, lease = _lease()
    release = threading.Event()
    pause_calls = 0

    def run(worker):
        assert release.wait(1)
        return "finished after approval"

    def paused() -> bool:
        nonlocal pause_calls
        pause_calls += 1
        if pause_calls == 3:
            release.set()
        return True

    result = execute_with_watchdog(
        supervisor,
        lease,
        run,
        policy=WatchdogPolicy(timeout_s=0.001, poll_s=0.001),
        paused=paused,
    )

    assert result == "finished after approval"
    assert pause_calls >= 3
    assert kernel.interrupt_calls == 0


def test_sigint_can_finish_without_resetting_the_namespace():
    supervisor, kernel, lease = _lease()
    release = threading.Event()
    kernel.on_interrupt = release.set

    def run(worker):
        assert release.wait(1)
        return {"interrupted": True}

    result = execute_with_watchdog(
        supervisor,
        lease,
        run,
        policy=WatchdogPolicy(
            timeout_s=0.001,
            poll_s=0.001,
            interrupt_grace_s=0.1,
            kill_grace_s=0.1,
        ),
    )

    assert result == {"interrupted": True}
    assert kernel.interrupt_calls == 1
    assert kernel.kill_calls == kernel.restart_calls == 0
    assert supervisor.current("python") == lease


def test_cancellation_cuts_through_permission_pause_with_one_interrupt():
    supervisor, kernel, lease = _lease()
    release = threading.Event()
    kernel.on_interrupt = release.set

    def run(worker):
        assert release.wait(1)
        return {"interrupted": True}

    result = execute_with_watchdog(
        supervisor,
        lease,
        run,
        policy=WatchdogPolicy(
            timeout_s=10,
            poll_s=0.001,
            interrupt_grace_s=0.1,
            kill_grace_s=0.1,
        ),
        cancelled=lambda: True,
        paused=lambda: True,
    )

    assert result == {"interrupted": True}
    assert kernel.interrupt_calls == 1
    assert kernel.kill_calls == 0


def test_hard_kill_restarts_exact_lease_and_runs_bootstrap():
    supervisor, kernel, lease = _lease()
    release = threading.Event()
    kernel.on_kill = release.set
    bootstrapped = []

    def run(worker):
        assert release.wait(1)
        raise RuntimeError("worker pipe closed")

    with pytest.raises(TimeoutError, match="cell exceeded"):
        execute_with_watchdog(
            supervisor,
            lease,
            run,
            policy=WatchdogPolicy(
                timeout_s=0.001,
                poll_s=0.001,
                interrupt_grace_s=0.001,
                kill_grace_s=0.1,
            ),
            after_restart=bootstrapped.append,
        )

    recovered = supervisor.current("python")
    assert recovered is not None and recovered.kernel is kernel
    assert recovered.generation == 1
    assert kernel.interrupt_calls == kernel.kill_calls == kernel.restart_calls == 1
    assert bootstrapped == [kernel]


def test_host_call_zombie_is_abandoned_without_touching_a_future_worker():
    supervisor, kernel, lease = _lease()
    release = threading.Event()

    def run(worker):
        assert release.wait(1)
        return "late host response"

    with pytest.raises(TimeoutError, match="cell exceeded"):
        execute_with_watchdog(
            supervisor,
            lease,
            run,
            policy=WatchdogPolicy(
                timeout_s=0.001,
                poll_s=0.001,
                interrupt_grace_s=0.001,
                kill_grace_s=0.001,
            ),
        )

    assert supervisor.current("python") is None
    assert kernel.kill_calls == 1
    assert kernel.restart_calls == kernel.shutdown_calls == 0

    replacement = FakeKernel()
    recovered = supervisor.ensure("python", "base", lambda: replacement)
    release.set()
    assert recovered.kernel is replacement
    assert replacement.interrupt_calls == replacement.kill_calls == 0


def test_bootstrap_failure_detaches_the_restarted_generation():
    supervisor, kernel, lease = _lease()
    release = threading.Event()
    kernel.on_kill = release.set

    def run(worker):
        assert release.wait(1)
        raise RuntimeError("worker pipe closed")

    def broken_bootstrap(worker):
        raise RuntimeError("bootstrap failed")

    with pytest.raises(TimeoutError):
        execute_with_watchdog(
            supervisor,
            lease,
            run,
            policy=WatchdogPolicy(
                timeout_s=0.001,
                poll_s=0.001,
                interrupt_grace_s=0.001,
                kill_grace_s=0.1,
            ),
            after_restart=broken_bootstrap,
        )

    assert supervisor.current("python") is None
    assert kernel.restart_calls == kernel.shutdown_calls == 1
