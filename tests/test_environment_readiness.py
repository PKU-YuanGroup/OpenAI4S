"""Local, side-effect-free readiness for the bundled standard profile."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from openai4s.kernel import environments as environments_mod
from openai4s.kernel.environments import Environment
from openai4s.kernel.readiness import (
    NEEDS_REPAIR,
    NEEDS_SETUP,
    READY,
    UNAVAILABLE,
    load_standard_profile_requirements,
    readiness_failure_message,
    standard_profile_readiness,
)

PYTHON_REQUIREMENTS = (
    "python",
    "anndata",
    "leidenalg",
    "python-igraph",
    "umap-learn",
    "numba",
    "rdkit",
    "scikit-learn",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "seaborn",
    "plotly",
    "pillow",
    "biopython",
    "h5py",
    "zarr",
    "pyarrow",
    "openpyxl",
    "pypdf",
    "beautifulsoup4",
    "httpx",
    "socksio",
    "pysocks",
    "tqdm",
    "statsmodels",
    "networkx",
    "pip",
    "fair-esm",
    "scanpy",
    "pydeseq2",
    "pypdfium2",
)
R_REQUIREMENTS = (
    "r-base",
    "r-tidyverse",
    "r-data-table",
    "r-ggplot2",
    "r-rmarkdown",
    "r-knitr",
    "r-jsonlite",
    "pandoc",
)


def _managed_repair() -> dict[str, object]:
    plan = ["openai4s", "env", "plan", "python", "r", "--repair"]
    apply = ["openai4s", "env", "apply", "python", "r", "--repair"]
    return {
        "kind": "managed_generation_repair",
        "plan_argv": plan,
        "apply_argv": apply,
        "commands": [
            {"label": "plan", "argv": plan, "command": " ".join(plan)},
            {"label": "apply", "argv": apply, "command": " ".join(apply)},
        ],
        "requires_explicit_action": True,
    }


def _environment(root: Path, name: str) -> Environment:
    root.mkdir(parents=True)
    if name == "r":
        return Environment(
            name="r",
            language="r",
            root=root,
            python=None,
            rscript=str(root / "bin" / "Rscript"),
        )
    return Environment(
        name="python",
        language="python",
        root=root,
        python=str(root / "bin" / "python"),
        rscript=None,
    )


def _write_inventory(root: Path, packages: tuple[str, ...] | list[str]) -> None:
    metadata = root / "conda-meta"
    metadata.mkdir(parents=True)
    for index, package in enumerate(packages):
        (metadata / f"{index:02d}-{package}.json").write_text(
            json.dumps({"name": package}), encoding="utf-8"
        )


def _complete_environments(tmp_path: Path) -> list[Environment]:
    python_root = tmp_path / "python-prefix"
    r_root = tmp_path / "r-prefix"
    python = _environment(python_root, "python")
    r = _environment(r_root, "r")
    _write_inventory(python_root, PYTHON_REQUIREMENTS)
    _write_inventory(r_root, R_REQUIREMENTS)
    return [python, r]


def _write_managed_generation(
    root: Path,
    name: str,
    packages: tuple[str, ...],
    *,
    generation_id: str = "env-ready",
    state: str = "ready",
) -> Path:
    generation = root / name / "generations" / generation_id
    prefix = generation / "prefix"
    (prefix / "bin").mkdir(parents=True)
    runtime = prefix / "bin" / ("Rscript" if name == "r" else "python")
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    _write_inventory(prefix, packages)
    (generation / "manifest.json").write_text(
        json.dumps(
            {
                "generation_id": generation_id,
                "environment": name,
                "state": state,
                "spec_sha256": "0" * 64,
                "prefix": str(prefix),
                "created_at": 1,
                "interpreter": str(runtime),
            }
        ),
        encoding="utf-8",
    )
    return generation


def test_shipped_manifests_are_the_authoritative_33_and_8_package_lists():
    requirements = load_standard_profile_requirements()

    assert requirements == {
        "python": PYTHON_REQUIREMENTS,
        "r": R_REQUIREMENTS,
    }
    assert len(requirements["python"]) == 33
    assert len(requirements["r"]) == 8
    # Both conda and pip constraints are gone before matching package metadata,
    # and pip extras are stripped down to the distribution name.
    assert "python=3.11" not in requirements["python"]
    assert "pandas<3" not in requirements["python"]
    assert "fair-esm==2.0.0" not in requirements["python"]
    assert "scanpy[harmony,skmisc]==1.11.5" not in requirements["python"]
    assert "pertpy" not in requirements["python"]  # extra-only: its core needs JAX
    assert requirements["python"][-2:] == ("pydeseq2", "pypdfium2")

    projection = standard_profile_readiness(enabled=True, discover=lambda: [])
    assert projection["requirements_digest"] == (
        "sha256:80c9ab912a275ffb0d41205019faca4c40124951ff630bf6cc3da0921a7c3928"
    )


def test_complete_standard_profile_is_ready_using_local_package_metadata(tmp_path):
    found = _complete_environments(tmp_path)

    result = standard_profile_readiness(enabled=True, discover=lambda: found)

    assert result["state"] == READY
    assert result["ready"] is True
    assert result["reason"] is None
    assert result["schema_version"] == 1
    assert result["network_contacted"] is False
    assert result["mutation_performed"] is False
    assert str(result["requirements_digest"]).startswith("sha256:")
    assert len(str(result["requirements_digest"])) == 71
    assert result["missing_environments"] == []
    assert result["missing_packages"] == {}
    assert result["remediation"] is None
    assert [row["required_package_count"] for row in result["environments"]] == [
        33,
        8,
    ]
    assert all(row["state"] == READY for row in result["environments"])
    serialized = json.dumps(result, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "prefix" not in serialized


def test_production_managed_discovery_accepts_only_valid_ready_generations(
    tmp_path, monkeypatch
):
    root = tmp_path / "environments"
    for name, packages in (("python", PYTHON_REQUIREMENTS), ("r", R_REQUIREMENTS)):
        _write_managed_generation(root, name, packages)
        (root / name / "current").write_text("env-ready\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_ENV_GENERATIONS_ROOT", str(root))

    result = standard_profile_readiness(
        enabled=True,
        discover=environments_mod._generation_environments,
    )

    assert result["state"] == READY
    assert result["ready"] is True


def test_out_of_root_managed_pointers_cannot_make_readiness_ready(
    tmp_path, monkeypatch
):
    root = tmp_path / "environments"
    outside = tmp_path / "outside"
    for name, packages in (("python", PYTHON_REQUIREMENTS), ("r", R_REQUIREMENTS)):
        generation = _write_managed_generation(
            outside,
            name,
            packages,
            generation_id=f"outside-{name}",
        )
        pointer = root / name / "current"
        pointer.parent.mkdir(parents=True)
        # pathlib discards every prefix before an absolute component. The old
        # discovery joined this text directly and therefore selected outside
        # bytes as a trusted managed generation.
        pointer.write_text(str(generation) + "\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_ENV_GENERATIONS_ROOT", str(root))

    result = standard_profile_readiness(
        enabled=True,
        discover=environments_mod._generation_environments,
    )

    assert result["ready"] is False
    assert result["state"] == NEEDS_SETUP
    assert result["missing_environments"] == ["python", "r"]


def test_managed_discovery_refuses_non_ready_and_symlinked_pointers(
    tmp_path, monkeypatch
):
    root = tmp_path / "environments"
    _write_managed_generation(
        root,
        "python",
        PYTHON_REQUIREMENTS,
        state="failed",
    )
    python_pointer = root / "python" / "current"
    python_pointer.write_text("env-ready\n", encoding="utf-8")
    _write_managed_generation(root, "r", R_REQUIREMENTS)
    external_pointer = tmp_path / "external-current"
    external_pointer.write_text("env-ready\n", encoding="utf-8")
    (root / "r" / "current").symlink_to(external_pointer)
    monkeypatch.setenv("OPENAI4S_ENV_GENERATIONS_ROOT", str(root))

    assert environments_mod._generation_environments() == []


def test_managed_discovery_refuses_a_symlinked_generations_directory(
    tmp_path, monkeypatch
):
    root = tmp_path / "environments"
    outside = tmp_path / "outside"
    for name, packages in (("python", PYTHON_REQUIREMENTS), ("r", R_REQUIREMENTS)):
        _write_managed_generation(outside, name, packages)
        env_dir = root / name
        env_dir.mkdir(parents=True)
        (env_dir / "generations").symlink_to(
            outside / name / "generations",
            target_is_directory=True,
        )
        (env_dir / "current").write_text("env-ready\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_ENV_GENERATIONS_ROOT", str(root))

    assert environments_mod._generation_environments() == []
    production = standard_profile_readiness(enabled=True)
    assert production["ready"] is False
    assert production["state"] == UNAVAILABLE
    assert production["reason"] == "managed_environment_layout_invalid"
    assert production["remediation"] is None


def test_managed_discovery_accepts_a_rollback_eligible_superseded_generation(
    tmp_path, monkeypatch
):
    root = tmp_path / "environments"
    for name, packages in (("python", PYTHON_REQUIREMENTS), ("r", R_REQUIREMENTS)):
        _write_managed_generation(root, name, packages, state="superseded")
        (root / name / "current").write_text("env-ready\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_ENV_GENERATIONS_ROOT", str(root))

    result = standard_profile_readiness(
        enabled=True,
        discover=environments_mod._generation_environments,
    )

    assert result["ready"] is True
    assert result["state"] == READY


def test_production_readiness_discovery_does_not_create_the_data_directory(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "fresh-data"
    monkeypatch.delenv("OPENAI4S_ENV_GENERATIONS_ROOT", raising=False)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data_dir))
    assert not data_dir.exists()

    result = standard_profile_readiness(
        enabled=True,
        discover=environments_mod._generation_environments,
    )

    assert result["mutation_performed"] is False
    assert not data_dir.exists()


def test_missing_environment_offers_transactional_managed_repair(tmp_path):
    python_root = tmp_path / "python-prefix"
    python = _environment(python_root, "python")
    _write_inventory(python_root, PYTHON_REQUIREMENTS)

    result = standard_profile_readiness(enabled=True, discover=lambda: [python])

    assert result["state"] == NEEDS_SETUP
    assert result["reason"] == "environment_missing"
    assert result["missing_environments"] == ["r"]
    assert result["missing_packages"] == {"r": list(R_REQUIREMENTS)}
    assert result["remediation"] == _managed_repair()


def test_missing_package_offers_explicit_transactional_repair(tmp_path):
    found = _complete_environments(tmp_path)
    missing_numpy = set(PYTHON_REQUIREMENTS) - {"numpy"}

    def scan(root: Path, *, language: str) -> set[str]:
        if language == "python":
            return missing_numpy
        return set(R_REQUIREMENTS)

    result = standard_profile_readiness(
        enabled=True, discover=lambda: found, scan_packages=scan
    )

    assert result["state"] == NEEDS_REPAIR
    assert result["reason"] == "environment_incomplete"
    assert result["missing_environments"] == []
    assert result["missing_packages"] == {"python": ["numpy"]}
    assert result["remediation"] == _managed_repair()

    message = readiness_failure_message(result)
    assert "openai4s env plan python r --repair" in message
    assert "openai4s env apply python r --repair" in message
    assert "openai4s setup" not in message


def test_package_scan_fault_is_unavailable_not_every_package_missing(tmp_path):
    found = _complete_environments(tmp_path)

    def failed_scan(root: Path, *, language: str) -> set[str]:
        if language == "python":
            raise OSError(f"private scanner detail: {root}")
        return set(R_REQUIREMENTS)

    result = standard_profile_readiness(
        enabled=True, discover=lambda: found, scan_packages=failed_scan
    )

    assert result["state"] == UNAVAILABLE
    assert result["reason"] == "package_inventory_unavailable"
    assert result["remediation"] is None
    python = result["environments"][0]
    assert python["state"] == UNAVAILABLE
    assert python["installed_required_package_count"] is None
    assert python["missing_packages"] == []
    assert python["issue"] == "package_inventory_unavailable"
    assert str(tmp_path) not in json.dumps(result, sort_keys=True)
    assert "private scanner detail" not in json.dumps(result, sort_keys=True)


def test_discovery_fault_is_reported_without_leaking_the_exception():
    def failed_discovery() -> list[Environment]:
        raise OSError("secret local discovery path")

    result = standard_profile_readiness(enabled=True, discover=failed_discovery)

    assert result["state"] == UNAVAILABLE
    assert result["reason"] == "environment_discovery_unavailable"
    assert result["environments"] == []
    assert "secret local discovery path" not in json.dumps(result)


def test_malformed_manifest_fails_honestly_and_does_not_discover(tmp_path, monkeypatch):
    (tmp_path / "python.yml").write_text(
        "name: python\ndependencies:\n  - numpy @ https://example.invalid/a.whl\n",
        encoding="utf-8",
    )
    (tmp_path / "r.yml").write_text(
        "name: r\ndependencies:\n  - r-base\n", encoding="utf-8"
    )
    discovered = []

    result = standard_profile_readiness(
        enabled=True,
        specs_dir=tmp_path,
        discover=lambda: (discovered.append(True) or []),
    )

    assert result["state"] == UNAVAILABLE
    assert result["reason"] == "manifest_unavailable"
    assert discovered == []
    assert str(tmp_path) not in json.dumps(result)


def test_disabled_flag_returns_before_manifest_or_environment_discovery(tmp_path):
    calls = []

    def forbidden_discovery() -> list[Environment]:
        calls.append("discover")
        raise AssertionError("disabled readiness discovered environments")

    result = standard_profile_readiness(
        enabled=False,
        specs_dir=tmp_path / "does-not-exist",
        discover=forbidden_discovery,
        scan_packages=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled readiness scanned packages")
        ),
    )

    assert result == {
        "schema_version": 1,
        "enabled": False,
        "profile": "standard",
        "state": UNAVAILABLE,
        "ready": False,
        "reason": "feature_disabled",
        "checked_locally": False,
        "network_contacted": False,
        "mutation_performed": False,
        "requirements_digest": None,
        "required_environments": ["python", "r"],
        "missing_environments": [],
        "missing_packages": {},
        "environments": [],
        "remediation": None,
    }
    assert calls == []


def test_readiness_never_starts_processes_uses_network_or_writes(tmp_path, monkeypatch):
    found = _complete_environments(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness attempted an external side effect")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)

    result = standard_profile_readiness(enabled=True, discover=lambda: found)

    assert result["state"] == READY


def test_requirements_digest_is_stable_across_environment_locations(tmp_path):
    first = _complete_environments(tmp_path / "first")
    second = _complete_environments(tmp_path / "second")

    first_result = standard_profile_readiness(enabled=True, discover=lambda: first)
    second_result = standard_profile_readiness(enabled=True, discover=lambda: second)

    assert first_result["requirements_digest"] == second_result["requirements_digest"]


def test_wrong_runtime_is_repairable_but_never_claimed_ready(tmp_path):
    found = _complete_environments(tmp_path)
    r = found[1]
    r.language = "python"
    r.python = str(r.root / "bin" / "python")
    r.rscript = None

    result = standard_profile_readiness(enabled=True, discover=lambda: found)

    assert result["state"] == NEEDS_REPAIR
    assert result["missing_packages"]["r"] == list(R_REQUIREMENTS)
    assert result["environments"][1]["issue"] == "runtime_mismatch"


def test_duplicate_standard_environment_names_fail_closed(tmp_path):
    found = _complete_environments(tmp_path)
    duplicate = _environment(tmp_path / "other-python", "python")

    result = standard_profile_readiness(
        enabled=True, discover=lambda: [*found, duplicate]
    )

    assert result["state"] == UNAVAILABLE
    assert result["reason"] == "duplicate_environment"
    assert result["environments"] == []
