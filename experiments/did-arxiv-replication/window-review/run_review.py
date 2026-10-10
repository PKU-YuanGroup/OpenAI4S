"""Offline coverage/variability audit; no event screening or DiD estimation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = ROOT / "skills/panel-data-preprocessing/kernel.py"
INPUTS = ROOT / "experiments/did-arxiv-replication/inputs"
OUTPUT_FILES = (
    "synthetic.csv",
    "audit.json",
    "comparison.png",
    "comparison.svg",
    "environment.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _skill() -> Any:
    spec = importlib.util.spec_from_file_location("window_review_skill", SKILL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Panel skill sidecar could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic(path: Path) -> None:
    """A constructed illustration, not an independent random-noise realization."""
    fluctuations = [-1.3, 0.7, 0.4, -0.9, 1.1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "period", "y"], lineterminator="\n"
        )
        writer.writeheader()
        for entity in ["quiet", "noisy", "trend"]:
            for period in range(1, 33):
                fluctuation = fluctuations[(period - 1) % len(fluctuations)]
                amplitude = 1.1 if entity == "noisy" else 0.04
                drift = 0.35 * (period - 1) if entity == "trend" else 0
                writer.writerow(
                    {
                        "id": entity,
                        "period": period,
                        "y": format(10 + amplitude * fluctuation + drift, ".17g"),
                    }
                )


def _compressed_audit(panel: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    """Preserve API summaries and deterministic examples, not a huge duplicate panel."""
    by_window = []
    for summary in audit["summary"]:
        width = summary["window"]
        eligible = [
            row
            for row in audit["assessments"]
            if row["window"] == width and row["eligible"]
        ]
        values = [row["pre_statistics"]["mean_noise_iid"] for row in eligible]
        ratios = [row["pre_statistics"]["noise_to_tolerance_ratio"] for row in eligible]
        examples = []
        seen = set()
        for row in eligible:
            if row["noise_status"] not in seen:
                examples.append(row)
                seen.add(row["noise_status"])
        by_window.append(
            {
                **summary,
                "eligible_noise_status_counts": dict(
                    sorted(Counter(row["noise_status"] for row in eligible).items())
                ),
                "eligible_mean_noise_iid": {
                    "minimum": min(values) if values else None,
                    "median": statistics.median(values) if values else None,
                    "maximum": max(values) if values else None,
                    "median_noise_to_tolerance_ratio": (
                        statistics.median(ratios) if ratios else None
                    ),
                },
                "representative_eligible_assessments": examples,
            }
        )
    eligible_requested = {
        (row["entity"], row["metric"], row["time"])
        for row in audit["assessments"]
        if row["window"] == audit["config"]["requested_window"] and row["eligible"]
    }
    recommendations = [
        row
        for row in audit["recommendations"]
        if (row["entity"], row["metric"], row["time"]) in eligible_requested
    ]
    examples = []
    seen = set()
    for row in recommendations:
        if row["action"] not in seen:
            examples.append(row)
            seen.add(row["action"])
    return {
        "panel_config": panel["config"],
        "transformation": panel["transformation"],
        "config": audit["config"],
        "summary": by_window,
        "all_anchor_recommendation_counts": dict(
            sorted(Counter(r["action"] for r in audit["recommendations"]).items())
        ),
        "eligible_requested_recommendation_counts": dict(
            sorted(Counter(r["action"] for r in recommendations).items())
        ),
        "representative_eligible_recommendations": examples,
        "limitations": audit["limitations"],
        "retention_note": (
            "Full input and reproducible script retained; per-window API summaries "
            "and first example of each eligible status/action saved. "
            "No filtering or imputation before assessment."
        ),
    }


def _render(report: dict[str, Any], synthetic: dict[str, Any], output: Path) -> str:
    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError("This audit's optional figure requires matplotlib") from exc

    with matplotlib.rc_context({"svg.hashsalt": "openai4s-window-review-v1"}):
        fig = Figure(figsize=(13, 9), layout="constrained")
        FigureCanvasAgg(fig)
        axes = fig.subplots(2, 2)
        labels, coverage, sizes, status_counts = [], [], [], []
        for name, dataset in report["public_data"].items():
            for row in dataset["summary"]:
                labels.append(f"{name}\nw={row['window']}")
                coverage.append(
                    100 * row["eligible_anchor_count"] / row["anchor_count"]
                )
                sizes.append((row["eligible_anchor_count"], row["anchor_count"]))
                status_counts.append(row["eligible_noise_status_counts"])
        ax = axes[0, 0]
        colors = ["#2b6a9c", "#2b6a9c", "#8064a2", "#8064a2"]
        bars = ax.bar(range(len(labels)), coverage, color=colors)
        for bar, (eligible, all_anchors) in zip(bars, sizes):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 2,
                f"{eligible}/{all_anchors}",
                ha="center",
                fontsize=9,
            )
        ax.set(xticks=range(len(labels)), xticklabels=labels, ylim=(0, 65))
        ax.set_ylabel("Eligible first-post anchors (%)")
        ax.set_title("A. Both complete windows must fit")
        ax = axes[0, 1]
        status_colors = {
            "noise_baseline_short": ("Baseline < 5", "#5d8fb0"),
            "review_trend": ("Review trend", "#d59b32"),
            "review_serial_dependence": ("Review correlation", "#9e6ca6"),
            "high_noise_under_iid": ("High under IID", "#bf5b50"),
            "within_tolerance_under_iid": ("Within under IID", "#4d8d72"),
        }
        bottoms = [0.0] * len(labels)
        for status, (label, color) in status_colors.items():
            heights = [
                100 * counts.get(status, 0) / sum(counts.values()) if counts else 0
                for counts in status_counts
            ]
            ax.bar(
                range(len(labels)), heights, bottom=bottoms, label=label, color=color
            )
            bottoms = [before + height for before, height in zip(bottoms, heights)]
        ax.set(xticks=range(len(labels)), xticklabels=labels, ylim=(0, 110))
        ax.set_ylabel("Status among eligible anchors (%)")
        ax.set_title("B. Coverage does not establish stable noise")
        fig.legend(
            *ax.get_legend_handles_labels(),
            loc="outside lower center",
            ncol=5,
            fontsize=9,
        )
        for position, counts in enumerate(status_counts):
            if not counts:
                ax.text(position, 45, "No eligible\nanchors", ha="center", fontsize=9)
        ax = axes[1, 0]
        entity_colors = {"quiet": "#4d8d72", "noisy": "#2b6a9c", "trend": "#bf5b50"}
        for entity, color in entity_colors.items():
            rows = [row for row in synthetic["rows"] if row["id"] == entity]
            ax.plot(
                [int(r["period"]) for r in rows],
                [r["y"] for r in rows],
                color=color,
                label=entity,
            )
        ax.axvspan(10, 16, color="#aab9c6", alpha=0.15)
        ax.axvline(17, color="#555555", linestyle="--", label="Fixed review anchor")
        ax.set(xlabel="Synthetic period", ylabel="Outcome units")
        ax.set_title("C. Constructed patterns; no event or causal claim")
        ax.legend(fontsize=8)
        ax = axes[1, 1]
        for entity, color in entity_colors.items():
            rows = [
                row
                for row in report["synthetic"]["fixed_anchor_assessments"]
                if row["entity"] == entity
            ]
            ax.plot(
                [r["window"] for r in rows],
                [r["pre_statistics"]["noise_to_tolerance_ratio"] for r in rows],
                "o-",
                color=color,
                label=entity,
            )
        ax.axhline(1, color="#555555", linestyle="--", label="Tolerance = 0.5")
        ax.axvline(5, color="#777777", linestyle=":", alpha=0.6)
        ax.set(
            xticks=[3, 5, 7],
            xlabel="Pre/post periods per window",
            ylabel="(Pre SD / sqrt(n)) / tolerance",
        )
        ax.set_title("D. Advice: noisy 5 → 7; quiet retain 5; trend review")
        ax.text(
            0.35,
            0.96,
            "w=3: short baseline\nApproximation conditional on IID",
            transform=ax.transAxes,
            va="top",
            fontsize=8,
        )
        ax.legend(loc="upper right", fontsize=8)
        fig.suptitle("Window audit before event screening", fontsize=16)
        fig.savefig(
            output / "comparison.png",
            dpi=160,
            metadata={"Software": "OpenAI4S window audit"},
        )
        fig.savefig(
            output / "comparison.svg",
            metadata={"Date": None, "Creator": "OpenAI4S window audit"},
        )
        svg = output / "comparison.svg"
        svg.write_text(
            "\n".join(
                line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )
    return matplotlib.__version__


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in OUTPUT_FILES):
        raise ValueError(
            "Choose output with none of this audit's artifacts present; "
            "existing outputs are preserved"
        )
    module = _skill()
    if not hasattr(module, "assess_windows"):
        raise RuntimeError("Updated skill with assess_windows is required")
    provenance = json.loads((INPUTS / "sources.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": (
            "Pre-screening coverage and pre-only variability review; "
            "no screen, estimator, treatment-date or old result changes"
        ),
        "assumptions": {
            "mpdta": (
                "Noise tolerance 0.05 log-employment units; educational "
                "diagnostic assumption, not a paper threshold"
            ),
            "base_stagg": (
                "Noise tolerance 1 outcome unit; educational diagnostic "
                "assumption, not a paper threshold"
            ),
            "synthetic": (
                "Noise tolerance 0.5 outcome units; constructed deterministic "
                "illustration, not evidence of independent noise"
            ),
            "minimum_noise_periods": 5,
            "independence": (
                "SD/sqrt(n) assumes stable independent noise. Displayed ratios "
                "and advice are descriptive, not confidence intervals, power "
                "or event-screen calibration."
            ),
        },
        "public_data": {},
    }
    specs = [
        ("mpdta", "countyreal", "year", "lemp", "year", 2, [2, 3], 0.05),
        ("base_stagg", "id", "year", "y", "integer", 3, [3, 5], 1.0),
    ]
    for name, entity, time, metric, frequency, width, options, tolerance in specs:
        path = INPUTS / f"{name}.csv"
        expected = provenance["datasets"][name]["csv_sha256"]
        if _sha256(path) != expected:
            raise ValueError(f"Input checksum mismatch: {name}")
        panel = module.prepare_panel(
            module.read_csv(path),
            entity=entity,
            time=time,
            numeric_columns=[metric],
            frequency=frequency,
        )
        assessment = module.assess_windows(
            panel,
            window=width,
            window_options=options,
            noise_tolerance={metric: tolerance},
            min_noise_periods=5,
        )
        report["public_data"][name] = {
            "input": {
                "filename": path.name,
                "sha256": expected,
                "source": provenance["datasets"][name]["url"],
            },
            **_compressed_audit(panel, assessment),
        }
    _synthetic(output / "synthetic.csv")
    synthetic = module.prepare_panel(
        module.read_csv(output / "synthetic.csv"),
        entity="id",
        time="period",
        numeric_columns=["y"],
        frequency="integer",
    )
    assessment = module.assess_windows(
        synthetic,
        window=5,
        window_options=[3, 5, 7],
        noise_tolerance={"y": 0.5},
        min_noise_periods=5,
    )
    report["synthetic"] = {
        **_compressed_audit(synthetic, assessment),
        "construction": (
            "32 periods, base10; repeated fluctuations[-1.3,0.7,0.4,-0.9,1.1] "
            "with amplitude0.04 quiet/trend or1.1 noisy; "
            "trend adds0.35*(period-1); no treatment or jump"
        ),
        "fixed_anchor": "17",
        "fixed_anchor_assessments": [
            a for a in assessment["assessments"] if a["time"] == "17"
        ],
        "fixed_anchor_recommendations": [
            r for r in assessment["recommendations"] if r["time"] == "17"
        ],
    }
    _write_json(output / "audit.json", report)
    matplotlib_version = _render(report, synthetic, output)
    _write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "matplotlib": matplotlib_version,
            "skill_sha256": _sha256(SKILL_PATH),
            "script_sha256": _sha256(Path(__file__)),
            "artifact_sha256": {
                name: _sha256(output / name)
                for name in OUTPUT_FILES
                if name != "environment.json"
            },
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    report = run(args.output.resolve())
    print(
        json.dumps(
            {name: data["summary"] for name, data in report["public_data"].items()},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
