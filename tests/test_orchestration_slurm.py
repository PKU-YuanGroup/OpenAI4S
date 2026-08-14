"""The Slurm backend against a fake scheduler (plan M3a testing strategy).

Fake `sbatch`/`squeue`/`sacct`/`scancel` executables are generated into
`tmp_path` and put on PATH, with a programmable state sequence. They are
deliberately **not** in `tests/fixtures/` — that directory is byte-exact
captured data, and these are programs.

The cases that matter are the ones where being wrong is expensive:

* a lost submission must become `Unknown`, and the retry after it must
  reconcile by token into `Existing` rather than putting a second job on the
  cluster (INV-8);
* a job in neither the queue nor accounting is LOST, never COMPLETED;
* an unreachable scheduler is not a failed job;
* argv is constructed, and a hostile profile name is an argument, never a
  command (INV-9);
* the token in `--comment` is our own opaque id, and a credential-shaped
  environment variable is refused outright.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from openai4s.orchestration import (
    Allocation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.ports import Created, Existing, Rejected, Unknown
from openai4s.orchestration.slurm import (
    ClusterConfig,
    ClusterConfigError,
    SlurmBackend,
    SlurmBroker,
    SlurmCommandError,
    SubmitSpec,
    parse_cluster_config,
)
from openai4s.orchestration.slurm.profiles import EXAMPLE_CLUSTER_TOML

# -- the fake cluster ---------------------------------------------------------


def _install_fake_scheduler(tmp_path: Path, *, script: dict[str, str]) -> Path:
    """Write executable stubs for the named commands; return the bin dir."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in script.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture()
def fake_cluster(tmp_path, monkeypatch):
    """A cluster whose answers a test can rewrite mid-run.

    State lives in files rather than in the harness, because the broker
    shells out: whatever the test wants the scheduler to say has to be
    readable by another process.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / "queue_state").write_text("PENDING", encoding="utf-8")
    (state / "acct_state").write_text("", encoding="utf-8")
    (state / "in_queue").write_text("1", encoding="utf-8")
    (state / "next_job_id").write_text("4242", encoding="utf-8")

    s = str(state)
    bin_dir = _install_fake_scheduler(
        tmp_path,
        script={
            "sbatch": f"""
comment=""
for arg in "$@"; do
  case "$arg" in --comment=*) comment="${{arg#--comment=}}" ;; esac
done
cat > "{s}/last_script"
printf '%s\\n' "$@" > "{s}/last_argv"
printf '%s' "$comment" > "{s}/last_comment"
if [ -f "{s}/sbatch_hang" ]; then sleep 30; fi
if [ -f "{s}/sbatch_fail" ]; then echo "sbatch: error: refused" >&2; exit 1; fi
jid=$(cat "{s}/next_job_id")
printf '%s' "$comment" > "{s}/job_comment"
printf '%s' "$jid" > "{s}/job_id"
echo "$jid"
""",
            # Both readers honour the --format they are actually given. A
            # fake that always prints one shape would let a parser read the
            # wrong column and still pass — which is how a "green" test suite
            # ships a reconciliation that never matches its own token.
            "squeue": f"""
if [ ! -f "{s}/job_id" ]; then exit 0; fi
if [ "$(cat "{s}/in_queue")" != "1" ]; then exit 0; fi
jid=$(cat "{s}/job_id")
st=$(cat "{s}/queue_state")
cm=$(cat "{s}/job_comment")
reason=""
if [ -f "{s}/queue_reason" ]; then reason=$(cat "{s}/queue_reason"); fi
fmt=""
for arg in "$@"; do
  case "$arg" in
    --job=*) want="${{arg#--job=}}"; [ "$want" = "$jid" ] || exit 0 ;;
    --format=*) fmt="${{arg#--format=}}" ;;
  esac
done
case "$fmt" in
  *%r*) echo "$jid|$st|$reason|$cm" ;;
  *) echo "$jid|$cm" ;;
esac
""",
            "sacct": f"""
