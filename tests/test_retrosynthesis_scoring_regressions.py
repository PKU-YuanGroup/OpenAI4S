"""Regression tests for retrosynthesis execution scoring and route rendering."""

import importlib
import json
import math
import sys

import pytest

from openai4s.config import get_config


@pytest.fixture(scope="module")
def kernel():
    sys.path.insert(0, str(get_config().skills_dir))
    return importlib.import_module("retrosynthesis_planning.kernel")


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
