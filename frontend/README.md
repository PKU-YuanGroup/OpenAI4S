# frontend

[中文说明](README_zh.md)

The workbench UI. Preact 10 + `@preact/signals` + TypeScript (strict) + Vite + Vitest. The committed build lands in [`../openai4s/server/webui/dist/`](../openai4s/server/webui/dist/) and is the default SPA shell. The hand-written `app.js` client is the `OPENAI4S_WEBUI=legacy` escape hatch; do not add features there. Dependencies live in this `package.json`; the repository-root npm package is `openai4s-skills` and must not grow frontend deps.

## Where this fits

`npm run dev` serves this app at `http://127.0.0.1:5173/static/dist/` and proxies `/api`, `/ws`, and `/static` (fonts + `style.css`) to the daemon on `8760`. `npm run build` writes `openai4s/server/webui/dist/` with `base: '/static/dist/'`; commit that tree in the same PR as the source. The daemon serves `dist/index.html` as the SPA shell by default. `OPENAI4S_WEBUI=legacy` is the escape hatch.

## Files

| File | Responsibility |
| --- | --- |
| [`index.html`](index.html) | SPA shell. Head loads `/static/style.css` (same global sheet as the legacy UI; F-21), `/static/theme-bootstrap.js` as a classic (non-module) script so the first paint has `data-theme`, `/static/favicon.js` (10 fps clamp), and `/static/scientific_renderers.js`. The app entry is an external `type="module"` `src=` tag so CSP `script-src 'self'` never has to authorize inline script. |
| [`package.json`](package.json) | Frontend package: Preact 10, `@preact/signals`, Vite, Vitest, TypeScript. `private: true`. |
| [`package-lock.json`](package-lock.json) | Lockfile for a deterministic `npm ci` rebuild. CI rebuilds `webui/dist` and `git diff --exit-code`s it. |
| [`PORTING_NOTES.md`](PORTING_NOTES.md) | Per-item map of old `app.js` line ranges onto new modules. F-03 has no domain kernel; F-04 maps `_serve_index` / package-data / `_WHEEL_REQUIRED`. |
| [`tsconfig.json`](tsconfig.json) | Strict TypeScript for `src/` (`strict`, `noUncheckedIndexedAccess`, Preact `jsxImportSource`). |
| [`tsconfig.node.json`](tsconfig.node.json) | Strict TypeScript for `vite.config.ts`. |
| [`vite.config.ts`](vite.config.ts) | `base: '/static/dist/'`, no `@vitejs/plugin-legacy`, `modulePreload.polyfill: false`, `assetsInlineLimit: 0`, outDir `openai4s/server/webui/dist/`, `/static` proxy for fonts + `style.css` in `npm run dev`, and a post-build guard that rejects inline `<script>`. |

`package.json` also exposes `extract-i18n` / `extract-i18n:check` (F-07; implementation lives in `src/i18n/extract-i18n.mjs`).

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`src/`](src/) | Application source. F-03 is the empty shell (`main.tsx` / `App`). Later items add `compat/`, `stores/`, `components/<area>/`, `features/<area>/`, and `islands/` in their own lanes. |

## Commands

```bash
cd frontend
npm ci
npm run dev          # Vite on :5173, proxy /api /ws /static → :8760
npm run build        # typecheck + emit openai4s/server/webui/dist/
npm test             # vitest run
npm run typecheck    # tsc --noEmit
```

## Constraints

- Workbench CSP is `script-src 'self' 'wasm-unsafe-eval'` (no `unsafe-eval`, no `unsafe-inline`). No runtime template compiler, no Vite inline-script polyfill, no `@vitejs/plugin-legacy`.
- Do not add frontend dependencies to the repository-root `package.json`.
- Global CSS is F-21. Store files under `src/stores/` are F-05. `compat/window-exports.ts` is F-05.
