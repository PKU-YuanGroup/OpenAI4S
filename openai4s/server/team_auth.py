"""Team-mode authentication service: login, identity, and rate limiting.

The gateway's team guard (active only when ``OPENAI4S_TEAM_MODE`` is on)
resolves every request to a :class:`TeamIdentity` before routing. Two ways in:

* a browser presents the ``os_user`` cookie, minted by ``POST /auth/login``
  and stored server-side as sha256 only (storage/team.py);
* the management CLI on the server presents the daemon access token over a
  loopback connection (``X-OpenAI4S-Token`` / ``Authorization: Bearer``) and
  gets the synthetic *service* identity — admin-equivalent, but named ``cli``
  in the audit trail so a human account and the machine path stay separable.

Login attempts are rate limited with a token bucket per (username, client ip):
capacity ``LOGIN_ATTEMPTS_PER_MINUTE``, refilling at the same rate. The bucket
is in-memory by design — a restart forgets it, which costs an attacker nothing
useful (the PBKDF2 cost per guess dwarfs it) and keeps this module free of
storage concerns.

Every login outcome is audited (INV-12): success, bad credentials, and
rate-limited refusals alike.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.storage.team import SESSION_TTL_S, check_new_password, username_key

#: The browser login cookie. Distinct from the daemon's ``os_token`` access
#: cookie: one says "this browser may talk to the daemon", the other says
#: "this browser is user X". Team mode needs the second question answered.
TEAM_COOKIE = "os_user"

#: Token-bucket capacity and refill rate for POST /auth/login (plan M1-4).
LOGIN_ATTEMPTS_PER_MINUTE = 5


@dataclass(frozen=True)
class TeamIdentity:
    """Who a request is, after the team guard has run."""

    user_id: str
    username: str
    role: str
    kind: str = "user"  # "user" (cookie) | "service" (loopback CLI token)

    @property
    def is_admin(self) -> bool:
        return self.kind == "service" or self.role == "admin"


#: The loopback-CLI identity. Admin-equivalent by decision D2 (the operator
#: who can read the access-token file owns the box anyway); auditable as
#: ``cli`` rather than impersonating any human account.
SERVICE_IDENTITY = TeamIdentity(
    user_id="service:cli", username="cli", role="admin", kind="service"
)


class RateLimited(Exception):
    """Too many login attempts for this (username, ip) in the last minute."""


class AccountDisabled(Exception):
    """The password was right and the account is disabled (TEAM-3).

    Raised only after the password matched, so it answers nothing a guesser
    could use; it tells the account's owner to ask an administrator rather
    than retry or ask for a reset, which does not lift a disable.
    """


class WrongPassword(Exception):
    """The current password given to a self-service change was not right."""


class TeamAuthService:
    """Login/logout/resolve on top of the Store's TeamRepository."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        secure_cookie: bool = False,
    ):
        self._store = store
        self._clock = clock
        # The daemon itself speaks HTTP on loopback when a TLS reverse proxy
        # fronts it, so request transport cannot tell us whether a browser used
        # HTTPS.  The explicit trusted-proxy origin configuration is the trust
        # anchor; gateway composition sets this for HTTPS deployments.  Keep it
        # false by default so the supported direct-loopback HTTP workflow works.
        self._secure_cookie = bool(secure_cookie)
        self._lock = threading.Lock()
        # (username_key(username), ip) -> (tokens, last_refill_ts)
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    # --- rate limiting ---------------------------------------------------

    def _take_login_token(self, username: str, ip: str) -> bool:
        # The account's key, not the spelling: login matches `BOB` to `bob`,
        # so a per-spelling bucket would hand a guesser five attempts per
        # case variant of the name.
        key = (username_key(username), ip or "")
        now = self._clock()
        rate = LOGIN_ATTEMPTS_PER_MINUTE / 60.0
        with self._lock:
            tokens, last = self._buckets.get(
                key, (float(LOGIN_ATTEMPTS_PER_MINUTE), now)
            )
            tokens = min(float(LOGIN_ATTEMPTS_PER_MINUTE), tokens + (now - last) * rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            # Opportunistic trim so a scan of many usernames cannot grow the
            # dict without bound. A bucket refills to full after
            # capacity/rate = 60s of no activity, at which point it carries no
            # rate-limiting state: a fresh get() re-seeds it at full, which is
            # identical behavior. So drop everything idle longer than that
            # window. The previous predicate tested the *stored* token count,
            # which for an allowed attempt is at most capacity-1 and so never
            # crossed the threshold — the trim kept every entry and the dict
            # grew without bound.
            if len(self._buckets) > 4096:
                full_after = LOGIN_ATTEMPTS_PER_MINUTE / rate  # == 60.0s
                self._buckets = {
                    k: v for k, v in self._buckets.items() if (now - v[1]) < full_after
                }
        return allowed

    # --- login / logout / resolve ---------------------------------------

    def login(self, username: str, password: str, ip: str) -> tuple[str, dict] | None:
        """(cookie_token, user) on success; None on bad credentials.

        Raises :class:`RateLimited` when the bucket is empty — *before* the
        password is even looked at, so the limiter also bounds the PBKDF2
        work an attacker can make this daemon do — and
        :class:`AccountDisabled` for the right password of a disabled
        account (a wrong one is None, like an unknown name).
        """
        if not self._take_login_token(username, ip):
            self._store.team.audit(
                actor=username or "?",
                action="login_rate_limited",
                detail=f"ip={ip}",
            )
            raise RateLimited(
                f"too many login attempts; wait a minute (limit "
                f"{LOGIN_ATTEMPTS_PER_MINUTE}/min per user+ip)"
            )
        outcome, user = self._store.team.authenticate(username, password)
        if outcome == "disabled":
            self._store.team.audit(
                actor=username or "?", action="login_disabled", detail=f"ip={ip}"
            )
            raise AccountDisabled("this account is disabled")
        if user is None:
            self._store.team.audit(
                actor=username or "?", action="login_failed", detail=f"ip={ip}"
            )
            return None
        token = self._store.team.create_auth_session(user["id"])
        self._store.team.audit(
            actor=user["username"],
            action="login",
            user_id=user["id"],
            detail=f"ip={ip}",
        )
        return token, user

    def change_password(
        self, identity: "TeamIdentity", current: str, new: str, ip: str
    ) -> str:
        """Set a signed-in user's own password; returns a fresh cookie token.

        The current password is required -- a borrowed or stolen cookie must
        not be enough to take the account over -- and guessing it here is
        charged to the same per-account bucket as the login, so this route is
        not a second, unthrottled password oracle. The new password meets the
        same rule as every other chosen one (`check_new_password`, raising
        ValueError before any hashing). Like a reset, the change revokes every
        live session of the account; the browser that made it gets a new one.
        """
        check_new_password(new)
        if not self._take_login_token(identity.username, ip):
            self._store.team.audit(
                actor=identity.username,
                action="password_change_rate_limited",
                user_id=identity.user_id,
                detail=f"ip={ip}",
            )
            raise RateLimited(
                f"too many attempts; wait a minute (limit "
                f"{LOGIN_ATTEMPTS_PER_MINUTE}/min per user+ip)"
            )
        outcome, user = self._store.team.authenticate(identity.username, current)
        if outcome != "ok" or user is None or user["id"] != identity.user_id:
            self._store.team.audit(
                actor=identity.username,
                action="password_change_failed",
                user_id=identity.user_id,
                detail=f"ip={ip}",
            )
            raise WrongPassword("the current password is not right")
        self._store.team.set_password(identity.user_id, new)
        token = self._store.team.create_auth_session(identity.user_id)
        self._store.team.audit(
            actor=identity.username,
            action="password_change",
            user_id=identity.user_id,
            detail=f"ip={ip}",
        )
        return token

    def logout(self, token: str | None) -> bool:
        user = self._store.team.resolve_auth_session(token)
        revoked = self._store.team.revoke_auth_session(token)
        if user is not None:
            self._store.team.audit(
                actor=user["username"], action="logout", user_id=user["id"]
            )
        return revoked

    def resolve(self, token: str | None) -> TeamIdentity | None:
        """The live identity behind an ``os_user`` cookie value, or None."""
        user = self._store.team.resolve_auth_session(token)
        if user is None:
            return None
        return TeamIdentity(
            user_id=user["id"], username=user["username"], role=user["role"]
        )

    def cookie_header(self, token: str) -> str:
        """The Set-Cookie value for a fresh browser login."""
        header = (
            f"{TEAM_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_TTL_S}"
        )
        return header + ("; Secure" if self._secure_cookie else "")

    def clear_cookie_header(self) -> str:
        header = f"{TEAM_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        return header + ("; Secure" if self._secure_cookie else "")


#: The longest deep link a login carries back. Far past any workbench URL;
#: the bound only keeps an absurd request target out of a Location header.
_NEXT_MAX = 2048


def safe_next_path(target: str | None) -> str | None:
    """``target`` if it is a page of *this* origin to return to after login.

    The login page navigates to it, so anything that could leave the origin
    is refused rather than repaired: it must start with exactly one ``/``
    (``//host`` and ``/\\host`` are scheme-relative links to another site),
    and every character must be printable ASCII other than a backslash --
    browsers drop tabs and newlines from a URL, which turns ``/<TAB>/host``
    back into ``//host``. ``/``, the login page itself and the API are not
    worth carrying. The client applies the same rule before it navigates
    (login.js), so a hand-written ``/login?next=`` is held to it too.
    """
    if not target or len(target) > _NEXT_MAX:
        return None
    if not target.startswith("/") or target[1:2] in ("/", "\\"):
        return None
    if any(ch == "\\" or not ("!" <= ch <= "~") for ch in target):
        return None
    path = target.split("?", 1)[0].split("#", 1)[0]
    if path in ("/", "/login", "/api") or path.startswith(("/login/", "/api/")):
        return None
    return target


def login_location(target: str | None) -> str:
    """Where the team guard sends a signed-out browser: the login page,
    carrying the page it asked for when that is safe to come back to."""
    from urllib.parse import quote

    nxt = safe_next_path(target)
    return "/login" if nxt is None else "/login?next=" + quote(nxt, safe="")


#: A workbench session link: `/projects/<project>/frames/<session id>`. The
#: id class is narrow on purpose -- it is written into a Location header.
_SESSION_LINK = re.compile(r"/projects/[^/]+/frames/([A-Za-z0-9_-]{1,128})")


def guest_landing(path: str) -> str:
    """Where a guest's browser goes instead of the workbench (TEAM-28).

    A guest's whole surface is the replay viewer, so loading the workbench
    only produced a run of 403s before the client sent them on. A session
    link opens that session in the viewer.
    """
    match = _SESSION_LINK.fullmatch(path or "")
    return "/replay#" + match.group(1) if match else "/replay"


def public_user(user: dict) -> dict:
    """The wire shape of a user row: no hash material, ever."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name"),
        "role": user["role"],
    }


__all__ = [
    "AccountDisabled",
    "LOGIN_ATTEMPTS_PER_MINUTE",
    "RateLimited",
    "SERVICE_IDENTITY",
    "TEAM_COOKIE",
    "TeamAuthService",
    "TeamIdentity",
    "WrongPassword",
    "guest_landing",
    "login_location",
    "public_user",
    "safe_next_path",
]
