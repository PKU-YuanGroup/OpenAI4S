"""Production Lab trajectories on a real temporary Store and an explicit fake.

Only this named adapter imports the Lab runtime. The reviewed golden contains
states, error codes and counts; provider observations, evaluations, identities
and clocks never enter the trace. A run compares its actual trajectory with the
golden even when called by the CLI, not only from characterization tests.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.lab.models import CommandOrigin, LabCaller, LabError
from openai4s.lab.runtime import build_lab_manager
from openai4s.permissions import broker
from openai4s.store import get_store

from .schema import Scenario, ScenarioValidationError

GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "golden_traces"
    / "v1"
    / "lab_simulation_expected.json"
)
CASES = (
    "lost_response",
    "duplicate_submission",
    "stale_revision",
    "provider_lost",
    "budget_exhausted",
    "approval_denied",
    "recovery_forbidden",
)


@dataclass(frozen=True)
class LabEvent:
    """The Lab trace deliberately has no identity or timestamp fields."""

    kind: str
    status: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class LabScenarioResult:
    scenario_id: str
    passed: bool
    terminal_reason: str
    model_attempts: int
    events: tuple[LabEvent, ...]
    errors: tuple[str, ...]
    normalized: bytes

    @property
    def trace_sha256(self) -> str:
        return hashlib.sha256(self.normalized).hexdigest()


class _CountingDevice(FakeExtractorDevice):
    def __init__(self) -> None:
        super().__init__()
        self.executions = 0
        self.queries = 0
        self.sessions: set[str] = set()

    def open(self, request):
        opened = super().open(request)
        self.sessions.add(opened.session_id)
        return opened

    def execute(self, session_id, dispatch):
        self.executions += 1
        return super().execute(session_id, dispatch)

    def query(self, session_id, command_id):
        self.queries += 1
        return super().query(session_id, command_id)


def _command(run_id: str, *, key: str = "first", revision: int = 0) -> dict:
    return {
        "run_id": run_id,
        "operation": "mix_model",
        "source": "extraction_vessel",
        "target": None,
        "parameters": {"duration": {"value": 1, "unit": "model_time"}},
        "expected_revision": revision,
        "idempotency_key": key,
    }


def _validate(scenario: Scenario, offline: bool) -> str:
    if scenario.surface != "lab_simulation":
        raise ScenarioValidationError("Lab adapter requires surface lab_simulation")
    if offline and not scenario.is_offline:
        raise ScenarioValidationError(
            "Lab scenario is not eligible for offline execution"
        )
    fixture = scenario.fixtures.get("lab_simulation")
    if (
        set(scenario.fixtures) != {"lab_simulation"}
        or not isinstance(fixture, dict)
        or set(fixture) != {"case"}
        or fixture["case"] not in CASES
    ):
        raise ScenarioValidationError(
            "expected one declared fixtures.lab_simulation.case"
        )
    # The common schema requires a model script. This surface has no model;
    # require an explicit sentinel instead of silently ignoring model steps or
    # a generic fault schedule that this adapter would never execute.
    if (
        len(scenario.provider_script) != 1
        or scenario.provider_script[0].response != {"content": "lab_simulation"}
        or scenario.provider_script[0].terminal_reason is not None
        or scenario.faults
    ):
        raise ScenarioValidationError(
            "Lab cases own fault injection; model script must be the lab_simulation sentinel"
        )
    if scenario.permissions.noninteractive != "rules_only":
        raise ScenarioValidationError("Lab scenarios require rules_only permissions")
    return fixture["case"]


def _trajectory(case: str, data_dir: Path) -> tuple[LabEvent, ...]:
    cfg = Config(data_dir=data_dir, team_mode=False, record_tape=False)
    store = get_store(cfg.db_path)
    device = _CountingDevice()
    registry = DeviceRegistry()
    registry.register(replace(fake_registration(), port_factory=lambda: device))
    manager = build_lab_manager(ledger_provider=lambda: store.lab, registry=registry)
    root = store.new_frame()
    caller = LabCaller(root, root, None, CommandOrigin.MANUAL_UI, None, "agent")
    events: list[LabEvent] = []
    run_id = ""

    def snapshot(kind: str, *, command: dict | None = None, code=None, **extra):
        run = store.lab.get_run(run_id)
        assert run is not None
        payload = {
            "run_state": run["status"],
            "end_reason": run["end_reason"],
            "revision": run["revision"],
            "command_count": run["command_count"],
            "step_count": run["step_count"],
            "command_state": command["state"] if command else None,
            "error_code": code or (command or {}).get("error_code"),
            "executions": device.executions,
            "queries": device.queries,
            **extra,
        }
        events.append(
            LabEvent(kind, payload["command_state"] or run["status"], payload)
        )

    try:
        request = {
            "device_id": device.device_id,
            "profile": "toy-extract-v0",
            "seed": 7,
        }
        if case == "budget_exhausted":
            request["budgets"] = {"max_steps": 1}
        run_id = manager.create_run(caller, request)["run"]["run_id"]
        snapshot("run_created")
        if case == "approval_denied":
            dispatcher = build_dispatcher(cfg, frame_id=root)
            dispatcher.set_lab_manager(lambda: manager)
            store.set_permission_rule(
                scope="conversation",
                scope_id=root,
                tool="lab_execute",
                pattern="*",
                decision="ask",
            )
            permission_broker = broker()
            requests = []

            resolutions = []

            def deny(event):
                if event.get("type") == "await_permission":
                    requests.append(event["decision_id"])
                    # resolve waits for the dispatcher's durable acknowledgement,
                    # so it must run on a different thread than the emit callback.
                    resolutions.append(
                        pool.submit(
                            permission_broker.resolve,
                            event["decision_id"],
                            allow=False,
                            scope="once",
                        )
                    )

            with ThreadPoolExecutor(max_workers=1) as pool:
                permission_broker.register_channel(root, deny, store=store)
                try:
                    result = dispatcher.invoke_lab_tool("lab_execute", _command(run_id))
                    for resolution in resolutions:
                        resolution.result()
                finally:
                    permission_broker.unregister_channel(root)
            decisions = [store.get_permission_request(key) for key in requests]
            denied = bool(decisions) and all(
                row and row["state"] == "denied" for row in decisions
            )
            # Host's established soft-failure envelope has no Lab error code.
            # Normalize it only when both the actual return and durable denial
            # support that interpretation; never invent a rejection on success.
            code = (
                "approval_denied"
                if denied
                and str(result.get("error", "")).startswith("Permission denied:")
                else None
            )
            snapshot(
                "approval_result",
                code=code,
                approval_requests=len(requests),
                denied_requests=sum(
                    bool(row and row["state"] == "denied") for row in decisions
                ),
            )
        elif case == "recovery_forbidden":
            code = None
            command = None
            try:
                command = manager.execute(
                    replace(caller, execution_owner="recovery"), _command(run_id)
                )["command"]
            except LabError as exc:
                code = exc.code.value
            snapshot("recovery_result", command=command, code=code)
        else:
            if case == "lost_response":
                device.lose_response_next()
                device.fail_query_next()
            elif case == "provider_lost":
                device.timeout_and_die_next()
            code = None
            try:
                command = manager.execute(caller, _command(run_id))["command"]
            except LabError as exc:
                code = exc.code.value
                rows = store.lab.list_commands(run_id)
                command = rows[-1] if rows else None
            snapshot("execute_result", command=command, code=code)
            if case == "lost_response" and command:
                result = manager.status(caller, run_id, command["command_id"])
                snapshot("status_reconciled", command=result["command"])
            elif case in {"duplicate_submission", "stale_revision", "budget_exhausted"}:
                request = _command(run_id)
                if case != "duplicate_submission":
                    request = _command(
                        run_id,
                        key="second",
                        revision=1 if case == "budget_exhausted" else 0,
                    )
                result = manager.execute(caller, request)
                snapshot("second_result", command=result["command"])
        # Capture the semantic result before cleanup turns a still-ready run
        # into ended(stopped). The final cleanup counts independently prove
        # that both manager ownership and provider sessions were released.
        manager.stop(caller, run_id)
        snapshot("run_stopped")
    finally:
        try:
            manager.close_all("Lab harness cleanup")
        finally:
            live = len(manager._live)
            alive = sum(device.alive(session) for session in device.sessions)
            events.append(
                LabEvent(
                    "cleanup",
                    "released" if live == alive == 0 else "leaked",
                    {"live_providers": live, "alive_sessions": alive},
                )
            )
            store.close()
    return tuple(events)


def run_lab_scenario(scenario: Scenario, *, offline: bool = True) -> LabScenarioResult:
    """Run real Lab behavior and compare it with independent reviewed data."""
    case = _validate(scenario, offline)
    with tempfile.TemporaryDirectory(prefix="openai4s-lab-harness-") as temporary:
        events = _trajectory(case, Path(temporary))
    semantic = events[-3]
    terminal = (
        semantic.payload.get("error_code")
        or semantic.payload.get("command_state")
        or semantic.status
    )
    document = {"schema_version": 1, "events": [asdict(event) for event in events]}
    normalized = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
    errors = []
    try:
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        expected = golden["cases"][scenario.id]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ScenarioValidationError(
            "Lab reviewed golden is missing or invalid"
        ) from exc
    if document != expected:
        errors.append("lab_simulation trajectory differs from reviewed golden")
    if terminal != scenario.expect.terminal_reason:
        errors.append(
            f"terminal_reason: expected {scenario.expect.terminal_reason!r}, got {terminal!r}"
        )
    if scenario.expect.model_attempts != 0:
        errors.append("model_attempts: Lab simulations do not call an LLM")
    if (
        scenario.expect.event_kinds
        and tuple(event.kind for event in events) != scenario.expect.event_kinds
    ):
        errors.append("event_kinds differ from declared Lab trajectory")
    # Lab's event record has no generic lifecycle/id envelope. Be explicit
    # about which invariants are available rather than silently skipping one.
    if scenario.expect.invariants:
        errors.append(
            "Lab scenarios require empty generic invariants; golden freezes the complete ordered trace"
        )
    return LabScenarioResult(
        scenario.id, not errors, str(terminal), 0, events, tuple(errors), normalized
    )
