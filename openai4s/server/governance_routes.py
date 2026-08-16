"""Team governance routes: users, members, invites, usage, audit, quotas (M2).

Same shape as `team_routes`/`file_routes`: a validated `RouteSpec` table
shared with the contract inventory, and a tri-state `handle()`.

Every route here is **admin-only** (the single declared table below — plan
M1-5 asked for admin-only routes to be centrally declared, and this module
is that declaration for the `/team/*` namespace). With team mode off they
answer the stable `team_off` shape the contract capture freezes; with team
mode on, a non-admin gets 403 `admin_only` — these are management surfaces,
their existence is not a secret.

Raw invite tokens appear exactly once, in the POST /team/invites response;
listings carry digest prefixes only. Every mutation lands in the team audit
log (INV-12).
"""

from __future__ import annotations

from typing import Any

from . import contract
from .team_auth import public_user

_USERS = contract.RouteSpec("team.users", "GET", r"/team/users", mutates=False)
_USER_ADD = contract.RouteSpec("team.users.add", "POST", r"/team/users", mutates=True)
_USER_DISABLE = contract.RouteSpec(
    "team.users.disable", "POST", r"/team/users/([^/]+)/disable", mutates=True
)
_USER_RESET = contract.RouteSpec(
    "team.users.reset",
    "POST",
    r"/team/users/([^/]+)/reset-password",
    mutates=True,
)
_MEMBERS = contract.RouteSpec(
    "team.members", "GET", r"/team/projects/([^/]+)/members", mutates=False
)
_MEMBER_PUT = contract.RouteSpec(
    "team.members.put",
    "PUT",
    r"/team/projects/([^/]+)/members/([^/]+)",
    mutates=True,
)
_MEMBER_DELETE = contract.RouteSpec(
    "team.members.delete",
    "DELETE",
    r"/team/projects/([^/]+)/members/([^/]+)",
    mutates=True,
)
_INVITES = contract.RouteSpec("team.invites", "GET", r"/team/invites", mutates=False)
_INVITE_ADD = contract.RouteSpec(
    "team.invites.add", "POST", r"/team/invites", mutates=True
)
_INVITE_REVOKE = contract.RouteSpec(
    "team.invites.revoke", "DELETE", r"/team/invites/([^/]+)", mutates=True
)
_USAGE = contract.RouteSpec("team.usage", "GET", r"/team/usage", mutates=False)
_AUDIT = contract.RouteSpec("team.audit", "GET", r"/team/audit", mutates=False)
_QUOTAS = contract.RouteSpec("team.quotas", "GET", r"/team/quotas", mutates=False)
_QUOTA_SET = contract.RouteSpec(
    "team.quotas.set", "POST", r"/team/quotas", mutates=True
)
_QUOTA_DELETE = contract.RouteSpec(
    "team.quotas.delete", "DELETE", r"/team/quotas", mutates=True
)

ROUTES = contract.validate_routes(
    (
        _USER_DISABLE,
        _USER_RESET,
        _USERS,
        _USER_ADD,
        _MEMBER_PUT,
        _MEMBER_DELETE,
        _MEMBERS,
        _INVITE_REVOKE,
        _INVITES,
        _INVITE_ADD,
        _USAGE,
        _AUDIT,
        _QUOTAS,
        _QUOTA_SET,
        _QUOTA_DELETE,
    )
)

_PATH_PREFIX = "/team/"


def _deny(self, team_auth: Any) -> bool:
    """The two refusals every route here shares."""
    if team_auth is None:
        self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
        return True
    identity = getattr(self, "_team_identity", None)
    if identity is None or not identity.is_admin:
        self._json({"error": "admin only", "code": "admin_only"}, 403)
        return True
    return False


def _actor(self) -> str:
    identity = getattr(self, "_team_identity", None)
    return identity.username if identity is not None else "?"


