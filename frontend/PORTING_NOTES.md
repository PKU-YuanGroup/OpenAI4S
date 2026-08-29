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
