# 合成 DiD Skill 验证

[English](README.md)

本离线实验执行实际的 `skills/did-analysis/kernel.py` sidecar，并用 statsmodels 与 SciPy 独立核对数值计算。六个预先指定的数据生成过程覆盖已知效应、零效应、违反前趋势、相关聚类噪声、三重差分和异质交错处理。报告保留有限聚类下的推断校准问题，不把统计比例设置为通过门槛。

## 复现

使用安装了 `requirements-analysis.txt` 可选依赖的分析 Python 环境。OpenAI4S 核心不增加依赖。保留的 `inputs/` 与 `results/` 不覆盖：目标目录已经有输入或结果子目录时，运行器拒绝执行；审计报告已经存在时，验证器拒绝覆盖。

```bash
python experiments/did-skill-validation/verify_experiment.py --replay --report /tmp/did-independent-audit.json
python experiments/did-skill-validation/run_experiment.py --output /tmp/did-new-bundle --plots
python experiments/did-skill-validation/verify_experiment.py --bundle /tmp/did-new-bundle --replay
```

再次运行时选择新的 `/tmp` 路径。`--plots` 执行 Skill 中有可选依赖保护的 matplotlib 绘图器。重放在临时目录中再次运行实际 Skill，并比较每个保留的确定性产物；启用绘图时也比较 SVG 和 PNG。字节比较依赖记录的 Python、包和字体环境；独立数值比较使用明确容差。运行前记录源文件哈希，清单最终确定前再次核查。

## 内容

| 条目 | 用途 |
| --- | --- |
| `prespecification.json` | 运行前规定的 DGP、处理聚类、事件日期、目标量、固定种子、模拟规模与推断范围。 |
| `requirements-analysis.txt` | 独立数值分析与可选绘图使用的版本。 |
| `run_experiment.py` | 生成并保存输入、执行实际 Skill API 和流程、保留请求中的拒绝记录并冻结校验和。 |
| `verify_experiment.py` | 独立重新计算回归、完整联合协方差、t/F 推断、全部蒙特卡洛决策，并核查产物哈希与重放。 |
| `REPORT.md` | 科学发现、校准限制与精确复现范围。 |
| `REPORT_zh.md` | 中文科学报告。 |
| `inputs/` | 六个长格式示例面板，以及无损宽格式 CSV 中保留的全部 512 个模拟面板。 |
| `results/` | Skill 结果、流程导出与诊断、保留的拒绝、蒙特卡洛摘要、环境与独立审计。 |

历史 `experiments/did-arxiv-replication/` 档案不是输入，也没有修改。本实验提供合成数据验证，不声称经验因果结论，也不声称完整的条件调整、双重稳健 Callaway–Sant'Anna bootstrap 复现。

当前产物清单 SHA-256 是 `38e7d5a81574814b47d534afde687c897880eb7886c4af1888cc490ebac57994`。Skill 的无网络 frontmatter 更新后，完整重跑得到的 45 个确定性产物全部不变。对恢复后的仓库产物包执行最终验证器，通过 **12,237 个检查，零失败**。先前本地输入/结果包与比较记录仍保留在仓库之外的本地档案；当前源/审计哈希与刷新溯源详见 `REPORT_zh.md`。
