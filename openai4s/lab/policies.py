"""Sensor-only baselines crossing the same manager boundary as other callers.

The direct DevicePort adapter belongs in tests/development tooling, never here.
A policy chooses advertised commands; physical feasibility remains a provider
judgment. Failed preconditions therefore do not make a sampled command illegal.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol
from uuid import uuid4

from openai4s.lab.manifest import (
    observation_from_row,
    project_command,
    project_descriptor,
    project_observation,
    project_run,
)
from openai4s.lab.models import (
    CommandRequest,
    DeviceDescriptor,
    ErrorCode,
    LabCaller,
    LabError,
)
from openai4s.lab.ports import LabManagerPort


class PolicyEnv(Protocol):
    """Public observations plus a run/descriptor status and command envelopes.

    ``status`` returns ``{run, descriptor, command}``; ``execute`` returns
    ``{run, command, observation}``. Observations use the full sensor projection
    (as with ``host.lab.observe(full=True)``), never provider evaluation payloads.
    """

    def observe(self) -> dict[str, Any]: ...
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...


class Policy(Protocol):
    def select_action(
        self,
        descriptor: Mapping[str, Any],
        observation: Mapping[str, Any],
        *,
        step: int,
    ) -> dict[str, Any] | None: ...


def _run_view(row: Mapping[str, Any]) -> dict[str, Any]:
    # A manager may already have applied project_run. Preserve its raw flags
    # when applying the same allowlist again at this public boundary.
    prepared = dict(row)
    raw = row.get("raw")
    if isinstance(raw, Mapping):
        prepared.setdefault("raw_terminated", raw.get("terminated"))
        prepared.setdefault("raw_truncated", raw.get("truncated"))
    return deepcopy(project_run(prepared))


def _observation_view(
    row: Mapping[str, Any], descriptor: DeviceDescriptor
) -> dict[str, Any]:
    return project_observation(
        observation_from_row(row), channels=descriptor.observation_channels, full=True
    )


def _result_view(
    result: Mapping[str, Any], descriptor: DeviceDescriptor
) -> dict[str, Any]:
    command, observation = result.get("command"), result.get("observation")
    return {
        "run": _run_view(result["run"]),
        "command": deepcopy(project_command(command)) if command is not None else None,
        "observation": (
            _observation_view(observation, descriptor)
            if observation is not None
            else None
        ),
    }


class ManagerPolicyEnv:
    """Adapt an already-created run; all I/O goes through LabManagerPort.

    ``descriptor`` is that run's pinned descriptor, not a newly probed device.
    The manager continues to own admission, budgets, leases and reconciliation.
    """

    def __init__(
        self,
        manager: LabManagerPort,
        caller: LabCaller,
        run_id: str,
        *,
        descriptor: DeviceDescriptor,
    ) -> None:
        self._manager = manager
        self._caller = caller
        self._run_id = run_id
        self._descriptor = DeviceDescriptor.from_dict(project_descriptor(descriptor))

    def observe(self) -> dict[str, Any]:
        result = self._manager.observe(self._caller, self._run_id, full=True)
        return _observation_view(result.get("observation", result), self._descriptor)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        command = CommandRequest.from_dict(request)
        if command.run_id != self._run_id:
            raise LabError(ErrorCode.RUN_NOT_FOUND, "Policy run does not match")
        return _result_view(
            self._manager.execute(self._caller, command.to_dict()), self._descriptor
        )

    def status(self) -> dict[str, Any]:
        result = self._manager.status(self._caller, self._run_id)
        run = result.get("run", result)
        if (
            run.get("run_id") != self._run_id
            or run.get("capability_revision") != self._descriptor.capability_revision
            or run.get("device_id") != self._descriptor.device_id
            or run.get("profile") != self._descriptor.profile
        ):
            raise LabError(ErrorCode.ADAPTER_MISMATCH, "Policy run descriptor differs")
        command = result.get("command")
        return {
            "run": _run_view(run),
            "descriptor": project_descriptor(self._descriptor),
            "command": (
                deepcopy(project_command(command)) if command is not None else None
            ),
        }


def _action(capability: Mapping[str, Any], levels: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation": capability["operation"],
        "source": capability["source"],
        "target": capability["target"],
        "parameters": {
            name: {"value": levels[name], "unit": spec["unit"]}
            for name, spec in capability["parameters"].items()
        },
    }


class RandomValidPolicy:
    """Uniform over capabilities, then independently uniform over each level.

    No hidden-state feasibility filter is used. The provider can still refuse
    a valid action, e.g. a transfer from an empty vessel.
    """

    def __init__(self, seed: int | None) -> None:
        self._rng = random.Random(seed)

    def select_action(
        self,
        descriptor: Mapping[str, Any],
        observation: Mapping[str, Any],
        *,
        step: int,
    ) -> dict[str, Any] | None:
        capabilities = descriptor["capabilities"]
        if not capabilities:
            return None
        capability = self._rng.choice(capabilities)
        return _action(
            capability,
            {
                name: self._rng.choice(spec["allowed"])
                for name, spec in capability["parameters"].items()
            },
        )


class FixedRulePolicy:
    """A declared WaterOilExtract-v0 simulation baseline, not a lab recipe.

    It adds the smallest water aliquot, mixes, lets the simulated layers settle,
    and collects them in stages before ending. It does not assert that this
    sequence achieves the experimental goal; evaluation decides separately.
    ``GenWurtzExtract-v2`` reuses the sequence with its ether source as a
    compatibility baseline, with no claim of goal attainment. The smaller
    ``toy-extract-v0`` uses its declared water-source/settling endpoints.
    """

    def select_action(
        self,
        descriptor: Mapping[str, Any],
        observation: Mapping[str, Any],
        *,
        step: int,
    ) -> dict[str, Any] | None:
        profile = descriptor["profile"]
        if profile not in {
            "WaterOilExtract-v0",
            "GenWurtzExtract-v2",
            "toy-extract-v0",
        }:
            raise LabError(
                ErrorCode.UNSUPPORTED_ACTION, "Fixed policy profile is unsupported"
            )
        toy = profile == "toy-extract-v0"
        source = {
            "toy-extract-v0": "water_source",
            "WaterOilExtract-v0": "h2o_vessel",
            "GenWurtzExtract-v2": "diethyl_ether_vessel",
        }[profile]
        settle_source = "extraction_vessel" if toy else None
        # Indices select advertised discrete levels, never absolute quantities.
        transfer = ("transfer_liquid", source, "extraction_vessel", 0)
        mix = ("mix_model", "extraction_vessel", None, -1)
        settle = ("settle_model", settle_source, None, 0)
        drain = ("drain_layers", "extraction_vessel", "beaker_1", 0)
        rules = (
            transfer,  # Add the smallest declared virtual solvent aliquot.
            mix,  # Mix at the highest declared model-time level.
            settle,  # Allow the model to separate layers.
            drain,  # Start collection with the smallest declared drain.
            (*drain[:3], -2),  # Continue at the next-to-highest drain level.
            settle,
            (*mix[:3], -2),  # Redistribute remaining material in the model.
            settle,
            drain,
            (*drain[:3], -1),  # Collect a larger portion using a declared level.
            settle,
            drain,
            ("end_experiment", None, None, 0),  # Explicitly end the run.
        )
        if step >= len(rules):
            return None
        operation, source, target, ordinal = rules[step]
        matches = [
            cap
            for cap in descriptor["capabilities"]
            if (cap["operation"], cap["source"], cap["target"])
            == (operation, source, target)
        ]
        if not matches and toy:
            # The subprocess toy has only a transfer and end capability. Do
            # not invent the richer fake's operations; finish through its own
            # advertised terminal action when the planned operation is absent.
            matches = [
                cap
                for cap in descriptor["capabilities"]
                if (cap["operation"], cap["source"], cap["target"])
                == ("end_experiment", None, None)
            ]
        if len(matches) != 1:
            raise LabError(
                ErrorCode.UNSUPPORTED_ACTION, "Fixed policy capability is unavailable"
            )
        capability = matches[0]
        return _action(
            capability,
            {
                name: spec["allowed"][max(ordinal, -len(spec["allowed"]))]
                for name, spec in capability["parameters"].items()
            },
        )


def run_episode(policy: Policy, env: PolicyEnv, *, max_steps: int) -> dict[str, Any]:
    """Run at most max_steps command attempts, including refused commands.

    An unknown or in-flight result stops immediately. There is no automatic
    retry, reconciliation, device stop or success inference in this runner.
    Reusing an environment never reuses an earlier episode's idempotency keys.
    The returned trace is public; evaluation records remain in the ledger.
    """
    if type(max_steps) is not int or max_steps < 0:
        raise ValueError("max_steps must be a nonnegative integer")
    commands: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    run: dict[str, Any] | None = None
    stop_reason = "max_steps"
    error_code: str | None = None
    episode_id = uuid4().hex
    try:
        for step in range(max_steps):
            state = env.status()
            descriptor = DeviceDescriptor.from_dict(state["descriptor"])
            run = _run_view(state["run"])
            if run["status"] != "ready":
                stop_reason = str(run["status"] or "unknown")
                break
            last_command = state.get("command")
            if last_command and last_command.get("state") == "outcome_unknown":
                stop_reason = "outcome_unknown"
                break
            observation = _observation_view(env.observe(), descriptor)
            if (
                not observations
                or observation["observation_id"] != observations[-1]["observation_id"]
            ):
                observations.append(observation)
            action = policy.select_action(
                project_descriptor(descriptor), deepcopy(observation), step=step
            )
            if action is None:
                stop_reason = "policy_stopped"
                break
            request = CommandRequest.from_dict(
                {
                    **action,
                    "run_id": run["run_id"],
                    "expected_revision": run["revision"],
                    "idempotency_key": f"policy-{episode_id}-{step}",
                }
            )
            # Only the manager admits/normalizes requests in production.
            result = _result_view(env.execute(request.to_dict()), descriptor)
            run = result["run"]
            command = result["command"]
            if command is None:
                stop_reason = "outcome_unknown"
                break
            commands.append(command)
            observation = result["observation"]
            if observation is not None:
                observations.append(observation)
            if command["state"] == "outcome_unknown":
                stop_reason = "outcome_unknown"
                break
            if command["state"] not in {
                "succeeded",
                "failed",
                "rejected",
                "not_dispatched",
            }:
                stop_reason = "command_pending"
                break
            if run["status"] != "ready":
                stop_reason = str(run["status"] or "unknown")
                break
    except LabError as exc:
        # Never forward transport/provider messages or arbitrary error details.
        error_code = exc.code.value
        stop_reason = (
            "outcome_unknown"
            if exc.code in {ErrorCode.PROVIDER_TIMEOUT, ErrorCode.OUTCOME_UNKNOWN}
            else "error"
        )
    return {
        "policy": type(policy).__name__,
        "stop_reason": stop_reason,
        "error_code": error_code,
        "steps": len(commands),
        "run": run,
        "commands": commands,
        "observations": observations,
    }
