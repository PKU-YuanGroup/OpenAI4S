# Lab

[中文说明](README_zh.md)

Simulation-only bench; REST confirms all displayed state. No evaluation or provider action is rendered.

## Files

| File | Responsibility |
| --- | --- |
| `api.ts` | Typed REST client and injectable fetch seam. |
| `api.test.ts` | REST paths, payloads and HTTP outcome tests. |
| `types.ts` | Public Lab contract types. |
| `state.ts` | Session and run guards, confirmed state, retained retry intents. |
| `state.test.ts` | Races, retry identity and state transition tests. |
| `fixtures.ts` | Sensor-only fixtures for offline tests. |
| `view.tsx` | Simulation bench, observations and manifest-driven controls. |
| `view.test.tsx` | Rendered controls, unknown values and public summaries. |
| `copy.ts` | Feature-local English and Chinese copy. |
| `lab.css` | Scoped bench layout and sensor graphics. |
| `boot.ts` | Dock mount, session lifecycle and WebSocket refresh hooks. |
| `boot.test.ts` | WebSocket coalescing, reconnect and replay-gap tests. |
| `summary.ts` | Whitelisted command and permission summaries. |
