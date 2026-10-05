"""The single vocabulary for openai4s.lab/v1-draft (stdlib, Python 3.10).

Unknown outcomes remain reconcilable, never successful by inference. Device
payloads have no ledger identity until the host persists them. Evaluation is
private: serializers are persistence/wire serializers, NOT public projections.
Only manifest.project_* may publish descriptions and observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import types
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints

CONTRACT = "openai4s.lab/v1-draft"
UNITS = frozenset({"mL", "L", "layer_px", "model_time", "dimensionless"})
OPERATIONS = frozenset(
    {"transfer_liquid", "drain_layers", "mix_model", "settle_model", "end_experiment"}
)


class RunMode(str, Enum):
    SIMULATION = "simulation"


class RunStatus(str, Enum):
    CREATING = "creating"
    READY = "ready"
    BUSY = "busy"
    QUARANTINED = "quarantined"
    ENDED = "ended"
    FAILED = "failed"


class EndReason(str, Enum):
    END_ACTION = "end_action"
    MAX_STEPS = "max_steps"
    ENV_TERMINATED = "env_terminated"
    STOPPED = "stopped"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_LOST = "provider_lost"
    IDLE_TIMEOUT = "idle_timeout"
    CREATE_FAILED = "create_failed"
    DELETED = "deleted"


class CommandState(str, Enum):
    CREATED = "created"
    AWAITING_APPROVAL = "awaiting_approval"
    ADMITTED = "admitted"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    NOT_DISPATCHED = "not_dispatched"
    OUTCOME_UNKNOWN = "outcome_unknown"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"


class CommandOrigin(str, Enum):
    AGENT_TOOL = "agent_tool"
    HOST_SDK = "host_sdk"
    MANUAL_UI = "manual_ui"
    SYSTEM = "system"


class CapabilityScope(str, Enum):
    SHARED = "shared"
    SIMULATION_ONLY = "simulation_only"


class SideEffect(str, Enum):
    MOVES_MATERIAL = "moves_material"
    CHANGES_STATE = "changes_state"
    ENDS_RUN = "ends_run"


class ChannelKind(str, Enum):
    ARRAY = "array"
    SCALAR = "scalar"
    CATEGORY = "category"


class ChannelSource(str, Enum):
    SIMULATED_SENSOR = "simulated_sensor"
    PHYSICAL_SENSOR = "physical_sensor"


class ChannelQuality(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ReproducibilityStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED_FOR_PROFILE = "verified_for_profile"
    NOT_REPRODUCIBLE = "not_reproducible"


class ErrorCode(str, Enum):
    INVALID_PARAMETERS = "invalid_parameters"
    UNSUPPORTED_ACTION = "unsupported_action"
    UNIT_MISMATCH = "unit_mismatch"
    PRECONDITION_FAILED = "precondition_failed"
    STALE_REVISION = "stale_revision"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RUN_NOT_FOUND = "run_not_found"
    DEVICE_NOT_FOUND = "device_not_found"
    RUN_ENDED = "run_ended"
    RESOURCE_BUSY = "resource_busy"
    RESOURCE_QUARANTINED = "resource_quarantined"
    BUDGET_EXHAUSTED = "budget_exhausted"
    APPROVAL_DENIED = "approval_denied"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_PROTOCOL_ERROR = "provider_protocol_error"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ADAPTER_MISMATCH = "adapter_mismatch"
    REPLAY_FORBIDDEN = "replay_forbidden"
    MODE_MISMATCH = "mode_mismatch"


RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        RunStatus.CREATING: frozenset({RunStatus.READY, RunStatus.FAILED}),
        RunStatus.READY: frozenset({RunStatus.BUSY, RunStatus.QUARANTINED}),
        RunStatus.BUSY: frozenset(
            {RunStatus.READY, RunStatus.ENDED, RunStatus.QUARANTINED}
        ),
        RunStatus.QUARANTINED: frozenset({RunStatus.ENDED}),
        RunStatus.ENDED: frozenset(),
        RunStatus.FAILED: frozenset(),
    }
)
COMMAND_TRANSITIONS: Mapping[CommandState, frozenset[CommandState]] = MappingProxyType(
    {
        CommandState.CREATED: frozenset(
            {
                CommandState.AWAITING_APPROVAL,
                CommandState.ADMITTED,
                CommandState.REJECTED,
                CommandState.NOT_DISPATCHED,
            }
        ),
        CommandState.AWAITING_APPROVAL: frozenset(
            {CommandState.ADMITTED, CommandState.REJECTED, CommandState.NOT_DISPATCHED}
        ),
        CommandState.ADMITTED: frozenset(
            {
                CommandState.DISPATCHING,
                CommandState.REJECTED,
                CommandState.NOT_DISPATCHED,
            }
        ),
        CommandState.DISPATCHING: frozenset(
            {
                CommandState.RUNNING,
                CommandState.SUCCEEDED,
                CommandState.FAILED,
                CommandState.OUTCOME_UNKNOWN,
                CommandState.STOP_REQUESTED,
            }
        ),
        CommandState.RUNNING: frozenset(
            {
                CommandState.SUCCEEDED,
                CommandState.FAILED,
                CommandState.OUTCOME_UNKNOWN,
                CommandState.STOP_REQUESTED,
            }
        ),
        CommandState.STOP_REQUESTED: frozenset(
            {
                CommandState.STOPPED,
                CommandState.SUCCEEDED,
                CommandState.FAILED,
                CommandState.OUTCOME_UNKNOWN,
            }
        ),
        CommandState.OUTCOME_UNKNOWN: frozenset(
            {CommandState.SUCCEEDED, CommandState.FAILED}
        ),
        CommandState.SUCCEEDED: frozenset(),
        CommandState.FAILED: frozenset(),
        CommandState.REJECTED: frozenset(),
        CommandState.NOT_DISPATCHED: frozenset(),
        CommandState.STOPPED: frozenset(),
    }
)
TERMINAL_RUN_STATUSES = frozenset({RunStatus.ENDED, RunStatus.FAILED})
TERMINAL_COMMAND_STATES = frozenset(
    {
        CommandState.SUCCEEDED,
        CommandState.FAILED,
        CommandState.REJECTED,
        CommandState.NOT_DISPATCHED,
        CommandState.STOPPED,
    }
)


def can_transition_run(a: RunStatus, b: RunStatus) -> bool:
    return b in RUN_TRANSITIONS[a]


def can_transition_command(a: CommandState, b: CommandState) -> bool:
    return b in COMMAND_TRANSITIONS[a]


def command_sources_for(target: CommandState) -> frozenset[CommandState]:
    return frozenset(
        source for source, targets in COMMAND_TRANSITIONS.items() if target in targets
    )


class LabError(Exception):
    """A stable code and a safe human explanation, never simulator truth."""

    def __init__(
        self, code: ErrorCode, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{code.value}: {message}")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.details is not None:
            result["details"] = _json_value(self.details, "details")
        return result


def _invalid(path: str, reason: str) -> None:
    # Never interpolate a supplied value: validation can touch evaluation data.
    raise LabError(ErrorCode.INVALID_PARAMETERS, f"{path}: {reason}")


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(path, "expected a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _invalid(path, "expected a finite number")
    # Preserve the advertised JSON number: 200 and 200.0 hash differently.
    return value


def _json_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        _number(value, path)
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _invalid(path, "expected string keys")
            result[key] = _json_value(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{path}[{i}]") for i, item in enumerate(value)]
    _invalid(path, "expected JSON data")


V = TypeVar("V", bound="_Value")


def _decode(value: Any, annotation: Any, path: str) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Any:
        return _json_value(value, path)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        for member in args:
            if member is type(None):
                continue
            try:
                return _decode(value, member, path)
            except LabError:
                pass
        _invalid(path, "invalid nullable value")
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            _invalid(path, "expected an array")
        return tuple(
            _decode(item, args[0], f"{path}[{i}]") for i, item in enumerate(value)
        )
    if origin is Mapping:
        if not isinstance(value, Mapping):
            _invalid(path, "expected an object")
        return {
            _decode(key, args[0], path): _decode(item, args[1], f"{path}.{key}")
            for key, item in value.items()
        }
    if annotation is float:
        return _number(value, path)
    if annotation is int:
        if type(value) is not int:
            _invalid(path, "expected an integer")
        _number(value, path)
        return value
    if annotation in (str, bool):
        if not isinstance(value, annotation):
            _invalid(path, f"expected {annotation.__name__}")
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not isinstance(value, str):
            _invalid(path, "expected an enum string")
        try:
            return annotation(value)
        except ValueError:
            _invalid(path, "unsupported enum value")
    if isinstance(annotation, type) and issubclass(annotation, _Value):
        if isinstance(value, annotation):
            value = value.to_dict()
        try:
            return annotation.from_dict(value)
        except LabError as exc:
            _invalid(path, exc.message)
    _invalid(path, "unsupported field type")


def _encode(value: Any) -> Any:
    if isinstance(value, _Value):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


class _Value:
    """Shared closed-schema codec; subclass annotations are the field schema."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls: type[V], data: Mapping[str, Any]) -> V:
        if not isinstance(data, Mapping):
            _invalid(cls.__name__, "expected an object")
        schema = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
        for key in data:
            if key not in schema:
                _invalid(f"{cls.__name__}.{key}", "unknown field")
        annotations = get_type_hints(cls)
        values: dict[str, Any] = {}
        for name, f in schema.items():
            if name not in data:
                if f.default is MISSING and f.default_factory is MISSING:
                    _invalid(f"{cls.__name__}.{name}", "required field")
                continue
            values[name] = _decode(
                data[name], annotations[name], f"{cls.__name__}.{name}"
            )
        result = cls(**values)
        result._validate()
        return result

    def _validate(self) -> None:
        pass


