"""Offline provider-port behavior, including reconciliation and private truth."""

import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock

import pytest

from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.lab.manifest import match_command
from openai4s.lab.models import (
    CapabilityScope,
    CommandRequest,
    CommandState,
    Dispatch,
    EndReason,
    ErrorCode,
    LabError,
    Quantity,
    Receipt,
    RunMode,
    SessionOpened,
    SessionOpenRequest,
    StopResult,
)


def _open(device, seed=7):
    descriptor = device.describe("toy-extract-v0")
    return device.open(
        SessionOpenRequest(descriptor.profile, seed, {}, descriptor.capability_revision)
    )


def _dispatch(
    opened,
    command_id="command-1",
    *,
    operation="transfer_liquid",
    source="extraction_vessel",
    target="beaker_2",
    value=200,
    token=1,
):
    capability = next(
        cap
        for cap in opened.descriptor.capabilities
        if (cap.operation, cap.source, cap.target) == (operation, source, target)
    )
    request = CommandRequest(
        "labrun-test",
        operation,
        source,
        target,
        {
            name: Quantity(value, spec.unit)
            for name, spec in capability.parameters.items()
        },
        0,
        command_id,
    )
    return Dispatch(
        command_id,
        match_command(opened.descriptor, request),
        {resource: token for resource in capability.resources},
    )


def _mix(opened, command_id="mix", *, token=1, value=1):
    return _dispatch(
        opened,
        command_id,
        operation="mix_model",
        source="extraction_vessel",
        target=None,
        value=value,
        token=token,
    )


def test_fake_registration_and_port_lifecycle():
    registration = fake_registration()
    assert registration.device_id == "fake.extractor.01"
    assert registration.backend == "fake"
    assert registration.mode is RunMode.SIMULATION
    assert registration.profiles == ("toy-extract-v0",)
    device = registration.port_factory()
    other = registration.port_factory()
    assert device is not other
    opened = _open(device)
    assert opened.descriptor == registration.descriptor_loader(registration.profiles[0])
    assert SessionOpened.from_dict(opened.to_dict()) == opened
    assert device.alive(opened.session_id)
    assert device.query(opened.session_id, "absent") is None
    receipt = device.execute(opened.session_id, _dispatch(opened))
    assert receipt.applied and receipt.status is CommandState.SUCCEEDED
    assert Receipt.from_dict(receipt.to_dict()) == receipt
    assert device.query(opened.session_id, receipt.provider_command_id) == receipt
    assert device.stop(opened.session_id, "test complete") == StopResult(
        True, "end_session"
    )
    assert device.alive(opened.session_id)
    assert device.execute(opened.session_id, _mix(opened)).error["code"] == "run_ended"
    device.close(opened.session_id)
    assert not device.alive(opened.session_id)
    with pytest.raises(LabError) as caught:
        device.query(opened.session_id, receipt.provider_command_id)
    assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE


def test_repeated_command_returns_original_receipt_without_advancing():
    device = FakeExtractorDevice()
    opened = _open(device)
    first = device.execute(opened.session_id, _dispatch(opened))
    changed_payload = _dispatch(opened, value=1000, token=0)
    assert device.execute(opened.session_id, changed_payload) == first
    second = device.execute(opened.session_id, _mix(opened))
    assert second.step_index == 2
    assert second.evaluation == first.evaluation


def test_fencing_tracks_each_resource_and_never_accepts_lower_tokens():
    device = FakeExtractorDevice()
    opened = _open(device)
    first = device.execute(opened.session_id, _mix(opened, token=9))
    independent = device.execute(
        opened.session_id,
        _dispatch(
            opened, "independent", source="water_source", target="beaker_1", token=1
        ),
    )
    assert independent.applied
    stale = device.execute(opened.session_id, _mix(opened, "stale", token=8))
    assert stale.status is CommandState.FAILED
    assert stale.error["code"] == "resource_busy"
    assert not stale.applied
    assert stale.step_index == independent.step_index
    assert stale.sim_time == independent.sim_time
    equal = device.execute(opened.session_id, _mix(opened, "equal", token=9))
    assert equal.applied and equal.step_index == first.step_index + 2


