# Plan ML Experiment Skill

这个渐进披露 Skill 把 ML 问题整理为可复现、leakage-aware 的实验计划。纯标准库 sidecar 根据 caller metadata 生成确定性 split 与 manifest；它不会训练模型，也不能证明 split 在科学上合理。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 定义 dataset/target/unit framing、random/grouped/chronological split 选择、baseline、metric、ablation、seed、leakage 检查与最小 Artifact。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：确定性 `random_split`、稳定时间排序 `chronological_split`、leakage-safe `grouped_split`、canonical config fingerprint、文件 SHA-256，以及不虚构环境状态的 JSON-compatible experiment manifest。 |

## 直属子目录

无。

Group identifier 与 chronology 必须来自领域知识；helper 输出无法发现错误定义的 experimental unit。
