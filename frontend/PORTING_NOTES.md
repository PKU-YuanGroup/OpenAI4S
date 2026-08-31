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

## F-21 style.css + a11y batch

Global stylesheet. Class names are not renamed. Token holes, the dark comma leak, eight dead rules, long lines, light `--text-400` contrast, ≤900px touch targets, and markdown table clipping are fixed in `openai4s/server/webui/style.css`. The next UI loads that same file from the SPA head.

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| `style.css:6-40` `:root` tokens | same block | Added `--text-100/#1f1f1c`, `--text-300/#5c5a55`, `--surface-0/#fff`, `--warn/#b8860b`. Light `--text-400` `#7b7974` → `#6f6d68` (body-on-`--bg` contrast 5.08:1). Aliases `--bg-1/--bg-2/--fg/--fg-2/--surface-2` and JS layout knobs `--side-w/--dock-w/--exec-indent/--step-child-indent/--delegation-indent/--timeline-row-height` so `var(--x) − --x:` is empty. `--muted/--faint` track the new `--text-400`. |
| `style.css:1577-1585` `html[data-theme="dark"]` tokens | same block | Same four tokens + aliases. Dark `--text-100/#e8e6dc`, `--text-300/#a8a69c`, `--surface-0/#1c1c19`, `--warn/#d4a017`. |
| `style.css:1617` `html[data-theme="dark"] .lang-btn.active,.seg-btn.active` | both sides dark-qualified | Comma no longer lets `.seg-btn.active` leak onto the light theme. Same fix on `.molmini` / `.txt` / `.art:hover` sibling commas. |
| `style.css:1685` `html[data-theme="dark"] #dash-theme,…` | unchanged | F-09 already dropped `body.theme-dark`. The exact selector string is still what `theme.test.ts` greps. |
| eight dead rules (`.files-view`, `.folder-tools`, `.side-mini`, `.nb-repl-prompt` / `.pmt`, `.nbc-error-msg`, `.nbc-toggle`, `.prov-file-h`) | deleted | Unused: no matching class string in `app.js` / `index.html` / `frontend/src`. `.step-*` kind classes and `.genome-variant/.genome-signal` stay — they are built as `step-${kind}` / `genome-${type}`. |
| `style.css:514/1455/1456/1477` packed rule runs | one rule per line | Declarations unchanged. |
| `style.css:204` `.md table{overflow:hidden;max-width:100%}` | `.md-table-wrap` + table `width:max-content;min-width:100%` | Wide markdown tables scroll instead of clipping columns. |
| `app.js:12869` `"<table>…</table>"` | `'<div class="md-table-wrap">…</div>'` | Same wrap in the legacy renderer. |
| `frontend/src/features/md/render.ts` table emit | same wrap | Verbatim port of the wrap; XSS chain untouched. |
| `style.css` `@media (max-width:900px)` 32–36px chrome | `min-height/min-width:40px` on buttons, tabs, icon-ghost, nb-icon, session rows, tiles, zoom-bar | Class names unchanged. |
| *(none)* | `scripts/check_css_tokens.py` | `var(--x)` refs − `--x:` defs = ∅. Lint job step + pre-commit hook. |
| `frontend/index.html` head | `<link rel="stylesheet" href="/static/style.css" />` | Same sheet as legacy. Vite `npm run dev` proxies `/static` to :8760 for fonts. |
## F-20 team surface + workbench chrome

