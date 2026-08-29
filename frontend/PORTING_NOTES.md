# Porting notes

## F-03 frontend/ scaffold

This work item does not port domain logic from `app.js`. It creates the empty Preact 10 + `@preact/signals` + TypeScript (strict) + Vite + Vitest workspace. Later F-series items append one section each.

| Old (`openai4s/server/webui/`) | New | Semantics kept |
| --- | --- | --- |
| *(none — no domain kernel in F-03)* | `frontend/` workspace | Empty shell only. `base: '/static/dist/'`. `@vitejs/plugin-legacy` forbidden. `build.modulePreload.polyfill: false` so modulepreload is external `<link>` tags, not an inline polyfill. Build fails if any HTML contains a `<script>` without `src=`. |
| Static files served from this directory | `npm run build` writes `openai4s/server/webui/dist/` | Wheel still Node-free. Serving `dist/index.html` behind `OPENAI4S_WEBUI_NEXT=1` is F-04. `theme-bootstrap.js` and `scientific_renderers.js` stay classic scripts (F-09 / unchanged). |

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
