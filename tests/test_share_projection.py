from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from openai4s.server.session_domain import SessionDomainService
from openai4s.server.session_package import SessionPackageError
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.store import Store


def _workspace_factory(tmp_path: Path):
    root_dir = tmp_path / "workspaces"

    def workspace(root_frame_id, branch_id):
        path = root_dir / root_frame_id / branch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    return workspace


def _base_session(tmp_path: Path):
    store = Store(tmp_path / "openai4s.db")
    workspace = _workspace_factory(tmp_path)
    domain = SessionDomainService(store, data_dir=tmp_path, workspace=workspace)
    project = store.create_project(name="Protein study")
    root = store.new_frame(project_id=project["project_id"], kind="turn", status="done")
    ws = workspace(root, root)
    (ws / "shared.txt").write_text("safe workspace file\n", encoding="utf-8")
    return store, domain, workspace, project, root


def _add_turn(store, root, project_id, *, user_text, assistant_text, code, stdout):
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content=user_text,
    )
    store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id=f"turn-user-{user_text[:6]}",
        kind="user",
        assistant_message={"role": "user", "content": user_text},
    )
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="assistant",
        content=assistant_text,
    )
    group = store.append_action_group(
        root_frame_id=root,
        branch_id=root,
        turn_id=f"turn-cell-{code[:6]}",
        kind="native_tools",
        assistant_content=assistant_text,
        assistant_message={"role": "assistant", "content": assistant_text},
    )
    store.append_action_event(
        group_id=group["group_id"],
        type="result",
        action_id=f"a-{code[:6]}",
        canonical_arguments={"language": "python"},
        result={"accepted": True},
    )
    return store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project_id,
        code=code,
        result={
            "id": f"cell-{code[:6]}",
            "stdout": stdout,
            "stderr": "",
            "error": None,
        },
        cell_index=1,
        state_revision=1,
        visibility="scientific",
    )


