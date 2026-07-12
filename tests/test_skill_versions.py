"""Offline contracts for content-addressed Skill install and rollback."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.server.skills import SkillCustomizationService
from openai4s.skills_loader import SkillLoader, SkillVersionService
from openai4s.store import Store, get_store


def _config(tmp_path: Path) -> Config:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    return Config(data_dir=tmp_path / "data", skills_dir=bundled)


def _document(name: str, body: str, *, origin: str = "draft") -> str:
    return (
        f"---\nname: {name}\ndescription: version test\n"
        f"origin: {origin}\n---\n\n{body}\n"
    )


def test_personal_install_upgrade_publish_and_rollback_preserve_exact_package(
    tmp_path,
):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        first_files = {
            "SKILL.md": _document("Analysis Helper", "first recipe"),
            "kernel.py": "VALUE = 'first'\n",
            "resources/schema.json": b'{"version":1}\n',
        }
        first = versions.install("Analysis Helper", first_files)
        first_id = first["version_id"]
        assert first["manifest"]["sidecar"] == {
            "present": True,
            "sha256": first["manifest"]["files"][1]["sha256"],
            "size": len(first_files["kernel.py"]),
            "gate": {"ok": True, "error": None},
        }

        second_files = {
            **first_files,
            "SKILL.md": _document("Analysis Helper", "second recipe"),
            "kernel.py": "VALUE = 'second'\n",
        }
        second = versions.upgrade("Analysis Helper", second_files)
        second_id = second["version_id"]
        assert second_id != first_id
        root = cfg.data_dir / "user-skills" / "analysis-helper"
        assert (root / "kernel.py").read_text("utf-8") == "VALUE = 'second'\n"

        published = versions.publish("Analysis Helper")
        assert published["version_id"] not in {first_id, second_id}
        assert "origin: personal" in (root / "SKILL.md").read_text("utf-8")

        rolled_back = versions.rollback("Analysis Helper", first_id)
        assert rolled_back["previous_version_id"] == published["version_id"]
        assert (root / "SKILL.md").read_bytes() == first_files["SKILL.md"].encode(
            "utf-8"
        )
        assert (root / "kernel.py").read_bytes() == first_files["kernel.py"].encode(
            "utf-8"
        )
        assert (root / "resources" / "schema.json").read_bytes() == first_files[
            "resources/schema.json"
        ]

        history = versions.history("Analysis Helper")
        assert history["installation"]["active_version_id"] == first_id
        assert [event["event"] for event in history["events"]] == [
            "rolled_back",
            "published",
            "upgraded",
            "installed",
        ]
        frozen = store.skill_versions().get_version(first_id, include_files=True)
        assert frozen["files"] == {
            key: value.encode("utf-8") if isinstance(value, str) else value
            for key, value in first_files.items()
        }
    finally:
        store.close()


def test_identical_package_reuses_content_address_but_keeps_events(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        files = {"SKILL.md": _document("Stable", "same bytes")}
        first = versions.install("Stable", files)
        second = versions.install("Stable", files, event="upgraded")
        assert second["version_id"] == first["version_id"]
        assert [row["event"] for row in versions.history("Stable")["events"]] == [
            "upgraded",
            "installed",
        ]
        assert (
            store._conn.execute("SELECT count(*) FROM skill_versions").fetchone()[0]
            == 1
        )
        assert (
            store._conn.execute("SELECT count(*) FROM skill_blobs").fetchone()[0] == 1
        )
        with pytest.raises(PermissionError, match="skill_blobs"):
            store.query("SELECT content FROM skill_blobs")
    finally:
        store.close()


def test_delete_keeps_immutable_history_and_old_version_can_be_reactivated(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        installed = versions.install(
            "Recoverable",
            {"SKILL.md": _document("Recoverable", "retained")},
        )
        root = cfg.data_dir / "user-skills" / "recoverable"
        assert versions.delete("Recoverable")["ok"] is True
        assert not root.exists()
        assert (
            versions.history("Recoverable")["installation"]["active_version_id"] is None
        )
        restored = versions.rollback("Recoverable", installed["version_id"])
        assert restored["ok"] is True
        assert root.joinpath("SKILL.md").read_text("utf-8") == _document(
            "Recoverable", "retained"
        )
        assert [row["event"] for row in versions.history("Recoverable")["events"]] == [
            "rolled_back",
            "deleted",
            "installed",
        ]
    finally:
        store.close()


def test_version_history_and_blob_bytes_survive_store_reopen(tmp_path):
    cfg = _config(tmp_path)
    first_store = Store(cfg.db_path)
    first_service = SkillVersionService(
        cfg,
        repository=first_store.skill_versions(),
    )
    installed = first_service.install(
        "Durable",
        {
            "SKILL.md": _document("Durable", "persisted"),
            "kernel.py": "VALUE = 7\n",
        },
    )
    first_store.close()

    reopened = Store(cfg.db_path)
    try:
        service = SkillVersionService(cfg, repository=reopened.skill_versions())
        assert (
            service.history("Durable")["installation"]["active_version_id"]
            == installed["version_id"]
        )
        frozen = reopened.skill_versions().get_version(
            installed["version_id"],
            include_files=True,
        )
        assert frozen["files"]["kernel.py"] == b"VALUE = 7\n"
        assert (
            frozen["manifest"]["sidecar"]["sha256"]
            == hashlib.sha256(b"VALUE = 7\n").hexdigest()
        )
    finally:
        reopened.close()


def test_default_version_service_rebinds_after_cached_store_generation_closes(tmp_path):
    cfg = _config(tmp_path)
    first_store = get_store(cfg.db_path)
    service = SkillVersionService(cfg)
    installed = service.install(
        "Rebound",
        {"SKILL.md": _document("Rebound", "generation one")},
    )
    first_store.close()

    assert (
        service.history("Rebound")["installation"]["active_version_id"]
        == installed["version_id"]
    )
    replacement = get_store(cfg.db_path)
    assert replacement is not first_store
    replacement.close()


def test_project_versions_are_isolated_and_override_personal_only_in_scope(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        versions.install(
            "Shared Method",
            {
                "SKILL.md": _document("Shared Method", "personal recipe"),
                "kernel.py": "SCOPE = 'personal'\n",
            },
        )
        versions.install(
            "Shared Method",
            {
                "SKILL.md": _document("Shared Method", "project recipe"),
                "kernel.py": "SCOPE = 'project'\n",
            },
            scope="project",
            project_id="project/with unsafe-looking id",
        )

        personal = SkillLoader(cfg=cfg).discover()["shared-method"]
        project = SkillLoader(
            cfg=cfg,
            project_id="project/with unsafe-looking id",
        ).discover()["shared-method"]
        assert personal.source == "user"
        assert "personal recipe" in personal.doc
        assert project.source == "project"
        assert "project recipe" in project.doc
        assert project.root.parent != personal.root.parent
        assert project.root.resolve().is_relative_to(cfg.data_dir.resolve())
        manifest = SkillLoader(
            cfg=cfg,
            capabilities=store.capability_state(
                project_id="project/with unsafe-looking id"
            ),
            project_id="project/with unsafe-looking id",
        ).bootstrap_manifest(persist=False)
        entry = next(
            item for item in manifest["entries"] if item["name"] == "Shared Method"
        )
        assert entry["distribution_scope"] == "project"
        assert entry["document_sha256"] == project.document_sha256
        assert entry["sidecar"]["sha256"] == project.sidecar_sha256
    finally:
        store.close()


def test_project_customization_service_writes_only_its_scoped_overlay(tmp_path):
    cfg = _config(tmp_path)
    personal_service = SkillCustomizationService(SkillLoader(cfg=cfg))
    personal_service.create_or_update(
        "Project Protocol",
        "personal procedure",
        "Personal fallback recipe",
    )
    service = SkillCustomizationService(
        SkillLoader(cfg=cfg),
        scope="project",
        project_id="project-a",
    )
    created = service.create_or_update(
        "Project Protocol",
        "local procedure",
        "Project-only recipe",
        existing=True,
    )
    assert created["ok"] is True
    personal = SkillLoader(cfg=cfg).get("Project Protocol")
    assert personal is not None
    assert "Personal fallback recipe" in personal.doc
    scoped = SkillLoader(cfg=cfg, project_id="project-a")
    skill = scoped.get("Project Protocol")
    assert skill is not None
    assert skill.source == "project"
    assert "Project-only recipe" in skill.doc
    history = service.history("Project Protocol")
    assert history["installation"]["scope"] == "project"
    assert history["installation"]["scope_id"] == "project-a"


def test_bundled_skill_is_never_installable_publishable_or_rollback_target(tmp_path):
    cfg = _config(tmp_path)
    builtin = cfg.skills_dir / "builtin"
    builtin.mkdir()
    (builtin / "SKILL.md").write_text(
        _document("Trusted Builtin", "trusted", origin="openai4s"),
        "utf-8",
    )
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        with pytest.raises(PermissionError, match="read-only bundled"):
            versions.install(
                "Trusted Builtin",
                {"SKILL.md": _document("Trusted Builtin", "shadow")},
            )
        with pytest.raises(KeyError, match="no installed Skill"):
            versions.rollback("Trusted Builtin", "skillv-missing")
        assert (builtin / "SKILL.md").read_text("utf-8").endswith("trusted\n")
    finally:
        store.close()


def test_sidecar_gate_and_package_path_limits_fail_before_activation(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    versions = SkillVersionService(cfg, repository=store.skill_versions())
    try:
        with pytest.raises(ValueError, match="compile gate"):
            versions.install(
                "Broken",
                {
                    "SKILL.md": _document("Broken", "draft"),
                    "kernel.py": "def nope(\n",
                },
            )
        assert not (cfg.data_dir / "user-skills" / "broken").exists()
        with pytest.raises(ValueError, match="unsafe Skill package path"):
            versions.install(
                "Escape",
                {
                    "SKILL.md": _document("Escape", "draft"),
                    "../outside": b"no",
                },
            )
        with pytest.raises(ValueError, match="non-portable duplicate"):
            versions.install(
                "Portable",
                {
                    "SKILL.md": _document("Portable", "draft"),
                    "Resource.txt": b"one",
                    "resource.txt": b"two",
                },
            )
        versions.install(
            "Name With Spaces",
            {"SKILL.md": _document("Name With Spaces", "first owner")},
        )
        with pytest.raises(ValueError, match="already active"):
            versions.install(
                "name-with-spaces",
                {"SKILL.md": _document("name-with-spaces", "second owner")},
            )
        assert (
            cfg.data_dir / "user-skills" / "name-with-spaces" / "SKILL.md"
        ).read_text("utf-8") == _document("Name With Spaces", "first owner")
        draft = versions.install(
            "Broken Draft",
            {
                "SKILL.md": _document("Broken Draft", "editable draft"),
                "kernel.py": "def unfinished(\n",
            },
            require_sidecar_gate=False,
        )
        with pytest.raises(ValueError, match="compile gate"):
            versions.publish("Broken Draft")
        assert (
            versions.history("Broken Draft")["installation"]["active_version_id"]
            == draft["version_id"]
        )
        assert "origin: draft" in (
            cfg.data_dir / "user-skills" / "broken-draft" / "SKILL.md"
        ).read_text("utf-8")
    finally:
        store.close()


def test_repository_rejects_manifest_that_lies_about_sidecar_bytes(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    try:
        content = b"---\nname: Tamper\norigin: draft\n---\nBody\n"
        sidecar = b"VALUE = 1\n"
        digest = hashlib.sha256(content).hexdigest()
        sidecar_digest = hashlib.sha256(sidecar).hexdigest()
        manifest = {
            "schema_version": 1,
            "name": "Tamper",
            "slug": "tamper",
            "origin": "draft",
            "distribution_scope": "personal",
            "document_sha256": digest,
            "sidecar": {
                "present": True,
                "sha256": "0" * 64,
                "size": len(sidecar),
                "gate": {"ok": True, "error": None},
            },
            "files": [
                {"path": "SKILL.md", "sha256": digest, "size": len(content)},
                {
                    "path": "kernel.py",
                    "sha256": sidecar_digest,
                    "size": len(sidecar),
                },
            ],
        }
        with pytest.raises(ValueError, match="sidecar digest mismatch"):
            store.skill_versions().put_version(
                manifest,
                {"SKILL.md": content, "kernel.py": sidecar},
            )
    finally:
        store.close()


def test_customize_flow_versions_resources_and_exposes_safe_rollback(tmp_path):
    cfg = _config(tmp_path)
    service = SkillCustomizationService(SkillLoader(cfg=cfg))
    created = service.create_or_update("Web Versioned", "one", "First body")
    assert created["ok"] is True
    root = cfg.data_dir / "user-skills" / "web-versioned"
    (root / "kernel.py").write_text("VALUE = 2\n", "utf-8")
    (root / "resources").mkdir()
    (root / "resources" / "note.txt").write_text("kept\n", "utf-8")
    updated = service.create_or_update(
        "Web Versioned",
        "two",
        "Second body",
        existing=True,
    )
    assert updated["ok"] is True
    history = service.history("Web Versioned")
    assert [event["event"] for event in history["events"]] == [
        "upgraded",
        "installed",
    ]
    first_id = history["events"][1]["to_version_id"]
    rolled_back = service.rollback("Web Versioned", first_id)
    assert rolled_back["ok"] is True
    assert service.get("Web Versioned")["body"] == "First body\n"
    assert not (root / "kernel.py").exists()
    assert not (root / "resources").exists()


def test_failed_pointer_switch_restores_previous_runtime_directory(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    base_repository = store.skill_versions()
    versions = SkillVersionService(cfg, repository=base_repository)
    try:
        first = versions.install(
            "Atomic",
            {"SKILL.md": _document("Atomic", "old")},
        )
        root = cfg.data_dir / "user-skills" / "atomic"

        class FailingActivationRepository:
            def __getattr__(self, name):
                return getattr(base_repository, name)

            def activate(self, *args, **kwargs):
                raise RuntimeError("simulated pointer failure")

        failing = SkillVersionService(
            cfg,
            repository=FailingActivationRepository(),
        )
        with pytest.raises(RuntimeError, match="simulated pointer failure"):
            failing.upgrade(
                "Atomic",
                {"SKILL.md": _document("Atomic", "new")},
            )
        assert root.joinpath("SKILL.md").read_text("utf-8") == _document(
            "Atomic", "old"
        )
        assert (
            versions.history("Atomic")["installation"]["active_version_id"]
            == first["version_id"]
        )
    finally:
        store.close()
