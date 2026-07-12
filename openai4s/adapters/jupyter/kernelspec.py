"""Pure-stdlib KernelSpec description, export, and installation.

The generated specs launch the optional Jupyter wire bridge.  They do not
replace or tunnel OpenAI4S's hardened JSON-per-line worker protocol: the bridge
is merely another host-side adapter around the existing ``Kernel`` manager.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Iterable, Mapping

KERNEL_NAMES = {
    "python": "openai4s-python",
    "r": "openai4s-r",
}

_DISPLAY_NAMES = {
    "python": "OpenAI4S Python (standalone bridge)",
    "r": "OpenAI4S R (standalone bridge)",
}


class KernelSpecError(RuntimeError):
    """Raised when a KernelSpec export/install would be ambiguous or unsafe."""


def _language(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in KERNEL_NAMES:
        raise ValueError("language must be python or r")
    return normalized


def selected_languages(value: str | Iterable[str] = "all") -> tuple[str, ...]:
    """Normalize a CLI/API selection into deterministic language order."""

    if isinstance(value, str):
        if value.strip().lower() == "all":
            return ("python", "r")
        return (_language(value),)
    selected = {_language(item) for item in value}
    return tuple(language for language in ("python", "r") if language in selected)


def kernel_spec(
    language: str,
    *,
    python_executable: str | os.PathLike[str] | None = None,
) -> dict:
    """Return one valid Jupyter ``kernel.json`` document.

    The absolute Python path is intentional: that interpreter owns the optional
    ``ipykernel`` dependency and the installed OpenAI4S adapter.  The scientific code
    itself still runs in an OpenAI4S worker subprocess selected by the bridge.
    """

    language = _language(language)
    # Keep a virtualenv/uv shim intact. Resolving the symlink can jump to the
    # base interpreter, where neither OpenAI4S nor the optional dependency is
    # necessarily installed.
    executable = os.path.abspath(
        os.fspath(Path(python_executable or sys.executable).expanduser())
    )
    return {
        "argv": [
            executable,
            "-m",
            "openai4s.adapters.jupyter.bridge",
            "--language",
            language,
            "-f",
            "{connection_file}",
        ],
        "display_name": _DISPLAY_NAMES[language],
        "language": "python" if language == "python" else "R",
        # The bridge owns a child worker, so an interrupt must be forwarded to
        # that exact process instead of merely signalling the bridge process.
        "interrupt_mode": "message",
        "metadata": {
            "openai4s": {
                "adapter_version": 1,
                "execution_scope": "standalone",
                "host_rpc": False,
                "internal_protocol": "hardened-jsonl",
                "language": language,
            }
        },
    }


def default_user_kernels_dir(
    *,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
    platform: str | None = None,
) -> Path:
    """Return Jupyter's documented per-user ``kernels`` directory.

    This duplicates only the stable platform path rules, avoiding a dependency
    on ``jupyter_core`` for a write that is just a KernelSpec directory.
    """

    env = os.environ if environ is None else environ
    if env.get("JUPYTER_DATA_DIR"):
        return Path(env["JUPYTER_DATA_DIR"]).expanduser() / "kernels"
    platform = platform or sys.platform
    home_path = Path(home).expanduser() if home is not None else Path.home()
    if platform == "darwin":
        return home_path / "Library" / "Jupyter" / "kernels"
    if platform.startswith("win"):
        appdata = env.get("APPDATA")
        return (
            Path(appdata).expanduser() / "jupyter" / "kernels"
            if appdata
            else home_path / "AppData" / "Roaming" / "jupyter" / "kernels"
        )
    data_home = env.get("XDG_DATA_HOME")
    return (
        Path(data_home).expanduser() / "jupyter" / "kernels"
        if data_home
        else home_path / ".local" / "share" / "jupyter" / "kernels"
    )


def prefix_kernels_dir(prefix: str | os.PathLike[str]) -> Path:
    return Path(prefix).expanduser() / "share" / "jupyter" / "kernels"


def write_kernelspecs(
    destination: str | os.PathLike[str],
    *,
    languages: str | Iterable[str] = "all",
    replace: bool = False,
    python_executable: str | os.PathLike[str] | None = None,
) -> list[dict]:
    """Write standard KernelSpec directories below ``destination``.

    Existing directories fail closed unless ``replace`` is explicit.  Replace
    updates only ``kernel.json`` and never recursively deletes user files.
    """

    root = Path(destination).expanduser()
    selected = selected_languages(languages)
    targets = [(language, root / KERNEL_NAMES[language]) for language in selected]
    for _language_name, target in targets:
        if target.is_symlink():
            raise KernelSpecError(
                f"KernelSpec destination must not be a symlink: {target}"
            )
        if target.exists() and not replace:
            raise KernelSpecError(
                f"KernelSpec destination already exists: {target}; use replace=True"
            )
        if target.exists() and not target.is_dir():
            raise KernelSpecError(
                f"KernelSpec destination is not a directory: {target}"
            )

    written: list[dict] = []
    for language, target in targets:
        target.mkdir(parents=True, exist_ok=True)
        document = kernel_spec(language, python_executable=python_executable)
        data = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        final = target / "kernel.json"
        temporary = target / f".kernel.json.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(data, encoding="utf-8")
            # KernelSpecs contain no secret and may live in a shared prefix.
            # Let the installer's umask choose the normal readable mode rather
            # than forcing 0600, which would make a root-installed prefix
            # unusable by the Jupyter users it was installed for.
            os.replace(temporary, final)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        written.append(
            {
                "language": language,
                "name": KERNEL_NAMES[language],
                "path": str(target),
                "kernel_json": str(final),
            }
        )
    return written


def install_kernelspecs(
    *,
    prefix: str | os.PathLike[str] | None = None,
    languages: str | Iterable[str] = "all",
    replace: bool = False,
    python_executable: str | os.PathLike[str] | None = None,
) -> list[dict]:
    """Install specs into a user or explicit-prefix Jupyter data directory."""

    destination = (
        prefix_kernels_dir(prefix) if prefix is not None else default_user_kernels_dir()
    )
    return write_kernelspecs(
        destination,
        languages=languages,
        replace=replace,
        python_executable=python_executable,
    )


def adapter_status() -> dict:
    """Describe availability without importing optional Jupyter packages."""

    try:
        available = importlib.util.find_spec("ipykernel") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    return {
        "adapter": "jupyter",
        "bridge_available": available,
        "dependency": "ipykernel>=7,<8",
        "execution_scope": "standalone",
        "host_rpc": False,
        "internal_protocol": "hardened-jsonl",
        "languages": ["python", "r"],
        "kernels": [
            {
                "language": language,
                "name": KERNEL_NAMES[language],
                "spec": kernel_spec(language),
            }
            for language in ("python", "r")
        ],
    }


__all__ = [
    "KERNEL_NAMES",
    "KernelSpecError",
    "adapter_status",
    "default_user_kernels_dir",
    "install_kernelspecs",
    "kernel_spec",
    "prefix_kernels_dir",
    "selected_languages",
    "write_kernelspecs",
]
