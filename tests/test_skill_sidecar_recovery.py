"""Real worker -> generation manifest -> checkpoint -> recovery sidecar flow."""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.kernel import Kernel, KernelSupervisor
from openai4s.kernel.recovery import BootstrapManifest
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
    assert bootstrap["host_capability_version"] == "1"
    assert bootstrap["provenance_version"] == "1"
    assert supervisor.record_bootstrap_if_current(
        "python", kernel, bootstrap, state="active"
    )
    recorder = GenerationSidecarRecorder(store)

    try:
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

        beta = kernel.execute("import beta.kernel as beta", origin="agent")
        assert beta["error"] is None
        recorder.record_result(supervisor, lease, beta)

        disabled = kernel.execute("import disabled.kernel", origin="agent")
        assert "disabled by capability policy" in disabled["error"]
        assert RESULT_KEY not in disabled

        broken = kernel.execute("import broken.kernel", origin="agent")
        assert "import failed" in broken["error"]
        assert RESULT_KEY not in broken

        generation = store.get_kernel_generation(lease.generation_id)
        manifest = BootstrapManifest.from_record(generation["bootstrap"])
        assert [sidecar.name for sidecar in manifest.sidecars] == [
            "alpha.kernel",
            "beta.kernel",
        ]
        assert [sidecar.order for sidecar in manifest.sidecars] == [0, 1]
        assert b"alpha-old" in manifest.sidecars[0].source
        assert b"beta-old" in manifest.sidecars[1].source

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
            b"VALUE = 'alpha-old'\n",
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