def test_missing_fence_and_forged_capability_are_rejected_without_dispatch():
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    missing = device.execute(opened.session_id, replace(dispatch, fencing_tokens={}))
    assert missing.status is CommandState.REJECTED
    assert missing.error["code"] == "invalid_parameters"
    assert not missing.applied and missing.step_index == 0
    forged = replace(
        _dispatch(opened, "forged"),
        command=replace(dispatch.command, capability_id="nonexistent"),
    )
    rejected = device.execute(opened.session_id, forged)
    assert rejected.status is CommandState.REJECTED
    assert rejected.error["code"] == "unsupported_action"
    assert not rejected.applied and rejected.step_index == 0


@pytest.mark.parametrize("failure", ["source_empty", "target_full"])
def test_precondition_failure_does_not_change_model(failure):
    device = FakeExtractorDevice()
    opened = _open(device)
    if failure == "target_full":
        baseline = device.execute(
            opened.session_id,
            _dispatch(opened, "fill", source="water_source", value=1000),
        )
        command = _dispatch(opened)
    else:
        baseline = device.execute(opened.session_id, _mix(opened))
        command = _dispatch(opened, source="beaker_1")
    failed = device.execute(opened.session_id, command)
    assert not failed.applied
    assert failed.status is CommandState.FAILED
    assert failed.error["code"] == "precondition_failed"
    assert (failed.step_index, failed.sim_time) == (
        baseline.step_index,
        baseline.sim_time,
    )
    assert failed.observation is None and failed.evaluation is None
    after = device.execute(opened.session_id, _mix(opened, "after"))
    assert after.evaluation == baseline.evaluation


def test_fail_next_is_one_shot_and_returns_a_cached_unapplied_receipt():
    device = FakeExtractorDevice()
    opened = _open(device)
    device.fail_next(ErrorCode.PRECONDITION_FAILED)
    dispatch = _dispatch(opened)
    failed = device.execute(opened.session_id, dispatch)
    assert failed.status is CommandState.FAILED
    assert failed.error["code"] == "precondition_failed"
    assert not failed.applied and failed.step_index == 0
    assert device.alive(opened.session_id)
    assert device.query(opened.session_id, dispatch.provider_command_id) == failed
    assert device.execute(opened.session_id, dispatch) == failed
    after = device.execute(opened.session_id, _dispatch(opened, "after"))
    assert after.applied and after.step_index == 1


def test_lost_response_keeps_live_session_and_queryable_applied_receipt():
    device = FakeExtractorDevice()
    opened = _open(device)
    device.lose_response_next()
    dispatch = _dispatch(opened)
    with pytest.raises(LabError) as caught:
        device.execute(opened.session_id, dispatch)
    assert caught.value.code is ErrorCode.PROVIDER_TIMEOUT
    assert device.alive(opened.session_id)
    recovered = device.query(opened.session_id, dispatch.provider_command_id)
    assert recovered.applied and recovered.step_index == 1
    assert recovered.evaluation["reward"] == pytest.approx(0.5)
    assert device.execute(opened.session_id, dispatch) == recovered
    after = device.execute(opened.session_id, _mix(opened))
    assert after.applied and after.step_index == 2


