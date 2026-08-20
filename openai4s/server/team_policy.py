"""Who may touch what, in one place (M2/M4 hardening).

An external review named the real problem with the first version of team
mode: authorization was "does this path match one of two regexes", so
every surface that is not `/frames/{id}` or `/projects/{id}/…` was still
the single-user API. Five separate isolation defects came out of that one
shape — a project self-join, global share administration, project-scoped
memory reached by query parameter, a cross-user file clobber, and an
instance-global LLM config any member could rewrite.

Point-fixing five call sites would reproduce the cause. What is here
instead is the *decisions*, as functions over resources rather than over
URLs: may this identity administer this project, use this share, read
this memory scope, write this file, change instance-global configuration.
The call sites still have to ask — nothing can force a new route to — but
there is now one model to read, one place a rule changes, and one file a
reviewer can hold the whole policy in their head from.

Two properties every predicate here keeps:

**Team mode off is not a policy question.** Every entry point returns the
permissive answer immediately when there is no identity, so a single-user
install runs the code it always ran (INV-1). The guards are not "disabled"
in that mode; they are not reached.

**Undecidable means refused.** A lookup that raises is not an open door.
The alternative — treating an unreadable ownership row as unrestricted —
is how a version-addressed artifact walked past this product's checks
once already.
"""

from __future__ import annotations

from typing import Any

#: Memory scopes that are not a project: the whole-instance tiers. A member
#: writing here writes standing context into every other user's turns, which
#: is an operator's decision and not a member's. Imported rather than spelled
#: out, so a rename in the repository cannot silently open the tier.
from openai4s.storage.memories import ALL_PROJECTS as _ALL_PROJECTS
from openai4s.storage.memories import GLOBAL_SCOPE as _GLOBAL_SCOPE

GLOBAL_MEMORY_SCOPES = frozenset({_ALL_PROJECTS, _GLOBAL_SCOPE})


def _identity(handler: Any) -> Any:
    return getattr(handler, "_team_identity", None)


def enabled(handler: Any) -> bool:
    """Is team mode actually in force for this request?

    The single question every predicate asks first. `None` identity means
    either team mode is off or this is the loopback service path, and both
    take the pre-team behaviour by design.
    """
    return _identity(handler) is not None


def is_admin(handler: Any) -> bool:
    identity = _identity(handler)
    return bool(identity is not None and identity.is_admin)


# --- projects ---------------------------------------------------------------


def may_read_project(store: Any, identity: Any, project_id: str | None) -> bool:
    """Participation: a membership row, or owning a session in the project."""
    if identity is None or identity.is_admin:
        return True
    if not project_id:
        return False
    try:
        return bool(
            store.governance.is_project_participant(project_id, identity.user_id)
        )
    except Exception:  # noqa: BLE001 — undecidable is refused
        return False


def may_administer_project(store: Any, identity: Any, project_id: str | None) -> bool:
    """Rename, rewrite the agent context, or delete.

    Deliberately *not* participation. Participation is a union that
    includes "owns a session here", and creating a session is something
    any member can do in any project they can name — so the union is a
    self-join primitive, and handing it a DELETE that takes every member's
    sessions, artifacts and workspaces with it is how one member erases
    another team's work. Administration requires a real membership row.
    """
    if identity is None or identity.is_admin:
        return True
    if not project_id:
        return False
    try:
        role = store.governance.member_role(project_id, identity.user_id)
    except Exception:  # noqa: BLE001
        return False
    return role is not None and str(role) != "guest"


def may_create_session_in(store: Any, identity: Any, project_id: str | None) -> bool:
    """Creating a session is also *joining*, so it needs authorizing.

    An unclaimed project — no members and no other user's sessions — stays
    open, which is what keeps a fresh install's seeded `default` project
    usable before anybody has organised anything. A project somebody else
    has claimed needs participation.
    """
    if identity is None or identity.is_admin:
        return True
    if not project_id:
        return False
    if may_read_project(store, identity, project_id):
        return True
    try:
        return not store.governance.project_is_claimed(project_id)
    except Exception:  # noqa: BLE001
        return False


# --- sessions, shares, memory ----------------------------------------------


def may_use_session(store: Any, identity: Any, session_id: str | None) -> bool:
    if identity is None:
        return True
    if not session_id:
        return False
    try:
        return bool(store.team.session_visible_to(session_id, _as_dict(identity)))
    except Exception:  # noqa: BLE001
        return False


def may_use_share(store: Any, identity: Any, share_row: Any) -> bool:
    """A share is a projection of a session, so it inherits that session's
    answer. A share whose session cannot be resolved is refused rather than
    treated as public — the whole point of a share is that publishing it
    was a decision, and an unresolvable one records no such decision."""
    if identity is None:
        return True
    if not share_row:
        return False
    root = share_row.get("root_frame_id") if hasattr(share_row, "get") else None
    if not root:
        return False
    return may_use_session(store, identity, str(root))


