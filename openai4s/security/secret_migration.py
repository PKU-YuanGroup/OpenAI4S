"""Move plaintext credentials out of the database, recoverably.

The order matters, and it is the whole design:

    write to the new store -> verify by reading it back -> replace the row with
    a reference -> only then delete the old value

Every prefix of that sequence is safe to be interrupted at. Crash after the
write and the old plaintext is still authoritative, so the app still works and
the next run re-migrates. Crash after the reference is recorded and the value is
already readable through the broker. The one ordering that must never happen is
deleting the plaintext before proving the new copy is readable — that is how a
"security improvement" locks a user out of their own model configuration.

A verify step that merely checked "the write did not raise" would be worthless:
a keychain can accept a write and return nothing (locked, wrong collection,
denied prompt), and the failure would surface later as an unexplained auth
error. So the check reads the value back and compares it.

Nothing here logs a secret. Progress is reported as references and a short hash
prefix, which is enough to correlate an entry with a row and useless to anyone
who obtains the log.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from openai4s.security.secret_broker import SecretBroker, is_ref


def fingerprint(secret: str) -> str:
    """A short, non-reversible tag for correlating log lines to a value."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "migrated": list(self.migrated),
            "already_migrated": list(self.already),
            "empty": list(self.empty),
            "failed": list(self.failed),
            "ok": not self.failed,
        }


# Settings keys holding a credential outright. `scope` groups them in the
# keychain so a user can see what an entry is for.
SETTINGS_SECRETS: tuple[tuple[str, str], ...] = (
    ("llm_api_key", "llm"),
    ("tavily_api_key", "search"),
)


def migrate_settings_secrets(store, broker: SecretBroker) -> MigrationReport:
    """Move each plaintext settings credential behind a reference."""
    report = MigrationReport()
    for key, scope in SETTINGS_SECRETS:
        value = store.get_setting(key)
        if not value:
            report.empty.append(key)
            continue
        if is_ref(value):
            report.already.append(key)
            continue
        try:
            _migrate_one(store, broker, key=key, scope=scope, name=key, value=value)
            report.migrated.append(key)
        except Exception as e:  # noqa: BLE001 - one bad key must not strand the rest
            report.failed.append({"key": key, "error": str(e)[:300]})
    return report


def _migrate_one(
    store, broker: SecretBroker, *, key: str, scope: str, name: str, value: str
) -> str:
    # 1. write
    ref = broker.put(scope, name, value)
    # 2. verify — read it back and compare, because a write that did not raise
    #    is not evidence the value is retrievable.
    readback = broker.get(ref)
    if readback != value:
        # Leave the plaintext exactly where it is. A half-migration that cannot
        # be read is strictly worse than the plaintext we started with.
        raise RuntimeError(
            f"refusing to migrate {key!r}: wrote to {ref} but read back "
            f"{'nothing' if readback is None else 'a different value'}"
        )
    # 3. replace the row with the reference (this also removes the plaintext,
    #    since the reference overwrites the same cell)
    store.set_setting(key, ref)
    return ref


def resolve_setting(store, broker: SecretBroker, key: str) -> str:
    """Read a settings value that may be a reference or a legacy plaintext.

    Both shapes must work: an install that has not migrated yet, one that has,
    and one where migration failed for a single key all have to keep running.
    The caller does not need to know which it is looking at.
    """
    value = store.get_setting(key)
    if not value:
        return ""
    if not is_ref(value):
        return value
    resolved = broker.get(value)
    return resolved or ""


__all__ = [
    "MigrationReport",
    "SETTINGS_SECRETS",
    "fingerprint",
    "migrate_settings_secrets",
    "resolve_setting",
]
