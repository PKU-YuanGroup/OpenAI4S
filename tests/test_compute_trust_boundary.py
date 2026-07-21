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
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv[0])
        return _Proc(0, b"1\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    assert out["exit_code"] == 1


def test_ssh_zero_rc_succeeds(mgr, monkeypatch):
    def fake_run(argv, **kw):
        return _Proc(0, b"0\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
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

    def fake_run(argv, **kw):
        if argv[0] == "ssh":
            return _Proc(0, b"0\n")
        return _Proc(1, b"", b"scp: stdout.log: No such file")

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    # The distinction `incomplete` used to carry: rc was 0, so this is not
    # an ordinary non-zero failure — the outputs simply cannot be trusted.
    assert out["exit_code"] == 0
    assert out["exit_code"] == 0
    assert "No such file" in out["harvest_error"]


def test_ssh_failed_job_with_bad_harvest_stays_failed(mgr, monkeypatch):
    def fake_run(argv, **kw):
        return _Proc(0, b"3\n") if argv[0] == "ssh" else _Proc(1, b"", b"scp broke")

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    out = mgr._result_ssh(_ssh_job(mgr))
    assert out["status"] == "failed"
    assert out["exit_code"] == 3


def test_ssh_unknown_is_not_cached_onto_the_job(mgr, monkeypatch):
    """`unknown` is unresolved, not terminal — a later poll must be free to
    resolve it once the host comes back."""
    job = _ssh_job(mgr)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(255), raising=True)
    assert mgr._result_ssh(job)["status"] == "unknown"
    assert job["status"] == "running"

    def fake_run(argv, **kw):
        return _Proc(0, b"0\n") if argv[0] == "ssh" else _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
    assert mgr._result_ssh(job)["status"] == "succeeded"


# --------------------------------------------------------------------------
# ssh: submit must make the exit code observable at all
# --------------------------------------------------------------------------


def test_ssh_submit_records_exit_code_atomically(mgr, monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["remote"] = argv[2]
        return _Proc(0, b"9911\n")

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

    @pytest.fixture
    def local_ssh(self, mgr, monkeypatch, tmp_path):
        real_run = subprocess.run

        def fake_run(argv, **kw):
            if argv[0] == "ssh":
                return real_run(
                    ["bash", "-c", argv[2]],
                    **{k: v for k, v in kw.items() if k != "timeout"},
                )
            if argv[0] == "scp":
                return _Proc(0)
            return real_run(argv, **kw)

        monkeypatch.setattr(subprocess, "run", fake_run, raising=True)
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


def test_confinement_enforce_fails_closed(mgr, monkeypatch):
    """No host-side boundary exists for the byoc helper, so `enforce` refuses
    the op rather than pretending. Passing expect_confined=1 here would only
    make the helper exit 71."""
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


def test_confinement_status_never_claims_an_unverified_boundary(mgr):
    st = mgr.confinement_status()
    assert st["enforced"] is False
    assert st["state"] == "unavailable"


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
