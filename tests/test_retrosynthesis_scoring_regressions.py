"""Regression tests for retrosynthesis execution scoring and route rendering."""

import importlib
import json
import math
import os
import sys
import textwrap

import pytest

from openai4s.config import get_config


@pytest.fixture(scope="module")
def kernel():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("retrosynthesis_planning.kernel")


@pytest.fixture(scope="module")
def workflow():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("retrosynthesis_planning.workflow")


@pytest.fixture(scope="module")
def worker():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("retrosynthesis_planning.syntheseus_worker")


@pytest.fixture(scope="module")
def backends():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("retrosynthesis_planning.external_backends")


def _direct_purchase_route(*, rank=1, score=1.0, stock=True):
    return {
        "rank": rank,
        "score": score,
        "solved": True,
        "steps": 0,
        "starting_materials": ["BUY-ME"],
        "tree": {
            "type": "mol",
            "smiles": "BUY-ME",
            "in_stock": stock,
            "children": [],
        },
    }


def _one_step_route(*, rank=1, score=1.0, solved=True, leaf_stock=True):
    return {
        "rank": rank,
        "score": score,
        "solved": solved,
        "steps": 1,
        "starting_materials": [f"LEAF-{rank}"],
        "tree": {
            "type": "mol",
            "smiles": f"TARGET-{rank}",
            "children": [
                {
                    "type": "reaction",
                    "template": f"template-{rank}",
                    "children": [
                        {
                            "type": "mol",
                            "smiles": f"LEAF-{rank}",
                            "in_stock": leaf_stock,
                            "children": [],
                        }
                    ],
                }
            ],
        },
    }


def _workflow_route(rank, product, template, *precursors):
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


def test_render_rerank_keeps_external_annotations_on_original_routes(kernel):
    originally_first = _one_step_route(rank=1, solved=False, leaf_stock=False)
    originally_second = _one_step_route(rank=2, score=0.1, solved=True)
    annotations = {
        "routes": {
            "1": {"route_strategy": "ANNOTATION-FOR-ORIGINAL-FIRST"},
            "2": {"route_strategy": "ANNOTATION-FOR-ORIGINAL-SECOND"},
        }
    }

    rendered = kernel.render_route_tree_html(
        [originally_first, originally_second],
        annotations=annotations,
        constraints={"require_solved": True},
    )

    # The originally second solved route is displayed first after reranking, and
    # must retain its own annotation rather than adopting old Route 1's record.
    assert rendered.index("ANNOTATION-FOR-ORIGINAL-SECOND") < rendered.index(
        "ANNOTATION-FOR-ORIGINAL-FIRST"
    )


def test_render_rerank_uses_new_ranks_for_annotations_generated_after_rerank(kernel):
    originally_first = _one_step_route(rank=1, solved=False, leaf_stock=False)
    originally_second = _one_step_route(rank=2, score=0.1, solved=True)

    def fake_llm(request):
        prompt = request["prompt"] if isinstance(request, dict) else request
        payload = json.loads(prompt.split("Route data:\n", 1)[1])
        return json.dumps(
            {
                "routes": {
                    str(route["rank"]): {
                        "route_strategy": "GENERATED-FOR-"
                        + route["starting_materials"][0]
                    }
                    for route in payload["routes"]
                }
            }
        )

    rendered = kernel.render_route_tree_html(
        [originally_first, originally_second],
        constraints={"require_solved": True},
        llm=fake_llm,
    )

    assert rendered.index("GENERATED-FOR-LEAF-2") < rendered.index(
        "GENERATED-FOR-LEAF-1"
    )


def test_solved_zero_reaction_route_is_not_penalized_for_inapplicable_evidence(
    kernel,
):
    ranked = kernel.rank_routes(
        [_one_step_route(rank=1), _direct_purchase_route(rank=2)],
        decision_weights={},
    )

    assert ranked[0]["steps"] == 0
    direct = ranked[0]
    assert direct["decision_breakdown"]["step_efficiency"]["value"] == 100
    evidence = direct["decision_breakdown"]["evidence_coverage"]
    assert evidence["value"] == 100
    assert evidence["applicable"] is False
    assert direct["decision_score"] == 100
    assert ranked[1]["decision_score"] < direct["decision_score"]
    assert "Not applicable (no reaction steps)" in kernel.render_route_tree_html(
        ranked, decision_weights={}
    )


