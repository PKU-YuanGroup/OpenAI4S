# frontend/src

[English](README.md)

下一版工作台的源码。F-03 挂载一个 Preact 空壳。后续 F 系列工作项在各自车道里加模块（`compat/`、`stores/`、`components/<area>/`、`features/<area>/`、`islands/`），对 `stores/` 只 import、不改本体。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`app.test.ts`](app.test.ts) | 脚手架 Vitest：`@preact/signals` 能更新。 |
| [`app.tsx`](app.tsx) | 空壳 `App`。真正的视图落地后会替换它。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。后续工作项可以在这里加一行模块 import。 |
| [`vite-env.d.ts`](vite-env.d.ts) | Vite 客户端类型（`import.meta.env`）。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`features/`](features/) | 按车道划分的功能。F-09 加入 `theme/`。 |
