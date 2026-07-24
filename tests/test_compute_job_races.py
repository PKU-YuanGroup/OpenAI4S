"""What a compute job says happened must be what happened.

Three defects, all reported by review against the *fixed* state machine:

  * the repository's compare-and-swap works, but ``ComputeManager`` wrote the
    terminal status into memory first and then handed the write to
    ``_persist``, which swallows exceptions. A cancel that lost the race to a
    result therefore left SQLite at ``succeeded`` while memory *and the caller*
    were told ``cancelled`` — the ledger and the answer disagreed, which is
    worse than either being wrong alone;
  * ``close()`` had the same shape, so a handle release could report a job it
    never actually ended;
  * ``residency: remote`` was honoured only by the *reconciler*. The harvest
    still tarred the whole work directory, downloaded it, and listed the file
    in ``output_files`` — the one thing the declaration exists to forbid.

The harvest tests run the real generated shell script against a real
directory, because the exclusion being tested lives in that script's ``find``
expression and nothing else can prove it. The end-to-end residency case lives
in ``test_compute_trust_boundary.py``, where it runs under three real shells.
"""
import subprocess
import tempfile
import threading
import types
from pathlib import Path

import pytest

from openai4s.compute import states
from openai4s.compute.manager import ComputeManager, _ssh_harvest_script
from openai4s.config import Config
from openai4s.store import get_store


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
    )


def _running_ssh_job(cfg, job_id="job-race"):
    """A live ssh job present in both the ledger and the manager's memory."""
    store = get_store(cfg.db_path)
    store.create_compute_job(job_id=job_id, provider="ssh:lab", status=states.RUNNING)
    manager = ComputeManager(cfg)
    manager._jobs[job_id] = {
        "job_id": job_id,
        "provider": "ssh:lab",
        "alias": "lab",
        "workdir": "/tmp/work",
        "status": states.RUNNING,
        "pid": "4242",
        "pgid": "4242",
    }
    return manager, store


# --------------------------------------------------------------------------
# terminal state: the ledger decides, and the caller is told what it decided
# --------------------------------------------------------------------------


def test_a_cancel_that_lost_the_terminal_race_reports_the_real_state(cfg, monkeypatch):
    """The minimal reproduction from review.

    The result thread got there first and wrote ``succeeded``. The cancel that
    follows cannot legally move a terminal row, so it must not claim it did.
    """
    manager, store = _running_ssh_job(cfg)
    monkeypatch.setattr(manager, "_terminate_ssh_job", lambda job: None)

    store.update_compute_job("job-race", status=states.SUCCEEDED)

    out = manager.cancel({"job_id": "job-race"})

    assert store.get_compute_job("job-race")["status"] == states.SUCCEEDED
    assert out["status"] == states.SUCCEEDED, (
        "the caller must be told the state the ledger actually holds, not the "
        "one the cancel wanted"
    )
    assert out.get("conflict") == {
        "requested": states.CANCELLED,
        "actual": states.SUCCEEDED,
    }
    assert (
        manager._jobs["job-race"]["status"] == states.SUCCEEDED
    ), "memory must follow the ledger, never the wish"


def test_a_close_that_lost_the_terminal_race_does_not_claim_a_release(cfg, monkeypatch):
    manager, store = _running_ssh_job(cfg, "job-close")
    monkeypatch.setattr(manager, "_terminate_ssh_job", lambda job: None)
    store.update_compute_job("job-close", status=states.SUCCEEDED)

    out = manager.close({"provider": "ssh:lab", "job_ids": ["job-close"]})

    assert "job-close" not in out.get(
        "released", []
    ), "a job that had already finished was not released by this close"
    assert store.get_compute_job("job-close")["status"] == states.SUCCEEDED
    assert manager._jobs["job-close"]["status"] == states.SUCCEEDED