def test_backend_score_tolerates_normalized_float_overshoot(kernel):
    assert kernel._backend_score_percent(1.0) == 100
    assert kernel._backend_score_percent(1.0 + 1e-10) == 100
    assert kernel._backend_score_percent(float("nan")) == 0


def test_constraints_parse_explicit_types_and_reject_invalid_hard_constraints(
    kernel,
):
    parsed = kernel.normalize_route_constraints(
        {
            "max_steps": "3",
            "max_precursors": 2.0,
            "minimum_evidence_coverage": "40.5",
            "require_solved": "false",
            "require_all_leaves_in_stock": "TRUE",
        }
    )
    assert parsed == {
        "max_steps": 3,
        "max_precursors": 2,
        "minimum_evidence_coverage": 40.5,
        "require_solved": False,
        "require_all_leaves_in_stock": True,
    }

    with pytest.raises(ValueError, match="max_steps"):
        kernel.normalize_route_constraints({"max_steps": "several"})
    with pytest.raises(ValueError, match="require_solved"):
        kernel.normalize_route_constraints({"require_solved": "sometimes"})
    with pytest.raises(ValueError, match="require_solved"):
        kernel.normalize_route_constraints({"require_solved": 10**10000})
    with pytest.raises(ValueError, match="unsupported route constraint"):
        kernel.normalize_route_constraints({"max_step": 3})


def test_string_false_stock_flags_never_count_as_available(kernel):
    route = _direct_purchase_route(stock="false")
    route["solved"] = False

    assert kernel._node_in_stock(route["tree"]) is False
    assert kernel._node_in_stock({"stock": 10**10000}) is False
    assert kernel._precursor_availability_percent(route) == 0
    assert kernel._all_leaves_in_stock(route["tree"]) is False
    scored = kernel.rank_routes(
        [route], constraints={"require_all_leaves_in_stock": True}
    )[0]
    assert "not all terminal precursors are in stock" in scored["constraint_violations"]


@pytest.mark.parametrize(
    "invalid",
    [
        math.nan,
        math.inf,
        -math.inf,
        True,
        pytest.param(10**10000, id="overflowing-int"),
    ],
)
def test_decision_weights_reject_invalid_numeric_values(kernel, invalid):
    with pytest.raises(ValueError, match="finite"):
        kernel._normalize_decision_weights({"backend_score": invalid})


def test_normalized_decision_weights_sum_to_exactly_one_hundred(kernel):
    weights = kernel._normalize_decision_weights(
        {
            "backend_score": 1,
            "step_efficiency": 1,
            "precursor_availability": 1,
            "evidence_coverage": 1,
            "constraint_fit": 3,
        }
    )
    assert sum(weights.values()) == 100.0
    assert all(value >= 0 and math.isfinite(value) for value in weights.values())


def test_execution_ranking_totally_orders_mixed_step_values(kernel):
    step_values = [
        None,
        "2",
        [],
        {"bad": 1},
        math.nan,
        math.inf,
        -math.inf,
        10**10000,
        1,
    ]
    routes = []
    for rank, steps in enumerate(step_values, start=1):
        route = _direct_purchase_route(rank=rank, stock=False)
        route.update(
            {
                "id": f"route-{rank}",
                "solved": False,
                "steps": steps,
                "score": 0.5,
            }
        )
        routes.append(route)

    ranked = kernel.rank_routes(
        routes,
        decision_weights={
            "backend_score": 1,
            "step_efficiency": 0,
            "precursor_availability": 0,
            "evidence_coverage": 0,
            "constraint_fit": 0,
        },
    )

    assert [route["id"] for route in ranked[:2]] == ["route-9", "route-2"]
    assert len(ranked) == len(routes)

    constrained = kernel.rank_routes(routes, constraints={"max_steps": 3})
    assert len(constrained) == len(routes)


