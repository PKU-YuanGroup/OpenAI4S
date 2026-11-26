"""Where a downloaded file came from (SCI-10).

A file the `web_download` tool saved is captured at the native control-tool
boundary, where there is no Cell, so its Provenance view said "No producing
code is included" and "produced by a non-Cell action" and never named the URL.
The capture now writes the download onto the version, and
`GET /artifacts/{id}/lineage` -- what the Provenance view reads -- carries it:
the URL, when it was saved, the size and SHA-256 of the captured bytes, and how
the request was approved.

The capture runs through the real `_invoke_control_with_artifacts` boundary
(only the network transfer is replaced by a write of the downloaded bytes),
and the lineage is read through the real HTTP handler.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

PDB = b"HEADER    PLANT PROTEIN                           30-APR-81   1CRN\nEND\n"
URL = "https://files.rcsb.org/download/1CRN.pdb"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _get(runner, cfg, path):
    handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
    handler._correlation_id = "req-1"
    sent: dict = {}

    def _send(code, payload, ctype, extra=None):
        sent["code"] = code
        sent["body"] = json.loads(payload.decode("utf-8"))

    handler._send = _send
    handler.command = "GET"
    handler.path = f"/api/v1{path}"
    handler.headers = {
        "Content-Length": "0",
        local_auth.TOKEN_HEADER: local_auth.load_or_mint(cfg.data_dir),
    }
    handler._route("GET")
    return sent["code"], sent["body"]


def _download(runner, state, call_id, filename="1crn.pdb"):
    def transfer():
        (state.workspace / filename).write_bytes(PDB)
        return "saved", True

    return runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(
            name="web_download",
            id=call_id,
            arguments={"url": URL, "path": filename},
        ),
        lambda _event: None,
        transfer,
    )


@pytest.fixture
def setup(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    yield cfg, runner, frame_id, state
    runner.close()


def test_a_downloaded_files_lineage_names_its_url_size_digest_and_approval(setup):
    cfg, runner, frame_id, state = setup
    assert _download(runner, state, "call-1") == ("saved", True)
    artifact = runner.store.artifact_by_filename("1crn.pdb", frame_id, strict=True)
    assert artifact is not None

    status, lineage = _get(runner, cfg, f"/artifacts/{artifact['artifact_id']}/lineage")
    assert status == 200, lineage
    download = lineage["download"]
    assert download["tool"] == "web_download"
    assert download["url"] == URL
    assert download["size_bytes"] == len(PDB)
    assert download["sha256"] == hashlib.sha256(PDB).hexdigest()
    assert download["downloaded_at"].endswith("Z")
    # Nobody was asked for this call: an existing rule or setting allowed it.
    assert download["approval"] == {"how": "no_prompt"}
    # Still honest about the producer: no Cell made this file.
    assert lineage["producer"]["kind"] == "non_cell"


def test_the_approval_answered_for_that_exact_call_is_the_one_recorded(setup):
    cfg, runner, frame_id, state = setup
    for call_id, scope in (("call-other", "global"), ("call-2", "conversation")):
        runner.store.create_permission_request(
            decision_id=f"d-{call_id}",
            tool="web_download",
            target="files.rcsb.org",
            root_frame_id=frame_id,
            frame_id=frame_id,
            project_id="default",
            tool_call_id=call_id,
        )
        runner.store.resolve_permission_request(
            f"d-{call_id}", state="allowed", scope=scope
        )
    _download(runner, state, "call-2")
    artifact = runner.store.artifact_by_filename("1crn.pdb", frame_id, strict=True)
    status, lineage = _get(runner, cfg, f"/artifacts/{artifact['artifact_id']}/lineage")
    assert status == 200, lineage
    assert lineage["download"]["approval"] == {
        "how": "approved",
        "scope": "conversation",
        "target": "files.rcsb.org",
    }


def test_a_file_written_by_another_tool_has_no_download_record(setup):
    cfg, runner, frame_id, state = setup

    def write():
        (state.workspace / "notes.md").write_text("hello", encoding="utf-8")
        return "ok", True

    runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="write_file", id="call-3", arguments={"path": "notes.md"}),
        lambda _event: None,
        write,
    )
    artifact = runner.store.artifact_by_filename("notes.md", frame_id, strict=True)
    status, lineage = _get(runner, cfg, f"/artifacts/{artifact['artifact_id']}/lineage")
    assert status == 200, lineage
    assert "download" not in lineage
