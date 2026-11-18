# frontend/src/i18n

[中文说明](README_zh.md)

F-07 lane: mechanically extracted zh/en dictionaries and the `t()` / `tOptional` runtime. Inactive locale is a `import()` chunk. Do not hand-edit `zh.ts` / `en.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`extract-i18n.mjs`](extract-i18n.mjs) | Runs the app.js `Object.assign(I18N.zh/en, …)` blocks via `new Function` and emits `zh.ts` / `en.ts`. `--check` / `--self-test`. |
| [`extract-i18n.d.mts`](extract-i18n.d.mts) | TypeScript declarations so Vitest can import the extractor. |
| [`zh.ts`](zh.ts) | Generated Chinese dictionary (app.js:250-1458). |
| [`en.ts`](en.ts) | Generated English dictionary (app.js:1459-2668). |
| [`runtime.ts`](runtime.ts) | `t` / `tOptional` / `setLang` / `applyStaticI18n` / `planModePayload`. The active dictionary and its zh fallback load together; the first load repaints static labels exactly as a language switch does, and a zh fallback that fails does not cancel that repaint. `languageRevision` bumps on every repaint and is read by `t()` / `tOptional()`, so a render through them repaints itself; `onLanguageChange` hooks get the language just applied. With no saved pick (`os-lang`), the language is the first of the browser's languages the UI has, in the user's order, and follows the browser's `languagechange`; `setLang("system")` removes the saved pick, a pick storage refuses is kept for the page, and a pick made in another tab is followed through the `storage` event. |
| [`copy.ts`](copy.ts) | `copyLookup` / `CopyTable`: the lookup behind every feature-local copy table (`filesT`, `ot`, `judgmentT`): dictionary, then the active language, then English, then the key. `overrideLookup`: a feature's corrections of the generated dictionaries (the table wins for the keys it names in the active language, the dictionary answers the rest), for zh entries the extract has in English. |
| [`index.ts`](index.ts) | Public exports for later F-series items. |
| [`i18n.test.ts`](i18n.test.ts) | Key-set parity, extract-vs-app.js diff, `t()` semantics (a render through `t()` repaints after a switch), plan-mode payload, first-launch detection in the browser's language order and the follow-the-system mode. |
| [`static-i18n-race.test.ts`](static-i18n-race.test.ts) | Locale chunks held behind a gate: static labels applied before they load keep their readable fallback, and are repainted (with the language toggle and hooks) once they land. With a gate per chunk: en and zh are requested together, a failed zh fallback still repaints in English, a failed active chunk still rejects. |
| [`system-language.test.ts`](system-language.test.ts) | Following the browser's language: loading the runtime installs a `languagechange` listener that switches a page with no saved pick, a pick that blocked storage refused still counts for the page, a pick or a return to System made in another tab (a second runtime instance sharing the storage) is followed, and `theme-bootstrap.js` (run in a VM) sets the same `<html lang>` as `systemLang` for each browser language list. |
