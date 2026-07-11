"""Durable Skill/Specialist capability policy contracts."""

from __future__ import annotations

import importlib
import sys

import pytest

from openai4s.config import Config
from openai4s.host.skills import SkillService
from openai4s.skills_loader import SkillLoader
from openai4s.store import Store, get_store


def _skill(root, directory: str, name: str, keyword: str) -> None:
    target = root / directory
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: recipe for {keyword}\n"
        "origin: openai4s\n"
        "version: 3\n"
        "---\n\n"
        f"Use the unique keyword {keyword}.\n",
        "utf-8",
    )
    (target / "kernel.py").write_text(f"VALUE = {keyword!r}\n", "utf-8")


def test_capability_scope_precedence_events_and_restart(tmp_path):
    database = tmp_path / "state.db"
    store = Store(database)
    store.set_capability_enabled("skill", "ALPHA", False)
    store.set_capability_enabled(
        "skill", "ALPHA", True, scope="project", scope_id="project-a"
    )
    store.set_capability_enabled(
        "skill", "ALPHA", False, scope="session", scope_id="session-a"
    )

    assert not store.capability_state().is_enabled("skill", "alpha")
    assert store.capability_state(project_id="project-a").is_enabled("skill", "alpha")
    assert not store.capability_state(
        project_id="project-a", session_id="session-a"
    ).is_enabled("skill", "alpha")
    events = store.capability_state().repository.list_events(kind="skill", name="alpha")
    assert [event["event"] for event in events] == [
        "disabled",
        "enabled",
        "disabled",
    ]
    store.close()

    reopened = Store(database)
    state = reopened.capability_snapshot(
        "skill",
        ["alpha", "never-configured"],
        project_id="project-a",
        session_id="session-a",
    )
    assert state["alpha"]["enabled"] is False
    assert state["alpha"]["scope"] == "session"
    assert state["never-configured"]["enabled"] is True
    assert state["never-configured"]["scope"] == "default"
    reopened.close()


def test_default_skill_loader_survives_store_generation_replacement(tmp_path):
    """A long-lived loader must not retain a closed SQLite repository."""

    bundled = tmp_path / "skills"
    _skill(bundled, "durable", "Durable", "lifecycletoken")
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    loader = SkillLoader(cfg=config)
    loader.discover()
    first = get_store(config.db_path)
    first.set_capability_enabled("skill", "Durable", False)
    assert loader.get("Durable") is None

    first.close()
    second = get_store(config.db_path)
    assert second is not first
    assert loader.get("Durable") is None

    second.set_capability_enabled("skill", "Durable", True)
    assert loader.get("Durable") is not None
    second.close()


