# Lab

[English](README.md)

仅仿真的实验台；展示状态由 REST 确认。历史回放只读，导出链接固定到精确 Artifact 版本。评价导出必须主动选择仿真真值；实验台不渲染 provider 原始动作或真值数值。

## 文件

| 文件 | 职责 |
| --- | --- |
| `api.ts` | 类型化 REST 客户端、操作时限及 fetch 测试缝。 |
| `api.test.ts` | REST 路径、请求和 HTTP 结果测试。 |
| `types.ts` | Lab 公开契约类型。 |
| `state.ts` | 会话与运行守卫、终止请求、精确导出回执及分页只读回放。 |
| `state.test.ts` | 竞态、重试身份和状态转换测试。 |
| `fixtures.ts` | 离线测试用传感器观测样例。 |
| `view.tsx` | 仿真实验台、终止确认、精确 Artifact 链接及历史回放滑块。 |
| `view.test.tsx` | 控件、未知值和公开摘要的渲染测试。 |
| `copy.ts` | 功能本地中英文文案。 |
| `lab.css` | 实验台布局与传感器图样式。 |
| `boot.ts` | 页签挂载、会话生命周期和 WebSocket 刷新。 |
| `boot.test.ts` | WebSocket 合并、重连和重放缺口测试。 |
| `summary.ts` | 白名单命令与权限摘要。 |
