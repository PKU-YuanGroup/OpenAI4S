# frontend/src

[中文说明](README_zh.md)

Source for the next workbench. F-03 mounts a Preact empty shell. Later F-series items add modules in their own lanes (`compat/`, `stores/`, `components/<area>/`, `features/<area>/`, `islands/`) and only import `stores/` rather than editing it.

## Files

| File | Responsibility |
| --- | --- |
| [`app.test.ts`](app.test.ts) | Scaffold Vitest: `@preact/signals` updates. |
| [`app.tsx`](app.tsx) | Empty-shell `App`. Replaced as real views land. |
| [`main.tsx`](main.tsx) | `preact.render` onto `#app`. Later items may add one module import here. |
| [`vite-env.d.ts`](vite-env.d.ts) | Vite client types (`import.meta.env`). |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`features/`](features/) | F-series domain kernels. F-08 lands the pure-function markdown / highlight / CSV / stream-cap / scrub modules. |
