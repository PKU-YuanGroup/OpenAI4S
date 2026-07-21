from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.agent.ledger import restore_action_history
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.execution_views import ExecutionViewService
from openai4s.server.session_domain import SessionDomainService
from openai4s.server.session_package import (
    SessionPackageError,
    session_import_quarantine_key,
)
from openai4s.store import Store


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _repack(files: dict[str, bytes]) -> bytes:
    body = {
        "format": "openai4s.session",
        "schema_version": 1,
        "files": [
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in sorted(files.items())
            if name != "manifest.json"
        ],
    }
    files = dict(files)
    files["manifest.json"] = _canonical(
        {
            **body,
            "manifest_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
        }
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    return output.getvalue()


def _unpack(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _corrupt_first_payload(data: bytes) -> bytes:
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = next(item for item in archive.infolist() if item.file_size)
        offset = info.header_offset
        filename_size, extra_size = struct.unpack_from("<HH", raw, offset + 26)
        payload_offset = offset + 30 + filename_size + extra_size
        raw[payload_offset + max(0, info.compress_size // 2)] ^= 0x01
    return bytes(raw)


def _source(tmp_path: Path):
    store = Store(tmp_path / "openai4s.db")
    project = store.create_project(name="Protein study")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    workspace_root = tmp_path / "workspaces"

    def workspace(root_frame_id, branch_id):
        path = workspace_root / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    source_workspace = workspace(root, root)
    (source_workspace / "analysis.txt").write_text("safe result\n", encoding="utf-8")
    (source_workspace / ".env").write_text(
        "OPENAI_API_KEY=must-not-export\n", encoding="utf-8"
    )
    message = store.add_message(
        root_frame_id=root,
        frame_id=root,
        role="user",
        content="Run the analysis",
    )
    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id="turn-source-user",
        kind="user",
        assistant_message={"role": "user", "content": "Run the analysis"},
    )
    group = store.append_action_group(
        root_frame_id=root,
        turn_id="turn-source",
        kind="cell",
        assistant_content="Running one scientific cell",
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="cell_proposed",
        action_id="action-source",
        canonical_arguments={"language": "python"},
        result={"accepted": True},
    )
    cell = store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
        code="score = 0.93",
        result={
            "id": "cell-source",
            "stdout": "score=0.93\n",
            "stderr": "",
            "error": None,
        },
        cell_index=1,
        state_revision=1,
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id=cell,
        state_revision=1,
        allocated_at=10,
    )
    store.mark_execution_attempt_started(attempt["attempt_id"], started_at=11)
    store.mark_execution_attempt_response(attempt["attempt_id"], response_at=12)
    store.mark_execution_attempt_capture(attempt["attempt_id"], capture_at=13)
    store.finish_execution_attempt(
        attempt["attempt_id"], terminal_state="completed", finished_at=14
    )
    artifact_path = source_workspace / "prediction.csv"
    artifact_path.write_text("id,score\n1,0.93\n", encoding="utf-8")
    artifact = store.save_artifact(
        path=str(artifact_path),
        filename="prediction.csv",
        content_type="text/csv",
        size_bytes=artifact_path.stat().st_size,
        checksum=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    store.create_plan(
        frame_id=root,
        project_id=project["project_id"],
        title="Validate prediction",
        rationale="Preserve the scientific plan",
        confidence="high",
        steps=[{"id": "validate", "title": "Validate"}],
        artifact_id=artifact["artifact_id"],
    )
    store.add_memory(
        project_id=project["project_id"],
        block="project",
        content="Model version is v1",
    )
    store.set_permission_rule(
        scope="conversation",
        scope_id=root,
        tool="web_fetch",
        pattern="https://example.test/*",
        decision="allow",
    )
    store.set_capability_enabled(
        "skill", "protein-analysis", True, scope="session", scope_id=root
    )
    domain = SessionDomainService(
        store,
        data_dir=tmp_path,
        workspace=workspace,
    )
    checkpoint = domain.create_checkpoint(
        root,
        metadata={"source_message_id": message["message_id"]},
    )
    domain.fork_branch(
        root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="source-analysis-branch",
        name="Alternative analysis",
    )
    return store, domain, project, root, artifact, checkpoint, workspace


def test_session_package_is_deterministic_and_round_trips_durable_state(tmp_path):
    store, domain, project, root, artifact, checkpoint, workspace = _source(tmp_path)
    try:
        store.add_step(
            step_id="review-source",
            frame_id=root,
            kind="review",
            title="Evidence review",
            input={"evidence_count": 3},
            status="running",
        )
        store.update_step(
            "review-source",
            status="done",
            output={"verdict": "pass", "summary": "No issues found", "issues": []},
            summary="No issues found",
        )
        store.set_setting(f"review:auto:{root}", "1")
        store.set_setting(f"review:model:{root}", "review-model")
        first = domain.session_export(root)
        second = domain.session_export(root)
        assert first["data"] == second["data"]
        assert first["sha256"] == hashlib.sha256(first["data"]).hexdigest()

        imported = domain.session_import(first["data"])
        assert imported["project_id"] != project["project_id"]
        assert imported["root_frame_id"] != root
        assert imported["view_only"] is True
        assert imported["explicit_recovery_required"] is True
        new_root = imported["root_frame_id"]

        groups = store.list_action_groups(new_root)
        assert any(group["kind"] == "cell" for group in groups)
        assert groups[-1]["kind"] == "session_import"
        cells = store.list_cells(new_root)
        assert len(cells) == 1
        assert cells[0]["code"] == "score = 0.93"
        assert cells[0]["producing_cell_id"] != "cell-source"
        artifacts = store.list_artifacts({"root_frame_id": new_root})
        assert len(artifacts) == 1
        imported_path = store.resolve_artifact_path(artifacts[0]["artifact_id"])
        assert Path(imported_path).read_bytes() == b"id,score\n1,0.93\n"
        imported_workspace = workspace(new_root, imported["active_branch_id"])
        assert (imported_workspace / "analysis.txt").read_text(
            "utf-8"
        ) == "safe result\n"
        assert not (imported_workspace / ".env").exists()
        assert store.list_session_checkpoints(new_root)
        assert len(store.list_session_branches(new_root)) == 2
        generation = store.latest_kernel_generation(
            new_root, "python", branch_id=imported["active_branch_id"]
        )
        assert generation["state"] == "released"
        assert generation["ended_reason"] == "session_package_import_view_only"
        rules = store.get_permission_rules(scope="conversation", scope_id=new_root)
        assert rules[0]["decision"] == "ask"
        assert (
            store.capability_state(session_id=new_root).is_enabled(
                "skill", "protein-analysis"
            )
            is False
        )
        review_steps = store.list_steps(new_root)
        imported_review = next(
            item for item in review_steps if item["kind"] == "review"
        )
        assert imported_review["output"]["verdict"] == "pass"
        imported_settings = next(
            item for item in review_steps if item["kind"] == "review_settings"
        )
        assert imported_settings["input"]["requested_auto_review"] is True
        assert imported_settings["input"]["requested_reviewer_model"] == "review-model"
        assert store.get_setting(f"review:auto:{new_root}") is None
        state_summaries = store.list_checkpoint_state_snapshots(new_root)
        assert len(state_summaries) == 1
        state_snapshot = store.get_checkpoint_state_snapshot(
            state_summaries[0]["checkpoint_id"], include_state=True
        )
        assert state_snapshot["trust_state"] == "quarantined_import"
        assert state_snapshot["state"]["plans"][0]["frame_id"] == new_root
        assert (
            state_snapshot["state"]["plans"][0]["project_id"] == imported["project_id"]
        )
        assert (
            state_snapshot["state"]["plans"][0]["artifact_id"]
            == artifacts[0]["artifact_id"]
        )
        assert state_snapshot["state"]["review"]["settings"]["auto_review"] == {
            "present": True,
            "value": "0",
            "updated_at": state_snapshot["state"]["review"]["settings"]["auto_review"][
                "updated_at"
            ],
        }
        assert (
            state_snapshot["state"]["review"]["settings"]["reviewer_model"]["present"]
            is False
        )
        assert state_snapshot["state"]["memory"]["project_id"] == imported["project_id"]
        snapshot_text = repr(state_snapshot["state"])
        assert root not in snapshot_text
        assert project["project_id"] not in snapshot_text
        assert artifact["artifact_id"] not in snapshot_text
    finally:
        store.close()


def test_session_package_rejects_tamper_traversal_symlink_and_secret_payload(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        exported = domain.session_export(root)["data"]
        files = _unpack(exported)

        tampered = dict(files)
        tampered["notebook.json"] += b" "
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, data in tampered.items():
                archive.writestr(name, data)
        with pytest.raises(SessionPackageError, match="hash mismatch"):
            domain.session_import(output.getvalue())

        corrupt_state = dict(files)
        snapshots = json.loads(corrupt_state["snapshots.json"])
        snapshots["checkpoint_states"][0]["state"]["plans"][0][
            "title"
        ] = "tampered checkpoint plan"
        corrupt_state["snapshots.json"] = _canonical(snapshots)
        with pytest.raises(SessionPackageError, match="checksum mismatch"):
            domain.session_import(_repack(corrupt_state))

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../escape", b"bad")
        with pytest.raises(SessionPackageError, match="unsafe package path"):
            domain.session_import(output.getvalue())

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            info = zipfile.ZipInfo("manifest.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
        with pytest.raises(SessionPackageError, match="symlinks"):
            domain.session_import(output.getvalue())

        secret = dict(files)
        artifact_manifest = json.loads(secret["artifacts.json"])
        artifact_manifest["artifacts"][0]["filename"] = ".env"
        secret["artifacts.json"] = _canonical(artifact_manifest)
        with pytest.raises(SessionPackageError, match="secret or unsafe artifact"):
            domain.session_import(_repack(secret))
    finally:
        store.close()


def test_session_package_filters_provider_secrets_binary_large_and_env_variants(
    tmp_path,
):
    store, domain, _project, root, _artifact, _checkpoint, workspace = _source(tmp_path)
    configured = "custom-secret-without-provider-prefix-123456"
    try:
        store.set_setting("llm_api_key", configured)
        root_workspace = workspace(root, root)
        secret_payloads = {
            ".env.local": b"OPENAI_API_KEY=not-exported\n",
            "provider.txt": b"token is ark-abcdefghijklmnop",
            "bearer.txt": b"Authorization: Bearer abcdefghijklmnop",
            "private.pem.txt": (
                b"-----BEGIN "
                + b"PRIVATE KEY-----\nabc\n-----END "
                + b"PRIVATE KEY-----"
            ),
            "binary.bin": b"\x00\xffprefix ark-qrstuvwxyz012345 suffix",
            "configured.txt": configured.encode("utf-8"),
            "large.txt": b"x" * (4 << 20) + b" Bearer zyxwvutsrqponmlk",
        }
        for name, payload in secret_payloads.items():
            (root_workspace / name).write_bytes(payload)

        files = _unpack(domain.session_export(root)["data"])
        archive_bytes = b"\n".join(files.values())
        for payload in secret_payloads.values():
            marker = payload[-32:] if len(payload) > 32 else payload
            assert marker not in archive_bytes
        snapshot = json.loads(files["snapshots.json"])
        tree_id = snapshot["workspace"]["tree_map"][
            snapshot["workspace"]["active_source_tree_id"]
        ]
        tree = json.loads(files[f"workspace/trees/{tree_id}.json"])
        exported_paths = {entry["path"] for entry in tree["entries"]}
        assert exported_paths.isdisjoint(secret_payloads)
    finally:
        store.close()


def test_session_package_rejects_version_filename_escape_and_corrupt_graph(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        files = _unpack(domain.session_export(root)["data"])

        escaped = dict(files)
        artifact_manifest = json.loads(escaped["artifacts.json"])
        artifact_manifest["artifacts"][0]["versions"][0][
            "filename"
        ] = "../../escaped.txt"
        escaped["artifacts.json"] = _canonical(artifact_manifest)
        with pytest.raises(SessionPackageError, match="unsafe artifact filename"):
            domain.session_import(_repack(escaped))

        missing_head = dict(files)
        session = json.loads(missing_head["session.json"])
        snapshots = json.loads(missing_head["snapshots.json"])
        child = next(
            branch
            for branch in snapshots["branches"]
            if branch["branch_id"] != session["source"]["root_frame_id"]
        )
        session["source"]["active_branch_id"] = child["branch_id"]
        snapshots["workspace"]["active_branch_id"] = child["branch_id"]
        child["head_checkpoint_id"] = None
        missing_head["session.json"] = _canonical(session)
        missing_head["snapshots.json"] = _canonical(snapshots)
        with pytest.raises(SessionPackageError, match="child branch head"):
            domain.session_import(_repack(missing_head))

        mismatched_workspace = dict(files)
        snapshots = json.loads(mismatched_workspace["snapshots.json"])
        snapshots["workspace"]["active_branch_id"] = child["branch_id"]
        mismatched_workspace["snapshots.json"] = _canonical(snapshots)
        with pytest.raises(SessionPackageError, match="active branch does not match"):
            domain.session_import(_repack(mismatched_workspace))
    finally:
        store.close()


def test_session_package_round_trips_a_valid_active_child_branch(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        child = store.get_session_branch("source-analysis-branch")
        store.activate_session_branch_checkpoint(
            root_frame_id=root,
            branch_id=child["branch_id"],
            checkpoint_id=child["head_checkpoint_id"],
            expected_current_branch_id=root,
        )
        store.add_message(
            root_frame_id=root,
            branch_id=child["branch_id"],
            frame_id=root,
            role="user",
            content="Continue only on the alternative branch",
        )
        package = domain.session_export(root)["data"]
        exported_session = json.loads(_unpack(package)["session.json"])
        assert exported_session["messages"][-1]["branch_id"] == child["branch_id"]
        imported = domain.session_import(package)
        assert imported["active_branch_id"] != imported["root_frame_id"]
        assert (
            store.active_session_branch(imported["root_frame_id"])
            == imported["active_branch_id"]
        )
        imported_branch = store.get_session_branch(imported["active_branch_id"])
        assert imported_branch["head_checkpoint_id"]
        local_messages = store.list_messages(
            imported["root_frame_id"], branch_id=imported["active_branch_id"]
        )
        assert [item["content"] for item in local_messages] == [
            "Continue only on the alternative branch"
        ]
        projected = store.list_branch_messages(
            imported["root_frame_id"], branch_id=imported["active_branch_id"]
        )
        assert [item["content"] for item in projected] == [
            "Run the analysis",
            "Continue only on the alternative branch",
        ]
    finally:
        store.close()


def test_session_package_accepts_legacy_checkpoint_without_domain_state(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        files = _unpack(domain.session_export(root)["data"])
        snapshots = json.loads(files["snapshots.json"])
        snapshots.pop("checkpoint_states", None)
        files["snapshots.json"] = _canonical(snapshots)

        imported = domain.session_import(_repack(files))

        assert store.list_checkpoint_state_snapshots(imported["root_frame_id"]) == []
        checkpoint = store.list_session_checkpoints(imported["root_frame_id"])[0]
        projection = store.restore_checkpoint_state_snapshot(
            checkpoint_id=checkpoint["checkpoint_id"],
            root_frame_id=imported["root_frame_id"],
            project_id=imported["project_id"],
        )
        assert projection["partial"] is True
        assert projection["plans"]["preserved_live_state"] is True
    finally:
        store.close()


def test_session_package_preserves_revert_projection_without_reviving_abandoned_rows(
    tmp_path,
):
    store, domain, project, root, _artifact, first, _workspace = _source(tmp_path)
    try:
        store.add_message(
            root_frame_id=root,
            branch_id=root,
            frame_id=root,
            role="user",
            content="abandoned middle",
        )
        store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-abandoned",
            kind="user",
            assistant_message={"role": "user", "content": "abandoned middle"},
        )
        store.log_cell(
            frame_id=root,
            root_frame_id=root,
            project_id=project["project_id"],
            code="abandoned_value = 2",
            result={"id": "cell-abandoned", "stdout": "", "stderr": ""},
            cell_index=2,
            state_revision=2,
        )
        domain.create_checkpoint(root, reason="abandoned checkpoint")
        reverted = domain.revert_apply(
            root, target_checkpoint_id=first["checkpoint_id"]
        )
        assert reverted["ok"] is True

        store.add_message(
            root_frame_id=root,
            branch_id=root,
            frame_id=root,
            role="user",
            content="continued after revert",
        )
        store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-continued",
            kind="user",
            assistant_message={
                "role": "user",
                "content": "continued after revert",
            },
        )
        store.log_cell(
            frame_id=root,
            root_frame_id=root,
            project_id=project["project_id"],
            code="continued_value = 3",
            result={"id": "cell-continued", "stdout": "", "stderr": ""},
            cell_index=3,
            state_revision=3,
        )
        domain.create_checkpoint(root, reason="continued checkpoint")

        package = domain.session_export(root)["data"]
        imported = domain.session_import(package)
        new_root = imported["root_frame_id"]
        assert [
            item["content"]
            for item in store.list_branch_messages(
                new_root, branch_id=new_root, limit=None
            )
        ] == ["Run the analysis", "continued after revert"]
        provider_users = [
            item["content"]
            for item in restore_action_history(store, new_root, branch_id=new_root)
            if item.get("role") == "user"
        ]
        assert provider_users == ["Run the analysis", "continued after revert"]
        execution = ExecutionViewService(
            store=store, format_timestamp=lambda value: str(value)
        ).execution_log(new_root)
        assert [item["source"] for item in execution["entries"]] == [
            "score = 0.93",
            "continued_value = 3",
        ]

        projection_checkpoint = next(
            item
            for item in store.list_session_checkpoints(new_root)
            if (item.get("metadata") or {}).get("history_projection")
        )
        metadata = projection_checkpoint["metadata"]
        projection = metadata["history_projection"]
        assert store.get_session_checkpoint(projection["base_checkpoint_id"])
        assert store.get_session_checkpoint(metadata["reverted_to"])
        assert store.get_session_checkpoint(metadata["undo_checkpoint_id"])
        assert projection["resume_cursors"]["cell_cursor"] == 2

        tampered_files = _unpack(package)
        tampered_snapshots = json.loads(tampered_files["snapshots.json"])
        tampered_projection = next(
            item["metadata"]["history_projection"]
            for item in tampered_snapshots["checkpoints"]
            if (item.get("metadata") or {}).get("history_projection")
        )
        tampered_projection["base_checkpoint_id"] = "cp-outside-package"
        tampered_files["snapshots.json"] = _canonical(tampered_snapshots)
        with pytest.raises(SessionPackageError, match="unknown identity"):
            domain.session_import(_repack(tampered_files))
    finally:
        store.close()


def test_session_package_preserves_complete_provider_tool_group_with_new_ids(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        declaration = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-source-status",
                    "wire_id": "wire-source-status",
                    "name": "session_status",
                    "ordinal": 0,
                    "raw_arguments": "{}",
                    "arguments": {},
                    "parse_error": None,
                    "provider_meta": {},
                }
            ],
        }
        group = store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-source-tool",
            kind="native_tools",
            provider="openai",
            model="test-model",
            wire_state={"last_call": "wire-source-status"},
            assistant_message=declaration,
            usage={"input_tokens": 10, "output_tokens": 2},
            cost_usd=0.01,
        )
        store.append_action_event(
            group_id=group["group_id"],
            type="result",
            action_id="action-source-status",
            tool_call_id="call-source-status",
            wire_id="wire-source-status",
            canonical_arguments={},
            raw_arguments="{}",
            result={"content": "session is ready", "is_error": False},
        )
        domain.create_checkpoint(root, reason="provider tool group")

        imported = domain.session_import(domain.session_export(root)["data"])
        new_root = imported["root_frame_id"]
        history = restore_action_history(store, new_root, branch_id=new_root)
        assistant, result = history[-2:]
        imported_call = assistant["tool_calls"][0]
        assert assistant["role"] == "assistant"
        assert result["role"] == "tool"
        assert imported_call["id"] != "call-source-status"
        assert imported_call["wire_id"] != "wire-source-status"
        assert result["tool_call_id"] == imported_call["id"]
        assert result["wire_id"] == imported_call["wire_id"]
        imported_group = next(
            item
            for item in store.list_action_groups(new_root, branch_id=new_root)
            if item["kind"] == "native_tools"
        )
        assert imported_group["wire_state"]["last_call"] == imported_call["wire_id"]
        assert imported_group["usage"] == {"input_tokens": 10, "output_tokens": 2}
        assert imported_group["cost_usd"] == 0.01
    finally:
        store.close()


@pytest.mark.parametrize("corruption", ["duplicate_cell", "dangling_attempt"])
def test_session_package_rejects_duplicate_and_dangling_identities(
    tmp_path, corruption
):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        files = _unpack(domain.session_export(root)["data"])
        if corruption == "duplicate_cell":
            notebook = json.loads(files["notebook.json"])
            notebook["cells"].append(dict(notebook["cells"][0]))
            files["notebook.json"] = _canonical(notebook)
        else:
            ledger = json.loads(files["ledger.json"])
            ledger["execution_attempts"][0]["producing_cell_id"] = "missing-cell"
            files["ledger.json"] = _canonical(ledger)
        with pytest.raises(SessionPackageError, match="identity|unknown"):
            domain.session_import(_repack(files))
    finally:
        store.close()


def test_session_package_maps_crc_compression_and_ratio_failures_to_validation(
    tmp_path,
):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        exported = domain.session_export(root)["data"]
        with pytest.raises(SessionPackageError, match="corrupt"):
            domain.session_import(_corrupt_first_payload(exported))

        unsupported = io.BytesIO()
        with zipfile.ZipFile(
            unsupported, "w", compression=zipfile.ZIP_BZIP2
        ) as archive:
            archive.writestr("manifest.json", b"{}")
        with pytest.raises(SessionPackageError, match="compression method"):
            domain.session_import(unsupported.getvalue())

        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bomb.bin", b"A" * (1 << 20))
        with pytest.raises(SessionPackageError, match="compression ratio"):
            domain.session_import(bomb.getvalue())
    finally:
        store.close()


def test_session_package_quarantines_replay_hooks_and_unicode_allow(tmp_path):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        sentinel = tmp_path / "should-not-run"
        files = _unpack(domain.session_export(root)["data"])
        permissions = json.loads(files["permissions.json"])
        permissions["conversation"][0]["decision"] = "ＡＬＬＯＷ"
        files["permissions.json"] = _canonical(permissions)

        environment = json.loads(files["environment.json"])
        environment["generations"].append(
            {
                "generation_id": "malicious-generation",
                "root_frame_id": root,
                "branch_id": root,
                "language": "python",
                "ordinal": 1,
                "environment": {"interpreter": "/tmp/untrusted-python"},
                "bootstrap": {
                    "sidecars": [{"name": "evil", "code": "raise SystemExit"}],
                    "init_hooks": [f"open({str(sentinel)!r}, 'w')"],
                },
            }
        )
        files["environment.json"] = _canonical(environment)

        notebook = json.loads(files["notebook.json"])
        notebook["cells"][0]["replay_policy"] = "safe"
        files["notebook.json"] = _canonical(notebook)
        snapshots = json.loads(files["snapshots.json"])
        snapshots["checkpoints"][0]["recovery_recipe"] = {
            "status": "complete",
            "steps": [
                {
                    "kind": "replay_cell",
                    "replay_policy": "safe",
                    "payload": {"code": f"open({str(sentinel)!r}, 'w')"},
                }
            ],
        }
        files["snapshots.json"] = _canonical(snapshots)

        imported = domain.session_import(_repack(files))
        new_root = imported["root_frame_id"]
        rule = store.get_permission_rules(scope="conversation", scope_id=new_root)[0]
        assert rule["decision"] == "ask"
        imported_cell = store.cell_detail(
            store.list_cells(new_root)[0]["producing_cell_id"]
        )
        assert imported_cell["replay_policy"] == "never"
        checkpoint = store.list_session_checkpoints(new_root, limit=10)[0]
        assert checkpoint["recovery_recipe"]["status"] == "quarantined_import"
        assert checkpoint["recovery_recipe"]["steps"] == []
        historical = next(
            item
            for item in store.list_kernel_generations(new_root)
            if item["ended_reason"] == "imported_historical_generation"
        )
        assert historical["bootstrap"]["sidecars"] == []
        assert historical["bootstrap"]["init_hooks"] == []
        assert historical["bootstrap"]["trusted"] is False
        assert not sentinel.exists()
    finally:
        store.close()


def test_confirmed_fresh_restart_is_the_only_quarantine_unlock(tmp_path, monkeypatch):
    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    project = runner.store.create_project(name="Fresh restart source")
    root = runner.store.new_frame(
        project_id=project["project_id"], kind="turn", status="done"
    )
    imported = runner.session_domain.session_import(
        runner.session_domain.session_export(root)["data"]
    )
    imported_root = imported["root_frame_id"]
    imported_project = imported["project_id"]

    class FakeRecoveryRuntime:
        def fresh_manifests(self):
            return (SimpleNamespace(language="python"),)

        def run(self, _plan):
            return {"ok": True, "status": "active", "recovery_id": "fresh"}

        def kernel_status_event(self, result, recovery_id):
            return {
                "type": "kernel_status",
                "frame_id": imported_root,
                "status": result["status"],
                "recovery_id": recovery_id,
            }

    monkeypatch.setattr(
        runner,
        "_recovery_runtime",
        lambda _state, _emit: FakeRecoveryRuntime(),
    )
    try:
        with pytest.raises(gateway_mod.RecoveryActionError):
            runner.execute_recovery_action(
                imported_root,
                imported_project,
                "restart_fresh",
                confirmed=False,
            )
        with pytest.raises(gateway_mod.RecoveryActionError):
            runner.execute_recovery_action(
                imported_root,
                imported_project,
                "restore",
                confirmed=True,
            )
        assert runner.import_quarantine(imported_root)

        result = runner.execute_recovery_action(
            imported_root,
            imported_project,
            "restart_fresh",
            confirmed=True,
        )
        assert result["quarantine_cleared"] is True
        assert result["trust_state"] == "trusted"
        assert runner.import_quarantine(imported_root) is None
        trust_group = runner.store.list_action_groups(imported_root)[-1]
        assert trust_group["kind"] == "session_import_trust"
    finally:
        runner.close()


@pytest.mark.parametrize(
    ("method_name", "record_name", "limit"),
    [
        ("list_snapshot_operations", "operations", 25_000),
        ("list_recovery_events", "recovery journal", 100_000),
        ("list_plans", "plans", 5_000),
    ],
)
def test_session_package_export_refuses_silently_truncated_history(
    tmp_path, monkeypatch, method_name, record_name, limit
):
    store, domain, _project, root, _artifact, _checkpoint, _workspace = _source(
        tmp_path
    )
    try:
        monkeypatch.setattr(
            store,
            method_name,
            lambda *_args, **_kwargs: [{}] * (limit + 1),
        )
        with pytest.raises(SessionPackageError, match=f"too many {record_name}"):
            domain.session_export(root)
    finally:
        store.close()


@pytest.mark.parametrize(
    "failure_hook", ["_import_artifacts", "_import_plans_review_memory"]
)
def test_session_package_import_fault_rolls_back_database_workspace_env_and_cas(
    tmp_path, monkeypatch, failure_hook
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (
        source_store,
        source_domain,
        source_project,
        root,
        artifact,
        _checkpoint,
        _workspace,
    ) = _source(source_dir)
    target_store = None
    try:
        env_id = source_store.upsert_env_snapshot(
            {"kind": "python", "packages": [], "package_count": 0}
        )
        metadata = source_store.version_meta(artifact["version_id"])
        source_store.save_artifact(
            path=metadata["path"],
            filename=metadata["filename"],
            content_type=metadata["content_type"],
            size_bytes=metadata["size_bytes"],
            checksum=metadata["checksum"],
            producing_cell_id=metadata["producing_cell_id"],
            frame_id=root,
            root_frame_id=root,
            project_id=source_project["project_id"],
            artifact_id=artifact["artifact_id"],
            env_snapshot_id=env_id,
        )
        package = source_domain.session_export(root)["data"]

        target_dir = tmp_path / "target"
        target_store = Store(target_dir / "openai4s.db")
        workspace_root = target_dir / "workspaces"

        def workspace(root_frame_id, branch_id):
            path = workspace_root / root_frame_id / branch_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        target_domain = SessionDomainService(
            target_store,
            data_dir=target_dir,
            workspace=workspace,
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError("fault injection")

        monkeypatch.setattr(target_domain.packages, failure_hook, fail)
        with pytest.raises(RuntimeError, match="fault injection"):
            target_domain.session_import(package)

        assert target_store.list_projects() == []
        assert (
            target_store._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0
        )
        assert (
            target_store._conn.execute("SELECT COUNT(*) FROM env_snapshots").fetchone()[
                0
            ]
            == 0
        )
        assert not any(path.is_file() for path in workspace_root.rglob("*"))
        assert not any(path.is_file() for path in target_domain.cas.root.rglob("*"))
        assert not any(
            path.is_file() for path in (target_dir / "session-imports").rglob("*")
        )
    finally:
        source_store.close()
        if target_store is not None:
            target_store.close()


class _Hub:
    def emitter(self, _root_frame_id):
        return lambda _event: None

    def broadcast(self, _root_frame_id, _event):
        return None

    def drop_frame(self, _root_frame_id):
        return None


def test_session_package_gateway_routes_use_binary_export_and_raw_import(tmp_path):
    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    project = runner.store.create_project(name="Route source")
    root = runner.store.new_frame(
        project_id=project["project_id"], kind="turn", status="done"
    )
    artifact_path = runner.active_workspace_for(root) / "route.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("route artifact\n", encoding="utf-8")
    runner.store.save_artifact(
        path=str(artifact_path),
        filename=artifact_path.name,
        content_type="text/plain",
        size_bytes=artifact_path.stat().st_size,
        checksum=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    handler_class = gateway_mod.make_handler(config, runner.hub, runner)
    handler = object.__new__(handler_class)
    replies = []
    handler._query = lambda: {}
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._json = lambda value, code=200: replies.append((code, value))
    try:
        handler._api("GET", f"/frames/{root}/session/export")
        code, data, content_type, headers = replies.pop()
        assert code == 200
        assert content_type == "application/vnd.openai4s.session+zip"
        assert headers["X-Content-SHA256"] == hashlib.sha256(data).hexdigest()

        handler._body_bytes = lambda **_kwargs: data
        handler._api("POST", "/sessions/import")
        code, imported = replies.pop()
        assert code == 201
        assert imported["root_frame_id"] != root
        assert imported["kernel_state"] == "ended"
        imported_root = imported["root_frame_id"]
        assert imported["trust_state"] == "quarantined"
        assert imported_root not in runner._sessions
        kernel = runner.kernel_status(imported_root)
        assert kernel["view_only"] is True
        assert kernel["trust_state"] == "quarantined"

        handler._body = lambda: {"request": "must stay blocked"}
        for route in (
            f"/frames/{imported_root}/message",
            f"/frames/{imported_root}/kernel/execute",
            f"/frames/{imported_root}/branches/checkpoints",
        ):
            with pytest.raises(gateway_mod.GatewayError) as blocked:
                handler._api("POST", route)
            assert blocked.value.code == 423

        imported_artifact = runner.store.list_artifacts(
            {"root_frame_id": imported_root}
        )[0]
        handler._body = lambda: {"content": "must not write"}
        with pytest.raises(gateway_mod.GatewayError) as blocked_artifact:
            handler._api("POST", f"/artifacts/{imported_artifact['artifact_id']}/edit")
        assert blocked_artifact.value.code == 423
        assert (
            Path(
                runner.store.resolve_artifact_path(imported_artifact["artifact_id"])
            ).read_text("utf-8")
            == "route artifact\n"
        )

        handler._api("GET", f"/frames/{imported_root}/session/export")
        assert replies.pop()[0] == 200

        import_staging = tmp_path / "session-imports" / imported_root
        assert import_staging.is_dir()
        handler._api("DELETE", f"/frames/{imported_root}")
        assert replies.pop()[1] == {"ok": True}
        assert (
            runner.store.get_setting(session_import_quarantine_key(imported_root))
            is None
        )
        assert not import_staging.exists()
    finally:
        runner.close()


def test_a_real_export_carries_reproduction_notes_and_still_verifies(tmp_path):
    """The package had per-file hashes and a verifier, but nothing telling the
    recipient the command exists — and reproduction notes are what the
    proposal asks for alongside the manifest.

    Driven through the real exporter rather than a synthetic archive, because
    the risk this pins is an ordering one: `REPRODUCE.md` has to join the file
    set *before* the manifest is computed, or the verifier rejects it as a file
    the manifest does not list.
    """
    import hashlib as _hashlib
    import hashlib as _hashlib_top
    import zipfile as _zipfile

    from openai4s.evidence import verify_package

    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    project = runner.store.create_project(name="Reproduction source")
    root = runner.store.new_frame(
        project_id=project["project_id"], kind="turn", status="done"
    )
    artifact_path = runner.active_workspace_for(root) / "result.csv"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("score\n0.93\n", encoding="utf-8")
    runner.store.save_artifact(
        path=str(artifact_path),
        filename=artifact_path.name,
        content_type="text/csv",
        size_bytes=artifact_path.stat().st_size,
        checksum=_hashlib_top.sha256(artifact_path.read_bytes()).hexdigest(),
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    handler_class = gateway_mod.make_handler(config, runner.hub, runner)
    handler = object.__new__(handler_class)
    replies = []
    handler._query = lambda: {}
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._json = lambda value, code=200: replies.append((code, value))
    try:
        handler._api("GET", f"/frames/{root}/session/export")
        code, data, _content_type, _headers = replies.pop()
        assert code == 200

        package = tmp_path / "exported.openai4s-session.zip"
        package.write_bytes(data)

        with _zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            notes = archive.read("REPRODUCE.md").decode("utf-8")
            manifest = json.loads(archive.read("manifest.json"))

        assert "REPRODUCE.md" in names
        listed = {entry["path"]: entry for entry in manifest["files"]}
        assert (
            "REPRODUCE.md" in listed
        ), "an unlisted member makes the whole package fail verification"
        assert (
            listed["REPRODUCE.md"]["sha256"]
            == _hashlib.sha256(notes.encode("utf-8")).hexdigest()
        )

        report = verify_package(package)
        assert report["ok"], report["problems"]

        # The notes have to carry the command and the honest limit of what
        # verification proves, or they are decoration.
        assert "openai4s verify-package" in notes
        assert "does not establish who produced" in notes
        assert "environment.json" in notes
    finally:
        runner.close()


def test_the_verify_route_answers_without_importing(tmp_path):
    """Verification has to be reachable before import, not only after: the
    recipient's question is whether to admit this archive to their database at
    all, and answering it afterwards is too late.

    It was CLI-only, so anyone working in the browser had no way to check what
    they had been handed.
    """
    import zipfile as _zipfile

    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    project = runner.store.create_project(name="Verify source")
    root = runner.store.new_frame(
        project_id=project["project_id"], kind="turn", status="done"
    )
    handler_class = gateway_mod.make_handler(config, runner.hub, runner)
    handler = object.__new__(handler_class)
    replies = []
    handler._query = lambda: {}
    handler._send = lambda code, data, content_type, extra=None: replies.append(
        (code, data, content_type, extra or {})
    )
    handler._json = lambda value, code=200: replies.append((code, value))
    try:
        handler._api("GET", f"/frames/{root}/session/export")
        _code, data, _ct, _h = replies.pop()

        handler._body_bytes = lambda **_kwargs: data
        handler._api("POST", "/sessions/verify")
        code, report = replies.pop()
        assert code == 200
        assert report["ok"] is True
        assert report["files_verified"]
        # The route must be honest about the limit of what it proves.
        assert "does not establish" in report["verifies"]

        # Nothing was admitted: verification is a read, not an import.
        assert len(runner.store.list_projects()) == 1

        tampered_path = tmp_path / "tampered.zip"
        with _zipfile.ZipFile(tmp_path / "src.zip", "w") as _seed:
            pass
        with _zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = {n: archive.read(n) for n in archive.namelist()}
        members["notebook.json"] = members["notebook.json"] + b" "
        with _zipfile.ZipFile(tampered_path, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)

        handler._body_bytes = lambda **_kwargs: tampered_path.read_bytes()
        handler._api("POST", "/sessions/verify")
        code, bad = replies.pop()
        assert code == 200
        assert bad["ok"] is False
        assert any("notebook.json" in p for p in bad["problems"])
    finally:
        runner.close()
