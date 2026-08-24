"""Product-surface contracts for versioned personal/project Skills."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.execution_principal import Principal
from openai4s.execution_principal import scope as principal_scope
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
    listing = get_tool("list_skills")
    status = get_tool("skill_status")
    history = get_tool("skill_history")
    rollback = get_tool("rollback_skill_version")

    assert type(listing).__name__ == "ListSkillsTool"
    # Both arguments are optional: the zero-argument call is the catalog
    # overview, and `collection` (paged by `offset`) enumerates one bundled
    # collection. That is what lets the overview stay small enough for the
    # 10k observation ceiling with 561 imported recipes present -- at the cost
    # of provider-strict generation, which requires every declared property to
    # be required (see tests/test_native_tools.py).
    assert listing.input_schema() == {
        "type": "object",
        "properties": {
            "collection": {
                "type": "string",
                "description": "Enumerate this collection's Skill names instead.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Start index when paging a collection listing.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }
    assert listing.read_only is True and listing.requires_approval is False
    assert listing.resource_keys({}) == ("skill:catalog",)
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


def test_list_skills_native_tool_dispatches_to_existing_catalog(tmp_path):
    dispatcher = build_dispatcher(_config(tmp_path))
    try:
        catalog = get_tool("list_skills").invoke(dispatcher, {})
    finally:
        dispatcher.store.close()

    # `count` is the whole catalog; `names` is the curated tier; each bundled
    # collection is one entry rather than N peers.
    assert catalog == {"count": 1, "names": ["Trusted"], "collections": []}


def test_list_skills_native_tool_pages_collections_with_next_offset():
    rows = [{"name": "Trusted", "collection": None}] + [
        {"name": f"member-{index:03d}", "collection": "bundle"} for index in range(151)
    ]
    runtime = SimpleNamespace(invoke=lambda method: rows)
    tool = get_tool("list_skills")

    overview = tool.execute(runtime, {})
    first = tool.execute(runtime, {"collection": "bundle", "offset": 0})
    final = tool.execute(
        runtime, {"collection": "bundle", "offset": first["next_offset"]}
    )

    assert overview == {
        "count": 152,
        "names": ["Trusted"],
        "collections": [{"id": "bundle", "count": 151}],
    }
    assert first == {
        "collection": "bundle",
        "count": 151,
        "offset": 0,
        "names": [f"member-{index:03d}" for index in range(150)],
        "next_offset": 150,
    }
    assert final == {
        "collection": "bundle",
        "count": 151,
        "offset": 150,
        "names": ["member-150"],
    }


@pytest.mark.parametrize("arguments", ["example_stats", {"name": "example_stats"}])
def test_load_skill_control_tool_accepts_legacy_sdk_and_native_arguments(arguments):
    calls = []
    runtime = SimpleNamespace(
        invoke=lambda method, *args: calls.append((method, args)) or {"name": args[0]}
    )
    tool = get_tool("load_skill")

    assert tool.execute(runtime, arguments) == {"name": "example_stats"}
    assert calls == [("load_skill", ("example_stats",))]
    assert tool.resource_keys(arguments) == ("skill:example_stats",)


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


@pytest.mark.security
def test_team_member_approval_cannot_mutate_personal_skills(tmp_path, monkeypatch):
    """Interactive consent is not instance-admin authorization.

    Before the team guard lived in ``SkillService``, every call below reached
    the daemon-global personal Skill store after the member approved it.  The
    Web routes already required an admin; the in-kernel Host route did not.
    """

    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(name="Project A", project_id="project-a")
    root = store.new_frame(project_id="project-a", kind="turn", status="ready")
    dispatcher = build_dispatcher(cfg=cfg, frame_id=root)
    for method in (
        "skills_edit",
        "skills_publish",
        "skills_delete",
        "skills_rollback",
    ):
        store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=method,
            pattern="*",
            decision="allow",
        )

    admin = Principal("admin-id", "admin", "admin")
    member = Principal("member-id", "member", "member")
    first_document = _document("Shared QC", "first")
    second_document = _document("Shared QC", "second")
    try:
        with principal_scope(admin):
            first = dispatcher(
                "skills_edit",
                [
                    {
                        "name": "Shared QC",
                        "path": "SKILL.md",
                        "content": first_document,
                    }
                ],
            )
            assert first["ok"] is True
            first_version = SkillVersionService(cfg).status("Shared QC")[
                "active_version_id"
            ]
            dispatcher(
                "skills_edit",
                [
                    {
                        "name": "Shared QC",
                        "path": "SKILL.md",
                        "content": second_document,
                    }
                ],
            )
        active_before = SkillVersionService(cfg).status("Shared QC")[
            "active_version_id"
        ]

        def member_edit():
            return dispatcher(
                "skills_edit",
                [
                    {
                        "name": "Shared QC",
                        "path": "SKILL.md",
                        "content": _document("Shared QC", "member overwrite"),
                    }
                ],
            )

        def member_publish():
            return dispatcher("skills_publish", ["Shared QC"])

        def member_delete():
            return dispatcher("skills_delete", ["Shared QC"])

        def member_rollback():
            return dispatcher(
                "skills_rollback",
                [
                    {
                        "name": "Shared QC",
                        "scope": "personal",
                        "version_id": first_version,
                    }
                ],
            )

        with principal_scope(member):
            for operation in (
                member_edit,
                member_publish,
                member_delete,
                member_rollback,
            ):
                with pytest.raises(PermissionError, match="administrator"):
                    operation()

        personal_root = cfg.data_dir / "user-skills" / "shared-qc"
        assert second_document in (personal_root / "SKILL.md").read_text("utf-8")
        assert (
            SkillVersionService(cfg).status("Shared QC")["active_version_id"]
            == active_before
        )

        # Team admins retain the same local authoring lifecycle.
        with principal_scope(admin):
            assert dispatcher("skills_publish", ["Shared QC"])["ok"] is True
            assert (
                dispatcher(
                    "skills_rollback",
                    [
                        {
                            "name": "Shared QC",
                            "scope": "personal",
                            "version_id": first_version,
                        }
                    ],
                )["version_id"]
                == first_version
            )
            assert dispatcher("skills_delete", ["Shared QC"])["ok"] is True
        assert not personal_root.exists()
    finally:
        store.close()


@pytest.mark.security
def test_project_skill_edit_is_not_silently_allowed_by_default(tmp_path, monkeypatch):
    """The real dispatcher asks before any model-authored Skill mutation."""

    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "deny")
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    store.create_project(name="Project A", project_id="project-a")
    member = Principal("member-id", "member", "member")
    store.governance.set_member("project-a", member.user_id, "member")
    root = store.new_frame(project_id="project-a", kind="turn", status="ready")
    versions = SkillVersionService(cfg)
    versions.install(
        "Project QC",
        {"SKILL.md": _document("Project QC", "trusted")},
        scope="project",
        project_id="project-a",
    )
    dispatcher = build_dispatcher(cfg=cfg, frame_id=root)
    project_root = versions.scope_root(scope="project", project_id="project-a")
    try:
        assert (
            store.resolve_permission(tool="skills_edit", pattern_input="Project QC")
            == "ask"
        )
        with principal_scope(member):
            denied = dispatcher(
                "skills_edit",
                [
                    {
                        "name": "Project QC",
                        "path": "kernel.py",
                        "content": "PEER_CODE_EXECUTED = True\n",
                    }
                ],
            )
        assert denied.get("error", "").startswith("Permission denied:")
        assert not (project_root / "project-qc" / "kernel.py").exists()
        assert "trusted" in (project_root / "project-qc" / "SKILL.md").read_text(
            "utf-8"
        )
    finally:
        store.close()


@pytest.mark.security
def test_team_project_skill_host_mutation_requires_admin_even_for_members(
    tmp_path, monkeypatch
):
    """A member's model cannot plant instructions or code for project peers."""

    monkeypatch.setenv("OPENAI4S_TEAM_MODE", "1")
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
    versions.upgrade(
        "Project QC",
        {"SKILL.md": _document("Project QC", "second")},
        scope="project",
        project_id="project-a",
    )
    dispatcher = build_dispatcher(cfg=cfg, frame_id=root)
    for method in (
        "skills_edit",
        "skills_publish",
        "skills_delete",
        "skills_rollback",
    ):
        store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=method,
            pattern="*",
            decision="allow",
        )

    member = Principal("member-id", "member", "member")
    admin = Principal("admin-id", "admin", "admin")

    def edit(path="SKILL.md", content=None):
        return dispatcher(
            "skills_edit",
            [
                {
                    "name": "Project QC",
                    "path": path,
                    "content": (
                        content
                        if content is not None
                        else _document("Project QC", "member edit")
                    ),
                }
            ],
        )

    def rollback():
        return dispatcher(
            "skills_rollback",
            [
                {
                    "name": "Project QC",
                    "scope": "project",
                    "version_id": first["version_id"],
                }
            ],
        )

    try:
        with principal_scope(member):
            with pytest.raises(PermissionError, match="team administrator"):
                edit()
            with pytest.raises(PermissionError, match="team administrator"):
                rollback()

        store.governance.set_member("project-a", member.user_id, "member")
        with principal_scope(member):
            # Membership authorizes the human project API, not code authored by
            # that member's model.  Both the instruction and executable sidecar
            # forms are refused at the real Host dispatcher sink even if a broad
            # permission rule was explicitly allowed.
            for path, content in (
                ("SKILL.md", _document("Project QC", "poisoned instructions")),
                ("kernel.py", "PEER_CODE_EXECUTED = True\n"),
            ):
                with pytest.raises(PermissionError, match="team administrator"):
                    edit(path, content)
            with pytest.raises(PermissionError, match="team administrator"):
                dispatcher("skills_publish", ["Project QC"])
            with pytest.raises(PermissionError, match="team administrator"):
                rollback()
        project_root = versions.scope_root(scope="project", project_id="project-a")
        assert "second" in (project_root / "project-qc" / "SKILL.md").read_text("utf-8")
        assert not (project_root / "project-qc" / "kernel.py").exists()
        assert (
            versions.repository.get_installation(
                "Project QC", scope="personal", scope_id=""
            )
            is None
        )

        # An admin need not hold a project_members row, but still passed the
        # permission broker above.  Host lifecycle remains available to admins.
        with principal_scope(admin):
            assert edit()["ok"] is True
            assert dispatcher("skills_publish", ["Project QC"])["ok"] is True
            assert rollback()["version_id"] == first["version_id"]
            assert dispatcher("skills_delete", ["Project QC"])["ok"] is True
    finally:
        store.close()


