from __future__ import annotations

import base64
import hashlib
import importlib
import sqlite3
import sys
import threading

import pytest

from openai4s.kernel.recovery import (
    REPLAY_NEVER,
    REPLAY_SAFE,
    BootstrapManifest,
    KernelRecoveryOrchestrator,
    RecoveryRecipe,
    RecoveryStep,
    SidecarManifest,
    frozen_sidecar_bootstrap_code,
    merge_bootstrap_sidecar_loads,
    replay_safety_error,
    sidecar_from_load_event,
)
from openai4s.storage.recovery import RecoveryJournalRepository


class _Candidate:
    def __init__(self, generation_id="candidate-1") -> None:
        self.generation_id = generation_id
        self.shutdown_calls = 0
        self.symbols = {"python": {"data", "model"}}
        self.artifacts = {"prediction.csv": "hash-prediction"}
        self.environment = {
            "interpreter": "/env/bin/python",
            "python_version": "3.12",
            "sdk_version": "sdk-1",
            "provenance_version": "prov-1",
        }

    def shutdown(self):
        self.shutdown_calls += 1


def _manifest():
    sidecar = SidecarManifest(
        "stats",
        b"def mean(values):\n    return sum(values) / len(values)\n",
        order=0,
        exports=("mean",),
        source_path="/snapshot/stats/kernel.py",
    )
    return BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory="/workspace",
        environment={"name": "science", "hash": "env-hash"},
        sdk_version="sdk-1",
        provenance_version="prov-1",
        sidecars=(sidecar,),
    )


def _orchestrator(candidate, events, published, executed, *, bootstrap=None):
    return KernelRecoveryOrchestrator(
        build_candidate=lambda manifest: candidate,
        bootstrap_candidate=bootstrap
        or (lambda current, manifest: events.append("bootstrap")),
        hydrate_workspace=lambda current, payload: events.append(
            ("workspace", dict(payload))
        ),
        hydrate_artifact=lambda current, payload: events.append(
            ("artifact", dict(payload))
        ),
        execute_cell=lambda current, code, language: executed.append((language, code))
        or {"error": None},
        inspect_symbols=lambda current, language: current.symbols.get(language, set()),
        artifact_digest=lambda current, name: current.artifacts.get(name),
        inspect_environment=lambda current: current.environment,
        publish=lambda current: published.append(current.generation_id),
        journal=lambda event: events.append(
            ("journal", event["phase"], event["status"])
        ),
    )


def test_bootstrap_manifest_snapshots_exact_sidecar_bytes_and_detects_tampering():
    manifest = _manifest()
    record = manifest.record()

    restored = BootstrapManifest.from_record(record)
    assert restored.manifest_id == manifest.manifest_id
    assert restored.sidecars[0].source == manifest.sidecars[0].source
    assert restored.sidecars[0].sha256 == record["sidecars"][0]["sha256"]
    assert record["sidecars"][0]["source_path"] == "/snapshot/stats/kernel.py"

    record["sidecars"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        BootstrapManifest.from_record(record)


def test_bootstrap_manifest_v2_binds_worker_packages_locale_and_protocol_versions():
    base = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="unknown",
        working_directory="/workspace",
        environment={"environment_name": "science", "environment_root": "/env"},
    )
    observed = {
        "interpreter": "/env/bin/python",
        "runtime_version": "3.12.4",
        "prefix": "/env",
        "base_prefix": "/base",
        "sdk_version": "sdk-2",
        "provenance_version": "prov-2",
        "host_capability_version": "host-2",
        "package_manifest": [
            {"name": "zeta", "version": "2"},
            {"name": "Alpha", "version": "1"},
            {"name": "ALPHA", "version": "ignored-duplicate"},
        ],
        "locale": {"preferred_encoding": "UTF-8", "lc_ctype": "C.UTF-8"},
    }

    bound = base.with_observed_environment(observed)
    record = bound.record()
    restored = BootstrapManifest.from_record(record)

    assert record["version"] == 2
    assert bound.package_manifest == (("Alpha", "1"), ("zeta", "2"))
    assert bound.environment["interpreter_prefix"] == "/env"
    assert bound.environment["base_prefix"] == "/base"
    assert bound.host_capability_version == "host-2"
    assert len(bound.environment_hash or "") == 64
    assert restored == bound
    assert (
        base.with_observed_environment(
            {**observed, "package_manifest": [{"name": "Alpha", "version": "9"}]}
        ).environment_hash
        != bound.environment_hash
    )

    legacy = {
        key: value
        for key, value in base.record().items()
        if key
        not in {
            "host_capability_version",
            "package_manifest",
            "locale",
            "environment_hash",
        }
    }
    legacy["version"] = 1
    parsed_legacy = BootstrapManifest.from_record(legacy)
    assert parsed_legacy.version == 1
    assert parsed_legacy.record() == legacy


