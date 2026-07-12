"""Product-surface contracts for versioned personal/project Skills."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.host.skills import SkillService
from openai4s.host_dispatch import build_dispatcher
from openai4s.sdk.host import _Host
from openai4s.server import gateway as gateway_mod
from openai4s.server.skills import SkillCustomizationService
from openai4s.skills_loader import SkillLoader, SkillVersionService
from openai4s.store import get_store
from openai4s.tools import get_tool


def _config(tmp_path) -> Config:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    root = bundled / "trusted"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: Trusted\norigin: openai4s\n---\nRead only.\n",
        "utf-8",
    )
    return Config(data_dir=tmp_path / "data", skills_dir=bundled)


def _document(name: str, body: str) -> str:
    return f"---\nname: {name}\norigin: personal\n---\n{body}\n"


def test_skill_control_tools_keep_schema_policy_and_behavior_in_named_classes():
    status = get_tool("skill_status")
    history = get_tool("skill_history")
    rollback = get_tool("rollback_skill_version")

    assert type(status).__name__ == "SkillStatusTool"
    assert type(history).__name__ == "SkillHistoryTool"
    assert type(rollback).__name__ == "RollbackSkillVersionTool"
    assert status.requires_approval is False and status.read_only is True
    assert history.requires_approval is False and history.read_only is True
    assert rollback.requires_approval is True and rollback.read_only is False
    assert rollback.side_effect_class == "runtime_mutation"
    assert rollback.resource_keys({"name": "QC", "scope": "project"}) == (
        "skill:project/QC",
    )
    assert (
        rollback.permission_target(
            {"name": "QC", "scope": "project", "version_id": "skillv-" + "a" * 64}
        )
        == "project/QC/skillv-" + "a" * 64
    )
    assert rollback.native_precheck(
        {"name": "QC", "scope": "project", "version_id": "latest"}
    )
    assert (
        rollback.native_precheck(
            {
                "name": "QC",
                "scope": "project",
                "version_id": "skillv-" + "a" * 64,
            }
        )
        is None
    )


def test_sdk_skill_version_methods_encode_only_narrow_scope_arguments():
    calls = []
    host = _Host(lambda method, args: calls.append((method, args)) or {"ok": True})

    host.skills.status("QC", "project")
    host.skills.history("QC", "personal", limit=7)
    host.skills.rollback("QC", "skillv-" + "b" * 64, "project")

    assert calls == [
        ("skills_status", [{"name": "QC", "scope": "project"}]),
        ("skills_history", [{"name": "QC", "scope": "personal", "limit": 7}]),
        (
            "skills_rollback",
            [
                {
                    "name": "QC",
                    "scope": "project",
                    "versionId": "skillv-" + "b" * 64,
                }
            ],
        ),
    ]


def test_dispatcher_scopes_rollback_to_current_project_and_audits_it(tmp_path):
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(name="Project A", project_id="project-a")
    root = store.new_frame(project_id="project-a", kind="turn", status="ready")
    versions = SkillVersionService(cfg)
    first = versions.install(
        "Project QC",
        {"SKILL.md": _document("Project QC", "first")},
        scope="project",
        project_id="project-a",
    )
    second = versions.upgrade(
        "Project QC",
        {"SKILL.md": _document("Project QC", "second")},
        scope="project",
        project_id="project-a",
    )
    dispatcher = build_dispatcher(cfg=cfg, frame_id=root)
    try:
        status = dispatcher(
            "skills_status",
            [{"name": "Project QC", "scope": "project"}],
        )
        assert status["active_version_id"] == second["version_id"]
        history = dispatcher(
            "skills_history",
            [{"name": "Project QC", "scope": "project", "limit": 20}],
        )
        assert {item["version_id"] for item in history["versions"]} == {
            first["version_id"],
            second["version_id"],
        }
        denied = dispatcher(
            "skills_rollback",
            [
                {
                    "name": "Project QC",
                    "scope": "project",
                    "version_id": first["version_id"],
                }
            ],
        )
        assert denied.get("error", "").startswith("Permission denied:")
        assert (
            versions.status("Project QC", scope="project", project_id="project-a")[
                "active_version_id"
            ]
            == second["version_id"]
        )
        store.set_permission_rule(
            scope="global",
            scope_id="",
            tool="skills_rollback",
            pattern="*",
            decision="allow",
        )
        rolled_back = dispatcher(
            "skills_rollback",
            [
                {
                    "name": "Project QC",
                    "scope": "project",
                    "version_id": first["version_id"],
                }
            ],
        )
        assert rolled_back["version_id"] == first["version_id"]
        audit = store._conn.execute(
            "SELECT ok,side_effect_class,resource_keys FROM host_call_log "
            "WHERE method='skills_rollback' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert audit["ok"] == 1
        assert audit["side_effect_class"] == "runtime_mutation"
        assert "skill:project/Project QC" in audit["resource_keys"]

        trusted = dispatcher(
            "skills_status",
            [{"name": "Trusted", "scope": "personal"}],
        )
        assert trusted["read_only"] is True
        with pytest.raises(PermissionError, match="bundled and read-only"):
            dispatcher(
                "skills_rollback",
                [
                    {
                        "name": "Trusted",
                        "scope": "personal",
                        "version_id": first["version_id"],
                    }
                ],
            )
    finally:
        store.close()


def test_host_service_rejects_cross_project_version_scope(tmp_path):
    cfg = _config(tmp_path)
    service = SkillService(cfg)
    service.set_scope(project_id="project-a", session_id="session-a")

    with pytest.raises(PermissionError, match="cannot cross projects"):
        service.history(
            {
                "name": "Any",
                "scope": "project",
                "project_id": "project-b",
            }
        )


def test_http_personal_and_project_history_and_rollback_routes(tmp_path):
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(name="Project A", project_id="project-a")
    personal = SkillCustomizationService(SkillLoader(cfg=cfg))
    personal.create_or_update("Personal QC", "first", "first")
    personal.create_or_update("Personal QC", "second", "second", existing=True)
    project = SkillCustomizationService(
        SkillLoader(cfg=cfg),
        scope="project",
        project_id="project-a",
    )
    project.create_or_update("Project QC", "first", "first")
    project.create_or_update("Project QC", "second", "second", existing=True)

    handler_class = gateway_mod.make_handler(
        cfg,
        gateway_mod.WSHub(),
        SimpleNamespace(),
    )
    handler = object.__new__(handler_class)

    def call(method, path, body=None):
        replies = []
        handler._query = lambda: {}
        handler._body = lambda: body or {}
        handler._json = lambda value, code=200: replies.append((code, value))
        handler._api(method, path)
        return replies[-1]

    try:
        code, personal_history = call("GET", "/skills/Personal%20QC/versions")
        assert code == 200 and len(personal_history["versions"]) == 2
        personal_first = personal_history["versions"][-1]["version_id"]
        code, rolled_back = call(
            "POST",
            "/skills/Personal%20QC/rollback",
            {"version_id": personal_first},
        )
        assert code == 200 and rolled_back["version_id"] == personal_first

        code, project_catalog = call("GET", "/projects/project-a/skills/catalog")
        assert code == 200
        assert [item["name"] for item in project_catalog["skills"]] == ["Project QC"]
        code, project_history = call(
            "GET", "/projects/project-a/skills/Project%20QC/versions"
        )
        assert code == 200 and project_history["status"]["scope"] == "project"
        project_first = project_history["versions"][-1]["version_id"]
        code, rolled_back = call(
            "POST",
            "/projects/project-a/skills/Project%20QC/rollback",
            {"version_id": project_first},
        )
        assert code == 200 and rolled_back["scope"] == "project"

        code, read_only = call("GET", "/skills/Trusted/versions")
        assert code == 404 and read_only["error"]
    finally:
        store.close()
