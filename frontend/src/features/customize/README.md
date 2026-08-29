# frontend/src/features/customize

[中文说明](README_zh.md)

F-19 Customize domain logic. Tab state machine, timer lease (unmount clears every poll), same-origin API client, Volcengine/DataPro/Doubao helpers. Window exports `openCust` / `custTab` / `telemetryRow` are assigned here, not in `compat/window-exports.ts`. Capability guards use `isReady` from `compat/stub.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`actions.ts`](actions.ts) | `openCust` / `custTab` / `closeCust`. Bumps generation so the pane remounts. |
| [`api.ts`](api.ts) | `api` / `ApiError` / `apiErrorText`. Path must be a single leading slash. |
| [`environment.ts`](environment.ts) | Skill readiness note; `sanitizeStandardProfileReadiness`. |
| [`host.ts`](host.ts) | `hint` / `openViewer` / `loadModels` via `isReady`; `effProject`. |
| [`index.ts`](index.ts) | `installCustomize` / `bootCustomize` and public re-exports. |
| [`layout.ts`](layout.ts) | `os-layout` density. `setLayout` / `applyLayout`. |
| [`memory.ts`](memory.ts) | Memory scopes. Never send the literal `"default"`. |
| [`models.ts`](models.ts) | Local-endpoint sanitizer, protocol catalogue, capability-receipt reader. |
| [`state.ts`](state.ts) | `customizeOpen` / `customizeTab` / `customizeGeneration` / `nestedEditor`. |
| [`tabs.ts`](tabs.ts) | Nine tab ids; `agents` → `specialists`. |
| [`tabs.test.ts`](tabs.test.ts) | Tab state machine. |
| [`telemetry.ts`](telemetry.ts) | Consent drain loop; contract `telemetryRow(host)`. |
| [`timers.ts`](timers.ts) | Per-mount timer lease. Dispose on unmount. |
| [`timers.test.ts`](timers.test.ts) | Unmount leaves zero timers; Volcengine key poll; vendor helpers; window exports. |
| [`vendors.ts`](vendors.ts) | DataPro index-complete; Doubao dedicated-source check. |
| [`volcengine.ts`](volcengine.ts) | Quota math; key-poll 2500/5000×24 bound to a lease. |