Team IIFEs, the modal focus trap kernel, ⌘K palette, upload / notes / mic, layout density, column resizers. Team modals now go through the trap (the old IIFEs bypassed it). Palette Artifact hits follow M-03.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| upload 10899-10907; paste 13414-13418; drop 13420-13423 | `features/chrome/upload.ts` `uploadFiles` | FileReader → `/uploads`; creates a frame if `currentId` is empty (`sub` + `loadSessions` + `openConversation` gated with `isReady`). |
| notes 10909-10914 | `features/chrome/notes.ts` | `effProject` from `S.project` else the current session's `project_id`. Empty / no-project copy via existing i18n keys. |
| micDictate 10919-10932 | `features/chrome/mic.ts` | `SpeechRecognition` / `webkitSpeechRecognition`; toggle stop; `interimResults` + `continuous`; append onto `#composer`. |
| applyLayout 10936-10937; setLayout 11225 | `features/chrome/layout.ts` | Body classes `layout-compact` / `layout-wide`; key `os-layout`; comfortable is the absence of both. |
| ⌘K palette 10940-11057 | `features/chrome/palette.ts` | Overlay / input / list, Arrow/Enter/Esc, `PAL.gen` discard. **Artifact hit (11030) is M-03, not the old `dockTab("files")`:** open owning session then `openViewer` with `version_id` forwarded as-is (never rewritten to latest). If `openViewer` is a F-05 stub, open the Files tab. `?artifact=&version_id=` parser exported as `parseArtifactQuery`. |
| dataproPaletteSummary / openDataproSearchHit 10973-11014 | `palette.ts` | Session-then-viewer; dashboard-only hit uses `openArtifact`; no artifact id opens Customize connectors. All later-lane names gated with `isReady`. |
| focus trap 11059-11120 | `features/chrome/modal.ts` | **Verbatim** stack / Tab cycle / Esc / focus restore. `_modalFocus.stack`, `_focusables`, `openModalEl` / `closeModalEl` / `trapModalKeydown`. Fallback selectors add `#team-admin-modal` and `#team-files-modal` so a bypassed team modal is still trapped. Palette Esc via `addModalEscapeBlocker`; `window.ac.open` still skips Esc. |
| column resizers 13250-13319 | `features/chrome/resizer.ts` | Keys `os-side-w` / `os-dock-w`; side clamp 200–520; dock ≥360 and viewport cap; `window._colClampBound` → module flag. |
| teamBootstrap IIFE 13450-13589 | `features/chrome/team.ts` | `/auth/me` 401 → `/login`; chip + sign-out; `/files` probe unhides Team files. **`openPanel` uses `openModalEl`.** |
| teamGovernance IIFE 13592-13682 | `team.ts` (same `/auth/me`) | Guest `/` → `/replay`; admin/service unhides `#team-admin`. **`openAdmin` uses `openModalEl`; closes use `closeModalEl`.** Five sections Users/Usage/Quotas/Invites/Audit, `.team-admin-table`. Two IIFEs share one `/auth/me` fetch. |
| init keydown 13370-13378 | `index.ts` `bootChrome` | Trap first, then ⌘K, ⌘B (`setSidebar` if `isReady`), ⌘Shift+L `cycleTheme`. |
| window names | `bootChrome()` assigns `openModalEl` / `closeModalEl` / `trapModalKeydown` / `openPalette` / `closePalette` / `applyLayout` / `setLayout` / `uploadFiles` / `micDictate` / `loadNotes` | Same pattern as F-06 `bootWs()` → `onEvent`. Not F-01 contract names; later lanes consume them. |
## F-19 Customize

Nine-tab modal plus isolated vendor cards. Polls (jobs 1500ms, job output 1200ms, copy restore 1200ms, Volcengine key 2500 then 5000×24) are bound to a per-mount timer lease and die on tab unmount / `closeCust`. Window exports `openCust` / `custTab` / `telemetryRow` are assigned by `bootCustomize()`, the same way F-06 assigns `onEvent`. Capability checks use `isReady` from `compat/stub.ts`.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `openCust` / `custTab` 11122-11131 | `features/customize/actions.ts` | Default tab `general`. `agents` aliases `specialists`. `#cust` loses `.hidden`. `.cust-tab` `active` + `aria-selected`. Every `custTab` remounts the pane (generation key). |
| `#cust` shell, 9 tabs in index.html:162-163 | `components/customize/Customize.tsx` | Frozen ids/classes: `#cust`, `#cust-close`, `#cust-content`, `.cust-tab[data-tab]`, `.prof-row`, `.cust-row`, `.toggle`. Esc / backdrop close. |
| `custGeneral` 11194-11224; `setLayout` 11225 / 10936 | `GeneralTab.tsx` + `layout.ts` | Theme via F-09 `setTheme`. `os-layout` comfortable/compact/wide. Language via F-07 `setLang`. LLM key row `GET /config/llm`. |
| `custPermissions` 11133-11192 | `PermissionsTab.tsx` | Conversation/project/global scopes; add/update/delete/reset. |
| `custSkills` 11241-11370 | `SkillsTab.tsx` + `NestedEditor.tsx` | Personal+project catalogs; collection collapse; insert `/name` mention; editor/import/history. |
| `custSpecialists` 11371-11391 | `SpecialistsTab.tsx` | Custom CRUD; builtin `PUT /agents/{name}/enabled`. |
| DataPro 11393-11546 | `vendors/datapro.tsx` + `vendors.ts` | Index-complete requires matching leaf counts and digests. Connector id `volcengine-datapro`. |
| `custConnectors` 11574-11589 | `ConnectorsTab.tsx` | DataPro card first; directory add; custom add; probe/toggle/delete. |
| `custCompute` 11590-11797 | `ComputeTab.tsx` | Host/GPU/remote rows are DOM text (no innerHTML). Job poll 1500ms on the lease. Copy restore 1200ms on the lease. |
| Doubao 11798-11887; `custNetwork` 11891-11985 | `vendors/doubao.tsx` + `NetworkTab.tsx` + `telemetry.ts` | Dedicated `source === "doubao"`. Telemetry drain loop (desired/confirmed, one in-flight PUT). `telemetryRow(host)` remains an imperative contract export. |
| `custMemory` 11986-12063 | `MemoryTab.tsx` + `memory.ts` | Scope is always sent; never the literal `"default"`. |
| Local discovery 12064-12123; protocols 12132-12150 | `models.ts` | Loopback-only chatgpt endpoints. Protocol list generated from the served catalogue. |
| Volcengine 12152-12574 | `volcengine.ts` + `vendors/volcengine.tsx` | Key poll 2500ms then 5000ms × 24, stopped on unmount. Auto-configure only while never linked. SSO popup severs `opener`. |
| `custModels` 12575-12707 | `ModelsTab.tsx` | Probe is a button, never a render-time call. Readiness is local-only. B-04 `capability_receipt` shown as tri-state badges (`true`/`false`/`unknown` + stale). Full M-01 wizard is later. |
| `window.openCust` / `custTab` / `telemetryRow` | `bootCustomize()` in `features/customize/index.ts` | Overwrites the F-05 stubs. `main.tsx` adds one import. |
## F-17 artifacts + Files (M-03)

