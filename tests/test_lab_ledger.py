"""Offline proofs for durable Lab transactions and aggregate boundaries."""

import json
import sqlite3

import pytest

from openai4s.lab.models import ErrorCode, LabError
from openai4s.storage.lab import _COMMAND_SOURCES, LabLedger
from openai4s.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "ledger.sqlite")
    yield value
    value.close()


@pytest.fixture
def ledger(store):
    return LabLedger(store._conn, store._lock, clock_ms=lambda: 123456)


def run_input(run_id="r1", root="root1", **changes):
    return {
        "run_id": run_id,
        "root_frame_id": root,
        "mode": "simulation",
        "backend": "fake",
        "device_id": "fake.extractor",
        "profile": "toy",
        "adapter_version": "1",
        "capability_revision": "rev",
        "descriptor": {},
        "config": {"seed": 7},
        "config_hash": "config1",
        "budgets": {},
        "create_idempotency_key": run_id,
        **changes,
    }


def command_input(command_id="c1", run_id="r1", **changes):
    return {
        "command_id": command_id,
        "run_id": run_id,
        "idempotency_key": command_id,
        "request_hash": "request1",
        "operation": "mix_model",
        "request": {},
        "origin": "system",
        "state": "admitted",
        **changes,
    }


def observation():
    return {
        "channels": [{"name": "layers", "source": "simulated_sensor", "value": [0.1]}],
        "sim_time": 0.1,
        "sim_time_unit": "model_time",
    }


def evaluation():
    return {
        "reward": 0.75,
        "ground_truth": {"private_test_value": 0.42},
        "metrics": {"test": True},
    }


def ready(ledger, run_id="r1", root="root1"):
    ledger.create_run(run_input(run_id, root))
    return ledger.append_initial_observation(
        run_id, observation=observation(), evaluation=evaluation()
    )


def dispatched(ledger, command_id="c1", run_id="r1", keys=("resource",)):
    ledger.insert_command(command_input(command_id, run_id))
    return ledger.begin_dispatch(
        command_id,
        resource_keys=keys,
        expected_revision=ledger.get_run(run_id)["revision"],
    )


def receipt(**changes):
    return {
        "provider_command_id": "c1",
        "applied": True,
        "status": "succeeded",
        "error": None,
        "raw": {"terminated": False, "truncated": False},
        "end_reason": None,
        "evaluation": evaluation(),
        "provider_action": {"index": 3},
        **changes,
    }


def force_state(store, command_id, state, *, fencing_token=None):
    """Seed a mid-life command for a fixture; the ledger itself never inserts one."""
    store._conn.execute(
        "UPDATE lab_commands SET state=?,fencing_token=? WHERE command_id=?",
        (state, fencing_token, command_id),
    )
    store._conn.commit()


def snapshot(store):
    # Includes all tables, schema, and sqlite_sequence (event cursors).
    return list(store._conn.iterdump())


def assert_code(code, invoke):
    with pytest.raises(LabError) as caught:
        invoke()
    assert isinstance(caught.value.code, ErrorCode)
    assert caught.value.code == ErrorCode(code)
    return caught.value


def test_create_run_idempotency_and_conflict(ledger, store):
    first, created = ledger.create_run(run_input(status="ended", revision=8))
    assert created and first["status"] == "creating" and first["revision"] == 0
    assert first["created_at"] == first["updated_at"] == 123456
    before = snapshot(store)
    assert ledger.create_run(
        run_input(run_id="ignored", create_idempotency_key="r1")
    ) == (first, False)
    assert snapshot(store) == before
    assert_code(
        "idempotency_conflict",
        lambda: ledger.create_run(run_input(config_hash="different")),
    )
    assert snapshot(store) == before
    assert ledger.get_run("missing") is None
    assert ledger.list_runs("root1") == [first]
    assert ledger.list_runs("other") == []
    assert ledger.nonterminal_runs() == [first]
    assert ledger.events_since("root1")[0]["state"] == "creating"


def test_insert_command_idempotency_sequence_and_rejected_accounting(ledger, store):
    ledger.create_run(run_input())
    ledger.end_run("r1", end_reason="create_failed", status="failed")
    first, inserted = ledger.insert_command(
        command_input(
            state="rejected", error_code="invalid_parameters", error="bad request"
        )
    )
    assert inserted and first["seq"] == 1 and first["completed_at"] == 123456
    before = snapshot(store)
    assert ledger.insert_command(command_input(state="rejected")) == (first, False)
    assert snapshot(store) == before
    assert_code(
        "idempotency_conflict",
        lambda: ledger.insert_command(command_input(request_hash="different")),
    )
    assert snapshot(store) == before
    second, inserted = ledger.insert_command(command_input("c2"))
    assert inserted and second["seq"] == 2
    assert ledger.get_run("r1")["command_count"] == 2
    assert ledger.list_commands("r1", after_seq=1, limit=1) == [second]
    assert ledger.get_command("missing") is None
    assert ledger.inflight_commands("r1") == [second]
    assert ledger.inflight_commands("other") == []


