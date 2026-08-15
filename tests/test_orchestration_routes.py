"""BatchJob end to end: routes, persistence, reconciler (plan M3a DoD).

Real HTTP against a real daemon, with the reconciler actually running — the
DoD asks for the whole chain (submit → PENDING → RUNNING → COMPLETED →
artifacts), and each of those transitions belongs to a different component.
Driving them separately would leave the seams between them untested, and the
seams are where this kind of system fails.

The cluster half runs against the fake scheduler from
`test_orchestration_slurm.py`; the local half runs real processes. Both go
through the same routes, which is the point of the backend abstraction.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from openai4s.orchestration import Phase
from openai4s.storage.workloads import ActiveAllocationExists
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _post,
    _speak,
    _TeamDaemon,
)


def _body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


@pytest.fixture(autouse=True)
def _fast_reconciler(monkeypatch):
    """The daemon's 5s cadence is right in production and wrong in a test
    that only wants to know whether the pipeline works."""
    monkeypatch.setenv("OPENAI4S_RECONCILE_INTERVAL", "0.1")


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    try:
        yield node
    finally:
        node.close()


def _submit(daemon, cookie: str, command: list[str], **extra):
    return _post(
        daemon.port,
        "/api/v1/orchestration/jobs",
        {"command": command, **extra},
        cookie=cookie,
    )


def _await_phase(daemon, job_id: str, cookie: str, *, timeout_s: float = 20.0):
    """Poll the route until the job reaches a terminal phase.

    Through the route rather than the store: the DoD's claim is that a user
    can see the job finish, and a store read would prove something weaker.
    """
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        status, raw = _get(
            daemon.port, f"/api/v1/orchestration/jobs/{job_id}", cookie=cookie
        )
        assert status == 200, raw[:300]
        last = _body(raw)
        if Phase(last["phase"]).is_terminal:
            return last
        time.sleep(0.2)
    raise AssertionError(f"job never reached terminal; last={last}")


# -- the full local lifecycle -------------------------------------------------


def test_submit_runs_and_completes_through_the_routes(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(
        daemon, cookie, [sys.executable, "-c", "print('batch output here')"]
    )
    # 202: accepted, not started — the reconciler submits on its next tick,
    # and 201 would promise a resource we have not been granted.
    assert status == 202, raw[:300]
    job = _body(raw)
    assert job["phase"] == Phase.PENDING.value
    assert job["backend"] == "local"

    final = _await_phase(daemon, job["id"], cookie)
    assert final["phase"] == Phase.COMPLETED.value
    assert final["allocations"], "the attempt should be recorded"
    assert final["allocations"][0]["epoch"] == 0

    # the logs route returns what the job actually wrote
    status, raw = _get(
        daemon.port, f"/api/v1/orchestration/jobs/{job['id']}/logs", cookie=cookie
    )
    assert status == 200
    assert "batch output here" in _body(raw)["stdout"]


def test_a_failing_job_ends_failed_not_completed(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(daemon, cookie, [sys.executable, "-c", "raise SystemExit(7)"])
    assert status == 202
    final = _await_phase(daemon, _body(raw)["id"], cookie)
    assert final["phase"] == Phase.FAILED.value


def test_cancel_reaches_terminal(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(
        daemon, cookie, [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    job_id = _body(raw)["id"]
    # let the reconciler actually start it, so this cancels something running
    time.sleep(1.0)

    status, raw = _post(
        daemon.port, f"/api/v1/orchestration/jobs/{job_id}/cancel", {}, cookie=cookie
    )
    assert status == 200, raw[:300]
    final = _await_phase(daemon, job_id, cookie)
    assert final["phase"] == Phase.CANCELLED.value


def test_cancelling_a_finished_job_is_a_conflict(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(daemon, cookie, [sys.executable, "-c", "pass"])
    job_id = _body(raw)["id"]
    _await_phase(daemon, job_id, cookie)

    status, raw = _post(
        daemon.port, f"/api/v1/orchestration/jobs/{job_id}/cancel", {}, cookie=cookie
    )
    assert status == 409
    assert _body(raw)["code"] == "already_final"


# -- isolation ----------------------------------------------------------------


def test_jobs_are_owned_and_invisible_to_others(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    status, raw = _submit(daemon, a, [sys.executable, "-c", "pass"])
    job_id = _body(raw)["id"]

    # 404 rather than 403: which jobs exist says what a colleague is working on
    for path in (
        f"/api/v1/orchestration/jobs/{job_id}",
        f"/api/v1/orchestration/jobs/{job_id}/logs",
    ):
        status, raw = _get(daemon.port, path, cookie=b)
        assert status == 404, (path, raw[:200])
    status, _ = _post(
        daemon.port, f"/api/v1/orchestration/jobs/{job_id}/cancel", {}, cookie=b
    )
    assert status == 404

    # and the listing does not mention it
    status, raw = _get(daemon.port, "/api/v1/orchestration/jobs", cookie=b)
    assert job_id not in raw.decode("utf-8", "replace")

    # the admin sees everything, and cancelling someone else's job is
    # recorded as an ADMIN cancellation rather than a user one
    r = _login(daemon, "root", "fake-pw-r")
    status, raw = _get(daemon.port, f"/api/v1/orchestration/jobs/{job_id}", cookie=r)
    assert status == 200


def test_admin_cancellation_is_attributed_to_the_admin(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    r = _login(daemon, "root", "fake-pw-r")
    status, raw = _submit(
        daemon, a, [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    job_id = _body(raw)["id"]

    status, raw = _post(
        daemon.port, f"/api/v1/orchestration/jobs/{job_id}/cancel", {}, cookie=r
    )
    assert status == 200
    assert _body(raw)["reason"] == "ADMIN_CANCELLED"
    actions = [x["action"] for x in daemon.store.team.list_audit()]
    assert "workload_cancel" in actions


def test_submission_is_audited(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    _submit(daemon, cookie, [sys.executable, "-c", "pass"])
    rows = daemon.store.team.list_audit(action="workload_submit")
    assert rows and rows[0]["actor"] == "alice"


# -- input handling -----------------------------------------------------------


def test_a_command_string_is_refused(daemon):
    """Splitting a command line is exactly where quoting bugs become
    injection, so the API refuses to do it."""
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _post(
        daemon.port,
        "/api/v1/orchestration/jobs",
        {"command": "echo hi; rm -rf /"},
        cookie=cookie,
    )
    assert status == 400
    assert _body(raw)["code"] == "invalid_command"


def test_an_empty_command_is_refused(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _post(
        daemon.port, "/api/v1/orchestration/jobs", {"command": []}, cookie=cookie
    )
    assert status == 400


def test_an_unknown_backend_is_refused_with_the_available_ones(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(
        daemon, cookie, [sys.executable, "-c", "pass"], backend="mars"
    )
    assert status == 400
    payload = _body(raw)
    assert payload["code"] == "unknown_backend"
    assert "local" in payload["available"]


def test_profiles_route_reports_no_cluster_when_unconfigured(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _get(daemon.port, "/api/v1/orchestration/profiles", cookie=cookie)
    assert status == 200
    assert _body(raw)["configured"] is False


def test_an_unknown_job_is_404(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, _ = _get(daemon.port, "/api/v1/orchestration/jobs/wl_nope", cookie=cookie)
    assert status == 404


# -- INV-2 and INV-3 at the API boundary --------------------------------------


def test_the_api_never_exposes_a_backend_job_id(daemon):
    """INV-2: allocation_id is the public identity of an attempt."""
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(daemon, cookie, [sys.executable, "-c", "pass"])
    job_id = _body(raw)["id"]
    final = _await_phase(daemon, job_id, cookie)

    text = json.dumps(final)
    assert "allocation_id" in text
    for leaked in ("external_id", "partition", "qos", "slurm", "pid"):
        assert leaked not in text.lower(), f"{leaked} reached the API"


def test_one_live_allocation_per_workload_is_enforced_by_the_database(daemon):
    """INV-3 is the partial unique index, not a Python check — the failure
    it prevents happens between a check and an insert."""
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _submit(
        daemon, cookie, [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    job_id = _body(raw)["id"]
    # Wait for the allocation to exist rather than for a fixed delay: the
    # reconciler's first tick fires before this job was submitted, so a
    # sleep shorter than its interval tests nothing.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if daemon.store.workloads.active_allocation(job_id) is not None:
            break
        time.sleep(0.2)
    assert daemon.store.workloads.active_allocation(job_id) is not None

    with pytest.raises(ActiveAllocationExists):
        daemon.store.workloads.create_allocation(job_id, 99)

    _post(daemon.port, f"/api/v1/orchestration/jobs/{job_id}/cancel", {}, cookie=cookie)


# -- the cluster backend, through the same routes -----------------------------


def test_a_cluster_job_uses_the_same_routes(tmp_path, monkeypatch):
    """The point of the abstraction: a cluster job is the same API call with
    a different backend name."""
    from tests.test_orchestration_slurm import _install_fake_scheduler

    state = tmp_path / "state"
    state.mkdir()
    (state / "queue_state").write_text("RUNNING", encoding="utf-8")
    (state / "acct_state").write_text("", encoding="utf-8")
    (state / "in_queue").write_text("1", encoding="utf-8")
    (state / "next_job_id").write_text("777", encoding="utf-8")
    s = str(state)
    bin_dir = _install_fake_scheduler(
        tmp_path,
        script={
            "sbatch": f"""
