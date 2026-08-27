"""Offline contracts for the six independent retrosynthesis science scenarios."""

import json
import os
import sys

import pytest

from openai4s.config import get_config

sys.path.insert(0, str(get_config().skills_dir))

from retrosynthesis_planning.atom_mapping_benchmark import (  # noqa: E402
    PREDICTION_FIELDS as ATOM_MAPPING_PREDICTION_FIELDS,
)
from retrosynthesis_planning.atom_mapping_benchmark import (  # noqa: E402
    BenchmarkProtocolError,
    evaluate_mappings,
    validate_public_reactions,
)
from retrosynthesis_planning.benchmark_common import (  # noqa: E402
    build_intermediate_artifact,
    require_exact_fields,
)
from retrosynthesis_planning.condition_benchmark import (  # noqa: E402
    PAYLOAD_FIELDS as CONDITION_PAYLOAD_FIELDS,
)
from retrosynthesis_planning.condition_benchmark import (  # noqa: E402
    SLOTS,
    evaluate_condition_predictions,
    normalize_condition_outputs,
    validate_condition_inputs,
)
from retrosynthesis_planning.forward_benchmark import (  # noqa: E402
    evaluate_forward_predictions,
    normalize_forward_outputs,
    validate_forward_inputs,
)
from retrosynthesis_planning.multistep_benchmark import (  # noqa: E402
    evaluate_routes,
    normalize_planner_outputs,
    normalize_stock,
    validate_targets,
    verify_route_tree,
)
from retrosynthesis_planning.reaction_model_backends import (  # noqa: E402
    ReactionModelBackend,
)
from retrosynthesis_planning.reaction_model_deployment import (  # noqa: E402
    ENVIRONMENTS,
    PARROT_HF_ARTIFACTS,
    ReactionModelDeploymentError,
    artifact_commands,
    model_manifest,
    snapshot_artifacts,
    verify_artifact_snapshot,
)
from retrosynthesis_planning.reaction_model_worker import (  # noqa: E402
    _parse_parrot_rank,
)
from retrosynthesis_planning.reproducibility_bundle import (  # noqa: E402
    ReproducibilityBundleError,
    build_reproducibility_bundle,
)
from retrosynthesis_planning.route_review import route_similarity  # noqa: E402
from retrosynthesis_planning.scenario_benchmark_cli import (  # noqa: E402
    main as scenario_cli_main,
)
from retrosynthesis_planning.yield_benchmark import (  # noqa: E402
    SPLITS,
    evaluate_yield_predictions,
    normalize_yield_outputs,
    validate_yield_inputs,
)


def _identity(value):
    if not isinstance(value, str) or not value:
        raise ValueError("invalid")
    return value


def _connectivity(value):
    return _identity(value).replace("@", "")


def test_multistep_requires_every_and_leaf_to_be_in_stock():
    targets = validate_targets(
        [{"target_id": "t1", "target_smiles": "T"}], canonicalizer=_identity
    )
    stock = normalize_stock(["A"], canonicalizer=_identity)
    route = {
        "tree": {
            "type": "molecule",
            "smiles": "T",
            "children": [
                {
                    "type": "reaction",
                    "children": [
                        {"type": "molecule", "smiles": "A", "children": []},
                        {"type": "molecule", "smiles": "B", "children": []},
                    ],
                }
            ],
        },
        "solved": True,
    }
    normalized = normalize_planner_outputs(
        targets,
        [
            {
                "target_id": "t1",
                "routes": [route],
                "termination_reason": "exhausted",
                "search_stats": {"expansions": 2},
            }
        ],
        stock=stock,
        max_routes=3,
        budget={"expansions": 4},
        canonicalizer=_identity,
    )
    assert normalized[0]["routes"][0]["verification"]["verified_solved"] is False
    metrics = evaluate_routes(normalized, [{"target_id": "t1", "routes": [route]}])
    assert metrics["solved_target_rate"] == 0.0


def test_atom_mapping_public_input_rejects_hidden_map_numbers():
    with pytest.raises(BenchmarkProtocolError, match="must not contain atom maps"):
        validate_public_reactions(
            [{"reaction_id": "r1", "reaction_smiles": "[CH3:1]O>>CO"}]
        )


def test_atom_mapping_evaluator_accepts_symmetry_equivalent_correspondence():
    prediction = {
        "reaction_id": "r1",
        "correspondence": ["r0:a0=>p0:a1"],
        "bond_changes": ["formed:x"],
        "valid": True,
    }
    reference = {
        "reaction_id": "r1",
        "equivalent_correspondences": [
            [{"reactant_atom": "r0:a0", "product_atom": "p0:a1"}]
        ],
        "bond_changes": ["formed:x"],
        "ambiguous": False,
    }
    metrics = evaluate_mappings([prediction], [reference])
    assert metrics["whole_reaction_exact_mapping_rate"] == 1.0
    assert metrics["mean_bond_change_f1"] == 1.0


