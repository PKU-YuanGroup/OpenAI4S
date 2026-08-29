# frontend/src/compat

[中文说明](README_zh.md)

F-05 E2E compatibility layer. `tests/webui-contract.md` is the input inventory. Later F-series lanes may append one window assignment per line below the `// === lane additions ===` marker in `window-exports.ts` and must not edit the rest of that file.

## Files

| File | Responsibility |
| --- | --- |
| [`window-exports.ts`](window-exports.ts) | F-01 globals on `window`, `window.S` Proxy (get/set ↔ signals), lane-additions marker. |
| [`window-exports.test.ts`](window-exports.test.ts) | Proxy read/write, identity of `_timelineView` / `actionTimeline` / `executionQueue`, nested writes. |