comment=""
for arg in "$@"; do
  case "$arg" in --comment=*) comment="${{arg#--comment=}}" ;; esac
done
cat > /dev/null
printf '%s' "$comment" > "{s}/job_comment"
printf '%s' "$(cat "{s}/next_job_id")" > "{s}/job_id"
cat "{s}/next_job_id"
""",
            "squeue": f"""
if [ ! -f "{s}/job_id" ] || [ "$(cat "{s}/in_queue")" != "1" ]; then exit 0; fi
jid=$(cat "{s}/job_id"); cm=$(cat "{s}/job_comment"); st=$(cat "{s}/queue_state")
fmt=""
for arg in "$@"; do case "$arg" in --format=*) fmt="${{arg#--format=}}" ;; esac; done
case "$fmt" in *%r*) echo "$jid|$st||$cm" ;; *) echo "$jid|$cm" ;; esac
""",
            "sacct": f"""
st=$(cat "{s}/acct_state"); [ -n "$st" ] || exit 0
jid=$(cat "{s}/job_id"); cm=$(cat "{s}/job_comment")
fmt=""
for arg in "$@"; do case "$arg" in --format=*) fmt="${{arg#--format=}}" ;; esac; done
case "$fmt" in *ExitCode*) echo "$jid|$st|0:0|$cm" ;; *) echo "$jid|$cm" ;; esac
""",
            "scancel": f"""