def test_forward_prediction_reports_stereochemistry_only_error():
    inputs = validate_forward_inputs(
        [{"reaction_id": "r1", "reactants": "A", "reagents": ""}],
        canonicalizer=_identity,
    )
    predictions = normalize_forward_outputs(
        inputs,
        [
            {
                "reaction_id": "r1",
                "predictions": [{"rank": 1, "product_smiles": "C@", "score": 1.0}],
                "error": None,
            }
        ],
        top_k=3,
        isomeric_canonicalizer=_identity,
        connectivity_canonicalizer=_connectivity,
    )
    metrics = evaluate_forward_predictions(
        predictions,
        [{"reaction_id": "r1", "products": ["C@@"]}],
        top_k=3,
        isomeric_canonicalizer=_identity,
        connectivity_canonicalizer=_connectivity,
    )
    assert metrics["isomeric_top_k_accuracy"]["1"] == 0.0
    assert metrics["connectivity_top_k_accuracy"]["1"] == 1.0
    assert metrics["stereochemistry_only_error_rate"] == 1.0


def test_condition_protocol_scores_complete_tuple_not_marginal_cartesian_product():
    inputs = validate_condition_inputs(
        [{"reaction_id": "r1", "reactants": "A", "product": "P"}],
        canonicalizer=_identity,
    )
    vocabulary = {slot: [f"{slot}_a", f"{slot}_b"] for slot in SLOTS}
    wrong = {slot: f"{slot}_a" for slot in SLOTS}
    right = {slot: f"{slot}_b" for slot in SLOTS}
    predictions = normalize_condition_outputs(
        inputs,
        [
            {
                "reaction_id": "r1",
                "predictions": [
                    {"rank": 1, "score": 0.9, "conditions": wrong},
                    {"rank": 2, "score": 0.8, "conditions": right},
                ],
                "error": None,
            }
        ],
        vocabulary=vocabulary,
        top_k=3,
    )
    metrics = evaluate_condition_predictions(
        predictions, [{"reaction_id": "r1", "condition_sets": [right]}], top_k=3
    )
    assert metrics["exact_tuple_top_k_accuracy"]["1"] == 0.0
    assert metrics["exact_tuple_top_k_accuracy"]["3"] == 1.0


def test_yield_protocol_preserves_raw_predictions_and_reports_worst_group():
    rows = [
        {
            "reaction_id": f"r{index}",
            "split": split,
            "reactants": f"A{index}",
            "reagents": "",
            "product": f"P{index}",
        }
        for index, split in enumerate(SPLITS)
    ]
    inputs = validate_yield_inputs(rows, canonicalizer=_identity)
    raw = [
        {
            "reaction_id": row["reaction_id"],
            "predicted_yield_percent": 120.0 if row["split"] == "mff_test4" else 50.0,
            "interval_lower": None,
            "interval_upper": None,
            "domain_status": (
                "out_of_domain" if row["split"].startswith("mff") else "matched"
            ),
        }
        for row in inputs
    ]
    predictions = normalize_yield_outputs(inputs, raw)
    refs = [
        {"reaction_id": row["reaction_id"], "yield_percent": 50.0} for row in inputs
    ]
    metrics = evaluate_yield_predictions(predictions, refs)
    assert predictions[-1]["predicted_yield_percent"] == 120.0
    assert metrics["by_split"]["mff_test4"]["mae"] == 70.0
    assert metrics["worst_group_mae"] == 70.0


def test_unified_cli_evaluates_hashed_condition_artifact(tmp_path):
    conditions = {slot: f"{slot}_a" for slot in SLOTS}
    artifact = build_intermediate_artifact(
        "reaction_condition_tuple_closed_vocab_v1",
        [
            {
                "reaction_id": "r1",
                "predictions": [
                    {
                        "rank": 1,
                        "condition_signature": list(conditions.values()),
                        "oov_slots": [],
                        "duplicate_of_rank": None,
                        "valid": True,
                    }
                ],
            }
        ],
        metadata={"top_k": 1},
    )
    references = [{"reaction_id": "r1", "condition_sets": [conditions]}]
    artifact_path = tmp_path / "intermediate.json"
    reference_path = tmp_path / "private.json"
    output_path = tmp_path / "metrics.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    reference_path.write_text(json.dumps(references), encoding="utf-8")
    assert (
        scenario_cli_main(
            [
                "evaluate",
                "conditions",
                "--predictions",
                str(artifact_path),
                "--references",
                str(reference_path),
                "--output",
                str(output_path),
                "--top-k",
                "1",
            ]
        )
        == 0
    )
    assert (
        json.loads(output_path.read_text(encoding="utf-8"))[
            "exact_tuple_top_k_accuracy"
        ]["1"]
        == 1.0
    )


