# frontend/src/features/icons

[English](README.md)

工作台线性图标（`app.js:7-77` 的 lucide 路径）的共享表。移植时把 app.js 的表拆成了各车道的副本，副本之间逐渐走样：`panel-left`、`panel-right`、`moon`、`sun` 不在 `paintIcons()` 读取的任何一张表里，于是侧栏、停靠面板和主题按钮只画出空的 `<svg>`。`features/sessions/icon.ts`、`features/chrome/dom.ts` 与 `features/notebook/chrome.ts` 从这里取图形；新增图标名加到这张表，不要加到某个车道里。还有四个车道保留自己的表，尚未合并进来：`features/send/icon.ts`、`features/timeline/dom.ts`、`islands/dom.ts` 和 `features/artifacts/api.ts`。

`paintIcons()` 只在绑定 Shell 时运行一次。此后创建的节点必须在创建处用 `paintIcon(node, name, size)` 画出图标；迟到的节点只带 `data-icon` 属性，会是一个空按钮。

表项是按 app.js 的方式以 innerHTML 注入的静态标记。不要用数据拼出表项。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`paths.ts`](paths.ts) | `ICON_PATHS`、`iconSvg(name, size, cls)` 与 `paintIcon(node, name, size)`。未知名称不画任何图形。 |
| [`icons.coverage.test.ts`](icons.coverage.test.ts) | 源码中每个 `data-icon="…"` / `setAttribute("data-icon", "…")` 名称，`paintIcon(node, "…")` 名称，加上主题切换用的 sun/moon，经 `paintIcons` 都能画出图形；本目录之外的源码不会只设置 `data-icon` 而不画图；notebook 的 `iconEl` 对传入的每个名称都能画出图形；sessions 与 chrome 两个辅助函数对同一名称画出相同的图。 |
