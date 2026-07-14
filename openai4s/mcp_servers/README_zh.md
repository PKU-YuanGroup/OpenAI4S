# 内置 MCP 服务器

[English](./README.md)

**状态：示例已实现，但不是生产 connector catalog。** 本包包含一个小型纯标准库 stdio 服务器，用于端到端演示和测试 OpenAI4S MCP 发现与调用。

## 架构位置

该服务器作为外部子进程运行。[`../mcp_client.py`](../mcp_client.py) 负责 Host 侧连接，[`../tools/mcp.py`](../tools/mcp.py) 则通过常规权限、审计和不可信输出策略，向原生控制平面暴露 connector 发现/读取/调用操作。示例服务器不会加载到科学 kernel 中。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 说明内置示例命名空间。 |
| [`example_server.py`](./example_server.py) | 通过 stdin/stdout 实现逐行 MCP JSON-RPC，包含 initialize、四个示例工具、一个文本资源和一个参数化 prompt。 |

## 直属子目录

无。

## 范围与扩展说明

- 该实现是 fixture/reference server。真实 connector 应是独立配置、具有明确 credential 和权限的子进程。
- stdout 只用于协议 frame；诊断信息写入 stderr。
- 保持协议版本和响应形状与 [`../mcp_client.py`](../mcp_client.py) 的预期一致。
- 服务器发起的 sampling 不在当前客户端契约内。
