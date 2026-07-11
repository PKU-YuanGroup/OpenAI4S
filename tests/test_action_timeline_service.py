"""Public Action Timeline projections stay bounded and protocol-safe."""

from __future__ import annotations

from openai4s.server.action_timeline import ActionTimelineService
from openai4s.store import Store


def test_timeline_projects_groups_events_and_attempt_milestones(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    store.append_action_group(
        group_id="group-user",
        root_frame_id="root",
        turn_id="turn",
        kind="user",
        assistant_message={"role": "user", "content": "Design a protein"},
        created_at=1,
    )
    store.append_action_group(
        group_id="group-code",
        root_frame_id="root",
        turn_id="turn",
        kind="code",
        provider="ark",
        model="science-model",
        wire_state={"response_id": "must-not-leak"},
        assistant_content="I will compute this.",
        created_at=2,
    )
    store.append_action_event(
        event_id="event-code",
        group_id="group-code",
        type="proposed",
        canonical_arguments={
            "language": "python",
            "code": "# Score candidate sequences\nprint(1)",
        },
        raw_arguments="raw-provider-cell-must-not-leak",
        resource_keys=["kernel:python"],
        created_at=3,
    )
    store.allocate_execution_attempt(
        attempt_id="attempt-1",
        group_id="group-code",
        producing_cell_id="cell-1",
        generation_id="generation-1",
        allocated_at=4,
    )
    store.mark_execution_attempt_started("attempt-1", started_at=5)
    store.mark_execution_attempt_response("attempt-1", response_at=6)
    store.mark_execution_attempt_capture("attempt-1", capture_at=7)
    store.finish_execution_attempt(
        "attempt-1", terminal_state="completed", finished_at=8
    )

    timeline = ActionTimelineService(store).get("root")

    assert timeline["count"] == 2
    assert timeline["last_ordinal"] == 1
    assert timeline["running"] is False
    assert timeline["groups"][0]["title"] == "Design a protein"
    code = timeline["groups"][1]
    assert code["title"] == "Score candidate sequences"
    assert code["status"] == "completed"
    assert code["events"][0]["resource_keys"] == ["kernel:python"]
    assert code["attempts"][0]["generation_id"] == "generation-1"
    assert code["attempts"][0]["capture_at"] == 7
    assert "wire_state" not in code
    assert "raw_arguments" not in code["events"][0]
    assert "must-not-leak" not in repr(timeline)
    store.close()


def test_timeline_omits_large_payloads_and_filters_branch_cursor(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    for ordinal in range(2):
        group = store.append_action_group(
            root_frame_id="root",
            turn_id=f"turn-{ordinal}",
            kind="code",
            ordinal=ordinal,
        )
        store.append_action_event(
            group_id=group["group_id"],
            type="observation",
            result={
                "text": "x" * 2_000,
                "artifact": {"artifact_id": f"artifact-{ordinal}"},
            },
        )
    store.append_action_group(
        root_frame_id="root",
        branch_id="branch-b",
        turn_id="fork-turn",
        kind="system",
        ordinal=0,
    )

    service = ActionTimelineService(store, payload_chars=256)
    canonical = service.get("root", after_ordinal=0)
    assert [group["ordinal"] for group in canonical["groups"]] == [1]
    event = canonical["groups"][0]["events"][0]
    assert "result" not in event
    assert "arguments" not in event
    assert event["artifacts"] == ["artifact-1"]

    branch = service.get("root", branch_id="branch-b")
    assert len(branch["groups"]) == 1
    assert branch["groups"][0]["branch_id"] == "branch-b"
    store.close()


def test_timeline_public_projection_redacts_secrets_and_provider_ids(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    group = store.append_action_group(
        root_frame_id="root",
        turn_id="turn",
        kind="native_tools",
        wire_state={"response_id": "provider-secret-wire"},
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="result",
        action_id="action-private",
        tool_call_id="tool-private",
        wire_id="wire-private",
        canonical_arguments={
            "name": "web_fetch",
            "arguments": {"url": "https://example.test/?token=secret-value"},
        },
        result={
            "is_error": False,
            "authorization": "Bearer secret-value",
            "artifact": {"artifact_id": "artifact-public"},
        },
    )

    timeline = ActionTimelineService(store).get("root")
    public_event = timeline["groups"][0]["events"][0]
    assert public_event["artifacts"] == ["artifact-public"]
    assert public_event["outcome"] == "ok"
    assert not {
        "arguments",
        "result",
        "action_id",
        "tool_call_id",
        "wire_id",
    } & set(public_event)
    rendered = repr(timeline)
    assert "secret-value" not in rendered
    assert "provider-secret-wire" not in rendered
    store.close()


def test_timeline_reports_open_attempt_as_running(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    group = store.append_action_group(
        root_frame_id="root", turn_id="turn", kind="code"
    )
    store.allocate_execution_attempt(
        group_id=group["group_id"], producing_cell_id="cell"
    )

    timeline = ActionTimelineService(store).get("root")
    assert timeline["running"] is True
    assert timeline["groups"][0]["status"] == "running"
    store.close()


def test_timeline_does_not_hide_an_earlier_tool_failure(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    store.append_tool_action_group(
        root_frame_id="root",
        turn_id="turn",
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "name": "first"},
                {"id": "call-2", "name": "second"},
            ],
        },
        events=[
            {
                "sequence": 0,
                "type": "proposed",
                "tool_call_id": "call-1",
                "canonical_arguments": {"name": "first", "arguments": {}},
            },
            {
                "sequence": 1,
                "type": "proposed",
                "tool_call_id": "call-2",
                "canonical_arguments": {"name": "second", "arguments": {}},
            },
            {
                "sequence": 2,
                "type": "result",
                "tool_call_id": "call-1",
                "result": {"role": "tool", "is_error": True},
            },
            {
                "sequence": 3,
                "type": "result",
                "tool_call_id": "call-2",
                "result": {"role": "tool", "is_error": False},
            },
        ],
    )

    group = ActionTimelineService(store).get("root")["groups"][0]
    assert group["title"] == "first, second"
    assert group["status"] == "failed"
    store.close()


def test_timeline_caps_initial_history_to_latest_and_pages_forward(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    for ordinal in range(6):
        store.append_action_group(
            root_frame_id="root",
            turn_id=f"turn-{ordinal}",
            kind="system",
            ordinal=ordinal,
        )

    service = ActionTimelineService(store)
    latest = service.get("root", limit=3)
    assert [group["ordinal"] for group in latest["groups"]] == [3, 4, 5]
    assert latest["count"] == 3
    assert latest["total_count"] == 6
    assert latest["truncated"] is True
    assert latest["has_earlier"] is True
    assert latest["has_more"] is False

    forward = service.get("root", after_ordinal=1, limit=3)
    assert [group["ordinal"] for group in forward["groups"]] == [2, 3, 4]
    assert forward["has_earlier"] is False
    assert forward["has_more"] is True
    store.close()
