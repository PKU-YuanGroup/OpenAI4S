"""Real worker -> generation manifest -> checkpoint -> recovery sidecar flow."""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.kernel import Kernel, KernelSupervisor
from openai4s.kernel.recovery import (
    BootstrapManifest,
    frozen_sidecar_bootstrap_code,
    sidecar_from_load_event,
)
from openai4s.server.recovery_runtime import (
    SessionRecoveryRuntime,
    bootstrap_python_generation,
)
from openai4s.server.session_domain import SessionDomainService
from openai4s.server.skill_sidecars import RESULT_KEY, GenerationSidecarRecorder
from openai4s.skills_loader import SkillLoader
from openai4s.store import Store


def _skill(root, name: str, source: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test sidecar\n---\nUse it.\n",
        encoding="utf-8",
    )
    (directory / "kernel.py").write_text(source, encoding="utf-8")


class _LiveKernel:
    pid = 4102
    python = "/env/bin/python"
    env_name = "base"
    env_root = "/env"
    cwd = "/workspace"

    def __init__(self) -> None:
        self.live = True

    def is_alive(self):
        return self.live

    def shutdown(self):
        self.live = False


def test_only_successful_sidecars_are_frozen_and_recovery_ignores_changed_disk(
    tmp_path,
):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(skills, "alpha", "VALUE = 'alpha-old'\n")
    _skill(skills, "beta", "VALUE = 'beta-old'\n")
    _skill(skills, "cleared", "VALUE = 'cleared-old'\n")
    _skill(skills, "replaced", "VALUE = 'replaced-old'\n")
    _skill(
        skills,
        "loader_tamper",
        "for attr, value in (('_audit_emit', None), ('_event_mirror', [])):\n"
        "    if hasattr(__loader__, attr):\n"
        "        setattr(__loader__, attr, value)\n"
        "VALUE = 'loader-tamper-old'\n",
    )
    _skill(skills, "shadowed_exec", "VALUE = 'shadowed-exec-old'\n")
    _skill(skills, "gamma", "VALUE = 'gamma-new-load'\n")
    _skill(skills, "disabled", "VALUE = 'must-not-load'\n")
    _skill(skills, "broken", "raise RuntimeError('import failed')\n")
    _skill(skills, "changed_early", "VALUE = 'discovered-old'\n")

    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    store = Store(cfg.db_path)
    root = store.new_frame(project_id="project-sidecars", kind="turn", status="ready")
    workspace = cfg.data_dir / "workspaces" / root
    workspace.mkdir(parents=True)
    loader = SkillLoader(
        cfg=cfg,
        capabilities=store.capability_state(
            project_id="project-sidecars", session_id=root
        ),
    )
    loader.set_enabled("disabled", False, scope="session", scope_id=root)

    supervisor = KernelSupervisor(
        root_frame_id=root,
        generations=store,
        owner_instance_id="daemon-sidecar-test",
    )
    kernel = Kernel(dispatcher=None, cwd=str(workspace), mode="jupyter")
    lease = supervisor.ensure("python", "base", lambda: kernel)
    bootstrap = bootstrap_python_generation(
        kernel,
        workspace,
        loader.bootstrap_code(),
    )
    assert bootstrap["status"] == "active"
    assert bootstrap["version"] == 2
    assert len(bootstrap["environment_hash"]) == 64
    assert bootstrap["package_manifest"]
    assert bootstrap["locale"]["filesystem_encoding"]
    assert bootstrap["host_capability_version"] == "2"
    assert bootstrap["provenance_version"] == "1"
    assert supervisor.record_bootstrap_if_current(
        "python", kernel, bootstrap, state="active"
    )
    recorder = GenerationSidecarRecorder(store)

    try:
        forged = kernel.execute(
            "import sys\n"
            "sys.audit('openai4s.skill_sidecar_loaded', "
            "{'event': 'invalid_sidecar_event', 'source_b64': 'eA=='})",
            origin="agent",
        )
        assert forged["error"] is None
        assert RESULT_KEY not in forged

        shadowed = kernel.execute(
            "exec = lambda code, namespace: None\n"
            "import shadowed_exec.kernel as shadowed\n"
            "loaded_value = shadowed.VALUE\n"
            "del exec",
            origin="agent",
        )
        assert shadowed["error"] is None
        assert [event["module"] for event in shadowed[RESULT_KEY]] == [
            "shadowed_exec.kernel"
        ]
        recorder.record_result(supervisor, lease, shadowed)

        # The discovery/bootstrap hash is authoritative. A sidecar changed
        # before its first import must not execute under the old manifest.
        (skills / "changed_early" / "kernel.py").write_text(
            "VALUE = 'changed-before-import'\n", encoding="utf-8"
        )
        changed_early = kernel.execute("import changed_early.kernel", origin="agent")
        assert "changed after bootstrap" in changed_early["error"]
        assert RESULT_KEY not in changed_early

        alpha = kernel.execute("import alpha.kernel as alpha", origin="agent")
        assert alpha["error"] is None
        assert len(alpha[RESULT_KEY]) == 1
        recorder.record_result(supervisor, lease, alpha)
        assert RESULT_KEY not in alpha

        # The diagnostic mirror belongs to the persistent user namespace, so
        # a Cell can clear or replace it after a successful import. The worker
        # queue remains authoritative and must still return the exact event.
        cleared = kernel.execute(
            "import cleared.kernel as cleared\n"
            "__openai4s_skill_load_events__.clear()",
            origin="agent",
        )
        assert cleared["error"] is None
        assert [event["module"] for event in cleared[RESULT_KEY]] == ["cleared.kernel"]
        recorder.record_result(supervisor, lease, cleared)

        replaced = kernel.execute(
            "import replaced.kernel as replaced\n"
            "__openai4s_skill_load_events__ = 'user-replaced'",
            origin="agent",
        )
        assert replaced["error"] is None
        assert [event["module"] for event in replaced[RESULT_KEY]] == [
            "replaced.kernel"
        ]
        recorder.record_result(supervisor, lease, replaced)

        loader_tamper = kernel.execute("import loader_tamper.kernel", origin="agent")
        assert loader_tamper["error"] is None
        assert [event["module"] for event in loader_tamper[RESULT_KEY]] == [
            "loader_tamper.kernel"
        ]
        recorder.record_result(supervisor, lease, loader_tamper)

        beta = kernel.execute("import beta.kernel as beta", origin="agent")
        assert beta["error"] is None
        recorder.record_result(supervisor, lease, beta)

        disabled = kernel.execute("import disabled.kernel", origin="agent")
        assert "disabled by capability policy" in disabled["error"]
        assert RESULT_KEY not in disabled

        broken = kernel.execute("import broken.kernel", origin="agent")
        assert "import failed" in broken["error"]
        assert broken[RESULT_KEY] == [{"event": "invalid_sidecar_event"}]

        generation = store.get_kernel_generation(lease.generation_id)
        manifest = BootstrapManifest.from_record(generation["bootstrap"])
        assert [sidecar.name for sidecar in manifest.sidecars] == [
            "shadowed_exec.kernel",
            "alpha.kernel",
            "cleared.kernel",
            "replaced.kernel",
            "loader_tamper.kernel",
            "beta.kernel",
        ]
        assert [sidecar.order for sidecar in manifest.sidecars] == [0, 1, 2, 3, 4, 5]
        assert b"alpha-old" in manifest.sidecars[1].source
        assert b"beta-old" in manifest.sidecars[5].source

        # Mutate both source files before checkpoint/recovery. The generation
        # record, checkpoint, and recovered module must keep the executed bytes.
        (skills / "alpha" / "kernel.py").write_text(
            "VALUE = 'alpha-new'\n", encoding="utf-8"
        )
        (skills / "beta" / "kernel.py").write_text(
            "VALUE = 'beta-new'\n", encoding="utf-8"
        )
        domain = SessionDomainService(
            store,
            data_dir=cfg.data_dir,
            workspace=lambda _root, _branch: workspace,
        )
        checkpoint = domain.create_checkpoint(root, reason="sidecar-freeze-test")
        checkpoint_bootstrap = checkpoint["generation_refs"]["python"]["bootstrap"]
        checkpoint_manifest = BootstrapManifest.from_record(checkpoint_bootstrap)
        assert [item.source for item in checkpoint_manifest.sidecars] == [
            b"VALUE = 'shadowed-exec-old'\n",
            b"VALUE = 'alpha-old'\n",
            b"VALUE = 'cleared-old'\n",
            b"VALUE = 'replaced-old'\n",
            (
                b"for attr, value in (('_audit_emit', None), "
                b"('_event_mirror', [])):\n"
                b"    if hasattr(__loader__, attr):\n"
                b"        setattr(__loader__, attr, value)\n"
                b"VALUE = 'loader-tamper-old'\n"
            ),
            b"VALUE = 'beta-old'\n",
        ]

        recovered_kernel = Kernel(
            dispatcher=None,
            cwd=str(workspace),
            mode="jupyter",
        )
        candidate = SimpleNamespace(
            language="python",
            kernel=recovered_kernel,
            observed_environment={},
        )
        try:
            runtime = object.__new__(SessionRecoveryRuntime)
            runtime._bootstrap_candidate(candidate, checkpoint_manifest)
            result = recovered_kernel.execute(
                "from alpha.kernel import VALUE as alpha_value\n"
                "from beta.kernel import VALUE as beta_value\n"
                "print(alpha_value, beta_value)",
                origin="recovery",
            )
            assert result["error"] is None
            assert "alpha-old beta-old" in result["stdout"]
            gamma = recovered_kernel.execute("import gamma.kernel", origin="agent")
            assert gamma["error"] is None
            assert [event["order"] for event in gamma[RESULT_KEY]] == [6]
        finally:
            recovered_kernel.shutdown()
    finally:
        supervisor.stop("python", manual=False, reason="test_complete")
        store.close()