Version cache, Files grid, renderer catalog, and the ten scientific renderer glues. Files search/filter/pagination/deep link is the M-03 surface (not a later rewrite of a legacy grid). `scientific_renderers.js` stays a classic UMD script.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `artifactCacheKey` 8353-8357 | `features/artifacts/cache.ts` `artifactCacheKey` | `_artVer[id]` then `version_id` / `latest_version_id` / `checksum` / `"unknown"`. Missing id → `"_live"`. |
| `syncArtifactVersion` 8359-8378 | `cache.ts` `syncArtifactVersion` | In-place `Object.assign` on matching `openTabs` items and `dockArtifact`. Force or version change busts `lineage` / `_envSnapById[artifactCacheKey]`. |
| `loadArtifacts` 8380-8401 | `load.ts` `loadArtifacts` | `_artifactLoadReq` generation; drop if `id !== S.currentId`. Bust `_artBust` when the seen version changes. |
| `loadProjectArtifacts` 8510-8516 | `load.ts` + `files-index.ts` `browseFiles` | Project Files walks `GET .../artifact-index` (50/page, cap 100). TODO fallback onto the legacy array route when the index 404s. Late responses after a Project switch are dropped (`filesIndexReq`). |
| `visibleArtifacts` / `filesGridArtifacts` 8492-8502 | `files-index.ts` | `priority < 0` hidden; frame scope still priority-sorts. Project scope keeps server keyset order so pages do not reshuffle. Same-name rows are not merged. |
| `artUrl` 8577 | `cache.ts` `artUrl` | Unpinned: `/artifacts/{id}?_={bust}`. `_exactVersion`: `/artifacts/versions/{vid}` — never latest. |
| `scientificRenderers` 8578 | `catalog.ts` `scientificRenderers` | `window.OpenAI4SScientificRenderers \|\| null`. Empty-value defense kept. |
| `loadRendererCatalog` / `artifactRendererDescriptor` / `compatibilityRendererDescriptor` 8580-8635 | `catalog.ts` | Descriptor `version_id` must match the requested version or the promise rejects (no silent latest). Missing runtime → `renderer_id: "download"`. |
| `renderArtifactBody` + 10 renderer glues 8637-8962 | `renderers.ts` | Sequence / MSA / genome / chemistry-2d / latex / markdown / table / text / download / sheet. image / pdf / html-preview / molecule-3d call later islands through `isReady` (F-18). PDF iframe still has no sandbox (F-18). html-preview `sandbox=""`. |
| `renderSheet` / `sheetShape` / `appendSheetShape` 8771-8802 | `sheet.ts` | Cap 5000×100; union of keys; `nb.table.*` hidden-copy reused. Window export for smoke. |
| `parseMolPoints` / `molSvg` / thumbs 8414-8490 | `thumbs.ts` | CA backbone preference, 500-point cap, spectrum XY SVG. |
| `artifact_created` 5314-5346 | `events.ts` `artifactCreatedSideEffects` | After F-06 upsert: `syncArtifactVersion(art, true)`, open-Viewer refresh, live-cell `figures` push, project Files reload. `nbRender` only if `isReady`. |
| `openViewer` 9437 | `ui.ts` `openViewer` | Sets `dockArtifact`, clears `provMode`, `addOpenTab`, `setActiveTab(a.id)`. |
| Files search / filter / Load more / deep link (M-03, new) | `files-index.ts` `deeplink.ts` `components/artifacts/FilesPanel.tsx` | Filename `q`, `content_type`, `origin=uploaded\|generated`, page 50. `?artifact=&version_id=`: omit version → latest; provided version **never** silent-falls-back to latest; missing exact → stale/not-found. ⌘K uses `openArtifactFromHit` (open session, then exact Viewer). |
| `parseTable` / `renderSheet` window globals | `boot.ts` `installArtifacts` | Overwrites the F-05 stubs. `parseTable` is F-08's kernel, published here because smoke/E2E read it as a bare global next to `renderSheet`. |
## F-15 Timeline

Verbatim port of the Action Timeline kernel. The Preact component only hosts `#dock-timeline`; the ledger is an imperative island so `_timelineView` identity, function names, 46px rows, overscan, signature reuse, `translateY`, and the SVG overview stay byte-equivalent for smoke:559-1658.