@pytest.mark.security
def test_team_member_keeps_authenticated_http_project_skill_rollback(
    tmp_path, monkeypatch
):
    """The Host boundary does not revoke deliberate human project authoring."""

    from openai4s.storage import team as team_mod
    from tests.test_team_auth_routes import _body_json, _login, _post, _TeamDaemon

    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)
    node = _TeamDaemon(tmp_path)
    try:
        member = node.seed_user("member", "fake-password")
        node.store.create_project(name="Project A", project_id="project-a")
        node.store.governance.set_member("project-a", str(member["id"]), "member")
        versions = SkillVersionService(node.cfg)
        web = SkillCustomizationService(
            SkillLoader(cfg=node.cfg),
            scope="project",
            project_id="project-a",
        )
        assert web.create_or_update("Project QC", "first", "first")["ok"] is True
        first_version = versions.status(
            "Project QC", scope="project", project_id="project-a"
        )["active_version_id"]
        assert (
            web.create_or_update("Project QC", "second", "second", existing=True)["ok"]
            is True
        )
        cookie = _login(node, "member", "fake-password")

        status, raw = _post(
            node.port,
            "/api/v1/projects/project-a/skills/Project%20QC/rollback",
            {"version_id": first_version},
            cookie=cookie,
        )

        assert status == 200, raw[:300]
        assert _body_json(raw)["version_id"] == first_version
    finally:
        node.close()


