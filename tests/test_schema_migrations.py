"""Versioned, transactional schema migrations.

The database carried no version marker at all. Every open re-probed every table
with PRAGMA table_info and ALTERed in whatever was missing, each ALTER wrapped
in a bare `except sqlite3.OperationalError: pass`. So:

  * "is this database current?" was not a question the code could answer;
  * a genuinely failed ALTER was indistinguishable from the benign "duplicate
    column name" of a re-run, and the process carried on against a schema
    missing a column it believed it had; and
  * the set was not atomic — the ALTERs ran outside any transaction, so an
    upgrade that failed part-way left a partially-migrated schema with nothing
    to detect it by.

The invariant these tests pin: a database is either fully at version N or still
fully at version N-1. Never in between.

The retrofit rests on one property, asserted below: the legacy pass is
idempotent *by predicate* (it adds only absent columns; every backfill is
guarded by a WHERE selecting only rows that still need it). That is what makes
it safe to define version 1 as "the legacy baseline has run" and stamp it,
without reconstructing which ALTERs an existing install had already applied —
history that is simply not recorded anywhere.
"""
import re
import sqlite3
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.storage.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    _is_duplicate_column,
    applied_migrations,
    backup_database,
    current_version,
    integrity_ok,
    run_migrations,
)
from openai4s.store import Store, get_store


def _schema_sql() -> str:
    src = Path("openai4s/store.py").read_text()
    return re.search(
        r'_SCHEMA\s*=\s*(?:r?"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S
    ).group(1)


@pytest.fixture
def plain_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t(a TEXT)")
    conn.execute("INSERT INTO t VALUES('precious-data')")
    conn.commit()
    yield conn, db
    conn.close()


# --------------------------------------------------------------------------
# versioning
# --------------------------------------------------------------------------


