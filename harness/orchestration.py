"""Replay orchestration scenarios against the real reconciler (M4-4).

The generic runner in `runner.py` models a model loop and deliberately
imports nothing from production. This one is the other kind of harness
file, and it is that kind on purpose: like the action-routing eval, it
calls a production component on recorded input, because the thing under
test — the reconciler's decision function — has no live boundary that a
fake would stand in for. Its inputs are a workload row and a backend
observation, and both are *data*.

That distinction is the whole value. A harness that re-implemented the
reconciler's rules and then checked them would be asserting a model
against a model: it would stay green through any defect the model shares
with the code, which is every defect that comes from misunderstanding the
problem rather than mistyping the solution. Driving the real `Reconciler`
means a scenario declaring `failure` is a claim about the shipped
behaviour.

The backend is scripted, the store is in memory, and the trace comes out
in the same normalized shape as every other scenario — so a golden diff
reads the same whichever runner produced it.

**A declared failure fails when the run succeeds.** That is not a detail
of the evaluator; it is why half these scenarios exist. Roughly half of
this system's job is refusing — a stale epoch, a double submission, a
cancel that must not resurrect — and a scenario that scored "no exception"
would measure nothing about any of it.
"""

from __future__ import annotations

from typing import Any, Iterator

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
from openai4s.orchestration.ports import Created, Existing, Rejected, Unknown
from openai4s.orchestration.reconciler import Reconciler

from .normalize import normalized_trace_bytes
from .runner import FakeClock, FakeUUIDFactory, ScenarioResult, _evaluate, _Recorder
from .schema import Scenario


class ScenarioError(RuntimeError):
    """A scenario that cannot be run as written."""


class _MemoryStore:
    """The reconciler's `WorkloadStore`, in memory.

    Not a fake standing in for SQLite: the reconciler's Protocol *is* this
    surface, and `tests/test_orchestration_session.py` already drives the
    real Store. What a scenario is for is the trajectory — which step ran
    on which tick, in what order — and that is the same either way.
    """

    def __init__(self, workload: Workload) -> None:
        self.workload = workload
        self.allocations: list[Allocation] = []
        self._counter = 0

    def workloads_needing_attention(self) -> list[Workload]:
        return [] if self.workload.phase.is_terminal else [self.workload]

    def active_allocation(self, workload_id: str) -> Allocation | None:
        for allocation in reversed(self.allocations):
            if allocation.phase.is_active_allocation:
                return allocation
        return None

    def create_allocation(self, workload_id: str, epoch: int) -> Allocation:
        self._counter += 1
        allocation = Allocation(
            id=f"alloc_{self._counter}",
            workload_id=workload_id,
            epoch=epoch,
            submission_token=SubmissionToken(f"tok_{self._counter}"),
        )
        self.allocations.append(allocation)
        return allocation

    def save_allocation(self, allocation: Allocation) -> None:
        return None  # the objects are the rows here

    def save_workload(self, workload: Workload) -> None:
        return None


class _ScriptedBackend:
    """A resource plane that answers from a script, one entry per call.

    The script is the scenario's contract with the backend boundary, which
    is exactly what a `SubmitResult` and an `Observation` are: four cases
    and a phase. Nothing here decides anything — deciding is the
    reconciler's job, and the point is to watch it decide.
    """

    name = "scripted"

    def __init__(self, script: dict[str, Any]) -> None:
        self._submits: Iterator[dict] = iter(script.get("submits") or [])
        self._observations: Iterator[dict] = iter(script.get("observations") or [])
        self._by_token = script.get("find_by_token") or []
        self._token_calls = 0
        self.cancels: list[str] = []
        self.last_submit: dict | None = None

    def submit(self, *, allocation, spec, profile):
        try:
            step = next(self._submits)
        except StopIteration:
            raise ScenarioError(
                "the backend was asked to submit more times than the script "
                "allows; a scenario that runs off the end of its script is "
                "asserting nothing about the tick that did it"
            ) from None
        self.last_submit = step
        kind = step.get("result")
        if kind == "created":
            return Created(handle=self._handle(step, allocation))
        if kind == "existing":
            return Existing(handle=self._handle(step, allocation))
        if kind == "rejected":
            return Rejected(
                reason=Reason(step.get("reason") or "BACKEND_REJECTED"),
                detail=step.get("detail", ""),
            )
        if kind == "unknown":
            return Unknown(
                token=allocation.submission_token, detail=step.get("detail", "")
            )
        raise ScenarioError(f"unknown submit result {kind!r}")

    def observe(self, allocation):
        try:
            step = next(self._observations)
        except StopIteration:
            raise ScenarioError(
                "the backend was asked to observe more times than the script " "allows"
            ) from None
        reason = step.get("reason")
        return Observation(
            phase=Phase(step["phase"]),
            reason=Reason(reason) if reason else None,
        )

    def cancel(self, allocation, *, reason):
        self.cancels.append(allocation.id)

    def find_by_token(self, token):
        index = self._token_calls
        self._token_calls += 1
        try:
            answer = self._by_token[index]
        except IndexError:
            answer = None
        if not answer:
            return None
        return ExternalHandle(backend=self.name, external_id=str(answer))

    def diagnostics(self):
        return {}

    @staticmethod
    def _handle(step: dict, allocation) -> ExternalHandle:
        return ExternalHandle(
            backend="scripted",
            external_id=str(step.get("external_id") or f"job-{allocation.epoch}"),
        )


