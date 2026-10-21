# 合成实验输出

[English](README.md)

两套分支以固定筛选配置执行实际 Skill。evaluation.json 保留插补样本数、相同遮挡比较、全部数值候选和无条件／合格真事件恢复。后续 verification.json 可保存独立验证，不计入运行 manifest。

## 文件

| 条目 | 用途 |
| --- | --- |
| `artifact_manifest.json` | 记录运行环境、源或产物校验和。 |
| `environment.json` | 记录运行环境、源或产物校验和。 |
| `evaluation.json` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `fit_profile.json` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `imputation_errors.csv` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `method_decisions.json` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `overview.png` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `overview.svg` | 保留的生成数据、诊断或分析产物；具体含义见实验报告。 |
| `filled-outcome/` | 独立保留的 Skill 输出包。 |
| `main/` | 独立保留的 Skill 输出包。 |

可选独立验证：`verification.json`。
