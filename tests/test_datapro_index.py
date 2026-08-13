"""Completeness and atomicity contracts for the local DataPro index.

These tests never contact DataPro.  They exercise the durable boundary that a
real ``dataPro_search`` result crosses only after credential redaction and a
strict integer ``structuredContent.code == 0`` check.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from openai4s import datapro
from openai4s.storage.datapro_index import MAX_INDEX_LEAVES, MAX_LOGICAL_ENTRIES
from openai4s.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "openai4s.db")


def _search(store: Store, needle: str, *, limit: int = 1000) -> dict:
    result = store.search_datapro_index(needle, limit=limit)
    assert isinstance(result, dict)
    assert isinstance(result.get("items"), list)
    assert type(result.get("total")) is int
    return result


def _table_names(store: Store) -> tuple[str, str]:
    rows = store._conn.execute(  # noqa: SLF001 - verifies transaction boundary
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'datapro_%' ORDER BY name"
    ).fetchall()
    names = [str(row[0]) for row in rows]
    batch = next((name for name in names if "batch" in name), "")
    entry = next((name for name in names if "entr" in name), "")
    assert batch and entry, names
    return batch, entry


@pytest.mark.parametrize(
    ("query", "content", "sentinel", "entry_count"),
    [
        (
            "academic papers",
            {
                "code": 0,
                "msg": "ok",
                "items": [
                    {
                        "name": "A complete paper",
                        "url": "https://example.invalid/paper",
                        "date": "2026-01-02",
                        "snippet": "abstract text",
                        "extra_data": {
                            "citation": {"doi": "10.0/example"},
                            "future_unknown": {"deep": ["academic-unknown-sentinel"]},
                        },
                    }
                ],
            },
            "academic-unknown-sentinel",
            1,
        ),
        (
            "other datasets list",
            {
                "code": 0,
                "items": [
                    {
                        "type": "other_datasets",
                        "data": {
                            "total": 2,
                            "items": [
                                {
                                    "id": "dataset-1",
                                    "future_unknown": "list-unknown-sentinel",
                                },
                                {"id": "dataset-2", "name": "second"},
                            ],
                        },
                        "wrapper_future": {"generation": 9},
                    }
                ],
            },
            "list-unknown-sentinel",
            2,
        ),
        (
            "entity profile",
            {
                "code": 0,
                "items": [
                    {
                        "type": "entity_profile",
                        "data": {
                            "aliases": ["Example Labs", "EL"],
                            "attributes": {"founded": 2024, "active": True},
                            "tags": ["science", "entity-unknown-sentinel"],
                        },
                    }
                ],
            },
            "entity-unknown-sentinel",
            1,
        ),
        (
            "future result shape",
            {
                "code": 0,
                "future_top_level": {
                    "never_seen_before": [
                        {"arbitrary_leaf": "future-shape-unknown-sentinel"}
                    ]
                },
            },
            "future-shape-unknown-sentinel",
            1,
        ),
    ],
)
def test_every_returned_field_is_indexed_without_a_schema_whitelist(
    tmp_path, query, content, sentinel, entry_count
):
    store = _store(tmp_path)

    receipt = store.index_datapro_result(
        query=query,
        structured_content=content,
    )

    assert receipt["complete"] is True
    assert receipt["entry_count"] == entry_count
    assert receipt["source_leaf_count"] == receipt["indexed_leaf_count"]
    assert receipt["source_digest"] == receipt["indexed_digest"]
    assert receipt["source_digest"]
    found = _search(store, sentinel)
    assert found["total"] >= 1
    assert found["items"]

    batch = store.get_datapro_index_batch(receipt["batch_id"])
    assert batch is not None
    assert batch["source_leaf_count"] == batch["indexed_leaf_count"]
    assert batch["source_digest"] == batch["indexed_digest"]
    assert batch["complete"] is True
    persisted = store._conn.execute(  # noqa: SLF001 - completeness oracle
        "SELECT structured_content,search_text FROM datapro_index_batches "
        "WHERE batch_id=?",
        (receipt["batch_id"],),
    ).fetchone()
    assert json.loads(persisted["structured_content"]) == content
    assert sentinel in persisted["search_text"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ('unknown"quoted-key', "quoted-key-value"),
        ("newline-key", "gamma\ndelta"),
        ("backslash-key", r"eps\zeta"),
        ("percent-key", "100% complete"),
        ("underscore-key", "literal_value"),
    ],
)
def test_original_key_and_scalar_characters_are_searchable_without_json_escaping(
    tmp_path, field, value
):
    store = _store(tmp_path)
    receipt = store.index_datapro_result(
        query="literal character coverage",
        structured_content={"code": 0, "items": [{field: value}]},
    )

    for original in (field, value):
        found = _search(store, original)
        assert found["total"] == 1
        assert found["items"][0]["batch_id"] == receipt["batch_id"]


def test_nul_bearing_keys_and_scalars_are_searchable_on_both_sides(tmp_path):
    store = _store(tmp_path)
    field = "nul-key-before\x00nul-key-after"
    value = "nul-value-before\x00nul-value-after%_literal"
    receipt = store.index_datapro_result(
        query="NUL character coverage",
        structured_content={"code": 0, "items": [{field: value}]},
    )

    # Full strings exercise the broken SQLite path: LIKE normally treats the
    # embedded NUL as the end of its pattern. Ordinary suffixes prove content
    # after the NUL was not dropped, and upper-case queries pin LIKE's existing
    # ASCII case-insensitive semantics. Percent/underscore stay literal.
    for needle in (
        field,
        value,
        "nul-key-after",
        "nul-value-after%_literal",
        value.upper(),
    ):
        found = _search(store, needle)
        assert found["total"] == 1
        assert found["items"][0]["batch_id"] == receipt["batch_id"]


def test_large_nested_wrapper_is_stored_once_but_context_projection_survives(
    tmp_path,
):
    store = _store(tmp_path)
    wrapper_blob = "wrapper-search-sentinel:" + ("w" * (100 * 1024))
    children = [
        {"id": f"child-{index}", "marker": f"nested-child-{index}"}
        for index in range(50)
    ]
    content = {
        "code": 0,
        "items": [
            {
                "type": "other_datasets",
                "wrapper_blob": wrapper_blob,
                "wrapper_unknown": {"owner": "wrapper-owner-search-sentinel"},
                "data": {"total": len(children), "items": children},
            }
        ],
    }

    receipt = store.index_datapro_result(
        query="large nested wrapper",
        structured_content=content,
    )
    assert receipt["entry_count"] == len(children)

    sizes = store._conn.execute(  # noqa: SLF001 - storage amplification oracle
        "SELECT "
        "(SELECT length(structured_content)+length(search_text) "
        " FROM datapro_index_batches WHERE batch_id=?) AS batch_payload,"
        "SUM(length(content_json)+length(search_text)+"
        "COALESCE(length(context_json),0)) AS entry_payload,"
        "SUM(CASE WHEN context_json IS NULL THEN 1 ELSE 0 END) AS null_contexts "
        "FROM datapro_index_entries WHERE batch_id=?",
        (receipt["batch_id"], receipt["batch_id"]),
    ).fetchone()
    source_size = len(
        json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    assert sizes["null_contexts"] == len(children)
    assert sizes["entry_payload"] < source_size // 4
    assert sizes["batch_payload"] + sizes["entry_payload"] < source_size * 3

    # Wrapper fields remain searchable through the one canonical batch copy.
    # The public entry projection reconstructs the historical context object
    # on read, so removing its persisted duplicate does not change the API.
    found = store.search_datapro_index(
        "wrapper-owner-search-sentinel", limit=1, include_context=True
    )
    assert found["total"] == len(children)
    assert found["truncated"] is True
    assert found["items"][0]["context"]["wrapper_blob"] == wrapper_blob
    assert found["items"][0]["context"]["data"] == {"total": len(children)}
    assert found["items"][0]["dataset_type"] == "other_datasets"

    # The command palette uses a deliberately lightweight projection. A batch
    # match applies to every child, but the shared wrapper must not be copied
    # into twenty response rows (or selected twenty times by the SQL join).
    palette = store.search("wrapper-owner-search-sentinel")
    assert len(palette["datapro"]) == 20
    assert all("context" not in item for item in palette["datapro"])
    assert all(item["dataset_type"] == "other_datasets" for item in palette["datapro"])
    palette_bytes = len(json.dumps(palette, ensure_ascii=False).encode("utf-8"))
    assert palette_bytes < source_size // 4

    batch = store.get_datapro_index_batch(receipt["batch_id"])
    assert batch is not None
    assert batch["entries"][0]["context"]["wrapper_unknown"] == {
        "owner": "wrapper-owner-search-sentinel"
    }


def test_redacted_mapping_key_collision_preserves_every_non_secret_value(tmp_path):
    store = _store(tmp_path)
    secret = "collision-agent-plan-credential"
    safe = datapro.redact_mcp_result(
        {
            "is_error": False,
            "raw": {
                "content": [],
                "structuredContent": {
                    "code": 0,
                    secret: "first-collision-value",
                    "[REDACTED]": "second-collision-value",
                },
            },
        },
        secret,
    )
    structured = safe["raw"]["structuredContent"]
    assert structured["[REDACTED]"] == "first-collision-value"
    assert structured["[REDACTED]#2"] == "second-collision-value"
    assert secret not in json.dumps(safe)

    receipt = datapro.index_successful_search(
        store,
        query="collision-safe indexing",
        result=safe,
        secrets=(secret,),
    )
    assert receipt is not None and receipt["complete"] is True
    assert _search(store, "first-collision-value")["total"] == 1
    assert _search(store, "second-collision-value")["total"] == 1


def test_short_credential_embedded_in_mapping_key_is_fully_redacted(tmp_path):
    store = _store(tmp_path)
    secret = "key-123"
    safe = datapro.redact_mcp_result(
        {
            "is_error": False,
            "raw": {
                "content": [],
                "structuredContent": {
                    "code": 0,
                    f"prefix-{secret}-suffix": "short-key-value-sentinel",
                },
            },
        },
        secret,
    )

    serialized = json.dumps(safe, ensure_ascii=False)
    assert secret not in serialized
    assert "prefix-[REDACTED]-suffix" in safe["raw"]["structuredContent"]
    receipt = datapro.index_successful_search(
        store,
        query="short credential mapping key",
        result=safe,
        secrets=(secret,),
    )
    assert receipt is not None and receipt["complete"] is True
    assert secret not in json.dumps(store.get_datapro_index_batch(receipt["batch_id"]))
    assert _search(store, "short-key-value-sentinel")["total"] == 1


def test_complete_result_envelope_indexes_content_text_and_future_fields(tmp_path):
    store = _store(tmp_path)
    result = {
        "is_error": False,
        "text": "top-level-text-only-sentinel",
        "raw": {
            "content": [{"type": "text", "text": "content-block-only-sentinel"}],
            "structuredContent": {"code": 0, "items": []},
            "future_result_field": {"unknown": "future-envelope-only-sentinel"},
        },
    }

    receipt = datapro.index_successful_search(
        store,
        query="complete result envelope",
        result=result,
    )

    assert receipt is not None and receipt["complete"] is True
    for sentinel in (
        "top-level-text-only-sentinel",
        "content-block-only-sentinel",
        "future-envelope-only-sentinel",
    ):
        assert _search(store, sentinel)["total"] == 1
    batch = store.get_datapro_index_batch(receipt["batch_id"])
    assert batch is not None
    assert batch["structured_content"] == result


def test_large_result_indexes_all_257_entries_without_truncation(tmp_path):
    store = _store(tmp_path)
    items = [
        {
            "ordinal": ordinal,
            "marker": f"bulk-sentinel-{ordinal:03d}-unique",
        }
        for ordinal in range(257)
    ]

    receipt = store.index_datapro_result(
        query="large complete response",
        structured_content={"code": 0, "items": items},
    )

    assert receipt["entry_count"] == 257
    assert receipt["source_leaf_count"] == receipt["indexed_leaf_count"]
    assert receipt["source_digest"] == receipt["indexed_digest"]
    for ordinal in (0, 128, 256):
        result = _search(store, f"bulk-sentinel-{ordinal:03d}-unique")
        assert any(
            item["json_pointer"] == f"/items/{ordinal}" for item in result["items"]
        )


def test_pathological_entry_count_fails_closed_without_partial_index(tmp_path):
    store = _store(tmp_path)
    content = {"code": 0, "items": [{} for _ in range(MAX_LOGICAL_ENTRIES + 1)]}

    with pytest.raises(ValueError, match="safe logical-entry indexing limit"):
        store.index_datapro_result(
            query="pathological derived entry count", structured_content=content
        )

    batch_table, entry_table = _table_names(store)
    assert (
        store._conn.execute(f'SELECT COUNT(*) FROM "{batch_table}"').fetchone()[0] == 0
    )  # noqa: SLF001
    assert (
        store._conn.execute(f'SELECT COUNT(*) FROM "{entry_table}"').fetchone()[0] == 0
    )  # noqa: SLF001


def test_query_text_is_part_of_the_searchable_index(tmp_path):
    store = _store(tmp_path)
    query = "query-only-datapro-sentinel-7f39c1"
    receipt = store.index_datapro_result(
        query=query,
        structured_content={
            "code": 0,
            "items": [{"name": "content deliberately omits the search phrase"}],
        },
    )
    batch = store.get_datapro_index_batch(receipt["batch_id"])
    assert batch is not None
    assert query not in json.dumps(batch["structured_content"], ensure_ascii=False)

    result = _search(store, query)

    assert result["total"] == 1
    assert result["items"][0]["batch_id"] == receipt["batch_id"]


def test_reingest_is_idempotent_but_duplicate_occurrences_do_not_collapse(tmp_path):
    store = _store(tmp_path)
    duplicate = {"name": "same record", "marker": "duplicate-pointer-sentinel"}
    content = {"code": 0, "items": [duplicate, dict(duplicate)]}

    first = store.index_datapro_result(
        query="duplicate occurrence",
        structured_content=content,
    )
    second = store.index_datapro_result(
        query="duplicate occurrence",
        structured_content=content,
    )

    assert first["batch_id"] == second["batch_id"]
    assert first["entry_count"] == second["entry_count"] == 2
    result = _search(store, "duplicate-pointer-sentinel")
    assert result["total"] == 2
    assert {item["json_pointer"] for item in result["items"]} == {
        "/items/0",
        "/items/1",
    }


def test_entry_failure_rolls_back_batch_and_prior_entries_then_retry_is_complete(
    tmp_path,
):
    store = _store(tmp_path)
    batch_table, entry_table = _table_names(store)
    quoted_entry = '"' + entry_table.replace('"', '""') + '"'
    store._conn.execute(  # noqa: SLF001 - fault injection at SQLite boundary
        f"CREATE TRIGGER datapro_test_fail_third BEFORE INSERT ON {quoted_entry} "
        f"WHEN (SELECT COUNT(*) FROM {quoted_entry}) >= 2 "
        "BEGIN SELECT RAISE(FAIL, 'injected third DataPro entry failure'); END"
    )
    store._conn.commit()  # noqa: SLF001
    content = {
        "code": 0,
        "items": [{"record": index} for index in range(4)],
    }

    with pytest.raises(sqlite3.IntegrityError, match="injected third"):
        store.index_datapro_result(
            query="atomic insertion",
            structured_content=content,
        )

    for table in (batch_table, entry_table):
        quoted = '"' + table.replace('"', '""') + '"'
        count = store._conn.execute(  # noqa: SLF001
            f"SELECT COUNT(*) FROM {quoted}"
        ).fetchone()[0]
        assert count == 0

    store._conn.execute("DROP TRIGGER datapro_test_fail_third")  # noqa: SLF001
    store._conn.commit()  # noqa: SLF001
    receipt = store.index_datapro_result(
        query="atomic insertion",
        structured_content=content,
    )
    assert receipt["entry_count"] == 4
    assert receipt["complete"] is True


@pytest.mark.parametrize("code", [4011, "0", 0.0, False])
def test_only_a_strict_integer_zero_result_reaches_the_index(tmp_path, code):
    store = _store(tmp_path)
    sentinel = f"must-not-index-{type(code).__name__}-{code!s}"

    receipt = datapro.index_successful_search(
        store,
        query="not successful",
        result={
            "structuredContent": {
                "code": code,
                "items": [{"marker": sentinel}],
            }
        },
    )

    assert receipt is None
    assert _search(store, sentinel)["total"] == 0
    batch_table, entry_table = _table_names(store)
    assert (
        store._conn.execute(  # noqa: SLF001
            f'SELECT COUNT(*) FROM "{batch_table}"'
        ).fetchone()[0]
        == 0
    )
    assert (
        store._conn.execute(  # noqa: SLF001
            f'SELECT COUNT(*) FROM "{entry_table}"'
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(ValueError, match="strict integer code-0"):
        store.index_datapro_result(
            query="direct internal bypass",
            structured_content={"code": code, "items": [{"marker": sentinel}]},
        )


def test_unknown_nested_leaf_explosion_fails_closed_without_partial_index(tmp_path):
    store = _store(tmp_path)
    content = {
        "code": 0,
        "future_nested": [None for _ in range(MAX_INDEX_LEAVES + 1)],
    }

    with pytest.raises(ValueError, match="safe index leaf limit"):
        store.index_datapro_result(
            query="unknown future leaf explosion", structured_content=content
        )

    batch_table, entry_table = _table_names(store)
    for table in (batch_table, entry_table):
        assert (
            store._conn.execute(  # noqa: SLF001
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            == 0
        )


def test_frame_scope_is_canonical_and_deleting_artifact_removes_its_index(tmp_path):
    store = _store(tmp_path)
    root = store.new_frame(project_id="datapro-project", kind="turn")
    child = store.new_frame(
        parent_id=root,
        project_id="datapro-project",
        kind="delegate",
        depth=1,
    )
    payload = tmp_path / "datapro-result.json"
    payload.write_text('{"saved":true}', encoding="utf-8")
    artifact = store.save_artifact(
        path=str(payload),
        filename=payload.name,
        content_type="application/json",
        size_bytes=payload.stat().st_size,
        checksum=hashlib.sha256(payload.read_bytes()).hexdigest(),
        frame_id=child,
        project_id="datapro-project",
    )

    receipt = store.index_datapro_result(
        query="scoped result",
        structured_content={
            "code": 0,
            "items": [{"marker": "scoped-artifact-sentinel"}],
        },
        frame_id=child,
        artifact_id=artifact["artifact_id"],
    )
    batch = store.get_datapro_index_batch(receipt["batch_id"])
    assert batch is not None
    assert batch["root_frame_id"] == root
    assert batch["project_id"] == "datapro-project"
    assert batch["artifact_id"] == artifact["artifact_id"]
    palette = store.search("scoped-artifact-sentinel")
    assert "datapro" in palette
    assert [item["batch_id"] for item in palette["datapro"]] == [receipt["batch_id"]]

    store.delete_artifact(artifact["artifact_id"])

    assert store.get_datapro_index_batch(receipt["batch_id"]) is None
    assert _search(store, "scoped-artifact-sentinel")["total"] == 0


def test_identical_real_call_occurrences_keep_independent_artifact_lifecycles(
    tmp_path,
):
    store = _store(tmp_path)
    content = {
        "code": 0,
        "items": [{"marker": "repeated-call-artifact-sentinel"}],
    }
    artifacts = []
    for label in ("a", "b"):
        payload = tmp_path / f"datapro-{label}.json"
        payload.write_text(f'{{"call":"{label}"}}', encoding="utf-8")
        artifacts.append(
            store.save_artifact(
                path=str(payload),
                filename=payload.name,
                content_type="application/json",
                size_bytes=payload.stat().st_size,
                checksum=hashlib.sha256(payload.read_bytes()).hexdigest(),
                project_id="default",
            )
        )

    first = store.index_datapro_result(
        query="same real query",
        structured_content=content,
        artifact_id=artifacts[0]["artifact_id"],
        occurrence_id="real-call-a",
    )
    second = store.index_datapro_result(
        query="same real query",
        structured_content=content,
        artifact_id=artifacts[1]["artifact_id"],
        occurrence_id="real-call-b",
    )
    assert first["batch_id"] != second["batch_id"]
    assert _search(store, "repeated-call-artifact-sentinel")["total"] == 2

    store.delete_artifact(artifacts[1]["artifact_id"])

    assert store.get_datapro_index_batch(second["batch_id"]) is None
    assert store.get_datapro_index_batch(first["batch_id"]) is not None
    assert _search(store, "repeated-call-artifact-sentinel")["total"] == 1


def test_session_and_project_deletion_remove_their_datapro_index_rows(tmp_path):
    store = _store(tmp_path)
    store.create_project(name="DataPro project", project_id="delete-datapro-project")
    session_only = store.new_frame(project_id="default", kind="turn")
    project_session = store.new_frame(
        project_id="delete-datapro-project",
        kind="turn",
    )
    session_batch = store.index_datapro_result(
        query="session cleanup",
        structured_content={
            "code": 0,
            "items": [{"marker": "datapro-session-delete-sentinel"}],
        },
        frame_id=session_only,
    )
    project_batch = store.index_datapro_result(
        query="project cleanup",
        structured_content={
            "code": 0,
            "items": [{"marker": "datapro-project-delete-sentinel"}],
        },
        frame_id=project_session,
    )

    store.delete_frame(session_only)

    assert store.get_datapro_index_batch(session_batch["batch_id"]) is None
    assert _search(store, "datapro-session-delete-sentinel")["total"] == 0
    assert store.get_datapro_index_batch(project_batch["batch_id"]) is not None

    store.delete_project("delete-datapro-project")

    assert store.get_datapro_index_batch(project_batch["batch_id"]) is None
    assert _search(store, "datapro-project-delete-sentinel")["total"] == 0


def test_agent_sql_cannot_read_or_discover_the_internal_datapro_index(tmp_path):
    store = _store(tmp_path)
    root = store.new_frame(project_id="index-scope", kind="turn")
    store.index_datapro_result(
        query="scoped agent query",
        structured_content={"code": 0, "items": [{"marker": "scope-visible"}]},
        frame_id=root,
    )

    schema = store.schema()
    assert "datapro_index_batches" not in schema
    assert "datapro_index_entries" not in schema
    assert "my_datapro_batches" not in schema
    assert "my_datapro_entries" not in schema
    scope = {"root_frame_id": root, "project_id": "index-scope"}
    for table in ("datapro_index_batches", "datapro_index_entries"):
        with pytest.raises(PermissionError):
            store.query(f"SELECT * FROM {table}", scope=scope)
    for view in ("my_datapro_batches", "my_datapro_entries"):
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            store.query(f"SELECT * FROM {view}", scope=scope)