Signal updates are **not** rAF-batched across WS types: smoke reads the DOM immediately after `onEvent({ type: "action_timeline" })`. The island already rAF-coalesces window reconcile (`scheduleActionTimelineWindow`) and overview path redraws.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `publicList` / `publicArtifacts` 2769-2782 | `features/timeline/sanitize.ts` | Same limits; `publicText` from F-08 `scrub.ts`. |
| `ACTION_TIMELINE_PAGE_SIZE/ROW_HEIGHT/OVERSCAN/OVERVIEW_WIDTH` 2784-2789 | F-05 `stores/timeline.ts` (unchanged) + island | 500 / 46 / 8 / 1000. Extra: `TOP_THRESHOLD`, `BOTTOM_THRESHOLD=2`, overview height 112, hover delay 500. |
| `timelineOrdinal` 2792 | `sanitize.ts` | Finite number or numeric string; empty → null. |
| `sanitizeActionTimeline` 2795-2855 | `sanitize.ts` | Last PAGE_SIZE groups; events not sliced; attempts `-50`; `has_earlier`/`has_more` aliases; usage integers ≥0. |
| `mergeActionTimelines` 2856-2888 | `sanitize.ts` | Frame/branch mismatch → incoming; Map dedupe; no slice; `before` vs `latest` concat order. |
| `queueMetadata` / `sanitizeExecutionQueue` 2890-2918 | `sanitize.ts` | Frozen ticket metadata; queue cap 100. |
| `rememberExecutionQueue` / `renderQueueStrip` 2920-2967 | `island.ts` | Writes `S.executionQueue` / `S.executionIdentity` by reference; `#queue-strip` no-op if missing. |
| `rememberExecutionState` 2984-3005 | `island.ts` | Identity lifecycle; pending REPL uses notebook store + `isReady` later-lane calls. |
| `sanitizeRecovery` / `sanitizeRecoveryActions` 3032-3068 | `sanitize.ts` | Allowlist ids `restore`/`retry`/`restart_fresh`. |
| `sanitizeBranches` / `branchUndoFromProjection` / `sanitizeRevertPreview` / `sanitizeRevertMutationResult` 3070-3148 | `sanitize.ts` | Capability enabled/reason; undo from head `undo_checkpoint_id`. |
| `sanitizeVariableInspection` 3150-3178 | `sanitize.ts` | Reads `branchState` for exact-scope; fail closed otherwise. |
| `sanitizeContext` / `sanitizeSecurity` / `sanitizeDelegations` 3180-3276 | `sanitize.ts` | Omitted reasons; sandbox-typed events; children without `child_id` dropped. |
| `mergeDelegationChildEvent` 3277-3298 | `island.ts` | In-place child upsert + stats rebuild; identity of `S.delegationState` kept. |
| `sanitizeComputeTasks` 4925-4946 | `sanitize.ts` | `polled` is server-said, never inferred. |
| `loadEarlierActionTimeline` 3314-3353 | `island.ts` | `before_ordinal` + `branch_id`; prependSnapshot scrollHeight/scrollTop; stale request drop. |
| `loadWorkbenchState` / `scheduleWorkbenchRefresh` 3354-3389 | `island.ts` | `optionalApi` fan-out; `_workbenchReq` generation. |
| `actionTimelineSpan` / overview model / `timelineOverviewTimeToX` / `actionTimelineOverviewVisualExtent` / `actionTimelineSelectionOverlaps` 3573-3820 | `features/timeline/model.ts` | Epoch parser `timelineEpochMs`; unfinished attempts are markers only; non-span groups still overlap via `created_at`. |
| `actionTimelineEntryKey` / `actionTimelineLedgerEntries` 3871-3899 | `model.ts` | `turn:` / `group:` keys; search temporarily reveals collapsed turns. |
| Overview SVG + gestures 3692-4245 | `island.ts` | Constant-size SVG; drag-select / right-drag pan / wheel zoom; 500ms hover. |
| Virtualizer `reconcileActionTimelineWindow` 4574-4634 | `island.ts` | 46px, overscan 8, signature row reuse, `translateY(index * 46)`, aria-rowindex. |
| `updateActionTimelineLedger` 4635-4708 | `island.ts` | Prepend scrollTop compensation; followTail; filter restore; `_timelineView` identity. |
| `toggleActionTimelineTurn` 4447-4457 | `island.ts` | Mutates `collapsedTurns` Set in place. |
| `createActionTimelineView` 4458-4549 | `island.ts` | Same `_timelineView` fields (searchQuery/searchNeedle/collapsedTurns/autoLoadArmed/…). |
| `renderActionTimeline` 5097-5154 | `island.ts` | `#dock-timeline`; five side panels + recovery card + ledger. |
| Sidebar panels 4835-5066 | `island.ts` `renderBranchPanel` / `renderDelegationPanel` / `renderComputeTasksPanel` / `renderContextPanel` / `renderSecurityPanel` | Class names `.branch-panel` `.recovery-action-list` `.delegation-child` frozen. Recovery is a card above the ledger, not a sixth side panel. |
| WS `action_timeline` … `security_status` 5225-5269 | `features/timeline/ws.ts` | One handler per type via F-06 registry; delayed other-branch deltas dropped. |
| Window names | `features/timeline/index.ts` `bootTimeline` | Overwrites F-05 stubs. Uses `isReady` from `compat/stub.ts` (not `typeof === "function"`, not `window-exports.ts`). |
| `#dock-timeline` host | `components/timeline/Timeline.tsx` | Container + lifecycle only. |
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
## F-13 dashboard / projects / sessions

