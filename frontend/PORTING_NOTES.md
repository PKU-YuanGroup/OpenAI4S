# Porting notes

## F-03 frontend/ scaffold

This work item does not port domain logic from `app.js`. It creates the empty Preact 10 + `@preact/signals` + TypeScript (strict) + Vite + Vitest workspace. Later F-series items append one section each.

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| *(none — no domain kernel in F-03)* | `frontend/` workspace | Empty shell only. `base: '/static/dist/'`. `@vitejs/plugin-legacy` forbidden. `build.modulePreload.polyfill: false` so modulepreload is external `<link>` tags, not an inline polyfill. Build fails if any HTML contains a `<script>` without `src=`. |
| Static files served from this directory | `npm run build` writes `openai4s/server/webui/dist/` | Wheel still Node-free. Serving `dist/index.html` behind `OPENAI4S_WEBUI_NEXT=1` is F-04. `theme-bootstrap.js` and `scientific_renderers.js` stay classic scripts (F-09 / unchanged). |

## F-05 stores + S Proxy

The `S` singleton becomes seven `@preact/signals` modules plus a `window.S` Proxy. No render, WS, or kernel logic is ported. Field-by-field map: [`src/stores/MIGRATION.md`](src/stores/MIGRATION.md).

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `const S = { … }` 120–131 | `src/stores/{session,stream,notebook,timeline,artifacts,ui,customize}.ts` | Same defaults (`dock.open: false`, `activeTab: "notebook"`, `workbenchErrors: {}`, `variableInspector` shape, `filesScope: "frame"`). |
| `S._seqSeen` 5176, `S._streamEpoch` 5180 | `stream._seqSeen` / `stream._streamEpoch` | Nested `S._seqSeen[rid] = sq` mutates the stored object. Epoch is a scalar. |
| `S._artBust` 5323, `S._tbl` 5334 | `artifacts._artBust` / `artifacts._tbl` | Objects stored by reference; nested write/delete keep identity. |
| `const _kc = { … }` 9954 | `notebook._kc` (not on `S`) | Same keys (`id/st/stAt/stBusy/envs/cur/envAt/envBusy`). F-14 invalidates. |
| `_timelineView` / `actionTimeline` / `executionQueue` 124, 129 | `timeline.*` | **By-reference.** Nested writes (`searchQuery`, `collapsedTurns.add`) do not clone. |
| `ACTION_TIMELINE_{PAGE_SIZE,ROW_HEIGHT,OVERSCAN,OVERVIEW_WIDTH}` 2784–2789 | `src/stores/timeline.ts` + `window` export | 500 / 46 / 8 / 1000. |
| Evaluate free identifiers (`renderMd`, `t`, `onEvent`, …) | `src/compat/window-exports.ts` | Names from `tests/webui-contract.md` §1. Functions are throwing stubs until a later lane overwrites them in the `// === lane additions ===` region. |
| classic-script lexical `S` (`typeof S` in `page.evaluate`) | `createSProxy()` assigned to `window.S` | get → `signal.value`, set → `signal.value`. |
