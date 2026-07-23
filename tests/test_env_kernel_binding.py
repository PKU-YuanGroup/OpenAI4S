"""An environment transaction that no kernel can see is not a transaction.

``openai4s env apply`` built generations under ``<data_dir>/environments`` and
moved a pointer at them. Kernel discovery scanned configured and conventional
Conda roots and nothing else — so ``apply`` and ``rollback`` reported a new
current generation while every cell kept running the interpreter it had always
run. The transaction was complete, verified, immutable and inert.

Three more holes on the same path:

  * the verifier accepted a generation whose interpreter it had never executed
    (a Python freeze failure became an empty package list; an R candidate was
    never run at all), so `current` could move onto a broken environment;
  * the manifest kept ``plan.spec_sha256`` while the builder read the *live*
    YAML through a captured path, so a spec edited between plan and apply
    produced a generation whose recorded provenance did not describe it;
  * an artifact whose generation had no interpreter path borrowed the daemon's
    package list and stamped it with that generation's id.

The kernel tests here execute a real cell in a real worker. Asserting on a
resolved path would have passed against the broken code too — the path was
right, it was simply never the one a kernel used.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.kernel import Kernel
from openai4s.kernel import environments as envmod
from openai4s.kernel.env_generations import EnvironmentError_, EnvironmentStore


@pytest.fixture(autouse=True)
def _fresh_discovery():
    envmod.invalidate_cache()
    yield
    envmod.invalidate_cache()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    # Keep the developer's real conda roots out of the discovery under test.
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(tmp_path / "no-such-root"))
    monkeypatch.delenv("CONDA_ENVS_DIRS", raising=False)
    monkeypatch.delenv("CONDA_ENVS_PATH", raising=False)
    return Config(data_dir=tmp_path / "data")


def _store(cfg) -> EnvironmentStore:
    return EnvironmentStore(Path(cfg.data_dir) / "environments")


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_python_prefix(prefix: Path) -> Path:
    """A prefix whose bin/python really is an interpreter."""
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    (prefix / "bin" / "python").symlink_to(sys.executable)
    return prefix


def _build_generation(store: EnvironmentStore, name: str, spec: Path) -> str:
    """Apply one generation whose build produces a working interpreter."""
    plan = store.plan(name, spec, tool="fake-conda")

    def build(prefix: Path, staged_spec: Path):
        _real_python_prefix(prefix)
        return ["true"]

    result = store.apply(plan, spec, tool="fake-conda", build=build)
    assert result.ok, result.detail
    return result.generation.id


# --------------------------------------------------------------------------
# discovery reads the pointer, and a real kernel runs what it points at
# --------------------------------------------------------------------------


def test_the_current_generation_is_the_interpreter_a_kernel_actually_runs(
    cfg, tmp_path
):
    spec = tmp_path / "science.yml"
    spec.write_text("name: science\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    generation_id = _build_generation(store, "science", spec)

    envmod.invalidate_cache()
    discovered = envmod.get_environment("science")
    assert discovered is not None, (
        "apply reported a new current generation that discovery cannot see; "
        "no cell can ever run in it"
    )
    expected = (
        Path(cfg.data_dir)
        / "environments"
        / "science"
        / "generations"
        / generation_id
        / "prefix"
        / "bin"
        / "python"
    )
    assert discovered.interpreter == str(expected)

    with Kernel(
        python=discovered.interpreter,
        env_root=str(discovered.root),
        env_name=discovered.name,
    ) as kernel:
        result = kernel.execute("import sys; print(sys.executable)")
    assert result["error"] is None, result["error"]
    assert result["stdout"].strip() == str(expected), (
        "the cell ran under a different interpreter than the current "
        "generation names"
    )


def test_rollback_moves_what_the_next_kernel_runs(cfg, tmp_path):
    spec = tmp_path / "science.yml"
    spec.write_text("name: science\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    first = _build_generation(store, "science", spec)

    spec.write_text("name: science\ndependencies: [python, numpy]\n", encoding="utf-8")
    second = _build_generation(store, "science", spec)
    assert second != first

    envmod.invalidate_cache()
    assert second in (envmod.get_environment("science").interpreter or "")

    store.rollback("science", first)

    after = envmod.get_environment("science")
    assert after is not None
    assert first in (after.interpreter or ""), (
        "rollback moved the pointer but discovery kept serving the cached "
        "interpreter, so nothing a kernel does could change"
    )
    with Kernel(
        python=after.interpreter, env_root=str(after.root), env_name=after.name
    ) as kernel:
        result = kernel.execute("import sys; print(sys.executable)")
    assert result["error"] is None
    assert first in result["stdout"]


def test_a_generation_environment_wins_over_a_conda_env_of_the_same_name(
    cfg, tmp_path, monkeypatch
):
    """The pointer is the explicit act; a scanned directory is an inference."""
    conda_root = tmp_path / "conda-envs"
    (conda_root / "science" / "bin").mkdir(parents=True)
    (conda_root / "science" / "bin" / "python").symlink_to(sys.executable)
    monkeypatch.setenv("OPENAI4S_ENV_ROOTS", str(conda_root))

    spec = tmp_path / "science.yml"
    spec.write_text("name: science\ndependencies: [python]\n", encoding="utf-8")
    _build_generation(_store(cfg), "science", spec)

    envmod.invalidate_cache()
    found = envmod.get_environment("science")
    assert "generations" in (
        found.interpreter or ""
    ), "a generation the user applied must outrank a directory we guessed at"


# --------------------------------------------------------------------------
# the pointer only moves onto an interpreter that ran
# --------------------------------------------------------------------------


def test_apply_refuses_a_python_that_cannot_run(cfg, tmp_path):
    spec = tmp_path / "broken.yml"
    spec.write_text("name: broken\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    plan = store.plan("broken", spec, tool="fake-conda")

    def build(prefix: Path, staged_spec: Path):
        (prefix / "bin").mkdir(parents=True)
        stub = prefix / "bin" / "python"
        stub.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return ["true"]

    result = store.apply(plan, spec, tool="fake-conda", build=build)

    assert result.ok is False
    assert (
        store.current_id("broken") is None
    ), "current moved onto an interpreter that exits non-zero on startup"
    assert "probe" in (result.detail or "").lower()


def test_apply_refuses_a_python_that_is_not_executable(cfg, tmp_path):
    spec = tmp_path / "broken.yml"
    spec.write_text("name: broken\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    plan = store.plan("broken", spec, tool="fake-conda")

    def build(prefix: Path, staged_spec: Path):
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").write_text("not an interpreter", encoding="utf-8")
        return ["true"]

    result = store.apply(plan, spec, tool="fake-conda", build=build)
    assert result.ok is False
    assert store.current_id("broken") is None


def test_apply_executes_the_r_candidate_too(cfg, tmp_path):
    """An R generation used never to be run at all."""
    spec = tmp_path / "r.yml"
    spec.write_text("name: r\ndependencies: [r-base]\n", encoding="utf-8")
    store = _store(cfg)
    plan = store.plan("r", spec, tool="fake-conda")

    def build(prefix: Path, staged_spec: Path):
        (prefix / "bin").mkdir(parents=True)
        stub = prefix / "bin" / "Rscript"
        stub.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return ["true"]

    result = store.apply(plan, spec, tool="fake-conda", build=build)
    assert result.ok is False
    assert store.current_id("r") is None


# --------------------------------------------------------------------------
# the spec the manifest names is the spec that was built
# --------------------------------------------------------------------------


def test_a_spec_edited_between_plan_and_apply_is_rejected(cfg, tmp_path):
    spec = tmp_path / "science.yml"
    spec.write_text("name: science\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    plan = store.plan("science", spec, tool="fake-conda")

    spec.write_text("name: science\ndependencies: [python, scipy]\n", encoding="utf-8")

    def build(prefix: Path, staged_spec: Path):
        _real_python_prefix(prefix)
        return ["true"]

    with pytest.raises(EnvironmentError_) as error:
        store.apply(plan, spec, tool="fake-conda", build=build)
    assert "changed" in str(error.value).lower()
    assert store.current_id("science") is None


def test_the_build_reads_a_staged_copy_of_the_spec(cfg, tmp_path):
    """The builder must not be able to read a file still open to editing."""
    spec = tmp_path / "science.yml"
    spec.write_text("name: science\ndependencies: [python]\n", encoding="utf-8")
    store = _store(cfg)
    plan = store.plan("science", spec, tool="fake-conda")
    seen: list[tuple[Path, str]] = []

    def build(prefix: Path, staged_spec: Path):
        seen.append((staged_spec, staged_spec.read_text(encoding="utf-8")))
        _real_python_prefix(prefix)
        return ["true"]

    result = store.apply(plan, spec, tool="fake-conda", build=build)
    assert result.ok, result.detail
    assert seen and seen[0][0] != spec, "the build was handed the live spec path"
    assert seen[0][1] == "name: science\ndependencies: [python]\n"

    # And it stays with the generation, so the manifest's hash has the bytes it
    # was taken from sitting next to it.
    kept = (
        Path(cfg.data_dir)
        / "environments"
        / "science"
        / "generations"
        / result.generation.id
        / "spec.yml"
    )
    assert kept.is_file()
    assert (
        _sha(kept)
        == json.loads((kept.parent / "manifest.json").read_text(encoding="utf-8"))[
            "spec_sha256"
        ]
    )


# --------------------------------------------------------------------------
# provenance never borrows
# --------------------------------------------------------------------------


def test_a_generation_without_an_interpreter_does_not_borrow_daemon_packages():
    from openai4s.server.artifacts import ArtifactManager

    manager = ArtifactManager.__new__(ArtifactManager)
    generation = {
        "generation_id": "kern-legacy",
        "environment": {"runtime": "python", "environment_name": "legacy"},
    }

    snapshot = ArtifactManager._snapshot_for(manager, generation, "python")

    assert snapshot["generation_id"] == "kern-legacy"
    assert snapshot["packages"] == [], (
        "a generation with no interpreter on record was given the daemon's "
        "package list under its own generation id"
    )
    assert "packages_unavailable" in snapshot
