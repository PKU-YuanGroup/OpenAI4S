"""Device-port consistency, including wrapper compositions and fault evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from openai4s.lab.fake import FakeExtractorDevice
from openai4s.lab.manifest import match_command, project_receipt
from openai4s.lab.models import (
    CommandRequest,
    Dispatch,
    ErrorCode,
    LabError,
    Quantity,
    SessionOpenRequest,
)
from openai4s.lab.wrappers import (
    BusyWrapper,
    FaultInjectionWrapper,
    LatencyWrapper,
    ObservationNoiseWrapper,
)


def noise(port, seed=10):
    return ObservationNoiseWrapper(
        port, channel="layers", model={"kind": "gaussian", "sigma": 0.1}, seed=seed
    )


def wrap(port, kind):
    if kind == "bare":
        return port
    if kind == "latency":
        return LatencyWrapper(
            port, execute_delay_ms=3, observe_delay_ms=4, sleep=lambda _: None
        )
    if kind == "noise":
        return noise(port)
    if kind == "fault":
        return FaultInjectionWrapper(port, schedule={})
    if kind == "busy":
        return BusyWrapper(port, busy_windows=[])
    if kind == "combined":
        return noise(wrap(wrap(wrap(port, "fault"), "busy"), "latency"))
    return FaultInjectionWrapper(noise(wrap(port, "latency")), schedule={})


def opened(port, seed=7):
    d = port.describe("toy-extract-v0")
    return port.open(SessionOpenRequest(d.profile, seed, {}, d.capability_revision))


def dispatch(o, ident="command", token=1):
    c = next(c for c in o.descriptor.capabilities if c.operation == "mix_model")
    request = CommandRequest(
        "run",
        c.operation,
        c.source,
        c.target,
        {k: Quantity(v.allowed[0], v.unit) for k, v in c.parameters.items()},
        0,
        ident,
    )
    return Dispatch(
        ident, match_command(o.descriptor, request), {r: token for r in c.resources}
    )


KINDS = ["bare", "latency", "noise", "fault", "busy", "combined", "reverse"]


@pytest.mark.parametrize("kind", KINDS)
def test_port_consistency(kind):
    base = FakeExtractorDevice()
    port = wrap(base, kind)
    o = opened(port)
    assert o.descriptor == port.describe(o.descriptor.profile)
    assert len(o.descriptor.assumptions) == len(
        base.describe(o.descriptor.profile).assumptions
    ) + (
        0
        if kind == "bare"
        else 4 if kind == "combined" else 3 if kind == "reverse" else 1
    )
    assert port.query(o.session_id, "never") is None
    d = dispatch(o, token=20)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: port.execute(o.session_id, d), range(8)))
    r = receipts[0]
    assert all(item == r for item in receipts)
    assert r.applied and r.step_index == 1
    assert port.query(o.session_id, d.provider_command_id) == r
    saved = r.to_dict()
    r.observation["channels"][0]["value"][0][0] = 900
    r.evaluation["ground_truth"]["vessels"].clear()
    assert port.execute(o.session_id, d).to_dict() == saved
    stale = port.execute(o.session_id, dispatch(o, "stale", token=19))
    assert stale.error["code"] == "resource_busy" and stale.step_index == 1
    second = opened(port)
    assert port.query(second.session_id, d.provider_command_id) is None
    assert port.stop(o.session_id, "test").stopped
    refused = port.execute(o.session_id, dispatch(o, "ended", token=21))
    assert not refused.applied and refused.error["code"] == "run_ended"
    port.close(o.session_id)
    port.close(o.session_id)
    assert not port.alive(o.session_id) and port.alive(second.session_id)
    for action in (
        lambda: port.execute(o.session_id, d),
        lambda: port.query(o.session_id, "never"),
    ):
        with pytest.raises(LabError) as caught:
            action()
        assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE


@pytest.mark.parametrize("kind", KINDS)
def test_underlying_eviction_is_never_mistaken_for_non_delivery(kind):
    base = FakeExtractorDevice()
    port = wrap(base, kind)
    o = opened(port)
    for i in range(257):
        base.fail_next(ErrorCode.PRECONDITION_FAILED)
        port.execute(o.session_id, dispatch(o, str(i)))
    for action in (
        lambda: port.query(o.session_id, "0"),
        lambda: port.execute(o.session_id, dispatch(o, "0")),
    ):
        with pytest.raises(LabError) as caught:
            action()
        assert caught.value.code is ErrorCode.OUTCOME_UNKNOWN
    assert port.query(o.session_id, "absent") is None
    assert port.execute(o.session_id, dispatch(o, "fresh")).step_index == 1


def test_latency_delays_all_observation_delivery_without_changing_time():
    delays = []
    port = LatencyWrapper(
        FakeExtractorDevice(),
        execute_delay_ms=12,
        observe_delay_ms=25,
        sleep=delays.append,
    )
    o = opened(port)
    d = dispatch(o)
    r = port.execute(o.session_id, d)
    assert port.query(o.session_id, d.provider_command_id) == r
    assert port.query(o.session_id, "missing") is None
    assert delays == [0.025, 0.012, 0.025, 0.025]
    assert r.sim_time == 1 and o.observation["sim_time"] == 0
    assert "wall clock only" in o.descriptor.assumptions[-1]


def test_noise_is_declared_reproducible_idempotent_and_truth_preserving():
    base = FakeExtractorDevice()
    port = noise(base)
    o = opened(port)
    clean_port = FakeExtractorDevice()
    clean = opened(clean_port)
    assert o.evaluation == clean.evaluation
    assert o.observation != clean.observation
    assert o.observation["channels"][1] == clean.observation["channels"][1]
    assert "gaussian sigma=0.1 on layers" in o.descriptor.assumptions[-1]
    r = port.execute(o.session_id, dispatch(o))
    plain = base.query(o.session_id, "command")
    assert r.evaluation == plain.evaluation
    assert r.observation != plain.observation
    assert port.query(o.session_id, "command") == r
    other = noise(FakeExtractorDevice())
    other_o = opened(other)
    assert other_o.observation == o.observation
    assert (
        other.execute(other_o.session_id, dispatch(other_o)).observation
        == r.observation
    )
    assert opened(noise(FakeExtractorDevice(), seed=11)).observation != o.observation
    assert "evaluation" not in project_receipt(r)
    assert "ground_truth" not in repr(r)


@pytest.mark.parametrize(
    "fault", ["lose_response", "lose_request", "crash", "busy", "precondition_failed"]
)
def test_failure_semantics_and_no_second_application(fault):
    base = FakeExtractorDevice()
    port = FaultInjectionWrapper(base, schedule={1: fault})
    o = opened(port)
    d = dispatch(o)
    assert fault in o.descriptor.assumptions[-1]
    if fault in {"busy", "precondition_failed"}:
        r = port.execute(o.session_id, d)
        assert r.status.value == "failed" and not r.applied and r.step_index == 0
        assert r.error["code"] == ("resource_busy" if fault == "busy" else fault)
        assert r.observation is None and r.evaluation is None
        assert port.query(o.session_id, "command") == r
        assert port.execute(o.session_id, d) == r
        assert port.execute(o.session_id, dispatch(o, "after")).step_index == 1
    else:
        with pytest.raises(LabError) as caught:
            port.execute(o.session_id, d)
        assert caught.value.code is (
            ErrorCode.PROVIDER_UNAVAILABLE
            if fault == "crash"
            else ErrorCode.PROVIDER_TIMEOUT
        )
        assert port.alive(o.session_id) is (fault != "crash")
        if fault == "crash":
            assert not base.alive(o.session_id)
            for action in (
                lambda: port.query(o.session_id, "command"),
                lambda: port.describe(o.descriptor.profile),
                lambda: opened(port),
                lambda: port.stop(o.session_id, "x"),
                lambda: port.close(o.session_id),
            ):
                with pytest.raises(LabError) as dead:
                    action()
                assert dead.value.code is ErrorCode.PROVIDER_UNAVAILABLE
        else:
            r = port.query(o.session_id, "command")
            if fault == "lose_request":
                assert r is None and base.query(o.session_id, "command") is None
            else:
                assert r.applied and r.step_index == 1
            replay = port.execute(o.session_id, d)
            assert replay.applied and replay.step_index == 1
            assert port.execute(o.session_id, dispatch(o, "after")).step_index == 2


def test_busy_windows_refusals_fences_and_eviction():
    port = BusyWrapper(FakeExtractorDevice(), busy_windows=[(1, 257)])
    o = opened(port)
    assert "busy_windows" in o.descriptor.assumptions[-1]
    for i in range(257):
        r = port.execute(o.session_id, dispatch(o, str(i), token=20))
        assert not r.applied and r.step_index == 0
        assert r.error["code"] == "resource_busy"
    for action in (
        lambda: port.query(o.session_id, "0"),
        lambda: port.execute(o.session_id, dispatch(o, "0")),
    ):
        with pytest.raises(LabError) as caught:
            action()
        assert caught.value.code is ErrorCode.OUTCOME_UNKNOWN
    assert port.query(o.session_id, "never") is None
    stale = port.execute(o.session_id, dispatch(o, "stale", token=19))
    assert stale.error["code"] == "resource_busy"
    assert port.execute(o.session_id, dispatch(o, "fresh", token=21)).step_index == 1


def test_noise_and_fault_composition_reconciles_identical_evidence():
    for outer in (True, False):
        base = FakeExtractorDevice()
        port = (
            noise(FaultInjectionWrapper(base, schedule={1: "lose_response"}))
            if outer
            else FaultInjectionWrapper(noise(base), schedule={1: "lose_response"})
        )
        o = opened(port)
        with pytest.raises(LabError):
            port.execute(o.session_id, dispatch(o))
        recovered = port.query(o.session_id, "command")
        assert recovered == port.execute(o.session_id, dispatch(o))
        assert recovered.evaluation == base.query(o.session_id, "command").evaluation
        assert recovered.observation != base.query(o.session_id, "command").observation
        refused = port.execute(o.session_id, dispatch(o, "stale", token=0))
        assert refused.step_index == 1


def test_invalid_wrapper_configuration_and_non_numeric_channels_are_refused():
    base = FakeExtractorDevice()
    for create in (
        lambda: LatencyWrapper(
            base, execute_delay_ms=-1, observe_delay_ms=0, sleep=lambda _: None
        ),
        lambda: ObservationNoiseWrapper(
            base,
            channel="layers",
            model={"kind": "gaussian", "sigma": float("nan")},
            seed=1,
        ),
        lambda: FaultInjectionWrapper(base, schedule={0: "crash"}),
        lambda: BusyWrapper(base, busy_windows=[(2, 1)]),
        lambda: ObservationNoiseWrapper(
            base, channel="targets", model={"kind": "gaussian", "sigma": 1}, seed=1
        ).describe("toy-extract-v0"),
    ):
        with pytest.raises(LabError) as caught:
            create()
        assert caught.value.code is ErrorCode.INVALID_PARAMETERS
