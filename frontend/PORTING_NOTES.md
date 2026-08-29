# Porting notes

## F-03 frontend/ scaffold

This work item does not port domain logic from `app.js`. It creates the empty Preact 10 + `@preact/signals` + TypeScript (strict) + Vite + Vitest workspace. Later F-series items append one section each.

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| *(none — no domain kernel in F-03)* | `frontend/` workspace | Empty shell only. `base: '/static/dist/'`. `@vitejs/plugin-legacy` forbidden. `build.modulePreload.polyfill: false` so modulepreload is external `<link>` tags, not an inline polyfill. Build fails if any HTML contains a `<script>` without `src=`. |
| Static files served from this directory | `npm run build` writes `openai4s/server/webui/dist/` | Wheel still Node-free. Serving `dist/index.html` behind `OPENAI4S_WEBUI_NEXT=1` is F-04. `theme-bootstrap.js` and `scientific_renderers.js` stay classic scripts (F-09 / unchanged). |

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
