"""Lifecycle contracts for the protocol-neutral kernel supervisor."""

import threading
import uuid

import pytest

from openai4s.kernel.manager import Kernel
from openai4s.kernel.supervisor import KernelSupervisor


class FakeKernel:
    def __init__(self, name: str):
        self.name = name
        self.live = True
        self.shutdown_calls = 0
        self.interrupt_calls = 0
        self.restart_calls = 0
        self.kill_calls = 0
        self.inspect_calls = 0

    def is_alive(self):
        return self.live

    def shutdown(self):
        self.shutdown_calls += 1
        self.live = False

    def interrupt(self):
        self.interrupt_calls += 1

    def restart(self):
        self.restart_calls += 1
        self.live = True

    def kill_worker(self):
        self.kill_calls += 1
        self.live = False

    def inspect_variables(self, *, limit=200):
        self.inspect_calls += 1
        return {"type": "variables_response", "variables": [], "limit": limit}


def _factory(created: list[FakeKernel], prefix: str):
    def create():
        kernel = FakeKernel(f"{prefix}-{len(created)}")
        created.append(kernel)
        return kernel

    return create


def test_ensure_reuses_matching_worker_and_replaces_changed_or_dead_worker():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    create = _factory(created, "python")

    first = supervisor.ensure("python", "base", create)
    reused = supervisor.ensure("python", "base", create)

    assert first.generation == 0
    assert reused == first
    assert len(created) == 1

    changed = supervisor.ensure("python", "struct", create)
    assert changed.kernel is created[1]
    assert changed.generation == 1
    assert created[0].shutdown_calls == 1

    changed.kernel.live = False
    recovered = supervisor.ensure("python", "struct", create)
    assert recovered.kernel is created[2]
    assert recovered.generation == 2
    assert changed.kernel.shutdown_calls == 1


def test_failed_factory_preserves_the_current_worker_and_generation():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    current = supervisor.ensure("python", "base", _factory(created, "python"))

    def fail():
        raise RuntimeError("spawn failed")

    with pytest.raises(RuntimeError, match="spawn failed"):
        supervisor.ensure("python", "struct", fail)

    assert supervisor.current("python") == current
    assert current.kernel.shutdown_calls == 0
    assert supervisor.status("python")["generation"] == 0


def test_variable_inspection_never_creates_a_slot_or_worker():
    supervisor = KernelSupervisor()
    with pytest.raises(RuntimeError, match="no live python kernel"):
        supervisor.inspect_variables("python")
    assert supervisor.lease("python") is None
    assert supervisor.status() == {}

    created: list[FakeKernel] = []
    lease = supervisor.ensure("python", "base", _factory(created, "python"))
    response = supervisor.inspect_variables("python", limit=7)
    assert response["limit"] == 7
    assert lease.kernel.inspect_calls == 1


def test_variable_inspection_rejects_a_worker_replaced_mid_read():
    entered = threading.Event()
    release = threading.Event()

    class SlowInspector(FakeKernel):
        def inspect_variables(self, *, limit=200):
            entered.set()
            assert release.wait(5)
            return {"type": "variables_response", "variables": [], "limit": limit}

    supervisor = KernelSupervisor()
    original = SlowInspector("original")
    supervisor.ensure("python", "base", lambda: original)
    result = {}

    def inspect():
        try:
            result["value"] = supervisor.inspect_variables("python")
        except Exception as error:  # noqa: BLE001 - asserted below
            result["error"] = error

    thread = threading.Thread(target=inspect)
    thread.start()
    assert entered.wait(5)
    supervisor.ensure("python", "other", lambda: FakeKernel("replacement"))
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "changed during inspection" in str(result["error"])