def _sidecar_event(module: str, source: bytes, order: int) -> dict:
    parts = module.split(".")
    local_import_roots = list(dict.fromkeys(parts[:-1]))
    if len(parts) == 2 and "skills" not in local_import_roots:
        local_import_roots.append("skills")
    return {
        "event": "sidecar_loaded",
        "skill_name": module.partition(".")[0],
        "module": module,
        "source_b64": base64.b64encode(source).decode("ascii"),
        "sha256": hashlib.sha256(source).hexdigest(),
        "expected_sha256": hashlib.sha256(source).hexdigest(),
        "source_path": f"/mutable/{parts[-2] if len(parts) >= 2 else module}/kernel.py",
        "local_import_roots": local_import_roots,
        "order": order,
        "import_mode": "module",
    }


def _frozen_policy(
    sidecar: SidecarManifest,
    *,
    direct: bool = False,
    collection: str | None = None,
) -> dict:
    skill_directory = sidecar.name.split(".")[-2]
    return {
        "_o4s_skill_dirs": {skill_directory},
        "_o4s_skill_entries": {
            skill_directory: {"sidecar": {"sha256": sidecar.sha256}}
        },
        "_o4s_direct_skill_dirs": {skill_directory} if direct else set(),
        "_o4s_collection_members": (
            {collection: frozenset({skill_directory})} if collection else {}
        ),
        "_o4s_catalog_namespace": "skills",
        "_o4s_denied_skills": set(),
        "_o4s_disabled_skills": set(),
        "_o4s_skill_load_order": [0],
    }


def test_runtime_sidecar_events_extend_manifest_in_exact_load_order():
    bootstrap = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory="/workspace",
    ).record()
    first = _sidecar_event("alpha.kernel", b"VALUE = 'alpha'\n", 0)
    second = _sidecar_event("beta.kernel", b"VALUE = 'beta'\n", 1)

    after_first = merge_bootstrap_sidecar_loads(bootstrap, [first])
    complete = merge_bootstrap_sidecar_loads(after_first, [second])
    restored = BootstrapManifest.from_record(complete)

    assert [item.name for item in restored.sidecars] == [
        "alpha.kernel",
        "beta.kernel",
    ]
    assert [item.order for item in restored.sidecars] == [0, 1]
    assert [item["module"] for item in complete["loaded_sidecars"]] == [
        "alpha.kernel",
        "beta.kernel",
    ]
    # Processing an already-committed exact event is idempotent.
    assert merge_bootstrap_sidecar_loads(complete, [first]) == complete


def test_collection_qualified_sidecar_event_is_recoverable():
    bootstrap = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory="/workspace",
    ).record()
    event = _sidecar_event(
        "bioskills.collection-member.kernel", b"VALUE = 'qualified'\n", 0
    )

    merged = merge_bootstrap_sidecar_loads(bootstrap, [event])
    sidecar = BootstrapManifest.from_record(merged).sidecars[0]
    try:
        exec(
            frozen_sidecar_bootstrap_code(sidecar),
            _frozen_policy(sidecar, collection="bioskills"),
        )
        module = importlib.import_module("bioskills.collection-member.kernel")
        assert module.VALUE == "qualified"
        assert importlib.import_module("collection-member.kernel") is module
        assert merged["loaded_sidecars"][0]["module"] == sidecar.name
    finally:
        sys.modules.pop("bioskills.collection-member.kernel", None)
        sys.modules.pop("bioskills.collection-member", None)
        sys.modules.pop("bioskills", None)


def test_unicode_sidecar_event_is_recoverable():
    event = _sidecar_event("café.kernel", b"VALUE = 'unicode'\n", 0)

    sidecar = sidecar_from_load_event(event)

    assert sidecar.name == "café.kernel"