@pytest.mark.parametrize("refusal", ["missing_fence", "forged", "stale", "ended"])
def test_fail_next_is_consumed_even_when_an_earlier_check_refuses(refusal):
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _mix(opened)
    expected = "invalid_parameters"
    if refusal == "missing_fence":
        dispatch = replace(dispatch, fencing_tokens={})
    elif refusal == "forged":
        dispatch = replace(
            dispatch, command=replace(dispatch.command, capability_id="nonexistent")
        )
        expected = "unsupported_action"
    elif refusal == "stale":
        device.execute(opened.session_id, _mix(opened, "first", token=9))
        expected = "resource_busy"
    else:
        device.stop(opened.session_id, "test")
        expected = "run_ended"
    device.fail_next(ErrorCode.PRECONDITION_FAILED)
    receipt = device.execute(opened.session_id, dispatch)
    assert not receipt.applied and receipt.error["code"] == expected
    # A new session also detects a leaked device-wide hook after RUN_ENDED.
    other = _open(device)
    after = device.execute(other.session_id, _mix(other))
    assert after.applied and after.step_index == 1


@pytest.mark.parametrize("refusal", ["fail_next", "missing_fence", "empty", "ended"])
def test_lost_response_hides_unapplied_receipts_and_does_not_leak(refusal):
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    expected = "precondition_failed"
    if refusal == "fail_next":
        device.fail_next(ErrorCode.PRECONDITION_FAILED)
    elif refusal == "missing_fence":
        dispatch = replace(dispatch, fencing_tokens={})
        expected = "invalid_parameters"
    elif refusal == "empty":
        dispatch = _dispatch(opened, source="beaker_1")
    else:
        device.stop(opened.session_id, "test")
        expected = "run_ended"
    device.lose_response_next()
    with pytest.raises(LabError) as caught:
        device.execute(opened.session_id, dispatch)
    assert caught.value.code is ErrorCode.PROVIDER_TIMEOUT
    assert device.alive(opened.session_id)
    receipt = device.query(opened.session_id, dispatch.provider_command_id)
    assert not receipt.applied and receipt.error["code"] == expected
    assert receipt.step_index == 0
    assert device.execute(opened.session_id, dispatch) == receipt
    other = _open(device)
    assert device.execute(other.session_id, _mix(other)).applied


@pytest.mark.parametrize(
    "hook",
    [
        "fail_next",
        "lose_response_next",
        "lose_request_next",
        "timeout_and_die_next",
        "protocol_error_next",
        "fail_and_lose_response",
    ],
)
def test_hooks_on_cached_replays_never_reapply_or_leak(hook, monkeypatch):
    device = FakeExtractorDevice()
    opened = _open(device)
    applied = []
    original_apply = device._apply

    def record_apply(session, dispatch):
        receipt = original_apply(session, dispatch)
        applied.append(receipt)
        return receipt

    monkeypatch.setattr(device, "_apply", record_apply)
    dispatch = _dispatch(opened)
    original = device.execute(opened.session_id, dispatch)
    if hook in {"fail_next", "fail_and_lose_response"}:
        device.fail_next(ErrorCode.PRECONDITION_FAILED)
    if hook == "fail_and_lose_response":
        device.lose_response_next()
    elif hook != "fail_next":
        getattr(device, hook)()
    if hook == "fail_next":
        assert device.execute(opened.session_id, dispatch) == original
    else:
        with pytest.raises(LabError) as caught:
            device.execute(opened.session_id, dispatch)
        expected = (
            ErrorCode.PROVIDER_PROTOCOL_ERROR
            if hook == "protocol_error_next"
            else ErrorCode.PROVIDER_TIMEOUT
        )
        assert caught.value.code is expected
    assert applied == [original]
    fatal = hook in {"timeout_and_die_next", "protocol_error_next"}
    assert device.alive(opened.session_id) is not fatal
    if not fatal:
        # Losing a duplicate request must not erase a known prior receipt.
        assert device.query(opened.session_id, dispatch.provider_command_id) == original
        assert device.execute(opened.session_id, dispatch) == original
    other = _open(device)
    after = device.execute(other.session_id, _mix(other))
    assert after.applied and after.step_index == 1
    assert len(applied) == 2


