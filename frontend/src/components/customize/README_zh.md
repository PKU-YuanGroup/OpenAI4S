# frontend/src/components/customize

[English](README.md)

F-19 Customize 模态。十一个 tab、嵌套编辑层，以及 `vendors/` 卡。类名（`#cust`、`.cust-tab`、`.prof-row`、`.cust-row`、`.toggle`）与 E2E 契约一致。Tab unmount 会 dispose 定时器租约。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute、远程 GPU、jobs。显示当前会话（没有会话时为默认）的 Python 和 R 实际运行在哪里：环境名、来源（内置 / 托管 / 本机）和版本，以及 R 的关键包能否加载，数据来自环境列表接口的 `runtimes`。常用包一行列出已安装的版本和缺少的包。安装结果留在页面上：装上的版本、装到哪个环境、运行中的内核需要做什么。Job 轮询只有一条链，1500ms 绑在租约上；新的读取取代旧的。就绪卡片用 onboarding 的 `EnvironmentCard`；状态来自 send 的 `refreshEnvironmentStatus`。 |
| [`ComputeTab.test.tsx`](ComputeTab.test.tsx) | Python 和 R 两行给出环境名、来源和版本，并说明哪些 R 包可用；常用包一行带版本；安装结果（含版本）和安装失败的原因留在页面上。提交和取消任务都会重读任务列表，但不会再开第二条轮询链；晚到的旧读取结果被丢弃。 |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector 列表；DataPro 卡隔离在 `vendors/`。 |
| [`Customize.tsx`](Customize.tsx) | `#cust` 外壳、tablist、Esc / 背景关闭，以及 tab 内容下方的状态行：重复显示上一个操作报告的结果（`customizeNotice`；单靠 `hint()` 只会写到输入框下方，被弹窗挡住）。 |
| [`Customize.test.tsx`](Customize.test.tsx) | 在输入框里拖选文字、到 Customize 或嵌套编辑器的遮罩上才松开，弹窗保持打开；在遮罩上按下仍会关闭。加载状态变化或嵌套编辑器开关不会让模态重新渲染；Skills 与 Memory tab 只在项目变化时重新渲染，不随每次会话列表更新重新渲染。 |
| [`NotebookReplSetting.tsx`](NotebookReplSetting.tsx) | 「通用」页上的「Notebook 手动单元」开关（`GET`/`PUT /notebook/settings`）：默认关闭；`OPENAI4S_NOTEBOOK_REPL` 强制开启时、或团队模式下非管理员时锁定；改动后让已打开的 Notebook 重新读取。 |
| [`GeneralTab.tsx`](GeneralTab.tsx) | 主题、布局、语言、实验室标签和 Notebook 手动单元开关；不向服务端读取任何东西。模型 key 在「模型」页，更新和诊断在「诊断与更新」页，语义判断层在「实验功能」页（M-22）。选主题、布局或语言只在原处更新对应的分段控件；语言控件还提供「跟随系统」（按浏览器语言顺序），在用户选定语言之前显示的就是它。 `LabTabSetting` 显示或隐藏实验室标签。 Notebook 开关放在「这台服务器」分组下，并写明它对所有窗口和用户生效（SYNC-27）。 |
| [`GeneralTab.test.tsx`](GeneralTab.test.tsx) | 选主题或布局只移动它自己的分段控件，不重新挂载，也不读取任何东西；「通用」页没有更新、实验功能或诊断区块；语言控件提供「跟随系统」，未保存选择时显示它，并把每次选择交给 `setLang`。 |
| [`UpdateCheck.tsx`](UpdateCheck.tsx) | U2「版本与更新」一行（附录 B.2）：挂载只读本地 `GET /updates/status`；「检查更新」发 `{refresh:false}`、「强制重新检查」发 `{refresh:true}`。显示运行/最新版本、安装渠道、缓存年龄、检查时间、两词信任摘要（作者身份始终「未验证」），`unknown` 时显示原因码与上次已知答案及时间；只有 `up_to_date` 才显示「已是最新版本」。渠道的 refusal 命令仅作可复制文本、绝不执行——无下载、安装、重启、一键升级或轮询。 |
| [`UpdateCheck.test.tsx`](UpdateCheck.test.tsx) | Vitest（HTTP 替身）：挂载只发一次本地 GET、无 POST；两个按钮分别恰发一次 `{refresh:false}` / `{refresh:true}`；双击只发一次 POST；429 显示有界等待；`unknown` 显示原因与上次答案；`admin_only` 拒绝后隐藏按钮；畸形载荷降级为「无法读取」；refusal 命令可复制且不存在安装/下载/重启动作；卸载后到达的答复被丢弃。 |
| [`DiagnosticsTab.tsx`](DiagnosticsTab.tsx) | 被动安全姿态、显式检查、脱敏支持包下载。位于「诊断与更新」页，在更新一行上方。 |
| [`DiagnosticsTab.test.tsx`](DiagnosticsTab.test.tsx) | 页面加载只发一次 status GET；检查与下载包要等点击；请求 id 在纯 http 下也能复制，没复制成功时如实提示。 |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory 开关 / 添加 / 编辑 / 删除，作用域显式发送。团队成员看到的开关是锁定的并写明原因，也没有「所有项目」作用域。 类型按名称列出并附一句用途说明，上下文一行说明新会话会带上什么（M-81）。 |
| [`MemoryTab.test.tsx`](MemoryTab.test.tsx) | 首次读取同时请求四项数据；写入进行中无论按几次保存，只添加一条记忆。 |
| [`ModelsTab.tsx`](ModelsTab.tsx) | 添加表单的「模型」输入框可以用「获取模型列表」（`POST /model-profiles/discover`，与向导同一个列表）给出候选，也仍可填写任意 id。配置档、本机扫描、probe、capability-receipt badge。没有激活的配置档时，把 `GET /config/llm` 的在用模型（环境变量或已保存设置）显示为当前行。团队成员只能查看列表（没有添加表单、key 输入框、服务商面板、本机扫描和行内操作，也不显示接口地址），并会看到自己的 key 该放在哪里。 |
| [`ModelsTab.test.tsx`](ModelsTab.test.tsx) | 只靠 `.env` 配置、没有配置档的安装会显示在用模型，而不是「还没有配置模型」；已有激活配置档时不重复加行；读不到配置也不遮住配置档列表；靠环境变量密钥运行的配置档标成「密钥来自环境变量」，而不是「无密钥」；写入进行中无论按几次添加，本机模型只添加一次；新增、删除配置档或添加本机模型后，重新读取输入框旁 `#model-select` 的列表。 |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job 输出覆盖层。编辑表单要等第一次读取成功后才能保存。 |
| [`NestedEditor.test.tsx`](NestedEditor.test.tsx) | Skill 或 specialist 读取失败时显示错误和重试并禁止保存，空白字段不会覆盖服务端内容。 |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | 导入审阅在启用前展示 requirements、网络模式与 readiness；导入一落地，背后的 Skills 列表就重新读取，不管审阅面板用哪种方式关闭。 |
| [`NetworkTab.tsx`](NetworkTab.tsx) | 豆包卡、allowlist、Tavily 备份、telemetry drain。白名单部分说明这些域名分组是什么、当前是否生效（`egress_mode` 为 off 或 `allowlist`），给出切换到另一种模式的完整重启命令和复制按钮（`egressRestartCommand`；模式在启动时读取，没有运行时开关），列出已批准的域名，按界面语言显示分组名（`allowlistGroupName`），超过 12 个域名时给出剩余数量而不是悄悄截掉。 页头和开关区分应用联网（智能体的网页搜索、读取网页、科学数据库和下载；不影响模型服务）与内核联网（单元里的代码，默认被沙盒阻止，这一页的设置不会放开它）。团队成员只能查看：没有 Ollama / 豆包 key 卡片，开关锁定并写明原因。 |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | 按作用域的审批规则。没有打开会话时读取 `GET /permissions`，只列出全局规则。每条规则写明工具的用途，标题下的摘要从全局规则读出。规则的决定是乐观更新，服务端拒绝时回到原值。团队成员看到全局规则被锁定、「恢复默认」不可用，并各自写明原因。 |
| [`SearchEngines.tsx`](SearchEngines.tsx) | 「网络」页的搜索引擎顺序：豆包固定置顶，备用引擎可拖动或用箭头排序、可开关，并可填写 Tavily / You.com key；`readOnly` 时只显示列表，没有 key 输入框，也不能修改。 |
| [`PermissionsTab.test.tsx`](PermissionsTab.test.tsx) | 服务端接受的决定保持显示；被拒绝的决定回到规则原来的值（会触发重新渲染，下拉框随之复位）。没有会话时读取并列出全局规则及其说明和摘要；有会话时列出三个作用域。 |
| [`refresh.test.tsx`](refresh.test.tsx) | 写入返回前用户已切到别的 tab 或打开了另一个嵌套编辑器，返回后不把用户拉回去；写入时 tab 仍在显示的，原地重读，不重新挂载。 |
| [`SkillsTab.tsx`](SkillsTab.tsx) | 个人 / 项目 / collection Skills。搜索框按与 `/` 选择器相同的方式给全部技能（含合集成员）排序（名称、说明、中文关键词、声明的域名）；`host_only` 技能会标出它把会话联网限定到几个域名，展开可见域名列表。 |
| [`SkillsTab.test.tsx`](SkillsTab.test.tsx) | 技能页搜索能按名称、说明里的词、中文关键词或声明的域名找到技能，能找到合集成员，未命中时为空；只有 `host_only` 技能带联网限制标记。 |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | 自定义 specialist 与内置角色。 |
| [`switches.test.tsx`](switches.test.tsx) | Notebook 手动单元开关写入 `repl_enabled`，被环境变量强制开启或对团队成员时锁定。网络出站与记忆开关在第一次读取落地前保持禁用；写入进行中再次点击不生效；写入失败时回到服务端确认过的值。Skill、specialist 与连接器行上的开关显示之后读取带来的值，服务端已接受的写入不会被更早的读取覆盖。 |
| [`customize.css`](customize.css) | 车道本地模态样式，直到 F-21 移植 `style.css`。窄屏（SYNC-22）：开关保持胶囊形状且点击区域为 40px，标签条在还有更多标签的一侧显示阴影，≤480px 时行内控件和添加行会换行，不再超出屏幕。 |
| [`icons.tsx`](icons.tsx) | `Icon`：只从共享的 `features/icons/paths.ts` 表取图形，自己不再保留任何图形。 |
| [`icons.test.tsx`](icons.test.tsx) | 模态用到的每个图标名都能画出；共享表里有的名字取自共享表；未知名字不画任何东西。 |
| [`hooks.ts`](hooks.ts) | `useTabRead`：tab 的读取，挂载时读一次，写入后经 `refreshCustTab` 原地重读；只有最新一次读取生效，第一次读取结算面板的加载状态。`useOptimistic` / `useOptimisticToggle`：绑定某项服务端设置的控件，在 tab 第一次读取落地前（`null`）保持禁用；点击后立即变化，同一时间只允许一次写入，写入失败时回到服务端确认过的值；调用方没有提供失败处理时会说明失败（`toast.failed`）。 |
| [`index.ts`](index.ts) | 再导出 `Customize`。 |
| [`ui.tsx`](ui.tsx) | 共用的 `Hdr` / `CustRow` / `Seg` / `Toggle`（`role="switch"` 并带 `aria-checked`）/ `Pill`。 |
| [`use-timer-lease.ts`](use-timer-lease.ts) | 绑 unmount 的 `useTimerLease` / `useAlive`。 |
| [`ExperimentsTab.tsx`](ExperimentsTab.tsx) | 「实验功能」页的语义判断区块：披露确认、能力开关、TypeSafe key、连接测试。 |
| [`ExperimentsTab.test.tsx`](ExperimentsTab.test.tsx) | 未确认披露不能打开；`env_off` 置灰开关；key 不回显；连接测试覆盖 ok / unavailable / disabled。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`vendors/`](vendors/) | 火山 / DataPro / 豆包卡，与 tab 外壳隔离。 |