def test_reaction_model_environment_plan_stays_under_explicit_external_root(tmp_path):
    root = tmp_path / "external-models"
    plan = ENVIRONMENTS["rxnmapper-0.4.3"].to_dict(root)
    assert plan["environment_prefix"].startswith(str(root.resolve()))
    assert plan["cache_dir"].startswith(str(root.resolve()))
    assert "rxnmapper[rdkit]==0.4.3" in plan["packages"]
    assert plan["source_revision"] == "640d9ddd304d28eb338482f4e9c2dd6b1a25de7c"


def test_parrot_hf_plan_uses_the_reviewed_mit_snapshot(tmp_path):
    root = tmp_path / "external-models"
    plan = ENVIRONMENTS["parrot-hf-b9ef6049"].to_dict(root)
    assert plan["checkpoint_license"] == "MIT"
    assert plan["requires_terms_review"] is False
    assert plan["source_revision"] == PARROT_HF_ARTIFACTS["revision"]
    command = artifact_commands("parrot-hf-b9ef6049", root)[0]
    assert "USPTO_condition.mar" in command
    assert "condition_predictor_metadata.zip" in command
    legacy = artifact_commands("parrot-0fb2325", root)
    assert not any(
        "download_data.py" in argument for item in legacy for argument in item
    )


def test_external_artifact_snapshot_detects_changed_checkpoint(tmp_path):
    checkpoint = tmp_path / "models" / "weights.bin"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"reviewed")
    manifest = snapshot_artifacts([checkpoint], base=tmp_path)
    assert verify_artifact_snapshot(manifest, base=tmp_path)["ok"] is True
    checkpoint.write_bytes(b"changed")
    result = verify_artifact_snapshot(manifest, base=tmp_path)
    assert result["ok"] is False
    assert result["failures"] == ["changed:models/weights.bin"]


def test_reaction_model_worker_capabilities_round_trip_without_heavy_imports():
    manifest = model_manifest(
        "rxnmapper-0.4.3",
        checkpoint_id="rxnmapper-wheel-and-embedded-model",
        checkpoint_sha256="a" * 64,
    )
    backend = ReactionModelBackend("rxnmapper", manifest=manifest)
    response = backend.capabilities()
    assert response["ok"] is True
    assert response["result"]["operations"] == ["capabilities", "map_reactions"]
    assert response["provenance_status"] == "complete"


def test_aizynth_worker_converts_search_to_scenario_payload(tmp_path):
    package = tmp_path / "aizynthfinder"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "aizynthfinder.py").write_text(
        """
class Collection:
    def __init__(self, items):
        self.items = items
        self.selected = None
    def select(self, value):
        self.selected = value
    def select_all(self):
        self.selected = list(self.items)

class Routes:
    def compute_scores(self, *objects):
        return None
    def dict_with_extra(self, include_metadata=False, include_scores=False):
        return [{
            "type": "mol", "smiles": "CCO",
            "metadata": {"is_solved": True},
            "scores": {"state score": 0.8},
            "children": [{
                "type": "reaction", "children": [
                    {"type": "mol", "smiles": "CC", "children": []},
                    {"type": "mol", "smiles": "O", "children": []},
                ],
            }],
        }]

class Scorers:
    def objects(self):
        return []

class AiZynthFinder:
    def __init__(self, configfile):
        self.stock = Collection(["zinc"])
        self.expansion_policy = Collection(["uspto"])
        self.filter_policy = Collection(["filter"])
        self.scorers = Scorers()
        self.routes = Routes()
        self.target_smiles = None
    def prepare_tree(self):
        return None
    def tree_search(self, show_progress=False):
        if self.target_smiles == "FAIL":
            raise ValueError("target search failed")
        return 0.1
    def build_routes(self):
        return None
    def extract_statistics(self):
        return {"is_solved": True, "number_of_nodes": 3, "iterations": 2}
""".lstrip(),
        encoding="utf-8",
    )
    config = tmp_path / "config.yml"
    config.write_text("stock: stub\n", encoding="utf-8")
    manifest = model_manifest(
        "aizynthfinder-4.4.1",
        checkpoint_id="stub-public-assets",
        checkpoint_sha256="a" * 64,
    )
    backend = ReactionModelBackend(
        "aizynthfinder",
        manifest=manifest,
        env={"PYTHONPATH": os.pathsep.join((str(tmp_path), *sys.path))},
    )
    response = backend.plan_routes(
        [
            {"target_id": "target-bad", "target_smiles": "FAIL"},
            {"target_id": "target-1", "target_smiles": "CCO"},
        ],
        config_path=str(config),
        stocks=["zinc"],
        max_routes=3,
    )
    failed, record = response["result"]["records"]
    assert failed["target_id"] == "target-bad"
    assert failed["termination_reason"] == "backend_error"
    assert failed["routes"] == []
    assert failed["search_stats"]["error"] == "ValueError: target search failed"
    assert record["target_id"] == "target-1"
    assert record["termination_reason"] == "solved"
    assert record["search_stats"]["number_of_nodes"] == 3
    assert record["routes"][0]["solved"] is True
    assert record["routes"][0]["tree"]["scores"]["state score"] == 0.8


