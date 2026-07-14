# 供应商中立 LLM 层

[English](./README.md)

**状态：所声明的 wire 已实现。** 本包提供纯标准库客户端，支持 OpenAI-compatible Chat Completions、OpenAI Responses、Anthropic Messages 和 Gemini `generateContent`，并标准化消息、原生工具调用、流式 delta、usage 和错误。

## 架构位置

这是 [`../agent/`](../agent/) 外层循环使用的 model port。它组装供应商 wire 请求并返回标准化 reply 数据；它不决定执行哪种动作、不执行工具、不启动 kernel，也不定义完成。回复返回后由 `AgentEngine` 完成动作路由。

能力元数据描述 OpenAI4S adapter 当前支持的内容，不能解释为供应商自身 SDK 所有能力的声明。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 向后兼容的包 facade，导出配置、能力、registry、标准化错误和 `chat`；保留测试/集成使用的 transport monkey-patch hook。 |
| [`capabilities.py`](./capabilities.py) | 定义供应商/模型能力记录、内置与 override 解析、请求校验、token usage 标准化、成本计算和 cache 状态。 |
| [`catalog.py`](./catalog.py) | 独立于 wire 实现，维护线程安全、进程内的模型 profile preset。 |
| [`client.py`](./client.py) | 校验模型请求、保护 vision 使用、解析已注册 provider/wire、分发到 wire adapter，并标准化 usage。 |
| [`messages.py`](./messages.py) | 将标准化会话历史（包括原生工具调用/结果和 multipart 内容）转换为 OpenAI、Responses、Anthropic 和 Gemini wire 形状。 |
| [`models.py`](./models.py) | 定义 transport 和 provider 共用的标准化 `LLMError`。 |
| [`registry.py`](./registry.py) | 校验并管理进程内 provider 定义、base URL、API key 环境变量名、wire 和能力绑定。 |
| [`tooling.py`](./tooling.py) | 规范化供应商中立工具声明和调用、校验参数编码、构建 wire-specific schema/tool choice，并构造标准化 assistant message。 |
| [`transport.py`](./transport.py) | 使用 `urllib` 实现 JSON POST 和 SSE streaming，提供有界错误且不依赖供应商 SDK。 |

## 直属子目录

| 目录 | 在架构中的位置 |
| --- | --- |
| [`providers/`](./providers/) | 面向 OpenAI-compatible Chat、Responses、Anthropic Messages 和 Gemini `generateContent` 的聚焦 wire adapter。 |

## 供应商扩展契约

- 将 provider 定义与 wire adapter 分开注册；协议兼容时复用现有 wire。
- 返回 Engine 之前，把每个原生调用标准化为共用的 ID/name/raw-arguments/parsed-arguments/error 形状。
- secret 保留在 Host 配置和 header 中；不得将 provider key 注入科学 Worker 环境。
- 当 adapter 开始声明新的输入、工具、vision、streaming 或 usage 行为时，更新能力校验和离线 mocked 测试。