Sidebar, paging, share/import-export, hint a11y, disconnect banner. Later-lane names are called through `isReady` (`compat/stub.ts`); this lane does not import `window-exports.ts`.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| dashboard 6616-6764 (`paintDashSkeleton` / `loadDashboard` / `renderDash*` / example CTA 6672-6694 / dash poll) | `features/sessions/dashboard.ts` | Running-count annotation, recent cap 10, example CTA poll **stopped** with the view (`stopDashPoll`). Dashboard rows / run-cards get `role=button` + tabIndex + Enter/Space. |
| projects 6765-6913 (`sanitizeProjectLineage` / research view / proj menu / modal) | `features/sessions/projects.ts` | publicText caps, 5000/10000 slice, modal mode token. `sanitizeActionTimeline` / `actionTimelineCard` via `isReady`. |
| sessions + paging 6914-7410 (`MESSAGE_PAGE_SIZE=300`, `SESSION_MAX_PAGES=50`, newest-first then seq-sort, cursor walk, `sessionRow` 7030-7032) | `features/sessions/paging.ts` + `messages.ts` + `load.ts` | Newest-first fetch, sort back into reading order. Session walk is keyset + root-frame + id-dedupe. `has_more` at the page cap is a sentence, not a dead button. |
| `openConversation` 7121-7219 / `resumeWatch` 7103-7120 / `newSession` | `features/sessions/conversation.ts` | Generation token, unsub previous, history + steps interleaved by time, later-lane loads via `isReady`. `sub`/`unsub` from F-06. |
| `renderStored` / earlier bar / ref chips 7226-7409 | `features/sessions/transcript.ts` + `messages.ts` | `renderMd` from F-08. `renderMessageRefChips` / `renderComposerRefChips` assigned on `window`. |
| session actions / share / import-export 7411-7793 | `features/sessions/actions.ts` | Verify-before-import; 128 MiB client cap; markdown export walks `fetchAllMessages` and names truncation. |
| `openMenu` 7744-7763 | `features/sessions/chrome.ts` | Esc closes, `role=menu` / `menuitem`, focus moves into the first item. |
| `hint` 12920 | `chrome.ts` `hint` | `#composer-hint` `role=status aria-live=polite`. Err branch prefixes `错误：` / `Error: ` from `LANG` (no new i18n key). |
| `#conn-dot` 5183 (element missing) | `#conn-banner` + wrap of F-06 socket `onopen`/`onclose` | Banner + hint on close; clear on open. Socket wrap is a microtask so F-06 handlers are assigned first. |
| `index.html` `#composer-hint` / `#tab-close` span | `components/dashboard/Shell.tsx` | Frozen ids. Close-tab is a real `<button>`. `.tile` / `.art` / `.t-close` get Enter/Space via a MutationObserver for later lanes. |
| routing 2678-2687 / 13231-13248 | `dom.ts` `framePath`/`navURL` + `routeInitialView` | Dashboard `/`, conversation `/projects/{pid}/frames/{fid}`. |
| window contract names | `boot.ts` `installSessionExports` | Overwrites F-05 stubs for this lane's names. `setLoadSessionsImpl(loadSessions)` for F-06's 300ms debounce. |
## F-10 message stream

