"""Cross-session "needs attention" aggregation.

The dashboard cannot poll each Session for running/queued work, pending
approvals, recoverable failures, view-only imports, or live remote compute
without either missing facts or doing work a GET must never do. This module
drives the real handler: six fixtures produce one card each, a colleague's
Session is invisible, the cursor is bound to caller scope, a 50-Session page
stays under 200ms, and opening the list does not spawn a kernel, call a
provider, retry, approve, or harvest.
"""

from __future__ import annotations

import ast
import inspect
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import attention, attention_routes, compute_tasks
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.session_package import session_import_quarantine_key
from openai4s.server.team_auth import TEAM_COOKIE, TeamAuthService
from openai4s.storage import team as team_mod

DOCK_FOR = {
    "running": "timeline",
    "queued": "timeline",
    "approval": "security",
    "recovery": "recovery",
    "blocked": "recovery",
    "compute": "compute",
}
ITEM_KEYS = {
    "id",
    "source_kind",
    "source_id",
    "state",
    "severity",
    "frame_id",
    "project_id",
    "title",
    "updated_at",
    "target",
    "action_hint",
}
URL_KEYS = {"url", "href", "uri", "link", "path"}
P95_RUNS = 30
P95_WARMUP = 3
P95_BUDGET_MS = 200.0


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    def __init__(self, tmp_path, *, team_mode: bool = False):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        if team_mode:
            self.cfg.team_mode = True
        self.runner = gateway_mod.SessionRunner(
            self.cfg, _Hub(), start_idle_sweeper=False
        )
        self.store = self.runner.store
        self.store.create_project(name="attention", description="", context="")
        self.project_id = [p["project_id"] for p in self.store.list_projects()][0]
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""

    def close(self):
        self.runner.close()

    def session(self, name: str) -> str:
        frame_id = self.runner.create_session(self.project_id)
        self.store.update_frame(frame_id, name=name)
        return frame_id

    def get(self, path: str, *, cookie: str | None = None, token: str | None = None):
        return self._call("GET", path, cookie=cookie, token=token)

    def _call(
        self,
        method: str,
        path: str,
        *,
        cookie: str | None = None,
        token: str | None = None,
        body: dict | None = None,
    ):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-attention"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None, security=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = method
        handler.path = f"/api/v1{path}"
        headers = {"Content-Length": "0"}
        if cookie:
            headers["Cookie"] = f"{TEAM_COOKIE}={cookie}"
        else:
            headers[local_auth.TOKEN_HEADER] = (
                token if token is not None else self._token
            )
        handler.headers = headers
        if body is not None:
            handler._body = lambda: body
        handler._route(method)
        return sent["code"], sent["body"]


@pytest.fixture
def client(tmp_path):
    node = _Client(tmp_path)
    try:
        yield node
    finally:
        node.close()


def _seed_running(client: _Client, name: str = "kind-running") -> str:
    frame_id = client.session(name)
    client.runner.executions.submit(frame_id, owner="agent", owner_id="turn-running")
    return frame_id


def _seed_queued(client: _Client, name: str = "kind-queued") -> str:
    frame_id = client.session(name)
    client.runner.executions.submit(frame_id, owner="recovery", owner_id="rec-hold")
    client.runner.executions.submit(frame_id, owner="agent", owner_id="turn-queued")
    return frame_id


def _seed_approval(client: _Client, name: str = "kind-approval") -> str:
    frame_id = client.session(name)
    client.store.create_permission_request(
        decision_id=f"perm-{frame_id}",
        root_frame_id=frame_id,
        frame_id=frame_id,
        project_id=client.project_id,
        tool="bash",
        target="echo",
        payload={"decision_id": f"perm-{frame_id}"},
    )
    return frame_id


def _seed_recovery(client: _Client, name: str = "kind-recovery") -> str:
    frame_id = client.session(name)
    client.store.append_recovery_event(
        recovery_id=f"recovery-{frame_id}",
        root_frame_id=frame_id,
        branch_id=frame_id,
        phase="validate",
        status="failed",
        detail={"missing": ["model"]},
    )
    return frame_id


