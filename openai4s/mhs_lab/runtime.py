"""Process environment and serialization for operator-owned lab mutations."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from openai4s.mhs_lab import lab_dir

_LOCK = threading.RLock()
_LOCAL = threading.local()


def child_environment(explicit: dict | None = None) -> dict[str, str]:
    """Pass runtime paths and the lab directory, never inherited provider keys."""
    from openai4s.config import get_config
    from openai4s.mcp_client import connector_environment

    data_dir = os.environ.get("OPENAI4S_DATA_DIR") or get_config().data_dir
    runtime_paths = {
        "OPENAI4S_DATA_DIR": str(Path(data_dir).expanduser().resolve()),
        "OPENAI4S_MHS_LAB_DIR": str(lab_dir().resolve()),
    }
    lims = os.environ.get("OPENAI4S_LAB_LIMS", "").strip()
    if lims:
        runtime_paths["OPENAI4S_LAB_LIMS"] = str(Path(lims).expanduser().resolve())

    paths = [str(Path(__file__).resolve().parents[2])]
    for name in ("mhs", "openai4s_mhs"):
        import importlib.util

        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin:
            paths.append(str(Path(spec.origin).resolve().parent.parent))
    return connector_environment(
        {
            "PYTHONPATH": os.pathsep.join(dict.fromkeys(paths)),
            "PYTHONDONTWRITEBYTECODE": "1",
            **runtime_paths,
            **(explicit or {}),
        }
    )


@contextmanager
def mutation_lock():
    """Serialize threads and CLI/daemon processes; nested mutations share one lock."""
    with _LOCK:
        if getattr(_LOCAL, "held", False):
            yield
            return
        with open(lab_dir() / "supervisor.lock", "a+b") as handle:
            handle.write(b"\0")
            handle.flush()
            deadline = time.monotonic() + 60
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("another lab operation is still running")
                    time.sleep(0.05)
            _LOCAL.held = True
            try:
                yield
            finally:
                _LOCAL.held = False
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def run(*args, **kwargs):
        with mutation_lock():
            return function(*args, **kwargs)

    return run
