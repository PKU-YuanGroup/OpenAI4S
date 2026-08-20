"""Isolated read-only scratch for Scientific Reviewer verification.

Scratch computation may read frozen Artifact copies and run a bounded Python
snippet. It cannot write the formal workspace, cannot reach the network, cannot
load MCP, cannot call submit_output, and inherits a secret-scrubbed child env.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai4s.kernel.environment import build_kernel_environment

_FORBIDDEN_IMPORTS = (
    "openai4s.sdk",
    "openai4s.host",
    "socket",
    "urllib",
    "http.client",
    "mcp",
)
_FORBIDDEN_TOKENS = (
    "submit_output",
    "host.delegate",
    "host.llm",
    "host.compute",
)


_PRELUDE = r"""
import builtins
import os
import sys

scratch = os.path.realpath(os.environ["OPENAI4S_REVIEW_SCRATCH"])
real_open = builtins.open

def open(file, mode="r", *args, **kwargs):
    path = os.path.realpath(str(file))
    writing = any(flag in str(mode) for flag in "wax+")
    if writing and not path.startswith(scratch + os.sep) and path != scratch:
        raise RuntimeError("review scratch cannot write the formal workspace")
    return real_open(file, mode, *args, **kwargs)

builtins.open = open

def _deny_network(*_a, **_k):
    raise RuntimeError("review scratch cannot open a network connection")

try:
    import socket
    socket.socket = _deny_network
    socket.create_connection = _deny_network
    socket.getaddrinfo = _deny_network
except Exception:
    pass
"""


class ReviewScratchError(RuntimeError):
    """A scratch verification attempt was refused or failed closed."""


def _assert_safe_code(code: str) -> None:
    lowered = code.lower()
    for token in _FORBIDDEN_TOKENS:
        if token.lower() in lowered:
            raise ReviewScratchError(f"review scratch forbids {token}")
    for name in _FORBIDDEN_IMPORTS:
        if name in code:
            raise ReviewScratchError(f"review scratch forbids import {name}")


def prepare_scratch(
    snapshot: Mapping[str, Any],
    *,
    artifact_paths: Mapping[str, str] | None = None,
    workspace: str | Path | None = None,
) -> Path:
    """Copy frozen artifact bytes into a private scratch directory."""

    root = Path(tempfile.mkdtemp(prefix="openai4s-review-scratch-"))
    (root / "artifacts").mkdir()
    copies: dict[str, str] = {}
    for version_id, source in dict(artifact_paths or {}).items():
        src = Path(source)
        if not src.is_file():
            continue
        dest = root / "artifacts" / f"{version_id}{src.suffix}"
        shutil.copyfile(src, dest)
        dest.chmod(0o444)
        copies[str(version_id)] = str(dest)
    (root / "snapshot.json").write_text(
        json.dumps(dict(snapshot), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (root / "artifact_index.json").write_text(
        json.dumps(copies, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    if workspace is not None:
        (root / "workspace_pointer.txt").write_text(
            str(Path(workspace).resolve()), encoding="utf-8"
        )
    return root


def run_scratch_python(
    code: str,
    *,
    scratch: Path,
    workspace: str | Path | None = None,
    timeout_s: float = 5.0,
    env_source: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a bounded snippet inside the isolated scratch."""

    _assert_safe_code(code)
    script = scratch / "snippet.py"
    script.write_text(_PRELUDE + "\n" + code, encoding="utf-8")
    env = build_kernel_environment(
        source=env_source if env_source is not None else {},
        mode="review_scratch",
        cwd=str(scratch),
    )
    env["OPENAI4S_REVIEW_SCRATCH"] = str(scratch)
    env["OPENAI4S_REVIEW_WORKSPACE"] = str(
        Path(workspace).resolve() if workspace else scratch
    )
    env["OPENAI4S_ALLOW_NETWORK"] = "0"
    # Never forward credential-shaped names even if a test source leaked one.
    for key in list(env):
        upper = key.upper()
        if any(token in upper for token in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            env.pop(key, None)
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(script)],
            cwd=str(scratch),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewScratchError("review scratch timed out") from exc
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4_000:],
        "stderr": completed.stderr[-4_000:],
    }


def cleanup_scratch(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
