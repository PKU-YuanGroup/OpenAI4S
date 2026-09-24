# frontend/src/features/customize

[中文说明](README_zh.md)

F-19 Customize domain logic. Tab state machine, timer lease (unmount clears every poll), same-origin API client, Volcengine/DataPro/Doubao helpers. Window exports `openCust` / `custTab` / `telemetryRow` are assigned here, not in `compat/window-exports.ts`. Capability guards use `isReady` from `compat/stub.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`actions.ts`](actions.ts) | `openCust` / `custTab` / `closeCust`. `custTab` bumps generation so the pane remounts; `refreshCustTab` has the tab on screen re-read in place after a write and leaves a tab the user has left alone. |
| [`load.ts`](load.ts) | Bounded first load per tab generation: `beginCustomizeLoad` / `markCustomizeLoaded` / `markCustomizeFailed` / `markCustomizeTimedOut`, `CUST_LOAD_TIMEOUT_MS` (30 s, as app.js). |
| [`load.test.ts`](load.test.ts) | A `custTab()` starts a pending load; marks settle it once; the deadline only fires for the generation still pending. |
| [`dismiss.ts`](dismiss.ts) | How Customize closes. `customizeOpen` is the one owner: while `#cust` is the modal on top chrome's trap leaves Escape to Customize (nested editor first, then the modal), and any other path that hides `#cust` is followed by `closeCust()`. A backdrop closes only for a press that did not begin inside the dialog. |
| [`dismiss.test.ts`](dismiss.test.ts) | Escape in a nested editor closes only the editor; a modal above Customize keeps its Escape; an IME Escape is ignored; `#cust` hidden by another path closes Customize; a backdrop click whose press began inside the dialog does not close it. |
| [`api.ts`](api.ts) | `api` / `ApiError` / `apiErrorText`. Path must be a single leading slash. |
| [`environment.ts`](environment.ts) | Skill readiness note; `sanitizeStandardProfileReadiness`. |
| [`host.ts`](host.ts) | `hint` / `openViewer` via `isReady`; re-exports the real `loadModels` from `models.ts` (no window bridge); `effProject`, and `customizeProject` (a computed of it that notifies only when the project changes, for rendering). |
| [`index.ts`](index.ts) | `installCustomize` / `bootCustomize` and public re-exports. `window.openCust` / `custTab` load and mount the Settings UI first (`ensureCustomizeMounted` / `openCustomize`); a failed load says so and the next open retries. |
| [`layout.ts`](layout.ts) | `os-layout` density. `setLayout` / `applyLayout`. |
| [`memory.ts`](memory.ts) | Memory scope ids and names. Never send the literal `"default"`. |
| [`models.ts`](models.ts) | Local-endpoint sanitizer, protocol catalogue, capability-receipt reader; `loadModels` / `chooseComposerModel` fill the composer `#model-select` stores from `GET /models` and `PUT /models/default` (called by `bootCustomize`). |
| [`mount.ts`](mount.ts) | `mountCustomize()`: renders the Settings modal into `#cust-root`. The only importer of the Customize component tree, loaded on first open (`ensureCustomizeMounted`), so the build keeps it out of the first-load bundle. |
| [`models.test.ts`](models.test.ts) | `loadModels` requests `/models` and fills `models` / `defaultModel` / `defaultModelName` (a profile entry is named by its model, ids stay verbatim); `bootCustomize` wires it. |
| [`state.ts`](state.ts) | `customizeOpen` / `customizeTab` / `customizeGeneration` / `customizeRefresh` / `nestedEditor`. |
| [`tabs.ts`](tabs.ts) | Nine tab ids; `agents` → `specialists`. |
| [`tabs.test.ts`](tabs.test.ts) | Tab state machine; an in-place refresh re-reads only the tab on screen and never remounts it or closes its editor. |
| [`telemetry.ts`](telemetry.ts) | Consent drain loop; contract `telemetryRow(host)`. |
| [`timers.ts`](timers.ts) | Per-mount timer lease. Dispose on unmount. |
| [`timers.test.ts`](timers.test.ts) | Unmount leaves zero timers; Volcengine key poll; vendor helpers; window exports. |
| [`vendors.ts`](vendors.ts) | DataPro index-complete; Doubao dedicated-source check. |
| [`volcengine.ts`](volcengine.ts) | Quota math; key-poll 2500/5000×24 bound to a lease. |
