# frontend/src/features/lab

[English](README.md)

与 Notebook 并列的"实验室"面板：操作员视角下的 OpenMHS 模拟实验室。面板可见时每 2 秒读取 `/api/v1/mhs-lab/*`：按智能体接入面分组的设备卡片（**MHS**：原生 `mhs_*` 工具；**MCP**：连接器行），标明线路、实时状态与急停锁存；一键安装 OpenMHS 市场任意设备的设备市场；缺少 OpenMHS 时的一键准备按钮；启动/停止；由回执输出生成的图表（剂量-反应、反应器优化轨迹、机械臂轨迹）；实时回执流；离线证据链校验；LIMS 样品瓶。急停与操作员复位按钮是人的操作，智能体没有通往它们的路径。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`index.ts`](index.ts) | `mountLab()` 只在第一次把面板渲染进 `#dock-lab`；`labTabLabel()` 给出面板标签名。 |
| [`LabPanel.tsx`](LabPanel.tsx) | 面板本体、双语文案与 SVG 图表（经校验的参考分类色，浅色/深色各一套）。 |
| [`MarketPanel.tsx`](MarketPanel.tsx) | 设备市场：搜索 OpenMHS 市场、按类别筛选，一键以 MCP 或 MHS 方式安装设备。 |
| [`lab.test.ts`](lab.test.ts) | 面板辅助函数的单元测试（最新读数、纯文本介绍、类别名）。 |
| [`types.ts`](types.ts) | `/mhs-lab/*` 响应的数据结构。 |
| [`lab.css`](lab.css) | 基于工作台设计变量的面板样式。 |
