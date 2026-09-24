"""Opt-in PaRoutes acceptance through the real native Artifact transaction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server.gateway import SessionRunner, WSHub
from openai4s.store import get_store
from openai4s.tools.registry import execute_tool_call

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.name != "posix", reason="native workspace capture requires POSIX"
    ),
]


def test_paroutes_native_import_and_store_reopen(tmp_path, monkeypatch):
    # Real Zenodo metadata and 465,689 bytes; no model, analysis or archive execution.
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = SessionRunner(cfg, WSHub(), start_idle_sweeper=False)
    try:
        frame = runner.store.new_frame(
            kind="turn", project_id="paroutes-acceptance", status="ready"
        )
        state = runner._state(frame, "paroutes-acceptance")
        dispatcher = runner._ensure_runtime(state)
        runner.store.set_permission_rule(
            scope="conversation",
            scope_id=frame,
            tool="science_import_dataset",
            pattern="zenodo.org",
            decision="allow",
        )
        call = {
            "id": "paroutes-selected-file",
            "name": "science_import_dataset",
            "arguments": {
                "record_id": "6275421",
                "file_key": "n1-targets.txt",
                "path": "datasets/n1-targets.txt",
                "expected_size": 465689,
                "expected_checksum": "md5:5adae99357cdad829073b197c7813152",
                "max_bytes": 1048576,
            },
        }
        observation, ok = runner._invoke_control_with_artifacts(
            state,
            call,
            lambda _event: None,
            lambda: execute_tool_call(dispatcher, call),
        )
        assert ok, observation
        artifact = runner.store.artifact_by_filename(
            call["arguments"]["path"], state.root_frame_id, strict=True
        )
        assert artifact is not None
        version = artifact["latest_version_id"]
        metadata = runner.store.version_meta(version)
        body = Path(metadata["snapshot_path"]).read_bytes()
        assert len(body) == 465689
        assert (
            hashlib.md5(body, usedforsecurity=False).hexdigest()
            == "5adae99357cdad829073b197c7813152"
        )
        sha256 = hashlib.sha256(body).hexdigest()
        assert (
            sha256 == "c0d1b48379e1ceb1129fba4bf3773f73f27bdb22bb4d468417e6e404d3210c15"
        )
        source = json.loads(metadata["source"])
        assert metadata["checksum"] == sha256 == source["dataset"]["local_sha256"]
        assert source["dataset"]["record_doi"] == "10.5281/zenodo.6275421"
        assert source["dataset"]["declared_license"] == "cc-by-4.0"
        assert (
            source["dataset"]["file_verification"]
            == "size_and_source_checksum_verified"
        )
        assert source["responses"][0]["hashed"] == "response_bytes"
        assert version in observation
        db_path = runner.cfg.db_path
    finally:
        runner.close()
    runner.store.close()
    reopened = get_store(db_path)
    try:
        assert reopened.version_meta(version)["source"] == metadata["source"]
        print(
            json.dumps(
                {
                    "record_id": "6275421",
                    "file_key": "n1-targets.txt",
                    "bytes": len(body),
                    "sha256": sha256,
                }
            )
        )
    finally:
        reopened.close()
