"""Contracts for the append-only canonical Action Ledger repository."""

from __future__ import annotations

import inspect
import sqlite3
import threading

import pytest

from openai4s.storage.actions import ActionLedgerRepository, AttemptStateError
from openai4s.storage.auto_mode import AutoModeConflictError
from openai4s.storage.snapshots import revert_recovery_setting_key
from openai4s.store import Store


def _store(tmp_path) -> Store:
    return Store(tmp_path / "openai4s.db")


def test_canonical_groups_events_and_normalized_assistant_message_roundtrip(tmp_path):
    store = _store(tmp_path)
    code_group = store.append_action_group(
        group_id="ag-code",
        root_frame_id="root-1",
        turn_id="turn-1",
        kind="code",
        provider="ark",
        model="science-model",
        wire_state={"response_id": "resp-1", "cursor": 7},
        assistant_content="I will calculate this.",
        assistant_message={
            "role": "assistant",
            "content": "I will calculate this.",
            "tool_calls": [],
        },
        created_at=100,
    )
    assert code_group["ordinal"] == 0
    assert code_group["branch_id"] == "root-1"
    assert code_group["wire_state"] == {"cursor": 7, "response_id": "resp-1"}
    assert code_group["assistant_message"] == {
        "role": "assistant",
        "content": "I will calculate this.",
        "tool_calls": [],
    }
    assert code_group["events"] == []

    proposed = store.append_action_event(
        event_id="ae-code-proposed",
        group_id="ag-code",
        type="proposed",
        action_id="action-code",
        canonical_arguments={"language": "python", "code": "print(1)"},
        raw_arguments="```python\nprint(1)\n```",
        resource_keys=["kernel:python"],
        created_at=101,
    )
    assert proposed["canonical_arguments"] == {
        "code": "print(1)",
        "language": "python",
    }
    assert proposed["raw_arguments"] == "```python\nprint(1)\n```"
    assert proposed["resource_keys"] == ["kernel:python"]

    tool_group = store.append_tool_action_group(
        group_id="ag-tools",
        root_frame_id="root-1",
        turn_id="turn-2",
        provider="ark",
        model="science-model",
        wire_state={"response_id": "resp-2"},
        assistant_content=None,
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "wire-call-1", "name": "web_search", "arguments": {}}
            ],
        },
        events=[
            {
                "sequence": 0,
                "type": "proposed",
                "event_id": "ae-tool-proposed",
                "action_id": "action-tool",
                "tool_call_id": "call-1",
                "wire_id": "wire-call-1",
                "canonical_arguments": {"query": "NIF3"},
                "raw_arguments": '{"query":"NIF3"}',
                "side_effect_class": "read_only",
                "resource_keys": ["network:search"],
            },
            {
                "sequence": 1,
                "type": "result",
                "event_id": "ae-tool-result",
                "action_id": "action-tool",
                "tool_call_id": "call-1",
                "wire_id": "wire-call-1",
                "result": {"items": [{"title": "NIF3"}]},
            },
        ],
        created_at=102,
    )
    assert tool_group["ordinal"] == 1
    assert [event["type"] for event in tool_group["events"]] == [
        "proposed",
        "result",
    ]
    assert tool_group["events"][1]["result"] == {"items": [{"title": "NIF3"}]}

    store.append_action_group(
        group_id="ag-fork",
        root_frame_id="root-1",
        branch_id="branch-fork",
        turn_id="turn-fork",
        ordinal=0,
        kind="system",
        assistant_message={"role": "system", "content": "forked"},
        created_at=103,
    )

    canonical = store.list_action_groups("root-1")
    assert [group["group_id"] for group in canonical] == ["ag-code", "ag-tools"]
    assert [group["ordinal"] for group in canonical] == [0, 1]
    assert [event["sequence"] for event in canonical[1]["events"]] == [0, 1]
    assert [
        group["group_id"]
        for group in store.list_action_groups("root-1", turn_id="turn-2")
    ] == ["ag-tools"]
    assert [
        group["group_id"]
        for group in store.list_action_groups("root-1", after_ordinal=0)
    ] == ["ag-tools"]
    assert [
        group["group_id"]
        for group in store.list_action_groups("root-1", branch_id="branch-fork")
    ] == ["ag-fork"]

    # Internal provider state and raw arguments are not exposed through the
    # read-only in-kernel SQL API.
    with pytest.raises(PermissionError):
        store.query("SELECT * FROM action_groups")
    assert "action_groups" not in store.schema()

    store.close()
    reopened = Store(tmp_path / "openai4s.db")
    assert (
        reopened.get_action_group("ag-tools")["assistant_message"]["tool_calls"][0][
            "id"
        ]
        == "wire-call-1"
    )
    reopened.close()