printf '0' > "{s}/in_queue"; printf 'CANCELLED' > "{s}/acct_state"
""",
        },
    )
    import os

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    home = tmp_path / "home"
    home.mkdir()
    (home / "cluster.toml").write_text(
        '[cluster]\nname = "test-cluster"\n\n'
        '[profiles.gpu-batch]\npartition = "gpu"\ngpus = 1\n',
        encoding="utf-8",
    )
    node = _TeamDaemon(home)
    node.seed_user("alice", "fake-pw-a")
    try:
        cookie = _login(node, "alice", "fake-pw-a")

        status, raw = _get(node.port, "/api/v1/orchestration/profiles", cookie=cookie)
        payload = _body(raw)
        assert payload["configured"] is True
        assert payload["profiles"][0]["name"] == "gpu-batch"
        # D5: the queue name is configured, and never leaves the daemon
        assert "gpu" not in json.dumps(payload).replace("gpu-batch", "").replace(
            '"gpus"', ""
        )

        status, raw = _post(
            node.port,
            "/api/v1/orchestration/jobs",
            {
                "command": ["echo", "hello"],
                "profile": "gpu-batch",
                "backend": "cluster",
            },
            cookie=cookie,
        )
        assert status == 202, raw[:300]
        job_id = _body(raw)["id"]

        # the reconciler submits it to the fake scheduler and sees RUNNING
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, raw = _get(
                node.port, f"/api/v1/orchestration/jobs/{job_id}", cookie=cookie
            )
            if _body(raw)["phase"] == Phase.ACTIVE.value:
                break
            time.sleep(0.2)
        assert _body(raw)["phase"] == Phase.ACTIVE.value, _body(raw)

        # the token really did reach the scheduler (INV-8's precondition)
        assert (state / "job_comment").read_text().startswith("tok_")

        # and the job finishes
        (state / "in_queue").write_text("0", encoding="utf-8")
        (state / "acct_state").write_text("COMPLETED", encoding="utf-8")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, raw = _get(
                node.port, f"/api/v1/orchestration/jobs/{job_id}", cookie=cookie
            )
            if Phase(_body(raw)["phase"]).is_terminal:
                break
            time.sleep(0.2)
        assert _body(raw)["phase"] == Phase.COMPLETED.value
    finally:
        node.close()
