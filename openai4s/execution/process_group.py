"""Stop a spawned process *group*, and confirm that it stopped.

Extracted from ``openai4s/jobs.py`` because the kernel-side ``host.bash``
executor needs the identical ladder and had none: it passed ``timeout=`` to
``subprocess.run`` with ``shell=True``, which kills the shell and leaves
whatever the shell started running. Two implementations of "stop this job"
would have disagreed about the case that matters -- the one where the leader
exits and the work does not.

Pure stdlib (``os``/``signal``/``subprocess``/``time``) so the worker process
can import it without pulling in the daemon's dependency graph.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

#: How long a process group may take to honour SIGTERM before it is killed.
TERM_GRACE_S = 5.0


def group_alive(pgid: int | None) -> bool:
    """Is anything at all still in the process group?

    ``killpg(pgid, 0)`` raises ESRCH only when the group holds no process, so
    this answers for the whole tree rather than for the one pid we happen to
    hold a handle to. A group we are not permitted to signal counts as alive:
    the honest reading of "I cannot tell" is not "it is gone".
    """
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def signal_group(proc: subprocess.Popen, pgid: int | None, sig: int) -> None:
    """Deliver one signal to the whole group, or to the leader if there is none.

    A named indirection rather than an inline call so a test can simulate a
    signal that is accepted and goes nowhere -- the "no permission to signal"
    case -- without reaching into the ``os`` module the rest of the process
    shares.
    """
    if pgid is not None:
        os.killpg(pgid, sig)
    else:  # no process group (Windows, or the child already reaped)
        proc.send_signal(sig)


def await_group_exit(proc: subprocess.Popen, pgid: int | None, timeout: float) -> bool:
    """Wait for the *group* to empty, not for the leader to be reaped.

    ``proc.wait()`` answers about one process. A shell that honours SIGTERM
    while the work it started ignores it satisfies ``wait()`` immediately and
    leaves the job running. The wait here is also what reaps the leader, so a
    zombie does not keep the group looking populated.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=0)
        except subprocess.TimeoutExpired:
            pass
        except Exception:  # noqa: BLE001 - another waiter got there first
            pass
        if pgid is None:
            if proc.poll() is not None:
                return True
        elif not group_alive(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def stop_process_group(
    proc: subprocess.Popen, pgid: int | None = None
) -> tuple[bool, str]:
    """TERM the process group, escalate to KILL, then confirm.

    Returns ``(stopped, detail)``. The confirmation is the point: a stop that
    reports success without checking is indistinguishable from one that
    worked, and the work carries on holding whatever it holds.

    The group, not the process -- the job is a shell, and the work is its
    child. Signalling only the shell leaves the work running, and so does
    *believing* the shell: both ``proc.poll()`` and ``proc.wait()`` answer
    about the leader alone.

    ``pgid`` is passed in because it must be read at spawn time. Looking it up
    here fails once the leader has been reaped, which is precisely the case
    where the surviving group most needs signalling.

    Stated limit: once the leader has been reaped, a pgid is a number the OS
    may eventually reuse, so a group probe cannot be perfectly certain it is
    asking about the same job. Nothing short of pidfd closes that window. The
    window is the interval between reaping the leader and this call, which is
    short, and erring toward signalling a group that may be gone is safer than
    reporting a stop that did not happen.
    """
    if pgid is None:
        try:
            pgid = os.getpgid(proc.pid)
        except (OSError, AttributeError):
            pgid = None

    if proc.poll() is not None and not group_alive(pgid):
        # Leader gone *and* group empty. Checking the group as well is what
        # stops `work & exit 0` from being reported as an exited job: the
        # shell finished immediately, and the work it left behind held the
        # job's stdout pipe open while nothing ever signalled it.
        return True, "already exited"

    def _signal(sig: int) -> None:
        signal_group(proc, pgid, sig)

    try:
        _signal(signal.SIGTERM)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"could not signal the job ({e})"

    if await_group_exit(proc, pgid, TERM_GRACE_S):
        return True, "exited on SIGTERM"

    try:
        _signal(signal.SIGKILL)
    except ProcessLookupError:
        return True, "already exited"
    except OSError as e:
        return False, f"ignored SIGTERM and could not be killed ({e})"

    if await_group_exit(proc, pgid, TERM_GRACE_S):
        return True, "killed"
    # Unkillable means uninterruptible sleep, almost always blocked I/O.
    # Saying so is far more useful than reporting a stop that did not happen.
    return False, "did not die after SIGKILL (likely blocked in the kernel)"


__all__ = [
    "TERM_GRACE_S",
    "await_group_exit",
    "group_alive",
    "signal_group",
    "stop_process_group",
]
