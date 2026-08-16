"""A chat session whose kernel lives on a granted resource (M3b-3/5).

Two ideas carry this module, and both are invariants rather than design
preferences.

**INV-5 — readiness is a conjunction.** A session is running only when all
four of these hold: the allocation is granted, the worker has registered,
the workspace exists, and the kernel answers. The resource plane saying
"running" means a process was started somewhere; it says nothing about
whether that process reached us, whether its files are where the user's
data is, or whether it can evaluate `1+1`. Reporting ready on the
scheduler's word alone is the specific failure this shape exists to
prevent: a user typing into a session that cannot execute, and a support
conversation that begins "but the cluster says it's running".
`SessionReadiness` therefore has no boolean shortcut — it names which
condition is missing, because "not ready" without "waiting for the worker
to dial in" is a spinner with no information in it.

**INV-9 — the credential is per attempt, and never persisted.** The user's
`WorkloadSpec` is durable and is what an operator can read back. The spec
that is actually submitted is derived from it at submission time, once per
attempt, and carries a freshly minted credential written to a 0600 file
whose *path* is all the resource plane is told. Persisting it would put a
signature into `spec_json`, where every later reader of the workloads
table would inherit it; deriving it per attempt is also what makes a
recovery a genuinely new attempt (INV-7) rather than a replay of the old
one's identity.

Nothing here names a scheduler (INV-2): a "worker launch" is a command and
an environment, and which resource plane runs it is the backend's business.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openai4s.orchestration.bootstrap import (
    CREDENTIAL_PATH_ENV,
    BootstrapAuthority,
    write_credential_file,
)
from openai4s.orchestration.models import (
    Allocation,
    Phase,
    RecoveryStrategy,
    ResourceProfile,
    UnsupportedRecoveryStrategy,
    Workload,
    WorkloadKind,
    WorkloadSpec,
)
from openai4s.orchestration.reconciler import (
    DEFAULT_MAX_RECOVERIES as _DEFAULT_MAX_RECOVERIES,
)

#: Where the worker is told to dial. Read by `kernel/worker.py`.
CONNECT_ENV = "OPENAI4S_WORKER_CONNECT"

#: A multi-node job's per-rank credential path, with `{rank}` still in it.
#: The resource plane's own launcher expands it, because which variable
#: holds a node's rank is that plane's vocabulary and not this layer's.
CREDENTIAL_PATH_TEMPLATE_ENV = "OPENAI4S_WORKER_BOOTSTRAP_PATH_TEMPLATE"

#: How long a credential is good for. Long enough to survive a queue wait
#: that the site's scheduler decides, short enough that one recovered from
#: a log months later is worthless. The queue wait is the reason this is
#: not minutes: a credential that expires while the job is still queued
#: turns a busy cluster into a bootstrap failure.
CREDENTIAL_TTL_S = 24 * 3600

#: Defaults from the plan: two hours idle, forty-eight hours absolute.
DEFAULT_IDLE_TTL_S = 2 * 3600
DEFAULT_MAX_LIFETIME_S = 48 * 3600

#: Re-exported, not redefined: the recovery limit is the reconciler's
#: policy, and two constants with one name drift the moment one is tuned.
DEFAULT_MAX_RECOVERIES = _DEFAULT_MAX_RECOVERIES


@dataclass(frozen=True)
class SessionReadiness:
    """INV-5, as four conditions that must all hold — and which one does not.

    Deliberately not a boolean with a comment. Every one of these can be
    true while the session is unusable, and the difference between them is
    exactly what a user staring at a spinner needs told.
    """

    allocation_granted: bool = False
    worker_registered: bool = False
    workspace_ready: bool = False
    kernel_ready: bool = False
    #: Gang readiness (M4-3). A multi-node session is ready when *every*
    #: rank has registered, not when the first one has: starting a
    #: distributed run against a job whose other nodes are still being
    #: placed surfaces the failure inside the user's computation, where it
    #: looks like their bug. Carried as counts rather than folded into the
    #: boolean so "3 of 4 nodes" is something the UI can say.
    workers_expected: int = 1
    workers_registered: int = 0

    @property
    def ready(self) -> bool:
        return (
            self.allocation_granted
            and self.worker_registered
            and self.workspace_ready
            and self.kernel_ready
            and self.gang_complete
        )

    @property
    def gang_complete(self) -> bool:
        """Whether every rank is in — a refinement of `worker_registered`,
        not a second condition beside it.

        A single-node session is the overwhelmingly common case and gang
        adds nothing to it, so below two expected workers this is exactly
        the boolean it refines. Making the count authoritative in both
        cases would have meant every hand-built readiness had to carry a
        number nobody has, which is how a defaulted 0 turns a ready session
        into a stuck one.
        """
        if self.workers_expected <= 1:
            return self.worker_registered
        return self.workers_registered >= self.workers_expected

    @property
    def blocked_on(self) -> str | None:
        """The first unmet condition, in the order they are met."""
        if not self.allocation_granted:
            return "allocation"
        if not self.workspace_ready:
            return "workspace"
        if not self.worker_registered or not self.gang_complete:
            return "worker"
        if not self.kernel_ready:
            return "kernel"
        return None

    def public(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blocked_on": self.blocked_on,
            "allocation_granted": self.allocation_granted,
            "worker_registered": self.worker_registered,
            "workspace_ready": self.workspace_ready,
            "kernel_ready": self.kernel_ready,
            "workers_expected": self.workers_expected,
            "workers_registered": self.workers_registered,
        }


@dataclass
class SessionRuntime:
    """What the daemon knows about one live session's remote kernel."""

    session_id: str
    workload_id: str
    epoch: int = 0
    registration: Any = None
    #: Every rank that dialled in for this attempt. `registration` stays the
    #: rank the kernel is driven through (rank 0), because one interpreter
    #: runs the cell and the rest are its peers.
    registrations: list[Any] = field(default_factory=list)
    kernel: Any = None
    kernel_ready: bool = False
    state_lost_epochs: list[int] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workload_id": self.workload_id,
            "epoch": self.epoch,
            "state_lost_epochs": list(self.state_lost_epochs),
        }