def test_a_new_store_is_stamped_and_recorded(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    state = store.schema_state()
    assert state["version"] == SCHEMA_VERSION
    assert state["current"] is True
    assert [m["name"] for m in state["applied"]] == [
        "legacy_baseline",
        "compute_job_states",
        "compute_job_manifest",
    ]
    assert state["applied"][0]["checksum"]
    assert state["applied"][0]["applied_at"] > 0


def test_an_unversioned_database_reports_version_zero(plain_db):
    conn, _ = plain_db
    assert current_version(conn) == 0


def test_reopening_a_current_database_does_no_work(tmp_path, monkeypatch):
    """The fast path is a user_version read. Previously every open re-derived
    the whole schema shape with a table_info scan per table."""
    path = Config(data_dir=tmp_path).db_path
    get_store(path).close()

    import openai4s.storage.migrations as migrations

    calls = []
    monkeypatch.setattr(
        migrations, "integrity_ok", lambda c: calls.append(1) or True, raising=True
    )
    monkeypatch.setattr(
        migrations,
        "backup_database",
        lambda *a: calls.append("backup"),
        raising=True,
    )
    Store(path).close()
    assert calls == [], "an already-current database must not be probed or backed up"


# --------------------------------------------------------------------------
# the retrofit onto a real old database
# --------------------------------------------------------------------------


def _make_legacy_db(tmp_path) -> Path:
    """A database as it existed before the branch_id migration, with data."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    # Roll `messages` back to its pre-branch_id shape.
    conn.execute(
        "CREATE TABLE _tmp AS SELECT message_id,frame_id,root_frame_id,role,"
        "content,seq,created_at,metadata FROM messages"
    )
    conn.execute("DROP TABLE messages")
    conn.execute("ALTER TABLE _tmp RENAME TO messages")
    conn.execute(
        "INSERT INTO messages(message_id,frame_id,root_frame_id,role,content,"
        "seq,created_at) VALUES('m1','f1','f1','user','hi',1,1)"
    )
    conn.execute(
        "INSERT INTO frames(frame_id,root_frame_id,project_id,kind,status,"
        "created_at,updated_at) VALUES('f1','f1','proj-x','turn','done',1,1)"
    )
    conn.commit()
    conn.close()
    return db


def test_legacy_database_is_migrated_and_stamped(tmp_path):
    db = _make_legacy_db(tmp_path)
    with sqlite3.connect(str(db)) as probe:
        assert "branch_id" not in {
            r[1] for r in probe.execute("PRAGMA table_info(messages)")
        }

    store = Store(db)
    try:
        cols = {r[1] for r in store._conn.execute("PRAGMA table_info(messages)")}
        assert "branch_id" in cols
        assert store.schema_state()["version"] == SCHEMA_VERSION
    finally:
        store.close()


def test_legacy_migration_preserves_data_and_backfills(tmp_path):
    db = _make_legacy_db(tmp_path)
    store = Store(db)
    try:
        row = store._conn.execute(
            "SELECT message_id,branch_id FROM messages"
        ).fetchone()
        assert row["message_id"] == "m1"
        # Backfilled from root_frame_id by the baseline's guarded UPDATE.
        assert row["branch_id"] == "f1"
    finally:
        store.close()


def test_the_legacy_baseline_is_idempotent(tmp_path):
    """The property the whole retrofit rests on. If running the baseline twice
    were not a no-op, defining version 1 as "it has run" would be unsound."""
    db = _make_legacy_db(tmp_path)
    first = Store(db)
    shape_1 = sorted(r[1] for r in first._conn.execute("PRAGMA table_info(messages)"))
    rows_1 = first._conn.execute("SELECT * FROM messages").fetchall()
    first.close()

    # Force the baseline to run a second time against the already-migrated db.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    second = Store(db)
    try:
        shape_2 = sorted(
            r[1] for r in second._conn.execute("PRAGMA table_info(messages)")
        )
        rows_2 = second._conn.execute("SELECT * FROM messages").fetchall()
        assert shape_2 == shape_1
        assert [tuple(r) for r in rows_2] == [tuple(r) for r in rows_1]
    finally:
        second.close()


def test_an_upgrade_repairs_bad_rows_before_stamping_the_version(tmp_path):
    """No real database loses the legacy repairs by gaining a version.

    The baseline is not only ALTERs — it also repairs historical rows (child
    frames that kept project_id='default' instead of inheriting the root's).
    That repair used to run on every open; it now runs once. The property that
    makes that safe is the ordering asserted here: an un-versioned database
    runs the repair and is only stamped afterwards, so the upgrade cannot
    strand anyone with unrepaired data.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_schema_sql())
    conn.execute(
        "INSERT INTO frames(frame_id,root_frame_id,project_id,kind,status,"
        "created_at,updated_at) VALUES('root','root','project-x','turn','done',1,1)"
    )
    # The historical shape: a child that kept 'default' rather than inheriting.
    conn.execute(
        "INSERT INTO frames(frame_id,parent_id,root_frame_id,project_id,kind,"
        "status,created_at,updated_at) "
        "VALUES('child','root','root','default','delegate','done',1,1)"
    )
    conn.commit()
    conn.close()

    store = Store(db)
    try:
        assert store.get_frame("child")["project_id"] == "project-x"
        assert store.schema_state()["version"] == SCHEMA_VERSION
    finally:
        store.close()


def test_successful_migration_cleans_up_its_backup(tmp_path):
    db = _make_legacy_db(tmp_path)
    store = Store(db)
    try:
        assert list(db.parent.glob("*.bak")) == []
    finally:
        store.close()


# --------------------------------------------------------------------------
# atomicity — the scorecard's "a mid-migration kill leaves no unrecognizable state"
# --------------------------------------------------------------------------


def test_a_failing_step_rolls_back_the_whole_set(plain_db):
    """Not just the failing step: the database must land fully on the old
    version, so there is no in-between state to have to recognise."""
    conn, db = plain_db

    def step_ok(c):
        c.execute("ALTER TABLE t ADD COLUMN b TEXT")
        c.execute("UPDATE t SET b='migrated'")

    def step_boom(c):
        c.execute("ALTER TABLE t ADD COLUMN c TEXT")
        raise RuntimeError("killed mid-migration")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("ok", step_ok), 2: ("boom", step_boom)}, target=2)

    assert sorted(r[1] for r in conn.execute("PRAGMA table_info(t)")) == ["a"]
    assert current_version(conn) == 0
    assert conn.execute("SELECT a FROM t").fetchone()[0] == "precious-data"


def test_ddl_really_is_transactional(plain_db):
    """The load-bearing mechanism: a rolled-back ALTER must un-add its column.

    SQLite supports this, but only inside an explicit transaction — DDL outside
    one runs in autocommit and survives the ROLLBACK. That is what the explicit
    BEGIN in run_migrations buys, and what this pins.
    """
    conn, db = plain_db

    def add_then_fail(c):
        c.execute("ALTER TABLE t ADD COLUMN gone TEXT")
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("x", add_then_fail)}, target=1)
    assert "gone" not in {r[1] for r in conn.execute("PRAGMA table_info(t)")}


def test_bare_ddl_outside_a_transaction_would_not_roll_back(plain_db):
    """The negative control that gives the test above its meaning.

    Without an explicit BEGIN, an ALTER commits itself and a later ROLLBACK
    cannot undo it. Pinned so nobody 'simplifies' the BEGIN away and leaves the
    atomicity tests passing for the wrong reason.
    """
    conn, _ = plain_db
    conn.execute("ALTER TABLE t ADD COLUMN survives TEXT")
    conn.rollback()
    assert "survives" in {r[1] for r in conn.execute("PRAGMA table_info(t)")}


def test_a_migration_leaves_no_transaction_open(plain_db):
    """The connection is handed straight to every repository afterwards; a
    dangling transaction would hold locks for the life of the process."""
    conn, db = plain_db
    run_migrations(conn, db, {1: ("noop", lambda c: None)}, target=1)
    assert conn.in_transaction is False


def test_a_failed_migration_leaves_no_transaction_open(plain_db):
    conn, db = plain_db

    def boom(c):
        c.execute("ALTER TABLE t ADD COLUMN x TEXT")
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("boom", boom)}, target=1)
    assert conn.in_transaction is False


