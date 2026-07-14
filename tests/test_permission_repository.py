"""Direct contracts for permission-rule persistence and resolution."""

from __future__ import annotations

import itertools
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openai4s.config import Config
from openai4s.storage.permissions import (
    DEFAULT_PERMISSION_RULES,
    PermissionRuleRepository,
    perm_match,
)
from openai4s.store import Store, get_store


def _repository(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    ticks = itertools.count(1000)
    repository = PermissionRuleRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: next(ticks),
        get_setting=store.get_setting,
        set_setting=store.set_setting,
    )
    return store, repository


def test_rule_upsert_normalizes_identity_and_delete(tmp_path):
    _store, repository = _repository(tmp_path)

    first = repository.set_rule(
        scope="conversation",
        scope_id=None,
        tool="bash",
        pattern=None,
        decision="ask",
    )
    second = repository.set_rule(
        scope="conversation",
        scope_id="",
        tool="bash",
        pattern="*",
        decision="allow",
    )

    assert second == first
    assert repository.get_rules(scope="conversation", scope_id=None) == [
        {
            "rule_id": first,
            "scope": "conversation",
            "scope_id": "",
            "tool": "bash",
            "pattern": "*",
            "decision": "allow",
            "created_at": 1000,
            "updated_at": 1001,
        }
    ]
    with sqlite3.connect(_store.db_path) as independent:
        assert independent.execute(
            "SELECT decision FROM permission_rules WHERE rule_id=?",
            (first,),
        ).fetchone() == ("allow",)

    repository.delete_rule(first)
    assert repository.get_rules(scope="conversation") == []
    with sqlite3.connect(_store.db_path) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM permission_rules WHERE rule_id=?",
            (first,),
        ).fetchone() == (0,)


def test_resolution_preserves_exact_globs_specificity_and_absolute_deny(tmp_path):
    _store, repository = _repository(tmp_path)
    assert perm_match("grep [a-z] file", "grep [a-z] file") is True
    assert perm_match("data/file.csv", "*.csv") is True
    assert perm_match("ABC", "abc") is False

    repository.set_rule(
        scope="global",
        tool="bash",
        pattern="git *",
        decision="allow",
    )
    repository.set_rule(
        scope="project",
        scope_id="science",
        tool="bash",
        pattern="git push *",
        decision="ask",
    )
    repository.set_rule(
        scope="conversation",
        scope_id="frame",
        tool="bash",
        pattern="git push origin main",
        decision="allow",
    )
    assert (
        repository.resolve(
            root_frame_id="frame",
            project_id="science",
            tool="bash",
            pattern_input="git push origin main",
        )
        == "allow"
    )

    repository.set_rule(
        scope="global",
        tool="*",
        pattern="git push origin main",
        decision="deny",
    )
    assert (
        repository.resolve(
            root_frame_id="frame",
            project_id="science",
            tool="bash",
            pattern_input="git push origin main",
        )
        == "deny"
    )
    assert repository.resolve(tool="unknown", pattern_input="x") == "ask"


def test_scope_projection_and_default_seed_reset_semantics(tmp_path):
    store, repository = _repository(tmp_path)
    repository.seed_defaults()
    assert store.get_setting("perm_seeded") == "1"
    assert len(repository.get_rules(scope="global")) == len(DEFAULT_PERMISSION_RULES)

    mcp = next(
        rule
        for rule in repository.get_rules(scope="global")
        if rule["tool"] == "mcp_call"
    )
    repository.delete_rule(mcp["rule_id"])
    repository.set_rule(
        scope="global",
        tool="custom_external",
        pattern="*",
        decision="allow",
    )
    repository.seed_defaults()
    assert repository.resolve(tool="mcp_call", pattern_input="server/tool") == "ask"
    assert not any(
        rule["tool"] == "mcp_call" for rule in repository.get_rules(scope="global")
    )

    repository.set_rule(
        scope="global",
        tool="mcp_call",
        pattern="*",
        decision="allow",
    )
    repository.seed_defaults(force=True)
    assert repository.resolve(tool="mcp_call", pattern_input="server/tool") == "ask"
    assert repository.resolve(tool="custom_external", pattern_input="x") == "allow"

    repository.set_rule(
        scope="project",
        scope_id="science",
        tool="bash",
        decision="allow",
    )
    repository.set_rule(
        scope="conversation",
        scope_id="frame",
        tool="bash",
        decision="deny",
    )
    grouped = repository.list_for_frame(
        root_frame_id="frame",
        project_id="science",
    )
    assert len(grouped["project"]) == 1
    assert len(grouped["conversation"]) == 1
    assert grouped["global"]


