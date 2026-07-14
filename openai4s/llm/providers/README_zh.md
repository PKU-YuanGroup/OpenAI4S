# LLM wire 适配器

[English](README.md)

四种 wire 协议放在这里，一种一个模块。每个模块把标准化的客户端请求翻译成自己那家供应商的 HTTP payload，再把该供应商的响应或流式事件翻译回引擎其余部分统一使用的 assistant-message 形状。

## 在架构中的位置

wire adapter 是 [`../client.py`](../client.py) 之下的叶子模块。endpoint 形状、header、stream event 和 usage 字段是它们该知道的；provider 注册、配置优先级、动作路由、权限检查和内核执行不是，这些都在本目录之上。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 把每个 wire 名字（`openai`、`anthropic`、`gemini`、`responses`）映射到对应的 adapter 函数。这张内部 dispatch 表就是本模块的全部内容。 |
| [`anthropic.py`](./anthropic.py) | Anthropic Messages 这条 wire，只有非流式一条路径。它把 system 消息提到顶层 `system` 字段，应用原生工具与 tool choice，再把返回的 content block 读成文本、标准化的工具调用和 usage。 |
| [`gemini.py`](./gemini.py) | 构造 Gemini `generateContent` 请求，映射 system 指令、历史消息和工具声明。返回后取第一个 candidate，从中解析出文本、function call 和 usage。 |
| [`openai.py`](./openai.py) | OpenAI-compatible Chat Completions 这条 wire。调用方传了 delta 回调时逐 token 流式输出；流在吐出任何内容之前就失败，会退回阻塞式请求重发一次；已经吐过 token 之后再出错，就直接抛出，不再回退。 |
| [`responses.py`](./responses.py) | OpenAI Responses 这条 wire，始终走 SSE。它负责 input 与工具的映射，从 output item 事件里拼出文本和 function call 参数；流在 `response.completed` 之前结束即视为失败。 |

## 适配器契约

- 复用 [`../messages.py`](../messages.py) 和 [`../tooling.py`](../tooling.py) 里的辅助函数，不要另起一套标准化格式。
- 失败统一抛 [`LLMError`](../models.py)，并带上有界的供应商细节：够定位问题，又不至于把整个响应体倒进日志。
- 流式与非流式两条路径必须给出语义相同的标准化结果。
- 供应商特有的行为写在这里；可复用的 HTTP 机制放到 [`../transport.py`](../transport.py)。
