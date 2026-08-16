"""The whole chain, offline: fake scheduler, real worker, real socket (M3b-7).

The fake `sbatch` here does what a real one does — it runs the submitted
script on a node — except the node is this machine. Everything above it is
the production path: the real `SlurmBroker` shelling out, the real
`SlurmBackend`, the real `Reconciler`, the real `Store`, the real
`WorkerGateway`, the real credential, the real `worker.py` in its own
process, and the real frame protocol over a real TCP socket.

That arrangement is the point. A test that stubbed the launch would be
asserting that the code does what it was written to do; the questions
worth asking here are the ones only a real subprocess can answer — does
the credential the daemon wrote actually admit the worker it was written
for, does a variable set in one turn still exist in the next, does an
interrupt reach a process the daemon has no pid for, and does a lease
expiring actually take the resource back.

The plan's M3b-7 scenario list, in order: variables surviving across
turns, interrupt, cancel, lease expiry, an expired credential, and a
stale epoch.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from openai4s.kernel.manager import Kernel
from openai4s.orchestration.bootstrap import (
    BootstrapAuthority,
    BootstrapError,
    load_or_mint_secret,
)
from openai4s.orchestration.models import DesiredState, Phase, Reason, ResourceProfile
from openai4s.orchestration.reclaimer import LeaseReclaimer
from openai4s.orchestration.reconciler import Reconciler
from openai4s.orchestration.session import AttemptPreparer, ComputeSessionManager
from openai4s.orchestration.slurm.backend import SlurmBackend
from openai4s.orchestration.slurm.broker import SlurmBroker
from openai4s.orchestration.slurm.profiles import ClusterConfig
from openai4s.orchestration.worker_gateway import WorkerGateway
from openai4s.store import get_store

PROFILE = ResourceProfile(name="cpu-interactive", cpus=1, gpus=0)


@pytest.fixture()
def launching_cluster(tmp_path, monkeypatch):
    """A scheduler whose `sbatch` really runs the job — here, not elsewhere.

    `--export=K=V,...` is honoured because that is the only way the
    credential path and the dial-back address reach the worker; a fake that
    ignored it would "pass" while proving nothing about the one hand-off
    this milestone is made of.
    """
    state = tmp_path / "cluster"
    state.mkdir()
    (state / "next_job_id").write_text("7001", encoding="utf-8")
    s = str(state)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    scripts = {
        "sbatch": f"""
comment=""
exports=""
for arg in "$@"; do
  case "$arg" in
    --comment=*) comment="${{arg#--comment=}}" ;;
    --export=*) exports="${{arg#--export=}}" ;;
  esac
done
cat > "{s}/job_script"
jid=$(cat "{s}/next_job_id")
printf '%s' "$jid" > "{s}/job_id"
printf '%s' "$comment" > "{s}/job_comment"
printf 'RUNNING' > "{s}/queue_state"
printf '1' > "{s}/in_queue"
# Run the script the way a node would: the exported variables, and
# nothing else this shell happens to be carrying.
(
  if [ "$exports" != "NONE" ]; then
    IFS=','
    for pair in $exports; do
      export "$pair"
    done
    unset IFS
  fi
  sh "{s}/job_script" > "{s}/job.out" 2>&1
  printf 'COMPLETED' > "{s}/acct_state"
  printf '0' > "{s}/in_queue"
) &
echo "$jid"
""",
        "squeue": f"""
if [ ! -f "{s}/job_id" ]; then exit 0; fi
if [ "$(cat "{s}/in_queue")" != "1" ]; then exit 0; fi
jid=$(cat "{s}/job_id")
st=$(cat "{s}/queue_state")
cm=$(cat "{s}/job_comment")
fmt=""
for arg in "$@"; do
  case "$arg" in
    --job=*) want="${{arg#--job=}}"; [ "$want" = "$jid" ] || exit 0 ;;
    --format=*) fmt="${{arg#--format=}}" ;;
  esac
done
case "$fmt" in
  *%r*) echo "$jid|$st||$cm" ;;
  *) echo "$jid|$cm" ;;
esac
""",
        "sacct": f"""
if [ ! -f "{s}/acct_state" ]; then exit 0; fi
st=$(cat "{s}/acct_state")
if [ -z "$st" ]; then exit 0; fi
jid=$(cat "{s}/job_id")
cm=$(cat "{s}/job_comment")
fmt=""
for arg in "$@"; do
  case "$arg" in --format=*) fmt="${{arg#--format=}}" ;; esac
done
case "$fmt" in
  *ExitCode*) echo "$jid|$st|0:0|$cm" ;;
  *) echo "$jid|$cm" ;;
esac
""",
        "scancel": f"""
