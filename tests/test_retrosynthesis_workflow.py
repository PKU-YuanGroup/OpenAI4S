"""Focused offline tests for the higher-level retrosynthesis workflow helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openai4s.config import get_config

sys.path.insert(0, str(get_config().skills_dir))
from retrosynthesis_planning.workflow import (  # noqa: E402
    AiZynthSearchSpec,
    audit_routes,
    build_aizynth_search_command,
    deduplicate_routes,
    prepare_routes,
    route_signature,
    select_diverse_routes,
)


def _route(rank: int, product: str, template: str, *precursors: str) -> dict:
    return {
        "rank": rank,
        "score": 1 - rank / 100,
        "solved": True,
        "steps": 1,
        "starting_materials": list(precursors),
        "tree": {
            "type": "mol",
            "smiles": product,
            "children": [
                {
                    "type": "reaction",
                    "template": template,
                    "children": [
                        {
                            "type": "mol",
                            "smiles": precursor,
                            "in_stock": True,
                            "children": [],
                        }
                        for precursor in precursors
                    ],
                }
            ],
        },
    }


def test_search_spec_builds_documented_cli_options_in_stable_order(tmp_path):
    checkpoint = tmp_path / "checkpoint.json.gz"
    command = build_aizynth_search_command(
        "CCO",
        "config.yml",
        output_path="routes.json",
        conda_env="retro",
        search=AiZynthSearchSpec(
            policies=("uspto", "ringbreaker"),
            filters=("quick",),
            stocks=("zinc", "internal"),
            cluster=True,
            nproc=4,
            checkpoint_path=checkpoint,
            log_to_file=True,
            post_processing=("my.post",),
            pre_processing="my.pre",
        ),
    )

    assert command == [
        "conda",
        "run",
        "-n",
        "retro",
        "aizynthcli",
        "--config",
        "config.yml",
        "--smiles",
        "CCO",
        "--output",
        "routes.json",
        "--policy",
        "uspto",
        "ringbreaker",
        "--filter",
        "quick",
        "--stocks",
        "zinc",
        "internal",
        "--cluster",
        "--nproc",
        "4",
        "--checkpoint",
        str(checkpoint),
        "--log_to_file",
        "--post_processing",
        "my.post",
        "--pre_processing",
        "my.pre",
    ]


@pytest.mark.parametrize("nproc", [0, -1])
def test_search_spec_rejects_non_positive_worker_counts(nproc):
    with pytest.raises(ValueError, match="nproc"):
        AiZynthSearchSpec(nproc=nproc)


def test_route_deduplication_preserves_best_route_and_source_ranks():
    best = _route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")
    duplicate = _route(4, "CCOC(=O)N", "amide", "NC=O", "CCO")

    unique = deduplicate_routes([best, duplicate])

    assert len(unique) == 1
    assert unique[0]["rank"] == 1
    assert unique[0]["duplicate_count"] == 2
    assert unique[0]["source_ranks"] == [1, 4]
    assert unique[0]["route_signature"] == route_signature(best)


def test_diversity_selection_prefers_a_distinct_route_before_near_duplicate():
    first = _route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")
    near_duplicate = _route(2, "CCOC(=O)N", "amide", "CCO", "NC=O")
    distinct = _route(3, "CCOC(=O)N", "carbamate", "CCN", "O=C=O")

    selected = select_diverse_routes(
        [first, near_duplicate, distinct], max_routes=2, similarity_threshold=0.8
    )

    assert [route["source_rank"] for route in selected] == [1, 3]
    assert all(route["diversity_relaxed"] is False for route in selected)


def test_prepare_routes_normalizes_deduplicates_and_limits_output():
    payload = {
        "routes": [
            {
                "score": 0.9,
                "solved": True,
                "tree": _route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")["tree"],
            },
            {
                "score": 0.8,
                "solved": True,
                "tree": _route(2, "CCOC(=O)N", "amide", "NC=O", "CCO")["tree"],
            },
        ]
    }

    prepared = prepare_routes(payload, max_routes=10)

    assert len(prepared) == 1
    assert prepared[0]["duplicate_count"] == 2
    assert prepared[0]["rank"] == 1


def test_structural_audit_reports_missing_precursors_without_external_services():
    route = {
        "rank": 7,
        "tree": {
            "type": "mol",
            "smiles": "CCO",
            "children": [{"type": "reaction", "children": []}],
        },
    }

    audit = audit_routes([route])

    assert audit["route_count"] == 1
    assert audit["severity_counts"]["error"] == 1
    assert any(
        issue["code"] == "reaction_without_precursors" for issue in audit["issues"]
    )
    assert "does not validate reaction feasibility" in audit["disclaimer"]
