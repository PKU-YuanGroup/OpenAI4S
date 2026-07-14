# 命令行接口

[English](./README.md)

**状态：已实现。** 本包通过一个 `openai4s` 命令提供 daemon 生命周期、本地任务执行、首次模型配置、科学环境配置和可选 Jupyter 适配器。

## 架构位置

CLI 是组合适配器，不是编排引擎。`openai4s run` 从 [`../agent/`](../agent/) 构建本地外层循环，并且只在路由到代码 Cell 后延迟启动持久 kernel。`openai4s serve` 委托给 HTTP/WebSocket server。setup 和 status 命令位于活动 Agent 回合之外。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 将 `main` 暴露为包级 CLI 入口。 |
| [`main.py`](./main.py) | 定义 `serve`、`status`、`stop`、`url`、`run`、`init`、`setup` 以及 Jupyter describe/export/install 操作的参数解析与 handler；管理 daemon 状态文件和 conda 环境创建。 |

## 直属子目录

无。

## 运维契约

- `run` 在进程内执行，并使用与本地 Agent facade 相同的 Engine 动作/完成规则。
- `serve` 应按 `Config` 绑定；安全默认值是 loopback，对外暴露应由可信反向代理或 SSH tunnel 处理。
- 可选 Jupyter import 保持在 Jupyter 命令路径之后。
- CLI 输出和退出码是运维接口；修改它们时同步更新测试和文档。
