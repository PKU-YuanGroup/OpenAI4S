"""Looking at remote work without paying for it.

A remote job outlives the turn that launched it, the kernel, and the daemon.
The durable record existed; the only way to see it was to be the agent, inside
a cell, calling `host.compute`. A person whose GPU job had been running for two
hours had nowhere to look.

The reason this is not simply "add a page that polls" is specific to this
system: **the probe is the harvest**. `ComputeManager.result()` is what
contacts the remote, and contacting the remote is what pulls files back into
the workspace, registers artifacts, and closes the job. There is no read-only
probe. A page that refreshed itself would harvest into a session nobody was
watching and bill a provider on a schedule the user never chose.

So the listing reads the durable record and cannot do anything else — not by
convention but because `compute_tasks` is handed a Store and has no path to a
manager at all. These tests assert that gap stays closed, that one session
cannot enumerate another's remote work, and that `unknown` is never rendered
as failure: it means the remote could not be reached, and the job may well be
running and billing.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.compute import registry
from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.compute.states import CANCELLED, RUNNING, SUCCEEDED
from openai4s.config import Config, LLMConfig
from openai4s.server import compute_tasks
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, team_policy


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    def __init__(self, tmp_path):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.runner = gateway_mod.SessionRunner(self.cfg, _Hub())
        self.store = self.runner.store
        self.store.create_project(name="p", description="", context="")
        self.project_id = [p["project_id"] for p in self.store.list_projects()][0]
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""

    def session(self):
        return self.runner.create_session(self.project_id)

    def owner_key(self, frame_id):
        return str(self.runner.active_workspace_for(frame_id))

    def seed(self, frame_id, *, job_id, status, **fields):
        """Create through the repository, then apply terminal fields via
        `update`, which is the only writer that accepts them — `create`
        deliberately records a job *before* submission and knows nothing about
        how it ended."""
        self.store.create_compute_job(
            job_id=job_id,
            provider="byoc-test",
            status="queued",
            owner_key=self.owner_key(frame_id),
        )
        # Walk the state machine rather than jumping. `check_transition` is a
        # real invariant — a row cannot go straight from `queued` to `running`
        # — and a test that bypassed it would seed states the system never
        # produces.
        for step in ("staging", status):
            if step != "queued":
                self.store.update_compute_job(job_id, status=step)
        return self.store.update_compute_job(job_id, **fields) if fields else None

    def get(self, path):
        return self._call("GET", path, None)

    def post(self, path, body=None):
        return self._call("POST", path, body or {})

    def _call(self, method, path, body):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        if body is not None:
            handler._body = lambda: body
        handler._route(method)
        return sent["code"], sent["body"]


@pytest.fixture
def client(tmp_path):
    return _Client(tmp_path)


# --------------------------------------------------------------------------
# reading costs nothing
# --------------------------------------------------------------------------


def test_the_listing_has_no_way_to_reach_a_provider():
    """The guarantee is structural, not a promise about call order.

    A test that stubbed a manager and asserted it went uncalled would prove
    only that *this* call path is clean today. What makes the page safe is that
    the module cannot construct a manager at all, so no future edit inside it
    can start polling by accident.
    """
    import ast
    import inspect

    # Read the imports, not the prose. The first version of this grepped the
    # source for "ComputeManager" and failed on the module docstring, which
    # names it precisely to explain why it is not used — a text search cannot
    # tell an explanation from a dependency.
    tree = ast.parse(inspect.getsource(compute_tasks))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    assert not any(name.startswith("openai4s.compute.manager") for name in imported)
    assert not any("gateway" in name or "host_dispatch" in name for name in imported)
    # The one compute import is the state vocabulary, which is data.
    assert {n for n in imported if n.startswith("openai4s.compute")} <= {
        "openai4s.compute.states",
        "openai4s.compute.states.LIVE_STATES",
    }
    # ...and its only collaborator is a store.
    assert "store" in inspect.signature(compute_tasks.owner_tasks).parameters


def test_opening_the_page_returns_the_record_and_says_it_did_not_poll(client):
    frame = client.session()
    client.seed(frame, job_id="job-a", status="running")
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert body["polled"] is False, "a read must not claim to be a fresh check"
    assert [task["job_id"] for task in body["tasks"]] == ["job-a"]
    assert body["live_count"] == 1


def test_a_finished_job_is_still_listed(client):
    """`live()` answers "what might still cost money", which is what
    rehydration needs. A person came to look at the job that failed an hour
    ago."""
    frame = client.session()
    client.seed(frame, job_id="job-done", status="succeeded", terminal_at=1)
    client.seed(frame, job_id="job-live", status="running")
    _status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert {task["job_id"] for task in body["tasks"]} == {"job-done", "job-live"}
    assert body["live_count"] == 1


# --------------------------------------------------------------------------
# owner scoping
# --------------------------------------------------------------------------


def test_one_session_cannot_enumerate_anothers_remote_work(client):
    """Not listed, not counted, and not reported as hidden — a count would
    itself tell one session how much remote work another is doing."""
    mine, theirs = client.session(), client.session()
    client.seed(theirs, job_id="job-theirs", status="running")
    _status, body = client.get(f"/frames/{mine}/compute/tasks")
    assert body["tasks"] == []
    assert body["live_count"] == 0
    assert "job-theirs" not in json.dumps(body)
    assert "hidden" not in json.dumps(body)


def test_the_record_survives_a_restart(client, tmp_path):
    """The whole point of a durable record. A job submitted before a daemon
    restart is exactly the one whose fate the user cannot otherwise learn."""
    frame = client.session()
    client.seed(frame, job_id="job-old", status="running")
    client.store.close()

    revived = _Client.__new__(_Client)
    revived.__init__(tmp_path)
    _status, body = revived.get(f"/frames/{frame}/compute/tasks")
    assert [task["job_id"] for task in body["tasks"]] == ["job-old"]


# --------------------------------------------------------------------------
# what a task record says
# --------------------------------------------------------------------------


def test_unknown_is_not_rendered_as_a_failure():
    """`unknown` means the remote could not be reached. The job may well be
    running and billing, so calling it failed is the opposite of the truth in
    exactly the case that costs money."""
    task = compute_tasks.public_task({"job_id": "j", "status": "unknown"})
    assert task["status"] == "unknown"
    assert task["live"] is True
    assert task["terminal"] is False


def test_process_handles_and_cluster_paths_are_not_published():
    """The record holds what the manager needs to cancel and harvest. A panel
    cannot act on a pid or a sandbox handle, and rendering them publishes the
    shape of someone's cluster to anything that reads the page."""
    task = compute_tasks.public_task(
        {
            "job_id": "j",
            "status": "running",
            "pid": 4242,
            "pgid": 4242,
            "sandbox_id": "sbx-secret",
            "alias": "gpu-node-7.internal",
            "workdir": "/scratch/lab/private",
            "receipt": {"token": "shh"},
        }
    )
    rendered = json.dumps(task)
    for leak in ("4242", "sbx-secret", "gpu-node-7", "/scratch/lab/private", "shh"):
        assert leak not in rendered


