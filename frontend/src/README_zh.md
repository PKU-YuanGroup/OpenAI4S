# frontend/src

[English](README.md)

下一版工作台的源码。F-03 挂载一个 Preact 空壳。后续 F 系列工作项在各自车道里加模块（`compat/`、`stores/`、`components/<area>/`、`features/<area>/`、`islands/`），对 `stores/` 只 import、不改本体。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`app.test.ts`](app.test.ts) | 脚手架 Vitest：`@preact/signals` 能更新；语言变化时应用根节点会重绘。 |
| [`app.tsx`](app.tsx) | 工作台 `App`。F-13 挂载仪表盘/工作台 `Shell`；它读取 `languageRevision`，切换语言时整棵 Shell 树都会重绘。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-20 import `features/chrome`（`bootChrome`）。后续工作项可以再加一行模块 import。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-19 import `features/customize`（`bootCustomize`）。后续工作项可以再加一行模块 import。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-17 import `features/artifacts`（`bootArtifacts`）。后续工作项可以再加一行模块 import。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-14 import `features/notebook`（`installNotebook`）。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-13 import `features/sessions`。后续工作项可以再加一行模块 import。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-10 import `features/messages`。 |
| [`main.tsx`](main.tsx) | `preact.render` 挂到 `#app`。F-05 在这里 import `compat/window-exports`。F-06 import `features/ws`（`bootWs`）。F-11 import `features/send`，并在 `render` 之后、`bootChrome()` 旁边调用 `bindComposer()`。 |
| [`vite-env.d.ts`](vite-env.d.ts) | Vite 客户端类型（`import.meta.env`）。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`compat/`](compat/) | F-05 的 window 导出层和 `window.S` Proxy。后续车道只允许在标记区 append。 |
| [`components/`](components/) | 视图组件。F-19 加入 `customize/`（九个 tab + vendor 卡）。 |
| [`components/`](components/) | 按车道划分的 Preact 视图。F-17 加入 `artifacts/`（Files dock）。 |
| [`components/`](components/) | 按车道划分的 Preact 视图。F-13 加入 `dashboard/`。 |
| [`features/`](features/) | 按车道划分的功能。F-09 加入 `theme/`。 |
| [`features/`](features/) | F 系列领域内核。F-08 放下 markdown / 高亮 / CSV / 流截断 / 涂抹这些纯函数模块。 |
| [`i18n/`](i18n/) | F-07：抽取的 zh/en 字典、`t()` / `tOptional` 运行时、计划模式 payload 辅助函数。 |
| [`stores/`](stores/) | F-05 的 signal 模块。后续车道只 import 这些文件，不改本体。 |
| [`components/`](components/) | 视图容器。Action Timeline 不在这里：它是 `features/timeline/` 里的命令式孤岛。 |
| [`islands/`](islands/) | F-18 命令式孤岛：3Dmol 懒注入、图片标注器、Ketcher、PDF/html-preview sandbox。 |
