"""SDK for BYOC (bring-your-own-compute) providers — the shared hardening and
lifecycle every confined provider process runs.

A provider is a ``provider.py`` that exports ``PROVIDER = <ByocProvider impl>``;
the ``__main__`` entrypoint loads it and runs either the per-op oneshot helper
(``run_oneshot``) or the long-lived repl kernel (``run_repl``).

Secret scrubbing is two-staged so provider code cannot read credential-shaped
or known-prefix environment variables (a name-based heuristic — a secret in an
unrecognized variable name is NOT scrubbed). ``__main__`` calls
``scrub_secret_env()`` (the provider-agnostic
baseline) BEFORE it imports provider.py, so the provider's top-level code runs
with credential-shaped and known-provider-secret env vars already removed. The
resident prologue then re-scrubs with the loaded provider's own declared
``secret_env_prefixes`` before the credential is read (from stdin for oneshot,
fd-3 for repl) — and the credential itself is never placed in the environment.

This package is intentionally split by concern; import from the top level:

    from openai4s_compute_provider import WORK, ByocError, ExecResult

Layout:
  _constants.py  wire limits, exit codes, sandbox paths, error kinds
  _protocol.py   the ByocProvider / ExecResult contract + ByocError
  _channel.py    fd-3 control channel, auth handshake, stdout scrubber
  _resident.py   ByocResident — the prologue + oneshot/repl op loop

Stdlib-only. Provider shims (the only files that import a third-party SDK)
live in ``skills/remote-compute-<id>/provider.py``.

Naming: despite the package name, this is really a *worker runtime* (see
``docs/package-architecture.md``). The alias package
``openai4s_worker_runtime`` re-exports the same public symbols under that more
accurate name; this package stays primary and import-compatible.
"""
from __future__ import annotations

from ._channel import ScrubWriter, read_auth, write_event, write_ready
from ._constants import (
    BASE_ERROR_KINDS,
    BASELINE_SECRET_PREFIXES,
    COMPRESSED_CAP_DEFAULT,
    EXIT_PROTOCOL,
    IDLE_TIMEOUT_S,
    STAGE_PREFIX,
    TAIL_BYTES,
    WORK,
)
from ._protocol import ByocError, ByocProvider, ExecResult
from ._resident import ByocResident, scrub_secret_env

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
