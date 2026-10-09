# frontend/src/components/dashboard

[English](README.md)

F-13 工作台外壳。冻结 id 对齐 `tests/webui-contract.md`（`#dashboard`、`#dash-projects`、`#dash-sessions`、`#workspace`、`#messages`、`#dock-notebook`）。`#composer-hint` 带 `role=status aria-live=polite`。`#tab-close` 是真 button。断连横幅接管不存在的 `#conn-dot`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`DashHero.tsx`](DashHero.tsx) | `DashHeroText`：仪表盘的大标题和一句话介绍，文案来自 `copy.ts`，经 `onLanguageChange` 在切换语言时重绘。 |
| [`ModelSelect.tsx`](ModelSelect.tsx) | composer 里的 `#model-select`，由 Customize 的模型 store 渲染。打开某个会话时显示该会话自己固定的模型（`composerSelection`），固定的配置已不在列表里时显示一个禁用的占位项；否则显示服务端默认。切换时调用 `chooseComposerModel`。 |
| [`ModelSelect.test.tsx`](ModelSelect.test.tsx) | 每个 `/models` 条目一个选项且默认项选中；加载前只有一个空选项；切换 → `PUT /models/default`；被拒绝时恢复原选择。打开会话时：显示该会话的固定而非默认（未固定或属于别的会话 → 默认；不在列表里或修订号较早 → 禁用占位项，选该条目会改绑到它当前的修订号）；切换先改绑该会话（`POST /frames/{id}/model-binding {model_id}`）再 `PUT /models/default`，随后重新加载列表；改绑被拒时两者都不变并重读固定；选择已过期时重新加载列表；默认被拒（团队模式 `admin_only`）时保留会话的切换；两次快速选择按顺序发出且只有后一次会完成；已被取代的选择或用户已离开的会话收到的拒绝不改变任何东西；迟到的响应和慢到的固定读取都不会撤销切换；发送的 202 绑定会被采用，除非发送开始后又选过模型；列表里找不到的固定会让列表重新加载一次；较早选择的默认被拒不会撤销较新的选择；运行中的提示文案。没有打开的会话时，会告诉成员默认值由管理员设置。 |
| [`Shell.tsx`](Shell.tsx) | 仪表盘 + 工作台 + composer + 项目模态的标记。仪表盘主视觉区（`.dash-hero`）放大标题、`#dash-new-project`（主操作）和 `#dash-import-session`；注意力流作为第二列挂进这里。`#dash-project-search` 是首页项目筛选框。会路由进工作台的路径（`routesToWorkspace`）首帧两个视图都不显示，只显示 `#route-loading`，因此深链接在语言分包加载期间不会先露出仪表盘。切换语言时 Shell 会重绘，所以其中不绑定任何由路由写入的值：`#conv-title` 用 `defaultValue`。 |
| [`Shell.test.tsx`](Shell.test.tsx) | 路由前的首帧：会话或项目深链接隐藏 `#dashboard` 并显示 `#route-loading`；根路径显示仪表盘并隐藏加载指示。「新建项目」是主视觉区里唯一的主按钮（`solid-btn`），它和「导入」都在主视觉区而不在页眉。重绘不会改动会话标题。 |
| [`copy.ts`](copy.ts) | `dashT`：主视觉区中英文案，不写进生成的 `i18n/en.ts` / `zh.ts`。 |
| [`dashboard.css`](dashboard.css) | `#conn-banner`、菜单焦点、`#route-loading`（任一视图显示后由兄弟选择器规则自动隐藏）、主视觉区布局，以及本地项目文件夹选择器/浏览器。全局 token 归 F-21。 |
| [`index.ts`](index.ts) | `Shell`，以及给后续产物瓦片 / 关标签钮车道用的键盘激活辅助函数。 |
