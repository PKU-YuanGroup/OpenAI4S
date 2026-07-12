"""Project-wide Timeline and lineage read-model contracts."""

from __future__ import annotations

from openai4s.server.action_timeline import ActionTimelineService
from openai4s.server.global_views import GlobalResearchViewService
from openai4s.store import Store


def test_global_timeline_merges_sessions_without_raw_payloads(tmp_path):
    store = Store(tmp_path / "global.db")
    first = store.new_frame(project_id="science", name="First")
    second = store.new_frame(project_id="science", name="Second")
    store.append_action_group(
        root_frame_id=second,
        turn_id="turn-2",
        kind="user",
        assistant_message={"role": "user", "content": "later"},
        created_at=20,
    )
    store.append_action_group(
        root_frame_id=first,
        turn_id="turn-1",
        kind="user",
        assistant_message={"role": "user", "content": "earlier"},
        wire_state={"secret": "must-not-leak"},
        created_at=10,
    )

    view = GlobalResearchViewService(store, ActionTimelineService(store)).timeline_view(
        "science"
    )

    assert [group["root_frame_id"] for group in view["groups"]] == [first, second]
    assert [group["session"]["name"] for group in view["groups"]] == [
        "First",
        "Second",
    ]
    assert "must-not-leak" not in repr(view)
    store.close()


def test_global_lineage_connects_artifact_versions_and_cells(tmp_path):
    store = Store(tmp_path / "global.db")
    root = store.new_frame(project_id="science")
    first = store.save_artifact(
        path=str(tmp_path / "input.csv"),
        filename="input.csv",
        content_type="text/csv",
        size_bytes=3,
        checksum="a" * 64,
        producing_cell_id="cell-input",
        frame_id=root,
        root_frame_id=root,
        project_id="science",
    )
    second = store.save_artifact(
        path=str(tmp_path / "output.csv"),
        filename="output.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="b" * 64,
        producing_cell_id="cell-output",
        frame_id=root,
        root_frame_id=root,
        project_id="science",
    )
    store.add_lineage_edge(
        input_version_id=first["version_id"],
        output_version_id=second["version_id"],
        producing_cell_id="cell-output",
        frame_id=root,
    )

    view = GlobalResearchViewService(store, ActionTimelineService(store)).lineage_view(
        "science"
    )

    ids = {node["id"] for node in view["nodes"]}
    assert first["version_id"] in ids
    assert second["version_id"] in ids
    assert "cell:cell-output" in ids
    assert {
        "from": first["version_id"],
        "to": second["version_id"],
        "kind": "artifact_lineage",
    } in view["edges"]
    assert {
        "from": "cell:cell-output",
        "to": second["version_id"],
        "kind": "produced",
    } in view["edges"]
    store.close()
