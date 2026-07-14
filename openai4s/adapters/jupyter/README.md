# Optional Jupyter adapter

[中文](./README_zh.md)

**Status: Implemented as a standalone optional bridge.** It exports and installs Python/R KernelSpecs and, when launched by Jupyter, adapts Jupyter messages to the existing OpenAI4S kernel managers. It is deliberately **not** attached to a Web session and does not expose Host RPC, Gateway Artifact capture, Action Ledger history, or Engine completion semantics.

## Architectural position

The bridge is a host-side adapter around the inner-loop workers in [`../../kernel/`](../../kernel/). Scientific code still travels through `Kernel` and the hardened JSON-per-line worker protocol. The Jupyter frontend is not an alternative OpenAI4S outer agent loop: there are no provider-native tool batches or `finalize_response` actions here.

`kernelspec.py` remains standard-library-only. `bridge.py` imports `ipykernel`/ZeroMQ only when a Jupyter process actually launches it.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Re-exports KernelSpec description, status, writing, and installation helpers without importing Jupyter dependencies. |
| [`bridge.py`](./bridge.py) | Defines the lazy `ipykernel` bridge, maps execution/interrupt/shutdown messages, spawns an OpenAI4S Python or R runtime, and supplies the CLI launched by a KernelSpec. |
| [`kernelspec.py`](./kernelspec.py) | Builds, writes, and atomically installs Python/R KernelSpec directories; resolves user/prefix destinations and reports optional dependency status. |

## Direct subdirectories

None.

## Boundaries for contributors

- Keep KernelSpec generation importable without Jupyter installed.
- Route execution through the existing kernel manager; do not speak directly to worker file descriptors from frontend code.
- Do not describe this standalone bridge as sharing Web notebook state, Host capabilities, or Artifact provenance unless those integrations are implemented end to end.
