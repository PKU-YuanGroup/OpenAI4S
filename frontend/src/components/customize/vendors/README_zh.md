# frontend/src/components/customize/vendors

[English](README.md)

从九个 Customize tab 里隔离出来的 vendor 卡。DataPro 在 Connectors，豆包在 Network，火山在 Models。Key 轮询绑在该 tab 的定时器租约上。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`datapro.tsx`](datapro.tsx) | DataPro 凭证 + 检索卡（`volcengine-datapro`）。 |
| [`doubao.tsx`](doubao.tsx) | 豆包搜索卡。专用 source，不回退 Tavily。 |
| [`volcengine.tsx`](volcengine.tsx) | 火山 SSO / 套餐 / key 轮询面板。 |