def test_latest_group_ordinal_is_an_index_probe_per_kind_and_branch(tmp_path):
    store = _store(tmp_path)
    for ordinal, kind in enumerate(("user", "native_tools", "user", "code")):
        store.append_action_group(
            group_id=f"ag-{ordinal}",
            root_frame_id="root-1",
            turn_id="turn-1",
            kind=kind,
            provider="ark",
            model="science-model",
            assistant_content="",
            assistant_message={"role": "assistant", "content": ""},
            created_at=100 + ordinal,
        )
    assert store.latest_action_group_ordinal("root-1") == 3
    assert store.latest_action_group_ordinal("root-1", kind="user") == 2
    assert store.latest_action_group_ordinal("root-1", kind="finalize") is None
    assert store.latest_action_group_ordinal("root-1", branch_id="other") is None
    assert store.latest_action_group_ordinal("root-2") is None
    plan = store._actions._connection.execute(
        "EXPLAIN QUERY PLAN SELECT ordinal FROM action_groups WHERE "
        "root_frame_id=? AND branch_id=? AND kind=? ORDER BY ordinal DESC LIMIT 1",
        ("root-1", "root-1", "user"),
    ).fetchall()
    assert any("ux_action_group_ordinal" in str(tuple(row)) for row in plan), plan
    store.close()