def test_tampered_worker_sidecar_record_marks_generation_unrecoverable(tmp_path):
    store = Store(tmp_path / "tamper.db")
    supervisor = KernelSupervisor(
        root_frame_id="root-tamper",
        generations=store,
        owner_instance_id="daemon-tamper",
    )
    kernel = _LiveKernel()
    lease = supervisor.ensure("python", "base", lambda: kernel)
    bootstrap = {
        **BootstrapManifest(
            language="python",
            interpreter=kernel.python,
            runtime_version="3.12",
            working_directory=kernel.cwd,
        ).record(),
        "status": "active",
        "sidecar_capture_status": "complete",
        "loaded_sidecars": [],
    }
    assert supervisor.record_bootstrap_if_current("python", kernel, bootstrap)
    source = b"VALUE = 2\n"
    result = {
        "error": "original Cell failure",
        RESULT_KEY: [
            {
                "event": "sidecar_loaded",
                "module": "tampered.kernel",
                "order": 0,
                "source_b64": base64.b64encode(source).decode("ascii"),
                # Deliberately hash different bytes.
                "sha256": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
            }
        ],
    }

    GenerationSidecarRecorder(store).record_result(supervisor, lease, result)
    assert RESULT_KEY not in result
    assert result["error"] == "original Cell failure"
    assert "source_b64" not in repr(result)
    assert result["runtime_warnings"] == [
        {
            "type": "skill_sidecar_recovery_capture_failed",
            "message": (
                "The Cell already executed, but its exact Skill "
                "sidecar recovery snapshot could not be persisted. Do not "
                "automatically rerun the Cell."
            ),
            "generation_marked_unrecoverable": True,
        }
    ]
    row = store.get_kernel_generation(lease.generation_id)
    assert row["bootstrap"]["sidecar_capture_status"] == "failed"
    with pytest.raises(ValueError, match="capture is incomplete"):
        BootstrapManifest.from_record(row["bootstrap"])
    supervisor.stop("python", manual=False, reason="test_complete")
    store.close()


