"""Dataset selections cross the existing download and native capture gates."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import threading
from pathlib import Path

import pytest

from openai4s import webtools
from openai4s.artifact_paths import require_capturable_destination
from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.host.datasets import (
    DatasetSelectionError,
    resolve_selection,
    validate_selection,
)
from openai4s.host.science import ScienceConnectorError, ScienceConnectorService
from openai4s.host_dispatch import build_dispatcher
from openai4s.sdk.host import _Host, decode_args
from openai4s.tools.dataset_import import ScienceImportDatasetTool

pytestmark = pytest.mark.stubbed_backend
BODY = b"wavelength,intensity\n500,0.75\n"
DECLARED = "md5:" + hashlib.md5(BODY, usedforsecurity=False).hexdigest()


def spec():
    return {
        "record_id": "123",
        "file_key": "spectra.csv",
        "path": "datasets/spectra.csv",
        "expected_size": len(BODY),
        "expected_checksum": DECLARED,
        "max_bytes": 1024 * 1024,
    }


def record():
    # Synthetic file bytes; no claim that these are PaRoutes file content.
    return {
        "id": 123,
        "doi": "10.5281/zenodo.123",
        "conceptdoi": "10.5281/zenodo.122",
        "metadata": {
            "resource_type": {"type": "dataset"},
            "title": "Synthetic spectra fixture",
            "access_right": "open",
            "license": {"id": "cc-by-4.0"},
            "version": "1.0.0",
        },
        "files": [{"key": "spectra.csv", "size": len(BODY), "checksum": DECLARED}],
    }


def resolver(document):
    calls = []
    raw = json.dumps(document).encode()

    def fetch(url, *_args):
        calls.append(url)
        return {
            "content": raw.decode(),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
        }

    return ScienceConnectorService(fetch), calls


def selection(arguments=None):
    args = arguments or spec()
    return validate_selection(args["record_id"], args["file_key"])


def test_paroutes_selection_uses_the_captured_source_declaration():
    fixture = Path(__file__).parent / "fixtures/zenodo/record-6275421.json"
    row = json.loads(fixture.read_text(encoding="utf-8"))["hits"]["hits"][0]
    service, calls = resolver(row)
    selected = validate_selection("6275421", "n1-targets.txt")
    source = resolve_selection(
        selected,
        expected_size=465689,
        expected_checksum="md5:5adae99357cdad829073b197c7813152",
        timeout=30,
        service=service,
    )
    assert calls == ["https://zenodo.org/api/records/6275421"]
    assert source["dataset"]["record_doi"] == "10.5281/zenodo.6275421"
    assert source["dataset"]["version"] == "1.0.0"
    assert source["dataset"]["declared_license"] == "cc-by-4.0"
    assert "local_sha256" not in source["dataset"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_id", True),
        ("record_id", "latest"),
        ("record_id", "0123"),
        ("record_id", "123/../456"),
        ("file_key", "  "),
        ("file_key", "x" * 513),
        ("max_bytes", -1),
        ("max_bytes", True),
        ("expected_size", True),
        ("expected_size", None),
        ("expected_checksum", None),
        ("timeout", float("nan")),
        ("timeout", float("inf")),
        ("url", "https://example.invalid/override"),
    ],
)
def test_invalid_import_arguments_are_rejected_without_workspace_or_network(
    field, value
):
    args = {**spec(), field: value}
    assert ScienceImportDatasetTool().validation_error(args)
    assert "error" in ScienceImportDatasetTool().execute(object(), args)


def test_paroutes_large_file_budget_refuses_before_metadata(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures/zenodo/record-6275421.json"
    row = json.loads(fixture.read_text(encoding="utf-8"))["hits"]["hits"][0]
    large = next(
        item for item in row["files"] if item["key"] == "uspto_raw_template_library.csv"
    )
    forbid_network(monkeypatch)
    result = ScienceImportDatasetTool().execute(
        object(),
        {
            **spec(),
            "record_id": "6275421",
            "file_key": large["key"],
            "expected_size": large["size"],
            "expected_checksum": large["checksum"],
            "max_bytes": 64 * 1024 * 1024,
        },
    )
    assert "budget" in result["error"]


def test_file_key_remains_an_exact_remote_identifier():
    selected = validate_selection("123", " ../../spectra α.csv ")
    assert selected.file_key == " ../../spectra α.csv "
    assert (
        selected.download_url
        == "https://zenodo.org/api/records/123/files/%20..%2F..%2Fspectra%20%CE%B1.csv%20/content"
    )


@pytest.mark.parametrize(
    "legacy_rule,import_rule", [("deny", "allow"), ("allow", "deny"), ("allow", "ask")]
)
def test_permission_compatibility_refuses_before_any_io(
    monkeypatch, tmp_path, legacy_rule, import_rule
):
    disp = dispatcher(tmp_path)
    forbid_network(monkeypatch)
    for tool, decision in (
        ("web_download", legacy_rule),
        ("science_import_dataset", import_rule),
    ):
        disp.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=tool,
            pattern="zenodo.org",
            decision=decision,
        )
    with disp.bind_native_artifact_committer(
        lambda _: pytest.fail("refused import reached capture")
    ):
        result = disp("science_import_dataset", [spec()])
    assert "Permission denied" in result["error"]


def test_existing_download_deny_precedes_native_capture_admission(tmp_path):
    disp = dispatcher(tmp_path)
    disp.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="web_download",
        pattern="zenodo.org",
        decision="deny",
    )
    # No native committer is bound: the standing deny must still win before
    # the separate native-capture admission message.
    result = disp("science_import_dataset", [spec()])
    assert "web_download to zenodo.org is denied" in result["error"]


def test_web_download_public_schema_does_not_offer_dataset_import():
    from openai4s.tools.web_download import WebDownloadTool

    assert set(WebDownloadTool().parameters["properties"]) == {
        "url",
        "path",
        "max_bytes",
        "timeout",
        "user_agent",
    }
    assert WebDownloadTool().validation_error(
        {**spec(), "url": selection().download_url}
    )


def test_fresh_metadata_binds_exact_record_and_declared_file():
    service, calls = resolver(record())
    source = resolve_selection(
        selection(),
        expected_size=len(BODY),
        expected_checksum=DECLARED,
        timeout=30,
        service=service,
    )
    assert calls == ["https://zenodo.org/api/records/123"]
    assert source["dataset"]["record_doi"] != source["dataset"]["concept_doi"]
    assert source["dataset"]["declared_checksum"] == DECLARED
    assert source["dataset"]["license_status"] == "declared_by_source"
    assert source["responses"][0]["hashed"] == "response_bytes"
    assert "local_sha256" not in source["dataset"]
    assert "file_verification" not in source["dataset"]


@pytest.mark.parametrize(
    "field,value",
    [("size", 1), ("checksum", "md5:" + "0" * 32), ("size", None), ("checksum", None)],
)
def test_changed_or_unknown_declaration_is_refused(field, value):
    row = record()
    row["files"][0][field] = value
    service, calls = resolver(row)
    with pytest.raises(DatasetSelectionError):
        resolve_selection(
            selection(),
            expected_size=len(BODY),
            expected_checksum=DECLARED,
            timeout=30,
            service=service,
        )
    assert calls == ["https://zenodo.org/api/records/123"]


@pytest.mark.parametrize("change", ["record", "file", "access", "duplicate"])
def test_wrong_identity_or_unavailable_file_is_refused(change):
    row = record()
    if change == "record":
        row["id"] = 124
    if change == "file":
        row["files"][0]["key"] = "another.csv"
    if change == "access":
        row["metadata"]["access_right"] = "restricted"
    if change == "duplicate":
        row["files"].append(copy.deepcopy(row["files"][0]))
    service, _ = resolver(row)
    with pytest.raises((DatasetSelectionError, ScienceConnectorError)):
        resolve_selection(
            selection(),
            expected_size=len(BODY),
            expected_checksum=DECLARED,
            timeout=30,
            service=service,
        )


def test_unknown_license_remains_unknown_after_record_resolution():
    row = record()
    row["metadata"].pop("license")
    service, _ = resolver(row)
    source = resolve_selection(
        selection(),
        expected_size=len(BODY),
        expected_checksum=DECLARED,
        timeout=30,
        service=service,
    )
    assert source["dataset"]["declared_license"] is None
    assert source["dataset"]["license_status"] == "unknown"


@pytest.mark.parametrize(
    "change", ["url", "provider", "record", "actor", "size", "checksum"]
)
def test_invalid_selection_is_rejected_by_pure_tool_validation(monkeypatch, change):
    args = spec()
    if change == "url":
        args["url"] = "https://example.invalid/another-file"
    if change == "provider":
        args["provider"] = "unknown"
    if change == "record":
        args["record_id"] = "latest"
    if change == "actor":
        args["actor"] = "not-the-owner"
    if change == "size":
        args.pop("expected_size")
    if change == "checksum":
        args.pop("expected_checksum")
    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_a, **_k: pytest.fail("validation must stay offline"),
    )
    assert ScienceImportDatasetTool().validation_error(args)


def dispatcher(tmp_path):
    value = build_dispatcher(
        Config(data_dir=tmp_path / "data"), workspace=tmp_path / "workspace"
    )
    value.frame_id = value.store.new_frame(project_id="dataset-contract")
    return value


def forbid_network(monkeypatch):
    monkeypatch.setattr(
        webtools,
        "web_fetch",
        lambda *_a, **_k: pytest.fail("metadata fetch was not authorized"),
    )
    monkeypatch.setattr(
        webtools,
        "web_download",
        lambda *_a, **_k: pytest.fail("file fetch was not authorized"),
    )


def test_receipt_list_without_real_committer_refuses_before_io(tmp_path, monkeypatch):
    disp = dispatcher(tmp_path)
    forbid_network(monkeypatch)
    with disp.bind_artifact_receipt_scope() as pending:
        result = disp("science_import_dataset", [spec()])
    assert "native Artifact capture" in result["error"]
    assert pending == []


def test_real_headless_native_adapter_does_not_discard_a_successful_import(
    tmp_path, monkeypatch
):
    from openai4s.agent.actions import NativeToolBatch, NativeToolCall
    from openai4s.agent.models import ModelReply, RunState
    from openai4s.agent.runtime import LocalActionExecutor

    disp = dispatcher(tmp_path)
    forbid_network(monkeypatch)
    call = NativeToolCall(
        id="dataset",
        wire_id="dataset",
        name="science_import_dataset",
        ordinal=0,
        raw_arguments=json.dumps(spec()),
        arguments=spec(),
    )
    executor = LocalActionExecutor(object(), disp, lambda *_: None, lambda *_: {})
    result = executor.execute(NativeToolBatch((call,)), ModelReply(), RunState([]))
    assert result.history_messages[0]["is_error"] is True
    assert "native Artifact capture" in result.history_messages[0]["content"]


def test_existing_download_deny_precedes_metadata_and_file_fetch(tmp_path, monkeypatch):
    disp = dispatcher(tmp_path)
    forbid_network(monkeypatch)
    disp.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="web_download",
        pattern="zenodo.org",
        decision="deny",
    )
    with disp.bind_native_artifact_committer(
        lambda _: pytest.fail("denied import reached capture")
    ):
        result = disp("science_import_dataset", [spec()])
    assert "Permission denied" in result["error"]


def test_exact_execution_cancel_survives_next_turn_and_scope_exit(tmp_path):
    disp = dispatcher(tmp_path)
    original = threading.Event()
    replacement = threading.Event()
    with disp.bind_download_cancellation(original.is_set):
        previous_probe = disp._tool_context.download_cancellation()
        assert previous_probe is not None and not previous_probe()
        original.set()
        with disp.bind_download_cancellation(replacement.is_set):
            current_probe = disp._tool_context.download_cancellation()
            assert current_probe is not None and not current_probe()
            assert previous_probe()
        assert current_probe()
    original.clear()
    assert (
        previous_probe()
    ), "a completed operation must not revive after an event reset"


@pytest.mark.parametrize(
    "relative",
    [
        ".datasets/a.csv",
        "venv/a.csv",
        "node_modules/a.csv",
        "foo.egg-info/a.csv",
        "a.pyc",
    ],
)
def test_non_captured_destination_refuses_before_download(tmp_path, relative):
    with pytest.raises(ValueError, match="excluded"):
        require_capturable_destination(tmp_path, tmp_path / relative)


def test_repository_destination_and_workspace_root_are_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: test")
    with pytest.raises(ValueError, match="repository"):
        require_capturable_destination(tmp_path, repo / "inputs/data.csv")
    with pytest.raises(ValueError, match="file"):
        require_capturable_destination(tmp_path, tmp_path)


def test_visibility_matches_real_artifact_snapshot(tmp_path):
    from tests.test_artifact_manager import ArtifactHarness

    harness = ArtifactHarness(tmp_path)
    for name in (
        "datasets/data.csv",
        ".datasets/data.csv",
        "venv/data.csv",
        "data.pyc",
    ):
        path = harness.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    snapshot = harness.manager.snapshot(harness.workspace)
    assert set(snapshot) == {str(harness.workspace / "datasets/data.csv")}
    require_capturable_destination(
        harness.workspace, harness.workspace / "datasets/data.csv"
    )


def test_python_host_wire_cannot_import_into_a_deferred_cell_scope(
    tmp_path, monkeypatch
):
    disp = dispatcher(tmp_path)
    forbid_network(monkeypatch)
    host = _Host(lambda method, args: disp(method, decode_args(args)))
    with disp.bind_artifact_receipt_scope() as pending:
        result = host._call("science_import_dataset", [spec()])
    assert "native Artifact capture" in result["error"]
    assert pending == []


class FakeParent:
    """File publication port for sequencing tests, not a confinement proof."""

    def __init__(self, root, relative):
        self.root, self.target_relative = root, relative
        self.target = root / relative
        self.target.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def create_staged(self, *, suffix):
        descriptor, path = tempfile.mkstemp(dir=self.target.parent, suffix=suffix)
        return descriptor, path

    def target_metadata(self):
        return None

    def publish(self, staged):
        Path(staged).replace(self.target)

    def discard(self, staged):
        Path(staged).unlink(missing_ok=True)


class FakeWorkspace:
    def __init__(self, root):
        self.root = root

    def download_cancellation(self):
        return None

    def workspace(self):
        return self.root

    def resolve(self, relative, *, must_exist=False):
        return self.root / relative

    def secure_parent(self, relative, *, create_parents):
        return FakeParent(self.root, relative)


def test_verified_file_returns_host_owned_source_receipt(monkeypatch, tmp_path):
    reads = []

    def metadata(url, **_kwargs):
        reads.append(url)
        return {"content": json.dumps(record())}

    @contextlib.contextmanager
    def download(url, **_kwargs):
        reads.append(url)
        yield io.BytesIO(BODY), url, "text/csv"

    monkeypatch.setattr(webtools, "web_fetch", metadata)
    monkeypatch.setattr(webtools, "_open_http_response", download)
    result = ScienceImportDatasetTool().execute(FakeWorkspace(tmp_path), spec())
    assert "error" not in result
    assert (tmp_path / spec()["path"]).read_bytes() == BODY
    receipt = result["_openai4s_artifact_capture"]
    assert receipt["filename"] == "datasets/spectra.csv"
    assert receipt["checksum"] == hashlib.sha256(BODY).hexdigest()
    assert (
        receipt["source"]["dataset"]["file_verification"]
        == "size_and_source_checksum_verified"
    )
    assert reads == ["https://zenodo.org/api/records/123", selection().download_url]


def test_metadata_drift_does_not_start_file_transfer(monkeypatch, tmp_path):
    row = record()
    row["files"][0]["size"] += 1
    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_a, **_k: {"content": json.dumps(row)}
    )
    monkeypatch.setattr(
        webtools,
        "web_download",
        lambda *_a, **_k: pytest.fail("stale selection must not download"),
    )
    result = ScienceImportDatasetTool().execute(FakeWorkspace(tmp_path), spec())
    assert "changed" in result["error"]
    assert not (tmp_path / spec()["path"]).exists()


def test_dispatcher_consumes_dataset_receipt_before_success(monkeypatch, tmp_path):
    disp = dispatcher(tmp_path)
    disp._tool_context._workspace = FakeWorkspace(tmp_path / "workspace")
    disp.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="science_import_dataset",
        pattern="zenodo.org",
        decision="allow",
    )
    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_a, **_k: {"content": json.dumps(record())}
    )

    @contextlib.contextmanager
    def download(url, **_kwargs):
        yield io.BytesIO(BODY), url, "text/csv"

    monkeypatch.setattr(webtools, "_open_http_response", download)
    captured = []

    def commit(receipts):
        captured.extend(receipts)
        return [
            {
                "artifact_id": "fixture-artifact",
                "version_id": "fixture-version",
                "filename": "datasets/spectra.csv",
            }
        ]

    with disp.bind_native_artifact_committer(commit):
        result = disp("science_import_dataset", [spec()])
    assert len(captured) == 1
    assert captured[0]["source"]["dataset"]["record_id"] == "123"
    assert result["artifact"]["version_id"] == "fixture-version"
    assert "_openai4s_artifact_capture" not in result


def test_cancel_during_workspace_copy_stops_before_writing_or_publishing(
    monkeypatch, tmp_path
):
    from openai4s.host import download as download_module

    body = b"x" * (512 * 1024)
    declared = "md5:" + hashlib.md5(body, usedforsecurity=False).hexdigest()
    args = spec()
    args.update(expected_size=len(body), expected_checksum=declared)
    row = record()
    row["files"][0].update(size=len(body), checksum=declared)
    monkeypatch.setattr(
        webtools, "web_fetch", lambda *_a, **_k: {"content": json.dumps(row)}
    )

    @contextlib.contextmanager
    def response(url, **_kwargs):
        yield io.BytesIO(body), url, "text/csv"

    monkeypatch.setattr(webtools, "_open_http_response", response)
    stopped = threading.Event()
    reads, writes = [], []
    original_fdopen = os.fdopen

    class Handle:
        def __init__(self, handle, mode):
            self.handle, self.mode = handle, mode

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def close(self):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, size):
            chunk = self.handle.read(size)
            reads.append(len(chunk))
            stopped.set()
            return chunk

        def write(self, chunk):
            writes.append(len(chunk))
            return self.handle.write(chunk)

    monkeypatch.setattr(
        download_module.os,
        "fdopen",
        lambda fd, mode, **kwargs: Handle(original_fdopen(fd, mode, **kwargs), mode),
    )
    workspace = FakeWorkspace(tmp_path)
    workspace.download_cancellation = lambda: stopped.is_set
    destination = tmp_path / args["path"]
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"previous-input")
    result = ScienceImportDatasetTool().execute(workspace, args)
    assert "cancelled" in result["error"]
    assert reads == [256 * 1024]
    assert writes == []
    assert destination.read_bytes() == b"previous-input"
    assert "_openai4s_artifact_capture" not in result


@contextlib.contextmanager
def native_dataset_session(tmp_path, monkeypatch, *, stage1):
    """Real workspace publication, Gateway capture and Store; offline network."""
    from openai4s.server.gateway import SessionRunner, WSHub
    from openai4s.tools.registry import execute_tool_call

    cfg = Config(
        data_dir=tmp_path / "native-data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        roadmap_features=RoadmapFeatureFlags(stage1_trusted_delivery=stage1),
    )
    runner = SessionRunner(cfg, WSHub(), start_idle_sweeper=False)
    frame = runner.store.new_frame(
        kind="turn", project_id="dataset-contract", status="ready"
    )
    state = runner._state(frame, "dataset-contract")
    dispatcher = runner._ensure_runtime(state)
    runner.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="science_import_dataset",
        pattern="zenodo.org",
        decision="allow",
    )
    document = record()
    reads = []

    def fetch(url, **_kwargs):
        reads.append(url)
        payload = json.dumps(document).encode()
        return {
            "content": payload.decode(),
            "raw_sha256": hashlib.sha256(payload).hexdigest(),
            "raw_bytes": len(payload),
        }

    @contextlib.contextmanager
    def response(url, **_kwargs):
        reads.append(url)
        yield io.BytesIO(BODY), url, "text/csv"

    monkeypatch.setattr(webtools, "web_fetch", fetch)
    monkeypatch.setattr(webtools, "_open_http_response", response)

    def invoke(emit):
        call = {
            "id": "import-input",
            "name": "science_import_dataset",
            "arguments": spec(),
        }
        return runner._invoke_control_with_artifacts(
            state, call, emit, lambda: execute_tool_call(dispatcher, call)
        )

    try:
        yield runner, state, document, reads, invoke
    finally:
        runner.close()


@pytest.mark.skipif(
    os.name != "posix",
    reason="native Artifact capture tests require POSIX file operations",
)
@pytest.mark.parametrize("stage1", [False, True])
def test_native_import_keeps_same_byte_sources_immutable_across_store_reopen(
    tmp_path, monkeypatch, stage1
):
    from openai4s.store import get_store

    with native_dataset_session(tmp_path, monkeypatch, stage1=stage1) as (
        runner,
        state,
        document,
        reads,
        invoke,
    ):
        events = []
        first_observation, ok = invoke(events.append)
        assert ok is True, first_observation
        first = runner.store.artifact_by_filename(
            spec()["path"], state.root_frame_id, strict=True
        )
        assert first is not None
        first_id = first["latest_version_id"]
        first_meta = runner.store.version_meta(first_id)
        first_source = json.loads(first_meta["source"])
        assert first["root_frame_id"] == state.root_frame_id
        assert first["project_id"] == state.project_id
        assert first_source["dataset"]["file_key"] == "spectra.csv"
        assert (
            first_source["dataset"]["local_sha256"] == hashlib.sha256(BODY).hexdigest()
        )
        assert Path(first_meta["snapshot_path"]).read_bytes() == BODY
        assert first_id in first_observation
        assert "_openai4s_artifact_capture" not in first_observation

        # Same declared file bytes, a later metadata read: do not replace the
        # original version's source with the new read's title or response hash.
        document["metadata"]["title"] = "Updated source annotation"
        second_observation, ok = invoke(events.append)
        assert ok is True, second_observation
        versions = runner.store.list_versions(first["artifact_id"])
        assert len(versions) == 2
        second_id = next(
            row["version_id"] for row in versions if row["version_id"] != first_id
        )
        assert runner.store.version_meta(first_id)["source"] == first_meta["source"]
        second_source = json.loads(runner.store.version_meta(second_id)["source"])
        assert second_source["dataset"]["title"] == "Updated source annotation"
        assert second_source["response_sha256"] != first_source["response_sha256"]
        assert (
            len([event for event in events if event.get("type") == "artifact_created"])
            == 2
        )
        assert reads == [selection().metadata_url, selection().download_url] * 2
        db_path = runner.cfg.db_path

    runner.store.close()
    reopened = get_store(db_path)
    try:
        historical = reopened.version_meta(first_id)
        assert historical["source"] == first_meta["source"]
        assert Path(historical["snapshot_path"]).read_bytes() == BODY
        assert len(reopened.list_versions(first["artifact_id"])) == 2
    finally:
        reopened.close()


@pytest.mark.skipif(
    os.name != "posix",
    reason="native Artifact capture tests require POSIX file operations",
)
@pytest.mark.parametrize("stage1", [False, True])
@pytest.mark.parametrize("fault", ["freeze", "emit_after_commit"])
def test_native_capture_failure_reports_retry_veto_and_preserves_durable_truth(
    tmp_path, monkeypatch, stage1, fault
):
    with native_dataset_session(tmp_path, monkeypatch, stage1=stage1) as (
        runner,
        state,
        _document,
        reads,
        invoke,
    ):
        if fault == "freeze":

            def fail_freeze(*_args, **_kwargs):
                raise OSError("injected snapshot failure")

            monkeypatch.setattr(
                runner.artifacts, "freeze_capture_snapshot", fail_freeze
            )

        def emit(event):
            if fault == "emit_after_commit" and event.get("type") == "artifact_created":
                raise OSError("injected event delivery failure")

        with pytest.raises(
            RuntimeError, match="trusted Artifact capture failed"
        ) as failure:
            invoke(emit)
        assert failure.value.output_committed is True
        assert (state.workspace / spec()["path"]).read_bytes() == BODY
        assert reads == [selection().metadata_url, selection().download_url]
        artifact = runner.store.artifact_by_filename(
            spec()["path"], state.root_frame_id, strict=True
        )
        if fault == "freeze":
            assert artifact is None
        else:
            assert artifact is not None
            versions = runner.store.list_versions(artifact["artifact_id"])
            assert len(versions) == 1
            meta = runner.store.version_meta(versions[0]["version_id"])
            assert Path(meta["snapshot_path"]).read_bytes() == BODY
            assert (
                json.loads(meta["source"])["dataset"]["local_sha256"]
                == meta["checksum"]
            )