if [ ! -f "{s}/job_id" ]; then exit 0; fi
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
echo cancelled > "{s}/cancelled"
printf '0' > "{s}/in_queue"
printf 'CANCELLED' > "{s}/acct_state"
""",
        },
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    class _Cluster:
        path = state

        def set(self, name: str, value: str) -> None:
            (state / name).write_text(value, encoding="utf-8")

        def get(self, name: str) -> str:
            try:
                return (state / name).read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""

        def touch(self, name: str) -> None:
            (state / name).write_text("1", encoding="utf-8")

    return _Cluster()


def _allocation() -> Allocation:
    return Allocation(
        id=Allocation.new_id(),
        workload_id="wl_test0001",
        epoch=0,
        submission_token=SubmissionToken.mint(),
    )


def _spec() -> WorkloadSpec:
    return WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="gpu-batch", gpus=1),
        command=("python", "-c", "print(1)"),
    )


def _backend(**kwargs) -> SlurmBackend:
    return SlurmBackend(broker=SlurmBroker(timeout_s=10), **kwargs)


# -- the happy path -----------------------------------------------------------


def test_submit_observe_complete(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()

    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(result, Created), result
    assert result.handle.external_id == "4242"
    allocation.handle = result.handle

    # the token really did travel in --comment: this is what INV-8 rests on
    assert fake_cluster.get("last_comment") == allocation.submission_token.value

    assert backend.observe(allocation).phase is Phase.PENDING
    fake_cluster.set("queue_state", "RUNNING")
    assert backend.observe(allocation).phase is Phase.ACTIVE

    # a finished job leaves the queue and appears in accounting
    fake_cluster.set("in_queue", "0")
    fake_cluster.set("acct_state", "COMPLETED")
    observed = backend.observe(allocation)
    assert observed.phase is Phase.COMPLETED
    assert observed.reason is None


@pytest.mark.parametrize(
    "state,phase,reason",
    [
        ("TIMEOUT", Phase.FAILED, Reason.TIME_LIMIT_EXCEEDED),
        ("OUT_OF_MEMORY", Phase.FAILED, Reason.OUT_OF_MEMORY),
        ("NODE_FAIL", Phase.LOST, Reason.NODE_FAILED),
        ("PREEMPTED", Phase.LOST, Reason.PREEMPTED),
        ("CANCELLED", Phase.CANCELLED, Reason.USER_CANCELLED),
        ("FAILED", Phase.FAILED, None),
    ],
)
def test_terminal_state_mapping(fake_cluster, state, phase, reason):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    allocation.handle = result.handle

    fake_cluster.set("in_queue", "0")
    fake_cluster.set("acct_state", state)
    observed = backend.observe(allocation)
    assert observed.phase is phase
    assert observed.reason is reason
    # the scheduler's own word survives where nothing branches on it
    assert observed.diagnostics.get("state") == state


def test_cancel_is_idempotent(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    allocation.handle = backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    ).handle

    backend.cancel(allocation, reason=Reason.USER_CANCELLED)
    assert fake_cluster.get("cancelled").strip() == "cancelled"
    # a second cancel of something already gone must not raise: the cancel
    # barrier can run twice
    backend.cancel(allocation, reason=Reason.USER_CANCELLED)
    assert backend.observe(allocation).phase is Phase.CANCELLED


# -- INV-8: the lost submission ----------------------------------------------


def test_a_lost_submission_is_unknown_not_failure(fake_cluster):
    """sbatch hangs: we do not know whether the job landed."""
    fake_cluster.touch("sbatch_hang")
    backend = SlurmBackend(broker=SlurmBroker(timeout_s=0.5))
    allocation = _allocation()
    spec = _spec()

    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(result, Unknown), result
    # the token rides along, because the only correct next step needs it
    assert result.token == allocation.submission_token


def test_retry_after_unknown_reconciles_instead_of_double_submitting(fake_cluster):
    """The defect this whole mechanism exists to prevent: two jobs holding
    two GPUs because one submission's response was lost."""
    backend = _backend()
    allocation = _allocation()
    spec = _spec()

    # the submission landed, but imagine the response never arrived
    first = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(first, Created)
    submitted_id = fake_cluster.get("job_id")

    # a naive retry would submit again; this one must find the token first
    fake_cluster.set("next_job_id", "9999")
    second = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(second, Existing), second
    assert second.handle.external_id == submitted_id
    # the cluster still holds exactly the first job
    assert fake_cluster.get("job_id") == submitted_id


