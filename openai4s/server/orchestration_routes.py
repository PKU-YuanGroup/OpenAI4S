"""BatchJob routes: submit / list / detail / cancel / logs (plan M3a-8).

Same shape as the other route groups: a validated `RouteSpec` table shared
with the contract inventory, and a tri-state `handle()`.

Ownership is the same rule sessions use — a workload belongs to whoever
submitted it, an admin sees everything, and someone else's workload answers
404 rather than 403, because which jobs exist is itself information about
what a colleague is working on.

Two things this module deliberately does NOT do. It never submits: it writes
a durable row and lets the reconciler do the talking, so a cancel survives a
daemon restart and a submission is never attempted twice from a request
thread. And it never names a scheduler — `profile` is a name from
cluster.toml, and the response carries `allocation_id`, never a job id
(INV-2).
"""

from __future__ import annotations

from typing import Any

from openai4s.orchestration.models import (
    Reason,
    ResourceProfile,
    WorkloadKind,
    WorkloadSpec,
)

from . import contract, team_policy

_JOBS = contract.RouteSpec(
    "orchestration.jobs", "GET", r"/orchestration/jobs", mutates=False
)
_SUBMIT = contract.RouteSpec(
    "orchestration.submit", "POST", r"/orchestration/jobs", mutates=True
)
_JOB = contract.RouteSpec(
    "orchestration.job", "GET", r"/orchestration/jobs/([^/]+)", mutates=False
)
_CANCEL = contract.RouteSpec(
    "orchestration.cancel",
    "POST",
    r"/orchestration/jobs/([^/]+)/cancel",
    mutates=True,
)
_LOGS = contract.RouteSpec(
    "orchestration.logs", "GET", r"/orchestration/jobs/([^/]+)/logs", mutates=False
)
_PROFILES = contract.RouteSpec(
    "orchestration.profiles", "GET", r"/orchestration/profiles", mutates=False
)

ROUTES = contract.validate_routes((_CANCEL, _LOGS, _JOB, _JOBS, _SUBMIT, _PROFILES))

_PATH_PREFIX = "/orchestration"

#: The profile a submission gets when it names none -- and, until
#: cluster.toml defines profiles, the only one there is.
DEFAULT_PROFILE = "cpu-interactive"

#: Where an operator describes the site; named in refusals because "unknown"
#: alone sent people looking for a typo in a name that could never work.
_CLUSTER_TOML_HINT = "<data_dir>/cluster.toml (see docs/team-server.md)"

#: How much of a log file the tail route will return. A batch job can write
#: gigabytes; a browser asking "how is it going" wants the end.
MAX_LOG_TAIL_BYTES = 64 * 1024


def _identity(self):
    return getattr(self, "_team_identity", None)


def _owner_id(self) -> str:
    identity = _identity(self)
    return identity.user_id if identity is not None else "local"


def _may_see(self, workload: Any) -> bool:
    identity = _identity(self)
    if identity is None:  # team mode off: single user, everything is theirs
        return True
    if identity.is_admin:
        return True
    return workload.owner_user_id == identity.user_id


#: Backends that launch work with daemon-controlled identity or credentials.
#:
#: `LocalBackend` runs `Popen(argv)` as the daemon's uid, outside the kernel
#: sandbox, with the daemon's filesystem access -- which in team mode is every
#: tenant's sessions, the access-token file and the group LLM credential.
#: `SlurmBackend` invokes the scheduler with the daemon's Unix identity and
#: site credential; OpenAI4S has no authenticated member-to-scheduler-account
#: mapping, so a browser member is not thereby submitting as themselves. Both
#: can read or spend instance resources and are operator actions in team mode.
#:
#: Deliberately not solved by wrapping `LocalBackend` in the kernel sandbox.
#: The sandbox degrades visibly rather than failing closed on hosts that
#: cannot give it namespaces, and a privilege boundary that sometimes is not
#: there is worse than a refusal. Member-submitted local work wants its own
#: fail-closed `local-sandboxed` backend, designed as such.
PRIVILEGED_BACKENDS = frozenset({"cluster", "local"})


def backend_for(runner: Any, body: Any) -> str:
    """Which backend a submission names, defaults resolved the same way the
    handler resolves them -- so the guard and the execution cannot disagree
    about what was asked for."""
    return str(body.get("backend") or getattr(runner, "default_backend", "local"))


def _may_submit_to(self, backend: str) -> bool:
    identity = _identity(self)
    if identity is None or identity.is_admin:
        # Team mode off (the single-user daemon is its own operator), or an
        # operator. Both keep the behaviour they have always had (INV-1).
        return True
    return backend not in PRIVILEGED_BACKENDS