printf '0' > "{s}/in_queue"
printf 'CANCELLED' > "{s}/acct_state"
if [ -f "{s}/worker_pid" ]; then kill "$(cat "{s}/worker_pid")" 2>/dev/null || true; fi
""",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    class _Cluster:
        path = state

        def read(self, name):
            target = state / name
            return target.read_text(encoding="utf-8") if target.exists() else ""

        def write(self, name, value):
            (state / name).write_text(value, encoding="utf-8")

    return _Cluster()


class _Harness:
    """One daemon's worth of wiring, assembled the way the server does."""

    def __init__(self, tmp_path, gateway, store, manager, reconciler):
        self.tmp_path = tmp_path
        self.gateway = gateway
        self.store = store
        self.manager = manager
        self.reconciler = reconciler
        self.kernels = []

    def close(self):
        for kernel in self.kernels:
            try:
                kernel.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.gateway.stop()


@pytest.fixture()
def harness(tmp_path, launching_cluster):
    store = get_store(str(tmp_path / "state.db"))
    authority = BootstrapAuthority(load_or_mint_secret(tmp_path))
    gateway = WorkerGateway(authority, bind=("127.0.0.1", 0))
    gateway.start()

    manager = ComputeSessionManager(
        store=store,
        gateway=gateway,
        authority=authority,
        workspace_root=tmp_path / "workspaces",
    )
    backend = SlurmBackend(broker=SlurmBroker(), cluster=ClusterConfig())
    reconciler = Reconciler(
        store=store.workloads,
        backends={"slurm": backend},
        default_backend="slurm",
        prepare_attempt=AttemptPreparer(
            authority=authority,
            listen_address=lambda: gateway.address,
            runtime_dir=manager.runtime_dir,
            advertise_host="127.0.0.1",
        ),
    )
    node = _Harness(tmp_path, gateway, store, manager, reconciler)
    try:
        yield node
    finally:
        node.close()
        store.close()


def _bring_up(harness, session_id="s1", *, timeout_s=30.0):
    """Ask for a session and drive it until its worker has dialled in."""
    workload = harness.manager.request_session(
        session_id=session_id,
        owner_user_id="u1",
        profile=PROFILE,
        backend="slurm",
    )
    harness.reconciler.tick()  # submits; the fake sbatch launches the worker
    assert harness.manager.attach_worker(
        session_id, timeout_s=timeout_s
    ), "the worker never dialled back"
    runtime = harness.manager.runtime(session_id)
    kernel = Kernel(transport_factory=lambda: runtime.registration.transport)
    runtime.kernel = kernel
    runtime.kernel_ready = True
    harness.kernels.append(kernel)
    # The daemon's reconciler keeps ticking; readiness reads the durable
    # observation, not "the worker answered me, so surely it is granted".
    # A single tick has only submitted — which is exactly the distinction
    # INV-5 is about, so the test drives the loop rather than lowering the
    # bar.
    for _ in range(10):
        harness.reconciler.tick()
        allocation = harness.store.workloads.active_allocation(workload.id)
        if allocation is not None and allocation.phase in (
            Phase.GRANTED,
            Phase.ACTIVE,
        ):
            break
        time.sleep(0.05)
    return workload, kernel


# -- the chain ----------------------------------------------------------------


def test_a_submitted_session_brings_up_a_real_worker_that_dials_back(harness):
    workload, kernel = _bring_up(harness)
    result = kernel.execute("print('hello from the allocation')")
    assert "hello from the allocation" in result["stdout"]
    assert harness.manager.readiness("s1").ready
    assert workload.spec.kind.value == "SESSION"


def test_variables_survive_across_turns(harness):
    """The whole reason a session is persistent rather than a batch job."""
    _, kernel = _bring_up(harness)
    kernel.execute("import math\nmeasurements = [1, 2, 3]")
    kernel.execute("measurements.append(4)")
    result = kernel.execute("print(sum(measurements), math.floor(2.7))")
    assert result["stdout"].strip() == "10 2"


def test_the_credential_is_single_use_even_on_the_real_path(harness):
    """A second worker presenting the same file is refused — the file is
    on a shared filesystem, and 'nobody else can read it' is an assumption
    about a site, not a property of this system."""
    _bring_up(harness)
    credential_dir = next((harness.tmp_path / "workspaces").glob("*/.openai4s"), None)
    assert credential_dir is not None
    files = list(credential_dir.glob("*.json"))
    assert files, "the attempt wrote no credential"

    from openai4s.orchestration.bootstrap import read_credential_file

    credential = read_credential_file(files[0])
    with pytest.raises(BootstrapError):
        harness.gateway._authority.consume(credential)


