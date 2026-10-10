"""Lab boundaries; persistence and provider side effects have separate owners.

Device failures are LabError, never bare transport exceptions: TIMEOUT can
leave a live session (query by command identity); UNAVAILABLE and PROTOCOL_ERROR
mean it is gone; ADAPTER_MISMATCH during open leaves no live session. Explicit
refusals return an unapplied Receipt. An unknown outcome is never auto-retried.

``query`` answers from a session that remembers every command id it accepted:
a Receipt when one is retained; ``None`` only when the session never received
that id, which is the proof that lets a caller record ``not_dispatched``; and
``LabError(OUTCOME_UNKNOWN)`` when the id was received but its receipt is no
longer retained. ``execute`` of such an id raises the same error rather than
running the command a second time.
Ledger dictionaries use decoded JSON column names, per contract §3.12.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from openai4s.lab.models import (
    DeviceDescriptor,
    Dispatch,
    LabCaller,
    Receipt,
    SessionOpened,
    SessionOpenRequest,
    StopResult,
)


class DevicePort(Protocol):
    device_id: str

    def describe(self, profile: str) -> DeviceDescriptor: ...
    def open(self, request: SessionOpenRequest) -> SessionOpened: ...
    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt: ...
    def query(self, session_id: str, provider_command_id: str) -> Receipt | None: ...
    def stop(self, session_id: str, reason: str) -> StopResult: ...
    def close(self, session_id: str) -> None: ...
    def alive(self, session_id: str) -> bool: ...


class LabLedgerPort(Protocol):
    """CAS and atomic writes; callers never assemble transactions themselves.

    Every run status change happens inside the composite write that also moves
    leases, observations and commands (create, initial observation, dispatch,
    receipt, unknown, not-dispatched, end); there is deliberately no free-form
    run status setter.
    """

    def create_run(self, run: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert creating. Duplicate (root_frame_id, create_idempotency_key):
        same config_hash returns (existing, False); mismatch raises
        LabError(IDEMPOTENCY_CONFLICT).
        """
        ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_runs(self, root_frame_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Newest created_at first."""
        ...

    def end_run(self, run_id: str, *, end_reason: str, status: str = "ended") -> bool:
        """End only nonterminal runs, writing ended_at and appending an event."""
        ...

    def nonterminal_runs(self) -> list[dict[str, Any]]:
        """Runs needing daemon-start reconciliation."""
        ...

    def insert_command(self, command: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Allocate seq. Duplicate (run_id, idempotency_key): same request_hash
        returns (existing, False); mismatch raises LabError(IDEMPOTENCY_CONFLICT).
        """
        ...

    def get_command(self, command_id: str) -> dict[str, Any] | None: ...

    def transition_command(
        self,
        command_id: str,
        *,
        to_state: str,
        from_states: Sequence[str] | None = None,
        **fields: Any,
    ) -> bool:
        """CAS from explicit/derived sources; illegal transitions raise ValueError."""
        ...

    def list_commands(
        self, run_id: str, *, after_seq: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Ascending seq."""
        ...

    def inflight_commands(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Admitted, dispatching, running and stop_requested commands."""
        ...

    def begin_dispatch(
        self,
        command_id: str,
        *,
        resource_keys: Sequence[str],
        expected_revision: int,
        now_ms: int | None = None,
    ) -> int:
        """One transaction checks ready run and expected revision, acquires ALL
        leases, increments fencing token, moves admitted -> dispatching and
        ready -> busy, returning the token. Any failed condition raises its
        LabError and rolls everything back; SQLite errors become
        PERSISTENCE_UNAVAILABLE.
        """
        ...

    def record_receipt(
        self,
        command_id: str,
        *,
        receipt: Mapping[str, Any],
        observation: Mapping[str, Any] | None,
        evaluation: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """One transaction commits command terminal state; when applied,
        increments revision/step_count and stores observation/evaluation; ends
        a terminated run, releases leases, appends events and updates
        consecutive_failures. Returns {run, command, observation | None}.
        """
        ...

    def mark_outcome_unknown(self, command_id: str, *, error: str) -> bool:
        """Command -> outcome_unknown; run -> quarantined; quarantine leases."""
        ...

    def mark_not_dispatched(
        self, command_id: str, *, error_code: str, error: str
    ) -> bool:
        """Record definite non-dispatch, release leases and move busy -> ready."""
        ...

    def append_initial_observation(
        self,
        run_id: str,
        *,
        observation: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        descriptor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write sequence 0 and move creating -> ready.

        ``descriptor`` is the opened session's descriptor: its assumptions
        and reproducibility claim replace the pinned ones; identity must match.
        """
        ...

    def latest_observation(self, run_id: str) -> dict[str, Any] | None: ...
    def list_observations(
        self, run_id: str, *, after_sequence: int = -1, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def list_evaluations(
        self, run_id: str, *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Evaluation-only boundary; never a default agent/UI projection."""
        ...

    def attach_observation_artifacts(
        self, run_id: str, versions: Mapping[str, str]
    ) -> None:
        """Atomic NULL-to-version bindings; same value is idempotent, replacement refuses."""
        ...

    def events_since(
        self, root_frame_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    def latest_event_seq(self, root_frame_id: str) -> int:
        """Zero when no events exist."""
        ...


class LabManagerPort(Protocol):
    """All mutating entry points reject execution_owner=recovery."""

    def list_devices(self, caller: LabCaller) -> list[dict[str, Any]]: ...
    def describe(
        self, caller: LabCaller, device_id: str, profile: str | None = None
    ) -> dict[str, Any]: ...
    def create_run(
        self, caller: LabCaller, request: dict[str, Any]
    ) -> dict[str, Any]: ...
    def observe(
        self, caller: LabCaller, run_id: str, *, full: bool = False
    ) -> dict[str, Any]: ...
    def execute(self, caller: LabCaller, request: dict[str, Any]) -> dict[str, Any]: ...
    def status(
        self, caller: LabCaller, run_id: str, command_id: str | None = None
    ) -> dict[str, Any]: ...
    def stop(
        self, caller: LabCaller, run_id: str, reason: str | None = None
    ) -> dict[str, Any]: ...
    def list_runs(
        self, caller: LabCaller, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...
    def events(
        self, caller: LabCaller, *, after_seq: int = 0, limit: int = 200
    ) -> dict[str, Any]: ...

    # Read-only views for the workbench and evaluation (W2 merge). They read
    # the ledger only: no provider call, no idle cleanup, allowed in recovery.
    def run(self, caller: LabCaller, run_id: str) -> dict[str, Any]: ...
    def describe_run(self, caller: LabCaller, run_id: str) -> dict[str, Any]: ...
    def commands(
        self, caller: LabCaller, run_id: str, *, after_seq: int = 0, limit: int = 50
    ) -> dict[str, Any]: ...
    def observations(
        self,
        caller: LabCaller,
        run_id: str,
        *,
        after_sequence: int = -1,
        limit: int = 20,
        full: bool = False,
    ) -> dict[str, Any]: ...