@pytest.mark.parametrize("module", ["foo bar.kernel", "x:y.kernel"])
def test_non_identifier_sidecar_event_spellings_remain_recoverable(module):
    event = _sidecar_event(module, b"VALUE = 1\n", 0)

    assert sidecar_from_load_event(event).name == module


@pytest.mark.parametrize(
    "module",
    ["kernel", "too.many.parts.kernel", "bad/name.kernel", "empty..kernel"],
)
def test_sidecar_event_rejects_malformed_qualified_modules(module):
    event = _sidecar_event(module, b"VALUE = 1\n", 0)

    with pytest.raises(ValueError, match=r"requires a \*\.kernel module"):
        sidecar_from_load_event(event)


def test_sidecar_event_requires_complete_local_alias_roots():
    event = _sidecar_event("stats.kernel", b"VALUE = 1\n", 0)
    event.pop("local_import_roots")
    with pytest.raises(ValueError, match="requires local import roots"):
        sidecar_from_load_event(event)

    event = _sidecar_event("skills.stats.kernel", b"VALUE = 1\n", 0)
    event["local_import_roots"] = ["skills"]
    with pytest.raises(ValueError, match="omit a module parent"):
        sidecar_from_load_event(event)


def test_frozen_sidecar_never_replaces_an_existing_module_for_an_alias_root():
    import json

    event = _sidecar_event("victim.kernel", b"VALUE = 1\n", 0)
    event["local_import_roots"] = ["victim", "json"]
    sidecar = sidecar_from_load_event(event)
    original_json = sys.modules["json"]
    original_json_path = list(original_json.__path__)

    # Even a catalog that deliberately claims a stdlib spelling cannot make
    # recovery replace an already loaded ordinary module with a namespace.
    policy = _frozen_policy(sidecar, collection="json")
    try:
        with pytest.raises(RuntimeError, match="collides with a loaded module"):
            exec(frozen_sidecar_bootstrap_code(sidecar), policy)  # noqa: S102
        assert sys.modules["json"] is original_json
        assert list(original_json.__path__) == original_json_path
    finally:
        sys.modules.pop("victim.kernel", None)
        sys.modules.pop("victim", None)


@pytest.mark.parametrize("policy_key", ["_o4s_denied_skills", "_o4s_disabled_skills"])
def test_frozen_sidecar_rechecks_current_capability_policy(policy_key):
    sidecar = SidecarManifest(
        name="victim.kernel",
        source=b"VALUE = 999\n",
        order=0,
        local_import_roots=("victim",),
    )
    policy = _frozen_policy(sidecar)
    policy[policy_key].add("victim")

    with pytest.raises(RuntimeError, match="denied by capability policy"):
        exec(frozen_sidecar_bootstrap_code(sidecar), policy)  # noqa: S102
    assert "victim.kernel" not in sys.modules


def test_frozen_sidecar_recovery_uses_manifest_bytes_not_changed_disk(tmp_path):
    skill = tmp_path / "frozen_skill"
    skill.mkdir()
    path = skill / "kernel.py"
    old_source = b"VALUE = 'frozen-old'\n"
    path.write_bytes(old_source)
    sidecar = SidecarManifest(
        name="frozen_skill.kernel",
        source=old_source,
        order=0,
        source_path=str(path),
    )
    path.write_text("VALUE = 'mutable-new'\n", encoding="utf-8")

    try:
        exec(frozen_sidecar_bootstrap_code(sidecar), _frozen_policy(sidecar))
        module = importlib.import_module("frozen_skill.kernel")
        assert module.VALUE == "frozen-old"
        assert module.__openai4s_frozen_sidecar_sha256__ == sidecar.sha256
        assert module.__file__ == "<recovery-sidecar:frozen_skill.kernel>"
        assert module.__spec__.origin == "<recovery-sidecar:frozen_skill.kernel>"
    finally:
        sys.modules.pop("frozen_skill.kernel", None)
        sys.modules.pop("frozen_skill", None)


