"""Trust-boundary regressions for host.compute.

Every test here pins one rule: **the manager must never report success it did
not observe.** Before these, the ssh path had no reachable `failed` state at
all — `_result_ssh` hardcoded `exit_code: 0` and discarded the probe's output,
so a job that died on `command not found` was harvested and announced as done.
The byoc path had the mirror-image bug: `job_exit_code` of None is falsy, so a
job whose terminal exit code could not be read fell through to "done".

The fault matrix below is the proposal's: no network, probe failure, a killed
remote process, a partial transfer, a hostile archive, a wedged helper, and a
cancel that never lands. Each must resolve to `failed`, `incomplete`, or
`unknown` — never `done`.
"""
import io
import os
import shutil
import subprocess
import tarfile
import time
import types
from pathlib import Path

import pytest

from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.compute.safe_archive import UnsafeArchiveError, safe_extract_tar


@pytest.fixture
def mgr(tmp_path):
    cfg = types.SimpleNamespace(
        data_dir=tmp_path / "data", skills_dir=tmp_path / "skills"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "ws").mkdir()
    return ComputeManager(cfg, workspace=tmp_path / "ws")


def _ssh_job(mgr, job_id="job-abc"):
    job = {
        "job_id": job_id,
        "provider": "ssh:lab",
        "alias": "lab",
        "workdir": "~/.openai4s-jobs/" + job_id,
        "status": "running",
        "pid": "4242",
        "outputs": None,
    }
    mgr._jobs[job_id] = job
    return job


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------------------
# ssh: the probe
# --------------------------------------------------------------------------


def _ssh_replies(probe: bytes, *, harvest: bytes = b"OPENAI4S_HARVEST empty\n", scp=0):
    """Route the two ssh round trips a poll makes to their own answers.

    `_result_ssh` asks the host two separate questions now: what the exit code
    is, and what files the job left behind. A single stub answering both with
    the same bytes would silently feed the exit code to the harvest parser.
    """

    def fake_run(argv, **kw):
        if argv[0] == "scp":
            return _Proc(scp, b"", b"" if scp == 0 else b"scp broke")
        if argv[0] != "ssh":
            return _Proc(0)
        return _Proc(0, harvest if "OPENAI4S_HARVEST" in argv[2] else probe)

    return fake_run


class _FakeCatProc:
    """Stand in for the `ssh cat` process `_fetch_archive_capped` streams from."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        return None


def _fake_transfer(returncode: int, stdout: bytes = b"", stderr: bytes = b""):
    """Patch `subprocess.Popen` for the capped archive transfer (ssh cat)."""

    def popen(argv, **kw):
        return _FakeCatProc(returncode, stdout, stderr)

    return popen


def test_ssh_missing_rc_is_unknown_not_done(mgr, monkeypatch):
    """The headline regression: a remote process that vanished without writing
    an exit code used to report done/exit_code 0."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"NORC\n"), raising=True
    )
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "unknown"
    assert out["exit_code"] is None
    assert out["error_kind"] == "unknown_state"
    assert "wrote no exit code" in out["reason"]


def test_ssh_nonzero_rc_is_failed(mgr, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _ssh_replies(b"1\n"), raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    assert out["exit_code"] == 1


def test_ssh_zero_rc_succeeds(mgr, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _ssh_replies(b"0\n"), raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "succeeded"
    assert out["exit_code"] == 0


def test_ssh_running_pid_reports_running(mgr, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"RUNNING\n"), raising=True
    )
    assert mgr._result_ssh(_ssh_job(mgr))["status"] == "running"


def test_ssh_probe_transport_failure_is_unknown(mgr, monkeypatch):
    """Network down / host unreachable. Our inability to observe the job says
    nothing about the job, so it must not be resolved either way."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Proc(255, b"", b"ssh: connect to host lab port 22: No route"),
        raising=True,
    )
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "unknown"
    assert "No route" in out["reason"]


def test_ssh_probe_timeout_is_unknown(mgr, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=30)

    monkeypatch.setattr(subprocess, "run", boom, raising=True)
    assert mgr._result_ssh(_ssh_job(mgr))["status"] == "unknown"


def test_ssh_unparseable_rc_is_unknown(mgr, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"not-a-number\n"), raising=True
    )
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "unknown"
    assert "unparseable" in out["reason"]


def test_ssh_log_harvest_failure_does_not_claim_clean_success(mgr, monkeypatch):
    """rc==0 is the job's own verdict and stays trustworthy, but a partial
    transfer must not be dressed up as a complete result."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _ssh_replies(b"0\n", harvest=b"OPENAI4S_HARVEST archive 10\n"),
        raising=True,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_transfer(1, b"", b"cat: transfer broke"),
        raising=True,
    )
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    # The distinction `incomplete` used to carry: rc was 0, so this is not
    # an ordinary non-zero failure — the outputs simply cannot be trusted.
    assert out["exit_code"] == 0
    assert "transfer" in out["harvest_error"]


def test_ssh_failed_job_with_bad_harvest_stays_failed(mgr, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _ssh_replies(b"3\n", harvest=b"OPENAI4S_HARVEST archive 10\n"),
        raising=True,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_transfer(1, b"", b"cat: transfer broke"),
        raising=True,
    )
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    assert out["exit_code"] == 3


