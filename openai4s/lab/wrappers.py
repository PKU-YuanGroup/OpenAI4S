"""Explicit simulation assumptions around the seven-method DevicePort.

There is no observe method on DevicePort: observation latency/noise applies to
open, execute and query payloads. Fault steps are one-based *new command*
ordinals per session, including refusals/dropped requests, excluding retries.
Busy windows are inclusive ordinal ranges, not real time or model time.
Internal evaluation payloads pass through unchanged; callers must still use the
manifest projections at the agent/UI boundary.
"""

from __future__ import annotations

import math
import random
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any

from openai4s.lab.manifest import match_command
from openai4s.lab.models import (
    ChannelKind,
    ChannelQuality,
    CommandRequest,
    CommandState,
    DeviceDescriptor,
    Dispatch,
    ErrorCode,
    LabError,
    ObservationChannel,
    Receipt,
    SessionOpened,
    SessionOpenRequest,
    StopResult,
    canonical_json,
    sha256_hex,
)
from openai4s.lab.ports import DevicePort


def _invalid() -> LabError:
    return LabError(ErrorCode.INVALID_PARAMETERS, "Invalid wrapper configuration")


def _nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


class _Wrapper:
    def __init__(self, port: DevicePort, assumption: str):
        self._port = port
        self.device_id = port.device_id
        self._assumption = assumption

    def _descriptor(self, descriptor: DeviceDescriptor) -> DeviceDescriptor:
        return replace(
            descriptor, assumptions=descriptor.assumptions + (self._assumption,)
        )

    def describe(self, profile: str) -> DeviceDescriptor:
        return self._descriptor(self._port.describe(profile))

    def open(self, request: SessionOpenRequest) -> SessionOpened:
        opened = self._port.open(request)
        return replace(opened, descriptor=self._descriptor(opened.descriptor))

    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        return self._port.execute(session_id, dispatch)

    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        return self._port.query(session_id, provider_command_id)

    def stop(self, session_id: str, reason: str) -> StopResult:
        return self._port.stop(session_id, reason)

    def close(self, session_id: str) -> None:
        self._port.close(session_id)

    def alive(self, session_id: str) -> bool:
        return self._port.alive(session_id)


class LatencyWrapper(_Wrapper):
    """Delays delivery in wall-clock seconds without changing model time."""

    def __init__(
        self,
        port: DevicePort,
        *,
        execute_delay_ms: float,
        observe_delay_ms: float,
        sleep: Callable[[float], None],
    ):
        if not all(_nonnegative(x) for x in (execute_delay_ms, observe_delay_ms)):
            raise _invalid()
        if not callable(sleep):
            raise _invalid()
        super().__init__(
            port,
            f"latency: execute={execute_delay_ms:g} ms; observation={observe_delay_ms:g} ms; wall clock only",
        )
        self._execute_delay = execute_delay_ms / 1000
        self._observe_delay = observe_delay_ms / 1000
        self._sleep = sleep

    def open(self, request: SessionOpenRequest) -> SessionOpened:
        opened = super().open(request)
        self._sleep(self._observe_delay)
        return opened

    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        self._sleep(self._execute_delay)
        receipt = super().execute(session_id, dispatch)
        if receipt.observation is not None:
            self._sleep(self._observe_delay)
        return receipt

    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        receipt = super().query(session_id, provider_command_id)
        if receipt is not None and receipt.observation is not None:
            self._sleep(self._observe_delay)
        return receipt


