"""Deterministic, in-process testing device; no chemistry or hardware backend.

Transfers use ``volume`` in mL; drains use ``pixels`` in layer_px (100 mL per
toy pixel); mix/settle use ``duration`` in model_time, never seconds. All toy
liquid is ideally mixed, with proportional solute transfer. Layers are coarse
fill sensors for the extraction vessel and the two collection beakers. The
evaluation alone records exact volumes, fictional solute amounts and recovery.

A lock covers each complete port transaction, including the receipt cache.
Execute hooks are consumed by the next call, even a refusal, replay or error.
Transport hooks may hide a cached receipt, but never execute its command again.
Each session represents an independent provider process for fencing and cache
purposes. Fatal execute hooks kill only that session; ``crash`` kills every
session owned by this device instance.
"""

from __future__ import annotations

import random
import secrets
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from openai4s.lab.devices import DeviceRegistration
from openai4s.lab.manifest import load_descriptor, match_command
from openai4s.lab.models import (
    CONTRACT,
    RECEIPT_ERROR_CODES,
    Capability,
    CapabilityScope,
    ChannelKind,
    ChannelQuality,
    ChannelSource,
    CommandRequest,
    CommandState,
    DeviceDescriptor,
    Dispatch,
    EndReason,
    ErrorCode,
    LabError,
    ObservationChannel,
    ObservationChannelSpec,
    ParameterSpec,
    Receipt,
    ResourceSpec,
    RunMode,
    SessionOpened,
    SessionOpenRequest,
    SideEffect,
    StopResult,
    capability_revision,
)

_PROFILE = "toy-extract-v0"
_RESOURCES = (
    "beaker_1",
    "beaker_2",
    "extraction_vessel",
    "oil_source",
    "water_source",
)
_VESSELS = ("extraction_vessel", "beaker_1", "beaker_2")


def _descriptor(profile: str) -> DeviceDescriptor:
    if profile != _PROFILE:
        raise LabError(ErrorCode.DEVICE_NOT_FOUND, "Profile is not supported")
    capabilities = []
    for source in _RESOURCES:
        for target in _VESSELS:
            if source == target:
                continue
            capabilities.append(
                Capability(
                    f"transfer_liquid:{source}->{target}",
                    "transfer_liquid",
                    CapabilityScope.SHARED,
                    source,
                    target,
                    {"volume": ParameterSpec("mL", (200, 400, 600, 800, 1000))},
                    SideEffect.MOVES_MATERIAL,
                    tuple(sorted((source, target))),
                    ("layers",),
                    False,
                    "1",
                )
            )
    for target in ("beaker_1", "beaker_2"):
        capabilities.append(
            Capability(
                f"drain_layers:extraction_vessel->{target}",
                "drain_layers",
                CapabilityScope.SIMULATION_ONLY,
                "extraction_vessel",
                target,
                {"pixels": ParameterSpec("layer_px", (1, 2, 5, 10))},
                SideEffect.MOVES_MATERIAL,
                tuple(sorted(("extraction_vessel", target))),
                ("layers",),
                False,
                "1",
            )
        )
    for operation in ("mix_model", "settle_model"):
        capabilities.append(
            Capability(
                f"{operation}:extraction_vessel",
                operation,
                CapabilityScope.SIMULATION_ONLY,
                "extraction_vessel",
                None,
                {"duration": ParameterSpec("model_time", (1, 2, 5))},
                SideEffect.CHANGES_STATE,
                ("extraction_vessel",),
                ("layers",),
                False,
                "1",
            )
        )
    capabilities.append(
        Capability(
            "end_experiment",
            "end_experiment",
            CapabilityScope.SHARED,
            None,
            None,
            {},
            SideEffect.ENDS_RUN,
            _RESOURCES,
            ("layers", "targets"),
            True,
            "1",
        )
    )
    descriptor = DeviceDescriptor(
        contract=CONTRACT,
        device_id=FakeExtractorDevice.device_id,
        mode=RunMode.SIMULATION,
        backend=FakeExtractorDevice.backend,
        profile=profile,
        backend_version={
            "package": "openai4s.lab.fake",
            "version": "1",
            "source_sha": "unavailable",
            "adapter_version": "1",
        },
        resources=tuple(
            ResourceSpec(r, "vessel", r.replace("_", " ")) for r in _RESOURCES
        ),
        capabilities=tuple(capabilities),
        capability_revision=capability_revision(capabilities),
        observation_channels=(
            ObservationChannelSpec(
                "layers",
                ChannelKind.ARRAY,
                (2, 10),
                "dimensionless",
                ChannelSource.SIMULATED_SENSOR,
                True,
                "Coarse fill of the extraction vessel and collection beakers",
            ),
            ObservationChannelSpec(
                "targets",
                ChannelKind.CATEGORY,
                (),
                "dimensionless",
                ChannelSource.SIMULATED_SENSOR,
                True,
                "Requested collection vessel",
            ),
        ),
        limits={"max_steps": 50},
        stop={"supported": True, "semantics": "end_session"},
        time={"unit": "model_time", "wall_clock_equivalent": None},
        reproducibility={"status": "unverified", "evidence": None},
        assumptions=(
            "Toy liquids are ideally mixed; transfer carries proportional solute.",
            "One toy layer pixel transfers 100 mL; model time is not seconds.",
        ),
    )
    return load_descriptor(descriptor.to_dict())


