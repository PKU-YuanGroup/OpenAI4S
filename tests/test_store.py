"""Store schema/serializer contracts for artifact_versions and lineage_edges.

store.py is a future extraction target (docs/refactor-plan.md) — these lock
the row shapes and id conventions callers depend on TODAY, so an extraction
that drops or renames a column fails here first.
"""
import sqlite3

import pytest

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


def test_record_cell_artifact_coalesces_metadata_and_lineage_atomically(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    source = tmp_path / "input.txt"
    source.write_text("science")
    source_record = store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=7,
        checksum="source",
        frame_id=frame_id,
    )
    output = tmp_path / "output.csv"
    output.write_text("value\n1\n")
    checksum = "same-bytes"

    provenance = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type="text/plain",
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-1",
        frame_id=frame_id,
        input_version_ids=[source_record["version_id"], source_record["version_id"]],
    )
    repeated = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type="text/plain",
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-1",
        frame_id=frame_id,
        input_version_ids=[source_record["version_id"]],
    )
    assert repeated["version_id"] == provenance["version_id"]
    env_id = store.upsert_env_snapshot(
        {"kind": "python", "packages": [], "package_count": 0}
    )
    capture = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type="text/csv",
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-1",
        frame_id=frame_id,
        root_frame_id=frame_id,
        env_snapshot_id=env_id,
    )

    assert capture["artifact_id"] == provenance["artifact_id"]
    assert capture["version_id"] == provenance["version_id"]
    assert capture["content_type"] == "text/csv"
    assert len(store.list_versions(provenance["artifact_id"])) == 1
    metadata = store.version_meta(provenance["version_id"])
    assert metadata["content_type"] == "text/csv"
    assert metadata["env_snapshot_id"] == env_id
    assert store.get_artifact(provenance["artifact_id"])["content_type"] == "text/csv"
    assert store.lineage_inputs(provenance["version_id"]) == [
        {
            "version_id": source_record["version_id"],
            "filename": "input.txt",
            "path": str(source),
        }
    ]

    next_cell = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type="text/csv",
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-2",
        frame_id=frame_id,
    )
    assert next_cell["artifact_id"] == provenance["artifact_id"]
    assert next_cell["version_id"] != provenance["version_id"]
    assert len(store.list_versions(provenance["artifact_id"])) == 2


def test_record_cell_artifact_finds_exact_version_before_newer_duplicate(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    output = tmp_path / "result.csv"
    output.write_text("value\n1\n")
    checksum = "provenance-bytes"

    provenance = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type=None,
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-provenance",
        frame_id=frame_id,
    )
    intervening = store.save_artifact(
        path=str(tmp_path / "explicit-copy.csv"),
        filename=output.name,
        content_type="text/csv",
        size_bytes=8,
        checksum="explicit-bytes",
        producing_cell_id="cell-explicit",
        frame_id=frame_id,
    )

    capture = store.record_cell_artifact(
        path=str(output),
        filename=output.name,
        content_type="text/csv",
        size_bytes=8,
        checksum=checksum,
        producing_cell_id="cell-provenance",
        frame_id=frame_id,
    )

    assert capture["artifact_id"] == provenance["artifact_id"]
    assert capture["version_id"] == provenance["version_id"]
    assert store.get_artifact(provenance["artifact_id"])["latest_version_id"] == (
        provenance["version_id"]
    )
    assert store.get_artifact(intervening["artifact_id"])["latest_version_id"] == (
        intervening["version_id"]
    )