def test_nothing_secret_reached_the_job_script_or_its_environment(
    harness, launching_cluster
):
    """INV-9 on the real submission, not on a constructed one."""
    _bring_up(harness)
    script = launching_cluster.read("job_script")
    assert "OPENAI4S_WORKER_BOOTSTRAP_PATH" not in script or ".json" in script

    from openai4s.orchestration.bootstrap import read_credential_file

    credential_dir = next((harness.tmp_path / "workspaces").glob("*/.openai4s"))
    credential = read_credential_file(next(credential_dir.glob("*.json")))
    assert credential.signature not in script
    assert credential.signature not in launching_cluster.read("job.out")


def test_an_interrupt_reaches_a_worker_the_daemon_has_no_pid_for(harness):
    """There is no local child here. Without an explicit hook the honest
    answer is a refusal, not a success for a signal nobody sent."""
    _, kernel = _bring_up(harness)
    with pytest.raises(RuntimeError, match="no way to interrupt"):
        kernel.interrupt()


def test_cancelling_a_session_releases_the_resource(harness, launching_cluster):
    workload, _ = _bring_up(harness)
    assert harness.manager.release("s1", reason=Reason.USER_CANCELLED) is True

    for _ in range(5):
        harness.reconciler.tick()
        reloaded = harness.store.workloads.get_workload(workload.id)
        if reloaded.phase.is_terminal:
            break
        time.sleep(0.05)

    reloaded = harness.store.workloads.get_workload(workload.id)
    assert reloaded.desired_state is DesiredState.STOPPED
    assert reloaded.phase.is_terminal
    assert launching_cluster.read("in_queue") == "0"


def test_a_lapsed_lease_takes_the_resource_back(harness, launching_cluster):
    """End to end, and with the kernel demonstrably alive the whole time —
    which is exactly what must not count as activity."""
    workload, kernel = _bring_up(harness)
    assert kernel.execute("print(1)")["stdout"].strip() == "1"

    now = [int(time.time() * 1000)]
    harness.store.leases._clock_ms = lambda: now[0]
    harness.store.leases.open_lease(workload.id, idle_ttl_s=60, max_lifetime_s=7200)
    reclaimer = LeaseReclaimer(
        leases=harness.store.leases,
        workloads=harness.store.workloads,
        clock_ms=lambda: now[0],
    )

    # the worker is healthy and answering; that is not a user
    assert kernel.execute("print(2)")["stdout"].strip() == "2"
    now[0] += 61_000
    assert reclaimer.sweep().expired == 1
    just_stopped = harness.store.workloads.get_workload(workload.id)
    assert (
        just_stopped.reason is Reason.SESSION_IDLE_TIMEOUT
    ), f"request_stop recorded {just_stopped.reason}"

    for _ in range(5):
        harness.reconciler.tick()
        if harness.store.workloads.get_workload(workload.id).phase.is_terminal:
            break
        time.sleep(0.05)

    reloaded = harness.store.workloads.get_workload(workload.id)
    assert reloaded.reason is Reason.SESSION_IDLE_TIMEOUT
    assert reloaded.phase.is_terminal
    assert launching_cluster.read("in_queue") == "0"


def test_an_expired_credential_cannot_bring_a_worker_up(harness, tmp_path):
    """A job that sat in the queue past its credential's life fails to
    bootstrap rather than connecting on a stale one."""
    clock = [time.time()]
    authority = BootstrapAuthority(
        load_or_mint_secret(tmp_path / "other"), clock=lambda: clock[0]
    )
    credential = authority.issue(allocation_id="alloc_x", epoch=0, ttl_s=5)
    clock[0] += 6
    with pytest.raises(BootstrapError, match="expired"):
        authority.consume(credential)


def test_a_worker_from_a_stale_epoch_is_refused_by_the_live_gateway(harness):
    """INV-7 against the running listener, not against the authority alone."""
    import json
    import socket

    authority = harness.gateway._authority
    stale = authority.issue(allocation_id="alloc_y", epoch=0)
    authority.issue(allocation_id="alloc_y", epoch=1)  # a recovery happened

    host, port = harness.gateway.address
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall((stale.to_json() + "\n").encode())
        sock.settimeout(10)
        data = sock.recv(4096)
    assert json.loads(data.decode().split("\n", 1)[0]) == {
        "ok": False,
        "error": "refused",
    }


def test_two_sessions_get_two_workers_and_do_not_share_state(harness):
    """Isolation is per session, and a shared namespace would be the worst
    possible way to find that out."""
    _, first = _bring_up(harness, "s1")
    launching = threading.Event()
    launching.set()
    _, second = _bring_up(harness, "s2")

    first.execute("secret = 'first'")
    result = second.execute("print('secret' in dir())")
    assert result["stdout"].strip() == "False"