Streaming render shell. Domain kernels (renderMd, appendLiveOutput) stay F-08 imports. `LIVE_OUTPUT_CHAR_CAP` 1MB truncation is unchanged. Window names this lane owns are assigned by `installMessages()` (F-06 `bootWs` / F-07 `t` pattern), not left as F-05 stubs. Capability checks use `isReady` from `compat/stub.ts`; this module does not import `compat/window-exports.ts`.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `_mdStableCut` 5378-5402 | `features/messages/cut.ts` `mdStableCut` / `_mdStableCut` | Same seal rules: top-level blank line or completed fence; blank line inside a fence is not a boundary; an opening fence is never a closer; final ~120 chars stay soft; `limit < 80` (text shorter than 200) returns 0. **Scan is incremental**: append-only streams resume from the last unprocessed line + `openFence`, instead of `text.split("\\n")` every token. |
| `flushRender` 5403-5426 | `features/messages/stream.ts` `flushRender` | Hysteresis `cut > _stableAt + 40` kept. Dual nodes: `.md-sealed` rewritten only when the cut advances (`renderMd(prefix)` once); `.md-tail` is `renderMd(unstable rest)` each frame. Final flush still sets `st.md.innerHTML = renderMd(text)` and drops the dual nodes. No marked / DOMPurify. |
| `scheduleRender` 5427-5440 | `scheduleRender` | Dirty flag + single rAF; streams `>600` chars skip a flush when the previous one was `<48ms` ago (~20/s). `down()` is the shared rAF, not a sync layout. |
| `sealText` 5445-5451 | `sealText` | Final render, drop `.cursor`, reset sealed prefix so the next text block after a tool card starts clean. |
| `startStream` / `ensure` / `feed` 5452-5510 | `stream.ts` | Same tool-header detection (`cell_index` / `◆` / `⚙`), `TOOL_LABELS`, `storedCandidateOwnsChunk` skip. Tool body uses `bindStreamingPre` → `textNode.appendData(delta)` instead of rewriting `pre.textContent`. Newline meta uses the increment (`toolMetaLabel`, including the dead `n === 1 ? " line"` branch). `nbLiveStart` / `nbLiveAppend` via impl hook or `isReady` window call (F-14). |
| `appendLiveOutput` 5361-5377 / 5494 | F-08 `features/stream/cap.ts` + F-10 `liveOutputDelta` | Cap 1_000_000 + `"\n...(live output truncated)"`; once the marker is present, further appends are no-ops (idempotent). Delta path never rereads `textContent`. |
| `openConversation` 7166-7181 (the 300-item `forEach`) | `list.ts` `scheduleFramedRender` + `open.ts` | 40 items per rAF (30-50 window) into one `DocumentFragment`. Generation guard `_openGen` kept. Messages + steps still interleave by `(t, seq)`. `renderStoredStep` is F-11 (`setRenderStoredStepImpl`). |
| `renderStored` 7234-7260 | `list.ts` `renderStored` | User bubble is `textContent`; assistant is `renderMd` innerHTML. `dataset.ts` for time order. Candidate identity + review badge + failure meta. `renderMessageRefChips` via `isReady`. |
| `insertMessageByTime` 7263-7274 | `list.ts` `insertMessageByTime` | Skip `#msgs-earlier`; first later `dataset.ts` wins; else append. |
| `down` / `paintJumpPill` / `updateJumpPill` 12934-12942 | `scroll.ts` | `messagesAtBottom` pad 80 (follow) / 60 (pill). `down(force)` sets follow and `scrollTop = scrollHeight`. **All writes go through one rAF** (replaces 5509 sync layout and 13384 unthrottled scroll). |
| `$("#jump-pill").onclick` / `$("#messages").scroll` 13383-13384 | `bindMessageScroll` | Click → `down(true)`; scroll → measure follow on the same rAF. |
| `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` 6926-6961 | `fetch.ts` | Newest-first page then sort by `seq`; older page is keyset `before_seq`; walk cap `MESSAGE_WALK_MAX_PAGES = 200`; `MESSAGE_PAGE_SIZE = 300`. F-13 should import these. |
| `text_reset` 5220 / `text_chunk` 5270-5276 | `handlers.ts` | `mine` + `!isStaleTurnEvent`; `feed(block_type, chunk, m, storedCandidateOwnsChunk(m))`. |
| `window.openConversation` / `fetch*Messages` | `installMessages()` | Overwrites the F-05 stub. F-06 `tryLane("openConversation")` / `tryLane("down")` now hit real functions. |

## F-12 autocomplete

Composer `@` / `#` / `/` and the right-dock editor completer. Keyword lists are F-08 `editorKeywords(ext)` (unified highlight table + editor-only extras). This lane does **not** keep a private `EDKW` table. `ac` is an object (not a function); F-11 send() reads `ac.open` rather than `isReady`. `edacTeardown` is a function and is assigned here so F-10 / F-13 `callLane("edacTeardown")` is real. Capability checks use `isReady` from `compat/stub.ts`; this module does not import `compat/window-exports.ts`.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `ac` 132 | `composer.ts` `ac` | `{ open, items, idx, trigger, start }`. Hung on `window.ac`. |
| `acDetect` 12946-12951 | `detect.ts` `acDetectFrom` + `composer.ts` `acDetect` | `(^|\s)([@#/])([^\s@#/]*)$`. `start = pos - query.length - 1`. Mid-token `foo@bar` is not a trigger. |
| `_acFiles` / `acProjectFiles` 12955-12976 | `composer.ts` `acProjectFiles` + `rank.ts` `mergeArtifactCandidates` | 4s per-project cache. Dedup by `artifact_id \|\| id \|\| filename` (not filename). Project list first, then `S.artifacts`. |
| `acUpdate` 12978-13011 | `composer.ts` `acUpdate` + `rank.ts` | `@` version-pins `name#version_id` and labels another session with `ac.fromOtherSession`. `#` sessions, `/` skills. Filter label/insert substring, cap 8. |
| `acRender` / `acPick` / `acClose` 13012-13033, 13125 | `composer.ts` | `#composer-ac` (created if Shell has not painted it). Pick splices `trigger+insert+" "` and calls `grow` + `renderComposerRefChips`. |
| composer keydown 13403-13411 | `bindComposerAutocomplete` capture-phase | Arrows cycle; Enter/Tab pick; Esc close. `stopImmediatePropagation` so a later send() keydown cannot fire after a pick. IME `isComposing` / keyCode 229 ignored. |
| `EDKW` 13137-13149 | **not copied**; `editorKeywords` from `features/md/highlight.ts` | Same aliases (`ts`/`mjs`/`bash`/…). py gains `self`/`print` from the unified table; sh gains `alias`. |
| `edacExt` / `edacDetect` / `edacItems` 13150-13169 | `detect.ts` + `rank.ts` `rankEditorItems` / `harvestBufferIdentifiers` | Identifier ≥2, ASCII only. Keywords first (prefix, skip the token already typed), then buffer ids. Scan cap 200_000. Cap 8. |
| `edacCaretXY` / `edacRender` / `edacPosition` 13174-13205 | `editor.ts` | Reused `.ed-mirror`. Flip above the caret near the bottom edge. |
| `edacUpdate` / `edacPick` / `edacClose` / `edacTeardown` 13206-13229 | `editor.ts` | Re-validate caret at pick. `execCommand('insertText')` with `setRangeText` fallback. `justPicked` suppresses the input reopen. |
| editor wiring 9481-9499 | `bindEditorAutocomplete` + `watchEditAreas` | Per-textarea controller on `.edit-area` (F-17 has not painted the editor yet; MutationObserver binds when it does). No document/window listeners on the controller itself. |

