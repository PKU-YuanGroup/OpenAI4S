"""Wave-2 composition: the real manager over real provider processes.

Each package proved its own side against fakes. These cases wire the actual
pieces together — ``build_lab_manager`` + ``register_builtin_devices`` + a real
Store + the stdlib toy provider in its own sandboxed subprocess — so a seam
that only the other package would notice still turns a test red.
"""

import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.lab.builtin import register_builtin_devices
from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.fake import fake_registration
from openai4s.lab.manager import LabLimits
from openai4s.lab.manifest import load_descriptor, match_command
from openai4s.lab.models import (
    CommandOrigin,
    CommandRequest,
    Dispatch,
    ErrorCode,
    LabCaller,
    LabError,
    Quantity,
    SessionOpenRequest,
)
from openai4s.lab.policies import (
    FixedRulePolicy,
    ManagerPolicyEnv,
    RandomValidPolicy,
    run_episode,
)
from openai4s.lab.provider_process import ProviderProcessDevice
from openai4s.lab.runtime import build_lab_manager
from openai4s.lab.wrappers import (
    BusyWrapper,
    FaultInjectionWrapper,
    LatencyWrapper,
    ObservationNoiseWrapper,
)
from openai4s.store import Store

TOY = "toy.extractor.01"
CHEMGYM = "chemgym.extractor.01"
PROFILE = "toy-extract-v0"
# Keys that only the ledger, the evaluation view or the provider may hold.
PRIVATE = {
    "evaluation",
    "reward",
    "ground_truth",
    "provider_action",
    "provider_action_json",
    "fencing_token",
    "fencing_tokens",
    "approval_ref",
    "owner_user_id",
    "daemon_instance",
    "config",
    "descriptor_json",
    "receipt_json",
}


def private_keys(value, path="$"):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRIVATE:
                found.append(f"{path}.{key}")
            found += private_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found += private_keys(item, f"{path}[{index}]")
    return found


