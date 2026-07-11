"""Deterministic idle sweeping and explicit recovery occupancy."""

from __future__ import annotations

from types import SimpleNamespace

from openai4s.kernel.supervisor import KernelSupervisor
from openai4s.server.session_recovery import SessionRecoveryService, kernel_idle_ttl


class _Kernel:
    def __init__(self) -> None:
        self.live = True
        self.shutdown_calls = 0

    def is_alive(self) -> bool:
        return self.live

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.live = False


class _Store:
    def __init__(self) -> None:
        self.reconciliations = []

    def abandon_live_kernel_generations(self, **kwargs):
        self.reconciliations.append(kwargs)
        return 3


def _service(
    *,
    session,
    now,
    blockers,
    released,
    background_at=None,
    ttl_s=10.0,
):
    def release(state, reason):
        state.kernels.stop(manual=False, reason=reason)
        released.append((state.root_frame_id, reason))
        return True

    return SessionRecoveryService(
        store=_Store(),
        sessions=lambda: [session],
        turn_active=lambda _root: blockers["turn"],
        approval_pending=lambda _root: blockers["approval"],
        background_active=lambda _state: blockers["background"],
        background_last_activity_ms=lambda _state: background_at["ms"]
        if background_at is not None
        else None,
        release_idle=release,
        owner_instance_id="daemon-test",
        ttl_s=ttl_s,
        sweep_interval_s=0.01,
        clock=lambda: now["s"],
    )


def test_ttl_parser_is_safe_and_zero_disables():
    assert kernel_idle_ttl({}, default=0) == 0
    assert kernel_idle_ttl({"OPENAI4S_KERNEL_IDLE_TTL": "12.5"}) == 12.5
    assert kernel_idle_ttl({"OPENAI4S_KERNEL_IDLE_TTL": "-1"}) == 0
    assert kernel_idle_ttl({"OPENAI4S_KERNEL_IDLE_TTL": "inf"}) == 0
    assert kernel_idle_ttl({"OPENAI4S_KERNEL_IDLE_TTL": "bad"}, default=7) == 7


def test_sweeper_requires_strict_expiry_and_every_blocker_to_be_clear():
    now = {"s": 0.0}
    kernel = _Kernel()
    supervisor = KernelSupervisor(clock_ms=lambda: int(now["s"] * 1000))
    supervisor.ensure("python", "base", lambda: kernel)
    session = SimpleNamespace(root_frame_id="root-1", kernels=supervisor)
    blockers = {
        "turn": False,
        "approval": False,
        "background": False,
    }
    released = []
    service = _service(
        session=session,
        now=now,
        blockers=blockers,
        released=released,
    )

    now["s"] = 10.0
    assert service.sweep_once() == []  # equal to TTL is not "exceeded"
    for blocker in blockers:
        now["s"] += 1.0
        blockers[blocker] = True
        assert service.sweep_once() == []
        blockers[blocker] = False

    assert service.sweep_once() == ["root-1"]
    assert released == [("root-1", "idle_ttl")]
    assert kernel.shutdown_calls == 1
    assert supervisor.status("python")["state"] == "ended"


def test_background_completion_advances_persisted_activity_before_release():
    now = {"s": 0.0}
    background_at = {"ms": 0}
    supervisor = KernelSupervisor(clock_ms=lambda: int(now["s"] * 1000))
    supervisor.ensure("python", "base", _Kernel)
    session = SimpleNamespace(root_frame_id="root-bg", kernels=supervisor)
    blockers = {
        "turn": False,
        "approval": False,
        "background": False,
    }
    released = []
    service = _service(
        session=session,
        now=now,
        blockers=blockers,
        released=released,
        background_at=background_at,
    )

    now["s"] = 20.0
    background_at["ms"] = 15_000
    assert service.sweep_once() == []
    assert supervisor.status("python")["last_activity_at"] == 15_000
    now["s"] = 25.001
    assert service.sweep_once() == ["root-bg"]


def test_recovery_scope_blocks_release_and_touches_generation():
    now = {"s": 0.0}
    supervisor = KernelSupervisor(clock_ms=lambda: int(now["s"] * 1000))
    supervisor.ensure("python", "base", _Kernel)
    session = SimpleNamespace(root_frame_id="root-recovery", kernels=supervisor)
    blockers = {
        "turn": False,
        "approval": False,
        "background": False,
    }
    released = []
    service = _service(
        session=session,
        now=now,
        blockers=blockers,
        released=released,
    )

    now["s"] = 20.0
    with service.recovery_scope(session):
        assert service.is_recovering("root-recovery")
        assert service.sweep_once() == []
    assert not service.is_recovering("root-recovery")
    assert supervisor.status("python")["last_activity_at"] == 20_000


def test_startup_reconcile_and_sweeper_thread_have_explicit_lifecycle():
    store = _Store()
    service = SessionRecoveryService(
        store=store,
        sessions=lambda: [],
        turn_active=lambda _root: False,
        approval_pending=lambda _root: False,
        background_active=lambda _session: False,
        release_idle=lambda _session, _reason: False,
        owner_instance_id="daemon-current",
        ttl_s=1,
        sweep_interval_s=0.01,
        clock=lambda: 3.0,
    )

    assert service.reconcile_startup() == 3
    assert store.reconciliations == [
        {
            "owner_instance_id": "daemon-current",
            "reason": "daemon_restart",
            "ended_at": 3000,
        }
    ]
    assert service.start() is True
    assert service.running
    service.stop()
    assert not service.running
