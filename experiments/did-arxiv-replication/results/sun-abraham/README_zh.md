# Sun–Abraham 示例

[English](README.md)

公开固定模拟的 95 个个体 × 十期；点估计比较同时保存已知模拟真值与普通 TWFE。

## 文件

| 文件 | 用途 |
| --- | --- |
| `candidate_events.csv` | 已知日期、暴露变化与探索性数值突变候选。 |
| `cohort_event_att.csv` | 未聚合的 Sun–Abraham 组别–事件时间系数。 |
| `dynamic_comparison.csv` | TWFE、交互加权估计、模拟真值与样本支持。 |
| `event_alignment.csv` | 候选日期与独立处理时间的对应关系。 |
| `event_alignment.json` | 窗口可用性与日期一致数量，仅作描述。 |
| `manifest.json` | Skill 配置、变换溯源与导出数据哈希。 |
| `metric-1.png` | 所选个体轨迹与事件标记，PNG。 |
| `metric-1.svg` | 所选个体轨迹与事件标记，SVG。 |
| `panel.csv` | 处理后的长面板、源行位置与当期暴露。 |
| `sample-coverage.png` | 全样本每期实体覆盖，PNG。 |
| `sample-coverage.svg` | 全样本每期实体覆盖，SVG。 |
| `screening_sensitivity.json` | 两期／三期窗口的完整筛选配置与候选。 |
| `summary.json` | 全样本覆盖、缺失审计与描述统计。 |
| `visualization.json` | 图形所选／未选实体、配置与诊断图哈希。 |
| `window-readiness.png` | 完整、未插补窗口的可用性与波动诊断，覆盖全部样本，PNG。 |
| `window-readiness.svg` | 同一全样本窗口审查图的矢量版本。 |
| `window_assessment.csv` | 每个实体、时间锚点与候选窗口的覆盖、排除原因与诊断。 |
| `window_assessment.json` | 完整窗口审查配置、汇总、逐锚点证据与建议。 |

## 目录

- `candidate-review/`: 内容与范围见目录内双语说明。