def test_command_sources_match_contract():
    assert _COMMAND_SOURCES == {
        "awaiting_approval": frozenset({"created"}),
        "admitted": frozenset({"created", "awaiting_approval"}),
        "dispatching": frozenset({"admitted"}),
        "running": frozenset({"dispatching"}),
        "succeeded": frozenset(
            {"dispatching", "running", "stop_requested", "outcome_unknown"}
        ),
        "failed": frozenset(
            {"dispatching", "running", "stop_requested", "outcome_unknown"}
        ),
        "rejected": frozenset({"created", "awaiting_approval", "admitted"}),
        "not_dispatched": frozenset(
            {
                "created",
                "awaiting_approval",
                "admitted",
                "dispatching",
                "outcome_unknown",
            }
        ),
        "outcome_unknown": frozenset({"dispatching", "running", "stop_requested"}),
        "stop_requested": frozenset({"dispatching", "running"}),
        "stopped": frozenset({"stop_requested"}),
    }


def test_transition_cas_and_fields(ledger, store):
    ledger.create_run(run_input())
    ledger.insert_command(command_input(state="created"))
    before = snapshot(store)
    assert not ledger.transition_command(
        "c1", to_state="admitted", from_states={"awaiting_approval"}
    )
    assert snapshot(store) == before
    with pytest.raises(ValueError):
        ledger.transition_command("c1", to_state="created")
    with pytest.raises(ValueError):
        ledger.transition_command("c1", to_state="succeeded", from_states={"created"})
    assert_code(
        "invalid_parameters",
        lambda: ledger.transition_command("c1", to_state="admitted", request={}),
    )
    assert ledger.transition_command("c1", to_state="admitted", approval_ref="approved")
    assert ledger.transition_command(
        "c1", to_state="rejected", error="x" * 2100, error_code="invalid_parameters"
    )
    row = ledger.get_command("c1")
    assert row["approval_ref"] == "approved" and row["completed_at"] == 123456
    assert len(row["error"]) == 2000 and row["error"].endswith("…[truncated]")


@pytest.mark.parametrize(
    "reason",
    [
        "stale_revision",
        "busy",
        "creating",
        "quarantined",
        "ended",
        "failed",
        "held",
        "lease_quarantined",
        "unknown_holder",
    ],
)
def test_dispatch_refusal_rolls_back_every_table(ledger, store, reason):
    ready(ledger)
    ledger.insert_command(command_input())
    revision = 0
    expected_code = {
        "busy": "resource_busy",
        "creating": "resource_busy",
        "quarantined": "resource_quarantined",
        "ended": "run_ended",
        "failed": "run_ended",
        "held": "resource_busy",
        "lease_quarantined": "resource_quarantined",
        "unknown_holder": "resource_busy",
    }.get(reason, reason)
    if reason == "stale_revision":
        revision = 99
    elif reason in {"busy", "creating", "quarantined", "ended", "failed"}:
        store._conn.execute("UPDATE lab_runs SET status=?", (reason,))
    else:
        ledger.insert_command(command_input("holder"))
        force_state(
            store,
            "holder",
            "outcome_unknown" if reason == "unknown_holder" else "running",
        )
        store._conn.execute(
            "INSERT INTO lab_leases(resource_key,run_id,root_frame_id,state,holder_command_id,updated_at) VALUES('z-held','r1','root1',?,'holder',1)",
            ("quarantined" if reason == "lease_quarantined" else "held",),
        )
    store._conn.commit()
    before = snapshot(store)
    error = assert_code(
        expected_code,
        lambda: ledger.begin_dispatch(
            "c1", resource_keys=["a-new", "z-held"], expected_revision=revision
        ),
    )
    if reason == "stale_revision":
        assert error.details == {"revision": 0}
    assert snapshot(store) == before


def test_dispatch_fencing_stale_lease_and_order(ledger, store):
    ready(ledger)
    ledger.insert_command(command_input("old"))
    force_state(store, "old", "failed")
    store._conn.execute(
        "INSERT INTO lab_leases(resource_key,run_id,root_frame_id,state,holder_command_id,fencing_token,updated_at) VALUES('b','r1','root1','held','old',8,1)"
    )
    store._conn.commit()
    ledger.insert_command(command_input())
    assert (
        ledger.begin_dispatch(
            "c1", resource_keys=["b", "a", "b"], expected_revision=0, now_ms=777
        )
        == 9
    )
    command = ledger.get_command("c1")
    assert command["resources"] == ["a", "b"] and command["fencing_token"] == 9
    assert command["dispatched_at"] == command["updated_at"] == 777
    assert ledger.get_run("r1")["status"] == "busy"
    leases = [
        dict(row)
        for row in store._conn.execute("SELECT * FROM lab_leases ORDER BY resource_key")
    ]
    assert [row["fencing_token"] for row in leases] == [9, 9]
    assert all(
        row["state"] == "held"
        and row["holder_command_id"] == "c1"
        and row["acquired_at"] == 777
        for row in leases
    )
    assert ledger.mark_not_dispatched(
        "c1", error_code="provider_unavailable", error="not sent"
    )
    ledger.insert_command(command_input("c2"))
    assert ledger.begin_dispatch("c2", resource_keys=["b"], expected_revision=0) == 10


