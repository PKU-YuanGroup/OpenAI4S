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

from openai4s.storage.team import check_new_password

from . import contract
from .team_auth import (
    TEAM_COOKIE,
    AccountDisabled,
    RateLimited,
    WrongPassword,
    public_user,
)

_LOGIN = contract.RouteSpec("auth.login", "POST", r"/auth/login", mutates=True)
_LOGOUT = contract.RouteSpec("auth.logout", "POST", r"/auth/logout", mutates=True)
_ME = contract.RouteSpec("auth.me", "GET", r"/auth/me", mutates=False)
_REDEEM = contract.RouteSpec(
    "auth.redeem_invite", "POST", r"/auth/redeem-invite", mutates=True
)

#: A user's own LLM credential (M4-1, decision D7's second half). Under
#: `/auth/` because it is self-service: it is the only credential surface a
#: member reaches without being an admin, and putting it beside `me` is what
#: keeps it out of the admin `/team/*` group where an admin could set
#: somebody else's.
_MY_KEY = contract.RouteSpec(
    "auth.me.llm_key", "GET", r"/auth/me/llm-key", mutates=False
)
_SET_MY_KEY = contract.RouteSpec(
    "auth.me.llm_key.set", "PUT", r"/auth/me/llm-key", mutates=True
)
_CLEAR_MY_KEY = contract.RouteSpec(
    "auth.me.llm_key.clear", "DELETE", r"/auth/me/llm-key", mutates=True
)

#: A user's own password (TEAM-22): self-service, so under `/auth/me` like
#: the key. A member could only use the CLI-generated password forever or ask
#: an admin at the host. Not a guest path: the gateway's guest gate keeps a
#: replay-only account to sign-in, sign-out and who-am-I.
_MY_PASSWORD = contract.RouteSpec(
    "auth.me.password", "POST", r"/auth/me/password", mutates=True
)

ROUTES = contract.validate_routes(
    (
        _CLEAR_MY_KEY,
        _LOGIN,
        _LOGOUT,
        _ME,
        _MY_KEY,
        _MY_PASSWORD,
        _REDEEM,
        _SET_MY_KEY,
    )
)

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


def _handle_my_llm_key(self, method: str, sub: str, store: Any, identity: Any) -> bool:
    """A user's own key: set it, clear it, or ask whether one is set.

    The value is write-only. `GET` answers whether a key exists and when it
    was set, never what it is — a credential a screen can display is a
    credential a screenshot leaks, and the user who typed it does not need
    it read back.
    """
    from openai4s.storage.user_keys import USER_KEY_SCOPE, secret_name

    if _MY_KEY.match(method, sub):
        records = store.user_keys.list_for_user(identity.user_id)
        self._json({"keys": [record.public() for record in records]})
        return True

    body = self._body() or {}
    provider = str(body.get("provider") or "").strip().lower()
    if not provider:
        self._json({"error": "provider is required"}, 400)
        return True

    if _CLEAR_MY_KEY.match(method, sub):
        removed = store.user_keys.delete(
            identity.user_id, provider, secrets=store.secrets
        )
        _audit_key_change(store, identity, provider, "user_llm_key_clear")
        # Clearing is not an error when there was nothing to clear: the
        # user's intent -- "do not use my key" -- is satisfied either way.
        self._json({"ok": True, "removed": bool(removed), "provider": provider})
        return True

    secret = str(body.get("api_key") or "")
    if not secret.strip():
        self._json({"error": "api_key is required"}, 400)
        return True
    try:
        ref = store.secrets.put(
            USER_KEY_SCOPE, secret_name(identity.user_id, provider), secret
        )
    except Exception as exc:  # noqa: BLE001
        # A broker that cannot store it must not leave a row pointing at a
        # slot that was never written: the next turn would then refuse with
        # "configured but unreadable" for a key that was never accepted.
        self._json(
            {"error": f"could not store the key: {exc}", "code": "secret_store"}, 503
        )
        return True
    store.user_keys.set_ref(identity.user_id, provider, ref)
    _audit_key_change(store, identity, provider, "user_llm_key_set")
    self._json({"ok": True, "provider": provider, "configured": True})
    return True


