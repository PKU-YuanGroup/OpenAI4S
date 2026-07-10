"""In-process deterministic runner for versioned harness scenarios."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from .faults import FakeClock, FakeUUIDFactory, FaultSchedule, InjectedFault
from .normalize import normalized_trace_bytes
from .providers.scripted_llm import ScriptedLLM, ScriptedProviderError
from .schema import SCHEMA_VERSION, EventEnvelope, Scenario


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    terminal_reason: str
    model_attempts: int
    events: tuple[EventEnvelope, ...]
    errors: tuple[str, ...]
    normalized: bytes

    @property
    def trace_sha256(self) -> str:
        return hashlib.sha256(self.normalized).hexdigest()


class _Recorder:
    def __init__(
        self,
        *,
        run_id: str,
        root_frame_id: str,
        clock: FakeClock,
        uuid_factory: FakeUUIDFactory,
    ):
        self.run_id = run_id
        self.root_frame_id = root_frame_id
        self.clock = clock
        self.uuid_factory = uuid_factory
        self.events: list[EventEnvelope] = []

    def emit(
        self,
        kind: str,
        *,
        phase: str,
        status: str,
        turn_id: str | None = None,
        parent_event_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> EventEnvelope:
        if parent_event_id is None and self.events:
            parent_event_id = self.events[-1].event_id
        event = EventEnvelope(
            schema_version=SCHEMA_VERSION,
            event_id=self.uuid_factory(),
            seq=len(self.events) + 1,
            run_id=self.run_id,
            root_frame_id=self.root_frame_id,
            turn_id=turn_id,
            parent_event_id=parent_event_id,
            kind=kind,
            phase=phase,
            status=status,
            monotonic_ms=self.clock.monotonic_ms(),
            payload=dict(payload or {}),
        )
        self.events.append(event)
        self.clock.advance_ms(1)
        return event


def _error_payload(exc: InjectedFault | ScriptedProviderError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_kind": exc.kind,
        "message": exc.message,
        "retryable": exc.retryable,
    }
    if isinstance(exc, ScriptedProviderError):
        if exc.status is not None:
            payload["status"] = exc.status
        if exc.headers:
            payload["headers"] = dict(exc.headers)
    return payload


def _ordered_events(events: tuple[EventEnvelope, ...]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    previous_ms: int | None = None
    for expected_seq, event in enumerate(events, start=1):
        if event.seq != expected_seq:
            errors.append(
                f"ordered_events: expected seq {expected_seq}, got {event.seq}"
            )
        if event.event_id in seen_ids:
            errors.append(f"ordered_events: duplicate event_id {event.event_id}")
        if event.parent_event_id is not None and event.parent_event_id not in seen_ids:
            errors.append(
                "ordered_events: parent_event_id does not refer to an earlier event"
            )
        if previous_ms is not None and event.monotonic_ms < previous_ms:
            errors.append("ordered_events: monotonic_ms moved backwards")
        seen_ids.add(event.event_id)
        previous_ms = event.monotonic_ms
    return errors


def _evaluate(
    scenario: Scenario,
    *,
    terminal_reason: str,
    model_attempts: int,
    events: tuple[EventEnvelope, ...],
    provider: ScriptedLLM,
    faults: FaultSchedule,
) -> list[str]:
    expect = scenario.expect
    errors: list[str] = []
    if terminal_reason != expect.terminal_reason:
        errors.append(
            f"terminal_reason: expected {expect.terminal_reason!r}, "
            f"got {terminal_reason!r}"
        )
    if model_attempts != expect.model_attempts:
        errors.append(
            f"model_attempts: expected {expect.model_attempts}, got {model_attempts}"
        )
    if expect.event_kinds:
        actual = tuple(event.kind for event in events)
        if actual != expect.event_kinds:
            errors.append(
                f"event_kinds: expected {expect.event_kinds!r}, got {actual!r}"
            )
    supported = {"ordered_events", "one_run_terminal", "script_consumed"}
    unknown = set(expect.invariants) - supported
    if unknown:
        errors.append(f"unknown invariant(s): {', '.join(sorted(unknown))}")
    if "ordered_events" in expect.invariants:
        errors.extend(_ordered_events(events))
    if "one_run_terminal" in expect.invariants:
        count = sum(event.kind == "run_finished" for event in events)
        if count != 1:
            errors.append(f"one_run_terminal: expected 1 run_finished, got {count}")
    if "script_consumed" in expect.invariants and provider.remaining != 0:
        errors.append(
            f"script_consumed: {provider.remaining} scripted response(s) remain"
        )
    # Always on: a declared fault that never fires (typo'd point, unreachable
    # occurrence) would otherwise let the scenario pass vacuously.
    for point, occurrence in faults.unfired:
        errors.append(
            f"faults: declared fault at ({point!r}, occurrence {occurrence}) "
            "never fired"
        )
    return errors


def run_scenario(
    scenario: Scenario,
    *,
    offline: bool = True,
    clock: FakeClock | None = None,
    uuid_factory: FakeUUIDFactory | None = None,
) -> ScenarioResult:
    """Run a harness-contract baseline without importing production runtime.

    This runner proves deterministic schema/event/fault behavior.  A scenario
    only becomes a production characterization when a later surface adapter
    explicitly drives the OpenAI4S CLI or gateway; this baseline makes no such
    claim merely because ``surface`` is present in the JSON contract.  The
    declared ``permissions`` mode is recorded in the trace but — like
    ``fixtures`` — not enforced here; only a surface adapter can exercise it.
    """

    if offline and not scenario.is_offline:
        raise ValueError(
            f"scenario {scenario.id!r} is not eligible for the offline tier"
        )
    clock = clock or FakeClock()
    uuid_factory = uuid_factory or FakeUUIDFactory()
    run_id = uuid_factory()
    root_frame_id = uuid_factory()
    recorder = _Recorder(
        run_id=run_id,
        root_frame_id=root_frame_id,
        clock=clock,
        uuid_factory=uuid_factory,
    )
    provider = ScriptedLLM(scenario.provider_script)
    faults = FaultSchedule(scenario.faults)
    messages: list[dict[str, Any]] = [{"role": "user", "content": scenario.task}]
    model_attempts = 0
    terminal_reason = "script_exhausted"
    # _Recorder.emit defaults parent_event_id to the previous event, which is
    # exactly the causal chain this linear loop produces.
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

    while provider.remaining:
        model_attempts += 1
        turn_id = uuid_factory()
        recorder.emit(
            "model_started",
            phase="model",
            status="running",
            turn_id=turn_id,
            payload={"attempt": model_attempts},
        )
        try:
            injected = faults.check("before_model")
            if injected is not None:
                recorder.emit(
                    "fault_injected",
                    phase="model",
                    status="error",
                    turn_id=turn_id,
                    payload={"point": injected.point, **_error_payload(injected)},
                )
                raise injected
            response = provider(messages)
        except (InjectedFault, ScriptedProviderError) as exc:
            recorder.emit(
                "model_finished",
                phase="model",
                status="error",
                turn_id=turn_id,
                payload={"attempt": model_attempts, **_error_payload(exc)},
            )
            terminal_reason = "model_error"
            break

        content = str(response.get("content", ""))
        messages.append({"role": "assistant", "content": content})
        step = provider.last_step
        recorder.emit(
            "model_finished",
            phase="model",
            status="ok",
            turn_id=turn_id,
            payload={
                "attempt": model_attempts,
                "content": content,
                "finish_reason": response.get("finish_reason"),
                "usage": response.get("usage", {}),
            },
        )
        if step is not None and step.terminal_reason is not None:
            terminal_reason = step.terminal_reason
            break

    recorder.emit(
        "run_finished",
        phase="lifecycle",
        status="terminal",
        payload={"terminal_reason": terminal_reason},
    )
    events = tuple(recorder.events)
    errors = tuple(
        _evaluate(
            scenario,
            terminal_reason=terminal_reason,
            model_attempts=model_attempts,
            events=events,
            provider=provider,
            faults=faults,
        )
    )
    return ScenarioResult(
        scenario_id=scenario.id,
        passed=not errors,
        terminal_reason=terminal_reason,
        model_attempts=model_attempts,
        events=events,
        errors=errors,
        normalized=normalized_trace_bytes(events),
    )
