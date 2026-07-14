---
title: Optional Jupyter compatibility
description: Standalone Jupyter bridge behavior and the boundaries it does not cross.
outline: deep
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Optional Jupyter compatibility

> Verified against repository revision `a92e736` on 2026-07-14.

OpenAI4S can expose its existing Python/R scientific workers to ordinary
Jupyter clients through an **optional standalone adapter**. The adapter is not
part of the daemon and is not imported by the stdlib core.

```text
Jupyter frontend
      |
      | Jupyter messaging / ZeroMQ (optional ipykernel)
      v
OpenAI4S Jupyter bridge
      |
      | existing hardened JSON-per-line protocol
      v
Python worker or R worker
```

The bridge is deliberately one adapter above `kernel/manager.py`; it does not
change the worker protocol, frame reader, Host-call transaction lock, or R file
descriptor discipline.

## Install and inspect

KernelSpec description/export/install is pure stdlib and works before Jupyter
is installed:

```bash
openai4s jupyter describe
openai4s jupyter describe --json
openai4s jupyter export ./jupyter-kernels
openai4s jupyter install
openai4s jupyter install --prefix "$VIRTUAL_ENV" --replace
```

The installed names are `openai4s-python` and `openai4s-r`. `install` uses the
documented per-user Jupyter data directory unless `--prefix` is supplied;
`export` writes the same standard `kernel.json` directories to an arbitrary
destination. Existing destinations fail closed unless `--replace` is explicit,
and replacement updates only `kernel.json` rather than deleting the directory.

Actual Jupyter wire execution is optional:

```bash
python -m pip install 'ipykernel>=7,<8'
openai4s jupyter install --replace
jupyter kernelspec list
```

The generated `argv` embeds the Python interpreter that installed the spec and
passes Jupyter's `{connection_file}` placeholder to the lazy bridge. Starting a
spec without the optional `ipykernel` dependency fails with an actionable message;
it never makes importing or serving OpenAI4S depend on Jupyter/ZeroMQ.

The R spec still requires a real `Rscript` (`openai4s setup --only r` or a host
R installation). It is a Python-hosted Jupyter wire adapter around the existing
R worker, not IRkernel.

## Implemented bridge surface

- standard KernelSpec metadata; the installed `ipykernel` advertises its actual
  Jupyter protocol version rather than the adapter hard-coding one;
- persistent standalone Python or R namespace for the lifetime of that Jupyter
  kernel process;
- Cell execute replies, live/final stdout, stderr, and structured errors;
- message-mode interrupt forwarded to the exact child worker;
- graceful child shutdown;
- the normal OpenAI4S child-environment allowlist and OS sandbox adapter.

## Important boundaries

This compatibility layer is intentionally smaller than the OpenAI4S Web
runtime:

- A Jupyter kernel owns an **independent namespace**. It cannot attach to or
  share variables with an existing Web/CLI session.
- There is no `HostDispatcher`, so `host.*` RPC (including
  `host.submit_output`) is unavailable. Scientific code and ordinary file I/O
  still run; Host-orchestrated services do not.
- Gateway artifact capture, Action Ledger, provenance registration,
  checkpoints, recovery journal, permissions, and the Web execution queue are
  not projected into this standalone process.
- Rich display/comm widgets, debugger, completion, inspection, history, stdin,
  and arbitrary `user_expressions` are not implemented. The reliable surface
  is Cell execution plus text/error streams.
- KernelSpec installation does not prove the optional wire dependency or an R
  interpreter is available; use `openai4s jupyter describe` and start the
  selected kernel to verify the local environment.

Use the built-in Live Notebook when session sharing, Host RPC, artifacts,
lineage, recovery, permissions, or `host.submit_output` completion semantics
matter. Use the optional bridge for ecosystem compatibility with standalone
Jupyter frontends.