@pytest.mark.parametrize(
    "operation",
    [
        "begin_dispatch",
        "record_receipt",
        "mark_outcome_unknown",
        "mark_not_dispatched",
        "append_initial_observation",
    ],
)
def test_composite_writes_are_one_transaction_and_roll_back(ledger, store, operation):
    if operation == "append_initial_observation":
        ledger.create_run(run_input())

        def invoke():
            return ledger.append_initial_observation(
                "r1", observation=observation(), evaluation=evaluation()
            )

    else:
        ready(ledger)
        ledger.insert_command(command_input())
        if operation != "begin_dispatch":
            ledger.begin_dispatch("c1", resource_keys=["a", "b"], expected_revision=0)
        invoke = {
            "begin_dispatch": lambda: ledger.begin_dispatch(
                "c1", resource_keys=["a", "b"], expected_revision=0
            ),
            "record_receipt": lambda: ledger.record_receipt(
                "c1",
                receipt=receipt(),
                observation=observation(),
                evaluation=evaluation(),
            ),
            "mark_outcome_unknown": lambda: ledger.mark_outcome_unknown(
                "c1", error="lost"
            ),
            "mark_not_dispatched": lambda: ledger.mark_not_dispatched(
                "c1", error_code="provider_unavailable", error="not sent"
            ),
        }[operation]
    # Failure happens after the main writes, including observation/evaluation.
    store._conn.execute(
        "CREATE TEMP TRIGGER fail_event BEFORE INSERT ON lab_events BEGIN SELECT RAISE(ABORT,'injected private payload'); END"
    )
    before = snapshot(store)
    trace = []
    store._conn.set_trace_callback(trace.append)
    try:
        error = assert_code("persistence_unavailable", invoke)
    finally:
        store._conn.set_trace_callback(None)
    assert "private payload" not in str(error)
    assert snapshot(store) == before
    assert trace.count("BEGIN IMMEDIATE") == 1 and trace.count("ROLLBACK") == 1
    assert "COMMIT" not in trace
    store._conn.execute("DROP TRIGGER fail_event")
    trace.clear()
    store._conn.set_trace_callback(trace.append)
    try:
        invoke()
    finally:
        store._conn.set_trace_callback(None)
    assert trace.count("BEGIN IMMEDIATE") == 1 and trace.count("COMMIT") == 1
    assert "ROLLBACK" not in trace


def test_receipt_applies_all_records_and_isolates_evaluation(ledger, store):
    ready(ledger)
    dispatched(ledger)
    original = receipt()
    result = ledger.record_receipt(
        "c1", receipt=original, observation=observation(), evaluation=evaluation()
    )
    run, command, obs = result["run"], result["command"], result["observation"]
    assert (
        run["revision"] == run["step_count"] == 1 and run["consecutive_failures"] == 0
    )
    assert (
        run["status"] == "ready"
        and run["raw_terminated"] is False
        and run["raw_truncated"] is False
    )
    assert command["state"] == "succeeded" and command["applied_revision"] == 1
    assert (
        command["completed_at"] == 123456
        and command["observation_id"] == obs["observation_id"]
    )
    assert (
        obs["sequence"] == 1
        and obs["command_id"] == "c1"
        and obs["root_frame_id"] == "root1"
    )
    assert (
        ledger.list_evaluations("r1")[-1]["ground_truth"]
        == evaluation()["ground_truth"]
    )
    assert ledger.list_evaluations("r1")[-1]["sequence"] == 1
    lease = store._conn.execute("SELECT * FROM lab_leases").fetchone()
    assert lease["state"] == "free" and lease["holder_command_id"] is None
    assert [e["kind"] for e in ledger.events_since("root1")[-3:]] == [
        "command",
        "observation",
        "run",
    ]
    assert command["provider_action"] == {"index": 3}
    assert (
        "provider_action" not in command["receipt"]
        and "evaluation" not in command["receipt"]
    )
    assert "private_test_value" not in json.dumps(
        result
    ) and "reward" not in json.dumps(result)
    assert original == receipt()
    before = snapshot(store)
    with pytest.raises(ValueError):
        ledger.record_receipt(
            "c1", receipt=original, observation=observation(), evaluation=evaluation()
        )
    assert snapshot(store) == before
    # Close/reopen the actual Store rather than relying on this repository's reads.
    path = store.db_path
    store.close()
    reopened = Store(path)
    try:
        assert reopened.lab.get_command("c1") == command
        assert reopened.lab.get_run("r1") == run
    finally:
        reopened.close()


