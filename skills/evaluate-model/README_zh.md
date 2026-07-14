# Evaluate Model Skill

这个渐进披露 Skill 提供 held-out evaluation recipe 与纯标准库 metric helper。加载 Skill 时可把 [`kernel.py`](kernel.py) 挂入持久 Python 内核；它不会自动产生模型、prediction、split 或科学结论。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义 leakage-aware evaluation、baseline/subgroup/uncertainty 检查、binary/regression 调用与最小报告契约。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`binary_classification_metrics` 计算 confusion count、accuracy、precision/recall/specificity/F1 与 tie-aware ROC AUC；`regression_metrics` 计算 MAE/RMSE/bias/R²；`bootstrap_ci` 为 scalar observation 返回确定性 percentile interval。 |

## 直属子目录

无。

这些 helper 只汇总传入 observation；不会验证数据是否 held out、independent、representative 或具临床意义。
