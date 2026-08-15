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

A failure is reported as the exception's *type name*, never ``str(exc)``. The
message text of a backend error is not ours to reason about: it is produced by
``security``/``secret-tool``/sqlite and can be changed by any of them, so
"today's messages happen not to contain the value" is an audit that expires the
next time one of those is upgraded. The type name is enough to tell a locked
keychain from a malformed reference, and cannot carry a credential at all.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from openai4s.security.secret_broker import (
    SecretBroker,
    SecretBrokerError,
    is_ref,
    make_ref,
)


def fingerprint(secret: str) -> str:
    """A short, non-reversible tag for correlating log lines to a value."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


@dataclass
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    reentry_required: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "migrated": list(self.migrated),
            "already_migrated": list(self.already),
            "reentry_required": list(self.reentry_required),
            "empty": list(self.empty),
            "failed": list(self.failed),
            "ok": not self.failed,
        }


# Settings columns holding a credential outright. `scope` groups them in the
# keychain so a user can see what an entry is for.
#
# The first element is a *column name*, never a credential, and it is reported
# and logged as such. It is called `setting` rather than `key` on purpose: in a
# module about credentials, `key` reads as "the secret", and both a human and a
# taint analysis will believe it -- CodeQL flagged the log lines that printed
# it for exactly that reason.
SETTINGS_SECRETS: tuple[tuple[str, str], ...] = (
    ("llm_api_key", "llm"),
    ("tavily_api_key", "search"),
    ("agent_plan_key", "agent_plan"),
)


def scope_for_setting(key: str) -> str:
    """Which scope a settings credential is filed under, or "" if it is not one.

    `resolve_setting` is handed only a key, but a secret is addressed by
    scope *and* name, so without this table there is no way to ask a backend
    whether it holds one. Threading a scope through every caller would work
    too and would be one more thing each of them could get wrong; the mapping
    is already declared here and is the same mapping migration uses.
    """
    for candidate, scope in SETTINGS_SECRETS:
        if candidate == key:
            return scope
    return ""


def migrate_settings_secrets(store, broker: SecretBroker) -> MigrationReport:
    """Move each plaintext settings credential behind a reference."""
    report = MigrationReport()
    for setting, scope in SETTINGS_SECRETS:
        value = store.get_setting(setting)
        if not value:
            report.empty.append(setting)
            continue
        if is_ref(value):
            try:
                described = broker.describe(value)
            except Exception as e:  # noqa: BLE001 - one bad key must not strand
                # `describe` reads the backend: a locked keychain (the CLI times
                # out after 10s) or a hand-edited ref raises here, and this ran
                # outside the per-key guard, so it aborted the whole pass -- and
                # with it `migrate_profile_keys` and `migrate_connector_env`,
                # which share one `try` at the call site.
                report.failed.append({"setting": setting, "error": type(e).__name__})
                continue
            if described["reentry_required"] or not described["configured"]:
                # A v1 system slot is process-global and contains no evidence
                # of which data directory owns it. Reading it to "migrate"
                # would reproduce the cross-Store disclosure this boundary is
                # meant to close. A matching v2 ref whose backend value was
                # revoked likewise needs re-entry rather than "already".
                report.reentry_required.append(setting)
            else:
                report.already.append(setting)
            continue
        try:
            _migrate_one(
                store, broker, key=setting, scope=scope, name=setting, value=value
            )
            report.migrated.append(setting)
        except Exception as e:  # noqa: BLE001 - one bad key must not strand the rest
            report.failed.append({"setting": setting, "error": type(e).__name__})
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


def migrate_connector_env(store) -> dict:
    """Move each connector's plaintext env values behind references.

    Re-upserting the row is what applies it: `Store.upsert_connector` brokers
    env on the way in, so this only has to hand back what it read. That keeps
    one implementation of the write rule rather than a second copy here that
    could drift from it.
    """
    migrated: list[str] = []
    reentry_required: list[str] = []
    failed: list[dict] = []
    for connector in store.list_connectors():
        env = connector.get("env")
        if not isinstance(env, dict) or not env:
            continue
        refs = [str(value or "") for value in env.values() if is_ref(str(value or ""))]
        try:
            needs_reentry = any(
                (description := store.secrets.describe(ref))["reentry_required"]
                or not description["configured"]
                for ref in refs
            )
        except Exception as e:  # noqa: BLE001 - one bad connector must not
            # strand the others; describe() reaches the backend and can raise.
            failed.append({"id": connector["connector_id"], "error": type(e).__name__})
            continue
        if refs and needs_reentry:
            reentry_required.append(str(connector["connector_id"]))
        if all(is_ref(str(v or "")) or not v for v in env.values()):
            continue
        try:
            store.upsert_connector(
                connector_id=connector["connector_id"],
                name=connector["name"],
                description=connector.get("description") or "",
                command=connector.get("command"),
                args=connector.get("args"),
                env=env,
                enabled=bool(connector.get("enabled", True)),
            )
            migrated.append(connector["connector_id"])
        except Exception as e:  # noqa: BLE001 - one bad connector must not
            # strand the others; its plaintext stays and it keeps working.
            failed.append({"id": connector["connector_id"], "error": type(e).__name__})
    return {
        "migrated": migrated,
        "reentry_required": reentry_required,
        "failed": failed,
    }


def _injected(broker: SecretBroker, key: str, scope: str) -> str:
    """What a read-only backend holds for a settings row that does not exist.

    Environment injection is the one backend the app can never write to: its
    `put` refuses by design, and `migrate_settings_secrets` has nothing to
    migrate because there is no plaintext to move. So on a fresh install
    nothing ever puts the reference row there, and without this lookup the
    injected variable is dead — `OPENAI4S_SECRET_LLM_LLM_API_KEY` would
    resolve empty and the UI would report the model as unconfigured, with
    nothing raised anywhere to say why.

    Restricted to a read-only backend on purpose. Behind a writable one an
    empty row is the app's own answer: `Store.set_secret_setting(key, "")`
    clears the row *and* deletes the stored value, and it swallows a failure of
    that delete so the row still gets cleared. Consulting the backend anyway
    would turn that swallowed failure into a revoked credential coming back to
    life. A read-only backend cannot have been written or cleared by the app,
    so what it holds is the operator's standing answer rather than a leftover.

    The reference is built with this Store's namespace whenever there is one,
    i.e. the same v2 reference `put` would have written had it been allowed to.
    That matters because the namespace decides which variable is consulted: a
    v1 reference reaches only the plain `OPENAI4S_SECRET_<SCOPE>_<NAME>`, while
    the refusal an operator actually sees when they try to save a key from the
    UI names the *namespaced* `OPENAI4S_SECRET_V2_<NS>_<SCOPE>_<NAME>`. Built
    the v1 way, following that instruction to the letter still resolved to
    nothing — the same silent dead end this function exists to close, moved to
    a different spelling. The v2 path tries the namespaced variable and falls
    back to the plain one, so both work.
    """
    if not scope or not broker.read_only:
        return ""
    try:
        return broker.get(make_ref(scope, key, broker.namespace)) or ""
    except SecretBrokerError:
        # An unusable scope/name is a configuration answer of "nothing here",
        # not a reason to fail a read that has a legitimate empty result.
        return ""


def resolve_setting(
    store, broker: SecretBroker, key: str, *, scope: str | None = None
) -> str:
    """Read a settings value that may be a reference or a legacy plaintext.

    Both shapes must work: an install that has not migrated yet, one that has,
    and one where migration failed for a single key all have to keep running.
    The caller does not need to know which it is looking at.

    A third shape has no row at all — see `_injected`. The row still wins when
    it holds something, so this only ever adds an answer where there was none.
    """
    value = store.get_setting(key)
    if not value:
        return _injected(broker, key, scope or scope_for_setting(key))
    if not is_ref(value):
        return value
    # A ref names its own scope and name, so it is already a complete address:
    # if the environment supplies that exact pair, `broker.get` finds it here.
    resolved = broker.get(value)
    return resolved or ""


__all__ = [
    "MigrationReport",
    "SETTINGS_SECRETS",
    "fingerprint",
    "migrate_connector_env",
    "migrate_settings_secrets",
    "resolve_setting",
    "scope_for_setting",
]
