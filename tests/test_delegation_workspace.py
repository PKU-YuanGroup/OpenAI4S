"""Private-scratch prototype: no parent mutation until explicit materialize."""

from __future__ import annotations

from pathlib import Path

import openai4s.agent.loop as loop_mod
from openai4s.agent.delegation import DelegationRunner
from openai4s.agent.delegation_workspace import private_scratch_enabled
from openai4s.config import get_config
from openai4s.store import get_store


def _submitted(output=None):
    return {
        "stop_reason": "submitted",
        "submitted_output": {
            "output": output if output is not None else {"ok": True},
            "completion_bullets": ["wrote"],
        },
        "final_message": None,
    }


def _parent_listing(workspace: Path) -> dict[str, str]:
    listing = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            listing[path.relative_to(workspace).as_posix()] = path.read_text(
                encoding="utf-8"
            )
    return listing


def test_private_scratch_defaults_off():
    assert private_scratch_enabled() is False


def test_two_children_same_filename_do_not_overwrite_and_parent_waits_for_materialize(
    monkeypatch, tmp_path
):
    def write_run(self, task):
        target = Path(self.workspace) / "shared.txt"
        target.write_text(f"from-{task}", encoding="utf-8")
        return _submitted({"wrote": "shared.txt", "task": task})

    monkeypatch.setattr(loop_mod.Agent, "run", write_run)
    cfg = get_config()
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="science")
    parent = tmp_path / "parent-workspace"
    parent.mkdir()
    (parent / "seed.txt").write_text("parent-seed", encoding="utf-8")
    before = _parent_listing(parent)

    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        workspace=str(parent),
        private_scratch=True,
        owner_instance_id="owner-scratch",
        runner_instance_id="runner-scratch",
    )
    first = runner({"request": "alpha", "name": "alpha"})
    second = runner({"request": "beta", "name": "beta"})
    after_children = _parent_listing(parent)
    assert after_children == before
    assert (parent / "shared.txt").exists() is False

    first_refs = first["artifact_refs"]
    second_refs = second["artifact_refs"]
    assert len(first_refs) == 1
    assert len(second_refs) == 1
    assert first_refs[0]["filename"] == "shared.txt"
    assert second_refs[0]["filename"] == "shared.txt"
    assert first_refs[0]["version_id"] != second_refs[0]["version_id"]
    assert first_refs[0]["artifact_id"] != second_refs[0]["artifact_id"]
    assert first_refs[0]["checksum"] != second_refs[0]["checksum"]
    assert first_refs[0]["frame_id"] == first["frame_id"]
    assert second_refs[0]["frame_id"] == second["frame_id"]
    assert first_refs[0]["durable_path"] != second_refs[0]["durable_path"]
    assert (
        Path(first_refs[0]["durable_path"]).read_text(encoding="utf-8") == "from-alpha"
    )
    assert (
        Path(second_refs[0]["durable_path"]).read_text(encoding="utf-8") == "from-beta"
    )

    materialized = runner.materialize_child(first["child_id"])
    assert materialized["deleted_versions"] == 0
    assert (parent / "shared.txt").read_text(encoding="utf-8") == "from-alpha"
    assert (parent / "seed.txt").read_text(encoding="utf-8") == "parent-seed"

    # Rollback of the parent file must not delete published versions.
    (parent / "shared.txt").unlink()
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM artifact_versions WHERE version_id IN (?,?)",
            (first_refs[0]["version_id"], second_refs[0]["version_id"]),
        ).fetchone()[0]
        == 2
    )
    runner.close()


def test_flag_off_children_still_share_the_parent_workspace(monkeypatch, tmp_path):
    def write_run(self, task):
        (Path(self.workspace) / "shared.txt").write_text(task, encoding="utf-8")
        return _submitted(task)

    monkeypatch.setattr(loop_mod.Agent, "run", write_run)
    cfg = get_config()
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="science")
    parent = tmp_path / "shared-parent"
    parent.mkdir()
    runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        workspace=str(parent),
        private_scratch=False,
    )
    runner({"request": "one"})
    runner({"request": "two"})
    runner.close()
    assert (parent / "shared.txt").read_text(encoding="utf-8") == "two"


def test_private_scratch_materializes_persisted_version_after_runner_restart(
    monkeypatch, tmp_path
):
    def write_run(self, task):
        target = Path(self.workspace) / "nested" / "result.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"durable-{task}", encoding="utf-8")
        return _submitted({"wrote": "nested/result.txt"})

    monkeypatch.setattr(loop_mod.Agent, "run", write_run)
    cfg = get_config()
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="science")
    parent = tmp_path / "restart-parent"
    parent.mkdir()
    first_runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        workspace=str(parent),
        private_scratch=True,
        owner_instance_id="owner-before-restart",
        runner_instance_id="runner-before-restart",
    )
    result = first_runner(
        {
            "request": "restart-proof",
            "parent_action_group_id": "ag-restart",
            "native_call_id": "call-restart",
        }
    )
    child_id = result["child_id"]
    assert result["artifact_refs"][0]["path"] == "nested/result.txt"
    first_runner.close()
    store.close()
    assert (parent / "nested" / "result.txt").exists() is False

    store = get_store(cfg.db_path)
    restored_runner = DelegationRunner(
        cfg,
        parent_frame_id=root,
        store=store,
        workspace=str(parent),
        private_scratch=True,
        owner_instance_id="owner-after-restart",
        runner_instance_id="runner-after-restart",
    )
    materialized = restored_runner.materialize_child(child_id)
    assert materialized == {
        "written": ["nested/result.txt"],
        "missing": [],
        "deleted_versions": 0,
    }
    assert (parent / "nested" / "result.txt").read_text("utf-8") == (
        "durable-restart-proof"
    )
    restored_runner.close()
