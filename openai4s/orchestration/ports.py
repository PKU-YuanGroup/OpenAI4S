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


__all__ = [
    "AllocationBackend",
    "Created",
    "Existing",
    "Rejected",
    "SubmitResult",
    "Unknown",
]
