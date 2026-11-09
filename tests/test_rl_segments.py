from __future__ import annotations

from openai4s.config import get_config
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
