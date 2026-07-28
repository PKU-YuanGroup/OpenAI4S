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

import json

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import compute_tasks
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


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


def test_a_session_that_does_not_exist_is_not_a_blank_page(client):
    status, _body = client.get("/frames/f-nope/compute/tasks")
    assert status == 404