def _unit(value: str, path: str) -> None:
    if value not in UNITS:
        _invalid(path, "unsupported unit")


def _nonnegative(value: float, path: str) -> None:
    if value < 0:
        _invalid(path, "must be nonnegative")


def _shape(kind: ChannelKind, shape: tuple[int, ...], path: str) -> None:
    if any(size <= 0 for size in shape) or (kind is ChannelKind.ARRAY) != bool(shape):
        _invalid(
            path, "array needs positive dimensions; scalar/category need an empty shape"
        )


def _object(
    data: Mapping[str, Any],
    required: Mapping[str, Any],
    path: str,
    optional: Mapping[str, Any] | None = None,
) -> None:
    options = optional or {}
    for key in data:
        if key not in required and key not in options:
            _invalid(f"{path}.{key}", "unknown field")
    for key, annotation in required.items():
        if key not in data:
            _invalid(f"{path}.{key}", "required field")
        _decode(data[key], annotation, f"{path}.{key}")
    for key, annotation in options.items():
        if key in data:
            _decode(data[key], annotation, f"{path}.{key}")


@dataclass(frozen=True)
class Quantity(_Value):
    value: float
    unit: str

    def _validate(self) -> None:
        _unit(self.unit, "Quantity.unit")


@dataclass(frozen=True)
class ParameterSpec(_Value):
    unit: str
    allowed: tuple[float, ...]

    def _validate(self) -> None:
        _unit(self.unit, "ParameterSpec.unit")
        if not self.allowed or any(
            a >= b for a, b in zip(self.allowed, self.allowed[1:])
        ):
            _invalid(
                "ParameterSpec.allowed", "must be nonempty and strictly increasing"
            )


