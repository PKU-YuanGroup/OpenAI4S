# Porting notes

## F-03 frontend/ scaffold

This work item does not port domain logic from `app.js`. It creates the empty Preact 10 + `@preact/signals` + TypeScript (strict) + Vite + Vitest workspace. Later F-series items append one section each.

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| *(none — no domain kernel in F-03)* | `frontend/` workspace | Empty shell only. `base: '/static/dist/'`. `@vitejs/plugin-legacy` forbidden. `build.modulePreload.polyfill: false` so modulepreload is external `<link>` tags, not an inline polyfill. Build fails if any HTML contains a `<script>` without `src=`. |
| Static files served from this directory | `npm run build` writes `openai4s/server/webui/dist/` | Wheel still Node-free. Serving `dist/index.html` behind `OPENAI4S_WEBUI_NEXT=1` is F-04. `theme-bootstrap.js` and `scientific_renderers.js` stay classic scripts (F-09 / unchanged). |

## F-04 dist serving and packaging

This work item does not port domain logic from `app.js`. It wires the committed Vite output into `_serve_index` and the wheel.

| Old | New | Semantics kept |
| --- | --- | --- |
| `_serve_index` (gateway.py; plan cited ~13506, now ~13948) always served `WEBUI_DIR/index.html` | `_serve_index` serves `WEBUI_DIR/dist/index.html` iff `OPENAI4S_WEBUI_NEXT=1` (exact `1` after strip). Helper: `_webui_next_enabled()` | Dispatch is unchanged: `/`, `/index.html` (`_serve_static` special-case), and unknown non-API GET (SPA deep links `/projects/{pid}/frames/{fid}`) still call `_serve_index`. Unset / any other value is the legacy shell. `/static/` resolution is unchanged, so `/static/dist/` is a normal tree under `WEBUI_DIR` with or without the flag. |
| `[tool.setuptools.package-data]` single-level globs (`server/webui/*.html`) | also `server/webui/dist/*.html` and `server/webui/dist/assets/*` | Existing globs stay. Dist is a subdirectory; omitting the new globs would drop the next UI from the wheel with no error. |
| `_WHEEL_REQUIRED` pinned `server/webui/index.html` / `app.js` / … | also pins `openai4s/server/webui/dist/index.html` | Sentinel only. Hashed asset names are not pinned. `_SDIST_REQUIRED` inherits the sentinel via `*_WHEEL_REQUIRED`. |
## F-09 theme

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| `theme-bootstrap.js` (entire file, 10 lines) | `frontend/index.html` head: `<script src="/static/theme-bootstrap.js">` (no `type="module"`) | Byte-identical file. Classic blocking script so `data-theme` is set before the body is parsed. A module script would defer and flash the light theme. |
| `app.js:176-179` `THEME` IIFE | `frontend/src/features/theme/theme.ts` `storedTheme` / `getTheme` | localStorage key `os-theme` unchanged. Invalid/missing → `system`. |
| `app.js:180-184` `themeIsDark` | `themeIsDark` | `dark` / `light` / `system` + `prefers-color-scheme`. |
| `app.js:185-204` `applyTheme` | `applyTheme` | Writes `data-theme` + `colorScheme` + `data-theme-instant` + 3Dmol `#1c1c19`/`white`. **Dropped** `document.body.classList.toggle("theme-dark")` — `data-theme` is the only source of truth. |
| `app.js:205-210` `setTheme` | `setTheme` | Persists `os-theme`. Toast via `window.hint`/`window.t` when present; no new i18n keys. |
| `app.js:211-215` `cycleTheme` | `cycleTheme` | light ↔ dark; from `system`, the opposite of the resolved value. |
| `app.js:216-227` `refreshThemeToggle` | `refreshThemeToggle` | `#dash-theme` / `#ws-theme` `data-icon` sun/moon. `icon()` innerHTML left to the later icon island. |
| `app.js:228-236` `matchMedia` | `watchSystemTheme` (from `installTheme`) | Follow OS while preference is `system`, with `{ instant: true }`. |
| `style.css:1685` `body.theme-dark #dash-theme,…` | `html[data-theme="dark"] #dash-theme,…` | CSS selector matches the single `data-theme` source. |
| `os-lang` | untouched | F-07 owns language. This lane neither reads nor writes `os-lang`. |
## F-07 i18n

