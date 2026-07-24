"""One compute-job vocabulary, enforced on write.

The vocabulary used to live in three places that disagreed:

  * ``LIVE_STATES`` named two states nothing ever wrote (``queued``,
    ``submitted``) and included ``staging``;
  * ``ComputeManager._live_count`` omitted ``staging``, so a row left there by
    a crash between claiming a job and submitting it was rehydrated on every
    restart, reported by ``reconcile()`` forever, and held no slot — nothing
    would ever notice it;
  * the SDK cached on ``timeout``/``harvesting``, neither of which the host
    produces.

Nothing enforced any of it: the repository's ``update()`` took any string, so
a typo was a state and a terminal job could be re-opened by a late probe.
"""
import sqlite3
import types

import pytest

from openai4s.compute import states
from openai4s.compute.states import IllegalTransition
from openai4s.config import Config
from openai4s.store import get_store


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


# --------------------------------------------------------------------------
# the vocabulary itself
# --------------------------------------------------------------------------


def test_terminal_states_are_mutually_exclusive_with_live_ones():
    """The scorecard's requirement: no job is in two end states at once, and
    no end state doubles as a live one."""
    assert not set(states.LIVE_STATES) & set(states.TERMINAL_STATES)
    assert set(states.ALL_STATES) == set(states.LIVE_STATES) | set(
        states.TERMINAL_STATES
    )


def test_unknown_is_live_not_terminal():
    """`unknown` means the remote op may or may not have landed. Treating it
    as finished is how a sandbox bills unnoticed after a restart: it would not
    be rehydrated, would hold no slot, and nothing would reconcile it."""
    assert states.is_live(states.UNKNOWN)
    assert not states.is_terminal(states.UNKNOWN)
    # ...and it must stay resolvable in both directions.
    assert states.can_transition(states.UNKNOWN, states.SUCCEEDED)
    assert states.can_transition(states.UNKNOWN, states.FAILED)


def test_a_terminal_job_cannot_be_reopened():
    """A late probe arriving after the job ended must not resurrect work
    nobody is tracking any more."""
    for terminal in states.TERMINAL_STATES:
        for target in states.ALL_STATES:
            if target == terminal:
                continue
            assert not states.can_transition(
                terminal, target
            ), f"{terminal} -> {target} must be refused"


def test_repeating_a_live_state_is_allowed():
    """Probes are naturally repeated; a no-op write is not a violation."""
    for state in states.LIVE_STATES:
        assert states.can_transition(state, state)


def test_rewriting_a_terminal_state_with_itself_is_refused():
    """A terminal status write is not a no-op: it carries the manifest, the
    digest, the reason and the terminal timestamp that established it. Two
    pollers that both read `running` could each reach the write, and the
    second — arriving after the remote directory was reused — used to commit
    its bytes over the row the first one's caller had already been told about.
    Same-state was legal, so the compare-and-swap could not see it."""
    for terminal in states.TERMINAL_STATES:
        assert not states.can_transition(
            terminal, terminal
        ), f"{terminal} -> {terminal} must be refused; terminal evidence is written once"


def test_staging_is_live_everywhere_it_is_live_anywhere():
    """The exact divergence that let a crashed claim linger unnoticed."""
    assert states.STAGING in states.LIVE_STATES
    assert states.is_live(states.STAGING)


def test_staging_can_reach_a_verified_terminal_state_directly():
    """Persisting the intermediate `running` is best-effort. A submit that
    landed and finished can have its result verify a terminal state while the
    durable row is still `staging`; forbidding that edge left the row — and
    every later result — stuck there."""
    for terminal in (
        states.SUCCEEDED,
        states.TIMED_OUT,
        states.FAILED,
        states.CANCELLED,
    ):
        assert states.can_transition(states.STAGING, terminal), (
            f"staging -> {terminal} must be allowed, or a best-effort running "
            f"persist failure strands the job"
        )


# --------------------------------------------------------------------------
# enforcement at the write
# --------------------------------------------------------------------------


def test_the_repository_refuses_an_unknown_status(store):
    store.create_compute_job(job_id="j1", provider="ssh:lab", status=states.STAGING)
    with pytest.raises(IllegalTransition):
        store.update_compute_job("j1", status="whatever")


def test_the_repository_refuses_an_illegal_transition(store):
    store.create_compute_job(job_id="j2", provider="ssh:lab", status=states.STAGING)
    store.update_compute_job("j2", status=states.RUNNING)
    store.update_compute_job("j2", status=states.SUCCEEDED)
    with pytest.raises(IllegalTransition):
        store.update_compute_job("j2", status=states.RUNNING)


def test_the_repository_refuses_a_second_terminal_write_of_the_same_status(store):
    """The stale-poller overwrite, at the write. A row that already holds its
    terminal evidence must not have the manifest, digest, reason or terminal
    timestamp replaced by a later poll that observed a reused remote."""
    store.create_compute_job(job_id="j2b", provider="ssh:lab", status=states.STAGING)
    store.update_compute_job(
        "j2b",
        status=states.SUCCEEDED,
        integrity_sha256="a" * 64,
        artifact_manifest=[{"path": "out.csv", "sha256": "a" * 64, "size": 3}],
        reason="",
        terminal_at=111,
    )
    with pytest.raises(IllegalTransition):
        store.update_compute_job(
            "j2b",
            status=states.SUCCEEDED,
            integrity_sha256="b" * 64,
            artifact_manifest=[],
            reason="clobbered",
            terminal_at=222,
        )
    row = store.get_compute_job("j2b")
    assert row["integrity_sha256"] == "a" * 64
    assert row["artifact_manifest"] == [
        {"path": "out.csv", "sha256": "a" * 64, "size": 3}
    ]
    assert row["reason"] == ""
    assert row["terminal_at"] == 111


