# LLM wire adapters

[中文说明](README_zh.md)

Four wire protocols live here, one module each. A module turns the normalized client request into its provider's HTTP payload, and turns that provider's response or stream events back into the single assistant-message shape the rest of the engine works with.

## Where this fits

Wire adapters are leaves under [`../client.py`](../client.py). Endpoint shapes, headers, stream events and usage fields are theirs to know. Provider registration, configuration precedence, action routing, permission checks and kernel execution are not; those live above this directory.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Maps each wire name (`openai`, `anthropic`, `gemini`, `responses`) to its adapter function. That internal dispatch table is all this module holds. |
| [`anthropic.py`](./anthropic.py) | The Anthropic Messages wire. It lifts the system message into the top-level `system` field, applies native tools and tool choice, then reads the returned content blocks back into text, normalized tool calls and usage. Given a delta callback it streams, reassembling the content blocks from the event sequence and falling back to a blocking request only when the stream fails before its first event. Content blocks it does not recognize are carried through unchanged, because `wire_state` is replayed as the next turn's assistant content. |
| [`gemini.py`](./gemini.py) | Builds a Gemini `generateContent` request, mapping the system instruction, the history and the tool declarations. On the way back it takes the first candidate and pulls text, function calls and usage out of it. |
| [`openai.py`](./openai.py) | The OpenAI-compatible Chat Completions wire. It streams token by token when the caller supplies a delta callback. An error event carried inside an HTTP-200 stream is matched against an exact allowlist of provider codes — never message prose — and raised as a typed `TransportError` with an HTTP-equivalent status, so the transport's bounded retry policy covers it, with the attempt's uncommitted state discarded before any replay. The blocking fallback is a compatibility path for endpoints that do not implement SSE, not a second retry budget: it runs only when the stream fails before its first event and the failure is not a typed auth, capacity or connection error; once tokens have gone out, every error is raised instead of retried. |
| [`responses.py`](./responses.py) | The OpenAI Responses wire, which is always SSE. It maps input items and tools, assembles text and function-call arguments from the output-item events, and treats a stream that ends before `response.completed` as a failure. |

## Adapter contract

- Use the helpers in [`../messages.py`](../messages.py) and [`../tooling.py`](../tooling.py) instead of inventing a second normalization format.
- Raise [`LLMError`](../models.py) for normalized failures, and keep the provider detail attached but bounded, so a failure can be diagnosed without dumping a whole response body into the log.
- Streaming and non-streaming paths must produce the same normalized semantic result.
- Provider-specific behavior belongs here; reusable HTTP mechanics belong in [`../transport.py`](../transport.py).
