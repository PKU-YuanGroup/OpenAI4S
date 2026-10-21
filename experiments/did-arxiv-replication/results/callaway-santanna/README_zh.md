# Callaway–Sant’Anna 示例

[English](README.md)

500 个县 × 五个年度；主探索使用三期窗口，无法在该短面板筛选突变，另行保留两期敏感性结果。

## 文件

| 文件 | 用途 |
| --- | --- |
| `candidate_events.csv` | 已知日期、暴露变化与探索性数值突变候选。 |
| `dynamic_att.csv` | C&S 动态聚合与变化的组别支持。 |
| `event_alignment.csv` | 候选日期与独立处理时间的对应关系。 |
| `event_alignment.json` | 窗口可用性与日期一致数量，仅作描述。 |
| `group_time_att.csv` | C&S 组别–时间点估计及处理前 pseudo-ATT。 |
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
