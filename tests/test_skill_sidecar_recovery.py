"""Real worker -> generation manifest -> checkpoint -> recovery sidecar flow."""

from __future__ import annotations

import base64
import hashlib

import pytest

from openai4s.config import Config
from openai4s.kernel import Kernel, KernelSupervisor
from openai4s.kernel.recovery import (
    BootstrapManifest,
    frozen_sidecar_bootstrap_code,
    sidecar_from_load_event,
)
from openai4s.server.recovery_runtime import bootstrap_python_generation
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


def test_worker_sidecar_records_fail_closed_and_never_enter_recovery_manifest(
    tmp_path,
):
    skills = tmp_path / "skills"
    skills.mkdir()
    _skill(skills, "alpha", "VALUE = 'alpha-old'\n")

    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    store = Store(cfg.db_path)
    root = store.new_frame(project_id="project-sidecars", kind="turn", status="ready")
    workspace = cfg.data_dir / "workspaces" / root
    workspace.mkdir(parents=True)
    loader = SkillLoader(cfg=cfg)

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
        alpha = kernel.execute("import alpha.kernel as alpha", origin="agent")
        assert alpha["error"] is None
        assert alpha[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]
        recorder.record_result(supervisor, lease, alpha)
        assert RESULT_KEY not in alpha
        assert alpha["runtime_warnings"][0]["type"] == (
            "skill_sidecar_recovery_capture_failed"
        )
        assert alpha["runtime_warnings"][0]["generation_marked_unrecoverable"] is True
        generation = store.get_kernel_generation(lease.generation_id)
        assert generation["bootstrap"]["sidecar_capture_status"] == "failed"
        assert generation["bootstrap"]["loaded_sidecars"] == []
        with pytest.raises(ValueError, match="capture is incomplete"):
            BootstrapManifest.from_record(generation["bootstrap"])
    finally:
        supervisor.stop("python", manual=False, reason="test_complete")
        store.close()


def test_worker_sidecar_record_marks_generation_unrecoverable(tmp_path):
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
                "sha256": hashlib.sha256(source).hexdigest(),
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
    source_bytes = source.encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    sidecar = sidecar_from_load_event(
        {
            "event": "sidecar_loaded",
            "module": "victim.kernel",
            "expected_sha256": source_sha256,
            "sha256": source_sha256,
            "source_b64": base64.b64encode(source_bytes).decode("ascii"),
            "source_path": str(skills / "victim" / "kernel.py"),
            "local_import_roots": ["skills", "victim"],
            "order": 0,
        }
    )
    recovered = None
    try:
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
        if recovered is not None:
            recovered.shutdown()


def test_manager_keeps_sidecar_capture_failed_across_cells(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    source = "VALUE = 'bounded'\n"
    _skill(skills, "first", source)
    _skill(skills, "second", source)
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
        assert first[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]

        second = kernel.execute(
            "import second.kernel\n__openai4s_skill_load_events__.clear()",
            origin="agent",
        )
        assert second[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]
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
        assert result[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]
        assert kernel._skill_sidecar_capture_failed is True
    finally:
        kernel.shutdown()


def test_gc_recovered_signer_cannot_authorize_sidecar_recovery(tmp_path):
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
            "import __main__, gc, types\n"
            "guard = next(\n"
            "    obj for obj in gc.get_objects()\n"
            "    if type(obj) is types.FunctionType\n"
            "    and obj.__name__ == '_dlopen_guard'\n"
            "    and '_signed_skill_event' in (obj.__kwdefaults__ or {})\n"
            ")\n"
            "signer = guard.__kwdefaults__['_signed_skill_event']\n"
            "key = signer.__kwdefaults__['_key']\n"
            "attestation_id = 'forged-attestation'\n"
            f"forged = {forged!r}\n"
            "started = signer({\n"
            "    'event': 'sidecar_capture_started',\n"
            "    'attestation_id': attestation_id,\n"
            "    'sha256': forged['sha256'],\n"
            "})\n"
            "loaded = signer(forged)\n"
            "for event in (started, loaded):\n"
            "    __main__._write_frame({\n"
            "        'type': 'skill_sidecar_load',\n"
            "        'id': __main__._ACTIVE_CELL_ID[0],\n"
            "        'event': event,\n"
            "    })\n"
            "print(len(key), len(loaded['attestation_mac']))\n"
            "print('victim.kernel' in __import__('sys').modules)",
            origin="agent",
        )
        assert result["error"] is None
        assert result["stdout"].splitlines() == ["32 64", "False"]
        assert result[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]
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
        assert loaded[RESULT_KEY] == [{"event": "untrusted_worker_sidecar_event"}]
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