def _unpack(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _builder(store, domain, workspace, tmp_path, *, extra_secret_values=None):
    return ShareProjectionBuilder(
        store,
        data_dir=tmp_path,
        workspace=workspace,
        cas=domain.cas,
        extra_secret_values=extra_secret_values,
    )


# --------------------------------------------------------------------------- #
#  round-trip: a flattened share bundle imports cleanly (== closure proof)
# --------------------------------------------------------------------------- #
def test_flattened_bundle_round_trips_through_import(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    cell = _add_turn(
        store,
        root,
        project["project_id"],
        user_text="run analysis",
        assistant_text="done",
        code="score = 0.93",
        stdout="score=0.93\n",
    )
    art_path = workspace(root, root) / "prediction.csv"
    art_path.write_text("id,score\n1,0.93\n", encoding="utf-8")
    store.save_artifact(
        path=str(art_path),
        filename="prediction.csv",
        content_type="text/csv",
        size_bytes=art_path.stat().st_size,
        checksum=hashlib.sha256(art_path.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    # Project-level memory / policy that must NEVER leave the machine.
    store.add_memory(
        project_id=project["project_id"], block="project", content="model is v1"
    )
    store.set_permission_rule(
        scope="conversation",
        scope_id=root,
        tool="web_fetch",
        pattern="https://example.test/*",
        decision="allow",
    )
    store.set_capability_enabled("skill", "x", True, scope="session", scope_id=root)

    builder = _builder(store, domain, workspace, tmp_path)
    proj = builder.build(root, store.active_session_branch(root))
    bundle = builder.serialize_package(proj)
    assert bundle["projection_id"] == proj.projection_id

    imported = domain.session_import(bundle["data"])
    assert imported["view_only"] is True
    new_root = imported["root_frame_id"]

    # flattened onto a single synthetic root branch, zero checkpoints
    assert len(store.list_session_branches(new_root)) == 1
    assert store.list_session_checkpoints(new_root) == []

    cells = store.list_cells(new_root)
    assert [c["code"] for c in cells] == ["score = 0.93"]
    artifacts = store.list_artifacts({"root_frame_id": new_root})
    assert len(artifacts) == 1
    assert (
        Path(store.resolve_artifact_path(artifacts[0]["artifact_id"])).read_bytes()
        == b"id,score\n1,0.93\n"
    )

    # privacy: memories/permissions/capabilities never cross the boundary
    assert store.list_memories(project_id=imported["project_id"]) == []
    assert store.get_permission_rules(scope="conversation", scope_id=new_root) == []
    imported_caps = [
        c
        for c in store.list_explicit_capability_states()
        if c.get("scope") == "session" and c.get("scope_id") == new_root
    ]
    assert imported_caps == []


def test_flattened_bundle_is_deterministic(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    _add_turn(
        store,
        root,
        project["project_id"],
        user_text="hi",
        assistant_text="ok",
        code="x = 1",
        stdout="",
    )
    builder = _builder(store, domain, workspace, tmp_path)
    a = builder.serialize_package(builder.build(root, root))
    b = builder.serialize_package(builder.build(root, root))
    assert a["data"] == b["data"]
    assert a["sha256"] == hashlib.sha256(a["data"]).hexdigest()


# --------------------------------------------------------------------------- #
#  no dangling references inside the bundle
# --------------------------------------------------------------------------- #
def test_bundle_has_no_dangling_references(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    cell = _add_turn(
        store,
        root,
        project["project_id"],
        user_text="q",
        assistant_text="a",
        code="y = 2",
        stdout="ok\n",
    )
    art_path = workspace(root, root) / "out.csv"
    art_path.write_text("a,b\n1,2\n", encoding="utf-8")
    store.save_artifact(
        path=str(art_path),
        filename="out.csv",
        content_type="text/csv",
        size_bytes=art_path.stat().st_size,
        checksum=hashlib.sha256(art_path.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    builder = _builder(store, domain, workspace, tmp_path)
    files = _unpack(builder.serialize_package(builder.build(root, root))["data"])

    notebook = json.loads(files["notebook.json"])
    cell_ids = {c["producing_cell_id"] for c in notebook["cells"]}
    artifacts = json.loads(files["artifacts.json"])["artifacts"]
    version_ids = {v["version_id"] for a in artifacts for v in a["versions"]}
    available = {
        v["version_id"] for a in artifacts for v in a["versions"] if v["available"]
    }
    for a in artifacts:
        assert a["latest_version_id"] in available
        for v in a["versions"]:
            assert v["producing_cell_id"] in cell_ids or v["producing_cell_id"] is None
            if v["available"]:
                assert f"artifact-data/{v['snapshot_sha256']}" in files
    for edge in json.loads(files["lineage.json"])["edges"]:
        assert edge["input_version_id"] in version_ids
        assert edge["output_version_id"] in version_ids
    branches = json.loads(files["snapshots.json"])["branches"]
    assert len(branches) == 1 and branches[0]["parent_branch_id"] is None


# --------------------------------------------------------------------------- #
#  revert-to-sibling: flattening must pull the sibling logical prefix
# --------------------------------------------------------------------------- #
def test_revert_to_sibling_checkpoint_is_flattened(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        content="main question",
    )
    c1 = domain.create_checkpoint(root)
    domain.fork_branch(
        root, from_checkpoint_id=c1["checkpoint_id"], branch_id="sib", name="sibling"
    )
    store.add_message(
        root_frame_id=root,
        branch_id="sib",
        frame_id=root,
        role="assistant",
        content="SIBLING ONLY answer",
    )
    c2 = domain.create_checkpoint(root, branch_id="sib")
    result = domain.revert_apply(root, target_checkpoint_id=c2["checkpoint_id"])
    assert result["ok"] is True

    active = store.active_session_branch(root)
    builder = _builder(store, domain, workspace, tmp_path)
    proj = builder.build(root, active)
    # the sibling-branch message is in the flattened logical history
    assert any("SIBLING ONLY answer" in str(m["content"]) for m in proj.messages)
    # and the bundle still imports (closure holds despite the cross-branch revert)
    imported = domain.session_import(builder.serialize_package(proj)["data"])
    new_root = imported["root_frame_id"]
    contents = [
        m["content"]
        for m in store.list_branch_message_boundaries(
            new_root, branch_id=imported["active_branch_id"], limit=None
        )
    ]
    assert any("SIBLING ONLY answer" in str(c) for c in contents)
    assert len(store.list_session_branches(new_root)) == 1


# --------------------------------------------------------------------------- #
#  secret handling: redaction vs fail-closed vs workspace filtering
# --------------------------------------------------------------------------- #
def test_secret_shaped_text_is_redacted_not_leaked(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    store.add_message(
        root_frame_id=root,
        branch_id=root,
        frame_id=root,
        role="user",
        # A deliberately fake, secret-SHAPED string to prove _safe_text redacts
        # it — not a real credential (gitleaks:allow).
        content="my api_key=sk-ABCDEFGH1234567890 keep it safe",  # gitleaks:allow
    )
    builder = _builder(store, domain, workspace, tmp_path)
    files = _unpack(builder.serialize_package(builder.build(root, root))["data"])
    session = files["session.json"].decode("utf-8")
    assert "sk-ABCDEFGH1234567890" not in session
    assert "[REDACTED]" in session


def test_residual_known_secret_bytes_fail_closed(tmp_path):
    # conftest sets OPENAI4S_LLM_API_KEY=test-key, so "test-key" is a configured
    # secret whose raw bytes must never leave — even though it is not a
    # secret-SHAPED string that _safe_text would redact.
    store, domain, workspace, project, root = _base_session(tmp_path)
    _add_turn(
        store,
        root,
        project["project_id"],
        user_text="q",
        assistant_text="a",
        code="print('x')",
        stdout="leaked test-key here\n",
    )
    builder = _builder(store, domain, workspace, tmp_path)
    with pytest.raises(SessionPackageError):
        builder.serialize_package(builder.build(root, root))


def test_extra_secret_value_is_caught(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    _add_turn(
        store,
        root,
        project["project_id"],
        user_text="q",
        assistant_text="a",
        code="print('x')",
        stdout="token=RELAYSECRETXYZ0\n",
    )
    builder = _builder(
        store,
        domain,
        workspace,
        tmp_path,
        extra_secret_values=lambda: ("RELAYSECRETXYZ0",),
    )
    with pytest.raises(SessionPackageError):
        builder.serialize_package(builder.build(root, root))


def test_secret_workspace_file_is_filtered(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    (workspace(root, root) / ".env").write_text(
        "SOME_API_KEY=zzzzzzzzzzzz\n", encoding="utf-8"
    )
    _add_turn(
        store,
        root,
        project["project_id"],
        user_text="q",
        assistant_text="a",
        code="z = 3",
        stdout="",
    )
    builder = _builder(store, domain, workspace, tmp_path)
    data = builder.serialize_package(builder.build(root, root))["data"]
    assert b"zzzzzzzzzzzz" not in data


# --------------------------------------------------------------------------- #
#  view document
# --------------------------------------------------------------------------- #
def test_view_matches_projection_and_hides_nonscientific(tmp_path):
    store, domain, workspace, project, root = _base_session(tmp_path)
    cell = _add_turn(
        store,
        root,
        project["project_id"],
        user_text="run",
        assistant_text="here",
        code="plot()",
        stdout="rendered\n",
    )
    store.log_cell(
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
        code="internal = 1",
        result={"id": "scratch", "stdout": "", "stderr": "", "error": None},
        cell_index=2,
        state_revision=2,
        visibility="scratch",
    )
    art_path = workspace(root, root) / "fig.png"
    art_path.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    store.save_artifact(
        path=str(art_path),
        filename="fig.png",
        content_type="image/png",
        size_bytes=art_path.stat().st_size,
        checksum=hashlib.sha256(art_path.read_bytes()).hexdigest(),
        producing_cell_id=cell,
        frame_id=root,
        root_frame_id=root,
        project_id=project["project_id"],
    )
    builder = _builder(store, domain, workspace, tmp_path)
    proj = builder.build(root, root)
    bundle = builder.serialize_package(proj)
    view = json.loads(
        builder.serialize_view(
            proj,
            bundle={
                "filename": bundle["filename"],
                "sha256": bundle["sha256"],
                "size_bytes": bundle["size_bytes"],
            },
        )
    )

    assert view["projection_id"] == proj.projection_id
    assert view["hidden_cell_count"] == 1
    assert [c["source"] for c in view["cells"]] == ["plot()"]
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
    assert view["bundle"]["sha256"] == bundle["sha256"]
    # fig.png resolves to an artifact byte hash the router can serve
    fignames = {a["filename"] for a in view["artifacts"]}
    assert "fig.png" in fignames
    assert "fig.png" in view["by_filename"]
