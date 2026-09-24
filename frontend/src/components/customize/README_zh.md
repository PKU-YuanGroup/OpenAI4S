# frontend/src/components/customize

[English](README.md)

F-19 Customize 模态。九个 tab 组件、嵌套编辑层，以及 `vendors/` 卡。类名（`#cust`、`.cust-tab`、`.prof-row`、`.cust-row`、`.toggle`）与 E2E 契约一致。Tab unmount 会 dispose 定时器租约。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute、远程 GPU、jobs。Job 轮询只有一条链，1500ms 绑在租约上；新的读取取代旧的。 |
| [`ComputeTab.test.tsx`](ComputeTab.test.tsx) | 提交和取消任务都会重读任务列表，但不会再开第二条轮询链；晚到的旧读取结果被丢弃。 |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector 列表；DataPro 卡隔离在 `vendors/`。 |
| [`Customize.tsx`](Customize.tsx) | `#cust` 外壳、tablist、Esc / 背景关闭。 |
| [`Customize.test.tsx`](Customize.test.tsx) | 在输入框里拖选文字、到 Customize 或嵌套编辑器的遮罩上才松开，弹窗保持打开；在遮罩上按下仍会关闭。 |
| [`GeneralTab.tsx`](GeneralTab.tsx) | 主题、布局、语言、API key 快捷入口。 |
| [`DiagnosticsTab.tsx`](DiagnosticsTab.tsx) | 被动安全姿态、显式检查、脱敏支持包下载。挂在 General 下。 |
| [`DiagnosticsTab.test.tsx`](DiagnosticsTab.test.tsx) | 页面加载只发一次 status GET；检查与下载包要等点击。 |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory 开关 / 添加 / 编辑 / 删除，作用域显式发送。 |
| [`MemoryTab.test.tsx`](MemoryTab.test.tsx) | 写入进行中无论按几次保存，只添加一条记忆。 |
| [`ModelsTab.tsx`](ModelsTab.tsx) | 配置档、本机扫描、probe、capability-receipt badge。没有激活的配置档时，把 `GET /config/llm` 的在用模型（环境变量或已保存设置）显示为当前行。 |
| [`ModelsTab.test.tsx`](ModelsTab.test.tsx) | 只靠 `.env` 配置、没有配置档的安装会显示在用模型，而不是「还没有配置模型」；已有激活配置档时不重复加行；读不到配置也不遮住配置档列表；靠环境变量密钥运行的配置档标成「密钥来自环境变量」，而不是「无密钥」；写入进行中无论按几次添加，本机模型只添加一次。 |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job 输出覆盖层。编辑表单要等第一次读取成功后才能保存。 |
| [`NestedEditor.test.tsx`](NestedEditor.test.tsx) | Skill 或 specialist 读取失败时显示错误和重试并禁止保存，空白字段不会覆盖服务端内容。 |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | 导入审阅在启用前展示 requirements、网络模式与 readiness。 |
| [`NetworkTab.tsx`](NetworkTab.tsx) | 豆包卡、allowlist、Tavily 备份、telemetry drain。 |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | 按作用域的审批规则。规则的决定是乐观更新，服务端拒绝时回到原值。 |
| [`PermissionsTab.test.tsx`](PermissionsTab.test.tsx) | 服务端接受的决定保持显示；被拒绝的决定回到规则原来的值（会触发重新渲染，下拉框随之复位）。 |
| [`SkillsTab.tsx`](SkillsTab.tsx) | 个人 / 项目 / collection Skills。 |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | 自定义 specialist 与内置角色。 |
| [`switches.test.tsx`](switches.test.tsx) | 网络出站与记忆开关在第一次读取落地前保持禁用；写入进行中再次点击不生效；写入失败时回到服务端确认过的值。 |
| [`customize.css`](customize.css) | 车道本地模态样式，直到 F-21 移植 `style.css`。 |
| [`icons.tsx`](icons.tsx) | 本模态用到的 Lucide path。 |
| [`hooks.ts`](hooks.ts) | `useOptimistic` / `useOptimisticToggle`：绑定某项服务端设置的控件，在 tab 第一次读取落地前（`null`）保持禁用；点击后立即变化，同一时间只允许一次写入，写入失败时回到服务端确认过的值。 |
| [`index.ts`](index.ts) | 再导出 `Customize`。 |
| [`ui.tsx`](ui.tsx) | 共用的 `Hdr` / `CustRow` / `Seg` / `Toggle` / `Pill`。 |
| [`use-timer-lease.ts`](use-timer-lease.ts) | 绑 unmount 的 `useTimerLease` / `useAlive`。 |
| [`ExperimentsTab.tsx`](ExperimentsTab.tsx) | General 上的实验性语义判断区块：披露确认、能力开关、TypeSafe key、连接测试。 |
| [`ExperimentsTab.test.tsx`](ExperimentsTab.test.tsx) | 未确认披露不能打开；`env_off` 置灰开关；key 不回显；连接测试覆盖 ok / unavailable / disabled。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`vendors/`](vendors/) | 火山 / DataPro / 豆包卡，与 tab 外壳隔离。 |
