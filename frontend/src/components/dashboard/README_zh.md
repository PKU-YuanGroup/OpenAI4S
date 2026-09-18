# frontend/src/components/dashboard

[English](README.md)

F-13 工作台外壳。冻结 id 对齐 `tests/webui-contract.md`（`#dashboard`、`#dash-projects`、`#dash-sessions`、`#workspace`、`#messages`、`#dock-notebook`）。`#composer-hint` 带 `role=status aria-live=polite`。`#tab-close` 是真 button。断连横幅接管不存在的 `#conn-dot`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`ModelSelect.tsx`](ModelSelect.tsx) | composer 里的 `#model-select`，由 Customize 的模型 store 渲染；切换时调用 `chooseComposerModel`。 |
| [`ModelSelect.test.tsx`](ModelSelect.test.tsx) | 每个 `/models` 条目一个选项且默认项选中；加载前只有一个空选项；切换 → `PUT /models/default`；被拒绝时恢复原选择。 |
| [`Shell.tsx`](Shell.tsx) | 仪表盘 + 工作台 + composer + 项目模态的标记。`#dash-project-search` 是首页项目筛选框。会路由进工作台的路径（`routesToWorkspace`）首帧两个视图都不显示，只显示 `#route-loading`，因此深链接在语言分包加载期间不会先露出仪表盘。 |
| [`Shell.test.tsx`](Shell.test.tsx) | 路由前的首帧：会话或项目深链接隐藏 `#dashboard` 并显示 `#route-loading`；根路径显示仪表盘并隐藏加载指示。 |
| [`dashboard.css`](dashboard.css) | `#conn-banner`、菜单焦点，以及 `#route-loading`（任一视图显示后由兄弟选择器规则自动隐藏）。全局 token 归 F-21。 |
| [`index.ts`](index.ts) | `Shell`，以及给后续产物瓦片 / 关标签钮车道用的键盘激活辅助函数。 |
