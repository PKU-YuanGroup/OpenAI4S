# frontend/src/features/automode

[English](README.md)

会话选项菜单里只读的 Auto Mode 状态区块及其审计视图（issue #217）。它实现了 [`docs/auto-mode.md`](../../../../docs/auto-mode.md) 中的“Workbench status surface”：三行彼此独立的事实（可用性、已保存的选择、运行），部署上限与用量计量，以及审计入口。它只读取 `GET /frames/{id}/auto-mode` 与 `GET /frames/{id}/auto-audits`——不发 PATCH、不调用状态转换、不触发模型调用、审核、修复、恢复或取消。菜单里旧的“自动审核”一行保持原样，仍然只访问 `review-settings`。

状态读取发生在：打开菜单、显式重试、规范事件提示、重连，以及重新打开会话（`_openGen` 变化）。属于其他会话或更早一次打开的响应会被丢弃；同时在途的读取依次按该分支的 `last_event_ordinal`、选择的 `revision`、发出顺序排序；在当前显示结果到达之后才发出的读取总是胜出（回退可能让游标变小）。事件注册表每种类型只允许一个处理器：本模块注册五种规范事件，send 模块的 `candidate_ready` / `auto_run_terminal` 处理器则通过 `autoModeHint` 把事件转交过来。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`types.ts`](types.ts) | 两个路由的封闭词表与净化后的数据形状。 |
| [`sanitize.ts`](sanitize.ts) | 白名单净化器。字段逐个复制；未知 schema 或未知封闭值返回 `null`（显示“状态不可用”）。 |
| [`copy.ts`](copy.ts) | 功能本地中英文案（`autoModeT`），不改动生成的 `i18n/en.ts` / `zh.ts`。 |
| [`present.ts`](present.ts) | 三行文案、预算区块（接近/已到上限、令牌上限未冻结、熔断）与审计行的纯文本。 |
| [`status.ts`](status.ts) | 状态存储：上下文重新键控、读取排序、失败映射。 |
| [`menu.ts`](menu.ts) | 会话选项菜单里显示的区块，打开期间原地重绘。 |
| [`audits.ts`](audits.ts) | 审计弹窗：类型筛选、`before` 游标分页、契约规定的错误码、按提示刷新。 |
| [`hints.ts`](hints.ts) | 把规范事件当作刷新提示，以及重连与重新打开的监听。 |
| [`index.ts`](index.ts) | 对外导出与 `installAutoMode()`（由 `main.tsx` 调用）。 |
| [`automode.css`](automode.css) | 区块与审计视图样式。只用已有 CSS token。 |
| [`testing.ts`](testing.ts) | 仅供测试：最小化的假 DOM、与真实服务输出同形的响应构造器、会记录请求的 `fetch` 桩。应用代码不导入它。 |
| [`sanitize.test.ts`](sanitize.test.ts) | 白名单、封闭词表、可用性一致性、未知用量、`has_more` 必须伴随游标、凭据清洗。 |
| [`present.test.ts`](present.test.ts) | 两种语言下每一行与每种来源的文案、示例场景、计量规则，以及任何地方都不出现 “On” / “已开启”。 |
| [`status.test.ts`](status.test.ts) | 乱序读取、游标不变时的选择刷新、回退、会话与分支变化、失败映射、只发 GET。 |
| [`menu.test.ts`](menu.test.ts) | 真实 `sessionOptionsMenu` 中的区块：位置、“自动审核”一行不变、重试、审计入口、事件提示。 |
| [`audits.test.ts`](audits.test.ts) | 分页、筛选、被取代的响应、脱敏、错误码、刷新与重新打开、只发 GET。 |
| [`hints.test.ts`](hints.test.ts) | 与 send 模块在注册表上的组合、重连与重新打开触发的读取。 |
