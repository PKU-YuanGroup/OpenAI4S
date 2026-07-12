"""Web composition contracts for durable Context Policy V2."""

from __future__ import annotations

import hashlib
import json

from openai4s.config import Config, LLMConfig
from openai4s.server.gateway import SessionRunner, SessionState


class _Hub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, root_frame_id, event):
        self.events.append((root_frame_id, event))

    def emitter(self, root_frame_id):
        return lambda event: self.broadcast(root_frame_id, event)


def _runner(tmp_path):
    hub = _Hub()
    runner = SessionRunner(
        Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test"),
        ),
        hub,
        start_idle_sweeper=False,
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="science", status="ready")
    state = SessionState(frame_id, "science", runner.workspace_for(frame_id))
    return runner, hub, state


def test_large_context_output_is_a_deduplicated_artifact_version(tmp_path):
    runner, hub, state = _runner(tmp_path)
    try:
        content = {"rows": ["measurement"] * 100}
        canonical = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        first = runner._archive_context_output(
            state,
            content,
            {"role": "tool"},
            {"sha256": digest, "original_chars": len(canonical)},
        )
        second = runner._archive_context_output(
            state,
            content,
            {"role": "tool"},
            {"sha256": digest, "original_chars": len(canonical)},
        )

        assert first["artifact_id"] == second["artifact_id"]
        assert first["version_id"] == second["version_id"]
        artifact = runner.store.get_artifact(first["artifact_id"])
        assert artifact["root_frame_id"] == state.root_frame_id
        assert artifact["project_id"] == "science"
        assert ".openai4s-context" in first["path"]
        assert any(event[1].get("type") == "artifact_created" for event in hub.events)
    finally:
        runner.close()


def test_compaction_payload_is_linked_into_session_history(tmp_path):
    runner, _hub, state = _runner(tmp_path)
    try:
        archive_id = runner._archive_compaction_record(
            state,
            {
                "metadata": {
                    "branch": state.root_frame_id,
                    "ledger_cursor": {"group_id": "ag-1", "ordinal": 2},
                    "recovery_pointer": {"checkpoint_id": "cp-1"},
                    "active_kernel_generation": "generation-1",
                },
                "summary": "summary",
                "handoff": "handoff",
                "context_estimate_before": {"total": 1000},
                "context_estimate_after": {"total": 300},
                "compacted_messages": [
                    {
                        "role": "tool",
                        "content": "preview",
                        "artifact_refs": [{"artifact_id": "a-1", "version_id": "v-1"}],
                    }
                ],
            },
        )

        history = runner.store.list_compaction_archives(state.root_frame_id)
        assert [item["archive_id"] for item in history] == [archive_id]
        assert history[0]["ledger_cursor"]["ordinal"] == 2
        assert history[0]["artifact_refs"][0]["version_id"] == "v-1"
    finally:
        runner.close()
