"""Real Store/manager proofs for the shared simulation Lab Host boundary."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from openai4s.config import get_config
from openai4s.host.delegation_policy import child_execution_policy
from openai4s.host.lab import LabService
from openai4s.host_dispatch import build_dispatcher
from openai4s.kernel import Kernel
from openai4s.kernel.recovery import (
    _SAFE_HOST_METHODS,
    _UNSAFE_HOST_METHODS,
    replay_safety_error,
)
from openai4s.lab import fake
from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.manager import LabLimits, LabManager
from openai4s.lab.models import LabError
from openai4s.permissions import broker
from openai4s.sdk.host import build_host
from openai4s.storage.metadata import DERIVABLE_HOST_CALLS
from openai4s.store import get_store
from openai4s.tools.registry import REGISTRY, execute_tool_call, get_tool

NAMES = {
    "lab_" + name
    for name in (
        "list",
        "describe",
        "create",
        "observe",
        "execute",
        "status",
        "stop",
        "commands",
        "observations",
    )
}
FULL = {"lab_observe_full", "lab_observations_full"}
UNSAFE = {"lab_create", "lab_execute", "lab_stop", "lab_status"}
FORBIDDEN = {
    "evaluation",
    "reward",
    "ground_truth",
    "provider_action",
    "fencing_token",
    "approval_ref",
    "owner_user_id",
    "daemon_instance",
    "config",
}
CREATE = {"device_id": "fake.extractor.01", "profile": "toy-extract-v0"}


class CountingDevice(fake.FakeExtractorDevice):
    def __init__(self):
        super().__init__()
        self.executions = 0
        self.queries = 0

    def execute(self, session_id, dispatch):
        self.executions += 1
        return super().execute(session_id, dispatch)

    def query(self, session_id, command_id):
        self.queries += 1
        return super().query(session_id, command_id)


@pytest.fixture
def rig():
    cfg = get_config()
    store = get_store(cfg.db_path)
    root = store.new_frame()
    frame = store.new_frame(parent_id=root)
    dispatcher = build_dispatcher(cfg, frame_id=frame)
    registry = DeviceRegistry()
    devices = []

    def factory():
        device = CountingDevice()
        devices.append(device)
        return device

    registry.register(replace(fake.fake_registration(), port_factory=factory))
    manager = LabManager(
        lambda: store.lab,
        registry,
        instance_id="lab-host-test",
        clock_ms=lambda: int(time.time() * 1000),
        limits=LabLimits(stop_wait_seconds=0),
    )
    dispatcher.set_lab_manager(lambda: manager)
    for name in ("lab_create", "lab_execute"):
        store.set_permission_rule(
            scope="conversation",
            scope_id=root,
            tool=name,
            pattern="*",
            decision="allow",
        )
    yield SimpleNamespace(
        cfg=cfg,
        store=store,
        root=root,
        frame=frame,
        dispatcher=dispatcher,
        manager=manager,
        devices=devices,
        registry=registry,
        host=build_host(dispatcher, mode="repl"),
    )
    manager.close_all("test teardown")


def create(rig, **kwargs):
    return rig.host.lab.create(**CREATE, **kwargs)


def command(run_id, **kwargs):
    return {
        "run_id": run_id,
        "operation": "mix_model",
        "source": "extraction_vessel",
        "parameters": {"duration": {"value": 1, "unit": "model_time"}},
        "expected_revision": 0,
        **kwargs,
    }


def assert_public(value):
    if isinstance(value, dict):
        assert not FORBIDDEN.intersection(value)
        for child in value.values():
            assert_public(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_public(child)


def audit(rig, method):
    return [
        row
        for row in rig.store.list_session_host_calls(rig.root, limit=100)
        if row["method"] == method
    ]


def test_lab_metadata_and_progressive_group(rig):
    tools = {t.name: t for t in REGISTRY if t.name.startswith("lab_")}
    assert set(tools) == NAMES
    catalog = rig.dispatcher.tool_catalog()
    group = next(g for g in catalog.group_metadata() if g["id"] == "lab")
    assert set(group["tools"]) == NAMES and group["always"] is False
    for name, tool in tools.items():
        assert tool.read_only == (name not in {"lab_create", "lab_execute", "lab_stop"})
        assert tool.requires_approval == (name in {"lab_create", "lab_execute"})
        assert "full" not in tool.parameters["properties"]
        assert "options" not in tool.parameters["properties"]
        if name in {"lab_create", "lab_execute", "lab_stop"}:
            key = "device_id" if name == "lab_create" else "run_id"
            assert tool.resource_keys({key: "x"}) == ("lab:x",)
            assert tool.side_effect_class == "runtime_mutation"
            assert tool.permission_target({key: "x"}) == "x"
    assert not ((NAMES | FULL) & DERIVABLE_HOST_CALLS)


def test_sdk_native_parity_idempotency_identity_and_audit(rig):
    rig.store.team.set_session_owner(rig.root, "session-owner")
    created = create(rig, idempotency_key="create-once", budgets={"max_steps": 500})
    assert (
        get_tool("lab_create").invoke(
            rig.dispatcher,
            {**CREATE, "idempotency_key": "create-once", "budgets": {"max_steps": 500}},
        )
        == created
    )
    assert created["run"]["budgets"]["max_steps"] == 50
    run_id = created["run"]["run_id"]
    request = command(run_id, idempotency_key="same-command")
    native = get_tool("lab_execute").invoke(rig.dispatcher, request)
    assert rig.host.lab.execute(**request) == native
    command_id = native["command"]["command_id"]
    row = rig.store.lab.get_command(command_id)
    assert (
        row["origin"],
        row["root_frame_id"],
        row["actor_frame_id"],
        row["owner_user_id"],
    ) == ("agent_tool", rig.root, rig.frame, "session-owner")
    second = rig.host.lab.execute(**command(run_id, expected_revision=1))
    assert second["command"]["idempotency_key"].startswith("lab-")
    assert (
        rig.store.lab.get_command(second["command"]["command_id"])["origin"]
        == "host_sdk"
    )
    assert rig.devices[0].executions == 2
    assert len(rig.host.lab.commands(run_id)["commands"]) == 2
    rows = audit(rig, "lab_execute")
    assert len(rows) == 3
    assert all(
        row["side_effect_class"] == "runtime_mutation"
        and row["resource_keys"] == [f"lab:{run_id}"]
        for row in rows
    )
    assert_public(native)
    generated = create(rig)
    assert generated["idempotency_key"].startswith("lab-")
    assert (
        create(rig, idempotency_key=generated["idempotency_key"])["run"]["run_id"]
        == generated["run"]["run_id"]
    )


@pytest.mark.parametrize("method", ["lab_create", "lab_execute"])
def test_approval_precedes_any_dispatch_and_binds_receipt(rig, method):
    run_id = create(rig)["run"]["run_id"] if method == "lab_execute" else None
    rig.store.set_permission_rule(
        scope="conversation",
        scope_id=rig.root,
        tool=method,
        pattern="*",
        decision="ask",
    )
    pending = Event()
    cancelled = Event()
    events = []

    def emit(event):
        if event.get("type") == "await_permission":
            events.append(event)
            pending.set()

    permission_broker = broker()
    permission_broker.register_channel(
        rig.root, emit, cancel_event=cancelled, store=rig.store
    )
    request = command(run_id) if run_id else CREATE
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(get_tool(method).invoke, rig.dispatcher, request)
            try:
                assert pending.wait(
                    5
                ), "call bypassed or never reached the approval gate"
                assert not future.done()
                assert sum(d.executions for d in rig.devices) == 0
                if run_id:
                    assert rig.store.lab.list_commands(run_id) == []
                else:
                    assert rig.devices == []
                decision_id = events[-1]["decision_id"]
                assert permission_broker.resolve(decision_id, allow=True, scope="once")
                result = future.result(timeout=5)
                assert "error" not in result
                if run_id:
                    row = rig.store.lab.get_command(result["command"]["command_id"])
                    assert row["approval_ref"] == decision_id
                assert audit(rig, method)[-1]["permission_decision_id"] == decision_id
                assert rig.dispatcher._lab_caller().approval_ref is None
            finally:
                cancelled.set()
                for event in events:
                    permission_broker.resolve(event["decision_id"], allow=False)
    finally:
        permission_broker.unregister_channel(rig.root)


def test_all_child_capabilities_are_denied_even_with_manager(rig):
    for capabilities in (None, ["compute"], sorted(NAMES | FULL)):
        policy = child_execution_policy(
            {} if capabilities is None else {"capabilities": capabilities}
        )
        rig.dispatcher.set_child_execution_policy(policy)
        for name in NAMES | FULL:
            result = rig.dispatcher(
                name, [CREATE if name == "lab_create" else {"run_id": "not-owned"}]
            )
            assert "delegated child policy" in result["error"]
    assert rig.devices == []


def test_session_owner_and_stop_boundary(rig):
    run_id = create(rig)["run"]["run_id"]
    other = build_dispatcher(rig.cfg, frame_id=rig.store.new_frame())
    other.set_lab_manager(lambda: rig.manager)
    for method in (
        "lab_observe",
        "lab_status",
        "lab_stop",
        "lab_commands",
        "lab_observations",
        *FULL,
    ):
        assert other(method, [{"run_id": run_id}])["error_kind"] == "run_not_found"
    rig.store.team.set_session_owner(rig.root, "different-owner")
    assert (
        rig.dispatcher("lab_observe", [{"run_id": run_id}])["error_kind"]
        == "run_not_found"
    )
    rig.store.team.delete_session_owner(rig.root)
    # Even a standing ask cannot delay this session's safety stop.
    rig.store.set_permission_rule(
        scope="conversation",
        scope_id=rig.root,
        tool="lab_stop",
        pattern="*",
        decision="ask",
    )
    assert rig.host.lab.stop(run_id)["stopped"] is True


def test_recovery_lists_and_manager_defense(rig):
    assert UNSAFE <= _UNSAFE_HOST_METHODS
    assert (NAMES | FULL) - UNSAFE <= _SAFE_HOST_METHODS
    for method in UNSAFE:
        assert "unsafe Host method" in replay_safety_error(
            f"host.{method}()", language="python"
        )
        assert "unsafe Host methods" in replay_safety_error(
            "x = 1", language="python", declared_host_methods=[method]
        )
    for method in (NAMES | FULL) - UNSAFE:
        assert (
            replay_safety_error(
                f"host.{method}()", language="python", declared_host_methods=[method]
            )
            is None
        )
    run_id = create(rig)["run"]["run_id"]
    service = LabService(
        lambda: rig.manager,
        lambda: replace(rig.dispatcher._lab_caller(), execution_owner="recovery"),
    )
    for op, spec in (
        ("create", CREATE),
        ("execute", command(run_id)),
        ("stop", {"run_id": run_id}),
    ):
        assert service.call(op, spec)["error_kind"] == "replay_forbidden"
    rig.devices[0].lose_response_next()
    rig.devices[0].fail_query_next()
    with pytest.raises(RuntimeError) as caught:
        rig.host.lab.execute(**command(run_id))
    command_id = caught.value.details["command_id"]
    queries = rig.devices[0].queries
    assert (
        service.call("status", {"run_id": run_id, "command_id": command_id})["command"][
            "state"
        ]
        == "outcome_unknown"
    )
    assert rig.devices[0].queries == queries


def test_full_arrays_only_on_sdk_routes_and_safe_projection(rig, monkeypatch):
    descriptor = fake._descriptor
    observation = fake.FakeExtractorDevice._observation

    def wide_descriptor(profile):
        original = descriptor(profile)
        return replace(
            original,
            observation_channels=tuple(
                replace(channel, shape=(300,)) if channel.name == "layers" else channel
                for channel in original.observation_channels
            ),
        )

    def wide_observation(session):
        original = observation(session)
        original["channels"][0].update(shape=[300], value=list(range(300)))
        return original

    monkeypatch.setattr(fake, "_descriptor", wide_descriptor)
    monkeypatch.setattr(
        fake.FakeExtractorDevice, "_observation", staticmethod(wide_observation)
    )
    rig.registry._devices.clear()
    rig.registry.register(
        replace(fake.fake_registration(), port_factory=lambda: CountingDevice())
    )
    result = create(rig)
    run_id = result["run"]["run_id"]
    for method in ("lab_observe", "lab_status", "lab_observations"):
        projected = get_tool(method).invoke(rig.dispatcher, {"run_id": run_id})
        assert_public(projected)
        obs = projected.get("observation") or projected["observations"][0]
        assert obs["channels"][0]["value"]["truncated"] is True
    for full in (
        rig.host.lab.observe(run_id, full=True),
        rig.host.lab.observations(run_id, full=True),
    ):
        assert_public(full)
        obs = full.get("observation") or full["observations"][0]
        assert obs["channels"][0]["value"] == list(range(300))
    for method in FULL:
        output, ok = execute_tool_call(
            rig.dispatcher, {"name": method, "arguments": {"run_id": run_id}}
        )
        assert not ok and "unknown tool" in output
    assert (
        rig.dispatcher("lab_observe", [{"run_id": run_id, "full": True}])["error_kind"]
        == "invalid_parameters"
    )
    assert_public(result)
    for method in FULL:
        assert audit(rig, method)[-1]["side_effect_class"] == "read_only"


def test_unknown_outcome_is_failed_envelope_then_same_command_status(rig):
    run_id = create(rig)["run"]["run_id"]
    rig.devices[0].lose_response_next()
    rig.devices[0].fail_query_next()
    output, ok = execute_tool_call(
        rig.dispatcher, {"name": "lab_execute", "arguments": command(run_id)}
    )
    assert not ok and "outcome_unknown" in output and "command_id" in output
    assert audit(rig, "lab_execute")[-1]["ok"] is False
    commands = rig.host.lab.commands(run_id)["commands"]
    assert len(commands) == 1
    command_id = commands[0]["command_id"]
    result = rig.host.lab.status(run_id, command_id)
    assert result["command"]["state"] == "succeeded"
    assert result["command"]["command_id"] == command_id
    assert rig.devices[0].executions == 1
    # SDK preserves the same structured unknown details through a real RPC.
    rig.devices[0].lose_response_next()
    rig.devices[0].fail_query_next()
    with Kernel(dispatcher=rig.dispatcher) as kernel:
        response = kernel.execute(
            "try:\n    host.lab.execute("
            + repr(run_id)
            + ", 'mix_model', source='extraction_vessel', parameters={'duration': {'value': 1, 'unit': 'model_time'}}, expected_revision=1)\nexcept RuntimeError as error:\n    print(error.error_kind, bool(error.details['command_id']))"
        )
        assert response["error"] is None
        assert response["stdout"].strip() == "outcome_unknown True"


def test_manager_unavailable_sdk_splice_and_safe_exception(rig, monkeypatch):
    cli = build_dispatcher(rig.cfg, frame_id=rig.root)
    assert hasattr(build_host(cli, mode="repl"), "lab")
    assert not hasattr(build_host(cli, mode="analysis"), "lab")
    for method in NAMES | FULL:
        result = cli(
            method, [CREATE if method == "lab_create" else {"run_id": "missing"}]
        )
        assert result == {
            "error": "Lab is available only in the web daemon",
            "error_kind": "provider_unavailable",
        }
    with pytest.raises(RuntimeError) as caught:
        build_host(cli, mode="repl").lab.list()
    assert caught.value.error_kind == "provider_unavailable"
    monkeypatch.setattr(
        rig.manager,
        "list_devices",
        lambda caller: (_ for _ in ()).throw(ValueError("private scientific value")),
    )
    result = rig.dispatcher("lab_list", [])
    assert result == {
        "error": "Lab service is unavailable",
        "error_kind": "provider_unavailable",
    }
    assert audit(rig, "lab_list")[-1]["ok"] is False


def test_no_options_and_activity_cards_keep_only_safe_fields(rig):
    events = []
    rig.dispatcher.on_step = events.append
    invalid = rig.dispatcher(
        "lab_create", [{**CREATE, "options": {"crash_on_execute": True}}]
    )
    assert invalid["error_kind"] == "invalid_parameters" and not rig.devices
    run_id = create(rig)["run"]["run_id"]
    result = rig.host.lab.execute(
        **command(
            run_id, parameters={"duration": {"value": 0.12345, "unit": "model_time"}}
        )
    )
    assert result["command"]["state"] == "rejected"
    assert rig.devices[0].executions == 0
    assert events[-1]["status"] == "error"
    assert "provider" not in json.dumps(events)
    begin = next(e for e in events if e.get("input", {}).get("operation"))
    assert "仿真" in begin["title"] and "→" in begin["title"]
    assert begin["input"]["device_id"] == CREATE["device_id"]
    assert begin["input"]["parameters"]["duration"]["unit"] == "model_time"
    for event in events:
        assert_public(event)


def test_permission_v5_upgrade_preserves_operator_choices(rig):
    store = rig.store
    store._conn.execute("DELETE FROM permission_rules WHERE tool LIKE 'lab_%'")
    store._conn.commit()
    store.set_setting("perm_seed_version", "4")
    store.seed_default_permission_rules()
    rules = {
        r["tool"]: r["decision"] for r in store.get_permission_rules(scope="global")
    }
    for method in NAMES | FULL:
        assert rules[method] == (
            "ask" if method in {"lab_create", "lab_execute"} else "allow"
        )
    store.set_permission_rule(
        scope="global", tool="lab_execute", pattern="*", decision="deny"
    )
    store.seed_default_permission_rules()
    assert (
        store.resolve_permission(
            root_frame_id=rig.root,
            project_id="default",
            tool="lab_execute",
            pattern_input="run",
        )
        == "deny"
    )