def _workload_from(spec: dict) -> Workload:
    profile = ResourceProfile(name=spec.get("profile") or "cpu-interactive")
    kind = WorkloadKind(spec.get("kind") or "BATCH")
    command = tuple(
        spec.get("command") or (("true",) if kind is WorkloadKind.BATCH else ())
    )
    return Workload(
        id=spec.get("id") or "wl_1",
        spec=WorkloadSpec(kind=kind, profile=profile, command=command),
        owner_user_id=spec.get("owner") or "u1",
        desired_state=DesiredState(spec.get("desired_state") or "RUNNING"),
    )


def run_orchestration_scenario(
    scenario: Scenario, *, offline: bool = True
) -> ScenarioResult:
    """Drive the real reconciler through this scenario's ticks."""
    if offline and not scenario.is_offline:
        raise ValueError(
            f"scenario {scenario.id!r} is not eligible for the offline tier"
        )
    script = dict(scenario.fixtures.get("orchestration") or {})
    if not script:
        raise ScenarioError(
            f"scenario {scenario.id!r} declares surface 'orchestration' but "
            f"carries no fixtures.orchestration block"
        )

    clock = FakeClock()
    uuid_factory = FakeUUIDFactory()
    recorder = _Recorder(
        run_id=uuid_factory(),
        root_frame_id=uuid_factory(),
        clock=clock,
        uuid_factory=uuid_factory,
    )
    workload = _workload_from(dict(script.get("workload") or {}))
    store = _MemoryStore(workload)
    backend = _ScriptedBackend(script)

    def emit(kind: str, payload: dict) -> None:
        recorder.emit(kind, phase="orchestration", status="running", payload=payload)

    reconciler = Reconciler(
        store=store,
        backends={"scripted": backend},
        default_backend="scripted",
        max_recoveries=int(script.get("max_recoveries", 3)),
        on_event=emit,
    )

    recorder.emit(
        "run_started",
        phase="lifecycle",
        status="running",
        payload={
            "scenario_id": scenario.id,
            "surface": scenario.surface,
            "permissions": {"noninteractive": scenario.permissions.noninteractive},
        },
    )

    ticks = int(script.get("ticks", 1))
    stop_at = script.get("stop_at_tick")
    stop_reason = script.get("stop_reason") or "USER_CANCELLED"
    for index in range(ticks):
        if stop_at is not None and index == int(stop_at):
            # A user (or a lapsed lease) asks for a stop between ticks —
            # which is the only place it can arrive, and the reason it must
            # not be overwritten by a reconciler pass that loaded the row
            # before it.
            workload.desired_state = DesiredState.STOPPED
            workload.reason = Reason(stop_reason)
            recorder.emit(
                "stop_requested",
                phase="orchestration",
                status="running",
                payload={"reason": stop_reason},
            )
        reconciler.tick()

    terminal_reason = (
        f"{workload.phase.value}:{workload.reason.value}"
        if workload.reason
        else workload.phase.value
    )
    recorder.emit(
        "run_finished",
        phase="lifecycle",
        status="terminal",
        payload={"terminal_reason": terminal_reason},
    )
    events = tuple(recorder.events)

    class _NoScript:
        """`_evaluate` asks a provider whether its script was consumed; an
        orchestration run has no provider, and saying "nothing remains" is
        the truthful answer rather than a special case in the evaluator."""

        remaining = 0

    class _NoFaults:
        """Likewise for the fault schedule. `unfired` being empty is true:
        a scenario on this surface declares its faults as backend answers,
        and one that never fires is a submit or observe step the script
        left unused — which the scripted backend already refuses to skip
        past silently."""

        unfired = ()

    errors = tuple(
        _evaluate(
            scenario,
            terminal_reason=terminal_reason,
            model_attempts=0,
            events=events,
            provider=_NoScript(),
            faults=_NoFaults(),
        )
    )
    return ScenarioResult(
        scenario_id=scenario.id,
        passed=not errors,
        terminal_reason=terminal_reason,
        model_attempts=0,
        events=events,
        errors=errors,
        normalized=normalized_trace_bytes(events),
    )


__all__ = ["ScenarioError", "run_orchestration_scenario"]
