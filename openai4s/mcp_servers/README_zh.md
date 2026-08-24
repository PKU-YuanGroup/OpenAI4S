# 内置 MCP 服务器

[English](README.md)

这里有两个纯标准库 stdio server。`example_server.py` 只是端到端 fixture；
`protein_design/` 是可部署的 MCP backend adapter，其可选科学依赖只会在单独配置的
子环境中运行。

## 在架构中的位置

该服务器作为外部子进程运行，不会加载进科学内核。[`../mcp_client.py`](../mcp_client.py) 负责把它拉起来并持有 Host 侧连接；模型看到的是 [`../tools/mcp.py`](../tools/mcp.py)，它把 connector 的发现、资源读取和工具调用暴露给原生控制平面，走的仍是常规的权限、审计与不可信输出策略。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 声明内置纯标准库 MCP-server namespace，并区分 fixture 与可部署 adapter。 |
| [`example_server.py`](./example_server.py) | 在 stdin/stdout 上讲逐行的 MCP JSON-RPC：`initialize`、四个示例工具（`echo`、`now`、`calc`、`random_int`）、一个文本资源，以及一个带参数的摘要 prompt。`calc` 不用 `eval`，而是自己走一遍受限的 AST。 |
| [`protein_design/`](./protein_design/) | 九个原子 protein-design 工具，带显式 attempt/seed、冻结 backend/checkpoint 证据、终态记录和运行后验证；重模型包仍在 core 外。 |

## 范围与扩展说明

- `example_server.py` 只是 fixture 和参考。protein-design 包实现了真实 MCP server 和
  真实 backend command adapter，但不是开箱即用的模型发行包；operator 仍必须单独冻结
  并准备每个 backend、checkpoint、GPU 和离线边界。
- 这些仓库内 server 的 directory 条目持久化 `@openai4s/python`，而不是特定
  机器上的解释器绝对路径。MCP manager 只在启动 server 时才把该 token 解析为
  当前 daemon 的解释器；运行时同样会解析匹配的旧命令，daemon 启动过程则会把
  含有旧绝对路径的历史记录重写为可移植形式。
- stdout 只跑协议 frame，别的什么都不写；诊断信息一律走 stderr。
- 协议版本和响应结构必须与 [`../mcp_client.py`](../mcp_client.py) 的预期一致，目前两边声明的都是 `2024-11-05`。
- sampling 以及其他由服务器发起的请求，都不在当前的客户端契约之内，这是有意为之。