def _job_json(workload: Any, allocation: Any = None, exit_code: Any = None) -> dict:
    """The wire shape. `allocation_id` is the public identity of a running
    attempt; the backend's own job id stays inside (INV-2). `exit_code` is
    the finished attempt's status, when its backend reported one."""
    payload = {
        "id": workload.id,
        "kind": workload.spec.kind.value,
        "profile": workload.spec.profile.name,
        "command": list(workload.spec.command),
        "phase": workload.phase.value,
        "desired_state": workload.desired_state.value,
        "reason": workload.reason.value if workload.reason else None,
        "execution_epoch": workload.execution_epoch,
        "owner_user_id": workload.owner_user_id,
        "project_id": workload.project_id,
        "backend": workload.backend or "local",
        "created_at": getattr(workload, "created_at", None),
        "updated_at": getattr(workload, "updated_at", None),
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if allocation is not None:
        payload["allocation"] = {
            "allocation_id": allocation.id,
            "epoch": allocation.epoch,
            "phase": allocation.phase.value,
            "reason": allocation.reason.value if allocation.reason else None,
        }
    return payload


def _exit_code(store: Any, workload: Any) -> int | None:
    """The exit status of a finished job's last attempt, if one was seen.

    A FAILED job with no reason and empty logs said nothing at all about why;
    the status the backend observed is the first thing to look at.
    """
    if not workload.phase.is_terminal:
        return None
    allocations = store.workloads.list_allocations(workload.id)
    if not allocations:
        return None
    code = (allocations[-1].diagnostics or {}).get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def handle(
    self,
    method: str,
    sub: str,
    q: dict,
    store: Any,
    runner: Any,
) -> bool:
    """Answer an orchestration route, or report it is not ours."""
    if not sub.startswith(_PATH_PREFIX):
        return False

    if _PROFILES.match(method, sub):
        cluster = getattr(runner, "cluster_config", None)
        self._json(
            cluster.public()
            if cluster is not None
            else {"cluster": "", "configured": False, "profiles": []}
        )
        return True

    m = _CANCEL.match(method, sub)
    if m:
        workload = store.workloads.get_workload(m.group(1))
        if workload is None or not _may_see(self, workload):
            self._json({"error": "job not found"}, 404)
            return True
        identity = _identity(self)
        reason = (
            Reason.ADMIN_CANCELLED
            if identity is not None
            and identity.is_admin
            and workload.owner_user_id != identity.user_id
            else Reason.USER_CANCELLED
        )
        # Recorded, not executed: the reconciler owns the cancel barrier, so
        # a cancel survives a restart and cannot half-happen in a request
        # thread that goes away.
        changed = store.workloads.request_stop(workload.id, reason=reason)
        if not changed:
            self._json(
                {"ok": False, "phase": workload.phase.value, "code": "already_final"},
                409,
            )
            return True
        try:
            store.team.audit(
                actor=identity.username if identity else "local",
                action="workload_cancel",
                user_id=workload.owner_user_id,
                project_id=workload.project_id,
                target=workload.id,
            )
        except Exception:  # noqa: BLE001 — auditing must not fail the request
            pass
        self._json({"ok": True, "id": workload.id, "reason": reason.value})
        return True

    m = _LOGS.match(method, sub)
    if m:
        workload = store.workloads.get_workload(m.group(1))
        if workload is None or not _may_see(self, workload):
            self._json({"error": "job not found"}, 404)
            return True
        allocation = store.workloads.active_allocation(workload.id)
        allocations = store.workloads.list_allocations(workload.id)
        target = allocation or (allocations[-1] if allocations else None)
        tail = {"stdout": "", "stderr": "", "allocation_id": None}
        if target is not None:
            tail["allocation_id"] = target.id
            backends = getattr(runner, "orchestration_backends", {}) or {}
            backend = backends.get(workload.backend or "local")
            log_paths = getattr(backend, "log_paths", None)
            if callable(log_paths):
                out_path, err_path = log_paths(target.id)
                tail["stdout"] = _tail(out_path)
                tail["stderr"] = _tail(err_path)
        self._json(tail)
        return True

    m = _JOB.match(method, sub)
    if m:
        workload = store.workloads.get_workload(m.group(1))
        if workload is None or not _may_see(self, workload):
            self._json({"error": "job not found"}, 404)
            return True
        allocation = store.workloads.active_allocation(workload.id)
        payload = _job_json(workload, allocation, _exit_code(store, workload))
        payload["allocations"] = [
            {
                "allocation_id": a.id,
                "epoch": a.epoch,
                "phase": a.phase.value,
                "reason": a.reason.value if a.reason else None,
            }
            for a in store.workloads.list_allocations(workload.id)
        ]
        self._json(payload)
        return True

    if _JOBS.match(method, sub):
        identity = _identity(self)
        owner = None
        if identity is not None and not identity.is_admin:
            owner = identity.user_id
        try:
            limit = contract.int_param(
                q.get("limit"), 100, name="limit", minimum=1, maximum=500
            )
        except contract.QueryParamError as exc:
            self._json({"error": str(exc), "code": exc.code}, 400)
            return True
        workloads = store.workloads.list_workloads(
            owner_user_id=owner,
            project_id=(q.get("project_id") or [None])[0],
            include_terminal=(q.get("all") or ["1"])[0] not in ("0", "false"),
            limit=limit,
        )
        self._json(
            {
                "jobs": [
                    _job_json(
                        w,
                        store.workloads.active_allocation(w.id),
                        _exit_code(store, w),
                    )
                    for w in workloads
                ]
            }
        )
        return True

    if _SUBMIT.match(method, sub):
        body = self._body()
        command = body.get("command")
        if isinstance(command, str):
            # A string would have to be split by something, and splitting a
            # command line is exactly where quoting bugs become injection.
            self._json(
                {
                    "error": "command must be a list of arguments, not a string",
                    "code": "invalid_command",
                },
                400,
            )
            return True
        if not command or not isinstance(command, list):
            self._json({"error": "command is required", "code": "invalid_command"}, 400)
            return True

        profile_name = str(body.get("profile") or DEFAULT_PROFILE)
        cluster = getattr(runner, "cluster_config", None)
        profile: ResourceProfile | None = None
        if cluster is not None and cluster.configured:
            try:
                profile = cluster.profile(profile_name).resources
            except Exception as exc:  # noqa: BLE001 — unknown profile is a 400
                self._json({"error": str(exc), "code": "unknown_profile"}, 400)
                return True
        if profile is None:
            if profile_name != DEFAULT_PROFILE:
                # Accepted, this ran on the local backend and was recorded
                # under a profile that exists nowhere.
                self._json(
                    {
                        "error": (
                            f"unknown profile {profile_name!r}: no usable "
                            f"cluster.toml is configured, so the only profile is "
                            f"{DEFAULT_PROFILE!r}; define profiles in "
                            f"{_CLUSTER_TOML_HINT}"
                        ),
                        "code": "unknown_profile",
                    },
                    400,
                )
                return True
            profile = ResourceProfile(name=profile_name)

        try:
            spec = WorkloadSpec(
                kind=WorkloadKind.BATCH,
                profile=profile,
                command=tuple(str(part) for part in command),
                workdir=body.get("workdir") or None,
                environment={
                    str(k): str(v) for k, v in (body.get("environment") or {}).items()
                },
            )
        except ValueError as exc:
            self._json({"error": str(exc), "code": "invalid_spec"}, 400)
            return True

        backend = backend_for(runner, body)
        available = getattr(runner, "orchestration_backends", {}) or {}
        if available and backend not in available:
            self._json(
                {
                    "error": (
                        "the cluster backend is not configured: describe the "
                        f"site in {_CLUSTER_TOML_HINT}; until then jobs run "
                        "on the local backend"
                        if backend == "cluster"
                        else f"unknown backend {backend!r}"
                    ),
                    "code": "unknown_backend",
                    "available": sorted(available),
                },
                400,
            )
            return True
        # After the name is known to be real, so an unknown backend still
        # reads as a typo rather than as a refusal; before anything is
        # written, so a refused submission leaves no workload behind.
        if not _may_submit_to(self, backend):
            self._json(
                {
                    "error": (
                        f"the {backend!r} backend launches work with "
                        f"daemon-managed identity or credentials and is admin only"
                    ),
                    "code": "admin_only",
                    "backend": backend,
                },
                403,
            )
            return True

        # The project is caller-supplied and lands in the workload row, the
        # audit trail and the project-scoped usage reporting. The sibling
        # write -- creating a session -- checks participation for exactly this
        # reason; this one did not, so a member could file a job into another
        # team's project and write into their audit log.
        submit_project = body.get("project_id") or None
        if submit_project and not team_policy.may_read_project(
            store, _identity(self), str(submit_project)
        ):
            self._json({"error": "project not found", "code": "not_found"}, 404)
            return True
        workload = store.workloads.create_workload(
            spec=spec,
            owner_user_id=_owner_id(self),
            project_id=submit_project,
            backend=backend,
        )
        try:
            identity = _identity(self)
            store.team.audit(
                actor=identity.username if identity else "local",
                action="workload_submit",
                user_id=workload.owner_user_id,
                project_id=workload.project_id,
                target=workload.id,
                detail=f"profile={profile_name} backend={backend}",
            )
        except Exception:  # noqa: BLE001
            pass
        # The loop is started on demand, so a daemon that never runs a batch
        # job never polls for one.
        ensure = getattr(runner, "ensure_reconciler", None)
        if callable(ensure):
            ensure()
        # 202: accepted, not started. The reconciler submits on its next
        # tick, and saying "created" here would promise a resource we have
        # not been granted.
        self._json(_job_json(workload), 202)
        return True
    return False


def _tail(path: Any) -> str:
    """The last MAX_LOG_TAIL_BYTES of a file, or ''."""
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_TAIL_BYTES))
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace")


__all__ = ["MAX_LOG_TAIL_BYTES", "ROUTES", "handle"]
