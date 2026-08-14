"""Cluster control plane: workloads, allocations, and the backend boundary.

Deliberately importable without importing any backend. `models` and `ports`
are the contract; a scheduler-specific implementation lives in its own
subpackage and is imported by composition code, never from here — which is
what lets the INV-2 leak guard assert that this package's import graph
cannot reach a scheduler.
"""

from openai4s.orchestration.models import (
    Allocation,
    DesiredState,
    ExternalHandle,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    Workload,
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.ports import (
    AllocationBackend,
    Created,
    Existing,
    Rejected,
    SubmitResult,
    Unknown,
)

__all__ = [
    "Allocation",
    "AllocationBackend",
    "Created",
    "DesiredState",
    "Existing",
    "ExternalHandle",
    "Observation",
    "Phase",
    "Reason",
    "Rejected",
    "ResourceProfile",
    "SubmissionToken",
    "SubmitResult",
    "Unknown",
    "Workload",
    "WorkloadKind",
    "WorkloadSpec",
]
