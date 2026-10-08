"""Process-level Lab ownership, durable admission and conservative dispatch.

The metadata lock covers short ledger operations only. Provider calls never
hold it. A per-run nonblocking lock prevents concurrent device operations;
stop bypasses that lock and waits only for the current receipt's event.
"""

from __future__ import annotations

import math
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from threading import Event, Lock, RLock
from typing import Any

from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.manifest import (
    load_descriptor,
    match_command,
    observation_from_row,
    project_command,
    project_descriptor,
    project_observation,
    project_run,
)
from openai4s.lab.models import (
    Budgets,
    CommandRequest,
    DeviceDescriptor,
    Dispatch,
    ErrorCode,
    LabCaller,
    LabError,
    Receipt,
    SessionOpenRequest,
    canonical_json,
    config_hash,
    new_id,
    request_hash,
    sha256_hex,
)
from openai4s.lab.policy import admission_error
from openai4s.lab.ports import DevicePort, LabLedgerPort

_TERMINAL = {"ended", "failed"}
_SENT = {"dispatching", "running", "stop_requested"}
_COMMAND_DONE = {"succeeded", "failed", "rejected", "not_dispatched", "stopped"}


@dataclass(frozen=True)
class LabLimits:
    max_live_providers: int = 4
    stop_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            type(self.max_live_providers) is not int
            or self.max_live_providers < 1
            or isinstance(self.stop_wait_seconds, bool)
            or not isinstance(self.stop_wait_seconds, (int, float))
            or not math.isfinite(self.stop_wait_seconds)
            or self.stop_wait_seconds < 0
        ):
            raise LabError(ErrorCode.INVALID_PARAMETERS, "Invalid Lab manager limits")


@dataclass
class _LiveRun:
    port: DevicePort
    session_id: str
    descriptor: DeviceDescriptor
    last_activity_ms: int
    lock: Any = field(default_factory=Lock)
    done: Event = field(default_factory=Event)
    active_command: str | None = None
    stopping: bool = False
    needs_restart: bool = False


