"""Stage 10 ClinVar / PubMed / ClinicalTrials connectors."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.models import ModelReply, RunState
from openai4s.agent.runtime import LocalActionExecutor
from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.host import stage10_science as stage10_mod
from openai4s.host.science import ScienceConnectorError, ScienceConnectorService
from openai4s.host.stage10_science import (
    official_stage10_enabled,
    write_search_artifact,
)
from openai4s.host_dispatch import build_dispatcher
from openai4s.server import gateway as gateway_mod
from openai4s.store import get_store
from openai4s.tools.registry import execute_tool_call
from openai4s.tools.science import ScienceListDatabasesTool, ScienceSearchTool

CLINVAR_SEARCH = {
    "esearchresult": {"count": "1", "idlist": ["424712"]},
}
CLINVAR_SUMMARY = {
    "result": {
        "uids": ["424712"],
        "424712": {
            "uid": "424712",
            "accession": "VCV000012345",
            "title": "NM_000059.4(BRCA2):c.5946del",
            "clinical_significance": "Pathogenic",
            "review_status": "reviewed by expert panel",
            "gene_sort": "BRCA2",
        },
    }
}
PUBMED_SEARCH = {"esearchresult": {"count": "1", "idlist": ["20301425"]}}
PUBMED_SUMMARY = {
    "result": {
        "uids": ["20301425"],
        "20301425": {
            "uid": "20301425",
            "title": "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
            "fulljournalname": "GeneReviews",
            "pubdate": "1998 Sep 4",
            "elocationid": "doi: 10.0000/example",
        },
    }
}
TRIALS = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00001379",
                    "briefTitle": "A study of BRCA-related cancer",
                    "organization": {"fullName": "NCI"},
                },
                "statusModule": {"overallStatus": "COMPLETED"},
            }
        }
    ],
    "nextPageToken": "",
}


@pytest.fixture(autouse=True)
def _clear_stage10_cache():
    with stage10_mod._CACHE_LOCK:
        stage10_mod._CACHE.clear()
    yield
    with stage10_mod._CACHE_LOCK:
        stage10_mod._CACHE.clear()


def _fetch(url, fmt, timeout, max_chars):
    parsed = urlparse(url)
    if "esearch" in url and "clinvar" in url:
        body = CLINVAR_SEARCH
    elif "esummary" in url and "clinvar" in url:
        body = CLINVAR_SUMMARY
    elif "esearch" in url and "pubmed" in url:
        body = PUBMED_SEARCH
    elif "esummary" in url and "pubmed" in url:
        body = PUBMED_SUMMARY
    elif parsed.hostname == "clinicaltrials.gov":
        body = TRIALS
    else:
        raise AssertionError(url)
    return json.dumps(body)


def test_flag_keeps_new_sources_out_of_the_default_catalog():
    off = ScienceConnectorService()
    ids = {item["id"] for item in off.list_databases()["databases"]}
    assert "clinvar" not in ids
    on = ScienceConnectorService(stage10=True)
    ids = {item["id"] for item in on.list_databases()["databases"]}
    assert {"clinvar", "pubmed", "clinicaltrials"} <= ids
    assert official_stage10_enabled(Config()) is False


def test_clinvar_search_records_accession_url_time_and_file_receipt(tmp_path):
    service = ScienceConnectorService(fetch=_fetch, stage10=True)
    result = service.search("clinvar", "VCV000012345", limit=1)
    assert result["count"] == 1
    row = result["results"][0]
    assert row["id"] == "VCV000012345"
    clinvar_url = urlparse(row["url"])
    assert clinvar_url.hostname == "www.ncbi.nlm.nih.gov"
    assert clinvar_url.path.startswith("/clinvar/")
    assert result["provenance"]["retrieved_at"]
    assert result["request_url"]
    workspace = tmp_path / "ws"
    receipt = write_search_artifact(workspace, result)
    path = workspace / receipt["filename"]
    assert path.is_file()
    assert receipt["checksum"] == hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["result"] == result
    source = receipt["source"]
    assert source["query"] == "VCV000012345"
    assert urlparse(source["endpoint"]).hostname == "eutils.ncbi.nlm.nih.gov"
    assert source["retrieved_at"]
    assert "VCV000012345" in source["accessions"]
    assert not list(workspace.glob(".openai4s-*"))


def test_search_artifact_rejects_a_hardlink_swap_of_its_staging_name(
    tmp_path, monkeypatch
):
    result = ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinvar", "VCV000012345", limit=1
    )
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(b"EXTERNAL_BYTES")
    original_fsync = os.fsync
    swapped = False

    def swap_staging_name(descriptor):
        nonlocal swapped
        original_fsync(descriptor)
        if swapped:
            return
        candidates = list(workspace.glob(".openai4s-*.science.part"))
        if not candidates:
            return
        candidates[0].unlink()
        os.link(outside, candidates[0])
        swapped = True

    monkeypatch.setattr(stage10_mod.os, "fsync", swap_staging_name)
    with pytest.raises(ValueError, match="staging file changed before publication"):
        write_search_artifact(workspace, result)

    assert swapped
    assert outside.read_bytes() == b"EXTERNAL_BYTES"
    assert outside.stat().st_nlink == 1
    assert not list(workspace.glob("science-*.json"))
    assert not list(workspace.glob(".openai4s-*"))


def test_search_artifact_rejects_a_hardlink_swap_at_publish(tmp_path, monkeypatch):
    from openai4s.host.files import SecureWorkspaceParent

    result = ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinvar", "VCV000012345", limit=1
    )
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(b"EXTERNAL_BYTES")
    original_publish = SecureWorkspaceParent.publish
    swapped = False

    def swap_then_publish(parent, staged_name):
        nonlocal swapped
        os.unlink(staged_name, dir_fd=parent.fd)
        os.link(outside, staged_name, dst_dir_fd=parent.fd)
        swapped = True
        original_publish(parent, staged_name)

    monkeypatch.setattr(SecureWorkspaceParent, "publish", swap_then_publish)
    with pytest.raises(ValueError, match="multiply-linked regular files"):
        write_search_artifact(workspace, result)

    assert swapped
    assert outside.read_bytes() == b"EXTERNAL_BYTES"
    assert outside.stat().st_nlink == 1
    assert not list(workspace.glob("science-*.json"))
    assert not list(workspace.glob(".openai4s-*"))


def test_search_artifact_keeps_using_pinned_parent_after_workspace_swap(
    tmp_path, monkeypatch
):
    result = ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "pubmed", "BRCA2", limit=1
    )
    workspace = tmp_path / "ws"
    moved_workspace = tmp_path / "moved-ws"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_fsync = os.fsync
    swapped = False

    def swap_workspace_path(descriptor):
        nonlocal swapped
        original_fsync(descriptor)
        if swapped:
            return
        candidates = list(workspace.glob(".openai4s-*.science.part"))
        if not candidates:
            return
        workspace.rename(moved_workspace)
        workspace.symlink_to(outside, target_is_directory=True)
        swapped = True

    monkeypatch.setattr(stage10_mod.os, "fsync", swap_workspace_path)
    receipt = write_search_artifact(workspace, result)

    published = moved_workspace / receipt["filename"]
    assert swapped
    assert published.is_file()
    assert receipt["checksum"] == hashlib.sha256(published.read_bytes()).hexdigest()
    assert not list(outside.iterdir())
    assert not list(moved_workspace.glob(".openai4s-*"))


def test_empty_429_and_schema_drift_are_honest():
    def empty(url, fmt, timeout, max_chars):
        if "esearch" in url:
            return json.dumps({"esearchresult": {"count": "0", "idlist": []}})
        raise AssertionError(url)

    empty_result = ScienceConnectorService(fetch=empty, stage10=True).search(
        "pubmed", "no-such-term-xyz", limit=1
    )
    assert empty_result["count"] == 0
    assert empty_result["results"] == []

    def drifted(url, fmt, timeout, max_chars):
        return json.dumps({"unexpected": True})

    with pytest.raises(ScienceConnectorError, match="unexpected"):
        ScienceConnectorService(fetch=drifted, stage10=True).search(
            "clinvar", "BRCA1", limit=1
        )

    def limited(url, fmt, timeout, max_chars):
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    with pytest.raises(ScienceConnectorError, match="429"):
        ScienceConnectorService(fetch=limited, stage10=True).search(
            "clinicaltrials", "melanoma", limit=1
        )


def test_clinicaltrials_and_pubmed_normalize(tmp_path):
    service = ScienceConnectorService(fetch=_fetch, stage10=True)
    papers = service.search("pubmed", "BRCA2", limit=1)
    assert papers["results"][0]["id"] == "20301425"
    assert papers["next_cursor"] is None
    trials = service.search("clinicaltrials", "BRCA", limit=1)
    assert trials["results"][0]["id"] == "NCT00001379"
    trial_url = urlparse(trials["results"][0]["url"])
    assert trial_url.hostname == "clinicaltrials.gov"
    assert trial_url.path == "/study/NCT00001379"


def test_tool_catalog_hides_stage10_until_the_flag_is_on():
    result = ScienceListDatabasesTool().execute(None, {"domain": "all"})
    assert "clinvar" not in {item["id"] for item in result["databases"]}
    runtime = SimpleNamespace(
        cfg=Config(
            roadmap_features=RoadmapFeatureFlags(stage10_scientific_connectors=True)
        )
    )
    enabled = ScienceListDatabasesTool().execute(runtime, {"domain": "all"})
    assert "clinvar" in {item["id"] for item in enabled["databases"]}


def test_science_search_runtime_write_metadata_is_flag_scoped(tmp_path):
    """The provider schema stays stable while execution metadata is dynamic."""

    def metadata(enabled):
        cfg = Config(
            data_dir=tmp_path / ("on" if enabled else "off"),
            roadmap_features=RoadmapFeatureFlags(stage10_scientific_connectors=enabled),
        )
        dispatcher = build_dispatcher(cfg, workspace=tmp_path / "workspace")
        tool = dispatcher.tool_catalog().get("science_search")
        assert tool is not None
        assert tool.resource_keys({"database": "clinvar"}) == (
            "network:science/clinvar",
        )
        assert tool.schema() == ScienceSearchTool().schema()
        return dispatcher.control_tool_execution_metadata("science_search"), (
            dispatcher.control_tool_policy("science_search", {"database": "clinvar"})
        )

    off_metadata, off_policy = metadata(False)
    assert off_metadata == {
        "writes_files": False,
        "read_only": True,
        "side_effect_class": "read_only",
    }
    assert off_policy == ("read_only", ["network:science/clinvar"])

    on_metadata, on_policy = metadata(True)
    assert on_metadata == {
        "writes_files": True,
        "read_only": False,
        "side_effect_class": "workspace_write",
    }
    assert on_policy == ("workspace_write", ["network:science/clinvar"])


def test_flag_off_science_search_does_not_enter_busy_capture(tmp_path):
    """Stage 10 off preserves the origin/next read-only scheduling behavior."""

    def runner_for(enabled, suffix):
        cfg = Config(
            data_dir=tmp_path / suffix,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            roadmap_features=RoadmapFeatureFlags(
                stage1_trusted_delivery=True,
                stage10_scientific_connectors=enabled,
            ),
        )
        runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
        frame_id = runner.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        state = runner._state(frame_id, "default")
        runner._ensure_runtime(state)
        return runner, state

    off_runner, off_state = runner_for(False, "off-runner")
    off_calls = []
    try:
        with off_state.trusted_capture.background():
            result = off_runner._invoke_control_with_artifacts(
                off_state,
                {"id": "off", "name": "science_search", "arguments": {}},
                lambda _event: None,
                lambda: off_calls.append("invoked") or ("legacy search", True),
            )
        assert result == ("legacy search", True)
        assert off_calls == ["invoked"]
    finally:
        off_runner.close()

    on_runner, on_state = runner_for(True, "on-runner")
    on_calls = []
    try:
        with on_state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as refused:
                on_runner._invoke_control_with_artifacts(
                    on_state,
                    {"id": "on", "name": "science_search", "arguments": {}},
                    lambda _event: None,
                    lambda: on_calls.append("invoked") or ("new search", True),
                )
        assert refused.value.error_code == "trusted_capture_busy"
        assert on_calls == []
    finally:
        on_runner.close()


def test_stage10_cache_hit_replays_the_response_provenance():
    first = ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinvar", "VCV000012345", limit=1
    )
    expected_responses = json.loads(json.dumps(first["provenance"]["responses"]))
    expected_digest = first["provenance"]["response_sha256"]
    first["results"][0]["title"] = "mutated-by-caller"
    first["provenance"]["responses"][0]["url"] = "mutated-by-caller"

    def no_second_fetch(*_args):
        raise AssertionError("cache hit unexpectedly fetched upstream")

    second = ScienceConnectorService(fetch=no_second_fetch, stage10=True).search(
        "clinvar", "VCV000012345", limit=1
    )
    assert second["results"][0]["title"] != "mutated-by-caller"
    assert second["provenance"]["responses"] == expected_responses
    assert second["provenance"]["response_sha256"] == expected_digest
    assert len(second["provenance"]["responses"]) == 2


def test_stage10_cache_expires_is_lru_bounded_and_returns_snapshots(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(stage10_mod, "_CACHE_CLOCK", lambda: clock[0])

    first = ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinicaltrials", "original", limit=1
    )
    with stage10_mod._CACHE_LOCK:
        original_key = next(iter(stage10_mod._CACHE))
    first["results"][0]["title"] = "mutated-by-caller"

    def no_second_fetch(*_args):
        raise AssertionError("cache hit unexpectedly fetched upstream")

    replayed = ScienceConnectorService(fetch=no_second_fetch, stage10=True).search(
        "clinicaltrials", "original", limit=1
    )
    assert (
        replayed["results"][0]["title"]
        == TRIALS["studies"][0]["protocolSection"]["identificationModule"]["briefTitle"]
    )
    replayed["results"][0]["title"] = "mutated-hit"
    third = ScienceConnectorService(fetch=no_second_fetch, stage10=True).search(
        "clinicaltrials", "original", limit=1
    )
    assert third["results"][0]["title"] != "mutated-hit"

    for index in range(stage10_mod._CACHE_MAX_ENTRIES + 3):
        ScienceConnectorService(fetch=_fetch, stage10=True).search(
            "clinicaltrials", f"query-{index}", limit=1
        )
    with stage10_mod._CACHE_LOCK:
        assert len(stage10_mod._CACHE) == stage10_mod._CACHE_MAX_ENTRIES
        assert original_key not in stage10_mod._CACHE
        assert stage10_mod._cache_size_bytes_locked() <= stage10_mod._CACHE_MAX_BYTES

    clock[0] += stage10_mod._CACHE_TTL_S
    ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinicaltrials", "after-expiry", limit=1
    )
    with stage10_mod._CACHE_LOCK:
        assert len(stage10_mod._CACHE) == 1


def test_stage10_cache_evicts_32_near_limit_results_by_total_bytes(monkeypatch):
    monkeypatch.setattr(stage10_mod, "_CACHE_CLOCK", lambda: 100.0)
    padding = "x" * (stage10_mod._CACHE_MAX_ENTRY_BYTES - 2_000)

    for index in range(stage10_mod._CACHE_MAX_ENTRIES):
        stage10_mod._store_cached_search(
            f"near-limit:{index}",
            ([{"id": str(index), "padding": padding}], "", "https://example.test"),
            (),
        )

    with stage10_mod._CACHE_LOCK:
        encoded_sizes = [
            len(encoded) for _stored_at, encoded in stage10_mod._CACHE.values()
        ]
        assert 0 < len(stage10_mod._CACHE) < stage10_mod._CACHE_MAX_ENTRIES
        assert "near-limit:31" in stage10_mod._CACHE
        assert min(encoded_sizes) > stage10_mod._CACHE_MAX_ENTRY_BYTES - 2_000
        assert stage10_mod._cache_size_bytes_locked() <= stage10_mod._CACHE_MAX_BYTES


def test_stage10_invalid_schema_is_never_cached():
    calls = []

    def drifted(url, fmt, timeout, max_chars):
        calls.append((url, fmt, timeout, max_chars))
        return json.dumps({"studies": "not-a-list"})

    for _attempt in range(2):
        with pytest.raises(ScienceConnectorError, match="unexpected result schema"):
            ScienceConnectorService(fetch=drifted, stage10=True).search(
                "clinicaltrials", "invalid-schema", limit=1
            )

    assert len(calls) == 2
    with stage10_mod._CACHE_LOCK:
        assert not stage10_mod._CACHE


def test_stage10_cache_concurrent_unique_queries_stay_bounded(monkeypatch):
    monkeypatch.setattr(stage10_mod, "_CACHE_CLOCK", lambda: 100.0)

    count = stage10_mod._CACHE_MAX_ENTRIES * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda index: ScienceConnectorService(
                    fetch=_fetch, stage10=True
                ).search("clinicaltrials", f"concurrent-{index}", limit=1),
                range(count),
            )
        )
    assert len(results) == count
    assert all(result["count"] == 1 for result in results)
    with stage10_mod._CACHE_LOCK:
        assert len(stage10_mod._CACHE) == stage10_mod._CACHE_MAX_ENTRIES
        assert stage10_mod._cache_size_bytes_locked() <= stage10_mod._CACHE_MAX_BYTES


def test_stage10_cache_hit_decodes_outside_the_global_lock(monkeypatch):
    ScienceConnectorService(fetch=_fetch, stage10=True).search(
        "clinicaltrials", "lock-free-hit", limit=1
    )
    entered_decode = threading.Event()
    finish_decode = threading.Event()
    real_decode = stage10_mod._decode_cached_search

    def blocking_decode(encoded):
        entered_decode.set()
        assert finish_decode.wait(timeout=5)
        return real_decode(encoded)

    monkeypatch.setattr(stage10_mod, "_decode_cached_search", blocking_decode)

    def no_second_fetch(*_args):
        raise AssertionError("cache hit unexpectedly fetched upstream")

    acquired = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            ScienceConnectorService(fetch=no_second_fetch, stage10=True).search,
            "clinicaltrials",
            "lock-free-hit",
            limit=1,
        )
        try:
            assert entered_decode.wait(timeout=5)
            acquired = stage10_mod._CACHE_LOCK.acquire(timeout=1)
        finally:
            if acquired:
                stage10_mod._CACHE_LOCK.release()
            finish_decode.set()
        assert acquired, "cache JSON decoding held the process-wide cache lock"
        assert result.result(timeout=5)["count"] == 1


class _Hub:
    def emitter(self, _root_frame_id):
        return lambda _event: None

    def broadcast(self, _root_frame_id, _event):
        return None

    def has_subscriber(self, _root_frame_id):
        return False

    def drop_frame(self, _root_frame_id):
        return None


def _stage10_result() -> dict:
    return {
        "database": "clinvar",
        "source": "ClinVar",
        "query": "VCV000012345",
        "count": 1,
        "results": [
            {
                "id": "VCV000012345",
                "title": "BRCA2 variant",
                "url": "https://www.ncbi.nlm.nih.gov/clinvar/variation/424712/",
                "record_type": "variant",
                "fields": {},
            }
        ],
        "next_cursor": None,
        "request_url": "https://eutils.ncbi.nlm.nih.gov/example",
        "provenance": {
            "retrieved_at": 123456789,
            "response_sha256": "ab" * 32,
            "responses": [
                {
                    "url": "https://eutils.ncbi.nlm.nih.gov/example",
                    "sha256": "ab" * 32,
                    "bytes": 42,
                    "hashed": "response_bytes",
                }
            ],
        },
    }


def test_real_native_stage10_tool_is_captured_as_a_trusted_artifact(
    tmp_path, monkeypatch
):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(
            stage1_trusted_delivery=True,
            stage10_scientific_connectors=True,
        ),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    result = _stage10_result()

    def fake_search(_self, *_args, **_kwargs):
        return result

    monkeypatch.setattr(ScienceConnectorService, "search", fake_search)
    events: list[dict] = []
    actual = runner._invoke_control_with_artifacts(
        state,
        {
            "id": "call-stage10",
            "name": "science_search",
            "arguments": {
                "database": "clinvar",
                "query": "VCV000012345",
                "limit": 1,
            },
        },
        events.append,
        lambda: execute_tool_call(
            dispatcher,
            {
                "name": "science_search",
                "arguments": {
                    "database": "clinvar",
                    "query": "VCV000012345",
                    "limit": 1,
                },
            },
        ),
    )
    observation, ok = actual
    assert ok is True
    assert "_openai4s_artifact_capture" not in observation
    files = list(state.workspace.glob("science-*.json"))
    assert len(files) == 1
    artifact = runner.store.artifact_by_filename(files[0].name, frame_id, strict=True)
    assert artifact is not None
    version_id = artifact["latest_version_id"]
    meta = runner.store.version_meta(version_id)
    assert artifact["latest_version_id"] == version_id
    assert Path(meta["snapshot_path"]).is_file()
    assert Path(meta["snapshot_path"]).read_bytes() == Path(meta["path"]).read_bytes()
    source = meta["source"]
    if isinstance(source, str):
        source = json.loads(source)
    assert source == {
        "kind": "science_search",
        "database": "clinvar",
        "query": "VCV000012345",
        "endpoint": "https://eutils.ncbi.nlm.nih.gov/example",
        "retrieved_at": 123456789,
        "source_checksum": "ab" * 32,
        "accessions": ["VCV000012345"],
    }
    created = next(event for event in events if event.get("type") == "artifact_created")
    assert created["artifact"]["artifact_id"] == artifact["artifact_id"]
    assert created["artifact"]["version_id"] == version_id
    runner.close()


def test_host_rpc_stage10_receipt_is_bound_by_enclosing_cell_capture(
    tmp_path, monkeypatch
):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(
            stage1_trusted_delivery=True,
            stage10_scientific_connectors=True,
        ),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    dispatcher = runner._ensure_runtime(state)
    monkeypatch.setattr(
        ScienceConnectorService,
        "search",
        lambda _self, *_args, **_kwargs: _stage10_result(),
    )
    before = runner.artifacts.snapshot(state.workspace)
    with dispatcher.bind_artifact_receipt_scope() as receipts:
        raw = dispatcher(
            "science_search",
            [{"database": "clinvar", "query": "VCV000012345", "limit": 1}],
        )
    assert "_openai4s_artifact_capture" not in raw
    events: list[dict] = []
    captured = runner._capture_artifacts(
        state,
        1,
        "cell-stage10",
        before,
        events.append,
        "python",
        receipts,
    )
    assert len(captured.artifacts) == 1
    version_id = captured.artifacts[0]["version_id"]
    source = runner.store.version_meta(version_id)["source"]
    if isinstance(source, str):
        source = json.loads(source)
    assert source["kind"] == "science_search"
    assert source["source_checksum"] == "ab" * 32
    assert events[-1]["artifact"]["version_id"] == version_id
    runner.close()


def test_delegated_cell_and_native_action_keep_their_own_receipt_scope(
    tmp_path, monkeypatch
):
    """The child dispatcher is usable without borrowing the parent queue."""

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(
            stage1_trusted_delivery=True,
            stage10_scientific_connectors=True,
        ),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    child_frame_id = runner.store.new_frame(
        parent_id=frame_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    dispatcher = build_dispatcher(
        cfg, frame_id=child_frame_id, workspace=state.workspace
    )
    events = []
    hooks = runner.artifacts.delegated_cell_hooks(state, child_frame_id, events.append)

    def fake_search(_self, _database, query, **_kwargs):
        result = _stage10_result()
        result["query"] = query
        result["results"] = [{**result["results"][0], "id": query}]
        return result

    monkeypatch.setattr(ScienceConnectorService, "search", fake_search)

    class _Kernel:
        generation = 1

        def execute(self, _code, origin=None):
            assert origin == "agent"
            result = dispatcher(
                "science_search",
                [{"database": "clinvar", "query": "CELL", "limit": 1}],
            )
            assert "error" not in result
            return {"stdout": "cell done\n", "stderr": "", "error": None}

    executor = LocalActionExecutor(
        _Kernel(),
        dispatcher,
        lambda _code, _messages: None,
        lambda _code: {"stdout": "", "stderr": "", "error": None},
        cell_hooks=hooks,
    )
    try:
        executor.execute(CodeCell("python", "search"), ModelReply(), RunState([]))
        native_call = NativeToolCall(
            id="call-child-native",
            wire_id="call-child-native",
            name="science_search",
            ordinal=0,
            raw_arguments='{"database":"clinvar","query":"NATIVE","limit":1}',
            arguments={"database": "clinvar", "query": "NATIVE", "limit": 1},
        )
        executor.execute(NativeToolBatch((native_call,)), ModelReply(), RunState([]))

        created = [event for event in events if event.get("type") == "artifact_created"]
        assert len(created) == 2
        sources = []
        for event in created:
            meta = runner.store.version_meta(event["artifact"]["version_id"])
            source = meta["source"]
            sources.append(json.loads(source) if isinstance(source, str) else source)
            assert meta["frame_id"] == child_frame_id
        assert {source["query"] for source in sources} == {"CELL", "NATIVE"}
        assert getattr(dispatcher._artifact_receipt_local, "receipts", None) is None
    finally:
        runner.close()