def test_lost_request_is_never_received_and_leaves_model_unchanged():
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    device.lose_request_next()
    with pytest.raises(LabError) as caught:
        device.execute(opened.session_id, dispatch)
    assert caught.value.code is ErrorCode.PROVIDER_TIMEOUT
    assert device.alive(opened.session_id)
    assert device.query(opened.session_id, dispatch.provider_command_id) is None
    after = device.execute(opened.session_id, _mix(opened))
    assert after.applied and after.step_index == 1
    assert after.evaluation == opened.evaluation


def test_failed_query_is_one_shot_and_preserves_a_lost_response_receipt():
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    device.fail_query_next()
    device.lose_response_next()
    with pytest.raises(LabError) as execute_error:
        device.execute(opened.session_id, dispatch)
    assert execute_error.value.code is ErrorCode.PROVIDER_TIMEOUT
    with pytest.raises(LabError) as query_error:
        device.query(opened.session_id, dispatch.provider_command_id)
    assert query_error.value.code is ErrorCode.PROVIDER_TIMEOUT
    assert device.alive(opened.session_id)
    receipt = device.query(opened.session_id, dispatch.provider_command_id)
    assert receipt.applied and receipt.step_index == 1
    assert device.query(opened.session_id, "never-received") is None
    assert device.execute(opened.session_id, dispatch) == receipt


@pytest.mark.parametrize("hook", ["timeout_and_die_next", "protocol_error_next"])
def test_fatal_execute_hooks_invalidate_only_the_affected_session(hook, monkeypatch):
    device = FakeExtractorDevice()
    opened, other = _open(device), _open(device)
    applied = []
    original_apply = device._apply

    def record_apply(session, dispatch):
        receipt = original_apply(session, dispatch)
        applied.append(receipt)
        return receipt

    monkeypatch.setattr(device, "_apply", record_apply)
    dispatch = _dispatch(opened)
    getattr(device, hook)()
    with pytest.raises(LabError) as caught:
        device.execute(opened.session_id, dispatch)
    timeout = hook == "timeout_and_die_next"
    expected = (
        ErrorCode.PROVIDER_TIMEOUT if timeout else ErrorCode.PROVIDER_PROTOCOL_ERROR
    )
    assert caught.value.code is expected
    assert len(applied) == int(timeout)
    if timeout:
        assert applied[0].applied and applied[0].step_index == 1
    assert not device.alive(opened.session_id)
    for call in (
        lambda: device.execute(opened.session_id, dispatch),
        lambda: device.query(opened.session_id, dispatch.provider_command_id),
        lambda: device.stop(opened.session_id, "test"),
    ):
        with pytest.raises(LabError) as dead:
            call()
        assert dead.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert device.alive(other.session_id)
    after = device.execute(other.session_id, _mix(other))
    assert after.applied and after.step_index == 1


@pytest.mark.parametrize("protocol_error", [False, True])
def test_transport_failure_consumes_all_other_execute_hooks(protocol_error):
    device = FakeExtractorDevice()
    opened, other = _open(device), _open(device)
    device.fail_next(ErrorCode.PRECONDITION_FAILED)
    device.lose_response_next()
    device.timeout_and_die_next()
    device.lose_request_next()
    if protocol_error:
        device.protocol_error_next()
    with pytest.raises(LabError) as caught:
        device.execute(opened.session_id, _dispatch(opened))
    expected = (
        ErrorCode.PROVIDER_PROTOCOL_ERROR
        if protocol_error
        else ErrorCode.PROVIDER_TIMEOUT
    )
    assert caught.value.code is expected
    assert device.alive(opened.session_id) is not protocol_error
    if not protocol_error:
        assert device.query(opened.session_id, "command-1") is None
    after = device.execute(other.session_id, _mix(other))
    assert after.applied and after.step_index == 1