def worker_launch_command(*, python: str | None = None) -> tuple[str, ...]:
    """The argv a compute node runs to become this daemon's worker.

    `python -u worker.py` and nothing else: everything that varies —
    where to dial, which credential to present — arrives in the
    environment, so the command is identical for every session and an
    operator reading a job script sees no per-session secret in it.
    """
    worker = Path(__file__).resolve().parent.parent / "kernel" / "worker.py"
    return (python or sys.executable, "-u", str(worker))


class AttemptPreparer:
    """Turns the user's durable spec into the spec for *this* attempt.

    Installed on the reconciler as `prepare_attempt`. It runs immediately
    before a submission and once per attempt, which is the only point at
    which "which allocation, which epoch" is known — and therefore the only
    point at which a credential bound to them can exist.
    """

    def __init__(
        self,
        *,
        authority: BootstrapAuthority,
        listen_address: Callable[[], tuple[str, int] | None],
        runtime_dir: Callable[[Workload], Path],
        python: str | None = None,
        credential_ttl_s: int = CREDENTIAL_TTL_S,
        advertise_host: str | None = None,
    ) -> None:
        self._authority = authority
        self._listen_address = listen_address
        self._runtime_dir = runtime_dir
        self._python = python
        self._ttl_s = credential_ttl_s
        self._advertise_host = advertise_host

    def __call__(self, workload: Workload, allocation: Allocation) -> WorkloadSpec:
        if workload.spec.kind is not WorkloadKind.SESSION:
            # A BATCH workload runs the user's command; there is nothing to
            # bootstrap and nothing to hand it.
            return workload.spec
        address = self._listen_address()
        if address is None:
            raise RuntimeError(
                "a cluster session needs a worker listener; set "
                "OPENAI4S_WORKER_LISTEN on the daemon"
            )
        host, port = address
        runtime_dir = self._runtime_dir(workload)
        # The bind address is not necessarily the reachable one: binding
        # 0.0.0.0 is how you accept from anywhere, and "0.0.0.0" is not a
        # place a compute node can dial. An operator who has not said which
        # name their nodes should use gets this daemon's hostname, which is
        # right on the common case and visibly wrong on the rest, rather
        # than an address that fails with no clue why.
        reachable = self._advertise_host or (
            host if host not in ("0.0.0.0", "", "::") else _default_advertise_host()
        )

        # One credential per rank (M4-3). A multi-node job places a worker
        # on every node and each one must present its own: a single rank-0
        # credential is single-use, so on a two-node job exactly one node
        # could ever register and gang readiness could never be satisfied.
        ranks = max(1, int(workload.spec.profile.nodes))
        paths: list[str] = []
        for rank in range(ranks):
            credential = self._authority.issue(
                allocation_id=allocation.id,
                epoch=workload.execution_epoch,
                rank=rank,
                ttl_s=self._ttl_s,
            )
            paths.append(str(write_credential_file(credential, runtime_dir)))

        environment = dict(workload.spec.environment)
        environment[CONNECT_ENV] = f"{reachable}:{port}"
        # The path, never the signature: a job's environment is readable by
        # anyone who can ask the resource plane about the job (INV-9).
        #
        # Rank 0's path is the plain variable, so a single-node job -- every
        # job today -- sees exactly what it saw before. A multi-node job also
        # gets a template it can expand per node, and the *name* of the
        # variable holding the rank, because which variable that is belongs
        # to the resource plane and naming it here would put a scheduler's
        # vocabulary in the orchestration core (INV-2).
        environment[CREDENTIAL_PATH_ENV] = paths[0]
        if ranks > 1:
            environment[CREDENTIAL_PATH_TEMPLATE_ENV] = str(
                runtime_dir
                / (
                    f"bootstrap-{allocation.id}-{workload.execution_epoch}"
                    f"-r{{rank}}.json"
                )
            )
        return WorkloadSpec(
            kind=workload.spec.kind,
            profile=workload.spec.profile,
            command=workload.spec.command or worker_launch_command(python=self._python),
            workdir=workload.spec.workdir,
            environment=environment,
            spec_revision=workload.spec.spec_revision,
        )