def test_a_harvest_is_summarised_rather_than_listed():
    """File count and total size answer "did my outputs come back". The paths
    are the part that names directories on someone's cluster."""
    task = compute_tasks.public_task(
        {
            "job_id": "j",
            "status": "succeeded",
            "artifact_manifest": [
                {"path": "/scratch/run/out.npz", "size": 100},
                {"path": "/scratch/run/log.txt", "size": 23},
            ],
        }
    )
    assert task["outputs"] == {"file_count": 2, "total_bytes": 123}
    assert "/scratch" not in json.dumps(task)


def test_a_long_provider_message_is_clipped():
    task = compute_tasks.public_task(
        {"job_id": "j", "status": "failed", "reason": "E" * 5000}
    )
    assert len(task["reason"]) < 600


# --------------------------------------------------------------------------
# refreshing is the explicit action
# --------------------------------------------------------------------------


def test_refresh_is_a_post_because_it_harvests(client):
    """Not a GET. It contacts the remote, pulls files back, and can close the
    job — a side effect a browser must never perform on navigation or prefetch.
    """
    frame = client.session()
    from openai4s.server import contract

    routes = contract.http_routes()
    assert any("compute/tasks" in route and "refresh" in route for route in routes)
    status, _body = client.get(f"/frames/{frame}/compute/tasks/job-x/refresh")
    assert status in (404, 405), "the harvesting action answered a GET"


