"""Retrosynthesis planning helpers for OpenAI4S.

The helpers in this module are intentionally pure stdlib. They normalize route
exports from retrosynthesis backends, rank candidate routes, and render compact
HTML/Markdown artifacts for human review.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import html
import json
import math
import re
import shlex
import warnings
from pathlib import Path
from typing import Any, Iterable


def canonicalize_smiles(smiles: str) -> str:
    """Return a canonical SMILES when RDKit is installed, else a stripped string."""
    value = (smiles or "").strip()
    if not value:
        raise ValueError("SMILES is empty")
    try:
        from rdkit import Chem  # type: ignore
    except ImportError:
        return value

    mol = Chem.MolFromSmiles(value)
    if mol is None:
        raise ValueError(f"invalid SMILES: {smiles!r}")
    return Chem.MolToSmiles(mol, canonical=True)


def build_aizynth_command(
    smiles: str,
    config_path: str,
    output_path: str | None = None,
    conda_env: str | None = None,
    extra_args: Iterable[str] | None = None,
) -> list[str]:
    """Build an aizynthcli command as a list suitable for shlex.join."""
    target = canonicalize_smiles(smiles)
    command = [
        "aizynthcli",
        "--config",
        str(Path(config_path).expanduser()),
        "--smiles",
        target,
    ]
    if output_path:
        command.extend(["--output", str(Path(output_path).expanduser())])
    if extra_args:
        command.extend(str(arg) for arg in extra_args)
    if conda_env:
        return ["conda", "run", "-n", conda_env, *command]
    return command


def command_to_shell(command: Iterable[str]) -> str:
    """Render a command list as a shell-safe command line."""
    return shlex.join(list(command))


def load_aizynth_routes(path: str | Path) -> Any:
    """Load a retrosynthesis route export from JSON."""
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_routes(payload: Any) -> list[dict[str, Any]]:
    """Normalize backend-specific route exports into a stable route schema."""
    routes = []
    for index, candidate in enumerate(_route_candidates(payload), start=1):
        routes.append(_normalize_route(candidate, rank=index))
    return routes


def rank_routes(routes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort routes by solved status, score, shorter route length, and precursor count."""

    def key(route: dict[str, Any]) -> tuple:
        solved = 1 if route.get("solved") else 0
        score = _as_float(route.get("score"), default=-math.inf)
        steps = route.get("steps")
        step_count = steps if isinstance(steps, int) else 10**6
        precursors = len(route.get("starting_materials") or [])
        return (-solved, -score, step_count, precursors, route.get("rank", 10**6))

    ranked = sorted((dict(route) for route in routes), key=key)
    for idx, route in enumerate(ranked, start=1):
        route["rank"] = idx
    return ranked