## F-11 send chain + cards

Composer send, turn tickets, step / plan / permission / candidate cards, attachment and @-ref problem cards, admission tracker. Window names this lane owns are assigned by `installSend()` (F-06 `bootWs` / F-10 `installMessages` pattern), not left as F-05 stubs. Capability checks use `isReady` from `compat/stub.ts`; this module does not import `compat/window-exports.ts`. Plan-mode payload goes through F-07 `planModePayload` (already called from `send.ts`; the drifted Chinese literal is not inlined). `frame_update` is not re-registered; the turn-ticket body is `setFrameUpdateTurnHandler`.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `send` 7908-8178 | `features/send/send.ts` `send` | Ref chips, attachments, plan/explore, admission mint **before** POST, turn ticket, `planModePayload(text)` instead of the drifted Chinese literal. `ac.open` is read as an object (F-12), not `isReady`. |
| plan / explore / composer Enter 13395-13411 | `send.ts` `bindComposer` | Called from `installSend`. IME `isComposing` / keyCode 229 ignored. `ac.open` skips Enter-to-send. |
| `openTurnTicket` / `commitTurnTicket` / `acceptTurnTicket` / `activateTurnTicket` / `closeTurnTicket` 5679-5784 | `ticket.ts` | Generation taken before POST. `queue_position === 0` is the only proof the 202 owns the running slot. `processing` always advances the generation. |
| `resumeWatch` 7103-7120 | `ticket.ts` `resumeWatch` | 2s status poll; miss reloads through `openConversation` (F-10, `isReady`). |
| `turnDone` 5799-5874 | `turn.ts` | Composer unlock, ticket close, artifact reload. **Must** call F-14 `notebookOnTurnDone()`. |
| standard-profile readiness 7842-7906 | `environment.ts` | Banner + 409/503 send block; terminal `environment_not_ready` / `environment_readiness_unavailable` opens Compute. |
| `markCandidateReady` / `applyCandidateResolution` / `applyFinalReviewStatus` 5511-5666 | `candidate.ts` | Three-state timing verbatim. Identity helpers stay F-10 `messages/identity.ts`. Verified never lands on undelivered bytes. One `.review-badge` replaced in place. |
| `renderPlanCard` / `updatePlanProgress` / approve/revise/discard/resume 5876-6041 | `plan.ts` | `PLAN_SETTLED_STEP_STATUSES` includes `skipped`. Dispatch takes the turn ticket **before** the POST. |
| `stepBody` / `buildStepCard` / live step 6043-6458 | `step.ts` | `searchResultHttpUrl` rebuilds the scheme from a literal. `_ocHighlight` not ported; code blocks use F-08 `mdHighlight`. |
| `renderPermissionCard` / `resolvePermissionCard` 6460-6614 | `permission.ts` | Frozen classes `.perm-card` / `.resolved` / `.allowed` / `.denied`. Null-proto registry. |
| `renderAttachmentProblems` / `renderRefProblems` 13035-13096 | `problems.ts` | Attachments: client wording from `{name, reason, limit, bytes}`. Refs: server `message`. |
| admission tracker 9016-9143 | `admission.ts` | Keys `openai4s.admission.{fid}.{id}` independent, never a container. Legacy `openai4s.admission.{fid}` migrates. Grace 60s. `outstandingAdmissions` stays on window. |
| `onEvent` `artifact_ref_problems` … `attachment_problems` 5206-5216; `step` … `candidate_resolved` 5277-5294 | `handlers.ts` `registerSendHandlers` | One handler per type via F-06 registry. Does **not** register `action_timeline` / `frame_update` / `replay_begin` / `text_chunk`. |
| `frame_update` turn-ticket body 5296-5310 | `handlers.ts` `handleFrameUpdateTurn` via `setFrameUpdateTurnHandler` | `processing` → `activateTurnTicket`; terminal → `applyFinalReviewStatus` + `turnDone`. Stale turn refreshes workbench only. |
| `renderStoredStep` hook (F-10 `setRenderStoredStepImpl`) | `installSend` | Interleaved history steps paint through `step.ts` `renderStoredStep`. |
| window contract names | `index.ts` `installSend` | Overwrites the F-05 stubs for the ten names. `main.tsx` adds one import. |

## F-16 execution view

