"""Frozen-dataset integrity and direct-entrypoint contracts for Scenario scoring."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.config import get_config

sys.path.insert(0, str(get_config().skills_dir))

from retrosynthesis_planning.gt_codebase import (  # noqa: E402
    BenchmarkProtocolError,
    evaluate_workspace,
    install_test_case,
    run_pipeline,
)

SCENARIOS = {
    "single_step": "01_single_step_retrosynthesis",
    "multistep": "02_multistep_route_planning",
    "atom_mapping": "03_atom_mapping",
    "forward": "04_forward_prediction",
    "conditions": "05_condition_recommendation",
    "yield": "06_yield_estimation",
}


def _scenario_dir():
    return Path(get_config().skills_dir) / "retrosynthesis_planning" / "scenarios"


def _install(tmp_path, scenario):
    workspace = tmp_path / "workspace"
    install_test_case(
        _scenario_dir() / "test_cases" / f"{SCENARIOS[scenario]}.json", workspace
    )
    return workspace


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_gt_entrypoint_runs_without_pythonpath_or_private_files(tmp_path, scenario):
    workspace = _install(tmp_path, scenario)
    hidden = tmp_path / "hidden"
    (workspace / "private_evaluator").rename(hidden)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(_scenario_dir() / "gt_codebases" / f"{SCENARIOS[scenario]}.py"),
            "--workspace",
            str(workspace),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (workspace / "results" / "intermediate_results.json").is_file()
    hidden.rename(workspace / "private_evaluator")
    assert evaluate_workspace(scenario, workspace)["scenario_id"]


def test_evaluator_rejects_changed_labels_without_replacing_metrics(tmp_path):
    workspace = _install(tmp_path, "yield")
    run_pipeline("yield", workspace)
    assert evaluate_workspace("yield", workspace)["overall"]["mae"] == 2.2
    evaluation_path = workspace / "results" / "evaluation.json"
    frozen_metrics = evaluation_path.read_bytes()
    artifact_path = workspace / "results" / "intermediate_results.json"
    frozen_artifact = artifact_path.read_bytes()
    outputs = json.loads((workspace / "public" / "model_outputs.json").read_text())
    references = [
        {
            "reaction_id": row["reaction_id"],
            "yield_percent": row["predicted_yield_percent"],
        }
        for row in outputs
    ]
    (workspace / "private_evaluator" / "references.json").write_text(
        json.dumps(references)
    )
    with pytest.raises(BenchmarkProtocolError, match="installed file hash mismatch"):
        evaluate_workspace("yield", workspace)
    assert evaluation_path.read_bytes() == frozen_metrics
    assert artifact_path.read_bytes() == frozen_artifact


@pytest.mark.parametrize("operation", [run_pipeline, evaluate_workspace])
@pytest.mark.parametrize("name", ["inputs.json", "model_outputs.json", "config.json"])
def test_changed_public_files_are_rejected(tmp_path, name, operation):
    workspace = _install(tmp_path, "yield")
    run_pipeline("yield", workspace)
    path = workspace / "public" / name
    # Even an equivalent JSON reserialization differs from the frozen file bytes.
    path.write_text(path.read_text() + "\n")
    with pytest.raises(BenchmarkProtocolError, match="installed file hash mismatch"):
        operation("yield", workspace)


@pytest.mark.parametrize("boundary", ["public", "private_evaluator"])
def test_evaluator_requires_hashes_for_consumed_files(tmp_path, boundary):
    workspace = _install(tmp_path, "yield")
    run_pipeline("yield", workspace)
    path = workspace / "installation.json"
    installation = json.loads(path.read_text())
    name = "inputs.json" if boundary == "public" else "references.json"
    del installation["file_sha256"][f"{boundary}/{name}"]
    path.write_text(json.dumps(installation))
    with pytest.raises(BenchmarkProtocolError, match="missing required file hashes"):
        evaluate_workspace("yield", workspace)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("valid", 2, "valid must be a boolean"),
        ("valid", "false", "valid must be a boolean"),
        ("valid", None, "valid must be a boolean"),
        ("issues", "not an array", "issues must be an array"),
        ("correspondence", "not an array", "correspondence must be an array"),
        ("bond_changes", "not an array", "bond_changes must be an array"),
        ("issues", [1], "issues entry must be a non-empty string"),
        ("correspondence", [{}], "correspondence entry must be a non-empty string"),
        ("bond_changes", [[]], "bond_changes entry must be a non-empty string"),
    ],
)
def test_atom_fixture_rejects_invalid_field_types(tmp_path, field, value, message):
    source = _scenario_dir() / "test_cases" / "03_atom_mapping.json"
    case = json.loads(source.read_text())
    case["public"]["model_outputs.json"][0][field] = value
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(case))
    workspace = tmp_path / "workspace"
    # Install the malformed row so its hash is valid: schema validation must fail.
    install_test_case(altered, workspace)
    with pytest.raises(BenchmarkProtocolError, match=message):
        run_pipeline("atom_mapping", workspace)
    assert not (workspace / "results" / "intermediate_results.json").exists()