def test_parrot_worker_parses_all_ranked_condition_beams(tmp_path):
    repository = tmp_path / "Parrot"
    repository.mkdir()
    (repository / "inference.py").write_text(
        """
import argparse
import csv

parser = argparse.ArgumentParser()
parser.add_argument("--config_path")
parser.add_argument("--input_path")
parser.add_argument("--output_path")
parser.add_argument("--num_workers")
parser.add_argument("--inference_batch_size")
parser.add_argument("--gpu")
args = parser.parse_args()
reaction = open(args.input_path, encoding="utf-8").read().strip()
with open(args.output_path, "w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=[
        "rxn_smiles", "top-k", "catalyst1", "solvent1", "solvent2",
        "reagent1", "reagent2", "scores",
    ])
    writer.writeheader()
    writer.writerow({"rxn_smiles": reaction, "top-k": "top-1", "catalyst1": "Pd", "solvent1": "THF", "reagent1": "base", "scores": "0.9"})
    writer.writerow({"rxn_smiles": "", "top-k": "top-2", "catalyst1": "Ni", "solvent1": "DMF", "reagent1": "base2", "scores": "0.7"})
""".lstrip(),
        encoding="utf-8",
    )
    config = repository / "config.yml"
    config.write_text("model: stub\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = model_manifest(
        "parrot-0fb2325",
        checkpoint_id="stub-parrot-checkpoint",
        checkpoint_sha256="b" * 64,
        checkpoint_license="MIT",
    )
    backend = ReactionModelBackend(
        "parrot", manifest=manifest, repository_dir=repository
    )
    response = backend.recommend_conditions(
        [{"reaction_id": "reaction-1", "reaction_smiles": "CCO>>CC=O"}],
        config_path=str(config),
        workspace_dir=str(workspace),
    )
    predictions = response["result"]["records"][0]["predictions"]
    assert [item["rank"] for item in predictions] == [1, 2]
    assert predictions[0]["conditions"]["catalyst1"] == "Pd"
    assert predictions[1]["conditions"]["solvent1"] == "DMF"


def test_parrot_rank_parser_supports_the_python38_environment():
    assert _parse_parrot_rank("top-2", 9) == 2
    assert _parse_parrot_rank("3", 9) == 3
    assert _parse_parrot_rank("not-a-rank", 9) == 9