def test_missing_session_consumes_execute_and_query_hooks():
    device = FakeExtractorDevice()
    opened = _open(device)
    device.fail_next(ErrorCode.PRECONDITION_FAILED)
    device.lose_response_next()
    device.lose_request_next()
    device.timeout_and_die_next()
    device.protocol_error_next()
    device.fail_query_next()
    dispatch = _dispatch(opened)
    with pytest.raises(LabError) as execute_error:
        device.execute("missing", dispatch)
    assert execute_error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    with pytest.raises(LabError) as query_error:
        device.query("missing", dispatch.provider_command_id)
    assert query_error.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    receipt = device.execute(opened.session_id, dispatch)
    assert receipt.applied and receipt.step_index == 1
    assert device.query(opened.session_id, dispatch.provider_command_id) == receipt


def test_crash_invalidates_all_sessions_and_all_port_calls():
    device = FakeExtractorDevice()
    opened = _open(device)
    another = _open(device)
    dispatch = _dispatch(opened)
    device.execute(opened.session_id, dispatch)
    device.crash()
    assert not device.alive(opened.session_id)
    assert not device.alive(another.session_id)
    request = SessionOpenRequest(
        "toy-extract-v0", 7, {}, opened.descriptor.capability_revision
    )
    for call in (
        lambda: device.describe("toy-extract-v0"),
        lambda: device.open(request),
        lambda: device.execute(opened.session_id, dispatch),
        lambda: device.query(opened.session_id, dispatch.provider_command_id),
        lambda: device.stop(opened.session_id, "test"),
        lambda: device.close(opened.session_id),
    ):
        with pytest.raises(LabError) as caught:
            call()
        assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE


def test_mismatch_hook_and_actual_revision_check_prevent_open():
    device = FakeExtractorDevice()
    descriptor = device.describe("toy-extract-v0")
    request = SessionOpenRequest(
        descriptor.profile, 7, {}, descriptor.capability_revision
    )
    device.describe_mismatch()
    with pytest.raises(LabError) as caught:
        device.open(request)
    assert caught.value.code is ErrorCode.ADAPTER_MISMATCH
    assert not device.alive("unknown-session")
    opened = device.open(request)
    assert device.alive(opened.session_id)
    with pytest.raises(LabError) as caught:
        device.open(replace(request, expected_capability_revision="wrong"))
    assert caught.value.code is ErrorCode.ADAPTER_MISMATCH
    assert device.alive(opened.session_id)


def test_seeded_action_sequences_are_reproducible_without_global_rng_changes():
    def sequence(seed):
        device = FakeExtractorDevice()
        opened = _open(device, seed)
        observations = [opened.observation]
        evaluations = [opened.evaluation]
        for dispatch in (_dispatch(opened), _mix(opened), _dispatch(opened, "last")):
            receipt = device.execute(opened.session_id, dispatch)
            observations.append(receipt.observation)
            evaluations.append(receipt.evaluation)
        return observations, evaluations

    global_state = random.getstate()
    first = sequence(113)
    assert sequence(113) == first
    assert sequence(114)[1] != first[1]
    assert random.getstate() == global_state


def test_truth_is_isolated_from_sensors_descriptors_errors_and_output(capsys, caplog):
    device = FakeExtractorDevice()
    opened = _open(device)
    applied = device.execute(opened.session_id, _dispatch(opened))
    failed = device.execute(opened.session_id, _dispatch(opened, "fail", value=1000))
    public = json.dumps(
        [
            opened.observation,
            applied.observation,
            opened.descriptor.to_dict(),
            failed.error,
        ]
    )
    for private in (
        "reward",
        "ground_truth",
        "evaluation",
        "moles",
        "toy_solute",
        "volume_L",
    ):
        assert private not in public
    assert "reward" in applied.evaluation
    assert "ground_truth" in applied.evaluation
    assert {c["name"] for c in applied.observation["channels"]} == {"layers", "targets"}
    assert all(
        c["source"] == "simulated_sensor" for c in applied.observation["channels"]
    )
    layers = applied.observation["channels"][0]
    assert layers["shape"] == [2, 10]
    assert len(layers["value"]) == 2 and all(len(row) == 10 for row in layers["value"])
    assert "pressure" not in public
    assert capsys.readouterr() == ("", "")
    assert not caplog.records