@pytest.mark.security
def test_team_member_cannot_reactivate_legacy_host_skill_poison_over_http(
    tmp_path, monkeypatch
):
    """Rollback cannot bypass the new Host write boundary with old versions."""

    from openai4s.storage import team as team_mod
    from tests.test_team_auth_routes import _body_json, _login, _post, _TeamDaemon

    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)
    node = _TeamDaemon(tmp_path)
    try:
        member = node.seed_user("member", "fake-password")
        node.seed_user("admin", "fake-admin-password", role="admin")
        node.store.create_project(name="Project A", project_id="project-a")
        node.store.governance.set_member("project-a", str(member["id"]), "member")
        versions = SkillVersionService(node.cfg)

        def legacy_history(name, files):
            poisoned = versions.install(
                name,
                files,
                scope="project",
                project_id="project-a",
                metadata={"source": "host_skills_edit", "path": "kernel.py"},
            )
            versions.upgrade(
                name,
                {"SKILL.md": _document(name, "safe human revision")},
                scope="project",
                project_id="project-a",
                metadata={"source": "web_customize"},
            )
            return poisoned["version_id"]

        poisoned_sidecar = legacy_history(
            "Sidecar QC",
            {
                "SKILL.md": _document("Sidecar QC", "poisoned instructions"),
                "kernel.py": "PEER_CODE_EXECUTED = True\n",
            },
        )
        poisoned_recipe = legacy_history(
            "Recipe QC",
            {"SKILL.md": _document("Recipe QC", "poisoned instructions")},
        )
        member_cookie = _login(node, "member", "fake-password")

        for name, version_id in (
            ("Sidecar%20QC", poisoned_sidecar),
            ("Recipe%20QC", poisoned_recipe),
        ):
            status, raw = _post(
                node.port,
                f"/api/v1/projects/project-a/skills/{name}/rollback",
                {"version_id": version_id},
                cookie=member_cookie,
            )
            # Rollback routes retain their established conflict-on-error HTTP
            # envelope; the stable product error code carries the narrower
            # authorization reason.
            assert status == 409, raw[:300]
            assert _body_json(raw)["code"] == "skill_admin_required"

        sidecar_root = (
            versions.scope_root(scope="project", project_id="project-a") / "sidecar-qc"
        )
        assert not (sidecar_root / "kernel.py").exists()
        assert "safe human revision" in (sidecar_root / "SKILL.md").read_text("utf-8")

        # An authenticated team administrator can deliberately re-authorize
        # the retained executable version.
        admin_cookie = _login(node, "admin", "fake-admin-password")
        status, raw = _post(
            node.port,
            "/api/v1/projects/project-a/skills/Sidecar%20QC/rollback",
            {"version_id": poisoned_sidecar},
            cookie=admin_cookie,
        )
        assert status == 200, raw[:300]
        assert _body_json(raw)["version_id"] == poisoned_sidecar
        assert (sidecar_root / "kernel.py").read_text("utf-8") == (
            "PEER_CODE_EXECUTED = True\n"
        )
    finally:
        node.close()


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
