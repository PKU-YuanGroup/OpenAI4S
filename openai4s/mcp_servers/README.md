# Bundled MCP servers

[中文说明](README_zh.md)

One small pure-stdlib stdio server lives here, so that MCP discovery and calls have a real server to be demonstrated and tested against end to end. It is an example, not a production connector catalogue.

## Where this fits

The server runs as an external child process, never inside a scientific kernel. [`../mcp_client.py`](../mcp_client.py) spawns it and owns the Host-side connection; [`../tools/mcp.py`](../tools/mcp.py) is what the model sees, exposing connector discovery, resource reads, and tool calls to the native control plane under the usual permission, audit, and untrusted-output policy.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | A docstring, nothing more: this is the bundled-example namespace. |
| [`example_server.py`](./example_server.py) | Speaks newline-delimited MCP JSON-RPC on stdin/stdout: `initialize`, four sample tools (`echo`, `now`, `calc`, `random_int`), one text resource, and one parameterized summarization prompt. `calc` walks a restricted AST instead of calling `eval`. |

## Scope and extension notes

- Treat this as a fixture and a reference, not a starting point for production. A real connector is a separately configured child process with its own explicit credentials and permissions.
- stdout carries protocol frames and nothing else. Diagnostics go to stderr.
- The protocol version and response shapes have to match what [`../mcp_client.py`](../mcp_client.py) expects; both sides currently declare `2024-11-05`.
- Sampling and other server-initiated requests are deliberately outside the current client contract.
