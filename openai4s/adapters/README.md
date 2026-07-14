# Ecosystem adapters

[中文](./README_zh.md)

**Status: Implemented extension boundary.** This package contains optional integrations that adapt external ecosystems to existing OpenAI4S runtime contracts. Importing the package does not add third-party dependencies to the standard-library core.

## Architectural position

Adapters sit at ecosystem boundaries and do not own either loop. They may drive an existing outer-loop or kernel interface, but they must not duplicate orchestration, bypass Host policy, or silently broaden completion guarantees. Optional imports belong at the point where an adapter is actually launched.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Marks the optional-adapter namespace and deliberately exports no eager integrations. |

## Direct subdirectories

| Directory | Place in the architecture |
| --- | --- |
| [`jupyter/`](./jupyter/) | Optional Jupyter wire bridge and pure-stdlib KernelSpec generation around existing Python/R worker managers. |

## Extension contract

- Reuse a core port or manager instead of introducing a second execution engine.
- Keep third-party imports lazy so normal `openai4s` imports remain stdlib-only.
- State unsupported integration semantics explicitly; an adapter is not automatically equivalent to the Web workbench.
