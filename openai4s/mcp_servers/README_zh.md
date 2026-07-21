# 内置 MCP 服务器

[English](README.md)

这里只有一个小型的纯标准库 stdio 服务器，为的是让 MCP 的发现与调用有一个真实服务器可以端到端地演示和测试。它是一个示例，不是生产 connector catalog。

## 在架构中的位置

该服务器作为外部子进程运行，不会加载进科学内核。[`../mcp_client.py`](../mcp_client.py) 负责把它拉起来并持有 Host 侧连接；模型看到的是 [`../tools/mcp.py`](../tools/mcp.py)，它把 connector 的发现、资源读取和工具调用暴露给原生控制平面，走的仍是常规的权限、审计与不可信输出策略。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 只有一段 docstring：说明这是内置示例的命名空间。 |
| [`example_server.py`](./example_server.py) | 在 stdin/stdout 上讲逐行的 MCP JSON-RPC：`initialize`、四个示例工具（`echo`、`now`、`calc`、`random_int`）、一个文本资源，以及一个带参数的摘要 prompt。`calc` 不用 `eval`，而是自己走一遍受限的 AST。 |

## 范围与扩展说明

- 把它当成 fixture 和参考实现，不要拿它当生产代码的起点。真实的 connector 应该是单独配置的子进程，凭据和权限都要显式给出。
- stdout 只跑协议 frame，别的什么都不写；诊断信息一律走 stderr。
- 协议版本和响应结构必须与 [`../mcp_client.py`](../mcp_client.py) 的预期一致，目前两边声明的都是 `2024-11-05`。
- sampling 以及其他由服务器发起的请求，都不在当前的客户端契约之内，这是有意为之。
