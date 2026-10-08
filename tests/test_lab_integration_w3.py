"""Wave-3 composition: REST, native tools and host.lab over one daemon manager.

Each package proved its own entry point against its own rig. These cases use
the real gateway composition (SessionRunner + its dispatcher wiring + the REST
handler) over the real manager and the stdlib toy provider in its own process,
so a seam between the entry points turns a test red.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher
from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.sdk.host import build_host
from openai4s.server import gateway, local_auth
from openai4s.tools.registry import get_tool

PROFILE = "toy-extract-v0"
TOY = "toy.extractor.01"


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        return lambda event: self.broadcast(root_frame_id, event)

    def broadcast(self, root_frame_id, event):
        self.events.append((root_frame_id, event))

    def drop_frame(self, root_frame_id):
        pass


class _Daemon:
    def __init__(self, tmp_path):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.hub = _Hub()
        self.runner = gateway.SessionRunner(
            self.cfg, self.hub, start_idle_sweeper=False
        )
        self.store = self.runner.store
        self.store.create_project(name="Lab W3", description="", context="")
        self.project_id = self.store.list_projects()[0]["project_id"]
        self.handler_class = gateway.make_handler(self.cfg, self.hub, self.runner)
        self.token = local_auth.read_token(tmp_path) or ""

    def session(self):
        frame_id = self.runner.create_session(self.project_id)
        for name in ("lab_create", "lab_execute"):
            self.store.set_permission_rule(
                scope="conversation",
                scope_id=frame_id,
                tool=name,
                pattern="*",
                decision="allow",
            )
        state = self.runner._state(frame_id, self.project_id)
        dispatcher = self.runner._ensure_runtime(state)
        return frame_id, dispatcher, build_host(dispatcher, mode="repl")

    def request(self, method, path, body=None):
        handler = object.__new__(self.handler_class)
        sent = {}

        def capture(status, payload, ctype, extra=None, security=None):
            sent.update(status=status, body=json.loads(payload.decode("utf-8")))

        handler._send = capture
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.client_address = ("127.0.0.1", 32123)
        handler.headers = {
            "Content-Length": "0",
            "Host": f"127.0.0.1:{self.cfg.port}",
            "X-Request-Id": "lab-w3-test",
            local_auth.TOKEN_HEADER: self.token,
        }
        handler._body = lambda: {} if body is None else body
        handler._route(method)
        return sent["status"], sent["body"]


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "1")
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    built = _Daemon(tmp_path)
    yield built
    built.runner.close()


def transfer(key, *, revision=0, litres=0.2):
    return {
        "operation": "transfer_liquid",
        "source": "beaker_1",
        "target": "extraction_vessel",
        "parameters": {"volume": {"value": litres, "unit": "L"}},
        "expected_revision": revision,
        "idempotency_key": key,
    }


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("door", ["web", "cli", "submit"])
@pytest.mark.parametrize("stop", [False, True])
def test_real_toy_completion_and_stop_refusal(daemon, monkeypatch, door, stop):
    from openai4s.lab import evidence
    from openai4s.lab.evaluation import Goal
    from tests.test_lab_completion import claim, finish

    # The real toy has no production chemistry goal. Only its goal definition
    # is injected; commands, receipts, observations, evaluation and doors are real.
    original = evidence.default_goal
    monkeypatch.setattr(
        evidence,
        "default_goal",
        lambda profile, **kw: (
            Goal(PROFILE, "toy_component", "extraction_vessel", 0.2, 0.9)
            if profile == PROFILE
            else original(profile, **kw)
        ),
    )
    fid, dispatcher, host = daemon.session()
    created = host.lab.create(TOY, PROFILE, seed=7, idempotency_key="completion")
    run_id = created["run"]["run_id"]
    executed = host.lab.execute(run_id, **transfer("move"))
    assert executed["command"]["state"] == "succeeded"
    if stop:
        host.lab.stop(run_id)
    else:
        ended = host.lab.execute(
            run_id, "end_experiment", expected_revision=1, idempotency_key="end"
        )
        assert ended["run"]["end_reason"] == "end_action"
    payload = claim(lab_runs=[{"run_id": run_id, "status": "completed"}])
    if door == "submit":
        output = {k: v for k, v in payload.items() if k != "completion_bullets"}
        if stop:
            refused = host.submit_output(
                output, completion_bullets=payload["completion_bullets"]
            )
            assert "not ended normally" in refused["error"]
            assert dispatcher.last_output is None
        else:
            assert host.submit_output(
                output, completion_bullets=payload["completion_bullets"]
            ) == {"status": "ok"}
            assert dispatcher.revalidate_pending_completion() is None
    else:
        completed, error = finish(door, dispatcher._completion_service, payload)
        if stop:
            assert completed is None and "not ended normally" in error
        else:
            assert completed["output"]["lab_runs"] == payload["lab_runs"]


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("available", [True, False, None])
def test_lab_seed_intro_is_available_only_and_cached(daemon, monkeypatch, available):
    runner = daemon.runner
    calls = []

    def devices(caller):
        calls.append(caller.root_frame_id)
        return [{"available": available}]

    monkeypatch.setattr(runner.lab_manager, "list_devices", devices)
    fid = runner.create_session(daemon.project_id)
    state = runner._state(fid, daemon.project_id)
    # Existing skills/memory have independent content; compare with exactly
    # the same seed path while only the Lab fragment is disabled.
    runner._lab_prompt_available = False
    runner._seed_messages(state)
    legacy = json.dumps(state.messages, ensure_ascii=False)
    state.messages = []
    del runner._lab_prompt_available
    runner._seed_messages(state)
    first = json.dumps(state.messages, ensure_ascii=False)
    fragment = '\\n\\nLab is simulation only; use the lab_* tools. First load_skill(\\"lab-simulation\\") for the recipe and evidence rules.'
    if available is True:
        assert fragment in first
        assert first.replace(fragment, "") == legacy
    else:
        assert first == legacy
    state.messages = []
    runner._seed_messages(state)
    assert json.dumps(state.messages, ensure_ascii=False) == first
    assert calls == [fid]


def _process(manager, run_id):
    live = manager._live[run_id]
    (session,) = live.port._sessions.values()
    return session.client.process


def test_rest_tools_and_sdk_share_one_manager_and_ledger(daemon):
    fid, dispatcher, host = daemon.session()
    base = f"/frames/{fid}/lab"
    status, created = daemon.request(
        "POST",
        f"{base}/runs",
        {"device_id": TOY, "profile": PROFILE, "seed": 4, "idempotency_key": "c1"},
    )
    assert status == 200, created
    run_id = created["run"]["run_id"]

    # The REST-created run is the tools' and the SDK's run too.
    observed = get_tool("lab_observe").invoke(dispatcher, {"run_id": run_id})
    assert observed["run"]["run_id"] == run_id
    native = get_tool("lab_execute").invoke(
        dispatcher, {"run_id": run_id, **transfer("k1")}
    )
    assert native["command"]["state"] == "succeeded", native
    command_id = native["command"]["command_id"]
    assert daemon.store.lab.get_command(command_id)["origin"] == "agent_tool"

    # One key, three entry points, one execution.
    replay = host.lab.execute(run_id, **{k: v for k, v in transfer("k1").items()})
    assert replay["command"]["command_id"] == command_id
    status, rest = daemon.request(
        "POST", f"{base}/runs/{run_id}/commands", transfer("k1")
    )
    assert status == 200 and rest["command"]["command_id"] == command_id
    run = daemon.store.lab.get_run(run_id)
    assert (run["revision"], run["step_count"], run["command_count"]) == (1, 1, 1)

    # The SDK-created run is listed by REST and stopped through it.
    sdk = host.lab.create(TOY, PROFILE, seed=5, idempotency_key="c2")
    status, overview = daemon.request("GET", base)
    assert status == 200
    assert {r["run_id"] for r in overview["runs"]} == {run_id, sdk["run"]["run_id"]}
    status, stopped = daemon.request(
        "POST", f"{base}/runs/{sdk['run']['run_id']}/stop", {}
    )
    assert status == 200 and stopped["run"]["status"] == "ended"
    after = get_tool("lab_status").invoke(dispatcher, {"run_id": sdk["run"]["run_id"]})
    assert after["run"]["end_reason"] == "stopped"

    # Exactly one manager behind every entry point.
    assert daemon.runner._session_lab.__getattr__("create_run").__self__ is (
        daemon.runner.lab_manager
    )


def test_tool_writes_reach_the_workbench_as_coalesced_hints(daemon):
    fid, dispatcher, host = daemon.session()
    created = get_tool("lab_create").invoke(
        dispatcher, {"device_id": TOY, "profile": PROFILE, "seed": 6}
    )
    run_id = created["run"]["run_id"]
    for index, key in enumerate(("a", "b", "c")):
        get_tool("lab_execute").invoke(
            dispatcher, {"run_id": run_id, **transfer(key, revision=index)}
        )
    deadline = time.monotonic() + 5
    hints = []
    while time.monotonic() < deadline:
        hints = [e for root, e in daemon.hub.events if e.get("type") == "lab_update"]
        if hints and hints[-1]["latest_event_seq"] == daemon.store.lab.latest_event_seq(
            fid
        ):
            break
        time.sleep(0.05)
    assert hints, "a tool-path write never reached the workbench"
    # A burst of writes is coalesced (250 ms trailing batches), not one per row.
    assert len(hints) < 4
    assert set(hints[-1]) == {
        "type",
        "root_frame_id",
        "frame_id",
        "run_id",
        "latest_event_seq",
    }
    assert hints[-1]["root_frame_id"] == fid and hints[-1]["run_id"] in {run_id, None}


def test_cli_and_child_dispatchers_have_no_lab(daemon):
    fid, _, _ = daemon.session()
    cli = build_dispatcher(daemon.cfg, frame_id=fid)
    with pytest.raises(RuntimeError) as caught:
        build_host(cli, mode="repl").lab.list()
    assert getattr(caught.value, "error_kind", None) == "provider_unavailable"
    from openai4s.host.delegation_policy import ChildExecutionPolicy

    unrestricted = ChildExecutionPolicy(
        restricted=False, allowed=frozenset(), permissions={}
    )
    assert not unrestricted.allows("lab_create")
    assert not unrestricted.allows("lab_list")


def test_tool_create_waits_for_and_respects_session_deletion(daemon):
    fid, dispatcher, host = daemon.session()
    with daemon.runner._lock:
        daemon.runner._deleting_sessions.add(fid)
    refused = get_tool("lab_create").invoke(
        dispatcher, {"device_id": TOY, "profile": PROFILE, "seed": 1}
    )
    assert refused["error_kind"] == "provider_unavailable"
    assert "deletion" in refused["error"]
    assert daemon.store.lab.list_runs(fid) == []
    assert not daemon.runner.lab_manager._live
    with daemon.runner._lock:
        daemon.runner._deleting_sessions.discard(fid)
    created = host.lab.create(TOY, PROFILE, seed=1)
    assert created["run"]["status"] == "ready"


def test_session_deletion_and_shutdown_reap_toy_processes(daemon):
    doomed, d1, _ = daemon.session()
    kept, d2, _ = daemon.session()
    run_a = get_tool("lab_create").invoke(d1, {"device_id": TOY, "profile": PROFILE})
    run_b = get_tool("lab_create").invoke(d2, {"device_id": TOY, "profile": PROFILE})
    manager = daemon.runner.lab_manager
    process_a = _process(manager, run_a["run"]["run_id"])
    process_b = _process(manager, run_b["run"]["run_id"])

    daemon.runner.delete_session(doomed)
    assert process_a.returncode is not None
    assert daemon.store.lab.get_run(run_a["run"]["run_id"]) is None
    assert process_b.returncode is None

    daemon.runner.close()
    assert process_b.returncode is not None


def test_deleting_a_session_never_waits_for_an_opening_provider(daemon):
    # A ChemGymRL open may take 180 s (cold JIT). Deletion only waits for the
    # admission check; the manager's tombstone discards the late opening.
    entered, release = threading.Event(), threading.Event()
    devices = []

    class SlowOpen(FakeExtractorDevice):
        closed: tuple = ()

        def open(self, request):
            entered.set()
            release.wait(10)
            return super().open(request)

        def close(self, session_id):
            self.closed = (*self.closed, session_id)
            return super().close(session_id)

    def factory():
        devices.append(SlowOpen())
        return devices[-1]

    daemon.runner.lab_manager._registry.register(
        replace(fake_registration(), port_factory=factory)
    )
    fid, _, _ = daemon.session()
    body = {
        "device_id": "fake.extractor.01",
        "profile": PROFILE,
        "idempotency_key": "s",
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(daemon.request, "POST", f"/frames/{fid}/lab/runs", body)
        assert entered.wait(10)
        started = time.monotonic()
        daemon.runner.delete_session(fid)
        elapsed = time.monotonic() - started
        release.set()
        status, created = pending.result(15)
    assert elapsed < 2, elapsed
    assert status in (409, 503), created
    assert not daemon.runner.lab_manager._live
    # The late opening was closed, not registered for a deleted session.
    assert len(devices) == 1 and devices[0].closed


def test_tool_and_rest_callers_agree_outside_team_mode(daemon):
    # An owner row left by an earlier team-mode run is not an identity in a
    # single-user daemon: a run the agent creates stays the workbench's run.
    fid, dispatcher, _ = daemon.session()
    daemon.store.team.set_session_owner(fid, "former-team-owner")
    created = get_tool("lab_create").invoke(
        dispatcher, {"device_id": TOY, "profile": PROFILE}
    )
    run_id = created["run"]["run_id"]
    assert daemon.store.lab.get_run(run_id)["owner_user_id"] is None
    status, overview = daemon.request("GET", f"/frames/{fid}/lab")
    assert status == 200 and [r["run_id"] for r in overview["runs"]] == [run_id]
    status, stopped = daemon.request(
        "POST", f"/frames/{fid}/lab/runs/{run_id}/stop", {}
    )
    assert status == 200 and stopped["run"]["status"] == "ended"