def test_dead_replacement_is_rejected_without_destroying_healthy_current():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    current = supervisor.ensure("python", "base", _factory(created, "python"))
    dead = FakeKernel("dead")
    dead.live = False

    with pytest.raises(RuntimeError, match="factory returned a dead worker"):
        supervisor.ensure("python", "struct", lambda: dead)

    assert supervisor.current("python") == current
    assert current.kernel.shutdown_calls == 0
    assert dead.shutdown_calls == 1


def test_reused_slot_refreshes_factory_for_a_later_start():
    supervisor = KernelSupervisor()
    original: list[FakeKernel] = []
    refreshed: list[FakeKernel] = []
    current = supervisor.ensure("python", "base", _factory(original, "old"))

    assert supervisor.ensure("python", "base", _factory(refreshed, "new")) == current
    supervisor.stop("python")
    started = supervisor.restart("python")

    assert started.kernel is refreshed[0]
    assert len(original) == len(refreshed) == 1


def test_verified_candidate_publish_is_exact_and_installs_restart_factory():
    supervisor = KernelSupervisor()
    original: list[FakeKernel] = []
    future: list[FakeKernel] = []
    current = supervisor.ensure("python", "base", _factory(original, "old"))
    candidate = FakeKernel("verified")

    published = supervisor.publish_candidate(
        "python",
        "science",
        candidate,
        factory=_factory(future, "recovered"),
        generation_id=str(uuid.uuid4()),
        expected=current,
    )

    assert published.kernel is candidate
    assert current.kernel.shutdown_calls == 1
    supervisor.stop("python")
    restarted = supervisor.restart("python")
    assert restarted.kernel is future[0]


def test_candidate_publish_rejects_a_stale_expected_lease_without_replacement():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    stale = supervisor.ensure("python", "base", _factory(created, "old"))
    current = supervisor.ensure("python", "new", _factory(created, "new"))
    candidate = FakeKernel("candidate")

    with pytest.raises(RuntimeError, match="changed before recovery publish"):
        supervisor.publish_candidate(
            "python",
            "science",
            candidate,
            factory=lambda: FakeKernel("future"),
            generation_id=str(uuid.uuid4()),
            expected=stale,
        )

    assert supervisor.lease("python") == current
    assert candidate.live is True


def test_restart_stop_and_start_keep_a_monotonic_session_generation():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    first = supervisor.ensure("python", "base", _factory(created, "python"))
    booted = []

    restarted = supervisor.restart("python", after_restart=booted.append)
    assert restarted.kernel is first.kernel
    assert restarted.generation == 1
    assert restarted.kernel.restart_calls == 1
    assert booted == [first.kernel]

    assert supervisor.stop("python") == 1
    stopped = supervisor.status("python")
    assert stopped["state"] == "stopped"
    assert stopped["manual_stop"] is True

    started = supervisor.restart("python", after_restart=booted.append)
    assert started.kernel is created[1]
    assert started.generation == 2
    assert supervisor.status("python")["state"] == "running"
    assert booted[-1] is started.kernel


def test_explicit_stop_before_first_worker_records_manual_state():
    supervisor = KernelSupervisor()

    assert supervisor.stop("python", manual=True) == 0
    assert supervisor.status("python") == {
        "language": "python",
        "state": "stopped",
        "alive": False,
        "generation": 0,
        "manual_stop": True,
        "key": None,
    }


def test_interrupt_and_stop_all_cover_python_and_r_slots():
    supervisor = KernelSupervisor()
    py_created: list[FakeKernel] = []
    r_created: list[FakeKernel] = []
    py = supervisor.ensure("python", "base", _factory(py_created, "python"))
    r = supervisor.ensure("r", "r", _factory(r_created, "r"))

    assert supervisor.interrupt() == 2
    assert py.kernel.interrupt_calls == r.kernel.interrupt_calls == 1
    assert supervisor.alive("python") and supervisor.alive("r")

    assert supervisor.stop() == 2
    assert py.kernel.shutdown_calls == r.kernel.shutdown_calls == 1
    status = supervisor.status()
    assert status["python"]["state"] == status["r"]["state"] == "stopped"


