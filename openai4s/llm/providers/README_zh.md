# LLM wire 适配器

[English](./README.md)

**状态：四种 wire 协议已实现。** 这些模块把标准化客户端契约转换为具体 HTTP payload，并把供应商响应/事件转换回统一的 assistant-message 形状。

## 架构位置

wire adapter 是 [`../client.py`](../client.py) 下方的叶子模块。它们可以了解供应商 endpoint 形状、header、stream event 和 usage 字段，但不负责 provider 注册、配置优先级、动作路由、权限检查或 kernel 执行。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 暴露内部 wire 到 adapter 的 dispatch map。 |
| [`anthropic.py`](./anthropic.py) | 构建非流式 Anthropic Messages 请求，应用原生工具/tool choice，解析 content block，并标准化工具调用与 usage。 |
| [`gemini.py`](./gemini.py) | 构建 Gemini `generateContent` 请求，映射 system/history/tool 声明，并标准化 candidate、function call、文本和 usage。 |
| [`openai.py`](./openai.py) | 实现 OpenAI-compatible Chat Completions，包括在 stream 尚未输出内容就初始化失败时回退到非流式请求。 |
| [`responses.py`](./responses.py) | 实现 OpenAI Responses wire，包括 input/tool 映射、output item 解析以及流式文本/工具调用组装。 |

## 直属子目录

无。

## 适配器契约

- 使用 [`../messages.py`](../messages.py) 和 [`../tooling.py`](../tooling.py) 中的辅助函数，不创建第二种标准化格式。
- 通过 [`LLMError`](../models.py) 报告标准化失败，并保留有界、可用于诊断的供应商细节。
- streaming 与 non-streaming 路径必须产生语义相同的标准化结果。
- provider-specific 行为放在这里；可复用 HTTP 机制放在 [`../transport.py`](../transport.py)。
