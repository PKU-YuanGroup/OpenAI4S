# frontend/src/features/theme

[English](README.md)

工作台外观（浅色 / 深色 / 跟随系统）。`openai4s/server/webui/theme-bootstrap.js` 继续作为经典 `<script src>` 放在 SPA 的 head 里，这样第一次绘制就已经带上 `data-theme`。本模块在加载后重新同步、持久化 `os-theme`，并在偏好为 `system` 时跟随 `prefers-color-scheme`。它只在 `<html>` 上写 `data-theme`，不写 `body.theme-dark`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`theme.ts`](theme.ts) | `applyTheme` / `setTheme` / `cycleTheme` / `installTheme`。localStorage 键为 `os-theme`。 |
| [`theme.test.ts`](theme.test.ts) | 单真值源、存储键、循环切换、跟随系统、经典 head 脚本、无 `body.theme-dark`。 |
