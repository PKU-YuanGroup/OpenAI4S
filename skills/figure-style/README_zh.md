# Figure Style Skill

这个渐进披露 Skill 是科学 figure 的正确性与可读性 checklist，并提供可选 matplotlib helper。它刻意定义按角色映射的规则，而不是固定 house style；自身无法验证 caller 数据的科学真实性。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 覆盖 data fidelity、claim-title 检查、label economy、axis/scale、color、typography、chart choice、layout、anti-pattern 与 render-then-inspect QA 的 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`apply_figure_style` 设置 rcParams；`set_frame`、`panel_letter` 与 label helper 统一呈现；palette/bar/strip/line-label helper 实现常用编码；`panel_crops` 返回保存图像的 crop box 用于视觉 QA。 |

## 直属子目录

无。

Matplotlib 属于可选 runtime 状态，必须安装在所选内核。Collision check 通过不能替代感知或领域审阅。
