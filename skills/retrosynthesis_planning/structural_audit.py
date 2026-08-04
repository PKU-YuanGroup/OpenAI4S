"""Deterministic structural checks for exported retrosynthesis route trees."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from .route_review import (
    _children,
    _iter_molecules,
    _iter_reactions,
    _reaction_descriptor,
    _route_tree,
)


def _rdkit_chem() -> Any | None:
    try:
        from rdkit import Chem  # type: ignore
    except ImportError:
        return None
    return Chem


def _atom_counts(smiles: str, chem: Any) -> Counter[str] | None:
    molecule = chem.MolFromSmiles(smiles)
    if molecule is None:
        return None
    return Counter(atom.GetSymbol() for atom in molecule.GetAtoms())


def _issue(
    *,
    severity: str,
    code: str,
    message: str,
    route_rank: Any,
    path: tuple[int, ...],
    product_smiles: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
        "route_rank": route_rank,
        "path": list(path),
    }
    if product_smiles:
        item["product_smiles"] = product_smiles
    return item


def audit_route_structure(route: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check one route tree for deterministic structural integrity problems."""
    issues: list[dict[str, Any]] = []
    route_rank = route.get("rank")
    tree = _route_tree(route)
    if not tree:
        return [
            _issue(
                severity="error",
                code="missing_route_tree",
                message="Route has no mapping-valued tree.",
                route_rank=route_rank,
                path=(),
            )
        ]

    chem = _rdkit_chem()
    for node, path in _iter_molecules(tree):
        smiles = str(node.get("smiles") or "").strip()
        if not smiles:
            issues.append(
                _issue(
                    severity="error",
                    code="missing_molecule_smiles",
                    message="Molecule node has no SMILES.",
                    route_rank=route_rank,
                    path=path,
                )
            )
        elif chem is not None and chem.MolFromSmiles(smiles) is None:
            issues.append(
                _issue(
                    severity="error",
                    code="invalid_molecule_smiles",
                    message=f"RDKit could not parse molecule SMILES {smiles!r}.",
                    route_rank=route_rank,
                    path=path,
                )
            )

    for node, product_smiles, path in _iter_reactions(tree):
        descriptor = _reaction_descriptor(node, product_smiles)
        precursors = descriptor["precursors"]
        if not product_smiles:
            issues.append(
                _issue(
                    severity="error",
                    code="reaction_without_product",
                    message="Reaction node is not attached below a product molecule.",
                    route_rank=route_rank,
                    path=path,
                )
            )
        if not precursors:
            issues.append(
                _issue(
                    severity="error",
                    code="reaction_without_precursors",
                    message="Reaction node has no molecule precursor children.",
                    route_rank=route_rank,
                    path=path,
                    product_smiles=product_smiles,
                )
            )
            continue
        if len(precursors) != len(set(precursors)):
            issues.append(
                _issue(
                    severity="warning",
                    code="duplicate_precursor",
                    message="Reaction contains duplicate precursor SMILES.",
                    route_rank=route_rank,
                    path=path,
                    product_smiles=product_smiles,
                )
            )
        if not descriptor["template"] and not descriptor["mapped_reaction"]:
            issues.append(
                _issue(
                    severity="warning",
                    code="missing_reaction_identity",
                    message="Reaction has neither a template nor a mapped reaction.",
                    route_rank=route_rank,
                    path=path,
                    product_smiles=product_smiles,
                )
            )

        if chem is None or not product_smiles:
            continue
        product_counts = _atom_counts(product_smiles, chem)
        precursor_counts = [_atom_counts(smiles, chem) for smiles in precursors]
        if product_counts is None or any(counts is None for counts in precursor_counts):
            continue
        available: Counter[str] = Counter()
        for counts in precursor_counts:
            available.update(counts or {})
        deficits = {
            element: count - available[element]
            for element, count in product_counts.items()
            if count > available[element]
        }
        if deficits:
            detail = ", ".join(
                f"{element}:{count}" for element, count in deficits.items()
            )
            issues.append(
                _issue(
                    severity="warning",
                    code="product_element_deficit",
                    message=(
                        "Product contains more atoms than all listed precursors for "
                        f"{detail}; the export may omit a reactant or encode an "
                        "invalid step."
                    ),
                    route_rank=route_rank,
                    path=path,
                    product_smiles=product_smiles,
                )
            )
    return issues


def audit_routes(routes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Audit multiple routes and return a JSON-serializable summary."""
    route_list = [dict(route) for route in routes]
    issues = [issue for route in route_list for issue in audit_route_structure(route)]
    counts = Counter(issue["severity"] for issue in issues)
    return {
        "route_count": len(route_list),
        "issue_count": len(issues),
        "severity_counts": {
            "error": counts.get("error", 0),
            "warning": counts.get("warning", 0),
        },
        "rdkit_checks_enabled": _rdkit_chem() is not None,
        "issues": issues,
        "disclaimer": (
            "Structural audit only; this does not validate reaction feasibility, "
            "conditions, yield, selectivity, safety, or scale-up readiness."
        ),
    }
