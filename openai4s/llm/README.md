# Provider-neutral LLM layer

[中文](./README_zh.md)

**Status: Implemented for the declared wires.** The package provides a pure-stdlib client for OpenAI-compatible Chat Completions, OpenAI Responses, Anthropic Messages, and Gemini `generateContent`, with normalized messages, native tool calls, streaming deltas, usage, and errors.

## Architectural position

This is the model port used by the outer loop in [`../agent/`](../agent/). It assembles provider wire requests and returns normalized reply data; it does not decide which action runs, execute tools, start kernels, or define completion. `AgentEngine` performs that routing after the reply returns.

Capability metadata describes what the OpenAI4S adapter currently supports. It must not be read as a claim about every feature exposed by a vendor's own SDK.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Backward-compatible package facade exporting configurations, capabilities, registries, normalized errors, and `chat`; preserves transport monkey-patch hooks used by tests/integrations. |
| [`capabilities.py`](./capabilities.py) | Defines provider/model capability records, built-in and override resolution, request validation, token-usage normalization, cost calculation, and cache state. |
| [`catalog.py`](./catalog.py) | Maintains thread-safe, process-local model-profile presets independently of wire implementations. |
| [`client.py`](./client.py) | Validates a model request, guards vision use, resolves a registered provider/wire, dispatches to the wire adapter, and normalizes usage. |
| [`messages.py`](./messages.py) | Converts normalized conversation history—including native tool call/results and multipart content—into OpenAI, Responses, Anthropic, and Gemini wire shapes. |
| [`models.py`](./models.py) | Defines the normalized `LLMError` raised across transports and providers. |
| [`registry.py`](./registry.py) | Validates and manages process-local provider definitions, base URLs, API-key environment names, wires, and capability bindings. |
| [`tooling.py`](./tooling.py) | Canonicalizes provider-neutral tool declarations and calls, validates argument encoding, builds wire-specific schemas/tool choices, and constructs normalized assistant messages. |
| [`transport.py`](./transport.py) | Implements JSON POST and SSE streaming with `urllib`, bounded errors, and no provider SDK dependency. |

## Direct subdirectories

| Directory | Place in the architecture |
| --- | --- |
| [`providers/`](./providers/) | Focused wire adapters for OpenAI-compatible Chat, Responses, Anthropic Messages, and Gemini `generateContent`. |

## Provider extension contract

- Register a provider definition separately from its wire adapter; reuse an existing wire when the protocol is compatible.
- Normalize every native call to the shared ID/name/raw-arguments/parsed-arguments/error shape before returning it to the Engine.
- Keep secrets in Host configuration and headers; never inject provider keys into scientific worker environments.
- Update capability validation and offline mocked tests whenever an adapter starts advertising a new input, tool, vision, streaming, or usage behavior.
