"""Real Linux/bubblewrap Python and R persistent-kernel interrupt smoke.

This is intentionally outside default pytest collection.  It needs Linux,
working unprivileged bubblewrap namespaces, pidfds, and a real R installation.
Unlike ``linux_sandbox.py`` it does not claim to prove network denial: GitHub's
hosted runner cannot configure loopback in bwrap's new network namespace, so
the dedicated CI job explicitly allows raw worker networking.  The filesystem
and process boundaries under test remain the production team path.

Each language starts with ``KernelReadIsolation``, which forces bubblewrap,
``--unshare-pid``, the inherited ``--info-fd`` namespace-init report, procfs
parent/child validation, and persistent command pidfd adoption.  The worker
must appear as PID 2, a long Cell must stop on SIGINT, and the same kernel and
namespace must answer a subsequent Cell.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import tempfile
import threading
from pathlib import Path
from typing import Any

from openai4s.kernel import Kernel
from openai4s.kernel.r_kernel import resolve_r_interpreter, spawn_r_kernel
from openai4s.security.sandbox import KernelReadIsolation

_START_TIMEOUT_S = 30.0
_INTERRUPT_TIMEOUT_S = 15.0


def _read_proc_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition(":")
        if separator:
            fields[name] = value.strip()
    return fields


def _require_worker_pidfd_identity(
    worker_pidfd: int,
    *,
    launcher_pid: int,
) -> dict[str, int]:
    """Prove the retained pidfd names command PID 2, not bwrap's PID 1."""

    info = _read_proc_fields(Path(f"/proc/self/fdinfo/{worker_pidfd}"))
    try:
        worker_pid = int(info["Pid"])
        worker_ns_pids = [int(value) for value in info["NSpid"].split()]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"pidfd exposes no usable process identity: {info}") from exc
    worker_status = _read_proc_fields(Path(f"/proc/{worker_pid}/status"))
    try:
        init_pid = int(worker_status["PPid"])
        status_ns_pids = [int(value) for value in worker_status["NSpid"].split()]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"pidfd target exposes no usable procfs identity: {worker_status}"
        ) from exc
    init_status = _read_proc_fields(Path(f"/proc/{init_pid}/status"))
    try:
        init_parent = int(init_status["PPid"])
        init_ns_pids = [int(value) for value in init_status["NSpid"].split()]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"bubblewrap init exposes no usable procfs identity: {init_status}"
        ) from exc
    if (
        worker_pid <= 0
        or worker_ns_pids != status_ns_pids
        or worker_ns_pids[-1:] != [2]
        or init_pid <= 0
        or init_ns_pids[-1:] != [1]
        or init_parent != launcher_pid
    ):
        raise RuntimeError(
            "pidfd did not pin command PID 2 below bubblewrap's PID 1 init: "
            f"launcher={launcher_pid}, init={init_pid}/{init_ns_pids}, "
            f"worker={worker_pid}/{worker_ns_pids}, status={status_ns_pids}"
        )
    signal.pidfd_send_signal(worker_pidfd, 0, None, 0)
    return {"host_init_pid": init_pid, "host_worker_pid": worker_pid}


