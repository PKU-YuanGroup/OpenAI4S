"""Budgeted multi-step route protocol for the PaRoutes-style Scenario 2."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .benchmark_common import (
    BenchmarkProtocolError,
    finite_number,
    json_copy,
    positive_int,
    require_exact_fields,
    require_text,
    sha256_json,
)
from .route_review import route_signature, route_similarity
from .single_step_benchmark import rdkit_canonicalize

Canonicalizer = Callable[[str], str]
TARGET_FIELDS = frozenset({"target_id", "target_smiles"})
PAYLOAD_FIELDS = frozenset(
    {"target_id", "routes", "termination_reason", "search_stats"}
)
REFERENCE_FIELDS = frozenset({"target_id", "routes"})


@dataclass(frozen=True, slots=True)
class PlanningTarget:
    target_id: str
    target_smiles: str


def validate_targets(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[PlanningTarget, ...]:
    targets: list[PlanningTarget] = []
    seen_ids: set[str] = set()
    seen_smiles: set[str] = set()
    for index, row in enumerate(rows, start=1):
        require_exact_fields(row, TARGET_FIELDS, field=f"target row {index}")
        target_id = require_text(row["target_id"], field="target_id")
        smiles = require_text(
            canonicalizer(require_text(row["target_smiles"], field="target_smiles")),
            field="canonical target",
        )
        if target_id in seen_ids or smiles in seen_smiles:
            raise BenchmarkProtocolError(
                "target IDs and canonical structures must be unique"
            )
        seen_ids.add(target_id)
        seen_smiles.add(smiles)
        targets.append(PlanningTarget(target_id, smiles))
    if not targets:
        raise BenchmarkProtocolError("planning targets must not be empty")
    return tuple(targets)


def normalize_stock(
    smiles_values: Iterable[str],
    *,
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> frozenset[str]:
    stock = {
        require_text(
            canonicalizer(require_text(value, field="stock SMILES")), field="stock"
        )
        for value in smiles_values
    }
    if not stock:
        raise BenchmarkProtocolError("stock must not be empty")
    return frozenset(stock)


def _children(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, Mapping)]


def _kind(node: Mapping[str, Any]) -> str:
    value = str(node.get("type") or node.get("kind") or "").lower()
    if value in {"reaction", "rxn"} or node.get("is_reaction") is True:
        return "reaction"
    return "molecule"


def verify_route_tree(
    route: Mapping[str, Any],
    *,
    target_smiles: str,
    stock: frozenset[str],
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> dict[str, Any]:
    """Recompute solved state with strict molecule-OR/reaction-AND semantics."""

    tree = route.get("tree")
    issues: list[str] = []
    unresolved: list[str] = []
    reaction_count = 0
    max_depth = 0
    visiting: set[int] = set()

    if not isinstance(tree, Mapping):
        return {
            "verified_solved": False,
            "valid": False,
            "unresolved_leaves": [],
            "reaction_count": 0,
            "max_depth": 0,
            "issues": ["missing_route_tree"],
        }

    def visit(node: Mapping[str, Any], depth: int) -> bool:
        nonlocal reaction_count, max_depth
        identity = id(node)
        if identity in visiting:
            issues.append("cycle_detected")
            return False
        visiting.add(identity)
        max_depth = max(max_depth, depth)
        kind = _kind(node)
        children = _children(node)
        if kind == "molecule":
            raw_smiles = node.get("smiles")
            try:
                smiles = require_text(
                    canonicalizer(require_text(raw_smiles, field="molecule SMILES")),
                    field="canonical molecule",
                )
            except Exception:
                issues.append("invalid_molecule_smiles")
                visiting.remove(identity)
                return False
            if not children:
                solved = smiles in stock
                if not solved:
                    unresolved.append(smiles)
            else:
                if any(_kind(child) != "reaction" for child in children):
                    issues.append("molecule_has_nonreaction_child")
                solved = any(visit(child, depth) for child in children)
        else:
            reaction_count += 1
            if not children:
                issues.append("reaction_without_precursors")
                solved = False
            else:
                if any(_kind(child) != "molecule" for child in children):
                    issues.append("reaction_has_nonmolecule_child")
                solved = all(visit(child, depth + 1) for child in children)
        visiting.remove(identity)
        return solved

    try:
        root = require_text(
            canonicalizer(require_text(tree.get("smiles"), field="root SMILES")),
            field="canonical root",
        )
    except Exception:
        root = ""
        issues.append("invalid_root_smiles")
    if root != target_smiles:
        issues.append("root_target_mismatch")
    solved = visit(tree, 0) and root == target_smiles
    return {
        "verified_solved": solved,
        "valid": not any(
            issue
            in {
                "cycle_detected",
                "invalid_molecule_smiles",
                "invalid_root_smiles",
                "root_target_mismatch",
                "reaction_without_precursors",
            }
            for issue in issues
        ),
        "unresolved_leaves": sorted(set(unresolved)),
        "reaction_count": reaction_count,
        "max_depth": max_depth,
        "issues": sorted(set(issues)),
    }


def normalize_planner_outputs(
    targets: Sequence[PlanningTarget],
    payloads: Iterable[Mapping[str, Any]],
    *,
    stock: frozenset[str],
    max_routes: int,
    budget: Mapping[str, int | float],
    canonicalizer: Canonicalizer = rdkit_canonicalize,
) -> tuple[dict[str, Any], ...]:
    route_limit = positive_int(max_routes, field="max_routes")
    target_by_id = {target.target_id: target for target in targets}
    payload_by_id: dict[str, Mapping[str, Any]] = {}
    for index, payload in enumerate(payloads, start=1):
        require_exact_fields(payload, PAYLOAD_FIELDS, field=f"planner payload {index}")
        target_id = require_text(payload["target_id"], field="target_id")
        if target_id not in target_by_id or target_id in payload_by_id:
            raise BenchmarkProtocolError(f"unknown or duplicate target {target_id!r}")
        if not isinstance(payload["routes"], list):
            raise BenchmarkProtocolError("routes must be an array")
        if len(payload["routes"]) > route_limit:
            raise BenchmarkProtocolError("planner output exceeds max_routes")
        payload_by_id[target_id] = payload
    if set(payload_by_id) != set(target_by_id):
        raise BenchmarkProtocolError(
            "planner output must cover every target exactly once"
        )

    normalized: list[dict[str, Any]] = []
    for target in targets:
        payload = payload_by_id[target.target_id]
        stats = payload["search_stats"]
        if not isinstance(stats, Mapping):
            raise BenchmarkProtocolError("search_stats must be an object")
        stats_copy = json_copy(dict(stats), field="search_stats")
        violations: list[str] = []
        for name, maximum in budget.items():
            actual = stats.get(name)
            if actual is None:
                violations.append(f"missing_{name}")
                continue
            actual_number = finite_number(actual, field=name, allow_none=False)
            maximum_number = finite_number(
                maximum, field=f"budget.{name}", allow_none=False
            )
            if (
                actual_number is not None
                and maximum_number is not None
                and actual_number > maximum_number
            ):
                violations.append(f"exceeded_{name}")
        routes: list[dict[str, Any]] = []
        seen_signatures: dict[str, int] = {}
        for rank, route in enumerate(payload["routes"], start=1):
            if not isinstance(route, Mapping):
                raise BenchmarkProtocolError("each route must be an object")
            route_copy = json_copy(dict(route), field="route")
            route_copy.setdefault("rank", rank)
            verification = verify_route_tree(
                route_copy,
                target_smiles=target.target_smiles,
                stock=stock,
                canonicalizer=canonicalizer,
            )
            signature = route_signature(route_copy)
            duplicate_of = seen_signatures.get(signature)
            seen_signatures.setdefault(signature, rank)
            routes.append(
                {
                    "rank": rank,
                    "route_signature": signature,
                    "duplicate_of_rank": duplicate_of,
                    "claimed_solved": route.get("solved"),
                    "verification": verification,
                    "raw_route": route_copy,
                }
            )
        normalized.append(
            {
                "target_id": target.target_id,
                "target_smiles": target.target_smiles,
                "termination_reason": require_text(
                    payload["termination_reason"], field="termination_reason"
                ),
                "search_stats": stats_copy,
                "budget_violations": violations,
                "routes": routes,
            }
        )
    return tuple(normalized)


def evaluate_routes(
    normalized: Sequence[Mapping[str, Any]],
    reference_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    references: dict[str, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(reference_rows, start=1):
        require_exact_fields(row, REFERENCE_FIELDS, field=f"reference row {index}")
        target_id = require_text(row["target_id"], field="reference target_id")
        if target_id in references:
            raise BenchmarkProtocolError(f"duplicate reference {target_id!r}")
        routes = row["routes"]
        if (
            not isinstance(routes, list)
            or not routes
            or not all(isinstance(route, Mapping) for route in routes)
        ):
            raise BenchmarkProtocolError("reference routes must be a non-empty array")
        references[target_id] = list(routes)
    if {item["target_id"] for item in normalized} != set(references):
        raise BenchmarkProtocolError("prediction and reference targets must match")

    target_results: list[dict[str, Any]] = []
    route_count = duplicate_count = 0
    for item in normalized:
        target_id = str(item["target_id"])
        refs = references[target_id]
        ref_signatures = {route_signature(route) for route in refs}
        first_exact: int | None = None
        best_similarity = 0.0
        solved_routes = 0
        for route in item["routes"]:
            route_count += 1
            if route["duplicate_of_rank"] is not None:
                duplicate_count += 1
            if route["verification"]["verified_solved"]:
                solved_routes += 1
            if route["route_signature"] in ref_signatures and first_exact is None:
                first_exact = int(route["rank"])
            best_similarity = max(
                best_similarity,
                *(
                    route_similarity(route["raw_route"], reference)
                    for reference in refs
                ),
            )
        target_results.append(
            {
                "target_id": target_id,
                "solved": solved_routes > 0,
                "solved_route_count": solved_routes,
                "first_reference_rank": first_exact,
                "reference_recovered": first_exact is not None,
                "best_reference_similarity": best_similarity,
                "budget_compliant": not item["budget_violations"],
            }
        )
    count = len(target_results)
    result = {
        "schema_version": 1,
        "scenario_id": "multistep_paroutes_budgeted_v1",
        "evaluation_unit": "target",
        "target_count": count,
        "solved_target_rate": sum(row["solved"] for row in target_results) / count,
        "reference_route_recovery_rate": sum(
            row["reference_recovered"] for row in target_results
        )
        / count,
        "mean_best_reference_similarity": sum(
            row["best_reference_similarity"] for row in target_results
        )
        / count,
        "budget_compliance_rate": sum(row["budget_compliant"] for row in target_results)
        / count,
        "duplicate_route_rate": duplicate_count / route_count if route_count else 0.0,
        "targets": target_results,
        "caveat": "A verified solved route reaches the frozen stock; it is not experimental validation.",
    }
    result["result_sha256"] = sha256_json(result)
    return result


__all__ = [
    "BenchmarkProtocolError",
    "PlanningTarget",
    "evaluate_routes",
    "normalize_planner_outputs",
    "normalize_stock",
    "validate_targets",
    "verify_route_tree",
]
