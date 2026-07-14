# Provider-neutral LLM layer

[中文说明](README_zh.md)

The client here is pure stdlib and speaks four wires: OpenAI-compatible Chat Completions, OpenAI Responses, Anthropic Messages, and Gemini `generateContent`. Messages, native tool calls, streaming deltas, usage counters, and errors all come back in one normalized shape.

## Where this fits

The outer loop in [`../agent/`](../agent/) reaches models through this package and nothing else. It assembles a provider wire request and hands back normalized reply data. It does not pick the next action, run tools, start kernels, or decide that a task is finished; `AgentEngine` does all of that once the reply is in hand.

Capability metadata records what the OpenAI4S adapter supports today. It says nothing about the full feature set a vendor exposes through its own SDK.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | The package facade, and the reason the module-to-package split did not break anything. It re-exports configuration, capabilities, the registry, `LLMError`, and `chat`. `_post_json` and `_post_sse` stay module globals on purpose: replacing those two names is how the offline tests and other integrations intercept the wire. |
| [`capabilities.py`](./capabilities.py) | What each provider and model is declared to support. A provider baseline, a deployment override, and an exact-model override resolve into one cached record, and `validate_model_request` refuses a request for a feature the model was never declared to have instead of letting the wire fail on it. The same record maps a vendor's usage fields onto canonical token counts, and cost is estimated from those. Overrides are process-local; this module touches no files. |
| [`catalog.py`](./catalog.py) | Model-profile presets, thread-safe and process-local. It knows nothing about wires. |
| [`client.py`](./client.py) | The one provider-neutral entry point. It turns configuration plus the provider spec into a base URL and a model, refuses image parts addressed to a text-only provider, and hands the request to the registered wire adapter. If the resolved model does not declare tool calling, the native declarations are dropped rather than sent — the turn falls back to the Code-as-Action path instead of failing on an unsupported schema. Usage is normalized on the way back. |
| [`messages.py`](./messages.py) | History translation, one function per wire. OpenAI keeps `system` as an ordinary message; Anthropic, Gemini, and Responses want it lifted out, and want consecutive tool results folded into a single turn. Native calls, tool results, and multipart image content all cross here without losing their original arguments. |
| [`models.py`](./models.py) | Defines `LLMError`, the single failure type every transport and provider raises. |
| [`registry.py`](./registry.py) | Which providers exist in this process, and what each one is: wire, base URL, API-key environment variable, default model, capability bindings. Registration is validated (an absolute http(s) `base_url`, no credentials embedded in it) and a built-in provider can be neither replaced nor removed. |
| [`tooling.py`](./tooling.py) | The native-tool contract, kept here so no wire adapter has to import the tool registry. Declarations are canonicalized to one name/description/schema form, then rendered into each wire's tool schema and tool choice. Calls coming back are normalized to a shared shape; arguments that fail to decode are carried as a `parse_error` on the call rather than discarded. |
| [`transport.py`](./transport.py) | The only place in the package that opens a socket: JSON POST and SSE decoding over `urllib`, no provider SDK. HTTP and connection failures surface as `LLMError`. A non-empty stream event that is not valid JSON is raised rather than skipped, because a dropped event could have been a tool call; the offending chunk is truncated in the error text. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`providers/`](./providers/) | The wire adapters themselves: OpenAI-compatible Chat, Responses, Anthropic Messages, and Gemini `generateContent`. |

## Provider extension contract

- Register the provider definition separately from the wire adapter. When the protocol already matches a shipped wire, reuse that wire instead of writing another one.
- Normalize every native call to the shared ID/name/raw-arguments/parsed-arguments/error shape before it reaches the Engine.
- Keep secrets in Host configuration and request headers. Provider keys must never reach a scientific worker environment.
- When an adapter starts advertising new input, tool, vision, streaming, or usage behavior, update capability validation and the offline mocked tests in the same change.