def test_refresh_of_an_owned_terminal_job_returns_the_record_without_a_probe(
    client, monkeypatch
):
    """The listing shows finished jobs. The manager map does not: only live
    work is rehydrated, and ``ComputeError`` stores the miss on ``error_kind``.

    Refresh used to treat that miss as a remote failure (502), or, when the
    code was read off the wrong attribute, as "no such job". The recorded row
    is the answer, and it does not contact a provider. Another session's id
    stays indistinguishable from one that does not exist.
    """
    mine, theirs = client.session(), client.session()
    client.seed(mine, job_id="job-done", status=SUCCEEDED, reason="finished cleanly")
    client.seed(theirs, job_id="job-theirs", status=SUCCEEDED)

    def boom(*_a, **_k):
        raise AssertionError("a recorded terminal job must not contact a provider")

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", boom)
    captures: list[object] = []
    monkeypatch.setattr(
        "openai4s.server.artifacts.ArtifactManager.capture",
        lambda *a, **k: captures.append(a),
    )

    status, body = client.post(f"/frames/{mine}/compute/tasks/job-done/refresh")
    assert status == 200, body
    assert body["polled"] is False
    # Nothing was harvested, so nothing may be captured and attributed to it.
    assert captures == []
    assert body["job_id"] == "job-done"
    assert body["status"] == SUCCEEDED
    assert body.get("reason") == "finished cleanly"

    foreign_status, foreign = client.post(
        f"/frames/{mine}/compute/tasks/job-theirs/refresh"
    )
    unknown_status, unknown = client.post(
        f"/frames/{mine}/compute/tasks/job-missing/refresh"
    )
    assert foreign_status == unknown_status == 404
    assert foreign.get("code") == unknown.get("code") == "not_found"
    assert foreign["error"].replace("job-theirs", "<job>") == unknown["error"].replace(
        "job-missing", "<job>"
    )


def test_a_session_that_does_not_exist_is_not_a_blank_page(client):
    status, _body = client.get("/frames/f-nope/compute/tasks")
    assert status == 404


# --------------------------------------------------------------------------
# explicit cancel: only a confirmed POST reaches a provider
# --------------------------------------------------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = io.BytesIO()

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _submit_ssh_job(client, frame, monkeypatch):
    """A live ssh job owned by this session, so the Web cancel path can find it."""
    registry.add_host("lab", data_dir=client.cfg.data_dir)
    workspace = client.runner.active_workspace_for(frame)
    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen",
        lambda *a, **k: _Proc(0, b"OPENAI4S_JOB 31337 31337\n"),
        raising=True,
    )
    manager = ComputeManager(client.cfg, store=client.store, workspace=workspace)
    return manager.submit({"provider": "ssh:lab", "command": "sleep 600"})["job_id"]


def test_cancel_is_a_post_because_it_signals_the_remote(client):
    frame = client.session()
    from openai4s.server import contract

    routes = contract.http_routes()
    assert any("compute/tasks" in route and "cancel" in route for route in routes)
    status, _body = client.get(f"/frames/{frame}/compute/tasks/job-x/cancel")
    assert status in (404, 405), "the cancelling action answered a GET"


@pytest.mark.stubbed_backend
def test_cancel_without_confirm_does_not_contact_the_remote(client, monkeypatch):
    frame = client.session()
    job_id = _submit_ssh_job(client, frame, monkeypatch)
    remote = []
    built = []
    cancels = []

    def boom(*a, **k):
        remote.append(a)
        raise AssertionError("remote must not be contacted without confirm")

    class _Manager:
        def cancel(self, spec):
            cancels.append(spec)
            raise AssertionError("manager.cancel must not run without confirm")

    def fake_build(*a, **k):
        built.append(1)
        return SimpleNamespace(compute=_Manager())

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", boom)
    monkeypatch.setattr(gateway_mod, "build_dispatcher", fake_build)
    status, body = client.post(f"/frames/{frame}/compute/tasks/{job_id}/cancel", {})
    assert status == 400
    assert body.get("code") == "confirmation_required"
    assert remote == []
    assert built == []
    assert cancels == []
    assert client.store.get_compute_job(job_id)["status"] == RUNNING

    status, body = client.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": False}
    )
    assert status == 400
    assert remote == []
    assert built == []
    assert cancels == []


