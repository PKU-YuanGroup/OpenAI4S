"""The backend boundary: what the control plane may ask of a resource plane.

One Protocol (`AllocationBackend`) with four operations, and a result type
for the one operation that can fail in a way neither side knows the answer
to.

`SubmitResult` is four cases, not a bool, because collapsing them is how
INV-8 gets violated:

* ``Created`` — a new allocation exists, here is its handle.
* ``Existing`` — a submission carrying this token was already there. This is
  the *reconciled* answer to a retry, and returning it (rather than another
  Created) is what makes retrying safe.
* ``Rejected`` — the backend refused, with a reason this layer understands.
  A rejection is an answer; the workload can fail cleanly.
* ``Unknown`` — the request may or may not have landed. **Not** an error to
  retry blindly: the caller must first ask the backend whether anything
  carries the token (`find_by_token`), because a blind retry is how one
  submission becomes two jobs holding two GPUs.

`observe` returns this layer's vocabulary, never the scheduler's — a backend
that hands back raw states has only moved the translation problem into code
that INV-2 says must not know about schedulers.

Interactive remote sessions have one additional, optional capability.  Their
bootstrap credential exists before a queued worker starts, so a resource plane
that runs every workload as the same Unix identity must not claim that 0600
alone isolates it: a sibling Cell can read the file, register first, and become
the victim's kernel.  ``SessionIsolationProvider`` is the fail-closed contract
for a backend that really supplies a per-allocation security principal or mount
namespace.  Batch work does not use this capability or a worker credential.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from openai4s.orchestration.models import (
    Allocation,
    ExternalHandle,
    Observation,
    Reason,
    ResourceProfile,
    SubmissionToken,
    TaskResult,
    TaskSpec,
    WorkloadSpec,
)


@dataclass(frozen=True)
class Created:
    """The submission landed and this is its handle."""

    handle: ExternalHandle
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Existing:
    """A submission carrying this token was already present.

    The safe answer to a retry: it means "yours is already here", not "here
    is a second one".
    """

    handle: ExternalHandle
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rejected:
    """The backend refused, in terms this layer can act on."""

    reason: Reason
    detail: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Unknown:
    """The outcome is genuinely unknown — reconcile by token before retrying.

    Carrying the token in the result is deliberate: the only correct next
    move needs it, and making the caller fish it out of its own state is how
    that step gets skipped.
    """

    token: SubmissionToken
    detail: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


SubmitResult = Created | Existing | Rejected | Unknown


@runtime_checkable
class AllocationBackend(Protocol):
    """A resource plane the control plane can ask for allocations.

    Implementations live in their own subpackages under
    ``openai4s/orchestration/``; nothing about them is visible here, which
    is what INV-2's leak guard checks — including the names, which is why
    this sentence does not spell them.
    """

    #: Stable identifier recorded on every ExternalHandle this backend mints.
    name: str

    def submit(
        self,
        *,
        allocation: Allocation,
        spec: WorkloadSpec,
        profile: ResourceProfile,
    ) -> SubmitResult:
        """Ask for a resource. The token is on ``allocation`` and MUST be
        recorded with the submission so `find_by_token` can answer later."""
        ...

    def observe(self, allocation: Allocation) -> Observation:
        """Current state, in this layer's vocabulary."""
        ...

    def cancel(self, allocation: Allocation, *, reason: Reason) -> None:
        """Ask for release. Idempotent: cancelling something already gone is
        success, because the cancel barrier may run twice."""
        ...

    def find_by_token(self, token: SubmissionToken) -> ExternalHandle | None:
        """Whatever this backend holds carrying that token, if anything.

        The reconciliation step INV-8 requires after an `Unknown`.
        """
        ...

    def diagnostics(self) -> dict[str, Any]:
        """Backend health, for an operator staring at a stuck queue."""
        ...

    def log_paths(self, allocation_id: str) -> tuple[Any, Any]:
        """Where an allocation's stdout and stderr went, or `(None, None)`.

        On the Protocol rather than discovered with `getattr`, because that
        is what let the log-tail route answer empty strings for every
        cluster job while only `LocalBackend` implemented it -- a missing
        capability and "the job printed nothing" are the same response, and
        the route cannot tell them apart. A backend with nowhere to point
        returns `(None, None)` and says so; a backend that forgets to
        implement it now fails the Protocol check instead of failing
        quietly.
        """
        ...


@runtime_checkable
class TerminalAllocationAcknowledger(Protocol):
    """Optional backend-owned recovery-state garbage collection.

    A candidate is only a hint that backend recovery/deduplication state
    exists.  The control plane MUST load the allocation from its durable store
    and prove that its phase is terminal plus either the parent workload is
    terminal or a later recovery epoch was durably opened before acknowledging
    it. This keeps an uncommitted terminal observation from deleting the only
    fact that makes submission reconciliation safe after a crash.
    """

    def terminal_acknowledgement_candidates(self) -> tuple[str, ...]:
        """Allocation ids whose backend recovery state may be reclaimable."""
        ...

    def acknowledge_terminal(self, allocation: Allocation) -> None:
        """Idempotently discard recovery state for a durably terminal attempt."""
        ...


@runtime_checkable
class SessionIsolationProvider(Protocol):
    """Optional proof boundary for interactive remote-session placement.

    Returning true promises that code running in one allocation cannot read or
    modify another allocation's workspace or pre-use bootstrap credential.  A
    shared Unix uid plus 0700/0600 modes does not satisfy that promise.  A
    backend should implement this only when it establishes a per-allocation OS
    identity, container, or mount namespace and verifies that boundary before
    reporting a successful submission.  The true result is a backend-lifetime
    promise covering every interactive allocation it accepts, not a transient
    configuration observation or a profile-specific best effort.
    """

    def isolates_session_credentials(self) -> bool:
        """Whether sibling allocations are separated by a verified OS boundary."""
        ...


def has_session_credential_isolation(backend: object | None) -> bool:
    """Read the optional capability fail closed, including provider errors."""

    try:
        if backend is None or not isinstance(backend, SessionIsolationProvider):
            return False
        return backend.isolates_session_credentials() is True
    except Exception:  # pragma: no cover - defensive boundary around extensions
        return False


@runtime_checkable
class TaskRunner(Protocol):
    """A backend that can run work inside an allocation it already granted.

    Separate from `AllocationBackend` and optional, because not every
    resource plane has the concept and a Protocol nobody can implement is
    a Protocol that gets implemented badly. A caller asks
    `isinstance(backend, TaskRunner)` and says so plainly when the answer
    is no, rather than falling back to submitting a second allocation —
    which is exactly what INV-4 forbids.
    """

    def run_task(self, allocation: Allocation, spec: TaskSpec) -> TaskResult: ...


__all__ = [
    "AllocationBackend",
    "Created",
    "Existing",
    "Rejected",
    "SessionIsolationProvider",
    "SubmitResult",
    "TaskRunner",
    "TerminalAllocationAcknowledger",
    "Unknown",
    "has_session_credential_isolation",
]