def _default_advertise_host() -> str:
    import socket

    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


class ComputeSessionManager:
    """Ties a chat session to a workload, a lease, and a remote kernel.

    Every method here is written so that calling it twice is the same as
    calling it once: a daemon restart replays this path, and a manager that
    needed a clean slate would make a restart into an outage.
    """

    def __init__(
        self,
        *,
        store: Any,
        gateway: Any,
        authority: BootstrapAuthority,
        workspace_root: Path | str,
        kernel_factory: Callable[[Any], Any] | None = None,
        idle_ttl_s: int = DEFAULT_IDLE_TTL_S,
        max_lifetime_s: int = DEFAULT_MAX_LIFETIME_S,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._authority = authority
        self._workspace_root = Path(workspace_root)
        self._kernel_factory = kernel_factory
        self._idle_ttl_s = idle_ttl_s
        self._max_lifetime_s = max_lifetime_s
        self._on_event = on_event or (lambda kind, payload: None)
        self._runtimes: dict[str, SessionRuntime] = {}

    # --- workspaces -------------------------------------------------------

    def workspace_for(self, workload_id: str) -> Path:
        return self._workspace_root / workload_id

    def ensure_workspace(self, workload_id: str) -> Path:
        path = self.workspace_for(workload_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def runtime_dir(self, workload: Workload) -> Path:
        """Where this attempt's credential file goes. Inside the workspace,
        because that is the directory both ends already agree on."""
        path = self.workspace_for(workload.id) / ".openai4s"
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return path

    # --- lifecycle --------------------------------------------------------

    def request_session(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        profile: ResourceProfile,
        project_id: str | None = None,
        # No default: naming one here would name a resource plane in the
        # orchestration core, which is the whole of what INV-2 forbids.
        # Which plane runs a session is the composition layer's decision.
        backend: str,
        environment: dict[str, str] | None = None,
        recovery: RecoveryStrategy | str = RecoveryStrategy.WORKSPACE_ONLY,
    ) -> Workload:
        """Ask for a cluster kernel for this chat session, or return the
        workload already backing it."""
        # Refused before anything durable happens (M4-6). A workload
        # created and then rejected would leave a row an operator has to
        # reason about for a request that was never honoured.
        try:
            strategy = RecoveryStrategy(recovery)
        except ValueError:
            raise UnsupportedRecoveryStrategy(recovery) from None
        if not strategy.supported:
            raise UnsupportedRecoveryStrategy(strategy)

        existing = self._store.leases.workload_for_session(session_id)
        if existing:
            found = self._store.workloads.get_workload(existing)
            if found is not None and not found.phase.is_terminal:
                return found

        workspace = self.ensure_workspace(session_id)
        spec = WorkloadSpec(
            kind=WorkloadKind.SESSION,
            profile=profile,
            workdir=str(workspace),
            environment=dict(environment or {}),
        )
        workload = self._store.workloads.create_workload(
            spec=spec,
            owner_user_id=owner_user_id,
            project_id=project_id,
            backend=backend,
        )
        # The workspace is keyed by workload from here on; the session-keyed
        # one above only existed to have somewhere to stand before an id
        # existed.
        self.ensure_workspace(workload.id)
        self._store.leases.bind_session(session_id, workload.id)
        self._store.leases.open_lease(
            workload.id,
            idle_ttl_s=self._idle_ttl_s,
            max_lifetime_s=self._max_lifetime_s,
        )
        self._runtimes[session_id] = SessionRuntime(
            session_id=session_id, workload_id=workload.id
        )
        self._emit(
            "session_workload_requested",
            {"session_id": session_id, "workload_id": workload.id},
        )
        return workload

    def readiness(self, session_id: str) -> SessionReadiness:
        """INV-5, evaluated against durable state — never against a cached
        'we were ready a minute ago'."""
        workload_id = self._store.leases.workload_for_session(session_id)
        if not workload_id:
            return SessionReadiness()
        allocation = self._store.workloads.active_allocation(workload_id)
        granted = allocation is not None and allocation.phase in (
            Phase.GRANTED,
            Phase.ACTIVE,
        )
        runtime = self._runtimes.get(session_id)
        workload = self._store.workloads.get_workload(workload_id)
        expected = 1
        if workload is not None:
            expected = max(1, int(workload.spec.profile.nodes))
        return SessionReadiness(
            allocation_granted=granted,
            worker_registered=bool(runtime and runtime.registration is not None),
            workspace_ready=self.workspace_for(workload_id).is_dir(),
            kernel_ready=bool(runtime and runtime.kernel_ready),
            workers_expected=expected,
            workers_registered=len(runtime.registrations) if runtime else 0,
        )

    def attach_worker(self, session_id: str, *, timeout_s: float = 60.0) -> bool:
        """Wait for this attempt's worker to dial in, then build its Kernel.

        Keyed by (allocation, epoch), so a straggler from a previous attempt
        cannot be mistaken for this one's worker — the whole point of INV-7
        at the rendezvous rather than only at the credential.
        """
        workload_id = self._store.leases.workload_for_session(session_id)
        if not workload_id:
            return False
        workload = self._store.workloads.get_workload(workload_id)
        allocation = self._store.workloads.active_allocation(workload_id)
        if workload is None or allocation is None:
            return False
        expected = max(1, int(workload.spec.profile.nodes))
        arrivals = self._gateway.await_workers(
            allocation.id,
            workload.execution_epoch,
            expected=expected,
            timeout_s=timeout_s,
        )
        runtime = self._runtimes.setdefault(
            session_id,
            SessionRuntime(session_id=session_id, workload_id=workload_id),
        )
        # Record the partial set even when it is short. A caller that threw
        # away three of four registrations would leave those workers
        # connected, waiting, and unreleasable — and the UI would have no
        # way to say "3 of 4" rather than "not ready".
        runtime.registrations = list(arrivals)
        runtime.epoch = workload.execution_epoch
        if len(arrivals) < expected:
            return False
        # Rank 0, not "whichever arrived first". One interpreter runs the
        # cell and the rest are its peers, and which one that is has to be
        # the same node the user's code thinks it is -- a distributed job
        # whose driver is chosen by network timing is a job whose results
        # depend on network timing.
        registration = next(
            (r for r in arrivals if int(getattr(r, "rank", 0)) == 0), arrivals[0]
        )
        runtime.registration = registration
        if self._kernel_factory is not None:
            runtime.kernel = self._kernel_factory(registration)
            runtime.kernel_ready = True
        self._emit(
            "session_worker_attached",
            {
                "session_id": session_id,
                "workload_id": workload_id,
                "epoch": workload.execution_epoch,
            },
        )
        return True

    def note_state_lost(self, workload_id: str, *, epoch: int) -> None:
        """Recovery happened. Say so — INV-11 forbids quietly continuing.

        The kernel's memory is gone: variables, imports, open files, the
        random seed somebody set three cells ago. Reconnecting to a fresh
        worker and letting the next cell run as if nothing happened is the
        one behaviour this system must never have, because the results
        afterwards look exactly like results from the session that was
        lost.
        """
        session_id = self._store.leases.session_for_workload(workload_id)
        if not session_id:
            return
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            runtime.registration = None
            runtime.registrations = []
            runtime.kernel = None
            runtime.kernel_ready = False
            if epoch not in runtime.state_lost_epochs:
                runtime.state_lost_epochs.append(epoch)
        self._emit(
            "session_kernel_state_lost",
            {"session_id": session_id, "workload_id": workload_id, "epoch": epoch},
        )

    def touch(self, session_id: str) -> bool:
        """A user did something. The *only* thing that renews a lease."""
        workload_id = self._store.leases.workload_for_session(session_id)
        if not workload_id:
            return False
        return self._store.leases.touch(workload_id)

    def release(self, session_id: str, *, reason: Any) -> bool:
        workload_id = self._store.leases.workload_for_session(session_id)
        if not workload_id:
            return False
        stopped = self._store.workloads.request_stop(workload_id, reason=reason)
        self._store.leases.release(workload_id)
        runtime = self._runtimes.pop(session_id, None)
        if runtime is not None and runtime.kernel is not None:
            try:
                runtime.kernel.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self._emit(
            "session_released",
            {"session_id": session_id, "workload_id": workload_id},
        )
        return stopped

    def bind_kernel(self, session_id: str, kernel: Any) -> bool:
        """Record the Kernel a caller built over this session's worker.

        The manager deliberately does not construct it. A session's kernel is
        bound to that session's Host dispatcher, and this layer has no
        business knowing what a dispatcher is — the `kernel_factory`
        constructor argument exists for tests that have no dispatcher at all.
        Production hands the finished object back here so `readiness()` can
        answer the fourth condition (INV-5) from something that exists rather
        than from something that was arranged.
        """
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return False
        runtime.kernel = kernel
        runtime.kernel_ready = kernel is not None
        return True

    def runtime(self, session_id: str) -> SessionRuntime | None:
        return self._runtimes.get(session_id)

    def _emit(self, kind: str, payload: dict) -> None:
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "CONNECT_ENV",
    "CREDENTIAL_PATH_TEMPLATE_ENV",
    "CREDENTIAL_TTL_S",
    "DEFAULT_IDLE_TTL_S",
    "DEFAULT_MAX_LIFETIME_S",
    "DEFAULT_MAX_RECOVERIES",
    "AttemptPreparer",
    "ComputeSessionManager",
    "SessionReadiness",
    "SessionRuntime",
    "worker_launch_command",
]
