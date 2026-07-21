# LLM wire adapters

[中文说明](README_zh.md)

Four wire protocols live here, one module each. A module turns the normalized client request into its provider's HTTP payload, and turns that provider's response or stream events back into the single assistant-message shape the rest of the engine works with.

## Where this fits

Wire adapters are leaves under [`../client.py`](../client.py). Endpoint shapes, headers, stream events and usage fields are theirs to know. Provider registration, configuration precedence, action routing, permission checks and kernel execution are not; those live above this directory.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Maps each wire name (`openai`, `anthropic`, `gemini`, `responses`) to its adapter function. That internal dispatch table is all this module holds. |
| [`anthropic.py`](./anthropic.py) | The Anthropic Messages wire, non-streaming only. It lifts the system message into the top-level `system` field, applies native tools and tool choice, then reads the returned content blocks back into text, normalized tool calls and usage. |
| [`gemini.py`](./gemini.py) | Builds a Gemini `generateContent` request, mapping the system instruction, the history and the tool declarations. On the way back it takes the first candidate and pulls text, function calls and usage out of it. |
| [`openai.py`](./openai.py) | The OpenAI-compatible Chat Completions wire. It streams token by token when the caller supplies a delta callback, and retries as a blocking request if the stream fails before any content was emitted; once tokens have gone out, the error is raised instead of retried. |
| [`responses.py`](./responses.py) | The OpenAI Responses wire, which is always SSE. It maps input items and tools, assembles text and function-call arguments from the output-item events, and treats a stream that ends before `response.completed` as a failure. |

## Adapter contract

- Use the helpers in [`../messages.py`](../messages.py) and [`../tooling.py`](../tooling.py) instead of inventing a second normalization format.
- Raise [`LLMError`](../models.py) for normalized failures, and keep the provider detail attached but bounded, so a failure can be diagnosed without dumping a whole response body into the log.
- Streaming and non-streaming paths must produce the same normalized semantic result.
- Provider-specific behavior belongs here; reusable HTTP mechanics belong in [`../transport.py`](../transport.py).