def _seed_blocked(client: _Client, name: str = "kind-blocked") -> str:
    frame_id = client.session(name)
    client.store.set_setting(
        session_import_quarantine_key(frame_id),
        json.dumps({"state": "quarantined", "reason": "test_import"}),
    )
    return frame_id


def _seed_compute(
    client: _Client, name: str = "kind-compute", *, status: str = "unknown"
) -> str:
    frame_id = client.session(name)
    owner_key = attention.workspace_key_for(client.runner, frame_id)
    job_id = f"job-{frame_id}"
    client.store.create_compute_job(
        job_id=job_id,
        provider="byoc-test",
        status="queued",
        owner_key=owner_key,
    )
    if status == "unknown":
        client.store.update_compute_job(job_id, status="unknown")
    elif status == "succeeded":
        client.store.update_compute_job(job_id, status="staging")
        client.store.update_compute_job(job_id, status="succeeded", terminal_at=1)
    else:
        for step in ("staging", status):
            client.store.update_compute_job(job_id, status=step)
    return frame_id


def _seed_six(client: _Client) -> dict[str, str]:
    return {
        "running": _seed_running(client),
        "queued": _seed_queued(client),
        "approval": _seed_approval(client),
        "recovery": _seed_recovery(client),
        "blocked": _seed_blocked(client),
        "compute": _seed_compute(client),
    }


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def _walk_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(_walk_strings(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_walk_strings(item))
    return out


def test_six_kind_fixtures_each_produce_exactly_one_card(client):
    frames = _seed_six(client)
    status, body = client.get("/attention")
    assert status == 200, body
    items = body["items"]
    kinds = [item["source_kind"] for item in items]
    assert sorted(kinds) == sorted(DOCK_FOR), kinds
    by_kind = {item["source_kind"]: item for item in items}
    assert len(by_kind) == 6
    assert by_kind["running"]["frame_id"] == frames["running"]
    assert by_kind["queued"]["frame_id"] == frames["queued"]
    assert by_kind["queued"]["action_hint"].startswith("queue:")
    assert by_kind["approval"]["frame_id"] == frames["approval"]
    assert by_kind["approval"]["state"] == "pending"
    assert by_kind["recovery"]["frame_id"] == frames["recovery"]
    assert by_kind["recovery"]["state"] == "failed"
    assert by_kind["blocked"]["frame_id"] == frames["blocked"]
    assert by_kind["blocked"]["state"] == "view_only"
    assert by_kind["compute"]["frame_id"] == frames["compute"]
    assert by_kind["compute"]["state"] == "unknown"
    for item in items:
        assert set(item) == ITEM_KEYS
        assert set(item["target"]) == {"surface", "dock", "frame_id"}
        assert item["target"]["surface"] == "session"
        assert item["target"]["frame_id"] == item["frame_id"]


def test_completed_and_idle_sessions_yield_zero_cards(client):
    idle = client.session("idle-session")
    done = _seed_compute(client, name="completed-compute", status="succeeded")
    status, body = client.get("/attention")
    assert status == 200, body
    frames = {item["frame_id"] for item in body["items"]}
    assert idle not in frames
    assert done not in frames
    assert body["items"] == []


def test_pending_approval_appears_within_one_poll_window(client):
    started = time.perf_counter()
    frame_id = _seed_approval(client, name="poll-approval")
    status, body = client.get("/attention")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert status == 200, body
    approvals = [
        item
        for item in body["items"]
        if item["source_kind"] == "approval" and item["frame_id"] == frame_id
    ]
    assert len(approvals) == 1
    assert elapsed_ms < 4000, f"pending approval took {elapsed_ms:.1f}ms"


def test_exact_dock_closed_target_set(client):
    _seed_six(client)
    status, body = client.get("/attention")
    assert status == 200, body
    for item in body["items"]:
        assert item["target"]["dock"] == DOCK_FOR[item["source_kind"]]
        assert item["target"]["surface"] in attention.SURFACES
        assert item["target"]["dock"] in attention.DOCKS


def test_response_has_zero_url_fields_and_no_secrets(client):
    _seed_six(client)
    secret_frame = client.session("api_key=sk-attention-secret-value")
    client.store.create_permission_request(
        decision_id="perm-secret-title",
        root_frame_id=secret_frame,
        frame_id=secret_frame,
        project_id=client.project_id,
        tool="bash",
        target="/Users/secret/bin/job",
    )
    status, body = client.get("/attention")
    assert status == 200, body
    keys = {key.lower() for key in _walk_keys(body)}
    assert not (keys & URL_KEYS), keys & URL_KEYS
    blob = json.dumps(body)
    assert "sk-attention-secret-value" not in blob
    assert "http://" not in blob.lower()
    assert "https://" not in blob.lower()
    assert "/Users/" not in blob
    assert "Traceback" not in blob
    for text in _walk_strings(body):
        assert "pid=" not in text.lower()


def test_cursor_is_bound_to_caller_scope(client):
    _seed_six(client)
    status, first = client.get("/attention?limit=2")
    assert status == 200, first
    assert first["has_more"] is True
    cursor = first["next_cursor"]
    assert cursor
    status, second = client.get(f"/attention?limit=2&cursor={cursor}")
    assert status == 200, second
    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)
    seen = list(first["items"]) + list(second["items"])
    while second["has_more"]:
        status, second = client.get(
            f"/attention?limit=2&cursor={second['next_cursor']}"
        )
        assert status == 200, second
        seen.extend(second["items"])
    assert len(seen) == 6
    assert len({item["id"] for item in seen}) == 6

    import base64

    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    payload["f"] = "0" * 32
    tampered = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    status, error = client.get(f"/attention?limit=2&cursor={tampered}")
    assert status == 400
    assert error.get("code") == "invalid_cursor"

    status, error = client.get("/attention?cursor=not-a-cursor")
    assert status == 400
    assert error.get("code") == "invalid_cursor"


