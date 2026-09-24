# frontend/src/features/chrome

[English](README.md)

F-20 工作台外壳：团队面、模态焦点陷阱、⌘K palette、上传 / 笔记 / 麦克风、布局密度、列宽拖拽。团队模态走 `openModalEl` / `closeModalEl`（旧 IIFE 绕过了陷阱）。Palette 的 Artifact 命中按 M-03（先开会话，再 exact `version_id`）。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 JSON 助手（`/api/v1`、`ApiError`）。 |
| [`chrome.css`](chrome.css) | 本车道样式：palette / notes / team / resizer 类名。 |
| [`clipboard.test.ts`](clipboard.test.ts) | 异步 API 以方法形式调用；被拒时退回选区复制；两条路都不可用时返回 false。 |
| [`clipboard.ts`](clipboard.ts) | `copyText()`：所有复制按钮共用的剪贴板写入，只有确认写入成功才返回 `true`。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `icon` / `ago` / `hint` / `grow`。`icon` 取自共享的 `icons/paths.ts` 表。 |
| [`host.ts`](host.ts) | 用 `isReady` 查 window 能力。不 import `window-exports`。 |
| [`index.ts`](index.ts) | `bootChrome()`：window 赋值、快捷键、绑定、`bootTeam`。每一步相互隔离，某一步抛错不会让其余绑定落空。 |
| [`layout.test.ts`](layout.test.ts) | `os-layout` 持久化、compact/wide 类、列宽钳制、站点存储被禁用，以及某一步抛错后 `bootChrome()` 仍绑定其后各步。 |
| [`layout.ts`](layout.ts) | `applyLayout` / `setLayout`。键 `os-layout`。 |
| [`mic.ts`](mic.ts) | SpeechRecognition 把口述写进 `#composer`。 |
| [`modal.test.ts`](modal.test.ts) | 陷阱栈、Tab 循环、Esc、焦点恢复、团队 fallback 选择器，以及在遮罩上松开的拖选不会关闭弹窗。 |
| [`modal.ts`](modal.ts) | 逐字焦点陷阱（栈 / Tab / Esc / 恢复）。只有按下也发生在遮罩上时，点击遮罩才会关闭。 |
| [`notes.ts`](notes.ts) | Files dock 里的项目笔记。 |
| [`palette.test.ts`](palette.test.ts) | M-03 Artifact 命中、stub 安全的 `isReady`、乱序 `PAL.gen`、技能目录读取失败时不缓存而是下次重试、会话打开失败被处理而不是留下未处理的 rejection、`/search` 未返回时回车作用于当前输入的查询。 |
| [`palette.ts`](palette.ts) | ⌘K palette。Artifact 命中先开会话再 exact version。查询的本地命令立即显示；回车不会作用于上一次查询的列表。 |
| [`resizer.ts`](resizer.ts) | 侧栏 / dock 列宽拖拽。键 `os-side-w` / `os-dock-w`。拖拽柄的提示是静态 `data-i18n-title` 标签，字典加载后和切换语言时随之重绘。 |
| [`resizer.i18n.test.ts`](resizer.i18n.test.ts) | 列宽拖拽柄的提示不会是裸键 `resizer.drag`，并随字典加载和语言切换更新。 |
| [`team.test.ts`](team.test.ts) | 身份芯片、admin 面板、guest 重定向、团队模态走陷阱、审计时间按毫秒时间戳显示。 |
| [`team.ts`](team.ts) | 团队 IIFE。`/auth/me` 探测；admin/files 模态走陷阱。 |
| [`upload.test.ts`](upload.test.ts) | 选择时刻锁定目的地、四条 batch 匹配、单飞、重试覆盖旧失败、失败集 64 上限。 |
| [`upload.ts`](upload.ts) | 文件选择 / 粘贴 / 拖放上传、`UPLOAD_STATE`、首个会话的单飞，以及 send 屏障用的 `waitForPendingUploads`。 |