@pytest.mark.parametrize(
    "source",
    [
        b"from .helper import VALUE\n",
        b"import local_skill.helper\n",
        b"from local_skill import helper\n",
    ],
)
def test_sidecar_manifest_rejects_unfrozen_local_imports(source):
    with pytest.raises(ValueError, match="unfrozen local import"):
        SidecarManifest(
            name="local_skill.kernel",
            source=source,
            order=0,
            source_path="/mutable/local_skill/kernel.py",
        )


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (b"from pathlib import Path\nROOT = Path(__file__).parent\n", "mutable"),
        (
            b"import importlib.util\n"
            b"spec = importlib.util.spec_from_file_location('x', '/tmp/x.py')\n",
            "unfrozen code loader",
        ),
        (b"import runpy\nVALUE = runpy.run_path('/tmp/x.py')\n", "unfrozen"),
        (
            b"from runpy import run_path as load\nVALUE = load('/tmp/x.py')\n",
            "unfrozen",
        ),
        (b"exec(open('/tmp/x.py').read())\n", "unfrozen code loader"),
    ],
    ids=("package-resource", "file-spec", "runpy", "aliased-runpy", "open-exec"),
)
def test_sidecar_manifest_rejects_mutable_explicit_code_loaders(source, error):
    with pytest.raises(ValueError, match=error):
        SidecarManifest(
            name="local_skill.kernel",
            source=source,
            order=0,
            source_path="/mutable/local_skill/kernel.py",
        )


def test_bundled_sidecar_recovery_compatibility_is_explicit():
    from pathlib import Path

    skills_root = Path(__file__).resolve().parent.parent / "skills"
    rejected: dict[str, str] = {}
    sidecars = sorted(skills_root.glob("*/kernel.py"))
    for path in sidecars:
        try:
            SidecarManifest(
                name=f"{path.parent.name}.kernel",
                source=path.read_bytes(),
                order=0,
                source_path=str(path),
            )
        except ValueError as error:
            rejected[path.parent.name] = str(error)

    assert len(sidecars) == 16
    # These packages intentionally read mutable package resources. Their
    # runtime import remains supported, but recovery must be marked partial
    # until those dependent resources are frozen alongside kernel.py.
    assert set(rejected) == {"bioprobench", "catalyst_sar_screening"}


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("bioskills.collection-member.kernel", b"import collection-member.helper\n"),
        ("bioskills.collection_member.kernel", b"import collection_member.helper\n"),
        ("skills.local_skill.kernel", b"from local_skill import helper\n"),
    ],
)
def test_qualified_sidecars_reject_unfrozen_local_imports(name, source):
    # The hyphenated form is syntactically invalid and is rejected before the
    # import-root check; the identifier forms exercise both qualified aliases.
    error = (
        "does not compile"
        if b"collection-member" in source
        else "unfrozen local import"
    )
    with pytest.raises(ValueError, match=error):
        SidecarManifest(name=name, source=source, order=0)


def test_direct_sidecar_rejects_static_catalog_alias_import():
    with pytest.raises(ValueError, match="unfrozen local import"):
        SidecarManifest(
            name="local_skill.kernel",
            source=b"from skills.local_skill.helper import VALUE\n",
            order=0,
            local_import_roots=("local_skill", "skills"),
        )