Mechanical extract of the 2,419-line dictionaries plus the `t()` runtime. Generated files are not transcribed by hand.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| I18N.zh Object.assign (250-1458) | `frontend/src/i18n/zh.ts` (generated) | Every key/value byte-equal to the Object.assign result. |
| I18N.en Object.assign (1459-2668) | `frontend/src/i18n/en.ts` (generated) | Same; zh/en key sets identical (≥1207). |
| `I18N` / `LANG` IIFE (137-143) | `runtime.ts` `I18N`, `LANG`, `detectLang` | `os-lang` localStorage, then `navigator.languages` `/^zh/i`, else `"zh"`. |
| `tOptional` / `t` (149-159) | `runtime.ts` `tOptional` / `t` | Missing en → zh → key; `{0}` positional; empty string is present; `tOptional` does not interpolate and returns `null` on miss. |
| `applyStaticI18n` (161-167) | `runtime.ts` `applyStaticI18n` | `data-i18n` / `-title` / `-ph` / `-val`. |
| `refreshLangToggle` / `setLang` (168-173) | `runtime.ts` `refreshLangToggle` / `setLang` | `os-lang` persist; `documentElement.lang`; then static i18n + lang-btn `.active`. `refreshThemeToggle` + `rerenderI18n` (238-248) are `onLanguageChange` hooks until those views exist. |
| Locale modules in the main script | `loadLocale` `import("./zh")` / `import("./en")` | Inactive language is a separate async chunk; zh is also loaded when LANG is en so the fallback stays sync. |
| Plan-mode Chinese literal (7955-7959) | `runtime.ts` `planModePayload` | Concatenates existing `plan.prompt.intro/part1/part2/jsonSchema/part3` through `t()`, then the task text. The send() literal had drifted (`["产出文件名.csv"]`); the dictionary is the source of truth. F-11 `send()` should call this instead of inlining Chinese. |
| Extractor | `frontend/src/i18n/extract-i18n.mjs` | `new Function` runs the real `Object.assign` blocks; `--check` fails on generated drift. |
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
## F-08 pure-function kernels

Verbatim ports of the markdown / highlight / CSV / live-output / publicText kernels. `openai4s/server/webui/app.js` and `scientific_renderers.js` are untouched. No marked, no DOMPurify. Window exports are F-05.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `esc` at line 5 (`&<>` only) | `frontend/src/features/md/esc.ts` `esc` | Same replace order, plus `"` → `&quot;` (F-08). `&` still first so `&quot;` is not double-encoded. |
| `escQuote` at 12778 | `esc.ts` `escQuote` | Still a separate attribute-discipline helper; used on every alt/href/src capture. |
| `renderMd` / `mdInline` / `mdCodeBlock` / `mdList` 12709-12876 | `frontend/src/features/md/render.ts` | Whole-string `esc` then markup. Inline code pulled out first (`U+E000`/`U+E001` sentinels). Scheme whitelist `(https?:\|mailto:\|/\|#)` byte-identical. Unclosed fence stays code. ReDoS-safe table delimiter. `mdCodeBlock` copy chrome uses t() key-name fallback until F-07/F-10. |
| `mdHighlight` 12740-12759 | `frontend/src/features/md/highlight.ts` `mdHighlight` | Character scanner is the **only** highlighter. `.tok-com/.tok-str/.tok-num/.tok-kw/.tok-fn` unchanged. Huge blobs (`>24000`) still `esc` only. |
| `_OC_KW` 6093-6096 + `MD_KEYWORDS` 12716-12723 | `highlight.ts` `MD_KEYWORDS` / `mdKw` | Union. Python gains nothing extra (MD already a superset). Bash gains `cd`/`exit` from `_OC_KW` and keeps `alias`/`time` from MD. |
| `_ocHighlight` 6100-6118 | not ported; `ocHighlight = mdHighlight` | Notebook cells will use this scanner. Intended visible change: chat keyword set + mdHighlight tokenizer (JS `//`, python triple quotes, sticky numbers) instead of the `#`-only regex tokenizer. |
| `EDKW` 13137-13149 | `highlight.ts` `EDKW` / `editorKeywords` | Original arrays, then any unified-table word that was missing is appended. Aliases (`ts`/`mjs`/`bash`/…) kept. |
| `parseDelimited` 9690-9704 | `frontend/src/features/csv/csv.ts` `parseDelimited` | RFC-4180-ish: quoted fields, `""` escapes, CRLF, newline inside quotes stays in the field. |
| `csvFields`/`csv` 12907-12917 + `delimiterFor` 12892-12904 | `csv.ts` | `csvFields` is parseDelimited of one record, then trim. `delimiterFor` unchanged (`.tsv`/`.csv` first, else widest sniff). |
| `parseTable` 12878 | `csv.ts` `parseTable` | JSON branch unchanged. CSV branch uses `parseDelimited` instead of `split("\n")+csvFields`, so quoted newlines match the notebook path. |
| `scientific_renderers.js` (CSV fact source) | **not modified** | That file has no CSV parser today. F-08 does not add one. The fact source for CSV is parseDelimited. |
| `appendLiveOutput` 5361-5371 | `frontend/src/features/stream/cap.ts` | `LIVE_OUTPUT_CHAR_CAP = 1_000_000`; marker `\n...(live output truncated)`; further appends are no-ops once the marker is present. |
| `publicText` 2761-2767 | `frontend/src/features/scrub/scrub.ts` | Bearer / `sk`/`ark`/`api_key`/`access_token`/`refresh_token` / `?[key\|token\|api_key]=` redaction; ellipsis at `limit`. |

