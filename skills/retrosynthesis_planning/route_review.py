"""Route de-duplication and diversity selection for retrosynthesis review."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Iterator, Mapping

from .kernel import canonicalize_smiles


def _children(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, Mapping):
        return []
    value = node.get("children")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _node_kind(node: Mapping[str, Any]) -> str:
    value = str(node.get("type") or node.get("kind") or "").strip().lower()
    if value in {"reaction", "rxn"} or node.get("is_reaction") is True:
        return "reaction"
    return "molecule"


def _safe_canonicalize(smiles: Any) -> str:
    value = str(smiles or "").strip()
    if not value:
        return ""
    try:
        return canonicalize_smiles(value)
    except (TypeError, ValueError):
        return value


def _reaction_descriptor(
    node: Mapping[str, Any], product_smiles: str | None
) -> dict[str, Any]:
    metadata = node.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    precursors = sorted(
        smiles
        for child in _children(node)
        if _node_kind(child) == "molecule"
        and (smiles := _safe_canonicalize(child.get("smiles")))
    )
    template = str(
        node.get("template")
        or metadata.get("template")
        or metadata.get("retro_template")
        or ""
    ).strip()
    reaction_smiles = node.get("smiles")
    mapped_reaction = str(
        node.get("mapped_reaction")
        or metadata.get("mapped_reaction")
        or (
            reaction_smiles
            if isinstance(reaction_smiles, str) and ">>" in reaction_smiles
            else ""
        )
    ).strip()
    return {
        "product": _safe_canonicalize(product_smiles),
        "precursors": precursors,
        "template": template,
        "mapped_reaction": mapped_reaction,
    }


def _iter_reactions(
    node: Any,
    *,
    product_smiles: str | None = None,
    path: tuple[int, ...] = (),
) -> Iterator[tuple[dict[str, Any], str | None, tuple[int, ...]]]:
    if not isinstance(node, Mapping):
        return
    current = dict(node)
    current_product = product_smiles
    if _node_kind(current) == "molecule":
        current_product = str(current.get("smiles") or "").strip() or product_smiles
    else:
        yield current, product_smiles, path
    for index, child in enumerate(_children(current)):
        yield from _iter_reactions(
            child,
            product_smiles=current_product,
            path=(*path, index),
        )


def _iter_molecules(
    node: Any, *, path: tuple[int, ...] = ()
) -> Iterator[tuple[dict[str, Any], tuple[int, ...]]]:
    if not isinstance(node, Mapping):
        return
    current = dict(node)
    if _node_kind(current) == "molecule":
        yield current, path
    for index, child in enumerate(_children(current)):
        yield from _iter_molecules(child, path=(*path, index))


def _route_tree(route: Mapping[str, Any]) -> dict[str, Any]:
    tree = route.get("tree")
    return dict(tree) if isinstance(tree, Mapping) else {}


def route_signature(route: Mapping[str, Any]) -> str:
    """Return a stable chemistry-context signature for one normalized route."""
    tree = _route_tree(route)
    reactions = sorted(
        (
            _reaction_descriptor(node, product)
            for node, product, _path in _iter_reactions(tree)
        ),
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True),
    )
    leaves = sorted(
        smiles
        for node, _path in _iter_molecules(tree)
        if not _children(node) and (smiles := _safe_canonicalize(node.get("smiles")))
    )
    payload = {"reactions": reactions, "leaves": leaves}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"route:{digest[:20]}"


def deduplicate_routes(
    routes: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse identical route trees while retaining their original ranks."""
    unique: list[dict[str, Any]] = []
    by_signature: dict[str, dict[str, Any]] = {}
    for index, route in enumerate(routes, start=1):
        candidate = dict(route)
        source_rank = candidate.get("rank", index)
        signature = route_signature(candidate)
        existing = by_signature.get(signature)
        if existing is None:
            candidate["route_signature"] = signature
            candidate["duplicate_count"] = 1
            candidate["source_ranks"] = [source_rank]
            by_signature[signature] = candidate
            unique.append(candidate)
            continue
        existing["duplicate_count"] = int(existing.get("duplicate_count", 1)) + 1
        ranks = list(existing.get("source_ranks") or [])
        if source_rank not in ranks:
            ranks.append(source_rank)
        existing["source_ranks"] = ranks
    return unique


def _route_feature_set(route: Mapping[str, Any]) -> set[str]:
    tree = _route_tree(route)
    features: set[str] = set()
    for node, product, _path in _iter_reactions(tree):
        descriptor = _reaction_descriptor(node, product)
        identity = descriptor["template"] or descriptor["mapped_reaction"]
        if identity:
            features.add(f"rxn:{identity}")
        if descriptor["product"]:
            features.add(f"product:{descriptor['product']}")
        features.update(f"precursor:{item}" for item in descriptor["precursors"])
    for node, _path in _iter_molecules(tree):
        if not _children(node):
            smiles = _safe_canonicalize(node.get("smiles"))
            if smiles:
                features.add(f"leaf:{smiles}")
    return features


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def select_diverse_routes(
    routes: Iterable[Mapping[str, Any]],
    *,
    max_routes: int = 10,
    similarity_threshold: float = 0.85,
    fill_to_limit: bool = True,
) -> list[dict[str, Any]]:
    """Prefer distinct routes while preserving deterministic backend order."""
    if isinstance(max_routes, bool) or not isinstance(max_routes, int):
        raise TypeError("max_routes must be a positive integer")
    if max_routes < 1:
        raise ValueError("max_routes must be at least 1")
    if isinstance(similarity_threshold, bool) or not isinstance(
        similarity_threshold, (int, float)
    ):
        raise TypeError("similarity_threshold must be a number between 0 and 1")
    threshold = float(similarity_threshold)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("similarity_threshold must be between 0 and 1")

    selected: list[dict[str, Any]] = []
    selected_features: list[set[str]] = []
    skipped: list[dict[str, Any]] = []
    for route in routes:
        if len(selected) >= max_routes:
            break
        candidate = dict(route)
        features = _route_feature_set(candidate)
        maximum = max(
            (_jaccard(features, previous) for previous in selected_features),
            default=0.0,
        )
        candidate["max_similarity_to_selected"] = round(maximum, 3)
        if selected and maximum >= threshold:
            skipped.append(candidate)
            continue
        candidate["diversity_relaxed"] = False
        selected.append(candidate)
        selected_features.append(features)

    if fill_to_limit and len(selected) < max_routes:
        for candidate in skipped:
            if len(selected) >= max_routes:
                break
            candidate["diversity_relaxed"] = True
            selected.append(candidate)

    for display_rank, route in enumerate(selected, start=1):
        route.setdefault("source_rank", route.get("rank", display_rank))
        route["rank"] = display_rank
    return selected