def test_search_spec_builds_documented_cli_options_in_stable_order(workflow, tmp_path):
    checkpoint = tmp_path / "checkpoint.json.gz"
    command = workflow.build_aizynth_search_command(
        "CCO",
        "config.yml",
        output_path="routes.json",
        conda_env="retro",
        search=workflow.AiZynthSearchSpec(
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
def test_search_spec_rejects_non_positive_worker_counts(workflow, nproc):
    with pytest.raises(ValueError, match="nproc"):
        workflow.AiZynthSearchSpec(nproc=nproc)


def test_route_deduplication_preserves_best_route_and_source_ranks(workflow):
    best = _workflow_route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")
    duplicate = _workflow_route(4, "CCOC(=O)N", "amide", "NC=O", "CCO")

    unique = workflow.deduplicate_routes([best, duplicate])

    assert len(unique) == 1
    assert unique[0]["rank"] == 1
    assert unique[0]["duplicate_count"] == 2
    assert unique[0]["source_ranks"] == [1, 4]
    assert unique[0]["route_signature"] == workflow.route_signature(best)


def test_diversity_selection_prefers_distinct_route_before_near_duplicate(workflow):
    first = _workflow_route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")
    near_duplicate = _workflow_route(2, "CCOC(=O)N", "amide", "CCO", "NC=O")
    distinct = _workflow_route(3, "CCOC(=O)N", "carbamate", "CCN", "O=C=O")

    selected = workflow.select_diverse_routes(
        [first, near_duplicate, distinct],
        max_routes=2,
        similarity_threshold=0.8,
    )

    assert [route["source_rank"] for route in selected] == [1, 3]
    assert all(route["diversity_relaxed"] is False for route in selected)


def test_prepare_routes_normalizes_deduplicates_and_limits_output(workflow):
    payload = {
        "routes": [
            {
                "score": 0.9,
                "solved": True,
                "tree": _workflow_route(1, "CCOC(=O)N", "amide", "CCO", "NC=O")["tree"],
            },
            {
                "score": 0.8,
                "solved": True,
                "tree": _workflow_route(2, "CCOC(=O)N", "amide", "NC=O", "CCO")["tree"],
            },
        ]
    }

    prepared = workflow.prepare_routes(payload, max_routes=10)

    assert len(prepared) == 1
    assert prepared[0]["duplicate_count"] == 2
    assert prepared[0]["rank"] == 1


def test_structural_audit_reports_missing_precursors_without_external_services(
    workflow,
):
    route = {
        "rank": 7,
        "tree": {
            "type": "mol",
            "smiles": "CCO",
            "children": [{"type": "reaction", "children": []}],
        },
    }

    audit = workflow.audit_routes([route])

    assert audit["route_count"] == 1
    assert audit["severity_counts"]["error"] == 1
    assert any(
        issue["code"] == "reaction_without_precursors" for issue in audit["issues"]
    )
    assert "does not validate reaction feasibility" in audit["disclaimer"]


def test_extra_args_precede_variadic_switches_so_values_are_not_absorbed(workflow):
    command = workflow.build_aizynth_search_command(
        "CCO",
        "config.yml",
        search=workflow.AiZynthSearchSpec(
            post_processing=("my.post",),
            extra_args=("--route_distance_model", "distance.ckpt"),
        ),
    )

    # Emitted after --post_processing, "distance.ckpt" would have been parsed as
    # a second post-processing module, because --post_processing is variadic.
    assert command.index("--route_distance_model") < command.index("--post_processing")
    assert command[command.index("--post_processing") + 1 :] == ["my.post"]


@pytest.mark.parametrize(
    "extra_args,match",
    [
        (("--config", "other.yml"), "managed switch"),
        (("--smiles", "CCC"), "managed switch"),
        (("--output=/tmp/elsewhere.json",), "managed switch"),
        # argparse resolves unambiguous prefixes, so an abbreviation reaches
        # exactly the switch the exact-match check was meant to protect.
        (("--out", "/tmp/elsewhere.json"), "managed switch"),
        (("--conf", "/tmp/attacker.yml"), "managed switch"),
        (("--sm", "ATTACKER-TARGET"), "managed switch"),
        (("--check", "/tmp/attacker.json"), "managed switch"),
        (("--", "junk"), "managed switch"),
        (("distance.ckpt",), "must start with a switch"),
    ],
)
def test_extra_args_reject_managed_switches_and_leading_bare_values(
    workflow, extra_args, match
):
    with pytest.raises(ValueError, match=match):
        workflow.AiZynthSearchSpec(extra_args=extra_args)


def test_worker_metadata_redacts_paths_by_value_not_by_key_name(worker):
    safe = worker._json_safe(
        {
            "checkpoint_path": "/opt/ckpt",
            "model_dir": "/home/chemist/models",
            "cache_dir": "~/.cache/models",
            "weights_file": "C:\\models\\w.pt",
            "home": "/home/chemist",
            "resource": "/home/chemist/secret/w.pt",
            "source": "FILE:///home/chemist/secret",
            "candidates": ["/home/chemist/a.pt", "CCO"],
            "shards": {"/home/chemist/private/shard0.pt": "sha-abc"},
            "probability": 0.7,
        }
    )

    # A key that names a filesystem location is still dropped outright.
    assert "checkpoint_path" not in safe
    # Every other path is caught by value, whatever the key is called, and a
    # path used as a key is masked too rather than published verbatim.
    assert safe["model_dir"] == worker.REDACTED_PATH
    assert safe["cache_dir"] == worker.REDACTED_PATH
    assert safe["weights_file"] == worker.REDACTED_PATH
    assert safe["home"] == worker.REDACTED_PATH
    assert safe["resource"] == worker.REDACTED_PATH
    assert safe["source"] == worker.REDACTED_PATH
    assert safe["candidates"] == [worker.REDACTED_PATH, "CCO"]
    assert safe["shards"] == {worker.REDACTED_PATH: "sha-abc"}
    assert safe["probability"] == 0.7


def test_worker_metadata_keeps_chemistry_that_only_looks_path_shaped(worker):
    """Redaction must not become the bigger data loss.

    ``use_cache`` and ``n_files`` cannot hold a path, ``bond_dir`` is an RDKit
    bond direction, and ``~5 kcal/mol`` is an approximate quantity — a `~`
    branch loose enough to match it is matching the wrong thing.
    """
    safe = worker._json_safe(
        {
            "use_cache": True,
            "num_cache_hits": 12,
            "cache_size": 4096,
            "n_files": 3,
            "bond_dir": "ENDUPRIGHT",
            "file_format": "SDF",
            "barrier": "~5 kcal/mol",
            "rate": "~120 reactions/s",
            "concentration": "~0.5 mol/L",
            "reaction": "F/C=C/F>>FC=CF",
            "direction": "retro",
            "root_atom": 3,
        }
    )

    assert safe == {
        "use_cache": True,
        "num_cache_hits": 12,
        "cache_size": 4096,
        "n_files": 3,
        "bond_dir": "ENDUPRIGHT",
        "file_format": "SDF",
        "barrier": "~5 kcal/mol",
        "rate": "~120 reactions/s",
        "concentration": "~0.5 mol/L",
        "reaction": "F/C=C/F>>FC=CF",
        "direction": "retro",
        "root_atom": 3,
    }


def test_worker_error_messages_do_not_publish_the_callers_model_dir(worker):
    """The missing-checkpoint failure is the one that reliably leaks a path."""
    response = worker._error_response(
        request_id="r",
        operation="single_step",
        code="inference_failed",
        message=(
            "FileNotFoundError: [Errno 2] No such file or directory: "
            "'/home/chemist/private-lab/rc-v3/model.ckpt'"
        ),
        retryable=False,
    )

    assert "/home/chemist" not in response["error"]["message"]
    assert worker.REDACTED_PATH in response["error"]["message"]
    assert "FileNotFoundError" in response["error"]["message"]


def test_worker_response_survives_native_stdout_writes(backends, worker, tmp_path):
    """A model library writing to fd 1 must not corrupt the JSON response.

    ``contextlib.redirect_stdout`` cannot see this write, so the worker swaps
    the descriptor itself before handling a request.
    """
    script = tmp_path / "noisy_worker.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os
            import sys
            import types

            sys.path.insert(0, {str(get_config().skills_dir)!r})


            class FakeMolecule:
                def __init__(self, smiles):
                    self.smiles = smiles


            class FakePrediction:
                reactants_str = "CCO.N"
                reaction_smiles = "CCO.N>>CCON"
                metadata = {{"probability": 0.7}}


            class NoisyModel:
                def __init__(self, **kwargs):
                    os.write(1, b"CUDA init: found 1 device\\n")

                def __call__(self, molecules, num_results):
                    os.write(1, b"[inference] running 1 batch\\n")
                    print("python-level chatter")
                    return [[FakePrediction()]]


            syntheseus = types.ModuleType("syntheseus")
            syntheseus.Molecule = FakeMolecule
            retrochimera = types.ModuleType("retrochimera")
            retrochimera.RetroChimeraModel = NoisyModel
            sys.modules["syntheseus"] = syntheseus
            sys.modules["retrochimera"] = retrochimera

            from retrosynthesis_planning.syntheseus_worker import main

            raise SystemExit(main())
            """
        ),
        encoding="utf-8",
    )

    backend = backends.SubprocessRetrosynthesisBackend([sys.executable, str(script)])
    response = backend.run(
        {
            "schema_version": 1,
            "request_id": "native-noise",
            "operation": "single_step",
            "target_smiles": "CCON",
            "model": "RetroChimera",
            "model_dir": "/synthetic/checkpoint",
            "num_results": 1,
            "allow_model_download": False,
            "model_manifest": None,
        }
    )

    assert response["ok"] is True
    assert response["predictions"][0]["reactants_smiles"] == "CCO.N"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_reserved_descriptor_does_not_outlive_a_forked_model_child(backends, tmp_path):
    """A forking model must not hold the host's stdout pipe open.

    ``os.dup`` marks the copy non-inheritable, but that only applies at exec.
    A PyTorch DataLoader on the default Linux start method forks without
    exec, so without an at-fork hook the host blocks in ``communicate()``
    until the timeout even though the worker already answered and exited 0.
    """
    script = tmp_path / "forking_worker.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os
            import sys
            import time
            import types

            sys.path.insert(0, {str(get_config().skills_dir)!r})


            class FakeMolecule:
                def __init__(self, smiles):
                    self.smiles = smiles


            class FakePrediction:
                reactants_str = "CCO.N"
                reaction_smiles = "CCO.N>>CCON"
                metadata = {{"probability": 0.7}}


            class ForkingModel:
                def __init__(self, **kwargs):
                    pass

                def __call__(self, molecules, num_results):
                    if os.fork() == 0:
                        # Release fds 0/1/2, so only a leaked duplicate could
                        # still be holding the host's stdout pipe open.
                        null = os.open(os.devnull, os.O_RDWR)
                        for fd in (0, 1, 2):
                            os.dup2(null, fd)
                        time.sleep(30)
                        os._exit(0)
                    return [[FakePrediction()]]


            syntheseus = types.ModuleType("syntheseus")
            syntheseus.Molecule = FakeMolecule
            retrochimera = types.ModuleType("retrochimera")
            retrochimera.RetroChimeraModel = ForkingModel
            sys.modules["syntheseus"] = syntheseus
            sys.modules["retrochimera"] = retrochimera

            from retrosynthesis_planning.syntheseus_worker import main

            raise SystemExit(main())
            """
        ),
        encoding="utf-8",
    )

    backend = backends.SubprocessRetrosynthesisBackend(
        [sys.executable, str(script)], timeout_seconds=20
    )
    response = backend.run(
        {
            "schema_version": 1,
            "request_id": "forking-child",
            "operation": "single_step",
            "target_smiles": "CCON",
            "model": "RetroChimera",
            "model_dir": "/synthetic/checkpoint",
            "num_results": 1,
            "allow_model_download": False,
            "model_manifest": None,
        }
    )

    assert response["ok"] is True
