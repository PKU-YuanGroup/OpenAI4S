"""``openai4s_worker_runtime`` — the accurately-named alias for the worker
runtime that ships as :mod:`openai4s_compute_provider`.

The legacy package name says "compute provider", but what it actually contains
is a stdlib-only *worker runtime*: the ``ByocProvider``/``ExecResult`` contract,
the fd-3 control channel + auth handshake + stdout scrubber, the resident
process lifecycle (prologue, oneshot, repl), and import-time secret scrubbing.
See ``docs/package-architecture.md`` (Worker Runtime section) and the Option-4
decision in ``docs/refactor-plan.md`` section E.

This module is a pure re-export alias — it defines nothing of its own and
changes no behavior:

- ``openai4s_compute_provider`` remains the **primary** package; its private
  submodules (``_protocol.py``, ``_resident.py``, ``_channel.py``,
  ``_constants.py``) stay where they are.
- Every public symbol is the *same object* under both names, so
  ``isinstance``/identity checks are interchangeable across the two imports.
- The runnable confined-process entrypoint remains
  ``python -m openai4s_compute_provider`` (this alias has no ``__main__``).

New code may prefer::

    from openai4s_worker_runtime import WORK, ByocError, ExecResult

while every existing ``from openai4s_compute_provider import ...`` keeps
working unchanged.

Stdlib-only, like the package it aliases.
"""
from __future__ import annotations

from openai4s_compute_provider import (
    BASE_ERROR_KINDS,
    BASELINE_SECRET_PREFIXES,
    COMPRESSED_CAP_DEFAULT,
    EXIT_PROTOCOL,
    IDLE_TIMEOUT_S,
    STAGE_PREFIX,
    TAIL_BYTES,
    WORK,
    ByocError,
    ByocProvider,
    ByocResident,
    ExecResult,
    ScrubWriter,
    read_auth,
    scrub_secret_env,
    write_event,
    write_ready,
)

__all__ = [
    "ByocError",
    "ByocProvider",
    "ByocResident",
    "ExecResult",
    "ScrubWriter",
    "scrub_secret_env",
    "read_auth",
    "write_event",
    "write_ready",
    "BASE_ERROR_KINDS",
    "BASELINE_SECRET_PREFIXES",
    "COMPRESSED_CAP_DEFAULT",
    "EXIT_PROTOCOL",
    "IDLE_TIMEOUT_S",
    "STAGE_PREFIX",
    "TAIL_BYTES",
    "WORK",
]
