"""What the daemon allocates for a remote command it does not control.

Every ssh and scp the compute manager runs went through
``subprocess.run(capture_output=True)``, which accumulates the whole of what
the other end says in the daemon's heap and only stops at the per-command
timeout. That is 30 seconds for the status probe, 300 for the harvest staging
and the transfer, and whatever the caller asked for on ``host.compute.ssh()`` —
a window measured in minutes for a remote to write as fast as the link allows.
``ssh()`` even sliced its result to 64 KiB *after* the fact, which bounded what
the caller saw and not one byte of what the daemon had already allocated.

A forced command or a chatty login profile is enough; it does not have to be
malicious. So the ceiling is applied while reading, by `_run_capped`, and these
tests drive it with **real** processes rather than stubs — a cap asserted
against a `BytesIO` proves the arithmetic and nothing about pipes, threads or
deadlocks, which is where this kind of code actually fails.
"""
from __future__ import annotations

import subprocess
import time
import types

import pytest

import openai4s.compute.manager as mod
from openai4s.compute.manager import ComputeManager


@pytest.fixture
def mgr(tmp_path):
    cfg = types.SimpleNamespace(
        data_dir=tmp_path / "data", skills_dir=tmp_path / "skills"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "ws").mkdir()
    return ComputeManager(cfg, workspace=tmp_path / "ws")


def _sh(script: str) -> list[str]:
    return ["/bin/sh", "-c", script]


# --------------------------------------------------------------------------
# the ceiling itself
# --------------------------------------------------------------------------


def test_a_flood_of_stdout_is_read_but_not_kept():
    """The headline property: unbounded output, bounded memory."""
    flood = 4 * 1024 * 1024
    completed = mod._run_capped(
        _sh(f"yes ABCDEFGH | head -c {flood}"),
        timeout=60,
        stdout_cap=4096,
    )
    assert completed.returncode == 0
    assert len(completed.stdout) == 4096, "the cap is the ceiling on what is held"
    assert completed.stdout_truncated is True
    # Head, not tail: stdout is what gets parsed, and the answer comes first.
    assert completed.stdout.startswith(b"ABCDEFGH")