@dataclass(frozen=True)
class Capability(_Value):
    capability_id: str
    operation: str
    scope: CapabilityScope
    source: str | None
    target: str | None
    parameters: Mapping[str, ParameterSpec]
    side_effect: SideEffect
    resources: tuple[str, ...]
    observes: tuple[str, ...]
    terminal: bool
    mapping_version: str

    def _validate(self) -> None:
        if self.operation not in OPERATIONS:
            _invalid("Capability.operation", "unsupported operation")
        if not self.capability_id:
            _invalid("Capability.capability_id", "must not be empty")
        if tuple(sorted(set(self.resources))) != self.resources:
            _invalid("Capability.resources", "must be unique and sorted")


@dataclass(frozen=True)
class ResourceSpec(_Value):
    resource_id: str
    kind: str
    label: str


@dataclass(frozen=True)
class ObservationChannelSpec(_Value):
    name: str
    kind: ChannelKind
    shape: tuple[int, ...]
    unit: str
    source: ChannelSource
    available: bool
    description: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if self.reason is None:
            del result["reason"]
        return result

    def _validate(self) -> None:
        _unit(self.unit, "ObservationChannelSpec.unit")
        _shape(self.kind, self.shape, "ObservationChannelSpec.shape")
        if not self.available and not self.reason:
            _invalid("ObservationChannelSpec.reason", "required when unavailable")


