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
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping

from openai4s.observability import (
    fingerprint,
    redact,
    redact_identities,
    redact_text,
)

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

# What the daemon's log is actually called. `app.out` is the redirection every
# packaged launcher uses -- macOS, Linux and Windows all `exec ... serve >>
# "$OPENAI4S_DATA_DIR/logs/app.out" 2>&1` -- and it is where
# `observability.log_event` lands, because that writes to stderr. It does not
# match `*.log*`, so a bundle from a real install used to carry no logs at all
# while its MANIFEST listed what it did include and so read as complete.
# `*.log*` stays for anything an operator drops in beside it.
_LOG_PATTERNS = ("*.log*", "app.out*")


#: The only keys a structured log line may carry into a shareable archive, and
#: the shape each one must already be in. Deny-by-default, because the opposite
#: rule -- scrub what looks dangerous -- was tried and lost: field-wise
#: redaction asks "is this whole value a credential", and an ordinary `message`
#: field holding a sentence is never opaque, so it delivered a credential and a
#: token URL through untouched. The field was not called `token` and never will
#: be. An allowlist does not have to guess.
_ARCHIVE_FIELDS = (
    "ts",
    "event",
    "level",
    "correlation_id",
    "request_id",
    "surface",
    "exception",
    "error_class",
    "error_type",
    "status",
    "detail",
)

#: What an allowlisted value may look like: short, and made of the characters
#: an identifier, a timestamp or a fingerprint uses. `detail` is allowlisted but
#: is only ever the fixed `errors.DIAGNOSTIC_DETAIL` sentence, so spaces are
#: permitted and the length bound is what keeps it a label rather than a
#: channel.
_ARCHIVE_VALUE = re.compile(r"^[A-Za-z0-9 _.:<>@/+-]{0,120}$")

_ARCHIVE_LINE_CLASSES = (
    ("traceback", re.compile(r"^\s*Traceback \(most recent call last\)")),
    ("traceback_frame", re.compile(r"^\s+File \"")),
    ("warning", re.compile(r"(?i)\bwarn(ing)?\b")),
    ("error", re.compile(r"(?i)\berror\b|^[A-Za-z_.]+Error\b")),
    ("openai4s_notice", re.compile(r"^\[openai4s\]")),
)


def _archive_scalar(value: Any) -> Any:
    """One allowlisted value, or a fingerprint standing where it was."""
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    text = value if isinstance(value, str) else None
    if text is not None and _ARCHIVE_VALUE.match(text):
        return text
    return f"<omitted:{fingerprint(_stable_repr(value))}>"


def _stable_repr(value: Any) -> str:
    """A fingerprint input that never depends on an object's own `__repr__`.

    `json.dumps(..., default=str)` used to stringify anything the encoder did
    not understand, which is the same "call str() and hope" the diagnostic
    record itself stopped doing -- an object whose `__repr__` returns a path
    and a command was rendered into the archive by the serializer.
    """
    if isinstance(value, str):
        return value
    try:
        return f"{type(value).__module__}.{type(value).__qualname__}"
    except Exception:  # noqa: BLE001
        return "unknown"


def _archive_structured(record: Mapping[str, Any]) -> dict:
    """A structured log line reduced to validated, bounded metadata."""
    out: dict[str, Any] = {}
    dropped = 0
    for key, value in record.items():
        if key in _ARCHIVE_FIELDS:
            out[str(key)] = _archive_scalar(value)
        else:
            dropped += 1
    if dropped:
        out["fields_omitted"] = dropped
    return out


def _classify_plain(line: str) -> str:
    for name, pattern in _ARCHIVE_LINE_CLASSES:
        if pattern.search(line):
            return name
    return "other"