@dataclass
class _Vessel:
    volume: float
    solute: float


@dataclass
class _Session:
    vessels: dict[str, _Vessel]
    initial_solute: float
    sim_time: float = 0.0
    step_index: int = 0
    ended: bool = False
    end_reason: EndReason | None = None
    receipts: OrderedDict[str, Receipt] = field(default_factory=OrderedDict)
    # Every id this session accepted, so an evicted receipt is never mistaken
    # for a command that was never received.
    seen: set[str] = field(default_factory=set)
    fencing_tokens: dict[str, int] = field(default_factory=dict)


class FakeExtractorDevice:
    device_id = "fake.extractor.01"
    backend = "fake"

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, _Session] = {}
        self._crashed = False
        self._failure: ErrorCode | None = None
        self._lose_response = False
        self._lose_request = False
        self._fail_query = False
        self._timeout_and_die = False
        self._protocol_error = False
        self._mismatch = False

    def _check_device(self) -> None:
        if self._crashed:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Fake provider is unavailable"
            )

    def _session(self, session_id: str) -> _Session:
        self._check_device()
        session = self._sessions.get(session_id)
        if session is None:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider session is unavailable"
            )
        return session

    def describe(self, profile: str) -> DeviceDescriptor:
        with self._lock:
            self._check_device()
            return _descriptor(profile)

    def open(self, request: SessionOpenRequest) -> SessionOpened:
        with self._lock:
            self._check_device()
            request = SessionOpenRequest.from_dict(request.to_dict())
            descriptor = _descriptor(request.profile)
            mismatch, self._mismatch = self._mismatch, False
            if (
                mismatch
                or request.expected_capability_revision
                != descriptor.capability_revision
            ):
                raise LabError(
                    ErrorCode.ADAPTER_MISMATCH, "Capability revision does not match"
                )
            if request.options:
                raise LabError(
                    ErrorCode.INVALID_PARAMETERS,
                    "options: fake profile accepts no options",
                )
            rng = random.Random(request.seed)
            solute = rng.uniform(0.5, 1.5)
            vessels = {name: _Vessel(0.0, 0.0) for name in _RESOURCES}
            vessels["extraction_vessel"] = _Vessel(400.0, solute)
            vessels["oil_source"] = _Vessel(1000.0, 0.0)
            vessels["water_source"] = _Vessel(1000.0, 0.0)
            session = _Session(vessels, solute)
            session_id = "fake-session-" + secrets.token_hex(6)
            self._sessions[session_id] = session
            return SessionOpened(
                session_id,
                descriptor,
                self._observation(session),
                self._evaluation(session),
            )

    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        with self._lock:
            # Take all execute hooks at entry, including when validation or
            # replay exits early. No hook can leak into a later command.
            failure, self._failure = self._failure, None
            lose_response, self._lose_response = self._lose_response, False
            lose_request, self._lose_request = self._lose_request, False
            timeout_and_die, self._timeout_and_die = self._timeout_and_die, False
            protocol_error, self._protocol_error = self._protocol_error, False
            session = self._session(session_id)
            # Protocol failure takes precedence over request loss. Both occur
            # before accepting an id, and neither can apply the command.
            if protocol_error:
                self._sessions.pop(session_id)
                raise LabError(
                    ErrorCode.PROVIDER_PROTOCOL_ERROR, "Provider response is invalid"
                )
            if lose_request:
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT, "Provider request was not received"
                )
            dispatch = Dispatch.from_dict(dispatch.to_dict())
            receipt = self._execute_received(session, dispatch, failure)
            if timeout_and_die:
                self._sessions.pop(session_id)
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT, "Provider timed out and exited"
                )
            if lose_response:
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT, "Provider response was not received"
                )
            return receipt

    def _execute_received(
        self, session: _Session, dispatch: Dispatch, failure: ErrorCode | None
    ) -> Receipt:
        cached = session.receipts.get(dispatch.provider_command_id)
        if cached is not None:
            return deepcopy(cached)
        if dispatch.provider_command_id in session.seen:
            raise LabError(
                ErrorCode.OUTCOME_UNKNOWN,
                "Receipt is no longer retained; the command will not run again",
            )
        session.seen.add(dispatch.provider_command_id)
        if session.ended:
            receipt = self._failure_receipt(session, dispatch, ErrorCode.RUN_ENDED)
        else:
            receipt = self._execute_new(session, dispatch, failure)
        session.receipts[dispatch.provider_command_id] = deepcopy(receipt)
        if len(session.receipts) > 256:
            session.receipts.popitem(last=False)
        return receipt

    def _execute_new(
        self, session: _Session, dispatch: Dispatch, failure: ErrorCode | None
    ) -> Receipt:
        descriptor = _descriptor(_PROFILE)
        try:
            data = dispatch.command.to_dict()
            data.pop("capability_id")
            normalized = match_command(descriptor, CommandRequest.from_dict(data))
            if normalized != dispatch.command:
                raise LabError(
                    ErrorCode.UNSUPPORTED_ACTION,
                    "Command is not the advertised normalized action",
                )
        except LabError as exc:
            return self._failure_receipt(session, dispatch, exc.code, rejected=True)
        capability = next(
            c
            for c in descriptor.capabilities
            if c.capability_id == normalized.capability_id
        )
        if set(dispatch.fencing_tokens) != set(capability.resources):
            return self._failure_receipt(
                session, dispatch, ErrorCode.INVALID_PARAMETERS, rejected=True
            )
        stale = any(
            token < session.fencing_tokens.get(resource, 0)
            for resource, token in dispatch.fencing_tokens.items()
        )
        for resource, token in dispatch.fencing_tokens.items():
            session.fencing_tokens[resource] = max(
                token, session.fencing_tokens.get(resource, 0)
            )
        if stale:
            return self._failure_receipt(session, dispatch, ErrorCode.RESOURCE_BUSY)
        if failure is not None:
            return self._failure_receipt(session, dispatch, failure)
        return self._apply(session, dispatch)

    def _apply(self, session: _Session, dispatch: Dispatch) -> Receipt:
        command = dispatch.command
        if command.operation in ("transfer_liquid", "drain_layers"):
            source = session.vessels[command.source]  # type: ignore[index]
            target = session.vessels[command.target]  # type: ignore[index]
            amount = (
                command.parameters["volume"].value
                if command.operation == "transfer_liquid"
                else command.parameters["pixels"].value * 100.0
            )
            if source.volume < amount or target.volume + amount > 1000.0:
                return self._failure_receipt(
                    session, dispatch, ErrorCode.PRECONDITION_FAILED
                )
            solute = source.solute * amount / source.volume
            source.volume -= amount
            source.solute -= solute
            target.volume += amount
            target.solute += solute
        duration = (
            command.parameters["duration"].value
            if command.operation in ("mix_model", "settle_model")
            else 1.0
        )
        session.sim_time += duration
        session.step_index += 1
        if command.operation == "end_experiment":
            session.ended = True
            session.end_reason = EndReason.END_ACTION
        elif session.step_index >= 50:
            session.ended = True
            session.end_reason = EndReason.MAX_STEPS
        return Receipt(
            dispatch.provider_command_id,
            True,
            CommandState.SUCCEEDED,
            None,
            {
                "terminated": session.end_reason is EndReason.END_ACTION,
                "truncated": session.end_reason is EndReason.MAX_STEPS,
            },
            session.end_reason,
            session.sim_time,
            session.step_index,
            self._observation(session),
            self._evaluation(session),
        )

    @staticmethod
    def _failure_receipt(
        session: _Session,
        dispatch: Dispatch,
        code: ErrorCode,
        *,
        rejected: bool = False,
    ) -> Receipt:
        return Receipt(
            dispatch.provider_command_id,
            False,
            CommandState.REJECTED if rejected else CommandState.FAILED,
            LabError(code, "Command was not applied").to_dict(),
            {
                "terminated": session.end_reason is EndReason.END_ACTION,
                "truncated": session.end_reason is EndReason.MAX_STEPS,
            },
            session.end_reason,
            session.sim_time,
            session.step_index,
            None,
            None,
        )

    @staticmethod
    def _observation(session: _Session) -> dict[str, Any]:
        volumes = (
            session.vessels["extraction_vessel"].volume,
            session.vessels["beaker_1"].volume + session.vessels["beaker_2"].volume,
        )
        layers = [
            [float(index < int(volume / capacity * 10)) for index in range(10)]
            for volume, capacity in zip(volumes, (1000.0, 2000.0))
        ]
        channels = (
            ObservationChannel(
                "layers",
                ChannelKind.ARRAY,
                "dimensionless",
                (2, 10),
                layers,
                ChannelQuality.OK,
                ChannelSource.SIMULATED_SENSOR,
            ),
            ObservationChannel(
                "targets",
                ChannelKind.CATEGORY,
                "dimensionless",
                (),
                "beaker_2",
                ChannelQuality.OK,
                ChannelSource.SIMULATED_SENSOR,
            ),
        )
        return {
            "channels": [channel.to_dict() for channel in channels],
            "sim_time": session.sim_time,
        }

    @staticmethod
    def _evaluation(session: _Session) -> dict[str, Any]:
        return {
            "reward": session.vessels["beaker_2"].solute / session.initial_solute,
            "ground_truth": {
                "vessels": [
                    {
                        "resource_id": name,
                        "volume_L": session.vessels[name].volume / 1000.0,
                        "moles": {"toy_solute": session.vessels[name].solute},
                    }
                    for name in _RESOURCES
                ]
            },
            "metrics": {},
        }

    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        with self._lock:
            fail_query, self._fail_query = self._fail_query, False
            session = self._session(session_id)
            if fail_query:
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT,
                    "Provider query response was not received",
                )
            receipt = session.receipts.get(provider_command_id)
            if receipt is not None:
                return deepcopy(receipt)
            if provider_command_id in session.seen:
                raise LabError(
                    ErrorCode.OUTCOME_UNKNOWN, "Receipt is no longer retained"
                )
            return None

    def stop(self, session_id: str, reason: str) -> StopResult:
        with self._lock:
            self._session(session_id).ended = True
            return StopResult(True, "end_session")

    def close(self, session_id: str) -> None:
        # Closing twice is not an error: shutdown paths may race.
        with self._lock:
            self._check_device()
            self._sessions.pop(session_id, None)

    def alive(self, session_id: str) -> bool:
        with self._lock:
            return not self._crashed and session_id in self._sessions

    def fail_next(self, code: ErrorCode | str) -> None:
        with self._lock:
            self._check_device()
            try:
                failure = ErrorCode(code)
            except (ValueError, TypeError):
                raise LabError(
                    ErrorCode.INVALID_PARAMETERS, "Unknown failure code"
                ) from None
            # fail_next is a device refusal; transport failures have their own
            # hooks and raise instead of returning a receipt.
            if failure.value not in RECEIPT_ERROR_CODES:
                raise LabError(
                    ErrorCode.INVALID_PARAMETERS, "Not a device refusal code"
                )
            self._failure = failure

    def lose_response_next(self) -> None:
        with self._lock:
            self._check_device()
            self._lose_response = True

    def lose_request_next(self) -> None:
        with self._lock:
            self._check_device()
            self._lose_request = True

    def fail_query_next(self) -> None:
        with self._lock:
            self._check_device()
            self._fail_query = True

    def timeout_and_die_next(self) -> None:
        with self._lock:
            self._check_device()
            self._timeout_and_die = True

    def protocol_error_next(self) -> None:
        with self._lock:
            self._check_device()
            self._protocol_error = True

    def crash(self) -> None:
        with self._lock:
            self._crashed = True
            self._sessions.clear()

    def describe_mismatch(self) -> None:
        with self._lock:
            self._check_device()
            self._mismatch = True


def fake_registration() -> DeviceRegistration:
    """Return an explicit registration; importing this module registers nothing."""
    return DeviceRegistration(
        device_id=FakeExtractorDevice.device_id,
        backend=FakeExtractorDevice.backend,
        mode=RunMode.SIMULATION,
        title="Fake extraction device",
        profiles=(_PROFILE,),
        descriptor_loader=_descriptor,
        port_factory=FakeExtractorDevice,
    )