def test_ssh_a_vanished_workdir_is_a_harvest_failure(mgr, monkeypatch):
    """The staging step exits 3 when the work directory is gone. Reporting
    success over a harvest that could not even start is the same false success
    as reporting it over an empty one."""

    def fake_run(argv, **kw):
        if argv[0] == "ssh" and "OPENAI4S_HARVEST" in argv[2]:
            return _Proc(3, b"", b"openai4s: work directory is gone")
        return _Proc(0, b"0\n")

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    assert "work directory is gone" in out["harvest_error"]


def test_ssh_unknown_is_not_cached_onto_the_job(mgr, monkeypatch):
    """`unknown` is unresolved, not terminal — a later poll must be free to
    resolve it once the host comes back."""
    job = _ssh_job(mgr)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(255), raising=True)
    assert mgr._result_ssh(job)["status"] == "unknown"
    assert job["status"] == "running"

    monkeypatch.setattr(subprocess, "run", _ssh_replies(b"0\n"), raising=True)
    assert mgr._result_ssh(job)["status"] == "succeeded"


# --------------------------------------------------------------------------
# ssh: submit must make the exit code observable at all
# --------------------------------------------------------------------------


def test_ssh_submit_records_exit_code_atomically(mgr, monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["remote"] = argv[2]
        return _Proc(0, b"OPENAI4S_JOB 9911 9911\n")

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    out = mgr.submit({"provider": "ssh:lab", "command": "echo hi"})
    assert out["status"] == "running"
    remote = seen["remote"]
    # rc is captured, staged, and moved into place — never written in situ,
    # so a concurrent reader cannot observe a half-written code.
    assert "rc=$?" in remote
    assert ".rc.tmp" in remote
    assert "mv -f .rc.tmp .rc" in remote
    # A reused workdir must not leave a stale code behind.
    assert "rm -f .rc .rc.tmp" in remote


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TestAgainstRealBash:
    """The submit/probe contract executed by a real shell, not a mock.

    This is where the worst defect lived, and no mocked test could have found
    it: `&` binds looser than `&&`, so the original

        mkdir -p W && cd W && cat > run.sh && nohup bash run.sh ... & echo $!

    made the *entire* and-list asynchronous, and POSIX assigns an async list's
    stdin to /dev/null. `cat > run.sh` therefore read nothing, run.sh was
    written as 0 bytes, and `bash run.sh` exited 0 having run no job at all —
    which `_result_ssh`'s hardcoded `exit_code: 0` then reported as success.
    Every ssh job "succeeded" without executing.

    Mocking subprocess.run asserts the string we hoped to send; only a shell
    tells us what that string does. `ssh` is swapped for a local `bash -c` —
    same non-interactive, no-job-control, piped-stdin conditions sshd gives a
    remote command.
    """

    #: The shell sshd actually hands a non-interactive command. `bash` is the
    #: friendly case and the one the original harness used; it is also the one
    #: where `set -m` happens to work, which is precisely why the process-group
    #: defect survived a real-shell test. `sh` is dash on Debian/Ubuntu and ash
    #: on Alpine — the login shells of most clusters and every slim container —
    #: and there `set -m` is a no-op, so `$!` is a pid and emphatically not a
    #: process group id.
    SHELL = "bash"

    @pytest.fixture
    def local_ssh(self, mgr, monkeypatch, tmp_path):
        real_run = subprocess.run
        real_popen = subprocess.Popen
        shell = shutil.which(self.SHELL)
        if shell is None:
            pytest.skip(f"{self.SHELL} is not on this host")

        def fake_popen(argv, **kw):
            # The capped harvest transfer streams the archive over `ssh cat`;
            # route it through the real shell so it actually cats the file.
            if argv[0] == "ssh":
                return real_popen([shell, "-c", argv[2]], start_new_session=True, **kw)
            return real_popen(argv, **kw)

        def fake_run(argv, **kw):
            if argv[0] == "ssh":
                # start_new_session mirrors sshd: OpenSSH's do_child() calls
                # setsid() before exec'ing the remote command, so the job is a
                # session leader with a process group of its own. Without it
                # the job inherits *pytest's* group, and a cancel that
                # correctly signals the job's group takes the test runner with
                # it — which is exactly the blast radius this whole mechanism
                # is about, observed on the wrong process.
                return real_run(
                    [shell, "-c", argv[2]],
                    start_new_session=True,
                    **{k: v for k, v in kw.items() if k != "timeout"},
                )
            if argv[0] == "scp":
                # A copy that copies. Stubbing scp to a bare success meant the
                # harvest was never exercised at all — the archive the remote
                # staged never reached the extractor, so nothing downstream of
                # the transfer had a test.
                source, destination = argv[-2], argv[-1]
                _alias, _, remote = source.partition(":")
                remote = remote.replace("~", str(tmp_path), 1)
                if not Path(remote).is_file():
                    return _Proc(1, b"", b"scp: no such file")
                shutil.copy2(remote, destination)
                return _Proc(0)
            return real_run(argv, **kw)

        monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
        monkeypatch.setattr(subprocess, "Popen", fake_popen, raising=True)
        monkeypatch.setenv("HOME", str(tmp_path))
        return mgr

    def _run_to_completion(self, mgr, command):
        out = mgr.submit({"provider": "ssh:lab", "command": command})
        job = mgr._jobs[out["job_id"]]
        for _ in range(100):
            res = mgr._result_ssh(job)
            if res["status"] != "running":
                return res, job
            time.sleep(0.05)
        pytest.fail("job never reached a terminal state")

    def test_the_job_script_actually_reaches_the_remote(self, local_ssh, tmp_path):
        """run.sh used to arrive empty. If this regresses, every other ssh
        assertion silently tests a job that never ran."""
        res, job = self._run_to_completion(local_ssh, "echo hello-from-the-job")
        workdir = Path(job["workdir"].replace("~", str(tmp_path)))
        assert (workdir / "run.sh").read_text() == "echo hello-from-the-job"
        assert (workdir / "stdout.log").read_text().strip() == "hello-from-the-job"
        assert res["status"] == "succeeded"
        assert res["exit_code"] == 0

    def test_command_not_found_is_failed_not_done(self, local_ssh):
        """The canonical false success: this reported done/exit_code 0."""
        res, _ = self._run_to_completion(local_ssh, "definitely-not-a-real-binary")
        assert res["status"] == "failed"
        assert res["exit_code"] == 127

    def test_the_recorded_pgid_is_the_job_s_real_process_group(
        self, local_ssh, tmp_path
    ):
        """The group we will signal must be the group the job is actually in.

        This used to be asserted as `os.getpgid(pid) == pid` — true under bash
        with job control, and false in the shell most hosts hand an `ssh host
        cmd`: `set -m` does nothing there, so `$!` is a pid whose group is the
        login shell's. `kill -- -$!` then addressed a group that did not exist,
        and on several shells still exited 0, so a cancel reported a freed
        allocation over a command tree that kept running.

        Executed for real, and compared against the kernel's own answer:
        whether a background job lands in a group of its own is a property of
        the shell, and a mocked `subprocess.run` can only assert the string we
        hoped to send.
        """
        out = local_ssh.submit(
            {"provider": "ssh:lab", "command": "sleep 60 & echo $! > child.pid; wait"}
        )
        job = local_ssh._jobs[out["job_id"]]
        pid = int(job["pid"])

        assert job["pgid"], "a job with no recorded group cannot be cancelled"
        assert int(job["pgid"]) == os.getpgid(pid)
        assert int(job["pgid"]) > 1, "0 and 1 are never a job's group"
        # And the child inherits it, which is the whole point: the group is
        # what reaches processes `run.sh` started.
        marker = Path(job["workdir"].replace("~", str(tmp_path))) / "child.pid"
        for _ in range(100):
            if marker.exists() and marker.read_text().strip():
                break
            time.sleep(0.05)
        assert os.getpgid(int(marker.read_text().strip())) == int(job["pgid"])

    def test_the_recorded_pgid_is_not_the_test_runner_s(self, local_ssh):
        """A group id read off the wrong process is worse than none at all.

        sshd gives the remote command its own session; if that ever stops being
        true, the group recorded here would be the caller's, and `cancel` would
        take down the process that issued it.
        """
        out = local_ssh.submit({"provider": "ssh:lab", "command": "sleep 30"})
        job = local_ssh._jobs[out["job_id"]]
        assert int(job["pgid"]) != os.getpgid(0)
        local_ssh.cancel({"job_id": out["job_id"]})

    def test_cancel_kills_the_whole_remote_tree(self, local_ssh, tmp_path):
        out = local_ssh.submit(
            {"provider": "ssh:lab", "command": "sleep 60 & echo $! > child.pid; wait"}
        )
        job = local_ssh._jobs[out["job_id"]]
        workdir = Path(job["workdir"].replace("~", str(tmp_path)))

        marker = workdir / "child.pid"
        for _ in range(100):
            if marker.exists() and marker.read_text().strip():
                break
            time.sleep(0.05)
        child = int(marker.read_text().strip())
        assert _alive(child), "the child should be running before we cancel"

        local_ssh.cancel({"job_id": out["job_id"]})

        for _ in range(100):
            if not _alive(child):
                break
            time.sleep(0.05)
        assert not _alive(child), "cancel must reach run.sh's children too"

    @pytest.mark.skipif(
        not (shutil.which("timeout") or shutil.which("gtimeout")),
        reason="the deadline is enforced by timeout(1), which this host lacks",
    )
    def test_a_deadline_is_actually_applied(self, local_ssh):
        """`timeout_seconds` was accepted and never read, so an ssh job ran
        until the host reclaimed it."""
        out = local_ssh.submit(
            {"provider": "ssh:lab", "command": "sleep 30", "timeout_seconds": 1}
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)
        assert (
            res["status"] == "timed_out"
        ), "a job killed by its deadline is not an ordinary failure"
        assert res["exit_code"] == 124

    @pytest.mark.skipif(
        bool(shutil.which("timeout") or shutil.which("gtimeout")),
        reason="this host has timeout(1), so the refusal path cannot be reached",
    )
    def test_a_host_without_timeout_refuses_the_submit(self, local_ssh):
        """Silently dropping the deadline would be the worse failure: the
        caller believes a limit is in force and the job runs unbounded."""
        with pytest.raises(ComputeError) as error:
            local_ssh.submit(
                {"provider": "ssh:lab", "command": "sleep 5", "timeout_seconds": 1}
            )
        assert "timeout" in str(error.value)

    def test_no_deadline_leaves_the_job_unwrapped(self, local_ssh, tmp_path):
        """Only wrap when a deadline was asked for — `timeout` is not present
        on every host, and requiring it unconditionally would break hosts that
        work today."""
        res, job = self._run_to_completion(local_ssh, "echo fine")
        assert res["status"] == "succeeded"
        workdir = Path(job["workdir"].replace("~", str(tmp_path)))
        assert (workdir / "stdout.log").read_text().strip() == "fine"

    def test_explicit_nonzero_exit_is_preserved(self, local_ssh):
        res, _ = self._run_to_completion(local_ssh, "echo out; exit 42")
        assert res["status"] == "failed"
        assert res["exit_code"] == 42

    def test_killed_job_reports_unknown_not_success(self, local_ssh, tmp_path):
        """SIGKILL leaves no .rc. The job is gone, we have no verdict, and
        `unknown` is the only honest answer."""
        out = local_ssh.submit({"provider": "ssh:lab", "command": "sleep 30"})
        job = local_ssh._jobs[out["job_id"]]
        assert local_ssh._result_ssh(job)["status"] == "running"
        pid = int(job["pid"])
        os.kill(pid, 9)
        for _ in range(60):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)
        assert res["status"] == "unknown"
        assert res["exit_code"] is None

    def test_declared_outputs_are_actually_harvested(self, local_ssh, tmp_path):
        """The documented main path, which could not succeed at all.

        `submit_job` accepts an `outputs` declaration and the bundled skill's
        worked example declares one — but this transport copied back
        `stdout.log` and `stderr.log` and nothing else. Every declared pattern
        therefore matched nothing, `reconcile` reported it missing, and the
        manager forced a job that had exited 0 having written exactly what it
        promised to `failed`.
        """
        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": "printf 'a,b\\n1,2\\n' > scores.csv; echo done",
                "outputs": ["*.csv"],
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)

        assert res["status"] == "succeeded", res.get("reason") or res
        assert [Path(p).name for p in res["featured_files"]] == ["scores.csv"]
        harvested = Path(res["featured_files"][0])
        assert harvested.read_text() == "a,b\n1,2\n", "the bytes must arrive too"
        entry = next(e for e in res["artifact_manifest"] if e["path"] == "scores.csv")
        assert len(entry["sha256"]) == 64 and entry["size"] == 8

    def test_a_declared_output_the_job_never_wrote_is_still_missing(self, local_ssh):
        """Harvesting for real must not turn the promise check into a rubber
        stamp: a job that declared `model.pt` and wrote nothing is not a
        success just because the transport now works."""
        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": "echo nothing-written",
                "outputs": ["model.pt"],
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)
        assert res["status"] == "failed"
        assert res["exit_code"] == 0
        assert res["unharvested_outputs"] == ["model.pt"]

    def test_an_output_declared_remote_is_not_owed(self, local_ssh):
        """`residency: 'remote'` means "leave it on the cluster". Failing the
        job for honouring that would punish the caller for saying so."""
        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": "echo ok",
                "outputs": [{"glob": "huge.bin", "residency": "remote"}],
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)
        assert res["status"] == "succeeded"

    def test_an_output_declared_remote_is_never_moved(self, local_ssh):
        """The other half of the same declaration, and the one that was missing.

        `residency: remote` was honoured only by the *reconciler*: the job was
        no longer blamed for the file staying put, while the harvest tarred it,
        downloaded it, and listed it in `output_files` regardless. The
        declaration's entire purpose is that the bytes do not move.
        """
        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": (
                    "mkdir -p ckpt; printf 'weights' > ckpt/model.pt; "
                    "printf 'done' > report.txt"
                ),
                "outputs": [
                    "report.txt",
                    {"glob": "ckpt/*.pt", "residency": "remote"},
                ],
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)

        assert res["status"] == "succeeded"
        harvested = {Path(p).name for p in res["output_files"]}
        assert "report.txt" in harvested
        assert (
            "model.pt" not in harvested
        ), f"a residency:remote output was downloaded and listed: {harvested}"
        local_copy = Path(res["output_files"][0]).parent / "ckpt" / "model.pt"
        assert not local_copy.exists(), "it must not be on local disk at all"
        # The workdir carries a literal `~`; the remote shell expands it, and
        # the fixture points HOME at the temp tree standing in for the cluster.
        remote_workdir = Path(job["workdir"].replace("~", os.environ["HOME"], 1))
        assert (
            remote_workdir / "ckpt" / "model.pt"
        ).is_file(), "and it must still be on the cluster, where it was asked to stay"
        left = {item["path"]: item for item in res["left_on_remote_files"]}
        assert "ckpt/model.pt" in left
        assert left["ckpt/model.pt"]["reason"] == "residency"
        assert left["ckpt/model.pt"]["uri"].startswith("lab:")

    def test_an_oversized_output_stays_on_the_host_and_says_so(
        self, local_ssh, monkeypatch
    ):
        """Bounded by construction, and never silently: a file left behind
        comes back named, with a URI the next job can chain from."""
        monkeypatch.setattr(
            "openai4s.compute.manager.HARVEST_MAX_FILE_BYTES", 64, raising=True
        )
        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": (
                    "head -c 4096 /dev/zero > big.bin; printf 'small' > small.txt"
                ),
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)

        harvested = {Path(p).name for p in res["output_files"]}
        assert "small.txt" in harvested
        assert "big.bin" not in harvested
        left = {item["path"]: item for item in res["left_on_remote_files"]}
        assert "big.bin" in left
        assert left["big.bin"]["reason"] == "threshold"
        assert left["big.bin"]["uri"].startswith("lab:")

    def test_the_harvest_lands_where_save_artifact_will_accept_it(
        self, local_ssh, tmp_path
    ):
        """The other half of the same broken main path.

        The harvest went to `<data_dir>/hpc/<job>` and came back as an absolute
        path, but `host.save_artifact` resolves through the Host file service,
        which only accepts paths inside the *session workspace*. So every
        normal job with a featured output raised "path escapes the workspace"
        at exactly the step the skill tells the agent to take next.
        """
        from openai4s.host.files import WorkspaceFileService

        out = local_ssh.submit(
            {
                "provider": "ssh:lab",
                "command": "printf 'result' > answer.txt",
                "outputs": ["answer.txt"],
            }
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)

        workspace = tmp_path / "ws"
        files = WorkspaceFileService(
            data_dir=tmp_path / "data",
            frame_id=lambda: "frame-1",
            workspace=lambda: workspace,
        )
        featured = res["featured_files"][0]
        # The gate itself, not a re-implementation of it.
        assert files.resolve(featured, must_exist=True).read_text() == "result"
        assert files.relative(Path(featured)) == f"hpc/{out['job_id']}/answer.txt"

    def test_an_ordinary_exit_124_is_not_a_deadline(self, local_ssh):
        """124 is GNU `timeout`'s expiry code and also just a number.

        With no `timeout_seconds` asked for, nothing armed a deadline — so
        reporting `timed_out` sent the caller looking for a walltime that was
        never set, and hid a real non-zero failure behind an infrastructure
        excuse. The deadline now writes its own marker and the host reads that
        instead of sniffing the code.
        """
        res, job = self._run_to_completion(local_ssh, "exit 124")
        assert res["status"] == "failed"
        assert res["exit_code"] == 124
        workdir = Path(job["workdir"].replace("~", str(Path.home())))
        assert not (workdir / ".timeout").exists()

    @pytest.mark.skipif(
        not (shutil.which("timeout") or shutil.which("gtimeout")),
        reason="the deadline is enforced by timeout(1), which this host lacks",
    )
    def test_a_fired_deadline_leaves_its_own_marker(self, local_ssh, tmp_path):
        """The marker is the evidence; the exit code alone never was."""
        out = local_ssh.submit(
            {"provider": "ssh:lab", "command": "sleep 30", "timeout_seconds": 1}
        )
        job = local_ssh._jobs[out["job_id"]]
        for _ in range(200):
            res = local_ssh._result_ssh(job)
            if res["status"] != "running":
                break
            time.sleep(0.05)
        assert res["status"] == "timed_out"
        workdir = Path(job["workdir"].replace("~", str(tmp_path)))
        assert (workdir / ".timeout").exists()

    def test_a_chatty_login_shell_cannot_impersonate_the_job(
        self, local_ssh, monkeypatch
    ):
        """`echo $!` was the whole acknowledgement, so the first line a login
        shell printed became the "pid" the host recorded and later signalled.

        A motd, a `.bashrc` echo, or an `ssh` warning is enough. Here the
        banner claims to be pid 1 — signalling init's group is the worst
        available outcome of believing it.
        """
        chatty = subprocess.run

        def noisy(argv, **kw):
            proc = chatty(argv, **kw)
            stdout = getattr(proc, "stdout", b"") or b""
            if argv[0] == "ssh":
                return _Proc(
                    proc.returncode,
                    b"Welcome to lab01\n1 1\n" + stdout,
                    getattr(proc, "stderr", b""),
                )
            return proc

        monkeypatch.setattr(subprocess, "run", noisy, raising=True)
        out = local_ssh.submit({"provider": "ssh:lab", "command": "sleep 30"})
        job = local_ssh._jobs[out["job_id"]]
        assert int(job["pid"]) > 1, "a banner line is not a job id"
        assert int(job["pgid"]) > 1
        local_ssh.cancel({"job_id": out["job_id"]})