def render_route_tree_html(
    routes: Iterable[dict[str, Any]],
    target_smiles: str | None = None,
    max_routes: int = 10,
    annotations: dict[str, Any] | None = None,
    llm: Any | None = None,
) -> str:
    """Render a self-contained, figure-style-inspired route dashboard."""
    all_routes = list(routes)
    route_list = all_routes[:max_routes]
    if annotations is None and llm is not None:
        annotations = annotate_routes_with_llm(
            route_list, llm=llm, target_smiles=target_smiles, max_routes=max_routes
        )
    molecule_briefs = collect_molecule_briefs(route_list, target_smiles=target_smiles)
    title = "Retrosynthesis route analysis"
    if target_smiles:
        title = f"Retrosynthesis route analysis for {target_smiles}"

    solved_count = sum(1 for route in all_routes if route.get("solved"))
    top_score = _format_score(route_list[0].get("score")) if route_list else "n/a"
    shortest_solved = min(
        (
            route.get("steps")
            for route in all_routes
            if route.get("solved") and isinstance(route.get("steps"), int)
        ),
        default="n/a",
    )
    interactive_panel = _render_interactive_andor_tree(
        route_list, target_smiles=target_smiles, annotations=annotations
    )

    cards: list[str] = []
    for route in route_list:
        materials = route.get("starting_materials") or []
        material_html = "".join(_material_chip(material) for material in materials)
        materials_block = material_html or '<span class="muted">Not detected</span>'
        diagram_html = _render_svg_tree(route.get("tree"), route.get("rank", "?"))
        analysis_html = _render_route_analysis(route, annotations)
        outline_html = _render_outline_tree(route.get("tree"))
        solved_class = "ok" if route.get("solved") else "warn"
        cards.append(
            "\n".join(
                [
                    '<section class="route-card">',
                    '<div class="route-head">',
                    f"<h2>Route {route.get('rank', '?')}</h2>",
                    f'<span class="pill {solved_class}">{"solved" if route.get("solved") else "unsolved"}</span>',
                    "</div>",
                    '<div class="metrics">',
                    f'<div><span>Score</span><strong>{_format_score(route.get("score"))}</strong></div>',
                    f'<div><span>Steps</span><strong>{html.escape(str(route.get("steps")))}</strong></div>',
                    f"<div><span>Stock precursors</span><strong>{len(materials)}</strong></div>",
                    "</div>",
                    '<div class="route-diagram">',
                    diagram_html,
                    "</div>",
                    analysis_html,
                    "<h3>Starting materials</h3>",
                    f'<div class="chips">{materials_block}</div>',
                    "<details>",
                    "<summary>Text outline</summary>",
                    outline_html,
                    "</details>",
                    "</section>",
                ]
            )
        )

    table = _render_route_table(route_list)
    molecules_panel = _render_molecule_briefs_panel(molecule_briefs, annotations)
    body = (
        "\n".join(cards)
        if cards
        else '<p class="empty">No routes were found in the export.</p>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --ink: #17212b;
      --muted: #687482;
      --line: #dce4ec;
      --line-strong: #c4d0dc;
      --panel: #ffffff;
      --soft: #f5f8fa;
      --paper: #fbfcfd;
      --target: rgba(48, 117, 191, 0.09);
      --target-stroke: #3075bf;
      --reaction: rgba(211, 142, 45, 0.12);
      --reaction-stroke: #b7791f;
      --stock: rgba(49, 139, 93, 0.09);
      --stock-stroke: #2f8b5d;
      --missing: rgba(199, 74, 92, 0.09);
      --missing-stroke: #c74a5c;
      --unknown: rgba(105, 121, 138, 0.08);
      --unknown-stroke: #69798a;
      --accent: #2f6f8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #f5f7f9;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 19px; margin: 0; }}
    h3 {{ font-size: 14px; margin: 18px 0 10px; color: #334155; }}
    code {{ background: rgba(47, 111, 143, 0.08); padding: 2px 5px; border-radius: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 22px; box-shadow: 0 10px 28px rgba(23, 33, 43, 0.06); }}
    .subtitle {{ color: var(--muted); margin: 0; }}
    .note {{ color: var(--muted); font-size: 13px; margin: 8px 0 0; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 18px; }}
    .kpi {{ background: var(--soft); border-radius: 8px; padding: 14px; }}
    .kpi span {{ color: var(--muted); display: block; font-size: 12px; }}
    .kpi strong {{ display: block; font-size: 22px; margin-top: 2px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin-top: 18px; overflow: hidden; }}
    .panel h2 {{ padding: 16px 18px; border-bottom: 1px solid var(--line); }}
    .route-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-top: 18px; }}
    .route-head {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; }}
    .pill {{ border-radius: 999px; font-size: 12px; font-weight: 700; padding: 4px 10px; text-transform: uppercase; }}
    .pill.ok {{ background: var(--stock); color: #14532d; }}
    .pill.warn {{ background: var(--missing); color: #9f1239; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 14px 0; }}
    .metrics div {{ background: var(--soft); border-radius: 8px; padding: 11px; }}
    .metrics span {{ color: var(--muted); display: block; font-size: 12px; }}
    .metrics strong {{ display: block; font-size: 18px; margin-top: 2px; }}
    .route-diagram {{ border: 1px solid var(--line); border-radius: 8px; background: #fbfcfe; overflow: auto; padding: 10px; }}
    .route-svg {{ display: block; min-width: 720px; width: 100%; height: auto; }}
    .route-analysis {{ margin-top: 16px; border-top: 1px solid var(--line); padding-top: 14px; }}
    .route-analysis h3 {{ margin-top: 0; color: #20313f; }}
    .analysis-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px 16px; margin: 0; }}
    .analysis-field {{ border-left: 3px solid rgba(47, 111, 143, 0.35); padding-left: 10px; min-width: 0; }}
    .analysis-field dt {{ color: var(--muted); font-size: 11px; font-weight: 750; text-transform: uppercase; }}
    .analysis-field dd {{ margin: 4px 0 0; font-size: 13px; overflow-wrap: anywhere; }}
    .analysis-field ul {{ margin: 0; padding-left: 17px; }}
    .analysis-field li {{ margin-bottom: 4px; }}
    .mini-kv {{ display: grid; grid-template-columns: minmax(86px, 0.34fr) 1fr; gap: 4px 9px; margin: 0; }}
    .mini-kv dt {{ color: var(--muted); text-transform: none; font-size: 12px; }}
    .mini-kv dd {{ margin: 0; }}
    .andor-panel {{ background: var(--panel); border-color: var(--line-strong); box-shadow: 0 20px 48px rgba(23, 33, 43, 0.11); }}
    .andor-panel h2 {{ color: #14202b; border-bottom-color: var(--line); background: #fbfcfd; letter-spacing: 0; }}
    .andor-shell {{ display: grid; grid-template-columns: minmax(0, 1fr) 380px; min-height: 740px; }}
    .andor-toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 14px; border-bottom: 1px solid var(--line); background: #f8fafb; }}
    .andor-toolbar button {{ border: 1px solid #bfd5e4; border-radius: 6px; background: rgba(255, 255, 255, 0.88); color: #25566f; cursor: pointer; font-weight: 650; padding: 7px 10px; }}
    .andor-toolbar button:hover {{ border-color: #6fa7c4; box-shadow: 0 0 0 2px rgba(111, 167, 196, 0.18); }}
    .andor-toolbar .note {{ color: var(--muted); }}
    .andor-canvas {{ background-color: #fbfcfd; background-image: radial-gradient(circle at center, rgba(47,111,143,0.08) 1px, transparent 1.6px); background-size: 24px 24px; overflow: hidden; position: relative; }}
    .andor-svg {{ display: block; width: 100%; height: 680px; cursor: grab; }}
    .andor-svg.dragging {{ cursor: grabbing; }}
    .andor-detail {{ border-left: 1px solid var(--line); background: #ffffff; color: var(--ink); padding: 18px; overflow: auto; }}
    .andor-detail h3 {{ margin: 0 0 12px; color: #14202b; font-size: 16px; }}
    .andor-detail dl {{ display: grid; grid-template-columns: 118px 1fr; gap: 8px 12px; font-size: 13px; }}
    .andor-detail dt {{ color: var(--muted); }}
    .andor-detail dd {{ margin: 0; overflow-wrap: anywhere; }}
    .andor-detail ul {{ margin: 0; padding-left: 17px; }}
    .andor-detail li {{ margin: 0 0 4px; }}
    .detail-kv {{ display: grid; grid-template-columns: minmax(82px, 0.38fr) 1fr; gap: 4px 9px; }}
    .detail-kv span:nth-child(odd) {{ color: var(--muted); }}
    .detail-kv span:nth-child(even) {{ color: var(--ink); }}
    .andor-detail img {{ display: block; width: 100%; max-height: 220px; object-fit: contain; border: 1px solid rgba(196, 208, 220, 0.55); border-radius: 8px; background: transparent; margin-bottom: 14px; }}
    .andor-detail a {{ color: #2563eb; }}
    .andor-node rect {{ stroke-width: 1.7; filter: drop-shadow(0 8px 16px rgba(23, 33, 43, 0.11)); }}
    .andor-node text {{ fill: #17212b; font-size: 12px; pointer-events: none; }}
    .andor-node .node-meta {{ fill: #65717c; font-size: 10px; text-transform: uppercase; }}
    .andor-node .node-kind {{ fill: #4d5b68; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0; }}
    .andor-node .route-badge {{ fill: #ffffff; stroke: rgba(23, 33, 43, 0.18); stroke-width: 1; }}
    .andor-node .route-badge-text {{ fill: #4d5b68; font-size: 9px; font-weight: 700; }}
    .andor-node .structure-well {{ fill: rgba(255, 255, 255, 0.10); stroke: rgba(255, 255, 255, 0.30); stroke-width: 1; }}
    .andor-node image {{ opacity: 0.98; }}
    .andor-node.selected rect {{ stroke-width: 3; }}
    .andor-node.dimmed {{ opacity: 0.34; }}
    .andor-node.neighbor {{ opacity: 0.96; }}
    .andor-node.collapsed rect {{ stroke-dasharray: 5 3; }}
    .andor-node.target rect {{ fill: var(--target); stroke: var(--target-stroke); }}
    .andor-node.reaction rect {{ fill: var(--reaction); stroke: var(--reaction-stroke); }}
    .andor-node.stock rect {{ fill: var(--stock); stroke: var(--stock-stroke); }}
    .andor-node.missing rect {{ fill: var(--missing); stroke: var(--missing-stroke); }}
    .andor-node.unknown rect {{ fill: var(--unknown); stroke: var(--unknown-stroke); }}
    .andor-edge {{ fill: none; stroke: #9eabb8; stroke-width: 1.45; opacity: 0.56; marker-end: url(#andor-arrow); }}
    .andor-edge.active {{ stroke: var(--accent); opacity: 0.92; stroke-width: 2.2; }}
    .andor-edge.merged {{ stroke: #5b9f7d; opacity: 0.82; stroke-width: 1.8; }}
    .edge {{ fill: none; stroke: #94a3b8; stroke-width: 1.5; }}
    .node rect {{ stroke-width: 1.5; }}
    .node text {{ fill: #182026; font-size: 12px; }}
    .node .meta {{ fill: #64748b; font-size: 10px; text-transform: uppercase; }}
    .node .structure-well {{ fill: rgba(255, 255, 255, 0.10); stroke: rgba(255, 255, 255, 0.30); stroke-width: 1; }}
    .node image {{ background: transparent; opacity: 0.98; }}
    .target rect {{ fill: var(--target); stroke: var(--target-stroke); }}
    .reaction rect {{ fill: var(--reaction); stroke: var(--reaction-stroke); }}
    .stock rect {{ fill: var(--stock); stroke: var(--stock-stroke); }}
    .missing rect {{ fill: var(--missing); stroke: var(--missing-stroke); }}
    .unknown rect {{ fill: var(--unknown); stroke: var(--unknown-stroke); }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; color: var(--muted); font-size: 12px; }}
    .legend span::before {{ content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
    .legend .lg-target::before {{ background: var(--target); border: 1px solid var(--target-stroke); }}
    .legend .lg-reaction::before {{ background: var(--reaction); border: 1px solid var(--reaction-stroke); }}
    .legend .lg-stock::before {{ background: var(--stock); border: 1px solid var(--stock-stroke); }}
    .legend .lg-missing::before {{ background: var(--missing); border: 1px solid var(--missing-stroke); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{ background: #eef6ff; border: 1px solid #bfdbfe; border-radius: 999px; padding: 5px 9px; font-size: 12px; }}
    .molecule-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; padding: 16px; }}
    .molecule-card {{ background: #fbfcfe; border: 1px solid var(--line); border-radius: 8px; padding: 13px; }}
    .molecule-card h3 {{ margin-top: 0; }}
    .structure-frame {{ display: grid; place-items: center; height: 158px; background: linear-gradient(180deg, rgba(255,255,255,0.14), rgba(245,248,250,0.24)); border: 1px solid rgba(226,232,240,0.62); border-radius: 6px; margin-bottom: 10px; overflow: hidden; }}
    .mol-structure {{ display: block; width: 100%; height: 150px; object-fit: contain; background: transparent; }}
    .structure-fallback {{ opacity: 0.92; }}
    .molecule-card dl {{ display: grid; grid-template-columns: 92px 1fr; gap: 5px 10px; margin: 0; font-size: 13px; }}
    .molecule-card dt {{ color: var(--muted); }}
    .molecule-card dd {{ margin: 0; overflow-wrap: anywhere; }}
    .muted, .empty {{ color: var(--muted); }}
    details {{ margin-top: 14px; }}
    summary {{ cursor: pointer; color: #334155; font-weight: 600; }}
    .tree, .tree ul {{ list-style: none; margin-left: 0; padding-left: 20px; }}
    .tree li {{ margin: 6px 0; }}
    .tag {{ color: var(--muted); font-size: 12px; margin-left: 4px; }}
    @media (max-width: 980px) {{
      .andor-shell {{ grid-template-columns: 1fr; }}
      .andor-detail {{ border-left: 0; border-top: 1px solid var(--line); }}
    }}
    @media (max-width: 720px) {{
      main {{ padding: 16px; }}
      h1 {{ font-size: 23px; }}
      th, td {{ padding: 8px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{html.escape(title)}</h1>
      <p class="subtitle">Generated from normalized retrosynthesis route data. Predictions require expert chemical review.</p>
      <p class="note">Visual style follows the bundled figure-style checklist: data-grounded labels, limited semantic colors, route-level claim consistency, and explicit uncertainty.</p>
      <div class="kpis">
        <div class="kpi"><span>Routes analyzed</span><strong>{len(all_routes)}</strong></div>
        <div class="kpi"><span>Solved routes</span><strong>{solved_count}</strong></div>
        <div class="kpi"><span>Top score</span><strong>{top_score}</strong></div>
        <div class="kpi"><span>Shortest solved route</span><strong>{shortest_solved}</strong></div>
      </div>
    </section>
    <section class="panel">
      <h2>Route Ranking</h2>
      {table}
    </section>
    {interactive_panel}
    {molecules_panel}
    <div class="legend">
      <span class="lg-target">Target/intermediate</span>
      <span class="lg-reaction">Reaction</span>
      <span class="lg-stock">Stock precursor</span>
      <span class="lg-missing">Not in stock</span>
    </div>
    {body}
  </main>
</body>
</html>
"""


def build_markdown_report(
    routes: Iterable[dict[str, Any]],
    target_smiles: str | None = None,
    max_routes: int = 5,
) -> str:
    """Build a compact analyst report for route review."""
    route_list = list(routes)
    solved = sum(1 for route in route_list if route.get("solved"))
    molecule_briefs = collect_molecule_briefs(
        route_list[:max_routes], target_smiles=target_smiles
    )
    heading = "# Retrosynthesis Planning Report"
    if target_smiles:
        heading += f"\n\nTarget SMILES: `{target_smiles}`"

    lines = [
        heading,
        "",
        "## Executive Summary",
        "",
        f"- Candidate routes analyzed: {len(route_list)}",
        f"- Routes reaching stock/purchasable materials: {solved}",
        "- Recommendation: prioritize solved, high-score, short routes and review reaction feasibility manually.",
        "",
        "## Ranked Routes",
        "",
    ]

    for route in route_list[:max_routes]:
        materials = route.get("starting_materials") or []
        material_text = (
            ", ".join(f"`{mat}`" for mat in materials) if materials else "not detected"
        )
        lines.extend(
            [
                f"### Route {route.get('rank', '?')}",
                "",
                f"- Solved: {route.get('solved')}",
                f"- Score: {_format_score(route.get('score'))}",
                f"- Estimated steps: {route.get('steps')}",
                f"- Starting materials: {material_text}",
                f"- Retrosynthetic rationale: {_route_rationale(route)}",
                "",
            ]
        )

    lines.extend(["## Molecule Briefs", ""])
    for brief in molecule_briefs:
        lines.extend(
            [
                f"### `{brief['smiles']}`",
                "",
                f"- Role: {brief['role']}",
                f"- Appears in routes: {', '.join(str(rank) for rank in brief['route_ranks'])}",
                f"- Stock status: {brief['stock_status']}",
                f"- Interpretation: {brief['interpretation']}",
                f"- Suggested query: {brief['pubchem_url']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Review Notes",
            "",
            "- Confirm reagent availability, price, purity, and vendor lead time.",
            "- Check stereochemistry, protecting-group logic, chemoselectivity, and hazardous transformations.",
            "- Treat predicted reaction trees as planning hypotheses, not experimental validation.",
            "- Record backend version, model files, stock file, and search parameters for reproducibility.",
            "",
        ]
    )
    return "\n".join(lines)


def collect_molecule_briefs(
    routes: Iterable[dict[str, Any]],
    target_smiles: str | None = None,
    max_molecules: int = 24,
) -> list[dict[str, Any]]:
    """Collect molecule roles, stock status, and query URLs from route trees."""
    records: dict[str, dict[str, Any]] = {}
    target = (target_smiles or "").strip()
    for route in routes:
        rank = route.get("rank", "?")
        for node, depth in _iter_molecule_nodes(route.get("tree")):
            smiles = _node_smiles(node)
            if not smiles:
                continue
            rec = records.setdefault(
                smiles,
                {
                    "smiles": smiles,
                    "roles": set(),
                    "route_ranks": set(),
                    "stock_values": [],
                    "depths": [],
                },
            )
            rec["roles"].add(_molecule_role(node, depth, target))
            rec["route_ranks"].add(rank)
            rec["stock_values"].append(_stock_status_value(node))
            rec["depths"].append(depth)

    briefs = []
    for rec in records.values():
        roles = sorted(rec["roles"], key=_role_sort_key)
        stock_status = _summarize_stock(rec["stock_values"])
        role = ", ".join(roles)
        smiles = rec["smiles"]
        briefs.append(
            {
                "smiles": smiles,
                "role": role,
                "route_ranks": sorted(rec["route_ranks"], key=lambda value: str(value)),
                "stock_status": stock_status,
                "interpretation": _molecule_interpretation(role, stock_status),
                "pubchem_url": build_pubchem_query_url(smiles),
                "min_depth": min(rec["depths"]) if rec["depths"] else 0,
            }
        )
    briefs.sort(
        key=lambda item: (
            _role_sort_key(item["role"]),
            item["min_depth"],
            item["smiles"],
        )
    )
    if len(briefs) > max_molecules:
        warnings.warn(
            f"molecule briefs truncated to {max_molecules} of {len(briefs)} route "
            "molecules; raise max_molecules to brief every displayed molecule",
            RuntimeWarning,
            stacklevel=2,
        )
    return briefs[:max_molecules]


def build_pubchem_query_url(smiles: str) -> str:
    """Return a PubChem search URL for a SMILES string."""
    from urllib.parse import quote

    return "https://pubchem.ncbi.nlm.nih.gov/#query=" + quote(smiles)


def build_pubchem_structure_image_url(
    smiles: str, width: int = 260, height: int = 180
) -> str:
    """Return a PubChem structure-image URL for a SMILES string."""
    from urllib.parse import quote

    encoded = quote(smiles, safe="")
    return (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/"
        f"{encoded}/PNG?image_size={width}x{height}"
    )


def _svg_data_uri(svg: str) -> str:
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"


@functools.lru_cache(maxsize=1024)
def build_molecule_structure_src(
    smiles: str, width: int = 260, height: int = 180
) -> str:
    """Return an embeddable molecule structure source.

    RDKit SVG is preferred when available so the HTML is self-contained and the
    molecule background stays transparent. If RDKit is absent or cannot parse
    the molecule, return a transparent local SVG fallback rather than an
    external structure-image URL.
    """
    svg = _rdkit_molecule_svg(smiles, width=width, height=height)
    if svg:
        return _svg_data_uri(svg)
    return build_molecule_fallback_structure_src(smiles, width=width, height=height)


@functools.lru_cache(maxsize=1024)
def build_molecule_fallback_structure_src(
    smiles: str, width: int = 260, height: int = 180
) -> str:
    """Return a transparent inline fallback when no structure renderer is available."""
    label = _short_label(str(smiles or "molecule"), 32)
    label = html.escape(label)
    w = max(180, int(width))
    h = max(120, int(height))
    cx = w / 2
    cy = h / 2 - 8
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <g fill="none" stroke="#5f6f7c" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" opacity="0.82">
    <path d="M {cx - 62:.1f} {cy:.1f} L {cx - 30:.1f} {cy - 28:.1f} L {cx + 8:.1f} {cy - 14:.1f} L {cx + 42:.1f} {cy - 38:.1f}" />
    <path d="M {cx - 30:.1f} {cy - 28:.1f} L {cx - 22:.1f} {cy + 18:.1f} L {cx + 18:.1f} {cy + 28:.1f} L {cx + 48:.1f} {cy + 2:.1f}" />
    <path d="M {cx + 8:.1f} {cy - 14:.1f} L {cx + 48:.1f} {cy + 2:.1f}" />
  </g>
  <g fill="none" stroke="#5f6f7c" stroke-width="2" opacity="0.95">
    <circle cx="{cx - 62:.1f}" cy="{cy:.1f}" r="8" />
    <circle cx="{cx - 30:.1f}" cy="{cy - 28:.1f}" r="8" />
    <circle cx="{cx + 8:.1f}" cy="{cy - 14:.1f}" r="8" />
    <circle cx="{cx + 48:.1f}" cy="{cy + 2:.1f}" r="8" />
  </g>
  <text x="{cx:.1f}" y="{h - 24:.1f}" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="13" font-weight="650" fill="#3d4b57">{label}</text>
  <text x="{cx:.1f}" y="{h - 8:.1f}" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="9" fill="#687482">structure renderer fallback</text>
</svg>"""
    return _svg_data_uri(svg)


def _molecule_structure_sources(
    smiles: str, width: int = 260, height: int = 180
) -> dict[str, str]:
    """Primary structure source, plus an `onerror` fallback only when it differs.

    `build_molecule_structure_src` already degrades to the placeholder when RDKit
    is missing, so carrying the placeholder a second time would embed the same
    base64 payload twice. The fallback is only meaningful as a safety net behind
    a real RDKit depiction.
    """
    primary = build_molecule_structure_src(smiles, width=width, height=height)
    fallback = build_molecule_fallback_structure_src(smiles, width=width, height=height)
    return {"primary": primary, "fallback": "" if primary == fallback else fallback}


def build_llm_annotation_prompt(
    routes: Iterable[dict[str, Any]],
    target_smiles: str | None = None,
    max_routes: int = 8,
) -> str:
    """Build a prompt for LLM molecule/reaction annotations."""
    route_list = list(routes)[:max_routes]
    molecules = collect_molecule_briefs(route_list, target_smiles=target_smiles)
    reactions = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        if _is_reaction_node(node):
            details = _reaction_info(node)["details"]
            reactions.append(
                {
                    "reaction_key": _reaction_annotation_key(details),
                    "backend_class": details.get("Reaction class", ""),
                    "policy": details.get("Policy", ""),
                    "policy_probability": details.get("Policy probability", ""),
                    "template": details.get("Template", ""),
                    "mapped_reaction": details.get("Mapped reaction", ""),
                    "conditions_in_export": details.get("Conditions", ""),
                }
            )
        for child in _children(node):
            walk(child)

    for route in route_list:
        walk(route.get("tree"))

    payload = {
        "target_smiles": target_smiles,
        "routes": [
            {
                "rank": route.get("rank", index + 1),
                "solved": route.get("solved"),
                "score": route.get("score"),
                "steps": route.get("steps"),
                "starting_materials": route.get("starting_materials") or [],
                "route_rationale": _route_rationale(route),
            }
            for index, route in enumerate(route_list)
        ],
        "molecules": [
            {
                "smiles": item["smiles"],
                "role": item["role"],
                "stock_status": item["stock_status"],
            }
            for item in molecules
        ],
        "reactions": reactions[:24],
    }
    return (
        "You are annotating a retrosynthesis planning result for medicinal/synthetic chemists.\n"
        "Return strict JSON with this schema:\n"
        "{\n"
        '  "routes": {"<route_rank>": {\n'
        '    "route_strategy": "3-5 sentence route-level retrosynthetic strategy and industrial feasibility readout",\n'
        '    "key_disconnections": ["named strategic disconnections or functional-group interconversions"],\n'
        '    "reaction_sequence": ["forward synthesis step descriptions in order"],\n'
        '    "conditions_strategy": "how conditions should be selected or screened across the route",\n'
        '    "yield_outlook": "route-level yield expectation with uncertainty and no unsupported experimental claims",\n'
        '    "route_risks": ["scale-up, chemoselectivity, availability, isolation, safety, or IP/literature risks"],\n'
        '    "recommended_next_steps": ["database, vendor, small-scale screen, analytics, or chemist-review actions"],\n'
        '    "chemist_verdict": "go|optimize|risky|insufficient evidence plus one sentence rationale"\n'
        "  }},\n"
        '  "molecules": {"<SMILES>": {"description": "2-3 sentence chemical identity, route role, functional-group features, and availability/risk note"}},\n'
        '  "reactions": {"<reaction_key>": {\n'
        '    "reaction_type": "human-readable reaction family, never Unrecognized",\n'
        '    "description": "2-4 sentence retrosynthetic and forward-reaction explanation",\n'
        '    "mechanistic_rationale": "brief mechanism or bond-forming/bond-breaking rationale",\n'
        '    "bond_changes": ["bond broken/formed or functional-group interconversion"],\n'
        '    "suggested_conditions": {"reagents": "...", "solvent": "...", "base_or_catalyst": "...", "temperature": "...", "atmosphere": "...", "workup": "..."},\n'
        '    "expected_yield_range": "plausible literature-style range or unknown",\n'
        '    "yield_rationale": "why that yield range is plausible; state uncertainty",\n'
        '    "selectivity_risks": ["chemoselectivity, regioselectivity, steric, protecting-group, or side-reaction risks"],\n'
        '    "safety_notes": ["hazards and scale-up concerns"],\n'
        '    "validation_plan": ["literature/reaction database/vendor checks before execution"],\n'
        '    "confidence": "low|medium|high"\n'
        "  }}\n"
        "}\n"
        "Explain human-readable chemistry with scientific caution. "
        "You may propose plausible reaction conditions and yield ranges only as hypotheses; never present them as validated experimental facts unless the route data explicitly contains evidence. "
        "If a reaction class is '0.0 Unrecognized', do not repeat it as the reaction_type. "
        "Infer a cautious reaction family from the template/mapped reaction, mark confidence low or medium when evidence is weak, and include literature/database validation steps.\n\n"
        "Route data:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _first_json_object(text: str) -> str:
    """Return the first balanced ``{...}`` block in `text`, ignoring braces in strings."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : index + 1]
    raise ValueError("no JSON object found in the LLM response")


def parse_llm_annotations(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Parse an LLM annotation response, accepting raw JSON or fenced JSON text.

    Conversation models routinely wrap JSON in prose ("Sure, here is...") or in a
    fenced block that does not start at character zero, so a bare `json.loads` is
    not enough. Raises `ValueError` when no JSON can be recovered.
    """
    if isinstance(raw, dict):
        if any(key in raw for key in ("routes", "molecules", "reactions")):
            return raw
        for key in ("output_text", "text", "content"):
            value = raw.get(key)
            if isinstance(value, str):
                return parse_llm_annotations(value)
        tool_use = raw.get("tool_use")
        if isinstance(tool_use, list) and tool_use:
            first = tool_use[0]
            if isinstance(first, dict) and isinstance(first.get("input"), dict):
                return first["input"]
        return raw
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_first_json_object(text))


def annotate_routes_with_llm(
    routes: Iterable[dict[str, Any]],
    llm: Any,
    target_smiles: str | None = None,
    max_routes: int = 8,
) -> dict[str, Any]:
    """Call the configured conversation LLM and parse route annotations.

    Never raises on a malformed model reply: an unparseable response warns and
    yields `{}`, which the renderers degrade to their no-annotation readout.
    """
    if llm is None:
        raise ValueError("llm callable is required, for example host.llm")
    prompt = build_llm_annotation_prompt(
        routes, target_smiles=target_smiles, max_routes=max_routes
    )
    try:
        raw = llm({"prompt": prompt, "max_tokens": 4096, "temperature": 0.2})
    except TypeError as dict_error:
        # The callable may only accept a positional prompt. If it rejects that
        # too, the original error is the honest one to surface.
        try:
            raw = llm(prompt)
        except TypeError:
            raise dict_error
    try:
        annotations = parse_llm_annotations(raw)
    except (ValueError, TypeError) as error:
        warnings.warn(
            f"LLM annotation response was not valid JSON ({error}); "
            "rendering without LLM annotations.",
            RuntimeWarning,
            stacklevel=2,
        )
        return {}
    return annotations if isinstance(annotations, dict) else {}


def _route_candidates(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("routes", "trees", "reaction_trees", "solutions", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    candidates: list[Any] = []
    for value in payload.values():
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) for item in value)
        ):
            candidates.extend(value)
    if candidates:
        return candidates
    if _looks_like_route(payload):
        return [payload]
    return []


def _normalize_route(candidate: Any, rank: int) -> dict[str, Any]:
    route = candidate if isinstance(candidate, dict) else {"tree": candidate}
    tree = _extract_tree(route)
    score = _extract_score(route)
    solved = _extract_solved(route, tree)
    materials = sorted(_collect_starting_materials(tree))
    steps = _count_reactions(tree)
    if steps == 0:
        steps = _as_int(
            route.get("steps") or route.get("length") or route.get("depth"), default=0
        )
    return {
        "rank": rank,
        "score": score,
        "solved": solved,
        "steps": steps,
        "starting_materials": materials,
        "tree": tree,
        "raw": route,
    }


def _render_route_analysis(
    route: dict[str, Any], annotations: dict[str, Any] | None = None
) -> str:
    annotation = _annotation_record_for_route(annotations or {}, route)
    if isinstance(annotation, dict) and annotation:
        fields = [
            ("Route strategy", _annotation_value(annotation, "route_strategy")),
            ("Key disconnections", _annotation_value(annotation, "key_disconnections")),
            ("Reaction sequence", _annotation_value(annotation, "reaction_sequence")),
            (
                "Conditions strategy",
                _annotation_value(annotation, "conditions_strategy"),
            ),
            ("Yield outlook", _annotation_value(annotation, "yield_outlook")),
            ("Route risks", _annotation_value(annotation, "route_risks", "risks")),
            (
                "Recommended next steps",
                _annotation_value(annotation, "recommended_next_steps", "next_steps"),
            ),
            ("Chemist verdict", _annotation_value(annotation, "chemist_verdict")),
        ]
        title = "LLM Route Analysis"
    else:
        fields = [
            ("Route strategy", _route_rationale(route)),
            (
                "Conditions strategy",
                "No route-level LLM annotation was supplied; verify reaction conditions through literature, internal ELN data, or condition-prediction tools.",
            ),
            (
                "Recommended next steps",
                [
                    "Run configured LLM annotation with host.llm before final review.",
                    "Check terminal precursor vendors, exact substructure precedent, and reaction-condition evidence.",
                ],
            ),
        ]
        title = "Route Planning Readout"

    rows = []
    for label, value in fields:
        if value in (None, "", [], {}):
            continue
        rows.append(
            "\n".join(
                [
                    '<div class="analysis-field">',
                    f"<dt>{html.escape(label)}</dt>",
                    f"<dd>{_render_rich_value_html(value)}</dd>",
                    "</div>",
                ]
            )
        )
    if not rows:
        return ""
    return "\n".join(
        [
            '<section class="route-analysis">',
            f"<h3>{title}</h3>",
            '<dl class="analysis-grid">',
            "\n".join(rows),
            "</dl>",
            "</section>",
        ]
    )


def _render_rich_value_html(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, list):
        return (
            "<ul>"
            + "".join(f"<li>{_render_rich_value_html(item)}</li>" for item in value)
            + "</ul>"
        )
    if isinstance(value, dict):
        rows = []
        for key, nested in value.items():
            rows.append(
                f"<dt>{html.escape(str(key))}</dt><dd>{_render_rich_value_html(nested)}</dd>"
            )
        return '<dl class="mini-kv">' + "".join(rows) + "</dl>"
    return html.escape(str(value))


def _render_molecule_briefs_panel(
    briefs: list[dict[str, Any]], annotations: dict[str, Any] | None = None
) -> str:
    if not briefs:
        return ""
    annotations = annotations or {}
    cards = []
    for brief in briefs:
        annotation = _annotation_for_molecule(annotations, str(brief["smiles"]))
        note = annotation or _molecule_panel_note(brief)
        structure_sources = _molecule_structure_sources(str(brief["smiles"]))
        alt = f'alt="Structure of {html.escape(str(brief["smiles"]))}"'
        img = f'<img class="mol-structure" src="{html.escape(structure_sources["primary"])}" '
        if structure_sources["fallback"]:
            img += (
                f'data-fallback-src="{html.escape(structure_sources["fallback"])}" '
                "onerror=\"this.onerror=null;this.src=this.dataset.fallbackSrc;this.classList.add('structure-fallback');\" "
            )
        cards.append(
            "\n".join(
                [
                    '<article class="molecule-card">',
                    f"<h3><code>{html.escape(str(brief['smiles']))}</code></h3>",
                    '<div class="structure-frame">',
                    img + alt + ">",
                    "</div>",
                    "<dl>",
                    f"<dt>Role</dt><dd>{html.escape(str(brief['role']))}</dd>",
                    f"<dt>Routes</dt><dd>{html.escape(', '.join(str(rank) for rank in brief['route_ranks']))}</dd>",
                    f"<dt>Stock</dt><dd>{html.escape(str(brief['stock_status']))}</dd>",
                    f"<dt>Interpretation</dt><dd>{html.escape(str(brief['interpretation']))}</dd>",
                    f"<dt>Annotation</dt><dd>{html.escape(note)}</dd>",
                    f'<dt>Query</dt><dd><a href="{html.escape(str(brief["pubchem_url"]))}">PubChem</a></dd>',
                    "</dl>",
                    "</article>",
                ]
            )
        )
    return "\n".join(
        [
            '<section class="panel">',
            "<h2>Molecule Briefs</h2>",
            '<div class="molecule-grid">',
            "\n".join(cards),
            "</div>",
            "</section>",
        ]
    )


def _render_interactive_andor_tree(
    routes: list[dict[str, Any]],
    target_smiles: str | None = None,
    annotations: dict[str, Any] | None = None,
) -> str:
    payload = _interactive_andor_payload(
        routes, target_smiles=target_smiles, annotations=annotations
    )
    # Escaping only "</" would still let a "<!--<script" sequence in LLM-authored
    # annotation text push the parser into the double-escaped state, where the
    # closing </script> no longer terminates the block. Escape < > & outright;
    # in JSON these only ever occur inside strings, and JSON.parse restores them.
    data_json = (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return "\n".join(
        [
            '<section class="panel andor-panel">',
            "<h2>Interactive Retrosynthesis Knowledge Graph</h2>",
            '<div class="andor-toolbar">',
            '<button type="button" id="andor-expand">Expand all</button>',
            '<button type="button" id="andor-collapse">Collapse reactions</button>',
            '<button type="button" id="andor-reset">Reset view</button>',
            '<span class="note">Merged molecule/reaction graph. Click a node for details and neighbor highlighting; double-click to collapse related descendants. Drag to pan; scroll to zoom.</span>',
            "</div>",
            '<div class="andor-shell">',
            '<div class="andor-canvas"><svg id="andor-svg" class="andor-svg" role="img" aria-label="Interactive retrosynthesis knowledge graph"></svg></div>',
            '<aside id="andor-detail" class="andor-detail"><h3>Node details</h3><p class="muted">Select a molecule or reaction node.</p></aside>',
            "</div>",
            f'<script type="application/json" id="andor-data">{data_json}</script>',
            f"<script>{_ANDOR_TREE_SCRIPT}</script>",
            "</section>",
        ]
    )


def _interactive_andor_payload(
    routes: list[dict[str, Any]],
    target_smiles: str | None = None,
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], set[str]] = {}
    annotations = annotations or {}

    def add_node(node: dict[str, Any]) -> dict[str, Any]:
        existing = nodes.get(node["id"])
        if existing:
            existing["routes"] = sorted(
                set(existing.get("routes", [])) | set(node.get("routes", [])), key=str
            )
            existing["depth"] = min(
                existing.get("depth", node.get("depth", 0)), node.get("depth", 0)
            )
            return existing
        nodes[node["id"]] = node
        return node

    root = add_node(
        {
            "id": "root",
            "kind": "root",
            "className": "target",
            "label": _short_label(target_smiles or "route forest", 34),
            "meta": "OR target root",
            "depth": 0,
            "routes": [route.get("rank", "?") for route in routes],
            "details": {
                "Type": "Merged retrosynthesis knowledge graph",
                "Target": target_smiles or "not specified",
                "Routes shown": str(len(routes)),
                "Search graph note": (
                    "This knowledge-graph view merges identical molecule nodes and reaction hypotheses across AiZynthFinder exported route trees. "
                    "It preserves AND-OR route semantics, while the complete internal MCTS visit graph still requires a backend checkpoint/search graph export."
                ),
            },
        }
    )

    def add_edge(source: str, target: str, route_rank: Any) -> None:
        edges.setdefault((source, target), set()).add(str(route_rank))

    def molecule_node(
        item: dict[str, Any], route_rank: Any, depth: int, is_target: bool = False
    ) -> dict[str, Any]:
        smiles = _node_smiles(item) or _node_display_label(item)
        # This walk starts the route tree at depth 1 (depth 0 is the synthetic
        # root), so the target has to be flagged explicitly rather than by depth.
        role = _molecule_role(item, depth, target_smiles or "", is_target=is_target)
        class_name = _node_visual_class(
            item, depth, bool(_children(item)), is_target=(role == "target")
        )
        annotation = _annotation_for_molecule(annotations, smiles)
        structure_sources = _molecule_structure_sources(smiles, width=240, height=160)
        node: dict[str, Any] = {
            "id": "mol:" + smiles,
            "kind": "molecule",
            "className": class_name,
            "label": _short_label(smiles, 34),
            "meta": role,
            "smiles": smiles,
            "structureSrc": structure_sources["primary"],
            "depth": depth,
            "routes": [route_rank],
            "details": {
                "Type": "Molecule",
                "Role": role,
                "SMILES": smiles,
                "Stock status": _stock_status_value(item),
                "Routes": str(route_rank),
                "Annotation": annotation
                or _molecule_detail_note(role, _stock_status_value(item)),
                "PubChem": build_pubchem_query_url(smiles),
            },
        }
        if structure_sources["fallback"]:
            node["structureFallbackSrc"] = structure_sources["fallback"]
        return add_node(node)

    def reaction_node(
        item: dict[str, Any], parent_id: str, route_rank: Any, depth: int
    ) -> dict[str, Any]:
        reaction_info = _reaction_info(item)
        child_smiles = sorted(
            _node_smiles(child) or _node_display_label(child)
            for child in _children(item)
            if isinstance(child, dict)
        )
        key = "|".join(
            [
                parent_id,
                str(reaction_info["details"].get("Template", "")),
                ".".join(child_smiles),
            ]
        )
        raw_details = dict(reaction_info["details"])
        annotation = _annotation_record_for_reaction(annotations, raw_details)
        annotation_key = _reaction_annotation_key(raw_details)
        backend_class = str(raw_details.pop("Reaction class", ""))
        llm_type = _annotation_reaction_type(annotation)
        display_type = llm_type or _display_reaction_type(backend_class, raw_details)
        details = {
            "Type": "Reaction",
            "Reaction type": display_type,
            "Backend taxonomy": _backend_taxonomy_note(backend_class),
            **{key: value for key, value in raw_details.items() if key != "Type"},
        }
        if llm_type:
            details["LLM confidence"] = str(
                annotation.get("confidence") or "not specified"
            )
        details["Reaction description"] = _annotation_description(
            annotation
        ) or _reaction_fallback_description(backend_class, details)
        mechanistic_note = _annotation_value(
            annotation, "mechanistic_rationale", "mechanism", "rationale"
        )
        if mechanistic_note:
            details["Mechanistic rationale"] = mechanistic_note
        bond_changes = _annotation_value(annotation, "bond_changes", "bond_change")
        if bond_changes:
            details["Bond changes"] = bond_changes
        conditions = _annotation_value(
            annotation,
            "suggested_conditions",
            "likely_conditions_or_caveat",
            "condition_caveat",
            "conditions",
        )
        if conditions:
            details["Suggested conditions"] = conditions
        yield_range = _annotation_value(
            annotation, "expected_yield_range", "yield_estimate", "possible_yield"
        )
        if yield_range:
            details["Expected yield"] = yield_range
        yield_rationale = _annotation_value(
            annotation, "yield_rationale", "yield_caveat"
        )
        if yield_rationale:
            details["Yield rationale"] = yield_rationale
        selectivity_risks = _annotation_value(
            annotation, "selectivity_risks", "risks", "risk_notes"
        )
        if selectivity_risks:
            details["Selectivity / risk"] = selectivity_risks
        safety_notes = _annotation_value(annotation, "safety_notes", "safety")
        if safety_notes:
            details["Safety notes"] = safety_notes
        validation_plan = _annotation_value(
            annotation, "validation_plan", "literature_queries", "validation"
        )
        if validation_plan:
            details["Validation plan"] = validation_plan
        details["Annotation key"] = annotation_key
        note = _unrecognized_reaction_note(backend_class)
        if note:
            details["Backend caveat"] = note
        label = display_type
        return add_node(
            {
                "id": "rxn:" + _stable_id(key),
                "kind": "reaction",
                "className": "reaction",
                "label": _short_label(label, 34),
                "meta": reaction_info["meta"],
                "depth": depth,
                "routes": [route_rank],
                "details": details,
            }
        )

    def walk(
        item: Any, parent_id: str, route_rank: Any, depth: int, is_root: bool = False
    ) -> None:
        if not isinstance(item, dict):
            return
        if _is_reaction_node(item):
            rxn = reaction_node(item, parent_id, route_rank, depth)
            add_edge(parent_id, rxn["id"], route_rank)
            for child in _children(item):
                walk(child, rxn["id"], route_rank, depth + 1)
            return
        mol = molecule_node(item, route_rank, depth, is_target=is_root)
        add_edge(parent_id, mol["id"], route_rank)
        for child in _children(item):
            walk(child, mol["id"], route_rank, depth + 1)

    for route in routes:
        walk(route.get("tree"), root["id"], route.get("rank", "?"), 1, is_root=True)

    return {
        "graph": {
            "nodes": list(nodes.values()),
            "edges": [
                {
                    "source": source,
                    "target": target,
                    "routes": sorted(route_ids, key=str),
                }
                for (source, target), route_ids in edges.items()
            ],
        }
    }


def _reaction_info(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    classification = (
        metadata.get("classification")
        or metadata.get("name")
        or node.get("classification")
        or "Unclassified reaction"
    )
    policy_name = metadata.get("policy_name") or node.get("policy_name") or "policy n/a"
    probability = _as_float(metadata.get("policy_probability"))
    probability_text = f"{probability:.3f}" if probability is not None else "n/a"
    template = (
        metadata.get("template")
        or node.get("template")
        or node.get("smarts")
        or node.get("reaction_smiles")
        or node.get("smiles")
        or "not available"
    )
    mapped = (
        metadata.get("mapped_reaction_smiles")
        or node.get("mapped_reaction_smiles")
        or ""
    )
    conditions = _reaction_conditions_note(node)
    return {
        "label": _short_label(str(classification), 34),
        "meta": f"AND reaction | {policy_name} p={probability_text}",
        "details": {
            "Type": "Reaction",
            "Reaction class": str(classification),
            "Policy": str(policy_name),
            "Policy probability": probability_text,
            "Template": str(template),
            "Mapped reaction": str(mapped),
            "Conditions": conditions,
            "Condition caveat": (
                "AiZynthFinder predicts disconnections, not validated lab conditions. "
                "Use literature, ELN/internal reaction DB, ASKCOS condition prediction, or manual chemist review."
            ),
        },
    }


def _reaction_conditions_note(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    candidates = []
    for source in (node, metadata):
        for key in (
            "conditions",
            "reaction_conditions",
            "solvent",
            "reagent",
            "catalyst",
            "temperature",
        ):
            value = source.get(key)
            if value:
                candidates.append(f"{key}: {value}")
    if candidates:
        return "; ".join(str(item) for item in candidates)
    return "Not predicted in this AiZynthFinder route export."


def _stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _annotation_for_molecule(annotations: dict[str, Any], smiles: str) -> str:
    item = _annotation_record_for_molecule(annotations, smiles)
    return _annotation_description(item)


def _annotation_record_for_route(
    annotations: dict[str, Any], route: dict[str, Any]
) -> dict[str, Any] | str:
    routes = annotations.get("routes") if isinstance(annotations, dict) else None
    rank = route.get("rank", "")
    key_candidates = [
        str(rank),
        f"Route {rank}",
        f"route_{rank}",
        f"rank_{rank}",
    ]
    if isinstance(routes, dict):
        for key in key_candidates:
            item = routes.get(key)
            if isinstance(item, (dict, str)):
                return item
    if isinstance(routes, list):
        for item in routes:
            if not isinstance(item, dict):
                continue
            item_rank = item.get("rank") or item.get("route") or item.get("route_rank")
            if str(item_rank) == str(rank):
                return item
    return {}


def _annotation_record_for_molecule(
    annotations: dict[str, Any], smiles: str
) -> dict[str, Any] | str:
    molecules = annotations.get("molecules") if isinstance(annotations, dict) else None
    if isinstance(molecules, dict):
        item = molecules.get(smiles)
        if isinstance(item, (dict, str)):
            return item
    return {}


def _annotation_record_for_reaction(
    annotations: dict[str, Any], details: dict[str, Any]
) -> dict[str, Any] | str:
    reactions = annotations.get("reactions") if isinstance(annotations, dict) else None
    if not isinstance(reactions, dict):
        return {}
    keys = _reaction_annotation_keys(details)
    for key in keys:
        item = reactions.get(key)
        if isinstance(item, (dict, str)):
            return item
    return {}


def _reaction_annotation_key(details: dict[str, Any]) -> str:
    core = {
        "mapped_reaction": str(details.get("Mapped reaction") or ""),
        "template": str(details.get("Template") or ""),
        "backend_class": str(
            details.get("Backend class") or details.get("Reaction class") or ""
        ),
    }
    return "rxn:" + _stable_id(json.dumps(core, ensure_ascii=False, sort_keys=True))


def _reaction_annotation_keys(details: dict[str, Any]) -> list[str]:
    keys = [
        str(details.get("Annotation key") or ""),
        _reaction_annotation_key(details),
        str(details.get("Mapped reaction") or ""),
        str(details.get("Template") or ""),
        str(details.get("Reaction class") or ""),
        str(details.get("Backend class") or ""),
    ]
    return [key for key in keys if key]


def _annotation_description(item: dict[str, Any] | str) -> str:
    if isinstance(item, dict):
        return str(
            item.get("description")
            or item.get("summary")
            or item.get("route_role_note")
            or ""
        )
    if isinstance(item, str):
        return item
    return ""


def _annotation_reaction_type(item: dict[str, Any] | str) -> str:
    if not isinstance(item, dict):
        return ""
    reaction_type = str(
        item.get("reaction_type")
        or item.get("type")
        or item.get("reaction_family")
        or ""
    ).strip()
    if _is_unrecognized_class(reaction_type):
        return ""
    return reaction_type


def _annotation_value(item: dict[str, Any] | str, *keys: str) -> Any:
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return ""


def _display_reaction_type(classification: str, details: dict[str, Any]) -> str:
    if _is_unrecognized_class(classification):
        template = str(details.get("Template") or "").lower()
        mapped = str(details.get("Mapped reaction") or "").lower()
        evidence = template + " " + mapped
        if "ester" in evidence or "acyl" in evidence or "c(=o)" in evidence:
            return "Template-derived acyl substitution"
        if "amide" in evidence or "n-" in evidence:
            return "Template-derived amide transformation"
        if "aryl" in evidence or "c1" in evidence:
            return "Template-derived aryl functionalization"
        return "Template-derived disconnection"
    if classification:
        return classification
    return "Unclassified reaction"


def _molecule_panel_note(brief: dict[str, Any]) -> str:
    return (
        f"{brief['role']} molecule in the displayed route forest. "
        f"Stock status is {brief['stock_status']}; confirm identity, purity, vendor/literature precedent, "
        "and functional-group compatibility before treating the route as actionable."
    )


def _molecule_detail_note(role: str, stock_status: str) -> str:
    return (
        f"{role} molecule. Stock status is {stock_status}; use the PubChem link and vendor/literature "
        "lookup to verify identity and practical availability."
    )


def _reaction_fallback_description(backend_class: str, details: dict[str, Any]) -> str:
    template = str(details.get("Template") or "not available")
    if _is_unrecognized_class(backend_class):
        return (
            "The backend template is not mapped to a reliable human reaction-name taxonomy. "
            f"Interpret the disconnection from the SMARTS/template evidence ({template}) and treat the reaction type as tentative "
            "until LLM, literature, or chemist review confirms it."
        )
    return (
        f"Backend classified this as {backend_class}. Use the template evidence ({template}) and policy probability "
        "as planning support, then verify conditions and precedent separately."
    )


def _backend_taxonomy_note(classification: str) -> str:
    if _is_unrecognized_class(classification):
        return "No reliable backend taxonomy; use the LLM/SMARTS interpretation above."
    return classification or "No backend taxonomy available."


def _is_unrecognized_class(classification: str) -> bool:
    text = str(classification or "").lower()
    return (
        "unrecognized" in text
        or "unclassified" in text
        or text in {"0", "0.0", "unknown", "n/a"}
    )


def _unrecognized_reaction_note(classification: str) -> str:
    if not _is_unrecognized_class(classification):
        return ""
    return (
        "The backend did not provide a reliable human-readable reaction-name taxonomy for this template. "
        "This does not mean the disconnection is invalid; inspect the SMARTS/mapped reaction "
        "and add an LLM/literature annotation."
    )


def _rdkit_molecule_svg(smiles: str | None, width: int, height: int) -> str | None:
    if not smiles:
        return None
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Draw  # type: ignore
    except ImportError:
        return None

    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None
    try:
        Chem.rdDepictor.Compute2DCoords(mol)
    except Exception:
        pass
    drawer = Draw.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    options.bondLineWidth = 2.6
    options.minFontSize = 15
    options.maxFontSize = 22
    options.annotationFontScale = 0.9
    options.padding = 0.08
    options.fixedBondLength = 28
    try:
        options.clearBackground = False
    except AttributeError:
        pass
    try:
        options.useBWAtomPalette()
    except AttributeError:
        pass
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    svg = svg.replace("svg:", "")
    svg = re.sub(
        r"<rect[^>]*(?:fill:\s*#?fff(?:fff)?|fill=['\"]#?fff(?:fff)?['\"])[^>]*/>",
        "",
        svg,
        flags=re.IGNORECASE,
    )
    return svg


def _extract_tree(route: dict[str, Any]) -> Any:
    for key in ("tree", "reaction_tree", "route", "root", "nodes"):
        if key in route:
            return route[key]
    return route


def _extract_score(route: dict[str, Any]) -> float | None:
    for key in ("score", "total_score", "route_score", "probability", "confidence"):
        if key in route:
            return _as_float(route[key])
    scores = route.get("scores")
    if isinstance(scores, dict):
        for key in ("state score", "score", "total_score", "probability", "confidence"):
            if key in scores:
                return _as_float(scores[key])
        numeric = [_as_float(value) for value in scores.values()]
        numeric = [value for value in numeric if value is not None]
        if numeric:
            return max(numeric)
    return None


def _extract_solved(route: dict[str, Any], tree: Any) -> bool:
    metadata = route.get("metadata")
    if isinstance(metadata, dict):
        for key in ("solved", "is_solved", "all_precursors_in_stock"):
            if key in metadata:
                return bool(metadata[key])
    for key in ("solved", "is_solved", "all_precursors_in_stock"):
        if key in route:
            return bool(route[key])
    # A target root can have in_stock=False even when all leaves are stock
    # precursors, so only use in_stock as a direct answer for leaf-like routes.
    if "in_stock" in route and not _children(route):
        return bool(route["in_stock"])
    materials = _collect_starting_materials(tree)
    return bool(materials) and _all_leaves_in_stock(tree)


def _collect_starting_materials(node: Any) -> set[str]:
    materials: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        children = _children(item)
        smiles = _node_smiles(item)
        if smiles and not children and _is_molecule_node(item):
            materials.add(smiles)
        for child in children:
            walk(child)

    walk(node)
    return materials


def _iter_molecule_nodes(node: Any) -> Iterable[tuple[dict[str, Any], int]]:
    def walk(item: Any, depth: int) -> Iterable[tuple[dict[str, Any], int]]:
        if isinstance(item, list):
            for child in item:
                yield from walk(child, depth)
            return
        if not isinstance(item, dict):
            return
        if _is_molecule_node(item):
            yield item, depth
        for child in _children(item):
            yield from walk(child, depth + 1)

    yield from walk(node, 0)


@functools.lru_cache(maxsize=4096)
def _canonical_key(smiles: str) -> str:
    """Canonical SMILES used for identity comparison; the raw string without RDKit."""
    value = (smiles or "").strip()
    if not value:
        return ""
    try:
        from rdkit import Chem  # type: ignore
    except ImportError:
        return value
    mol = Chem.MolFromSmiles(value)
    if mol is None:
        return value
    return Chem.MolToSmiles(mol, canonical=True)


def _molecule_role(
    node: dict[str, Any], depth: int, target_smiles: str, is_target: bool = False
) -> str:
    smiles = _node_smiles(node) or ""
    children = _children(node)
    if is_target or depth == 0:
        return "target"
    if target_smiles and _canonical_key(smiles) == _canonical_key(target_smiles):
        return "target"
    if not children and (
        node.get("in_stock") or node.get("is_in_stock") or node.get("stock")
    ):
        return "stock precursor"
    if not children:
        return "unresolved precursor"
    return "intermediate"


def _role_sort_key(role: str) -> tuple[int, str]:
    order = {
        "target": 0,
        "intermediate": 1,
        "stock precursor": 2,
        "unresolved precursor": 3,
    }
    first = str(role).split(", ")[0]
    return (order.get(first, 9), str(role))


def _stock_status_value(node: dict[str, Any]) -> str:
    if node.get("in_stock") or node.get("is_in_stock") or node.get("stock"):
        return "in stock"
    if not _children(node):
        return "not in stock"
    return "not a terminal precursor"


def _summarize_stock(values: Iterable[str]) -> str:
    unique = set(values)
    if "in stock" in unique and len(unique) == 1:
        return "in stock"
    if "in stock" in unique:
        return "mixed across routes"
    if "not in stock" in unique:
        return "not in stock"
    return "not a terminal precursor"


def _molecule_interpretation(role: str, stock_status: str) -> str:
    if "target" in role:
        return "Target molecule being disconnected into simpler purchasable or stock precursors."
    if "intermediate" in role:
        return "Predicted synthetic intermediate; inspect functional groups, stereochemistry, and whether downstream disconnections are plausible."
    if "stock precursor" in role or stock_status == "in stock":
        return "Terminal precursor found in the selected stock database; verify vendor, purity, price, and regulatory constraints."
    if "unresolved precursor" in role:
        return "Terminal precursor not confirmed in stock; route may need another disconnection, alternate stock, or manual substitution."
    return "Route molecule; query external chemistry databases before treating it as available or validated."


def _route_rationale(route: dict[str, Any]) -> str:
    steps = route.get("steps")
    materials = route.get("starting_materials") or []
    score = _format_score(route.get("score"))
    if route.get("solved"):
        status = "The route reaches stock/purchasable terminal precursors"
    else:
        status = "The route does not fully reach confirmed stock precursors"
    if materials:
        precursor_text = ", ".join(str(material) for material in materials[:3])
        if len(materials) > 3:
            precursor_text += f", plus {len(materials) - 3} more"
    else:
        precursor_text = "no detected terminal precursors"
    return (
        f"{status}; prioritize it by score {score}, estimated {steps} step(s), "
        f"and terminal precursor set ({precursor_text}). Treat this as a planning "
        "hypothesis until reaction conditions and literature precedent are checked."
    )


def _count_reactions(node: Any) -> int:
    count = 0

    def walk(item: Any) -> None:
        nonlocal count
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        if _is_reaction_node(item):
            count += 1
        for child in _children(item):
            walk(child)

    walk(node)
    return count


def _all_leaves_in_stock(node: Any) -> bool:
    leaves: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        children = _children(item)
        if _is_molecule_node(item) and not children:
            leaves.append(item)
        for child in children:
            walk(child)

    walk(node)
    if not leaves:
        return False
    return all(
        bool(leaf.get("in_stock") or leaf.get("is_in_stock") or leaf.get("stock"))
        for leaf in leaves
    )


def _render_route_table(routes: list[dict[str, Any]]) -> str:
    if not routes:
        return '<p class="empty">No ranked routes available.</p>'
    rows = []
    for route in routes:
        materials = route.get("starting_materials") or []
        material_text = ", ".join(str(material) for material in materials[:4])
        if len(materials) > 4:
            material_text += f", +{len(materials) - 4} more"
        solved_class = "ok" if route.get("solved") else "warn"
        rows.append(
            "\n".join(
                [
                    "<tr>",
                    f"<td>{html.escape(str(route.get('rank', '?')))}</td>",
                    f'<td><span class="pill {solved_class}">{"solved" if route.get("solved") else "unsolved"}</span></td>',
                    f"<td>{_format_score(route.get('score'))}</td>",
                    f"<td>{html.escape(str(route.get('steps')))}</td>",
                    f"<td>{html.escape(str(len(materials)))}</td>",
                    f"<td>{html.escape(material_text or 'not detected')}</td>",
                    "</tr>",
                ]
            )
        )
    return "\n".join(
        [
            "<table>",
            "<thead><tr><th>Rank</th><th>Status</th><th>Score</th><th>Steps</th><th>Precursors</th><th>Starting materials</th></tr></thead>",
            "<tbody>",
            "\n".join(rows),
            "</tbody>",
            "</table>",
        ]
    )


def _material_chip(material: Any) -> str:
    return f'<span class="chip"><code>{html.escape(str(material))}</code></span>'


def _render_svg_tree(node: Any, route_id: Any) -> str:
    nodes, edges = _layout_svg_tree(node)
    if not nodes:
        return '<p class="empty">No tree data detected.</p>'

    width = max(item["x"] for item in nodes) + 170
    height = max(item["y"] for item in nodes) + 70
    edge_svg = []
    for parent, child in edges:
        x1 = parent["x"] + 115
        y1 = parent["y"]
        x2 = child["x"] - 115
        y2 = child["y"]
        mid = (x1 + x2) / 2
        edge_svg.append(
            f'<path class="edge" d="M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" />'
        )

    node_svg = [_svg_node(item) for item in nodes]
    return "\n".join(
        [
            f'<svg class="route-svg" role="img" aria-label="Route {html.escape(str(route_id))} tree" viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg">',
            "<g>",
            "\n".join(edge_svg),
            "\n".join(node_svg),
            "</g>",
            "</svg>",
        ]
    )


def _layout_svg_tree(
    node: Any,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[dict[str, Any], dict[str, Any]]] = []
    leaf_index = 0
    next_id = 0

    def build(
        item: Any, depth: int, parent: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        nonlocal leaf_index, next_id
        if isinstance(item, list):
            synthetic = {
                "id": "root",
                "x": 120,
                "y": 0,
                "label": "route set",
                "meta": "root",
                "class": "target",
                "full": "route set",
            }
            child_nodes = [
                child
                for child in (build(child, depth + 1, synthetic) for child in item)
                if child
            ]
            if not child_nodes:
                return None
            synthetic["y"] = sum(child["y"] for child in child_nodes) / len(child_nodes)
            nodes.append(synthetic)
            if parent:
                edges.append((parent, synthetic))
            return synthetic
        if not isinstance(item, dict):
            return None

        children = _children(item)
        current_id = f"n{next_id}"
        next_id += 1
        rendered_children = [
            child for child in (build(child, depth + 1) for child in children) if child
        ]
        if rendered_children:
            y = sum(child["y"] for child in rendered_children) / len(rendered_children)
        else:
            y = 86 + leaf_index * 140
            leaf_index += 1

        structure_sources = (
            _molecule_structure_sources(_node_smiles(item), width=180, height=110)
            if _is_molecule_node(item) and _node_smiles(item)
            else None
        )
        layout_node = {
            "id": current_id,
            "x": 140 + depth * 300,
            "y": y,
            "label": _short_label(_node_display_label(item), 32),
            "meta": _node_meta_label(item, depth),
            "class": _node_visual_class(item, depth, bool(children)),
            "full": _node_display_label(item),
            "structure_src": structure_sources["primary"]
            if structure_sources
            else None,
            "structure_fallback_src": (
                structure_sources["fallback"] if structure_sources else None
            ),
        }
        nodes.append(layout_node)
        if parent:
            edges.append((parent, layout_node))
        for child in rendered_children:
            edges.append((layout_node, child))
        return layout_node

    build(node, 0)
    return nodes, edges


def _svg_node(item: dict[str, Any]) -> str:
    has_structure = bool(item.get("structure_src"))
    node_height = 118 if has_structure else 62
    x = item["x"] - 115
    y = item["y"] - node_height / 2
    lines = _split_label(item["label"], max_len=24, max_lines=2)
    text_lines = []
    if has_structure:
        fallback = html.escape(str(item.get("structure_fallback_src") or ""))
        well = (
            f'<rect class="structure-well" x="{item["x"] - 90:.1f}" y="{item["y"] - 50:.1f}" '
            'width="180" height="78" rx="6" />'
        )
        image = f'<image href="{html.escape(str(item["structure_src"]))}" '
        if fallback:
            image += (
                f'data-fallback-src="{fallback}" '
                "onerror=\"this.onerror=null;this.setAttribute('href', this.dataset.fallbackSrc);this.classList.add('structure-fallback');\" "
            )
        image += (
            f'x="{item["x"] - 84:.1f}" y="{item["y"] - 49:.1f}" '
            'width="168" height="74" preserveAspectRatio="xMidYMid meet" />'
        )
        start_y = item["y"] + 37
    else:
        well = ""
        image = ""
        start_y = item["y"] - (6 if len(lines) == 1 else 13)
    for idx, line in enumerate(lines):
        text_lines.append(
            f'<text x="{item["x"]:.1f}" y="{start_y + idx * 13:.1f}" text-anchor="middle">{html.escape(line)}</text>'
        )
    text_lines.append(
        f'<text class="meta" x="{item["x"]:.1f}" y="{item["y"] + node_height / 2 - 9:.1f}" text-anchor="middle">{html.escape(str(item["meta"]))}</text>'
    )
    return "\n".join(
        [
            f'<g class="node {html.escape(str(item["class"]))}">',
            f"<title>{html.escape(str(item['full']))}</title>",
            f'<rect x="{x:.1f}" y="{y:.1f}" width="230" height="{node_height}" rx="8" />',
            well,
            image,
            "\n".join(text_lines),
            "</g>",
        ]
    )


def _render_outline_tree(node: Any) -> str:
    if node is None:
        return "<p>No tree data detected.</p>"

    def label(item: Any) -> str:
        if not isinstance(item, dict):
            return html.escape(str(item))
        smiles = _node_smiles(item)
        kind = "reaction" if _is_reaction_node(item) else "molecule"
        text = (
            _node_display_label(item)
            if _is_reaction_node(item)
            else (
                smiles
                or item.get("name")
                or item.get("metadata", {}).get("name")
                or kind
            )
        )
        stock = (
            " stock"
            if item.get("in_stock") or item.get("is_in_stock") or item.get("stock")
            else ""
        )
        return f'<code>{html.escape(str(text))}</code><span class="tag">{kind}{stock}</span>'

    def walk(item: Any) -> str:
        if isinstance(item, list):
            return (
                '<ul class="tree">'
                + "".join(f"<li>{walk(child)}</li>" for child in item)
                + "</ul>"
            )
        children = _children(item) if isinstance(item, dict) else []
        if not children:
            return label(item)
        return (
            label(item)
            + "<ul>"
            + "".join(f"<li>{walk(child)}</li>" for child in children)
            + "</ul>"
        )

    return f'<div class="tree">{walk(node)}</div>'


def _node_display_label(node: dict[str, Any]) -> str:
    if _is_reaction_node(node):
        metadata = node.get("metadata")
        if isinstance(metadata, dict):
            for key in ("classification", "name", "template_code"):
                value = metadata.get(key)
                if value and not _is_unrecognized_class(str(value)):
                    return f"reaction {value}"
        template = node.get("template") or node.get("smarts") or node.get("smiles")
        if template:
            return f"reaction {_short_label(str(template), 42)}"
        if isinstance(metadata, dict) and metadata.get("policy_name"):
            return f"reaction template from {metadata['policy_name']}"
        return str(node.get("reaction_smiles") or "template-derived reaction")
    return str(
        _node_smiles(node)
        or node.get("name")
        or node.get("inchi_key")
        or node.get("metadata", {}).get("name")
        or "molecule"
    )


def _node_meta_label(node: dict[str, Any], depth: int) -> str:
    if _is_reaction_node(node):
        metadata = node.get("metadata")
        if isinstance(metadata, dict):
            policy = metadata.get("policy_name")
            probability = _as_float(metadata.get("policy_probability"))
            if policy and probability is not None:
                return f"{policy} p={probability:.2f}"
            if policy:
                return str(policy)
        return "reaction"
    if depth == 0:
        return "target"
    if node.get("in_stock") or node.get("is_in_stock") or node.get("stock"):
        return "stock precursor"
    if not _children(node):
        return "not in stock"
    return "intermediate"


def _node_visual_class(
    node: dict[str, Any], depth: int, has_children: bool, is_target: bool = False
) -> str:
    if _is_reaction_node(node):
        return "reaction"
    if is_target or depth == 0:
        return "target"
    if node.get("in_stock") or node.get("is_in_stock") or node.get("stock"):
        return "stock"
    if not has_children:
        return "missing"
    return "unknown"


def _short_label(value: str, max_len: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _split_label(value: str, max_len: int = 22, max_lines: int = 2) -> list[str]:
    text = str(value)
    if len(text) <= max_len:
        return [text]
    lines = [text[idx : idx + max_len] for idx in range(0, len(text), max_len)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _short_label(lines[-1], max_len)
    return lines


def _children(node: dict[str, Any]) -> list[Any]:
    children: list[Any] = []
    for key in ("children", "reactants", "precursors", "outcomes"):
        value = node.get(key)
        if isinstance(value, list):
            children.extend(value)
    if isinstance(node.get("children"), dict):
        children.extend(node["children"].values())
    return children


def _node_smiles(node: dict[str, Any]) -> str | None:
    for key in ("smiles", "smile", "mol", "molecule"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("smiles")
        if isinstance(value, str) and value:
            return value
    return None


def _is_molecule_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or node.get("kind") or "").lower()
    if node_type in {"mol", "molecule", "compound"}:
        return True
    return _node_smiles(node) is not None and not _is_reaction_node(node)


def _is_reaction_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or node.get("kind") or "").lower()
    return node_type in {"reaction", "rxn"} or any(
        key in node for key in ("reaction_smiles", "smarts", "template")
    )


def _looks_like_route(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in ("tree", "reaction_tree", "route", "score", "scores", "solved")
    )


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_ANDOR_TREE_SCRIPT = r"""
(function () {
  const dataEl = document.getElementById("andor-data");
  const svg = document.getElementById("andor-svg");
  const detail = document.getElementById("andor-detail");
  if (!dataEl || !svg || !detail) return;

  const ns = "http://www.w3.org/2000/svg";
  const graph = JSON.parse(dataEl.textContent).graph;
  const nodeById = new Map(graph.nodes.map(node => [node.id, node]));
  const out = new Map();
  const linked = new Map();
  function addLinked(a, b) {
    if (!linked.has(a)) linked.set(a, new Set());
    linked.get(a).add(b);
  }
  graph.edges.forEach(edge => {
    if (!out.has(edge.source)) out.set(edge.source, []);
    out.get(edge.source).push(edge.target);
    addLinked(edge.source, edge.target);
    addLinked(edge.target, edge.source);
  });
  const collapsed = new Set();
  let selectedId = "root";
  const viewport = { width: 1180, height: 700 };
  let view = null;
  let dragging = false;
  let lastPoint = null;

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function makeSvg(name, attrs) {
    const el = document.createElementNS(ns, name);
    Object.entries(attrs || {}).forEach(([key, value]) => el.setAttribute(key, value));
    return el;
  }

  function textNode(x, y, text, cls) {
    const t = makeSvg("text", { x, y, "text-anchor": "middle" });
    if (cls) t.setAttribute("class", cls);
    t.textContent = text;
    return t;
  }

  function splitText(text, maxLen, maxLines) {
    const clean = String(text || "").replace(/\s+/g, " ");
    const chunks = [];
    for (let idx = 0; idx < clean.length; idx += maxLen) chunks.push(clean.slice(idx, idx + maxLen));
    if (!chunks.length) return [""];
    if (chunks.length > maxLines) {
      const kept = chunks.slice(0, maxLines);
      kept[maxLines - 1] = kept[maxLines - 1].slice(0, Math.max(0, maxLen - 1)) + "…";
      return kept;
    }
    return chunks;
  }

  function nodeSize(node) {
    if (node.kind === "reaction") return { w: 238, h: 84 };
    if (node.structureSrc) return { w: 252, h: 154 };
    return { w: 238, h: 76 };
  }

  function hiddenIds() {
    const hidden = new Set();
    function hideChildren(id) {
      (out.get(id) || []).forEach(child => {
        if (!hidden.has(child)) {
          hidden.add(child);
          hideChildren(child);
        }
      });
    }
    collapsed.forEach(hideChildren);
    return hidden;
  }

  function layout() {
    const hidden = hiddenIds();
    const nodes = graph.nodes.filter(node => !hidden.has(node.id));
    const nodeIds = new Set(nodes.map(node => node.id));
    const edges = graph.edges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target));
    const rings = new Map();
    nodes.forEach(node => {
      const depth = Number.isFinite(node.depth) ? node.depth : 0;
      if (!rings.has(depth)) rings.set(depth, []);
      rings.get(depth).push(node);
    });
    const ringEntries = Array.from(rings.entries()).sort((a, b) => a[0] - b[0]);
    const maxDepth = Math.max(1, ...ringEntries.map(([depth]) => depth));
    const maxCount = Math.max(1, ...ringEntries.map(([, ringNodes]) => ringNodes.length));
    const ringGap = 172;
    const radiusMax = 118 + maxDepth * ringGap;
    const width = Math.max(1180, radiusMax * 2 + 430, maxCount * 120 + 520);
    const height = Math.max(700, radiusMax * 2 + 260);
    const centerX = width / 2;
    const centerY = height / 2;
    ringEntries.forEach(([depth, ringNodes]) => {
      ringNodes.sort((a, b) => {
        const routeA = Array.isArray(a.routes) ? a.routes.join(",") : "";
        const routeB = Array.isArray(b.routes) ? b.routes.join(",") : "";
        return `${routeA} ${a.kind} ${a.label}`.localeCompare(`${routeB} ${b.kind} ${b.label}`);
      });
      if (depth === 0) {
        ringNodes.forEach(node => { node.x = centerX; node.y = centerY; });
        return;
      }
      const radius = 112 + depth * ringGap;
      const angleOffset = -Math.PI / 2 + depth * 0.34;
      const step = (Math.PI * 2) / Math.max(1, ringNodes.length);
      ringNodes.forEach((node, index) => {
        const angle = angleOffset + index * step;
        node.x = centerX + Math.cos(angle) * radius;
        node.y = centerY + Math.sin(angle) * radius;
      });
    });
    return { nodes, edges, width, height };
  }

  function draw() {
    clear(svg);
    const defs = makeSvg("defs", {});
    const marker = makeSvg("marker", {
      id: "andor-arrow",
      viewBox: "0 0 10 10",
      refX: 8,
      refY: 5,
      markerWidth: 5,
      markerHeight: 5,
      orient: "auto-start-reverse"
    });
    marker.appendChild(makeSvg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#9eabb8", opacity: "0.72" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    const laid = layout();
    if (!view) view = defaultView(laid);
    svg.setAttribute("viewBox", `0 0 ${viewport.width} ${viewport.height}`);
    const scene = makeSvg("g", { transform: `translate(${view.x},${view.y}) scale(${view.k})` });
    svg.appendChild(scene);

    const selectedNeighbors = linked.get(selectedId) || new Set();
    laid.edges.forEach(edge => {
      const from = nodeById.get(edge.source);
      const to = nodeById.get(edge.target);
      if (!from || !to) return;
      const path = edgePath(from, to);
      const active = edge.source === selectedId || edge.target === selectedId ||
        (selectedNeighbors.has(edge.source) && selectedNeighbors.has(edge.target));
      scene.appendChild(makeSvg("path", {
        class: `andor-edge${edge.routes && edge.routes.length > 1 ? " merged" : ""}${active ? " active" : ""}`,
        d: path
      }));
    });

    laid.nodes.forEach(node => scene.appendChild(drawNode(node)));
  }

  function defaultView(laid) {
    const scale = laid.nodes.length > 16 ? 0.82 : 0.94;
    return {
      x: viewport.width / 2 - (laid.width / 2) * scale,
      y: viewport.height / 2 - (laid.height / 2) * scale,
      k: scale
    };
  }

  function edgePath(from, to) {
    const fromSize = nodeSize(from);
    const toSize = nodeSize(to);
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    const startPad = Math.min(fromSize.w, fromSize.h) / 2 + 2;
    const endPad = Math.min(toSize.w, toSize.h) / 2 + 8;
    const x1 = from.x + (dx / distance) * startPad;
    const y1 = from.y + (dy / distance) * startPad;
    const x2 = to.x - (dx / distance) * endPad;
    const y2 = to.y - (dy / distance) * endPad;
    const curve = Math.min(90, distance * 0.14);
    const cx = (x1 + x2) / 2 + (-dy / distance) * curve;
    const cy = (y1 + y2) / 2 + (dx / distance) * curve;
    return `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`;
  }

  function drawNode(node) {
    const size = nodeSize(node);
    const selectedNeighbors = linked.get(selectedId) || new Set();
    const related = node.id === selectedId || selectedNeighbors.has(node.id) || selectedId === "root";
    const group = makeSvg("g", {
      class: `andor-node ${node.className || "unknown"}${selectedId === node.id ? " selected" : ""}${related && node.id !== selectedId ? " neighbor" : ""}${!related ? " dimmed" : ""}${collapsed.has(node.id) ? " collapsed" : ""}`,
      transform: `translate(${node.x - size.w / 2},${node.y - size.h / 2})`,
      tabindex: "0"
    });
    group.appendChild(makeSvg("rect", { width: size.w, height: size.h, rx: 12 }));
    const kind = node.kind === "reaction" ? "AND reaction" : (node.kind === "root" ? "OR root" : "molecule");
    group.appendChild(textNode(54, 18, kind, "node-kind"));
    const routeText = routeBadge(node);
    if (routeText) {
      const badgeWidth = Math.max(34, routeText.length * 6 + 16);
      group.appendChild(makeSvg("rect", {
        class: "route-badge",
        x: size.w - badgeWidth - 12,
        y: 8,
        width: badgeWidth,
        height: 20,
        rx: 10
      }));
      group.appendChild(textNode(size.w - badgeWidth / 2 - 12, 22, routeText, "route-badge-text"));
    }
    if (node.structureSrc) {
      group.appendChild(makeSvg("rect", {
        class: "structure-well",
        x: 22,
        y: 30,
        width: size.w - 44,
        height: 76,
        rx: 7
      }));
      const moleculeImage = makeSvg("image", {
        href: node.structureSrc,
        x: 26,
        y: 28,
        width: size.w - 52,
        height: 78,
        preserveAspectRatio: "xMidYMid meet"
      });
      if (node.structureFallbackSrc) moleculeImage.setAttribute("data-fallback-src", node.structureFallbackSrc);
      moleculeImage.addEventListener("error", () => {
        const fallback = moleculeImage.getAttribute("data-fallback-src");
        if (fallback && moleculeImage.getAttribute("href") !== fallback) {
          moleculeImage.setAttribute("href", fallback);
          moleculeImage.classList.add("structure-fallback");
        }
      });
      group.appendChild(moleculeImage);
      splitText(node.label, 24, 2).forEach((line, idx) => group.appendChild(textNode(size.w / 2, 123 + idx * 13, line)));
      group.appendChild(textNode(size.w / 2, size.h - 9, node.meta || "", "node-meta"));
    } else {
      splitText(node.label, 25, 2).forEach((line, idx) => group.appendChild(textNode(size.w / 2, 40 + idx * 14, line)));
      group.appendChild(textNode(size.w / 2, size.h - 13, node.meta || "", "node-meta"));
    }
    const title = makeSvg("title", {});
    title.textContent = node.label;
    group.appendChild(title);
    group.addEventListener("click", event => {
      event.stopPropagation();
      selectedId = node.id;
      showDetail(node);
      draw();
    });
    group.addEventListener("dblclick", event => {
      event.stopPropagation();
      if (collapsed.has(node.id)) collapsed.delete(node.id);
      else collapsed.add(node.id);
      draw();
    });
    return group;
  }

  function routeBadge(node) {
    const routes = Array.isArray(node.routes) ? node.routes.filter(Boolean) : [];
    if (!routes.length) return "";
    if (node.kind === "root") return `${routes.length} routes`;
    const shown = routes.slice(0, 2).join(",");
    return `R${shown}${routes.length > 2 ? "+" : ""}`;
  }

  function showDetail(node) {
    clear(detail);
    const h = document.createElement("h3");
    h.textContent = node.kind === "reaction" ? "Reaction node"
      : (node.kind === "root" ? "Graph root" : "Molecule node");
    detail.appendChild(h);
    if (node.structureSrc) {
      const img = document.createElement("img");
      img.src = node.structureSrc;
      if (node.structureFallbackSrc) {
        img.dataset.fallbackSrc = node.structureFallbackSrc;
        img.addEventListener("error", () => {
          if (img.dataset.fallbackSrc && img.src !== img.dataset.fallbackSrc) {
            img.src = img.dataset.fallbackSrc;
            img.classList.add("structure-fallback");
          }
        });
      }
      img.alt = `Structure of ${node.smiles || node.label}`;
      detail.appendChild(img);
    }
    const dl = document.createElement("dl");
    Object.entries(node.details || {}).forEach(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      appendDetailValue(dd, value);
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    detail.appendChild(dl);
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = "This knowledge graph merges identical molecule nodes across displayed routes; reaction nodes represent route hypotheses and require literature or experimental validation.";
    detail.appendChild(p);
  }

  function appendDetailValue(container, value) {
    if (value == null || value === "") {
      container.textContent = "n/a";
      return;
    }
    if (Array.isArray(value)) {
      const ul = document.createElement("ul");
      value.forEach(item => {
        const li = document.createElement("li");
        appendDetailValue(li, item);
        ul.appendChild(li);
      });
      container.appendChild(ul);
      return;
    }
    if (typeof value === "object") {
      const grid = document.createElement("div");
      grid.className = "detail-kv";
      Object.entries(value).forEach(([key, nested]) => {
        const k = document.createElement("span");
        k.textContent = key;
        const v = document.createElement("span");
        appendDetailValue(v, nested);
        grid.appendChild(k);
        grid.appendChild(v);
      });
      container.appendChild(grid);
      return;
    }
    const text = String(value);
    if (text.startsWith("http")) {
      const a = document.createElement("a");
      a.href = text;
      a.textContent = text;
      container.appendChild(a);
      return;
    }
    container.textContent = text;
  }

  function resetView() {
    view = null;
    draw();
  }

  document.getElementById("andor-expand").addEventListener("click", () => { collapsed.clear(); draw(); });
  document.getElementById("andor-collapse").addEventListener("click", () => {
    collapsed.clear();
    graph.nodes.forEach(node => { if (node.kind === "reaction") collapsed.add(node.id); });
    draw();
  });
  document.getElementById("andor-reset").addEventListener("click", resetView);
  svg.addEventListener("wheel", event => {
    event.preventDefault();
    if (!view) view = { x: 0, y: 0, k: 1 };
    view.k = Math.max(0.25, Math.min(2.8, view.k * (event.deltaY < 0 ? 1.08 : 0.92)));
    draw();
  }, { passive: false });
  svg.addEventListener("pointerdown", event => {
    dragging = true;
    lastPoint = { x: event.clientX, y: event.clientY };
    svg.classList.add("dragging");
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", event => {
    if (!dragging || !lastPoint) return;
    if (!view) view = { x: 0, y: 0, k: 1 };
    view.x += event.clientX - lastPoint.x;
    view.y += event.clientY - lastPoint.y;
    lastPoint = { x: event.clientX, y: event.clientY };
    draw();
  });
  svg.addEventListener("pointerup", event => {
    dragging = false;
    lastPoint = null;
    svg.classList.remove("dragging");
    try { svg.releasePointerCapture(event.pointerId); } catch (_) {}
  });
  showDetail(nodeById.get(selectedId) || graph.nodes[0]);
  draw();
})();
"""


def _format_score(value: Any) -> str:
    score = _as_float(value)
    if score is None:
        return "n/a"
    return f"{score:.3f}"
