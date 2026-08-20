"""Bootstrap credentials for a worker that dials in (plan M3b-2).

A worker placed by a scheduler has to prove, over a socket the daemon did
not initiate, that it is the worker this daemon asked for. The credential
that proves it is an HMAC over the facts that make it *this* attempt:

    (allocation_id, epoch, rank, expires_at, nonce)

signed with a per-daemon secret that never leaves the machine. Each field
is there because leaving it out permits something:

* ``allocation_id`` — without it a credential is transferable between jobs.
* ``epoch`` — without it a credential from a *previous* incarnation still
  works, and INV-7's whole point is that an old epoch is refused.
* ``rank`` — a multi-node allocation has several workers and they are not
  interchangeable; M4's gang readiness counts them.
* ``expires_at`` — a queued job may sit for hours, but a credential that
  never expires is a credential that leaks eventually.
* ``nonce`` — consumed at registration, so a captured credential cannot be
  replayed by a second connection.

**The credential travels as a file, never as an environment variable.** The
file is 0600 in the session's runtime directory and the scheduler is given
only its *path* — which is why `SlurmBroker` refuses credential-shaped
environment names outright. A job's environment is visible to anyone who
can read `/proc/<pid>/environ` or `scontrol show job`; a 0600 file is
visible to the user who owns it.

Verification is constant-time and, on success, single-use: `consume`
records the nonce, and a second presentation of the same credential is
refused even though its signature is still perfectly valid.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai4s.security.permissions import (
    FILE_MODE,
)
from openai4s.security.permissions import fsync_dir as _fsync_dir
from openai4s.security.permissions import (
    harden_dir,
    harden_file,
)

#: Filename of the per-daemon signing secret, under the data dir.
SECRET_FILENAME = "worker-bootstrap-secret"

#: Filename of the durable replay fence, beside the secret. Named here rather
#: than spelled at the one construction site, because the sandbox has to deny
#: reads of it too and a second spelling is how the first one gets missed.
STATE_FILENAME = "worker-bootstrap-state.json"

#: Default credential lifetime. Long enough to cover a start-up that waits
#: on a shared filesystem and a slow interpreter; short enough that a
#: credential found in a stale workspace is already useless.
DEFAULT_TTL_S = 3600

#: The environment variable the job script reads. Deliberately named for a
#: *path*: `SlurmBroker` refuses names matching SECRET/TOKEN/KEY/CREDENTIAL,
#: and this must pass that filter because what it carries is a filename.
CREDENTIAL_PATH_ENV = "OPENAI4S_WORKER_BOOTSTRAP_PATH"


class BootstrapError(RuntimeError):
    """A credential was absent, malformed, expired, replayed or forged."""


def load_or_mint_secret(data_dir: Path | str) -> bytes:
    """The daemon's signing secret, created 0600 on first use.

    Published through `os.link`, the one filesystem operation that can only
    succeed once, so two daemons starting together settle on a single secret
    instead of each holding its own — the same reasoning (and the same
    failure) as the access token in `server/local_auth.py`.
    """
    directory = Path(data_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    harden_dir(directory)
    path = directory / SECRET_FILENAME

    existing = _read_secret(path)
    if existing is not None:
        return existing

    candidate = secrets.token_bytes(32)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    try:
        # 0o600 in the open() itself rather than a chmod afterwards: the mode
        # is on the inode before a single byte of the secret exists.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        harden_file(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError:
            published = False
        else:
            published = True
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass

    # Persist the directory entry, not just the bytes behind it. Publishing
    # is a change to the *directory*, so fsyncing only the file leaves a
    # crash able to keep the content and lose the name -- and the next boot
    # then mints a different signing secret, which invalidates every worker
    # credential still sitting inside its 24h life on the shared filesystem.
    # `local_auth.load_or_mint` has done this since it was written and this
    # copy of the same protocol omitted it.
    _fsync_dir(directory)

    if published:
        return candidate
    settled = _read_secret(path)
    if settled is None:
        raise BootstrapError(f"{path} exists but is empty; remove it and start again")
    return settled


def _read_secret(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except (OSError, ValueError):
        return None
    return data or None


@dataclass(frozen=True)
class BootstrapCredential:
    """What a worker presents. The signature covers every other field."""

    allocation_id: str
    epoch: int
    rank: int
    expires_at: float
    nonce: str
    signature: str

    def payload(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "epoch": self.epoch,
            "rank": self.rank,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }

    def to_json(self) -> str:
        return json.dumps({**self.payload(), "signature": self.signature})

    @staticmethod
    def from_json(text: str) -> BootstrapCredential:
        try:
            data = json.loads(text)
            return BootstrapCredential(
                allocation_id=str(data["allocation_id"]),
                epoch=int(data["epoch"]),
                rank=int(data["rank"]),
                expires_at=float(data["expires_at"]),
                nonce=str(data["nonce"]),
                signature=str(data["signature"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise BootstrapError(f"malformed credential: {exc}") from exc


def _sign(secret: bytes, payload: dict[str, Any]) -> str:
    # sort_keys so the signed bytes do not depend on dict ordering: a
    # signature that verifies only when the fields happen to be in the same
    # order is a signature that fails in production and passes in tests.
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class BootstrapAuthority:
    """Issues credentials, verifies them once, and refuses stale epochs."""

    def __init__(
        self,
        secret: bytes,
        *,
        clock=time.time,
        state_path: Path | str | None = None,
    ) -> None:
        self._secret = secret
        self._clock = clock
        self._lock = threading.Lock()
        #: Consumed nonces, and the epoch fence per allocation (INV-7).
        #:
        #: Durable, and the reasoning that said otherwise was wrong: "a
        #: restart invalidates every outstanding credential anyway, because
        #: the workers that held them lost their connection". The connection
        #: is gone; the *credential file* is not. It sits on the shared
        #: filesystem the job was given, still inside its 24h life, and a
        #: fresh daemon with an empty set admits it -- so a burned
        #: credential un-burns itself on restart, and an epoch fenced by a
        #: recovery stops being fenced. Both are exactly what INV-7 and the
        #: single-use rule exist to prevent, and neither survived a process
        #: boundary.
        self._consumed: set[str] = set()
        self._consumed_expiry: dict[str, float] = {}
        self._current_epoch: dict[str, int] = {}
        self._state_path = Path(state_path) if state_path else None
        #: Set by `_load_state` when durable state existed and could not be
        #: read. Non-None means every admission is refused: an unreadable
        #: fence cannot answer "has this nonce been spent", and the answer it
        #: would otherwise default to is the unsafe one.
        self._fence_error: str | None = None
        self._load_state()

    # --- durable fence ----------------------------------------------------

    def _load_state(self) -> None:
        """Read the fence back.

        A *missing* file is an empty fence and is correct: a fresh install
        has burned nothing. An *unreadable* one is not the same thing, and
        the comment here used to treat them alike -- "refusing to boot
        because a cache is corrupt would trade a replay window for an
        outage". This is not a cache. It is the record of which credentials
        have been spent, and reading it as empty re-admits every unexpired
        credential still sitting on the shared filesystem, plus every epoch
        a recovery had fenced off.

        Corruption is also the shape tampering takes. `_save_state` publishes
        through `os.replace`, which is atomic, so an interrupted write leaves
        the previous complete file rather than half of a new one; a file that
        will not parse is therefore disk damage or somebody editing it. The
        second is exactly the party this fence exists to refuse, so the fence
        marks itself untrusted and admission fails closed until an operator
        deals with it.
        """
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._fence_error = (
                f"the worker bootstrap fence at {self._state_path} could not be "
                f"read ({exc}). It records which credentials have already been "
                f"used, so continuing without it would re-admit every unexpired "
                f"credential and un-fence every recovered epoch. Rotate the "
                f"signing secret (delete the secret file beside it, which "
                f"invalidates outstanding credentials) or restore the fence "
                f"from backup, then restart."
            )
            return
        now = self._clock()
        consumed = data.get("consumed")
        if isinstance(consumed, dict):
            # nonce -> expiry. Pruned on load, so the file cannot grow
            # without bound across a long-lived install: a nonce past its
            # credential's expiry is refused by the expiry check anyway.
            self._consumed = {
                str(nonce)
                for nonce, expires in consumed.items()
                if _as_float(expires) > now
            }
            self._consumed_expiry = {
                str(nonce): _as_float(expires)
                for nonce, expires in consumed.items()
                if _as_float(expires) > now
            }
        epochs = data.get("epochs")
        if isinstance(epochs, dict):
            self._current_epoch = {
                str(allocation): int(epoch) for allocation, epoch in epochs.items()
            }

    def _save_state(self) -> None:
        """Rewrite the fence, atomically and 0600.

        Called with the lock held. Best-effort: a write that fails must not
        fail the registration it was recording, because refusing a worker
        that presented a valid credential is a worse outcome than a fence
        that has to be rebuilt.
        """
        if self._state_path is None:
            return
        # Prune here, not only in `_load_state`. The claim there -- "pruned on
        # load, so the file cannot grow without bound across a long-lived
        # install" -- was true only of installs that restart: load happens once,
        # at construction, and a daemon that stays up for a month accumulated
        # every nonce it ever burned and rewrote all of them on every worker
        # registration. An expired nonce is refused by the expiry check anyway,
        # so dropping it here costs nothing and bounds the file by the TTL.
        now = self._clock()
        live = {
            nonce: expires
            for nonce, expires in (getattr(self, "_consumed_expiry", {}) or {}).items()
            if expires > now
        }
        self._consumed_expiry = live
        self._consumed = set(live)
        payload = {
            "consumed": live,
            "epochs": self._current_epoch,
        }
        tmp = self._state_path.with_suffix(".tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(
                os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(payload, handle)
                handle.flush()
                # `os.replace` is atomic about the *directory entry*, not
                # about the bytes: without this the rename can reach disk
                # before the data does, and a power loss leaves the new name
                # pointing at an empty file. `_load_state` reads an
                # unparseable fence as tampering and fails admission closed
                # *permanently*, so the missing fsync turned an ordinary
                # crash into "no worker may ever register again". The sibling
                # protocol twenty lines up (`load_or_mint_secret`) has done
                # both of these since it was written.
                os.fsync(handle.fileno())
            os.replace(tmp, self._state_path)
            _fsync_dir(self._state_path.parent)
        except Exception:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    def issue(
        self,
        *,
        allocation_id: str,
        epoch: int,
        rank: int = 0,
        ttl_s: int = DEFAULT_TTL_S,
    ) -> BootstrapCredential:
        payload = {
            "allocation_id": allocation_id,
            "epoch": int(epoch),
            "rank": int(rank),
            "expires_at": self._clock() + ttl_s,
            "nonce": secrets.token_urlsafe(24),
        }
        with self._lock:
            # Issuing for an epoch fences every earlier one: recovery mints a
            # new epoch, and the worker from the old one must not be able to
            # register afterwards.
            known = self._current_epoch.get(allocation_id)
            if known is None or epoch > known:
                self._current_epoch[allocation_id] = int(epoch)
                self._save_state()
        return BootstrapCredential(signature=_sign(self._secret, payload), **payload)

    def verify(self, credential: BootstrapCredential) -> None:
        """Raise unless this credential is ours, current, and unused."""
        if self._fence_error is not None:
            raise BootstrapError(self._fence_error)
        expected = _sign(self._secret, credential.payload())
        if not hmac.compare_digest(expected, credential.signature):
            raise BootstrapError("credential signature does not verify")
        if credential.expires_at <= self._clock():
            raise BootstrapError("credential has expired")
        with self._lock:
            current = self._current_epoch.get(credential.allocation_id)
            if current is not None and credential.epoch < current:
                # INV-7: an old epoch's worker is refused, by the same rule
                # that refuses its callbacks.
                raise BootstrapError(
                    f"STALE_EPOCH: allocation {credential.allocation_id} is at "
                    f"epoch {current}, credential carries {credential.epoch}"
                )
            if credential.nonce in self._consumed:
                raise BootstrapError("credential has already been used")

    def consume(self, credential: BootstrapCredential) -> None:
        """Verify and burn, atomically enough that two connections presenting
        the same credential cannot both win."""
        if self._fence_error is not None:
            raise BootstrapError(self._fence_error)
        with self._lock:
            if credential.nonce in self._consumed:
                raise BootstrapError("credential has already been used")
            # Signature and expiry first, so a replay of a forged credential
            # is refused as forged rather than as replayed.
            expected = _sign(self._secret, credential.payload())
            if not hmac.compare_digest(expected, credential.signature):
                raise BootstrapError("credential signature does not verify")
            if credential.expires_at <= self._clock():
                raise BootstrapError("credential has expired")
            current = self._current_epoch.get(credential.allocation_id)
            if current is not None and credential.epoch < current:
                raise BootstrapError(
                    f"STALE_EPOCH: allocation {credential.allocation_id} is at "
                    f"epoch {current}, credential carries {credential.epoch}"
                )
            self._consumed.add(credential.nonce)
            expiry = getattr(self, "_consumed_expiry", None)
            if expiry is None:
                expiry = self._consumed_expiry = {}
            expiry[credential.nonce] = float(credential.expires_at)
            # Written before the caller is told the credential was accepted:
            # a crash between "burned" and "recorded as burned" is the one
            # ordering that reopens the replay this fence exists to close.
            self._save_state()

    def fence_epoch(self, allocation_id: str, epoch: int) -> None:
        """Declare the epoch now in force, refusing everything older."""
        with self._lock:
            self._current_epoch[allocation_id] = int(epoch)
            self._save_state()


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return 0.0


def write_credential_file(
    credential: BootstrapCredential, directory: Path | str
) -> Path:
    """Write the credential 0600 and return its path — the only thing the
    scheduler is ever told."""
    target_dir = Path(directory).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    harden_dir(target_dir)
    # The rank is in the name, not merely in the payload: a multi-node job
    # writes one credential per rank into the same shared workspace, and
    # without it rank N overwrites rank N-1 and only one node can ever
    # register.
    path = target_dir / (
        f"bootstrap-{credential.allocation_id}-{credential.epoch}"
        f"-r{credential.rank}.json"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(credential.to_json())
        handle.flush()
        os.fsync(handle.fileno())
    harden_file(path)
    return path


def read_credential_file(path: Path | str) -> BootstrapCredential:
    return BootstrapCredential.from_json(
        Path(path).expanduser().read_text(encoding="utf-8")
    )


__all__ = [
    "CREDENTIAL_PATH_ENV",
    "DEFAULT_TTL_S",
    "SECRET_FILENAME",
    "STATE_FILENAME",
    "BootstrapAuthority",
    "BootstrapCredential",
    "BootstrapError",
    "load_or_mint_secret",
    "read_credential_file",
    "write_credential_file",
]