def test_unapplied_receipt_does_not_create_observation(ledger):
    ready(ledger)
    dispatched(ledger)
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(
            applied=False,
            status="failed",
            error={"code": "precondition_failed", "message": "x" * 2100},
        ),
        observation=None,
        evaluation=evaluation(),
    )
    assert result["observation"] is None and result["command"]["observation_id"] is None
    assert result["run"]["revision"] == result["run"]["step_count"] == 0
    assert (
        result["run"]["consecutive_failures"] == 1
        and result["run"]["status"] == "ready"
    )
    assert (
        result["command"]["state"] == "failed"
        and result["command"]["error_code"] == "precondition_failed"
    )
    assert (
        len(result["command"]["error"])
        == len(result["command"]["receipt"]["error"]["message"])
        == 2000
    )
    assert (
        len(ledger.list_observations("r1")) == len(ledger.list_evaluations("r1")) == 1
    )


@pytest.mark.parametrize(
    "flags,reason,expected",
    [
        ({"terminated": True, "truncated": False}, None, "env_terminated"),
        ({"terminated": False, "truncated": True}, None, "env_terminated"),
        ({"terminated": False, "truncated": False}, "end_action", "end_action"),
    ],
)
def test_receipt_ends_run(ledger, flags, reason, expected):
    ready(ledger)
    dispatched(ledger)
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(raw=flags, end_reason=reason),
        observation=observation(),
        evaluation=None,
    )
    assert (
        result["run"]["status"] == "ended" and result["run"]["end_reason"] == expected
    )
    assert result["run"]["ended_at"] == 123456
    assert (
        result["run"]["raw_terminated"] is flags["terminated"]
        and result["run"]["raw_truncated"] is flags["truncated"]
    )
    assert ledger.nonterminal_runs() == []


@pytest.mark.parametrize("another_unknown", [False, True])
def test_unknown_quarantines_and_receipt_reconciles(ledger, store, another_unknown):
    ready(ledger)
    dispatched(ledger)
    assert ledger.mark_outcome_unknown("c1", error="lost")
    assert not ledger.mark_outcome_unknown("c1", error="lost twice")
    assert ledger.get_command("c1")["completed_at"] is None
    assert ledger.get_run("r1")["status"] == "quarantined"
    assert (
        store._conn.execute("SELECT state FROM lab_leases").fetchone()[0]
        == "quarantined"
    )
    ledger.insert_command(command_input("c2"))
    before = snapshot(store)
    assert_code(
        "resource_quarantined",
        lambda: ledger.begin_dispatch(
            "c2", resource_keys=["other"], expected_revision=0
        ),
    )
    assert snapshot(store) == before
    if another_unknown:
        ledger.insert_command(command_input("c3"))
        force_state(store, "c3", "outcome_unknown", fencing_token=1)
    result = ledger.record_receipt(
        "c1", receipt=receipt(), observation=observation(), evaluation=None
    )
    assert result["run"]["status"] == ("quarantined" if another_unknown else "ready")
    assert store._conn.execute("SELECT state FROM lab_leases").fetchone()[0] == "free"
    assert (
        result["command"]["state"] == "succeeded"
        and result["command"]["error_code"] is None
    )


def test_not_dispatched_only_frees_its_own_busy_run(ledger, store):
    ready(ledger)
    dispatched(ledger)
    ledger.insert_command(command_input("c2"))
    assert ledger.mark_not_dispatched(
        "c2", error_code="approval_denied", error="denied"
    )
    assert ledger.get_run("r1")["status"] == "busy"
    assert ledger.mark_not_dispatched(
        "c1", error_code="provider_unavailable", error="not sent"
    )
    assert ledger.get_run("r1")["status"] == "ready"
    assert store._conn.execute("SELECT state FROM lab_leases").fetchone()[0] == "free"
    assert ledger.get_command("c1")["completed_at"] == 123456
    before = snapshot(store)
    assert not ledger.mark_not_dispatched(
        "c1", error_code="provider_unavailable", error="again"
    )
    assert snapshot(store) == before


def test_end_run_and_quarantined_lease_survival(ledger, store):
    ledger.create_run(run_input(daemon_instance="daemon-test"))
    ledger.append_initial_observation("r1", observation=observation(), evaluation=None)
    dispatched(ledger)
    store._conn.execute(
        "INSERT INTO lab_leases(resource_key,run_id,root_frame_id,state,updated_at) VALUES('quarantine','r1','root1','quarantined',1)"
    )
    store._conn.commit()
    assert ledger.end_run("r1", end_reason="provider_lost")
    assert ledger.get_run("r1")["ended_at"] == 123456
    assert ledger.get_run("r1")["daemon_instance"] == "daemon-test"
    assert [
        r[0]
        for r in store._conn.execute(
            "SELECT state FROM lab_leases ORDER BY resource_key"
        )
    ] == ["quarantined", "free"]
    before = snapshot(store)
    assert not ledger.end_run("r1", end_reason="deleted")
    assert snapshot(store) == before
    assert ledger.mark_outcome_unknown("c1", error="provider lost")
    assert ledger.get_run("r1")["status"] == "ended"


