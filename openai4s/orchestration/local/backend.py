"""LocalBackend: the default resource plane — this machine (M3a-3).

Same `AllocationBackend` contract as any cluster, so the reconciler, the
routes and the CLI have exactly one code path whether or not a scheduler
exists. That is the point of collecting local execution into a backend
rather than special-casing it: "no cluster configured" becomes a different
*backend*, not a different program.

Two details are worth stating because getting them wrong is invisible until
it matters:

**The token is honoured here too (INV-8).** A local submit is not going to
lose its response to a network partition, but the reconciler cannot know
which backend it is talking to — so `find_by_token` really searches, and a
resubmission with a token already in flight returns `Existing`. A backend
that answered "of course it's new" would make the reconciler's INV-8 path
untested on the only backend every install has.

**A process that vanished is LOST, not COMPLETED.** Same rule as the
cluster: we record an exit status when we reap the child ourselves; a
tracked process that is simply gone (daemon restarted, someone killed it)
has no exit status, and inventing a successful one loses work silently.

**Launch receipts are at-most-once tombstones until durable acknowledgement.**
The child cannot execute user code until a token/PID/PGID/launch-identity
receipt is fsynced. Merely *observing* exit never deletes that receipt: it stays
token-discoverable until the reconciler reloads a terminal allocation plus a
terminal workload or later recovery epoch from durable storage. The optional
terminal-acknowledgement port then atomically renames the receipt to a cleanup
marker before removing its sidecars, so a crash during garbage collection is
completed on restart rather than reopening the duplicate-submission window.

Bounded by construction: `MAX_CONCURRENT` refuses rather than queues, and
the refusal is `UNSCHEDULABLE` — the same reason a cluster gives when a job
can never be placed, so callers need no local-only branch.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from openai4s.execution.process_group import group_alive, stop_process_group
from openai4s.kernel.environment import name_can_carry_a_secret
from openai4s.orchestration.models import (
    Allocation,
    ExternalHandle,
    Observation,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    WorkloadSpec,
)
from openai4s.orchestration.ports import (
    Created,
    Existing,
    Rejected,
    SubmitResult,
    Unknown,
)

#: How many local allocations may run at once. A workstation is not a
#: cluster; admitting the tenth concurrent job is how a shared login node
#: becomes unusable for everyone.
MAX_CONCURRENT = 4

# Receipt states are deliberately ordered around the only irreversible step:
# executing user code. ``intent`` exists before spawn, ``prepared`` names the
# gated wrapper, and the supervisor durably writes ``running`` before it starts
# the command.
# Therefore a receipt that is not ``running`` is proof that user code did not
# start, while a running receipt remains token-discoverable even if a very fast
# command has already exited.
_RECEIPT_VERSION = 1
_RECEIPT_DIRNAME = ".local-job-receipts"
_ACK_SUFFIX = ".acked"
_ARM_LINE = b"openai4s-go\n"
_ARM_TIMEOUT_S = 5.0
_UNKNOWN_EXIT_CODE = 255

# This small supervisor is the crash boundary between Popen and the durable
# receipt. It holds a per-launch flock as the process-generation identity,
# waits for the parent to arm it, then transitions prepared -> running under a
# separate receipt lock and fsyncs that fact *before* user code can execute. If
# the parent dies before arming, stdin reaches EOF and no user command starts.
_DURABLE_LAUNCHER = r"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile

receipt_path = sys.argv[1]
identity_path = sys.argv[2]
allocation_id = sys.argv[3]
token = sys.argv[4]
command = sys.argv[5:]
identity_fd = os.open(identity_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(identity_fd, fcntl.LOCK_EX)
# The launcher, not user code, owns the launch-generation proof.  Keeping this
# fd out of the command prevents a workload from accidentally closing the
# proof while it is still running.
os.set_inheritable(identity_fd, False)
if sys.stdin.buffer.readline() != b"openai4s-go\n":
    os._exit(125)

lock_path = receipt_path + ".lock"
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    with open(receipt_path, "r", encoding="utf-8") as source:
        receipt = json.load(source)
    if (
        receipt.get("state") != "prepared"
        or receipt.get("pid") != os.getpid()
        or receipt.get("pgid") != os.getpid()
        or receipt.get("allocation_id") != allocation_id
        or receipt.get("token") != token
        or receipt.get("identity_path") != identity_path
    ):
        os._exit(126)
    receipt["state"] = "running"
    parent = os.path.dirname(receipt_path)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".receipt-", dir=parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as target:
            json.dump(receipt, target, sort_keys=True, separators=(",", ":"))
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp_path, receipt_path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)

# Stay resident as a tiny supervisor.  The command inherits this launcher's
# process group but not its identity fd, so the flock is held for exactly the
# lifetime of the direct workload.  Group signals still reach both processes.
try:
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        env=os.environ,
    )
    exit_code = child.wait()
except (OSError, ValueError):
    exit_code = 127
if exit_code < 0:
    signum = -exit_code
    if signum not in (signal.SIGKILL, signal.SIGSTOP):
        signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    os._exit(128 + signum)
os._exit(min(exit_code, 255))
"""


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _acknowledgement_path(receipt_path: Path) -> Path:
    return receipt_path.with_suffix(_ACK_SUFFIX)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Publish one receipt durably; a torn JSON file must never authorize retry."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _receipt_lock(path: Path) -> Iterator[None]:
    """Serialize recovery against the wrapper's prepared -> running commit."""

    import fcntl

    lock_path = Path(f"{path}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _identity_is_held(path: Path | None) -> bool:
    """Whether the exact launch generation still holds its inherited lock."""

    if path is None or not path.exists():
        return False
    import fcntl

    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class _AdoptedProcess:
    """The Popen-sized surface needed for a process from a previous daemon."""

    def __init__(
        self,
        pid: int,
        identity_path: Path | None,
        uncertain_pgid: int | None = None,
    ) -> None:
        self.pid = pid
        self._identity_path = identity_path
        self._uncertain_pgid = uncertain_pgid

    def poll(self) -> int | None:
        if _identity_is_held(self._identity_path):
            return None
        # A program may deliberately close inherited descriptors. Without the
        # launch lock we must not signal this numeric group, but while a group
        # with the recorded id exists we also must not call the allocation gone
        # and let a recovery overlap it. This is the fail-closed middle state.
        if group_alive(self._uncertain_pgid):
            return None
        # In tests the old child may still belong to this Python process; reap
        # it opportunistically. In production a new daemon gets ChildProcessError.
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        return _UNKNOWN_EXIT_CODE

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(["adopted-local-job"], timeout)
            time.sleep(0.05)
        return _UNKNOWN_EXIT_CODE

    def send_signal(self, sig: int) -> None:
        os.kill(self.pid, sig)


@dataclass
class _LocalJob:
    """One local process, plus what we will need after it exits."""

    allocation_id: str
    token: str
    process: Any
    started_at: float
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    exit_code: int | None = None
    cancelled: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
    #: The child's process group, read at *spawn*. `os.getpgid(pid)` fails once
    #: the leader has been reaped -- which `observe()` does on every tick --
    #: and that is exactly when the surviving group most needs signalling.
    pgid: int | None = None
    #: The receipt and inherited identity lock survive daemon restart. Receipts
    #: remain token-discoverable after terminal exit until the reconciler proves
    #: the allocation was durably terminal and its workload either terminal or
    #: advanced to a later recovery epoch.
    receipt_path: Path | None = None
    identity_path: Path | None = None
    adopted: bool = False
    identity_verified: bool = True


class LocalBackend:
    """Run allocations as child processes on this machine."""

    name = "local"

    def __init__(
        self,
        *,
        log_dir: Path | str | None = None,
        max_concurrent: int = MAX_CONCURRENT,
        clock: Any = time.monotonic,
    ) -> None:
        self._jobs: dict[str, _LocalJob] = {}
        self._lock = threading.RLock()
        self._log_dir = Path(log_dir).expanduser() if log_dir else None
        self._receipt_dir: Path | None = None
        self._receipt_error: str | None = None
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            # Native Windows is unsupported. Supported macOS/Linux/WSL paths
            # provide flock, the process-generation proof these receipts use.
            if os.name == "posix":
                self._receipt_dir = self._log_dir / _RECEIPT_DIRNAME
                self._receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._max_concurrent = max_concurrent
        self._clock = clock
        if self._receipt_dir is not None:
            self._load_receipts()

    # --- durable launch receipts ------------------------------------------

    def _receipt_path(self, allocation_id: str) -> Path:
        assert self._receipt_dir is not None
        digest = hashlib.sha256(allocation_id.encode("utf-8")).hexdigest()
        return self._receipt_dir / f"{digest}.json"

    def _identity_path(self, allocation_id: str) -> Path:
        assert self._receipt_dir is not None
        digest = hashlib.sha256(allocation_id.encode("utf-8")).hexdigest()
        return self._receipt_dir / (f"{digest}.{secrets.token_hex(16)}.identity")

    @staticmethod
    def _read_receipt(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("receipt is not an object")
        required_strings = ("allocation_id", "token", "state", "identity_path")
        if value.get("version") != _RECEIPT_VERSION or any(
            not isinstance(value.get(key), str) or not value.get(key)
            for key in required_strings
        ):
            raise ValueError("receipt shape is invalid")
        if value["state"] not in {"intent", "prepared", "running"}:
            raise ValueError("receipt state is invalid")
        for key in ("pid", "pgid"):
            item = value.get(key)
            if item is not None and (
                isinstance(item, bool) or not isinstance(item, int) or item <= 0
            ):
                raise ValueError(f"receipt {key} is invalid")
        if value["state"] == "intent":
            if value.get("pid") is not None or value.get("pgid") is not None:
                raise ValueError("intent receipt already has process identity")
        elif value.get("pid") is None or value.get("pid") != value.get("pgid"):
            # Durable launches are POSIX session leaders, so PID == PGID. A
            # corrupt group must never become authority to signal that number.
            raise ValueError("armed receipt process identity is inconsistent")
        return value

    def _write_receipt(self, path: Path, value: dict[str, Any]) -> None:
        """Test seam around the one durable publication operation."""

        _atomic_write_json(path, value)

    def _validate_receipt_paths(self, path: Path, receipt: dict[str, Any]) -> None:
        """A corrupt receipt cannot turn recovery into arbitrary path access."""

        assert self._receipt_dir is not None
        allocation_id = str(receipt["allocation_id"])
        if path.is_symlink() or path != self._receipt_path(allocation_id):
            raise ValueError("receipt path does not match allocation")
        identity_path = Path(str(receipt["identity_path"]))
        receipt_root = self._receipt_dir.resolve()
        try:
            identity_resolved = identity_path.resolve(strict=False)
        except OSError as exc:
            raise ValueError("receipt identity path is invalid") from exc
        digest = hashlib.sha256(allocation_id.encode("utf-8")).hexdigest()
        name_parts = identity_path.name.split(".")
        if (
            identity_path.is_symlink()
            or identity_resolved.parent != receipt_root
            or len(name_parts) != 3
            or name_parts[0] != digest
            or len(name_parts[1]) != 32
            or any(char not in "0123456789abcdef" for char in name_parts[1])
            or name_parts[2] != "identity"
        ):
            raise ValueError("receipt identity path escapes its registry")
        if self._log_dir is not None:
            for field_name, suffix in (
                ("stdout_path", ".out"),
                ("stderr_path", ".err"),
            ):
                raw = receipt.get(field_name)
                if raw is None:
                    continue
                expected = (self._log_dir / f"{allocation_id}{suffix}").resolve(
                    strict=False
                )
                if (
                    expected.parent != self._log_dir.resolve()
                    or Path(str(raw)).resolve(strict=False) != expected
                ):
                    raise ValueError(f"receipt {field_name} is invalid")

    def _validate_launch_receipt(
        self,
        path: Path,
        receipt: dict[str, Any],
        *,
        allocation_id: str,
        token: str,
        pid: int,
        pgid: int | None,
        identity_path: Path,
    ) -> None:
        """Verify that a running commit belongs to this exact Popen attempt."""

        self._validate_receipt_paths(path, receipt)
        if (
            receipt.get("allocation_id") != allocation_id
            or receipt.get("token") != token
            or receipt.get("pid") != pid
            or receipt.get("pgid") != pgid
            or Path(str(receipt.get("identity_path"))) != identity_path
        ):
            raise ValueError("receipt does not match the current launch")

    @staticmethod
    def _unlink_if_present(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _remove_unarmed_receipt(self, path: Path, receipt: dict[str, Any]) -> None:
        identity_path = Path(str(receipt["identity_path"]))
        self._unlink_if_present(path)
        if not _identity_is_held(identity_path):
            self._unlink_if_present(identity_path)
        # Every caller holds this lock. The only post-spawn caller first proves
        # the gated wrapper unable to execute or conclusively stopped, so no
        # legitimate waiter remains even though our fd keeps this inode alive
        # until the context manager exits.
        self._unlink_if_present(Path(f"{path}.lock"))
        _fsync_directory(path.parent)

    def _complete_acknowledgement(self, ack_path: Path) -> None:
        """Finish a post-terminal cleanup marker while its receipt lock is held."""

        receipt = self._read_receipt(ack_path)
        allocation_id = str(receipt["allocation_id"])
        receipt_path = self._receipt_path(allocation_id)
        if ack_path.is_symlink() or ack_path != _acknowledgement_path(receipt_path):
            raise ValueError("terminal acknowledgement path is invalid")
        # Validate every path against the original receipt name.  The .acked
        # rename is authorization to complete cleanup, never authorization to
        # trust paths embedded in a corrupt file.
        self._validate_receipt_paths(receipt_path, receipt)
        identity_path = Path(str(receipt["identity_path"]))
        if _identity_is_held(identity_path):
            raise RuntimeError("terminal allocation still holds its launch identity")
        self._unlink_if_present(receipt_path)
        self._unlink_if_present(identity_path)
        # No wrapper can still need this lock: the running receipt was committed
        # before user code started, and terminal acknowledgement refuses while
        # that exact launch identity is held. Unlink while we still hold the
        # inode so no new lock file can race this cleanup.
        self._unlink_if_present(Path(f"{receipt_path}.lock"))
        # Make every sidecar deletion durable while the acknowledgement marker
        # still exists. If power fails before this fsync, the already-fsynced
        # marker survives and restart repeats cleanup; after it, all sidecars
        # are conclusively gone before the marker itself may disappear.
        _fsync_directory(ack_path.parent)
        # Marker last: if the daemon dies before here, the next construction
        # can finish the already-authorized cleanup without consulting a row
        # that might no longer make the reconciler thread start.
        self._unlink_if_present(ack_path)
        _fsync_directory(ack_path.parent)

    def _sweep_orphan_sidecars(self) -> None:
        """Remove sidecars whose receipt/ack owner is conclusively absent."""

        assert self._receipt_dir is not None
        live_digests = {
            path.name.split(".", 1)[0]
            for pattern in ("*.json", f"*{_ACK_SUFFIX}")
            for path in self._receipt_dir.glob(pattern)
        }
        held_identity_digests: set[str] = set()
        changed = False
        for path in self._receipt_dir.glob("*.identity"):
            parts = path.name.split(".")
            if len(parts) == 3 and _identity_is_held(path):
                held_identity_digests.add(parts[0])
                continue
            if (
                len(parts) != 3
                or len(parts[0]) != 64
                or any(char not in "0123456789abcdef" for char in parts[0])
                or parts[0] in live_digests
            ):
                continue
            self._unlink_if_present(path)
            changed = True
        for path in self._receipt_dir.glob("*.json.lock"):
            digest = path.name.removesuffix(".json.lock")
            if (
                len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or digest in live_digests
                or digest in held_identity_digests
            ):
                continue
            self._unlink_if_present(path)
            changed = True
        if changed:
            _fsync_directory(self._receipt_dir)

    def _adopt_receipt(self, path: Path, receipt: dict[str, Any]) -> None:
        allocation_id = str(receipt["allocation_id"])
        token = str(receipt["token"])
        pid = int(receipt.get("pid") or 0)
        pgid = int(receipt.get("pgid") or 0) or None
        identity_path = Path(str(receipt["identity_path"]))
        identity_held = _identity_is_held(identity_path)
        # Never signal a numeric group after its identity disappeared: the OS
        # may have reused it. The tombstone still blocks a duplicate token.
        safe_pgid = pgid if identity_held else None
        uncertain_pgid = pgid if not identity_held and group_alive(pgid) else None
        proxy_pgid = pgid if identity_held or uncertain_pgid is not None else None
        self._jobs[allocation_id] = _LocalJob(
            allocation_id=allocation_id,
            token=token,
            process=_AdoptedProcess(
                pid,
                identity_path if identity_held else None,
                # Keep the recorded group only inside the non-signalling
                # liveness proxy. If the supervisor identity disappears after
                # adoption, observation stays fail-closed until it is empty.
                uncertain_pgid=proxy_pgid,
            ),
            started_at=float(receipt.get("started_at") or self._clock()),
            stdout_path=(
                Path(str(receipt["stdout_path"]))
                if receipt.get("stdout_path")
                else None
            ),
            stderr_path=(
                Path(str(receipt["stderr_path"]))
                if receipt.get("stderr_path")
                else None
            ),
            diagnostics={
                "pid": pid,
                "adopted_after_restart": True,
                "process_identity_verified": identity_held,
                "unverified_group_still_present": uncertain_pgid is not None,
            },
            pgid=safe_pgid,
            receipt_path=path,
            identity_path=identity_path,
            adopted=True,
            identity_verified=identity_held,
        )

    def _recover_unarmed_receipt(self, path: Path) -> None:
        """Resolve a wrapper that had not durably authorized user code."""

        with _receipt_lock(path):
            receipt = self._read_receipt(path)
            self._validate_receipt_paths(path, receipt)
            if receipt["state"] == "running":
                # The wrapper committed before recovery acquired the lock.
                adopt = receipt
            elif receipt["state"] == "intent" or receipt.get("pid") is None:
                self._remove_unarmed_receipt(path, receipt)
                return
            else:
                identity_path = Path(str(receipt["identity_path"]))
                pid = int(receipt["pid"])
                pgid = int(receipt.get("pgid") or 0) or None
                if not _identity_is_held(identity_path):
                    # The gated wrapper saw parent EOF and exited. It cannot
                    # write running or exec while the receipt says prepared.
                    self._remove_unarmed_receipt(path, receipt)
                    return
                proxy = _AdoptedProcess(pid, identity_path)
                stopped, _detail = stop_process_group(proxy, pgid)
                if stopped and not _identity_is_held(identity_path):
                    self._remove_unarmed_receipt(path, receipt)
                    return
                # Undecidable: retain token identity instead of allowing retry.
                adopt = receipt
        self._adopt_receipt(path, adopt)

    def _load_receipts(self) -> None:
        assert self._receipt_dir is not None
        # An .acked rename is durable proof that the control plane committed
        # terminal state before cleanup began. Complete these first so a crash
        # at any individual unlink cannot turn into permanent growth.
        for ack_path in sorted(self._receipt_dir.glob(f"*{_ACK_SUFFIX}")):
            receipt_path = ack_path.with_suffix(".json")
            try:
                with _receipt_lock(receipt_path):
                    self._complete_acknowledgement(ack_path)
            except Exception as exc:  # noqa: BLE001 - malformed proof fails closed
                self._receipt_error = (
                    f"cannot complete local-job acknowledgement {ack_path.name}: "
                    f"{type(exc).__name__}"
                )
        for path in sorted(self._receipt_dir.glob("*.json")):
            try:
                receipt = self._read_receipt(path)
                self._validate_receipt_paths(path, receipt)
                if receipt["state"] == "running":
                    self._adopt_receipt(path, receipt)
                else:
                    self._recover_unarmed_receipt(path)
            except Exception as exc:  # noqa: BLE001 - corruption fails closed
                # A corrupt durable fact is not evidence that nothing landed.
                self._receipt_error = (
                    f"cannot reconcile durable local-job receipt {path.name}: "
                    f"{type(exc).__name__}"
                )
        self._sweep_orphan_sidecars()

    def _wait_for_running_receipt(
        self, path: Path, process: subprocess.Popen
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + _ARM_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                receipt = self._read_receipt(path)
            except (OSError, ValueError, json.JSONDecodeError):
                receipt = None
            if receipt is not None and receipt.get("state") == "running":
                return receipt
            if process.poll() is not None:
                return None
            time.sleep(0.01)
        return None

    # --- submission -------------------------------------------------------

    def _live_count(self) -> int:
        live = 0
        for job in self._jobs.values():
            code, whole_group_alive = self._poll_job(job)
            if code is None or whole_group_alive:
                live += 1
        return live

    @staticmethod
    def _poll_job(job: _LocalJob) -> tuple[int | None, bool]:
        """Return leader status/group liveness and freeze a terminal job.

        A saved pgid is needed only for the short leader-exited/child-alive
        window. Keeping it after the group is confirmed empty lets OS pgid
        reuse resurrect a completed allocation or makes a later cancel/close
        signal an unrelated process group. Once terminal, cache the exit code
        and discard the pgid permanently.
        """

        if job.exit_code is not None:
            return job.exit_code, False
        if (
            job.adopted
            and job.identity_verified
            and not _identity_is_held(job.identity_path)
        ):
            # Identity is a live fact, not something adoption may cache.  The
            # resident supervisor normally holds it until the workload exits;
            # if it disappears while descendants remain, retain liveness in
            # _AdoptedProcess but permanently revoke permission to signal this
            # numeric group, which the OS may later reuse.
            job.identity_verified = False
            job.pgid = None
            job.diagnostics["process_identity_verified"] = False
            job.diagnostics["launch_identity_lost_after_adoption"] = True
        code = job.process.poll()
        if code is None:
            return None, True
        whole_group_alive = group_alive(job.pgid)
        if not whole_group_alive:
            job.exit_code = code
            job.pgid = None
        return code, whole_group_alive

    def submit(
        self,
        *,
        allocation: Allocation,
        spec: WorkloadSpec,
        profile: ResourceProfile,
    ) -> SubmitResult:
        token = allocation.submission_token.value
        with self._lock:
            # INV-8 on the backend every install has: a resubmission of a
            # token already in flight is Existing, not a second process.
            for job in self._jobs.values():
                if job.token == token:
                    return Existing(handle=self._handle(job.allocation_id))

            if self._receipt_error is not None:
                return Unknown(
                    token=allocation.submission_token,
                    detail=(
                        "durable local-job state could not be reconciled; "
                        "refusing a submission whose token outcome is unknown"
                    ),
                )

            if not spec.command:
                return Rejected(
                    reason=Reason.INVALID_SPEC,
                    detail="a local workload needs a command",
                )
            if self._live_count() >= self._max_concurrent:
                # The same reason a cluster gives for "this can never be
                # placed", so no caller needs a local-only branch.
                return Rejected(
                    reason=Reason.UNSCHEDULABLE,
                    detail=(
                        f"local backend is at capacity "
                        f"({self._max_concurrent} concurrent allocations)"
                    ),
                )

            try:
                child_env = self._child_env(spec)
            except ValueError as exc:
                return Rejected(reason=Reason.INVALID_SPEC, detail=str(exc))

            stdout_path = stderr_path = None
            stdout_handle = stderr_handle = subprocess.DEVNULL
            if self._log_dir is not None:
                stdout_path = self._log_dir / f"{allocation.id}.out"
                stderr_path = self._log_dir / f"{allocation.id}.err"
                stdout_handle = open(stdout_path, "wb")  # noqa: SIM115
                stderr_handle = open(stderr_path, "wb")  # noqa: SIM115

            receipt_path: Path | None = None
            identity_path: Path | None = None
            receipt: dict[str, Any] | None = None
            launch_unknown: str | None = None
            durable = self._receipt_dir is not None
            if durable:
                receipt_path = self._receipt_path(allocation.id)
                identity_path = self._identity_path(allocation.id)
                try:
                    identity_fd = os.open(
                        identity_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    os.close(identity_fd)
                    receipt = {
                        "version": _RECEIPT_VERSION,
                        "allocation_id": allocation.id,
                        "token": token,
                        "state": "intent",
                        "pid": None,
                        "pgid": None,
                        "started_at": self._clock(),
                        "identity_path": str(identity_path),
                        "stdout_path": str(stdout_path) if stdout_path else None,
                        "stderr_path": str(stderr_path) if stderr_path else None,
                    }
                    with _receipt_lock(receipt_path):
                        self._write_receipt(receipt_path, receipt)
                except OSError:
                    if identity_path is not None:
                        self._unlink_if_present(identity_path)
                    for handle in (stdout_handle, stderr_handle):
                        if handle not in (subprocess.DEVNULL, None):
                            handle.close()
                    return Rejected(
                        reason=Reason.BOOTSTRAP_FAILED,
                        detail="could not create a durable local-job receipt",
                    )

            launch_argv = list(spec.command)
            launch_stdin: Any = subprocess.DEVNULL
            if durable:
                assert receipt_path is not None and identity_path is not None
                launch_argv = [
                    sys.executable,
                    "-I",
                    "-c",
                    _DURABLE_LAUNCHER,
                    str(receipt_path),
                    str(identity_path),
                    allocation.id,
                    token,
                    *spec.command,
                ]
                launch_stdin = subprocess.PIPE

            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, no shell
                    launch_argv,
                    cwd=spec.workdir or None,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    stdin=launch_stdin,
                    # Its own process group, so cancelling kills the whole
                    # tree rather than the parent that spawned the real work.
                    start_new_session=True,
                    env=child_env,
                    shell=False,
                )
            except (OSError, ValueError) as exc:
                if receipt_path is not None and receipt is not None:
                    with _receipt_lock(receipt_path):
                        self._remove_unarmed_receipt(receipt_path, receipt)
                for handle in (stdout_handle, stderr_handle):
                    if handle not in (subprocess.DEVNULL, None):
                        handle.close()
                return Rejected(reason=Reason.BOOTSTRAP_FAILED, detail=str(exc))
            finally:
                # The child holds its own duplicated descriptors.
                for handle in (stdout_handle, stderr_handle):
                    if handle not in (subprocess.DEVNULL, None):
                        try:
                            handle.close()
                        except OSError:
                            pass

            # ``start_new_session=True`` makes the child a POSIX session
            # leader, so its process group id is its pid by definition.  Do
            # not ask the kernel for it after spawn: a wrapper may already
            # have exited while a descendant remains in that group, and
            # ``getpgid`` then loses the only handle capable of stopping the
            # surviving work.
            job_pgid: int | None = process.pid if os.name == "posix" else None

            if durable:
                assert receipt_path is not None
                assert identity_path is not None
                assert receipt is not None
                prepared = dict(
                    receipt,
                    state="prepared",
                    pid=process.pid,
                    pgid=job_pgid,
                )
                try:
                    with _receipt_lock(receipt_path):
                        self._write_receipt(receipt_path, prepared)
                except Exception:  # noqa: BLE001 - user code is still gated
                    try:
                        process.stdin and process.stdin.close()
                    except OSError:
                        pass
                    stopped, _detail = stop_process_group(process, job_pgid)
                    if stopped:
                        with _receipt_lock(receipt_path):
                            self._remove_unarmed_receipt(receipt_path, prepared)
                    return Rejected(
                        reason=Reason.BOOTSTRAP_FAILED,
                        detail="could not publish the local-job launch identity",
                    )

                # No user command can execute before this byte. The supervisor
                # writes and fsyncs ``running`` before it spawns the command.
                try:
                    assert process.stdin is not None
                    process.stdin.write(_ARM_LINE)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin and process.stdin.close()
                    except OSError:
                        pass

                running_receipt = self._wait_for_running_receipt(receipt_path, process)
                try:
                    if running_receipt is not None:
                        self._validate_launch_receipt(
                            receipt_path,
                            running_receipt,
                            allocation_id=allocation.id,
                            token=token,
                            pid=process.pid,
                            pgid=job_pgid,
                            identity_path=identity_path,
                        )
                except Exception as exc:  # noqa: BLE001 - fail closed on registry
                    self._receipt_error = (
                        f"cannot validate durable local-job receipt "
                        f"{receipt_path.name}: {type(exc).__name__}"
                    )
                    stop_process_group(process, job_pgid)
                    running_receipt = None
                    launch_unknown = (
                        "local-job launch receipt became unreadable; reconcile by token"
                    )

                if running_receipt is None and launch_unknown is None:
                    # Re-read under the commit lock. If the wrapper already
                    # published ``running``, the original Popen is the known
                    # submission and must not be killed just because the wait
                    # lost its confirmation. Otherwise the same lock keeps the
                    # wrapper gated while teardown is conclusively checked.
                    try:
                        with _receipt_lock(receipt_path):
                            latest = self._read_receipt(receipt_path)
                            self._validate_launch_receipt(
                                receipt_path,
                                latest,
                                allocation_id=allocation.id,
                                token=token,
                                pid=process.pid,
                                pgid=job_pgid,
                                identity_path=identity_path,
                            )
                            if latest.get("state") == "running":
                                running_receipt = latest
                            else:
                                stopped, _detail = stop_process_group(process, job_pgid)
                                if stopped:
                                    self._remove_unarmed_receipt(receipt_path, latest)
                                    return Rejected(
                                        reason=Reason.BOOTSTRAP_FAILED,
                                        detail="local-job launch gate did not arm",
                                    )
                                launch_unknown = (
                                    "local-job launch gate could not be "
                                    "conclusively stopped; reconcile by token"
                                )
                    except Exception as exc:  # noqa: BLE001 - outcome is unknown
                        # A broken durable fact can never be treated as absence.
                        # Stop the exact Popen we own, retain the damaged fact,
                        # and keep an in-memory token handle for reconciliation.
                        self._receipt_error = (
                            f"cannot reconcile durable local-job receipt "
                            f"{receipt_path.name}: {type(exc).__name__}"
                        )
                        stop_process_group(process, job_pgid)
                        launch_unknown = (
                            "local-job launch receipt became unreadable; "
                            "reconcile by token"
                        )

            self._jobs[allocation.id] = _LocalJob(
                allocation_id=allocation.id,
                pgid=job_pgid,
                token=token,
                process=process,
                started_at=self._clock(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                diagnostics={"pid": process.pid, "command": shlex.join(spec.command)},
                receipt_path=receipt_path,
                identity_path=identity_path,
            )
            if launch_unknown is not None:
                return Unknown(
                    token=allocation.submission_token,
                    detail=launch_unknown,
                )
        return Created(
            handle=self._handle(allocation.id), diagnostics={"pid": process.pid}
        )

    def _child_env(self, spec: WorkloadSpec) -> dict[str, str]:
        """A named environment, never the daemon's.

        The daemon's environment holds API keys; inheriting it by default
        would put them in every batch job's `/proc/<pid>/environ`.
        """
        base = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if key in os.environ
        }
        # The spec's environment is caller-supplied -- `POST /orchestration/jobs`
        # passes `body["environment"]` straight through, and `WorkloadSpec`
        # validates only `command`. Taking it verbatim let a submission set
        # `LD_PRELOAD` or `PYTHONSTARTUP` on a child of the daemon, and put a
        # credential-shaped value into `/proc/<pid>/environ` where every other
        # process of the same uid can read it. The scheduler sibling has
        # refused exactly this since it was written; two backends disagreeing
        # about the same field is the drift, not the rule.
        for key, value in spec.environment.items():
            if name_can_carry_a_secret(str(key)):
                raise ValueError(
                    f"refusing to put {key!r} in a job environment "
                    f"(INV-9: pass a path to a 0600 file instead)"
                )
            base[str(key)] = str(value)
        return base

    # --- observation ------------------------------------------------------

    def observe(self, allocation: Allocation) -> Observation:
        with self._lock:
            job = self._jobs.get(allocation.id)
            if job is None:
                if self._receipt_error is not None:
                    return Observation(
                        phase=allocation.phase,
                        reason=Reason.BACKEND_UNAVAILABLE,
                        handle=allocation.handle,
                        diagnostics={"note": "durable local-job state is unreadable"},
                    )
                # Tracked by the caller, unknown to us: the daemon restarted,
                # so the child is gone with no exit status to report. LOST,
                # never COMPLETED — see the module docstring.
                if allocation.handle is not None:
                    return Observation(
                        phase=Phase.LOST,
                        reason=Reason.WORKER_LOST,
                        handle=allocation.handle,
                        diagnostics={"note": "no local record; daemon restarted?"},
                    )
                return Observation(phase=Phase.SUBMITTING)

            code, whole_group_alive = self._poll_job(job)
            if code is None or whole_group_alive:
                diagnostics = dict(job.diagnostics)
                if code is not None:
                    # The process we spawned was only the group leader. A
                    # wrapper may exit after starting the real work, and that
                    # work is still the allocation until the whole group is
                    # empty. Publishing COMPLETED here made the reconciler
                    # release capacity while descendants kept consuming it.
                    diagnostics["leader_exit_code"] = code
                return Observation(
                    phase=Phase.ACTIVE,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            assert code is not None
            diagnostics = dict(job.diagnostics, exit_code=code)
            if job.cancelled:
                return Observation(
                    phase=Phase.CANCELLED,
                    reason=Reason.USER_CANCELLED,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            if job.adopted:
                # A receipt proves submission identity, not an exit status.
                # Once a previous daemon's process is gone, inventing success
                # would silently lose work. Keep the receipt for token lookup.
                return Observation(
                    phase=Phase.LOST,
                    reason=Reason.WORKER_LOST,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            if code == 0:
                return Observation(
                    phase=Phase.COMPLETED,
                    handle=self._handle(job.allocation_id),
                    diagnostics=diagnostics,
                )
            # A negative code is a signal. SIGKILL after an OOM is the one
            # worth naming, because "killed" and "failed" send an operator
            # looking in different places.
            reason = Reason.OUT_OF_MEMORY if code == -signal.SIGKILL else None
            return Observation(
                phase=Phase.FAILED,
                reason=reason,
                handle=self._handle(job.allocation_id),
                diagnostics=diagnostics,
            )

    # --- lifecycle --------------------------------------------------------

    def cancel(self, allocation: Allocation, *, reason: Reason) -> None:
        """Idempotent: cancelling something already gone is success."""
        with self._lock:
            job = self._jobs.get(allocation.id)
            if job is None:
                return
            code, whole_group_alive = self._poll_job(job)
            if code is not None and not whole_group_alive:
                return
            job.cancelled = True
            if job.adopted and not job.identity_verified:
                # A numeric PID/PGID without the inherited launch lock may now
                # belong to somebody else. Refuse to signal it and leave the
                # cancel barrier open until observation proves the group gone.
                job.cancelled = False
                job.diagnostics["cancel"] = {
                    "stopped": False,
                    "detail": "launch identity unavailable; refusing unsafe signal",
                }
                return
            # `stop_process_group`, not a bare `killpg(SIGTERM)`: it escalates
            # to SIGKILL and then *confirms* the group is gone. A TERM that the
            # work ignores used to return here as success, so the cancel
            # barrier concluded "released" for a job still holding its CPUs --
            # the one outcome the barrier exists to prevent. The shared helper
            # is also where the escalation ladder is tuned, so this path gets
            # future fixes instead of missing them.
            stopped, detail = stop_process_group(job.process, job.pgid)
            job.diagnostics["cancel"] = {"stopped": stopped, "detail": detail}
            if stopped:
                code = job.process.poll()
                if code is not None:
                    job.exit_code = code
                    job.pgid = None

    def find_by_token(self, token: SubmissionToken) -> ExternalHandle | None:
        with self._lock:
            for job in self._jobs.values():
                if job.token == token.value:
                    return self._handle(job.allocation_id)
            if self._receipt_error is not None:
                raise RuntimeError(
                    "durable local-job state is unreadable; token absence is unknown"
                )
        return None

    def terminal_acknowledgement_candidates(self) -> tuple[str, ...]:
        """Backend recovery facts; the durable store decides terminality."""

        with self._lock:
            return tuple(self._jobs)

    def acknowledge_terminal(self, allocation: Allocation) -> None:
        """Discard one recovery/token fact after durable terminal commit.

        Renaming ``.json`` to ``.acked`` is the atomic acknowledgement point.
        The marker is itself a cleanup outbox: a daemon crash after the rename
        leaves enough durable proof for ``_load_receipts`` to finish removing
        the identity and lock sidecars on the next start.
        """

        if not allocation.phase.is_terminal:
            raise ValueError("only a durably terminal allocation may be acknowledged")
        with self._lock:
            job = self._jobs.get(allocation.id)
            if job is None:
                # Idempotent after an earlier successful acknowledgement.
                return
            if job.token != allocation.submission_token.value:
                raise ValueError("terminal acknowledgement token does not match")
            code, whole_group_alive = self._poll_job(job)
            if code is None or whole_group_alive:
                raise RuntimeError("terminal acknowledgement reached a live process")

            receipt_path = job.receipt_path
            if receipt_path is not None:
                ack_path = _acknowledgement_path(receipt_path)
                with _receipt_lock(receipt_path):
                    if ack_path.exists():
                        ack_receipt = self._read_receipt(ack_path)
                        if ack_receipt.get("token") != job.token:
                            raise ValueError(
                                "terminal acknowledgement token does not match"
                            )
                    elif receipt_path.exists():
                        receipt = self._read_receipt(receipt_path)
                        self._validate_receipt_paths(receipt_path, receipt)
                        if (
                            receipt.get("allocation_id") != allocation.id
                            or receipt.get("token") != job.token
                        ):
                            raise ValueError(
                                "terminal acknowledgement receipt does not match"
                            )
                        os.replace(receipt_path, ack_path)
                        _fsync_directory(receipt_path.parent)
                    if ack_path.exists():
                        self._complete_acknowledgement(ack_path)
                    elif job.identity_path is not None:
                        # A previous idempotent cleanup may already have removed
                        # the receipt and marker.  The exact identity is still
                        # safe to remove only after its lock is no longer held.
                        if _identity_is_held(job.identity_path):
                            raise RuntimeError(
                                "terminal allocation still holds its launch identity"
                            )
                        self._unlink_if_present(job.identity_path)
                        _fsync_directory(receipt_path.parent)
            self._jobs.pop(allocation.id, None)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": self.name,
                "available": True,
                "running": self._live_count(),
                "max_concurrent": self._max_concurrent,
                "tracked": len(self._jobs),
                "durable_receipts": self._receipt_dir is not None,
                "receipt_error": self._receipt_error,
            }

    def log_paths(self, allocation_id: str) -> tuple[Path | None, Path | None]:
        """Where this allocation's output went, for the log-tail route."""
        with self._lock:
            job = self._jobs.get(allocation_id)
            if job is not None:
                return job.stdout_path, job.stderr_path
            # Terminal acknowledgement intentionally removes the in-memory
            # process record. Logs have deterministic names and remain a user
            # projection after that GC, including after daemon restart.
            if self._log_dir is not None and (
                allocation_id.startswith("alloc_")
                and len(allocation_id) == len("alloc_") + 12
                and all(char in "0123456789abcdef" for char in allocation_id[6:])
            ):
                return (
                    self._log_dir / f"{allocation_id}.out",
                    self._log_dir / f"{allocation_id}.err",
                )
            return None, None

    def close(self) -> None:
        """Terminate anything still running. The daemon owns these children,
        so leaving them behind on shutdown orphans real compute."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            with self._lock:
                code, whole_group_alive = self._poll_job(job)
            if code is not None and not whole_group_alive:
                continue
            if job.adopted and not job.identity_verified:
                # Same PID-reuse rule as cancel(): shutdown must not turn an
                # uncertain receipt into a signal for an unrelated process.
                continue
            # Same stopper as `cancel`: shutdown is the other place a group
            # that ignores TERM turns into orphaned compute. The helper also
            # handles a reaped leader with surviving descendants, which a
            # `poll()` guard would skip.
            stopped, _detail = stop_process_group(job.process, job.pgid)
            if stopped:
                with self._lock:
                    code = job.process.poll()
                    if code is not None:
                        job.exit_code = code
                        job.pgid = None

    # --- helpers ----------------------------------------------------------

    def _handle(self, allocation_id: str) -> ExternalHandle:
        with self._lock:
            job = self._jobs.get(allocation_id)
            external = str(job.process.pid) if job else allocation_id
        return ExternalHandle(backend=self.name, external_id=external)


__all__ = ["MAX_CONCURRENT", "LocalBackend"]