def test_provisional_policy_keeps_distinct_explicit_aliases(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    output = tmp_path / "physical.csv"
    output.write_text("value\n1\n")
    first_snapshot = tmp_path / "first.snapshot"
    second_snapshot = tmp_path / "second.snapshot"
    first_snapshot.write_bytes(output.read_bytes())
    second_snapshot.write_bytes(output.read_bytes())

    first = store.record_cell_artifact(
        path=str(output),
        filename="first.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum="same-bytes",
        producing_cell_id="cell-alias",
        frame_id=frame_id,
        snapshot_path=str(first_snapshot),
        reuse_policy="provisional",
    )
    second = store.record_cell_artifact(
        path=str(output),
        filename="second.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum="same-bytes",
        producing_cell_id="cell-alias",
        frame_id=frame_id,
        snapshot_path=str(second_snapshot),
        reuse_policy="provisional",
    )

    assert second["artifact_id"] != first["artifact_id"]
    assert store.get_artifact(first["artifact_id"])["filename"] == "first.csv"
    assert store.get_artifact(second["artifact_id"])["filename"] == "second.csv"
    assert len(store.list_versions(first["artifact_id"])) == 1
    assert len(store.list_versions(second["artifact_id"])) == 1


def test_record_cell_artifact_coalesces_unframed_alias_and_versions_new_bytes(
    tmp_path,
):
    store = _store(tmp_path)
    output = tmp_path / "unframed.txt"
    output.write_text("first")
    alias = tmp_path / "unframed-alias.txt"
    alias.symlink_to(output)

    first = store.record_cell_artifact(
        path=str(output),
        filename="result.txt",
        content_type=None,
        size_bytes=5,
        checksum="first-bytes",
        producing_cell_id="cell-unframed",
        frame_id=None,
    )
    finalized = store.record_cell_artifact(
        path=str(alias),
        filename="result.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum="first-bytes",
        producing_cell_id="cell-unframed",
        frame_id=None,
    )

    assert finalized["artifact_id"] == first["artifact_id"]
    assert finalized["version_id"] == first["version_id"]
    assert finalized["content_type"] == "text/plain"

    output.write_text("second")
    changed = store.record_cell_artifact(
        path=str(output),
        filename="result.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum="second-bytes",
        producing_cell_id="cell-unframed",
        frame_id=None,
    )

    assert changed["artifact_id"] == first["artifact_id"]
    assert changed["version_id"] != first["version_id"]
    assert len(store.list_versions(first["artifact_id"])) == 2


def test_record_cell_artifact_rolls_back_version_when_lineage_fails(tmp_path):
    store = _store(tmp_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    source = store.save_artifact(
        path=str(tmp_path / "input.txt"),
        filename="input.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="input",
        frame_id=frame_id,
    )
    store._conn.execute(
        "CREATE TRIGGER fail_lineage BEFORE INSERT ON lineage_edges "
        "BEGIN SELECT RAISE(ABORT, 'lineage failed'); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="lineage failed"):
        store.record_cell_artifact(
            path=str(tmp_path / "output.txt"),
            filename="output.txt",
            content_type="text/plain",
            size_bytes=1,
            checksum="output",
            producing_cell_id="cell-fail",
            frame_id=frame_id,
            input_version_ids=[source["version_id"]],
        )

    assert store.artifact_by_filename("output.txt", frame_id, strict=True) is None
    assert store.version_for_path(str(tmp_path / "output.txt")) is None


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


def test_version_for_path_breaks_timestamp_ties_by_newest_row(monkeypatch, tmp_path):
    import openai4s.store as store_module

    monkeypatch.setattr(store_module, "_now_ms", lambda: 123456)
    store = _store(tmp_path)
    first = store.save_artifact(
        path="/live/tied.txt",
        filename="tied.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="first",
    )
    second = store.save_artifact(
        path="/live/tied.txt",
        filename="tied.txt",
        content_type="text/plain",
        size_bytes=1,
        checksum="second",
        artifact_id=first["artifact_id"],
    )

    assert store.version_for_path("/live/tied.txt") == second["version_id"]


def test_version_for_path_resolves_legacy_relative_and_symlink_aliases(
    monkeypatch, tmp_path
):
    store = _store(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "relative.txt"
    target.write_text("data")
    monkeypatch.chdir(workspace)
    relative = store.save_artifact(
        path="relative.txt",
        filename="relative.txt",
        content_type="text/plain",
        size_bytes=4,
        checksum="relative",
    )
    assert store.version_for_path(str(target)) == relative["version_id"]

    physical = tmp_path / "physical"
    physical.mkdir()
    aliased = tmp_path / "alias"
    aliased.symlink_to(physical, target_is_directory=True)
    alias_path = aliased / "linked.txt"
    real_path = physical / "linked.txt"
    real_path.write_text("linked")
    exact = store.save_artifact(
        path=str(real_path),
        filename="linked.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum="exact-older",
    )
    linked = store.save_artifact(
        path=str(alias_path),
        filename="linked.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum="linked",
        artifact_id=exact["artifact_id"],
    )
    assert store.version_for_path(str(real_path)) == linked["version_id"]
    assert store.version_for_path(str(alias_path)) == linked["version_id"]

    external = tmp_path / "external"
    external_dir = external / "dir"
    external_dir.mkdir(parents=True)
    traversal_link = workspace / "traversal-link"
    traversal_link.symlink_to(external_dir, target_is_directory=True)
    external_secret = external / "secret.txt"
    workspace_secret = workspace / "secret.txt"
    external_secret.write_text("outside")
    workspace_secret.write_text("inside")
    lexical_traversal = traversal_link / ".." / "secret.txt"
    traversed = store.save_artifact(
        path=str(lexical_traversal),
        filename="secret.txt",
        content_type="text/plain",
        size_bytes=7,
        checksum="outside",
    )
    assert store.version_for_path(str(external_secret)) == traversed["version_id"]
    assert store.version_for_path(str(workspace_secret)) is None


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
