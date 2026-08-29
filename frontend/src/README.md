# frontend/src

[中文说明](README_zh.md)

Source for the next workbench. F-03 mounts a Preact empty shell. Later F-series items add modules in their own lanes (`compat/`, `stores/`, `components/<area>/`, `features/<area>/`, `islands/`) and only import `stores/` rather than editing it.

## Files

| File | Responsibility |
| --- | --- |
| [`app.test.ts`](app.test.ts) | Scaffold Vitest: `@preact/signals` updates. |
| [`app.tsx`](app.tsx) | Empty-shell `App`. Replaced as real views land. |
| [`main.tsx`](main.tsx) | `preact.render` onto `#app`. F-05 imports `compat/window-exports`. F-06 imports `features/ws` (`bootWs`). F-19 imports `features/customize` (`bootCustomize`). Later items may add one module import here. |
| [`vite-env.d.ts`](vite-env.d.ts) | Vite client types (`import.meta.env`). |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`compat/`](compat/) | F-05 window export layer and `window.S` Proxy. Later lanes only append in the marker region. |
| [`components/`](components/) | View components. F-19 adds `customize/` (nine tabs + vendor cards). |
| [`features/`](features/) | Lane-owned features. F-09 adds `theme/`. |
| [`features/`](features/) | F-series domain kernels. F-08 lands the pure-function markdown / highlight / CSV / stream-cap / scrub modules. |
| [`i18n/`](i18n/) | F-07: extracted zh/en dictionaries, `t()` / `tOptional` runtime, plan-mode payload helper. |
| [`stores/`](stores/) | F-05 signal modules. Later lanes import these files; they do not edit them. |
