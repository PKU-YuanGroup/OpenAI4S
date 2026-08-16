"""Provider-neutral values for the cluster control plane (plan M3a-1).

This module is the *orchestration* vocabulary: what work was asked for
(``Workload``), what resource was granted for one attempt at it
(``Allocation``), what shape that resource has (``ResourceProfile``), and why
something ended (``Reason``). It deliberately knows nothing about how any of
it is realised — no scheduler, no ssh, no submission command — because INV-2
makes that ignorance checkable: `tests/test_orchestration_backend_opacity.py`
asserts this module's source and import graph never name a scheduler. That
check is literal, and deliberately so: if "explanatory" mentions were
allowed, every leak would arrive as an explanatory mention. So the words
belong in a backend subpackage and in cluster.toml, including here in prose.

Terminology, fixed by the plan's §2 so two vocabularies cannot drift:

* the kernel layer's ``generation`` is this layer's **execution_epoch** —
  the same idea (a monotonically increasing incarnation counter whose old
  values must be refused, INV-7), named per layer;
* the spec's "declarative configuration version" is **spec_revision** here,
  never "generation", because that word is already spoken for.

Two shapes carry the invariants that are easy to lose:

``ExternalHandle`` keeps a backend's own identifier (a scheduler's job id, a
local pid) *wrapped*. INV-2 says the API exposes ``allocation_id`` and
nothing else; a bare string would have leaked through a dozen call sites by
the second week.

``SubmissionToken`` is minted and persisted *before* a submission is
attempted, which is what makes INV-8 expressible at all: after a lost
response, "did my submission land?" is a question about a token the backend
was told to record, not about a job id that may not have reached us.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkloadKind(str, Enum):
    """What the work *is*, which decides how it ends.

    A SESSION is interactive and ends when a person or a lease says so; a
    BATCH runs to completion on its own. The distinction is not cosmetic:
    INV-4 forbids an interactive task from implicitly creating an
    allocation, so the kind is what a scheduling decision consults.
    """

    SESSION = "SESSION"
    BATCH = "BATCH"


class DesiredState(str, Enum):
    """What the operator/user wants — the left-hand side of reconciliation."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class Phase(str, Enum):
    """Where a workload or allocation actually is.

    Terminal phases are terminal (INV-6): recovery mints a *new* epoch rather
    than rewinding one of these. `is_terminal` is the single place that says
    which are which, so a state machine and a projection cannot disagree.
    """

    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    GRANTED = "GRANTED"
    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LOST = "LOST"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_PHASES

    @property
    def is_active_allocation(self) -> bool:
        """Counts against INV-3's one-active-allocation-per-workload rule.

        The same set the schema's partial unique index uses; the index is
        the enforcement, this is the readable copy.

        ``RELEASING`` is in the set, which is a deliberate departure from
        the plan's appendix A draft (recorded in appendix D). INV-3 exists
        so two allocations cannot hold resources at once, and an allocation
        being torn down still holds one — excluding it would let a new
        submission start while the old job is still dying, which is the
        exact double-allocation the invariant forbids. It also kept the
        cancel barrier from finding its own allocation on the second pass,
        so a lagging backend produced a workload marked cancelled while its
        job was still running.
        """
        return self in _ACTIVE_PHASES


_TERMINAL_PHASES = frozenset(
    {Phase.COMPLETED, Phase.FAILED, Phase.CANCELLED, Phase.LOST}
)
_ACTIVE_PHASES = frozenset(
    {
        Phase.SUBMITTING,
        Phase.PENDING,
        Phase.GRANTED,
        Phase.ACTIVE,
        Phase.RELEASING,
    }
)


