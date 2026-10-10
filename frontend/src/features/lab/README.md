# Lab

[中文说明](README_zh.md)

Simulation-only bench; REST confirms displayed state. Recorded history is read-only; export links pin exact Artifact versions. Evaluation export requires an explicit simulation-ground-truth opt-in; no provider action or truth values are rendered in the bench.

## Files

| File | Responsibility |
| --- | --- |
| `api.ts` | Typed REST client, operation deadlines and injectable fetch seam. |
| `api.test.ts` | REST paths, payloads and HTTP outcome tests. |
| `types.ts` | Public Lab contract types. |
| `state.ts` | Session/run guards, terminal intents, exact export receipts and paginated read-only replay. |
| `state.test.ts` | Races, retry identity and state transition tests. |
| `fixtures.ts` | Sensor-only fixtures for offline tests. |
| `view.tsx` | Simulation bench, terminal confirmations, exact Artifact links and recorded-history slider. |
| `view.test.tsx` | Rendered controls, unknown values and public summaries. |
| `copy.ts` | Feature-local English and Chinese copy. |
| `lab.css` | Scoped bench layout and sensor graphics. |
| `boot.ts` | Dock mount, session lifecycle and WebSocket refresh hooks. |
| `boot.test.ts` | WebSocket coalescing, reconnect and replay-gap tests. |
| `summary.ts` | Whitelisted command and permission summaries. |
