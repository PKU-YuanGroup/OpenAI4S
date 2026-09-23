"""Dataset discovery must not invent version, license, or byte evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import urllib.parse
from types import SimpleNamespace

import pytest

from openai4s.host import zenodo
from openai4s.host.science import ScienceConnectorError, ScienceConnectorService
from openai4s.tools.catalog import SessionToolCatalog
from openai4s.tools.science import ScienceSearchTool
from tests.test_science_connectors import RESPONSES

pytestmark = pytest.mark.stubbed_backend


def payload():
    return copy.deepcopy(RESPONSES["zenodo.org"])


def run(document=None, **kwargs):
    calls = []
    data = payload() if document is None else document
    raw = json.dumps(data).encode()

    def fetch(url, fmt, timeout, max_chars):
        calls.append(url)
        return {
            "content": raw.decode(),
            "raw_bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }

    result = ScienceConnectorService(fetch).search("zenodo", "hyperspectral", **kwargs)
    return result, calls


def test_search_preserves_record_concept_and_source_declared_files():
    result, calls = run()
    record = result["results"][0]
    attrs = record["attributes"]
    assert record["url"] == "https://zenodo.org/records/6275421"
    assert attrs["record_doi"] == "10.5281/zenodo.6275421"
    assert attrs["concept_doi"] == "10.5281/zenodo.6275420"
    assert attrs["declared_license"] == "cc-by-4.0"
    assert attrs["file_count"] == 14
    assert attrs["declared_total_bytes"] == 2704484528
    assert attrs["file_verification"] == "not_downloaded"
    assert "sha256" not in attrs["files"][0]
    assert len(calls) == 1
    request = urllib.parse.urlsplit(calls[0])
    assert request.hostname == "zenodo.org"
    assert request.path == "/api/records"
    assert urllib.parse.parse_qs(request.query) == {
        "q": ["hyperspectral"],
        "type": ["dataset"],
        "size": ["10"],
        "page": ["1"],
    }
    assert result["provenance"]["responses"][0]["hashed"] == "response_bytes"


def test_canonical_paroutes_fixture_binds_the_maintainer_selected_file():
    result, _ = run()
    record = result["results"][0]
    attrs = record["attributes"]
    assert record["id"] == "6275421"
    assert attrs["version"] == "1.0.0"
    assert attrs["declared_license"] == "cc-by-4.0"
    selected = next(item for item in attrs["files"] if item["key"] == "n1-targets.txt")
    assert selected["declared_size_bytes"] == 465689
    assert selected["declared_checksum"] == "md5:5adae99357cdad829073b197c7813152"


def test_unknowns_are_not_zero_or_open_license():
    document = payload()
    row = document["hits"]["hits"][0]
    row["metadata"].pop("license")
    row["metadata"].pop("version")
    row["files"][0].pop("size")
    row["files"][0].pop("checksum")
    attrs = run(document)[0]["results"][0]["attributes"]
    assert attrs["declared_license"] is None
    assert attrs["license_status"] == "unknown"
    assert attrs["version"] is None
    assert attrs["declared_total_bytes"] is None
    assert attrs["files"][0]["declared_checksum"] is None


def test_unlisted_files_differ_from_empty_inventory():
    document = payload()
    row = document["hits"]["hits"][0]
    row.pop("files")
    unknown = run(document)[0]["results"][0]["attributes"]
    assert unknown["files"] is None and unknown["file_count"] is None
    row["files"] = []
    empty = run(document)[0]["results"][0]["attributes"]
    assert empty["files"] == [] and empty["file_count"] == 0


@pytest.mark.parametrize("access_right", ["restricted", "embargoed", None])
def test_hidden_file_list_is_unknown_not_an_empty_inventory(access_right):
    # Zenodo answers `files: []` for restricted and embargoed records whose
    # files exist; reporting 0 files / 0 bytes would invent an empty dataset.
    document = payload()
    row = document["hits"]["hits"][0]
    if access_right is None:
        row["metadata"].pop("access_right")
    else:
        row["metadata"]["access_right"] = access_right
    row["files"] = []
    attrs = run(document)[0]["results"][0]["attributes"]
    assert attrs["access_right"] == access_right
    assert attrs["files"] is None
    assert attrs["file_count"] is None
    assert attrs["declared_total_bytes"] is None


def _cursor(page, limit, query="hyperspectral"):
    binding = hashlib.sha256(f"{query}\0{limit}".encode()).hexdigest()[:24]
    return f"zenodo:{page}:{binding}"


def test_paging_stops_at_the_upstream_result_window():
    # Live Zenodo still sends `links.next` on page 400 at size 25, then answers
    # HTTP 400 for page 401. A cursor to that page would always fail.
    document = payload()
    document["links"]["next"] = "https://zenodo.org/api/records?page=next"
    before_edge, _ = run(document, limit=25, cursor=_cursor(399, 25))
    assert before_edge["next_cursor"] == _cursor(400, 25)
    last, calls = run(document, limit=25, cursor=_cursor(400, 25))
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)["page"] == [
        "400"
    ]
    assert last["count"] == 1
    assert last["next_cursor"] is None
    # A page starting inside the window is valid even when it overhangs it.
    overhang, _ = run(document, limit=3, cursor=_cursor(3334, 3))
    assert overhang["next_cursor"] is None
    service = ScienceConnectorService(
        lambda *_: pytest.fail("must reject before fetch")
    )
    with pytest.raises(ScienceConnectorError, match="10,000-result"):
        service.search("zenodo", "hyperspectral", limit=25, cursor=_cursor(401, 25))


def test_paging_rebuilds_url_and_retains_query_binding():
    document = payload()
    document["links"]["next"] = "https://untrusted.invalid/collect"
    first, _ = run(document, limit=2)
    cursor = first["next_cursor"]
    second, calls = run(document, limit=2, cursor=cursor)
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)["page"] == ["2"]
    assert "untrusted.invalid" not in calls[0]
    assert second["next_cursor"] != cursor
    with pytest.raises(ScienceConnectorError, match="cursor"):
        run(document, limit=3, cursor=cursor)
    service = ScienceConnectorService(
        lambda *_: pytest.fail("must reject before fetch")
    )
    with pytest.raises(ScienceConnectorError, match="cursor"):
        service.search("zenodo", "different", limit=2, cursor=cursor)


@pytest.mark.parametrize(
    "document", [None, [], {}, {"hits": []}, {"hits": {"hits": None}}]
)
def test_schema_drift_is_not_an_empty_success(document):
    service = ScienceConnectorService(lambda *_: json.dumps(document))
    with pytest.raises(ScienceConnectorError, match="schema|results"):
        service.search("zenodo", "hyperspectral")


def test_valid_empty_result_terminates_even_with_stale_next_hint():
    result, _ = run({"hits": {"hits": []}, "links": {"next": "stale"}})
    assert result["count"] == 0
    assert result["next_cursor"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("size", -1),
        ("size", "3"),
        ("checksum", "md5:unknown"),
        ("key", ""),
    ],
)
def test_invalid_file_metadata_refuses_the_page(field, value):
    document = payload()
    document["hits"]["hits"][0]["files"][0][field] = value
    with pytest.raises(ScienceConnectorError):
        run(document)


def test_duplicate_file_and_record_identities_refuse():
    document = payload()
    files = document["hits"]["hits"][0]["files"]
    files.append(copy.deepcopy(files[0]))
    with pytest.raises(ScienceConnectorError, match="duplicated"):
        run(document)
    document = payload()
    rows = document["hits"]["hits"]
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ScienceConnectorError, match="duplicate record"):
        run(document)


def test_published_non_dataset_is_refused():
    document = payload()
    document["hits"]["hits"][0]["metadata"]["resource_type"]["type"] = "publication"
    with pytest.raises(ScienceConnectorError, match="non-dataset"):
        run(document)


def test_public_page_size_checked_before_network():
    service = ScienceConnectorService(
        lambda *_: pytest.fail("must reject before fetch")
    )
    with pytest.raises(ScienceConnectorError, match="1 to 25"):
        service.search("zenodo", "hyperspectral", limit=26)


def test_result_bound_refuses_without_truncating_a_manifest(monkeypatch):
    monkeypatch.setattr(zenodo, "MAX_RESULT_BYTES", 32)
    with pytest.raises(ScienceConnectorError, match="too large"):
        run()


def test_file_inventory_bound_refuses_without_truncation(monkeypatch):
    monkeypatch.setattr(zenodo, "MAX_RECORD_FILES", 0)
    with pytest.raises(ScienceConnectorError, match="inventory"):
        run()


def test_tool_discovery_and_dynamic_write_posture_are_preserved():
    for request in ("Search Zenodo for hyperspectral datasets", "公开数据集检索"):
        specs = SessionToolCatalog().specs_for([{"role": "user", "content": request}])
        assert {"science_search", "science_list_dbs"} <= {s.name for s in specs}
    tool = ScienceSearchTool()
    assert (
        tool.validation_error({"database": "zenodo", "query": "hyperspectral"}) is None
    )
    for enabled in (False, True):
        runtime = SimpleNamespace(stage10_enabled=lambda: enabled)
        assert tool.read_only_for(runtime) is (not enabled)
        assert tool.writes_files_for(runtime) is enabled


def test_tool_schema_failure_is_soft_and_does_not_record_artifact(monkeypatch):
    from openai4s import webtools

    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_args, **_kwargs: {"content": "{}"}
    )
    runtime = SimpleNamespace(
        stage10_enabled=lambda: True,
        record_science_artifact=lambda _: pytest.fail(
            "failed discovery must not save an artifact"
        ),
    )
    result = ScienceSearchTool().execute(
        runtime, {"database": "zenodo", "query": "hyperspectral"}
    )
    assert set(result) == {"error"}


def test_metadata_uses_existing_stage10_capture_instead_of_downloading(monkeypatch):
    from openai4s import webtools

    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_args, **_kwargs: {"content": json.dumps(payload())},
    )
    monkeypatch.setattr(
        webtools,
        "web_download",
        lambda *_args, **_kwargs: pytest.fail("discovery must not download files"),
    )
    records = []

    def record(result):
        records.append(result)
        return {"path": "dataset-metadata.json"}

    runtime = SimpleNamespace(
        stage10_enabled=lambda: True, record_science_artifact=record
    )
    result = ScienceSearchTool().execute(
        runtime, {"database": "zenodo", "query": "hyperspectral"}
    )
    assert len(records) == 1
    assert (
        records[0]["results"][0]["attributes"]["file_verification"] == "not_downloaded"
    )
    assert result["_openai4s_artifact_capture"] == {"path": "dataset-metadata.json"}


def test_dispatcher_denial_precedes_dataset_fetch(tmp_path, monkeypatch):
    from openai4s import webtools
    from openai4s.config import Config
    from openai4s.host_dispatch import build_dispatcher
    from openai4s.tools.registry import execute_tool_call

    dispatcher = build_dispatcher(
        Config(data_dir=tmp_path / "data"), workspace=tmp_path
    )
    dispatcher.frame_id = dispatcher.store.new_frame(project_id="dataset-policy")
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="science_search",
        pattern="zenodo",
        decision="deny",
    )
    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_args, **_kwargs: pytest.fail("denied dataset query must not fetch"),
    )
    result, ok = execute_tool_call(
        dispatcher,
        {
            "name": "science_search",
            "arguments": {"database": "zenodo", "query": "hyperspectral"},
        },
    )
    assert ok is False
    assert "Permission denied" in result


def test_existing_egress_denial_is_preserved(monkeypatch):
    from openai4s import egress, webtools

    def denied(*_args, **_kwargs):
        raise egress.EgressBlocked("Zenodo denied by test policy")

    monkeypatch.setattr(webtools, "web_fetch", denied)
    result = ScienceSearchTool().execute(
        None, {"database": "zenodo", "query": "hyperspectral"}
    )
    assert set(result) == {"error"}
    assert "denied" in result["error"]


def test_file_key_whitespace_is_preserved_as_remote_identity():
    document = payload()
    document["hits"]["hits"][0]["files"][0]["key"] = " spectra α.csv "
    record = run(document)[0]["results"][0]
    assert record["attributes"]["files"][0]["key"] == " spectra α.csv "
    assert (
        record["attributes"]["files"][0]["download_url"]
        == "https://zenodo.org/api/records/6275421/files/%20spectra%20%CE%B1.csv%20/content"
    )


def test_native_observation_keeps_cursor_receipt_and_complete_file_list(monkeypatch):
    from openai4s import webtools
    from openai4s.tools.registry import finalize_tool_batch, format_tool_result

    document = payload()
    document["links"]["next"] = "https://zenodo.org/api/records?page=2"
    document["hits"]["hits"][0]["files"] = [
        {"key": f"spectrum-{i:03}.csv", "size": i, "checksum": None} for i in range(200)
    ]
    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_args, **_kwargs: {"content": json.dumps(document)},
    )
    tool = ScienceSearchTool()
    result = tool.execute(None, {"database": "zenodo", "query": "hyperspectral"})
    assert "error" not in result
    observation = finalize_tool_batch([format_tool_result(tool, result)], 1, [])
    assert result["next_cursor"] in observation
    assert result["provenance"]["response_sha256"] in observation
    assert "metadata_response_only_not_dataset_file_bytes" in observation
    assert "spectrum-199.csv" in observation
    assert '"file_count": 200' in observation
    assert "truncated]" not in observation


def test_pretty_native_budget_refuses_before_persisting_partial_evidence(monkeypatch):
    from openai4s import webtools

    document = payload()
    document["hits"]["hits"][0]["files"] = [
        {"key": f"spectrum-{i:03}.csv", "size": i, "checksum": None} for i in range(350)
    ]
    # This fits the service's JSON bound but not the actual native formatter.
    assert run(document)[0]["count"] == 1
    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_args, **_kwargs: {"content": json.dumps(document)},
    )
    runtime = SimpleNamespace(
        stage10_enabled=lambda: True,
        record_science_artifact=lambda _: pytest.fail("must reject before capture"),
    )
    result = ScienceSearchTool().execute(
        runtime, {"database": "zenodo", "query": "hyperspectral"}
    )
    assert set(result) == {"error"}
    assert "observation budget" in result["error"]
