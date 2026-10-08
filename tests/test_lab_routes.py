"""Real gateway adapters over the real Lab manager and explicit fake device.

Only the HTTP byte sink is replaced. Device faults use the fake port's public
fault hooks; no route response or Lab service is fabricated for the recorder.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.lab.models import CommandOrigin, LabCaller, LabError
from openai4s.server import gateway, local_auth, response_capture
from openai4s.storage import team as team_storage
from openai4s.storage.snapshots import revert_recovery_setting_key


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        return lambda event: self.broadcast(root_frame_id, event)

    def broadcast(self, root_frame_id, event):
        self.events.append((root_frame_id, event))

    def drop_frame(self, root_frame_id):
        pass


class _Device(FakeExtractorDevice):
    def __init__(self):
        super().__init__()
        self.executions = 0
        self.closed = []

    def execute(self, session_id, dispatch):
        self.executions += 1
        return super().execute(session_id, dispatch)

    def close(self, session_id):
        self.closed.append(session_id)
        return super().close(session_id)


class _Client:
    def __init__(self, tmp_path, *, team=False):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.cfg.team_mode = team
        self.hub = _Hub()
        self.runner = gateway.SessionRunner(self.cfg, self.hub)
        self.store = self.runner.store
        self.store.create_project(name="Lab routes", description="", context="")
        self.project_id = self.store.list_projects()[0]["project_id"]
        self.frame_id = self.runner.create_session(self.project_id)
        self.handler_class = gateway.make_handler(self.cfg, self.hub, self.runner)
        self.token = local_auth.read_token(tmp_path) or ""
        self.devices = []

        def factory():
            device = _Device()
            self.devices.append(device)
            return device

        self.runner.lab_manager._registry.register(
            replace(fake_registration(), port_factory=factory)
        )

    @property
    def base(self):
        return f"/frames/{self.frame_id}/lab"

    def request(self, method, path, body=None, *, cookie=None):
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
            "X-Request-Id": "lab-route-test",
        }
        if cookie:
            handler.headers["Cookie"] = cookie
        else:
            handler.headers[local_auth.TOKEN_HEADER] = self.token
        handler._body = lambda: {} if body is None else body
        handler._route(method)
        return sent["status"], sent["body"]

    def create(self, **changes):
        status, body = self.request("POST", f"{self.base}/runs", _create(**changes))
        assert status == 200, body
        return body

    def execute(self, run_id, **changes):
        return self.request(
            "POST", f"{self.base}/runs/{run_id}/commands", _command(**changes)
        )


def _create(**changes):
    return {
        "device_id": "fake.extractor.01",
        "profile": "toy-extract-v0",
        "seed": 7,
        "idempotency_key": "create-first",
        **changes,
    }


def _command(**changes):
    return {
        "operation": "mix_model",
        "source": "extraction_vessel",
        "target": None,
        "parameters": {"duration": {"value": 1, "unit": "model_time"}},
        "expected_revision": 0,
        "idempotency_key": "command-first",
        **changes,
    }


def _assert_safe(value):
    forbidden = {
        "evaluation",
        "reward",
        "ground_truth",
        "provider_action",
        "provider_action_json",
        "fencing_token",
        "approval_ref",
        "owner_user_id",
        "daemon_instance",
        "config",
        "interpreter",
        "interpreter_path",
    }
    if isinstance(value, dict):
        assert not forbidden.intersection(value), value.keys()
        for item in value.values():
            _assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe(item)
    elif isinstance(value, str):
        assert "/bin/python" not in value and "/provider-env/" not in value


@pytest.fixture
def client(tmp_path):
    node = _Client(tmp_path)
    try:
        yield node
    finally:
        node.runner.close()


@pytest.mark.parametrize(
    "route",
    [
        "overview",
        "device",
        "create",
        "detail",
        "commands",
        "observations",
        "execute",
        "reconcile",
        "stop",
        "events",
    ],
)
def test_each_lab_route_returns_its_real_success_shape(client, route):
    created = client.create()
    run_id = created["run"]["run_id"]
    status, executed = client.execute(run_id)
    assert status == 200 and executed["command"]["state"] == "succeeded"
    command_id = executed["command"]["command_id"]
    root = f"{client.base}/runs/{run_id}"
    requests = {
        "overview": ("GET", client.base, None, {"devices", "runs", "latest_event_seq"}),
        "device": (
            "GET",
            f"{client.base}/devices/fake.extractor.01?profile=toy-extract-v0",
            None,
            {"descriptor"},
        ),
        "create": (
            "POST",
            f"{client.base}/runs",
            _create(),
            {"run", "descriptor", "observation"},
        ),
        "detail": ("GET", root, None, {"run", "descriptor", "observation", "commands"}),
        "commands": (
            "GET",
            f"{root}/commands?after_seq=0&limit=1",
            None,
            {"commands", "next_after_seq"},
        ),
        "observations": (
            "GET",
            f"{root}/observations?after_sequence=-1&limit=2&full=true",
            None,
            {"observations", "next_after_sequence"},
        ),
        "execute": (
            "POST",
            f"{root}/commands",
            _command(),
            {"run", "command", "observation"},
        ),
        "reconcile": (
            "POST",
            f"{root}/commands/{command_id}/reconcile",
            {},
            {"run", "command", "observation"},
        ),
        "stop": (
            "POST",
            f"{root}/stop",
            {"reason": "test complete"},
            {"run", "stopped", "semantics"},
        ),
        "events": (
            "GET",
            f"{client.base}/events?after_seq=0&limit=100",
            None,
            {"events", "next_after_seq", "latest_event_seq"},
        ),
    }
    method, path, request, expected = requests[route]
    status, body = client.request(method, path, request)
    assert status == 200, body
    assert set(body) == expected
    _assert_safe(body)
    if route == "overview":
        assert body["runs"][0]["run_id"] == run_id and body["latest_event_seq"] > 0
    elif route == "device":
        assert body["descriptor"]["device_id"] == "fake.extractor.01"
    elif route in {"create", "detail", "execute", "reconcile", "stop"}:
        assert body["run"]["run_id"] == run_id
        if route in {"detail", "execute", "reconcile"}:
            assert body["observation"]["sequence"] == 1
        if route in {"execute", "reconcile"}:
            assert body["command"]["command_id"] == command_id
        if route == "detail":
            assert body["commands"][0]["command_id"] == command_id
        if route == "stop":
            assert body["stopped"] and body["run"]["status"] == "ended"
    elif route == "commands":
        assert body["commands"][0]["command_id"] == command_id
        assert body["next_after_seq"] == 1
    elif route == "observations":
        assert [row["sequence"] for row in body["observations"]] == [0, 1]
        assert body["next_after_sequence"] == 1
    elif route == "events":
        assert body["events"] and body["next_after_seq"] == body["latest_event_seq"]


@pytest.mark.parametrize(
    "endpoint,field",
    [
        ("runs", "options"),
        ("runs", "unknown"),
        ("commands", "options"),
        ("commands", "run_id"),
        ("stop", "unknown"),
        ("reconcile", "unknown"),
        ("runs", "missing_key"),
        ("commands", "missing_key"),
    ],
)
def test_mutation_bodies_are_closed_and_require_idempotency(client, endpoint, field):
    run_id = client.create()["run"]["run_id"]
    status, result = client.execute(run_id)
    assert status == 200
    command_id = result["command"]["command_id"]
    root = f"{client.base}/runs/{run_id}"
    paths = {
        "runs": f"{client.base}/runs",
        "commands": f"{root}/commands",
        "stop": f"{root}/stop",
        "reconcile": f"{root}/commands/{command_id}/reconcile",
    }
    body = (
        _create()
        if endpoint == "runs"
        else _command() if endpoint == "commands" else {}
    )
    if field == "missing_key":
        body.pop("idempotency_key")
    else:
        body[field] = {} if field == "options" else "unexpected"
    before = client.store.lab.get_run(run_id)["command_count"]
    status, body = client.request("POST", paths[endpoint], body)
    assert status == 422 and body["code"] == "invalid_parameters"
    assert body["status"] == 422 and body["request_id"] == "lab-route-test"
    assert client.store.lab.get_run(run_id)["command_count"] == before


@pytest.mark.parametrize(
    "suffix", ["", "/devices/fake.extractor.01", "/runs/missing", "/events"]
)
def test_unknown_session_is_404_in_single_user_mode(client, suffix):
    status, body = client.request("GET", f"/frames/unknown/lab{suffix}")
    assert status == 404 and body["status"] == 404


@pytest.mark.parametrize(
    "suffix",
    [
        "/devices/unknown",
        "/runs/unknown",
        "/runs/unknown/commands",
        "/runs/unknown/observations",
    ],
)
def test_unknown_lab_resource_is_404(client, suffix):
    status, body = client.request("GET", client.base + suffix)
    assert status == 404 and body["code"] in {"run_not_found", "device_not_found"}


def test_same_key_replays_and_changed_request_conflicts(client):
    first = client.create()
    replay = client.create()
    assert replay == first and len(client.devices) == 1
    run_id = first["run"]["run_id"]
    status, result = client.execute(run_id)
    assert status == 200
    assert client.execute(run_id) == (200, result)
    assert client.devices[0].executions == 1
    for path, body in [
        (f"{client.base}/runs", _create(seed=8)),
        (f"{client.base}/runs/{run_id}/commands", _command(operation="settle_model")),
    ]:
        status, error = client.request("POST", path, body)
        assert status == 409 and error["code"] == "idempotency_conflict"


def test_provider_capacity_failure_is_503(client):
    for index in range(4):
        client.create(idempotency_key=f"capacity-{index}")
    status, body = client.request(
        "POST", f"{client.base}/runs", _create(idempotency_key="capacity-overflow")
    )
    assert status == 503 and body["code"] == "provider_unavailable"
    assert body["status"] == 503 and body["request_id"] == "lab-route-test"
    assert len(client.devices) == 4
    _assert_safe(body)


def test_unknown_result_is_200_and_preserves_the_durable_command(client):
    run_id = client.create()["run"]["run_id"]
    client.devices[0].timeout_and_die_next()
    status, body = client.execute(run_id)
    assert status == 200 and body["command"]["state"] == "outcome_unknown"
    command_id = body["command"]["command_id"]
    assert client.store.lab.get_command(command_id)["state"] == "outcome_unknown"
    status, reconciled = client.request(
        "POST", f"{client.base}/runs/{run_id}/commands/{command_id}/reconcile", {}
    )
    assert status == 200 and reconciled["command"]["state"] == "outcome_unknown"
    assert client.devices[0].executions == 1
    _assert_safe(body)


def test_host_rejection_is_a_200_command_result(client):
    run_id = client.create()["run"]["run_id"]
    status, body = client.execute(run_id, operation="unknown_operation")
    assert status == 200 and body["command"]["state"] == "rejected"
    assert body["command"]["error_code"] == "unsupported_action"
    assert client.devices[0].executions == 0
    _assert_safe(body)


def test_lab_posts_respect_the_revert_write_barrier(client):
    client.runner.executions.submit(
        client.frame_id, owner="user", owner_id="revert-owner", language="python"
    )
    client.store.set_setting(
        revert_recovery_setting_key(client.frame_id), json.dumps({"state": "preparing"})
    )
    status, body = client.request("POST", f"{client.base}/runs", _create())
    assert status == 423 and body["status"] == 423
    assert not client.devices
    assert client.request("GET", client.base)[0] == 200


def test_team_visible_member_reads_owner_runs_but_cannot_mutate(tmp_path, monkeypatch):
    monkeypatch.setattr(team_storage, "PBKDF2_ITERATIONS", 1200)
    node = _Client(tmp_path, team=True)
    try:
        users = {
            name: node.store.team.create_user(
                username=name, password="fake-password", role="member"
            )
            for name in ("owner", "member", "outsider")
        }
        for name in ("owner", "member"):
            node.store.governance.set_member(
                node.project_id, users[name]["id"], "member"
            )
        node.store.team.set_session_owner(
            node.frame_id, users["owner"]["id"], project_id=node.project_id
        )
        cookies = {
            name: "os_user=" + node.store.team.create_auth_session(user["id"])
            for name, user in users.items()
        }
        status, made = node.request(
            "POST", f"{node.base}/runs", _create(), cookie=cookies["owner"]
        )
        assert status == 200, made
        run_id = made["run"]["run_id"]
        assert node.store.lab.get_run(run_id)["owner_user_id"] == users["owner"]["id"]
        status, body = node.request("GET", node.base, cookie=cookies["member"])
        assert status == 200 and body["runs"][0]["run_id"] == run_id
        assert (
            node.request("GET", f"{node.base}/runs/{run_id}", cookie=cookies["member"])[
                0
            ]
            == 200
        )
        status, body = node.request(
            "POST", f"{node.base}/runs/{run_id}/stop", {}, cookie=cookies["member"]
        )
        assert status == 403 and body["code"] == "owner_only"
        for method, path, request in [
            ("GET", node.base, None),
            ("POST", f"{node.base}/runs", _create()),
        ]:
            assert (
                node.request(method, path, request, cookie=cookies["outsider"])[0]
                == 404
            )
        assert not node.devices[0].closed
    finally:
        node.runner.close()


@pytest.mark.parametrize("action", ["close", "delete_session", "delete_project"])
def test_lifecycle_releases_provider_before_deleting_ledger(
    client, action, monkeypatch
):
    run_id = client.create()["run"]["run_id"]
    closed_with_ledger = []
    original_close = client.devices[0].close

    def close(session_id):
        closed_with_ledger.append(client.store.lab.get_run(run_id) is not None)
        return original_close(session_id)

    monkeypatch.setattr(client.devices[0], "close", close)
    if action == "close":
        client.runner.close()
        assert client.store.lab.get_run(run_id)["status"] == "ended"
    elif action == "delete_session":
        client.runner.delete_session(client.frame_id)
        assert client.store.lab.get_run(run_id) is None
    else:
        client.runner.delete_project(client.project_id)
        assert client.store.lab.get_run(run_id) is None
    assert client.devices[0].closed
    assert closed_with_ledger and all(closed_with_ledger)
    assert not client.devices[0]._sessions


def test_lab_capture_drives_all_eleven_real_routes(client):
    recorder = response_capture.Recorder()
    response_capture._drive_lab_surface(
        recorder,
        client.handler_class,
        client.runner,
        client.frame_id,
        {local_auth.TOKEN_HEADER: client.token},
    )
    assert not recorder.drive_failures
    from openai4s.server.lab_routes import ROUTES

    for route in ROUTES:
        key = f"{route.method} {route.pattern} [ok]"
        assert key in recorder.shapes, key


@pytest.mark.parametrize(
    "suffix",
    [
        "/commands?after_seq=-1",
        "/commands?limit=0",
        "/commands?limit=garbage",
        "/observations?after_sequence=-2",
        "/observations?full=perhaps",
    ],
)
def test_invalid_page_values_are_422(client, suffix):
    run_id = client.create()["run"]["run_id"]
    status, body = client.request("GET", f"{client.base}/runs/{run_id}{suffix}")
    assert status == 422 and body["code"] == "invalid_parameters"


def test_run_from_a_different_session_is_not_disclosed(client):
    run_id = client.create()["run"]["run_id"]
    other = client.runner.create_session(client.project_id)
    for method, suffix, body in [
        ("GET", "", None),
        ("GET", "/commands", None),
        ("GET", "/observations", None),
        ("POST", "/commands", _command()),
        ("POST", "/stop", {}),
        ("POST", "/export", {}),
    ]:
        status, error = client.request(
            method, f"/frames/{other}/lab/runs/{run_id}{suffix}", body
        )
        assert status == 404 and error["code"] == "run_not_found"
    assert not client.devices[0].closed and client.devices[0].executions == 0


@pytest.mark.parametrize("scope", ["session", "project"])
def test_create_refuses_a_session_or_project_already_being_deleted(client, scope):
    deleting = (
        client.runner._deleting_sessions
        if scope == "session"
        else client.runner._deleting_projects
    )
    key = client.frame_id if scope == "session" else client.project_id
    with client.runner._lock:
        deleting.add(key)
    try:
        status, body = client.request("POST", f"{client.base}/runs", _create())
        assert status == 409 and body["code"] == "conflict"
        assert not client.devices
    finally:
        with client.runner._lock:
            deleting.discard(key)


def test_reconcile_queries_the_same_unknown_command_without_redispatch(client):
    run_id = client.create()["run"]["run_id"]
    client.devices[0].lose_response_next()
    client.devices[0].fail_query_next()
    caller = LabCaller(
        client.frame_id, client.frame_id, None, CommandOrigin.MANUAL_UI, None, None
    )
    with pytest.raises(LabError) as caught:
        client.runner.lab_manager.execute(caller, {"run_id": run_id, **_command()})
    assert caught.value.code.value == "outcome_unknown"
    command_id = caught.value.details["command_id"]
    status, body = client.request(
        "POST", f"{client.base}/runs/{run_id}/commands/{command_id}/reconcile", {}
    )
    assert status == 200 and body["command"]["state"] == "succeeded"
    assert body["command"]["command_id"] == command_id
    assert body["run"]["revision"] == 1 and client.devices[0].executions == 1
    _assert_safe(body)


def test_real_ledger_write_refusal_is_503_without_opening_provider(client):
    # The ledger refuses nesting inside another SQLite transaction. This is
    # its real persistence_unavailable boundary, without replacing a service.
    with client.store._lock:
        client.store._conn.execute("BEGIN IMMEDIATE")
        try:
            status, body = client.request("POST", f"{client.base}/runs", _create())
        finally:
            client.store._conn.rollback()
    assert status == 503 and body["code"] == "persistence_unavailable"
    assert not client.devices
    _assert_safe(body)


def test_terminal_command_accepts_omitted_optional_slots(client):
    run_id = client.create()["run"]["run_id"]
    status, body = client.request(
        "POST",
        f"{client.base}/runs/{run_id}/commands",
        {
            "operation": "end_experiment",
            "expected_revision": 0,
            "idempotency_key": "end",
        },
    )
    assert status == 200 and body["command"]["state"] == "succeeded"
    assert body["run"]["end_reason"] == "end_action"


def test_command_routes_give_the_workbench_full_sensor_arrays(client, monkeypatch):
    # CONTRACT §10: the UI view does not summarize arrays. The manager's command
    # results are the agent view (GenWurtz `layers` 3x100 arrives summarized),
    # so the execute and reconcile routes re-read that observation in full.
    import openai4s.lab.manager as manager_module

    summarize = manager_module.project_observation
    monkeypatch.setattr(
        manager_module,
        "project_observation",
        lambda obs, **kw: summarize(obs, **{**kw, "max_elements": 4}),
    )
    caller = LabCaller(
        client.frame_id, client.frame_id, None, CommandOrigin.MANUAL_UI, None, None
    )
    created = client.create()
    run_id = created["run"]["run_id"]
    status, executed = client.execute(run_id)
    assert status == 200 and executed["command"]["state"] == "succeeded"
    client.devices[0].lose_response_next()
    client.devices[0].fail_query_next()
    with pytest.raises(LabError) as caught:
        client.runner.lab_manager.execute(
            caller,
            {
                "run_id": run_id,
                **_command(idempotency_key="second", expected_revision=1),
            },
        )
    command_id = caught.value.details["command_id"]
    status, reconciled = client.request(
        "POST", f"{client.base}/runs/{run_id}/commands/{command_id}/reconcile", {}
    )
    assert status == 200 and reconciled["command"]["state"] == "succeeded"
    for envelope in (created, executed, reconciled):
        layers = next(
            c for c in envelope["observation"]["channels"] if c["name"] == "layers"
        )
        assert isinstance(layers["value"], list), layers["value"]
    # The agent view itself is still summarized: only the UI routes widen it.
    agent = client.runner.lab_manager.observe(caller, run_id, full=False)["observation"]
    layers = next(c for c in agent["channels"] if c["name"] == "layers")
    assert layers["value"]["truncated"] is True


def _wide_lab_sensor(client, run_id):
    descriptor = client.store.lab.get_run(run_id)["descriptor"]
    descriptor["observation_channels"][0]["shape"] = [300]
    descriptor["observation_channels"][0].pop("axes", None)
    client.store._conn.execute(
        "UPDATE lab_runs SET descriptor_json=? WHERE run_id=?",
        (json.dumps(descriptor), run_id),
    )
    for row in client.store.lab.list_observations(run_id):
        channels = row["channels"]
        channels[0].update(shape=[300], value=list(range(300)))
        client.store._conn.execute(
            "UPDATE lab_observations SET channels_json=? WHERE observation_id=?",
            (json.dumps(channels), row["observation_id"]),
        )
    client.store._conn.commit()


def _export_files(client, result):
    files = {}
    for item in result["artifacts"]:
        version = client.store.version_meta(item["version_id"])
        assert version["artifact_id"] == item["artifact_id"]
        raw = Path(version["snapshot_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["checksum"]
        files[item["kind"]] = raw.decode("utf-8")
    return files


def test_lab_export_rest_exact_evidence_and_explicit_truth(client):
    run_id = client.create()["run"]["run_id"]
    assert client.execute(run_id)[0] == 200
    _wide_lab_sensor(client, run_id)
    client.store._conn.execute(
        "UPDATE lab_evaluations SET reward=?,ground_truth_json=? WHERE run_id=?",
        (987654.321987, json.dumps({"private_composition": 876543.219876}), run_id),
    )
    client.store._conn.commit()
    path = f"{client.base}/runs/{run_id}/export"
    status, result = client.request("POST", path)
    assert status == 200, result
    assert result["command_count"] == 1 and result["observation_count"] == 2
    assert result["include_evaluation"] is False
    assert "channels" not in json.dumps(result)
    files = _export_files(client, result)
    assert set(files) == {"actions", "observations_json", "observations_csv", "report"}
    for content in files.values():
        for secret in (
            '"reward"',
            '"ground_truth"',
            '"evaluation"',
            '"provider_action"',
            "private_composition",
            "987654.321987",
            "876543.219876",
        ):
            assert secret not in content
    commands = client.request("GET", f"{client.base}/runs/{run_id}/commands")[1][
        "commands"
    ]
    assert [json.loads(row) for row in files["actions"].splitlines()] == commands
    observations = client.request("GET", f"{client.base}/runs/{run_id}/observations")[
        1
    ]["observations"]
    observation_version = next(
        a["version_id"] for a in result["artifacts"] if a["kind"] == "observations_json"
    )
    assert all(o["artifact_version_id"] == observation_version for o in observations)
    assert json.loads(files["observations_json"])["observations"] == [
        {k: v for k, v in o.items() if k != "artifact_version_id"} for o in observations
    ]
    csv_rows = list(csv.DictReader(io.StringIO(files["observations_csv"])))
    expected_channels = [(o, c) for o in observations for c in o["channels"]]
    assert len(csv_rows) == len(expected_channels)
    for actual, (obs, channel) in zip(csv_rows, expected_channels):
        assert actual["observation_id"] == obs["observation_id"]
        assert actual["command_id"] == (obs["command_id"] or "")
        assert json.loads(actual["value_json"]) == channel["value"]
    assert observation_version in files["report"]
    for item in result["artifacts"]:
        assert len(client.store.list_versions(item["artifact_id"])) == 1
    status, repeated = client.request("POST", path)
    assert status == 200
    for first, second in zip(result["artifacts"], repeated["artifacts"]):
        assert first["artifact_id"] == second["artifact_id"]
        assert first["version_id"] != second["version_id"]
        assert len(client.store.list_versions(first["artifact_id"])) == 2
    assert all(
        row["artifact_version_id"] == observation_version
        for row in client.store.lab.list_observations(run_id)
    )
    status, explicit = client.request("POST", path, {"include_evaluation": True})
    assert status == 200
    # Truth goes to the requesting person only, inline for the browser to
    # download. It is never an Artifact in the session workspace, where the
    # agent could read it (CONTRACT §3.9).
    explicit_files = _export_files(client, explicit)
    assert set(explicit_files) == {
        "actions",
        "observations_json",
        "observations_csv",
        "report",
    }
    truth = explicit["ground_truth"]
    assert truth["filename"] == f"{run_id}-simulation-ground-truth.json"
    assert "Simulation ground truth (仿真真值)" in truth["content"]
    assert "987654.321987" in truth["content"] and "876543.219876" in truth["content"]
    assert "separate download" in explicit_files["report"]
    st = client.runner._state(client.frame_id, client.project_id)
    for written in st.workspace.rglob("*"):
        if written.is_file():
            content = written.read_bytes()
            assert b"987654.321987" not in content and b"876543" not in content
    assert client.devices[0].executions == 1


@pytest.mark.parametrize(
    "body",
    [
        {"include_evaluation": "false"},
        {"include_evaluation": 1},
        {"include_evaluation": None},
        {"unexpected": True},
    ],
)
def test_lab_export_real_handler_refuses_invalid_flags(client, body):
    run_id = client.create()["run"]["run_id"]
    status, result = client.request("POST", f"{client.base}/runs/{run_id}/export", body)
    assert status == 422 and result["code"] == "invalid_parameters"
    assert result["status"] == 422
    assert client.request("POST", f"{client.base}/runs/missing/export")[0] == 404


def test_lab_export_tool_sdk_rest_capture_once(client):
    from openai4s.sdk.host import build_host
    from openai4s.tools.registry import get_tool

    run_id = client.create()["run"]["run_id"]
    _wide_lab_sensor(client, run_id)
    st = client.runner._state(client.frame_id, client.project_id)
    client.runner._ensure_runtime(st)
    dispatcher = st.dispatcher
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="allow",
    )
    call = {
        "id": "lab-export-native",
        "name": "lab_export",
        "arguments": {"run_id": run_id},
    }
    native = client.runner._invoke_control_with_artifacts(
        st,
        call,
        lambda e: None,
        lambda: get_tool("lab_export").invoke(dispatcher, call["arguments"]),
    )
    assert "error" not in native, native
    _export_files(client, native)
    assert all(
        len(client.store.list_versions(a["artifact_id"])) == 1
        for a in native["artifacts"]
    )
    before = client.runner.artifacts.snapshot(st.workspace)
    with st.trusted_capture.capture(), dispatcher.bind_artifact_receipt_scope():
        sdk = build_host(dispatcher, mode="repl").lab.export(run_id)
        # Available synchronously, before the enclosing Cell capture.
        _export_files(client, sdk)
        captured = client.runner.artifacts.capture(
            st, 0, None, before, lambda e: None, language="native"
        )
    assert captured.artifacts == []
    observed = build_host(dispatcher, mode="repl").lab.observe(run_id)["observation"]
    assert observed["channels"][0]["value"] == {
        "shape": [300],
        "summary": {"min": 0, "max": 299, "mean": 149.5},
        "truncated": True,
    }
    assert observed["artifact_version_id"] == next(
        a["version_id"] for a in native["artifacts"] if a["kind"] == "observations_json"
    )
    assert all(
        len(client.store.list_versions(a["artifact_id"])) == 2 for a in sdk["artifacts"]
    )
    status, rest = client.request("POST", f"{client.base}/runs/{run_id}/export")
    assert status == 200
    assert native.keys() == sdk.keys() == rest.keys()
    for kind in ("actions", "observations_json", "observations_csv"):
        assert (
            _export_files(client, native)[kind]
            == _export_files(client, sdk)[kind]
            == _export_files(client, rest)[kind]
        )
    assert client.devices[0].executions == 0


def test_lab_observation_artifact_bindings_are_atomic_and_immutable(client):
    run_id = client.create()["run"]["run_id"]
    first = client.request("POST", f"{client.base}/runs/{run_id}/export")[1]
    version = first["artifacts"][0]["version_id"]
    other_run = client.create(idempotency_key="second-run")["run"]["run_id"]
    obs = client.store.lab.latest_observation(other_run)
    with pytest.raises(LabError):
        client.store.lab.attach_observation_artifacts(
            other_run, {obs["observation_id"]: version, "missing": version}
        )
    assert client.store.lab.latest_observation(other_run)["artifact_version_id"] is None
    with pytest.raises(LabError):
        client.store.lab.attach_observation_artifacts(
            run_id, {obs["observation_id"]: version}
        )
    with pytest.raises(LabError):
        client.store.lab.attach_observation_artifacts(
            other_run, {obs["observation_id"]: "missing-version"}
        )
    client.store.lab.attach_observation_artifacts(
        other_run, {obs["observation_id"]: version}
    )
    client.store.lab.attach_observation_artifacts(
        other_run, {obs["observation_id"]: version}
    )
    with pytest.raises(LabError, match="already attached"):
        client.store.lab.attach_observation_artifacts(
            other_run, {obs["observation_id"]: first["artifacts"][1]["version_id"]}
        )
    assert (
        client.store.lab.latest_observation(other_run)["artifact_version_id"] == version
    )


def test_lab_export_permission_and_foreground_scope_are_required(client):
    from openai4s.sdk.host import build_host

    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    client.runner._ensure_runtime(st)
    host = build_host(st.dispatcher, mode="repl")
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="deny",
    )
    with pytest.raises(RuntimeError, match="Permission"):
        host.lab.export(run_id)
    assert client.store.lab.latest_observation(run_id)["artifact_version_id"] is None
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="allow",
    )
    # A refusal before anything was written is reported as such, not as a
    # failed commit (review: every refusal used to read "could not commit").
    with pytest.raises(RuntimeError, match="foreground execution scope") as caught:
        host.lab.export(run_id)
    assert caught.value.error_kind == "resource_busy"
    assert not list(st.workspace.glob("lab-*-observations.json"))


@pytest.mark.stubbed_backend
def test_lab_export_capture_failure_never_attaches_uncommitted_evidence(
    client, monkeypatch
):
    run_id = client.create()["run"]["run_id"]
    register = client.runner.artifacts.register_file

    def fail_observations(session, path, *args, **kwargs):
        if path.name.endswith("observations.json"):
            raise OSError("private payload must not escape")
        return register(session, path, *args, **kwargs)

    monkeypatch.setattr(client.runner.artifacts, "register_file", fail_observations)
    status, result = client.request("POST", f"{client.base}/runs/{run_id}/export")
    assert status == 503 and result["code"] == "persistence_unavailable"
    assert "private payload" not in json.dumps(result)
    assert client.store.lab.latest_observation(run_id)["artifact_version_id"] is None


def test_lab_export_does_not_follow_existing_workspace_links(client, tmp_path):
    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    st.workspace.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    filename = (
        "lab-" + hashlib.sha256(run_id.encode()).hexdigest()[:24] + "-observations.json"
    )
    (st.workspace / filename).symlink_to(outside)
    status, result = client.request("POST", f"{client.base}/runs/{run_id}/export")
    assert status == 200, result
    assert outside.read_text("utf-8") == "untouched"
    assert not (st.workspace / filename).is_symlink()
    _export_files(client, result)


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("rewrite_after", ["register", "claim"])
def test_lab_export_failed_verification_refuses_enclosing_capture(
    client, monkeypatch, rewrite_after
):
    from openai4s.sdk.host import build_host
    from openai4s.server.artifacts import ArtifactOperationError

    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    client.runner._ensure_runtime(st)
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="allow",
    )
    artifacts = client.runner.artifacts
    register = artifacts.register_file
    claim = artifacts.claim_delegated_artifacts
    committed = []
    changed = b"concurrent workspace rewrite\n"

    def rewrite_registered(session, path, *args, **kwargs):
        record = register(session, path, *args, **kwargs)
        committed.append(record)
        if rewrite_after == "register":
            path.write_bytes(changed)
        return record

    def rewrite_claimed(records, *, workspace):
        claim(records, workspace=workspace)
        if rewrite_after == "claim":
            Path(records[0]["storage_path"]).write_bytes(changed)

    monkeypatch.setattr(artifacts, "register_file", rewrite_registered)
    monkeypatch.setattr(artifacts, "claim_delegated_artifacts", rewrite_claimed)
    before = artifacts.snapshot(st.workspace)
    with st.trusted_capture.capture(), st.dispatcher.bind_artifact_receipt_scope():
        with pytest.raises(RuntimeError, match="could not commit all evidence"):
            build_host(st.dispatcher, mode="repl").lab.export(run_id)
        assert len(committed) == 1
        row = committed[0]
        version = client.store.version_meta(row["version_id"])
        assert Path(version["snapshot_path"]).read_bytes() != changed
        assert Path(row["storage_path"]).read_bytes() == changed
        # No success claim may hide bytes absent from the committed snapshot.
        with pytest.raises(ArtifactOperationError, match="capture failed"):
            artifacts.capture(
                st, 0, None, before, lambda event: None, language="native"
            )
    assert client.store.lab.latest_observation(run_id)["artifact_version_id"] is None


def test_lab_export_refuses_while_a_turn_owns_the_workspace(client):
    # Like upload/edit/restore, a person's export is refused while a turn or
    # execution owns the workspace, instead of being registered twice and
    # attributed to that turn's Artifact delta.
    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    path = f"{client.base}/runs/{run_id}/export"
    assert st.turn_lock.acquire(blocking=False)
    try:
        status, body = client.request("POST", path)
    finally:
        st.turn_lock.release()
    assert status == 409 and body["code"] == "trusted_capture_busy", body
    assert not list(st.workspace.glob("lab-*"))
    assert client.store.lab.latest_observation(run_id)["artifact_version_id"] is None
    status, body = client.request("POST", path)
    assert status == 200, body


def test_the_agent_can_never_obtain_simulation_truth(client):
    from openai4s.sdk.host import build_host
    from openai4s.tools.registry import get_tool

    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    client.runner._ensure_runtime(st)
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="allow",
    )
    tool = get_tool("lab_export")
    assert "include_evaluation" not in tool.parameters["properties"]
    assert tool.validation_error({"run_id": run_id, "include_evaluation": True})
    # A raw host call bypassing the SDK still meets the tool's closed schema.
    refused = st.dispatcher(
        "lab_export", [{"run_id": run_id, "include_evaluation": True}]
    )
    assert refused["error_kind"] == "invalid_parameters"
    host = build_host(st.dispatcher, mode="repl")
    with pytest.raises(TypeError):
        host.lab.export(run_id, include_evaluation=True)
    assert not list(st.workspace.glob("lab-*"))


def test_lab_export_waits_for_approval_before_writing_anything(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from openai4s.permissions import broker
    from openai4s.tools.registry import get_tool

    run_id = client.create()["run"]["run_id"]
    st = client.runner._state(client.frame_id, client.project_id)
    client.runner._ensure_runtime(st)
    client.store.set_permission_rule(
        scope="conversation",
        scope_id=client.frame_id,
        tool="lab_export",
        pattern="*",
        decision="ask",
    )
    pending, cancelled, events = Event(), Event(), []

    def emit(event):
        if event.get("type") == "await_permission":
            events.append(event)
            pending.set()

    permission_broker = broker()
    permission_broker.register_channel(
        client.frame_id, emit, cancel_event=cancelled, store=client.store
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                get_tool("lab_export").invoke, st.dispatcher, {"run_id": run_id}
            )
            assert pending.wait(10)
            assert not list(st.workspace.glob("lab-*"))
            latest = client.store.lab.latest_observation(run_id)
            assert latest["artifact_version_id"] is None
            assert permission_broker.resolve(events[-1]["decision_id"], allow=False)
            result = future.result(timeout=10)
    finally:
        cancelled.set()
        permission_broker.unregister_channel(client.frame_id)
    assert "Permission denied" in result["error"]
    assert not list(st.workspace.glob("lab-*"))


@pytest.mark.stubbed_backend
def test_a_partial_export_still_announces_the_versions_it_committed(
    client, monkeypatch
):
    run_id = client.create()["run"]["run_id"]
    register = client.runner.artifacts.register_file

    def fail_csv(session, path, *args, **kwargs):
        if path.name.endswith("observations.csv"):
            raise OSError("disk full")
        return register(session, path, *args, **kwargs)

    monkeypatch.setattr(client.runner.artifacts, "register_file", fail_csv)
    client.hub.events.clear()
    status, body = client.request("POST", f"{client.base}/runs/{run_id}/export")
    assert status == 503 and body["code"] == "persistence_unavailable"
    created = [
        event
        for _root, event in client.hub.events
        if event.get("type") == "artifact_created"
    ]
    assert len(created) == 2, client.hub.events


@pytest.mark.parametrize("origin", ["agent_tool", "host_sdk", "system"])
def test_only_the_workbench_origin_may_request_truth(origin):
    from openai4s.host.lab import LabService
    from openai4s.lab.models import CommandOrigin, LabCaller

    calls = []
    caller = LabCaller("root", "root", None, CommandOrigin(origin), None, None)
    service = LabService(lambda: object(), lambda: caller)
    service.exporter = lambda *args: calls.append(args) or {"ok": True}
    refused = service.call("export", {"run_id": "r", "include_evaluation": True})
    assert refused["error_kind"] == "invalid_parameters"
    assert "workbench" in refused["error"] and not calls
    assert service.call("export", {"run_id": "r"}) == {"ok": True}
    workbench = LabCaller("root", "root", None, CommandOrigin.MANUAL_UI, None, None)
    service = LabService(lambda: object(), lambda: workbench)
    service.exporter = lambda *args: calls.append(args) or {"ok": True}
    assert service.call("export", {"run_id": "r", "include_evaluation": True})
    assert calls[-1][2] is True
