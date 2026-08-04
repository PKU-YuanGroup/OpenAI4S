#!/usr/bin/env python
"""Helpers for ADMET-guided genetic molecule optimization demos.

The public visualization entry is render_optimization_history(...). The module
also keeps small validation and scoring helpers that are useful when building a
demo pipeline around RDKit, SA-Score, and ADMET-AI.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

matplotlib = None
pd = None
plt = None

__all__ = [
    "standardize_smiles",
    "canonicalize_smiles",
    "classify_admet_columns",
    "aggregate_admet_predictions",
    "operation_detail_json",
    "validate_generation_log",
    "render_optimization_history",
]

_SVG_DECLARATION = re.compile(r"<\?xml[^>]*\?>", re.IGNORECASE)
_SVG_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*(?:\[[\s\S]*?\]\s*)?>", re.IGNORECASE)


def _require_pandas() -> Any:
    global pd
    if pd is None:
        try:
            import pandas as pandas_module
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required; switch to the admet-sa-ga environment "
                "or install the skill prerequisites"
            ) from exc
        pd = pandas_module
    return pd


def _require_plotting() -> tuple[Any, Any]:
    global matplotlib, plt
    if matplotlib is None or plt is None:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/admet-sa-ga-mpl-cache")
        os.environ.setdefault("XDG_CACHE_HOME", "/tmp/admet-sa-ga-cache")
        Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)
        try:
            import matplotlib as matplotlib_module

            matplotlib_module.use("Agg")
            import matplotlib.pyplot as pyplot_module
        except ImportError as exc:
            raise RuntimeError(
                "matplotlib is required to render the optimization dashboard; "
                "switch to the admet-sa-ga environment or install the skill prerequisites"
            ) from exc
        matplotlib = matplotlib_module
        plt = pyplot_module
    return matplotlib, plt


def standardize_smiles(smiles: str) -> tuple[str | None, str]:
    """Return (canonical_smiles, failure_reason) after RDKit standardization."""
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise RuntimeError(
            "RDKit is required to standardize SMILES; switch to the "
            "admet-sa-ga environment or install the skill prerequisites"
        ) from exc

    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None, "parse_failed"
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None, "sanitize_failed"
    mol = Chem.RemoveHs(mol)
    if mol.GetNumAtoms() == 0:
        return None, "empty_molecule"
    fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    if not fragments:
        return None, "multi_fragment_failed"
    organic = [
        frag
        for frag in fragments
        if any(atom.GetAtomicNum() == 6 for atom in frag.GetAtoms())
    ]
    mol = max(organic or fragments, key=lambda item: item.GetNumHeavyAtoms())
    try:
        Chem.SanitizeMol(mol)
        return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True), ""
    except Exception:
        return None, "sanitize_failed"


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


def classify_admet_columns(
    columns: list[str], admet_config: dict[str, Any]
) -> dict[str, str]:
    """Map ADMET-AI columns to positive, negative, or ignored score roles."""
    positive = [key.lower() for key in admet_config.get("positive_keywords", [])]
    negative = [key.lower() for key in admet_config.get("negative_keywords", [])]
    mapping = {}
    for column in columns:
        name = column.lower()
        if "drugbank_approved_percentile" in name:
            mapping[column] = "ignored"
        elif any(key in name for key in negative):
            mapping[column] = "negative"
        elif any(key in name for key in positive):
            mapping[column] = "positive"
        else:
            mapping[column] = "ignored"
    return mapping


def _numeric_01(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text in {"true", "yes", "positive", "active", "high", "1"}:
            return 1.0
        if text in {"false", "no", "negative", "inactive", "low", "0"}:
            return 0.0
        return None
    if math.isnan(number):
        return None
    if 0.0 <= number <= 1.0:
        return number
    return 1.0 / (1.0 + pow(2.718281828459045, -number))


def aggregate_admet_predictions(
    predictions: dict[str, Any],
    admet_config: dict[str, Any],
) -> tuple[float, list[str], dict[str, str]]:
    """Aggregate raw ADMET endpoint predictions into score, risk flags, mapping."""
    mapping = classify_admet_columns(list(predictions.keys()), admet_config)
    scores: list[float] = []
    flags: list[str] = []
    threshold = float(admet_config.get("risk_threshold", 0.5))
    for column, role in mapping.items():
        if role == "ignored":
            continue
        value = _numeric_01(predictions.get(column))
        if value is None:
            continue
        if role == "positive":
            scores.append(value)
            if value < 1.0 - threshold:
                flags.append(f"low_{column}")
        else:
            scores.append(1.0 - value)
            if value >= threshold:
                flags.append(f"high_{column}")
    score = sum(scores) / len(scores) if scores else 0.5
    return score, flags, mapping


def operation_detail_json(
    operation: str,
    operator_detail: Any,
    child_canonical_smiles: str,
    parent_ids: list[str],
    parent_smiles: list[str],
) -> str:
    """Build the canonical operation_detail JSON string used in generation logs."""
    payload = {
        "operation": operation,
        "operator_detail": operator_detail,
        "parent_ids": parent_ids,
        "parent_smiles": parent_smiles,
        "child_canonical_smiles": child_canonical_smiles,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def validate_generation_log(frame: pd.DataFrame) -> None:
    """Validate lineage invariants expected by the visualization workflow."""
    _require_pandas()
    required = {"molecule_id", "smiles", "generation", "operation", "parent", "parents"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"generation log missing required columns: {missing}")
    duplicate_ids = [
        key
        for key, count in Counter(frame["molecule_id"].astype(str)).items()
        if count > 1
    ]
    duplicate_smiles = [
        key for key, count in Counter(frame["smiles"].astype(str)).items() if count > 1
    ]
    if duplicate_ids or duplicate_smiles:
        raise ValueError(
            "molecule_id and canonical smiles must be one-to-one; "
            f"duplicate_ids={duplicate_ids[:5]}, duplicate_smiles={duplicate_smiles[:5]}"
        )
    mutation = frame[frame["operation"].astype(str).str.lower() == "mutation"].fillna(
        ""
    )
    if len(mutation) and (
        (mutation["parent"].astype(str) == "").any()
        or (mutation["parents"].astype(str) != "").any()
    ):
        raise ValueError(
            "mutation rows must set parent to one ID and leave parents empty"
        )
    crossover = frame[frame["operation"].astype(str).str.lower() == "crossover"].fillna(
        ""
    )
    if len(crossover):
        bad_parent = (crossover["parent"].astype(str) != "").any()
        bad_parents = (
            crossover["parents"].astype(str).str.split(";").map(len) != 2
        ).any()
        if bad_parent or bad_parents:
            raise ValueError(
                "crossover rows must leave parent empty and set parents to exactly two IDs"
            )


def _clean_svg(svg: str) -> str:
    svg = _SVG_DECLARATION.sub("", svg)
    svg = _SVG_DOCTYPE.sub("", svg)
    return svg.strip()


def _mol_svg(smiles: str, width: int = 150, height: int = 95) -> str:
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw
    except ImportError:
        return (
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            'aria-label="Structure unavailable" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{width}" height="{height}" fill="#f8fafc"/>'
            f'<text x="{width / 2:g}" y="{height / 2:g}" text-anchor="middle" '
            'dominant-baseline="middle" fill="#667085" font-family="sans-serif" '
            'font-size="11">structure unavailable</text></svg>'
        )

    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return "<div class='missing'>invalid</div>"
    Draw.rdMolDraw2D.PrepareMolForDrawing(mol)
    drawer = Draw.rdMolDraw2D.MolDraw2DSVG(width, height)
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText().replace("\n", "")


def _build_payload(log_path: Path) -> dict:
    _require_pandas()
    frame = pd.read_csv(log_path)
    frame = frame.fillna("")
    for column in [
        "parent",
        "parents",
        "parent_id",
        "parent_ids",
        "parent_smiles",
        "source_molecule_id",
    ]:
        if column not in frame.columns:
            frame[column] = ""
    frame["parent_id"] = frame["parent_id"].where(
        frame["parent_id"].astype(str) != "", frame["parent"]
    )
    frame["parent_ids"] = frame["parent_ids"].where(
        frame["parent_ids"].astype(str) != "", frame["parents"]
    )
    validate_generation_log(frame)
    records = frame.to_dict(orient="records")
    first_seen = {}
    first_seen_by_smiles = {}
    for record in records:
        first_seen.setdefault(record["molecule_id"], int(record["generation"]))
        first_seen_by_smiles.setdefault(record["smiles"], int(record["generation"]))
        record["svg"] = _mol_svg(record["smiles"])
    generations = sorted({int(record["generation"]) for record in records})
    best_by_gen = []
    for gen in generations:
        subset = frame[frame["generation"] == gen]
        best_by_gen.append(
            {
                "generation": gen,
                "best": float(subset["total_score"].max()),
                "mean": float(subset["total_score"].mean()),
                "total_count": int(len(subset)),
                "generated_count": int(
                    (subset["operation"].astype(str).str.lower() != "seed").sum()
                ),
                "pass_count": int(
                    subset["passes_filters"]
                    .astype(str)
                    .str.lower()
                    .isin(["true", "1"])
                    .sum()
                ),
            }
        )
    return {
        "records": records,
        "generations": generations,
        "best_by_gen": best_by_gen,
        "first_seen": first_seen,
        "plots": _build_plots(best_by_gen),
        "first_seen_by_smiles": first_seen_by_smiles,
        "meta": {
            "record_count": int(len(records)),
            "svg_count": int(
                len([1 for record in records if record["svg"] is not None])
            ),
        },
    }


def _setup_plot_style() -> bool:
    _require_plotting()
    use_tex = bool(shutil.which("latex") and shutil.which("dvipng"))
    matplotlib.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 240,
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "axes.linewidth": 0.7,
            "xtick.major.width": 0.7,
            "ytick.major.width": 0.7,
            "xtick.major.size": 3.5,
            "ytick.major.size": 3.5,
            "svg.fonttype": "none",
            "text.usetex": use_tex,
        }
    )
    if use_tex:
        matplotlib.rcParams.update(
            {
                "text.latex.preamble": r"\usepackage{amsmath}\usepackage{newtxtext}\usepackage{newtxmath}",
            }
        )
    return use_tex


def _figure_to_svg(fig: plt.Figure) -> str:
    _require_plotting()
    buffer = io.StringIO()
    fig.savefig(
        buffer, format="svg", bbox_inches="tight", pad_inches=0.025, transparent=True
    )
    plt.close(fig)
    return _clean_svg(buffer.getvalue())


def _make_line_plot(
    frame: pd.DataFrame,
    series: list[tuple[str, str, str, str]],
    title: str,
    ylabel: str,
) -> str:
    _require_plotting()
    fig, ax = plt.subplots(figsize=(6.1, 2.28), constrained_layout=True)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")

    x = frame["generation"].astype(float)
    for key, label, color, marker in series:
        y = frame[key].astype(float)
        ax.plot(
            x,
            y,
            label=label,
            color=color,
            linewidth=2.15,
            marker=marker,
            markersize=4.8,
            markerfacecolor="white",
            markeredgewidth=1.35,
            solid_capstyle="round",
            zorder=3,
        )

    ax.set_title(title, loc="left", pad=7, fontweight="bold", color="#111827")
    ax.set_xlabel("Generation")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color="#d9e0e8", linewidth=0.75, alpha=0.75)
    ax.grid(axis="x", color="#eef2f6", linewidth=0.55, alpha=0.65)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#6b7280")
    ax.spines["bottom"].set_color("#6b7280")
    ax.tick_params(colors="#374151", labelcolor="#374151")
    ax.margins(x=0.035, y=0.14)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, 1.02),
        ncol=min(3, len(series)),
        frameon=False,
        handlelength=2.2,
        columnspacing=1.1,
    )
    return _figure_to_svg(fig)


def _build_plots(best_by_gen: list[dict]) -> dict:
    _require_pandas()
    _require_plotting()
    _setup_plot_style()
    plot_frame = pd.DataFrame(best_by_gen)
    try:
        score_plot = _make_line_plot(
            plot_frame,
            [
                ("best", "Best total score", "#0f766e", "o"),
                ("mean", "Mean total score", "#4f46e5", "s"),
            ],
            r"Optimization score trajectory",
            "Total score",
        )
        count_plot = _make_line_plot(
            plot_frame,
            [
                ("generated_count", "Generated", "#2563eb", "o"),
                ("total_count", "Logged", "#b45309", "D"),
                ("pass_count", "Passed filters", "#16a34a", "s"),
            ],
            r"Generation throughput and filter yield",
            "Molecules",
        )
    except Exception:
        matplotlib.rcParams.update({"text.usetex": False})
        score_plot = _make_line_plot(
            plot_frame,
            [
                ("best", "Best total score", "#0f766e", "o"),
                ("mean", "Mean total score", "#4f46e5", "s"),
            ],
            "Optimization score trajectory",
            "Total score",
        )
        count_plot = _make_line_plot(
            plot_frame,
            [
                ("generated_count", "Generated", "#2563eb", "o"),
                ("total_count", "Logged", "#b45309", "D"),
                ("pass_count", "Passed filters", "#16a34a", "s"),
            ],
            "Generation throughput and filter yield",
            "Molecules",
        )
    return {"score": score_plot, "count": count_plot}


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Optimization History</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef2f5;
      --panel: rgba(255, 255, 255, 0.92);
      --panel-strong: #ffffff;
      --ink: #101828;
      --muted: #667085;
      --line: #d7dee8;
      --line-soft: #e8edf3;
      --accent: #df7656;
      --accent-soft: #f3c1ae;
      --success: #12805c;
      --danger: #c2410c;
      --shadow: 0 16px 44px rgba(15, 23, 42, 0.08);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-width: 320px;
      background:
        linear-gradient(180deg, #f8fafc 0%, #eef2f5 42%, #e7edf3 100%);
      color: var(--ink);
    }
    header {
      position: sticky;
      top: 0;
      z-index: 10;
      padding: 18px 24px;
      background: rgba(248, 250, 252, 0.88);
      border-bottom: 1px solid rgba(215, 222, 232, 0.88);
      backdrop-filter: blur(16px);
    }
    main { padding: 18px 24px 30px; }
    .topbar {
      display: grid;
      grid-template-columns: minmax(180px, auto) 1fr auto;
      align-items: center;
      gap: 18px;
      max-width: 1600px;
      margin: 0 auto;
    }
    .brand { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
    .brand strong { font-size: 17px; letter-spacing: 0; white-space: nowrap; }
    .brand span { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .stats {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      color: #344054;
      font-weight: 600;
    }
    .workspace {
      max-width: 1600px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(420px, 1fr) minmax(330px, 0.72fr) minmax(430px, 1.02fr);
      gap: 16px;
      min-height: 610px;
    }
    section,
    .plot-panel {
      min-width: 0;
      background: var(--panel);
      border: 1px solid rgba(215, 222, 232, 0.9);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .panel-head {
      min-height: 47px;
      padding: 13px 15px;
      border-bottom: 1px solid var(--line-soft);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    h2 {
      margin: 0;
      font-size: 13px;
      line-height: 1.2;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: #344054;
    }
    .subtle { color: var(--muted); font-size: 12px; white-space: nowrap; }
    #population {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 9px;
      padding: 12px;
      max-height: 720px;
      overflow: auto;
      align-content: start;
    }
    .mol-card {
      aspect-ratio: 1 / 1.08;
      border: 1px solid #d7dee8;
      border-top: 3px solid #98a2b3;
      border-radius: 8px;
      padding: 8px;
      cursor: pointer;
      background: #fff;
      min-width: 0;
      display: flex;
      flex-direction: column;
      transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
    }
    .mol-card:hover {
      transform: translateY(-1px);
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.09);
    }
    .mol-card.filter-pass { border-top-color: var(--success); }
    .mol-card.filter-fail { border-top-color: var(--danger); opacity: 0.74; }
    .mol-card.active {
      border-color: rgba(37, 99, 235, 0.78);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14), 0 12px 26px rgba(37, 99, 235, 0.12);
    }
    .mol-title,
    .lineage-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      line-height: 1.2;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
    }
    .mol-title span,
    .lineage-meta strong {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      color: #1f2937;
    }
    .mol-title strong { color: #111827; font-variant-numeric: tabular-nums; }
    .mol-card svg {
      width: 100%;
      height: calc(100% - 19px);
      min-height: 0;
      display: block;
      margin-top: 5px;
    }
    .center {
      padding: 14px;
      display: grid;
      gap: 14px;
    }
    #selectedSvg {
      min-height: 210px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      background: linear-gradient(180deg, #ffffff, #f8fafc);
      padding: 14px;
    }
    #selectedSvg svg {
      width: min(270px, 100%);
      height: auto;
      max-height: 245px;
      display: block;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      table-layout: fixed;
      overflow: hidden;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      background: #fff;
    }
    td {
      border-bottom: 1px solid var(--line-soft);
      padding: 7px 8px;
      vertical-align: top;
      word-break: break-word;
    }
    tr:last-child td { border-bottom: 0; }
    td:first-child {
      width: 118px;
      font-weight: 700;
      color: #475467;
      background: #f8fafc;
    }
    #tree {
      padding: 16px;
      max-height: 720px;
      overflow: auto;
    }
    .lineage-tree {
      min-width: max-content;
      padding: 8px 20px 28px;
      display: flex;
      justify-content: center;
    }
    .pedigree-unit { display: flex; flex-direction: column; align-items: center; position: relative; }
    .pedigree-parents { display: flex; justify-content: center; align-items: flex-end; position: relative; }
    .pedigree-parents.single { margin-bottom: 30px; }
    .pedigree-parents.single::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -30px;
      width: 1px;
      height: 30px;
      background: #a9b6c5;
      transform: translateX(-0.5px);
    }
    .pedigree-parents.pair { gap: 30px; margin-bottom: 40px; padding-bottom: 18px; }
    .pedigree-parents.pair::before {
      content: "";
      position: absolute;
      left: 24%;
      right: 24%;
      bottom: 18px;
      height: 1px;
      background: #a9b6c5;
    }
    .pedigree-parents.pair::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -22px;
      width: 1px;
      height: 40px;
      background: #a9b6c5;
      transform: translateX(-0.5px);
    }
    .pedigree-parent { display: flex; justify-content: center; }
    .lineage-card {
      width: 156px;
      border: 1px solid #d7dee8;
      border-radius: 8px;
      padding: 7px;
      background: #fff;
      cursor: pointer;
      box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
      position: relative;
      z-index: 1;
    }
    .lineage-card.active {
      border-color: rgba(37, 99, 235, 0.75);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14);
    }
    .lineage-card svg {
      width: 100%;
      height: 96px;
      display: block;
      margin: 4px 0;
    }
    .lineage-empty {
      color: var(--muted);
      font-size: 13px;
      padding: 10px;
    }
    .plots {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .plot-panel {
      height: 334px;
      padding: 14px 16px 10px;
      background: rgba(255, 255, 255, 0.96);
    }
    .plot-panel svg {
      width: 100%;
      height: 100%;
      display: block;
    }
    @media (max-width: 1360px) {
      #population { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }
    @media (max-width: 1160px) {
      .layout { grid-template-columns: 1fr; }
      .plots { grid-template-columns: 1fr; }
      #population { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .topbar { grid-template-columns: 1fr; gap: 10px; }
      .stats { justify-content: flex-start; flex-wrap: wrap; }
    }
    @media (max-width: 760px) {
      header, main { padding-left: 14px; padding-right: 14px; }
      #population { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .brand { flex-direction: column; gap: 2px; align-items: flex-start; }
      .plot-panel { height: 300px; }
    }
    @media (max-width: 460px) {
      #population { grid-template-columns: 1fr; }
      td:first-child { width: 102px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <strong>GA Optimization History</strong>
        <span>Generation <b id="generationLabel"></b></span>
      </div>
      <input id="generationSlider" type="range" min="0" step="1" aria-label="Generation">
      <div class="stats">
        <span class="pill"><span id="generationCount"></span> generations</span>
        <span class="pill"><span id="recordCount"></span> molecules</span>
        <span class="pill"><span id="svgCount"></span> drawings</span>
      </div>
    </div>
  </header>
  <main>
    <div class="workspace">
      <div class="layout">
        <section>
          <div class="panel-head"><h2>Population</h2><span id="populationCount" class="subtle"></span></div>
          <div id="population"></div>
        </section>
        <section>
          <div class="panel-head"><h2>Selected Molecule</h2><span id="selectedScore" class="subtle"></span></div>
          <div class="center"><div id="selectedSvg"></div><table id="detailTable"></table></div>
        </section>
        <section>
          <div class="panel-head"><h2>Lineage</h2><span class="subtle">parent trace</span></div>
          <div id="tree"></div>
        </section>
      </div>
      <div class="plots">
        <div id="scorePlot" class="plot-panel"></div>
        <div id="countPlot" class="plot-panel"></div>
      </div>
    </div>
  </main>
  <script>
    const payload = __DATA__;
    const records = payload.records;
    const generations = payload.generations;
    const byGeneration = new Map();
    const byId = new Map();
    const bySmiles = new Map();
    records.forEach(r => {
      const gen = Number(r.generation);
      if (!byGeneration.has(gen)) byGeneration.set(gen, []);
      byGeneration.get(gen).push(r);
      byId.set(r.molecule_id, r);
      bySmiles.set(r.smiles, r);
    });
    let current = null;
    const slider = document.getElementById('generationSlider');
    slider.min = Math.min(...generations);
    slider.max = Math.max(...generations);
    slider.value = slider.min;
    slider.addEventListener('input', () => renderGeneration(Number(slider.value)));

    document.getElementById('generationCount').textContent = generations.length;
    document.getElementById('recordCount').textContent = payload.meta.record_count;
    document.getElementById('svgCount').textContent = payload.meta.svg_count;
    document.getElementById('scorePlot').innerHTML = payload.plots.score;
    document.getElementById('countPlot').innerHTML = payload.plots.count;

    function fmt(v) {
      if (typeof v === 'number') return Number.isFinite(v) ? v.toFixed(3) : '';
      const n = Number(v);
      return Number.isFinite(n) && String(v).trim() !== '' ? n.toFixed(3) : v;
    }

    function escapeHtml(value) {
      const entities = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
      return String(value ?? '').replace(/[&<>"']/g, char => entities[char]);
    }

    function renderGeneration(gen) {
      document.getElementById('generationLabel').textContent = gen;
      const list = byGeneration.get(gen) || [];
      document.getElementById('populationCount').textContent = `${list.length} records`;
      const pop = document.getElementById('population');
      pop.innerHTML = '';
      list.sort((a,b) => Number(b.total_score) - Number(a.total_score)).forEach(r => {
        const div = document.createElement('div');
        const passesFilters = r.passes_filters === true || r.passes_filters === 1 || String(r.passes_filters).toLowerCase() === 'true';
        const filterClass = passesFilters ? 'filter-pass' : 'filter-fail';
        div.className = `mol-card ${filterClass} ${current && r.molecule_id === current.molecule_id ? 'active' : ''}`;
        div.innerHTML = `<div class="mol-title"><span>${escapeHtml(r.molecule_id)}</span><strong>${escapeHtml(fmt(r.total_score))}</strong></div>${r.svg}`;
        div.onclick = () => selectRecord(r, true);
        pop.appendChild(div);
      });
      if (!list.some(r => current && r.molecule_id === current.molecule_id) && list[0]) selectRecord(list[0], false);
    }

    function selectRecord(record, syncSlider) {
      current = record;
      if (syncSlider) {
        const first = payload.first_seen[record.molecule_id] ?? record.generation;
        slider.value = first;
        document.getElementById('generationLabel').textContent = first;
      }
      document.getElementById('selectedSvg').innerHTML = record.svg;
      document.getElementById('selectedScore').textContent = `score ${fmt(record.total_score)}`;
      const keys = ['molecule_id','generation','smiles','parent','parents','parent_smiles','operation','operation_detail','status','MW','LogP','TPSA','HBD','HBA','RotatableBonds','RingCount','qed','sa_score','admet_score','admet_risk_flags','total_score','passes_filters'];
      document.getElementById('detailTable').innerHTML = keys.map(k => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(fmt(record[k]))}</td></tr>`).join('');
      renderTree(record);
      renderGeneration(Number(slider.value));
    }

    function renderTree(record) {
      const tree = document.getElementById('tree');
      const seen = new Set();
      function parentIds(r) {
        const crossoverParents = String(r.parents || r.parent_ids || '').split(';').map(x => x.trim()).filter(Boolean);
        if (crossoverParents.length) return crossoverParents;
        const parent = String(r.parent || r.parent_id || '').trim();
        if (parent) return [parent];
        const legacy = String(r.source_molecule_id || '').split('|').map(x => x.trim()).filter(Boolean);
        return legacy;
      }
      function card(r) {
        const active = current && r.molecule_id === current.molecule_id ? ' active' : '';
        return `<div class="lineage-card${active}" data-id="${escapeHtml(r.molecule_id)}">
          <div class="lineage-meta"><strong>${escapeHtml(r.molecule_id)}</strong><span>g${escapeHtml(r.generation)}</span></div>
          ${r.svg}
          <div class="lineage-meta"><span>${escapeHtml(r.operation)}</span><span>${escapeHtml(fmt(r.total_score))}</span></div>
        </div>`;
      }
      function walk(r) {
        if (!r) return '<div class="lineage-empty">missing parent</div>';
        if (seen.has(r.molecule_id)) return `<div class="lineage-card" data-id="${escapeHtml(r.molecule_id)}"><div class="lineage-meta"><strong>${escapeHtml(r.molecule_id)}</strong><span>seen</span></div>${r.svg}</div>`;
        seen.add(r.molecule_id);
        const parents = parentIds(r).map(id => byId.get(id)).filter(Boolean);
        if (parents.length === 1) {
          return `<div class="pedigree-unit">
            <div class="pedigree-parents single">${walk(parents[0])}</div>
            ${card(r)}
          </div>`;
        }
        if (parents.length >= 2) {
          return `<div class="pedigree-unit">
            <div class="pedigree-parents pair">
              <div class="pedigree-parent">${walk(parents[0])}</div>
              <div class="pedigree-parent">${walk(parents[1])}</div>
            </div>
            ${card(r)}
          </div>`;
        }
        return `<div class="pedigree-unit">${card(r)}</div>`;
      }
      tree.innerHTML = `<div class="lineage-tree">${walk(record)}</div>`;
      tree.querySelectorAll('.lineage-card').forEach(el => {
        el.onclick = () => {
          const target = byId.get(el.dataset.id);
          if (target) selectRecord(target, true);
        };
      });
    }

    renderGeneration(Number(slider.value));
  </script>
</body>
</html>"""


def _render_html(payload: dict) -> str:
    data = (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return _HTML_TEMPLATE.replace("__DATA__", data)


def render_optimization_history(log_path: str | Path, out_path: str | Path) -> Path:
    """Render a self-contained optimization-history HTML file from generation_log.csv."""
    payload = _build_payload(Path(log_path))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_html(payload), encoding="utf-8")
    return out
