"""Table workbench profile/export: parser snapshot and resource bounds."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openai4s.config import Config, RoadmapFeatureFlags
from openai4s.server import artifact_workbench as workbench_mod
from openai4s.server import table_profile as profile_mod
from openai4s.server.artifact_workbench import query_table
from openai4s.server.table_profile import (
    MAX_TABLE_PROFILE_BINS,
    TABLE_QUERY_PARSER_VERSION,
    export_csv_chunks,
    parquet_engine_available,
    parse_table_query,
    profile_cache_clear,
    profile_from_prepared,
    resource_manifest_ready,
    table_workbench_ga_blocked,
)
from tests.test_artifact_workbench import (
    _call,
    _checksum,
    _freeze_version,
    _save_snapshot,
    _setup,
)

FIXED_CSV = "name,n,score\n" "a,1,1.5\n" "b,1,2.5\n" "c,2,\n" "a,3,3.5\n"

PARSER_SNAPSHOT = (
    (
        {},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"sort": ["n"], "dir": ["desc"]},
        "page",
        {
            "sort": "n",
            "descending": True,
            "filters": {},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"dir": ["DESC"]},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"dir": ["desc"]},
        "page",
        {
            "sort": "",
            "descending": True,
            "filters": {},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"offset": ["-3"], "limit": ["999"]},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {},
            "offset": -3,
            "limit": 999,
            "version_id": "",
        },
    ),
    (
        {"q_name": ["Al"], "q_n": ["7"]},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {"name": "Al", "n": "7"},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"q_name": [""]},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {"name": ""},
            "offset": 0,
            "limit": 50,
            "version_id": "",
        },
    ),
    (
        {"version_id": ["v-exact"]},
        "page",
        {
            "sort": "",
            "descending": False,
            "filters": {},
            "offset": 0,
            "limit": 50,
            "version_id": "v-exact",
        },
    ),
    (
        {"version_id": ["v-1"], "q_n": ["1"]},
        "profile",
        {
            "sort": "",
            "descending": False,
            "filters": {"n": "1"},
            "offset": 0,
            "limit": 50,
            "version_id": "v-1",
        },
    ),
    (
        {"version_id": ["v-1"], "sort": ["n"], "dir": ["desc"], "q_n": ["1"]},
        "export",
        {
            "sort": "n",
            "descending": True,
            "filters": {"n": "1"},
            "offset": 0,
            "limit": 50,
            "version_id": "v-1",
        },
    ),
)


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    profile_cache_clear()
    yield
    profile_cache_clear()


def _column(payload: dict, name: str) -> dict:
    for item in payload["columns"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"missing column {name}")


def test_shared_parser_snapshot_locks_historical_table_semantics():
    assert TABLE_QUERY_PARSER_VERSION == 2
    for query, mode, expected in PARSER_SNAPSHOT:
        parsed = parse_table_query(query, mode=mode)
        assert parsed.sort == expected["sort"]
        assert parsed.descending is expected["descending"]
        assert parsed.filters == expected["filters"]
        assert parsed.offset == expected["offset"]
        assert parsed.limit == expected["limit"]
        assert parsed.version_id == expected["version_id"]


def test_export_spreadsheet_safe_query_is_explicit_and_export_only():
    raw = parse_table_query({"version_id": ["v-1"]}, mode="export")
    safe = parse_table_query(
        {"version_id": ["v-1"], "spreadsheet_safe": ["1"]},
        mode="export",
    )
    explicit_raw = parse_table_query(
        {"version_id": ["v-1"], "spreadsheet_safe": ["0"]},
        mode="export",
    )
    assert raw.spreadsheet_safe is False
    assert safe.spreadsheet_safe is True
    assert explicit_raw.spreadsheet_safe is False

    for mode in ("page", "profile"):
        with pytest.raises(workbench_mod.WorkbenchError) as error:
            parse_table_query(
                {"version_id": ["v-1"], "spreadsheet_safe": ["1"]},
                mode=mode,
            )
        assert (error.value.status, error.value.code) == (400, "invalid_query")
    with pytest.raises(workbench_mod.WorkbenchError) as error:
        parse_table_query(
            {"version_id": ["v-1"], "spreadsheet_safe": ["true"]},
            mode="export",
        )
    assert (error.value.status, error.value.code) == (400, "invalid_query")


def test_shared_parser_pagination_clamps_only_inside_query_table():
    rows = [["n"], *[[str(i)] for i in range(10)]]
    parsed = parse_table_query({"offset": ["-3"], "limit": ["999"]}, mode="page")
    page = query_table(
        rows,
        sort=parsed.sort,
        descending=parsed.descending,
        filters=parsed.filters,
        offset=parsed.offset,
        limit=parsed.limit,
    )
    assert page["offset"] == 0
    assert page["limit"] == 500
    assert page["total_rows"] == 10
    assert "descending" in page


def test_profile_and_export_reject_forbidden_query_keys():
    for query in (
        {"version_id": ["v"], "sort": ["n"]},
        {"version_id": ["v"], "dir": ["desc"]},
        {"version_id": ["v"], "offset": ["0"]},
        {"version_id": ["v"], "limit": ["10"]},
    ):
        with pytest.raises(workbench_mod.WorkbenchError) as error:
            parse_table_query(query, mode="profile")
        assert (error.value.status, error.value.code) == (400, "invalid_query")
    for query in (
        {"version_id": ["v"], "offset": ["0"]},
        {"version_id": ["v"], "limit": ["10"]},
    ):
        with pytest.raises(workbench_mod.WorkbenchError) as error:
            parse_table_query(query, mode="export")
        assert (error.value.status, error.value.code) == (400, "invalid_query")
    with pytest.raises(workbench_mod.WorkbenchError) as error:
        parse_table_query({}, mode="profile")
    assert (error.value.status, error.value.code) == (400, "invalid_query")
    with pytest.raises(workbench_mod.WorkbenchError) as error:
        parse_table_query({}, mode="export")
    assert (error.value.status, error.value.code) == (400, "invalid_query")


def test_empty_query_table_response_omits_descending():
    page = query_table([])
    assert page == {
        "columns": [],
        "column_types": [],
        "rows": [],
        "total_rows": 0,
        "offset": 0,
        "limit": 50,
        "sorted_by": None,
        "filters": {},
    }


def test_fixed_fixture_profile_is_deterministic():
    rows = [line.split(",") for line in FIXED_CSV.strip().splitlines()]
    prepared = workbench_mod.materialize_table(rows)
    first = profile_from_prepared(prepared)
    second = profile_from_prepared(prepared)
    assert first == second
    assert first["filtered_rows"] == 4
    assert first["approximate"] is False
    name = _column(first, "name")
    assert name["type"] == "text"
    assert name["missing"] == 0
    assert name["unique"] == 3
    assert name["min"] is None
    n = _column(first, "n")
    assert n["type"] == "integer"
    assert n["unique"] == 3
    assert n["min"] == 1
    assert n["max"] == 3
    assert n["mean"] == pytest.approx(1.75)
    score = _column(first, "score")
    assert score["type"] == "number"
    assert score["missing"] == 1
    assert score["unique"] == 3
    assert score["min"] == pytest.approx(1.5)
    assert score["max"] == pytest.approx(3.5)
    assert score["mean"] == pytest.approx(2.5)


def test_histogram_bins_cap_and_include_min_max_boundaries():
    rows = [["n"], *[[str(i)] for i in range(51)]]
    prepared = workbench_mod.materialize_table(rows)
    profile = profile_from_prepared(prepared)
    histogram = _column(profile, "n")["histogram"]
    assert len(histogram) <= MAX_TABLE_PROFILE_BINS
    assert len(histogram) == MAX_TABLE_PROFILE_BINS
    assert histogram[0]["start"] == 0
    assert histogram[-1]["end"] == 50
    assert sum(item["count"] for item in histogram) == 51
    assert histogram[0]["count"] >= 1
    assert histogram[-1]["count"] >= 1
    singleton = profile_from_prepared(
        workbench_mod.materialize_table([["n"], ["4"], ["4"], ["4"]])
    )
    bins = _column(singleton, "n")["histogram"]
    assert bins == [{"start": 4, "end": 4, "count": 3}]


def test_unique_tracking_bound_is_approximate_not_pretended_exact(monkeypatch):
    monkeypatch.setattr(profile_mod, "MAX_TABLE_PROFILE_UNIQUE_EXACT", 2)
    rows = [["label"], ["a"], ["b"], ["c"], ["a"]]
    profile = profile_from_prepared(workbench_mod.materialize_table(rows))
    column = _column(profile, "label")
    assert profile["approximate"] is True
    assert column["unique"] == 2
    assert column["unique"] != 3


def test_profile_http_and_lru_do_not_write_store_rows(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    artifact = _save_snapshot(
        runner,
        fid,
        workspace / "table.csv",
        FIXED_CSV.encode("utf-8"),
        content_type="text/csv",
    )
    db_path = Path(runner.store.db_path)

    def _tables() -> list[tuple[str]]:
        with sqlite3.connect(str(db_path)) as connection:
            return connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
            ).fetchall()

    before = _tables()
    source = Path(profile_mod.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "sqlite3" not in source

    observed = []
    original = profile_mod.profile_from_prepared

    def wrapped(prepared):
        observed.append(1)
        return original(prepared)

    profile_mod.profile_from_prepared = wrapped  # type: ignore[method-assign]
    try:
        code, first = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/table/profile",
            query={"version_id": [artifact["version_id"]]},
        )
        code2, second = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/table/profile",
            query={"version_id": [artifact["version_id"]]},
        )
    finally:
        profile_mod.profile_from_prepared = original  # type: ignore[method-assign]
    assert code == code2 == 200
    assert first["version_id"] == artifact["version_id"]
    assert first["checksum"] == _checksum(workspace / "table.csv")
    assert first["filtered_rows"] == 4
    assert first["approximate"] is False
    assert len(observed) == 1
    assert second["columns"] == first["columns"]
    assert _tables() == before
    runner.close()


def test_profile_http_rejects_sort_dir_offset_limit(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    artifact = _save_snapshot(
        runner, fid, workspace / "table.csv", FIXED_CSV.encode("utf-8")
    )
    for extra in (
        {"sort": ["n"]},
        {"dir": ["desc"]},
        {"offset": ["0"]},
        {"limit": ["10"]},
    ):
        query = {"version_id": [artifact["version_id"]], **extra}
        code, payload = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/table/profile",
            query=query,
        )
        assert (code, payload["code"]) == (400, "invalid_query")
    runner.close()


def test_export_http_rejects_offset_limit_and_streams_filtered_csv(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    artifact = _save_snapshot(
        runner, fid, workspace / "table.csv", FIXED_CSV.encode("utf-8")
    )
    for extra in ({"offset": ["0"]}, {"limit": ["10"]}):
        query = {"version_id": [artifact["version_id"]], **extra}
        code, payload = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/table/export.csv",
            query=query,
        )
        assert (code, payload["code"]) == (400, "invalid_query")
    reply = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/table/export.csv",
        query={
            "version_id": [artifact["version_id"]],
            "sort": ["n"],
            "dir": ["desc"],
            "q_name": ["a"],
        },
    )
    assert reply[0] == 200
    body = reply[1]
    assert reply[2].startswith("text/csv")
    headers = reply[3]
    assert headers["X-Version-Id"] == artifact["version_id"]
    assert headers["X-Filtered-Rows"] == "2"
    assert headers["X-Approximate"] == "false"
    assert headers["X-Checksum"] == artifact["checksum"]
    lines = body.decode("utf-8").splitlines()
    assert lines[0] == "name,n,score"
    assert lines[1].startswith("a,3,")
    assert lines[2].startswith("a,1,")
    runner.close()


def test_export_chunks_stay_at_or_under_one_mebibyte_and_total_overflow_is_413(
    monkeypatch,
):
    rows = [[f"value-{index}"] for index in range(20)]
    monkeypatch.setattr(profile_mod, "MAX_TABLE_EXPORT_CHUNK_BYTES", 40)
    chunks = export_csv_chunks(["label"], rows)
    assert chunks
    assert all(len(chunk) <= 40 for chunk in chunks)
    joined = b"".join(chunks)
    assert joined.decode("utf-8").splitlines()[0] == "label"
    assert joined.decode("utf-8").count("\n") == 21

    monkeypatch.setattr(profile_mod, "MAX_WORKBENCH_ARTIFACT_BYTES", 30)
    with pytest.raises(workbench_mod.WorkbenchError) as error:
        export_csv_chunks(["label"], rows)
    assert (error.value.status, error.value.code) == (413, "artifact_too_large")


def test_export_spreadsheet_safe_mode_neutralizes_formulas_but_raw_is_faithful():
    columns = ["=header", " @hidden", "+2.5", "-3e-4"]
    rows = [
        ["=SUM(A1:A2)", "\t@cmd", "+2.5", "-3e-4"],
        ["\x00-2+3", "\n+run()", "-1", " +1.25e+6\r"],
    ]

    raw_text = b"".join(export_csv_chunks(columns, rows)).decode("utf-8")
    raw = list(csv.reader(io.StringIO(raw_text, newline="")))
    assert raw == [columns, *rows]

    safe_text = b"".join(
        export_csv_chunks(columns, rows, spreadsheet_safe=True)
    ).decode("utf-8")
    safe = list(csv.reader(io.StringIO(safe_text, newline="")))
    assert safe == [
        ["'=header", "' @hidden", "+2.5", "-3e-4"],
        ["'=SUM(A1:A2)", "'\t@cmd", "+2.5", "-3e-4"],
        ["'\x00-2+3", "'\n+run()", "-1", " +1.25e+6\r"],
    ]


def test_export_http_defaults_raw_and_opt_in_is_spreadsheet_safe(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    source = "=header,signed\n@SUM(A1),-3e-4\n"
    artifact = _save_snapshot(
        runner, fid, workspace / "formula.csv", source.encode("utf-8")
    )
    base_query = {"version_id": [artifact["version_id"]]}

    raw_reply = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/table/export.csv",
        query=base_query,
    )
    assert raw_reply[0] == 200
    raw = list(csv.reader(io.StringIO(raw_reply[1].decode("utf-8"), newline="")))
    assert raw == [["=header", "signed"], ["@SUM(A1)", "-3e-4"]]

    safe_reply = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/table/export.csv",
        query={**base_query, "spreadsheet_safe": ["1"]},
    )
    assert safe_reply[0] == 200
    safe = list(csv.reader(io.StringIO(safe_reply[1].decode("utf-8"), newline="")))
    assert safe == [["'=header", "signed"], ["'@SUM(A1)", "-3e-4"]]
    runner.close()


def test_export_http_total_overflow_is_413(tmp_path, monkeypatch):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    data = "name\n" + "\n".join(f"row-{i}" for i in range(30)) + "\n"
    artifact = _save_snapshot(runner, fid, workspace / "wide.csv", data.encode("utf-8"))
    monkeypatch.setattr(profile_mod, "MAX_WORKBENCH_ARTIFACT_BYTES", 20)
    code, payload = _call(
        handler,
        "GET",
        f"/artifacts/{artifact['artifact_id']}/table/export.csv",
        query={"version_id": [artifact["version_id"]]},
    )
    assert (code, payload["code"]) == (413, "artifact_too_large")
    runner.close()


def test_malicious_parquet_metadata_is_invalid_on_profile_and_export(
    tmp_path, monkeypatch
):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    path = workspace / "evil.parquet"
    artifact = _save_snapshot(runner, fid, path, b"parquet")

    class FakeMetadata:
        num_rows = True
        num_columns = 1
        num_row_groups = 1

        def row_group(self, _index):
            return SimpleNamespace(
                column=lambda _column: SimpleNamespace(total_uncompressed_size=4)
            )

    class FakeParquetFile:
        metadata = FakeMetadata()

        def __init__(self, source):
            source.read()

        def read(self):
            raise AssertionError("decode must not run on malicious metadata")

    pyarrow = ModuleType("pyarrow")
    pyarrow.__path__ = []
    parquet = ModuleType("pyarrow.parquet")
    parquet.ParquetFile = FakeParquetFile
    pyarrow.parquet = parquet
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)

    for route in ("table/profile", "table/export.csv"):
        code, payload = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/{route}",
            query={"version_id": [artifact["version_id"]]},
        )
        assert (code, payload["code"]) == (415, "invalid_parquet")
    runner.close()


def test_missing_parquet_engine_is_unavailable_not_available(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    assert parquet_engine_available() is False

    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    artifact = _save_snapshot(
        runner, fid, workspace / "table.parquet", b"parquet-bytes"
    )

    def unavailable(_path):
        raise workbench_mod.WorkbenchError(
            415,
            "parquet requires pyarrow from the science extra",
            "parquet_unavailable",
        )

    monkeypatch.setattr(workbench_mod, "read_parquet_rows", unavailable)
    for route in ("table", "table/profile", "table/export.csv"):
        query = {"version_id": [artifact["version_id"]]} if route != "table" else {}
        code, payload = _call(
            handler,
            "GET",
            f"/artifacts/{artifact['artifact_id']}/{route}",
            query=query,
        )
        assert (code, payload["code"]) == (415, "parquet_unavailable")
    runner.close()


def test_provided_version_id_never_reads_latest(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path)
    workspace = runner.workspace_for_branch(fid, fid)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "table.csv"
    path.write_text("name,n\nold,1\n", encoding="utf-8")
    first = runner.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum=_checksum(path),
        frame_id=fid,
        project_id="default",
    )
    _freeze_version(runner, first, path)
    edited = runner.edit_artifact(first["artifact_id"], "name,n\nNEW,999\n")
    assert edited["version_id"] != first["version_id"]

    seen: list[str | None] = []
    original = runner.workbench_artifacts._version_snapshot

    def wrapped(artifact, version_id=None):
        seen.append(version_id)
        return original(artifact, version_id)

    runner.workbench_artifacts._version_snapshot = wrapped
    code, page = _call(
        handler,
        "GET",
        f"/artifacts/{first['artifact_id']}/table",
        query={"version_id": [first["version_id"]]},
    )
    assert code == 200
    assert page["version_id"] == first["version_id"]
    assert page["rows"] == [["old", "1"]]
    assert seen == [first["version_id"]]
    assert edited["version_id"] not in seen

    code, profile = _call(
        handler,
        "GET",
        f"/artifacts/{first['artifact_id']}/table/profile",
        query={"version_id": [first["version_id"]]},
    )
    assert code == 200
    assert profile["version_id"] == first["version_id"]
    latest = runner.store.get_artifact(first["artifact_id"])["latest_version_id"]
    assert latest == edited["version_id"]
    assert profile["version_id"] != latest
    runner.close()


def test_flag_off_forbids_profile_and_export(tmp_path):
    _cfg, runner, handler, fid = _setup(tmp_path, workbench=False)
    for route in ("table", "table/profile", "table/export.csv"):
        code, payload = _call(handler, "GET", f"/artifacts/missing/{route}")
        assert (code, payload["code"]) == (403, "workbench_disabled")
    runner.close()


def test_resource_manifest_and_default_flag_block_ga(tmp_path):
    assert Config().roadmap_features.stage9_artifact_workbench is False
    assert table_workbench_ga_blocked() is True
    assert resource_manifest_ready(tmp_path / "missing.json") is False
    incomplete = tmp_path / "partial.json"
    incomplete.write_text("{}", encoding="utf-8")
    assert resource_manifest_ready(incomplete) is False
    short = {
        "machine": "ci",
        "os": "linux",
        "dependency_versions": {},
        "fixture_checksum": "abc",
        "warmup": "none",
        "measurement_count": 29,
        "rss_method": "peak",
        "wall_time_method": "monotonic",
        "thresholds": {},
        "approver": "reviewer",
    }
    short_path = tmp_path / "short.json"
    short_path.write_text(json.dumps(short), encoding="utf-8")
    assert resource_manifest_ready(short_path) is False
    ready = dict(short)
    ready["measurement_count"] = 30
    ready_path = tmp_path / "ready.json"
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    assert resource_manifest_ready(ready_path) is True
    assert RoadmapFeatureFlags().stage9_artifact_workbench is False
