"""Sanitized environment construction for scientific kernel workers.

Kernel code is intentionally powerful, but it must not inherit credentials
owned by the daemon.  Build the worker environment from a small, explicit
allowlist instead of copying :data:`os.environ`: LLM/provider keys, cloud
tokens, OAuth material, password/credential variables, agent sockets, and
dynamic-loader injection settings therefore never cross the process boundary.

The resulting mapping is also inherited by subprocesses launched from a cell
(including ``host.bash``), unless the cell explicitly obtains and injects a
credential through a Host broker.  This module is pure stdlib and contains no
protocol or process-lifecycle code.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# Exact host variables that are safe and useful in a non-interactive science
# runtime.  Keep this list deliberately boring: adding a name here is a trust
# decision because every program launched by a kernel will inherit it.
_RUNTIME_ALLOWLIST = frozenset(
    {
        # User/runtime identity and command resolution.
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "PATH",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        # Locale and time-zone configuration.
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_ADDRESS",
        "LC_COLLATE",
        "LC_CTYPE",
        "LC_IDENTIFICATION",
        "LC_MEASUREMENT",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NAME",
        "LC_NUMERIC",
        "LC_PAPER",
        "LC_TELEPHONE",
        "LC_TIME",
        "TZ",
        # Temporary-directory and platform runtime paths.
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        # TLS trust stores.  Proxy variables are intentionally absent because
        # proxy URLs frequently embed credentials.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # Reproducible/headless scientific runtime controls.
        "MPLBACKEND",
        "OMP_NUM_THREADS",
        "OMP_THREAD_LIMIT",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
    }
)

# These OPENAI4S settings are read by code that genuinely runs inside the
# worker.  Provider/model/network credentials and host-only toggles do not
# belong here.  OPENAI4S_KERNEL_MODE and OPENAI4S_WORKSPACE are synthesized
# below and cannot be overridden by the daemon environment.
_TRUSTED_OPENAI4S_ALLOWLIST = frozenset(
    {
        "OPENAI4S_ARTIFACTS_ROOTS",
        "OPENAI4S_DATA_DIR",
        "OPENAI4S_DLOPEN_BLOCK_ROOTS",
        "OPENAI4S_EGRESS",
        "OPENAI4S_GUARDS_OFF",
        "OPENAI4S_PROVENANCE_OFF",
        "OPENAI4S_SAFETY_AUDIT_HOOK",
    }
)

_SECRET_MARKERS = (
    "API_KEY",
    "APIKEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CLIENT_SECRET",
    "SECRET",
    "TOKEN",
    "OAUTH",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "BEARER",
    "COOKIE",
)

_INJECTION_NAMES = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "NODE_OPTIONS",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONSTARTUP",
        "R_ENVIRON",
        "R_ENVIRON_USER",
        "R_PROFILE",
        "R_PROFILE_USER",
        "RUBYOPT",
    }
)


def _forbidden_name(name: str) -> bool:
    """Return whether an environment name can carry a secret or inject code.

    The exact allowlist is the primary boundary.  This deny check is a second
    layer so a future allowlist edit cannot accidentally admit an obviously
    credential-shaped or loader-injection variable.
    """
    upper = str(name).upper()
    if upper.startswith(("DYLD_", "LD_")) or upper in _INJECTION_NAMES:
        return True
    return any(marker in upper for marker in _SECRET_MARKERS)


def _copy_allowed(source: Mapping[str, str], names: frozenset[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in names:
        value = source.get(name)
        if value is None or _forbidden_name(name):
            continue
        env[name] = str(value)
    return env


def build_kernel_environment(
    *,
    source: Mapping[str, str] | None = None,
    mode: str = "repl",
    cwd: str | None = None,
    env_root: str | None = None,
    env_name: str | None = None,
    kernel_generation: str | None = None,
    repo_root: str | None = None,
) -> dict[str, str]:
    """Build the complete environment for a Python or R kernel child.

    ``source`` exists for deterministic tests; production callers leave it
    unset and still receive a newly allocated mapping, never a copy of the full
    host environment.  Selected-environment metadata and all Host-owned values
    are synthesized after filtering, so hostile source values cannot override
    them.
    """
    host_env: Mapping[str, str] = os.environ if source is None else source
    env = _copy_allowed(host_env, _RUNTIME_ALLOWLIST)
    env.update(_copy_allowed(host_env, _TRUSTED_OPENAI4S_ALLOWLIST))

    # A usable command path is required even under a deliberately sparse host
    # environment.  The selected conda prefix wins for all kernel subprocesses.
    path = env.get("PATH") or os.defpath
    if env_root:
        bindir = str(Path(env_root) / ("Scripts" if os.name == "nt" else "bin"))
        path = bindir + os.pathsep + path
        env["CONDA_PREFIX"] = str(env_root)
        env["CONDA_DEFAULT_ENV"] = str(env_name or Path(env_root).name)
        env["CONDA_SHLVL"] = "1"
    elif host_env.get("VIRTUAL_ENV") and not _forbidden_name("VIRTUAL_ENV"):
        # The base kernel may be the daemon's uv/venv interpreter.  This path
        # carries no credential and helps its pip/CLI subprocesses stay bound
        # to that same environment.
        env["VIRTUAL_ENV"] = str(host_env["VIRTUAL_ENV"])
    env["PATH"] = path

    workspace = Path(cwd or os.getcwd()).resolve(strict=False)
    env["PWD"] = str(workspace)
    env["OPENAI4S_WORKSPACE"] = str(workspace)
    env["OPENAI4S_KERNEL_MODE"] = str(mode)
    if kernel_generation:
        # Synthesized by the trusted manager for this exact worker spawn.  It
        # is never inherited from the daemon environment and is used only to
        # bind one-shot host.bash capabilities to the originating process.
        env["OPENAI4S_KERNEL_GENERATION"] = str(kernel_generation)

    # Do not inherit a host PYTHONPATH: arbitrary entries are import-time code
    # injection.  The worker only needs the trusted OpenAI4S source root when a
    # selected conda interpreter differs from the daemon interpreter.
    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve(
        strict=False
    )
    env["PYTHONPATH"] = str(root)
    return env


__all__ = ["build_kernel_environment"]
