# Bundled MCP servers

[中文](./README_zh.md)

**Status: Implemented example, not a production connector catalogue.** This package contains a small pure-stdlib stdio server used to demonstrate and test OpenAI4S MCP discovery and calls end to end.

## Architectural position

The server runs as an external child process. [`../mcp_client.py`](../mcp_client.py) owns the Host-side connection, while [`../tools/mcp.py`](../tools/mcp.py) exposes connector discovery/read/call operations to the native control plane through normal permission, audit, and untrusted-output policy. The example server is not loaded into scientific kernels.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Documents the bundled-example namespace. |
| [`example_server.py`](./example_server.py) | Implements newline-delimited MCP JSON-RPC over stdin/stdout with initialize, four sample tools, one text resource, and one parameterized prompt. |

## Direct subdirectories

None.

## Scope and extension notes

- This implementation is a fixture/reference server. Real connectors should be separately configured child processes with explicit credentials and permissions.
- Keep stdout reserved for protocol frames; diagnostics belong on stderr.
- Match the protocol version and response shapes expected by [`../mcp_client.py`](../mcp_client.py).
- Server-initiated sampling is outside the current client contract.
