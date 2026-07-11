"""Persistent UUID identity layered over the supervisor's ABA generation."""

from __future__ import annotations

import uuid

from openai4s.kernel.supervisor import KernelSupervisor
from openai4s.store import Store


class _Kernel:
    next_pid = 7000

    def __init__(self, name: str) -> None:
        self.name = name
        self.live = True
        self.pid = self.next_pid
        type(self).next_pid += 1
        self.python = f"/env/{name}/bin/python"
        self.env_name = name
        self.env_root = f"/env/{name}"
        self.cwd = "/workspace"
        self.restart_calls = 0

    def is_alive(self) -> bool:
        return self.live

    def shutdown(self) -> None:
        self.live = False

    def restart(self) -> None:
        self.restart_calls += 1
        self.pid = self.next_pid
        type(self).next_pid += 1
        self.live = True


def test_supervisor_exposes_and_persists_uuid_across_worker_replacement(tmp_path):
    now = {"ms": 1000}
    store = Store(tmp_path / "supervisor.db")
    supervisor = KernelSupervisor(
        root_frame_id="root-1",
        generations=store,
        owner_instance_id="daemon-a",
        clock_ms=lambda: now["ms"],
    )
    first_kernel = _Kernel("base")
    first = supervisor.ensure("python", "base", lambda: first_kernel)
    assert str(uuid.UUID(first.generation_id)) == first.generation_id
    assert first.generation == 0
    assert supervisor.status("python")["generation_id"] == first.generation_id

    supervisor.record_bootstrap_if_current(
        "python",
        first.kernel,
        {"status": "active", "loaded_sidecars": []},
    )
    now["ms"] = 2000
    second_kernel = _Kernel("struct")
    second = supervisor.ensure("python", "struct", lambda: second_kernel)

    assert second.generation == 1
    assert second.generation_id != first.generation_id
    rows = store.list_kernel_generations("root-1", language="python")
    assert [row["ordinal"] for row in rows] == [0, 1]
    assert rows[0]["state"] == "released"
    assert rows[0]["ended_reason"] == "replaced"
    assert rows[1]["parent_generation_id"] == rows[0]["generation_id"]
    assert rows[1]["worker_pid"] == second_kernel.pid


def test_restart_gets_new_uuid_while_integer_generation_stays_aba_guard(tmp_path):
    now = {"ms": 1000}
    store = Store(tmp_path / "restart.db")
    supervisor = KernelSupervisor(
        root_frame_id="root-2",
        generations=store,
        owner_instance_id="daemon-a",
        clock_ms=lambda: now["ms"],
    )
    kernel = _Kernel("base")
    first = supervisor.ensure("python", "base", lambda: kernel)
    now["ms"] = 1500
    restarted = supervisor.restart("python")

    assert restarted.kernel is kernel
    assert restarted.generation == first.generation + 1
    assert restarted.generation_id != first.generation_id
    assert kernel.restart_calls == 1
    first_row = store.get_kernel_generation(first.generation_id)
    assert first_row["ended_reason"] == "restarted"

    now["ms"] = 2000
    supervisor.stop("python", manual=False, reason="idle_ttl")
    final = store.get_kernel_generation(restarted.generation_id)
    assert final["state"] == "released"
    assert final["ended_reason"] == "idle_ttl"
    status = supervisor.status("python")
    assert status["state"] == "ended"
    assert status["generation_id"] == restarted.generation_id


def test_r_generation_records_actual_rscript_instead_of_manager_python(tmp_path):
    store = Store(tmp_path / "r-generation.db")
    supervisor = KernelSupervisor(
        root_frame_id="root-r",
        generations=store,
        owner_instance_id="daemon-a",
        clock_ms=lambda: 1000,
    )
    kernel = _Kernel("r")
    kernel.mode = "r"
    kernel.argv = [
        "/bin/sh",
        "-c",
        "exec wrapper",
        "/opt/r/bin/Rscript",
        "/app/r_worker.R",
    ]
    lease = supervisor.ensure("r", "r", lambda: kernel)

    row = store.get_kernel_generation(lease.generation_id)
    assert row["environment"]["runtime"] == "r"
    assert row["environment"]["interpreter"] == "/opt/r/bin/Rscript"


def test_exact_bootstrap_failure_is_terminal_and_shuts_down_candidate(tmp_path):
    store = Store(tmp_path / "bootstrap-failed.db")
    supervisor = KernelSupervisor(
        root_frame_id="root-bootstrap",
        generations=store,
        owner_instance_id="daemon-a",
        clock_ms=lambda: 1000,
    )
    kernel = _Kernel("broken-sidecar")
    lease = supervisor.ensure("python", "base", lambda: kernel)

    assert supervisor.shutdown_if_current(
        lease,
        reason="bootstrap_failed",
        terminal_state="failed",
    )

    row = store.get_kernel_generation(lease.generation_id)
    assert row["state"] == "failed"
    assert row["ended_reason"] == "bootstrap_failed"
    assert kernel.live is False