class Reason(str, Enum):
    """Standard reason codes (plan appendix C, trimmed from spec §40).

    An enum rather than free strings: a reason travels from a backend
    through the reconciler to a UI badge and an audit row, and a typo in
    that chain is a failure that reads as a different failure.
    """

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    POLICY_REJECTED = "POLICY_REJECTED"
    INVALID_SPEC = "INVALID_SPEC"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_SUBMISSION_UNKNOWN = "BACKEND_SUBMISSION_UNKNOWN"
    BACKEND_REJECTED = "BACKEND_REJECTED"
    UNSCHEDULABLE = "UNSCHEDULABLE"
    BOOTSTRAP_FAILED = "BOOTSTRAP_FAILED"
    WORKER_REGISTRATION_TIMEOUT = "WORKER_REGISTRATION_TIMEOUT"
    WORKER_LOST = "WORKER_LOST"
    NODE_FAILED = "NODE_FAILED"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    PREEMPTED = "PREEMPTED"
    USER_CANCELLED = "USER_CANCELLED"
    ADMIN_CANCELLED = "ADMIN_CANCELLED"
    SESSION_IDLE_TIMEOUT = "SESSION_IDLE_TIMEOUT"
    SESSION_MAX_LIFETIME_EXCEEDED = "SESSION_MAX_LIFETIME_EXCEEDED"
    STALE_EPOCH = "STALE_EPOCH"
    STALE_SPEC_REVISION = "STALE_SPEC_REVISION"
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    KERNEL_STATE_LOST = "KERNEL_STATE_LOST"


