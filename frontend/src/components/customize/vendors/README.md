# frontend/src/components/customize/vendors

[中文说明](README_zh.md)

Vendor cards isolated from the nine Customize tabs. DataPro lives on Connectors; Doubao and Ollama web tools on Network; Volcengine on Models. Key polling is bound to the tab's timer lease.

## Files

| File | Responsibility |
| --- | --- |
| [`datapro.tsx`](datapro.tsx) | DataPro credential + search card (`volcengine-datapro`). The connector switch, the Skill button and an empty query answer inside the card; the key-state line is a live region (SCI-04). |
| [`doubao.tsx`](doubao.tsx) | Doubao search card. Dedicated source; no Tavily fallback. With no key the search would send, it says that a model key set only through `OPENAI4S_ARK_API_KEY` / `OPENAI4S_LLM_API_KEY` is not shared with search, rather than a bare "not configured" beside a model running on that key. |
| [`ollama.tsx`](ollama.tsx) | Independent Ollama web credential: read status, save/replace/clear through the secret broker without changing the model, with English/Chinese copy. |
| [`use-vendor-key.ts`](use-vendor-key.ts) | `useVendorKey`: the Agent Plan key state of the DataPro and Doubao cards, derived from the `config` prop on every render (`null` until read, nothing to save until then); a save's answer is kept only for the config it answered. `missing` says the read found no key the outbound resolver would send. `useModelKey` shares the key of the model in use after one confirmation (`{"use_model_key": true}`; offered only while no key is configured and `model_key_available`); the key never reaches the browser. A save's outcome (saved, refused, nothing typed) is the key-state line under the field, not the session status line (SCI-04). |
| [`vendors.test.tsx`](vendors.test.tsx) | A card first rendered before its config read answers shows the key, connector and skill state that read brings once it lands; nothing can be toggled or saved before it does. The Doubao card names the environment-key case when no key is saved; the Network allowlist says what its groups are, whether they apply under the current `egress_mode`, and which domains were approved. The Network tab tells app networking from kernel networking. Both cards offer "Use the current model's key" only while no key is configured and the server says the model's key can be shared, ask once, and post `{use_model_key:true}`. SCI-04: an empty or successful key save and a connector test answer beside their control, never through the status-line `hint`. |
| [`volcengine.tsx`](volcengine.tsx) | Volcengine SSO / plan / key-poll panel: `VolcenginePanel` holds the state and picks a step (`NotInstalled`, `Connecting`, `AwaitingCode`, `FailedLogin`, `SignedOut`, `Connected`); `accessActions` says what a signed-in account can do next. State written after an await is an update of the state as it then is, never a spread of an earlier copy. One sign-in start at a time; a tab opened for it is closed when no authorize URL comes back. |
| [`volcengine.test.tsx`](volcengine.test.tsx) | When the key wait runs out the panel keeps what the last recheck read, not the state from when the wait began. Pressing Connect twice opens one sign-in tab, which is closed rather than left blank when the daemon answers without an authorize URL. For each connection and access state: the buttons, notices, pickers and markers shown, and the request the main button sends. |