@dataclass(frozen=True)
class DeviceDescriptor(_Value):
    contract: str
    device_id: str
    mode: RunMode
    backend: str
    profile: str
    backend_version: Mapping[str, Any]
    resources: tuple[ResourceSpec, ...]
    capabilities: tuple[Capability, ...]
    capability_revision: str
    observation_channels: tuple[ObservationChannelSpec, ...]
    limits: Mapping[str, Any]
    stop: Mapping[str, Any]
    time: Mapping[str, Any]
    reproducibility: Mapping[str, Any]
    assumptions: tuple[str, ...]

    def _validate(self) -> None:
        if self.contract != CONTRACT:
            _invalid("DeviceDescriptor.contract", "unsupported contract")
        _object(
            self.backend_version,
            {"package": str, "version": str, "source_sha": str, "adapter_version": str},
            "backend_version",
        )
        _object(self.limits, {"max_steps": int}, "limits")
        if self.limits["max_steps"] <= 0:
            _invalid("limits.max_steps", "must be positive")
        _object(self.stop, {"supported": bool, "semantics": str}, "stop")
        _object(self.time, {"unit": str, "wall_clock_equivalent": float | None}, "time")
        if (
            self.time["unit"] != "model_time"
            or self.time["wall_clock_equivalent"] is not None
        ):
            _invalid("time", "model_time has no wall clock conversion")
        _object(
            self.reproducibility,
            {"status": ReproducibilityStatus, "evidence": Any},
            "reproducibility",
        )


@dataclass(frozen=True)
class CommandRequest(_Value):
    run_id: str
    operation: str
    source: str | None
    target: str | None
    parameters: Mapping[str, Quantity]
    expected_revision: int
    idempotency_key: str

    def _validate(self) -> None:
        _nonnegative(self.expected_revision, "CommandRequest.expected_revision")
        if not 1 <= len(self.idempotency_key) <= 128 or any(
            not 32 <= ord(c) <= 126 for c in self.idempotency_key
        ):
            _invalid(
                "CommandRequest.idempotency_key",
                "expected 1-128 printable ASCII characters",
            )


@dataclass(frozen=True)
class NormalizedCommand(CommandRequest):
    capability_id: str


@dataclass(frozen=True)
class Dispatch(_Value):
    provider_command_id: str
    command: NormalizedCommand
    fencing_tokens: Mapping[str, int]

    def _validate(self) -> None:
        for resource, token in self.fencing_tokens.items():
            _nonnegative(token, f"Dispatch.fencing_tokens.{resource}")


