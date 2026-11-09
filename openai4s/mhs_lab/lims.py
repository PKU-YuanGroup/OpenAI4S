"""A tiny shared sample registry (LIMS) for the simulated lab.

Real self-driving labs track vials by barcode in a LIMS.  Here the flow
reactor labels the vials it fills and the assay workstation scans a vial's
label when it loads it.  The registry is one JSON file guarded by an exclusive
``fcntl`` lock, so the two endpoint processes can share it.  Nothing in it is
physical state of a device: moving a vial is the robot arm's job.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX; the lab runs on macOS/Linux
    import fcntl
except ImportError:  # pragma: no cover - Windows: best-effort without a lock
    fcntl = None  # type: ignore[assignment]

__all__ = ["lims_path", "register_vial", "get_vial", "list_vials", "update_vial"]


def lims_path() -> Path:
    explicit = os.environ.get("OPENAI4S_LAB_LIMS", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from openai4s.mhs_lab import lab_dir

    return lab_dir() / "lims.json"


@contextmanager
def _locked() -> Iterator[dict[str, Any]]:
    path = lims_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("vials", {})
            yield data
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def register_vial(record: dict[str, Any]) -> dict[str, Any]:
    with _locked() as data:
        vials = data["vials"]
        vial_id = str(record.get("vial_id") or f"V-{len(vials) + 1:03d}")
        entry = dict(record, vial_id=vial_id, registered_at=time.time())
        entry.setdefault("location", "reactor_outlet")
        vials[vial_id] = entry
        return dict(entry)


def update_vial(vial_id: str, **changes: Any) -> dict[str, Any] | None:
    with _locked() as data:
        entry = data["vials"].get(vial_id)
        if entry is None:
            return None
        entry.update(changes)
        return dict(entry)


def get_vial(vial_id: str) -> dict[str, Any] | None:
    with _locked() as data:
        entry = data["vials"].get(vial_id)
        return dict(entry) if entry else None


def list_vials() -> list[dict[str, Any]]:
    with _locked() as data:
        return [dict(v) for v in data["vials"].values()]


def next_vial_id() -> str:
    with _locked() as data:
        return f"V-{len(data['vials']) + 1:03d}"
