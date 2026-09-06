# frontend/src/components/customize/vendors

[中文说明](README_zh.md)

Vendor cards isolated from the nine Customize tabs. DataPro lives on Connectors; Doubao on Network; Volcengine on Models. Key polling is bound to the tab's timer lease.

## Files

| File | Responsibility |
| --- | --- |
| [`datapro.tsx`](datapro.tsx) | DataPro credential + search card (`volcengine-datapro`). |
| [`doubao.tsx`](doubao.tsx) | Doubao search card. Dedicated source; no Tavily fallback. |
| [`volcengine.tsx`](volcengine.tsx) | Volcengine SSO / plan / key-poll panel. |