@dataclass(frozen=True)
class ObservationChannel(_Value):
    name: str
    kind: ChannelKind
    unit: str
    shape: tuple[int, ...]
    value: Any
    quality: ChannelQuality
    source: ChannelSource

    def _validate(self) -> None:
        _unit(self.unit, "ObservationChannel.unit")
        _shape(self.kind, self.shape, "ObservationChannel.shape")
        if self.quality is not ChannelQuality.OK:
            if self.value is not None:
                _invalid(
                    "ObservationChannel.value", "must be null unless quality is ok"
                )
            return
        if self.kind is ChannelKind.ARRAY:
            _array(self.value, self.shape, "ObservationChannel.value")
        elif self.kind is ChannelKind.SCALAR:
            _number(self.value, "ObservationChannel.value")
        elif not isinstance(self.value, (str, int)) or isinstance(self.value, bool):
            _invalid(
                "ObservationChannel.value", "expected a category string or integer"
            )


def _array(value: Any, shape: tuple[int, ...], path: str) -> None:
    if not shape:
        _number(value, path)
    elif not isinstance(value, (list, tuple)) or len(value) != shape[0]:
        _invalid(path, "does not match shape")
    else:
        for index, item in enumerate(value):
            _array(item, shape[1:], f"{path}[{index}]")


def _observation_payload(data: Mapping[str, Any], path: str) -> None:
    _object(data, {"channels": tuple[ObservationChannel, ...], "sim_time": float}, path)
    _nonnegative(data["sim_time"], f"{path}.sim_time")


def _evaluation_payload(data: Mapping[str, Any], path: str) -> None:
    _object(
        data,
        {"reward": float, "ground_truth": Mapping[str, Any]},
        path,
        {"metrics": Mapping[str, Any]},
    )


@dataclass(frozen=True)
class Receipt(_Value):
    provider_command_id: str
    applied: bool
    status: CommandState
    error: Mapping[str, Any] | None
    raw: Mapping[str, Any]
    end_reason: EndReason | None
    sim_time: float
    step_index: int
    observation: Mapping[str, Any] | None
    evaluation: Mapping[str, Any] | None

    def _validate(self) -> None:
        if self.status not in (
            CommandState.SUCCEEDED,
            CommandState.FAILED,
            CommandState.REJECTED,
        ):
            _invalid("Receipt.status", "expected succeeded, failed or rejected")
        if self.status is not CommandState.SUCCEEDED and self.applied:
            _invalid("Receipt.applied", "failed/rejected cannot apply")
        if self.end_reason not in (
            None,
            EndReason.END_ACTION,
            EndReason.MAX_STEPS,
            EndReason.ENV_TERMINATED,
        ):
            _invalid("Receipt.end_reason", "unsupported provider end reason")
        _object(self.raw, {"terminated": bool, "truncated": bool}, "Receipt.raw")
        if self.error is not None:
            _object(
                self.error,
                {"code": ErrorCode, "message": str},
                "Receipt.error",
                {"details": Mapping[str, Any]},
            )
        _nonnegative(self.sim_time, "Receipt.sim_time")
        _nonnegative(self.step_index, "Receipt.step_index")
        if self.observation is not None:
            _observation_payload(self.observation, "Receipt.observation")
        if self.evaluation is not None:
            _evaluation_payload(self.evaluation, "Receipt.evaluation")


@dataclass(frozen=True)
class Observation(_Value):
    observation_id: str
    run_id: str
    command_id: str | None
    sequence: int
    sim_time: float
    sim_time_unit: str
    wall_time_ms: int
    channels: tuple[ObservationChannel, ...]
    artifact_version_id: str | None

    def _validate(self) -> None:
        for name in ("sequence", "sim_time", "wall_time_ms"):
            _nonnegative(getattr(self, name), f"Observation.{name}")
        if self.sim_time_unit != "model_time":
            _invalid("Observation.sim_time_unit", "expected model_time")
        if len({c.name for c in self.channels}) != len(self.channels):
            _invalid("Observation.channels", "duplicate channel name")


@dataclass(frozen=True)
class Evaluation(_Value):
    evaluation_id: str
    run_id: str
    command_id: str | None
    sequence: int
    reward: float
    ground_truth: Mapping[str, Any]
    metrics: Mapping[str, Any]

    def _validate(self) -> None:
        _nonnegative(self.sequence, "Evaluation.sequence")