class ObservationNoiseWrapper(_Wrapper):
    """Additive Gaussian noise, model={"kind": "gaussian", "sigma": number}.

    No clipping or inferred uncertainty. A local RNG is keyed by wrapper seed,
    profile, session seed and provider step so query/retry/interleaving cannot
    resample evidence. Noise is in the declared channel's units.
    """

    def __init__(self, port: DevicePort, *, channel: str, model: Mapping, seed: int):
        if (
            not isinstance(channel, str)
            or not channel
            or not isinstance(model, Mapping)
            or set(model) != {"kind", "sigma"}
            or model["kind"] != "gaussian"
            or not _nonnegative(model["sigma"])
            or not isinstance(seed, int)
            or isinstance(seed, bool)
        ):
            raise _invalid()
        self._channel = channel
        self._sigma = float(model["sigma"])
        self._seed = seed
        self._sessions: dict[str, tuple[DeviceDescriptor, int | None]] = {}
        self._dead: set[str] = set()
        self._lock = RLock()
        super().__init__(
            port,
            # The wrapper seed stays private: with the run seed, profile and
            # step all public, publishing it would let a policy subtract the
            # noise exactly. Comparability rests on the declared model.
            f"observation_noise: gaussian sigma={self._sigma:g} on {channel}; additive, unclipped; privately seeded per provider step",
        )

    def _descriptor(self, descriptor: DeviceDescriptor) -> DeviceDescriptor:
        spec = next(
            (c for c in descriptor.observation_channels if c.name == self._channel),
            None,
        )
        if spec is None or spec.kind not in (ChannelKind.ARRAY, ChannelKind.SCALAR):
            raise _invalid()
        return super()._descriptor(descriptor)

    def _noise(self, observation: Mapping, descriptor, session_seed, step):
        spec = next(
            c for c in descriptor.observation_channels if c.name == self._channel
        )
        rng = random.Random(
            sha256_hex(
                canonical_json([self._seed, descriptor.profile, session_seed, step])
            )
        )

        def add(value):
            if isinstance(value, (list, tuple)):
                return [add(v) for v in value]
            return value + rng.gauss(0, self._sigma)

        result = deepcopy(dict(observation))
        for index, row in enumerate(result["channels"]):
            channel = ObservationChannel.from_dict(row)
            if channel.name != self._channel:
                continue
            if (channel.kind, channel.shape, channel.unit, channel.source) != (
                spec.kind,
                spec.shape,
                spec.unit,
                spec.source,
            ):
                raise LabError(
                    ErrorCode.PROVIDER_PROTOCOL_ERROR,
                    "Sensor does not match its declaration",
                )
            if spec.available and channel.quality is ChannelQuality.OK:
                result["channels"][index] = replace(
                    channel, value=add(channel.value)
                ).to_dict()
        return result

    def _check_session(self, session_id: str) -> None:
        if session_id in self._dead:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider session is unavailable"
            )

    def _sample(self, session_id, observation, descriptor, seed, step):
        try:
            return self._noise(observation, descriptor, seed, step)
        except (LabError, OverflowError):
            # An invalid sensor frame or nonfinite transformed sample cannot
            # remain a usable session after a protocol failure.
            self._dead.add(session_id)
            self._sessions.pop(session_id, None)
            try:
                self._port.close(session_id)
            except LabError:
                pass
            raise LabError(
                ErrorCode.PROVIDER_PROTOCOL_ERROR, "Invalid sensor sample"
            ) from None

    def open(self, request: SessionOpenRequest) -> SessionOpened:
        with self._lock:
            self.describe(request.profile)  # Reject invalid channel before opening.
            opened = super().open(request)
            self._sessions[opened.session_id] = (opened.descriptor, request.seed)
            return replace(
                opened,
                observation=self._sample(
                    opened.session_id,
                    opened.observation,
                    opened.descriptor,
                    request.seed,
                    0,
                ),
            )

    def _receipt(self, session_id: str, receipt: Receipt | None) -> Receipt | None:
        if receipt is None or receipt.observation is None:
            return receipt
        descriptor, seed = self._sessions[session_id]
        return replace(
            receipt,
            observation=self._sample(
                session_id, receipt.observation, descriptor, seed, receipt.step_index
            ),
        )

    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        with self._lock:
            self._check_session(session_id)
            receipt = self._receipt(session_id, super().execute(session_id, dispatch))
            assert receipt is not None
            return receipt

    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        with self._lock:
            self._check_session(session_id)
            return self._receipt(
                session_id, super().query(session_id, provider_command_id)
            )

    def stop(self, session_id: str, reason: str) -> StopResult:
        with self._lock:
            self._check_session(session_id)
            return super().stop(session_id, reason)

    def alive(self, session_id: str) -> bool:
        with self._lock:
            return session_id not in self._dead and super().alive(session_id)

    def close(self, session_id: str) -> None:
        if session_id in self._dead:
            return  # Already closed when its sensor frame was rejected.
        # Outside the lock: an execute holding it may be blocked in the inner
        # port, and close must be able to preempt it (CONTRACT §9).
        super().close(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)