class CountingPort:
    """Delegates every DevicePort method and counts what reached the device."""

    def __init__(self, inner):
        self.inner = inner
        self.device_id = inner.device_id
        self.executed = []

    def describe(self, profile):
        return self.inner.describe(profile)

    def open(self, request):
        return self.inner.open(request)

    def execute(self, session_id, dispatch):
        self.executed.append(dispatch.provider_command_id)
        return self.inner.execute(session_id, dispatch)

    def query(self, session_id, provider_command_id):
        return self.inner.query(session_id, provider_command_id)

    def stop(self, session_id, reason):
        return self.inner.stop(session_id, reason)

    def close(self, session_id):
        return self.inner.close(session_id)

    def alive(self, session_id):
        return self.inner.alive(session_id)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Real Store + builtin registration (toy enabled) + build_lab_manager."""
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "1")
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    cfg = SimpleNamespace(data_dir=str(tmp_path / "data"))
    builtin = DeviceRegistry()
    register_builtin_devices(builtin, cfg)
    ports = []

    def counted(factory):
        def make():
            port = CountingPort(factory())
            ports.append(port)
            return port

        return make

    registry = DeviceRegistry()
    for registration in builtin.list():
        registry.register(
            replace(registration, port_factory=counted(registration.port_factory))
        )
    registry.register(fake_registration())
    store = Store(tmp_path / "lab.sqlite")
    manager = build_lab_manager(
        ledger_provider=lambda: store.lab,
        registry=registry,
        instance_id="daemon-w2",
        limits=LabLimits(stop_wait_seconds=0.2),
    )
    caller = LabCaller(
        "root-w2", "frame-1", None, CommandOrigin.AGENT_TOOL, None, "agent"
    )
    yield SimpleNamespace(
        manager=manager,
        caller=caller,
        store=store,
        ports=ports,
        data_dir=Path(cfg.data_dir),
    )
    manager.close_all("test teardown")
    store.close()


def transfer(run_id, *, revision, key, litres=0.2):
    return {
        "run_id": run_id,
        "operation": "transfer_liquid",
        "source": "beaker_1",
        "target": "extraction_vessel",
        # 0.2 L must be converted to the declared 200 mL level (CONTRACT §5).
        "parameters": {"volume": {"value": litres, "unit": "L"}},
        "expected_revision": revision,
        "idempotency_key": key,
    }


def test_builtin_composition_hides_toy_and_never_borrows_the_daemon_python(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI4S_LAB_ENABLE_TOY", raising=False)
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    registry = DeviceRegistry()
    register_builtin_devices(registry, SimpleNamespace(data_dir=str(tmp_path)))
    store = Store(tmp_path / "lab.sqlite")
    manager = build_lab_manager(ledger_provider=lambda: store.lab, registry=registry)
    caller = LabCaller("root", "frame", None, CommandOrigin.AGENT_TOOL, None, "agent")
    try:
        devices = manager.list_devices(caller)
        assert [d["device_id"] for d in devices] == [CHEMGYM]
        assert devices[0]["available"] is False
        assert "openai4s lab setup chemgymrl" in devices[0]["availability_detail"]
        assert private_keys(devices) == []
        # The committed manifest still describes the device without a provider.
        described = manager.describe(caller, CHEMGYM, "WaterOilExtract-v0")
        assert described["capability_revision"]
        with pytest.raises(LabError) as caught:
            manager.create_run(
                caller, {"device_id": CHEMGYM, "profile": "WaterOilExtract-v0"}
            )
        assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE
        assert "openai4s lab setup chemgymrl" in caught.value.message
        assert sys.executable not in caught.value.message
        with pytest.raises(LabError) as caught:
            manager.create_run(caller, {"device_id": TOY, "profile": PROFILE})
        assert caught.value.code is ErrorCode.DEVICE_NOT_FOUND
    finally:
        manager.close_all("test teardown")
        store.close()


def test_toy_provider_through_the_real_manager(lab):
    m, caller, ledger = lab.manager, lab.caller, lab.store.lab
    devices = {d["device_id"]: d for d in m.list_devices(caller)}
    assert devices[TOY]["available"] is True and devices[CHEMGYM]["available"] is False

    created = m.create_run(
        caller,
        {"device_id": TOY, "profile": PROFILE, "seed": 11, "idempotency_key": "c1"},
    )
    run_id = created["run"]["run_id"]
    assert created["run"]["status"] == "ready" and created["run"]["revision"] == 0
    assert [c["name"] for c in created["observation"]["channels"]] == ["layers"]
    # The live descriptor carries the provider's real sandbox posture.
    assert any("provider sandbox:" in a for a in created["descriptor"]["assumptions"])
    assert (
        m.create_run(
            caller,
            {"device_id": TOY, "profile": PROFILE, "seed": 11, "idempotency_key": "c1"},
        )["run"]["run_id"]
        == run_id
    )
    (port,) = lab.ports

    observed = m.observe(caller, run_id)
    assert (
        observed["observation"]["observation_id"]
        == created["observation"]["observation_id"]
    )

    first = m.execute(caller, transfer(run_id, revision=0, key="k1"))
    command = first["command"]
    assert command["state"] == "succeeded" and command["applied_revision"] == 1
    assert command["request"]["parameters"]["volume"] == {"value": 200, "unit": "mL"}
    assert first["observation"]["command_id"] == command["command_id"]
    assert port.executed == [command["command_id"]]

    # Same key: the stored command and ITS observation, without a second send.
    replay = m.execute(caller, transfer(run_id, revision=0, key="k1"))
    assert replay == first
    assert port.executed == [command["command_id"]]
    with pytest.raises(LabError) as caught:
        m.execute(caller, transfer(run_id, revision=0, key="k1", litres=0.4))
    assert caught.value.code is ErrorCode.IDEMPOTENCY_CONFLICT

    stale = m.execute(caller, transfer(run_id, revision=0, key="k2"))
    assert stale["command"]["state"] == "rejected"
    assert stale["command"]["error_code"] == "stale_revision"
    assert port.executed == [command["command_id"]]

    second = m.execute(caller, transfer(run_id, revision=1, key="k3", litres=0.4))
    assert second["command"]["state"] == "succeeded"
    assert second["run"]["revision"] == 2 and second["run"]["step_count"] == 2

    status = m.status(caller, run_id, command["command_id"])
    assert status["command"] == first["command"]
    run_row = ledger.get_run(run_id)
    assert [c["state"] for c in ledger.list_commands(run_id)] == [
        "succeeded",
        "rejected",
        "succeeded",
    ]
    assert len(ledger.list_observations(run_id)) == 3
    # Simulator truth is recorded privately for evaluation, never projected.
    assert len(ledger.list_evaluations(run_id)) == 3

    stopped = m.stop(caller, run_id)
    assert stopped["stopped"] is True and stopped["semantics"] == "end_session"
    assert stopped["run"]["status"] == "ended"
    assert stopped["run"]["end_reason"] == "stopped"
    assert run_row["daemon_instance"] == "daemon-w2"
    assert not port.inner._sessions
    assert not list((lab.data_dir / "lab" / "runs").iterdir())
    assert not list((lab.data_dir / "lab" / "cache").iterdir())

    events = m.events(caller)
    for payload in (
        created,
        observed,
        first,
        stale,
        second,
        status,
        stopped,
        m.list_runs(caller),
        events,
        m.describe(caller, TOY),
    ):
        assert private_keys(payload) == []
    assert events["latest_event_seq"] >= len(events["events"]) > 0


def _provider_process(port):
    (session,) = port.inner._sessions.values()
    return session.client.process


def test_killed_toy_provider_is_outcome_unknown_and_provider_lost(lab):
    m, caller, ledger = lab.manager, lab.caller, lab.store.lab
    run_id = m.create_run(caller, {"device_id": TOY, "profile": PROFILE, "seed": 3})[
        "run"
    ]["run_id"]
    (port,) = lab.ports
    process = _provider_process(port)
    os.killpg(process.pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG):
                break
        except ChildProcessError:
            break
        time.sleep(0.01)

    with pytest.raises(LabError) as caught:
        m.execute(caller, transfer(run_id, revision=0, key="after-kill"))
    assert caught.value.code is ErrorCode.OUTCOME_UNKNOWN
    command_id = caught.value.details["command_id"]
    command = ledger.get_command(command_id)
    assert command["state"] == "outcome_unknown"
    run = ledger.get_run(run_id)
    assert (run["status"], run["end_reason"]) == ("ended", "provider_lost")
    assert port.executed == [command_id]

    # Neither a replay nor a status read ever resends an unknown command.
    replay = m.execute(caller, transfer(run_id, revision=0, key="after-kill"))
    assert replay["command"]["state"] == "outcome_unknown"
    assert m.status(caller, run_id, command_id)["command"]["state"] == (
        "outcome_unknown"
    )
    assert port.executed == [command_id]
    refused = m.execute(caller, transfer(run_id, revision=0, key="next"))
    assert refused["command"]["state"] == "rejected"
    assert refused["command"]["error_code"] == "run_ended"
    assert port.executed == [command_id]


def test_stop_preempts_a_hung_provider_execute(lab):
    # The manager waits stop_wait_seconds for the in-flight receipt, then
    # closes. Close must not wait for the 60 s execute timeout: the provider
    # is killed, the command stays unknown and is never resent.
    m, caller, ledger = lab.manager, lab.caller, lab.store.lab
    run_id = m.create_run(
        caller,
        {
            "device_id": TOY,
            "profile": PROFILE,
            "seed": 5,
            "options": {"sleep_on_execute_s": 120},
        },
    )["run"]["run_id"]
    (port,) = lab.ports
    process = _provider_process(port)
    outcome = {}

    def blocked():
        try:
            outcome["result"] = m.execute(
                caller, transfer(run_id, revision=0, key="hung")
            )
        except LabError as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=blocked)
    worker.start()
    deadline = time.monotonic() + 10
    while not port.executed:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    time.sleep(0.2)
    started = time.monotonic()
    stopped = m.stop(caller, run_id)
    elapsed = time.monotonic() - started
    worker.join(10)
    assert not worker.is_alive()
    assert elapsed < 5, elapsed
    assert (stopped["run"]["status"], stopped["run"]["end_reason"]) == (
        "ended",
        "stopped",
    )
    assert outcome["error"].code is ErrorCode.OUTCOME_UNKNOWN
    command_id = outcome["error"].details["command_id"]
    assert ledger.get_command(command_id)["state"] == "outcome_unknown"
    assert port.executed == [command_id]
    assert process.returncode is not None


@pytest.mark.parametrize("kind", ["noise", "fault", "all"])
def test_wrapped_close_preempts_an_in_flight_execute(tmp_path, kind):
    inner = toy_device(tmp_path)
    port = wrapped(inner, kind)
    descriptor = port.describe(PROFILE)
    opened = port.open(
        SessionOpenRequest(
            PROFILE, 42, {"sleep_on_execute_s": 120}, descriptor.capability_revision
        )
    )
    outcome = {}

    def blocked():
        try:
            outcome["receipt"] = port.execute(
                opened.session_id, toy_dispatch(opened, "hung", token=1)
            )
        except LabError as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=blocked)
    worker.start()
    deadline = time.monotonic() + 10
    while inner._lock.acquire(blocking=False):
        inner._lock.release()
        assert time.monotonic() < deadline
        time.sleep(0.005)
    time.sleep(0.2)
    started = time.monotonic()
    port.close(opened.session_id)
    elapsed = time.monotonic() - started
    worker.join(10)
    assert not worker.is_alive() and elapsed < 3, elapsed
    assert "receipt" not in outcome
    assert outcome["error"].code is ErrorCode.PROVIDER_UNAVAILABLE
    assert not port.alive(opened.session_id)
    assert not list((tmp_path / "lab" / "runs").iterdir())


def _fake_run(lab, seed):
    created = lab.manager.create_run(
        lab.caller,
        {"device_id": "fake.extractor.01", "profile": PROFILE, "seed": seed},
    )
    run_id = created["run"]["run_id"]
    descriptor = load_descriptor(lab.store.lab.get_run(run_id)["descriptor"])
    return run_id, ManagerPolicyEnv(
        lab.manager, lab.caller, run_id, descriptor=descriptor
    )


ILLEGAL = {"unsupported_action", "invalid_parameters", "unit_mismatch"}


def test_policies_cross_the_real_manager_without_illegal_actions(lab):
    run_id, env = _fake_run(lab, 7)
    fixed = run_episode(FixedRulePolicy(), env, max_steps=40)
    assert fixed["error_code"] is None
    assert fixed["stop_reason"] == "ended"
    assert fixed["run"]["end_reason"] == "end_action"
    codes = {c["error_code"] for c in fixed["commands"]}
    assert not codes & ILLEGAL and codes <= {None, "precondition_failed"}
    assert private_keys(fixed) == []

    run_id, env = _fake_run(lab, 8)
    randomized = run_episode(RandomValidPolicy(3), env, max_steps=40)
    assert randomized["error_code"] is None
    codes = {c["error_code"] for c in randomized["commands"]}
    assert not codes & (ILLEGAL | {"stale_revision", "idempotency_conflict"})
    # Exhausted budgets end the run instead of refusing commands forever.
    assert randomized["stop_reason"] in {"ended", "max_steps"}
    if randomized["stop_reason"] == "ended":
        assert randomized["run"]["end_reason"] in {"end_action", "budget_exhausted"}
    assert private_keys(randomized) == []


def toy_device(tmp_path):
    return ProviderProcessDevice(
        TOY,
        "toy",
        python=sys.executable,
        package_dir=Path(str(files("openai4s_lab_provider"))),
        runs_root=tmp_path / "lab" / "runs",
        sandbox_mode=os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto"),
    )


def wrapped(port, kind):
    if kind == "bare":
        return port
    if kind == "latency":
        return LatencyWrapper(
            port, execute_delay_ms=3, observe_delay_ms=4, sleep=lambda _: None
        )
    if kind == "noise":
        return ObservationNoiseWrapper(
            port, channel="layers", model={"kind": "gaussian", "sigma": 0.1}, seed=5
        )
    if kind == "fault":
        return FaultInjectionWrapper(port, schedule={})
    if kind == "busy":
        return BusyWrapper(port, busy_windows=[])
    return wrapped(wrapped(wrapped(wrapped(port, "fault"), "busy"), "latency"), "noise")


def toy_dispatch(opened, ident, *, token, litres=0.2):
    capability = next(
        c for c in opened.descriptor.capabilities if c.operation == "transfer_liquid"
    )
    request = CommandRequest(
        "labrun-w2",
        capability.operation,
        capability.source,
        capability.target,
        {"volume": Quantity(litres, "L")},
        0,
        ident,
    )
    return Dispatch(
        ident,
        match_command(opened.descriptor, request),
        {resource: token for resource in capability.resources},
    )


@pytest.mark.parametrize("kind", ["bare", "latency", "noise", "fault", "busy", "all"])
def test_wrapped_real_toy_provider_keeps_the_port_contract(tmp_path, kind):
    port = wrapped(toy_device(tmp_path), kind)
    descriptor = port.describe(PROFILE)
    opened = port.open(
        SessionOpenRequest(PROFILE, 42, {}, descriptor.capability_revision)
    )
    other = None
    try:
        assert opened.descriptor == descriptor
        assert port.alive(opened.session_id)
        assert port.query(opened.session_id, "never-sent") is None
        dispatch = toy_dispatch(opened, "cmd-1", token=20)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(
                pool.map(lambda _: port.execute(opened.session_id, dispatch), range(4))
            )
        receipt = receipts[0]
        assert receipt.applied and receipt.step_index == 1
        assert all(item == receipt for item in receipts)
        assert port.query(opened.session_id, "cmd-1") == receipt
        stale = port.execute(opened.session_id, toy_dispatch(opened, "cmd-2", token=19))
        assert not stale.applied and stale.error["code"] == "resource_busy"
        assert stale.step_index == 1
        other = port.open(
            SessionOpenRequest(PROFILE, 43, {}, descriptor.capability_revision)
        )
        assert port.query(other.session_id, "cmd-1") is None
        assert port.stop(opened.session_id, "test").stopped
        port.close(opened.session_id)
        port.close(opened.session_id)
        assert not port.alive(opened.session_id) and port.alive(other.session_id)
        for action in (
            lambda: port.execute(opened.session_id, dispatch),
            lambda: port.query(opened.session_id, "never-sent"),
        ):
            with pytest.raises(LabError) as caught:
                action()
            assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    finally:
        port.close(opened.session_id)
        if other is not None:
            port.close(other.session_id)
    assert not list((tmp_path / "lab" / "runs").iterdir())