Executed-code history, variable inspector, Provenance tab, and fork-without-checkpoint 409 presentation. Recovery/branch **sanitize** (app.js:3032-3148) and the Timeline panel buttons stay in F-15; this lane does not rewrite them. Fork-from-cell without an exact cursor checkpoint is HTTP 409 `historical source has no exact cursor checkpoint` — the UI surfaces that sentence, does not retry, and does not rewrite it as success. F-14 already `isReady`-gates `toggleExecutedCode` / `buildExecutedCodeView` and exports `cellNode`; F-17 Viewer already calls `window.renderProvenanceInto` when `provMode` is set. This lane assigns those names and composes F-14 `renderNotebook` with the inspector.

| Old (`openai4s/server/webui/app.js`) | New | Semantics kept |
| --- | --- | --- |
| `execSourcesState` / `toggleExecutedCode` / `loadExecutionSources` / `selectExecFrame` / `buildExecutedCodeView` 10148-10229 | `features/execution/exec.ts` | Identity of `S.execSources`; request generation; do not cache a failed cell list; indent `min(max(depth,0),8)*14px`; executed-code **replaces** the Notebook body (history, not deliverables). |
| `notebookExportLink` 10231-10264 | F-14 `chrome.ts` (unchanged) | Provenance exec subtab reuses it. |
| `refreshVariableInspector` / `variablePreviewText` / `renderVariableInspector` 10265-10332 | `features/execution/inspector.ts` | `sanitizeVariableInspection` from F-15 (exact-scope fail-closed). Inspection never runs a Cell. Stale = runtime revision/generation ahead of the snapshot. |
| `sanitizeRecovery` / `sanitizeBranches` / `sanitizeVariableInspection` 3032-3178 | F-15 `features/timeline/sanitize.ts` (unchanged) | This lane only imports. |
| Timeline recovery/branch REST buttons 4715-4878 | F-15 `island.ts` (unchanged) | 409 on the banner is `publicText(error.message)`. This lane adds `conflict.ts` / `branch.ts` so a missing cursor checkpoint cannot be retried or rewritten. |
| `loadLineage` / `showProvenance` / `renderProvenanceInto` 10631-10676 | `features/execution/provenance.ts` + `lineage.ts` | Lineage request generation; tab/version change drops the response. Subtabs code/exec/messages/environment/review. |
| `renderProvEnvironment` 10683-10766 | `provenance.ts` + `lineage.ts` `envSnapshotHonesty` / `envPythonChip` | Three honesty states (live / verified / unverified). No `Python ?` on an R snapshot. `packages_unavailable` and unverified `provenance` rendered, not reworded. |
| `renderProvMessages` 10769-10788 | `provenance.ts` | `fetchRecentMessages` (F-13); `renderMd` (F-08). |
| `renderProvReview` 10789-10833 | `lineage.ts` `lineageReviewModel` + `captureInRootNotebook` | `head_checksum_reused` always listed; other captures only without a cell card. Delegate captures do not get a root-Notebook view-code link. |
| window names | `boot.ts` `bootExecution` | Overwrites F-05 stubs for `buildExecutedCodeView` / `execSourcesState` / `selectExecFrame`. Also assigns `toggleExecutedCode` / `showProvenance` / `renderProvenanceInto` / `renderNotebook`. `main.tsx` adds one import after `bootArtifacts`. |
## F-22 zero-coverage locator smokes

This work item does not port domain logic. It adds locator-only waits at the tail of `tests/browser_smoke.mjs` and one step on the existing `browser-smoke` CI job.

| Old (`openai4s/server/webui/` / `app.js`) | Smoke | Semantics kept |
| --- | --- | --- |
| `applyTheme` / `cycleTheme` 185-215; `theme-bootstrap.js`; `os-theme` | `#ws-theme` click → `html[data-theme]` flips, `localStorage.os-theme` matches, survives `reload` | Single source of truth is `data-theme` on `<html>` (F-09). Waits are locator/`waitUntil` polls, not `waitForFunction` (CSP has no `unsafe-eval`). |
| `setLang` / `refreshLangToggle` 168-173; `I18N.zh`/`I18N.en` 250-2668 | `.lang-btn[data-lang]` click → `html[lang]` + `[data-i18n="ws.nav.files"]` ("文件"/"Files"); zh/en key-set difference is empty | `os-lang` persist and `.lang-btn.active` unchanged. Loaded `I18N` dictionaries are asserted when the classic-script const is in scope; otherwise surface keys whose `t(key)` is still the key. |
| mobile drawer 2689-2707; `#sidebar-reopen`; `#mobile-scrim` | viewport 375×812 → `body.sidebar-collapsed`, no horizontal overflow, reopen/scrim cycle | `matchMedia("(max-width: 900px)")` still drives `setSidebar`. Overflow compares `scrollWidth` to `innerWidth` so a vertical scrollbar is not a false positive. |
| `tests/scientific_renderers_smoke.cjs` (manual) | `browser-smoke` job step `node tests/scientific_renderers_smoke.cjs` | Same UMD file. **Step, not job** — the 17 PR-job count stays locked. |