def test_initial_observation_json_and_pagination(ledger, store):
    run = ready(ledger)
    assert run["status"] == "ready" and run["revision"] == run["step_count"] == 0
    initial = ledger.latest_observation("r1")
    assert (
        initial["sequence"] == 0
        and initial["command_id"] is None
        and initial["wall_time_ms"] == 123456
    )
    assert ledger.latest_observation("missing") is None
    assert ledger.list_observations("r1", after_sequence=0) == []
    assert ledger.list_evaluations("r1", limit=0) == []
    assert initial["channels"] == observation()["channels"]
    assert ledger.list_evaluations("r1")[0]["metrics"] == {"test": True}
    for row in [run, initial, ledger.list_evaluations("r1")[0]]:
        assert all(not k.endswith("_json") for k in row)
    assert (
        run["descriptor"] == {}
        and run["config"] == {"seed": 7}
        and run["budgets"] == {}
    )
    before = snapshot(store)
    with pytest.raises(ValueError):
        ledger.append_initial_observation(
            "r1", observation=observation(), evaluation=None
        )
    assert snapshot(store) == before


def lab_tables(store):
    return [
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'lab_*' ORDER BY name"
        )
    ]


@pytest.mark.parametrize("team_mode", ["0", "1"])
def test_every_lab_table_is_denied_to_agent_sql(store, monkeypatch, team_mode):
    monkeypatch.setenv("OPENAI4S_TEAM_MODE", team_mode)
    tables = lab_tables(store)
    assert len(tables) == 6
    for table in tables:
        with pytest.raises(PermissionError):
            store.query(f'SELECT * FROM "{table}"')
        assert table not in store.schema()


@pytest.mark.parametrize("entry", ["session", "project"])
def test_mechanical_lab_deletion_completeness(store, ledger, entry):
    store.create_project(project_id="deleted", name="Deleted")
    store.create_project(project_id="kept", name="Kept")
    roots = [
        store.new_frame(project_id=project, status="ready")
        for project in ("deleted", "kept")
    ]
    for i, root in enumerate(roots):
        ready(ledger, f"r{i}", root)
        dispatched(ledger, f"c{i}", f"r{i}", keys=[f"resource{i}"])
    tables = lab_tables(store)
    for table in tables:
        columns = store._conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        assert "root_frame_id" in {r[1] for r in columns}
        # A future seventh table is seeded too: forgetting its cleanup must fail.
        for i, root in enumerate(roots):
            if store._conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE root_frame_id=?', (root,)
            ).fetchone()[0]:
                continue
            values = {
                r[1]: (i + 1 if r[2] in {"INTEGER", "REAL"} else f"row-{i}")
                for r in columns
            }
            values["root_frame_id"] = root
            store._conn.execute(
                f'INSERT INTO "{table}" ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',
                tuple(values.values()),
            )
    store._conn.commit()
    kept = {
        table: [
            tuple(r)
            for r in store._conn.execute(
                f'SELECT * FROM "{table}" WHERE root_frame_id=?', (roots[1],)
            )
        ]
        for table in tables
    }
    counts = {
        table: store._conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE root_frame_id=?', (roots[0],)
        ).fetchone()[0]
        for table in tables
    }
    result = (
        store.delete_frame(roots[0])
        if entry == "session"
        else store.delete_project("deleted")
    )
    for table in tables:
        assert not store._conn.execute(
            f'SELECT * FROM "{table}" WHERE root_frame_id=?', (roots[0],)
        ).fetchall(), table
        assert [
            tuple(r)
            for r in store._conn.execute(
                f'SELECT * FROM "{table}" WHERE root_frame_id=?', (roots[1],)
            )
        ] == kept[table]
        assert result["deleted_rows"][table] == counts[table]


def test_events_are_scoped_monotonic_and_never_reused(ledger, store):
    root = store.new_frame(status="ready")
    ready(ledger, root=root)
    ready(ledger, "r2", "other")
    events = ledger.events_since(root)
    assert all(e["root_frame_id"] == root and e["run_id"] == "r1" for e in events)
    seqs = [e["event_seq"] for e in events]
    assert seqs == sorted(set(seqs)) and len(seqs) == 3
    assert ledger.latest_event_seq(root) == seqs[-1]
    assert ledger.events_since(root, after_seq=seqs[0], limit=1) == events[1:2]
    assert ledger.latest_event_seq("missing") == 0
    high = ledger.latest_event_seq("other")
    store.delete_frame(root)
    store._conn.execute("DELETE FROM lab_events WHERE root_frame_id='other'")
    store._conn.commit()
    assert ledger.events_since(root) == []
    ledger.create_run(run_input("r3", "third"))
    assert ledger.latest_event_seq("third") > high