class TestAgainstRealPosixSh(TestAgainstRealBash):
    """The same contract under `sh` — the shell most hosts actually give us.

    `bash` above is the friendly case, and it is friendly in exactly the way
    that hid the defect: `set -m` enables job control there, so the background
    job really did become its own group leader and `$!` really was a pgid.
    Under a POSIX `sh` — dash on Debian/Ubuntu, ash on Alpine, the login shell
    of most clusters and every slim container — `set -m` is a no-op, `$!` is a
    pid whose group is the login shell's, and `kill -- -$!` addressed a group
    that did not exist while still exiting 0.

    Subclassing rather than parametrising so every assertion above runs under
    both shells, including the ones about staging, stdin and the deadline.
    """

    SHELL = "sh"


class TestAgainstRealDash(TestAgainstRealBash):
    """Explicitly dash where it is installed, since macOS `sh` is bash."""

    SHELL = "dash"


def test_ssh_submit_propagates_failure(mgr, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Proc(255, b"", b"host key changed"),
        raising=True,
    )
    with pytest.raises(ComputeError) as e:
        mgr.submit({"provider": "ssh:lab", "command": "echo hi"})
    assert "host key changed" in str(e.value)


# --------------------------------------------------------------------------
# ssh: scp + cancel must not invent success
# --------------------------------------------------------------------------


