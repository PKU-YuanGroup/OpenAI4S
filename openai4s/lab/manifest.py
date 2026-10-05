"""Validate advertised capabilities and publish only sensor-side information.

Discrete values are exact advertised settings after L/mL conversion. There is
no rounding or invented seconds/pressure. Projection constructs allowlisted
objects independently of persistence serializers, which may carry private data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from openai4s.lab.models import (
    CONTRACT,
    ChannelKind,
    ChannelQuality,
    ChannelSource,
    CommandRequest,
    DeviceDescriptor,
    ErrorCode,
    LabError,
    NormalizedCommand,
    Observation,
    ObservationChannel,
    ParameterSpec,
    Quantity,
    capability_revision,
)


def load_descriptor(data: Mapping[str, Any]) -> DeviceDescriptor:
    if not isinstance(data, Mapping):
        raise LabError(ErrorCode.INVALID_PARAMETERS, "descriptor: expected an object")
    # Revision is optional at the loading boundary only, not on the value type.
    values = dict(data)
    values.setdefault("capability_revision", "")
    descriptor = DeviceDescriptor.from_dict(values)
    resources = {r.resource_id for r in descriptor.resources}
    if len(resources) != len(descriptor.resources):
        raise LabError(ErrorCode.INVALID_PARAMETERS, "resources: duplicate resource_id")
    channels = {c.name for c in descriptor.observation_channels}
    if len(channels) != len(descriptor.observation_channels):
        raise LabError(
            ErrorCode.INVALID_PARAMETERS, "observation_channels: duplicate name"
        )
    identities: set[str] = set()
    actions: set[tuple[str, str | None, str | None]] = set()
    for cap in descriptor.capabilities:
        if cap.capability_id in identities:
            raise LabError(
                ErrorCode.INVALID_PARAMETERS, "capabilities: duplicate capability_id"
            )
        identities.add(cap.capability_id)
        action = (cap.operation, cap.source, cap.target)
        if action in actions:
            raise LabError(
                ErrorCode.INVALID_PARAMETERS,
                "capabilities: ambiguous operation/source/target",
            )
        actions.add(action)
        referenced = set(cap.resources) | {
            s for s in (cap.source, cap.target) if s is not None
        }
        if not referenced <= resources:
            raise LabError(
                ErrorCode.INVALID_PARAMETERS, "capabilities.resources: unknown resource"
            )
        if not set(cap.observes) <= channels:
            raise LabError(
                ErrorCode.INVALID_PARAMETERS, "capabilities.observes: unknown channel"
            )
    revision = capability_revision(descriptor.capabilities)
    if "capability_revision" in data and descriptor.capability_revision != revision:
        raise LabError(
            ErrorCode.ADAPTER_MISMATCH,
            "capability_revision: descriptor differs from its capabilities",
        )
    values["capability_revision"] = revision
    return DeviceDescriptor.from_dict(values)


def normalize_quantity(q: Quantity, spec: ParameterSpec) -> float:
    q = Quantity.from_dict(q.to_dict())
    spec = ParameterSpec.from_dict(spec.to_dict())
    value = q.value
    if q.unit != spec.unit:
        if q.unit == "L" and spec.unit == "mL":
            value *= 1000
        elif q.unit == "mL" and spec.unit == "L":
            value /= 1000
        else:
            raise LabError(
                ErrorCode.UNIT_MISMATCH, "parameter.unit: incompatible units"
            )
    for allowed in spec.allowed:
        if math.isclose(value, allowed, rel_tol=1e-9, abs_tol=1e-9):
            return allowed
    raise LabError(
        ErrorCode.UNSUPPORTED_ACTION,
        "parameter.value: must match an allowed setting",
        {"parameter": "value", "allowed": list(spec.allowed), "unit": spec.unit},
    )


def match_command(
    descriptor: DeviceDescriptor, request: CommandRequest
) -> NormalizedCommand:
    # Contract §5 ordering matters: capability identity precedes parameter checks.
    capability = next(
        (
            cap
            for cap in descriptor.capabilities
            if (cap.operation, cap.source, cap.target)
            == (request.operation, request.source, request.target)
        ),
        None,
    )
    if capability is None:
        raise LabError(
            ErrorCode.UNSUPPORTED_ACTION, "operation/source/target: unsupported action"
        )
    if set(request.parameters) != set(capability.parameters):
        raise LabError(
            ErrorCode.INVALID_PARAMETERS, "parameters: names must match capability"
        )
    request = CommandRequest.from_dict(request.to_dict())
    parameters: dict[str, Quantity] = {}
    for name, spec in capability.parameters.items():
        try:
            value = normalize_quantity(request.parameters[name], spec)
        except LabError as exc:
            if exc.code is ErrorCode.UNSUPPORTED_ACTION:
                raise LabError(
                    exc.code,
                    f"parameters.{name}: must match an allowed setting",
                    {
                        "parameter": name,
                        "allowed": list(spec.allowed),
                        "unit": spec.unit,
                    },
                ) from None
            raise
        parameters[name] = Quantity(value, spec.unit)
    return NormalizedCommand(
        run_id=request.run_id,
        operation=request.operation,
        source=request.source,
        target=request.target,
        parameters=parameters,
        expected_revision=request.expected_revision,
        idempotency_key=request.idempotency_key,
        capability_id=capability.capability_id,
    )


_PRIVATE_KEYS = frozenset(
    {
        "evaluation",
        "reward",
        "ground_truth",
        "provider_action",
        "provider_action_json",
        "action_index",
    }
)


def _public(value: Any) -> Any:
    """Defense in depth for open metadata maps, including nested future fields."""
    if isinstance(value, Mapping):
        return {
            key: _public(item)
            for key, item in value.items()
            if key not in _PRIVATE_KEYS
        }
    if isinstance(value, (tuple, list)):
        return [_public(item) for item in value]
    return value


def project_descriptor(descriptor: DeviceDescriptor) -> dict[str, Any]:
    # Explicit fields prevent a future to_dict addition from becoming public.
    # Re-validate nested closed schemas; private additions are removed first.
    data = _public(descriptor.to_dict())
    keys = (
        "contract",
        "device_id",
        "mode",
        "backend",
        "profile",
        "backend_version",
        "resources",
        "capabilities",
        "capability_revision",
        "observation_channels",
        "limits",
        "stop",
        "time",
        "reproducibility",
        "assumptions",
    )
    selected = {key: data[key] for key in keys}
    DeviceDescriptor.from_dict(selected)
    return selected


def _flatten(value: Any) -> list[float]:
    if isinstance(value, (tuple, list)):
        return [number for item in value for number in _flatten(item)]
    return [float(value)]


def project_observation(
    obs: Observation, *, full: bool = False, max_elements: int = 256
) -> dict[str, Any]:
    if type(max_elements) is not int or max_elements < 1:
        raise LabError(
            ErrorCode.INVALID_PARAMETERS, "max_elements: expected a positive integer"
        )
    channels: list[dict[str, Any]] = []
    for c in obs.channels:
        if c.source != ChannelSource.SIMULATED_SENSOR:
            continue
        # Explicit fields also protect against a future private serializer field.
        channel = {
            "name": c.name,
            "kind": c.kind.value,
            "unit": c.unit,
            "shape": list(c.shape),
            "value": c.value if c.quality is ChannelQuality.OK else None,
            "quality": c.quality.value,
            "source": c.source.value,
        }
        channel = ObservationChannel.from_dict(channel).to_dict()
        if c.kind is ChannelKind.ARRAY and c.quality is ChannelQuality.OK and not full:
            numbers = _flatten(channel["value"])
            if len(numbers) > max_elements:
                # Dividing first avoids overflowing a sum of finite large values.
                channel["value"] = {
                    "shape": list(c.shape),
                    "summary": {
                        "min": min(numbers),
                        "max": max(numbers),
                        "mean": math.fsum(n / len(numbers) for n in numbers),
                    },
                    "truncated": True,
                }
        channels.append(channel)
    return {
        "observation_id": obs.observation_id,
        "run_id": obs.run_id,
        "command_id": obs.command_id,
        "sequence": obs.sequence,
        "sim_time": obs.sim_time,
        "sim_time_unit": obs.sim_time_unit,
        "wall_time_ms": obs.wall_time_ms,
        "channels": channels,
        "artifact_version_id": obs.artifact_version_id,
    }