@dataclass
class _FaultSession:
    descriptor: DeviceDescriptor
    attempts: set[str] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)
    receipts: OrderedDict[str, Receipt] = field(default_factory=OrderedDict)
    fences: dict[str, int] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    sim_time: float = 0
    step: int = 0
    raw: dict = field(default_factory=lambda: {"terminated": False, "truncated": False})
    end_reason: Any = None
    stopped: bool = False


class FaultInjectionWrapper(_Wrapper):
    """One-shot faults at new-command ordinals; cached refusals retain 256 ids.

    lose_request times out without delivery (query is authoritative None).
    lose_response delivers then times out (query delegates to the real cache).
    crash closes the wrapped sessions and makes this wrapper permanently dead.
    Seen synthetic refusal ids survive cache eviction and cannot execute again.
    """

    _FAULTS = frozenset(
        {"lose_response", "lose_request", "crash", "busy", "precondition_failed"}
    )

    def __init__(self, port: DevicePort, *, schedule: Mapping[int, str]):
        if not isinstance(schedule, Mapping) or any(
            type(k) is not int
            or k < 1
            or not isinstance(v, str)
            or v not in self._FAULTS
            for k, v in schedule.items()
        ):
            raise _invalid()
        self._schedule = dict(schedule)
        self._sessions: dict[str, _FaultSession] = {}
        self._lock = RLock()
        self._crashed = False
        super().__init__(
            port,
            "fault_injection: new-command ordinals "
            + canonical_json({str(k): v for k, v in sorted(schedule.items())}),
        )

    def _check(self, session_id: str | None = None) -> None:
        if self._crashed or (
            session_id is not None and not self._port.alive(session_id)
        ):
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider session is unavailable"
            )

    def describe(self, profile: str) -> DeviceDescriptor:
        with self._lock:
            self._check()
            return super().describe(profile)

    def open(self, request: SessionOpenRequest) -> SessionOpened:
        with self._lock:
            self._check()
            opened = super().open(request)
            self._sessions[opened.session_id] = _FaultSession(
                opened.descriptor, sim_time=opened.observation["sim_time"]
            )
            return opened

    @staticmethod
    def _remember_state(session: _FaultSession, receipt: Receipt) -> None:
        if receipt.step_index >= session.step:
            session.step = receipt.step_index
            session.sim_time = receipt.sim_time
            session.raw = dict(receipt.raw)
            session.end_reason = receipt.end_reason

    def _cached(self, session_id: str, command_id: str) -> Receipt | None:
        self._check(session_id)
        session = self._sessions[session_id]
        if command_id in session.receipts:
            return deepcopy(session.receipts[command_id])
        if command_id in session.seen:
            raise LabError(
                ErrorCode.OUTCOME_UNKNOWN,
                "Receipt no longer retained; command will not run again",
            )
        receipt = self._port.query(session_id, command_id)
        session.pending.discard(command_id)
        if receipt is not None:
            self._remember_state(session, receipt)
        return receipt

    def _refuse(self, session, dispatch, code, *, rejected=False):
        receipt = Receipt(
            dispatch.provider_command_id,
            False,
            CommandState.REJECTED if rejected else CommandState.FAILED,
            LabError(code, "Command was not applied").to_dict(),
            session.raw,
            session.end_reason,
            session.sim_time,
            session.step,
            None,
            None,
        )
        session.seen.add(dispatch.provider_command_id)
        session.receipts[dispatch.provider_command_id] = deepcopy(receipt)
        if len(session.receipts) > 256:
            session.receipts.popitem(last=False)
        return deepcopy(receipt)

    def _fault(self, ordinal: int) -> str | None:
        return self._schedule.get(ordinal)

    def execute(self, session_id: str, dispatch: Dispatch) -> Receipt:
        with self._lock:
            cached = self._cached(session_id, dispatch.provider_command_id)
            if cached is not None:
                return cached
            session = self._sessions[session_id]
            # A nested port may time out after advancing. Reconcile its state
            # before manufacturing a refusal; never stamp stale step/time.
            for pending_id in tuple(session.pending):
                self._cached(session_id, pending_id)
            first = dispatch.provider_command_id not in session.attempts
            session.attempts.add(dispatch.provider_command_id)
            fault = self._fault(len(session.attempts)) if first else None
            if fault == "lose_request":
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT, "Provider request was not delivered"
                )
            if fault == "crash":
                self._crashed = True
                for sid in self._sessions:
                    try:
                        self._port.close(sid)
                    except LabError:
                        pass
                raise LabError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "Provider session is unavailable"
                )
            if session.stopped or session.end_reason is not None:
                return self._refuse(session, dispatch, ErrorCode.RUN_ENDED)
            data = dispatch.command.to_dict()
            data.pop("capability_id")
            try:
                normalized = match_command(
                    session.descriptor, CommandRequest.from_dict(data)
                )
                if normalized != dispatch.command:
                    raise LabError(ErrorCode.UNSUPPORTED_ACTION, "Unadvertised command")
                capability = next(
                    c
                    for c in session.descriptor.capabilities
                    if c.capability_id == normalized.capability_id
                )
                if set(dispatch.fencing_tokens) != set(capability.resources):
                    raise LabError(
                        ErrorCode.INVALID_PARAMETERS, "Invalid fencing resources"
                    )
            except LabError as exc:
                return self._refuse(session, dispatch, exc.code, rejected=True)
            stale = any(
                t < session.fences.get(r, 0) for r, t in dispatch.fencing_tokens.items()
            )
            for resource, token in dispatch.fencing_tokens.items():
                session.fences[resource] = max(token, session.fences.get(resource, 0))
            if stale or fault == "busy":
                return self._refuse(session, dispatch, ErrorCode.RESOURCE_BUSY)
            if fault == "precondition_failed":
                return self._refuse(session, dispatch, ErrorCode.PRECONDITION_FAILED)
            try:
                receipt = self._port.execute(session_id, dispatch)
            except LabError as exc:
                if exc.code in (ErrorCode.PROVIDER_TIMEOUT, ErrorCode.OUTCOME_UNKNOWN):
                    session.pending.add(dispatch.provider_command_id)
                raise
            self._remember_state(session, receipt)
            if fault == "lose_response":
                raise LabError(
                    ErrorCode.PROVIDER_TIMEOUT, "Provider response was not received"
                )
            return receipt

    def query(self, session_id: str, provider_command_id: str) -> Receipt | None:
        with self._lock:
            return self._cached(session_id, provider_command_id)

    def stop(self, session_id: str, reason: str) -> StopResult:
        with self._lock:
            self._check(session_id)
            stopped = super().stop(session_id, reason)
            if stopped.stopped:
                self._sessions[session_id].stopped = True
            return stopped

    def close(self, session_id: str) -> None:
        self._check()
        # Outside the lock: an execute holding it may be blocked in the inner
        # port, and close must be able to preempt it (CONTRACT §9).
        super().close(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)

    def alive(self, session_id: str) -> bool:
        with self._lock:
            return not self._crashed and super().alive(session_id)


class BusyWrapper(FaultInjectionWrapper):
    """Refuse new commands in inclusive, one-based ordinal windows."""

    def __init__(self, port: DevicePort, *, busy_windows: Sequence[tuple[int, int]]):
        if not isinstance(busy_windows, Sequence):
            raise _invalid()
        windows = tuple(busy_windows)
        if any(
            not isinstance(w, (list, tuple))
            or len(w) != 2
            or type(w[0]) is not int
            or type(w[1]) is not int
            or w[0] < 1
            or w[1] < w[0]
            for w in windows
        ):
            raise _invalid()
        super().__init__(port, schedule={})
        self._windows = tuple((a, b) for a, b in windows)
        self._assumption = (
            "busy_windows: inclusive new-command ordinals "
            + canonical_json(self._windows)
        )

    def _fault(self, ordinal: int) -> str | None:
        return "busy" if any(a <= ordinal <= b for a, b in self._windows) else None