def test_frozen_worker_blocks_aliased_mutable_file_loader(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 'ORIGINAL'\n", encoding="utf-8")
    source = (
        "import importlib.util as utility\n"
        "make = utility.spec_from_file_location\n"
        "build = utility.module_from_spec\n"
        f"spec = make('victim._helper', {str(helper)!r})\n"
        "module = build(spec)\n"
        "load = spec.loader.exec_module\n"
        "load(module)\n"
        "VALUE = module.VALUE\n"
    )
    _skill(skills, "victim", source)
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    loader = SkillLoader(cfg=cfg)
    runtime = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    recovered = None
    try:
        assert (
            runtime.execute(loader.bootstrap_code(), origin="system")["error"] is None
        )
        loaded = runtime.execute("import victim.kernel", origin="agent")
        assert loaded["error"] is None
        sidecar = sidecar_from_load_event(loaded[RESULT_KEY][0])

        helper.write_text("VALUE = 'MUTATED'\n", encoding="utf-8")
        recovered = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
        assert (
            recovered.execute(loader.bootstrap_code(), origin="recovery")["error"]
            is None
        )
        result = recovered.execute(
            frozen_sidecar_bootstrap_code(sidecar), origin="sidecar_recovery"
        )
        assert "Refusing mutable file/code access" in str(result["error"])
    finally:
        runtime.shutdown()
        if recovered is not None:
            recovered.shutdown()


def test_manager_enforces_sidecar_capture_budget_across_cells(tmp_path, monkeypatch):
    from openai4s.kernel import manager as manager_module

    skills = tmp_path / "skills"
    skills.mkdir()
    source = "VALUE = 'bounded'\n"
    _skill(skills, "first", source)
    _skill(skills, "second", source)
    encoded_size = len(base64.b64encode(source.encode("utf-8")))
    monkeypatch.setattr(
        manager_module,
        "_SKILL_SIDECAR_CAPTURE_B64_BYTES",
        encoded_size + 1,
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    kernel = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    try:
        assert (
            kernel.execute(SkillLoader(cfg=cfg).bootstrap_code(), origin="system")[
                "error"
            ]
            is None
        )
        first = kernel.execute(
            "import first.kernel\n__openai4s_skill_load_events__.clear()",
            origin="agent",
        )
        assert [event["event"] for event in first[RESULT_KEY]] == ["sidecar_loaded"]

        second = kernel.execute(
            "import second.kernel\n__openai4s_skill_load_events__.clear()",
            origin="agent",
        )
        assert second[RESULT_KEY] == [{"event": "invalid_sidecar_event"}]
        assert kernel._skill_sidecar_capture_failed is True
    finally:
        kernel.shutdown()


def test_mutated_loader_exec_default_cannot_forge_successful_capture(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(skills, "victim", "VALUE = 123\n")
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    kernel = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    try:
        assert (
            kernel.execute(SkillLoader(cfg=cfg).bootstrap_code(), origin="system")[
                "error"
            ]
            is None
        )
        result = kernel.execute(
            "_OpenAI4STrackedSkillLoader.exec_module.__kwdefaults__['_exec'] = "
            "lambda code, namespace: None\n"
            "import victim.kernel as victim\n"
            "print(hasattr(victim, 'VALUE'))",
            origin="agent",
        )
        assert result["error"] is None
        assert result["stdout"].strip() == "False"
        assert result[RESULT_KEY] == [{"event": "invalid_sidecar_event"}]
        assert kernel._skill_sidecar_capture_failed is True
    finally:
        kernel.shutdown()


def test_user_protocol_frames_cannot_forge_sidecar_attestation(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(skills, "victim", "VALUE = 999\n")
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    loader = SkillLoader(cfg=cfg)
    entry = next(
        item
        for item in loader.bootstrap_manifest(persist=False)["entries"]
        if item["directory"] == "victim"
    )
    source = (skills / "victim" / "kernel.py").read_bytes()
    forged = {
        "event": "sidecar_loaded",
        "skill_name": "victim",
        "module": "victim.kernel",
        "version": None,
        "expected_sha256": entry["sidecar"]["sha256"],
        "sha256": hashlib.sha256(source).hexdigest(),
        "source_b64": base64.b64encode(source).decode("ascii"),
        "source_path": str(skills / "victim" / "kernel.py"),
        "local_import_roots": ["skills", "victim"],
        "order": 0,
        "exports": [],
        "import_mode": "module",
        "loaded_at_ns": 1,
        "attestation_id": "forged-attestation",
    }
    kernel = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    try:
        assert kernel.execute(loader.bootstrap_code(), origin="system")["error"] is None
        result = kernel.execute(
            "import __main__, json, os\n"
            "print(hasattr(__main__, '_publish_skill_sidecar_event'))\n"
            "print('OPENAI4S_SKILL_ATTESTATION_KEY' in os.environ)\n"
            "print(os.path.exists('/proc/self/environ') and "
            "b'OPENAI4S_SKILL_ATTESTATION_KEY' in "
            "open('/proc/self/environ', 'rb').read())\n"
            "__main__._write_frame({\n"
            "    'type': 'skill_sidecar_load',\n"
            "    'id': __main__._ACTIVE_CELL_ID[0],\n"
            f"    'event': {forged!r},\n"
            "})\n"
            "print('victim.kernel' in __import__('sys').modules)",
            origin="agent",
        )
        assert result["error"] is None
        assert result["stdout"].splitlines() == [
            "False",
            "False",
            "False",
            "False",
        ]
        assert result[RESULT_KEY] == [{"event": "invalid_sidecar_event"}]
        assert kernel._skill_sidecar_capture_failed is True
    finally:
        kernel.shutdown()


def test_runpy_cannot_execute_a_sidecar_outside_the_tracked_loader(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(
        skills,
        "victim",
        "import builtins\n"
        "builtins.__o4s_runpy_counter = "
        "getattr(builtins, '__o4s_runpy_counter', 0) + 1\n"
        "VALUE = builtins.__o4s_runpy_counter\n",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    kernel = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    try:
        assert (
            kernel.execute(SkillLoader(cfg=cfg).bootstrap_code(), origin="system")[
                "error"
            ]
            is None
        )
        bypass = kernel.execute(
            "import runpy\nrunpy.run_module('victim.kernel')", origin="agent"
        )
        assert "must be imported through the tracked loader" in bypass["error"]
        assert RESULT_KEY not in bypass

        loaded = kernel.execute("import victim.kernel", origin="agent")
        assert loaded["error"] is None
        assert [event["module"] for event in loaded[RESULT_KEY]] == ["victim.kernel"]
        assert (
            kernel.execute("print(victim.kernel.VALUE)", origin="agent")[
                "stdout"
            ].strip()
            == "1"
        )

        second = kernel.execute(
            "import runpy\nrunpy.run_module('victim.kernel')", origin="agent"
        )
        assert "must be imported through the tracked loader" in second["error"]
        assert RESULT_KEY not in second
        count = kernel.execute(
            "import builtins\nprint(builtins.__o4s_runpy_counter)", origin="agent"
        )
        assert count["stdout"].strip() == "1"
    finally:
        kernel.shutdown()


def test_cell_cannot_mutate_the_finders_capability_snapshot(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(skills, "victim", "VALUE = 'DENIED_EXECUTED'\n")
    loader = SkillLoader(cfg=Config(data_dir=tmp_path / "data", skills_dir=skills))
    kernel = Kernel(dispatcher=None, cwd=str(tmp_path), mode="jupyter")
    try:
        assert (
            kernel.execute(loader.bootstrap_code(allowed=frozenset()), origin="system")[
                "error"
            ]
            is None
        )
        denied = kernel.execute("import victim.kernel", origin="agent")
        assert "not available to this agent" in denied["error"]

        mutated = kernel.execute(
            "_o4s_denied_skills.clear()\n"
            "_o4s_disabled_skills.clear()\n"
            "_o4s_skill_entries.clear()\n"
            "import victim.kernel",
            origin="agent",
        )
        assert "not available to this agent" in mutated["error"]
        assert RESULT_KEY not in mutated
    finally:
        kernel.shutdown()
