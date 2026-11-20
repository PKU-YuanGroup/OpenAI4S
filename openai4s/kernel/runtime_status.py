"""Which interpreter each language runs in, described for a person.

Settings → Compute and the session's runtime menu show the Python and the R a
session actually uses: the environment's name and where it comes from, its
version, and — for R — whether the packages people reach for first load.
Nothing said that before: R ran without ``ggplot2`` and the only trace was a
menu row reading "R · current: default".

Never a path. The answer goes to the browser, and an interpreter path carries
the account name. Every field keeps one type on every host (``""`` rather than
``None`` for "unknown"), so the frozen response shape does not follow whether
the capturing machine happens to have R.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

#: R packages whose absence changes what an analysis looks like (plots, data
#: frames) or whether the R kernel starts at all (jsonlite).
R_KEY_PACKAGES = ("ggplot2", "dplyr", "jsonlite")

_R_PROBE = (
    'cat("version=", R.version$major, ".", R.version$minor, "\\n", sep = "");'
    "for (p in c(%s)) "
    'cat("package=", p, "=", '
    'suppressWarnings(requireNamespace(p, quietly = TRUE)), "\\n", sep = "")'
) % ", ".join(f'"{name}"' for name in R_KEY_PACKAGES)

#: Probe answers keyed by interpreter identity. One that found every key
#: package holds until the binary changes; anything less is asked again after
#: a minute, so installing a package shows up without restarting the daemon.
_R_PROBES: dict[tuple[str, int, int], tuple[dict[str, Any], float]] = {}
_R_PROBES_LOCK = threading.Lock()
_R_PROBE_TTL_S = 60.0


def environment_kind(env: object) -> str:
    """``builtin`` | ``managed`` | ``local``.

    The daemon's own interpreter is built in; an environment OpenAI4S set up
    (``openai4s setup`` / ``env apply``) is managed; anything else was found on
    this machine and belongs to its user.
    """
    if getattr(env, "name", "") == "base" and not getattr(env, "is_conda", True):
        return "builtin"
    return "managed" if getattr(env, "builtin", False) else "local"


def python_runtime(env_name: str | None) -> dict[str, Any]:
    """The Python a kernel started now would run in."""
    from openai4s.kernel import environments as envmod

    env = envmod.get_environment(env_name)
    if env is None or env.interpreter is None:
        env = envmod.get_environment(envmod.default_env_name())
    if env is None or env.interpreter is None:
        return {"available": False, "environment": "", "kind": "", "version": ""}
    version = env.python_version() or ""
    return {
        "available": True,
        "environment": env.name,
        "kind": environment_kind(env),
        "version": "" if version == "?" else version,
    }


def _parse_r_probe(stdout: str) -> dict[str, Any]:
    version = ""
    packages: dict[str, bool] = {}
    for line in stdout.splitlines():
        if line.startswith("version="):
            version = line[len("version=") :].strip()
        elif line.startswith("package="):
            name, _, loaded = line[len("package=") :].rpartition("=")
            if name:
                packages[name] = loaded.strip() == "TRUE"
    return {
        "checked": bool(version) and all(name in packages for name in R_KEY_PACKAGES),
        "version": version,
        "packages": packages,
    }


def _probe_r(rscript: str) -> dict[str, Any]:
    """R's version and whether each key package loads (cached)."""
    unknown: dict[str, Any] = {"checked": False, "version": "", "packages": {}}
    try:
        st = os.stat(rscript)
    except OSError:
        return unknown
    key = (rscript, st.st_mtime_ns, st.st_size)
    now = time.monotonic()
    with _R_PROBES_LOCK:
        cached = _R_PROBES.get(key)
    if cached is not None:
        answer, when = cached
        complete = answer["checked"] and all(answer["packages"].values())
        if complete or now - when < _R_PROBE_TTL_S:
            return answer
    try:
        from openai4s.kernel import preinstall

        # Confined like every other probe of an interpreter that is not the
        # daemon's own: loading a package runs that package's code.
        out = preinstall.run_confined_probe(
            [rscript, "--vanilla", "-e", _R_PROBE], timeout=30
        )
        stdout = out.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        answer = _parse_r_probe(stdout or "")
    except Exception:  # noqa: BLE001 - unreadable, or confinement refused it
        answer = unknown
    with _R_PROBES_LOCK:
        _R_PROBES[key] = (answer, now)
    return answer


def r_runtime(env_name: str | None) -> dict[str, Any]:
    """The R an R cell would run in: ``env_name`` if the session chose one,
    otherwise what the default resolution lands on."""
    from openai4s.kernel import environments as envmod
    from openai4s.kernel.r_kernel import resolve_r_interpreter

    selected = envmod.get_environment(env_name) if env_name else None
    if selected is not None and not selected.rscript:
        selected = None
    row: dict[str, Any] = {
        "available": False,
        "selected": selected is not None,
        "environment": "",
        "kind": "",
        "version": "",
        "checked": False,
        "packages": [{"name": name, "available": False} for name in R_KEY_PACKAGES],
    }
    rscript = resolve_r_interpreter(selected)
    if not rscript:
        return row
    owner = selected or next(
        (
            env
            for env in envmod.discover_environments()
            if env.rscript and str(env.rscript) == rscript
        ),
        None,
    )
    probe = _probe_r(rscript)
    row.update(
        available=True,
        # An R on PATH belongs to no environment: it is this machine's own.
        environment=owner.name if owner is not None else "",
        kind=environment_kind(owner) if owner is not None else "local",
        version=probe["version"],
        checked=probe["checked"],
        packages=[
            {"name": name, "available": bool(probe["packages"].get(name))}
            for name in R_KEY_PACKAGES
        ],
    )
    return row


def describe_runtimes(python_env: str | None, r_env: str | None) -> dict[str, Any]:
    return {"python": python_runtime(python_env), "r": r_runtime(r_env)}
