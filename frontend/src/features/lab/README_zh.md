# Lab

[English](README.md)

仅仿真的实验台；展示状态均由 REST 确认。不渲染评价或 provider 原始动作。

## 文件

| 文件 | 职责 |
| --- | --- |
| `api.ts` | 类型化 REST 客户端及 fetch 测试缝。 |
| `api.test.ts` | REST 路径、请求和 HTTP 结果测试。 |
| `types.ts` | Lab 公开契约类型。 |
| `state.ts` | 会话与运行守卫、确认状态和重试请求。 |
| `state.test.ts` | 竞态、重试身份和状态转换测试。 |
| `fixtures.ts` | 离线测试用传感器观测样例。 |
| `view.tsx` | 仿真实验台、观测和清单驱动控件。 |
| `view.test.tsx` | 控件、未知值和公开摘要的渲染测试。 |
| `copy.ts` | 功能本地中英文文案。 |
| `lab.css` | 实验台布局与传感器图样式。 |
| `boot.ts` | 页签挂载、会话生命周期和 WebSocket 刷新。 |
| `boot.test.ts` | WebSocket 合并、重连和重放缺口测试。 |
| `summary.ts` | 白名单命令与权限摘要。 |