def test_blocking_restart_hook_does_not_block_concurrent_interrupt():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    supervisor.ensure("python", "base", _factory(created, "python"))
    hook_started = threading.Event()
    release_hook = threading.Event()

    def hook(kernel):
        del kernel
        hook_started.set()
        release_hook.wait(2)

    thread = threading.Thread(target=lambda: supervisor.restart("python", hook))
    thread.start()
    assert hook_started.wait(1)

    assert supervisor.interrupt("python") == 1
    assert created[0].interrupt_calls == 1
    release_hook.set()
    thread.join(2)
    assert not thread.is_alive()


def test_stale_lease_cannot_interrupt_kill_restart_or_abandon_a_new_worker():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    create = _factory(created, "python")
    stale = supervisor.ensure("python", "base", create)
    current = supervisor.ensure("python", "struct", create)

    assert supervisor.interrupt_if_current(stale) is False
    assert supervisor.kill_if_current(stale) is False
    assert supervisor.restart_if_current(stale) is None
    assert supervisor.abandon_if_current(stale) is False
    assert supervisor.current("python") == current
    assert (
        current.kernel.interrupt_calls
        == current.kernel.kill_calls
        == current.kernel.restart_calls
        == 0
    )

    assert supervisor.interrupt_if_current(current) is True
    assert current.kernel.interrupt_calls == 1
    assert supervisor.kill_if_current(current) is True
    assert current.kernel.kill_calls == 1
    restarted = supervisor.restart_if_current(current)
    assert restarted is not None and restarted.generation == 2
    assert supervisor.abandon_if_current(current) is False
    assert supervisor.current("python") == restarted


def test_abandon_current_detaches_zombie_without_shutdown_then_recovers():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    create = _factory(created, "python")
    lease = supervisor.ensure("python", "base", create)

    assert supervisor.abandon_if_current(lease) is True
    assert supervisor.current("python") is None
    assert lease.kernel.shutdown_calls == 0

    recovered = supervisor.ensure("python", "base", create)
    assert recovered.generation == 1
    assert recovered.kernel is created[1]


def test_shutdown_if_current_closes_only_the_exact_desynchronized_worker():
    supervisor = KernelSupervisor()
    created: list[FakeKernel] = []
    create = _factory(created, "r")
    stale = supervisor.ensure("r", "r-old", create)
    current = supervisor.ensure("r", "r-new", create)

    assert supervisor.shutdown_if_current(stale) is False
    assert supervisor.current("r") == current
    assert current.kernel.shutdown_calls == 0

    assert supervisor.shutdown_if_current(current) is True
    assert supervisor.current("r") is None
    assert current.kernel.shutdown_calls == 1


def test_unknown_slots_are_empty_and_restart_requires_a_factory():
    supervisor = KernelSupervisor()

    assert supervisor.current("r") is None
    assert supervisor.alive("r") is False
    assert supervisor.status("r") == {
        "language": "r",
        "state": "none",
        "alive": False,
        "generation": 0,
        "manual_stop": False,
        "key": None,
    }
    assert supervisor.interrupt("r") == 0
    assert supervisor.stop("r") == 0
    with pytest.raises(RuntimeError, match="no r kernel factory configured"):
        supervisor.restart("r")

    created: list[FakeKernel] = []
    lease = supervisor.ensure("r", None, _factory(created, "r"))
    assert lease.key is None and lease.generation == 0


class _FakeProc:
    def __init__(self):
        self.calls = 0

    def kill(self):
        self.calls += 1
        if self.calls > 1:
            raise ProcessLookupError


def test_manager_kill_worker_is_exact_and_idempotent():
    kernel = object.__new__(Kernel)
    proc = _FakeProc()
    kernel._proc = proc

    kernel.kill_worker()
    kernel.kill_worker()

    assert proc.calls == 2