def test_seed_upgrade_adds_only_new_defaults_to_existing_stores(tmp_path):
    store, repository = _repository(tmp_path)
    for tool, pattern, decision in DEFAULT_PERMISSION_RULES:
        if tool not in {"mcp_call", "science_search"}:
            repository.set_rule(
                scope="global",
                tool=tool,
                pattern=pattern,
                decision=decision,
            )
    store.set_setting("perm_seeded", "1")

    repository.seed_defaults()

    assert repository.resolve(tool="science_search", pattern_input="uniprot") == "allow"
    assert repository.resolve(tool="mcp_call", pattern_input="server/tool") == "ask"
    assert not any(
        rule["tool"] == "mcp_call" for rule in repository.get_rules(scope="global")
    )
    assert store.get_setting("perm_seed_version") == "2"


def test_seed_rules_commit_before_marker_and_recover_after_marker_failure(tmp_path):
    store, _unused_repository = _repository(tmp_path)

    def fail_marker(_key, _value):
        raise RuntimeError("settings unavailable")

    failing = PermissionRuleRepository(
        store._conn,
        store._lock,
        clock_ms=lambda: 2000,
        get_setting=store.get_setting,
        set_setting=fail_marker,
    )
    with pytest.raises(RuntimeError, match="settings unavailable"):
        failing.seed_defaults()

    assert store.get_setting("perm_seeded") is None
    with sqlite3.connect(store.db_path) as independent:
        count = independent.execute(
            "SELECT COUNT(*) FROM permission_rules WHERE scope='global'"
        ).fetchone()[0]
    assert count == len(DEFAULT_PERMISSION_RULES)

    store._permissions.seed_defaults()
    assert store.get_setting("perm_seeded") == "1"
    assert len(store.get_permission_rules(scope="global")) == len(
        DEFAULT_PERMISSION_RULES
    )


