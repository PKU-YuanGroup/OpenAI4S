"""Real Store + fake-device proofs of process-level Lab invariants."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.lab.manager import LabLimits, LabManager
from openai4s.lab.models import CommandOrigin, ErrorCode, LabCaller, LabError
from openai4s.lab.runtime import build_lab_manager
from openai4s.storage.lab import LabLedger
from openai4s.store import Store


class CountingDevice(FakeExtractorDevice):
    def __init__(self):
        super().__init__()
        self.executions = 0
        self.queries = 0
        self.opens = []
        self.closed = []

    def open(self, request):
        self.opens.append(request)
        return super().open(request)

    def execute(self, session_id, dispatch):
        self.executions += 1
        return super().execute(session_id, dispatch)

    def query(self, session_id, command_id):
        self.queries += 1
        return super().query(session_id, command_id)

    def close(self, session_id):
        self.closed.append(session_id)
        return super().close(session_id)


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path / "manager.sqlite")
    now = [1000]
    # Same real Store connection, with deterministic budget timestamps.
    ledger = LabLedger(store._conn, store._lock, clock_ms=lambda: now[0])
    registry = DeviceRegistry()
    devices = []

    def factory():
        device = CountingDevice()
        devices.append(device)
        return device

    registry.register(replace(fake_registration(), port_factory=factory))
    manager = LabManager(
        lambda: ledger,
        registry,
        instance_id="daemon-a",
        clock_ms=lambda: now[0],
        limits=LabLimits(stop_wait_seconds=0),
    )
    caller = LabCaller("root", "frame", None, CommandOrigin.MANUAL_UI, None, "agent")
    yield manager, caller, ledger, devices, now, registry, store
    manager.close_all("test teardown")
    store.close()


def create(rig, **changes):
    manager, caller = rig[:2]
    return manager.create_run(
        caller,
        {
            "device_id": "fake.extractor.01",
            "profile": "toy-extract-v0",
            "seed": 7,
            **changes,
        },
    )


def command(run_id, key="first", revision=0, **changes):
    return {
        "run_id": run_id,
        "operation": "mix_model",
        "source": "extraction_vessel",
        "target": None,
        "parameters": {"duration": {"value": 1, "unit": "model_time"}},
        "expected_revision": revision,
        "idempotency_key": key,
        **changes,
    }


def error(code, call):
    with pytest.raises(LabError) as caught:
        call()
    assert caught.value.code == ErrorCode(code)
    return caught.value


def test_create_seed_and_duplicate_survive_manager_restart(rig):
    manager, caller, ledger, devices, now, registry, _ = rig
    first = create(rig, seed=None, idempotency_key="create")
    run_id = first["run"]["run_id"]
    row = ledger.get_run(run_id)
    assert type(row["seed"]) is int and 0 <= row["seed"] < 2**31
    assert row["config"]["seed"] == devices[0].opens[0].seed == row["seed"]
    assert create(rig, seed=None, idempotency_key="create")["run"]["run_id"] == run_id
    another = LabManager(
        lambda: ledger, registry, instance_id="daemon-b", clock_ms=lambda: now[0]
    )
    replay = another.create_run(
        caller,
        {
            "device_id": "fake.extractor.01",
            "profile": "toy-extract-v0",
            "idempotency_key": "create",
        },
    )
    assert replay["run"]["run_id"] == run_id and len(devices) == 1
    error(
        "idempotency_conflict",
        lambda: create(rig, seed=row["seed"] + 1, idempotency_key="create"),
    )
    assert first["observation"]["sequence"] == 0


def test_dispatch_persists_intent_and_replays_without_calling_device(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    device = devices[0]
    execute = device.execute

    def checked(session_id, dispatch):
        row = ledger.get_command(dispatch.provider_command_id)
        assert row is not None and row["state"] == "dispatching"
        assert row["fencing_token"] is not None
        assert all(f"#{run_id}:" in r for r in row["resources"])
        return execute(session_id, dispatch)

    monkeypatch.setattr(device, "execute", checked)
    request = command(run_id)
    first = manager.execute(caller, request)
    second = manager.execute(caller, command(run_id, "second", 1))
    assert second["run"]["revision"] == 2
    repeated = manager.execute(caller, {**request, "expected_revision": 2})
    assert repeated["command"] == first["command"]
    assert repeated["observation"] == first["observation"]
    assert device.executions == 2
    error(
        "idempotency_conflict",
        lambda: manager.execute(
            caller,
            command(
                run_id, parameters={"duration": {"value": 2, "unit": "model_time"}}
            ),
        ),
    )
    stale = manager.execute(caller, command(run_id, "stale", 0))
    assert (
        stale["command"]["state"] == "rejected"
        and stale["command"]["error_code"] == "stale_revision"
    )
    assert device.executions == 2


@pytest.mark.parametrize("stage", ["insert_command", "begin_dispatch"])
def test_persistence_failure_before_dispatch_never_calls_device(
    rig, monkeypatch, stage
):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, stage, unavailable)
        error(
            "persistence_unavailable", lambda: manager.execute(caller, command(run_id))
        )
    assert devices[0].executions == 0
    rows = ledger.list_commands(run_id)
    assert (
        not rows if stage == "insert_command" else rows[0]["state"] == "not_dispatched"
    )


def test_post_dispatch_write_failure_blocks_and_reconciles(rig, monkeypatch):
    manager, caller, ledger, devices, now, registry, _ = rig
    run_id = create(rig)["run"]["run_id"]

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "record_receipt", unavailable)
        exc = error(
            "persistence_unavailable", lambda: manager.execute(caller, command(run_id))
        )
        assert "may have executed" in str(exc)
    refused = manager.execute(caller, command(run_id, "blocked", 1))
    assert refused["command"]["state"] == "rejected"
    assert refused["command"]["error_code"] == "resource_quarantined"
    assert devices[0].executions == 1
    row = ledger.list_commands(run_id)[0]
    assert row["state"] == "dispatching"
    replacement = build_lab_manager(
        ledger_provider=lambda: ledger,
        registry=registry,
        instance_id="daemon-b",
        clock_ms=lambda: now[0],
    )
    assert replacement.startup_summary["outcome_unknown"] == 1
    assert ledger.get_command(row["command_id"])["state"] == "outcome_unknown"
    assert ledger.get_run(run_id)["end_reason"] == "provider_lost"


def test_lost_response_queries_receipt_once(rig):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()
    result = manager.execute(caller, command(run_id))
    assert result["command"]["state"] == "succeeded" and result["run"]["revision"] == 1
    assert devices[0].queries == devices[0].executions == 1
    assert manager.execute(caller, command(run_id))["command"] == result["command"]
    assert devices[0].executions == 1


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"operation": "not_supported"}, "unsupported_action"),
        ({"parameters": {}}, "invalid_parameters"),
        ({"parameters": {"duration": {"value": 1, "unit": "mL"}}}, "unit_mismatch"),
    ],
)
def test_semantic_refusals_are_durable_and_idempotent(rig, changes, code):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    request = command(run_id, **changes)
    result = manager.execute(caller, request)
    assert (
        result["command"]["state"] == "rejected"
        and result["command"]["error_code"] == code
    )
    assert manager.execute(caller, request)["command"] == result["command"]
    assert ledger.get_run(run_id)["command_count"] == 1 and devices[0].executions == 0


@pytest.mark.parametrize(
    "budget", ["max_steps", "max_commands", "max_wall_ms", "max_consecutive_failures"]
)
def test_budget_enforcement(rig, budget):
    manager, caller, ledger, devices, now = rig[:5]
    run_id = create(rig, budgets={budget: 1})["run"]["run_id"]
    if budget == "max_wall_ms":
        now[0] += 1
    elif budget == "max_consecutive_failures":
        rejected = manager.execute(caller, command(run_id, operation="not_supported"))
        # The published counter is the one admission enforces (CONTRACT §7).
        assert rejected["run"]["consecutive_failures"] == 1
    else:
        manager.execute(caller, command(run_id))
    refused = manager.execute(
        caller, command(run_id, "exhausted", ledger.get_run(run_id)["revision"])
    )
    assert refused["command"]["error_code"] == "budget_exhausted"
    assert devices[0].executions == (
        1 if budget in {"max_steps", "max_commands"} else 0
    )
    # Every budget is monotone, so exhausting any of them ends the run and
    # releases its provider instead of refusing commands forever.
    assert refused["run"]["status"] == "ended"
    assert refused["run"]["end_reason"] == "budget_exhausted" and devices[0].closed


def test_scope_recovery_and_observe_never_touch_device(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    other = replace(caller, root_frame_id="other")
    for alternate in (other, replace(caller, owner_user_id="different")):
        for call in (
            lambda: manager.observe(alternate, run_id),
            lambda: manager.status(alternate, run_id),
            lambda: manager.stop(alternate, run_id),
            lambda: manager.execute(alternate, command(run_id)),
        ):
            error("run_not_found", call)
        assert (
            not manager.list_runs(alternate) and not manager.events(alternate)["events"]
        )
    recovery = replace(caller, execution_owner="recovery")
    for call in (
        lambda: manager.create_run(recovery, {}),
        lambda: manager.execute(recovery, {}),
        lambda: manager.stop(recovery, run_id),
    ):
        error("replay_forbidden", call)

    def forbidden(*args, **kwargs):
        pytest.fail("observation touched the device")

    for name in ("execute", "query", "alive", "describe", "stop", "close"):
        monkeypatch.setattr(devices[0], name, forbidden)
    assert manager.observe(caller, run_id, full=True)["observation"]["sequence"] == 0
    monkeypatch.undo()


def test_parallel_dispatch_rejects_competitor_and_replays_inflight(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    entered, release = Event(), Event()
    execute = devices[0].execute

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return execute(*args)

    monkeypatch.setattr(devices[0], "execute", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(manager.execute, caller, command(run_id))
        try:
            assert entered.wait(10)
            replay = manager.execute(caller, command(run_id))
            assert replay["command"]["state"] == "dispatching"
            competitor = manager.execute(caller, command(run_id, "competitor"))
            assert (
                competitor["command"]["state"] == "rejected"
                and competitor["command"]["error_code"] == "resource_busy"
            )
        finally:
            release.set()
        assert (
            first.result()["command"]["command_id"] == replay["command"]["command_id"]
        )
    assert devices[0].executions == 1


def test_stop_inflight_does_not_wait_for_command_lock(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    entered, release = Event(), Event()
    execute = devices[0].execute

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return execute(*args)

    monkeypatch.setattr(devices[0], "execute", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute, caller, command(run_id))
        try:
            assert entered.wait(10)
            result = manager.stop(caller, run_id)
            assert result["run"]["end_reason"] == "stopped"
            assert ledger.list_commands(run_id)[0]["state"] == "outcome_unknown"
            assert devices[0].closed
        finally:
            release.set()
        error("outcome_unknown", future.result)
    assert ledger.get_run(run_id)["end_reason"] == "stopped"


def test_terminal_receipt_idle_reaping_and_live_limit(rig):
    manager, caller, ledger, devices, now = rig[:5]
    first = create(rig, budgets={"idle_timeout_ms": 1})["run"]["run_id"]
    now[0] += 1
    assert (
        manager.list_runs(caller)[0]["end_reason"] == "idle_timeout"
        and devices[0].closed
    )
    run_id = create(rig)["run"]["run_id"]
    result = manager.execute(
        caller, command(run_id, operation="end_experiment", source=None, parameters={})
    )
    assert result["run"]["end_reason"] == "end_action" and devices[-1].closed
    replay = manager.execute(
        caller, command(run_id, operation="end_experiment", source=None, parameters={})
    )
    assert replay["command"] == result["command"]
    refused = manager.execute(caller, command(run_id, "later"))
    assert refused["command"]["error_code"] == "run_ended"
    for _ in range(4):
        create(rig)
    error("provider_unavailable", lambda: create(rig))
    assert len(manager._live) == 4 and len(devices) == 6
    assert ledger.get_run(first)["end_reason"] == "idle_timeout"


def test_public_returns_exclude_private_provider_and_ledger_values(rig):
    manager, caller, ledger, devices = rig[:4]
    opened = create(rig)
    run_id = opened["run"]["run_id"]
    result = manager.execute(caller, command(run_id))
    values = [
        opened,
        result,
        manager.observe(caller, run_id),
        manager.status(caller, run_id, result["command"]["command_id"]),
        manager.list_runs(caller),
        manager.events(caller),
        manager.describe(caller, "fake.extractor.01"),
        manager.list_devices(caller),
        manager.stop(caller, run_id),
    ]
    forbidden = {
        "reward",
        "ground_truth",
        "evaluation",
        "moles",
        "provider_action",
        "provider_action_json",
        "fencing_token",
        "fencing_tokens",
        "leases",
        "approval_ref",
        "daemon_instance",
    }

    def scan(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    scan(values)
    assert ledger.list_evaluations(run_id)
    assert manager.list_devices(caller)[0]["available"] is None


def test_shutdown_and_session_deletion_close_only_owned_providers(rig):
    manager, caller, ledger, devices = rig[:4]
    first = create(rig)["run"]["run_id"]
    other = replace(caller, root_frame_id="other")
    second = manager.create_run(
        other, {"device_id": "fake.extractor.01", "profile": "toy-extract-v0"}
    )["run"]["run_id"]
    manager.on_session_deleted(caller.root_frame_id)
    assert devices[0].closed and not devices[1].closed
    assert first not in manager._live and second in manager._live
    manager.close_all("shutdown")
    assert devices[1].closed and ledger.get_run(second)["end_reason"] == "provider_lost"


@pytest.mark.parametrize(
    "hook,expected",
    [
        ("lose_request_next", "not_dispatched"),
        ("timeout_and_die_next", "outcome_unknown"),
        ("protocol_error_next", "outcome_unknown"),
        ("crash", "outcome_unknown"),
    ],
)
def test_transport_hooks_reconcile_without_resending(rig, hook, expected):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    getattr(devices[0], hook)()
    if expected == "outcome_unknown":
        exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
        row = ledger.get_command(exc.details["command_id"])
        assert ledger.get_run(run_id)["end_reason"] == "provider_lost"
        assert run_id not in manager._live
    else:
        result = manager.execute(caller, command(run_id))
        row = result["command"]
        assert result["run"]["status"] == "ready" and result["run"]["revision"] == 0
    assert row["state"] == expected
    replay = manager.execute(caller, command(run_id))
    assert replay["command"]["command_id"] == row["command_id"]
    assert devices[0].executions == 1


def test_unknown_quarantines_until_status_queries_receipt(rig):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()
    devices[0].fail_query_next()
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    command_id = exc.details["command_id"]
    assert ledger.get_run(run_id)["status"] == "quarantined"
    assert (
        manager.execute(caller, command(run_id))["command"]["state"]
        == "outcome_unknown"
    )
    assert devices[0].queries == 1 and devices[0].executions == 1
    rejected = manager.execute(caller, command(run_id, "blocked", 1))
    assert rejected["command"]["error_code"] == "resource_quarantined"
    result = manager.status(caller, run_id, command_id)
    assert (
        result["command"]["state"] == "succeeded" and result["run"]["status"] == "ready"
    )
    assert result["run"]["revision"] == 1
    assert devices[0].queries == 2 and devices[0].executions == 1
    assert manager.status(caller, run_id, command_id) == result
    assert devices[0].queries == 2


def test_authoritative_nonreceipt_releases_quarantine(rig):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_request_next()
    devices[0].fail_query_next()
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    assert ledger.get_run(run_id)["status"] == "quarantined"
    result = manager.status(caller, run_id, exc.details["command_id"])
    assert (
        result["command"]["state"] == "not_dispatched"
        and result["run"]["status"] == "ready"
    )
    assert result["run"]["revision"] == 0 and devices[0].executions == 1


def test_evicted_receipt_stays_unknown_even_after_status(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()

    def evicted(*args):
        raise LabError(ErrorCode.OUTCOME_UNKNOWN, "Receipt is no longer retained")

    monkeypatch.setattr(devices[0], "query", evicted)
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    row = manager.status(caller, run_id, exc.details["command_id"])
    assert (
        row["command"]["state"] == "outcome_unknown"
        and row["run"]["status"] == "quarantined"
    )
    assert row["command"]["receipt"] is None and devices[0].executions == 1


@pytest.mark.parametrize("code", ["precondition_failed", "unsupported_action"])
@pytest.mark.parametrize("rejected", [False, True])
def test_explicit_refusal_is_a_failed_command_not_unknown(
    rig, monkeypatch, code, rejected
):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    original = devices[0]._failure_receipt
    monkeypatch.setattr(
        devices[0],
        "_failure_receipt",
        lambda session, dispatch, code, **kwargs: original(
            session, dispatch, code, rejected=rejected
        ),
    )
    devices[0].fail_next(code)
    devices[0].lose_response_next()
    result = manager.execute(caller, command(run_id))
    assert (
        result["command"]["state"] == "failed"
        and result["command"]["error_code"] == code
    )
    assert result["run"]["revision"] == 0 and result["run"]["status"] == "ready"
    assert result["run"]["consecutive_failures"] == 1
    assert devices[0].queries == 1 and devices[0].executions == 1


@pytest.mark.parametrize("failure", ["mismatch", "observation_write"])
def test_create_failure_closes_session_and_records_failure(rig, monkeypatch, failure):
    manager, caller, ledger, devices = rig[:4]
    if failure == "mismatch":
        original = CountingDevice.open

        def mismatch(self, request):
            self.describe_mismatch()
            return original(self, request)

        monkeypatch.setattr(CountingDevice, "open", mismatch)
    else:

        def unavailable(*args, **kwargs):
            raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected write failure")

        monkeypatch.setattr(ledger, "append_initial_observation", unavailable)
    error(
        "adapter_mismatch" if failure == "mismatch" else "persistence_unavailable",
        lambda: create(rig),
    )
    run = ledger.list_runs(caller.root_frame_id)[0]
    assert run["status"] == "failed" and run["end_reason"] == "create_failed"
    assert not manager._live and not manager._opening
    if failure == "observation_write":
        assert devices[0].closed


def test_store_generation_is_resolved_for_every_operation(rig):
    manager, caller, ledger, devices, now, registry, store = rig
    current = [store]
    manager._ledger_provider = lambda: current[0].lab
    run_id = create(rig)["run"]["run_id"]
    store.close()
    current[0] = Store(store.db_path)
    try:
        assert (
            manager.execute(caller, command(run_id))["command"]["state"] == "succeeded"
        )
        assert manager.observe(caller, run_id)["run"]["revision"] == 1
        manager.close_all("test complete")
    finally:
        for live in list(manager._live.values()):
            live.port.close(live.session_id)
        manager._live.clear()
        current[0].close()


def test_creation_reserves_slots_and_stop_cancels_late_open(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    manager._limits = LabLimits(max_live_providers=1, stop_wait_seconds=0)
    entered, release = Event(), Event()
    original = CountingDevice.open

    def blocked(self, request):
        entered.set()
        assert release.wait(10)
        return original(self, request)

    monkeypatch.setattr(CountingDevice, "open", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(create, rig, idempotency_key="slow-open")
        try:
            assert entered.wait(10)
            run_id = ledger.list_runs(caller.root_frame_id)[0]["run_id"]
            duplicate = create(rig, idempotency_key="slow-open")
            assert (
                duplicate["run"]["run_id"] == run_id
                and duplicate["run"]["status"] == "creating"
            )
            error("provider_unavailable", lambda: create(rig))
            result = manager.stop(caller, run_id)
            assert result["run"]["end_reason"] == "stopped"
        finally:
            release.set()
        error("run_ended", pending.result)
    assert len(devices) == 1 and devices[0].closed and not manager._live


@pytest.mark.parametrize("received", [True, False])
def test_stop_waits_for_receipt_or_authoritative_nonreceipt(rig, monkeypatch, received):
    manager, caller, ledger, devices = rig[:4]
    manager._limits = LabLimits(stop_wait_seconds=10)
    run_id = create(rig)["run"]["run_id"]
    entered, release = Event(), Event()
    original = devices[0].execute
    completion = manager._live[run_id].done
    if not received:
        devices[0].lose_request_next()

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    class ReceiptWait:
        def clear(self):
            completion.clear()

        def set(self):
            completion.set()

        def wait(self, timeout):
            assert ledger.list_commands(run_id)[0]["state"] == "stop_requested"
            # Release the provider only once stop actually waits for evidence.
            release.set()
            return completion.wait(timeout)

    monkeypatch.setattr(devices[0], "execute", blocked)
    monkeypatch.setattr(manager._live[run_id], "done", ReceiptWait())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.execute, caller, command(run_id))
        try:
            assert entered.wait(10)
            result = manager.stop(caller, run_id)
        finally:
            release.set()
        assert pending.result()["command"]["state"] == (
            "succeeded" if received else "not_dispatched"
        )
    assert result["run"]["end_reason"] == "stopped"
    assert result["run"]["revision"] == int(received)
    assert ledger.list_commands(run_id)[0]["state"] == (
        "succeeded" if received else "not_dispatched"
    )


def test_postwrite_failure_shutdown_preserves_unknown_intent(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "record_receipt", unavailable)
        error(
            "persistence_unavailable", lambda: manager.execute(caller, command(run_id))
        )
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "mark_outcome_unknown", unavailable)
        error("persistence_unavailable", lambda: manager.close_all("shutdown"))
    assert ledger.get_run(run_id)["status"] == "busy" and devices[0].closed
    manager.close_all("shutdown retry")
    assert ledger.list_commands(run_id)[0]["state"] == "outcome_unknown"
    assert ledger.get_run(run_id)["end_reason"] == "provider_lost"


@pytest.mark.parametrize(
    "changes",
    [
        {"unexpected": True},
        {"parameters": []},
        {"expected_revision": True},
        {"idempotency_key": ""},
    ],
)
def test_structurally_invalid_command_has_no_ledger_entry(rig, changes):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    error(
        "invalid_parameters",
        lambda: manager.execute(caller, command(run_id, **changes)),
    )
    assert ledger.list_commands(run_id) == [] and devices[0].executions == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"unexpected": True},
        {"seed": True},
        {"options": []},
        {"budgets": {"max_steps": 0}},
        {"idempotency_key": ""},
    ],
)
def test_structurally_invalid_create_does_not_open_or_persist(rig, changes):
    manager, caller, ledger, devices = rig[:4]
    error("invalid_parameters", lambda: create(rig, **changes))
    assert ledger.list_runs(caller.root_frame_id) == [] and devices == []


def test_profile_step_limit_caps_requested_budget(rig):
    result = create(rig, budgets={"max_steps": 100})
    assert (
        result["run"]["budgets"]["max_steps"]
        == result["descriptor"]["limits"]["max_steps"]
        == 50
    )


def test_recovery_status_does_not_query_unknown_or_reap_idle(rig):
    manager, caller, ledger, devices, now = rig[:5]
    run_id = create(rig, budgets={"idle_timeout_ms": 1})["run"]["run_id"]
    devices[0].lose_response_next()
    devices[0].fail_query_next()
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    now[0] += 100
    recovery = replace(caller, execution_owner="recovery")
    result = manager.status(recovery, run_id, exc.details["command_id"])
    assert (
        result["command"]["state"] == "outcome_unknown"
        and result["run"]["status"] == "quarantined"
    )
    assert devices[0].queries == 1 and not devices[0].closed


def test_create_readback_failure_preserves_committed_live_session(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    original = ledger.latest_observation
    attempts = 0

    def fail_once(run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected read failure")
        return original(run_id)

    monkeypatch.setattr(ledger, "latest_observation", fail_once)
    error("persistence_unavailable", lambda: create(rig, idempotency_key="readback"))
    assert len(devices) == 1 and not devices[0].closed
    repeated = create(rig, idempotency_key="readback")
    assert (
        repeated["run"]["status"] == "ready"
        and repeated["observation"]["sequence"] == 0
    )
    assert len(devices) == 1
    assert (
        manager.execute(caller, command(repeated["run"]["run_id"]))["command"]["state"]
        == "succeeded"
    )


def test_deletion_keeps_closing_provider_in_live_limit(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    manager._limits = LabLimits(max_live_providers=1, stop_wait_seconds=0)
    create(rig)
    entered, release = Event(), Event()
    original = devices[0].close

    def blocked(session_id):
        entered.set()
        assert release.wait(10)
        original(session_id)

    monkeypatch.setattr(devices[0], "close", blocked)
    # Another session: the deleted one is tombstoned (it may never create again).
    elsewhere = (manager, replace(caller, root_frame_id="another-session")) + rig[2:]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.on_session_deleted, caller.root_frame_id)
        try:
            assert entered.wait(10)
            refused = error("provider_unavailable", lambda: create(elsewhere))
            assert "limit" in refused.message
            assert len(devices) == 1
        finally:
            release.set()
        pending.result()
    assert not manager._live
    assert create(elsewhere)["run"]["status"] == "ready" and len(devices) == 2


# --- W2 merge: review findings closed by the integrator -----------------------


def unknown_command(rig):
    """A received command whose receipt the manager could not obtain."""
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()
    devices[0].fail_query_next()
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    assert ledger.get_run(run_id)["status"] == "quarantined"
    return run_id, exc.details["command_id"]


@pytest.mark.parametrize("ending", ["idle_status", "stop", "close_all"])
def test_ending_a_run_settles_unknown_outcomes_first(rig, ending):
    # Closing the session destroys the only witness. Every way of ending a run
    # with a live session asks once before closing; nothing is resent.
    manager, caller, ledger, devices, now = rig[:5]
    run_id, command_id = unknown_command(rig)
    if ending == "idle_status":
        now[0] += ledger.get_run(run_id)["budgets"]["idle_timeout_ms"]
        manager.status(caller, run_id, command_id)
        reason = "idle_timeout"
    elif ending == "stop":
        manager.stop(caller, run_id)
        reason = "stopped"
    else:
        manager.close_all("shutdown")
        reason = "provider_lost"
    assert ledger.get_command(command_id)["state"] == "succeeded"
    run = ledger.get_run(run_id)
    assert (run["status"], run["end_reason"], run["revision"]) == ("ended", reason, 1)
    assert devices[0].executions == 1 and devices[0].queries == 2
    assert devices[0].closed


def test_status_on_a_dead_provider_ends_the_run_and_keeps_unknown(rig):
    manager, caller, ledger, devices = rig[:4]
    run_id, command_id = unknown_command(rig)
    devices[0].crash()
    result = manager.status(caller, run_id, command_id)
    assert result["command"]["state"] == "outcome_unknown"
    assert (result["run"]["status"], result["run"]["end_reason"]) == (
        "ended",
        "provider_lost",
    )
    assert devices[0].executions == 1


def test_a_port_raising_outside_the_vocabulary_is_a_lost_provider(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    execute = devices[0].execute

    def broken(session_id, dispatch):
        execute(session_id, dispatch)
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(devices[0], "execute", broken)
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    assert ledger.get_command(exc.details["command_id"])["state"] == "outcome_unknown"
    run = ledger.get_run(run_id)
    assert (run["status"], run["end_reason"]) == ("ended", "provider_lost")
    assert devices[0].closed


def test_a_live_session_answers_for_any_port_error(rig, monkeypatch):
    # An encode failure raised before a byte was sent (W2-B keeps the session)
    # must not end the run: the live session says it never received it.
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    execute = devices[0].execute
    calls = []

    def unsendable(session_id, dispatch):
        calls.append(dispatch.provider_command_id)
        if len(calls) == 1:
            raise LabError(ErrorCode.INVALID_PARAMETERS, "cannot be encoded")
        return execute(session_id, dispatch)

    monkeypatch.setattr(devices[0], "execute", unsendable)
    first = manager.execute(caller, command(run_id))
    assert first["command"]["state"] == "not_dispatched"
    assert first["run"]["status"] == "ready" and devices[0].queries == 1
    second = manager.execute(caller, command(run_id, "second"))
    assert second["command"]["state"] == "succeeded"


def test_a_query_that_kills_the_provider_ends_the_run(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()

    def dying(*args):
        devices[0].crash()
        raise LabError(ErrorCode.PROVIDER_UNAVAILABLE, "gone")

    monkeypatch.setattr(devices[0], "query", dying)
    exc = error("outcome_unknown", lambda: manager.execute(caller, command(run_id)))
    assert ledger.get_command(exc.details["command_id"])["state"] == "outcome_unknown"
    assert ledger.get_run(run_id)["end_reason"] == "provider_lost"


def test_a_failed_unknown_write_says_the_device_may_have_executed(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    devices[0].lose_response_next()
    devices[0].fail_query_next()

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "mark_outcome_unknown", unavailable)
        exc = error(
            "persistence_unavailable", lambda: manager.execute(caller, command(run_id))
        )
    assert "may have executed" in exc.message and exc.details["command_id"]


def test_stop_between_admission_and_dispatch_never_reaches_the_device(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    get_command = ledger.get_command

    def stopping_meanwhile(command_id):
        row = get_command(command_id)
        if row is not None and row["state"] == "admitted":
            # What a concurrent stop does before it ends the run.
            manager._live[run_id].stopping = True
        return row

    monkeypatch.setattr(ledger, "get_command", stopping_meanwhile)
    result = manager.execute(caller, command(run_id))
    assert result["command"]["state"] == "not_dispatched"
    assert devices[0].executions == 0


def test_ending_a_run_resolves_admitted_intents(rig):
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    # An admitted intent the stop overtook before begin_dispatch.
    ledger.insert_command(
        {
            "command_id": "labcmd-admitted01",
            "run_id": run_id,
            "idempotency_key": "admitted",
            "request_hash": "h",
            "operation": "mix_model",
            "request": {},
            "origin": "manual_ui",
            "state": "admitted",
        }
    )
    manager.stop(caller, run_id)
    row = ledger.get_command("labcmd-admitted01")
    assert (row["state"], row["error_code"]) == ("not_dispatched", "run_ended")


def test_idle_cleanup_never_reaps_a_run_with_a_command_in_flight(rig, monkeypatch):
    manager, caller, ledger, devices, now = rig[:5]
    run_id = create(rig)["run"]["run_id"]
    entered, release = Event(), Event()
    execute = devices[0].execute

    def slow(session_id, dispatch):
        entered.set()
        release.wait(10)
        return execute(session_id, dispatch)

    monkeypatch.setattr(devices[0], "execute", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.execute, caller, command(run_id))
        assert entered.wait(10)
        now[0] += ledger.get_run(run_id)["budgets"]["idle_timeout_ms"] * 2
        manager.list_devices(caller)
        assert ledger.get_run(run_id)["status"] == "busy"
        release.set()
        assert pending.result(10)["command"]["state"] == "succeeded"
    assert ledger.get_run(run_id)["status"] == "ready" and not devices[0].closed


def test_events_and_runs_page_over_what_the_caller_may_see(rig):
    manager, caller, ledger, devices, now = rig[:5]
    bob = replace(caller, owner_user_id="bob")
    alice = replace(caller, owner_user_id="alice")
    alice_run = create((manager, alice) + rig[2:])["run"]["run_id"]
    now[0] += 1  # bob's run is strictly the newest: it heads an unfiltered page
    create((manager, bob) + rig[2:])
    first = manager.events(bob, after_seq=0, limit=2)
    assert first["events"] and all(e["run_id"] != alice_run for e in first["events"])
    assert first["next_after_seq"] >= first["events"][-1]["event_seq"]
    seen = list(first["events"])
    page = first
    while page["events"]:
        page = manager.events(bob, after_seq=page["next_after_seq"], limit=2)
        seen += page["events"]
    assert seen and {e["run_id"] for e in seen} != {alice_run}
    assert [r["run_id"] for r in manager.list_runs(alice, limit=1)] == [alice_run]
    for bad in ({"after_seq": -1}, {"limit": 0}, {"limit": True}):
        error("invalid_parameters", lambda: manager.events(bob, **bad))


def test_refusals_say_how_to_correct_the_request(rig):
    manager, caller, ledger = rig[:3]
    created = create(rig)
    run_id = created["run"]["run_id"]
    capability = next(
        c
        for c in created["descriptor"]["capabilities"]
        if c["operation"] == "mix_model"
    )
    allowed = capability["parameters"]["duration"]["allowed"]
    refused = manager.execute(
        caller,
        command(
            run_id,
            parameters={"duration": {"value": max(allowed) + 7, "unit": "model_time"}},
        ),
    )
    assert refused["command"]["error_code"] == "unsupported_action"
    assert str(allowed).replace(" ", "") in refused["command"]["error"].replace(" ", "")
    run_id = create(rig, budgets={"max_commands": 1}, idempotency_key="tiny")["run"][
        "run_id"
    ]
    manager.execute(caller, command(run_id))
    exhausted = manager.execute(caller, command(run_id, "more", 1))
    assert "max_commands" in exhausted["command"]["error"]


def test_an_unnormalizable_replay_ignores_key_and_revision(rig):
    manager, caller = rig[:2]
    run_id = create(rig)["run"]["run_id"]
    first = manager.execute(caller, command(run_id, "bad", 0, operation="nope"))
    again = manager.execute(caller, command(run_id, "bad", 3, operation="nope"))
    assert again["command"]["command_id"] == first["command"]["command_id"]


def test_budgets_can_only_be_tightened(rig):
    loose = create(
        rig,
        budgets={
            "max_steps": 10**6,
            "max_commands": 10**6,
            "max_wall_ms": 10**12,
            "max_consecutive_failures": 10**6,
            "idle_timeout_ms": 10**12,
        },
    )["run"]["budgets"]
    assert loose == {
        "max_steps": 50,
        "max_commands": 200,
        "max_wall_ms": 30 * 60 * 1000,
        "max_consecutive_failures": 3,
        "idle_timeout_ms": 60 * 60 * 1000,
    }
    tight = create(rig, budgets={"max_commands": 5}, idempotency_key="tight")
    assert tight["run"]["budgets"]["max_commands"] == 5
    assert tight["run"]["budgets"]["max_steps"] == 50


def test_read_views_are_scoped_projected_and_ledger_only(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    created = create(rig)
    run_id = created["run"]["run_id"]
    manager.execute(caller, command(run_id))
    manager.execute(caller, command(run_id, "second", 1))

    def forbidden(*args, **kwargs):
        raise AssertionError("read views never touch the device")

    recovery = replace(caller, execution_owner="recovery")
    with monkeypatch.context() as patch:
        for name in ("execute", "query", "stop", "close", "alive"):
            patch.setattr(devices[0], name, forbidden)
        for who in (caller, recovery):
            assert manager.describe_run(who, run_id) == created["descriptor"]
            page = manager.commands(who, run_id, after_seq=0, limit=1)
            assert [c["seq"] for c in page["commands"]] == [1]
            assert page["next_after_seq"] == 1
            rest = manager.commands(who, run_id, after_seq=1)
            assert [c["seq"] for c in rest["commands"]] == [2]
            assert "fencing_token" not in rest["commands"][0]
            observed = manager.observations(who, run_id, after_sequence=0, full=True)
            assert [o["sequence"] for o in observed["observations"]] == [1, 2]
            assert observed["next_after_sequence"] == 2
    other = replace(caller, root_frame_id="other-root")
    for call in (
        lambda: manager.describe_run(other, run_id),
        lambda: manager.commands(other, run_id),
        lambda: manager.observations(other, run_id),
    ):
        error("run_not_found", call)
    for call in (
        lambda: manager.commands(caller, run_id, after_seq=-1),
        lambda: manager.commands(caller, run_id, limit=0),
        lambda: manager.observations(caller, run_id, after_sequence=-2),
        lambda: manager.observations(caller, run_id, full="yes"),
    ):
        error("invalid_parameters", call)


def test_out_of_range_integers_are_refused_before_the_ledger(rig):
    # A seed is a provider uint32 and a revision a SQLite int64; neither may
    # escape as a bare OverflowError or reach a ledger row.
    manager, caller, ledger = rig[:3]
    for seed in (2**32, 2**63, -1):
        error("invalid_parameters", lambda: create(rig, seed=seed, idempotency_key="s"))
    assert ledger.list_runs("root", limit=10) == []
    run_id = create(rig)["run"]["run_id"]
    error(
        "invalid_parameters",
        lambda: manager.execute(caller, command(run_id, "big", 2**63)),
    )
    assert ledger.list_commands(run_id) == []


@pytest.fixture
def notified_rig(rig):
    """Observe committed state and test lock ownership from another thread."""
    _, caller, ledger, devices, now, registry, store = rig
    changes = []
    run_locks = {}

    def notify(root_frame_id, run_id):
        live = manager._live.get(run_id)
        if live is not None:
            run_locks[run_id] = live.lock

        def available(lock):
            if lock is None:
                return True
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            return acquired

        with ThreadPoolExecutor(max_workers=1) as pool:
            locks_free = pool.submit(
                lambda: available(manager._lock) and available(run_locks.get(run_id))
            ).result(10)
        run = ledger.get_run(run_id)
        changes.append(
            {
                "root": root_frame_id,
                "run_id": run_id,
                "status": run["status"],
                "end_reason": run["end_reason"],
                "commands": [row["state"] for row in ledger.list_commands(run_id)],
                "locks_free": locks_free,
            }
        )

    manager = LabManager(
        lambda: ledger,
        registry,
        instance_id="daemon-a",
        clock_ms=lambda: now[0],
        limits=LabLimits(stop_wait_seconds=0),
        on_change=notify,
    )
    yield (manager, caller, ledger, devices, now, registry, store), changes
    manager.close_all("test teardown")


def test_change_callbacks_follow_committed_operations_and_skip_replays(notified_rig):
    rig, changes = notified_rig
    manager, caller, ledger = rig[:3]
    run_id = create(rig, idempotency_key="creation")["run"]["run_id"]
    assert len(changes) == 1 and changes[0]["status"] == "ready"
    assert changes[0]["commands"] == []
    create(rig, idempotency_key="creation")
    error("invalid_parameters", lambda: create(rig, options=[]))
    assert len(changes) == 1

    first = manager.execute(caller, command(run_id))
    assert len(changes) == 2 and changes[-1]["commands"] == ["succeeded"]
    manager.execute(caller, command(run_id))
    rejected = manager.execute(caller, command(run_id, "unsupported", operation="nope"))
    assert rejected["command"]["state"] == "rejected"
    assert len(changes) == 3
    assert changes[-1]["commands"] == ["succeeded", "rejected"]
    manager.status(caller, run_id, first["command"]["command_id"])
    manager.observe(caller, run_id)
    manager.describe_run(caller, run_id)
    manager.commands(caller, run_id)
    manager.observations(caller, run_id)
    manager.events(caller)
    manager.list_runs(caller)
    assert len(changes) == 3
    assert all(row["root"] == caller.root_frame_id for row in changes)
    assert all(row["locks_free"] for row in changes)
    assert ledger.get_run(run_id)["revision"] == 1


@pytest.mark.parametrize("result", ["receipt", "not_received", "dead", "still_unknown"])
def test_change_callbacks_cover_unknown_and_status_reconciliation(
    notified_rig, monkeypatch, result
):
    rig, changes = notified_rig
    manager, caller, ledger, devices = rig[:4]
    run_id, command_id = unknown_command(rig)
    assert len(changes) == 2
    assert changes[-1]["status"] == "quarantined"
    assert changes[-1]["commands"] == ["outcome_unknown"]
    assert changes[-1]["locks_free"]
    if result == "not_received":
        monkeypatch.setattr(devices[0], "query", lambda *args: None)
    elif result == "dead":
        devices[0].crash()
    elif result == "still_unknown":
        devices[0].fail_query_next()
    reconciled = manager.status(caller, run_id, command_id)
    if result == "still_unknown":
        assert reconciled["command"]["state"] == "outcome_unknown"
        assert len(changes) == 2  # A failed query committed no new state.
    else:
        assert len(changes) == 3 and changes[-1]["locks_free"]
        assert changes[-1]["commands"] == [
            {
                "receipt": "succeeded",
                "not_received": "not_dispatched",
                "dead": "outcome_unknown",
            }[result]
        ]
        assert changes[-1]["status"] == ("ended" if result == "dead" else "ready")
    assert devices[0].executions == 1


@pytest.mark.parametrize("failure", ["limit", "open", "readback"])
def test_change_callbacks_report_create_failures_after_writes(
    notified_rig, monkeypatch, failure
):
    rig, changes = notified_rig
    manager, caller, ledger, devices = rig[:4]
    if failure == "limit":
        manager._limits = LabLimits(max_live_providers=1)
        create(rig)
        changes.clear()
    elif failure == "open":

        def failed_open(self, request):
            raise LabError(ErrorCode.PROVIDER_UNAVAILABLE, "injected open failure")

        monkeypatch.setattr(CountingDevice, "open", failed_open)
    else:

        def failed_readback(run_id):
            raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected read failure")

        monkeypatch.setattr(ledger, "latest_observation", failed_readback)
    error(
        "persistence_unavailable" if failure == "readback" else "provider_unavailable",
        lambda: create(rig),
    )
    assert len(changes) == 1 and changes[0]["locks_free"]
    assert changes[0]["status"] == ("ready" if failure == "readback" else "failed")
    assert changes[0]["end_reason"] == (
        None if failure == "readback" else "create_failed"
    )


@pytest.mark.parametrize(
    "entry",
    [
        "list_devices",
        "describe",
        "create_run",
        "execute",
        "status",
        "stop",
        "list_runs",
        "events",
        "close_all",
    ],
)
def test_change_callbacks_cover_idle_sweeps_and_lifecycle(notified_rig, entry):
    rig, changes = notified_rig
    manager, caller, ledger, devices, now = rig[:5]
    run_id = create(rig, budgets={"idle_timeout_ms": 1})["run"]["run_id"]
    changes.clear()
    if entry not in {"stop", "close_all"}:
        now[0] += 1
    calls = {
        "list_devices": lambda: manager.list_devices(caller),
        "describe": lambda: manager.describe(caller, "fake.extractor.01"),
        "create_run": lambda: create(rig),
        "execute": lambda: manager.execute(caller, command(run_id)),
        "status": lambda: manager.status(caller, run_id),
        "stop": lambda: manager.stop(caller, run_id),
        "list_runs": lambda: manager.list_runs(caller),
        "events": lambda: manager.events(caller),
        "close_all": lambda: manager.close_all("shutdown"),
    }
    calls[entry]()
    ended = [row for row in changes if row["run_id"] == run_id]
    assert len(ended) == 1 and ended[0]["locks_free"]
    assert ended[0]["status"] == "ended"
    assert ended[0]["end_reason"] == {
        "stop": "stopped",
        "close_all": "provider_lost",
    }.get(entry, "idle_timeout")
    assert devices[0].closed


def test_change_callbacks_survive_partial_close_failure(notified_rig, monkeypatch):
    rig, changes = notified_rig
    manager, caller, ledger, devices = rig[:4]
    first = create(rig)["run"]["run_id"]
    second = create(rig)["run"]["run_id"]
    ledger.insert_command(
        {
            "command_id": "labcmd-callback01",
            "run_id": first,
            "idempotency_key": "admitted",
            "request_hash": "h",
            "operation": "mix_model",
            "request": {},
            "origin": "manual_ui",
            "state": "admitted",
        }
    )
    end_run = ledger.end_run

    def unavailable(run_id, **kwargs):
        if run_id == first:
            raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected end failure")
        return end_run(run_id, **kwargs)

    changes.clear()
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "end_run", unavailable)
        error("persistence_unavailable", lambda: manager.close_all("shutdown"))
    assert {row["run_id"] for row in changes} == {first, second}
    assert all(row["locks_free"] for row in changes)
    assert changes[0]["status"] == "ready"
    assert changes[0]["commands"] == ["not_dispatched"]
    assert changes[1]["status"] == "ended" and all(device.closed for device in devices)


def test_change_callback_exceptions_preserve_success_and_failure(rig):
    _, caller, ledger, devices, now, registry = rig[:6]
    calls = []

    def broken(root, run_id):
        calls.append((root, run_id))
        raise RuntimeError("notification unavailable")

    manager = LabManager(
        lambda: ledger,
        registry,
        instance_id="daemon-a",
        clock_ms=lambda: now[0],
        limits=LabLimits(max_live_providers=1),
        on_change=broken,
    )
    notifying = (manager, *rig[1:])
    try:
        run_id = create(notifying)["run"]["run_id"]
        assert (
            manager.execute(caller, command(run_id))["command"]["state"] == "succeeded"
        )
        error("provider_unavailable", lambda: create(notifying))
        assert len(calls) == 3 and calls[0] == (caller.root_frame_id, run_id)
    finally:
        manager.close_all("test teardown")


def test_change_callbacks_are_isolated_between_concurrent_operations(
    notified_rig, monkeypatch
):
    rig, changes = notified_rig
    manager, caller, ledger, devices = rig[:4]
    first = create(rig)["run"]["run_id"]
    entered, release = Event(), Event()
    execute = devices[0].execute

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return execute(*args)

    monkeypatch.setattr(devices[0], "execute", blocked)
    changes.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.execute, caller, command(first))
        try:
            assert entered.wait(10)
            second = create(rig)["run"]["run_id"]
            assert [row["run_id"] for row in changes] == [second]
            assert changes[0]["status"] == "ready" and changes[0]["locks_free"]
        finally:
            release.set()
        assert pending.result(10)["command"]["state"] == "succeeded"
    assert [row["run_id"] for row in changes] == [second, first]
    assert changes[-1]["commands"] == ["succeeded"] and changes[-1]["locks_free"]


def test_startup_change_callback_reports_only_committed_reconciliation(rig):
    manager, caller, ledger, devices, now, registry = rig[:6]
    first = create(rig)["run"]["run_id"]
    second = create(rig)["run"]["run_id"]
    changes = []

    def changed(root, run_id):
        changes.append((root, run_id, ledger.get_run(run_id)["status"]))
        raise RuntimeError("notification unavailable")

    new = build_lab_manager(
        ledger_provider=lambda: ledger,
        registry=registry,
        instance_id="daemon-b",
        clock_ms=lambda: now[0],
        on_change=changed,
    )
    assert new.startup_summary["runs_ended"] == 2
    assert set(changes) == {
        (caller.root_frame_id, first, "ended"),
        (caller.root_frame_id, second, "ended"),
    }
    assert not new._live
    changes.clear()
    build_lab_manager(
        ledger_provider=lambda: ledger,
        registry=registry,
        instance_id="daemon-c",
        clock_ms=lambda: now[0],
        on_change=changed,
    )
    assert changes == []


def test_startup_change_callback_reports_partial_failure(rig, monkeypatch):
    manager, caller, ledger, devices, now, registry = rig[:6]
    run_id = create(rig)["run"]["run_id"]
    ledger.insert_command(
        {
            "command_id": "labcmd-startup01",
            "run_id": run_id,
            "idempotency_key": "admitted",
            "request_hash": "h",
            "operation": "mix_model",
            "request": {},
            "origin": "manual_ui",
            "state": "admitted",
        }
    )
    changes = []

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected end failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "end_run", unavailable)
        error(
            "persistence_unavailable",
            lambda: build_lab_manager(
                ledger_provider=lambda: ledger,
                registry=registry,
                instance_id="daemon-b",
                clock_ms=lambda: now[0],
                on_change=lambda root, run: changes.append((root, run)),
            ),
        )
    assert changes == [(caller.root_frame_id, run_id)]
    assert ledger.get_command("labcmd-startup01")["state"] == "not_dispatched"
    assert ledger.get_run(run_id)["status"] == "ready"


def test_no_callback_keeps_original_ledger_accesses(rig, monkeypatch):
    manager, caller, ledger, devices, now, registry = rig[:6]
    run_id = create(rig)["run"]["run_id"]

    reads = []

    def track(name):
        original = getattr(ledger, name)

        def read(*args, **kwargs):
            reads.append(name)
            return original(*args, **kwargs)

        return read

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "events_since", track("events_since"))
        patch.setattr(ledger, "latest_event_seq", track("latest_event_seq"))
        assert (
            manager.execute(caller, command(run_id))["command"]["state"] == "succeeded"
        )
        reconciled = build_lab_manager(
            ledger_provider=lambda: ledger,
            registry=registry,
            instance_id="daemon-b",
            clock_ms=lambda: now[0],
        )
    assert reconciled.startup_summary["runs_ended"] == 1
    assert reads == []


def test_a_deleted_session_can_never_open_another_run(rig):
    # The tombstone covers every entry point (REST, tools, host.lab, an
    # approval answered mid-deletion); a rolled-back deletion lifts it.
    manager, caller, ledger, devices = rig[:4]
    run_id = create(rig)["run"]["run_id"]
    manager.on_session_deleted(caller.root_frame_id)
    run = ledger.get_run(run_id)
    assert (run["status"], run["end_reason"]) == ("ended", "deleted")
    assert devices[0].closed and not manager._live
    error("provider_unavailable", lambda: create(rig, idempotency_key="again"))
    assert len(devices) == 1
    manager.on_session_restored(caller.root_frame_id)
    assert create(rig, idempotency_key="again")["run"]["status"] == "ready"


def test_releasing_a_session_never_depends_on_reading_the_ledger(rig, monkeypatch):
    manager, caller, ledger, devices = rig[:4]
    create(rig)

    def unavailable(*args, **kwargs):
        raise LabError(ErrorCode.PERSISTENCE_UNAVAILABLE, "injected read failure")

    with monkeypatch.context() as patch:
        for name in ("get_run", "end_run", "list_commands", "inflight_commands"):
            patch.setattr(ledger, name, unavailable)
        manager.on_session_deleted(caller.root_frame_id)
    assert devices[0].closed and not manager._live


def test_idle_cleanup_releases_a_provider_whose_run_row_is_gone(rig):
    manager, caller, ledger, devices, now, registry, store = rig
    run_id = create(rig)["run"]["run_id"]
    # A session whose rows were deleted while its release failed.
    store._conn.execute("DELETE FROM lab_runs WHERE run_id=?", (run_id,))
    store._conn.commit()
    manager.list_devices(caller)
    assert devices[0].closed and not manager._live
