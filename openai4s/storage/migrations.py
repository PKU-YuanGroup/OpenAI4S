"""Versioned, transactional schema migrations for the Store's SQLite database.

Before this, the database carried no version marker at all. Every open re-probed
every table with ``PRAGMA table_info`` and issued ``ALTER TABLE ADD COLUMN`` for
whatever was absent, with a bare ``except sqlite3.OperationalError: pass`` around
each one. Three problems follow from that, in increasing order of seriousness:

  * the shape had to be rediscovered on every open, so "is this database current"
    was not a question the code could answer;
  * the swallow made a genuinely failed ALTER indistinguishable from the benign
    "duplicate column name" of a re-run, so the process continued against a
    schema missing a column it believed it had; and
  * the set was not atomic. The ALTERs ran outside any transaction, so an
    upgrade that failed part-way left a partially-migrated schema — with no
    version marker to detect it by.

## Retrofitting a version onto an unversioned database

An existing install has an unknown subset of the legacy ALTERs applied, and no
record of which. That history cannot be reconstructed — but it does not need to
be. The legacy pass is *idempotent by predicate*: it adds only columns that are
absent, and every backfill is guarded by a WHERE clause that selects only rows
that still need it (``WHERE code_hash IS NULL``, ``WHERE branch_id IS NULL``…).
Running it once more on any database, old or new, converges to the same shape.

So version 1 is defined as "the legacy baseline has been run to completion", and
is stamped after it succeeds. From version 2 on, migrations are numbered, run
exactly once, and are recorded with a checksum.

## Transactions

SQLite supports transactional DDL: inside an explicit transaction, a rolled-back
``ALTER TABLE ADD COLUMN`` really does un-add its column. So the whole set rides
one explicit BEGIN/COMMIT issued here.

The explicit BEGIN is the load-bearing part, and it is worth being precise about
why, because the folklore says otherwise. Old pysqlite implicitly committed
before DDL, which would have silently defeated this — but that behaviour was
removed in Python 3.6, long before this project's 3.10 floor. What *does* still
bite is DDL outside any transaction: with the default ``isolation_level``,
pysqlite opens a transaction before DML but not before DDL, so a bare ALTER runs
in autocommit and survives a subsequent ROLLBACK. Hence: open the transaction
explicitly, and never let a step commit inside it.

The invariant this buys: a database is either fully at version N or still fully
at version N-1. There is no in-between state to recognise, which is what makes
an interrupted upgrade recoverable by simply running again.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

# Bump when adding a numbered migration below.
SCHEMA_VERSION = 3

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at INTEGER NOT NULL
)
"""


class MigrationError(RuntimeError):
    """A migration could not be applied. The database is unchanged."""


def _is_duplicate_column(exc: sqlite3.OperationalError) -> bool:
    """True only for the one error a re-run legitimately produces.

    This is the whole reason the old blanket ``except OperationalError: pass``
    was wrong: "duplicate column name" means the column is already there, which
    is success. "database is locked", "no such table", or a malformed type
    declaration mean the schema is not what we think it is — and continuing
    against that was how a missing column became a runtime mystery instead of a
    migration failure.
    """
    return "duplicate column name" in str(exc).lower()


def current_version(conn: sqlite3.Connection) -> int:
    """The database's schema version.

    ``PRAGMA user_version`` is the fast path — it is a header field, so reading
    it costs nothing on the overwhelmingly common already-current open. The
    schema_migrations table is the auditable record of how it got there.
    """
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def applied_migrations(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT version,name,checksum,applied_at FROM schema_migrations "
            "ORDER BY version"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "version": r[0],
            "name": r[1],
            "checksum": r[2],
            "applied_at": r[3],
        }
        for r in rows
    ]


