from __future__ import annotations

import json

import pytest

from openai4s.config import Config, get_config
from openai4s.host_dispatch import build_dispatcher
from openai4s.sdk.host import _Host


def test_ordinary_host_calls_do_not_create_rl_capture_rows(tmp_path) -> None:
    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="ordinary-frame")
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        assert host.artifacts()["count"] == 0
        count = dispatcher.store._conn.execute(
            "SELECT COUNT(*) FROM rl_execution_events WHERE episode_id=?",
            ("ordinary-frame",),
        ).fetchone()[0]
        assert count == 0
        assert dispatcher.store.rl.get_episode("ordinary-frame") is None
    finally:
        dispatcher.store.close()


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


def test_host_rpc_lifecycle_is_joined_to_decision_and_segment(
    tmp_path, monkeypatch
) -> None:
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
        assert "arguments_hmac_sha256" not in started["payload"]
        assert "result_hmac_sha256" not in completed["payload"]
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


def test_segment_uses_bound_rollout_episode_and_reload_keeps_closed_status(
    tmp_path,
) -> None:
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path), frame_id="frame-segment-binding"
    )
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context(
            {"episode_id": "rollout-episode", "decision_id": "decision-1"}
        ):
            with host.segment("analysis") as segment:
                pass

        row = dispatcher.store._conn.execute(
            "SELECT episode_id,status FROM rl_segments WHERE segment_id=?",
            (segment.segment_id,),
        ).fetchone()
        assert tuple(row) == ("rollout-episode", "completed")
        loaded = dispatcher.store.rl.load_trajectory("rollout-episode")
        assert loaded.segments[0].status == "completed"
    finally:
        dispatcher.store.close()


def test_nested_segment_restores_parent_host_rpc_context(tmp_path) -> None:
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path), frame_id="frame-nested-segment"
    )
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context({"episode_id": "frame-nested-segment"}):
            with host.segment("parent") as parent:
                with host.segment("child"):
                    pass
                assert host.artifacts()["count"] == 0

        rows = dispatcher.store._conn.execute(
            "SELECT kind,payload FROM rl_execution_events "
            "WHERE episode_id=? ORDER BY sequence",
            ("frame-nested-segment",),
        ).fetchall()
        host_rows = [row for row in rows if row[0].startswith("host_call.")]
        assert [row[0] for row in host_rows] == [
            "host_call.started",
            "host_call.completed",
        ]
        assert json.loads(host_rows[0][1])["segment_id"] == parent.segment_id
        assert json.loads(host_rows[1][1])["segment_id"] == parent.segment_id
    finally:
        dispatcher.store.close()


def test_segment_events_reject_unknown_or_closed_segments(tmp_path) -> None:
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path), frame_id="frame-segment-validation"
    )
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with host.segment("analysis") as segment:
            pass

        result = dispatcher(
            "rl_segment_observe",
            [
                {
                    "segment_id": segment.segment_id,
                    "episode_id": segment.episode_id,
                    "value": {"late": True},
                }
            ],
        )
        assert result == {"error": "segment is already completed"}
        unknown = dispatcher(
            "rl_segment_close",
            [{"segment_id": "missing", "episode_id": segment.episode_id}],
        )
        assert unknown == {"error": "unknown segment 'missing'"}
        count = dispatcher.store._conn.execute(
            "SELECT COUNT(*) FROM rl_execution_events WHERE episode_id=?",
            (segment.episode_id,),
        ).fetchone()[0]
        assert count == 2
    finally:
        dispatcher.store.close()


