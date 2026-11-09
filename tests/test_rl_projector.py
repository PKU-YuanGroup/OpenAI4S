from __future__ import annotations

import json

from openai4s.config import Config
from openai4s.rl import Episode, ExecutionEvent, TrajectoryRecorder
from openai4s.rl.host_events import HostCallLifecycle
from openai4s.store import get_store


def test_durable_projector_uses_exact_artifact_version_and_compute_receipt(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        episode = Episode("ep-projector", "task", "session", 1)
        recorder = TrajectoryRecorder(episode, repository=store.rl)
        store._conn.execute(
            "INSERT INTO artifacts(artifact_id,project_id,root_frame_id,filename,"
            "content_type,is_user_upload,priority,latest_version_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "artifact-1",
                "default",
                episode.episode_id,
                "out.csv",
                "text/csv",
                0,
                0,
                "v-new",
                2,
                3,
            ),
        )
        for version_id, checksum, created_at in (
            ("v-old", "old-checksum", 4),
            ("v-new", "new-checksum", 5),
        ):
            store._conn.execute(
                "INSERT INTO artifact_versions(version_id,artifact_id,filename,content_type,"
                "size_bytes,checksum,path,producing_cell_id,frame_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    "artifact-1",
                    "out.csv",
                    "text/csv",
                    7,
                    checksum,
                    f"/tmp/{version_id}",
                    f"cell-{version_id}",
                    episode.episode_id,
                    created_at,
                ),
            )
        store._conn.commit()
        job = store.create_compute_job(
            job_id="job-projector",
            provider="byoc:test",
            status="running",
            input_versions=["v-old"],
        )
        assert job["job_id"] == "job-projector"
        store.append_compute_job_event("job-projector", "submitted", {"receipt": "r-1"})
        recorder.add_event(
            ExecutionEvent(
                "source-event",
                episode.episode_id,
                "compute.submitted",
                6,
                {"output_version_id": "v-old", "job_id": "job-projector"},
            )
        )
        trajectory = store.rl.load_trajectory(episode.episode_id)
        projected = {event.kind: event for event in trajectory.execution_events}
        artifact = projected["artifact.version"]
        assert artifact.payload["version_id"] == "v-old"
        assert artifact.payload["checksum"] == "old-checksum"
        assert artifact.payload["producing_cell_id"] == "cell-v-old"
        assert artifact.source_id == "source-event"
        compute = projected["compute.job"]
        assert compute.payload["job_id"] == "job-projector"
        assert compute.payload["provider"] == "byoc:test"
        assert compute.payload["receipt"] is None
        assert compute.payload["input_versions"] == ["v-old"]
        assert compute.payload["artifact_manifest"] is None
        assert [event.kind for event in trajectory.execution_events].count(
            "compute.job_event"
        ) == 2
        assert (
            json.loads(recorder.export_json())["execution_events"][-1]["sequence"] == 5
        )
    finally:
        store.close()


def test_host_lifecycle_keeps_only_durable_reference_ids():
    class _Repository:
        def __init__(self):
            self.events = []

        def append_execution_event(self, event):
            self.events.append(event)
            return event

    repository = _Repository()
    lifecycle = HostCallLifecycle(repository, "ep", clock_ms=lambda: 1)
    handle = lifecycle.begin("compute_result", [{"job_id": "job-1"}])
    lifecycle.end(
        handle,
        ok=True,
        result={
            "job_id": "job-1",
            "version_id": "v-1",
            "content": "private result body",
        },
    )
    assert repository.events[-1].payload["references"] == {
        "job_id": "job-1",
        "version_id": "v-1",
    }
    assert "content" not in repository.events[-1].payload
