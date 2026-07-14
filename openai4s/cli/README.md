# Command-line interface

[中文](./README_zh.md)

**Status: Implemented.** This package exposes daemon lifecycle, local task execution, first-run model setup, scientific environment setup, and the optional Jupyter adapter from one `openai4s` command.

## Architectural position

The CLI is a composition adapter, not an orchestration engine. `openai4s run` builds the local outer loop from [`../agent/`](../agent/) and starts persistent kernels lazily only when a code cell is routed. `openai4s serve` delegates to the HTTP/WebSocket server. Setup and status commands operate outside active agent turns.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Exposes `main` as the package-level CLI entry point. |
| [`main.py`](./main.py) | Defines argument parsing and handlers for `serve`, `status`, `stop`, `url`, `run`, `init`, `setup`, and Jupyter describe/export/install operations; manages daemon state files and conda-environment creation. |

## Direct subdirectories

None.

## Operational contract

- `run` is in-process and uses the same Engine action/completion rules as the local Agent facade.
- `serve` should remain bound according to `Config`; the secure default is loopback, with external exposure handled by a trusted reverse proxy or SSH tunnel.
- Optional Jupyter imports stay behind the Jupyter command path.
- CLI output and exit codes are operator interfaces; update tests and documentation when changing them.