def test_receipt_cache_retains_only_the_latest_256_commands():
    device = FakeExtractorDevice()
    opened = _open(device)
    for index in range(257):
        device.fail_next(ErrorCode.PRECONDITION_FAILED)
        receipt = device.execute(opened.session_id, _mix(opened, f"command-{index}"))
        assert not receipt.applied
    # Evicted is not "never received": the fake still knows it took the id.
    with pytest.raises(LabError) as evicted:
        device.query(opened.session_id, "command-0")
    assert evicted.value.code is ErrorCode.OUTCOME_UNKNOWN
    assert device.query(opened.session_id, "never-sent") is None
    assert device.query(opened.session_id, "command-1").step_index == 0
    assert device.query(opened.session_id, "command-256") == receipt
    # And an evicted id is never executed a second time.
    device.fail_next(ErrorCode.PRECONDITION_FAILED)
    device.lose_response_next()
    with pytest.raises(LabError) as again:
        device.execute(opened.session_id, _mix(opened, "command-0"))
    assert again.value.code is ErrorCode.OUTCOME_UNKNOWN
    assert device.execute(opened.session_id, _mix(opened, "fresh")).step_index == 1


def test_returned_nested_receipts_cannot_mutate_cached_evidence():
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    receipt = device.execute(opened.session_id, dispatch)
    saved = receipt.to_dict()
    receipt.observation["channels"][0]["value"][0][0] = 99
    receipt.evaluation["ground_truth"]["vessels"].clear()
    queried = device.query(opened.session_id, dispatch.provider_command_id)
    assert queried.to_dict() == saved
    queried.raw["terminated"] = True
    assert device.execute(opened.session_id, dispatch).to_dict() == saved