def test_reproducibility_bundle_is_deterministic_and_rejects_local_paths(tmp_path):
    source = tmp_path / "public"
    source.mkdir()
    (source / "summary.json").write_text('{"status":"pass"}\n', encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_result = build_reproducibility_bundle(source, first)
    second_result = build_reproducibility_bundle(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]

    (source / "leak.md").write_text(
        "machine path: /home/operator/model", encoding="utf-8"
    )
    with pytest.raises(ReproducibilityBundleError, match="local or forbidden"):
        build_reproducibility_bundle(source, tmp_path / "rejected.zip")


def _identity_smiles(value):
    return value


def test_stock_closure_alone_never_certifies_a_solved_route():
    """A route with no reaction node is not a route, however well it closes."""

    verification = verify_route_tree(
        {
            "tree": {
                "type": "mol",
                "smiles": "TARGET",
                "children": [{"type": "mol", "smiles": "BUY"}],
            }
        },
        target_smiles="TARGET",
        stock=frozenset({"BUY"}),
        canonicalizer=_identity_smiles,
    )
    assert verification["verified_solved"] is False
    assert verification["valid"] is False
    assert verification["reaction_count"] == 0


def test_route_verification_does_not_depend_on_child_order():
    """``visit`` has side effects, so the walk must not short-circuit."""

    def route(order):
        return {
            "tree": {
                "type": "mol",
                "smiles": "T",
                "children": [
                    {
                        "type": "reaction",
                        "children": [{"type": "mol", "smiles": name} for name in order],
                    }
                ],
            }
        }

    forward = verify_route_tree(
        route(["X", "Y", "Z"]),
        target_smiles="T",
        stock=frozenset(),
        canonicalizer=_identity_smiles,
    )
    reverse = verify_route_tree(
        route(["Z", "Y", "X"]),
        target_smiles="T",
        stock=frozenset(),
        canonicalizer=_identity_smiles,
    )
    assert forward["unresolved_leaves"] == ["X", "Y", "Z"]
    assert forward == reverse


def test_node_kind_tolerates_whitespace_like_route_review():
    """``reaction `` must stay an AND-node, not decay into a molecule OR-node."""

    verification = verify_route_tree(
        {
            "tree": {
                "type": "mol",
                "smiles": "T",
                "children": [
                    {
                        "type": "reaction ",
                        "children": [
                            {"type": "mol", "smiles": "A"},
                            {"type": "mol", "smiles": "B"},
                        ],
                    }
                ],
            }
        },
        target_smiles="T",
        stock=frozenset({"A"}),
        canonicalizer=_identity_smiles,
    )
    assert verification["verified_solved"] is False
    assert verification["unresolved_leaves"] == ["B"]
    assert verification["reaction_count"] == 1


def test_route_similarity_treats_an_empty_feature_set_as_no_evidence():
    assert route_similarity({"rank": 1}, {"tree": {}}) == 0.0


def test_require_exact_fields_rejects_a_non_mapping_row():
    with pytest.raises(BenchmarkProtocolError):
        require_exact_fields(["reaction_id"], {"reaction_id"}, field="row 1")


def test_every_evaluator_refuses_empty_records():
    """The normalize side rejects empty input; the evaluate side must too."""

    for call in (
        lambda: evaluate_routes([], []),
        lambda: evaluate_mappings([], []),
        lambda: evaluate_forward_predictions([], [], top_k=1),
        lambda: evaluate_condition_predictions([], [], top_k=1),
        lambda: evaluate_yield_predictions([], []),
    ):
        with pytest.raises(BenchmarkProtocolError):
            call()


def test_worker_payload_schemas_accept_their_own_worker_records():
    """The record shapes reaction_model_worker emits must normalize."""

    assert "error" in ATOM_MAPPING_PREDICTION_FIELDS
    assert {"raw_output", "error"} <= CONDITION_PAYLOAD_FIELDS


def test_quarantined_model_is_refused_without_an_explicit_override():
    manifest = model_manifest(
        "reactiont5v2",
        checkpoint_id="sagawa/ReactionT5v2-yield@f0658bfd",
        checkpoint_sha256="c" * 64,
    )
    with pytest.raises(ValueError, match="quarantined"):
        ReactionModelBackend("reactiont5_yield", manifest=manifest)


def test_backend_refuses_a_checkpoint_from_the_sibling_model():
    manifest = model_manifest(
        "reactiont5v2",
        checkpoint_id="sagawa/ReactionT5v2-yield@f0658bfd",
        checkpoint_sha256="c" * 64,
    )
    with pytest.raises(ValueError, match="ReactionT5v2-forward"):
        ReactionModelBackend("reactiont5_forward", manifest=manifest)


def test_artifact_verification_detects_a_file_added_after_the_snapshot(tmp_path):
    base = tmp_path / "base"
    (base / "models").mkdir(parents=True)
    (base / "models" / "weights.bin").write_text("w", encoding="utf-8")
    manifest = snapshot_artifacts([base / "models"], base=base)
    assert verify_artifact_snapshot(manifest, base=base)["ok"] is True
    (base / "models" / "model.safetensors").write_text("added", encoding="utf-8")
    result = verify_artifact_snapshot(manifest, base=base)
    assert result["ok"] is False
    assert "unexpected:models/model.safetensors" in result["failures"]


def test_snapshot_refuses_a_symlink_that_escapes_the_base(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "weights.bin").write_text("outside", encoding="utf-8")
    base = tmp_path / "base"
    (base / "models").mkdir(parents=True)
    os.symlink(outside / "weights.bin", base / "models" / "weights.bin")
    with pytest.raises(ReactionModelDeploymentError, match="symlink"):
        snapshot_artifacts([base / "models"], base=base)