class LabManager:
    def __init__(
        self,
        ledger_provider: Callable[[], LabLedgerPort],
        registry: DeviceRegistry,
        *,
        instance_id: str,
        clock_ms: Callable[[], int],
        limits: LabLimits = LabLimits(),
    ) -> None:
        self._ledger_provider = ledger_provider
        self._registry = registry
        self._instance_id = instance_id
        self._clock_ms = clock_ms
        self._limits = limits
        self._lock = RLock()
        self._live: dict[str, _LiveRun] = {}
        self._opening: dict[str, str] = {}
        self._discarded: set[str] = set()
        self._closed = False
        self.startup_summary: dict[str, int] = {}

    @property
    def _ledger(self) -> LabLedgerPort:
        # Never keep a repository belonging to a closed Store generation.
        return self._ledger_provider()

    @staticmethod
    def _writable(caller: LabCaller) -> None:
        if caller.execution_owner == "recovery":
            raise LabError(
                ErrorCode.REPLAY_FORBIDDEN, "Lab side effects cannot be replayed"
            )

    def _run(self, caller: LabCaller, run_id: str) -> dict[str, Any]:
        run = self._ledger.get_run(run_id)
        if (
            run is None
            or run["root_frame_id"] != caller.root_frame_id
            or run["owner_user_id"] != caller.owner_user_id
        ):
            raise LabError(ErrorCode.RUN_NOT_FOUND, "Lab run was not found")
        return run

    def _observation(
        self, run: Mapping[str, Any], *, full: bool = False, row: Any = None
    ) -> dict[str, Any] | None:
        if row is None:
            row = self._ledger.latest_observation(run["run_id"])
        if row is None:
            return None
        descriptor = load_descriptor(run["descriptor"])
        return project_observation(
            observation_from_row(row),
            channels=descriptor.observation_channels,
            full=full,
        )

    def _result(self, run_id: str, command_id: str | None = None) -> dict[str, Any]:
        run = self._ledger.get_run(run_id)
        if run is None:
            raise LabError(ErrorCode.RUN_NOT_FOUND, "Lab run was not found")
        command = self._ledger.get_command(command_id) if command_id else None
        observation = None
        if command is None:
            observation = self._observation(run)
        elif command["observation_id"]:
            # A replay must return this command's observation, not a later one.
            for row in self._ledger.list_observations(run_id, limit=2**63 - 1):
                if row["observation_id"] == command["observation_id"]:
                    observation = self._observation(run, row=row)
                    break
        return {
            "run": project_run(run),
            "command": project_command(command) if command is not None else None,
            "observation": observation,
        }

    @staticmethod
    def _close(live: _LiveRun) -> None:
        try:
            live.port.close(live.session_id)
        except LabError:
            # A dead port has already lost its process/session.
            pass

    def _finish(self, run_id: str, reason: str) -> None:
        # Make every outstanding intent reconcilable before ending the run:
        # startup reconciliation deliberately visits only nonterminal runs.
        with self._lock:
            live = self._live.get(run_id)
            if live is not None:
                live.stopping = True
            for command in self._ledger.inflight_commands(run_id):
                if command["state"] == "admitted":
                    self._ledger.mark_not_dispatched(
                        command["command_id"],
                        error_code="run_ended",
                        error="Lab run ended before dispatch",
                    )
                elif command["state"] in _SENT:
                    self._ledger.mark_outcome_unknown(
                        command["command_id"], error="Provider receipt is unavailable"
                    )
            self._ledger.end_run(run_id, end_reason=reason)
        if live is not None:
            self._close(live)
            with self._lock:
                if self._live.get(run_id) is live:
                    self._live.pop(run_id)

    def _sweep(self, caller: LabCaller) -> None:
        if caller.execution_owner == "recovery":
            return
        with self._lock:
            expired = []
            for run_id, live in self._live.items():
                if live.active_command or live.stopping or live.needs_restart:
                    continue
                run = self._ledger.get_run(run_id)
                if (
                    run is not None
                    and self._clock_ms() - live.last_activity_ms
                    >= run["budgets"]["idle_timeout_ms"]
                ):
                    live.stopping = True
                    expired.append(run_id)
        for run_id in expired:
            self._finish(run_id, "idle_timeout")

    def list_devices(self, caller: LabCaller) -> list[dict[str, Any]]:
        self._sweep(caller)
        result = []
        for reg in self._registry.list():
            # The registration owns optional installation availability. No
            # provider is started just to populate the device picker.
            availability = getattr(reg, "available", None)
            available = availability() if callable(availability) else None
            result.append(
                {
                    "device_id": reg.device_id,
                    "backend": reg.backend,
                    "mode": reg.mode.value,
                    "title": reg.title,
                    "profiles": list(reg.profiles),
                    "available": available,
                }
            )
        return result

    def describe(
        self, caller: LabCaller, device_id: str, profile: str | None = None
    ) -> dict[str, Any]:
        self._sweep(caller)
        reg = self._registry.get(device_id)
        profile = reg.profiles[0] if profile is None else profile
        return project_descriptor(
            load_descriptor(self._registry.describe(device_id, profile).to_dict())
        )

    def create_run(self, caller: LabCaller, request: dict[str, Any]) -> dict[str, Any]:
        self._writable(caller)
        self._sweep(caller)
        if (
            not isinstance(request, dict)
            or set(request)
            - {"device_id", "profile", "seed", "budgets", "options", "idempotency_key"}
            or not {"device_id", "profile"} <= set(request)
            or not all(
                isinstance(request[k], str) and request[k]
                for k in ("device_id", "profile")
            )
        ):
            raise LabError(ErrorCode.INVALID_PARAMETERS, "Invalid Lab create request")
        key = request.get("idempotency_key", "auto-" + secrets.token_hex(16))
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 128
            or any(not 32 <= ord(c) <= 126 for c in key)
        ):
            raise LabError(
                ErrorCode.INVALID_PARAMETERS, "Invalid create idempotency key"
            )
        descriptor = load_descriptor(
            self._registry.describe(request["device_id"], request["profile"]).to_dict()
        )
        budgets = Budgets.from_dict(request.get("budgets", {}))
        budgets = replace(
            budgets, max_steps=min(budgets.max_steps, descriptor.limits["max_steps"])
        )
        # Reuse the typed opening value to validate options and seed without
        # reflecting arbitrary input keys or provider internals in errors.
        opening = SessionOpenRequest.from_dict(
            {
                "profile": descriptor.profile,
                "seed": request.get("seed"),
                "options": request.get("options", {}),
                "expected_capability_revision": descriptor.capability_revision,
            }
        )
        with self._lock:
            seed = opening.seed
            if seed is None:
                # The ledger port has no create-key lookup. This also handles
                # retries after a manager/daemon restart, unlike a seed cache.
                existing = next(
                    (
                        r
                        for r in self._ledger.list_runs(
                            caller.root_frame_id, limit=2**63 - 1
                        )
                        if r["create_idempotency_key"] == key
                    ),
                    None,
                )
                seed = (
                    existing["seed"]
                    if existing is not None
                    else secrets.randbelow(2**31)
                )
            opening = replace(opening, seed=seed)
            config = {
                "device_id": descriptor.device_id,
                "profile": descriptor.profile,
                "seed": seed,
                "budgets": budgets.to_dict(),
                "options": dict(opening.options),
            }
            run, created = self._ledger.create_run(
                {
                    "run_id": new_id("labrun"),
                    "root_frame_id": caller.root_frame_id,
                    "frame_id": caller.frame_id,
                    "owner_user_id": caller.owner_user_id,
                    "mode": descriptor.mode.value,
                    "backend": descriptor.backend,
                    "device_id": descriptor.device_id,
                    "profile": descriptor.profile,
                    "adapter_version": descriptor.backend_version["adapter_version"],
                    "backend_source_sha": descriptor.backend_version.get("source_sha"),
                    "capability_revision": descriptor.capability_revision,
                    "descriptor": descriptor.to_dict(),
                    "config": config,
                    "config_hash": config_hash(
                        descriptor.device_id,
                        descriptor.profile,
                        seed,
                        budgets,
                        opening.options,
                    ),
                    "seed": seed,
                    "budgets": budgets.to_dict(),
                    "daemon_instance": self._instance_id,
                    "create_idempotency_key": key,
                }
            )
            run_id = run["run_id"]
            self._run(caller, run_id)
            if not created:
                return {
                    "run": project_run(run),
                    "descriptor": project_descriptor(
                        load_descriptor(run["descriptor"])
                    ),
                    "observation": self._observation(run),
                }
            if (
                self._closed
                or len(self._live) + len(self._opening)
                >= self._limits.max_live_providers
            ):
                self._ledger.end_run(
                    run_id, end_reason="create_failed", status="failed"
                )
                raise LabError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "Lab live provider limit reached or manager closed",
                )
            self._opening[run_id] = caller.root_frame_id
        live = None
        try:
            port = self._registry.get(descriptor.device_id).port_factory()
            opened = port.open(opening)
            live = _LiveRun(port, opened.session_id, descriptor, self._clock_ms())
            actual = load_descriptor(opened.descriptor.to_dict())
            if (
                actual.device_id,
                actual.backend,
                actual.mode,
                actual.profile,
                actual.capability_revision,
            ) != (
                descriptor.device_id,
                descriptor.backend,
                descriptor.mode,
                descriptor.profile,
                descriptor.capability_revision,
            ):
                raise LabError(
                    ErrorCode.ADAPTER_MISMATCH,
                    "Opened device differs from registration",
                )
            with self._lock:
                current = self._ledger.get_run(run_id)
                if (
                    self._closed
                    or run_id in self._discarded
                    or current is None
                    or current["status"] != "creating"
                ):
                    raise LabError(ErrorCode.RUN_ENDED, "Lab run ended during creation")
                run = self._ledger.append_initial_observation(
                    run_id,
                    observation={
                        **opened.observation,
                        "sim_time_unit": descriptor.time["unit"],
                    },
                    evaluation=opened.evaluation,
                )
                live.done.set()
                self._opening.pop(run_id, None)
                self._live[run_id] = live
                return {
                    "run": project_run(run),
                    "descriptor": project_descriptor(actual),
                    "observation": self._observation(run),
                }
        except Exception as exc:
            if live is not None:
                self._close(live)
            with self._lock:
                self._live.pop(run_id, None)
                self._ledger.end_run(
                    run_id, end_reason="create_failed", status="failed"
                )
            code = (
                exc.code
                if isinstance(exc, LabError)
                else ErrorCode.PROVIDER_UNAVAILABLE
            )
            raise LabError(code, "Lab provider creation failed") from None
        finally:
            with self._lock:
                self._opening.pop(run_id, None)
                self._discarded.discard(run_id)

    def observe(
        self, caller: LabCaller, run_id: str, *, full: bool = False
    ) -> dict[str, Any]:
        # Observation is strictly ledger-only, including for recovery callers.
        run = self._run(caller, run_id)
        return {
            "run": project_run(run),
            "observation": self._observation(run, full=full),
        }

    def _policy_run(self, run: dict[str, Any]) -> dict[str, Any]:
        # W1 counts receipt failures but not host rejections. Derive the full
        # trailing failure count from durable command history for admission.
        failures = 0
        after = 0
        while True:
            commands = self._ledger.list_commands(
                run["run_id"], after_seq=after, limit=200
            )
            if not commands:
                break
            for command in commands:
                if command["state"] in {"failed", "rejected"}:
                    failures += 1
                elif command["state"] == "succeeded":
                    failures = 0
            after = commands[-1]["seq"]
        return {**run, "consecutive_failures": failures}

    def execute(self, caller: LabCaller, request: dict[str, Any]) -> dict[str, Any]:
        self._writable(caller)
        req = CommandRequest.from_dict(request)
        self._sweep(caller)
        with self._lock:
            run = self._run(caller, req.run_id)
            descriptor = load_descriptor(run["descriptor"])
            normalized = None
            error = None
            try:
                normalized = match_command(descriptor, req)
            except LabError as exc:
                error = exc
            digest = (
                request_hash(normalized)
                if normalized is not None
                else sha256_hex(canonical_json(req.to_dict()))
            )
            error = error or admission_error(
                self._policy_run(run),
                Budgets.from_dict(run["budgets"]),
                self._clock_ms(),
            )
            live = self._live.get(req.run_id)
            if live is not None and live.needs_restart:
                error = LabError(
                    ErrorCode.RESOURCE_QUARANTINED,
                    "Lab run requires restart reconciliation",
                )
            if error is None and (live is None or live.stopping):
                error = LabError(
                    ErrorCode.RESOURCE_BUSY if live else ErrorCode.PROVIDER_UNAVAILABLE,
                    "Lab provider is not accepting commands",
                )
            command, inserted = self._ledger.insert_command(
                {
                    "command_id": new_id("labcmd"),
                    "run_id": req.run_id,
                    "idempotency_key": req.idempotency_key,
                    "request_hash": digest,
                    "operation": req.operation,
                    "capability_id": normalized.capability_id if normalized else None,
                    "request": normalized.to_dict() if normalized else req.to_dict(),
                    "expected_revision": req.expected_revision,
                    "origin": caller.origin.value,
                    "actor_frame_id": caller.frame_id,
                    "owner_user_id": caller.owner_user_id,
                    "approval_ref": caller.approval_ref,
                    "state": "rejected" if error else "admitted",
                    "error_code": error.code.value if error else None,
                    "error": error.message if error else None,
                }
            )
            command_id = command["command_id"]
            if not inserted:
                return self._result(req.run_id, command_id)
            if error is not None:
                if error.code is ErrorCode.BUDGET_EXHAUSTED and error.details == {
                    "budget": "max_steps"
                }:
                    self._ledger.end_run(req.run_id, end_reason="budget_exhausted")
                    # Close outside the metadata lock below.
                else:
                    return self._result(req.run_id, command_id)
            else:
                assert live is not None and normalized is not None
                if not live.lock.acquire(blocking=False):
                    self._ledger.transition_command(
                        command_id,
                        to_state="rejected",
                        error_code="resource_busy",
                        error="Lab run is busy",
                    )
                    return self._result(req.run_id, command_id)
                live.active_command = command_id
                live.done.clear()
                live.last_activity_ms = self._clock_ms()
        if error is not None:
            self._finish(req.run_id, "budget_exhausted")
            return self._result(req.run_id, command_id)
        assert live is not None and normalized is not None
        try:
            capability = next(
                c
                for c in descriptor.capabilities
                if c.capability_id == normalized.capability_id
            )
            with self._lock:
                # Stop may have won before begin_dispatch. Its durable run end
                # prevents dispatch; its admitted intent is not_dispatched.
                current = self._ledger.get_command(command_id)
                if current is None or current["state"] != "admitted":
                    return self._result(req.run_id, command_id)
                if live.stopping:
                    self._ledger.mark_not_dispatched(
                        command_id,
                        error_code="run_ended",
                        error="Stop requested before dispatch",
                    )
                    return self._result(req.run_id, command_id)
                try:
                    token = self._ledger.begin_dispatch(
                        command_id,
                        resource_keys=[
                            f"lab:{run['device_id']}#{req.run_id}:{r}"
                            for r in capability.resources
                        ],
                        expected_revision=req.expected_revision,
                    )
                except LabError as exc:
                    if exc.code is ErrorCode.PERSISTENCE_UNAVAILABLE:
                        try:
                            self._ledger.mark_not_dispatched(
                                command_id,
                                error_code=exc.code.value,
                                error="Dispatch intent could not be persisted",
                            )
                        except LabError:
                            live.needs_restart = True
                        raise
                    self._ledger.transition_command(
                        command_id,
                        to_state="rejected",
                        error_code=exc.code.value,
                        error=exc.message,
                    )
                    return self._result(req.run_id, command_id)
            try:
                receipt = live.port.execute(
                    live.session_id,
                    Dispatch(
                        command_id, normalized, {r: token for r in capability.resources}
                    ),
                )
            except LabError as exc:
                if exc.code in {
                    ErrorCode.PROVIDER_TIMEOUT,
                    ErrorCode.OUTCOME_UNKNOWN,
                } and self._alive(live):
                    self._query(req.run_id, command_id, live)
                else:
                    self._unknown(req.run_id, command_id, live, lost=True)
            else:
                self._record(req.run_id, command_id, live, receipt)
            return self._result(req.run_id, command_id)
        finally:
            with self._lock:
                live.active_command = None
                live.last_activity_ms = self._clock_ms()
                live.done.set()
                live.lock.release()

    @staticmethod
    def _alive(live: _LiveRun) -> bool:
        try:
            return live.port.alive(live.session_id)
        except LabError:
            return False

    def _record(
        self, run_id: str, command_id: str, live: _LiveRun, receipt: Receipt
    ) -> None:
        if receipt.provider_command_id != command_id:
            self._unknown(run_id, command_id, live, lost=True)
        try:
            with self._lock:
                current = self._ledger.get_command(command_id)
                if current is None or current["state"] in _COMMAND_DONE:
                    return
                result = self._ledger.record_receipt(
                    command_id,
                    receipt=receipt.to_dict(),
                    observation=(
                        {
                            **receipt.observation,
                            "sim_time_unit": live.descriptor.time["unit"],
                        }
                        if receipt.observation is not None
                        else None
                    ),
                    evaluation=receipt.evaluation,
                )
                ended = result["run"]["status"] in _TERMINAL
                if ended:
                    live.stopping = True
        except LabError as exc:
            with self._lock:
                live.needs_restart = True
            if exc.code is ErrorCode.PERSISTENCE_UNAVAILABLE:
                raise LabError(
                    ErrorCode.PERSISTENCE_UNAVAILABLE,
                    "Device may have executed; restart reconciliation is required",
                    {"command_id": command_id},
                ) from None
            self._unknown(run_id, command_id, live, lost=True)
            return
        if ended:
            self._finish(run_id, result["run"]["end_reason"])

    def _unknown(
        self, run_id: str, command_id: str, live: _LiveRun, *, lost: bool
    ) -> None:
        try:
            with self._lock:
                self._ledger.mark_outcome_unknown(
                    command_id, error="Provider receipt is unavailable"
                )
        except LabError:
            with self._lock:
                live.needs_restart = True
            raise
        if lost:
            self._finish(run_id, "provider_lost")
        raise LabError(
            ErrorCode.OUTCOME_UNKNOWN,
            "Command outcome is unknown; it will not be resent",
            {"command_id": command_id},
        )

    def _query(self, run_id: str, command_id: str, live: _LiveRun) -> None:
        try:
            receipt = live.port.query(live.session_id, command_id)
        except LabError as exc:
            lost = exc.code in {
                ErrorCode.PROVIDER_UNAVAILABLE,
                ErrorCode.PROVIDER_PROTOCOL_ERROR,
            } or not self._alive(live)
            self._unknown(run_id, command_id, live, lost=lost)
            return
        if receipt is not None:
            self._record(run_id, command_id, live, receipt)
            return
        try:
            with self._lock:
                current = self._ledger.get_command(command_id)
                if current is not None and current["state"] == "stop_requested":
                    self._ledger.mark_outcome_unknown(
                        command_id,
                        error="Resolving stop with provider non-receipt evidence",
                    )
                self._ledger.mark_not_dispatched(
                    command_id,
                    error_code="provider_timeout",
                    error="Provider confirms command was never received",
                )
        except LabError:
            with self._lock:
                live.needs_restart = True
            raise

    def status(
        self, caller: LabCaller, run_id: str, command_id: str | None = None
    ) -> dict[str, Any]:
        self._sweep(caller)
        with self._lock:
            self._run(caller, run_id)
            command = self._ledger.get_command(command_id) if command_id else None
            if command_id and (command is None or command["run_id"] != run_id):
                raise LabError(ErrorCode.RUN_NOT_FOUND, "Lab command was not found")
            live = self._live.get(run_id)
            query = bool(
                command
                and command["state"] == "outcome_unknown"
                and live
                and not live.needs_restart
                and not live.stopping
                and caller.execution_owner != "recovery"
            )
            if query and live is not None:
                query = live.lock.acquire(blocking=False)
                if query:
                    live.active_command = command_id
                    live.done.clear()
        if query:
            assert live is not None and command_id is not None
            try:
                if self._alive(live):
                    self._query(run_id, command_id, live)
                else:
                    self._unknown(run_id, command_id, live, lost=True)
            except LabError as exc:
                if exc.code is not ErrorCode.OUTCOME_UNKNOWN:
                    raise
            finally:
                with self._lock:
                    live.active_command = None
                    live.done.set()
                    live.lock.release()
        return self._result(run_id, command_id)

    def stop(
        self, caller: LabCaller, run_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        self._writable(caller)
        self._sweep(caller)
        with self._lock:
            run = self._run(caller, run_id)
            live = self._live.get(run_id)
            if live is not None:
                live.stopping = True
                if live.active_command:
                    self._ledger.transition_command(
                        live.active_command,
                        to_state="stop_requested",
                        from_states=["dispatching", "running"],
                    )
            semantics = project_descriptor(load_descriptor(run["descriptor"]))["stop"][
                "semantics"
            ]
        if live is not None:
            if live.active_command:
                live.done.wait(timeout=self._limits.stop_wait_seconds)
            else:
                try:
                    live.port.stop(live.session_id, reason or "stopped")
                except LabError:
                    pass
        self._finish(run_id, "stopped")
        return {
            "run": project_run(self._run(caller, run_id)),
            "stopped": True,
            "semantics": semantics,
        }

    def list_runs(self, caller: LabCaller, *, limit: int = 20) -> list[dict[str, Any]]:
        self._sweep(caller)
        return [
            project_run(r)
            for r in self._ledger.list_runs(caller.root_frame_id, limit=limit)
            if r["owner_user_id"] == caller.owner_user_id
        ]

    def events(
        self, caller: LabCaller, *, after_seq: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        self._sweep(caller)
        rows = self._ledger.events_since(
            caller.root_frame_id, after_seq=after_seq, limit=limit
        )
        visible = []
        for row in rows:
            run = self._ledger.get_run(row["run_id"])
            if run is not None and run["owner_user_id"] == caller.owner_user_id:
                visible.append(row)
        return {
            "events": visible,
            "latest_event_seq": self._ledger.latest_event_seq(caller.root_frame_id),
        }

    def close_all(self, reason: str) -> None:
        with self._lock:
            self._closed = True
            runs = list(dict.fromkeys([*self._live, *self._opening]))
        first_error = None
        for run_id in runs:
            try:
                self._finish(run_id, "provider_lost")
            except LabError as exc:
                # Keep nonterminal rows for startup reconciliation if the
                # ledger is unavailable, but still release every provider.
                with self._lock:
                    live = self._live.get(run_id)
                    if live is not None:
                        live.needs_restart = True
                if live is not None:
                    self._close(live)
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def on_session_deleted(self, root_frame_id: str) -> None:
        with self._lock:
            self._discarded.update(
                r for r, root in self._opening.items() if root == root_frame_id
            )
            removed = []
            for run_id, live in list(self._live.items()):
                run = self._ledger.get_run(run_id)
                if run is None or run["root_frame_id"] == root_frame_id:
                    live.stopping = True
                    removed.append(self._live.pop(run_id))
        for live in removed:
            self._close(live)
