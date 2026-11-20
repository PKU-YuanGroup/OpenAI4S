# frontend/src/features/chrome

[English](README.md)

F-20 工作台外壳：团队面、模态焦点陷阱、⌘K palette、上传 / 麦克风、布局密度、列宽拖拽。团队模态走 `openModalEl` / `closeModalEl`（旧 IIFE 绕过了陷阱）。Palette 的 Artifact 命中按 M-03（先开会话，再 exact `version_id`）。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 JSON 助手（`/api/v1`、`ApiError`）。 |
| [`chrome.css`](chrome.css) | 本车道样式：palette / team / resizer 类名。 |
| [`clipboard.test.ts`](clipboard.test.ts) | 异步 API 以方法形式调用；被拒时退回选区复制；两条路都不可用时返回 false。 |
| [`clipboard.ts`](clipboard.ts) | `copyText()`：所有复制按钮共用的剪贴板写入，只有确认写入成功才返回 `true`。 |
| [`dom.ts`](dom.ts) | `$` / `el` / `icon` / `ago` / `localDateTime` / `localClock` / `hint` / `grow`。`icon` 取自共享的 `icons/paths.ts` 表。 `ago` / `agoMs` 按界面语言给出相对时间（刚刚 / 3 分钟前，just now / 3m）。 |
| [`host.ts`](host.ts) | 用 `isReady` 查 window 能力。不 import `window-exports`。 |
| [`index.ts`](index.ts) | `bootChrome()`：window 赋值、快捷键、绑定、`bootTeam`。每一步相互隔离，某一步抛错不会让其余绑定落空。 |
| [`layout.test.ts`](layout.test.ts) | `os-layout` 持久化、compact/wide 类、列宽钳制、站点存储被禁用，以及某一步抛错后 `bootChrome()` 仍绑定其后各步，且不再重复绑定归 artifacts 所有的 `#files-btn`。 |
| [`layout.ts`](layout.ts) | `applyLayout` / `setLayout`。键 `os-layout`。 |
| [`mic.ts`](mic.ts) | SpeechRecognition 把口述写进 `#composer`。 |
| [`modal.test.ts`](modal.test.ts) | 陷阱栈、Tab 循环、Esc、焦点恢复、团队 fallback 选择器、在遮罩上松开的拖选不会关闭弹窗，以及延后一帧的初始聚焦不会抢走弹窗内已获得焦点的控件。 |
| [`modal.ts`](modal.ts) | 逐字焦点陷阱（栈 / Tab / Esc / 恢复）。初始聚焦延后一帧等内容渲染；若焦点在此之前已进入弹窗内部，则不再改动。只有按下也发生在遮罩上时，点击遮罩才会关闭。 |
| [`palette.test.ts`](palette.test.ts) | M-03 Artifact 命中、stub 安全的 `isReady`、乱序 `PAL.gen`、技能目录读取失败时不缓存而是下次重试、会话打开失败被处理而不是留下未处理的 rejection、`/search` 未返回时回车作用于当前输入的查询。 |
| [`palette.ts`](palette.ts) | ⌘K palette。Artifact 命中先开会话再 exact version。查询的本地命令立即显示；回车不会作用于上一次查询的列表。 |
| [`resizer.ts`](resizer.ts) | 侧栏 / dock 列宽拖拽。键 `os-side-w` / `os-dock-w`。拖拽柄的提示是静态 `data-i18n-title` 标签，字典加载后和切换语言时随之重绘。 |
| [`resizer.i18n.test.ts`](resizer.i18n.test.ts) | 列宽拖拽柄的提示不会是裸键 `resizer.drag`，并随字典加载和语言切换更新。 |
| [`team-account.ts`](team-account.ts) | 当前登录者自己的账号对话框，从用户菜单打开：修改密码（`POST /auth/me/password`，需要当前密码）与我的模型 key（`GET`/`PUT`/`DELETE /auth/me/llm-key`；保存后不再显示 key）。另有 `teamDialog`：带真实关闭图标的团队小模态。 |
| [`team-admin.ts`](team-admin.ts) | 团队管理面板的「用户」小节：添加用户（自动生成的密码只显示一次），以及每行的停用 / 启用 / 重置密码，破坏性操作在原位二次确认而不弹原生对话框；管理员自己的那一行不提供这些操作。另有共享标签：`auditActionLabel`、`userLabel`。 |
| [`team-chip.tsx`](team-chip.tsx) | `TeamChip`：当前登录者（点击打开用户菜单）与「团队文件」入口，由 Shell 渲染在首页顶栏和工作台侧栏底部。单用户模式且没有数据根目录时不渲染任何内容。 |
| [`team-copy.ts`](team-copy.ts) | `teamT`：团队界面的中英文案（用户菜单、角色、账号对话框、团队模式下的「会话不存在」说明）。 |
| [`team-identity.ts`](team-identity.ts) | `teamUser` 信号（当前登录者，来自 `/auth/me`；单用户模式为 `null`）、`teamFilesAvailable`，以及 `isTeamAdmin()` / `isTeamMember()`，供「设置 → 计算」这类视图跳过成员必然被拒绝的请求。 |
| [`team.test.ts`](team.test.ts) | 两处位置的用户芯片、按角色的用户菜单、退出登录不弹原生确认框、除模态外不向 `<body>` 追加任何节点、admin 面板、guest 重定向、团队模态走陷阱、审计时间按毫秒时间戳显示、晚到的目录读取不会覆盖较新的目录列表、admin 分节标题随界面语言显示。 |
| [`team.ts`](team.ts) | `/auth/me` 探测（写入 `teamUser`）、用户菜单（`teamMenuItems`、`openUserMenu`、`signOut`），以及走焦点陷阱的 admin/files 模态；管理面板各小节在 id 旁显示名字，并跟随界面语言。只有最新一次目录读取会渲染，也只有它会改变上传目标。 |
| [`upload.test.ts`](upload.test.ts) | 选择时刻锁定目的地、四条 batch 匹配、单飞、重试覆盖旧失败、失败集 64 上限；UP-3：每次上传先带 `if_exists: "error"`，保留两者 / 替换 / 取消分别重发或跳过，取消不算失败。 |
| [`upload.ts`](upload.ts) | 文件选择 / 粘贴 / 拖放上传、`UPLOAD_STATE`、首个会话的单飞（带 `reuseEmpty` 时向 `POST /frames` 要项目中最新的未使用会话），以及 send 屏障用的 `waitForPendingUploads`。同名文件先被拒绝（`if_exists: "error"`），再按 `upload-conflict.ts` 的回答重发（UP-3）；被取消的文件记为 `skipped`，不算失败。多文件一次选中时逐个上传，每个文件都会等会话空闲（409 `trusted_capture_busy`）再发，期间显示逐个进度，最后给一条汇总（UP-1/UP-2）。 |
| [`upload-conflict.ts`](upload-conflict.ts) | UP-3 同名上传提示：「保留两者」（`name-2.ext`）、「替换为新版本」或「取消」；并发上传的对话框排队逐个显示，「对本批其余同名文件执行相同操作」回答整批，Esc/点遮罩/其它关闭方式一律视为取消；功能内中英文案（`uploadT`）。 |
| [`upload-conflict.test.ts`](upload-conflict.test.ts) | 编号规则与服务端一致、对话框按序逐个显示、按批记忆、无法显示时取消。 |