def test_persistent_phase_spans_decisions_in_existing_trainer_episode(tmp_path):
    from openai4s.rl import Episode

    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="frame-phase")
    try:
        dispatcher.store.rl.ensure_episode(
            Episode(
                "trainer-ep",
                "actual task",
                "actual-session",
                1,
                policy_name="policy",
                policy_version="v7",
                metadata={"seed": 7},
            )
        )
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_action_context(
            {"episode_id": "trainer-ep", "decision_id": "d1"}
        ):
            phase = host.begin_segment("analysis", segment_id="phase")
        with dispatcher.bind_action_context(
            {"episode_id": "trainer-ep", "decision_id": "d2"}
        ):
            host.artifacts()
            with host.segment("nested", parent_segment_id=phase.segment_id):
                host.artifacts()
            host.artifacts()
        with dispatcher.bind_action_context(
            {"episode_id": "trainer-ep", "decision_id": "d3"}
        ):
            phase = host.resume_segment("phase")
            phase.close()
            phase.close()
        trajectory = dispatcher.store.rl.load_trajectory("trainer-ep")
        assert trajectory.episode.task == "actual task"
        calls = [
            e for e in trajectory.execution_events if e.kind == "host_call.started"
        ]
        # A caller-chosen id is scoped to its episode; resume accepts the raw id.
        assert [e.segment_id for e in calls][::2] == [
            "trainer-ep:phase",
            "trainer-ep:phase",
        ]
        assert calls[1].segment_id != "trainer-ep:phase"
        assert all(e.decision_id == "d2" for e in calls)
        assert all(s.status == "completed" for s in trajectory.segments)
    finally:
        dispatcher.store.close()


@pytest.mark.stubbed_backend
def test_real_kernel_phase_spans_cells_and_dataset_decisions(monkeypatch, tmp_path):
    import openai4s.agent.loop as loop_mod
    from openai4s.agent import Agent
    from openai4s.rl import Episode, RolloutDataset, TrajectoryRecorder
    from tests.test_agent import ScriptedLLM

    dispatcher = build_dispatcher(Config(data_dir=tmp_path), frame_id="real-phase")
    store = dispatcher.store
    frame = store.new_frame(kind="turn")
    recorder = TrajectoryRecorder(
        Episode("real-phase-ep", "analysis", frame, 1), repository=store.rl
    )
    monkeypatch.setattr(
        loop_mod,
        "chat",
        ScriptedLLM(
            [
                "```python\nphase = host.begin_segment('analysis', segment_id='science-phase')\nphase.observe({'step':1})\n```",
                "```python\nprint(host.artifacts())\nphase.observe({'step':2})\n```",
                "```python\nphase.close()\nhost.submit_output({'ok':True}, ['Analyzed evidence'])\n```",
            ]
        ),
    )
    try:
        result = Agent(
            cfg=Config(data_dir=tmp_path),
            frame_id=frame,
            max_turns=3,
            use_skills=False,
            allow_delegate=False,
            trajectory_recorder=recorder,
        ).run("analyze")
        assert result["stop_reason"] == "submitted"
        trajectory = store.rl.load_trajectory("real-phase-ep")
        scoped = "real-phase-ep:science-phase"
        phase = next(s for s in trajectory.segments if s.segment_id == scoped)
        assert phase.status == "completed"
        dataset = RolloutDataset.from_trajectory(
            trajectory, recorder.create_snapshot("phase-snapshot")
        )
        assert all(scoped in sample.segment_ids for sample in dataset.samples)
        calls = [
            e
            for e in trajectory.execution_events
            if e.kind == "host_call.started" and e.payload["method"] == "artifacts"
        ]
        assert len(calls) == 1 and calls[0].segment_id == scoped
    finally:
        store.close()


def test_new_action_context_cannot_inherit_stale_segment_decision(tmp_path) -> None:
    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path), frame_id="frame-context-refresh"
    )
    try:
        host = _Host(lambda method, args: dispatcher(method, args))
        with dispatcher.bind_rl_context(
            {"episode_id": "frame-context-refresh", "decision_id": "old"}
        ):
            with host.segment("first"):
                with dispatcher.bind_action_context(
                    {"episode_id": "frame-context-refresh", "decision_id": "new"}
                ):
                    assert host.artifacts()["count"] == 0
        payload = dispatcher.store._conn.execute(
            "SELECT payload FROM rl_execution_events "
            "WHERE kind='host_call.started' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
        assert json.loads(payload)["decision_id"] == "new"
        assert json.loads(payload)["segment_id"] is None
    finally:
        dispatcher.store.close()
