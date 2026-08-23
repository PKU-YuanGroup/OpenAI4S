"""Who a unit of work is running *as*, carried with the execution.

Team mode answers "who is this request?" at the HTTP boundary and then
loses the answer. Everything past that boundary — the turn thread, the
kernel RPC, a delegated sub-agent — runs with no idea whose it is, so the
one surface that needed it read every tenant's data: `host.frames` reads
frames, cell code and stdout through ordinary Store methods, and the
team-mode scoping lives in the SQLite authorizer that only `host.query`
goes through. A guard on one of two doors into the same rows.

The fix is not a `visible_to_user_id=` parameter threaded through the
Store API by hand. That is the same defect with more call sites: every
future reader is one forgotten argument away from the leak, and the
argument's absence reads as "unscoped" rather than as "refused".

So: a `Principal` travels with the execution, in a `ContextVar`, the way
`correlation_id` already does. Three properties make it a boundary rather
than a convenience.

**Absent is refused, not trusted.** `resolve()` raises in team mode when
nothing set a principal. The tempting shape — `if principal is None:
return everything` — is how an unowned row became everyone's row twice
already in this codebase.

**`None` is never an operator.** The single-user daemon and the loopback
CLI get *explicit* principals (`SINGLE_USER`, `SERVICE`) that say so.
Reading a missing value as "no restrictions" is the same mistake in a
different spelling.

**It is not stored on anything reused.** `HostDispatcher` is built once
per session and serves every turn in it, so a `dispatcher.user` field
would be a mutable authorization input shared across turns — the shape
where one turn's identity answers another turn's question. The value
lives in the context of the execution and dies with it.

Crossing a thread needs `observability.carry_context`, exactly as the
correlation id does; `ContextVar` does not follow a bare
`threading.Thread`.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Any, Iterator

__all__ = [
    "PrincipalRequired",
    "Principal",
    "SERVICE",
    "SINGLE_USER",
    "current",
    "for_identity",
    "reset",
    "resolve",
    "scope",
    "set_principal",
    "team_mode_active",
]


class PrincipalRequired(PermissionError):
    """Team mode is on and nothing said who this execution is running as.

    A `PermissionError` rather than a `RuntimeError` because that is what
    it is: the answer to "may this code read that row" is no, and callers
    that already refuse on permission grounds get it right by default.
    """


@dataclass(frozen=True)
class Principal:
    """The identity a unit of work runs as.

    Frozen: an authorization input that a later frame can edit is not an
    authorization input. A turn that needs a different identity makes a
    different principal.
    """

    user_id: str
    username: str
    role: str
    #: "user" — a logged-in member; "service" — the loopback management CLI;
    #: "single_user" — a daemon with no team mode, whose operator is the
    #: person who started it. Spelled out so that "admin" is never inferred
    #: from a missing value.
    kind: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.kind in ("service", "single_user") or self.role == "admin"

    @property
    def unrestricted(self) -> bool:
        """Whether row-level scoping applies at all.

        Distinct from `is_admin` on purpose. A team-mode *admin* is
        unrestricted but is still a person whose privileged reads are
        audited (D4); a single-user daemon has no one to be isolated from.
        Callers that need the audit ask `is_admin`; callers deciding
        whether to filter ask this.
        """
        return self.kind == "single_user" or self.is_admin

    def as_visibility_user(self) -> dict[str, Any]:
        """The shape `TeamRepository.session_visible_to` reads.

        The key is `id`, not `user_id`. Writing the obvious one made every
        ownership comparison fail, which looks exactly like a working
        guard from the outside.
        """
        return {
            "id": self.user_id,
            "username": self.username,
            "role": self.role,
            "kind": self.kind,
        }


#: The daemon nobody logged into. Its operator is whoever started it, so it
#: is unrestricted -- but it says so by being this value, not by being absent.
SINGLE_USER = Principal(user_id="", username="local", role="admin", kind="single_user")

#: The loopback management CLI (decision D2): admin-equivalent, and named
#: `cli` in the audit trail so the machine path and a human account stay
#: separable.
SERVICE = Principal(user_id="service:cli", username="cli", role="admin", kind="service")


_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "openai4s_execution_principal", default=None
)


def for_identity(identity: Any) -> Principal | None:
    """The principal a team-mode `TeamIdentity` runs as, or None.

    Called only from the team guard, i.e. only when team mode is *on*. So
    `None` here means "this request has no logged-in user" -- an exempt
    path like the login page -- and the answer is None, not `SINGLE_USER`.
    Mapping it to the unrestricted principal would hand every anonymous
    request the operator's reach, which is the "absent means allowed"
    mistake this module exists to refuse.

    A single-user daemon never reaches this function: it binds nothing, and
    `resolve()` supplies `SINGLE_USER` because team mode is off.
    """
    if identity is None:
        return None
    if getattr(identity, "kind", "user") == "service":
        return SERVICE
    return Principal(
        user_id=str(getattr(identity, "user_id", "")),
        username=str(getattr(identity, "username", "")),
        role=str(getattr(identity, "role", "")),
        kind=str(getattr(identity, "kind", "user")),
    )


def team_mode_active() -> bool:
    """Whether this daemon is multi-tenant.

    Routed through `config._env_flag` rather than re-implemented. A second,
    narrower truthiness rule for `OPENAI4S_TEAM_MODE` is precisely how the
    `host.query` guard came to be inactive on daemons that were otherwise
    fully in team mode.
    """
    from openai4s.config import _env_flag

    return _env_flag("OPENAI4S_TEAM_MODE", False)


def current() -> Principal | None:
    """The principal in force, or None. Callers making an authorization
    decision want `resolve()`; this is for logging and diagnostics."""
    return _principal.get()


def set_principal(principal: Principal | None) -> contextvars.Token:
    return _principal.set(principal)


def reset(token: contextvars.Token) -> None:
    _principal.reset(token)


@contextlib.contextmanager
def scope(principal: Principal | None) -> Iterator[Principal | None]:
    """Bind a principal for the duration of a block, then restore.

    Restoring matters on the request thread, which serves many requests:
    leaving the last caller's identity set is how the next one inherits it.
    """
    token = _principal.set(principal)
    try:
        yield principal
    finally:
        _principal.reset(token)


def resolve() -> Principal:
    """The principal to authorize against, or refuse.

    Team mode with nothing set is a bug in the propagation, and the only
    safe reading of a bug in the propagation is "no".
    """
    principal = _principal.get()
    if principal is not None:
        return principal
    if team_mode_active():
        raise PrincipalRequired(
            "this execution has no principal, so it cannot be authorized. "
            "In team mode every path that reaches user data must carry the "
            "identity it is running as."
        )
    return SINGLE_USER
