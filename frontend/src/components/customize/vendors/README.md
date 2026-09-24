# frontend/src/components/customize/vendors

[中文说明](README_zh.md)

Vendor cards isolated from the nine Customize tabs. DataPro lives on Connectors; Doubao on Network; Volcengine on Models. Key polling is bound to the tab's timer lease.

## Files

| File | Responsibility |
| --- | --- |
| [`datapro.tsx`](datapro.tsx) | DataPro credential + search card (`volcengine-datapro`). |
| [`doubao.tsx`](doubao.tsx) | Doubao search card. Dedicated source; no Tavily fallback. |
| [`use-vendor-key.ts`](use-vendor-key.ts) | `useVendorKey`: the Agent Plan key state of the DataPro and Doubao cards, derived from the `config` prop on every render (`null` until read, nothing to save until then); a save's answer is kept only for the config it answered. |
| [`vendors.test.tsx`](vendors.test.tsx) | A card first rendered before its config read answers shows the key, connector and skill state that read brings once it lands; nothing can be toggled or saved before it does. |
| [`volcengine.tsx`](volcengine.tsx) | Volcengine SSO / plan / key-poll panel. State written after an await is an update of the state as it then is, never a spread of an earlier copy. |
| [`volcengine.test.tsx`](volcengine.test.tsx) | When the key wait runs out the panel keeps what the last recheck read, not the state from when the wait began. |
