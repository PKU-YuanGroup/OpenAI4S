# LLM wire 适配器

[English](README.md)

四种 wire 协议放在这里，一种一个模块。每个模块把标准化的客户端请求翻译成自己那家供应商的 HTTP payload，再把该供应商的响应或流式事件翻译回引擎其余部分统一使用的 assistant-message 形状。

## 在架构中的位置

wire adapter 是 [`../client.py`](../client.py) 之下的叶子模块。endpoint 形状、header、stream event 和 usage 字段是它们该知道的；provider 注册、配置优先级、动作路由、权限检查和内核执行不是，这些都在本目录之上。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 把每个 wire 名字（`openai`、`anthropic`、`gemini`、`responses`）映射到对应的 adapter 函数。这张内部 dispatch 表就是本模块的全部内容。 |
| [`anthropic.py`](./anthropic.py) | Anthropic Messages wire：提取 system 指令、应用原生工具，并从流事件重建文本、工具与 usage。未知内容块保留原始 wire 身份。结构化容量错误共享 transport 重试预算；任何内容块、delta 或 usage 均阻止透明重发。仅 HTTP 400/422 明确拒绝 `stream` 时，允许在同一最多三次发送预算内执行一次非流式兼容请求。 |
| [`gemini.py`](./gemini.py) | 构造 Gemini `generateContent` 请求，映射 system 指令、历史消息和工具声明。返回后取第一个 candidate，从中解析出文本、function call 和 usage。 |
| [`openai.py`](./openai.py) | OpenAI-compatible Chat Completions wire：从 SSE 拼接文本、reasoning、工具与 usage。结构化容量错误保留响应头并使用共享重试状态；语义输出阻止重发。仅 HTTP 400/422 的 `streaming_not_supported`，或带 `param: stream` 的 `unsupported_parameter` / `unknown_parameter` 允许一次非流式兼容请求。显式参数矛盾、不确定断流、空流和普通错误文案均不触发降级。 |
| [`responses.py`](./responses.py) | OpenAI Responses 这条 wire，始终走 SSE。它负责 input 与工具的映射，从 output item 事件里拼出文本和 function call 参数；流在 `response.completed` 之前结束即视为失败。 |

## 适配器契约

- 复用 [`../messages.py`](../messages.py) 和 [`../tooling.py`](../tooling.py) 里的辅助函数，不要另起一套标准化格式。
- 失败统一抛 [`LLMError`](../models.py)，并带上有界的供应商细节：够定位问题，又不至于把整个响应体倒进日志。
- 流式与非流式两条路径必须给出语义相同的标准化结果。
- 供应商特有的行为写在这里；可复用的 HTTP 机制放到 [`../transport.py`](../transport.py)。
