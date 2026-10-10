# DiD arXiv 复现实验

[English](README.md)

实际调用[面板预处理 Skill](../../skills/panel-data-preprocessing/SKILL.md)，完成两个范围明确的官方示例复现：Callaway–Sant'Anna 使用公开县级就业数据，Sun–Abraham 使用公开固定模拟数据。**24/24 个参考值核对通过。** 当前未复现论文完整实证样本与推断过程。方法、限制和事件探索发现见[实验报告](REPORT_zh.md)。

## 离线运行

从仓库根目录运行，使用 Python 3.13、numpy 2.3.5 和 matplotlib 3.10.9 的科研环境。已保存 CSV，因此正常分析无需 R、pyreadr、pandas、模型凭据或联网。独立验证额外需要 statsmodels 0.14.5。`requirements-analysis.txt` 固定分析库版本；不同机器的间接依赖和字体仍可能不同，因此图像字节一致只在已记录环境下验证。

```bash
python experiments/did-arxiv-replication/run_experiment.py --output /tmp/did-new-run
python experiments/did-arxiv-replication/verify_experiment.py --results /tmp/did-new-run
```

使用新的空输出目录，实验拒绝覆盖既有分析产物。本仓库已保存核验过的 `results/`。验证会独立回归、在临时目录完整重跑，并将 verification.json 写入所选结果目录；验证报告可重新生成，分析产物保留。

保存的 `results/` 已于 2026-10-10 使用当前 Skill 实际重跑，并通过完整独立验证器：24/24 个参考值检查与 48/48 个产物字节比较全部通过。验证器仍会拒绝不同的 Skill 源码哈希。已有估计、面板、候选 CSV 和原有图形与先前运行逐字节一致；现在增加了十个窗口审查与图形文件。报告记录刷新前后的源哈希及更新范围。独立的 `window-review/` 也用当前 Skill 执行两次，五个生成文件全部逐字节重现。

需要恢复输入时，另行显式运行联网获取步骤：

```bash
uv run --no-project --with pyreadr==0.5.7 --with pandas==3.0.3 python experiments/did-arxiv-replication/fetch_inputs.py
```

如环境不同，以 `inputs/sources.json` 中原始获取版本为准。已有输入保留；CSV 或溯源不一致时拒绝改写。

## 文件与目录

| 文件或目录 | 用途 |
| --- | --- |
| `fetch_inputs.py` | 获取固定版本公开 R 数据，无损转换 CSV 并记录溯源；显式联网操作。 |
| `run_experiment.py` | 调用实际 Skill，完成面板审计、探索绘图、两套 DiD 估计与公开参考值核对。 |
| `verify_experiment.py` | 使用显式固定效应／变化回归独立核对，检查输入完整性及重跑产物字节一致。 |
| `reference_values.json` | 少量人工摘录的官方点估计、来源链接、复现范围与小数舍入容差。 |
| `requirements-analysis.txt` | 固定科研库版本，不更改核心依赖。 |
| `REPORT.md` | 英文实验报告与解释。 |
| `REPORT_zh.md` | 中文实验报告与解释。 |
| `inputs/` | 原始公开输入、CSV、GPL-3 许可与来源。 |
| `results/` | 已验证的处理后面板、图形、估计、运行环境与核对记录。 |
| `window-review/` | 独立离线扩展，审查筛选前的数据覆盖与事前窗口波动；保留原有复现结果。 |

## 查看结果

总览图是 `results/comparison.png`；模拟数据的 `results/sun-abraham/candidate-review/` 展示暴露日期与数值变化的区别。短真实面板不能支持默认的两个三期窗口；两期敏感性探索得到更多候选。两套配置均保留，均未用于选择 DiD 处理日期。此次没有置信区间或政策因果结论。

[窗口审查](window-review/README_zh.md)另行使用两套保留输入比较数据充分性与波动建议，并通过明确标注的构造示例展示何时考虑扩大窗口、何时先审查趋势。