def test_a_failed_migration_keeps_its_backup(plain_db):
    conn, db = plain_db

    def boom(c):
        raise RuntimeError("boom")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("boom", boom)}, target=1)
    assert [p.name for p in db.parent.glob("*.bak")] == ["t.db.v0.bak"]


def test_rerunning_after_a_failure_is_safe(plain_db):
    conn, db = plain_db
    state = {"fail": True}

    def flaky(c):
        c.execute("ALTER TABLE t ADD COLUMN b TEXT")
        if state["fail"]:
            raise RuntimeError("transient")

    with pytest.raises(MigrationError):
        run_migrations(conn, db, {1: ("flaky", flaky)}, target=1)
    state["fail"] = False
    report = run_migrations(conn, db, {1: ("flaky", flaky)}, target=1)
    assert report["migrated"] is True
    assert current_version(conn) == 1


# --------------------------------------------------------------------------
# error classification — no more blanket swallow
# --------------------------------------------------------------------------


def test_duplicate_column_is_the_only_benign_operational_error():
    assert _is_duplicate_column(
        sqlite3.OperationalError("duplicate column name: branch_id")
    )
    for hostile in (
        "database is locked",
        "no such table: frames",
        'near "TEXTT": syntax error',
        "attempt to write a readonly database",
    ):
        assert not _is_duplicate_column(sqlite3.OperationalError(hostile)), hostile


def test_a_real_alter_failure_is_not_swallowed(tmp_path, monkeypatch):
    """The old `except OperationalError: pass` hid this, and the process
    continued against a schema missing a column it believed it had.

    Driven with a genuinely malformed column declaration rather than a mocked
    error, so what is under test is the real classification path: SQLite raises
    OperationalError, and it is not "duplicate column name", so it must
    propagate rather than be absorbed.
    """
    db = _make_legacy_db(tmp_path)
    monkeypatch.setattr(
        Store, "_MIGRATIONS", {"messages": [("broken", "NOT-A-TYPE(((")]}
    )
    with pytest.raises(MigrationError, match="ADD COLUMN"):
        Store(db)


def test_the_database_is_untouched_after_a_failed_alter(tmp_path, monkeypatch):
    """Failing loudly is only half of it — the failure must also leave nothing
    behind to be half-migrated."""
    db = _make_legacy_db(tmp_path)
    monkeypatch.setattr(
        Store,
        "_MIGRATIONS",
        {"messages": [("added_ok", "TEXT"), ("broken", "NOT-A-TYPE(((")]},
    )
    with pytest.raises(MigrationError):
        Store(db)

    probe = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in probe.execute("PRAGMA table_info(messages)")}
        assert "added_ok" not in cols, "the earlier successful ALTER must roll back too"
        assert probe.execute("PRAGMA user_version").fetchone()[0] == 0
        assert probe.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        probe.close()


# --------------------------------------------------------------------------
# integrity + backup
# --------------------------------------------------------------------------


def test_migration_refuses_a_corrupt_database(plain_db, monkeypatch):
    """Migrating an already-corrupt database turns a recoverable problem into
    a confusing one."""
    conn, db = plain_db
    import openai4s.storage.migrations as migrations

    monkeypatch.setattr(migrations, "integrity_ok", lambda c: False)
    with pytest.raises(MigrationError, match="integrity_check"):
        run_migrations(conn, db, {1: ("x", lambda c: None)}, target=1)
    assert current_version(conn) == 0


def test_integrity_ok_on_a_healthy_database(plain_db):
    conn, _ = plain_db
    assert integrity_ok(conn) is True


def test_backup_uses_the_sqlite_api_not_a_file_copy(plain_db):
    """A `cp` of a live database can capture a hot journal or torn pages — a
    backup that only fails to restore later, when it is needed."""
    conn, db = plain_db
    backup = backup_database(db, 0)
    assert backup is not None and backup.exists()
    restored = sqlite3.connect(str(backup))
    try:
        assert restored.execute("SELECT a FROM t").fetchone()[0] == "precious-data"
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_no_backup_for_a_database_with_nothing_to_lose(tmp_path):
    assert backup_database(tmp_path / "absent.db", 0) is None


def test_backup_is_owner_only(plain_db):
    """The backup carries the same plaintext credentials as the database."""
    import os

    if os.name != "posix":
        pytest.skip("POSIX modes only")
    from openai4s.security.permissions import is_owner_only

    conn, db = plain_db
    backup = backup_database(db, 0)
    assert is_owner_only(backup)


# --------------------------------------------------------------------------
# PRAGMA policy
# --------------------------------------------------------------------------


def test_foreign_keys_is_on(tmp_path):
    """A no-op today — the schema declares no REFERENCES — but the pragma is
    per-connection and OFF by default, so without this the day someone adds a
    foreign key it would be silently unenforced and read as documentation."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_busy_timeout_is_set(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_synchronous_stays_full(tmp_path):
    """FULL is the safe end, and this database holds an audit ledger. Pinned so
    a future 'performance' change has to argue for the durability trade."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
