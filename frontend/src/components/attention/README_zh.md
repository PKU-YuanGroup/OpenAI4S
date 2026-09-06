# frontend/src/components/attention

[English](README.md)

M-02 仪表盘「需要处理」卡片。由 `features/attention/boot.ts` 挂到 `#dash-attention`（插在 `.dash-grid` 上方）。卡片是真正的 button——点击按闭集 target 本地生成导航。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`AttentionCard.tsx`](AttentionCard.tsx) | 单卡：安全摘要、项目/会话、更新时间、下一动作提示。 |
| [`AttentionStream.tsx`](AttentionStream.tsx) | 绑定车道局部 `attentionCards` signal 的卡片列表。 |
| [`attention.css`](attention.css) | 车道局部卡片样式。全局 token 归 F-21。 |
| [`index.ts`](index.ts) | 再导出 `AttentionStream` / `AttentionCard`。 |
