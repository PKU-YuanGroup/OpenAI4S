"""Regenerate the aspirin example dashboard from its committed source data.

    uv run python skills/retrosynthesis_planning/examples/build_example.py

`aspirin_routes.json` holds AiZynthFinder-shaped route trees and
`aspirin_annotations.json` holds deterministic demonstration annotations — they
are illustrative text, not experimental evidence for aspirin manufacturing.

Molecule depictions come from RDKit when it is importable, and from the
transparent placeholder SVG otherwise. Install RDKit before regenerating if you
want the example to show real structures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from retrosynthesis_planning.kernel import (  # noqa: E402
    build_markdown_report,
    normalize_routes,
    rank_routes,
    render_route_tree_html,
)

TARGET = "CC(=O)Oc1ccccc1C(=O)O"


def main() -> None:
    routes = json.loads((HERE / "aspirin_routes.json").read_text(encoding="utf-8"))
    annotations = json.loads(
        (HERE / "aspirin_annotations.json").read_text(encoding="utf-8")
    )
    ranked = rank_routes(normalize_routes(routes))

    html = render_route_tree_html(
        ranked, target_smiles=TARGET, max_routes=10, annotations=annotations
    )
    report = build_markdown_report(ranked, target_smiles=TARGET)

    (HERE / "aspirin_retrosynthesis.html").write_text(html, encoding="utf-8")
    (HERE / "aspirin_retrosynthesis_report.md").write_text(report, encoding="utf-8")

    try:
        import rdkit  # type: ignore  # noqa: F401

        depictions = "RDKit"
    except ImportError:
        depictions = "placeholder SVG (RDKit not installed)"
    print(f"routes={len(ranked)} depictions={depictions} html={len(html)} bytes")


if __name__ == "__main__":
    main()
