# Bundled MCP servers

[中文说明](README_zh.md)

Two pure-stdlib stdio servers live here. `example_server.py` is only an
end-to-end fixture. `protein_design/` is a deployable MCP backend adapter whose
optional scientific dependencies run only in separately configured child
environments.

## Where this fits

The server runs as an external child process, never inside a scientific kernel. [`../mcp_client.py`](../mcp_client.py) spawns it and owns the Host-side connection; [`../tools/mcp.py`](../tools/mcp.py) is what the model sees, exposing connector discovery, resource reads, and tool calls to the native control plane under the usual permission, audit, and untrusted-output policy.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Declares the bundled pure-stdlib MCP-server namespace and distinguishes the fixture from the deployable adapter. |
| [`example_server.py`](./example_server.py) | Speaks newline-delimited MCP JSON-RPC on stdin/stdout: `initialize`, four sample tools (`echo`, `now`, `calc`, `random_int`), one text resource, and one parameterized summarization prompt. `calc` walks a restricted AST instead of calling `eval`. |
| [`protein_design/`](./protein_design/) | Nine atomic protein-design tools with explicit attempts, seeds, pinned backend/checkpoint evidence, terminal records and post-run validation; heavy model packages remain outside core. |

## Scope and extension notes

- Treat `example_server.py` as a fixture and reference. The protein-design
  package implements a real MCP server and real backend command adapters, but
  it is not a turnkey model distribution: the operator must separately pin and
  provision every backend, checkpoint, GPU and offline boundary.
- Directory entries for these in-tree servers persist `@openai4s/python`
  instead of a machine-specific interpreter path. The MCP manager resolves the
  token to the current daemon's interpreter only when it spawns the server;
  it also resolves matching legacy commands at runtime, while daemon startup
  rewrites those old absolute-path rows into the portable form.
- stdout carries protocol frames and nothing else. Diagnostics go to stderr.
- The protocol version and response shapes have to match what [`../mcp_client.py`](../mcp_client.py) expects; both sides currently declare `2024-11-05`.
- Sampling and other server-initiated requests are deliberately outside the current client contract.
