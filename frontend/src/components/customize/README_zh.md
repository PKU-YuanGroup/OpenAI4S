# frontend/src/components/customize

[English](README.md)

F-19 Customize 模态。九个 tab 组件、嵌套编辑层，以及 `vendors/` 卡。类名（`#cust`、`.cust-tab`、`.prof-row`、`.cust-row`、`.toggle`）与 E2E 契约一致。Tab unmount 会 dispose 定时器租约。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute、远程 GPU、jobs。显示当前会话（没有会话时为默认）的 Python 和 R 实际运行在哪里：环境名、来源（内置 / 托管 / 本机）和版本，以及 R 的关键包能否加载，数据来自环境列表接口的 `runtimes`。常用包一行列出已安装的版本和缺少的包。安装结果留在页面上：装上的版本、装到哪个环境、运行中的内核需要做什么。添加或移除远程主机、提交或取消作业的结果显示在各自的表单下，不再打到弹窗后面的状态行（SCI-04）。本机没有 GPU 时，GPU 一行说明哪些会失败、不会用近似结果代替；「其他远程算力」一行介绍 NVIDIA NIM（守护进程环境里的 Key，此页无需设置）和用于 Slurm 的 `openai4s cluster`，并链接 docs/compute.md；团队成员看不到这些指引（SCI-18）。主机下拉框下面在点击之前就说明「添加」会做什么——确切的只读 `ssh … nvidia-smi` 检查（`remoteProbeCommand`）、主机需要满足的条件、服务要等之后通过受权限规则约束的 ssh 步骤部署、列表保存在哪里，以及「移除」不会改动主机；没选主机时「添加」保持禁用，检查没找到 GPU 时会说明（SCI-19）。输入框为空时「安装」和「运行」保持禁用（SCI-05）。Job 轮询只有一条链，1500ms 绑在租约上；新的读取取代旧的。就绪卡片用 onboarding 的 `EnvironmentCard`；状态来自 send 的 `refreshEnvironmentStatus`。 |
| [`ComputeTab.test.tsx`](ComputeTab.test.tsx) | Python 和 R 两行给出环境名、来源和版本，并说明哪些 R 包可用；常用包一行带版本；安装结果（含版本）和安装失败的原因留在页面上。提交和取消任务都会重读任务列表，但不会再开第二条轮询链；晚到的旧读取结果被丢弃。安装与取消的结果显示在页面里，不经过 `hint`（SCI-04）。GPU 一行不再提 Modal，NIM / Slurm 一行链接算力文档；团队成员两者都看不到（SCI-18）。「添加」说明它的检查、等待选择主机，不可达的结果显示在表单旁（SCI-19）。输入框为空时「添加」「安装」「运行」保持禁用（SCI-05）。 |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector 列表；DataPro 卡隔离在 `vendors/`。测试连接器、删除、从目录添加与添加自定义服务器的结果都显示在控件旁（`ActionStatus`）；两个输入框都填好之前，自定义「添加」保持禁用（SCI-04）。 |
| [`Customize.tsx`](Customize.tsx) | `#cust` 外壳、tablist、Esc / 背景关闭。 |
| [`Customize.test.tsx`](Customize.test.tsx) | 在输入框里拖选文字、到 Customize 或嵌套编辑器的遮罩上才松开，弹窗保持打开；在遮罩上按下仍会关闭。加载状态变化或嵌套编辑器开关不会让模态重新渲染；Skills 与 Memory tab 只在项目变化时重新渲染，不随每次会话列表更新重新渲染。 |
| [`NotebookReplSetting.tsx`](NotebookReplSetting.tsx) | 「通用」页上的「Notebook 手动单元」开关（`GET`/`PUT /notebook/settings`）：默认关闭；`OPENAI4S_NOTEBOOK_REPL` 强制开启时、或团队模式下非管理员时锁定；改动后让已打开的 Notebook 重新读取。 |
| [`GeneralTab.tsx`](GeneralTab.tsx) | 主题、布局、语言、API key 快捷入口、Notebook 手动单元开关，以及「版本与更新」一行。选主题、布局或语言只在原处更新对应的分段控件；语言控件还提供「跟随系统」（按浏览器语言顺序），在用户选定语言之前显示的就是它。 `LabTabSetting` 显示或隐藏实验室标签。 |
| [`GeneralTab.test.tsx`](GeneralTab.test.tsx) | 选主题或布局只移动它自己的分段控件，不重新挂载，也不重新读取整个 tab；语言控件提供「跟随系统」，未保存选择时显示它，并把每次选择交给 `setLang`。 |
| [`UpdateCheck.tsx`](UpdateCheck.tsx) | U2「版本与更新」一行（附录 B.2）：挂载只读本地 `GET /updates/status`；「检查更新」发 `{refresh:false}`、「强制重新检查」发 `{refresh:true}`。显示运行/最新版本、安装渠道、缓存年龄、检查时间、两词信任摘要（作者身份始终「未验证」），`unknown` 时显示原因码与上次已知答案及时间；只有 `up_to_date` 才显示「已是最新版本」。渠道的 refusal 命令仅作可复制文本、绝不执行——无下载、安装、重启、一键升级或轮询。 |
| [`UpdateCheck.test.tsx`](UpdateCheck.test.tsx) | Vitest（HTTP 替身）：挂载只发一次本地 GET、无 POST；两个按钮分别恰发一次 `{refresh:false}` / `{refresh:true}`；双击只发一次 POST；429 显示有界等待；`unknown` 显示原因与上次答案；`admin_only` 拒绝后隐藏按钮；畸形载荷降级为「无法读取」；refusal 命令可复制且不存在安装/下载/重启动作；卸载后到达的答复被丢弃。 |
| [`DiagnosticsTab.tsx`](DiagnosticsTab.tsx) | 被动安全姿态、显式检查、脱敏支持包下载。挂在 General 下。 |
| [`DiagnosticsTab.test.tsx`](DiagnosticsTab.test.tsx) | 页面加载只发一次 status GET；检查与下载包要等点击；请求 id 在纯 http 下也能复制，没复制成功时如实提示。 |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory 开关 / 添加 / 编辑 / 删除，作用域显式发送。 |
| [`MemoryTab.test.tsx`](MemoryTab.test.tsx) | 首次读取同时请求四项数据；写入进行中无论按几次保存，只添加一条记忆。 |
| [`ModelsTab.tsx`](ModelsTab.tsx) | 配置档、本机扫描、probe、capability-receipt badge。没有激活的配置档时，把 `GET /config/llm` 的在用模型（环境变量或已保存设置）显示为当前行。 |
| [`ModelsTab.test.tsx`](ModelsTab.test.tsx) | 只靠 `.env` 配置、没有配置档的安装会显示在用模型，而不是「还没有配置模型」；已有激活配置档时不重复加行；读不到配置也不遮住配置档列表；靠环境变量密钥运行的配置档标成「密钥来自环境变量」，而不是「无密钥」；写入进行中无论按几次添加，本机模型只添加一次；新增、删除配置档或添加本机模型后，重新读取输入框旁 `#model-select` 的列表。 |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job 输出覆盖层。编辑表单要等第一次读取成功后才能保存。由「另存为技能」预填的 Skill 会把会话执行过的步骤列成复选框，勾选会重建正文，直到正文被手动修改（M-84）。 |
| [`NestedEditor.test.tsx`](NestedEditor.test.tsx) | Skill 或 specialist 读取失败时显示错误和重试并禁止保存，空白字段不会覆盖服务端内容。 |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | 导入审阅在启用前展示 requirements、网络模式与 readiness；导入一落地，背后的 Skills 列表就重新读取，不管审阅面板用哪种方式关闭。 |
| [`NetworkTab.tsx`](NetworkTab.tsx) | 豆包卡、allowlist、Tavily 备份、telemetry drain。白名单部分说明这些域名分组是什么、当前是否生效（`egress_mode` 为 off 或 `allowlist`），列出已批准的域名，按界面语言显示分组名（`allowlistGroupName`），超过 12 个域名时给出剩余数量而不是悄悄截掉。 页头和开关区分应用联网（智能体的网页搜索、读取网页、科学数据库和下载；不影响模型服务）与内核联网（单元里的代码，默认被沙盒阻止，这一页的设置不会放开它）。 |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | 按作用域的审批规则。没有打开会话时读取 `GET /permissions`，只列出全局规则。每条规则写明工具的用途，标题下的摘要从全局规则读出。规则的决定是乐观更新，服务端拒绝时回到原值。 |
| [`SearchEngines.tsx`](SearchEngines.tsx) | 「网络」页的搜索引擎顺序：豆包固定置顶，备用引擎可拖动或用箭头排序、可开关，并可填写 Tavily / You.com key。 |
| [`PermissionsTab.test.tsx`](PermissionsTab.test.tsx) | 服务端接受的决定保持显示；被拒绝的决定回到规则原来的值（会触发重新渲染，下拉框随之复位）。没有会话时读取并列出全局规则及其说明和摘要；有会话时列出三个作用域。 |
| [`refresh.test.tsx`](refresh.test.tsx) | 写入返回前用户已切到别的 tab 或打开了另一个嵌套编辑器，返回后不把用户拉回去；写入时 tab 仍在显示的，原地重读，不重新挂载。 |
| [`SkillsTab.tsx`](SkillsTab.tsx) | 个人 / 项目 / collection Skills。搜索框按与 `/` 选择器相同的方式给全部技能（含合集成员）排序（名称、说明、中文关键词、声明的域名），也能按合集自己的名字（「bioskills」「bio skills」）找到合集：合集行显示在结果上方、可以展开，计数行会说明其中多少个结果属于某个合集（`searchSkillCatalog`，SCI-09）；`host_only` 技能会标出它把会话联网限定到几个域名，展开后说明加载它对本会话余下时间的影响：只能访问列出的域名，或者——没有声明任何域名时——关闭联网，以及新开会话才能恢复（SCI-07）。无法在本机运行的技能用人话说明原因；需要远程 GPU 或安装包时提供「打开「计算」」（SCI-08）。点击名称展开该行：完整说明（来自 `GET /skills/{name}` 的 `full_description`）、用人话写的需求与联网方式，以及渲染后的 SKILL.md（`renderMd`，不加载外部资源）；「在对话中使用」是带文字的按钮（SCI-06）。 |
| [`SkillsTab.test.tsx`](SkillsTab.test.tsx) | 技能页搜索能按名称、说明里的词、中文关键词或声明的域名找到技能，能找到合集成员，未命中时为空；只有 `host_only` 技能带联网限制标记，0 个域名时说明为关闭联网，1 个域名时英文用单数（SCI-07）。展开的行在文档返回后显示完整说明，并用人话写出需求和联网方式（SCI-06）。按名字能找到合集，计数行说明多少个结果是合集成员（SCI-09）。 |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | 自定义 specialist 与内置角色。 |
| [`switches.test.tsx`](switches.test.tsx) | Notebook 手动单元开关写入 `repl_enabled`，被环境变量强制开启或对团队成员时锁定。网络出站与记忆开关在第一次读取落地前保持禁用；写入进行中再次点击不生效；写入失败时回到服务端确认过的值。Skill、specialist 与连接器行上的开关显示之后读取带来的值，服务端已接受的写入不会被更早的读取覆盖。 |
| [`customize.css`](customize.css) | 车道本地模态样式，直到 F-21 移植 `style.css`。嵌套编辑框滚动时保存按钮固定在底部可见，以及「另存为技能」的步骤列表（M-84）。`.cust-action-status`：操作结果显示在控件旁（SCI-04）。 |
| [`icons.tsx`](icons.tsx) | `Icon`：只从共享的 `features/icons/paths.ts` 表取图形，自己不再保留任何图形。 |
| [`icons.test.tsx`](icons.test.tsx) | 模态用到的每个图标名都能画出；共享表里有的名字取自共享表；未知名字不画任何东西。 |
| [`hooks.ts`](hooks.ts) | `useTabRead`：tab 的读取，挂载时读一次，写入后经 `refreshCustTab` 原地重读；只有最新一次读取生效，第一次读取结算面板的加载状态。`useOptimistic` / `useOptimisticToggle`：绑定某项服务端设置的控件，在 tab 第一次读取落地前（`null`）保持禁用；点击后立即变化，同一时间只允许一次写入，写入失败时回到服务端确认过的值。 |
| [`index.ts`](index.ts) | 再导出 `Customize`。 |
| [`ui.tsx`](ui.tsx) | 共用的 `Hdr` / `CustRow` / `Seg` / `Toggle` / `Pill`，以及 `ActionStatus`：始终挂载的 live region，把操作结果显示在控件旁，而不是弹窗后面的会话状态行（SCI-04）。 |
| [`use-timer-lease.ts`](use-timer-lease.ts) | 绑 unmount 的 `useTimerLease` / `useAlive`。 |
| [`ExperimentsTab.tsx`](ExperimentsTab.tsx) | General 上的实验性语义判断区块：披露确认、能力开关、TypeSafe key、连接测试。 |
| [`ExperimentsTab.test.tsx`](ExperimentsTab.test.tsx) | 未确认披露不能打开；`env_off` 置灰开关；key 不回显；连接测试覆盖 ok / unavailable / disabled。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`vendors/`](vendors/) | 火山 / DataPro / 豆包卡，与 tab 外壳隔离。 |
