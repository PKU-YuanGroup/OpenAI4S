"""Persistent registry of configured remote-GPU hosts + the services provisioned
on each — the "memory" that lets ``host.fold`` / ``host.score_mutations`` resolve
a real remote host, and that grows as new services get provisioned (never
fabricated: a capability is registered only after a real service is verified on
the host).

One JSON file per data dir: ``<data_dir>/remote_compute.json`` ::

    {
      "hosts": {
        "<ssh-alias>": {
          "alias": str, "label": str, "gpus": str|null, "gpu_count": int,
          "added_at": <epoch-ms>,
          "capabilities": {
            "<cap>": {"script": str, "invoke": str, "engine": str,
                      "markers": {...}, "notes": str, "verified_at": <epoch-ms>}
          }
        }
      },
      "default_host": "<alias>" | null
    }

A host is an SSH *alias* the user already has in ``~/.ssh/config`` — the
transport is plain ``ssh <alias>`` (see ``openai4s.compute.manager``). The
registry never stores secrets; auth stays in the user's ssh config/agent.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_FILE = "remote_compute.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _data_dir() -> Path:
    from openai4s.config import get_config

    return Path(get_config().data_dir)


def _path(data_dir: Path | None = None) -> Path:
    return (data_dir or _data_dir()) / _FILE


def _empty() -> dict:
    return {"hosts": {}, "default_host": None}


def _seed_from_env(data: dict) -> bool:
    """First-run back-compat: if the registry is empty but a fold host is set via
    OPENAI4S_FOLD_SSH, seed it so existing ``host.fold`` keeps working. Returns
    True if it mutated ``data``."""
    if data.get("hosts"):
        return False
    alias = (os.environ.get("OPENAI4S_FOLD_SSH") or "").strip()
    if not alias:
        return False
    script = os.environ.get("OPENAI4S_FOLD_SCRIPT", "/opt/os-fold/fold.sh")
    data["hosts"][alias] = {
        "alias": alias,
        "label": alias,
        "gpus": None,
        "gpu_count": 0,
        "added_at": _now_ms(),
        "capabilities": {
            "fold": {
                "script": script,
                "invoke": (
                    f"{script} --seq <SEQ> --name <NAME> --out <DIR> " "--gpu <N>"
                ),
                "engine": "Protenix v1.0.0 (AlphaFold3-class)",
                "markers": {
                    "result": "===FOLD_RESULT_JSON===",
                    "pdb_b64": "===FOLD_PDB_B64===",
                },
                "notes": "seeded from OPENAI4S_FOLD_SSH",
                "verified_at": None,
            }
        },
    }
    data["default_host"] = alias
    return True


def load(data_dir: Path | None = None) -> dict:
    """Read the registry (seeding fold from env on first empty run)."""
    with _LOCK:
        p = _path(data_dir)
        data = _empty()
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8")) or _empty()
            except (OSError, ValueError):
                data = _empty()
        data.setdefault("hosts", {})
        data.setdefault("default_host", None)
        if _seed_from_env(data):
            _write(data, data_dir)
        return data


def _write(data: dict, data_dir: Path | None = None) -> None:
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)


def save(data: dict, data_dir: Path | None = None) -> None:
    with _LOCK:
        _write(data, data_dir)


# --- host CRUD ------------------------------------------------------------- #
def list_hosts(data_dir: Path | None = None) -> dict:
    return load(data_dir).get("hosts", {})


def ssh_config_aliases() -> list[str]:
    """Concrete `Host` aliases from ``~/.ssh/config``, wildcards skipped.

    The second place an alias can legitimately come from. The Web UI offers
    these as remote-GPU candidates, so treating only this registry as "known"
    would refuse the very names the product invites the user to pick.
    """
    out: list[str] = []
    try:
        text = (Path.home() / ".ssh" / "config").read_text("utf-8", "replace")
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition(" ")
        if key.lower() != "host":
            continue
        for token in value.split():
            if token and "*" not in token and "?" not in token and token not in out:
                out.append(token)
    return out


def is_known_alias(alias: str, data_dir: Path | None = None) -> bool:
    """Whether this daemon has any record of ``alias`` as a remote host.

    Registration is the union of the two sources a user can actually create an
    alias through: this registry, and their own ``~/.ssh/config``. A name in
    neither has never been offered to them by anything, and spawning
    ``ssh <name>`` for it lets an agent-chosen string reach the network through
    whatever a ``Host *`` stanza or a DNS search domain resolves.

    Deliberately not a shape check -- `manager._safe_alias` already covers
    "cannot be read as an option". Shape is about argv parsing; this is about
    whether the destination is one the user has ever named.

    ``data_dir`` is taken from the caller rather than from the global config,
    because a `ComputeManager` is constructed with a config and must read the
    registry that config points at -- otherwise a manager bound to one data
    directory answers questions about another's hosts.
    """
    text = str(alias or "").strip()
    if not text:
        return False
    if get_host(text, data_dir) is not None:
        return True
    return text in ssh_config_aliases()


def get_host(alias: str, data_dir: Path | None = None) -> dict | None:
    return load(data_dir).get("hosts", {}).get(alias)


def add_host(
    alias: str,
    *,
    label: str | None = None,
    gpus: str | None = None,
    gpu_count: int = 0,
    data_dir: Path | None = None,
) -> dict:
    """Register (or update basic info of) a remote host by ssh alias. Preserves
    any existing capabilities."""
    with _LOCK:
        data = load(data_dir)
        h = data["hosts"].get(alias) or {
            "alias": alias,
            "added_at": _now_ms(),
            "capabilities": {},
        }
        h.update(alias=alias, label=label or h.get("label") or alias)
        if gpus is not None:
            h["gpus"] = gpus
        if gpu_count:
            h["gpu_count"] = gpu_count
        h.setdefault("capabilities", {})
        data["hosts"][alias] = h
        if not data.get("default_host"):
            data["default_host"] = alias
        _write(data, data_dir)
        return h


def remove_host(alias: str, data_dir: Path | None = None) -> bool:
    with _LOCK:
        data = load(data_dir)
        if alias not in data["hosts"]:
            return False
        del data["hosts"][alias]
        if data.get("default_host") == alias:
            data["default_host"] = next(iter(data["hosts"]), None)
        _write(data, data_dir)
        return True


def set_default(alias: str, data_dir: Path | None = None) -> bool:
    with _LOCK:
        data = load(data_dir)
        if alias not in data["hosts"]:
            return False
        data["default_host"] = alias
        _write(data, data_dir)
        return True


def default_host(data_dir: Path | None = None) -> str | None:
    return load(data_dir).get("default_host")


# --- capabilities ---------------------------------------------------------- #
def set_capability(
    alias: str, cap: str, meta: dict, data_dir: Path | None = None
) -> dict:
    """Record/overwrite a provisioned capability on a host. Called after a
    service has been verified real on the box."""
    with _LOCK:
        data = load(data_dir)
        h = data["hosts"].get(alias)
        if h is None:
            h = {
                "alias": alias,
                "label": alias,
                "added_at": _now_ms(),
                "capabilities": {},
            }
            data["hosts"][alias] = h
        m = dict(meta)
        m.setdefault("verified_at", _now_ms())
        h.setdefault("capabilities", {})[cap] = m
        if not data.get("default_host"):
            data["default_host"] = alias
        _write(data, data_dir)
        return h


def capability_host(
    cap: str, data_dir: Path | None = None
) -> tuple[str | None, dict | None]:
    """Resolve which host provides ``cap``. Prefers the default host; otherwise
    the first host that has it. Returns (alias, capability_meta) or (None, None).
    Reachability is NOT checked here — the caller probes at run time and errors
    honestly if unreachable (never fabricates)."""
    data = load(data_dir)
    hosts = data.get("hosts", {})
    default = data.get("default_host")
    order = ([default] if default in hosts else []) + [a for a in hosts if a != default]
    for alias in order:
        cap_meta = (hosts.get(alias, {}).get("capabilities") or {}).get(cap)
        if cap_meta:
            return alias, cap_meta
    return None, None
