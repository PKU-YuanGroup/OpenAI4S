"""Team-mode auth routes: login / logout / me (plan M1-4).

Same shape as `kernel_routes`: a `RouteSpec` tuple consumed by both the
runtime dispatch below and the contract inventory, and a `handle()` that
returns True when it answered.

Naming note (plan appendix B says `/api/auth/*`): this gateway's API is
versioned, so the routes live under the existing `/api/v1/auth/*` prefix
beside `auth/status` — recorded in the plan's appendix D.

These routes answer in both modes. With team mode off, `login`/`logout`
answer 403 with a stable code and `me` reports `team_mode: false` — the
contract capture runs with team mode off, so the disabled shapes are the
frozen ones, and they must therefore stay deterministic.

Set-Cookie goes through `self._send(..., extra=...)` — the one header path
with the sanitizer on it. The raw session token exists only in that header
and in the client's cookie jar; the server keeps its sha256.
"""

from __future__ import annotations

import json
from typing import Any

from . import contract
from .team_auth import TEAM_COOKIE, RateLimited, public_user

_LOGIN = contract.RouteSpec("auth.login", "POST", r"/auth/login", mutates=True)
_LOGOUT = contract.RouteSpec("auth.logout", "POST", r"/auth/logout", mutates=True)
_ME = contract.RouteSpec("auth.me", "GET", r"/auth/me", mutates=False)
_REDEEM = contract.RouteSpec(
    "auth.redeem_invite", "POST", r"/auth/redeem-invite", mutates=True
)

ROUTES = contract.validate_routes((_LOGIN, _LOGOUT, _ME, _REDEEM))

_PATH_PREFIX = "/auth/"


def _cookie_token(self) -> str | None:
    from http.cookies import SimpleCookie

    jar = SimpleCookie(self.headers.get("Cookie", "") or "")
    morsel = jar.get(TEAM_COOKIE)
    return morsel.value if morsel is not None else None


def _json_with_cookie(self, obj: dict, cookie: str, code: int = 200) -> None:
    self._send(
        code,
        json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
        extra={"Set-Cookie": cookie},
    )


def handle(self, method: str, sub: str, team_auth: Any, store: Any) -> bool:
    """Answer an auth route, or report that this group does not own it."""
    if not sub.startswith(_PATH_PREFIX):
        return False
    if _LOGIN.match(method, sub):
        if team_auth is None:
            self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
            return True
        body = self._body()
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        ip = ""
        try:
            ip = str(self.client_address[0])
        except Exception:
            pass
        try:
            result = team_auth.login(username, password, ip)
        except RateLimited as e:
            self._json({"error": str(e), "code": "rate_limited"}, 429)
            return True
        if result is None:
            # One sentence for wrong password, unknown user, and disabled
            # account alike — the difference is an attacker's question.
            self._json({"error": "invalid username or password"}, 401)
            return True
        token, user = result
        _json_with_cookie(
            self,
            {"user": public_user(user)},
            team_auth.cookie_header(token),
        )
        return True
    if _LOGOUT.match(method, sub):
        if team_auth is None:
            self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
            return True
        team_auth.logout(_cookie_token(self))
        _json_with_cookie(self, {"ok": True}, team_auth.clear_cookie_header())
        return True
    if _REDEEM.match(method, sub):
        if team_auth is None:
            self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
            return True
        body = self._body()
        token = str(body.get("token") or "")
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        # Redemption consumes the invite only after the account details
        # validate — a typo'd username must not burn a single-use token.
        if not username or not password:
            self._json({"error": "username and password are required"}, 400)
            return True
        if store.team.get_user_by_username(username) is not None:
            self._json({"error": f"username {username!r} already exists"}, 409)
            return True
        invite = store.governance.redeem_invite(token)
        if invite is None:
            # unknown, expired, revoked, and already-used are one sentence
            self._json({"error": "invalid invite", "code": "invalid_invite"}, 403)
            return True
        user = store.team.create_user(
            username=username, password=password, role="guest"
        )
        store.governance.set_member(invite["project_id"], user["id"], role="guest")
        store.team.audit(
            actor=username,
            action="invite_redeem",
            user_id=user["id"],
            project_id=invite["project_id"],
        )
        session_token = store.team.create_auth_session(user["id"])
        _json_with_cookie(
            self,
            {"user": public_user(user), "project_id": invite["project_id"]},
            team_auth.cookie_header(session_token),
            201,
        )
        return True
    if _ME.match(method, sub):
        if team_auth is None:
            self._json({"team_mode": False, "user": None})
            return True
        identity = getattr(self, "_team_identity", None)
        if identity is None or identity.kind != "user":
            user = team_auth.resolve(_cookie_token(self))
            if user is not None:
                identity = user
        if identity is None:
            self._json({"error": "login required", "team_mode": True}, 401)
            return True
        self._json(
            {
                "team_mode": True,
                "user": {
                    "id": identity.user_id,
                    "username": identity.username,
                    "role": identity.role,
                    "kind": identity.kind,
                },
            }
        )
        return True
    return False
