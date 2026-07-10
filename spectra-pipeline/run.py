"""Entry point: build library -> synthesise a dirty mixture -> run the config
search loop -> write a diagnostic report + figures.

Usage:
    python run.py [--seed N] [--budget N] [--n-components 2|3] [--max-minerals N]
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src.data import build_library
from src.loop import gt_score, search
from src.synth import synth_mixture

OUT_ROOT = os.path.join(os.path.dirname(__file__), "outputs")


def make_figures(case, lib, outcome, fig_dir):
    os.makedirs(fig_dir, exist_ok=True)
    res = outcome.best_result
    grid = lib.grid

    # 1. overlay: processed target vs reconstruction
    plt.figure(figsize=(10, 4))
    plt.plot(grid, res.processed, label="observed (preprocessed)", lw=1.2)
    plt.plot(grid, res.recon, label="NNLS reconstruction", lw=1.2, alpha=0.8)
    plt.xlabel("Raman shift (cm$^{-1}$)"); plt.ylabel("norm. intensity")
    plt.title("Observed vs reconstruction"); plt.legend()
    plt.tight_layout(); plt.savefig(os.path.join(fig_dir, "overlay.png"), dpi=120); plt.close()

    # 2. residual
    plt.figure(figsize=(10, 3))
    plt.plot(grid, res.processed - res.recon, color="crimson", lw=1.0)
    plt.axhline(0, color="k", lw=0.5)
    plt.xlabel("Raman shift (cm$^{-1}$)"); plt.ylabel("residual")
    plt.title("Residual (observed - reconstruction)")
    plt.tight_layout(); plt.savefig(os.path.join(fig_dir, "residual.png"), dpi=120); plt.close()

    # 3. iteration curve
    its = [h["iteration"] for h in outcome.history]
    rmse = [h["residual_rmse"] for h in outcome.history]
    f1 = [h["gt"]["f1"] for h in outcome.history]
    fig, ax1 = plt.subplots(figsize=(8, 4))
    ax1.plot(its, rmse, "o-", color="tab:blue", label="residual RMSE")
    ax1.set_xlabel("iteration"); ax1.set_ylabel("residual RMSE", color="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(its, f1, "s--", color="tab:green", label="component F1 (GT)")
    ax2.set_ylabel("component F1", color="tab:green"); ax2.set_ylim(-0.05, 1.05)
    plt.title("Loop progress"); fig.tight_layout()
    plt.savefig(os.path.join(fig_dir, "iterations.png"), dpi=120); plt.close()

    # 4. raw dirty spectrum
    plt.figure(figsize=(10, 3))
    plt.plot(grid, case.spectrum, color="gray", lw=0.9)
    plt.xlabel("Raman shift (cm$^{-1}$)"); plt.ylabel("intensity (a.u.)")
    plt.title("Synthetic dirty spectrum (input)")
    plt.tight_layout(); plt.savefig(os.path.join(fig_dir, "input.png"), dpi=120); plt.close()


def write_report(case, outcome, path):
    res = outcome.best_result
    gt = gt_score(res, case)
    d = res.diagnostics

    lines = []
    lines.append("# 光谱成分识别诊断报告\n")
    lines.append(f"- 生成时间(相对): run 完成\n")

    lines.append("## 1. 结论：识别到的成分与比例\n")
    lines.append("| 成分 | 估计比例 | 支持特征峰 (cm⁻¹) |")
    lines.append("|---|---|---|")
    for name, frac in sorted(res.fractions.items(), key=lambda kv: -kv[1]):
        peaks = ", ".join(str(p) for p in res.support.get(name, [])) or "—"
        lines.append(f"| {name} | {frac*100:.1f}% | {peaks} |")
    lines.append("")

    lines.append("## 2. 可信性诊断\n")
    lines.append(f"- 重构拟合相关 (Pearson): **{d['fit_corr']:.3f}**")
    lines.append(f"- 残差 RMSE: **{d['residual_rmse']:.4f}**")
    lines.append(f"- 解释能量占比: **{d['explained_energy']*100:.1f}%**")
    lines.append(f"- 残差残留显著峰数: **{d['n_residual_peaks']}** "
                 f"{'(提示可能漏成分)' if d['n_residual_peaks'] else '(无明显未解释峰)'}")
    if d["residual_peak_positions"]:
        lines.append(f"  - 位置: {d['residual_peak_positions']}")
    lines.append(f"- 综合可信度: **{d['reliability'].upper()}**")
    lines.append("")

    lines.append("## 3. 与真值对比（合成算例，用于验证）\n")
    lines.append(f"- 真实成分: {case.true_names}")
    tf = {k: round(v, 3) for k, v in case.true_fractions.items()}
    lines.append(f"- 真实比例: {tf}")
    lines.append(f"- 成分识别 Precision/Recall/F1: "
                 f"{gt['precision']:.2f} / {gt['recall']:.2f} / **{gt['f1']:.2f}**")
    lines.append(f"- 比例估计 MAE: **{gt['fraction_mae']:.3f}**")
    lines.append("")

    lines.append("## 4. 循环搜索到的最优配置\n")
    lines.append("```json")
    lines.append(json.dumps(outcome.best_config, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append(f"\n- 迭代次数: {len(outcome.history)}  |  最优目标值(残差+简约罚): {outcome.best_objective:.4f}\n")

    lines.append("## 5. 迭代过程摘要\n")
    lines.append("| iter | best | rel_residual | fit_corr | F1(GT) | MAE(GT) | 可信度 | 识别成分 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for h in outcome.history:
        star = "★" if h["is_best"] else ""
        lines.append(f"| {h['iteration']} | {star} | {h['rel_residual']:.4f} | "
                     f"{h['fit_corr']:.3f} | {h['gt']['f1']:.2f} | {h['gt']['fraction_mae']:.3f} | "
                     f"{h['reliability']} | {list(h['identified'])} |")
    lines.append("")

    lines.append("## 6. 图\n")
    lines.append("![input](figures/input.png)\n")
    lines.append("![overlay](figures/overlay.png)\n")
    lines.append("![residual](figures/residual.png)\n")
    lines.append("![iterations](figures/iterations.png)\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--n-components", type=int, default=None)
    ap.add_argument("--max-minerals", type=int, default=120)
    ap.add_argument("--noise", type=float, default=0.02)
    args = ap.parse_args()

    print("Building library from RRUFF excellent_oriented ...")
    lib = build_library("excellent_oriented", max_minerals=args.max_minerals)
    print(f"  library: {len(lib.names)} minerals, grid {lib.grid[0]:.0f}-{lib.grid[-1]:.0f} "
          f"cm^-1 ({len(lib.grid)} pts)")

    rng = np.random.default_rng(args.seed)
    case = synth_mixture(lib, rng, n_components=args.n_components, noise_level=args.noise)
    print(f"Synthesised mixture: {case.true_names} "
          f"fractions={ {k: round(v,3) for k,v in case.true_fractions.items()} }")

    run_dir = os.path.join(OUT_ROOT, f"run_seed{args.seed}")
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "iterations.jsonl")

    print("\nRunning config-search loop ...")
    outcome = search(case, lib, seed=args.seed, budget=args.budget, log_path=log_path)

    make_figures(case, lib, outcome, os.path.join(run_dir, "figures"))
    write_report(case, outcome, os.path.join(run_dir, "report.md"))
    print(f"\nDone. Report + figures + log in: {run_dir}")


if __name__ == "__main__":
    main()