def _archive_plain(lines: list[str]) -> list[str]:
    """A plain log line is never shared verbatim.

    `app.out` is the daemon's entire stdout and stderr: every `print`, every
    `traceback.print_exc`, every dependency's chatter. There is no pattern set
    that makes arbitrary text safe -- the canary matrix that produced this
    change had an English sentence, a `/srv` path and a shell command survive
    every scrubber in the module. So the archive carries what can be counted
    and classified instead of what someone hoped could be scrubbed: how many
    lines there were, of what kind, and a fingerprint that still ties two
    reports of the same failure together.
    """
    counts: dict[str, int] = {}
    for line in lines:
        counts[_classify_plain(line)] = counts.get(_classify_plain(line), 0) + 1
    out = [
        json.dumps(
            {
                "archive_note": "unstructured lines are summarised, never shared",
                "lines": len(lines),
                "classes": dict(sorted(counts.items())),
                "fingerprint": fingerprint("\n".join(lines)),
            },
            ensure_ascii=False,
        )
    ]
    return out


def _safe_read_tail(path: Path, limit: int = 512 * 1024) -> str:
    """The last `limit` bytes of a log, reduced to what is safe to share.

    Two layers, and the distinction matters. `redact`/`redact_text`/
    `redact_identities`/`redact_url` still run first -- they are what make the
    *local* operator log safer to read, and nothing here reduces its richness
    on disk. This function is the second layer, and it is the one standing
    between a user's disk and a public issue tracker, so it is deny-by-default
    rather than pattern-based.
    """
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # discard the partial first line
            lines = handle.readlines()
    except OSError as e:
        # `str(e)` on an OSError names the path it failed on, and this string
        # goes straight into the archive.
        return json.dumps(
            {
                "archive_note": "log could not be read",
                "error_type": type(e).__name__,
            }
        )
    out: list[str] = []
    plain: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            plain.append(stripped)
            continue
        if not isinstance(parsed, dict):
            plain.append(stripped)
            continue
        scrubbed = redact_identities(
            json.dumps(redact(_archive_structured(parsed)), ensure_ascii=False)
        )
        out.append(redact_text(scrubbed))
    out.extend(_archive_plain(plain) if plain else [])
    return "\n".join(out)


def archive_safe(value: Any, _depth: int = 0) -> Any:
    """Reduce any in-process structure to what may be shared.

    `report.json` is assembled here rather than read off disk, which is exactly
    why it read as trusted -- but `environment_report()` and
    `security_posture()` both reach out to the machine, and whatever they bring
    back is free text the moment it is written into the archive.
    """
    if _depth > 8:
        return "<too-deep>"
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: archive_safe(item, _depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [archive_safe(item, _depth + 1) for item in value]
    return _archive_scalar(value)


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


def _probe_failure(exc: BaseException) -> dict:
    """What a failed posture probe is allowed to say in a shareable report."""
    try:
        kind = type(exc).__name__
    except Exception:  # noqa: BLE001
        kind = "unknown"
    return {"status": "unavailable", "error_type": kind}


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
        # `str(e)` here landed in `report.json`, which is the one file the
        # bundle has always shipped -- so a probe that failed put its own
        # exception text into a shareable archive, path, command and all. The
        # type is the part that is bounded and the part that is actionable.
        report["permissions"] = _probe_failure(e)
    try:
        from openai4s.store import get_store

        store = get_store(cfg.db_path)
        report["schema"] = store.schema_state()
        report["secret_store"] = store.secrets.posture()
    except Exception as e:  # noqa: BLE001
        report["schema"] = _probe_failure(e)
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
        # `default=str` rendered any object the encoder did not understand,
        # which is the same "call str() and hope" the diagnostic record
        # stopped doing. `archive_safe` reduces the structure first, so by
        # the time json sees it every leaf is already a validated scalar.
        bundle.writestr("report.json", json.dumps(archive_safe(report), indent=2))
        included.append("report.json")
        logs_dir = data_dir / "logs"
        if logs_dir.is_dir():
            collected = {
                path for pattern in _LOG_PATTERNS for path in logs_dir.glob(pattern)
            }
            for log in sorted(collected):
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
                archive_safe({"included": included, "excluded": excluded}), indent=2
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
    "archive_safe",
    "build_bundle",
    "environment_report",
    "rotate_log",
    "security_posture",
]
