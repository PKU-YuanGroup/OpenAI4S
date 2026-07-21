# Ecosystem adapters

[中文说明](README_zh.md)

This package is the extension boundary: optional integrations that attach an external ecosystem to runtime contracts OpenAI4S already has. Importing this package adds no third-party dependency to the standard-library core.

## Where this fits

An adapter sits at the edge of the system and owns neither loop. It may drive an outer-loop or kernel interface that already exists, but it must not reimplement orchestration, route around Host policy, or quietly widen what counts as completion. Third-party imports belong at the point where the adapter is actually launched.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Marks the namespace for optional adapters. It exports nothing eagerly, on purpose. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`jupyter/`](./jupyter/) | The optional Jupyter wire bridge, which drives the Python and R worker managers that already exist, plus pure-stdlib KernelSpec generation that writes the `kernel.json` files launching it. |

## Extension contract

- Reuse a core port or manager. Do not stand up a second execution engine.
- Keep third-party imports lazy, so a plain `openai4s` import stays stdlib-only.
- Say plainly what an integration does not support. An adapter is not automatically equivalent to the Web workbench.
