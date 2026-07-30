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

from openai4s.security.permissions import FILE_MODE, harden_dir, harden_file

#: Filename under the data dir. Not in the database: see the module docstring.
TOKEN_FILENAME = "access-token"

#: The header a non-browser client presents. Named here so the gateway and the
#: CLI cannot disagree about it -- two string literals is one typo away from a
#: client that authenticates against nothing.
TOKEN_HEADER = "X-OpenAI4S-Token"

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


def _fsync_dir(directory: Path) -> None:
    """Persist the directory entry, not just the bytes behind it.

    Publishing the token is a change to the *directory*; fsyncing only the file
    leaves a crash able to keep the content and lose the name. The next boot
    would mint a second token and invalidate every cookie already issued --
    the exact failure persisting the token was meant to end.

    Best-effort by contract: not every filesystem lets a directory be opened
    for fsync, and refusing to start the daemon over a durability nicety is
    the worse trade.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def load_or_mint(data_dir: Path | str) -> str:
    """Return the daemon's token, creating it on first use.

    Concurrent minters settle on exactly one token. The candidate is written
    into a temporary in the same directory, fsynced, and published with
    `os.link` onto the final name: the link is the one operation that can only
    succeed once, so a single process creates the token and everybody else
    gets EEXIST and reads what that winner wrote.

    This used to be a read, then a mint, then an *unconditional* `os.replace`,
    which meant every racer won. Each overwrote the file, and each re-read
    outside any exclusion, so N daemons starting together held N different
    tokens with only the last write on disk -- a daemon authorising cookies
    against a value the CLI could no longer read. `os.replace` cannot pick the
    winner here precisely because it never fails on a name already taken; only
    an exclusive create can.

    Publishing through a link is also what makes "the loser re-reads" true
    rather than hopeful: the content is complete *before* the final name
    exists, so a loser's read cannot land on an empty or half-written file --
    which an `O_EXCL` create on the final path itself would leave open for as
    long as the winner takes to write and fsync.
    """
    directory = Path(data_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    harden_dir(directory)

    path = token_path(directory)
    existing = read_token(directory)
    if existing:
        return existing

    candidate = secrets.token_urlsafe(_TOKEN_BYTES)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    try:
        # 0o600 in the open() itself rather than a chmod afterwards: the mode
        # is on the inode before a single byte of the secret exists, so there
        # is no instant at which another local account could read it.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        # A umask only ever clears bits, so this is about an unusual umask
        # having taken owner-read away -- not about exposure.
        harden_file(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError:
            published = False
        else:
            published = True
    finally:
        # Removing the temporary does not touch the published inode: after the
        # link both names refer to it, and the mode travels with it, so the
        # final path is owner-only from the instant it appears.
        try:
            os.unlink(temporary)
        except OSError:
            pass

    _fsync_dir(directory)
    if published:
        return candidate

    # Lost the race. Returning `candidate` here is the whole defect: it is not
    # what is on disk, and a daemon authorising against it accepts a token no
    # other process can present.
    settled = read_token(directory)
    if settled:
        return settled
    raise RuntimeError(
        f"access token file {path} exists but is empty; minting cannot claim a "
        f"name that is already taken, so remove it and start again"
    )


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
    "TOKEN_HEADER",
    "load_or_mint",
    "matches",
    "read_token",
    "token_path",
]
