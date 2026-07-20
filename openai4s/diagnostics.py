"""Redacted diagnostic bundle and log retention.

When a user reports "it failed", the useful reply is a single command whose
output can be pasted into an issue. Doing that by hand means deciding, under
time pressure, which of the daemon's files are safe to share — and the failure
mode of getting that wrong is a credential in a public tracker. So the bundle is
assembled by code that knows what must never go in, and the redaction runs on
the way out rather than being left to the person in a hurry.

What it contains is deliberately narrow: postures and versions, not data. The
database holds research work and credentials and is never included. Log lines
pass through the same shape-based redaction as the structured logger, so an
opaque credential is replaced by a fingerprint wherever it appears — including
in a line some future code emits without thinking about this module.

Retention: structured logs rotate by size with a bounded number of generations.
Unbounded logs are not a neutral default — they are a slow disk-full that
arrives at the least convenient moment, and on a long-lived daemon they also
accumulate an ever-larger record of activity nobody decided to keep.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from pathlib import Path
from typing import Any

from openai4s.observability import redact, redact_text

# One generation is a size, not a duration: a daemon can be quiet for a week or
# chatty for an hour, and bytes are what actually run out.
LOG_MAX_BYTES = 8 * 1024 * 1024
LOG_KEEP = 3

# Never collected, whatever the caller asks for. The database carries research
# work and (until fully brokered) credentials; the keychain-backed store is not
# ours to export at all.
_NEVER_COLLECT = (
    "openai4s.db",
    "openai4s.db-wal",
    "openai4s.db-shm",
    "openai4s.db-journal",
)


def _safe_read_tail(path: Path, limit: int = 512 * 1024) -> str:
    """The last `limit` bytes of a log, redacted."""
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # discard the partial first line
            lines = handle.readlines()
    except OSError as e:
        return f"<could not read {path.name}: {e}>"
    out = []
    for line in lines:
        # Structured lines redact field-wise; anything else is redacted as one
        # opaque string so a stray print of a token is still caught.
        try:
            out.append(json.dumps(redact(json.loads(line)), ensure_ascii=False))
        except (ValueError, TypeError):
            out.append(redact_text(line.rstrip("\n")))
    return "\n".join(out)


def environment_report() -> dict:
    """Versions and platform. No paths that reveal a home directory."""
    return {
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "openai4s": _version(),
    }


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("openai4s")
    except Exception:  # noqa: BLE001 - a missing version must not break support
        return "unknown"


def security_posture(cfg: Any) -> dict:
    """Every boundary's self-reported state, in one place.

    Assembled from the same functions the runtime uses, not a separate summary:
    a posture that could disagree with the code would be worse than none, since
    it would be believed.
    """
    report: dict[str, Any] = {}
    try:
        from openai4s.security.permissions import posture

        report["permissions"] = posture(Path(cfg.data_dir), Path(cfg.db_path))
    except Exception as e:  # noqa: BLE001
        report["permissions"] = {"error": str(e)}
    try:
        from openai4s.store import get_store

        store = get_store(cfg.db_path)
        report["schema"] = store.schema_state()
        report["secret_store"] = store.secrets.posture()
    except Exception as e:  # noqa: BLE001
        report["schema"] = {"error": str(e)}
    for name, env in (
        ("kernel_sandbox", "OPENAI4S_KERNEL_SANDBOX"),
        ("compute_confinement", "OPENAI4S_COMPUTE_CONFINEMENT"),
        ("secret_store_mode", "OPENAI4S_SECRET_STORE"),
        ("egress", "OPENAI4S_EGRESS"),
        ("structured_logs", "OPENAI4S_STRUCTURED_LOGS"),
    ):
        report[name] = os.environ.get(env, "(default)")
    return report


def rotate_log(
    path: Path, *, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP
) -> bool:
    """Roll `path` when it exceeds `max_bytes`, keeping `keep` generations.

    Returns True if a rotation happened. Oldest generation is deleted, which is
    the retention policy: bounded by construction rather than by someone
    remembering to prune.
    """
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    try:
        oldest = path.with_suffix(path.suffix + f".{keep}")
        if oldest.exists():
            oldest.unlink()
        for index in range(keep - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{index}")
            if src.exists():
                src.rename(path.with_suffix(path.suffix + f".{index + 1}"))
        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError:
        return False
    return True


def build_bundle(cfg: Any, destination: Path) -> dict:
    """Write a redacted diagnostic zip. Returns a manifest of what went in."""
    data_dir = Path(cfg.data_dir)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    excluded: list[dict[str, str]] = []
    report = {
        "environment": environment_report(),
        "security": security_posture(cfg),
    }

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("report.json", json.dumps(report, indent=2, default=str))
        included.append("report.json")
        logs_dir = data_dir / "logs"
        if logs_dir.is_dir():
            for log in sorted(logs_dir.glob("*.log*")):
                if not log.is_file():
                    continue
                bundle.writestr(f"logs/{log.name}", _safe_read_tail(log))
                included.append(f"logs/{log.name}")
        for name in _NEVER_COLLECT:
            if (data_dir / name).exists():
                excluded.append(
                    {"path": name, "reason": "may contain research data or credentials"}
                )
        bundle.writestr(
            "MANIFEST.json",
            json.dumps(
                {"included": included, "excluded": excluded}, indent=2, default=str
            ),
        )

    try:
        from openai4s.security.permissions import harden_file

        harden_file(destination)
    except Exception:  # noqa: BLE001 - hardening is best-effort
        pass
    return {"path": str(destination), "included": included, "excluded": excluded}


__all__ = [
    "LOG_KEEP",
    "LOG_MAX_BYTES",
    "build_bundle",
    "environment_report",
    "rotate_log",
    "security_posture",
]
