"""The daemon's own access token: minted once, owner-readable, reused.

The gateway used to mint `secrets.token_hex(16)` into a closure local and print
it. Nothing persisted it, so the token changed on every restart and every
cookie issued before the restart became invalid — which is tolerable for a
gate that is off by default and intolerable for one that is on.

It also lives here rather than in `gateway.py` because the CLI needs to read
the same value: with the gate required, every `openai4s` subcommand that talks
to the daemon has to present a credential, and the two must agree on where it
is kept without the CLI importing the web server.

Pure stdlib, and deliberately a file rather than a Store row: the CLI can read
it before any database exists, and `openai4s doctor` has to work when the
database is the thing that is broken.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from openai4s.security.permissions import harden_dir, harden_file

#: Filename under the data dir. Not in the database: see the module docstring.
TOKEN_FILENAME = "access-token"

#: 32 bytes of urlsafe randomness. Long enough that guessing is not the attack,
#: short enough to paste.
_TOKEN_BYTES = 32


def token_path(data_dir: Path | str) -> Path:
    return Path(data_dir).expanduser() / TOKEN_FILENAME


def read_token(data_dir: Path | str) -> str | None:
    """Return the stored token, or None when there is not one to read."""
    path = token_path(data_dir)
    try:
        value = path.read_text("utf-8").strip()
    except (OSError, ValueError):
        return None
    return value or None


def load_or_mint(data_dir: Path | str) -> str:
    """Return the daemon's token, creating it on first use.

    Written through a temporary in the same directory and `os.replace`d, so a
    second daemon starting concurrently either sees no file or sees a complete
    one -- never a half-written token that would authenticate nothing and lock
    the user out of their own machine.

    The loser of that race keeps the winner's token rather than overwriting it:
    a token that changes because two processes started together is the same
    failure as one that changes on every restart.
    """
    directory = Path(data_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    harden_dir(directory)

    existing = read_token(directory)
    if existing:
        return existing

    path = token_path(directory)
    candidate = secrets.token_urlsafe(_TOKEN_BYTES)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    try:
        # x mode: never clobber a token another process is mid-write on.
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        harden_file(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    harden_file(path)

    # Re-read rather than returning `candidate`: if another process won the
    # replace, the file holds *its* token and that is the one the cookies and
    # the CLI will be checked against.
    return read_token(directory) or candidate


def matches(supplied: str | None, expected: str | None) -> bool:
    """Constant-time comparison that tolerates absent values.

    `==` on a secret leaks its prefix through timing. That is a weak channel
    over loopback and a real one over a tunnel, and the fix costs nothing.
    """
    if not supplied or not expected:
        return False
    return hmac.compare_digest(str(supplied), str(expected))


__all__ = [
    "TOKEN_FILENAME",
    "load_or_mint",
    "matches",
    "read_token",
    "token_path",
]