def test_scp_download_failure_raises_instead_of_claiming_a_file(mgr, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(1, b"", b"No such file"), raising=True
    )
    with pytest.raises(ComputeError) as e:
        mgr.scp({"provider": "ssh:lab", "direction": "down", "remote": "/x/y.dat"})
    assert "No such file" in str(e.value)


def test_scp_upload_failure_raises(mgr, monkeypatch, tmp_path):
    (tmp_path / "ws" / "a.dat").write_text("payload")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Proc(1, b"", b"Permission denied"),
        raising=True,
    )
    with pytest.raises(ComputeError) as e:
        mgr.scp(
            {
                "provider": "ssh:lab",
                "direction": "up",
                "local": "a.dat",
                "remote": "/remote/a",
            }
        )
    assert "Permission denied" in str(e.value)


def test_scp_success_returns_path(mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    out = mgr.scp({"provider": "ssh:lab", "direction": "down", "remote": "/x/y.dat"})
    # A resolved path, not the bare name: the caller should not have to guess
    # which directory the file landed in.
    assert out["local"] == str(tmp_path / "ws" / "y.dat")


# --------------------------------------------------------------------------
# the direct scp surface is no looser than the job path beside it
# --------------------------------------------------------------------------


def test_a_download_cannot_escape_the_workspace(mgr, monkeypatch):
    """The agent picks the destination — that is the whole risk. Without this,
    `local="/etc/cron.d/x"` writes wherever the daemon can."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    for escape in ("/etc/passwd", "../../outside.txt"):
        with pytest.raises(ComputeError) as e:
            mgr.scp(
                {
                    "provider": "ssh:lab",
                    "direction": "down",
                    "remote": "/x/y",
                    "local": escape,
                }
            )
        assert "workspace" in str(e.value), escape


def test_a_symlink_cannot_redirect_the_write(mgr, monkeypatch, tmp_path):
    """Resolution happens BEFORE the containment check, so a link planted
    inside the workspace cannot point the write outside it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "ws" / "link").symlink_to(outside)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    with pytest.raises(ComputeError, match="workspace"):
        mgr.scp(
            {
                "provider": "ssh:lab",
                "direction": "down",
                "remote": "/x/y",
                "local": "link/y.dat",
            }
        )


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../b", "x\x00y", "line\nbreak"])
def test_a_hostile_remote_path_is_rejected(mgr, monkeypatch, bad):
    """scp accepts `../../etc/passwd` happily; quoting stops word-splitting,
    not traversal."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    with pytest.raises(ComputeError):
        mgr.scp({"provider": "ssh:lab", "direction": "down", "remote": bad})


def test_an_oversized_upload_is_refused(mgr, monkeypatch, tmp_path):
    """The job path stages through a manifest and harvests through the safe
    extractor; this surface has neither, so it gets an explicit cap."""
    from openai4s.compute import manager as manager_mod

    source = tmp_path / "ws" / "big.bin"
    source.write_bytes(b"x" * 2048)
    monkeypatch.setattr(manager_mod, "MAX_TRANSFER_BYTES", 1024)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    with pytest.raises(ComputeError, match="cap"):
        mgr.scp(
            {
                "provider": "ssh:lab",
                "direction": "up",
                "local": "big.bin",
                "remote": "/remote/big.bin",
            }
        )


def test_an_upload_of_a_missing_file_is_refused(mgr, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0), raising=True)
    with pytest.raises(ComputeError, match="not a file"):
        mgr.scp(
            {
                "provider": "ssh:lab",
                "direction": "up",
                "local": "nope.dat",
                "remote": "/remote/x",
            }
        )


def test_direct_surface_calls_are_audited(mgr, monkeypatch):
    """A compatibility surface that bypasses the job record still has to leave
    one."""
    seen = []
    import openai4s.observability as obs

    monkeypatch.setattr(
        obs, "log_event", lambda event, **f: seen.append((event, f)), raising=True
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Proc(0, b"ok"), raising=True
    )
    mgr.ssh({"provider": "ssh:lab", "command": "hostname"})
    assert any(e == "compute_ssh_command" for e, _ in seen)


def test_cancel_that_never_landed_is_not_a_cancellation(mgr, monkeypatch):
    job = _ssh_job(mgr)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _Proc(255, b"", b"connection refused"),
        raising=True,
    )
    with pytest.raises(ComputeError) as e:
        mgr.cancel({"job_id": job["job_id"]})
    assert e.value.error_kind == "unknown_state"
    assert "may still be running" in str(e.value)
    assert job["status"] != "cancelled"


# --------------------------------------------------------------------------
# byoc: terminal-state interpretation
# --------------------------------------------------------------------------


def _byoc_mgr(mgr, reply):
    mgr._providers["fake"] = {
        "id": "fake",
        "dir": None,
        "provider_py": "/nonexistent/provider.py",
        "meta": {},
    }
    mgr._run_helper = lambda *a, **k: reply
    job = {
        "job_id": "job-b1",
        "provider": "byoc:fake",
        "sandbox_id": "sb-1",
        "status": "running",
        "outputs": None,
    }
    mgr._jobs["job-b1"] = job
    return job


def test_byoc_missing_exit_code_is_unknown_not_done(mgr):
    """`job_exit_code: None` is falsy — it used to select "done" via
    `"failed" if rep.get("job_exit_code") else "done"`."""
    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": None})
    out = mgr._result_byoc(job)
    assert out["status"] == "unknown"
    assert out["exit_code"] is None
    assert out["error_kind"] == "unknown_state"


def test_byoc_unknown_keeps_the_job_in_the_live_count(mgr):
    """Same rule as the ssh path: `unknown` is unresolved, so it is not cached
    onto the job. A job we cannot account for keeps occupying its concurrency
    slot — freeing capacity we have no evidence is free would let the session
    oversubscribe a provider that is still running the work."""
    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": None})
    mgr._limit = 1
    assert mgr._result_byoc(job)["status"] == "unknown"
    assert job["status"] == "running"
    assert mgr._live_count() == 1


def test_byoc_unparseable_phase_surfaces_helper_diagnostic(mgr):
    job = _byoc_mgr(
        mgr,
        {
            "ready": True,
            "job_exit_code": None,
            "phase_read_error": "unrecognized .phase content: 'garbage'",
        },
    )
    out = mgr._result_byoc(job)
    assert out["status"] == "unknown"
    assert "garbage" in out["reason"]


def test_byoc_zero_exit_succeeds(mgr):
    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": 0})
    assert mgr._result_byoc(job)["status"] == "succeeded"


def test_byoc_nonzero_exit_is_failed(mgr):
    """The old expression got this one right by accident, for the wrong
    reason — truthiness, not a comparison to 0."""
    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": 7})
    out = mgr._result_byoc(job)
    assert out["status"] == "failed"
    assert out["exit_code"] == 7


def test_byoc_deadline_sentinel_is_timed_out(mgr):
    """The helper computes these; the manager never read them, so a timeout
    was indistinguishable from an ordinary non-zero exit."""
    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": 143, "deadline_fired": True})
    assert mgr._result_byoc(job)["status"] == "timed_out"


def test_byoc_job_timeout_sentinel_is_timed_out(mgr):
    job = _byoc_mgr(
        mgr, {"ready": True, "job_exit_code": 137, "job_timeout_fired": True}
    )
    assert mgr._result_byoc(job)["status"] == "timed_out"


def test_byoc_harvest_failed_phase_is_not_a_clean_success(mgr):
    """`harvest_failed:0` means the wrapper's tar/mv lost the outputs. rc==0,
    but there is nothing verified to show for it."""
    job = _byoc_mgr(
        mgr,
        {
            "ready": True,
            "job_exit_code": 0,
            "phase_read_error": "tar/mv failed in wrapper (likely disk-full)",
        },
    )
    out = mgr._result_byoc(job)
    assert out["status"] == "failed"
    # The distinction `incomplete` used to carry: rc was 0, so this is not
    # an ordinary non-zero failure — the outputs simply cannot be trusted.
    assert out["exit_code"] == 0
    assert "disk-full" in out["phase_read_error"]


def test_byoc_not_ready_is_running(mgr):
    job = _byoc_mgr(mgr, {"ready": False})
    assert mgr._result_byoc(job)["status"] == "running"


# --------------------------------------------------------------------------
# byoc: helper deadline + confinement posture
# --------------------------------------------------------------------------


def test_helper_wedge_is_killed_and_reported_unknown(mgr, monkeypatch, tmp_path):
    """A bare proc.wait() blocked the dispatcher forever on a wedged helper."""
    killed = {}

    class _Wedged:
        returncode = None

        def __init__(self, *a, **k):
            self.stdin = io.BytesIO()

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="helper", timeout=timeout)

        def kill(self):
            killed["yes"] = True

    monkeypatch.setattr(subprocess, "Popen", _Wedged, raising=True)
    prov = {"provider_py": "/x/provider.py", "meta": {}}
    with pytest.raises(ComputeError) as e:
        mgr._run_helper(prov, "wait", {}, {}, tmp_path, timeout=0.01)
    assert e.value.error_kind == "unknown_state"
    assert "deadline" in str(e.value)
    assert killed.get("yes"), "a helper past its deadline must actually be killed"


def test_confinement_enforce_fails_closed_where_no_boundary_exists(mgr, monkeypatch):
    """`enforce` refuses rather than pretending. Passing expect_confined=1 into
    an unconfined helper would only make it exit 71 while proving nothing."""
    monkeypatch.setattr(
        "openai4s.security.byoc_confinement.available",
        lambda: (False, "no backend on this host"),
    )
    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    mgr._confinement_mode = "enforce"
    mgr._providers["fake"] = {
        "id": "fake",
        "dir": None,
        "provider_py": "/x/p.py",
        "meta": {},
    }
    with pytest.raises(ComputeError) as e:
        mgr.submit({"provider": "byoc:fake", "command": "run"})
    assert e.value.error_kind == "confinement_unavailable"
    assert "no backend on this host" in str(e.value)


def test_confinement_status_matches_what_can_actually_be_applied(mgr, monkeypatch):
    """The posture must track the boundary, in both directions: silence about a
    degradation and a claim about an unbuilt boundary are the same defect."""
    monkeypatch.setattr(
        "openai4s.security.byoc_confinement.available",
        lambda: (False, "no backend on this host"),
    )
    st = mgr.confinement_status()
    assert st["enforced"] is False and st["state"] == "unavailable"
    assert "no backend on this host" in st["detail"]

    monkeypatch.setattr(
        "openai4s.security.byoc_confinement.available",
        lambda: (True, "macOS Seatbelt"),
    )
    st = mgr.confinement_status()
    assert st["enforced"] is True and st["state"] == "active"


def test_confinement_mode_rejects_garbage(mgr, monkeypatch):
    from openai4s.compute.manager import _confinement_mode

    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "sorta")
    with pytest.raises(ComputeError):
        _confinement_mode()


# --------------------------------------------------------------------------
# hostile archives
# --------------------------------------------------------------------------


def _tar_with(tmp_path, build):
    p = tmp_path / "out.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        build(tf)
    return p


def _add_bytes(tf, name, data=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def test_archive_traversal_is_rejected(tmp_path):
    arc = _tar_with(tmp_path, lambda tf: _add_bytes(tf, "../../pwned"))
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        safe_extract_tar(arc, tmp_path / "dest")
    assert not (tmp_path.parent / "pwned").exists()


def test_archive_absolute_path_is_rejected(tmp_path):
    arc = _tar_with(tmp_path, lambda tf: _add_bytes(tf, "/etc/pwned"))
    with pytest.raises(UnsafeArchiveError, match="absolute"):
        safe_extract_tar(arc, tmp_path / "dest")


def test_archive_symlink_is_rejected(tmp_path):
    def build(tf):
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)

    arc = _tar_with(tmp_path, build)
    with pytest.raises(UnsafeArchiveError, match="symlink"):
        safe_extract_tar(arc, tmp_path / "dest")


def test_archive_hardlink_is_rejected(tmp_path):
    def build(tf):
        _add_bytes(tf, "real")
        info = tarfile.TarInfo("hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "real"
        tf.addfile(info)

    arc = _tar_with(tmp_path, build)
    with pytest.raises(UnsafeArchiveError, match="hardlink"):
        safe_extract_tar(arc, tmp_path / "dest")


def test_archive_device_node_is_rejected(tmp_path):
    def build(tf):
        info = tarfile.TarInfo("dev")
        info.type = tarfile.CHRTYPE
        tf.addfile(info)

    arc = _tar_with(tmp_path, build)
    with pytest.raises(UnsafeArchiveError, match="device"):
        safe_extract_tar(arc, tmp_path / "dest")


def test_archive_fifo_is_rejected(tmp_path):
    def build(tf):
        info = tarfile.TarInfo("pipe")
        info.type = tarfile.FIFOTYPE
        tf.addfile(info)

    arc = _tar_with(tmp_path, build)
    with pytest.raises(UnsafeArchiveError, match="FIFO"):
        safe_extract_tar(arc, tmp_path / "dest")


def test_archive_nul_name_guard(tmp_path):
    """Asserted against the guard directly, not through a tar.

    Tar's name field is NUL-terminated, so `tarfile` truncates "ok\\x00evil" to
    "ok" on read and a NUL can never reach _validate_name via a real archive.
    Building this case through `safe_extract_tar` would therefore pass for a
    reason that has nothing to do with the guard.
    """
    from openai4s.compute.safe_archive import _validate_name

    info = tarfile.TarInfo("ok")
    info.name = "ok\x00evil"  # bypass the writer's truncation
    with pytest.raises(UnsafeArchiveError, match="NUL"):
        _validate_name(info, tmp_path / "dest")


def test_archive_member_count_cap(tmp_path):
    def build(tf):
        for i in range(30):
            _add_bytes(tf, f"f{i}")

    arc = _tar_with(tmp_path, build)
    with pytest.raises(UnsafeArchiveError, match="over the 10 cap"):
        safe_extract_tar(arc, tmp_path / "dest", max_files=10)


def test_archive_decompression_bomb_is_rejected(tmp_path):
    """A gzip bomb: tiny compressed, enormous decompressed."""
    arc = _tar_with(tmp_path, lambda tf: _add_bytes(tf, "bomb", b"\0" * 5_000_000))
    with pytest.raises(UnsafeArchiveError, match="ratio|cap"):
        safe_extract_tar(arc, tmp_path / "dest", max_total_bytes=1_000_000)


def test_archive_lying_header_is_caught_during_streaming(tmp_path):
    """The declared size is only a header claim. The real cap is enforced
    against bytes actually written, so an understated header still trips."""
    payload = b"A" * 100_000
    p = tmp_path / "out.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        info = tarfile.TarInfo("liar")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    with pytest.raises(UnsafeArchiveError):
        safe_extract_tar(p, tmp_path / "dest", max_file_bytes=1000, max_ratio=10**9)


def test_archive_rejection_leaves_destination_untouched(tmp_path):
    def build(tf):
        _add_bytes(tf, "good.txt", b"fine")
        _add_bytes(tf, "../escape", b"bad")

    arc = _tar_with(tmp_path, build)
    dest = tmp_path / "dest"
    with pytest.raises(UnsafeArchiveError):
        safe_extract_tar(arc, dest)
    # All-or-nothing: a partial unpack of a hostile archive is still a breach.
    assert not dest.exists() or not any(dest.iterdir())


def test_archive_happy_path_extracts(tmp_path):
    def build(tf):
        _add_bytes(tf, "out/result.json", b'{"ok":true}')
        _add_bytes(tf, "stdout.log", b"hello")

    arc = _tar_with(tmp_path, build)
    dest = tmp_path / "dest"
    files = safe_extract_tar(arc, dest)
    assert (dest / "out" / "result.json").read_bytes() == b'{"ok":true}'
    assert (dest / "stdout.log").read_bytes() == b"hello"
    assert len(files) == 2
    assert not (tmp_path / ".dest.unpack").exists(), "staging dir must be cleaned up"


def test_harvest_of_hostile_archive_is_not_a_success(mgr, tmp_path):
    """End-to-end: a hostile out.tar.gz must degrade the byoc result, never
    silently yield an empty-but-successful harvest."""
    stage = tmp_path / "stage"
    stage.mkdir()
    with tarfile.open(stage / "out.tar.gz", "w:gz") as tf:
        _add_bytes(tf, "../../../etc/pwned", b"owned")

    job = _byoc_mgr(mgr, {"ready": True, "job_exit_code": 0})
    mgr._run_helper = lambda *a, **k: {"ready": True, "job_exit_code": 0}

    real_harvest = mgr._harvest
    mgr._harvest = lambda job_id, _stage: real_harvest(job_id, stage)
    out = mgr._result_byoc(job)
    assert out["status"] == "failed"
    # The distinction `incomplete` used to carry: rc was 0, so this is not
    # an ordinary non-zero failure — the outputs simply cannot be trusted.
    assert out["exit_code"] == 0
    assert out["error_kind"] == "unsafe_archive"
    assert out["output_files"] == []