def test_concurrent_upserts_share_one_rule_identity(tmp_path):
    _store, repository = _repository(tmp_path)
    workers = 12
    barrier = threading.Barrier(workers)

    def upsert(index):
        barrier.wait()
        return repository.set_rule(
            scope="conversation",
            scope_id="same-frame",
            tool="bash",
            pattern="git status",
            decision="allow" if index % 2 else "ask",
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rule_ids = list(pool.map(upsert, range(workers)))

    assert len(set(rule_ids)) == 1
    rules = repository.get_rules(scope="conversation", scope_id="same-frame")
    assert len(rules) == 1
    assert rules[0]["decision"] in {"allow", "ask"}


def test_store_facade_and_frame_project_cascades_remain_aggregate(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    project_id = store.create_project(name="Science")["project_id"]
    frame_id = store.new_frame(project_id=project_id)
    global_id = store.set_permission_rule(
        scope="global",
        tool="mcp_call",
        decision="ask",
    )
    project_rule = store.set_permission_rule(
        scope="project",
        scope_id=project_id,
        tool="bash",
        decision="allow",
    )
    conversation_rule = store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="bash",
        decision="deny",
    )

    assert isinstance(store._permissions, PermissionRuleRepository)
    assert (
        store.resolve_permission(
            root_frame_id=frame_id,
            project_id=project_id,
            tool="bash",
        )
        == "deny"
    )
    store.delete_frame(frame_id)
    assert (
        store.get_permission_rules(
            scope="conversation",
            scope_id=frame_id,
        )
        == []
    )
    remaining = {
        rule["rule_id"]
        for rules in store.list_permission_rules_for_frame(
            project_id=project_id
        ).values()
        for rule in rules
    }
    assert conversation_rule not in remaining
    assert project_rule in remaining
    assert global_id in remaining

    second_frame = store.new_frame(project_id=project_id)
    second_conversation = store.set_permission_rule(
        scope="conversation",
        scope_id=second_frame,
        tool="bash",
        decision="allow",
    )
    store.delete_project(project_id)
    all_rules = store.get_permission_rules(scope="global")
    assert global_id in {rule["rule_id"] for rule in all_rules}
    assert store.get_permission_rules(scope="project", scope_id=project_id) == []
    assert (
        store.get_permission_rules(
            scope="conversation",
            scope_id=second_frame,
        )
        == []
    )
    assert second_conversation != global_id


def test_durable_permission_request_is_append_only_and_terminal_is_immutable(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    request = store.create_permission_request(
        decision_id="perm-request-1",
        root_frame_id="root-1",
        frame_id="root-1",
        project_id="science",
        tool="mcp_call",
        target="lab/send",
        payload={"type": "await_permission", "decision_id": "perm-request-1"},
        created_at=100,
        expires_at=500,
    )
    assert request["state"] == "pending"
    assert request["payload"]["type"] == "await_permission"
    with pytest.raises(sqlite3.IntegrityError):
        store.create_permission_request(
            decision_id="perm-request-1",
            tool="mcp_call",
            payload={},
        )

    resolved = store.resolve_permission_request(
        "perm-request-1",
        state="allowed",
        scope="once",
        message="approved",
        resolved_at=200,
    )
    assert resolved["state"] == "allowed"
    assert store.list_permission_requests(root_frame_id="root-1", state="pending") == []
    assert [
        item["decision_id"]
        for item in store.list_permission_requests(
            root_frame_id="root-1", state="allowed"
        )
    ] == ["perm-request-1"]
    # Idempotent same-terminal delivery is safe; a rewrite is not.
    assert (
        store.resolve_permission_request("perm-request-1", state="allowed")[
            "resolved_at"
        ]
        == 200
    )
    with pytest.raises(RuntimeError, match="already allowed"):
        store.resolve_permission_request("perm-request-1", state="denied")


def test_pending_permission_request_times_out_on_read(tmp_path):
    """A pending whose expires_at has passed (e.g. it outlived its backstop
    across a daemon restart) is swept to ``timed_out`` on the next pending read,
    not re-surfaced to a reconnecting client as a still-valid approval."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    store.create_permission_request(
        decision_id="perm-stale",
        root_frame_id="root-1",
        frame_id="root-1",
        project_id="science",
        tool="mcp_call",
        target="lab/send",
        payload={"type": "await_permission", "decision_id": "perm-stale"},
        created_at=100,
        expires_at=500,  # epoch-ms far in the past
    )

    # Reading the pending list runs the lazy backstop first.
    assert store.list_permission_requests(root_frame_id="root-1", state="pending") == []
    assert [
        item["decision_id"]
        for item in store.list_permission_requests(
            root_frame_id="root-1", state="timed_out"
        )
    ] == ["perm-stale"]


def test_permission_request_is_atomically_bound_to_action_ledger(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    group = store.append_action_group(
        root_frame_id="root-ledger",
        turn_id="turn-ledger",
        kind="native_tools",
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="proposed",
        action_id="call-1",
        tool_call_id="call-1",
        side_effect_class="external_side_effect",
        resource_keys=["mcp:lab/run"],
    )

    request = store.create_permission_request(
        decision_id="perm-ledger-1",
        root_frame_id="root-ledger",
        frame_id="root-ledger",
        project_id="science",
        action_group_id=group["group_id"],
        action_id="call-1",
        tool_call_id="call-1",
        side_effect_class="external_side_effect",
        resource_keys=["mcp:lab/run"],
        tool="mcp_call",
        target="lab/run",
        payload={"type": "await_permission"},
    )
    assert request["action_group_id"] == group["group_id"]
    assert request["action_id"] == "call-1"
    assert request["resource_keys"] == ["mcp:lab/run"]
    assert [
        event["type"] for event in store.get_action_group(group["group_id"])["events"]
    ] == ["proposed", "permission_pending"]

    store.resolve_permission_request(
        "perm-ledger-1",
        state="allowed",
        scope="once",
        resolution_context="live_thread",
    )
    events = store.get_action_group(group["group_id"])["events"]
    assert [event["type"] for event in events] == [
        "proposed",
        "permission_pending",
        "permission_resolved",
    ]
    assert events[-1]["action_id"] == "call-1"
    assert events[-1]["result"]["state"] == "allowed"
    assert events[-1]["resource_keys"] == ["mcp:lab/run"]


def test_permission_request_rolls_back_when_action_group_is_unknown(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    with pytest.raises(KeyError, match="unknown action group"):
        store.create_permission_request(
            decision_id="perm-no-group",
            action_group_id="missing-group",
            action_id="call-1",
            tool="mcp_call",
        )
    assert store.get_permission_request("perm-no-group") is None


def test_restart_once_grant_is_exact_and_consumed_atomically(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    store.create_permission_request(
        decision_id="perm-restart-once",
        root_frame_id="root-1",
        frame_id="root-1",
        project_id="science",
        tool="mcp_call",
        target="lab/send",
        payload={"type": "await_permission"},
    )
    store.resolve_permission_request(
        "perm-restart-once",
        state="allowed",
        scope="once",
        resolution_context="after_restart",
        continuation_required=False,
    )
    assert (
        store.consume_restart_permission_grant(
            root_frame_id="root-1",
            project_id="science",
            tool="mcp_call",
            target="lab/other",
        )
        is None
    )
    store.activate_restart_permission_continuation(
        "perm-restart-once", expires_at=9_999_999_999_999
    )

    barrier = threading.Barrier(8)

    def consume(_index):
        barrier.wait()
        return store.consume_restart_permission_grant(
            root_frame_id="root-1",
            project_id="science",
            tool="mcp_call",
            target="lab/send",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        consumed = list(pool.map(consume, range(8)))

    winners = [item for item in consumed if item is not None]
    assert [item["decision_id"] for item in winners] == ["perm-restart-once"]
    request = store.get_permission_request("perm-restart-once")
    assert request["continuation_required"] == 1
    assert request["continuation_consumed_at"] is not None

    store.create_permission_request(
        decision_id="perm-restart-expired",
        root_frame_id="root-1",
        frame_id="root-1",
        project_id="science",
        tool="mcp_call",
        target="lab/expired",
        payload={},
    )
    store.resolve_permission_request(
        "perm-restart-expired",
        state="allowed",
        scope="once",
        resolution_context="after_restart",
    )
    store.activate_restart_permission_continuation("perm-restart-expired", expires_at=1)
    assert (
        store.consume_restart_permission_grant(
            root_frame_id="root-1",
            project_id="science",
            tool="mcp_call",
            target="lab/expired",
            consumed_at=2,
        )
        is None
    )
    assert (
        store.get_permission_request("perm-restart-expired")["continuation_consumed_at"]
        is None
    )


def test_existing_permission_request_table_gains_restart_continuation_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE permission_requests ("
            "decision_id TEXT PRIMARY KEY,root_frame_id TEXT,frame_id TEXT,"
            "project_id TEXT,tool TEXT NOT NULL,target TEXT NOT NULL DEFAULT '',"
            "payload TEXT,state TEXT NOT NULL DEFAULT 'pending',scope TEXT,"
            "pattern TEXT,message TEXT,created_at INTEGER NOT NULL,"
            "expires_at INTEGER,resolved_at INTEGER)"
        )
        connection.execute(
            "INSERT INTO permission_requests(decision_id,root_frame_id,tool,"
            "target,state,created_at) VALUES('legacy','root','mcp_call','x',"
            "'pending',1)"
        )
        connection.commit()

    store = Store(db_path)
    try:
        columns = {
            row["name"]
            for row in store._conn.execute(
                "PRAGMA table_info(permission_requests)"
            ).fetchall()
        }
        assert {
            "resolution_context",
            "continuation_required",
            "continuation_expires_at",
            "continuation_consumed_at",
            "action_group_id",
            "action_id",
            "tool_call_id",
            "side_effect_class",
            "resource_keys",
        } <= columns
        request = store.get_permission_request("legacy")
        assert request["continuation_required"] == 0
        assert request["continuation_expires_at"] is None
    finally:
        store.close()
