"""Offline tests for the protein-mutation-enhancement workflow skill."""
import importlib
import sys

import pytest

from openai4s.config import get_config


@pytest.fixture(scope="module")
def pm():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("protein-mutation-enhancement.kernel")


def test_enumerate_single_and_double_mutants_is_deterministic(pm):
    library = pm.enumerate_mutants(
        "ACD",
        positions=[1, 2],
        substitutions={1: ["A", "V"], 2: ["C", "D"]},
        max_order=2,
    )

    assert [cand["id"] for cand in library] == ["A1V", "C2D", "A1V+C2D"]
    assert [cand["sequence"] for cand in library] == ["VCD", "ADD", "VDD"]
    assert library[-1]["order"] == 2


def test_seeded_library_expands_without_repeating_positions(pm):
    library = pm.enumerate_mutants(
        "ACDE",
        positions=[1, 2, 3],
        substitutions={1: "V", 2: "D", 3: "E"},
        max_order=2,
        seeds=["A1V"],
    )

    assert [cand["id"] for cand in library] == ["A1V", "A1V+C2D", "A1V+D3E"]


def test_apply_mutations_rejects_wild_type_mismatch(pm):
    with pytest.raises(ValueError, match="wild-type mismatch"):
        pm.apply_mutations("ACD", "G1V")


def test_rank_mutants_merges_scores_and_applies_thresholds(pm):
    candidates = pm.enumerate_mutants(
        "ACD",
        positions=[1, 2],
        substitutions={1: "V", 2: "D"},
        max_order=2,
    )
    esm_scores = {
        "A1V": {"esm_delta": 0.4},
        "C2D": {"esm_delta": 1.2},
        "A1V+C2D": {"esm_delta": 1.9},
    }
    structure_scores = {
        "A1V": {"plddt": 88.0, "rmsd_to_wt": 0.7},
        "C2D": {"plddt": 65.0, "rmsd_to_wt": 1.4},
        "A1V+C2D": {"plddt": 83.0, "rmsd_to_wt": 0.9},
    }

    result = pm.run_selection_round(
        candidates,
        score_tables=[esm_scores, structure_scores],
        weights={
            "esm_delta": 0.45,
            "plddt": 0.25,
            "rmsd_to_wt": 0.15,
            "property_score": 0.15,
        },
        directions={"rmsd_to_wt": "low"},
        acceptance_thresholds={
            "composite_score": 0.70,
            "esm_delta": 1.0,
            "plddt": 75.0,
            "rmsd_to_wt": 1.0,
        },
        top_k=2,
    )

    assert result["should_continue"] is False
    assert result["accepted"][0]["id"] == "A1V+C2D"
    assert result["ranked"][0]["id"] == "A1V+C2D"
    assert "property_score" in result["ranked"][0]
    assert result["ranked"][0]["normalized_scores"]["rmsd_to_wt"] > 0


def test_selection_round_continues_when_thresholds_fail(pm):
    candidates = pm.enumerate_mutants(
        "ACD",
        positions=[1, 2],
        substitutions={1: "V", 2: "D"},
        max_order=1,
    )
    scores = {
        "A1V": {"esm_delta": 0.2, "plddt": 70.0},
        "C2D": {"esm_delta": 0.4, "plddt": 71.0},
    }

    result = pm.run_selection_round(
        candidates,
        score_tables=[scores],
        acceptance_thresholds={"esm_delta": 1.0, "plddt": 80.0},
        top_k=1,
    )

    assert result["should_continue"] is True
    assert result["accepted"] == []
    assert len(result["next_round_seeds"]) == 1


def test_suggest_next_positions_uses_rank_order(pm):
    ranked = [
        {"id": "A1V+C2D", "mutations": pm.normalize_mutations("A1V+C2D")},
        {"id": "C2D", "mutations": pm.normalize_mutations("C2D")},
        {"id": "D3E", "mutations": pm.normalize_mutations("D3E")},
    ]

    assert pm.suggest_next_positions(ranked, max_positions=2) == [2, 1]


def test_selection_round_seeds_feed_back_into_enumerate(pm):
    # Regression: the documented Loop Pattern feeds next_round_seeds straight
    # back into enumerate_mutants. This previously raised TypeError because the
    # seeds were ranked dicts without a top-level position.
    seq = "ACDEFGHIKL"
    library = pm.enumerate_mutants(seq, positions=[2, 5], max_order=1)
    scores = {item["id"]: {"esm_delta": 0.0, "plddt": 50.0} for item in library}
    result = pm.run_selection_round(
        candidates=library,
        score_tables=[scores],
        acceptance_thresholds={"esm_delta": 5.0},
    )
    seeds = result["next_round_seeds"]
    assert seeds
    assert all(isinstance(s, str) for s in seeds)
    next_library = pm.enumerate_mutants(
        seq, positions=[2, 5, 7], max_order=2, seeds=seeds
    )
    assert next_library
    next_ids = {item["id"] for item in next_library}
    assert set(seeds) <= next_ids


def test_rank_mutants_rejects_explicit_empty_weights(pm):
    candidates = [
        {
            "id": "A1V",
            "sequence": "VCDEF",
            "mutations": [{"from": "A", "position": 1, "to": "V"}],
            "order": 1,
        }
    ]
    scores = {"A1V": {"esm_delta": 1.0}}
    with pytest.raises(ValueError, match="weights must not be empty"):
        pm.rank_mutants(candidates, score_tables=[scores], weights={})


def test_out_of_range_tuple_mutation_raises_clear_error(pm):
    with pytest.raises(ValueError, match="exceeds sequence length"):
        pm.normalize_mutations([(99, "V")], "ACDEF")


def test_variant_dict_without_position_raises_clear_error(pm):
    variant = {"id": "A1V", "mutations": [{"from": "A", "position": 1, "to": "V"}]}
    with pytest.raises(ValueError, match="missing a position"):
        pm.normalize_mutations(variant)