def test_frozen_sidecar_package_path_cannot_load_changed_sibling(tmp_path, monkeypatch):
    skill = tmp_path / "frozen_dynamic_skill"
    skill.mkdir()
    (skill / "__init__.py").write_text("", encoding="utf-8")
    helper = skill / "helper.py"
    helper.write_text("VALUE = 'helper-old'\n", encoding="utf-8")
    source = (
        b"import importlib\n"
        b"VALUE = importlib.import_module(__package__ + '.helper').VALUE\n"
    )
    sidecar = SidecarManifest(
        name="frozen_dynamic_skill.kernel",
        source=source,
        order=0,
        source_path=str(skill / "kernel.py"),
    )
    helper.write_text("VALUE = 'helper-new'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        with pytest.raises(ModuleNotFoundError, match="helper"):
            exec(frozen_sidecar_bootstrap_code(sidecar), _frozen_policy(sidecar))
        assert "frozen_dynamic_skill.helper" not in sys.modules
    finally:
        sys.modules.pop("frozen_dynamic_skill.helper", None)
        sys.modules.pop("frozen_dynamic_skill.kernel", None)
        sys.modules.pop("frozen_dynamic_skill", None)


def test_real_loader_event_seals_dynamic_imports_through_every_package_alias(
    tmp_path,
):
    from openai4s.config import Config
    from openai4s.skills_loader import SkillLoader

    bundled = tmp_path / "skills"
    victim = bundled / "victim"
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text(
        "---\nname: victim\ndescription: victim\n---\nbody\n", "utf-8"
    )
    (victim / "helper.py").write_text("VALUE = 'old'\n", "utf-8")
    (victim / "kernel.py").write_text(
        "import importlib\n"
        "VALUE = importlib.import_module('skills.victim.helper').VALUE\n",
        "utf-8",
    )
    loader = SkillLoader(cfg=Config(data_dir=tmp_path / "data", skills_dir=bundled))

    original_meta_path = list(sys.meta_path)
    original_sys_path = list(sys.path)
    namespace: dict = {}
    try:
        exec(loader.bootstrap_code(), namespace)  # noqa: S102 - generated code
        loaded = importlib.import_module("victim.kernel")
        assert loaded.VALUE == "old"
        event = namespace["__openai4s_skill_load_events__"][0]
        assert set(event["local_import_roots"]) == {"victim", "skills"}
        sidecar = sidecar_from_load_event(event)

        (victim / "helper.py").write_text("VALUE = 'MUTATED-CURRENT-TREE'\n", "utf-8")
        sys.meta_path[:] = original_meta_path
        for module_name in list(sys.modules):
            if module_name in {"victim", "skills"} or module_name.startswith(
                ("victim.", "skills.")
            ):
                sys.modules.pop(module_name, None)

        # Candidate recovery reinstalls the ordinary loader hook first.  The
        # frozen bootstrap must still seal every alias package so dynamic
        # importlib/__import__ calls cannot read a changed sibling from disk.
        recovery_namespace: dict = {}
        exec(loader.bootstrap_code(), recovery_namespace)  # noqa: S102
        with pytest.raises(ModuleNotFoundError, match="helper"):
            exec(  # noqa: S102 - generated recovery code is under test
                frozen_sidecar_bootstrap_code(sidecar), recovery_namespace
            )
        assert "skills.victim.helper" not in sys.modules
        assert "victim.helper" not in sys.modules
    finally:
        sys.meta_path[:] = original_meta_path
        sys.path[:] = original_sys_path
        for module_name in list(sys.modules):
            if module_name in {"victim", "skills"} or module_name.startswith(
                ("victim.", "skills.")
            ):
                sys.modules.pop(module_name, None)


def test_runtime_and_recovery_share_one_module_across_sidecar_aliases(tmp_path):
    import builtins

    from openai4s.config import Config
    from openai4s.skills_loader import SkillLoader

    bundled = tmp_path / "skills"
    victim = bundled / "victim"
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text(
        "---\nname: victim\ndescription: victim\n---\nbody\n", "utf-8"
    )
    counter_name = "__openai4s_sidecar_alias_test_counter__"
    (victim / "kernel.py").write_text(
        "import builtins\n"
        f"builtins.{counter_name} = getattr(builtins, '{counter_name}', 0) + 1\n"
        f"LOAD_COUNT = builtins.{counter_name}\n",
        "utf-8",
    )
    loader = SkillLoader(cfg=Config(data_dir=tmp_path / "data", skills_dir=bundled))

    original_meta_path = list(sys.meta_path)
    original_sys_path = list(sys.path)
    previous_counter = getattr(builtins, counter_name, None)
    had_counter = hasattr(builtins, counter_name)
    try:
        setattr(builtins, counter_name, 0)
        runtime_namespace: dict = {}
        exec(loader.bootstrap_code(), runtime_namespace)  # noqa: S102
        direct = importlib.import_module("victim.kernel")
        qualified = importlib.import_module("skills.victim.kernel")
        assert direct is qualified
        assert direct.LOAD_COUNT == 1
        assert getattr(builtins, counter_name) == 1
        events = runtime_namespace["__openai4s_skill_load_events__"]
        assert [event["module"] for event in events] == ["victim.kernel"]
        sidecar = sidecar_from_load_event(events[0])

        sys.meta_path[:] = original_meta_path
        for module_name in list(sys.modules):
            if module_name in {"victim", "skills"} or module_name.startswith(
                ("victim.", "skills.")
            ):
                sys.modules.pop(module_name, None)
        setattr(builtins, counter_name, 0)

        recovery_namespace: dict = {}
        exec(loader.bootstrap_code(), recovery_namespace)  # noqa: S102
        exec(  # noqa: S102 - generated frozen bootstrap is under test
            frozen_sidecar_bootstrap_code(sidecar), recovery_namespace
        )
        recovered_direct = importlib.import_module("victim.kernel")
        recovered_qualified = importlib.import_module("skills.victim.kernel")
        assert recovered_direct is recovered_qualified
        assert recovered_direct.LOAD_COUNT == 1
        assert getattr(builtins, counter_name) == 1
    finally:
        sys.meta_path[:] = original_meta_path
        sys.path[:] = original_sys_path
        for module_name in list(sys.modules):
            if module_name in {"victim", "skills"} or module_name.startswith(
                ("victim.", "skills.")
            ):
                sys.modules.pop(module_name, None)
        if had_counter:
            setattr(builtins, counter_name, previous_counter)
        else:
            try:
                delattr(builtins, counter_name)
            except AttributeError:
                pass


@pytest.mark.parametrize(
    ("kernel_source", "expected_error"),
    [
        (
            "from dependency.helper import VALUE\n",
            "unfrozen Skill dependency",
        ),
        (
            "import importlib\n"
            "VALUE = importlib.import_module('dependency.helper').VALUE\n",
            "unfrozen Skill dependency",
        ),
        ("from helper import VALUE\n", "unfrozen module"),
    ],
    ids=("static-skill", "dynamic-skill", "workspace-module"),
)
def test_frozen_recovery_rejects_unfrozen_cross_skill_dependencies(
    tmp_path, kernel_source, expected_error
):
    from openai4s.config import Config
    from openai4s.skills_loader import SkillLoader

    bundled = tmp_path / "skills"
    victim = bundled / "victim"
    dependency = bundled / "dependency"
    victim.mkdir(parents=True)
    dependency.mkdir()
    (victim / "SKILL.md").write_text(
        "---\nname: victim\ndescription: victim\n---\nbody\n", "utf-8"
    )
    (dependency / "SKILL.md").write_text(
        "---\nname: dependency\ndescription: dependency\n---\nbody\n", "utf-8"
    )
    (dependency / "helper.py").write_text("VALUE = 'ORIGINAL'\n", "utf-8")
    workspace_helper = tmp_path / "helper.py"
    workspace_helper.write_text("VALUE = 'ORIGINAL'\n", "utf-8")
    (victim / "kernel.py").write_text(kernel_source, "utf-8")
    loader = SkillLoader(cfg=Config(data_dir=tmp_path / "data", skills_dir=bundled))

    original_meta_path = list(sys.meta_path)
    original_sys_path = list(sys.path)
    runtime_namespace: dict = {}
    try:
        sys.path.insert(0, str(tmp_path))
        exec(loader.bootstrap_code(), runtime_namespace)  # noqa: S102
        assert importlib.import_module("victim.kernel").VALUE == "ORIGINAL"
        events = runtime_namespace["__openai4s_skill_load_events__"]
        assert [event["module"] for event in events] == ["victim.kernel"]
        sidecar = sidecar_from_load_event(events[0])

        (dependency / "helper.py").write_text("VALUE = 'MUTATED'\n", "utf-8")
        workspace_helper.write_text("VALUE = 'MUTATED'\n", "utf-8")
        sys.meta_path[:] = original_meta_path
        for module_name in list(sys.modules):
            if module_name in {
                "victim",
                "dependency",
                "skills",
                "helper",
            } or module_name.startswith(("victim.", "dependency.", "skills.")):
                sys.modules.pop(module_name, None)

        recovery_namespace: dict = {}
        exec(loader.bootstrap_code(), recovery_namespace)  # noqa: S102
        with pytest.raises(ModuleNotFoundError, match=expected_error):
            exec(  # noqa: S102 - generated frozen bootstrap is under test
                frozen_sidecar_bootstrap_code(sidecar), recovery_namespace
            )
        assert "dependency.helper" not in sys.modules
        assert "skills.dependency.helper" not in sys.modules
    finally:
        sys.meta_path[:] = original_meta_path
        sys.path[:] = original_sys_path
        for module_name in list(sys.modules):
            if module_name in {
                "victim",
                "dependency",
                "skills",
                "helper",
            } or module_name.startswith(("victim.", "dependency.", "skills.")):
                sys.modules.pop(module_name, None)


def test_sidecar_event_tampering_and_capture_failure_fail_closed():
    bootstrap = BootstrapManifest(
        language="python",
        interpreter="/env/bin/python",
        runtime_version="3.12",
        working_directory="/workspace",
    ).record()
    event = _sidecar_event("stats.kernel", b"VALUE = 1\n", 0)
    event["source_b64"] = base64.b64encode(b"VALUE = 2\n").decode("ascii")
    with pytest.raises(ValueError, match="hash mismatch"):
        merge_bootstrap_sidecar_loads(bootstrap, [event])

    wrong_bootstrap_hash = _sidecar_event("stats.kernel", b"VALUE = 1\n", 0)
    wrong_bootstrap_hash["expected_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="bootstrap hash"):
        merge_bootstrap_sidecar_loads(bootstrap, [wrong_bootstrap_hash])

    non_integer_order = _sidecar_event("stats.kernel", b"VALUE = 1\n", 0)
    non_integer_order["order"] = 0.0
    with pytest.raises(ValueError, match="order must be an integer"):
        merge_bootstrap_sidecar_loads(bootstrap, [non_integer_order])

    failed = dict(bootstrap)
    failed["sidecar_capture_status"] = "failed"
    failed["sidecar_capture_error"] = "worker event was invalid"
    with pytest.raises(ValueError, match="capture is incomplete"):
        BootstrapManifest.from_record(failed)


@pytest.mark.parametrize(
    ("code", "methods", "expected"),
    [
        ("host.submit_output({'ok': True}, ['Done'])", (), "submit_output"),
        ("host.bash('echo unsafe')", (), "bash"),
        ("value = host.unknown_service()", (), "unknown Host method"),
        ("import subprocess\nsubprocess.run(['true'])", (), "process"),
        # ``from <module> import ...`` is caught by the import blocklist (the
        # module, not the imported symbol name); previously it slipped past to
        # the write-method attribute check.
        ("from pathlib import Path\nPath('x').write_text('x')", (), "imports a direct"),
        ("from shutil import rmtree\nrmtree('x')", (), "imports a direct"),
        ("scores.to_csv('x')", (), "external state"),
        ("open('x', 'w').write('x')", (), "external state"),
        ("x = 1", ("write_file",), "unsafe Host methods"),
    ],
)
def test_replay_safety_fails_closed_for_external_side_effects(code, methods, expected):
    error = replay_safety_error(code, language="python", declared_host_methods=methods)
    assert expected in error


def test_replay_safety_allows_pure_computation_and_declared_read_only_host_calls():
    assert (
        replay_safety_error("scores = [x*x for x in data]", language="python") is None
    )
    assert (
        replay_safety_error(
            "rows = host.query({'sql': 'select 1'})",
            language="python",
            declared_host_methods=("query",),
        )
        is None
    )


def test_verified_candidate_is_published_only_after_hydration_replay_and_validation():
    candidate = _Candidate()
    events = []
    published = []
    executed = []
    orchestrator = _orchestrator(candidate, events, published, executed)
    recipe = RecoveryRecipe(
        steps=(
            RecoveryStep("hydrate_workspace", {"tree_id": "tree-1"}, REPLAY_NEVER),
            RecoveryStep(
                "hydrate_artifact",
                {"version_id": "version-1"},
                REPLAY_NEVER,
            ),
            RecoveryStep(
                "replay_cell",
                {"language": "python", "code": "scores = [x*x for x in data]"},
                REPLAY_SAFE,
                step_id="safe-cell",
            ),
        ),
        required_symbols={"python": ("data", "model")},
        artifact_hashes={"prediction.csv": "hash-prediction"},
        environment_requirements={"python_version": "3.12"},
    )

    result = orchestrator.restore(
        root_frame_id="root",
        branch_id="branch",
        manifest=_manifest(),
        recipe=recipe,
        source_generation_id="old-generation",
    )

    assert result.status == "active"
    assert result.replayed_steps == ("safe-cell",)
    assert result.issues == ()
    assert published == ["candidate-1"]
    assert executed == [("python", "scores = [x*x for x in data]")]
    assert candidate.shutdown_calls == 0
    assert ("journal", "publish", "completed") in events


def test_non_replayable_step_yields_partial_and_preserves_old_generation():
    candidate = _Candidate()
    events = []
    published = []
    executed = []
    old = {"generation_id": "old", "alive": True}
    orchestrator = _orchestrator(candidate, events, published, executed)
    recipe = RecoveryRecipe(
        steps=(
            RecoveryStep(
                "replay_cell",
                {"language": "python", "code": "host.bash('train.sh')"},
                REPLAY_SAFE,
                step_id="unsafe-cell",
            ),
        )
    )

    result = orchestrator.restore(
        root_frame_id="root",
        branch_id=None,
        manifest=_manifest(),
        recipe=recipe,
        source_generation_id=old["generation_id"],
    )

    assert result.status == "partial"
    assert result.skipped_steps == ("unsafe-cell",)
    assert result.issues[0]["type"] == "non_replayable"
    assert published == []
    assert executed == []
    assert candidate.shutdown_calls == 1
    assert old["alive"] is True


def test_replay_step_source_hash_is_rechecked_before_execution():
    candidate = _Candidate()
    executed = []
    published = []
    code = "scores = [x*x for x in data]"
    recipe = RecoveryRecipe(
        steps=(
            RecoveryStep(
                "replay_cell",
                {
                    "language": "python",
                    "code": code,
                    "code_hash": hashlib.sha256(b"different source").hexdigest(),
                },
                REPLAY_SAFE,
                step_id="tampered-cell",
            ),
        )
    )

    result = _orchestrator(candidate, [], published, executed).restore(
        root_frame_id="root",
        branch_id=None,
        manifest=_manifest(),
        recipe=recipe,
        source_generation_id="old",
    )

    assert result.status == "partial"
    assert result.skipped_steps == ("tampered-cell",)
    assert "source hash" in result.issues[0]["reason"]
    assert executed == []
    assert published == []


def test_prior_cells_without_namespace_coverage_cannot_be_declared_active():
    candidate = _Candidate()
    published = []
    result = _orchestrator(candidate, [], published, []).restore(
        root_frame_id="root",
        branch_id=None,
        manifest=_manifest(),
        recipe=RecoveryRecipe(namespace_coverage="unverified"),
        source_generation_id="old",
    )

    assert result.status == "partial"
    assert result.issues[0]["type"] == "namespace_unverified"
    assert published == []
    assert candidate.shutdown_calls == 1


def test_validation_failure_is_partial_and_bootstrap_failure_is_failed():
    candidate = _Candidate()
    candidate.symbols["python"].remove("model")
    partial = _orchestrator(candidate, [], [], []).restore(
        root_frame_id="root",
        branch_id=None,
        manifest=_manifest(),
        recipe=RecoveryRecipe(required_symbols={"python": ("model",)}),
        source_generation_id="old",
    )
    assert partial.status == "partial"
    assert partial.issues[0]["type"] == "missing_symbols"
    assert candidate.shutdown_calls == 1

    broken = _Candidate("candidate-broken")
    published = []
    failed = _orchestrator(
        broken,
        [],
        published,
        [],
        bootstrap=lambda current, manifest: (_ for _ in ()).throw(
            RuntimeError("missing package")
        ),
    ).restore(
        root_frame_id="root",
        branch_id=None,
        manifest=_manifest(),
        recipe=RecoveryRecipe(),
        source_generation_id="old",
    )
    assert failed.status == "failed"
    assert "missing package" in failed.issues[0]["error"]
    assert published == []
    assert broken.shutdown_calls == 1


def test_recovery_journal_is_append_only_and_survives_repository_reopen(tmp_path):
    connection = sqlite3.connect(tmp_path / "recovery.sqlite")
    connection.row_factory = sqlite3.Row
    lock = threading.RLock()
    repository = RecoveryJournalRepository(connection, lock, clock_ms=lambda: 1000)
    first = repository.append(
        recovery_id="recovery-1",
        root_frame_id="root",
        branch_id="branch",
        phase="build",
        status="completed",
        detail={"candidate": "gen-2"},
    )
    second = repository.append(
        recovery_id="recovery-1",
        root_frame_id="root",
        branch_id="branch",
        phase="validate",
        status="partial",
        detail={"missing": ["model"]},
    )

    reopened = RecoveryJournalRepository(connection, lock, clock_ms=lambda: 2000)
    rows = reopened.list(recovery_id="recovery-1")
    assert [row["sequence"] for row in rows] == [0, 1]
    assert rows[0]["entry_id"] == first["entry_id"]
    assert rows[1]["entry_id"] == second["entry_id"]
    assert rows[1]["detail"] == {"missing": ["model"]}
    connection.close()
