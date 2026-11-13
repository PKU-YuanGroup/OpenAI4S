from __future__ import annotations

import pytest

from openai4s.rl import (
    ResourceAdmission,
    ResourceCapacity,
    ResourceRequest,
)


def test_resource_request_validates_bounded_provider_neutral_fields():
    request = ResourceRequest(
        cpu_cores=4,
        gpu_count=1,
        gpu_memory_gb=80,
        max_wall_time_ms=10_000,
        priority="high",
        budget_group="rollout",
    )
    assert request.to_dict() == {
        "cpu_cores": 4,
        "gpu_count": 1,
        "gpu_memory_gb": 80.0,
        "max_wall_time_ms": 10_000,
        "priority": "high",
        "budget_group": "rollout",
    }
    with pytest.raises(ValueError, match="max_wall_time_ms"):
        ResourceRequest(max_wall_time_ms=0)
    with pytest.raises(ValueError, match="gpu_count"):
        ResourceRequest(gpu_count=-1)


def test_admission_wait_and_compute_time_are_separate():
    clock = iter((100, 250, 1_000))
    admission = ResourceAdmission(
        ResourceCapacity(cpu_cores=4, gpu_count=1, gpu_memory_gb=80),
        clock_ms=lambda: next(clock),
    )
    request = ResourceRequest(cpu_cores=4, gpu_count=1, gpu_memory_gb=80)
    first = admission.admit("job-1", request)
    assert first.status == "admitted"
    admission.bind_receipt("job-1", "provider-receipt-1")
    started = admission.start("job-1")
    assert started.admission_wait_ms == 150
    finished = admission.finish("job-1")
    assert finished.actual_compute_time_ms == 750
    assert finished.to_dict()["admission_wait_ms"] == 150


def test_admission_is_idempotent_and_waiting_does_not_consume_capacity():
    admission = ResourceAdmission(
        ResourceCapacity(cpu_cores=1, gpu_count=1, gpu_memory_gb=8),
        clock_ms=lambda: 10,
    )
    request = ResourceRequest(cpu_cores=1, gpu_count=1, gpu_memory_gb=8)
    first = admission.admit("job-1", request)
    assert admission.admit("job-1", request).status == "already_admitted"
    assert admission.admit("job-2", request).status == "waiting"
    with pytest.raises(ValueError, match="another request"):
        admission.admit("job-1", ResourceRequest(cpu_cores=0))
    admission.bind_receipt("job-1", "receipt-1")
    with pytest.raises(ValueError, match="another receipt"):
        admission.bind_receipt("job-1", "receipt-2")
    admission.start("job-1")
    admission.finish("job-1")
    assert admission.admit("job-2", request).status == "admitted"
    assert first.record is not None


def test_unstarted_allocation_can_release_but_running_one_cannot():
    admission = ResourceAdmission(ResourceCapacity(2, 1, 8), clock_ms=lambda: 1)
    request = ResourceRequest(gpu_count=1, gpu_memory_gb=8)
    admission.admit("job-release", request)
    released = admission.release("job-release")
    assert released.state == "released"
    with pytest.raises(ValueError, match="reserved"):
        admission.start("job-release")


def test_unknown_remote_allocation_continues_to_hold_capacity():
    admission = ResourceAdmission(ResourceCapacity(1, 1, 8), clock_ms=lambda: 1)
    request = ResourceRequest(gpu_count=1, gpu_memory_gb=8)
    admission.admit("job-unknown", request)
    admission.bind_receipt("job-unknown", "receipt-unknown")
    admission.start("job-unknown")
    admission.finish("job-unknown", state="unknown")
    assert admission.admit("job-next", request).status == "waiting"