## F-06 WS layer

`connectWS` + inner `onEvent` become a Map registry. The if/else chain had one branch per type; the cursor still advances only after `onEvent` returns. Domain bodies for streaming/notebook/timeline/cards stay in later lanes, which `registerWsHandler` their own types.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `connectWS` 5157-5172 | `frontend/src/features/ws/connect.ts` `connectWS` | `ws:`/`wss:` + `location.host` + `/api/v1/ws`. onopen → `conn` + `sub(currentId)`. onclose → reconnect 1500ms. JSON ping every 25s on that socket. `connectWS._p` interval id. |
| onmessage 5162-5169 | `handleIncomingMessage` | **onEvent first, then `_seqSeen[root_frame_id] = seq` iff `seq > cursor`.** Comment at 5164-5167 kept. `JSON.parse` failure returns without a cursor write. Cursor key is `root_frame_id`, not `frame_id`. |
| `_seqSeen` 5176 / `_streamEpoch` 5180 | F-05 `stream._seqSeen` / `stream._streamEpoch` (imported, not edited) | Nested `S._seqSeen[rid] = sq` mutates the stored object. Epoch mismatch **replaces** `_seqSeen` with `{}`. |
| `sub` / `unsub` 5181-5182 | `sub` / `unsub` | `view_session` carries `since_seq` and `epoch` (undefined omitted). |
| `conn` 5183 | `conn` | `#conn-dot` `dot on`/`dot off`. Missing node is a no-op. |
| `onEvent` 5184-5357 if/else | `registry.ts` Map + per-type handlers | **Exactly one handler per type; `registerWsHandler` throws on duplicate.** Unknown types no-op then still advance the cursor (same as falling off the chain). |
| `replay_begin` 5186-5198 | `handlers.ts` `handleReplayBegin` | Epoch mismatch (truthy and ≠ current) sets `_streamEpoch` and `_seqSeen = {}` **before** `mine`. If `mine(fid)`: tear down `S.stream.wrap`, `S.stream = null`, `S.liveCells = []`, `S._liveCell = null`; `gap` zeros that cursor and sets `_replayGap`. |
| `replay_end` 5200-5205 | `handleReplayEnd` | If `mine` and `_replayGap === fid`: clear flag, `openConversation(fid, S.project)` when that lane export exists. Then `down()` if present. |
| `mine` 5358 | `guards.ts` `mine` | `f && S.currentId && f === S.currentId`. |
| `isStaleTurnEvent` 5755-5761 | `guards.ts` `isStaleTurnEvent` | Execution id first; one-side-silent is current; else request id; neither is current. |
| `frame_update` `loadSessions()` 5312 | `patchSessionFromFrameUpdate` + 300ms trailing debounce | In-place row mutate (`running` / `task_summary` / `name`); array identity kept. REST walk is `setLoadSessionsImpl` (F-13). Turn-ticket body is `setFrameUpdateTurnHandler` (F-11) — do not register a second `frame_update` handler. |
| `artifact_created` `loadArtifacts` 5343 | `upsertArtifactFromEvent` + 150ms trailing debounce | Nested / flat / bare payloads; `_artBust` + `_tbl` filename bust. REST fetch is `setLoadArtifactsImpl` (F-17). Remaining 32-line side effects via `setArtifactCreatedSideEffects`. |
| `artifact_ref_problems` … `kernel_status` | not registered here | Later lanes: F-10 text_*; F-11 cards/candidate/step/plan/permission; F-14 notebook_cell_* / kernel_status; F-15 timeline/execution/recovery/branch/delegation/sandbox. |
| `window.onEvent` | `bootWs()` / `installWs()` | Overwrites the F-05 stub. E2E still calls `onEvent(m)` without advancing the cursor (that stays in `onmessage`). |

## F-14 Notebook

