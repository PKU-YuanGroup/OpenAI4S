"""Runtime evidence in Session packages, its diagnosis, and its capture.

The package always carried what a session said and produced. These tests pin
what it now carries about how the runtime *behaved* -- delegated children,
activity cards, host calls, permission requests, the model endpoint and the
failure detail of a stopped turn -- and the boundaries around it: no URL, no
credential, no permission payload, no exception text, and no export that
fails because a piece of supporting evidence could not be read.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s import package_diagnosis
from openai4s.agent.ledger import RuntimeActionLedger, reduce_action_groups
from openai4s.agent.models import CALL_TELEMETRY_KEY, ModelReply
from openai4s.agent.runtime import ChatModel
from openai4s.config import Config, LLMConfig
from openai4s.evidence import verify_package
from openai4s.llm.models import StreamTimeoutError
from openai4s.server import gateway as gateway_mod
from openai4s.server import package_runtime
from openai4s.server.action_timeline import ActionTimelineService
from openai4s.server.errors import GatewayError
from openai4s.server.session_package import SessionPackageError, SessionPackageService
from openai4s.storage.snapshots import WorkspaceCAS
from openai4s.store import Store

CANARY = "CANARY-private-research-7f3a"


def _service(tmp_path: Path, store: Store | None = None):
    store = store or Store(tmp_path / "openai4s.db")

    def workspace(root_frame_id, branch_id):
        path = tmp_path / "ws" / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    return store, SessionPackageService(
        store,
        data_dir=tmp_path,
        workspace=workspace,
        cas=WorkspaceCAS(tmp_path / "cas"),
    )


def _unpack(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _repack(files: dict[str, bytes]) -> bytes:
    from openai4s.server.session_package import _canonical_json, _sha256, _zip_bytes

    files = {name: data for name, data in files.items() if name != "manifest.json"}
    body = {
        "format": "openai4s.session",
        "schema_version": 1,
        "files": [
            {"path": name, "size": len(data), "sha256": _sha256(data)}
            for name, data in sorted(files.items())
        ],
    }
    manifest = {**body, "manifest_sha256": _sha256(_canonical_json(body))}
    return _zip_bytes({**files, "manifest.json": _canonical_json(manifest)})


def _terminal(store, root, turn, reason, *, at, **result):
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id=turn,
        kind="terminal",
        provider="ark",
        model="mock-model",
        created_at=at,
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="completed" if reason == "submitted" else "failed",
        result={"reason": reason, **result},
        created_at=at,
    )
    return group


def _session(store: Store, tmp_path: Path) -> dict:
    """A session that failed the ways real reports did."""

    project = store.create_project(name="Runtime evidence")
    root = store.new_frame(
        project_id=project["project_id"], kind="turn", status="failed"
    )
    t0 = 1_758_600_000_000
    store.add_message(
        root_frame_id=root,
        frame_id=root,
        role="user",
        content=f"analyse {CANARY}",
        created_at=t0,
    )
    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-1",
        kind="user",
        assistant_message={"role": "user", "content": f"analyse {CANARY}"},
        created_at=t0,
    )
    for index in range(3):
        store.append_tool_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-1",
            provider="ark",
            model="mock-model",
            events=[
                {
                    "sequence": 0,
                    "type": "proposed",
                    "action_id": f"a{index}",
                    "tool_call_id": f"c{index}",
                    "canonical_arguments": {
                        "name": "write_file",
                        "arguments": {"path": f"{CANARY}.md"},
                        "parse_error": "Expecting value" if index == 2 else None,
                    },
                    "raw_arguments": "" if index == 2 else '{"path":"r.md"}',
                },
                {
                    "sequence": 1,
                    "type": "model_call",
                    "result": {
                        "outcome": "ok",
                        "duration_ms": 900,
                        "stream": True,
                        "deltas": 4,
                        "first_delta_ms": 120,
                        "last_delta_ms": 800,
                        "attempts": 2 if index == 0 else 1,
                        "retried_after": "llm_rate_limited" if index == 0 else None,
                        "finish_reason": "tool_calls",
                    },
                },
                {
                    "sequence": 2,
                    "type": "result",
                    "action_id": f"a{index}",
                    "tool_call_id": f"c{index}",
                    "result": {
                        "role": "tool",
                        "name": "write_file",
                        "content": f"[Tool error] {CANARY}",
                        "is_error": True,
                    },
                },
            ],
            created_at=t0 + 1000 * (index + 1),
        )
    _terminal(
        store,
        root,
        "turn-1",
        "no_progress",
        at=t0 + 4000,
        progress_reason="same_action",
    )
    store.add_message(
        root_frame_id=root,
        frame_id=root,
        role="assistant",
        content="stopped",
        metadata={"failure": {"request_id": "abc123def4567890", "code": "no_progress"}},
        created_at=t0 + 4100,
    )
    t1 = t0 + 60_000
    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-2",
        kind="user",
        assistant_message={"role": "user", "content": "continue"},
        created_at=t1,
    )
    _terminal(
        store,
        root,
        "turn-2",
        "llm_stream_timeout",
        at=t1 + 612_000,
        error={
            "type": "StreamTimeoutError",
            "message": "timed out",
            "detail": {
                "category": "LLMError",
                "chain": ["openai4s.llm.models.StreamTimeoutError", "TimeoutError"],
                "code": "llm_stream_timeout",
                "output_committed": True,
                "where": ["openai4s/llm/transport.py:903:_consume"],
                "call": {
                    "outcome": "error",
                    "duration_ms": 612_000,
                    "stream": True,
                    "deltas": 57,
                    "first_delta_ms": 1800,
                    "last_delta_ms": 12_000,
                    "attempts": 1,
                },
            },
        },
    )
    store.add_message(
        root_frame_id=root,
        frame_id=root,
        role="assistant",
        content="upstream went quiet",
        metadata={
            "failure": {
                "request_id": "ffee00112233aabb",
                "code": "llm_stream_timeout",
                "output_committed": True,
            }
        },
        created_at=t1 + 612_100,
    )
    # A root Cell that produced an Artifact, which a Saving card refers to.
    cell = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
        code="import docx",
        result={
            "id": "cell-root",
            "stdout": "",
            "stderr": "",
            "error": (
                "Traceback (most recent call last):\n"
                f"ModuleNotFoundError: No module named '{CANARY}'"
            ),
        },
        cell_index=1,
        state_revision=1,
    )
    workspace = tmp_path / "ws" / root / root
    workspace.mkdir(parents=True, exist_ok=True)
    artifact_path = workspace / "result.csv"
    artifact_path.write_text("id,score\n1,0.9\n", encoding="utf-8")
    artifact = store.save_artifact(
        path=str(artifact_path),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=artifact_path.stat().st_size,
        checksum=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    # A delegated child: its ledger is rooted at its own frame.
    child = store.new_frame(
        parent_id=root, kind="delegate", name="helper", model="mock-model", depth=1
    )
    store.append_action_group(
        root_frame_id=child,
        turn_id="child-turn",
        kind="user",
        assistant_message={"role": "user", "content": "help"},
    )
    child_terminal = store.append_action_group(
        root_frame_id=child, turn_id="child-turn", kind="terminal"
    )
    store.append_action_event(
        group_id=child_terminal["group_id"],
        type="failed",
        result={"reason": "max_turns"},
    )
    child_cell = store.log_cell(
        frame_id=child,
        root_frame_id=root,
        project_id=project["project_id"],
        code="x = 1",
        result={"id": "cell-child", "stdout": "", "stderr": "", "error": None},
        cell_index=2,
        state_revision=2,
    )
    # Cards: one done (pointing at the Artifact version), one failed, one that
    # never recorded its end.
    store.add_step(
        step_id="s-save",
        frame_id=root,
        kind="artifact",
        title="Saving result.csv",
        input={"path": "result.csv"},
        status="running",
    )
    store.update_step(
        "s-save",
        status="done",
        output={"artifacts": [{"version_id": artifact["version_id"]}]},
    )
    store.add_step(
        step_id="s-env",
        frame_id=root,
        kind="env",
        title="Installing python-docx",
        status="running",
    )
    store.update_step("s-env", status="error", output={"error": "network unreachable"})
    store.add_step(step_id="s-run", frame_id=root, kind="bash", title="Running")
    store.log_host_call(
        method="write_file",
        args=[{"path": str(Path.home() / "research" / "r.md")}],
        ok=True,
        frame_id=root,
    )
    for _ in range(2):
        store.log_host_call(
            method="pip_install", args=["python-docx"], ok=False, frame_id=root
        )
    store.log_host_call(method="llm", args=["summarize"], ok=True, frame_id=child)
    store.create_permission_request(
        decision_id="perm-1",
        tool="bash",
        target="pip install python-docx",
        root_frame_id=root,
        frame_id=root,
        payload={"command": CANARY},
    )
    store.resolve_permission_request(
        "perm-1", state="denied", message=CANARY, resolution_context=CANARY
    )
    generation = store.create_kernel_generation(
        root_frame_id=root,
        language="python",
        state="active",
        environment={"sandbox": {"mode": "auto", "state": "unavailable"}},
    )
    store.finish_kernel_generation(
        generation["generation_id"], state="crashed", reason="watchdog_restart"
    )
    return {
        "project": project,
        "root": root,
        "child": child,
        "child_cell": child_cell,
        "artifact": artifact,
    }


def _export(tmp_path, **kwargs):
    store, service = _service(tmp_path)
    session = _session(store, tmp_path)
    exported = service.export(session["root"], **kwargs)
    return store, service, session, exported


# ----------------------------------------------------------------- export
def test_runtime_members_are_listed_verifiable_and_deterministic(tmp_path):
    store, service, session, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        listed = {
            entry["path"] for entry in json.loads(files["manifest.json"])["files"]
        }
        for name in (
            "runtime/environment.json",
            "runtime/frames.json",
            "runtime/activity.json",
            "runtime/host_calls.json",
            "runtime/permissions.json",
            "runtime/compactions.json",
            "runtime/collection.json",
            "runtime/diagnosis.json",
            "DIAGNOSTICS.md",
        ):
            assert name in files and name in listed, name
        target = tmp_path / exported["filename"]
        target.write_bytes(exported["data"])
        assert verify_package(target)["ok"] is True
        # Nothing time-of-export in the evidence: the same session exports to
        # the same bytes, so the archive's own hash stays meaningful.
        assert service.export(session["root"])["data"] == exported["data"]
        collection = json.loads(files["runtime/collection.json"])
        assert {
            name: item["status"] for name, item in collection["sections"].items()
        } == {
            "activity": "ok",
            "compactions": "ok",
            "compute_jobs": "ok",
            "diagnosis": "ok",
            "environment": "ok",
            "frames": "ok",
            "host_calls": "ok",
            "permissions": "ok",
        }
    finally:
        store.close()


def test_delegated_child_ledger_and_cell_attribution_travel(tmp_path):
    store, _service_, session, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        frames = json.loads(files["runtime/frames.json"])
        assert [frame["frame_id"] for frame in frames["frames"]] == [
            session["root"],
            session["child"],
        ]
        (ledger,) = frames["child_ledgers"]
        assert ledger["frame_id"] == session["child"]
        terminal = [group for group in ledger["groups"] if group["kind"] == "terminal"]
        assert terminal[0]["events"][0]["result"] == {"reason": "max_turns"}
        # `ledger.json` is still the root's own history only.
        root_ledger = json.loads(files["ledger.json"])
        assert {group["root_frame_id"] for group in root_ledger["groups"]} == {
            session["root"]
        }
        # The Notebook stays frame-free; the attribution is runtime evidence.
        notebook = json.loads(files["notebook.json"])
        assert all("frame_id" not in cell for cell in notebook["cells"])
        assert frames["cell_frames"] == {session["child_cell"]: session["child"]}
    finally:
        store.close()


def test_activity_cards_host_calls_and_permissions_are_bounded_and_scrubbed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(package_runtime, "MAX_HOST_CALLS", 2)
    store, _service_, session, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        activity = json.loads(files["runtime/activity.json"])
        (frame,) = activity["frames"]
        steps = {step["step_id"]: step for step in frame["steps"]}
        assert set(steps) == {"s-save", "s-env", "s-run"}
        assert steps["s-env"]["status"] == "error"
        assert steps["s-run"]["status"] == "running"
        assert all(
            isinstance(step["created_at"], int) and isinstance(step["updated_at"], int)
            for step in steps.values()
        )
        host = json.loads(files["runtime/host_calls.json"])
        # Newest two kept, every row still counted.
        assert host["total"] == 4 and host["truncated"] is True
        assert [call["method"] for call in host["calls"]] == ["pip_install", "llm"]
        assert {item["method"]: item["failed"] for item in host["totals"]} == {
            "llm": 0,
            "pip_install": 2,
            "write_file": 0,
        }
        permissions = json.loads(files["runtime/permissions.json"])
        (request,) = permissions["requests"]
        assert request["state"] == "denied" and request["tool"] == "bash"
        for key in ("payload", "resolution_context", "message", "pattern"):
            assert key not in request
        assert CANARY not in files["runtime/permissions.json"].decode()
    finally:
        store.close()


def test_runtime_evidence_redacts_known_secrets_and_home_paths(tmp_path, monkeypatch):
    secret = "zz-very-private-value-123456"
    monkeypatch.setenv("LAB_SERVICE_API_KEY", secret)
    store, service = _service(tmp_path)
    try:
        session = _session(store, tmp_path)
        store.log_host_call(
            method="connector_call",
            args=[{"note": f"uses {secret}", "cwd": str(tmp_path / "work")}],
            ok=True,
            frame_id=session["root"],
        )
        exported = service.export(session["root"])
        files = _unpack(exported["data"])
        text = files["runtime/host_calls.json"].decode()
        assert secret not in text and "[REDACTED]" in text
        assert str(Path.home()) not in text and "~/research/r.md" in text
        # The data directory is named, not spelled out.
        assert "<data_dir>/work" in text
    finally:
        store.close()


def test_a_section_that_cannot_be_read_is_recorded_not_fatal(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    try:
        session = _session(store, tmp_path)

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store, "list_session_host_calls", broken)
        files = _unpack(service.export(session["root"])["data"])
        assert "runtime/host_calls.json" not in files
        collection = json.loads(files["runtime/collection.json"])
        assert collection["sections"]["host_calls"]["status"] == "unavailable"
        assert "`host_calls`: unavailable" in files["DIAGNOSTICS.md"].decode()
    finally:
        store.close()


def test_the_diagnosis_page_names_what_went_wrong_and_reproduces_no_content(
    tmp_path,
):
    store, _service_, _session_, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        page = files["DIAGNOSTICS.md"].decode()
        diagnosis = files["runtime/diagnosis.json"].decode()
        assert (
            "turn 2 stopped with llm_stream_timeout: the model call ran 612.0 s, "
            "received 57 stream delta(s) (first at 1.8 s, last at 12.0 s), then "
            "nothing for 600.0 s"
        ) in page
        assert "request_id ffee00112233aabb" in page
        assert "no-progress circuit (same_action)" in page
        assert "write_file returned an error 3/3" in page
        assert "1 with empty argument strings" in page
        assert "ModuleNotFoundError×1" in page
        assert "pip_install 2/2" in page
        assert "delegated child turns ended max_turns×1" in page
        assert "python:watchdog_restart×1" in page
        assert "auto:unavailable×1" in page
        assert "after llm_rate_limited×1" in page
        # Content-free: the canary sits in a message, tool arguments, a tool
        # result, a Cell traceback and a permission payload.
        assert CANARY not in page and CANARY not in diagnosis
    finally:
        store.close()


def test_remote_compute_jobs_travel_scoped_to_the_session(tmp_path):
    store, service = _service(tmp_path)
    try:
        session = _session(store, tmp_path)
        workspace = tmp_path / "ws" / session["root"] / session["root"]
        store.create_compute_job(
            job_id="job-mine",
            provider="ssh:gpu-lab-private",
            owner_key=str(workspace),
        )
        store.update_compute_job(
            "job-mine",
            status="failed",
            exit_code=137,
            termination_reason="oom",
            # Recorded as `str(exc)` from the ssh call: a host name inside.
            reason="ssh: connect to host gpu.private-lab port 22: Connection refused",
        )
        store.append_compute_job_event("job-mine", "submitted", {"cmd": CANARY})
        store.create_compute_job(
            job_id="job-other-session",
            provider="ssh:elsewhere",
            owner_key=str(tmp_path / "ws" / "someone-else"),
        )
        files = _unpack(service.export(session["root"])["data"])
        document = json.loads(files["runtime/compute_jobs.json"])
        (job,) = document["jobs"]
        assert job["job_id"] == "job-mine" and job["status"] == "failed"
        assert job["provider_kind"] == "ssh" and len(job["provider_fingerprint"]) == 16
        # Event kinds and times only: payloads can carry a command line.
        assert [event["kind"] for event in job["events"]][-1] == "submitted"
        assert all(set(event) == {"seq", "kind", "at"} for event in job["events"])
        text = files["runtime/compute_jobs.json"].decode()
        for leaked in (
            "gpu-lab-private",
            "gpu.private-lab",
            CANARY,
            "workdir",
            "receipt",
        ):
            assert leaked not in text
        assert (
            job["reason"]["kind"] == "text" and len(job["reason"]["fingerprint"]) == 12
        )
        assert "remote compute jobs did not deliver: ssh:failed(oom)×1" in (
            files["DIAGNOSTICS.md"].decode()
        )
    finally:
        store.close()


def test_records_naming_a_secret_file_travel_without_their_payload(tmp_path):
    # No API_KEY/TOKEN/SECRET/PASSWORD in the name: the pattern scrubber keeps
    # it, so only refusing the *file* protects it -- as the artifact filter does.
    secret = "DB_PASS=hunter2-not-pattern-shaped"
    store, service = _service(tmp_path)
    try:
        session = _session(store, tmp_path)
        root, child = session["root"], session["child"]
        store.add_step(
            step_id="s-write-env",
            frame_id=root,
            kind="write",
            title="Writing .env",
            input={"path": "proj/.env", "content": secret},
            status="running",
        )
        store.update_step("s-write-env", status="done", output={"bytes": 35})
        store.add_step(
            step_id="s-cat",
            frame_id=root,
            kind="bash",
            title="Running",
            input={"command": "cat .env"},
            status="running",
        )
        store.update_step("s-cat", status="done", output={"stdout": secret})
        store.log_host_call(
            method="write_file",
            args=[{"path": "keys/id_rsa.pem", "content": secret}],
            ok=True,
            frame_id=root,
        )
        arguments = {"path": ".env", "content": secret}
        group = store.append_tool_action_group(
            root_frame_id=child,
            turn_id="child-turn-2",
            provider="ark",
            model="mock-model",
            assistant_message={
                "role": "assistant",
                "tool_calls": [{"name": "write_file", "arguments": arguments}],
            },
            events=[
                {
                    "sequence": 0,
                    "type": "proposed",
                    "action_id": "w",
                    "tool_call_id": "w",
                    "canonical_arguments": {
                        "name": "write_file",
                        "arguments": arguments,
                    },
                    "raw_arguments": json.dumps(arguments),
                }
            ],
        )
        store.append_action_event(
            group_id=group["group_id"],
            type="result",
            action_id="w",
            tool_call_id="w",
            result={"role": "tool", "name": "write_file", "content": "wrote it"},
        )
        files = _unpack(service.export(root)["data"])
        for name in (
            "runtime/activity.json",
            "runtime/host_calls.json",
            "runtime/frames.json",
        ):
            assert "hunter2" not in files[name].decode(), name
        cards = {
            step["step_id"]: step
            for step in json.loads(files["runtime/activity.json"])["frames"][0]["steps"]
        }
        withheld = {"withheld": "names_a_secret_file"}
        assert cards["s-write-env"]["input"] == withheld
        assert cards["s-write-env"]["title"] == "Writing .env"
        assert cards["s-write-env"]["status"] == "done"
        assert cards["s-cat"]["output"] == withheld
        # An ordinary card is untouched.
        assert cards["s-save"]["input"] == {"path": "result.csv"}
        sections = json.loads(files["runtime/collection.json"])["sections"]
        assert sections["activity"]["withheld"] == 2
        assert sections["host_calls"]["withheld"] == 1
        assert sections["frames"]["withheld"] == 1
        (ledger,) = json.loads(files["runtime/frames.json"])["child_ledgers"]
        (hidden,) = [g for g in ledger["groups"] if g["turn_id"] == "child-turn-2"]
        proposed = [e for e in hidden["events"] if e["type"] == "proposed"][0]
        # The tool name survives, so the diagnosis still counts the call.
        assert proposed["canonical_arguments"] == {"name": "write_file", **withheld}
        assert hidden["assistant_message"] == withheld
    finally:
        store.close()


def test_child_failures_overrides_and_stops_travel_content_free():
    row = package_runtime._child_row(
        {
            "child_id": "c1",
            "status": "failed",
            "error": "LLM HTTP 502: <html>gateway at relay.private-lab.example</html>",
            "stop_reason": "stop: the relay at relay.private-lab.example is down",
            "task_status": "failed",
            "overrides": {
                "model": {
                    "provider": "ark",
                    "model": "glm",
                    "base_url": "https://lab:pw@relay.private-lab.example/v1",
                },
                "skill_names": ["a"],
            },
            "result": {"output": CANARY},
        }
    )
    assert row["error"]["kind"] == "llm_http_502"
    assert len(row["error"]["fingerprint"]) == 12
    assert row["stop_reason"] == "text"
    assert row["overrides"]["model"]["endpoint"]["class"] == "hostname"
    assert row["overrides"]["skill_names"] == ["a"]
    assert "result" not in row
    encoded = json.dumps(row)
    for leaked in ("relay.private-lab", "lab:pw", "<html>", CANARY):
        assert leaked not in encoded
    # A recorded code travels as it is.
    assert package_runtime._child_row({"stop_reason": "max_turns"}) == {
        "stop_reason": "max_turns"
    }
    assert package_runtime.error_summary(
        "openai4s.llm.models.StreamTimeoutError: read timed out"
    )["kind"] == ("StreamTimeoutError")


def test_bookkeeping_groups_are_not_read_as_turns(tmp_path):
    store, service, session, exported = _export(tmp_path)
    try:
        imported = service.import_bytes(exported["data"])
        files = _unpack(service.export(imported["root_frame_id"])["data"])
        diagnosis = json.loads(files["runtime/diagnosis.json"])
        # The import marker is a ledger group of its own turn; it is not a stop.
        assert diagnosis["counts"]["turns"] == 2
        assert "unterminated" not in [item["kind"] for item in diagnosis["findings"]]
    finally:
        store.close()


# ----------------------------------------------------------------- import
def test_import_restores_activity_cards_with_original_times(tmp_path):
    store, service, session, exported = _export(tmp_path)
    try:
        source_steps = {
            step["step_id"]: step
            for step in store.list_steps_for_export(session["root"])
        }
        imported = service.import_bytes(exported["data"])
        restored = store.list_steps_for_export(imported["root_frame_id"])
        assert [step["kind"] for step in restored] == ["artifact", "env", "bash"]
        by_kind = {step["kind"]: step for step in restored}
        assert by_kind["env"]["status"] == "error"
        # A card no process here is running arrives stopped.
        assert by_kind["bash"]["status"] == "stopped"
        assert by_kind["artifact"]["created_at"] == source_steps["s-save"]["created_at"]
        # The Saving card points at the imported version, not the sender's.
        (ref,) = by_kind["artifact"]["output"]["artifacts"]
        assert ref["version_id"] != session["artifact"]["version_id"]
        versions = store.list_versions(
            store.list_artifacts({"root_frame_id": imported["root_frame_id"]})[0][
                "artifact_id"
            ]
        )
        assert ref["version_id"] in {version["version_id"] for version in versions}
    finally:
        store.close()


def test_a_package_without_activity_member_imports_review_cards_only(tmp_path):
    store, service, session, exported = _export(tmp_path)
    try:
        store.add_step(
            step_id="s-review",
            frame_id=session["root"],
            kind="review",
            title="Evidence review",
            status="done",
        )
        files = _unpack(service.export(session["root"])["data"])
        files.pop("runtime/activity.json")
        imported = service.import_bytes(_repack(files))
        restored = store.list_steps(imported["root_frame_id"])
        assert [step["kind"] for step in restored] == ["review"]
    finally:
        store.close()


@pytest.mark.parametrize(
    "document, match",
    [
        ({"frames": "nope"}, "frames must be a list"),
        ({"frames": [{"frame_id": "x"}]}, "invalid frame"),
        (
            {"frames": [{"frame_id": "ROOT", "steps": [{"kind": "x", "input": "s"}]}]},
            "input must be an object",
        ),
        (
            {"frames": [{"frame_id": "ROOT", "steps": ["not-a-step"]}]},
            "invalid step",
        ),
    ],
)
def test_import_rejects_a_malformed_activity_member(tmp_path, document, match):
    store, service, session, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        text = json.dumps(document).replace("ROOT", session["root"])
        files["runtime/activity.json"] = text.encode()
        projects = {item["project_id"] for item in store.list_projects()}
        with pytest.raises(SessionPackageError, match=match):
            service.import_bytes(_repack(files))
        assert {item["project_id"] for item in store.list_projects()} == projects
    finally:
        store.close()


def test_too_many_imported_cards_fail_closed(tmp_path, monkeypatch):
    from openai4s.server import session_package as module

    store, service, session, exported = _export(tmp_path)
    try:
        monkeypatch.setitem(module._RECORD_LIMITS, "activity_steps", 2)
        with pytest.raises(SessionPackageError, match="too many activity steps"):
            service.import_bytes(exported["data"])
    finally:
        store.close()


# ------------------------------------------------------------------ facts
def test_endpoint_facts_never_carry_the_url():
    local = package_runtime.llm_facts(
        LLMConfig(
            provider="ark",
            base_url="http://127.0.0.1:18931/api/v3",
            model="mock-model",
            api_key="sk-" + "x" * 40,
        )
    )
    assert local["endpoint"]["class"] == "loopback"
    assert local["capabilities"]["local_endpoint"] is True
    # The fact that explains "the model never called a tool": chat() drops
    # native tools for a local endpoint unless an override enables them.
    assert local["capabilities"]["tool_calling"] is False
    relay = package_runtime.llm_facts(
        LLMConfig(
            provider="ark",
            base_url="https://relay.private-lab.example/v1",
            model="glm",
            api_key="k" * 20,
        )
    )
    assert relay["endpoint"]["class"] == "hostname"
    assert relay["endpoint"]["provider_default"] is False
    assert len(relay["endpoint"]["fingerprint"]) == 16
    encoded = json.dumps([local, relay])
    for leaked in ("127.0.0.1", "18931", "relay.private-lab", "sk-", "kkkk"):
        assert leaked not in encoded
    default = package_runtime.llm_facts(LLMConfig(provider="ark", api_key="k" * 20))
    assert default["endpoint"]["provider_default"] is True


def test_an_unregistered_provider_keeps_what_the_configuration_says():
    facts = package_runtime.llm_facts(
        LLMConfig(provider="not-registered", model="m", api_key="k" * 20)
    )
    assert facts["provider"] == "not-registered" and facts["model"] == "m"
    assert facts["timeout_s"] == LLMConfig().timeout_s
    assert facts["resolution"]["status"] == "unavailable"
    assert "endpoint" not in facts and "capabilities" not in facts


def test_an_unresolvable_model_is_recorded_instead_of_failing():
    def pinned_to_a_deleted_profile():
        raise GatewayError(409, "rebind it", "model_revision_unavailable")

    facts = package_runtime.runtime_facts_for(
        Config(), llm_config=pinned_to_a_deleted_profile
    )
    assert facts["llm"] == {
        "status": "unavailable",
        "reason": "model_revision_unavailable",
    }
    assert facts["agent"]["max_turns"] == Config().max_turns


def test_failure_evidence_is_content_free_and_total():
    def inner():
        raise TimeoutError(f"read timed out {CANARY}")

    try:
        try:
            inner()
        except TimeoutError as cause:
            raise StreamTimeoutError(
                f"event stream read error {CANARY} /Users/someone/secret",
                output_committed=True,
            ) from cause
    except StreamTimeoutError as error:
        error.call_telemetry = {"duration_ms": 5, "deltas": 1, "stream": True}
        detail = package_runtime.failure_evidence(error)
    assert detail["chain"] == ["openai4s.llm.models.StreamTimeoutError", "TimeoutError"]
    assert detail["code"] == "llm_stream_timeout"
    assert detail["output_committed"] is True
    assert detail["call"] == {"duration_ms": 5, "deltas": 1, "stream": True}
    assert all(not item.startswith("/") for item in detail["where"])
    encoded = json.dumps(detail)
    assert CANARY not in encoded and "/Users/" not in encoded

    class Hostile(Exception):
        @property
        def __traceback__(self):  # type: ignore[override]
            raise RuntimeError("no")

    assert isinstance(package_runtime.failure_evidence(Hostile()), dict)


def test_bundle_tag_matches_the_support_bundle():
    from openai4s.observability import fingerprint

    assert package_diagnosis.bundle_tag("ffee00112233aabb") == (
        f"<redacted:{fingerprint('ffee00112233aabb')}>"
    )


def test_package_strings_cannot_drive_the_terminal(tmp_path):
    store, service, _session_, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        environment = json.loads(files["runtime/environment.json"])
        environment["openai4s"]["version"] = "0.3.0\x1b]52;c;cHduZWQ=\x07"
        environment["posture"]["egress"] = "\x1b[2J\u202etxt"
        files["runtime/environment.json"] = json.dumps(environment).encode()
        target = tmp_path / "hostile.openai4s-session.zip"
        target.write_bytes(_repack(files))
        page = package_diagnosis.render_markdown(
            package_diagnosis.diagnose_package(target)
        )
        assert "\x1b" not in page and "\x07" not in page and "\u202e" not in page
        assert "OpenAI4S 0.3.0]52;c;cHduZWQ=" in page
    finally:
        store.close()


# -------------------------------------------------------------- telemetry
def test_chat_model_measures_stream_deltas_and_failures():
    def streaming(messages, cfg, *, on_delta=None, **_kwargs):
        for word in ("a", "b", "c"):
            on_delta(word)
        return {"content": "abc", "finish_reason": "stop"}

    reply = ChatModel(LLMConfig(), streaming, stream=True).complete(
        [{"role": "user", "content": "hi"}], lambda _delta: None
    )
    telemetry = reply[CALL_TELEMETRY_KEY]
    assert telemetry["outcome"] == "ok" and telemetry["stream"] is True
    assert telemetry["deltas"] == 3 and telemetry["finish_reason"] == "stop"
    assert telemetry["first_delta_ms"] is not None
    assert telemetry["last_delta_ms"] >= telemetry["first_delta_ms"]

    def failing(messages, cfg, **_kwargs):
        raise StreamTimeoutError("quiet upstream", output_committed=True)

    with pytest.raises(StreamTimeoutError) as caught:
        ChatModel(LLMConfig(), failing).complete(
            [{"role": "user", "content": "hi"}], lambda _delta: None
        )
    assert caught.value.call_telemetry["outcome"] == "error"


def test_ledger_records_call_telemetry_that_replay_and_timeline_ignore(tmp_path):
    store = Store(tmp_path / "openai4s.db")
    try:
        root = store.new_frame(kind="turn", status="ready")
        ledger = RuntimeActionLedger(store, root, "turn-x", provider="ark", model="m")
        ledger.append_user({"role": "user", "content": "go"})
        from openai4s.agent.actions import route_action
        from openai4s.agent.events import ActionRouted, OutcomeProduced, ReplyReceived
        from openai4s.agent.models import ExecutionOutcome

        telemetry = {"outcome": "ok", "duration_ms": 12, "stream": False}
        reply = ModelReply.from_mapping(
            {
                "content": "```python\nx = 1\n```",
                CALL_TELEMETRY_KEY: telemetry,
            }
        )
        action = route_action(reply.content, reply.tool_calls)
        ledger.emit(ReplyReceived(reply, 0))
        ledger.emit(ActionRouted(action, 0))
        ledger.emit(
            OutcomeProduced(
                ExecutionOutcome(
                    history_messages=({"role": "user", "content": "[Observation] ok"},)
                ),
                0,
            )
        )
        groups = store.list_action_groups(root)
        code = [group for group in groups if group["kind"] == "code"][0]
        assert [event["type"] for event in code["events"]] == [
            "proposed",
            "model_call",
            "observation",
        ]
        assert code["events"][1]["result"] == telemetry
        without = [
            {
                **group,
                "events": [e for e in group["events"] if e["type"] != "model_call"],
            }
            for group in groups
        ]
        assert reduce_action_groups(groups) == reduce_action_groups(without)
        timeline = ActionTimelineService(store).get(root)
        assert all(
            event["type"] != "model_call"
            for group in timeline["groups"]
            for event in group["events"]
        )
    finally:
        store.close()


# ------------------------------------------------------------- diagnosis
def test_a_package_from_before_runtime_evidence_still_gets_a_diagnosis(tmp_path):
    store, service, _session_, exported = _export(tmp_path)
    try:
        files = _unpack(exported["data"])
        old = {
            name: data
            for name, data in files.items()
            if not name.startswith("runtime/") and name != "DIAGNOSTICS.md"
        }
        target = tmp_path / "old.openai4s-session.zip"
        target.write_bytes(_repack(old))
        diagnosis = package_diagnosis.diagnose_package(target)
        assert diagnosis["package"]["runtime_evidence"] is False
        assert diagnosis["environment"]["derived_from"] == (
            "artifact_environment_snapshots"
        )
        kinds = [finding["kind"] for finding in diagnosis["findings"]]
        assert "stream_failure" in kinds and "no_progress" in kinds
        assert "host_call_failures" not in kinds
    finally:
        store.close()


def test_inspect_package_cli_verifies_then_diagnoses(tmp_path, capsys):
    from openai4s.cli.main import main

    store, _service_, _session_, exported = _export(tmp_path)
    try:
        target = tmp_path / "session.openai4s-session.zip"
        target.write_bytes(exported["data"])
        assert main(["inspect-package", str(target)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("# Runtime diagnosis") and "llm_stream_timeout" in out
        assert main(["inspect-package", "--json", str(target)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["integrity"]["ok"] is True
        assert payload["diagnosis"]["counts"]["turns"] == 2
        files = _unpack(exported["data"])
        files["REPRODUCE.md"] += b"\ntampered\n"
        tampered = tmp_path / "tampered.zip"
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        assert main(["inspect-package", str(tampered)]) == 1
        assert "does not verify" in capsys.readouterr().out
        (tmp_path / "junk.zip").write_bytes(b"not a zip")
        assert main(["inspect-package", str(tmp_path / "junk.zip")]) == 2
    finally:
        store.close()


# ------------------------------------------------------------------ gateway
class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


@pytest.mark.stubbed_backend
def test_a_stalled_stream_is_recorded_with_its_failure_detail_and_exported(
    monkeypatch, tmp_path
):
    from openai4s.llm.transport import post_sse

    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="ark", api_key="test-key"),
        max_turns=3,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    store = runner.store
    project = store.create_project(name="stall")
    fid = store.new_frame(kind="turn", project_id=project["project_id"], status="ready")

    class Interrupted:
        def __iter__(self):
            yield b'data: {"text":"Partial answer."}\n'
            yield b"\n"
            raise TimeoutError(f"private upstream detail {CANARY}")

        def close(self):
            pass

    def fake_ensure(st):
        if not st.booted:
            st.dispatcher = SimpleNamespace(last_output=None)
            st.messages = [{"role": "system", "content": "sys"}]
            st.booted = True

    def stalled_chat(messages, cfg, on_delta=None, **kwargs):
        post_sse("https://x.invalid", {}, {}, 1, lambda event: on_delta(event["text"]))

    monkeypatch.setattr(
        "openai4s.llm.transport._urlopen", lambda *a, **k: Interrupted()
    )
    monkeypatch.setattr(gateway_mod, "chat", stalled_chat)
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    try:
        result = runner.run_message(fid, project["project_id"], "answer")
        assert result["code"] == "llm_stream_timeout"
        terminal = [
            group
            for group in store.list_action_groups(fid)
            if group["kind"] == "terminal"
        ][-1]
        error = terminal["events"][0]["result"]["error"]
        detail = error["detail"]
        assert detail["code"] == "llm_stream_timeout"
        assert detail["output_committed"] is True
        assert "TimeoutError" in detail["chain"]
        assert any(item.startswith("openai4s/llm/") for item in detail["where"])
        # The failing call's own clock came along from ChatModel.
        assert detail["call"]["outcome"] == "error"
        assert detail["call"]["deltas"] >= 1
        assert CANARY not in json.dumps(terminal)

        exported = runner.export_session_package(fid, project["project_id"])
        files = _unpack(exported["data"])
        environment = json.loads(files["runtime/environment.json"])
        assert environment["llm"]["provider"] == "ark"
        assert environment["llm"]["wire"] == "openai"
        assert environment["llm"]["endpoint"]["provider_default"] is True
        assert environment["agent"]["max_turns"] == 3
        page = files["DIAGNOSTICS.md"].decode()
        assert "turn 1 stopped with llm_stream_timeout: the model call ran" in page
        assert CANARY not in page
    finally:
        runner.close()
