"""Store schema/serializer contracts for artifact_versions and lineage_edges.

store.py is a future extraction target (docs/refactor-plan.md) — these lock
the row shapes and id conventions callers depend on TODAY, so an extraction
that drops or renames a column fails here first.
"""
from openai4s.config import Config, LLMConfig
from openai4s.store import get_store


def _store(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return get_store(cfg.db_path)


# --- frames ------------------------------------------------------------------
def test_frames_row_columns_and_runtime_env_roundtrip(tmp_path):
    """The frames row shape an extraction must preserve (base schema + migration
    columns), and the runtime_env pin the resumed-session env selection depends
    on: a freshly created frame has it NULL, update_frame persists it."""
    store = _store(tmp_path)
    fid = store.new_frame(kind="turn", project_id="default")

    row = store.get_frame(fid)
    assert set(row) == {
        "frame_id",
        "parent_id",
        "project_id",
        "root_frame_id",
        "kind",
        "name",
        "task_summary",
        "model",
        "effort",
        "status",
        "runtime_env",
        "folder_id",
        "depth",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "created_at",
        "updated_at",
    }
    # a brand-new frame has no pinned runtime env yet
    assert row["runtime_env"] is None

    store.update_frame(fid, runtime_env="struct")
    assert store.get_frame(fid)["runtime_env"] == "struct"


# --- artifact_versions -------------------------------------------------------
def test_save_artifact_return_shape_and_version_row_columns(tmp_path):
    store = _store(tmp_path)
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n")

    rec = store.save_artifact(
        path=str(f),
        filename="data.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum="abc123",
        producing_cell_id="cell-1",
        frame_id="f-1",
        project_id="default",
    )
    # the serializer dict handed back to every caller (gateway, dispatcher)
    assert set(rec) == {
        "artifact_id",
        "version_id",
        "filename",
        "path",
        "content_type",
        "size_bytes",
        "checksum",
        "created_at",
    }
    assert rec["artifact_id"].startswith("a-")
    assert rec["version_id"].startswith("v-")
    assert isinstance(rec["created_at"], int)

    # the raw artifact_versions row: base schema + migration columns
    row = store.version_meta(rec["version_id"])
    assert set(row) == {
        "version_id",
        "artifact_id",
        "filename",
        "content_type",
        "size_bytes",
        "checksum",
        "path",
        "snapshot_path",
        "producing_cell_id",
        "frame_id",
        "created_at",
        "env_snapshot_id",
    }
    assert row["path"] == str(f)
    assert row["snapshot_path"] is None  # bound later by the gateway snapshotter
    assert row["env_snapshot_id"] is None
    assert row["producing_cell_id"] == "cell-1"
    assert row["frame_id"] == "f-1"
    assert store.version_meta("v-does-not-exist") is None


def test_list_versions_row_shape_ordering_and_latest_pointer(tmp_path):
    store = _store(tmp_path)
    rec1 = store.save_artifact(
        path="/w/x.txt",
        filename="x.txt",
        content_type=None,
        size_bytes=1,
        checksum=None,
    )
    rec2 = store.save_artifact(
        path="/w/x.txt",
        filename="x.txt",
        content_type=None,
        size_bytes=2,
        checksum=None,
        artifact_id=rec1["artifact_id"],
    )
    # latest_version_id never dangles: it points at the newest version row
    assert store.get_artifact(rec1["artifact_id"])["latest_version_id"] == (
        rec2["version_id"]
    )

    vs = store.list_versions(rec1["artifact_id"])
    assert set(vs[0]) == {
        "version_id",
        "filename",
        "content_type",
        "size_bytes",
        "checksum",
        "producing_cell_id",
        "frame_id",
        "created_at",
        "is_latest",
        "ordinal",
    }
    # newest first (rowid breaks same-millisecond ties), ordinal 1 = oldest
    assert [v["version_id"] for v in vs] == [rec2["version_id"], rec1["version_id"]]
    assert [v["ordinal"] for v in vs] == [2, 1]
    assert vs[0]["is_latest"] is True and vs[1]["is_latest"] is False


def test_resolve_artifact_path_prefers_snapshot_keeps_live_path(tmp_path):
    """resolve_artifact_path serves COALESCE(snapshot_path, path) for both
    version and artifact idents, while set_version_snapshot leaves ``path``
    untouched so the version_for_path reverse lookup keeps resolving."""
    store = _store(tmp_path)
    rec = store.save_artifact(
        path="/live/file.txt",
        filename="file.txt",
        content_type=None,
        size_bytes=1,
        checksum=None,
    )
    assert store.resolve_artifact_path(rec["version_id"]) == "/live/file.txt"

    store.set_version_snapshot(rec["version_id"], "/frozen/file.txt")
    assert store.resolve_artifact_path(rec["version_id"]) == "/frozen/file.txt"
    assert store.resolve_artifact_path(rec["artifact_id"]) == "/frozen/file.txt"
    assert store.version_meta(rec["version_id"])["path"] == "/live/file.txt"
    assert store.version_for_path("/live/file.txt") == rec["version_id"]
    assert store.resolve_artifact_path("nope") is None


# --- lineage_edges -----------------------------------------------------------
def test_lineage_edge_row_shape_and_directional_queries(tmp_path):
    store = _store(tmp_path)
    rec_in = store.save_artifact(
        path="/w/in.csv",
        filename="in.csv",
        content_type="text/csv",
        size_bytes=1,
        checksum=None,
    )
    rec_out = store.save_artifact(
        path="/w/out.csv",
        filename="out.csv",
        content_type="text/csv",
        size_bytes=1,
        checksum=None,
    )
    store.add_lineage_edge(
        input_version_id=rec_in["version_id"],
        output_version_id=rec_out["version_id"],
        producing_cell_id="cell-9",
        frame_id="f-9",
    )

    # raw row shape (the schema contract an extraction must preserve)
    row = store._conn.execute("SELECT * FROM lineage_edges").fetchone()
    assert set(row.keys()) == {
        "edge_id",
        "input_version_id",
        "output_version_id",
        "producing_cell_id",
        "frame_id",
        "created_at",
    }
    assert row["edge_id"].startswith("e-")
    assert row["input_version_id"] == rec_in["version_id"]
    assert row["output_version_id"] == rec_out["version_id"]
    assert row["producing_cell_id"] == "cell-9"
    assert row["frame_id"] == "f-9"

    # serializer: lineage_inputs joins back to the input's file identity
    assert store.lineage_inputs(rec_out["version_id"]) == [
        {
            "version_id": rec_in["version_id"],
            "filename": "in.csv",
            "path": "/w/in.csv",
        }
    ]
    # directional traversal returns BARE version ids, both ways
    assert store.lineage_edges_for(rec_out["version_id"], "up") == [
        rec_in["version_id"]
    ]
    assert store.lineage_edges_for(rec_in["version_id"], "down") == [
        rec_out["version_id"]
    ]
    assert store.lineage_edges_for("v-unknown", "up") == []


def test_lineage_input_with_no_version_row_keeps_id_null_identity(tmp_path):
    """LEFT JOIN contract: an edge whose input version row is missing still
    reports the id, with filename/path null — never dropped from the list."""
    store = _store(tmp_path)
    rec_out = store.save_artifact(
        path="/w/out.csv",
        filename="out.csv",
        content_type=None,
        size_bytes=1,
        checksum=None,
    )
    store.add_lineage_edge(
        input_version_id="v-ghost", output_version_id=rec_out["version_id"]
    )
    assert store.lineage_inputs(rec_out["version_id"]) == [
        {"version_id": "v-ghost", "filename": None, "path": None}
    ]