@pytest.mark.parametrize("stage", ["begin", "commit", "read"])
def test_sqlite_boundary_errors_never_fall_back(ledger, store, stage):
    class FaultConnection:
        def execute(self, sql, params=()):
            if (stage == "begin" and sql == "BEGIN IMMEDIATE") or (
                stage == "read" and sql.startswith("SELECT")
            ):
                raise sqlite3.OperationalError("injected secret payload")
            return store._conn.execute(sql, params)

        def commit(self):
            raise sqlite3.OperationalError("injected secret payload")

        def rollback(self):
            store._conn.rollback()

    faulty = LabLedger(FaultConnection(), store._lock, clock_ms=lambda: 123456)
    before = snapshot(store)
    error = assert_code(
        "persistence_unavailable",
        lambda: (
            faulty.get_run("r1") if stage == "read" else faulty.create_run(run_input())
        ),
    )
    assert "secret payload" not in str(error)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"applied": True, "status": "failed"},
        {"applied": True, "status": "rejected"},
        {"applied": False, "status": "failed", "raw": {"terminated": "false"}},
        {"applied": False, "status": "failed", "raw": {"truncated": 1}},
    ],
    ids=[
        "applied-failed",
        "applied-rejected",
        "string-termination",
        "integer-truncation",
    ],
)
def test_invalid_receipt_cannot_advance_or_end_run(ledger, store, changes):
    ready(ledger)
    dispatched(ledger)
    before = snapshot(store)
    assert_code(
        "invalid_parameters",
        lambda: ledger.record_receipt(
            "c1", receipt=receipt(**changes), observation=observation(), evaluation=None
        ),
    )
    assert snapshot(store) == before


@pytest.mark.parametrize("target", ["succeeded", "failed"])
def test_unknown_requires_receipt_reconciliation(ledger, store, target):
    ready(ledger)
    dispatched(ledger)
    ledger.mark_outcome_unknown("c1", error="lost response")
    before = snapshot(store)
    with pytest.raises(ValueError, match="through its receipt"):
        ledger.transition_command("c1", to_state=target)
    assert snapshot(store) == before
    # Reconciliation remains possible after refusing the unverified terminal.
    applied = target == "succeeded"
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(
            applied=applied,
            status=target,
            error=(
                None
                if applied
                else {"code": "precondition_failed", "message": "refused"}
            ),
        ),
        observation=observation() if applied else None,
        evaluation=None,
    )
    assert result["command"]["state"] == target
    assert result["command"]["receipt"] is not None
    assert result["run"]["status"] == "ready"
    assert store._conn.execute("SELECT state FROM lab_leases").fetchone()[0] == "free"


@pytest.mark.parametrize(
    "target", ["succeeded", "failed", "outcome_unknown", "not_dispatched", "stopped"]
)
def test_a_bare_cas_cannot_take_a_dispatched_command_out_of_dispatch(
    ledger, store, target
):
    # Leaving dispatch also frees leases and moves the run; only the dedicated
    # methods do both, so a plain state CAS is refused before anything changes.
    ready(ledger)
    dispatched(ledger)
    before = snapshot(store)
    with pytest.raises(ValueError, match="through its receipt"):
        ledger.transition_command("c1", to_state=target)
    assert snapshot(store) == before
    assert ledger.transition_command("c1", to_state="stop_requested")


def test_proof_of_non_receipt_resolves_an_unknown_command_and_its_quarantine(
    ledger, store
):
    ready(ledger)
    dispatched(ledger)
    assert ledger.mark_outcome_unknown("c1", error="no reply")
    assert ledger.get_run("r1")["status"] == "quarantined"
    assert ledger.mark_not_dispatched(
        "c1", error_code="outcome_unknown", error="device never received it"
    )
    command = ledger.get_command("c1")
    assert command["state"] == "not_dispatched" and command["completed_at"]
    assert ledger.get_run("r1")["status"] == "ready"
    assert store._conn.execute("SELECT state FROM lab_leases").fetchone()[0] == "free"
    # The run accepts the next dispatch at the unchanged revision.
    assert dispatched(ledger, "c2") > 0


def test_every_non_terminal_run_can_end_but_only_a_creating_run_fails(ledger):
    ledger.create_run(run_input("creating"))
    assert ledger.end_run("creating", end_reason="deleted")
    ready(ledger, "ready-run")
    assert not ledger.end_run("ready-run", end_reason="create_failed", status="failed")
    assert ledger.end_run("ready-run", end_reason="stopped")
    assert ledger.get_run("ready-run")["status"] == "ended"
    ledger.create_run(run_input("broken"))
    assert ledger.end_run("broken", end_reason="create_failed", status="failed")


