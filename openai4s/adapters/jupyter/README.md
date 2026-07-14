# Optional Jupyter adapter

[中文说明](README_zh.md)

This adapter is an optional bridge. It exports and installs Python/R KernelSpecs, and when Jupyter launches one, it adapts Jupyter's messages onto the existing OpenAI4S kernel managers. The bridge stands alone on purpose. It does not attach to a Web session, and it exposes no Host RPC, no Gateway Artifact capture, no Action Ledger history, and no Engine completion semantics.

## Where this fits

The bridge is a host-side adapter around the inner-loop workers in [`../../kernel/`](../../kernel/). Scientific code still travels through `Kernel` and the hardened JSON-per-line worker protocol. The Jupyter frontend is not an alternative OpenAI4S outer agent loop: there are no provider-native tool batches or `finalize_response` actions here.

`kernelspec.py` remains standard-library-only. `bridge.py` imports `ipykernel`/ZeroMQ only when a Jupyter process actually launches it.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports the KernelSpec helpers: describe, status, write, install. Importing the package pulls in no Jupyter, IPython, or ZeroMQ. |
| [`bridge.py`](./bridge.py) | The `ipykernel` bridge itself, plus the CLI a KernelSpec launches. It spawns an OpenAI4S Python or R runtime and maps Jupyter's execute, interrupt and shutdown messages onto it. An interrupt goes to that exact child worker rather than to the bridge's own process group. |
| [`kernelspec.py`](./kernelspec.py) | Builds the Python and R `kernel.json` documents, resolves the per-user or explicit-prefix destination, and writes each spec directory atomically. An existing destination is an error unless the caller passes `replace`, which rewrites `kernel.json` and never recursively deletes user files. It also reports whether the optional dependency is installed. |

## Boundaries for contributors

- Keep KernelSpec generation importable without Jupyter installed.
- Route execution through the existing kernel manager; do not speak directly to worker file descriptors from frontend code.
- Do not describe this standalone bridge as sharing Web notebook state, Host capabilities, or Artifact provenance unless those integrations are implemented end to end.