def test_concurrent_replays_apply_exactly_once(monkeypatch):
    device = FakeExtractorDevice()
    opened = _open(device)
    dispatch = _dispatch(opened)
    barrier = Barrier(12)
    all_attempted = Event()
    count_lock = Lock()
    original_lock = device._lock
    original_apply = device._apply
    attempts = 0

    class ContendedLock:
        def __enter__(self):
            nonlocal attempts
            with count_lock:
                attempts += 1
                if attempts == 12:
                    all_attempted.set()
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    def apply_after_contention(session, command):
        # No clock-based scheduling assumption: the first application stays
        # in its transaction until all other callers have attempted the lock.
        assert all_attempted.wait(timeout=10)
        return original_apply(session, command)

    monkeypatch.setattr(device, "_lock", ContendedLock())
    monkeypatch.setattr(device, "_apply", apply_after_contention)

    def execute(_):
        barrier.wait(timeout=10)
        return device.execute(opened.session_id, dispatch)

    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(pool.map(execute, range(12)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert receipts[0].step_index == 1
    assert device.execute(opened.session_id, _mix(opened)).step_index == 2


@pytest.mark.parametrize("ending", ["end_action", "max_steps"])
def test_terminal_actions_and_step_limit_stop_further_mutation(ending):
    device = FakeExtractorDevice()
    opened = _open(device)
    if ending == "end_action":
        dispatch = _dispatch(
            opened, operation="end_experiment", source=None, target=None
        )
        receipt = device.execute(opened.session_id, dispatch)
        assert receipt.end_reason is EndReason.END_ACTION
        assert receipt.raw == {"terminated": True, "truncated": False}
    else:
        for index in range(opened.descriptor.limits["max_steps"]):
            dispatch = _mix(opened, f"step-{index}")
            receipt = device.execute(opened.session_id, dispatch)
            assert receipt.applied
        assert receipt.end_reason is EndReason.MAX_STEPS
        assert receipt.raw == {"terminated": False, "truncated": True}
    assert device.execute(opened.session_id, dispatch) == receipt
    refused = device.execute(opened.session_id, _mix(opened, "too-late"))
    assert not refused.applied and refused.error["code"] == "run_ended"
    assert (refused.step_index, refused.sim_time) == (
        receipt.step_index,
        receipt.sim_time,
    )


def test_toy_transfers_conserve_material_and_report_model_time_and_reward():
    device = FakeExtractorDevice()
    opened = _open(device)
    first = device.execute(opened.session_id, _dispatch(opened))
    assert first.evaluation["reward"] == pytest.approx(0.5)
    assert opened.observation["channels"][0]["value"] == [
        [1.0] * 4 + [0.0] * 6,
        [0.0] * 10,
    ]
    assert first.observation["channels"][0]["value"] == [
        [1.0] * 2 + [0.0] * 8,
        [1.0] + [0.0] * 9,
    ]
    drain = _dispatch(
        opened, "drain", operation="drain_layers", target="beaker_1", value=1
    )
    device.execute(opened.session_id, drain)
    device.execute(opened.session_id, _mix(opened, value=2))
    settle = _dispatch(opened, "settle", operation="settle_model", target=None, value=5)
    receipt = device.execute(opened.session_id, settle)
    truth = {v["resource_id"]: v for v in receipt.evaluation["ground_truth"]["vessels"]}
    assert truth["extraction_vessel"]["volume_L"] == pytest.approx(0.1)
    assert truth["beaker_1"]["volume_L"] == pytest.approx(0.1)
    assert truth["beaker_2"]["volume_L"] == pytest.approx(0.2)
    assert sum(v["volume_L"] for v in truth.values()) == pytest.approx(2.4)
    initial = opened.evaluation["ground_truth"]["vessels"]
    assert sum(v["moles"]["toy_solute"] for v in truth.values()) == pytest.approx(
        sum(v["moles"]["toy_solute"] for v in initial)
    )
    assert receipt.sim_time == 9.0 and receipt.step_index == 4
    assert receipt.observation["sim_time"] == receipt.sim_time
    assert opened.descriptor.time == {
        "unit": "model_time",
        "wall_clock_equivalent": None,
    }
    assert all(
        cap.scope is CapabilityScope.SIMULATION_ONLY
        for cap in opened.descriptor.capabilities
        if cap.operation in {"mix_model", "settle_model", "drain_layers"}
    )


def test_sessions_have_independent_receipts_fences_and_model_state():
    device = FakeExtractorDevice()
    first, second = _open(device), _open(device)
    assert first.session_id != second.session_id
    device.execute(first.session_id, _dispatch(first, token=20))
    assert device.query(second.session_id, "command-1") is None
    receipt = device.execute(second.session_id, _dispatch(second, token=1))
    assert receipt.applied and receipt.step_index == 1
    assert receipt.evaluation["reward"] == pytest.approx(0.5)
    device.close(first.session_id)
    assert device.alive(second.session_id)


def test_profile_options_and_failure_codes_are_strictly_validated():
    device = FakeExtractorDevice()
    with pytest.raises(LabError) as caught:
        device.describe("unsupported")
    assert caught.value.code is ErrorCode.DEVICE_NOT_FOUND
    descriptor = device.describe("toy-extract-v0")
    with pytest.raises(LabError) as caught:
        device.open(
            SessionOpenRequest(
                descriptor.profile,
                1,
                {"real_device": True},
                descriptor.capability_revision,
            )
        )
    assert caught.value.code is ErrorCode.INVALID_PARAMETERS
    with pytest.raises(LabError) as caught:
        device.fail_next("made_up_code")
    assert caught.value.code is ErrorCode.INVALID_PARAMETERS
    # A transport failure is not a refusal the device could put in a receipt.
    with pytest.raises(LabError) as caught:
        device.fail_next(ErrorCode.PROVIDER_TIMEOUT)
    assert caught.value.code is ErrorCode.INVALID_PARAMETERS


def test_closing_a_session_twice_is_not_an_error():
    device = FakeExtractorDevice()
    opened = _open(device)
    device.close(opened.session_id)
    device.close(opened.session_id)
    assert not device.alive(opened.session_id)