@dataclass(frozen=True)
class ResourceProfile:
    """What a workload asks for, in units a scientist states.

    Named profiles (`cpu-interactive`, `gpu-batch`) resolve to scheduler
    settings in cluster.toml and *only* there (D5): no queue or
    service-class name reaches this layer, the user, or the agent.
    """

    name: str
    cpus: int = 1
    memory_mb: int = 4096
    gpus: int = 0
    walltime_s: int = 3600
    nodes: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("profile name is required")
        for field_name in ("cpus", "memory_mb", "walltime_s", "nodes"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.gpus < 0:
            raise ValueError("gpus cannot be negative")


@dataclass(frozen=True)
class ExternalHandle:
    """A backend's own identifier for a granted resource, kept wrapped.

    INV-2: the API exposes ``allocation_id``. A raw scheduler id passed
    around as a string is how a scheduler's vocabulary reaches modules that
    are supposed to be unable to name it.
    """

    backend: str
    external_id: str
    namespace: str | None = None

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        space = f"{self.namespace}/" if self.namespace else ""
        return f"{self.backend}:{space}{self.external_id}"


@dataclass(frozen=True)
class SubmissionToken:
    """A submission's identity, minted and persisted BEFORE it is attempted.

    This is the whole of INV-8. A backend is told to record the token with
    the submission (in whatever free-text field it offers), so an Unknown
    result is answerable by asking the backend "do you have anything
    carrying this token?" — a question that has an answer even when the
    response that would have carried the job id was lost.
    """

    value: str

    @staticmethod
    def mint() -> SubmissionToken:
        return SubmissionToken(f"tok_{uuid.uuid4().hex}")


@dataclass(frozen=True)
class WorkloadSpec:
    """The declarative ask. Versioned by ``spec_revision`` (never
    'generation' — see the module docstring)."""

    kind: WorkloadKind
    profile: ResourceProfile
    command: tuple[str, ...] = ()
    workdir: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    spec_revision: int = 1

    def __post_init__(self) -> None:
        if self.kind is WorkloadKind.BATCH and not self.command:
            raise ValueError("a BATCH workload needs a command")
        if self.spec_revision < 1:
            raise ValueError("spec_revision starts at 1")


@dataclass(frozen=True)
class TaskSpec:
    """Work run *inside* an allocation this workload already holds.

    INV-4 is the whole reason this type exists separately from
    `WorkloadSpec`: an interactive task must never implicitly create an
    allocation. A distributed run is a step within a resource already
    granted — asking for a new one behind the user's back is how a session
    quietly becomes two jobs, one of which nobody is watching and both of
    which are billed.
    """

    command: tuple[str, ...]
    tasks: int = 1
    nodes: int = 1
    cpus_per_task: int = 1
    workdir: str | None = None
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("a task needs a command")
        for name in ("tasks", "nodes", "cpus_per_task"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class RecoveryStrategy(str, Enum):
    """What is restored when a session's resource goes away (M4-6).

    `WORKSPACE_ONLY` is what this version does and all it claims: the files
    survive because they were always on the shared filesystem, and the
    kernel's memory does not. `CHECKPOINT` is declared here and refused at
    every entry point, on purpose — the alternative to a named, refusing
    placeholder is a field that silently means WORKSPACE_ONLY, and a user
    who selected "checkpoint" and got a fresh interpreter has been told
    something untrue about results they may publish.

    The refusal is the feature until a real implementation exists. A
    checkpoint of a Python interpreter is not a thing this system can honour
    by trying harder: it needs process-level snapshotting (CRIU or
    equivalent) that the cluster must also support, and half of one would
    restore some state and quietly drop the rest, which is the worst of the
    three possible behaviours.
    """

    WORKSPACE_ONLY = "WORKSPACE_ONLY"
    CHECKPOINT = "CHECKPOINT"

    @property
    def supported(self) -> bool:
        return self is RecoveryStrategy.WORKSPACE_ONLY


class UnsupportedRecoveryStrategy(ValueError):
    """Raised for a strategy this version declares but cannot honour."""

    def __init__(self, strategy: "RecoveryStrategy | str") -> None:
        name = getattr(strategy, "value", strategy)
        super().__init__(
            f"recovery strategy {name} is not supported yet: this version "
            f"restores the workspace, and the kernel's memory is lost on "
            f"recovery (KERNEL_STATE_LOST). Selecting it and receiving a "
            f"fresh interpreter would be a claim about your results that is "
            f"not true, so it is refused rather than approximated."
        )
        self.strategy = name


@dataclass(frozen=True)
class TaskResult:
    """A finished step: what it was, and what it wrote.

    Both, because a distributed step is blocking — the caller that asked
    for it is the caller that wants its output, and making them fetch it
    separately means a log that can be lost between the two calls.
    """

    handle: "TaskHandle"
    output: str = ""


@dataclass(frozen=True)
class TaskHandle:
    """A running step, named the way its resource plane names it.

    Wrapped for the same reason `ExternalHandle` is: a raw step id passed
    around as a string is how a scheduler's vocabulary reaches modules that
    are supposed to be unable to name it (INV-2).
    """

    allocation_id: str
    step_id: str
    tasks: int = 1


@dataclass
class Workload:
    """One durable unit of asked-for work, across however many attempts."""

    id: str
    spec: WorkloadSpec
    owner_user_id: str
    project_id: str | None = None
    desired_state: DesiredState = DesiredState.RUNNING
    phase: Phase = Phase.PENDING
    execution_epoch: int = 0
    reason: Reason | None = None

    @staticmethod
    def new_id() -> str:
        return f"wl_{uuid.uuid4().hex[:12]}"


@dataclass
class Allocation:
    """One attempt at satisfying a workload: one epoch, one resource.

    Recovery does not mutate an allocation — it ends this one and creates
    the next at ``epoch + 1`` (INV-6/7), which is what makes the history a
    record rather than a running total.
    """

    id: str
    workload_id: str
    epoch: int
    submission_token: SubmissionToken
    phase: Phase = Phase.SUBMITTING
    handle: ExternalHandle | None = None
    reason: Reason | None = None
    #: The backend's own words, kept out of the state machine on purpose:
    #: core code branches on `phase`/`reason`, never on raw scheduler text.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new_id() -> str:
        return f"alloc_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Observation:
    """What a backend currently reports about one allocation.

    Normalised at the backend boundary: `phase` and `reason` are this
    layer's vocabulary, and whatever the scheduler actually said lives in
    `diagnostics` where no decision reads it.
    """

    phase: Phase
    reason: Reason | None = None
    handle: ExternalHandle | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "Allocation",
    "DesiredState",
    "ExternalHandle",
    "Observation",
    "Phase",
    "Reason",
    "RecoveryStrategy",
    "ResourceProfile",
    "SubmissionToken",
    "TaskHandle",
    "TaskResult",
    "TaskSpec",
    "Workload",
    "WorkloadKind",
    "UnsupportedRecoveryStrategy",
    "WorkloadSpec",
]