def _require_team_bwrap_identity(
    kernel: Kernel, protected_root: Path
) -> dict[str, Any]:
    """Assert this is the private-PID team path, not a permissive substitute."""

    status = kernel.sandbox_status
    if (
        status.get("backend") != "bubblewrap"
        or status.get("enforced") is not True
        or status.get("self_test_passed") is not True
    ):
        raise RuntimeError(f"team bubblewrap was not enforced: {status}")
    if status.get("network_policy") != "raw_allowed":
        raise RuntimeError(
            "this hosted-runner smoke must name its deliberate raw-network "
            f"exception, got: {status}"
        )

    sandbox = kernel._sandbox
    isolation = sandbox._read_isolation
    roots = {Path(root).resolve() for root in isolation.roots} if isolation else set()
    if protected_root.resolve() not in roots:
        raise RuntimeError("kernel did not retain the requested team read boundary")
    launcher = kernel._proc
    launcher_pid = int(getattr(launcher, "pid", 0) or 0)
    launcher_argv = [str(part) for part in getattr(launcher, "args", ())]
    if "--unshare-pid" not in launcher_argv or "--info-fd" not in launcher_argv:
        raise RuntimeError(
            "team worker did not launch through private PID + info-fd: "
            f"{launcher_argv!r}"
        )
    if "--as-pid-1" in launcher_argv:
        raise RuntimeError("team worker bypassed bubblewrap's required init/reaper")
    if "--unshare-net" in launcher_argv:
        raise RuntimeError("hosted-runner raw-network exception was not applied")
    if launcher_pid <= 0 or sandbox._bwrap_launcher_pid != launcher_pid:
        raise RuntimeError("bubblewrap namespace init was not bound to its launcher")
    worker_pidfd = sandbox._bwrap_worker_pidfd
    if worker_pidfd is None:
        raise RuntimeError("bubblewrap command was not adopted through a pidfd")
    os.fstat(worker_pidfd)
    pid_identity = _require_worker_pidfd_identity(
        worker_pidfd,
        launcher_pid=launcher_pid,
    )
    if (
        sandbox._bwrap_info_read_fd is not None
        or sandbox._bwrap_info_write_fd is not None
    ):
        raise RuntimeError("bubblewrap info-fd channel remained open after adoption")
    return {**status, **pid_identity}


def _interrupt_and_continue(
    *,
    kernel: Kernel,
    label: str,
    long_cell: str,
    continuation: str,
    continuation_expected: str,
) -> dict[str, Any]:
    """Interrupt one live Cell and prove the same worker answers afterwards."""

    started = threading.Event()
    outcome: dict[str, Any] = {}

    def on_chunk(text: str) -> None:
        if f"{label}-started" in text:
            started.set()

    def execute() -> None:
        try:
            outcome["result"] = kernel.execute(long_cell, on_chunk=on_chunk)
        except BaseException as exc:  # surfaced in the main smoke thread
            outcome["failure"] = exc

    launcher_pid = int(kernel._proc.pid)
    generation = kernel.generation
    thread = threading.Thread(
        target=execute,
        name=f"{label}-bwrap-interrupt-smoke",
        daemon=True,
    )
    thread.start()
    if not started.wait(_START_TIMEOUT_S):
        kernel.kill_worker()
        thread.join(_INTERRUPT_TIMEOUT_S)
        suffix = (
            " and its worker thread could not be reaped" if thread.is_alive() else ""
        )
        raise RuntimeError(f"{label} Cell never proved it was executing{suffix}")
    try:
        kernel.interrupt()
    except BaseException:
        kernel.kill_worker()
        thread.join(_INTERRUPT_TIMEOUT_S)
        raise
    thread.join(_INTERRUPT_TIMEOUT_S)
    if thread.is_alive():
        kernel.kill_worker()
        thread.join(_INTERRUPT_TIMEOUT_S)
        suffix = " or after its worker was killed" if thread.is_alive() else ""
        raise RuntimeError(f"{label} Cell did not stop after SIGINT{suffix}")
    if "failure" in outcome:
        raise RuntimeError(f"{label} Cell execution raised") from outcome["failure"]

    result = outcome.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{label} Cell returned no structured result: {result!r}")
    if result.get("interrupted") is not True or result.get("error") != "Interrupted":
        raise RuntimeError(f"{label} SIGINT result was not Interrupted: {result!r}")
    if (
        not kernel.is_alive()
        or int(kernel._proc.pid) != launcher_pid
        or kernel.generation != generation
    ):
        raise RuntimeError(f"{label} kernel was replaced or died after SIGINT")

    followup = kernel.execute(continuation)
    if followup.get("error") is not None:
        raise RuntimeError(f"{label} continuation failed: {followup!r}")
    if str(followup.get("stdout") or "").strip() != continuation_expected:
        raise RuntimeError(f"{label} namespace did not persist: {followup!r}")
    return {
        "interrupted": True,
        "launcher_pid": launcher_pid,
        "continued": continuation_expected,
    }


