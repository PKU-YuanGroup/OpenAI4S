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

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.storage.team import SESSION_TTL_S

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


class TeamAuthService:
    """Login/logout/resolve on top of the Store's TeamRepository."""

    def __init__(self, store: Any, *, clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        # (username.lower(), ip) -> (tokens, last_refill_ts)
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    # --- rate limiting ---------------------------------------------------

    def _take_login_token(self, username: str, ip: str) -> bool:
        key = (username.strip().lower(), ip or "")
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
        work an attacker can make this daemon do.
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
        user = self._store.team.verify_password(username, password)
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

    @staticmethod
    def cookie_header(token: str) -> str:
        """The Set-Cookie value for a fresh login (M1-4: HttpOnly, Lax)."""
        return (
            f"{TEAM_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_TTL_S}"
        )

    @staticmethod
    def clear_cookie_header() -> str:
        return f"{TEAM_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def public_user(user: dict) -> dict:
    """The wire shape of a user row: no hash material, ever."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name"),
        "role": user["role"],
    }


__all__ = [
    "LOGIN_ATTEMPTS_PER_MINUTE",
    "RateLimited",
    "SERVICE_IDENTITY",
    "TEAM_COOKIE",
    "TeamAuthService",
    "TeamIdentity",
    "public_user",
]
