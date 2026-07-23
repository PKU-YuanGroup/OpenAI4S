"""Whether telemetry may run at all, and the identity it runs under.

Two decisions are worth stating because their alternatives look reasonable.

**The install id lives inside the consent record**, not beside it. That makes
revocation a single delete that destroys permission and identity together.
With two rows, "revoke" could clear the flag and leave a stable identifier
behind -- and an identifier that outlives the consent it was minted under is
not anonymous, it is pseudonymous with a longer memory than the user agreed to.
Re-consenting mints a fresh id, so two periods of participation cannot be
linked to each other.

**An environment variable cannot turn telemetry on.** `OPENAI4S_*` variables
are how CI, containers and scripts configure this program, so honouring one
here would mean a machine could start reporting because of a line in a
Dockerfile nobody read as a privacy decision. The variable can only turn
telemetry *off*, which is the direction that needs no consent. Granting is a
deliberate act recorded in the database by a person using this install.

Nothing in this module opens a socket or starts a thread. Reading it must stay
cheap enough that the caller has no excuse to cache the answer, because a
cached "enabled" survives a revocation.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

#: The single settings row. One row, so revoke is atomic.
CONSENT_KEY = "telemetry_consent"

#: Set to any of these and telemetry stays off whatever the database says. The
#: kill switch works without consent because refusing needs no permission.
_OFF_VALUES = frozenset({"0", "off", "false", "no", "disabled"})

ENV_VAR = "OPENAI4S_TELEMETRY"


class Consent:
    """A recorded grant. Absent means telemetry does not run."""

    __slots__ = ("granted_at", "install_id", "schema_version")

    def __init__(
        self, *, install_id: str, granted_at: int, schema_version: int
    ) -> None:
        self.install_id = install_id
        self.granted_at = granted_at
        self.schema_version = schema_version

    def as_record(self) -> dict[str, Any]:
        return {
            "install_id": self.install_id,
            "granted_at": self.granted_at,
            "schema_version": self.schema_version,
        }


def env_forbids() -> bool:
    """Whether the environment vetoes telemetry regardless of the record."""
    raw = os.environ.get(ENV_VAR)
    return raw is not None and raw.strip().lower() in _OFF_VALUES


def read(store: Any) -> Consent | None:
    """The recorded consent, or None.

    A malformed or partial row is treated as no consent. The safe reading of
    "I cannot tell whether this person agreed" is that they did not.
    """
    if env_forbids():
        return None
    raw = store.get_setting(CONSENT_KEY)
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    install_id = record.get("install_id")
    granted_at = record.get("granted_at")
    if not isinstance(install_id, str) or len(install_id) != 32:
        return None
    if not all(c in "0123456789abcdef" for c in install_id):
        return None
    if not isinstance(granted_at, int) or isinstance(granted_at, bool):
        return None
    # `int(...)` on a non-numeric string raises, which escaped this function
    # entirely — past the "a malformed row is no consent" contract stated
    # above. The consent GET then answered 500, and `grant()` could not repair
    # the row either, because it calls `read()` first and inherited the same
    # exception. A record nobody can parse is a record nobody agreed to.
    schema_version = record.get("schema_version")
    if schema_version is None:
        # A row written before the field existed is not malformed, just old.
        schema_version = 1
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        return None
    return Consent(
        install_id=install_id,
        granted_at=granted_at,
        schema_version=schema_version,
    )


def enabled(store: Any) -> bool:
    """The one question every emit site asks."""
    return read(store) is not None


def grant(store: Any, *, now: float | None = None) -> Consent | None:
    """Record consent and mint the install id that goes with it.

    Returns None when the environment vetoes, and writes nothing in that case.
    Recording it anyway would be the worse kind of bug: the row lands silently
    and takes effect the moment the variable goes away, so an operator who
    disabled telemetry for a fleet gets it back on the next image that drops
    the setting. The caller can tell the two outcomes apart and say so.
    """
    if env_forbids():
        return None
    existing = read(store)
    if existing is not None:
        return existing
    from openai4s.telemetry.schema import SCHEMA_VERSION

    consent = Consent(
        install_id=uuid.uuid4().hex,
        granted_at=int((time.time() if now is None else now) * 1000),
        schema_version=SCHEMA_VERSION,
    )
    store.set_setting(CONSENT_KEY, json.dumps(consent.as_record(), sort_keys=True))
    return consent


def revoke(store: Any) -> None:
    """Destroy consent and identity in one operation.

    There is deliberately no way to revoke while keeping the id, and no
    tombstone recording that a grant once existed: a record of participation is
    itself a fact about the user that outlives their withdrawal of it.
    """
    store.delete_setting(CONSENT_KEY)


__all__ = [
    "CONSENT_KEY",
    "Consent",
    "ENV_VAR",
    "enabled",
    "env_forbids",
    "grant",
    "read",
    "revoke",
]
