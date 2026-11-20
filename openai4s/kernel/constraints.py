"""What a Cell can and cannot do on this machine, in the words the model reads.

The OS sandbox (``openai4s.security.sandbox``) enforces the limits; this is
the paragraph that tells the model about them before it runs into them. Left
untold, a model that needed a missing R package spent a session running
``install.packages`` into a read-only environment, creating library
directories under ``$HOME``, reading ``~/.condarc`` and scanning local ports
for a proxy -- every attempt refused by the sandbox, none by anything the
model could read.

It reads the configuration the sandbox reads and looks for the sandbox
executable on PATH. It starts no worker, runs no self-test and opens no
socket, so it can be composed into a system prompt on every session start.
The measured posture of the worker that actually runs a Cell can still be
weaker (``auto`` degrades when the self-test fails), which is why the text
says what the sandbox does where it is in force.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

_SANDBOX_ENV = "OPENAI4S_KERNEL_SANDBOX"
_RAW_NETWORK_ENV = "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"
_BACKENDS = {
    "darwin": ("sandbox-exec", "macOS Seatbelt"),
    "linux": ("bwrap", "bubblewrap"),
}


def expected_posture(
    *, platform: str | None = None, which: Any = shutil.which
) -> dict[str, Any]:
    """The sandbox a new Kernel is configured to get: mode, backend, raw network."""

    mode = (os.environ.get(_SANDBOX_ENV) or "auto").strip().lower() or "auto"
    raw = (os.environ.get(_RAW_NETWORK_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    backend = None
    name = platform or sys.platform
    name = "linux" if name.startswith("linux") else name
    executable, label = _BACKENDS.get(name, (None, None))
    if mode in {"auto", "enforce"} and executable and which(executable):
        backend = label
    return {"mode": mode, "backend": backend, "raw_network": raw}


def _home_relative(path: str | os.PathLike[str] | None) -> str | None:
    """A path with the home directory written as ``~``; the model needs which
    interpreter, not whose account it lives in."""

    if not path:
        return None
    text = str(path)
    home = str(Path.home())
    if home and home != "/" and (text == home or text.startswith(home + os.sep)):
        return "~" + text[len(home) :]
    return text


def _interpreters(python_env_name: str | None, r_env_name: str | None) -> list[str]:
    lines: list[str] = []
    try:
        from openai4s.kernel.environments import get_environment
        from openai4s.kernel.r_kernel import resolve_r_interpreter
    except Exception:  # noqa: BLE001 - interpreter discovery is advisory
        return lines
    try:
        python_env = get_environment(python_env_name) if python_env_name else None
        python = (python_env.interpreter if python_env is not None else None) or (
            sys.executable
        )
        label = python_env.name if python_env is not None else "base"
        lines.append(
            f"```python cells run on {_home_relative(python)} (environment "
            f"'{label}')."
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        r_env = get_environment(r_env_name) if r_env_name else None
        rscript = resolve_r_interpreter(r_env)
        on_path = shutil.which("Rscript")
        if rscript:
            line = f"```r cells run on {_home_relative(rscript)}"
            if on_path and Path(on_path).resolve() != Path(rscript).resolve():
                line += (
                    f"; a different R is on PATH at {_home_relative(on_path)} "
                    "(its packages are not the R kernel's)"
                )
            lines.append(line + ".")
        elif on_path:
            lines.append(f"Rscript on PATH: {_home_relative(on_path)}.")
        else:
            lines.append("No R interpreter was found; ```r cells cannot run.")
    except Exception:  # noqa: BLE001
        pass
    return lines


def kernel_constraints_note(
    *,
    python_env_name: str | None = None,
    r_env_name: str | None = None,
    posture: dict[str, Any] | None = None,
) -> str:
    """The "Kernel environment" paragraph of the system prompt."""

    posture = posture if posture is not None else expected_posture()
    host_tools = (
        "web_search, web_fetch, web_download and science_search "
        "(host.web_search / host.web_fetch / host.web_download / host.science "
        "in a python cell)"
    )
    sandboxed = posture["backend"] is not None and posture["mode"] != "off"
    if sandboxed and not posture["raw_network"]:
        network = (
            f"- Network: Cells and host.bash run inside the OS sandbox "
            f"({posture['backend']}) with direct network blocked. requests, "
            "urllib, curl, wget, git clone, pip install and install.packages() "
            "from the internet fail with a connection error; that is policy, "
            "not an outage, so do not retry them or look for a proxy. Reach "
            f"the network only through the Host tools: {host_tools}."
        )
    else:
        reason = (
            "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK is set"
            if posture["raw_network"]
            else "this deployment runs Cells without an OS sandbox"
        )
        network = (
            f"- Network: direct network from Cells may work here ({reason}), "
            "but use the Host tools for anything external -- they are "
            f"audited and visible to the user: {host_tools}."
        )
    packages = (
        "- Packages: do not install into an existing environment (pip, conda "
        "or mamba install, install.packages(), an R library directory under "
        "$HOME). "
        + ("The kernel cannot write there, and " if sandboxed else "")
        + "other sessions run in the same environments. If a package is "
        'missing, call host.env.list(["pkg"]): switch to the built-in '
        'environment it recommends with host.env.use("<name>"), or request the '
        "install with env_setup / host.env.create (the user approves each "
        "install). An environment listed with builtin=false is the user's own: "
        "switch into it only if the user named it."
    )
    files = (
        "- Files: Cells can write only the session workspace (the current "
        "directory) and a private temporary directory; $HOME, the "
        "environments and every other path are read-only."
        if sandboxed
        else "- Files: keep data and outputs in the session workspace (the "
        "current directory); do not write elsewhere on this machine."
    )
    lines = [
        "Kernel environment (the limits Cells and host.bash run under here):",
        network,
        packages,
        files,
    ]
    interpreters = _interpreters(python_env_name, r_env_name)
    if interpreters:
        lines.append("- Interpreters: " + " ".join(interpreters))
    lines.append(
        "- If one of these limits blocks the task, say so and ask the user. Do "
        "not probe the machine for a way around it (reading ~/.condarc or "
        "pip.conf, scanning local ports, `which -a`, `conda info`)."
    )
    return "\n".join(lines)


__all__ = ["expected_posture", "kernel_constraints_note"]
