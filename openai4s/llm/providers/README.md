# LLM wire adapters

[中文](./README_zh.md)

**Status: Implemented for four wire protocols.** These modules translate the normalized client contract into concrete HTTP payloads and translate provider responses/events back into one shared assistant-message shape.

## Architectural position

Wire adapters are leaves below [`../client.py`](../client.py). They may know provider endpoint shapes, headers, stream events, and usage fields, but they do not own provider registration, configuration precedence, action routing, permission checks, or kernel execution.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Exposes the internal wire-to-adapter dispatch map. |
| [`anthropic.py`](./anthropic.py) | Builds non-streaming Anthropic Messages requests, applies native tools/tool choice, parses content blocks, and normalizes tool calls and usage. |
| [`gemini.py`](./gemini.py) | Builds Gemini `generateContent` requests, maps system/history/tool declarations, and normalizes candidates, function calls, text, and usage. |
| [`openai.py`](./openai.py) | Implements OpenAI-compatible Chat Completions, including streaming with a non-stream fallback when stream setup fails before emitting content. |
| [`responses.py`](./responses.py) | Implements the OpenAI Responses wire, including input/tool mapping, output-item parsing, and streamed text/tool-call assembly. |

## Direct subdirectories

None.

## Adapter contract

- Use helpers in [`../messages.py`](../messages.py) and [`../tooling.py`](../tooling.py) rather than creating a second normalization format.
- Raise [`LLMError`](../models.py) for normalized failures and preserve bounded provider details useful for diagnosis.
- Streaming and non-streaming paths must produce the same normalized semantic result.
- Provider-specific behavior belongs here; reusable HTTP mechanics belong in [`../transport.py`](../transport.py).
