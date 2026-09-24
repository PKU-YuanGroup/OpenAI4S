# frontend/src/components/customize/vendors

[English](README.md)

从九个 Customize tab 里隔离出来的 vendor 卡。DataPro 在 Connectors，豆包在 Network，火山在 Models。Key 轮询绑在该 tab 的定时器租约上。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`datapro.tsx`](datapro.tsx) | DataPro 凭证 + 检索卡（`volcengine-datapro`）。 |
| [`doubao.tsx`](doubao.tsx) | 豆包搜索卡。专用 source，不回退 Tavily。 |
| [`use-vendor-key.ts`](use-vendor-key.ts) | `useVendorKey`：DataPro 与豆包卡的 Agent Plan key 状态，每次渲染都从 `config` prop 推导（读取完成前为 `null`，此前不能保存）；保存的应答只对它所对应的那次配置读取有效。 |
| [`vendors.test.tsx`](vendors.test.tsx) | 卡片在配置读取返回前就已渲染，读取落地后显示读取到的 key、连接器与 Skill 状态；返回前不能切换也不能保存。 |
| [`volcengine.tsx`](volcengine.tsx) | 火山 SSO / 套餐 / key 轮询面板：`VolcenginePanel` 持有状态并选择步骤组件（`NotInstalled`、`Connecting`、`AwaitingCode`、`FailedLogin`、`SignedOut`、`Connected`）；`accessActions` 给出已登录账号下一步能做什么。await 之后写入的状态都基于写入时的最新状态更新，不再展开更早的副本。同一时间只发起一次登录；为登录打开的标签页在没有拿到授权地址时会被关闭。 |
| [`volcengine.test.tsx`](volcengine.test.tsx) | key 等待超时后，面板保留最后一次复查读到的状态，而不是等待开始时的旧状态。连按两次「连接」只打开一个登录标签页；守护进程没有返回授权地址时关闭该页，不留空白页。按每种连接与授权状态核对显示的按钮、提示、选择器与标记，以及主按钮发出的请求。 |
