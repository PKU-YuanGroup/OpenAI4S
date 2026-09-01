# frontend/src/components/timeline

[English](README.md)

Action Timeline 孤岛的 Preact 容器。只负责生命周期：挂 `#dock-timeline`、调用 `renderActionTimeline`、卸载时销毁 `_timelineView`。类名（`.timeline-ledger-row` 等）为 E2E 冻结。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`index.ts`](index.ts) | 再导出 `Timeline`。 |
| [`Timeline.css`](Timeline.css) | 孤岛布局：46px 行高、绝对定位行、overview SVG 尺寸。视觉 token 仍归 F-21。 |
| [`Timeline.tsx`](Timeline.tsx) | `#dock-timeline` 宿主与挂载/卸载。 |
