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


def test_repeating_the_current_state_is_allowed():
    """Probes are naturally repeated; a no-op write is not a violation."""
    for state in states.ALL_STATES:
        assert states.can_transition(state, state)


def test_staging_is_live_everywhere_it_is_live_anywhere():
    """The exact divergence that let a crashed claim linger unnoticed."""
    assert states.STAGING in states.LIVE_STATES
    assert states.is_live(states.STAGING)


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