def _audit_key_change(store: Any, identity: Any, provider: str, action: str) -> None:
    try:
        store.team.audit(
            actor=identity.username,
            action=action,
            user_id=identity.user_id,
            target=provider,
        )
    except Exception:  # noqa: BLE001 — auditing must not fail the request
        pass


def _client_ip(self) -> str:
    try:
        return str(self.client_address[0])
    except Exception:  # noqa: BLE001
        return ""


def _handle_my_password(self, team_auth: Any) -> bool:
    """Change the signed-in user's own password (TEAM-22)."""
    identity = getattr(self, "_team_identity", None)
    if team_auth is None:
        self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
        return True
    if identity is None:
        self._json({"error": "login required", "code": "login_required"}, 401)
        return True
    if identity.kind != "user":
        # The loopback CLI's service identity has no account row; its
        # credential is the access-token file.
        self._json(
            {"error": "this identity has no password", "code": "no_password"}, 400
        )
        return True
    body = self._body() or {}
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    try:
        token = team_auth.change_password(identity, current, new, _client_ip(self))
    except ValueError as e:
        self._json({"error": str(e), "code": "weak_password"}, 400)
        return True
    except RateLimited as e:
        self._json({"error": str(e), "code": "rate_limited"}, 429)
        return True
    except WrongPassword as e:
        self._json({"error": str(e), "code": "wrong_password"}, 403)
        return True
    _json_with_cookie(self, {"ok": True}, team_auth.cookie_header(token))
    return True


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
        except AccountDisabled:
            # Only reachable with the right password, so it is not the
            # enumeration oracle the sentence below guards against: it tells
            # the account's owner that retrying, or a new password, will not
            # help -- an administrator has to enable the account.
            self._json(
                {
                    "error": "this account is disabled; ask an administrator",
                    "code": "account_disabled",
                },
                403,
            )
            return True
        if result is None:
            # One sentence for wrong password and unknown user alike (and a
            # disabled account with a wrong password) — the difference is an
            # attacker's question.
            self._json({"error": "invalid username or password"}, 401)
            return True
        token, user = result
        _json_with_cookie(
            self,
            {"user": public_user(user)},
            team_auth.cookie_header(token),
        )
        return True
    if _MY_PASSWORD.match(method, sub):
        return _handle_my_password(self, team_auth)
    if _LOGOUT.match(method, sub):
        if team_auth is None:
            self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
            return True
        team_auth.logout(_cookie_token(self))
        _json_with_cookie(self, {"ok": True}, team_auth.clear_cookie_header())
        return True
    if (
        _MY_KEY.match(method, sub)
        or _SET_MY_KEY.match(method, sub)
        or (_CLEAR_MY_KEY.match(method, sub))
    ):
        identity = getattr(self, "_team_identity", None)
        if team_auth is None or identity is None:
            self._json({"error": "team mode is disabled", "code": "team_off"}, 403)
            return True
        return _handle_my_llm_key(self, method, sub, store, identity)
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
        # The invite first, and read-only. Answering about usernames ahead of
        # it made this unauthenticated route an enumeration oracle for every
        # account on the instance: "already exists" and "invalid invite" are
        # distinguishable with no valid token at all. Asked without consuming
        # anything, so the property the ordering was written for -- a typo'd
        # username must not burn a single-use token -- still holds.
        if not store.governance.invite_is_live(token):
            self._json({"error": "invalid invite", "code": "invalid_invite"}, 403)
            return True
        if store.team.get_user_by_username(username) is not None:
            self._json({"error": f"username {username!r} already exists"}, 409)
            return True
        # Before the invite is consumed, like the username: a password that
        # is too short must not burn a single-use token either.
        try:
            check_new_password(password)
        except ValueError as e:
            self._json({"error": str(e), "code": "weak_password"}, 400)
            return True
        invite = store.governance.redeem_invite(token)
        if invite is None:
            # unknown, expired, revoked, and already-used are one sentence
            self._json({"error": "invalid invite", "code": "invalid_invite"}, 403)
            return True
        try:
            user = store.team.create_user(
                username=username, password=password, role="guest"
            )
        except ValueError as e:
            # The pre-check above is not a lock: a concurrent redemption can
            # take the same username between it and here. Returning the
            # invite is what keeps a lost race recoverable — the holder
            # retries with another name instead of holding a burnt token.
            store.governance.reinstate_invite(token)
            self._json({"error": str(e)}, 409)
            return True
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