def test_groups_and_events_cannot_overwrite_and_tool_group_is_atomic(tmp_path):
    store = _store(tmp_path)
    original = store.append_action_group(
        group_id="ag-original",
        root_frame_id="root",
        turn_id="turn",
        ordinal=0,
        kind="code",
        assistant_content="original",
        assistant_message={"role": "assistant", "content": "original"},
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.append_action_group(
            group_id="ag-original",
            root_frame_id="root",
            turn_id="turn",
            ordinal=1,
            kind="code",
            assistant_content="replacement",
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_action_group(
            group_id="ag-other",
            root_frame_id="root",
            turn_id="turn",
            ordinal=0,
            kind="code",
        )
    assert (
        store.get_action_group("ag-original")["assistant_content"]
        == original["assistant_content"]
    )

    first_event = store.append_action_event(
        event_id="ae-original",
        group_id="ag-original",
        sequence=0,
        type="proposed",
        result={"original": True},
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_action_event(
            event_id="ae-original",
            group_id="ag-original",
            sequence=1,
            type="result",
            result={"replacement": True},
        )
    assert store.list_action_events("ag-original") == [first_event]

    with pytest.raises(sqlite3.IntegrityError):
        store.append_tool_action_group(
            group_id="ag-half-tool",
            root_frame_id="root",
            turn_id="turn-2",
            ordinal=1,
            events=[
                {"sequence": 0, "type": "proposed"},
                {"sequence": 0, "type": "result"},
            ],
        )
    assert store.get_action_group("ag-half-tool") is None
    assert [group["group_id"] for group in store.list_action_groups("root")] == [
        "ag-original"
    ]

    # Keep this explicit: ledger history must never acquire an upsert path.
    assert "INSERT OR REPLACE" not in inspect.getsource(ActionLedgerRepository)
    store.close()


def test_execution_attempt_is_allocated_first_and_terminal_state_is_immutable(
    tmp_path,
):
    store = _store(tmp_path)
    store.append_action_group(
        group_id="ag-code",
        root_frame_id="root",
        turn_id="turn",
        kind="code",
    )
    attempt = store.allocate_execution_attempt(
        attempt_id="xa-1",
        group_id="ag-code",
        producing_cell_id="cell-1",
        state_revision=3,
        generation_id="python:7",
        replayed_from_cell_id="cell-old",
        allocated_at=100,
    )
    assert attempt == {
        "attempt_id": "xa-1",
        "group_id": "ag-code",
        "producing_cell_id": "cell-1",
        "attempt_ordinal": 0,
        "state_revision": 3,
        "generation_id": "python:7",
        "allocated_at": 100,
        "started_at": None,
        "response_at": None,
        "capture_at": None,
        "finished_at": None,
        "terminal_state": None,
        "error": None,
        "replayed_from_cell_id": "cell-old",
    }

    with pytest.raises(AttemptStateError, match="before started_at"):
        store.mark_execution_attempt_response("xa-1", response_at=102)
    started = store.mark_execution_attempt_started("xa-1", started_at=101)
    assert started["started_at"] == 101
    # Repeated delivery is idempotent but cannot replace the first timestamp.
    assert (
        store.mark_execution_attempt_started("xa-1", started_at=999)["started_at"]
        == 101
    )
    store.mark_execution_attempt_response("xa-1", response_at=102)
    store.mark_execution_attempt_capture("xa-1", capture_at=103)
    finished = store.finish_execution_attempt(
        "xa-1",
        terminal_state="completed",
        finished_at=104,
    )
    assert finished["terminal_state"] == "completed"
    assert finished["finished_at"] == 104

    with pytest.raises(AttemptStateError, match="already finished"):
        store.finish_execution_attempt(
            "xa-1", terminal_state="failed", error={"message": "rewrite"}
        )
    with pytest.raises(AttemptStateError, match="already finished"):
        store.mark_execution_attempt_capture("xa-1", capture_at=105)
    assert store.get_execution_attempt("xa-1")["terminal_state"] == "completed"

    second = store.allocate_execution_attempt(
        attempt_id="xa-2",
        group_id="ag-code",
        producing_cell_id="cell-1",
        generation_id="python:8",
        allocated_at=105,
    )
    assert second["attempt_ordinal"] == 1
    failed = store.finish_execution_attempt(
        "xa-2",
        terminal_state="worker_died",
        error={"kind": "eof", "message": "worker exited"},
        finished_at=106,
    )
    assert failed["error"] == {"kind": "eof", "message": "worker exited"}
    assert [
        record["attempt_id"]
        for record in store.list_execution_attempts(
            root_frame_id="root", turn_id="turn"
        )
    ] == ["xa-1", "xa-2"]
    store.close()


def test_repository_additively_migrates_normalized_assistant_message_column():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE action_groups("
        "group_id TEXT PRIMARY KEY,root_frame_id TEXT NOT NULL,"
        "branch_id TEXT NOT NULL,turn_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
        "kind TEXT NOT NULL,provider TEXT,model TEXT,wire_state TEXT,"
        "assistant_content TEXT,created_at INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE execution_attempts("
        "attempt_id TEXT PRIMARY KEY,group_id TEXT NOT NULL,"
        "producing_cell_id TEXT NOT NULL,attempt_ordinal INTEGER NOT NULL,"
        "generation_id TEXT,allocated_at INTEGER NOT NULL,started_at INTEGER,"
        "response_at INTEGER,capture_at INTEGER,finished_at INTEGER,"
        "terminal_state TEXT,error TEXT,replayed_from_cell_id TEXT)"
    )
    repository = ActionLedgerRepository(
        connection,
        threading.RLock(),
        clock_ms=lambda: 123,
    )
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(action_groups)")
    }
    assert "assistant_message" in columns
    attempt_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(execution_attempts)")
    }
    assert {"owner_instance_id", "state_revision"} <= attempt_columns
    group = repository.append_group(
        root_frame_id="root",
        turn_id="turn",
        kind="finalize",
        assistant_message={"role": "assistant", "content": "done"},
    )
    assert group["assistant_message"] == {"role": "assistant", "content": "done"}


def _append_group_variant(store: Store, variant: str, *, turn_id: str) -> dict:
    if variant == "group":
        return store.append_action_group(
            root_frame_id="root-race",
            branch_id="root-race",
            turn_id=turn_id,
            kind="system",
        )
    return store.append_tool_action_group(
        root_frame_id="root-race",
        branch_id="root-race",
        turn_id=turn_id,
        events=[{"type": "proposed", "action_id": f"action-{turn_id}"}],
    )


@pytest.mark.parametrize("variant", ["group", "tool_group"])
def test_revert_barrier_and_new_group_are_linearized_across_store_connections(
    tmp_path, variant
):
    database = tmp_path / "openai4s.db"
    action_store = Store(database)
    barrier_store = Store(database)
    admitted = threading.Event()
    release = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    outcomes: list[object] = []
    original_admission = action_store._actions._admit_action_scope

    def paused_admission(root_frame_id, branch_id, operation):
        assert original_admission is not None
        original_admission(root_frame_id, branch_id, operation)
        admitted.set()
        assert release.wait(5)

    action_store._actions._admit_action_scope = paused_admission

    def append_action():
        try:
            outcomes.append(
                _append_group_variant(action_store, variant, turn_id="turn-before")
            )
        except BaseException as error:  # recorded for the parent-thread assertion
            outcomes.append(error)

    def publish_barrier():
        writer_started.set()
        barrier_store.set_setting(
            revert_recovery_setting_key("root-race"),
            '{"schema_version":1,"state":"preparing"}',
        )
        writer_done.set()

    action_thread = threading.Thread(target=append_action)
    marker_thread = threading.Thread(target=publish_barrier)
    try:
        action_thread.start()
        assert admitted.wait(5)
        marker_thread.start()
        assert writer_started.wait(5)
        # BEGIN IMMEDIATE spans admission through INSERT, so the second Store
        # cannot commit the marker in the middle of that decision.
        assert writer_done.wait(0.2) is False
        release.set()
        action_thread.join(5)
        marker_thread.join(5)
        assert not action_thread.is_alive()
        assert not marker_thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert writer_done.is_set()
        assert barrier_store.get_setting(revert_recovery_setting_key("root-race"))

        with pytest.raises(AutoModeConflictError, match="requires recovery"):
            _append_group_variant(
                action_store,
                variant,
                turn_id="turn-after",
            )
        assert [
            group["turn_id"] for group in action_store.list_action_groups("root-race")
        ] == ["turn-before"]
    finally:
        release.set()
        action_thread.join(5)
        marker_thread.join(5)
        action_store.close()
        barrier_store.close()
