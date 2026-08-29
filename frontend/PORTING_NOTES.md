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