def test_a_flood_of_stderr_keeps_only_the_tail_and_says_so():
    """stderr is only ever shown to a human, and what explains a failure is
    its last words — so the tail is kept, and the elision is stated rather
    than leaving a truncated message reading as the whole of it."""
    completed = mod._run_capped(
        _sh("yes ERR | head -c 2000000 >&2; echo done"),
        timeout=60,
        stderr_cap=4096,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == b"done", "stdout is unaffected by stderr's cap"
    assert completed.stderr_truncated is True
    assert b"discarded" in completed.stderr, "a silent truncation reads as the whole"
    # The annotation rides on top of the tail budget, not inside it.
    assert len(completed.stderr) < 4096 + 200


def test_both_streams_flooding_at_once_does_not_deadlock():
    """The reason draining and *keeping* have to be separate.

    A single-threaded reader that drains stdout to completion first blocks
    forever once the child fills the stderr pipe — 64 KiB on Linux, less on
    macOS — and the child blocks writing to it. This is the case the caps
    must survive, not merely the arithmetic.
    """
    started = time.monotonic()
    completed = mod._run_capped(
        _sh("yes OUT | head -c 3000000 & yes ERR | head -c 3000000 >&2; wait"),
        timeout=60,
        stdout_cap=8192,
        stderr_cap=8192,
    )
    elapsed = time.monotonic() - started
    assert completed.returncode == 0
    assert len(completed.stdout) == 8192 and len(completed.stdout) > 0
    assert elapsed < 30, f"both pipes flooding took {elapsed:.1f}s — a stall, not work"


def test_output_that_fits_is_returned_whole():
    """The cap must not become a truncation of the ordinary case: every caller
    here parses a short protocol line out of stdout."""
    completed = mod._run_capped(_sh("echo RC 0 -; echo oops >&2"), timeout=30)
    assert completed.returncode == 0
    assert completed.stdout == b"RC 0 -\n"
    assert completed.stderr == b"oops\n"
    assert completed.stdout_truncated is False
    assert completed.stderr_truncated is False


def test_a_nonzero_exit_is_reported_with_its_stderr():
    completed = mod._run_capped(_sh("echo nope >&2; exit 3"), timeout=30)
    assert completed.returncode == 3
    assert completed.stderr.strip() == b"nope"


def test_stdin_still_reaches_the_command():
    """The submit path writes `run.sh` to the remote's stdin. Its own thread,
    because a script larger than the pipe buffer would otherwise block while
    the child is blocked writing output nobody is reading yet."""
    payload = b"x" * (1024 * 1024)
    completed = mod._run_capped(
        _sh("wc -c"), timeout=60, input=payload, stdout_cap=1024
    )
    assert completed.returncode == 0
    assert int(completed.stdout.split()[0]) == len(payload)


def test_a_hung_command_raises_timeoutexpired_rather_than_hanging():
    """The contract is deliberately `subprocess.run`'s, so every existing
    `except (subprocess.TimeoutExpired, OSError)` handler still applies."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        mod._run_capped(_sh("sleep 30"), timeout=0.5)
    assert time.monotonic() - started < 10, "the deadline did not actually fire"


def test_a_command_that_cannot_start_raises_oserror():
    with pytest.raises(OSError):
        mod._run_capped(["/nonexistent/openai4s-not-a-binary"], timeout=5)


# --------------------------------------------------------------------------
# ...and what the manager does with it
# --------------------------------------------------------------------------


def test_the_ssh_op_says_when_it_truncated_the_output(mgr, monkeypatch, tmp_path):
    """`host.compute.ssh()` sliced its result to 64 KiB after the fact, which
    said nothing about the gigabytes the daemon had allocated to produce it —
    and said nothing to the *caller* either, so a truncated result read as a
    command that produced exactly that much."""
    monkeypatch.setattr(mod, "_REMOTE_STDOUT_CAP", 4096, raising=True)

    real_popen = subprocess.Popen

    def fake_popen(argv, **kw):
        if argv[0] == "ssh":
            return real_popen(_sh("yes FLOOD | head -c 500000"), **kw)
        return real_popen(argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen, raising=True)
    monkeypatch.setattr(mgr, "_has_ssh_skill", lambda: True)

    out = mgr.ssh({"provider": "ssh:lab", "command": "cat /dev/urandom"})
    assert out["exit_code"] == 0
    assert len(out["stdout"]) <= 4096
    assert out["stdout_truncated"] is True
    assert "harvest" in out["hint"]


def test_the_ssh_op_also_flags_the_reply_slice_it_always_had(mgr, monkeypatch):
    """The 64 KiB slice predates the read ceiling and was always silent, so a
    reply between the two limits came back cut with `stdout_truncated` absent —
    which reads as "this is all of it". Both cuts have to count."""
    monkeypatch.setattr(mod, "_CALL_COMMAND_CAP", 100, raising=True)
    real_popen = subprocess.Popen

    def fake_popen(argv, **kw):
        if argv[0] == "ssh":
            return real_popen(_sh("yes SMALL | head -c 5000"), **kw)
        return real_popen(argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen, raising=True)
    monkeypatch.setattr(mgr, "_has_ssh_skill", lambda: True)

    out = mgr.ssh({"provider": "ssh:lab", "command": "echo hi"})
    # Well under the read ceiling, so nothing was dropped while reading...
    assert len(out["stdout"]) == 100
    # ...but the reply is still not the whole of it, and now it says so.
    assert out["stdout_truncated"] is True


def test_the_ssh_op_stays_quiet_when_nothing_was_cut(mgr, monkeypatch):
    """The flag must mean something: an ordinary short reply carries neither
    the truncation marker nor the hint."""
    real_popen = subprocess.Popen

    def fake_popen(argv, **kw):
        if argv[0] == "ssh":
            return real_popen(_sh("echo hello"), **kw)
        return real_popen(argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen, raising=True)
    monkeypatch.setattr(mgr, "_has_ssh_skill", lambda: True)

    out = mgr.ssh({"provider": "ssh:lab", "command": "echo hello"})
    assert out["stdout"] == "hello\n"
    assert "stdout_truncated" not in out
    assert "hint" not in out


def test_a_truncated_harvest_ack_is_not_reported_as_a_vanished_workdir(
    mgr, monkeypatch, tmp_path
):
    """Two different facts. The staging script exits 0 and prints a tagged
    acknowledgement; if the remote also prints megabytes of banner ahead of it
    the ack falls outside what we read, and reporting "the work directory may
    be gone" sends someone to look at a directory that is fine."""
    monkeypatch.setattr(mod, "_REMOTE_STDOUT_CAP", 4096, raising=True)

    real_popen = subprocess.Popen

    def fake_popen(argv, **kw):
        # A login profile that never shuts up, then the real acknowledgement.
        return real_popen(
            _sh("yes BANNER | head -c 200000; echo 'OPENAI4S_HARVEST empty'"), **kw
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen, raising=True)

    error, _oversized, _stayed, transient = mgr._harvest_ssh(
        "lab", "/remote/work", tmp_path / "dest"
    )
    assert error is not None
    assert "acknowledgement was not found within the first" in error
    assert "work directory may be gone" not in error
    assert transient is False, "the remote answered; repeating it says the same"
