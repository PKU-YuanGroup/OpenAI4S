"""Execute generated Scenario CLIs across fixture and chemistry boundaries."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.kernel.environment import build_kernel_environment

SKILLS = Path(__file__).resolve().parents[1] / "skills"
SCENARIOS = SKILLS / "retrosynthesis_planning" / "scenarios"
NAMES = (
    "01_single_step_retrosynthesis",
    "02_multistep_route_planning",
    "03_atom_mapping",
    "04_forward_prediction",
    "05_condition_recommendation",
    "06_yield_estimation",
)

sys.path.insert(0, str(SKILLS))

from retrosynthesis_planning.gt_codebase import install_test_case, run_pipeline


def _workspace(tmp_path, name):
    workspace = tmp_path / "workspace"
    install_test_case(SCENARIOS / "test_cases" / f"{name}.json", workspace)
    (workspace / "private_evaluator").rename(tmp_path / "hidden")
    return workspace


def _run(name, workspace, *, stdlib_only=True):
    # Isolated mode excludes PYTHONPATH and user configuration. -S additionally
    # proves the protocol fixture needs no installed chemistry or other package.
    flags = ["-I", "-S"] if stdlib_only else ["-I"]
    return subprocess.run(
        [
            sys.executable,
            *flags,
            str(SCENARIOS / "openai4s_codebases" / f"{name}.py"),
            "--workspace",
            str(workspace),
        ],
        cwd=workspace,
        # These are model-written programs. Handing them the operator's whole
        # environment would give LLM-generated code the provider credentials
        # this PR's own generator test asserts must never cross the boundary.
        env=build_kernel_environment(cwd=str(workspace)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _rewrite(workspace, relative, payload):
    path = workspace / relative
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest_path = workspace / "installation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_generated_cli_matches_frozen_fixture_without_site_packages(tmp_path, name):
    workspace = _workspace(tmp_path, name)
    manifest = json.loads((workspace / "installation.json").read_text(encoding="utf-8"))
    artifact_path = workspace / "results" / "intermediate_results.json"
    run_pipeline(manifest["scenario"], workspace)
    expected = artifact_path.read_bytes()
    artifact_path.unlink()

    completed = _run(name, workspace)

    assert completed.returncode == 0, completed.stderr
    assert artifact_path.read_bytes() == expected
    generation = json.loads(
        (SCENARIOS / "openai4s_codebases" / "generation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    entry = next(entry for entry in generation["entries"] if entry["name"] == name)
    assert hashlib.sha256(expected).hexdigest() == entry["verified_artifact_sha256"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("dataset_profile", ["production", None])
def test_generated_cli_requires_chemistry_outside_fixture_profile(
    tmp_path, name, dataset_profile
):
    workspace = _workspace(tmp_path, name)
    manifest_path = workspace / "installation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if dataset_profile is None:
        manifest.pop("dataset_profile")
    else:
        manifest["dataset_profile"] = dataset_profile
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    completed = _run(name, workspace)

    assert completed.returncode != 0
    assert "RDKit is required" in completed.stderr
    assert not (workspace / "results" / "intermediate_results.json").exists()


def test_generated_atom_mapping_rejects_duplicate_output_ids(tmp_path):
    name = "03_atom_mapping"
    workspace = _workspace(tmp_path, name)
    rows = json.loads((workspace / "public" / "model_outputs.json").read_text())
    _rewrite(workspace, "public/model_outputs.json", rows + rows)

    completed = _run(name, workspace)

    assert completed.returncode != 0
    assert "duplicate reaction" in completed.stderr
    assert not (workspace / "results" / "intermediate_results.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid", 1),
        ("valid", "true"),
        ("correspondence", "r0:a0=>p0:a0"),
        ("correspondence", [1]),
        ("correspondence", ["  "]),
        ("bond_changes", {}),
        ("bond_changes", [None]),
        ("bond_changes", ["  "]),
        ("issues", "warning"),
        ("issues", [False]),
        ("issues", [""]),
    ],
)
def test_generated_atom_mapping_rejects_malformed_fixture_values(
    tmp_path, field, value
):
    name = "03_atom_mapping"
    workspace = _workspace(tmp_path, name)
    rows = json.loads((workspace / "public" / "model_outputs.json").read_text())
    rows[0][field] = value
    _rewrite(workspace, "public/model_outputs.json", rows)

    completed = _run(name, workspace)

    assert completed.returncode != 0
    assert field in completed.stderr
    assert not (workspace / "results" / "intermediate_results.json").exists()


def test_generated_forward_production_canonicalizes_equivalent_products(tmp_path):
    pytest.importorskip("rdkit", reason="production chemistry dependency is optional")
    name = "04_forward_prediction"
    workspace = _workspace(tmp_path, name)
    manifest_path = workspace / "installation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_profile"] = "production"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _rewrite(
        workspace,
        "public/inputs.json",
        [{"reaction_id": "reaction_001", "reactants": "CC", "reagents": ""}],
    )
    _rewrite(
        workspace,
        "public/model_outputs.json",
        [
            {
                "reaction_id": "reaction_001",
                "predictions": [
                    {"rank": 1, "product_smiles": "OCC", "score": 0.9},
                    {"rank": 2, "product_smiles": "CCO", "score": 0.1},
                ],
                "error": None,
            }
        ],
    )
    artifact_path = workspace / "results" / "intermediate_results.json"
    run_pipeline("forward", workspace)
    expected = artifact_path.read_bytes()
    artifact_path.unlink()

    completed = _run(name, workspace, stdlib_only=False)

    assert completed.returncode == 0, completed.stderr
    assert artifact_path.read_bytes() == expected
    records = json.loads(expected)["records"]
    assert records[0]["predictions"][1]["duplicate_of_rank"] == 1