def test_cross_user_zero_leakage(tmp_path, monkeypatch):
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)
    node = _Client(tmp_path, team_mode=True)
    try:
        alice = node.store.team.create_user(
            username="alice", password="fake-pw-a", role="member"
        )
        bob = node.store.team.create_user(
            username="bob", password="fake-pw-b", role="member"
        )
        alice_session = node.runner.create_session(
            node.project_id, owner_user_id=alice["id"]
        )
        node.store.update_frame(alice_session, name="alice-pending")
        node.store.create_permission_request(
            decision_id="perm-alice-only",
            root_frame_id=alice_session,
            frame_id=alice_session,
            project_id=node.project_id,
            tool="bash",
            target="echo",
        )
        bob_session = node.runner.create_session(
            node.project_id, owner_user_id=bob["id"]
        )
        node.store.update_frame(bob_session, name="bob-idle")
        auth = TeamAuthService(node.store)
        alice_token, _alice_user = auth.login("alice", "fake-pw-a", "127.0.0.1")
        bob_token, _bob_user = auth.login("bob", "fake-pw-b", "127.0.0.1")
        assert alice_token and bob_token

        status, alice_body = node.get("/attention", cookie=alice_token)
        assert status == 200, alice_body
        alice_frames = {item["frame_id"] for item in alice_body["items"]}
        assert alice_session in alice_frames
        assert bob_session not in alice_frames

        status, bob_body = node.get("/attention", cookie=bob_token)
        assert status == 200, bob_body
        bob_frames = {item["frame_id"] for item in bob_body["items"]}
        assert alice_session not in bob_frames
        assert alice_body["items"]
        for item in bob_body["items"]:
            assert item["frame_id"] != alice_session
            assert "perm-alice-only" not in json.dumps(item)

        stolen_cursor = alice_body.get("next_cursor")
        if stolen_cursor is None and alice_body["items"]:
            # Force a scoped cursor even when the listing fits one page.
            stolen_cursor = attention._encode_cursor(
                int(alice_body["items"][-1]["updated_at"]),
                str(alice_body["items"][-1]["id"]),
                attention._scope_fingerprint(alice["id"]),
            )
        if stolen_cursor:
            status, rejected = node.get(
                f"/attention?cursor={stolen_cursor}", cookie=bob_token
            )
            assert status == 400
            assert rejected.get("code") == "invalid_cursor"
    finally:
        node.close()


