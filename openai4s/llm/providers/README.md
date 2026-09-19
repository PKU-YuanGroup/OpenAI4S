# LLM wire adapters

[中文说明](README_zh.md)

Four wire protocols live here, one module each. A module turns the normalized client request into its provider's HTTP payload, and turns that provider's response or stream events back into the single assistant-message shape the rest of the engine works with.

## Where this fits

Wire adapters are leaves under [`../client.py`](../client.py). Endpoint shapes, headers, stream events and usage fields are theirs to know. Provider registration, configuration precedence, action routing, permission checks and kernel execution are not; those live above this directory.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Maps each wire name (`openai`, `anthropic`, `gemini`, `responses`) to its adapter function. That internal dispatch table is all this module holds. |
| [`anthropic.py`](./anthropic.py) | The Anthropic Messages wire. It lifts system instructions, applies native tools and rebuilds text, tool and usage blocks from streaming events. Unknown blocks retain their wire identity. Structured capacity errors share the transport retry budget; any content block, delta or usage vetoes replay. Only an explicit HTTP 400/422 refusal of `stream` permits one blocking compatibility POST within the same three-send budget. |
| [`gemini.py`](./gemini.py) | Builds a Gemini `generateContent` request, mapping the system instruction, the history and the tool declarations. On the way back it takes the first candidate and pulls text, function calls and usage out of it. |
| [`openai.py`](./openai.py) | The OpenAI-compatible Chat Completions wire. It assembles text, reasoning, tools and usage from SSE. Structured capacity error events preserve the response headers and use the same retry state; semantic output vetoes replay. Only HTTP 400/422 with `streaming_not_supported`, or `unsupported_parameter` / `unknown_parameter` naming `stream`, permits one blocking compatibility POST. A contradictory explicit parameter, uncertain disconnect, empty stream or ordinary error text never triggers compatibility. |
| [`responses.py`](./responses.py) | The OpenAI Responses wire, which is always SSE. It maps input items and tools, assembles text and function-call arguments from the output-item events, and treats a stream that ends before `response.completed` as a failure. |

## Adapter contract

- Use the helpers in [`../messages.py`](../messages.py) and [`../tooling.py`](../tooling.py) instead of inventing a second normalization format.
- Raise [`LLMError`](../models.py) for normalized failures, and keep the provider detail attached but bounded, so a failure can be diagnosed without dumping a whole response body into the log.
- Streaming and non-streaming paths must produce the same normalized semantic result.
- Provider-specific behavior belongs here; reusable HTTP mechanics belong in [`../transport.py`](../transport.py).