def handle(self, method: str, sub: str, q: dict, team_auth: Any, store: Any) -> bool:
    """Answer a governance route, or report that this group does not own it."""
    if not sub.startswith(_PATH_PREFIX):
        return False
    if _deny(self, team_auth):
        return True

    m = _USER_DISABLE.match(method, sub)
    if m:
        user = store.team.get_user(m.group(1))
        if user is None:
            self._json({"error": "no such user"}, 404)
            return True
        store.team.set_disabled(user["id"], True)
        # Their own LLM credentials go with the account (M4-1). A key that
        # outlives the account it belongs to is a key nobody is watching,
        # and the row is the only thing that would ever have named its slot
        # again — leaving it behind makes the credential unreachable *and*
        # unrevocable, which is the worst of both.
        try:
            store.user_keys.delete_all_for_user(user["id"], secrets=store.secrets)
        except Exception:  # noqa: BLE001 — disabling must still land
            pass
        store.team.audit(
            actor=_actor(self),
            action="user_disable",
            user_id=user["id"],
            target=user["username"],
        )
        self._json({"ok": True, "user": public_user(user)})
        return True
    m = _USER_RESET.match(method, sub)
    if m:
        user = store.team.get_user(m.group(1))
        if user is None:
            self._json({"error": "no such user"}, 404)
            return True
        body = self._body()
        password = str(body.get("password") or "")
        generated = None
        if not password:
            import secrets as _secrets

            generated = _secrets.token_urlsafe(12)
            password = generated
        store.team.set_password(user["id"], password)
        store.team.audit(
            actor=_actor(self),
            action="user_reset_password",
            user_id=user["id"],
            target=user["username"],
        )
        payload: dict = {"ok": True}
        if generated is not None:
            # printed exactly once, never stored
            payload["password"] = generated
        self._json(payload)
        return True
    if _USERS.match(method, sub):
        self._json({"users": store.team.list_users()})
        return True
    if _USER_ADD.match(method, sub):
        body = self._body()
        try:
            user = store.team.create_user(
                username=str(body.get("username") or ""),
                password=str(body.get("password") or ""),
                role=str(body.get("role") or "member"),
                display_name=body.get("display_name"),
            )
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return True
        store.team.audit(
            actor=_actor(self),
            action="user_add",
            user_id=user["id"],
            target=user["username"],
        )
        self._json({"user": public_user(user)}, 201)
        return True

    m = _MEMBER_PUT.match(method, sub)
    if m:
        role = str((self._body().get("role") or "member"))
        try:
            store.governance.set_member(m.group(1), m.group(2), role=role)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return True
        store.team.audit(
            actor=_actor(self),
            action="member_set",
            user_id=m.group(2),
            project_id=m.group(1),
            detail=f"role={role}",
        )
        self._json({"ok": True})
        return True
    m = _MEMBER_DELETE.match(method, sub)
    if m:
        removed = store.governance.remove_member(m.group(1), m.group(2))
        store.team.audit(
            actor=_actor(self),
            action="member_remove",
            user_id=m.group(2),
            project_id=m.group(1),
        )
        self._json({"ok": bool(removed)})
        return True
    m = _MEMBERS.match(method, sub)
    if m:
        self._json({"members": store.governance.list_members(m.group(1))})
        return True

    m = _INVITE_REVOKE.match(method, sub)
    if m:
        revoked = store.governance.revoke_invite(m.group(1))
        store.team.audit(actor=_actor(self), action="invite_revoke", target=m.group(1))
        self._json({"ok": bool(revoked)})
        return True
    if _INVITES.match(method, sub):
        project_id = (q.get("project_id") or [None])[0]
        self._json({"invites": store.governance.list_invites(project_id=project_id)})
        return True
    if _INVITE_ADD.match(method, sub):
        body = self._body()
        project_id = str(body.get("project_id") or "")
        if not project_id:
            self._json({"error": "project_id is required"}, 400)
            return True
        ttl_s = int(body.get("ttl_s") or 0) or None
        kwargs = {"ttl_s": ttl_s} if ttl_s else {}
        token = store.governance.create_invite(project_id, _actor(self), **kwargs)
        store.team.audit(
            actor=_actor(self), action="invite_create", project_id=project_id
        )
        # the raw token's only appearance
        self._json({"token": token, "project_id": project_id}, 201)
        return True

    if _USAGE.match(method, sub):
        try:
            since = contract.int_param(q.get("since_ms"), None, name="since_ms")
        except contract.QueryParamError as exc:
            self._json({"error": str(exc), "code": exc.code}, 400)
            return True
        self._json(
            {
                "usage": store.governance.usage_summary(
                    since_ms=since,
                    user_id=(q.get("user_id") or [None])[0],
                    project_id=(q.get("project_id") or [None])[0],
                )
            }
        )
        return True
    if _AUDIT.match(method, sub):
        try:
            limit = contract.int_param(
                q.get("limit"), 200, name="limit", minimum=1, maximum=1000
            )
        except contract.QueryParamError as exc:
            self._json({"error": str(exc), "code": exc.code}, 400)
            return True
        self._json(
            {
                "audit": store.team.list_audit(
                    limit=limit, action=(q.get("action") or [None])[0]
                )
            }
        )
        return True

    if _QUOTAS.match(method, sub):
        self._json({"quotas": store.governance.list_quotas()})
        return True
    if _QUOTA_SET.match(method, sub):
        body = self._body()
        try:
            store.governance.set_quota(
                scope=str(body.get("scope") or ""),
                scope_id=str(body.get("scope_id") or ""),
                kind=str(body.get("kind") or ""),
                limit_amount=float(body.get("limit_amount") or 0),
                window=str(body.get("window") or ""),
            )
        except (ValueError, TypeError) as e:
            self._json({"error": str(e)}, 400)
            return True
        store.team.audit(
            actor=_actor(self),
            action="quota_set",
            target=f"{body.get('scope')}:{body.get('scope_id')}",
            detail=f"{body.get('kind')}={body.get('limit_amount')}/{body.get('window')}",
        )
        self._json({"ok": True})
        return True
    if _QUOTA_DELETE.match(method, sub):
        body = self._body()
        try:
            removed = store.governance.delete_quota(
                scope=str(body.get("scope") or ""),
                scope_id=str(body.get("scope_id") or ""),
                kind=str(body.get("kind") or ""),
                window=str(body.get("window") or ""),
            )
        except (ValueError, TypeError) as e:
            self._json({"error": str(e)}, 400)
            return True
        store.team.audit(
            actor=_actor(self),
            action="quota_delete",
            target=f"{body.get('scope')}:{body.get('scope_id')}",
        )
        self._json({"ok": bool(removed)})
        return True
    return False