def test_a_racing_result_and_cancel_agree_across_memory_ledger_and_caller(cfg):
    """The barrier race itself, under real threads.

    Whichever write lands, the three views must not diverge: the row, the
    in-memory job, and the value handed back to the caller.
    """
    manager, store = _running_ssh_job(cfg, "job-both")
    manager._terminate_ssh_job = lambda job: None  # confirmed remote stop

    start = threading.Barrier(2)
    answers: dict[str, object] = {}
    errors: list[BaseException] = []

    def do_cancel():
        try:
            start.wait(timeout=10)
            answers["cancel"] = manager.cancel({"job_id": "job-both"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def do_result():
        # Straight at the repository, as the poll path does: the compare-and-
        # swap there was never the defect. What the manager did *around* it was.
        try:
            start.wait(timeout=10)
            store.update_compute_job("job-both", status=states.SUCCEEDED, exit_code=0)
        except states.IllegalTransition:
            pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=do_cancel), threading.Thread(target=do_result)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    row = store.get_compute_job("job-both")["status"]
    assert row in (states.SUCCEEDED, states.CANCELLED)
    assert (
        manager._jobs["job-both"]["status"] == row
    ), f"memory says {manager._jobs['job-both']['status']}, ledger says {row}"
    assert (
        answers["cancel"]["status"] == row
    ), f"cancel answered {answers['cancel']['status']}, ledger says {row}"


def test_a_terminal_write_that_cannot_be_persisted_is_not_reported_as_done(
    cfg, monkeypatch
):
    """A ledger write that fails for a real reason must not be swallowed."""
    manager, _store = _running_ssh_job(cfg, "job-disk")
    monkeypatch.setattr(manager, "_terminate_ssh_job", lambda job: None)

    def explode(*_a, **_k):
        raise RuntimeError("disk is on fire")

    monkeypatch.setattr(manager._store, "update_compute_job", explode)

    with pytest.raises(Exception) as error:
        manager.cancel({"job_id": "job-disk"})
    assert "disk is on fire" in str(error.value)
    assert (
        manager._jobs["job-disk"]["status"] == states.RUNNING
    ), "memory must not advance past a terminal state the ledger refused"


# --------------------------------------------------------------------------
# residency: remote — the file must not move
# --------------------------------------------------------------------------


def _run_script(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", "-c", script], cwd=str(cwd), capture_output=True, timeout=60
    )


def test_the_real_harvest_script_excludes_a_remote_residency_output(tmp_path):
    """Run the generated script. The archive is the evidence."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "small.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (work / "checkpoints").mkdir()
    (work / "checkpoints" / "step-1.ckpt").write_text("weights", encoding="utf-8")

    script = _ssh_harvest_script(
        str(work), 10_000_000, exclude_patterns=["checkpoints/*.ckpt"]
    )
    proc = _run_script(script, work)
    assert proc.returncode == 0, proc.stderr.decode()
    assert "OPENAI4S_HARVEST_STAYED checkpoints/step-1.ckpt" in proc.stdout.decode()

    listing = subprocess.run(
        ["tar", "-tzf", ".openai4s-harvest.tar.gz"],
        cwd=str(work),
        capture_output=True,
        timeout=60,
    )
    names = listing.stdout.decode().split()
    assert any(name.endswith("small.csv") for name in names)
    assert not any(
        "step-1.ckpt" in name for name in names
    ), f"a residency:remote output was archived anyway: {names}"


def test_a_newline_in_a_filename_cannot_inject_a_second_tar_entry(tmp_path):
    """`find -print` + `tar -T` is newline-delimited, so a filename containing a
    newline split into a second `-T` entry — a path the job never produced, and
    potentially one outside the work directory. `-print0` + `tar --null` reads
    names verbatim between NULs, so the file is one member and nothing else is."""
    import tarfile

    work = tmp_path / "work"
    work.mkdir()
    weird = "data\nnote.txt"  # one file whose name embeds a newline
    (work / weird).write_text("payload", encoding="utf-8")
    (work / "decoy.txt").write_text("x", encoding="utf-8")

    proc = _run_script(_ssh_harvest_script(str(work), 10_000_000), work)
    assert proc.returncode == 0, proc.stderr.decode()

    with tarfile.open(work / ".openai4s-harvest.tar.gz") as archive:
        names = archive.getnames()
    normalized = {name.lstrip("./") for name in names}
    assert weird in normalized, f"the newline-named file was not archived: {names}"
    assert normalized == {
        weird,
        "decoy.txt",
    }, f"the newline split into a phantom entry: {names}"


def test_a_nested_file_sharing_a_wrapper_basename_is_still_harvested(tmp_path):
    """The control-file exclusions matched by `-name`, so a declared output like
    `results/run.sh` was dropped at any depth — reconcile then reported it
    missing and turned an exit-0 job into `failed`. Only the root wrapper files
    may be excluded."""
    import tarfile

    work = tmp_path / "work"
    work.mkdir()
    (work / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")  # the wrapper
    (work / "results").mkdir()
    (work / "results" / "run.sh").write_text("real output", encoding="utf-8")
    (work / "checkpoint").mkdir()
    (work / "checkpoint" / ".timeout").write_text("real output", encoding="utf-8")

    proc = _run_script(_ssh_harvest_script(str(work), 10_000_000), work)
    assert proc.returncode == 0, proc.stderr.decode()

    with tarfile.open(work / ".openai4s-harvest.tar.gz") as archive:
        names = {n.lstrip("./") for n in archive.getnames()}
    assert "results/run.sh" in names, f"a nested declared output was dropped: {names}"
    assert "checkpoint/.timeout" in names, f"a nested output was dropped: {names}"
    assert "run.sh" not in names, "the root wrapper file must still be excluded"


def test_the_harvest_ack_reports_the_archive_size(tmp_path):
    """The host refuses an oversized download before scp writes a byte, which it
    can only do if the remote reports the compressed size with the ack."""
    from openai4s.compute.manager import _parse_harvest_ack

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a" * 1000, encoding="utf-8")

    proc = _run_script(_ssh_harvest_script(str(work), 10_000_000), work)
    assert proc.returncode == 0, proc.stderr.decode()
    marker, _oversized, _stayed, archive_bytes = _parse_harvest_ack(
        proc.stdout.decode()
    )
    assert marker == "archive"
    assert archive_bytes is not None and archive_bytes > 0
    assert archive_bytes == (work / ".openai4s-harvest.tar.gz").stat().st_size


def test_the_harvest_script_still_archives_everything_when_nothing_stays(tmp_path):
    """The exclusion must be inert when no residency was declared."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a", encoding="utf-8")
    (work / "nested").mkdir()
    (work / "nested" / "b.txt").write_text("b", encoding="utf-8")

    proc = _run_script(_ssh_harvest_script(str(work), 10_000_000), work)
    assert proc.returncode == 0, proc.stderr.decode()
    listing = subprocess.run(
        ["tar", "-tzf", ".openai4s-harvest.tar.gz"],
        cwd=str(work),
        capture_output=True,
        timeout=60,
    )
    names = listing.stdout.decode()
    assert "a.txt" in names and "b.txt" in names


def test_the_local_gate_removes_a_stay_remote_file_the_remote_let_through():
    """``find``'s glob semantics are not fnmatch's on every host.

    The remote exclusion is the one that stops the transfer; this is the gate
    on the side we control, so a file that slipped through cannot survive on
    local disk to be listed as an output.
    """
    from openai4s.compute.manager import _prune_local_matches

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td)
        (dest / "keep.csv").write_text("keep", encoding="utf-8")
        (dest / "ckpt").mkdir()
        (dest / "ckpt" / "model.pt").write_text("weights", encoding="utf-8")

        removed = _prune_local_matches(dest, ["ckpt/*.pt"])

        assert removed == ["ckpt/model.pt"]
        assert not (dest / "ckpt" / "model.pt").exists()
        assert (dest / "keep.csv").exists()
