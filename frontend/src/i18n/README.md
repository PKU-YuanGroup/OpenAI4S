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
| [`runtime.ts`](runtime.ts) | `t` / `tOptional` / `setLang` / `applyStaticI18n` / `planModePayload`. |
| [`index.ts`](index.ts) | Public exports for later F-series items. |
| [`i18n.test.ts`](i18n.test.ts) | Key-set parity, extract-vs-app.js diff, `t()` semantics, plan-mode payload. |
