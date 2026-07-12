from __future__ import annotations

import hashlib

from openai4s.execution.dependencies import analyze_code
from openai4s.kernel.recovery import BootstrapManifest
from openai4s.server.recovery_recipe import build_recovery_recipe


def _manifest(language: str = "python") -> dict:
    return BootstrapManifest(
        language=language,
        interpreter="/env/bin/python" if language == "python" else "/env/bin/Rscript",
        runtime_version="3.12" if language == "python" else "R version 4.4.0",
        working_directory="/workspace",
        environment={"environment_name": "science", "is_conda": True},
        sdk_version="sdk-1" if language == "python" else None,
    ).record()


def _cell(code: str, index: int, *, language: str = "python", **updates) -> dict:
    metadata = analyze_code(code, language)
    value = {
        "producing_cell_id": f"cell-{index}",
        "state_revision": index,
        "language": language,
        "status": "ok",
        "code": code,
        "replay_policy": "conditional",
        "files_read": [],
        "files_written": [],
        **metadata.as_record(),
    }
    value.update(updates)
    return value


def test_recipe_compiles_a_dependency_closed_python_namespace():
    recipe = build_recovery_recipe(
        [
            _cell("data = [2, 4, 6]", 1),
            _cell(
                "factor = 3\nscores = [value * factor for value in data]\n"
                "total = sum(scores)",
                2,
            ),
        ],
        generation_refs={"python": {"bootstrap": _manifest()}},
        artifact_hashes={"prediction.csv": "a" * 64},
    )

    assert recipe["namespace_coverage"] == "verified"
    assert recipe["summary"] == {
        "state_cells": 2,
        "safe_replay_cells": 2,
        "manual_cells": 0,
    }
    assert [step["replay_policy"] for step in recipe["steps"]] == ["safe", "safe"]
    assert recipe["steps"][1]["payload"]["dependency_cells"] == ["cell-1"]
    assert recipe["steps"][1]["payload"]["required_symbols"] == [
        "data",
        "sum",
    ]
    assert recipe["required_symbols"]["python"] == [
        "data",
        "factor",
        "scores",
        "total",
    ]
    assert recipe["artifact_hashes"] == {"prediction.csv": "a" * 64}
    assert recipe["environment_requirements"]["python"] == {
        "environment_name": "science",
        "is_conda": True,
        "runtime_version": "3.12",
        "sdk_version": "sdk-1",
    }


def test_recipe_retains_external_and_unresolved_state_as_manual_steps():
    external = _cell(
        "job = host.compute_submit({'command': 'train'})",
        1,
        files_written=["checkpoint.bin"],
    )
    dependent = _cell("result = job", 2)
    random_cell = _cell("import random\nnonce = random.random()", 3)

    recipe = build_recovery_recipe(
        [external, dependent, random_cell],
        generation_refs={"python": {"bootstrap": _manifest()}},
        artifact_hashes={},
    )

    assert recipe["namespace_coverage"] == "unverified"
    assert recipe["summary"]["manual_cells"] == 3
    assert all(step["replay_policy"] == "never" for step in recipe["steps"])
    first_reasons = recipe["steps"][0]["payload"]["manual_reasons"]
    assert "Cell wrote external/workspace files" in first_reasons
    assert any("unsafe Host" in reason for reason in first_reasons)
    assert recipe["steps"][1]["payload"]["manual_reasons"] == [
        "unresolved input symbols: job"
    ]
    assert any(
        "random state" in reason
        for reason in recipe["steps"][2]["payload"]["manual_reasons"]
    )


def test_recipe_requires_exact_recorded_source_hash_and_r_bootstrap():
    r_cell = _cell("values <- c(1, 2, 3)\naverage <- mean(values)", 1, language="r")
    r_cell["code_hash"] = hashlib.sha256(b"different").hexdigest()

    recipe = build_recovery_recipe(
        [r_cell],
        generation_refs={"r": {"bootstrap": _manifest("r")}},
        artifact_hashes={},
    )

    assert recipe["namespace_coverage"] == "unverified"
    assert recipe["steps"][0]["replay_policy"] == "never"
    assert (
        "recorded source hash does not match Cell source"
        in recipe["steps"][0]["payload"]["manual_reasons"]
    )
