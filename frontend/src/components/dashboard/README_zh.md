# frontend/src/components/dashboard

[English](README.md)

F-13 工作台外壳。冻结 id 对齐 `tests/webui-contract.md`（`#dashboard`、`#dash-projects`、`#dash-sessions`、`#workspace`、`#messages`、`#dock-notebook`）。`#composer-hint` 带 `role=status aria-live=polite`。`#tab-close` 是真 button。断连横幅接管不存在的 `#conn-dot`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`Shell.tsx`](Shell.tsx) | 仪表盘 + 工作台 + composer + 项目模态的标记。 |
| [`dashboard.css`](dashboard.css) | `#conn-banner` 与菜单焦点。全局 token 归 F-21。 |
| [`index.ts`](index.ts) | `Shell`，以及给后续产物瓦片 / 关标签钮车道用的键盘激活辅助函数。 |