def may_use_memory_scope(store: Any, identity: Any, scope: str | None) -> bool:
    """Standing context is injected into turns, so a write here is a write
    into somebody else's prompts. The instance-wide tiers are the
    operator's."""
    if identity is None or identity.is_admin:
        return True
    if not scope:
        return False
    if scope in GLOBAL_MEMORY_SCOPES:
        return False
    return may_read_project(store, identity, scope)


# --- instance-global configuration ------------------------------------------

#: Config surfaces that belong to the whole instance rather than to a user:
#: the provider, its endpoint and its credential, and the model profiles that
#: carry the same. Enumerated rather than prefix-matched so that a new one is
#: a deliberate addition to this list and not an accident of its URL.
INSTANCE_CONFIG_PATHS = frozenset(
    {
        "/config/llm",
        "/model-profiles",
        "/models/default",
        "/search/config",
        "/datapro/config",
        "/doubao-search/config",
        "/telemetry/consent",
        # Three that enumeration missed, all writing a process-global or
        # instance-global switch rather than anything owned:
        #   `/network/status` sets `os.environ["OPENAI4S_ALLOW_NETWORK"]` in
        #     the daemon every member's kernel runs inside, so one member
        #     turns egress on (or off) for everyone;
        #   `/share/settings` opens the outbound relay tunnel for the whole
        #     daemon, which publishes this instance to the internet;
        #   `/memory/enabled` switches standing-context injection for every
        #     user's turns.
        "/network/status",
        "/share/settings",
        "/memory/enabled",
    }
)

#: Prefixes under which every addressed resource is instance-global too.
INSTANCE_CONFIG_PREFIXES = ("/model-profiles/",)

#: Daemon-level operations: things done *as the daemon* on the machine it
#: runs on, with no session and no owner. Admin-only in every verb, reads
#: included, because the read is another member's data too -- a compute
#: job's row carries the command line somebody typed.
#:
#: - `/compute/jobs`: the legacy JobManager runs `bash -c <command>` as the
#:   daemon's own uid, unsandboxed. In a single-user install that is the
#:   user's own machine; in team mode it is arbitrary command execution as
#:   the daemon for every member. This is the finding that put this table
#:   here.
DAEMON_OPERATION_PATHS = frozenset({"/compute/jobs"})
DAEMON_OPERATION_PREFIXES = ("/compute/jobs/",)

#: Instance-global mutations beyond configuration: registering a remote
#: compute provider (credentials, code on another host), installing
#: packages into the venv every kernel shares, configuring a connector that
#: carries the group's credentials, publishing a skill into the directory
#: every member's agent loads recipes from, and resetting standing
#: permission rules. Members keep the reads and the *uses* (a connector
#: `/call` or `/probe` is using what an admin set up); what they must not
#: have is the write.
INSTANCE_MUTATION_PATHS = frozenset(
    {
        "/compute/remote",
        "/kernel/install",
        "/connectors",
        "/skills",
        "/skills/import",
        "/permissions/reset",
        # A specialist is a named agent profile: a system prompt, a skill
        # list, a connector list and an `unrestricted` flag, stored globally
        # and offered to every member's agent selection. `upsert_agent`
        # writes all of it from the request body and defaults `unrestricted`
        # to True, so a member could persist a prompt -- and a capability
        # posture -- that later runs inside somebody else's turn. Same class
        # as `/skills`, and it was missing for the same reason: the policy
        # enumerated the surfaces somebody remembered.
        "/specialists",
    }
)
#: `/skills/` and `/agents/` are prefixes, not bare paths, because the
#: authoring surface is addressed by name while the bare-path entries covered
#: only the collection. `POST /skills` was refused while
#: `PUT /skills/<existing-name>` rewrote the recipe body every member's agent
#: loads and executes -- and `skill_customization` is one process-wide
#: instance writing at global scope, so there is no per-user variant of that
#: write to fall back on. Every non-GET route under either prefix
#: (`{name}`, `{name}/rollback`, `catalog/{name}/enabled`,
#: `agents/{name}/enabled`) writes instance-global state; the reads stay open.
INSTANCE_MUTATION_PREFIXES = (
    "/compute/remote/",
    "/skills/",
    "/agents/",
    "/specialists/",
)
#: `/connectors/{id}` and `/connectors/{id}/enabled` are configuration;
#: `/connectors/{id}/call` and `/probe` are use. Enumerated by suffix so the
#: distinction is a rule and not an accident of prefix length.
CONNECTOR_USE_SUFFIXES = ("/call", "/probe")


def is_instance_config(method: str, path: str) -> bool:
    """Only mutating verbs. Members keep the reads the UI needs to render
    which model is active; what they must not have is the write."""
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    if path in INSTANCE_CONFIG_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in INSTANCE_CONFIG_PREFIXES)