def test_disabled_skill_is_consistent_across_prompt_search_read_and_bootstrap(
    tmp_path,
):
    bundled = tmp_path / "skills"
    _skill(bundled, "allowed_skill", "Allowed Skill", "allowtoken")
    _skill(bundled, "blocked_skill", "Blocked Skill", "blocktoken")
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    store = Store(config.db_path)
    store.set_capability_enabled("skill", "Blocked Skill", False)
    loader = SkillLoader(
        cfg=config,
        capabilities=store.capability_state(
            project_id="project-a",
            session_id="session-a",
        ),
    )

    # Discovery still sees disabled content so Customize can re-enable it; all
    # agent-facing projections use the same policy predicate.
    assert set(loader.discover()) == {"allowed_skill", "blocked_skill"}
    assert "Allowed Skill" in loader.system_context()
    assert "Blocked Skill" not in loader.system_context()
    assert [item["name"] for item in loader.catalog()] == ["Allowed Skill"]
    full_catalog = {
        item["name"]: item for item in loader.catalog(include_disabled=True)
    }
    assert full_catalog["Blocked Skill"]["enabled"] is False
    assert loader.search("blocktoken") == []
    assert loader.get("Blocked Skill") is None

    host_service = SkillService(config)
    with pytest.raises(KeyError, match="no such skill"):
        host_service.read({"name": "Blocked Skill", "path": "SKILL.md"})

    manifest = loader.bootstrap_manifest()
    blocked = next(
        entry for entry in manifest["entries"] if entry["name"] == "Blocked Skill"
    )
    allowed = next(
        entry for entry in manifest["entries"] if entry["name"] == "Allowed Skill"
    )
    assert blocked["enabled"] is False
    assert blocked["sidecar"]["loaded"] is False
    assert allowed["sidecar"]["loaded"] is False
    assert manifest["load_events"] == []
    stored = store.capability_state().repository.latest_manifest(
        "session-a", kind="skill"
    )
    assert stored is not None
    assert all(entry["sidecar"]["loaded"] is False for entry in stored["entries"])

    # The generated import gate is the actual sidecar exposure boundary.  It
    # records an event only after exec_module succeeds.
    code = loader.bootstrap_code()
    compile(code, "<skill-bootstrap>", "exec")
    namespace: dict = {}
    original_path = list(sys.path)
    try:
        exec(code, namespace)  # noqa: S102 - generated bootstrap is under test
        with pytest.raises(ModuleNotFoundError, match="disabled"):
            importlib.import_module("blocked_skill.kernel")
        module = importlib.import_module("allowed_skill.kernel")
        assert module.VALUE == "allowtoken"
        runtime_manifest = namespace["__openai4s_skill_bootstrap_manifest__"]
        runtime_allowed = next(
            entry
            for entry in runtime_manifest["entries"]
            if entry["name"] == "Allowed Skill"
        )
        assert runtime_allowed["sidecar"]["loaded"] is True
        assert runtime_manifest["load_events"][0]["event"] == "sidecar_loaded"
    finally:
        sys.path[:] = original_path
        sys.meta_path[:] = [
            finder
            for finder in sys.meta_path
            if not getattr(finder, "_openai4s_skill_gate", False)
        ]
        for module_name in list(sys.modules):
            if module_name.partition(".")[0] in {"allowed_skill", "blocked_skill"}:
                sys.modules.pop(module_name, None)
        store.close()


def test_sidecar_hash_version_and_explicit_load_event_are_durable(tmp_path):
    bundled = tmp_path / "skills"
    _skill(bundled, "versioned", "Versioned", "vtoken")
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    store = Store(config.db_path)
    loader = SkillLoader(
        cfg=config,
        capabilities=store.capability_state(session_id="session-version"),
    )
    first = loader.bootstrap_manifest()
    entry = first["entries"][0]
    assert entry["version"] == "3"
    assert len(entry["document_sha256"]) == 64
    assert len(entry["sidecar"]["sha256"]) == 64
    assert entry["sidecar"]["loaded"] is False

    event = loader.record_sidecar_loaded(
        "Versioned",
        module="versioned.kernel",
        manifest_id=first["manifest_id"],
    )
    assert event["event"] == "sidecar_loaded"
    persisted = store.capability_state().repository.list_events(
        kind="skill", name="Versioned"
    )
    assert persisted[0]["metadata"]["manifest_id"] == first["manifest_id"]
    assert persisted[0]["metadata"]["sidecar_sha256"] == entry["sidecar"]["sha256"]
    store.close()


def test_specialist_list_and_resolve_use_the_same_scoped_policy(tmp_path):
    database = tmp_path / "specialists.db"
    store = Store(database)
    store.upsert_agent(name="CHEMIST", description="chemistry")
    store.upsert_agent(name="PHYSICIST", description="physics")
    store.set_capability_enabled("specialist", "CHEMIST", False)

    assert [profile["name"] for profile in store.list_agents()] == ["PHYSICIST"]
    assert store.get_agent("CHEMIST") is None
    disabled = store.get_agent("CHEMIST", include_disabled=True)
    assert disabled is not None and disabled["enabled"] is False
    assert (
        store.specialist_profiles().filter_profiles(
            [{"name": "CHEMIST", "description": "built-in"}]
        )
        == []
    )
    assert (
        store.specialist_profiles().filter_profiles(
            [{"name": "CHEMIST", "description": "built-in"}],
            include_disabled=True,
        )[0]["enabled"]
        is False
    )

    store.set_capability_enabled(
        "specialist",
        "CHEMIST",
        True,
        scope="project",
        scope_id="project-a",
    )
    assert store.get_agent("CHEMIST", project_id="project-a")["enabled"] is True
    assert store.get_agent("CHEMIST", project_id="project-b") is None
    store.close()

    reopened = Store(database)
    assert reopened.get_agent("CHEMIST") is None
    assert reopened.get_agent("CHEMIST", project_id="project-a") is not None
    reopened.close()