def test_a_legal_transition_still_writes(store):
    store.create_compute_job(job_id="j3", provider="ssh:lab", status=states.STAGING)
    store.update_compute_job("j3", status=states.RUNNING)
    assert store.get_compute_job("j3")["status"] == states.RUNNING


def test_the_termination_reason_survives_the_round_trip(store):
    store.create_compute_job(job_id="j4", provider="ssh:lab", status=states.STAGING)
    store.update_compute_job(
        "j4",
        status=states.FAILED,
        termination_reason=states.REASON_OUTPUTS_UNVERIFIED,
    )
    row = store.get_compute_job("j4")
    assert row["status"] == states.FAILED
    assert row["termination_reason"] == states.REASON_OUTPUTS_UNVERIFIED


# --------------------------------------------------------------------------
# the migration actually moves old rows
# --------------------------------------------------------------------------


def _legacy_db(path, rows):
    """A v1 database holding rows in the pre-migration vocabulary."""
    store = get_store(Config(data_dir=path).db_path)
    db = store.db_path
    store.close()
    conn = sqlite3.connect(str(db))
    for job_id, status in rows:
        conn.execute(
            "INSERT INTO compute_jobs"
            "(job_id,provider,status,created_at,updated_at) VALUES(?,?,?,1,1)",
            (job_id, "ssh:lab", status),
        )
    # Rewind to before the compute-state migration so opening re-applies it.
    conn.execute("PRAGMA user_version = 1")
    conn.execute("DELETE FROM schema_migrations WHERE version=2")
    conn.commit()
    conn.close()
    return db


def test_the_migration_renames_done_and_folds_the_two_lost_states(tmp_path):
    """`done` is a rename, but the other two carried information: `incomplete`
    meant rc==0 with unverifiable outputs, and `closed` meant the user let go
    of the handle. Mapping them onto a status without recording why would
    destroy the distinction."""
    db = _legacy_db(
        tmp_path,
        [
            ("old-done", "done"),
            ("old-incomplete", "incomplete"),
            ("old-closed", "closed"),
        ],
    )

    store = get_store(db)
    by_id = {row["job_id"]: row for row in store.list_compute_jobs()}

    assert by_id["old-done"]["status"] == states.SUCCEEDED
    assert by_id["old-done"]["termination_reason"] is None

    assert by_id["old-incomplete"]["status"] == states.FAILED
    assert (
        by_id["old-incomplete"]["termination_reason"]
        == states.REASON_OUTPUTS_UNVERIFIED
    )

    assert by_id["old-closed"]["status"] == states.CANCELLED
    assert by_id["old-closed"]["termination_reason"] == states.REASON_HANDLE_CLOSED


def test_the_migration_is_idempotent(tmp_path):
    """A re-run after a partial apply must converge, not double-write."""
    db = _legacy_db(tmp_path, [("j", "done")])
    first = get_store(db)
    assert first.get_compute_job("j")["status"] == states.SUCCEEDED
    first.close()

    again = get_store(db)
    assert again.get_compute_job("j")["status"] == states.SUCCEEDED
    assert again.schema_state()["current"] is True


def test_a_migrated_job_is_not_rehydrated_as_live(tmp_path):
    """`done` and `closed` were both terminal before the rename; if the fold
    landed them anywhere live, every historical job would come back holding a
    concurrency slot."""
    from openai4s.compute.manager import ComputeManager

    db = _legacy_db(tmp_path, [("a", "done"), ("b", "closed"), ("c", "incomplete")])
    get_store(db)  # apply the migration

    cfg = types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=db,
    )
    (tmp_path / "skills").mkdir(exist_ok=True)
    assert ComputeManager(cfg)._live_count() == 0


def test_the_manifest_migration_upgrades_a_pre_manifest_database(tmp_path):
    """Migration 3 adds the manifest columns. Historical rows keep NULL: a
    harvest that happened before anything hashed it cannot be reconstructed,
    and inventing a manifest for it would be worse than admitting so."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    db = store.db_path
    store.close()

    conn = sqlite3.connect(str(db))
    columns = [r[1] for r in conn.execute("PRAGMA table_info(compute_jobs)")]
    keep = [c for c in columns if c not in ("artifact_manifest", "integrity_sha256")]
    conn.execute("ALTER TABLE compute_jobs RENAME TO _old")
    conn.execute(f"CREATE TABLE compute_jobs AS SELECT {','.join(keep)} FROM _old")
    conn.execute("DROP TABLE _old")
    conn.execute(
        "INSERT INTO compute_jobs(job_id,provider,status,created_at,updated_at)"
        " VALUES('legacy-1','ssh:lab','succeeded',1,1)"
    )
    conn.execute("PRAGMA user_version = 2")
    conn.execute("DELETE FROM schema_migrations WHERE version=3")
    conn.commit()
    conn.close()

    reopened = get_store(db)
    row = reopened.get_compute_job("legacy-1")

    assert row["status"] == states.SUCCEEDED, "the historical row survives"
    assert row["artifact_manifest"] is None
    assert row["integrity_sha256"] is None
    # This test is about migrations 1-3; asserting the whole list would make it
    # fail every time an unrelated later migration is added.
    assert [m["name"] for m in reopened.schema_state()["applied"]][:3] == [
        "legacy_baseline",
        "compute_job_states",
        "compute_job_manifest",
    ]
