# 随机面板 Skill 实验

[English](README.md)

这个固定随机种子的离线实验直接执行
`skills/panel-data-preprocessing/kernel.py` sidecar。它生成随机面板，单独保留完整
生成真值，检验显式数值清理、类别／文本编码、面板审计、窗口准备与探索性事件发现。
详见[结果与解释](REPORT_zh.md)。

## 复跑

使用科学环境中安装了 matplotlib 的 Python。生成、清理与核对使用标准库；绘图
需要 matplotlib。从仓库根目录运行，写入新的产物目录，保留归档运行：

```bash
python3 experiments/panel-cleaning-synthetic/run_experiment.py --output /tmp/openai4s-panel-random-new
python3 experiments/panel-cleaning-synthetic/verify_experiment.py --experiment /tmp/openai4s-panel-random-new
```

默认种子为 `20261007`，已有生成产物包会拒绝覆盖。归档输入与输出在本目录；
数值及图像字节比较依赖记录的 Python／matplotlib 环境。核对器独立重算结果，
并在新的目录再运行一遍实验。

## 设计

32 个字符串实体 ID 各有 48 个潜在时期，预设八类场景：稳定、持续上升、持续
下降、高噪声变化、线性趋势、单点尖峰、季节性与晚进入。在这份人工设计中，日历
记录缺失与测量缺失独立随机生成。原始行数在缺失记录后另行记录；完整真值不参与训练。

清理器只拟合原始数据第 1–16 期。基线数据画像与变量角色用于说明均值、中位数
和 KNN 的选择；与真值的比较发生在选择之后。数值邻居限制在实体内，类别／文本
映射则使用跨实体的基线样本。后期新类别与新词检验冻结映射下的变换。

主实验保留结果变量缺失。对照实验显式填补结果变量，检查填补标记及填补结果被
事件窗口排除的行为。筛选预设窗口 3、阈值 3、最小变化 2，噪声预算为 0.5 个
结果单位；审查宽度 3／5／7／9。建议不会改变配置的筛选窗口。生成时已知的事件
日期与数值候选分别记录。

## 文件

| 文件或目录 | 用途 |
| --- | --- |
| `run_experiment.py` | 固定种子生成器、真实 Skill 调用、真值评估与诊断图。 |
| `verify_experiment.py` | 独立核对拟合、变换、窗口、匹配、哈希与复跑。 |
| `REPORT.md` | 英文结果、审计发现与解释。 |
| `REPORT_zh.md` | 中文结果、审计发现与解释。 |
| `inputs/` | 原始记录、完整真值与生成设置。 |
| `results/` | 基线画像、决定、评估、环境、总览和两份 Skill 产物包。 |

这是一次合成样本上的功能检验，不能据此确定检测器的普遍性能或因果效应。