Rendering shell rewrite of the Notebook dock. The live protocol, `_seenChunks` dedup, `_kc` invalidate timings, and scroll-follow/reading-delay gate are verbatim; the `innerHTML=""` full rebuild at `renderNotebook` 10352 is not.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `nbEventCellId` / `nbCellKey` / `mergeNotebookCells` / `nbFindCell` 9765-9785 | `features/notebook/cells.ts` | Server record wins on the same Cell ID; sort by `cell_index` then key; legacy key `legacy:{kernel}:{index}`. |
| `nbCellDraft` / `nbCellStart` / `nbCellChunk` / `nbCellFinished` 9787-9876 | `cells.ts` | Draft revision; discarded draft; start does **not** inherit finished output on replay; running live cell *does* inherit stdout/stderr/`_seenChunks`. |
| `_seenChunks` 9851-9853 | `nbCellChunk` | `stream + ":" + chunkId` (`chunk_id` else `sequence`); `chunk_id` 0 is a real id (`!= null`). Duplicate replay is a no-op. |
| `nbLiveStart` / `nbLiveAppend` 9880-9898 | `cells.ts` | Unstructured tool-stream fallback; divider `----- output -----`. |
| `nbRender` 9900-9908 | `scroll.ts` `nbRender` | If `running && _nbReading` → `_nbDirty`, no paint. `_nbSched` coalesces to one rAF. |
| `renderNotebook` follow + scroll listener 10339-10350 | `scroll.ts` `measureNotebookFollow` / `bindNotebookScroll` / `onNotebookScroll` | 120px threshold; `_nbScrollBound` once; returning to bottom flushes `_nbDirty`. |
| `renderNotebook` `nb.innerHTML=""` 10352 + `cellNode` 10567-10621 | `Notebook.tsx` CellList + memo completed cells | Keyed by `producing_cell_id`. Chunks write only that cell's output signal and `textNode.appendData(delta)`. Code highlight memoized on source. |
| kernel chips / REPL / status strip 10357-10475 | `Notebook.tsx` `KernelChips` / `ReplPanel` / `StatusStrip` | Rendered apart from the cell list. REPL only when `repl_enabled` and not quarantined. Classes `#dock-notebook .nb-repl` / `.nb-repl-input` / `.nb-status` / `.notebook-cell` unchanged. |
| `_kc` 9954 + `invalidateKernelCache` 9955 | F-05 `stores/notebook.ts` `_kc` + `kernel.ts` `invalidateKernelCache` | Clears `id/st/stAt/envs/cur/envAt`; leaves `stBusy/envBusy`. |
| invalidate at `kernel_status` 5352, `turnDone` 5854, `nbSwitchEnv` 10060 | `handleKernelStatus` / `notebookOnTurnDone` / `nbSwitchEnv` | F-11 **must** call `notebookOnTurnDone()` from `turnDone` (this lane cannot register a second `frame_update` handler). |
| `kernelCtl` / `executeNotebookCode` / `refreshKernelState` / `nbPopulateEnvSelect` 9911-10047 | `kernel.ts` | 800ms state / 8000ms env cache; session-id race drop. |
| `projectNotebookCells` 10076-10112 | `cells.ts` | Agent retry grouping after a failed same-runtime cell. |
| `NOTEBOOK_EXPORTS` / `notebookExportLink` 10119-10136, 10231-10264 | `chrome.ts` | Window contract global. sources.zip → `/execution-sources/export`. |
| `highlightTraceback` 10513-10521 | `chrome.ts` | Window contract global. `esc` then `.tb-loc` / `.tb-final`. |
| `looksBinary` 6055-6066 / `stripAnsi` 10482 | `chrome.ts` | Unchanged. |
| figure live-mount 5337-5341 | `chrome.ts` `mountLiveNotebookFigure` via `setArtifactCreatedSideEffects` | `_tbl` bust stays in F-06 upsert. F-17 must **compose** this call, not replace the setter. |
| `parseDelimited` / `renderTableInto` 9690-9744 | F-08 `csv.ts` + `chrome.ts` `renderTableInto` | `_tbl` keyed by busted URL, in the artifacts store. |
| cell highlighter `_ocHighlight` 6100-6118 | F-08 `mdHighlight` via `highlightCellSource` | Intended visible change (F-08): unified keyword set. Recomputed only when source/lang change. |

F-11: import `notebookOnTurnDone` and call it from `turnDone`.
F-16: import `cellNode` / `NotebookDock`; hang `buildExecutedCodeView` / `toggleExecutedCode` on window (`isReady` gated here).
F-17: call `mountLiveNotebookFigure` from `artifact_created` side effects.