def test_fifty_session_p95_under_200ms(client):
    frames = _seed_six(client)
    for index in range(44):
        client.session(f"idle-{index:02d}")
    assert len(frames) == 6
    for _ in range(P95_WARMUP):
        status, body = client.get("/attention")
        assert status == 200, body
        assert len(body["items"]) == 6

    samples: list[float] = []
    for _ in range(P95_RUNS):
        started = time.perf_counter()
        status, body = client.get("/attention")
        samples.append((time.perf_counter() - started) * 1000)
        assert status == 200, body
        assert len(body["items"]) == 6
    p95 = statistics.quantiles(samples, n=20)[-1]
    db_path = Path(client.cfg.db_path)
    record = {
        "p95_ms": round(p95, 3),
        "max_ms": round(max(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "runs": P95_RUNS,
        "warmup": P95_WARMUP,
        "sessions": 50,
        "db_bytes": db_path.stat().st_size if db_path.is_file() else 0,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "node": platform.node(),
    }
    print("ATTENTION_P95 " + json.dumps(record), flush=True)
    assert (
        p95 < P95_BUDGET_MS
    ), f"attention p95 {p95:.1f}ms exceeded {P95_BUDGET_MS}ms; {record}"


def test_get_does_not_spawn_retry_approve_or_harvest(client, monkeypatch):
    counts = {
        "kernel": 0,
        "runtime": 0,
        "popen": 0,
        "urlopen": 0,
        "resolve": 0,
        "prepare": 0,
        "harvest": 0,
    }

    def _count(name):
        def _inner(*_args, **_kwargs):
            counts[name] += 1
            raise AssertionError(f"attention GET must not call {name}")

        return _inner

    monkeypatch.setattr(client.runner, "_ensure_kernel", _count("kernel"))
    monkeypatch.setattr(client.runner, "_ensure_r_kernel", _count("kernel"))
    monkeypatch.setattr(client.runner, "_ensure_runtime", _count("runtime"))
    monkeypatch.setattr(client.store, "resolve_permission_request", _count("resolve"))
    monkeypatch.setattr(
        client.runner.session_domain.recovery, "prepare_action", _count("prepare")
    )

    import subprocess
    from urllib import request as urllib_request

    monkeypatch.setattr(subprocess, "Popen", _count("popen"))
    monkeypatch.setattr(urllib_request, "urlopen", _count("urlopen"))

    _seed_six(client)
    status, body = client.get("/attention")
    assert status == 200, body
    assert len(body["items"]) == 6
    assert counts == {
        "kernel": 0,
        "runtime": 0,
        "popen": 0,
        "urlopen": 0,
        "resolve": 0,
        "prepare": 0,
        "harvest": 0,
    }
    listing = compute_tasks.owner_tasks(
        client.store,
        attention.workspace_key_for(client.runner, body["items"][0]["frame_id"]),
    )
    assert listing["polled"] is False


def test_attention_modules_cannot_reach_a_provider_or_kernel():
    for module in (attention, attention_routes):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        assert not any(name.startswith("openai4s.compute.manager") for name in imported)
        assert not any(name.startswith("openai4s.kernel.manager") for name in imported)
        assert not any(name.startswith("openai4s.host_dispatch") for name in imported)
        assert "openai4s.server.gateway" not in imported
        assert not any(
            name == "subprocess" or name.startswith("subprocess.") for name in imported
        )
        assert not any(name.startswith("urllib") for name in imported)
