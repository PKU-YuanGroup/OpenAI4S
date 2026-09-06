"""Contracts for Web Customize user-skill behavior and routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.server import gateway as gateway_mod
from openai4s.server.skills import SkillCustomizationService
from openai4s.skills_loader import SkillLoader
from openai4s.skills_loader.loader import _parse_frontmatter
from openai4s.store import get_store

IMPORT_SEED = """---
name: cryo-import
description: Import me whole
origin: draft
requirements: [gpu, cuda]
capabilities:
  network:
    mode: host_only
    domains:
      - api.openalex.org
      - doi.org
license: CC-BY-4.0
category: structural-biology
# keep this comment
x-vendor-ext:
  nested:
    keep: me
    list:
      - one
      - two
---

Original imported body.
"""


def _skill_version_rows(config: Config) -> int:
    store = get_store(config.db_path)
    row = store._conn.execute("SELECT count(*) FROM skill_versions").fetchone()
    return int(row[0])


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
    assert service.get("My Skill") == {
        "error": "skill not found",
        "code": "skill_not_found",
    }
    assert service.delete("My Skill") == {
        "error": "skill not found",
        "code": "skill_not_found",
    }


def test_validation_builtin_collision_and_read_only_delete_contract(tmp_path):
    _config, service = _service(tmp_path)

    assert service.create_or_update("", "", "") == {
        "error": "skill name is required",
        "code": "skill_name_required",
    }
    assert service.create_or_update("Builtin", "custom", "body") == {
        "error": "'builtin' collides with a built-in skill — pick a different name",
        "code": "skill_name_conflict",
    }
    builtin = service.get("Builtin")
    assert builtin["origin"] == "openai4s"
    assert builtin["editable"] is False
    assert service.delete("Builtin") == {
        "error": "only user-authored skills can be deleted",
        "code": "skill_read_only",
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
        "pick a different name",
        "code": "skill_name_conflict",
    }
    assert service.get("Canonical Skill")["editable"] is False


def test_project_update_rejects_collection_member_directory_alias(tmp_path):
    config, _personal = _service(tmp_path, with_builtin=False)
    collection = config.skills_dir / "collection"
    member = collection / "physical-directory"
    member.mkdir(parents=True)
    (collection / "COLLECTION.json").write_text(
        '{"id":"collection","prompt_line":"collection: {count}"}\n',
        "utf-8",
    )
    (member / "SKILL.md").write_text(
        "---\nname: Canonical Member\ndescription: bundled\n---\nbody\n",
        "utf-8",
    )
    service = SkillCustomizationService(
        SkillLoader(cfg=config), scope="project", project_id="project-a"
    )

    result = service.create_or_update(
        "physical-directory",
        "project overlay",
        "replacement",
        existing=True,
    )

    assert result == {
        "error": "'physical-directory' collides with a built-in skill — "
        "pick a different name",
        "code": "skill_name_conflict",
    }
    project_root = service.versions.scope_root(scope="project", project_id="project-a")
    assert not (project_root / "physical-directory" / "SKILL.md").exists()


def test_web_authoring_fails_before_write_for_invalid_collection_catalog(tmp_path):
    config, _personal = _service(tmp_path, with_builtin=False)
    for directory in ("first", "second"):
        collection = config.skills_dir / directory
        member = collection / f"{directory}-member"
        member.mkdir(parents=True)
        (collection / "COLLECTION.json").write_text(
            '{"id":"duplicate","prompt_line":"duplicate: {count}"}\n',
            "utf-8",
        )
        (member / "SKILL.md").write_text(
            "---\n"
            f"name: {directory} member\n"
            f"description: {directory}\n"
            "---\nbody\n",
            "utf-8",
        )
    service = SkillCustomizationService(SkillLoader(cfg=config))

    result = service.create_or_update("Never Written", "description", "body")

    assert result["code"] == "skill_write_failed"
    assert "duplicate skill collection id 'duplicate'" in result["error"]
    assert not (
        service.loader.user_skills_dir() / "never-written" / "SKILL.md"
    ).exists()


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
        "error": "skill name is required",
        "code": "skill_name_required",
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


def test_import_round_trip_keeps_requirements_network_license_comment_and_unknown_nested(
    tmp_path,
):
    """The exam is the unknown nested key. A fixture with only name/body is
    green before and after the fix."""
    config, service = _service(tmp_path)

    imported = service.import_document(content=IMPORT_SEED)

    assert imported["ok"] is True
    assert imported["origin"] == "user"
    assert imported["slug"] == "cryo-import"
    document = (
        service.loader.user_skills_dir() / "cryo-import" / "SKILL.md"
    ).read_text("utf-8")
    meta, body = _parse_frontmatter(document)
    assert meta["name"] == "cryo-import"
    assert meta["origin"] == "user"
    assert meta["description"] == "Import me whole"
    assert meta["requirements"] == "[gpu, cuda]"
    assert meta["license"] == "CC-BY-4.0"
    assert meta["category"] == "structural-biology"
    assert "mode: host_only" in document
    assert "- api.openalex.org" in document
    assert "- doi.org" in document
    assert "# keep this comment" in document
    assert "x-vendor-ext:" in document
    assert "    keep: me" in document
    assert "      - two" in document
    assert body.strip() == "Original imported body."
    review = imported["review"]
    assert set(review) >= {"requirements", "capabilities", "readiness", "warnings"}
    assert set(review["requirements"]) == {"cuda", "gpu"}
    assert review["capabilities"]["network"]["mode"] == "host_only"
    assert review["capabilities"]["network"]["domains"] == [
        "api.openalex.org",
        "doi.org",
    ]
    assert "state" in review["readiness"]
    assert service.get("cryo-import")["origin"] == "user"


def test_import_explicit_name_description_body_still_keep_the_seed_fields(tmp_path):
    config, service = _service(tmp_path)

    imported = service.import_document(
        content=IMPORT_SEED,
        name="Explicit Name",
        description="explicit description",
        body="Explicit body",
    )

    assert imported["slug"] == "explicit-name"
    assert imported["origin"] == "user"
    document = (
        service.loader.user_skills_dir() / "explicit-name" / "SKILL.md"
    ).read_text("utf-8")
    meta, body = _parse_frontmatter(document)
    assert meta["name"] == "Explicit Name"
    assert meta["description"] == "explicit description"
    assert meta["origin"] == "user"
    assert meta["requirements"] == "[gpu, cuda]"
    assert meta["license"] == "CC-BY-4.0"
    assert "x-vendor-ext:" in document
    assert "# keep this comment" in document
    assert body.strip() == "Explicit body"


def test_invalid_network_mode_is_400_and_does_not_write_a_version(tmp_path):
    config, service = _service(tmp_path)
    before = _skill_version_rows(config)
    bad = IMPORT_SEED.replace("mode: host_only", "mode: warp")

    result = service.import_document(content=bad)

    assert _skill_version_rows(config) == before
    assert result.get("code") == "skill_capability_invalid"
    assert result.get("error")
    user_root = service.loader.user_skills_dir()
    assert not (user_root / "cryo-import" / "SKILL.md").exists()


def test_gateway_import_rejects_illegal_network_mode_without_a_version_row(tmp_path):
    config, _service_instance = _service(tmp_path)
    handler_class = gateway_mod.make_handler(
        config,
        gateway_mod.WSHub(),
        SimpleNamespace(),
    )
    handler = object.__new__(handler_class)
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: {
        "content": IMPORT_SEED.replace("mode: host_only", "mode: warp")
    }
    handler._json = lambda value, code=200: replies.append((code, value))
    before = _skill_version_rows(config)

    handler._api("POST", "/skills/import")

    status, body = replies[-1]
    assert _skill_version_rows(config) == before
    assert status == 400
    assert body.get("code") == "skill_capability_invalid"
    assert not (
        _service_instance.loader.user_skills_dir() / "cryo-import" / "SKILL.md"
    ).exists()


def test_import_rollback_review_matches_the_restored_version(tmp_path):
    _config, service = _service(tmp_path)
    first = service.import_document(content=IMPORT_SEED)
    first_id = service.history("cryo-import")["installation"]["active_version_id"]
    service.create_or_update(
        "cryo-import",
        "second description",
        "Second body",
        existing=True,
    )
    document = service.loader.user_skills_dir() / "cryo-import" / "SKILL.md"
    assert "second description" in document.read_text("utf-8")

    rolled = service.rollback("cryo-import", first_id)

    assert rolled["ok"] is True
    restored = document.read_text("utf-8")
    meta, body = _parse_frontmatter(restored)
    assert meta["description"] == "Import me whole"
    assert meta["requirements"] == "[gpu, cuda]"
    assert "x-vendor-ext:" in restored
    assert body.strip() == "Original imported body."
    assert rolled["review"]["requirements"] == first["review"]["requirements"]
    assert rolled["review"]["capabilities"]["network"]["mode"] == "host_only"
    assert rolled["review"] == service._review_from_document(restored)


def test_imported_catalog_readiness_stays_beside_disabled_state(tmp_path):
    _config, service = _service(tmp_path)
    service.import_document(content=IMPORT_SEED)
    service.set_enabled("cryo-import", False)
    row = next(item for item in service.catalog() if item["name"] == "cryo-import")
    assert row["enabled"] is False
    assert set(row["requirements"]) == {"cuda", "gpu"}
    assert row["capabilities"]["network"]["mode"] == "host_only"
    assert row["readiness"]["state"] in {"ready", "needs_setup", "unknown"}
    assert "ready" in row
    assert row["origin"] == "user"


def test_project_import_keeps_seed_fields_and_stamps_user_origin(tmp_path):
    config, _personal = _service(tmp_path)
    service = SkillCustomizationService(
        SkillLoader(cfg=config), scope="project", project_id="proj-a"
    )

    imported = service.import_document(content=IMPORT_SEED)

    assert imported["ok"] is True
    assert imported["origin"] == "user"
    root = service.versions.scope_root(scope="project", project_id="proj-a")
    saved = (root / imported["slug"] / "SKILL.md").read_text("utf-8")
    assert "x-vendor-ext:" in saved
    assert "requirements: [gpu, cuda]" in saved
    assert "origin: user" in saved
    personal = _personal.loader.user_skills_dir() / "cryo-import" / "SKILL.md"
    assert not personal.exists()


def test_gateway_skill_routes_answer_a_real_status_and_share_enablement(tmp_path):
    """This used to assert `(200, {"error": ...})` three times, and its name
    said "keep soft errors".

    The service returning soft dictionaries is a deliberate design and is kept.
    What was not deliberate is the gateway answering 200 for them. Three
    consequences: the body never reached `errors.public_failure`, so it carried
    no `request_id`; a client had nothing to branch on but the prose, which the
    contract documents as explicitly not an interface; and `api()` in the web
    client only throws on a non-2xx, so the Customize editor reported "saved"
    and closed the modal on a save that had not happened.

    The status is read from the code, never from the message.
    """
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
        400,
        {"error": "skill name is required", "code": "skill_name_required"},
    )
    # A name already taken by a bundled skill is a conflict, not a bad request:
    # the input is well-formed and the collision is about state.
    assert call(
        first,
        "POST",
        "/skills",
        {"name": "Builtin", "body": "shadow"},
    ) == (
        409,
        {
            "error": "'builtin' collides with a built-in skill — pick a different name",
            "code": "skill_name_conflict",
        },
    )
    # Refusing to delete a bundled skill is policy, so 403 rather than 404 --
    # the skill plainly exists, and saying "not found" would be a lie the user
    # can immediately disprove.
    assert call(first, "DELETE", "/skills/Builtin") == (
        403,
        {
            "error": "only user-authored skills can be deleted",
            "code": "skill_read_only",
        },
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
        404,
        {"error": "skill not found", "code": "skill_not_found"},
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
        "error": "only user-authored skills can be deleted",
        "code": "skill_read_only",
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
        "error": "unsafe user skill path",
        "code": "skill_name_unsafe",
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
    ) == {"error": "unsafe user skill path", "code": "skill_name_unsafe"}
    assert outside_document.read_text("utf-8") == "sentinel\n"

    service.create_or_update("Real Skill", "", "real body")
    real_root = user_directory / "real-skill"
    sentinel = real_root / "sentinel.txt"
    sentinel.write_text("keep real skill\n", "utf-8")
    alias = user_directory / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    service.loader.discover()

    assert service.delete("alias") == {
        "error": "unsafe user skill path",
        "code": "skill_name_unsafe",
    }
    assert alias.is_symlink()
    assert sentinel.read_text("utf-8") == "keep real skill\n"


def test_every_declared_failure_code_has_a_status_and_vice_versa(tmp_path):
    """The code is the contract; the status is a projection of it.

    Two ways that drifts. A new `_fail("skill_whatever", ...)` with no entry in
    the table answers 400 by default -- safe, but it means a route quietly
    stopped distinguishing a conflict from a bad request. And an entry left in
    the table after its code is gone reads as surface that exists.
    """
    import pathlib
    import re

    from openai4s.server import skills as skills_module

    source = pathlib.Path(skills_module.__file__).read_text("utf-8")
    used = set(re.findall(r'_fail\(\s*"([a-z_]+)"', source))
    declared = set(skills_module.SKILL_FAILURE_STATUS)

    assert not (used - declared), (
        "these failure codes are returned but have no HTTP status, so they "
        f"fall back to 400: {sorted(used - declared)}"
    )
    assert not (declared - used), (
        f"these statuses are declared for codes nothing returns: "
        f"{sorted(declared - used)}"
    )


def test_the_status_is_read_from_the_code_never_from_the_message(tmp_path):
    """Mapping prose to a status is the thing this change removed.

    A payload whose code is unrecognised answers 400, not 200: a failure whose
    *kind* is unknown is still a failure, and answering 200 is what let a
    failed save be reported to the user as a successful one.
    """
    assert gateway_mod._skill_result_status({"ok": True}) == 200
    assert gateway_mod._skill_result_status({"slug": "x", "name": "y"}) == 200
    # No error key -> success, even if other fields look alarming.
    assert gateway_mod._skill_result_status({"error": ""}) == 200

    assert (
        gateway_mod._skill_result_status(
            {"error": "skill not found", "code": "skill_not_found"}
        )
        == 404
    )
    # The message says "not found"; the code says otherwise. The code wins.
    assert (
        gateway_mod._skill_result_status(
            {"error": "skill not found", "code": "skill_name_conflict"}
        )
        == 409
    )
    assert gateway_mod._skill_result_status({"error": "boom", "code": "made_up"}) == 400
    assert gateway_mod._skill_result_status({"error": "boom"}) == 400


def test_a_failed_skill_save_is_not_reported_to_the_user_as_a_success(tmp_path):
    """The consequence that made this worth changing.

    `api()` in the web client throws only on a non-2xx. The Customize editor's
    save handler does not inspect the body, so while these routes answered 200
    with an error, a rejected save closed the modal and showed "saved" -- and
    the skill was not written. Asserting the status here is asserting that the
    client's success path can no longer be reached by a failure.
    """
    config, service = _service(tmp_path)
    handler_class = gateway_mod.make_handler(
        config, gateway_mod.WSHub(), SimpleNamespace()
    )
    handler = object.__new__(handler_class)
    replies = []
    handler._query = lambda: {}
    handler._json = lambda value, code=200: replies.append((code, value))

    handler._body = lambda: {"name": "", "body": "x"}
    handler._api("POST", "/skills")
    status, body = replies[-1]
    assert status >= 400, "a rejected save still answers 2xx; api() will not throw"
    assert body["error"] and body["code"]

    # And the skill really was not written, which is what the user was being
    # told had happened.
    assert service.get("").get("error")