def test_find_by_token_answers_from_accounting_too(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    backend.submit(allocation=allocation, spec=spec, profile=spec.profile)

    # the job has left the queue by the time we reconcile
    fake_cluster.set("in_queue", "0")
    fake_cluster.set("acct_state", "RUNNING")
    found = backend.find_by_token(allocation.submission_token)
    assert found is not None and found.external_id == "4242"


def test_unknown_token_finds_nothing(fake_cluster):
    backend = _backend()
    assert backend.find_by_token(SubmissionToken.mint()) is None


# -- absence, and unreachability, are different from failure ------------------


def test_a_job_in_neither_queue_nor_accounting_is_lost(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    allocation.handle = backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    ).handle

    fake_cluster.set("in_queue", "0")
    fake_cluster.set("acct_state", "")
    observed = backend.observe(allocation)
    assert observed.phase is Phase.LOST, "absence must never read as COMPLETED"
    assert observed.reason is Reason.WORKER_LOST


def test_an_unreachable_scheduler_is_not_a_failed_job(tmp_path, monkeypatch):
    """A cluster outage must not terminate everyone's work."""
    monkeypatch.setenv("PATH", str(tmp_path))  # no scheduler at all
    backend = _backend()
    allocation = _allocation()
    allocation.phase = Phase.ACTIVE
    from openai4s.orchestration.models import ExternalHandle

    allocation.handle = ExternalHandle(backend="slurm", external_id="1")

    observed = backend.observe(allocation)
    assert observed.phase is Phase.ACTIVE, "the phase must not move"
    assert observed.reason is Reason.BACKEND_UNAVAILABLE


def test_a_refused_submission_is_rejected(fake_cluster):
    fake_cluster.touch("sbatch_fail")
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(result, Rejected)
    assert result.reason is Reason.BACKEND_REJECTED


def test_unschedulable_pending_reason_becomes_a_failure(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = _spec()
    allocation.handle = backend.submit(
        allocation=allocation, spec=spec, profile=spec.profile
    ).handle
    fake_cluster.set("queue_reason", "PartitionNodeLimit")
    observed = backend.observe(allocation)
    assert observed.phase is Phase.FAILED
    assert observed.reason is Reason.UNSCHEDULABLE

    # ordinary waiting stays waiting — a busy cluster is not a broken one
    fake_cluster.set("queue_reason", "Resources")
    assert backend.observe(allocation).phase is Phase.PENDING


# -- INV-9: argv construction and secret hygiene ------------------------------


def test_argv_is_constructed_not_concatenated():
    broker = SlurmBroker()
    spec = SubmitSpec(
        job_name="openai4s-wl_1",
        comment="tok_abc",
        script="#!/bin/sh\necho hi\n",
        cpus=4,
        memory_mb=8192,
        gpus=2,
        walltime_s=7200,
        partition="gpu",
        qos="interactive",
    )
    argv = broker.build_submit_argv(spec)
    assert argv[0] == "sbatch"
    assert "--parsable" in argv
    assert "--cpus-per-task=4" in argv
    assert "--mem=8192M" in argv
    assert "--time=02:00:00" in argv
    assert "--gpus=2" in argv
    assert "--partition=gpu" in argv
    assert "--qos=interactive" in argv
    assert "--comment=tok_abc" in argv
    # no environment named -> nothing inherited, so daemon API keys cannot
    # ride along by default
    assert "--export=NONE" in argv


def test_a_hostile_name_is_an_argument_never_a_command(fake_cluster):
    """The value that would be a shell injection if argv were a string."""
    backend = _backend()
    allocation = _allocation()
    hostile = "gpu; touch /tmp/openai4s-pwned"
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name=hostile),
        command=("echo", "ok"),
    )
    # the job name is derived from ids we mint, so this submits fine and the
    # hostile text simply never reaches a shell
    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(result, Created)
    assert not Path("/tmp/openai4s-pwned").exists()


def test_unsafe_job_name_or_comment_is_refused():
    for bad in ("has space", "semi;colon", "new\nline", "", "x" * 200):
        with pytest.raises(ValueError):
            SubmitSpec(job_name=bad, comment="tok_a", script="x")
        with pytest.raises(ValueError):
            SubmitSpec(job_name="ok", comment=bad, script="x")


def test_credential_shaped_environment_is_refused():
    """INV-9: a secret must travel as a path to an 0600 file, not as an
    environment variable the scheduler records."""
    for key in ("OPENAI4S_LLM_API_KEY", "MY_SECRET", "db_password", "AUTH_TOKEN"):
        with pytest.raises(ValueError, match="INV-9|invalid environment"):
            SubmitSpec(
                job_name="ok",
                comment="tok_a",
                script="x",
                environment={key: "value"},
            )
    # an ordinary variable is fine
    spec = SubmitSpec(
        job_name="ok", comment="tok_a", script="x", environment={"OMP_NUM_THREADS": "4"}
    )
    assert "--export=OMP_NUM_THREADS=4" in SlurmBroker().build_submit_argv(spec)