def test_cancel_of_another_sessions_job_is_not_found_and_does_not_contact_the_remote(
    client, monkeypatch
):
    mine, theirs = client.session(), client.session()
    job_id = _submit_ssh_job(client, theirs, monkeypatch)
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("foreign cancel must not reach a provider")

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", boom)
    status, body = client.post(
        f"/frames/{mine}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 404
    assert body.get("code") == "not_found"
    assert calls == []
    assert client.store.get_compute_job(job_id)["status"] == RUNNING


def test_team_members_cannot_cancel_someone_elses_session():
    """Same owner-only gate as refresh: every frame POST except revert preview."""
    assert team_policy.is_session_control_mutation(
        "POST", "/frames/abc/compute/tasks/job-1/cancel"
    )


def test_confirmed_cancel_writes_cancelled_and_contacts_the_remote(client, monkeypatch):
    frame = client.session()
    job_id = _submit_ssh_job(client, frame, monkeypatch)
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return _Proc(0)

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", fake_run, raising=True
    )
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 200
    assert body["outcome"] == "cancel_confirmed"
    assert body["task"]["status"] == "cancelled"
    assert body["task"]["live"] is False
    assert client.store.get_compute_job(job_id)["status"] == CANCELLED
    assert seen, "confirmed cancel must actually signal the remote"
    assert any("31337" in str(argv) for argv in seen)


def test_unreachable_cancel_is_indeterminate_and_does_not_write_cancelled(
    client, monkeypatch
):
    frame = client.session()
    job_id = _submit_ssh_job(client, frame, monkeypatch)
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=45)

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", boom)
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 202
    assert body["outcome"] == "cancel_indeterminate"
    assert body["reason"] == "remote_unreachable"
    assert "billing" in body["hint"]
    assert body["task"]["status"] != "cancelled"
    assert body["task"]["live"] is True
    assert client.store.get_compute_job(job_id)["status"] != CANCELLED
    assert calls, "unreachable is a remote attempt, not a skipped one"


@pytest.mark.stubbed_backend
def test_unsupported_cancel_is_indeterminate(client, monkeypatch):
    frame = client.session()
    client.seed(frame, job_id="job-open", status="running")
    calls = []

    class _Manager:
        def cancel(self, spec):
            calls.append(spec)
            raise ComputeError(
                "this provider cannot confirm a cancel",
                "provider_cancel_unsupported",
                indeterminate=True,
            )

    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *a, **k: SimpleNamespace(compute=_Manager()),
    )
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/job-open/cancel", {"confirm": True}
    )
    assert status == 202
    assert body["outcome"] == "cancel_indeterminate"
    assert body["reason"] == "provider_cancel_unsupported"
    assert "billing" in body["hint"]
    assert client.store.get_compute_job("job-open")["status"] != CANCELLED
    assert calls == [{"job_id": "job-open"}]


@pytest.mark.stubbed_backend
def test_a_natural_completion_race_returns_the_real_terminal_state(client, monkeypatch):
    frame = client.session()
    client.seed(frame, job_id="job-done", status="succeeded", terminal_at=1)
    calls = []

    class _Manager:
        def cancel(self, spec):
            calls.append(spec)
            return {
                "status": "succeeded",
                "conflict": {"requested": "cancelled", "actual": "succeeded"},
            }

    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *a, **k: SimpleNamespace(compute=_Manager()),
    )
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/job-done/cancel", {"confirm": True}
    )
    assert status == 409
    assert body["outcome"] == "already_terminal"
    assert body["code"] == "already_terminal"
    # The envelope's `status` is the integer HTTP status, as it is on every
    # error shape but one pinned legacy route. Returning the terminal state
    # under that key clobbered the integer `public_failure` adds -- it defers
    # to a value the route set -- and made this the second route declaring
    # both types. The terminal state rides on `task`, where the UI reads it.
    assert body["status"] == 409
    assert body["task"]["status"] == "succeeded"
    assert client.store.get_compute_job("job-done")["status"] == SUCCEEDED


def test_cancel_after_restart_still_requires_an_explicit_confirm(
    client, tmp_path, monkeypatch
):
    frame = client.session()
    job_id = _submit_ssh_job(client, frame, monkeypatch)
    client.store.close()

    revived = _Client.__new__(_Client)
    revived.__init__(tmp_path)
    row = revived.store.get_compute_job(job_id)
    assert row["status"] == RUNNING

    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("restart must not cancel")

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", boom)
    # Opening the listing after a restart is a read. It must not signal.
    status, body = revived.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert body["tasks"][0]["job_id"] == job_id
    assert body["tasks"][0]["live"] is True
    assert calls == []

    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return _Proc(0)

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen", fake_run, raising=True
    )
    status, body = revived.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 200
    assert body["outcome"] == "cancel_confirmed"
    assert revived.store.get_compute_job(job_id)["status"] == CANCELLED
    assert seen