# -- W1 merge review: holes that let a caller skip the composite invariants --


@pytest.mark.parametrize(
    "state",
    [
        "dispatching",
        "running",
        "stop_requested",
        "outcome_unknown",
        "succeeded",
        "failed",
        "not_dispatched",
        "stopped",
    ],
)
def test_a_command_cannot_be_inserted_mid_life(ledger, store, state):
    ready(ledger)
    before = snapshot(store)
    assert_code(
        "invalid_parameters", lambda: ledger.insert_command(command_input(state=state))
    )
    assert snapshot(store) == before


def test_only_begin_dispatch_puts_a_command_in_dispatch(ledger, store):
    ready(ledger)
    ledger.insert_command(command_input())
    before = snapshot(store)
    with pytest.raises(ValueError, match="begin_dispatch"):
        ledger.transition_command("c1", to_state="dispatching")
    assert snapshot(store) == before


@pytest.mark.parametrize("state", ["created", "awaiting_approval"])
def test_an_unapproved_command_is_never_dispatched(ledger, store, state):
    ready(ledger)
    ledger.insert_command(command_input(state=state))
    before = snapshot(store)
    with pytest.raises(ValueError, match="admitted"):
        ledger.begin_dispatch("c1", resource_keys=["a"], expected_revision=0)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "keys",
    # A bare string is the bug: it used to lease each of its characters.
    ["beaker", "lab:dev#r1:beaker", b"a", [], [""], [3], ["lab:dev#other:beaker"]],
)
def test_resource_keys_are_names_of_this_run(ledger, store, keys):
    ready(ledger)
    ledger.insert_command(command_input())
    before = snapshot(store)
    assert_code(
        "invalid_parameters",
        lambda: ledger.begin_dispatch("c1", resource_keys=keys, expected_revision=0),
    )
    assert snapshot(store) == before
    assert ledger.begin_dispatch(
        "c1", resource_keys=["lab:dev#r1:beaker"], expected_revision=0
    )


def test_a_held_lease_without_a_holder_row_is_not_free(ledger, store):
    ready(ledger)
    store._conn.execute(
        "INSERT INTO lab_leases(resource_key,run_id,root_frame_id,state,holder_command_id,updated_at) VALUES('a','r1','root1','held','vanished',1)"
    )
    store._conn.commit()
    ledger.insert_command(command_input())
    assert_code(
        "resource_busy",
        lambda: ledger.begin_dispatch("c1", resource_keys=["a"], expected_revision=0),
    )


def test_a_success_resets_consecutive_failures(ledger):
    ready(ledger)
    dispatched(ledger)
    failed = ledger.record_receipt(
        "c1",
        receipt=receipt(
            applied=False,
            status="failed",
            error={"code": "precondition_failed", "message": "refused"},
        ),
        observation=None,
        evaluation=None,
    )
    assert failed["run"]["consecutive_failures"] == 1
    dispatched(ledger, "c2")
    ok = ledger.record_receipt(
        "c2",
        receipt=receipt(provider_command_id="c2"),
        observation=observation(),
        evaluation=None,
    )
    assert ok["run"]["consecutive_failures"] == 0


def test_host_rejections_count_as_consecutive_failures(ledger):
    # CONTRACT §7 counts failed AND rejected commands. The manager admits on
    # this column, so a rejection recorded either way must move it.
    ready(ledger)
    ledger.insert_command(
        command_input(
            "c0", state="rejected", error_code="unsupported_action", error="no"
        )
    )
    assert ledger.get_run("r1")["consecutive_failures"] == 1
    ledger.insert_command(command_input("c-busy"))
    assert ledger.transition_command(
        "c-busy", to_state="rejected", error_code="resource_busy", error="busy"
    )
    assert ledger.get_run("r1")["consecutive_failures"] == 2
    # Neither an admitted intent nor an unsuccessful CAS is a failure.
    ledger.insert_command(command_input("c-admitted", request_hash="other"))
    assert not ledger.transition_command(
        "c-busy", to_state="rejected", error_code="resource_busy", error="busy"
    )
    assert ledger.get_run("r1")["consecutive_failures"] == 2
    dispatched(ledger)
    ok = ledger.record_receipt(
        "c1", receipt=receipt(), observation=observation(), evaluation=None
    )
    assert ok["run"]["consecutive_failures"] == 0
    # An ended run's counters are final.
    ledger.end_run("r1", end_reason="stopped")
    ledger.insert_command(
        command_input("c-late", state="rejected", error_code="run_ended", error="x")
    )
    assert ledger.get_run("r1")["consecutive_failures"] == 0


