"""Regenerate the case1 mineral spectra example report from committed data.

    uv run python skills/mineral_spectra_analysis/examples/build_example.py

The full numerical pipeline lives in ``mineral_spectra_analysis.kernel`` and
requires numpy/scipy/pybaselines/matplotlib. This example builder is deliberately
stdlib-only: it formats the committed blind-analysis summary and hidden truth so
the example report can be regenerated even when the scientific runtime is not
installed.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_json(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_report() -> str:
    analysis = _load_json("case1_analysis.json")
    components = _load_json("case1_components.json")
    truth = _load_json("case1/truth.json")

    lines = [
        "# Mineral Spectra Example: case1",
        "",
        "- Source case: `examples/case1/spectrum.csv`",
        "- Spectrum type: synthetic dirty mixed-mineral Raman spectrum",
        "- Pipeline: global preprocessing once -> iterative residual peak-find/match/subtract -> NNLS unmixing -> final evaluation",
        "- Ground truth is shown here only because this is an evaluation example; the blind loop does not read it.",
        "",
        "## Synthetic Components",
        "",
        "| Component | Type | True fraction | Role |",
        "|---|---|---:|---|",
    ]
    for item in components["components"]:
        lines.append(
            f"| {item['name']} | {item['type']} | {_pct(item['true_fraction'])} | {item['role']} |"
        )

    lines.extend(
        [
            "",
            "## Blind Pipeline Prediction",
            "",
            "| Predicted component | Type | Estimated fraction | Supporting peaks (cm^-1) |",
            "|---|---|---:|---|",
        ]
    )
    for item in analysis["predicted_components"]:
        peaks = ", ".join(str(peak) for peak in item["support_peaks_cm1"])
        lines.append(
            f"| {item['name']} | {item['type']} | {_pct(item['estimated_fraction'])} | {peaks} |"
        )

    diag = analysis["diagnostics"]
    lines.extend(
        [
            "",
            "## Reliability Diagnostics",
            "",
            f"- Clean-spectrum second-derivative peaks: **{analysis['clean_peak_count']}**",
            f"- First peak positions shown in source run: {analysis['clean_peak_positions_head_cm1']}",
            f"- Pearson fit correlation: **{diag['fit_corr']:.3f}**",
            f"- Residual RMSE: **{diag['residual_rmse']:.4f}**",
            f"- Relative residual: **{diag['rel_residual']:.4f}**",
            f"- Explained energy: **{diag['explained_energy'] * 100:.1f}%**",
            f"- Remaining significant residual peaks: **{diag['n_residual_peaks']}**",
            f"- Reliability: **{diag['reliability'].upper()}**",
            "",
            "## Ground-Truth Evaluation",
            "",
            f"- True components: {truth['true_names']}",
            f"- True fractions: {truth['true_fractions']}",
            f"- Precision/Recall/F1: {analysis['evaluation']['precision']:.2f} / "
            f"{analysis['evaluation']['recall']:.2f} / **{analysis['evaluation']['f1']:.2f}**",
            f"- Fraction MAE: **{analysis['evaluation']['fraction_mae']:.3f}**",
            "",
            "## Iteration Trace",
            "",
            "| Step | Added component | Match corr | rel_residual | Residual peaks | Cumulative components |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for item in analysis["history"]:
        lines.append(
            f"| {item['step']} | {item['added_component']} | {item['match_corr']:.3f} | "
            f"{item['rel_residual']:.4f} | {item['n_residual_peaks']} | {item['cumulative_components']} |"
        )

    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `case1/spectrum.csv` - observable two-column Raman spectrum",
            "- `case1/truth.json` - hidden answer key for this evaluation example",
            "- `case1/input.png` - dirty input spectrum plot",
            "- `case1_components.json` - component type and true-fraction summary",
            "- `case1_analysis.json` - committed blind-analysis summary used by this report",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    report = build_report()
    out = HERE / "case1_mineral_spectra_report.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out} ({len(report)} chars)")


if __name__ == "__main__":
    main()
