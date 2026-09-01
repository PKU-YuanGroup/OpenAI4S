# frontend/src/components/customize

[English](README.md)

F-19 Customize 模态。九个 tab 组件、嵌套编辑层，以及 `vendors/` 卡。类名（`#cust`、`.cust-tab`、`.prof-row`、`.cust-row`、`.toggle`）与 E2E 契约一致。Tab unmount 会 dispose 定时器租约。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute、远程 GPU、jobs。Job 轮询 1500ms 绑在租约上。 |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector 列表；DataPro 卡隔离在 `vendors/`。 |
| [`Customize.tsx`](Customize.tsx) | `#cust` 外壳、tablist、Esc / 背景关闭。 |
| [`GeneralTab.tsx`](GeneralTab.tsx) | 主题、布局、语言、API key 快捷入口。 |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory 开关 / 添加 / 编辑 / 删除，作用域显式发送。 |
| [`ModelsTab.tsx`](ModelsTab.tsx) | 配置档、本机扫描、probe、capability-receipt badge。 |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job 输出覆盖层。 |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | 导入审阅在启用前展示 requirements、网络模式与 readiness。 |
| [`NetworkTab.tsx`](NetworkTab.tsx) | 豆包卡、allowlist、Tavily 备份、telemetry drain。 |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | 按作用域的审批规则。 |
| [`SkillsTab.tsx`](SkillsTab.tsx) | 个人 / 项目 / collection Skills。 |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | 自定义 specialist 与内置角色。 |
| [`customize.css`](customize.css) | 车道本地模态样式，直到 F-21 移植 `style.css`。 |
| [`icons.tsx`](icons.tsx) | 本模态用到的 Lucide path。 |
| [`index.ts`](index.ts) | 再导出 `Customize`。 |
| [`ui.tsx`](ui.tsx) | 共用的 `Hdr` / `CustRow` / `Seg` / `Toggle` / `Pill`。 |
| [`use-timer-lease.ts`](use-timer-lease.ts) | 绑 unmount 的 `useTimerLease` / `useAlive`。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`vendors/`](vendors/) | 火山 / DataPro / 豆包卡，与 tab 外壳隔离。 |