def test_only_simulation_runs_and_complete_rows_are_created(ledger, store):
    before = snapshot(store)
    assert_code("mode_mismatch", lambda: ledger.create_run(run_input(mode="physical")))
    assert_code(
        "invalid_parameters", lambda: ledger.create_run(run_input(device_id=""))
    )
    assert snapshot(store) == before
    ledger.create_run(run_input())
    # Another key for a taken run id is a caller error, not an outage.
    assert_code(
        "invalid_parameters",
        lambda: ledger.create_run(run_input(create_idempotency_key="other")),
    )


def test_a_command_must_belong_to_its_runs_session(ledger, store):
    ready(ledger)
    before = snapshot(store)
    assert_code(
        "invalid_parameters",
        lambda: ledger.insert_command(command_input(root_frame_id="another-root")),
    )
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "pending"},
        {"provider_command_id": "someone-else"},
        {"applied": False, "status": "succeeded"},
        {"applied": False, "status": "failed", "error": None},
        {"applied": False, "status": "failed", "error": {"code": "bogus"}},
        {"error": {"code": "precondition_failed", "message": "but applied"}},
    ],
)
def test_a_contradictory_or_foreign_receipt_changes_nothing(ledger, store, changes):
    ready(ledger)
    dispatched(ledger)
    before = snapshot(store)
    assert_code(
        "invalid_parameters",
        lambda: ledger.record_receipt(
            "c1",
            receipt=receipt(**changes),
            observation=observation(),
            evaluation=None,
        ),
    )
    assert snapshot(store) == before


def test_a_receipt_needs_a_command_that_begin_dispatch_sent(ledger, store):
    ready(ledger)
    ledger.insert_command(command_input())
    force_state(store, "c1", "dispatching")
    before = snapshot(store)
    with pytest.raises(ValueError, match="begin_dispatch"):
        ledger.record_receipt(
            "c1", receipt=receipt(), observation=observation(), evaluation=None
        )
    assert snapshot(store) == before


def test_quarantine_lifts_only_for_the_unknown_command_it_was_for(ledger, store):
    ready(ledger)
    dispatched(ledger)
    ledger.mark_outcome_unknown("c1", error="lost")
    ledger.insert_command(command_input("c2"))
    force_state(store, "c2", "dispatching", fencing_token=5)
    result = ledger.record_receipt(
        "c2",
        receipt=receipt(provider_command_id="c2"),
        observation=observation(),
        evaluation=None,
    )
    assert result["run"]["status"] == "quarantined"


def test_a_run_stays_busy_while_another_command_is_in_dispatch(ledger, store):
    ready(ledger)
    dispatched(ledger)
    ledger.insert_command(command_input("c2"))
    force_state(store, "c2", "dispatching", fencing_token=5)
    result = ledger.record_receipt(
        "c1", receipt=receipt(), observation=observation(), evaluation=None
    )
    assert result["run"]["status"] == "busy"


def test_a_late_receipt_never_rewrites_an_ended_run(ledger, store):
    ready(ledger)
    dispatched(ledger)
    ledger.mark_outcome_unknown("c1", error="lost")
    assert ledger.end_run("r1", end_reason="stopped")
    ended = ledger.get_run("r1")
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(
            end_reason="end_action", raw={"terminated": True, "truncated": False}
        ),
        observation=observation(),
        evaluation=None,
    )
    assert result["command"]["state"] == "succeeded"
    assert result["run"] == ended


def test_an_unapplied_terminal_receipt_keeps_the_raw_flags(ledger):
    ready(ledger)
    dispatched(ledger)
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(
            applied=False,
            status="failed",
            error={"code": "run_ended", "message": "done"},
            raw={"terminated": True, "truncated": False},
        ),
        observation=None,
        evaluation=None,
    )
    run = result["run"]
    assert run["status"] == "ended" and run["end_reason"] == "env_terminated"
    assert run["raw_terminated"] is True and run["raw_truncated"] is False


def test_a_stored_receipt_keeps_only_contract_keys(ledger):
    ready(ledger)
    dispatched(ledger)
    result = ledger.record_receipt(
        "c1",
        receipt=receipt(reward=0.9, info={"ground_truth": {"private": 1}}),
        observation=observation(),
        evaluation=None,
    )
    stored = result["command"]["receipt"]
    assert set(stored) <= {
        "provider_command_id",
        "applied",
        "status",
        "error",
        "raw",
        "end_reason",
        "sim_time",
        "step_index",
    }
    assert "private" not in json.dumps(result["command"])


def test_ending_a_run_needs_a_reason_and_a_valid_error_code_needs_the_table(
    ledger, store
):
    ready(ledger)
    assert_code("invalid_parameters", lambda: ledger.end_run("r1", end_reason=None))
    assert ledger.get_run("r1")["status"] == "ready"
    ledger.insert_command(command_input(state="created"))
    assert_code(
        "invalid_parameters",
        lambda: ledger.transition_command(
            "c1", to_state="rejected", error_code="bogus", error="no"
        ),
    )