def test_the_submitted_script_quotes_its_command(fake_cluster):
    backend = _backend()
    allocation = _allocation()
    spec = WorkloadSpec(
        kind=WorkloadKind.BATCH,
        profile=ResourceProfile(name="cpu-interactive"),
        command=("python", "-c", "print('it works')"),
        workdir="/tmp/some dir",
    )
    backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    script = fake_cluster.get("last_script")
    assert "cd '/tmp/some dir'" in script
    assert "'print('\"'\"'it works'\"'\"')'" in script


# -- cluster.toml -------------------------------------------------------------


def test_example_config_parses_on_every_supported_python():
    """The floor is 3.10 and tomllib is 3.11+, so this is the case a
    tomllib-only reader would have failed on the interpreter CI actually
    runs."""
    cfg = parse_cluster_config(EXAMPLE_CLUSTER_TOML)
    assert sorted(cfg.profiles) == ["cpu-interactive", "gpu-batch", "gpu-interactive"]
    batch = cfg.profile("gpu-batch")
    assert batch.partition == "gpu" and batch.qos == "normal"
    assert batch.resources.gpus == 1
    assert batch.resources.walltime_s == 172800
    assert cfg.job_name_prefix == "openai4s"


def test_profile_public_view_never_names_a_queue():
    """D5: the admin route shows which profiles exist, not where they run."""
    cfg = parse_cluster_config(
        '[profiles.p]\npartition = "secret-queue"\nqos = "secret-qos"\ngpus = 2\n'
    )
    public = str(cfg.profile("p").public())
    assert "secret-queue" not in public
    assert "secret-qos" not in public
    assert "'gpus': 2" in public


def test_malformed_config_is_refused_with_its_location():
    for text in (
        "[profiles.x]\ncpus = [1, 2]\n",  # arrays: refused, not guessed
        "[cluster\nname = 'x'\n",
        "not a key value line\n",
        "[profiles.x]\ncpus = 1.5\n",  # floats: refused
    ):
        with pytest.raises(ClusterConfigError):
            parse_cluster_config(text, source="cluster.toml")


def test_absent_config_is_not_an_error(tmp_path):
    from openai4s.orchestration.slurm import load_cluster_config

    cfg = load_cluster_config(tmp_path)
    assert cfg.configured is False
    assert cfg.public()["profiles"] == []


def test_an_unconfigured_profile_still_submits(fake_cluster):
    """A profile with no site mapping uses the scheduler's own defaults
    rather than refusing: an unconfigured cluster.toml is a default, not an
    outage."""
    backend = SlurmBackend(broker=SlurmBroker(timeout_s=10), cluster=ClusterConfig())
    allocation = _allocation()
    spec = _spec()
    result = backend.submit(allocation=allocation, spec=spec, profile=spec.profile)
    assert isinstance(result, Created)
    argv = fake_cluster.get("last_argv")
    assert "--partition" not in argv


def test_backend_diagnostics_report_availability(fake_cluster):
    backend = _backend(cluster=parse_cluster_config(EXAMPLE_CLUSTER_TOML))
    diag = backend.diagnostics()
    assert diag["backend"] == "slurm"
    assert diag["available"] is True
    assert "gpu-batch" in diag["profiles"]


def test_broker_timeout_is_distinguishable_from_refusal(fake_cluster):
    """A caller must be able to tell 'it said no' from 'it did not answer';
    conflating them is how INV-8 gets violated."""
    fake_cluster.touch("sbatch_hang")
    broker = SlurmBroker(timeout_s=0.5)
    with pytest.raises(SlurmCommandError) as timed:
        broker.submit(SubmitSpec(job_name="n", comment="tok_a", script="x"))
    assert timed.value.timed_out is True

    (fake_cluster.path / "sbatch_hang").unlink()
    fake_cluster.touch("sbatch_fail")
    with pytest.raises(SlurmCommandError) as refused:
        broker.submit(SubmitSpec(job_name="n", comment="tok_a", script="x"))
    assert refused.value.timed_out is False
    assert refused.value.returncode == 1


def test_the_fake_cluster_is_really_being_used(fake_cluster):
    """Guards the whole file: if PATH injection stopped working, every test
    above would pass against a machine with no scheduler and prove nothing."""
    out = subprocess.run(
        ["sbatch", "--comment=tok_probe"],
        input="#!/bin/sh\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "4242"
    assert fake_cluster.get("last_comment") == "tok_probe"