@dataclass(frozen=True)
class SessionOpenRequest(_Value):
    profile: str
    seed: int | None
    options: Mapping[str, Any]
    expected_capability_revision: str


@dataclass(frozen=True)
class SessionOpened(_Value):
    session_id: str
    descriptor: DeviceDescriptor
    observation: Mapping[str, Any]
    evaluation: Mapping[str, Any]

    def _validate(self) -> None:
        _observation_payload(self.observation, "SessionOpened.observation")
        _evaluation_payload(self.evaluation, "SessionOpened.evaluation")


@dataclass(frozen=True)
class StopResult(_Value):
    stopped: bool
    semantics: str


@dataclass(frozen=True)
class Budgets(_Value):
    # A caller selecting another profile supplies that profile's max_steps.
    max_steps: int = 50
    max_commands: int = 200
    max_wall_ms: int = 30 * 60 * 1000
    max_consecutive_failures: int = 3
    idle_timeout_ms: int = 60 * 60 * 1000

    def _validate(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) <= 0:
                _invalid(f"Budgets.{f.name}", "must be positive")


@dataclass(frozen=True)
class LabCaller(_Value):
    root_frame_id: str
    frame_id: str
    owner_user_id: str | None
    origin: CommandOrigin
    approval_ref: str | None
    execution_owner: str | None

    def _validate(self) -> None:
        if self.execution_owner not in (
            None,
            "agent",
            "user_repl",
            "lifecycle",
            "recovery",
        ):
            _invalid("LabCaller.execution_owner", "unsupported execution owner")


# Ledger column vocabulary (§3.10/3.11). Ports exchange decoded JSON columns:
# strip _json for descriptor/config/budgets/request/resources/provider_action/
# receipt/channels/ground_truth/metrics. Persistence itself belongs to storage.
LAB_RUN_FIELDS = (
    "run_id",
    "root_frame_id",
    "frame_id",
    "owner_user_id",
    "mode",
    "backend",
    "device_id",
    "profile",
    "adapter_version",
    "backend_source_sha",
    "capability_revision",
    "descriptor_json",
    "config_json",
    "config_hash",
    "seed",
    "status",
    "revision",
    "step_count",
    "end_reason",
    "raw_terminated",
    "raw_truncated",
    "budgets_json",
    "consecutive_failures",
    "parent_run_id",
    "daemon_instance",
    "create_idempotency_key",
    "created_at",
    "updated_at",
    "ended_at",
)
LAB_COMMAND_FIELDS = (
    "command_id",
    "run_id",
    "seq",
    "idempotency_key",
    "request_hash",
    "operation",
    "capability_id",
    "request_json",
    "expected_revision",
    "applied_revision",
    "origin",
    "actor_frame_id",
    "owner_user_id",
    "approval_ref",
    "resources_json",
    "fencing_token",
    "state",
    "error_code",
    "error",
    "provider_action_json",
    "receipt_json",
    "observation_id",
    "created_at",
    "updated_at",
    "dispatched_at",
    "completed_at",
)


def canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def capability_revision(capabilities: Sequence[Capability]) -> str:
    return sha256_hex(
        canonical_json(
            [c.to_dict() for c in sorted(capabilities, key=lambda c: c.capability_id)]
        )
    )


def request_hash(normalized: NormalizedCommand) -> str:
    data = normalized.to_dict()
    del data["idempotency_key"]
    del data["expected_revision"]
    return sha256_hex(canonical_json(data))


def config_hash(
    device_id: str,
    profile: str,
    seed: int | None,
    budgets: Budgets,
    options: Mapping[str, Any],
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "device_id": device_id,
                "profile": profile,
                "seed": seed,
                "budgets": budgets.to_dict(),
                "options": dict(options),
            }
        )
    )


def new_id(prefix: str) -> str:
    if prefix not in ("labrun", "labcmd", "labobs", "labeval"):
        raise ValueError("unsupported Lab ID prefix")
    return f"{prefix}-{secrets.token_hex(6)}"
