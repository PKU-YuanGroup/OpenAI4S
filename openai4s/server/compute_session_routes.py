"""Where a session's kernel runs, and whether it is ready yet (M3b-6).

Three routes, same shape as the other groups: a validated `RouteSpec`
table shared with the contract inventory and a tri-state `handle()`.

The response these exist for is `readiness`. A session on a cluster is not
one boolean but four conditions (INV-5), and the UI needs to say *which*
one is outstanding — "queued for a node", "waiting for the worker to dial
in" and "starting the kernel" are three different waits with three
different expected durations, and a single spinner for all of them is how
a user decides the product is broken.

`state_lost_epochs` is the other reason. When a node dies the kernel's
memory dies with it: variables, imports, the seed somebody set three cells
ago. The session continues on a new epoch, and the UI must say so (INV-11)
— a banner, not a log line — because the results afterwards look exactly
like the results from the session that was lost.

Nothing here names a scheduler. A run location is `local` or a profile
name from cluster.toml, and what the response carries is `allocation_id`.
"""

from __future__ import annotations

from typing import Any

from openai4s.orchestration.models import Reason, ResourceProfile

from . import contract

_STATUS = contract.RouteSpec(
    "session.compute", "GET", r"/sessions/([^/]+)/compute", mutates=False
)
_REQUEST = contract.RouteSpec(
    "session.compute.request", "POST", r"/sessions/([^/]+)/compute", mutates=True
)
_RELEASE = contract.RouteSpec(
    "session.compute.release",
    "POST",
    r"/sessions/([^/]+)/compute/release",
    mutates=True,
)

ROUTES = contract.validate_routes((_RELEASE, _REQUEST, _STATUS))

#: Hoisted rather than inlined at the `startswith` below. The contract
#: scanner treats a string literal handed to `startswith` as a route
#: declaration, so an inline prefix check publishes a bare prefix as an
#: endpoint that does not exist. Same trap the team-scope guards hit; same
#: fix. (Writing that sentence with an example in it triggered it once
#: more, which is a fair measure of how easy the mistake is.)
_PATH_PREFIX = "/sessions/"


def _identity(self):
    return getattr(self, "_team_identity", None)


def _may_use(self, session_id: str, store: Any) -> bool:
    """The same rule the rest of the session surface uses, called rather
    than reimplemented — a second copy of a visibility rule is a second
    place for it to be wrong."""
    identity = _identity(self)
    if identity is None:  # team mode off: single user, everything is theirs
        return True
    try:
        return bool(
            store.team.session_visible_to(session_id, self._team_identity_dict())
        )
    except Exception:  # noqa: BLE001 — undecidable visibility fails closed
        return False


def _status_json(manager: Any, store: Any, session_id: str) -> dict:
    workload_id = store.leases.workload_for_session(session_id)
    if not workload_id:
        # Not an error: "this session runs on the daemon" is the default and
        # the honest answer, not a missing resource.
        return {"session_id": session_id, "location": "local", "workload": None}

    workload = store.workloads.get_workload(workload_id)
    allocation = store.workloads.active_allocation(workload_id)
    readiness = manager.readiness(session_id)
    runtime = manager.runtime(session_id)
    lease = store.leases.get(workload_id)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "location": "cluster",
        "readiness": readiness.public(),
        "state_lost_epochs": list(runtime.state_lost_epochs) if runtime else [],
        "workload": None,
        "allocation": None,
        "lease": None,
    }
    if workload is not None:
        payload["workload"] = {
            "id": workload.id,
            "profile": workload.spec.profile.name,
            "phase": workload.phase.value,
            "desired_state": workload.desired_state.value,
            "reason": workload.reason.value if workload.reason else None,
            "execution_epoch": workload.execution_epoch,
        }
    if allocation is not None:
        payload["allocation"] = {
            "allocation_id": allocation.id,
            "epoch": allocation.epoch,
            "phase": allocation.phase.value,
            "reason": allocation.reason.value if allocation.reason else None,
        }
    if lease is not None:
        payload["lease"] = {
            "idle_ttl_s": lease.idle_ttl_s,
            "max_lifetime_s": lease.max_lifetime_s,
            "last_active_at": lease.last_active_at,
            "created_at": lease.created_at,
            "released_at": lease.released_at,
        }
    return payload


def handle(
    self,
    method: str,
    sub: str,
    q: dict,
    store: Any,
    runner: Any,
) -> bool:
    """Answer a session-compute route, or report it is not ours."""
    if not sub.startswith(_PATH_PREFIX):
        return False

    manager = getattr(runner, "compute_sessions", None)

    m = _STATUS.match(method, sub)
    if m:
        session_id = m.group(1)
        if not _may_use(self, session_id, store):
            self._json({"error": "session not found"}, 404)
            return True
        if manager is None:
            self._json(
                {"session_id": session_id, "location": "local", "workload": None}
            )
            return True
        self._json(_status_json(manager, store, session_id))
        return True

    m = _RELEASE.match(method, sub)
    if m:
        session_id = m.group(1)
        if not _may_use(self, session_id, store):
            self._json({"error": "session not found"}, 404)
            return True
        if manager is None:
            self._json({"ok": False, "code": "not_configured"}, 409)
            return True
        identity = _identity(self)
        released = manager.release(session_id, reason=Reason.USER_CANCELLED)
        try:
            store.team.audit(
                actor=identity.username if identity else "local",
                action="session_compute_release",
                user_id=identity.user_id if identity else None,
                target=session_id,
            )
        except Exception:  # noqa: BLE001 — auditing must not fail the request
            pass
        self._json({"ok": bool(released), "session_id": session_id})
        return True

    m = _REQUEST.match(method, sub)
    if m:
        session_id = m.group(1)
        if not _may_use(self, session_id, store):
            self._json({"error": "session not found"}, 404)
            return True
        if manager is None:
            self._json(
                {
                    "ok": False,
                    "code": "not_configured",
                    "error": (
                        "this daemon has no worker listener; set "
                        "OPENAI4S_WORKER_LISTEN to run sessions on a cluster"
                    ),
                },
                409,
            )
            return True
        body = self._body() or {}
        profile_name = str(body.get("profile") or "").strip()
        if not profile_name:
            self._json({"ok": False, "error": "profile is required"}, 400)
            return True

        cluster = getattr(runner, "cluster_config", None)
        try:
            site = cluster.profile(profile_name) if cluster is not None else None
        except Exception:
            site = None
        if site is None:
            # A profile the operator has not configured is a typo, not a
            # request to invent resource limits: guessing here is how a
            # session quietly lands on the wrong queue.
            self._json(
                {"ok": False, "code": "unknown_profile", "error": profile_name}, 400
            )
            return True
        profile: ResourceProfile = site.resources

        identity = _identity(self)
        workload = manager.request_session(
            session_id=session_id,
            owner_user_id=identity.user_id if identity else "local",
            project_id=getattr(identity, "project_id", None),
            profile=profile,
            backend=getattr(runner, "cluster_backend_name", "cluster"),
        )
        ensure = getattr(runner, "ensure_reconciler", None)
        if callable(ensure):
            ensure()
        try:
            store.team.audit(
                actor=identity.username if identity else "local",
                action="session_compute_request",
                user_id=identity.user_id if identity else None,
                target=f"{session_id}:{workload.id}",
            )
        except Exception:  # noqa: BLE001
            pass
        self._json(_status_json(manager, store, session_id), 201)
        return True

    return False


__all__ = ["ROUTES", "handle"]