def is_daemon_operation(method: str, path: str) -> bool:
    """Every verb: the surface has no owner to scope a read to."""
    if path in DAEMON_OPERATION_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in DAEMON_OPERATION_PREFIXES)


def is_instance_mutation(method: str, path: str) -> bool:
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    if path in INSTANCE_MUTATION_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in INSTANCE_MUTATION_PREFIXES):
        return True
    if path.startswith("/connectors/"):
        return not path.endswith(CONNECTOR_USE_SUFFIXES)
    return False


def is_admin_only_surface(method: str, path: str) -> bool:
    """The union a route guard asks about."""
    return (
        is_instance_config(method, path)
        or is_daemon_operation(method, path)
        or is_instance_mutation(method, path)
    )


def may_change_instance_config(handler: Any) -> bool:
    """Rewriting `llm_base_url` points every user's traffic at a host of the
    writer's choosing, which delivers everyone's prompts and the group
    credential in the outgoing header. That is an operator's action."""
    return not enabled(handler) or is_admin(handler)


# --- permission rules -------------------------------------------------------


def may_write_permission_rule(
    store: Any, identity: Any, scope: str, scope_id: str | None
) -> bool:
    """May this identity create or delete a standing permission rule here?

    A standing rule is authorization for *future* actions, and a global one
    is authorization for everybody's -- `permissions.resolve()` treats a
    matching global `allow` as an answer for every other member's agent, and
    a matching global `deny` as an absolute veto that deleting turns back
    into an allow.

    A function rather than two inlined copies in the REST handlers, because
    it was two inlined copies and there are four writers. The two that were
    guarded are `POST /permissions` and `DELETE /permissions/{id}`; the two
    that were not are in `openai4s/permissions.py`, reached from
    `POST /frames/{id}/decision` -- a route every member legitimately uses,
    which passes the client's own `scope` straight through. So a member
    approving a prompt in their own session with `{"scope": "global"}`
    planted exactly the rule the 403 next door refuses them.
    """
    if identity is None or identity.is_admin:
        return True
    if scope == "global":
        return False
    if scope == "project":
        return may_read_project(store, identity, str(scope_id or ""))
    if scope == "conversation":
        return may_use_session(store, identity, str(scope_id or ""))
    # "once" is not a standing rule at all.
    return True


# --- files ------------------------------------------------------------------


def may_overwrite_file(identity: Any, owner_username: str | None) -> bool:
    """Containment inside `data_roots` says where a file may live, not whose
    it is. Overwriting is the destructive verb, so it is the one that needs
    an owner.

    A *username*, because that is what the file area is keyed by:
    `<root>/users/<username>/` is the whole ownership record a path carries,
    and `FileArea.personal_area_owner` returns that segment. The parameter
    used to be spelled `owner_user_id` and compared against
    `identity.user_id`, which no caller could ever have satisfied -- a member
    would have been refused their own file. It had no callers, so nothing
    said so.
    """
    if identity is None or identity.is_admin:
        return True
    if not owner_username:
        # No recorded owner: shared space, pre-team files, anything written
        # outside the team surface. Refuse rather than adopt — "we do not
        # know whose this is" must not resolve to "anyone's", which is the
        # same fail-closed rule an unowned session already gets.
        return False
    return str(owner_username) == str(identity.username)


def _as_dict(identity: Any) -> dict[str, Any]:
    """The shape `TeamRepository` reads a user in.

    The key is `id`, not `user_id`. Writing the obvious one made every
    ownership comparison in `session_visible_to` fail, so every share --
    including the caller's own -- came back invisible: fail-closed, and
    still wrong. A predicate that refuses everything looks exactly like a
    working guard from the outside, which is why the test that caught it
    checks the owner still has access rather than only that the stranger
    does not.
    """
    return {
        "id": getattr(identity, "user_id", ""),
        "username": getattr(identity, "username", ""),
        "role": getattr(identity, "role", ""),
        "kind": getattr(identity, "kind", "user"),
    }


__all__ = [
    "CONNECTOR_USE_SUFFIXES",
    "DAEMON_OPERATION_PATHS",
    "DAEMON_OPERATION_PREFIXES",
    "GLOBAL_MEMORY_SCOPES",
    "INSTANCE_CONFIG_PATHS",
    "INSTANCE_CONFIG_PREFIXES",
    "INSTANCE_MUTATION_PATHS",
    "INSTANCE_MUTATION_PREFIXES",
    "enabled",
    "is_admin",
    "is_admin_only_surface",
    "is_daemon_operation",
    "is_instance_config",
    "is_instance_mutation",
    "may_administer_project",
    "may_change_instance_config",
    "may_create_session_in",
    "may_overwrite_file",
    "may_write_permission_rule",
    "may_read_project",
    "may_use_memory_scope",
    "may_use_session",
    "may_use_share",
]
