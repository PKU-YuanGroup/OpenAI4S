from __future__ import annotations

import json

from openai4s.config import Config, get_config
from openai4s.host_dispatch import build_dispatcher
from openai4s.sdk.host import _Host


def test_host_segment_context_records_lifecycle_events_and_output() -> None:
    dispatcher = build_dispatcher(get_config(), frame_id="frame-segment")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with host.segment(
            "structure_generation", objective="make a structure"
        ) as segment:
            assert segment.segment_id.startswith("seg-")
            segment.observe({"valid": True}, kind="validation")
            segment.set_output({"confidence": 0.9})

        rows = dispatcher.store._conn.execute(
            "SELECT kind FROM rl_execution_events WHERE episode_id=? ORDER BY sequence",
            ("frame-segment",),
        ).fetchall()
        assert [row[0] for row in rows] == [
            "segment.started",
            "segment.observation",
            "segment.output",
            "segment.closed",
        ]
        status = dispatcher.store._conn.execute(
            "SELECT status FROM rl_segments WHERE episode_id=?", ("frame-segment",)
        ).fetchone()[0]
        assert status == "completed"
    finally:
        dispatcher.store.close()


def test_segment_events_keep_parent_decision_context(tmp_path) -> None:
    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="frame-parent")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context(
            {"episode_id": "frame-parent", "decision_id": "decision-2"}
        ):
            with host.segment("analysis") as segment:
                segment.observe({"ready": True})
                segment.set_output({"value": 3})
        rows = dispatcher.store._conn.execute(
            "SELECT kind, payload FROM rl_execution_events "
            "WHERE episode_id=? ORDER BY sequence",
            ("frame-parent",),
        ).fetchall()
        assert all(json.loads(row[1])["decision_id"] == "decision-2" for row in rows)
    finally:
        dispatcher.store.close()


def test_segment_api_persists_explicit_parent_segment_id(tmp_path) -> None:
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path), frame_id="frame-parent-segment"
    )
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with host.segment("parent") as parent:
            with host.segment("child", parent_segment_id=parent.segment_id) as child:
                assert child.segment_id != parent.segment_id
        row = dispatcher.store._conn.execute(
            "SELECT parent_segment_id FROM rl_segments WHERE segment_id=?",
            (child.segment_id,),
        ).fetchone()
        assert row[0] == parent.segment_id
        event = dispatcher.store._conn.execute(
            "SELECT payload FROM rl_execution_events WHERE event_id=?",
            (f"{child.segment_id}:started",),
        ).fetchone()
        assert json.loads(event[0])["payload"]["parent_segment_id"] == (
            parent.segment_id
        )
        trajectory = dispatcher.store.rl.load_trajectory("frame-parent-segment")
        child_record = next(
            item for item in trajectory.segments if item.segment_id == child.segment_id
        )
        assert child_record.parent_segment_id == parent.segment_id
    finally:
        dispatcher.store.close()


def test_host_segment_marks_exception_as_failed() -> None:
    dispatcher = build_dispatcher(get_config(), frame_id="frame-failed")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        try:
            with host.segment("failing"):
                raise RuntimeError("scientific failure")
        except RuntimeError:
            pass
        status = dispatcher.store._conn.execute(
            "SELECT status FROM rl_segments WHERE episode_id=?", ("frame-failed",)
        ).fetchone()[0]
        assert status == "failed"
    finally:
        dispatcher.store.close()


def test_host_can_open_two_segments_in_one_episode() -> None:
    dispatcher = build_dispatcher(get_config(), frame_id="frame-two-segments")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with host.segment("first"):
            pass
        with host.segment("second"):
            pass

        rows = dispatcher.store._conn.execute(
            "SELECT kind, sequence FROM rl_execution_events "
            "WHERE episode_id=? ORDER BY sequence",
            ("frame-two-segments",),
        ).fetchall()
        assert [row[0] for row in rows] == [
            "segment.started",
            "segment.closed",
            "segment.started",
            "segment.closed",
        ]
        assert [row[1] for row in rows] == [1, 2, 3, 4]
    finally:
        dispatcher.store.close()


def test_host_rpc_lifecycle_is_joined_to_decision_and_segment(tmp_path) -> None:
    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="frame-rpc")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context(
            {
                "episode_id": "frame-rpc",
                "decision_id": "decision-7",
                "source_id": "call-7",
            }
        ):
            with host.segment("analysis"):
                assert host.artifacts()["count"] == 0
        rows = dispatcher.store._conn.execute(
            "SELECT kind, payload FROM rl_execution_events "
            "WHERE episode_id=? ORDER BY sequence",
            ("frame-rpc",),
        ).fetchall()
        assert [row[0] for row in rows] == [
            "segment.started",
            "host_call.started",
            "host_call.completed",
            "segment.closed",
        ]
        started = json.loads(rows[1][1])
        completed = json.loads(rows[2][1])
        assert started["decision_id"] == "decision-7"
        assert started["segment_id"] == completed["segment_id"]
        assert started["payload"]["method"] == "artifacts"
        assert "arguments_sha256" in started["payload"]
        assert "result_sha256" in completed["payload"]
    finally:
        dispatcher.store.close()


def test_host_rpc_failure_preserves_error_category(tmp_path) -> None:
    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="frame-rpc-fail")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context(
            {"episode_id": "frame-rpc-fail", "decision_id": "decision-8"}
        ):
            try:
                host.artifact_path("missing-version")
            except Exception:
                pass
        row = dispatcher.store._conn.execute(
            "SELECT kind, payload FROM rl_execution_events "
            "WHERE episode_id=? ORDER BY sequence DESC LIMIT 1",
            ("frame-rpc-fail",),
        ).fetchone()
        assert row[0] == "host_call.failed"
        assert json.loads(row[1])["payload"]["error_category"] == "KeyError"
    finally:
        dispatcher.store.close()
