"""Regenerate text-only developer demos in this skill directory (flat layout).

Never commit figure PNGs or numerical UMA/heuristic screening results here.
Live user answers must come from ``run_pipeline`` deliverables in a workdir.

    uv run python skills/catalyst_sar_screening/build_example.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from catalyst_sar_screening.kernel import (  # noqa: E402
    build_markdown_report,
    render_sar_dashboard,
)

OUT_HTML = "metal_center_dissolution_dashboard.html"
OUT_REPORT = "metal_center_dissolution_report.md"
OUT_SUMMARY = "metal_center_dissolution_summary.json"

_DEMO_NOTE = (
    "Synthetic developer demo only — not experimental results. "
    "Figures and metrics for a real request are written under the run "
    "workdir deliverables, never as metal_center_dissolution_* demo files."
)


def _demo_analysis(summary: dict) -> dict:
    ranked = []
    for row in summary.get("ranked") or []:
        ranked.append(
            {
                "name": row.get("name"),
                "metal": row.get("metal"),
                "source": row.get("source") or "catalog",
                "rank": row.get("rank"),
                "converged": False,
                "passes_filters": False,
                "dissolution_potential": None,
                "overpotential": None,
            }
        )
    return {
        "mode": summary.get("mode") or "dissolution",
        "metrics": summary.get("metrics") or ["dissolution"],
        "computation": summary.get("computation")
        or {
            "calculator": "uma",
            "mlip_model": "uma-s-1p1",
            "mlip_task": "oc20",
            "protocol": "catalyst-design-agent/uma-s-1p1+oc20",
        },
        "figures": [],
        "structure_renders": [],
        "ranked": ranked,
        "insights": list(summary.get("insights") or []) + [_DEMO_NOTE],
        "n_total": len(ranked),
        "n_converged": 0,
        "n_passing": 0,
        "filters": {"min_dissolution": 0.0, "max_overpotential": None},
    }


def main() -> None:
    summary = json.loads((HERE / OUT_SUMMARY).read_text(encoding="utf-8"))
    # Strip any accidental numerical backends/results before writing demos.
    summary["figures"] = []
    summary["structure_renders"] = []
    summary["demo"] = True
    summary["disclaimer"] = (
        "Not unpublished experimental results. Do not treat "
        "metal_center_dissolution_* demo files as user deliverables."
    )
    for row in summary.get("ranked") or []:
        row["converged"] = False
        row["passes_filters"] = False
        row["dissolution_potential"] = None
        row.pop("backend", None)
        row.pop("metal_binding_energy", None)
        row.pop("composite_score", None)
    analysis = _demo_analysis(summary)
    (HERE / OUT_HTML).write_text(
        render_sar_dashboard(
            analysis, title="SAC dissolution-potential screening (demo shell)"
        ),
        encoding="utf-8",
    )
    (HERE / OUT_REPORT).write_text(
        build_markdown_report(
            analysis, title="SAC Dissolution Potential Report (demo shell)"
        ),
        encoding="utf-8",
    )
    (HERE / OUT_SUMMARY).write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {HERE / OUT_HTML}")
    print(f"wrote {HERE / OUT_REPORT}")
    print(f"wrote {HERE / OUT_SUMMARY}")
    print("note: demo shells must not contain PNGs or unpublished numeric results")


if __name__ == "__main__":
    main()
