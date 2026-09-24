# frontend/src/components/timeline

[English](README.md)

已退役。这里原有一个没有任何地方 import 的 `Timeline` Preact 容器：工作台在 [`../dashboard/Shell.tsx`](../dashboard/Shell.tsx) 里自己渲染 `#dock-timeline` 宿主，由 [`../../features/timeline/island.ts`](../../features/timeline/island.ts) 的命令式孤岛填充；孤岛的布局规则（46px 行高、绝对定位、overview SVG 尺寸）在 `openai4s/server/webui/style.css`。容器自带的样式表也因此从未进入打包。

保留这个目录只是因为 [`../README.md`](../README.md) 仍然列出它；两者应一起删除。

## 文件

没有源文件。