def _python_smoke(workspace: Path, isolation: KernelReadIsolation) -> dict[str, Any]:
    with Kernel(cwd=str(workspace), read_isolation=isolation) as kernel:
        status = _require_team_bwrap_identity(kernel, Path(isolation.roots[0]))
        setup = kernel.execute(
            "import os\n"
            "python_marker = 'python-still-here'\n"
            "print(os.getpid(), os.getppid())"
        )
        if (
            setup.get("error") is not None
            or str(setup.get("stdout") or "").strip() != "2 1"
        ):
            raise RuntimeError(
                "Python worker is not PID 2 below bubblewrap's PID 1 init: "
                f"{setup!r}"
            )
        result = _interrupt_and_continue(
            kernel=kernel,
            label="python",
            long_cell=(
                "import time\n" "print('python-started', flush=True)\n" "time.sleep(60)"
            ),
            continuation="print(python_marker)",
            continuation_expected="python-still-here",
        )
        result["sandbox"] = status
        result["namespace_pid"] = 2
        return result


def _r_smoke(
    workspace: Path,
    isolation: KernelReadIsolation,
    rscript: str,
) -> dict[str, Any]:
    with spawn_r_kernel(
        cwd=str(workspace),
        rscript=rscript,
        read_isolation=isolation,
    ) as kernel:
        status = _require_team_bwrap_identity(kernel, Path(isolation.roots[0]))
        setup = kernel.execute(
            'r_marker <- "r-still-here"\n'
            'ppid_line <- grep("^PPid:", readLines("/proc/self/status"), '
            "value = TRUE)\n"
            'if (length(ppid_line) != 1L) stop("missing unique PPid")\n'
            'ppid <- sub("^[^:]+:[[:space:]]*", "", ppid_line)\n'
            'cat(Sys.getpid(), ppid, "\\n")'
        )
        if (
            setup.get("error") is not None
            or str(setup.get("stdout") or "").strip() != "2 1"
        ):
            raise RuntimeError(
                "R worker is not PID 2 below bubblewrap's PID 1 init: " f"{setup!r}"
            )
        result = _interrupt_and_continue(
            kernel=kernel,
            label="r",
            long_cell='cat("r-started\\n"); flush.console(); Sys.sleep(60)',
            continuation="cat(r_marker)",
            continuation_expected="r-still-here",
        )
        result["sandbox"] = status
        result["namespace_pid"] = 2
        return result


def main() -> int:
    if platform.system() != "Linux":
        raise RuntimeError("Linux bubblewrap interrupt smoke must run on Linux")
    if shutil.which("bwrap") is None:
        raise RuntimeError("Linux bubblewrap interrupt smoke requires bwrap")
    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(signal, "pidfd_send_signal", None)
    ):
        raise RuntimeError("Linux bubblewrap interrupt smoke requires pidfd support")
    rscript = resolve_r_interpreter()
    if not rscript:
        raise RuntimeError("Linux bubblewrap interrupt smoke requires Rscript")

    # Team mode never degrades, but `enforce` makes the intended posture visible
    # in the standalone invocation too. Raw networking is the narrow hosted-
    # runner compatibility exception; this smoke does not claim to test egress.
    os.environ["OPENAI4S_KERNEL_SANDBOX"] = "enforce"
    os.environ["OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"] = "1"

    home = Path.home().resolve()
    root = Path(tempfile.mkdtemp(prefix="openai4s-bwrap-interrupt-", dir=home))
    python_workspace = root / "agent-workspaces" / "python"
    r_workspace = root / "agent-workspaces" / "r"
    python_workspace.mkdir(parents=True)
    r_workspace.mkdir(parents=True)
    isolation = KernelReadIsolation((root,))
    try:
        result = {
            "ok": True,
            "python": _python_smoke(python_workspace, isolation),
            "r": _r_smoke(r_workspace, isolation, rscript),
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
