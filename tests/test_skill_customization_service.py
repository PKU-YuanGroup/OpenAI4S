"""Contracts for Web Customize user-skill behavior and routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.server import gateway as gateway_mod
from openai4s.server.skills import SkillCustomizationService
from openai4s.skills_loader import SkillLoader


def _service(tmp_path, *, with_builtin=True):
    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    if with_builtin:
        root = bundled / "builtin"
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\nname: Builtin\ndescription: bundled skill\n"
            "origin: openai4s\n---\n\n# Builtin\n",
            "utf-8",
        )
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    return config, SkillCustomizationService(SkillLoader(cfg=config))


def test_create_update_read_delete_writes_exact_user_document(tmp_path):
    _config, service = _service(tmp_path)

    created = service.create_or_update(
        "  My Skill  ",
        " multi\n  space description ",
        "\n# Recipe\nDo it.\n",
    )

    assert created == {
        "ok": True,
        "name": "My Skill",
        "slug": "my-skill",
        "origin": "user",
    }
    document = (service.loader.user_skills_dir() / "my-skill" / "SKILL.md").read_text(
        "utf-8"
    )
    assert document == (
        "---\nname: My Skill\ndescription: multi space description\n"
        "origin: user\n---\n\n# Recipe\nDo it.\n"
    )
    assert service.get("My Skill") == service.get("my-skill")
    assert service.get("My Skill")["editable"] is True

    skill_root = service.loader.user_skills_dir() / "my-skill"
    (skill_root / "kernel.py").write_text("VALUE = 1\n", "utf-8")
    (skill_root / "resources").mkdir()
    (skill_root / "resources" / "schema.json").write_text("{}\n", "utf-8")
    updated = service.create_or_update(
        "My Skill",
        "updated",
        "New body",
        existing=True,
    )
    assert updated["slug"] == "my-skill"
    assert service.get("my-skill")["body"] == "New body\n"
    assert (skill_root / "kernel.py").read_text("utf-8") == "VALUE = 1\n"
    assert (skill_root / "resources" / "schema.json").is_file()
    assert service.delete("My Skill") == {"ok": True}
    assert not skill_root.exists()
    assert service.get("My Skill") == {"error": "skill not found"}
    assert service.delete("My Skill") == {"error": "skill not found"}


def test_validation_builtin_collision_and_read_only_delete_contract(tmp_path):
    _config, service = _service(tmp_path)

    assert service.create_or_update("", "", "") == {"error": "skill name is required"}
    assert service.create_or_update("Builtin", "custom", "body") == {
        "error": "'builtin' collides with a built-in skill — pick a different name"
    }
    builtin = service.get("Builtin")
    assert builtin["origin"] == "openai4s"
    assert builtin["editable"] is False
    assert service.delete("Builtin") == {
        "error": "only user-authored skills can be deleted"
    }


def test_declared_builtin_name_collision_is_rejected_when_slug_differs(tmp_path):
    config, service = _service(tmp_path, with_builtin=False)
    bundled = config.skills_dir / "trusted-directory"
    bundled.mkdir()
    (bundled / "SKILL.md").write_text(
        "---\nname: Canonical Skill\ndescription: trusted\n"
        "origin: personal\n---\n# Trusted\n",
        "utf-8",
    )

    assert service.create_or_update(" canonical  skill ", "custom", "body") == {
        "error": "'canonical-skill' collides with a built-in skill — "
        "pick a different name"
    }
    assert service.get("Canonical Skill")["editable"] is False


def test_customize_edits_host_draft_and_personal_skills_by_user_root(tmp_path):
    _config, service = _service(tmp_path)
    user_directory = service.loader.user_skills_dir()
    for directory, name, origin in (
        ("host-draft-directory", "Host Draft", "draft"),
        ("host-personal-directory", "Host Personal", "personal"),
    ):
        root = user_directory / directory
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: host authored\n"
            f"origin: {origin}\n---\n# Original\n",
            "utf-8",
        )

    service.loader.discover()
    catalog = {item["name"]: item for item in service.catalog()}
    assert catalog["Host Draft"]["editable"] is True
    assert catalog["Host Personal"]["editable"] is True
    assert service.get("Host Draft")["editable"] is True
    assert service.get("Host Personal")["editable"] is True

    updated = service.create_or_update(
        "Host Draft",
        "edited in Customize",
        "# Updated",
        existing=True,
    )
    assert updated == {
        "ok": True,
        "name": "Host Draft",
        "slug": "host-draft-directory",
        "origin": "draft",
    }
    document = (user_directory / "host-draft-directory" / "SKILL.md").read_text("utf-8")
    assert "origin: draft" in document
    assert document.endswith("# Updated\n")


def test_import_precedence_and_catalog_enablement(tmp_path):
    _config, service = _service(tmp_path)
    raw = (
        "---\nname: Imported\ndescription: from document\norigin: draft\n---\n\n"
        "# Imported body\n"
    )

    imported = service.import_document(content=raw)
    assert imported["slug"] == "imported"
    assert service.get("Imported")["body"] == "# Imported body\n"
    assert service.get("Imported")["origin"] == "user"

    explicit = service.import_document(
        content=raw,
        name="Explicit Name",
        description="explicit description",
    )
    assert explicit["slug"] == "explicit-name"
    assert service.get("Explicit Name")["description"] == "explicit description"
    body_wins = service.import_document(
        content=raw,
        name="Body Wins",
        description="manual",
        body="Explicit body",
    )
    assert body_wins["slug"] == "body-wins"
    assert service.get("Body Wins")["body"] == "Explicit body\n"
    assert service.import_document(content=raw, body="explicit body") == {
        "error": "skill name is required"
    }

    assert service.set_enabled("Imported", False) == {"ok": True}
    catalog = {item["name"]: item for item in service.catalog()}
    assert {
        "name",
        "displayName",
        "description",
        "origin",
        "editable",
        "enabled",
    } <= set(catalog["Imported"])
    assert catalog["Imported"]["enabled"] is False
    assert catalog["Imported"]["editable"] is True
    assert service.set_enabled("Imported", True) == {"ok": True}
    assert (
        next(item for item in service.catalog() if item["name"] == "Imported")[
            "enabled"
        ]
        is True
    )

    class BrokenLoader:
        def catalog(self):
            raise OSError("unavailable")

    assert SkillCustomizationService(BrokenLoader()).catalog() == []


def test_gateway_skill_routes_keep_soft_errors_and_shared_enablement(tmp_path):
    config, _service_instance = _service(tmp_path)
    handler_class = gateway_mod.make_handler(
        config,
        gateway_mod.WSHub(),
        SimpleNamespace(),
    )
    first = object.__new__(handler_class)
    second = object.__new__(handler_class)

    def call(handler, method, path, body=None):
        replies = []
        handler._query = lambda: {}
        handler._body = lambda: body or {}
        handler._json = lambda value, code=200: replies.append((code, value))
        handler._api(method, path)
        assert replies
        return replies[-1]

    assert gateway_mod._skill_slug("My Connector") == "my-connector"
    assert call(first, "POST", "/skills", {}) == (
        200,
        {"error": "skill name is required"},
    )
    assert call(
        first,
        "POST",
        "/skills",
        {"name": "Builtin", "body": "shadow"},
    ) == (
        200,
        {"error": "'builtin' collides with a built-in skill — pick a different name"},
    )
    assert call(first, "DELETE", "/skills/Builtin") == (
        200,
        {"error": "only user-authored skills can be deleted"},
    )

    code, created = call(
        first,
        "POST",
        "/skills",
        {"name": "Web Skill", "description": "web", "body": "First body"},
    )
    assert code == 200 and created["slug"] == "web-skill"
    code, fetched = call(second, "GET", "/skills/Web%20Skill")
    assert code == 200 and fetched["body"] == "First body\n"

    assert call(
        first,
        "PATCH",
        "/skills/catalog/Web%20Skill/enabled",
        {"enabled": False},
    ) == (200, {"ok": True})
    _code, catalog = call(second, "GET", "/skills/catalog")
    web_skill = next(item for item in catalog["skills"] if item["name"] == "Web Skill")
    assert web_skill["enabled"] is False

    fresh_handler_class = gateway_mod.make_handler(
        config,
        gateway_mod.WSHub(),
        SimpleNamespace(),
    )
    fresh = object.__new__(fresh_handler_class)
    _code, fresh_catalog = call(fresh, "GET", "/skills/catalog")
    fresh_web_skill = next(
        item for item in fresh_catalog["skills"] if item["name"] == "Web Skill"
    )
    # Enablement is durable capability policy, not handler-local UI state.
    assert fresh_web_skill["enabled"] is False

    code, updated = call(
        second,
        "PUT",
        "/skills/Web%20Skill",
        {"description": "updated", "content": "Second body"},
    )
    assert code == 200 and updated["ok"] is True
    assert call(first, "GET", "/skills/Web%20Skill")[1]["body"] == "Second body\n"

    imported = call(
        first,
        "POST",
        "/skills/import",
        {"content": ("---\nname: Route Import\ndescription: route\n---\n\nRoute body")},
    )
    assert imported[0] == 200 and imported[1]["slug"] == "route-import"
    assert call(first, "GET", "/skills/Missing") == (
        200,
        {"error": "skill not found"},
    )
    assert call(first, "DELETE", "/skills/Web%20Skill") == (200, {"ok": True})


def test_skill_delete_rejects_same_prefix_sibling_directory(tmp_path):
    user_directory = tmp_path / "user-skills"
    user_directory.mkdir()
    outside = tmp_path / "user-skills-evil" / "victim"
    outside.mkdir(parents=True)
    marker = outside / "keep.txt"
    marker.write_text("keep\n", "utf-8")
    skill = SimpleNamespace(name="Victim", root=outside)
    loader = SimpleNamespace(
        user_skills_dir=lambda: user_directory,
        skills=lambda: {"victim": skill},
        discover=lambda: None,
    )

    assert SkillCustomizationService(loader).delete("Victim") == {
        "error": "only user-authored skills can be deleted"
    }
    assert marker.read_text("utf-8") == "keep\n"


def test_skill_write_rejects_directory_and_document_symlink_escape(tmp_path):
    _config, service = _service(tmp_path)
    user_directory = service.loader.user_skills_dir()
    user_directory.mkdir(parents=True)
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    directory_link = user_directory / "escape"
    try:
        directory_link.symlink_to(outside_directory, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    assert service.create_or_update("Escape", "", "outside write") == {
        "error": "unsafe user skill path"
    }
    assert not (outside_directory / "SKILL.md").exists()

    safe_root = user_directory / "document-link"
    safe_root.mkdir()
    outside_document = tmp_path / "outside-skill.md"
    outside_document.write_text("sentinel\n", "utf-8")
    (safe_root / "SKILL.md").symlink_to(outside_document)

    assert service.create_or_update(
        "Document Link",
        "",
        "replacement",
        existing=True,
    ) == {"error": "unsafe user skill path"}
    assert outside_document.read_text("utf-8") == "sentinel\n"

    service.create_or_update("Real Skill", "", "real body")
    real_root = user_directory / "real-skill"
    sentinel = real_root / "sentinel.txt"
    sentinel.write_text("keep real skill\n", "utf-8")
    alias = user_directory / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    service.loader.discover()

    assert service.delete("alias") == {"error": "unsafe user skill path"}
    assert alias.is_symlink()
    assert sentinel.read_text("utf-8") == "keep real skill\n"