def checksum(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def integrity_ok(conn: sqlite3.Connection) -> bool:
    """PRAGMA integrity_check on the live connection.

    Run before an upgrade: migrating an already-corrupt database turns a
    recoverable problem into a confusing one.
    """
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        return False
    return bool(result) and str(result[0]).lower() == "ok"


def backup_database(db_path: Path, version: int) -> Path | None:
    """Copy the database aside before an upgrade touches it.

    Uses SQLite's own backup API rather than a file copy: the file may have a
    hot journal or in-flight pages, and `cp` of a live database is how you get a
    backup that only fails to restore later, when you need it.

    Returns the backup path, or None when there is nothing worth backing up (a
    database being created for the first time has no data to lose).
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    target = db_path.with_name(f"{db_path.name}.v{version}.bak")
    try:
        source = sqlite3.connect(str(db_path))
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except (sqlite3.DatabaseError, OSError) as e:
        raise MigrationError(
            f"refusing to migrate: could not back up {db_path} first ({e})"
        ) from e
    try:
        from openai4s.security.permissions import harden_file

        harden_file(target)
    except Exception:  # noqa: BLE001 - hardening is best-effort, never fatal
        pass
    return target


def run_migrations(
    conn: sqlite3.Connection,
    db_path: Path,
    steps: dict[int, tuple[str, Callable[[sqlite3.Connection], None]]],
    *,
    target: int = SCHEMA_VERSION,
) -> dict:
    """Bring the database from its current version up to ``target``.

    ``steps`` maps version -> (name, apply_fn). Each apply_fn receives the
    connection and must be a pure schema/data transform; it runs inside a
    transaction this function owns.

    Returns a report. Raises MigrationError if a step fails, having rolled the
    database back — the caller gets an unchanged database, not a half-migrated
    one.
    """
    version = current_version(conn)
    if version >= target:
        return {"migrated": False, "from": version, "to": version, "applied": []}

    if not integrity_ok(conn):
        raise MigrationError(
            f"refusing to migrate {db_path}: PRAGMA integrity_check failed. "
            f"The database is damaged; migrating it would compound the problem. "
            f"Restore from a backup before upgrading."
        )

    backup = backup_database(db_path, version)

    applied: list[int] = []
    try:
        # Explicit, because DDL outside a transaction runs in autocommit and
        # would survive the ROLLBACK below. Steps must not commit inside it.
        conn.execute("BEGIN")
        conn.execute(_MIGRATIONS_TABLE)
        for number in sorted(steps):
            if number <= version or number > target:
                continue
            name, apply_fn = steps[number]
            apply_fn(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations"
                "(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (number, name, checksum(name, str(number)), int(time.time() * 1000)),
            )
            applied.append(number)
        # user_version cannot be parameterised — it is a pragma, not a
        # statement. The value is an int from module-level constants, never
        # user input.
        conn.execute(f"PRAGMA user_version = {int(target)}")
        conn.execute("COMMIT")
    except Exception as e:
        # The ROLLBACK is what undoes the partial upgrade. The backup is NOT
        # restored over the live file: nothing needs it to be. The transaction
        # covers an error raised by a step, and SQLite's own rollback journal
        # covers the harder case of the process being killed outright — the
        # next open replays it. Copying a file over a database this connection
        # still has open would meanwhile leave its page cache disagreeing with
        # the disk, turning a handled failure into a corrupt one.
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise MigrationError(
            f"migration to version {target} failed at step "
            f"{applied[-1] + 1 if applied else version + 1}: {e}. "
            f"The database was rolled back and remains at version {version}; "
            f"re-running is safe."
            + (f" A pre-upgrade backup is at {backup}." if backup else "")
        ) from e

    # Only now that the upgrade is committed is the pre-upgrade copy redundant.
    # Kept on failure, deliberately: it is the operator's escape hatch if the
    # migration was wrong rather than merely failed.
    if backup is not None:
        try:
            backup.unlink()
        except OSError:
            pass

    return {"migrated": True, "from": version, "to": target, "applied": applied}


__all__ = [
    "MigrationError",
    "SCHEMA_VERSION",
    "applied_migrations",
    "backup_database",
    "current_version",
    "integrity_ok",
    "run_migrations",
    "_is_duplicate_column",
]