def test_page_load_and_ordinary_stop_never_post_cancel():
    """The five non-goals: close page, Stop turn, delete session, timeout UI,
    daemon restart. None of them is allowed to grow a compute cancel call.
    """
    island = Path("frontend/src/features/timeline/island.ts").read_text(
        encoding="utf-8"
    )
    actions = Path("frontend/src/features/sessions/actions.ts").read_text(
        encoding="utf-8"
    )
    deletion = Path("openai4s/server/session_deletion.py").read_text(encoding="utf-8")
    # Page load fetches the listing. C2 keeps that read in `island.ts` and
    # routes it through the dedicated `loadComputeTasks`; the endpoint string
    # lives only in the `computeTasksPath` builder beside it. The cancel POST
    # lives only in the confirm-gated helper, which neither the load path nor
    # the listing read calls.
    load_fn = island.split("export async function loadWorkbenchState", 1)[1].split(
        "export function scheduleWorkbenchRefresh", 1
    )[0]
    assert "loadComputeTasks" in load_fn
    # One builder names the endpoint, and it is the one the load reaches.
    assert "function computeTasksPath" in island
    read_fn = island.split("function computeTasksPath", 1)[1].split("\n}\n", 1)[0]
    assert "/compute/tasks" in read_fn
    # The load path itself does not build the URL, and the template that names
    # the listing endpoint exists exactly once — inside that read builder. The
    # cancel/refresh helpers build their own per-job sub-paths.
    assert "/compute/tasks" not in load_fn
    assert island.count("/compute/tasks${") == 1
    assert read_fn.count("/compute/tasks${") == 1
    assert "/cancel" not in load_fn
    assert "/cancel" not in read_fn
    assert "confirm: true" not in load_fn
    # The cancel helper itself is the only POST, and it is behind confirm().
    assert "function cancelComputeTask" in island
    assert "globalThis.confirm" in island
    assert "computeCancelInFlight" in island
    assert "JSON.stringify({ confirm: true })" in island
    assert "may still be running and billing" in island
    # Stop the turn / delete the session: different verbs, different resources.
    assert "/compute/tasks/" not in actions
    assert "compute/tasks" not in deletion
    assert "ComputeManager" not in deletion
    # Closing the page and a timeout UI have no compute-cancel listener.
    frontend_root = Path("frontend/src")
    hits = []
    for path in list(frontend_root.rglob("*.ts")) + list(frontend_root.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        if "compute/tasks/" in text and "/cancel" in text:
            hits.append(str(path))
    assert hits == ["frontend/src/features/timeline/island.ts"]
    assert "beforeunload" not in island
    assert "visibilitychange" not in island


def test_double_click_is_one_in_flight_request():
    """The button disables and a Set drops a second click before fetch."""
    island = Path("frontend/src/features/timeline/island.ts").read_text(
        encoding="utf-8"
    )
    helper = island.split("async function cancelComputeTask", 1)[1].split(
        "export function renderComputeTasksPanel", 1
    )[0]
    assert "computeCancelInFlight.has(jobId)" in helper
    assert "button.disabled = true" in helper
    assert helper.index("computeCancelInFlight.add(jobId)") < helper.index("await api(")
    assert helper.index("button.disabled = true") < helper.index("await api(")


def test_a_confirmed_cancel_clears_the_earlier_unconfirmed_note(client, monkeypatch):
    """An unconfirmed attempt writes "may still be running and billing" onto
    the row; the confirmed stop that follows must not leave it there, or the
    Task Centre keeps warning about a job that is over."""
    frame = client.session()
    job_id = _submit_ssh_job(client, frame, monkeypatch)

    def unreachable(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=45)

    monkeypatch.setattr("openai4s.compute.manager.subprocess.Popen", unreachable)
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 202
    assert "billing" in (client.store.get_compute_job(job_id).get("reason") or "")

    monkeypatch.setattr(
        "openai4s.compute.manager.subprocess.Popen",
        lambda *a, **k: _Proc(0),
        raising=True,
    )
    status, body = client.post(
        f"/frames/{frame}/compute/tasks/{job_id}/cancel", {"confirm": True}
    )
    assert status == 200
    assert body["outcome"] == "cancel_confirmed"
    row = client.store.get_compute_job(job_id)
    assert row["status"] == CANCELLED
    assert not row.get("reason"), row.get("reason")
    assert "billing" not in str(body["task"].get("reason") or "")


# --------------------------------------------------------------------------
# paging: the live job the newest-200 view could not reach
# --------------------------------------------------------------------------


def _seed_many(client, frame, count, *, live_ids=frozenset(), prefix="job"):
    """Create ``count`` jobs owned by ``frame``, optionally pinning the order.

    Seeded through the repository's state machine (``create`` then ``update``),
    because a row that jumped straight to a terminal state is one the system
    never produces and a test that used one would prove nothing about the real
    listing.
    """
    owner = client.owner_key(frame)
    for index in range(count):
        job_id = f"{prefix}-{index:03d}"
        client.store.create_compute_job(
            job_id=job_id, provider="byoc-test", status="queued", owner_key=owner
        )
        client.store.update_compute_job(job_id, status="staging")
        if job_id in live_ids:
            client.store.update_compute_job(job_id, status="running")
        else:
            client.store.update_compute_job(job_id, status="succeeded", terminal_at=1)
        # Pin the ordering key so the tie-break is exercised deterministically:
        # all 201 rows land in the same millisecond, which is exactly the case
        # the old `ORDER BY created_at DESC` left undefined.
        client.store.update_compute_job(job_id, created_at=5000)


def _tasks_path(frame, **query):
    path = f"/frames/{frame}/compute/tasks"
    if query:
        from urllib.parse import urlencode

        path += "?" + urlencode(query)
    return path


def _page_ids(body):
    return [task["job_id"] for task in body["tasks"]]


def test_the_old_response_keys_keep_their_meaning_and_the_new_ones_are_added(client):
    """`live_count` still counts live jobs on *this page*, `polled` is still
    false, and the three new fields are always present — a client that only
    knows the old shape reads what it always did."""
    frame = client.session()
    client.seed(frame, job_id="job-a", status="running")
    client.seed(frame, job_id="job-b", status="succeeded", terminal_at=1)
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert body["polled"] is False
    assert body["live_count"] == 1
    assert body["total_live_count"] == 1
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert set(_page_ids(body)) == {"job-a", "job-b"}


@pytest.mark.stubbed_backend
def test_an_empty_page_is_a_success_and_a_failure_is_not_an_empty_page(client):
    """A session with no remote work and an unreadable record must not look the
    same. The first is "nothing is running"; the second cannot say that."""
    frame = client.session()
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert body["tasks"] == []
    assert body["live_count"] == 0
    assert body["total_live_count"] == 0
    assert body["has_more"] is False

    def unreadable(*_args, **_kwargs):
        raise sqlite3.OperationalError(
            "no such table: compute_jobs at /private/lab/secret.db"
        )

    client.store.compute_jobs_page_for_owner = unreadable
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 503
    assert body["code"] == "compute_record_unavailable"
    assert body["status"] == 503
    assert body["error"] == "compute task records are temporarily unavailable"
    assert "tasks" not in body and "live_count" not in body
    rendered = json.dumps(body)
    for leak in (
        "no such table",
        "/private/lab",
        "secret.db",
        "sqlite",
        "Traceback",
        str(client.cfg.data_dir),
    ):
        assert leak not in rendered


def test_an_unreadable_session_lookup_is_also_503_rather_than_404(client):
    """The frame read comes from the same database. It cannot answer "unknown
    session" when it cannot read at all — and a 404 would tell the client the
    session is gone."""
    frame = client.session()

    def unreadable(_frame_id):
        raise sqlite3.OperationalError("database is locked: /private/lab/secret.db")

    client.store.get_frame = unreadable
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 503
    assert body["code"] == "compute_record_unavailable"
    assert body["error"] == "compute task records are temporarily unavailable"
    assert "/private/lab" not in json.dumps(body)
    assert str(client.cfg.data_dir) not in json.dumps(body)


def test_the_total_reaches_a_live_job_pushed_out_of_the_newest_page(client):
    """The failure the paging exists for: 201 jobs, only the oldest still
    running. The newest-200 view cannot contain it, and `live_count` over that
    page is 0. `total_live_count` is 1, and the "live" filter locates it."""
    frame = client.session()
    _seed_many(client, frame, 201, live_ids={"job-000"})

    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert len(body["tasks"]) == 200
    assert body["total_live_count"] == 1
    assert body["live_count"] == 0, "the legacy field counts this page only"
    assert body["has_more"] is True
    assert body["next_cursor"]

    status, body = client.get(f"/frames/{frame}/compute/tasks?state=live")
    assert status == 200
    assert _page_ids(body) == ["job-000"]
    assert body["live_count"] == 1
    assert body["total_live_count"] == 1
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_the_limit_is_defaulted_capped_and_validated(client):
    frame = client.session()
    _seed_many(client, frame, 201, live_ids={"job-000"})

    status, body = client.get(f"/frames/{frame}/compute/tasks?limit=500")
    assert status == 200
    assert (
        len(body["tasks"]) == 200
    ), "a limit above the ceiling is clamped, not honoured"
    assert body["has_more"] is True

    for bad in ("0", "-1", "many"):
        status, body = client.get(f"/frames/{frame}/compute/tasks?limit={bad}")
        assert status == 400
        assert body["code"] == "invalid_limit"

    status, body = client.get(f"/frames/{frame}/compute/tasks?state=done")
    assert status == 400
    assert body["code"] == "invalid_state"


def test_paging_is_stable_and_never_repeats_or_skips_a_job(client):
    """All 201 rows share a `created_at`. A keyset ordering without a tiebreak
    had no defined position between them, so a page boundary could repeat one
    and skip another while both pages looked complete."""
    frame = client.session()
    _seed_many(client, frame, 201, live_ids={"job-000"})
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        query = {"limit": 7}
        if cursor:
            query["cursor"] = cursor
        status, body = client.get(_tasks_path(frame, **query))
        assert status == 200
        ids = _page_ids(body)
        assert ids == sorted(ids, reverse=True), "a page is not a stable slice"
        assert not (set(ids) & set(seen)), f"repeated across pages: {ids}"
        seen.extend(ids)
        assert body["total_live_count"] == 1
        pages += 1
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
        assert cursor
    assert pages == 29, (pages, len(seen))
    assert len(seen) == 201
    assert len(set(seen)) == 201
    assert seen == sorted(seen, reverse=True)


def test_a_cursor_is_opaque_and_carries_no_owner_path(client):
    """The cursor carries the sort key and the filter. It must not carry
    `owner_key`: that is the session's workspace path, which is a leak and,
    worse, would tempt a server to trust it as authorization."""
    frame = client.session()
    _seed_many(client, frame, 3)
    _status, body = client.get(f"/frames/{frame}/compute/tasks?limit=1")
    cursor = body["next_cursor"]
    assert cursor
    padded = cursor + "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(padded).decode("utf-8")
    assert client.owner_key(frame) not in raw
    assert "owner_key" not in raw
    assert "/" not in raw
    payload = json.loads(raw)
    assert set(payload) == {"fp", "id", "t"}
    assert len(payload["fp"]) == 64
    assert all(c in "0123456789abcdef" for c in payload["fp"])


def test_a_malformed_or_cross_filter_cursor_is_refused(client):
    frame = client.session()
    _seed_many(client, frame, 3)

    for bad in ("not-a-cursor", "e30", base64.urlsafe_b64encode(b"[1,2]").decode()):
        status, body = client.get(f"/frames/{frame}/compute/tasks?cursor={bad}")
        assert status == 400
        assert body["code"] == "invalid_cursor"

    _status, body = client.get(f"/frames/{frame}/compute/tasks?limit=1")
    cursor = body["next_cursor"]
    status, body = client.get(
        f"/frames/{frame}/compute/tasks?limit=1&state=live&cursor={cursor}"
    )
    assert status == 400
    assert body["code"] == "invalid_cursor", "a page cursor is bound to its filter"


def test_another_owners_cursor_is_refused_and_their_jobs_are_never_counted(client):
    mine, theirs = client.session(), client.session()
    _seed_many(client, mine, 3, prefix="mine")
    _seed_many(client, theirs, 2, live_ids={"theirs-000"}, prefix="theirs")
    _status, body = client.get(f"/frames/{mine}/compute/tasks?limit=1")
    foreign_cursor = body["next_cursor"]

    status, body = client.get(
        f"/frames/{theirs}/compute/tasks?limit=1&cursor={foreign_cursor}"
    )
    assert status == 400
    assert body["code"] == "invalid_cursor"

    status, body = client.get(f"/frames/{mine}/compute/tasks")
    assert status == 200
    assert body["total_live_count"] == 0, "their live job is not my total"
    assert all(job_id.startswith("mine-") for job_id in _page_ids(body))
    assert "hidden" not in json.dumps(body)


def test_unknown_is_counted_as_live_in_the_same_owner_total(client):
    """`unknown` means the remote could not be reached, and the job may still
    be billing. It is live for the total exactly as it is for the row."""
    frame = client.session()
    client.seed(frame, job_id="job-maybe", status="unknown")
    status, body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200
    assert body["tasks"][0]["status"] == "unknown"
    assert body["tasks"][0]["live"] is True
    assert body["live_count"] == 1
    assert body["total_live_count"] == 1


def test_reading_a_page_does_not_advance_or_rewrite_a_job(client):
    """The page reads the durable record. It does not reconcile it, stamp it,
    or append an event — that is the explicit refresh/cancel POST."""
    frame = client.session()
    client.seed(frame, job_id="job-live", status="running")
    before = client.store.get_compute_job("job-live")
    events_before = client.store.compute_job_events("job-live")

    status, _body = client.get(f"/frames/{frame}/compute/tasks?limit=1&state=live")
    assert status == 200
    status, _body = client.get(f"/frames/{frame}/compute/tasks")
    assert status == 200

    assert client.store.get_compute_job("job-live") == before
    assert client.store.compute_job_events("job-live") == events_before


def test_the_new_fields_survive_a_restart(client, tmp_path):
    frame = client.session()
    _seed_many(client, frame, 3, live_ids={"job-000"})
    client.store.close()

    revived = _Client.__new__(_Client)
    revived.__init__(tmp_path)
    status, body = revived.get(f"/frames/{frame}/compute/tasks?limit=1")
    assert status == 200
    assert body["total_live_count"] == 1
    assert body["has_more"] is True
    assert body["next_cursor"]


def test_a_cursor_anchored_to_another_owners_job_is_refused(client):
    """The frame/state fingerprint is computable by a client; owner binding is
    the server-side anchor check, not secrecy. A cursor naming somebody else's
    job is refused even with a correct-looking fingerprint."""
    mine, theirs = client.session(), client.session()
    _seed_many(client, mine, 2, prefix="mine")
    _seed_many(client, theirs, 2, prefix="theirs")
    fingerprint = hashlib.sha256(
        json.dumps(
            {"frame_id": theirs, "state": "all"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    forged = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"fp": fingerprint, "id": "mine-000", "t": 5000},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )

    status, body = client.get(f"/frames/{theirs}/compute/tasks?cursor={forged}")
    assert status == 400
    assert body["code"] == "invalid_cursor"


def test_a_cursor_whose_created_at_no_longer_matches_is_refused(client):
    """The anchor has to carry the recorded creation time the cursor claims; a
    tampered one would resume the ordering at a position the row does not
    occupy."""
    frame = client.session()
    _seed_many(client, frame, 3)
    _status, body = client.get(f"/frames/{frame}/compute/tasks?limit=1")
    cursor = body["next_cursor"]
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    assert payload["t"] == 5000
    payload["t"] = 4000
    tampered = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    status, body = client.get(f"/frames/{frame}/compute/tasks?cursor={tampered}")
    assert status == 400
    assert body["code"] == "invalid_cursor"


def test_reading_the_listing_cannot_reach_the_manager(client, monkeypatch):
    """The read path holds a Store, never a manager. Making the manager's remote
    calls raise proves that nothing on this path reaches them: a GET still
    answers with the durable record."""
    frame = client.session()
    client.seed(frame, job_id="job-live", status="running")

    def boom(*_args, **_kwargs):
        raise AssertionError("the listing contacted a remote compute manager")

    monkeypatch.setattr(ComputeManager, "result", boom, raising=True)
    monkeypatch.setattr(ComputeManager, "cancel", boom, raising=True)

    status, body = client.get(f"/frames/{frame}/compute/tasks?state=live")
    assert status == 200
    assert body["live_count"] == 1
    assert body["total_live_count"] == 1
