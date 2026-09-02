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
from typing import Any, BinaryIO, Mapping

from openai4s.observability import (
    _looks_opaque,
    fingerprint,
    redact,
    redact_identities,
    redact_text,
)

# One generation is a size, not a duration: a daemon can be quiet for a week or
# chatty for an hour, and bytes are what actually run out.
LOG_MAX_BYTES = 8 * 1024 * 1024
LOG_KEEP = 3

#: Web bundle cap. The CLI path stays unbounded: an operator at a terminal
#: chose the destination. The HTTP path is a DoS surface, so 32 MiB is hard.
BUNDLE_MAX_BYTES = 32 * 1024 * 1024
BUNDLE_FILENAME = "openai4s-diagnostics.zip"
BUNDLE_TEMP_PREFIX = "openai4s-diagnostics-"

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


#: Per-field validators, and every one is a closed set, a number, a fixed
#: literal or a fingerprint. Nothing is admitted for matching a pattern.
#:
#: Two rounds got this wrong the same way. First one shared "short enough"
#: regex, which admitted spaces, `/` and `.` -- prose in `detail`, a path in
#: `surface`, a command in `status`. Then per-field *patterns*, which admitted
#: `PRIVATE_COHORT_ALPHA_SEVEN`: a legal identifier, no digits, so it satisfies
#: every identifier rule and never reads as opaque. Syntax is not provenance.
#: Membership of a set written down here is.

#: The events this repository emits, from the `log_event` call sites.
_ARCHIVE_EVENTS = frozenset(
    {
        "unhandled_exception",
        "http_request",
        "compute_ssh_command",
        "compute_scp_upload",
        "compute_scp_download",
    }
)

#: The surfaces passed to `record_diagnostic`, from its call sites. Anything
#: else is fingerprinted -- including `jobs:_run:<job id>`, whose variable half
#: is an internal id with no business in a shared archive.
_ARCHIVE_SURFACES = frozenset(
    {
        "agent:pending_env",
        "artifact_refs:materialise",
        "artifact_refs:read",
        "artifacts:restore",
        "artifacts:upload:stage",
        "artifacts:write",
        "cell:attempt",
        "compute:provider",
        "compute:refresh",
        "connector:call",
        "jobs:submit",
        "kernel:restart_after_install",
        "web",
        "web:turn",
        "unknown",
    }
)
_ARCHIVE_LEVELS = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "fatal"}
)
_ARCHIVE_STATUSES = frozenset(
    {"ok", "unavailable", "degraded", "failed", "skipped", "partial", "unknown"}
)


def _v_number(value: Any) -> Any:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _v_bool(value: Any) -> Any:
    return value if isinstance(value, bool) else None


def _exact_str(value: Any) -> bool:
    """`type(x) is str`, deliberately not `isinstance`.

    A `str` subclass is accepted by `isinstance` and may define `__eq__`,
    `__hash__`, `lower`, `__str__` and `encode`. Every one of those is a method
    the *value* owns, so a membership test downstream of an `isinstance` gate
    asks the attacker whether it is allowed — and it says yes, while the JSON
    encoder writes the real buffer. Measured: an `Alias(str)` whose content was
    a private phrase and whose `__eq__` claimed to be `Darwin` reached the
    archive as both a value and a key.

    So the exact type is checked *first*, before any membership, hash,
    equality, fingerprint, encode or lower.
    """
    return type(value) is str


def _v_enum(allowed: frozenset) -> Any:
    """Membership, returning the repository's own literal.

    Returning the *input* is what lets a subclass through even once the type
    check is right: the object itself travels onward and something downstream
    writes its buffer. Returning the source literal makes that unreachable
    rather than merely caught.
    """
    canonical = {str(item): str(item) for item in allowed}

    def check(value: Any) -> Any:
        if not _exact_str(value):
            return None
        return canonical.get(value)

    return check


def _v_lower_enum(allowed: frozenset) -> Any:
    canonical = {str(item): str(item) for item in allowed}

    def check(value: Any) -> Any:
        # `value.lower()` is the value's own method; call it only after the
        # exact-type check has established there is no override.
        if not _exact_str(value):
            return None
        return canonical.get(value.lower())

    return check


def _v_identity(value: Any) -> Any:
    """A variable id: never kept, always fingerprinted, never dropped.

    Request and correlation ids ARE server-generated in the daemon, which makes
    keeping a value that matches the server's own 16-hex shape tempting. But
    the archive reads them out of `app.out`, and a line in a file can carry any
    string of that shape -- a 16- or 32-hex secret among them. Dropping them
    would lose the one thing tying a bundle to a ticket; the fingerprint keeps
    that, because support can fingerprint the id the user quotes and match.
    """
    if not _exact_str(value) or not value:
        return None
    return f"<redacted:{fingerprint(value)}>"


def _v_type_name(value: Any) -> Any:
    from openai4s.server.errors import _KNOWN_EXCEPTIONS, UNKNOWN_TYPE_NAME

    if not _exact_str(value):
        return None
    for known in _KNOWN_EXCEPTIONS:
        if value == known:
            return str(known)
    return UNKNOWN_TYPE_NAME if value == UNKNOWN_TYPE_NAME else None


def _v_detail(value: Any) -> Any:
    """Exactly the one sentence, or nothing."""
    from openai4s.server.errors import DIAGNOSTIC_DETAIL

    if not _exact_str(value):
        return None
    return DIAGNOSTIC_DETAIL if value == DIAGNOSTIC_DETAIL else None


def _v_version(value: Any) -> Any:
    """Only the parsed numeric components, or nothing.

    A version is the one genuinely variable string here that is genuinely worth
    keeping, so it is *reduced* rather than matched: split on dots, keep the
    leading run of all-digit components. `3.12.13` keeps its meaning,
    `6.5.0-15-generic` becomes `6.5` (parsing stops at the first component that
    is not a number, rather than trying to salvage the rest), and
    `3.privatecohortalpha` becomes nothing.
    """
    if not _exact_str(value) or not value:
        return None
    parts: list[str] = []
    for chunk in value.split("."):
        if chunk.isdigit() and len(chunk) <= 6:
            parts.append(chunk)
        else:
            break
    return ".".join(parts) if parts else None


#: `platform.machine()` on the platforms this project supports.
_ARCHITECTURES = frozenset(
    {
        "x86_64",
        "amd64",
        "arm64",
        "aarch64",
        "i386",
        "i686",
        "armv7l",
        "armv6l",
        "ppc64le",
        "s390x",
        "riscv64",
        "",
    }
)
#: `platform.system()`.
_SYSTEMS = frozenset({"Darwin", "Linux", "Windows", "Java", ""})
#: `sys.platform`.
_SYS_PLATFORMS = frozenset(
    {"darwin", "linux", "win32", "cygwin", "aix", "freebsd", "openbsd", "netbsd", ""}
)
#: `SecretBroker` backend names.
_SECRET_BACKENDS = frozenset(
    {
        "none",
        "macos-keychain",
        "secret-service",
        "env-injection",
        "plaintext-db",
        "memory",
        "",
    }
)


#: The posture knobs are a closed vocabulary, so they are an enum rather than a
#: token: the value comes from the process environment, which an operator can
#: set to anything, and echoing an unrecognised one back into a shared archive
#: is the same "it looked short enough" mistake in a smaller place. `(default)`
#: is the literal `security_posture` writes when the variable is unset.
_POSTURE_MODES = frozenset(
    {
        "(default)",
        "auto",
        "enforce",
        "off",
        "on",
        "keychain",
        "plaintext",
        "allowlist",
        "deny",
        "allow",
        "0",
        "1",
        "true",
        "false",
        "yes",
        "no",
    }
)
_v_posture_mode = _v_enum(_POSTURE_MODES)


_ARCHIVE_FIELDS: dict[str, Any] = {
    "ts": _v_number,
    "event": _v_enum(_ARCHIVE_EVENTS),
    "level": _v_lower_enum(_ARCHIVE_LEVELS),
    "correlation_id": _v_identity,
    "request_id": _v_identity,
    "surface": _v_enum(_ARCHIVE_SURFACES),
    "exception": _v_type_name,
    "error_type": _v_type_name,
    # Re-fingerprinted, not checked for hex. A fingerprint *looks* like a safe
    # thing, which is exactly why this was the last field still judged by
    # shape -- and a line in `app.out` can put any 6-64 hex string there.
    # Fingerprinting is stable, so correlation survives.
    "error_class": _v_identity,
    "status": _v_lower_enum(_ARCHIVE_STATUSES),
    "detail": _v_detail,
    "fields_omitted": _v_number,
}


#: How an unstructured line is described when it cannot be quoted. Stable
#: labels, so two reports of the same failure look alike.
_ARCHIVE_LINE_CLASSES = (
    ("traceback", re.compile(r"^\s*Traceback \(most recent call last\)")),
    ("traceback_frame", re.compile(r'^\s+File "')),
    ("openai4s_notice", re.compile(r"^\[openai4s\]")),
    ("error", re.compile(r"(?i)\berror\b|^[A-Za-z_.]+Error\b")),
    ("warning", re.compile(r"(?i)\bwarn(ing)?\b")),
)


def _stable_repr(value: Any) -> str:
    """A fingerprint input that never depends on an object's own `__repr__`.

    `json.dumps(..., default=str)` used to stringify anything the encoder did
    not understand, which is the same "call str() and hope" the diagnostic
    record itself stopped doing -- an object whose `__repr__` returns a path
    and a command was rendered into the archive by the serializer.
    """
    if _exact_str(value):
        return value
    try:
        return f"{type(value).__module__}.{type(value).__qualname__}"
    except Exception:  # noqa: BLE001
        return "unknown"


def _archive_field(key: str, value: Any) -> Any:
    validator = _ARCHIVE_FIELDS.get(key)
    if validator is None:
        return f"<omitted:{fingerprint(_stable_repr(value))}>"
    checked = validator(value)
    if checked is None:
        return f"<omitted:{fingerprint(_stable_repr(value))}>"
    return checked


def _archive_structured(record: Mapping[str, Any]) -> dict:
    """A structured log line reduced to validated, bounded metadata."""
    out: dict[str, Any] = {}
    dropped = 0
    for key, value in record.items():
        if not _exact_str(key) or key not in _ARCHIVE_FIELDS:
            dropped += 1
            continue
        out[key] = _archive_field(key, value)
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


def _safe_error_type(exc: BaseException) -> str:
    """The same category rule the rest of the boundary uses."""
    from openai4s.server.errors import safe_type_name

    return safe_type_name(exc)


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
                "error_type": _safe_error_type(e),
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


#: The report is built to a declared shape rather than walked generically.
#:
#: A generic walk has to answer "is this value safe" with one rule, and that is
#: the question that cannot be answered without knowing the field: a 35-char
#: token and a version string are the same shape. So the schema names what may
#: appear, and everything else is counted rather than rendered. Nothing here
#: calls `str()` or `repr()` on a value or a key -- an object the encoder does
#: not understand is dropped, not stringified, because `default=str` was
#: exactly that bypass.
_REPORT_SCHEMA: dict[str, Any] = {
    "environment": {
        "python": _v_version,
        "platform": _v_enum(_SYSTEMS),
        "release": _v_version,
        "machine": _v_enum(_ARCHITECTURES),
        "openai4s": _v_version,
    },
    "security": {
        "permissions": {
            "supported": _v_bool,
            "platform": _v_enum(_SYS_PLATFORMS),
            "data_dir_owner_only": _v_bool,
            "db_owner_only": _v_bool,
            "status": _v_lower_enum(_ARCHIVE_STATUSES),
            "error_type": _v_type_name,
        },
        "schema": {
            "version": _v_number,
            "expected": _v_number,
            "current": _v_bool,
            "applied": [
                {
                    "version": _v_number,
                    "applied_at": _v_number,
                    # Variable text from a table this archive does not own. The
                    # version number already says which migrations ran, and an
                    # enumerated set of names would go stale *silently* the day
                    # someone adds one.
                    "name": _v_identity,
                    "checksum": _v_identity,
                }
            ],
            "status": _v_lower_enum(_ARCHIVE_STATUSES),
            "error_type": _v_type_name,
        },
        "secret_store": {
            "mode": _v_posture_mode,
            "backend": _v_enum(_SECRET_BACKENDS),
            "secure": _v_bool,
            "persistent": _v_bool,
            "status": _v_lower_enum(_ARCHIVE_STATUSES),
            "error_type": _v_type_name,
        },
        "kernel_sandbox": _v_posture_mode,
        "compute_confinement": _v_posture_mode,
        "secret_store_mode": _v_posture_mode,
        "egress": _v_posture_mode,
        "structured_logs": _v_posture_mode,
    },
}


def _archive_to_schema(value: Any, schema: Any) -> tuple[Any, int]:
    """Project `value` onto `schema`. Returns the value and how much was cut."""
    dropped = 0
    if isinstance(schema, dict):
        if not isinstance(value, Mapping):
            return None, 1
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not _exact_str(key) or key not in schema:
                # Never `str(key)`: a mapping key can be an object whose
                # `__str__` this report does not own.
                dropped += 1
                continue
            projected, cut = _archive_to_schema(item, schema[key])
            dropped += cut
            if projected is not None:
                out[key] = projected
        if dropped:
            out["fields_omitted"] = dropped
        return out, 0
    if isinstance(schema, list):
        if not isinstance(value, (list, tuple)):
            return None, 1
        items = []
        for item in value:
            projected, cut = _archive_to_schema(item, schema[0])
            dropped += cut
            if projected is not None:
                items.append(projected)
        return items, dropped
    checked = schema(value)
    return (checked, 0) if checked is not None else (None, 1)


def archive_safe(report: Mapping[str, Any]) -> dict:
    """The shareable projection of the in-process report."""
    projected, _dropped = _archive_to_schema(report, _REPORT_SCHEMA)
    return projected if isinstance(projected, dict) else {}


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
    from openai4s.server.errors import safe_type_name

    return {"status": "unavailable", "error_type": safe_type_name(exc)}


def passive_security_posture(cfg: Any) -> dict:
    """Env knobs plus permission stats. Does not open the Store.

    `security_posture()` still probes schema and the secret broker for the
    CLI bundle, which needs the live answer. A page-load GET cannot: opening
    the database is a business write on a fresh install (and a WAL touch on
    an existing one). Schema and broker state therefore stay off this path.
    """
    report: dict[str, Any] = {}
    try:
        from openai4s.security.permissions import posture

        report["permissions"] = posture(Path(cfg.data_dir), Path(cfg.db_path))
    except Exception as e:  # noqa: BLE001
        report["permissions"] = _probe_failure(e)
    for name, env in (
        ("kernel_sandbox", "OPENAI4S_KERNEL_SANDBOX"),
        ("compute_confinement", "OPENAI4S_COMPUTE_CONFINEMENT"),
        ("secret_store_mode", "OPENAI4S_SECRET_STORE"),
        ("egress", "OPENAI4S_EGRESS"),
        ("structured_logs", "OPENAI4S_STRUCTURED_LOGS"),
    ):
        report[name] = os.environ.get(env, "(default)")
    return report


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
    store = None
    try:
        from openai4s.store import get_store

        store = get_store(cfg.db_path)
        report["schema"] = store.schema_state()
    except Exception as e:  # noqa: BLE001
        # The database never opened, so neither probe below it ran. Both keys
        # record that same failure rather than one of them going missing.
        report["schema"] = _probe_failure(e)
        report["secret_store"] = _probe_failure(e)
    if store is not None:
        # Its own clause, deliberately: `store.secrets` resolves a SecretBroker,
        # which fails closed on a host with no secure store -- every headless
        # Linux server without libsecret, every container. Sharing the schema
        # probe's `except` meant that on exactly the hosts an operator runs
        # `doctor` on, an absent secret store overwrote a schema state that had
        # already been read successfully, so the report blamed the schema probe
        # for the secret store's absence and dropped `secret_store` entirely --
        # a misattributed failure in the one file the bundle always ships.
        try:
            report["secret_store"] = store.secrets.posture()
        except Exception as e:  # noqa: BLE001
            report["secret_store"] = _probe_failure(e)
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


class BundleTooLarge(Exception):
    """The archive would exceed the web bundle cap. Never sent to a client."""


class _CappedIO:
    """A seekable binary file that refuses to grow past `limit` bytes."""

    def __init__(self, fh: BinaryIO, limit: int) -> None:
        self._fh = fh
        self._limit = limit
        self.bytes_written = 0

    def write(self, data: bytes) -> int:  # noqa: D401 - file-like
        n = len(data)
        self.bytes_written += n
        if self.bytes_written > self._limit:
            raise BundleTooLarge(f"diagnostic bundle exceeds {self._limit} bytes")
        return self._fh.write(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._fh.seek(offset, whence)

    def tell(self) -> int:
        return self._fh.tell()

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        return self._fh.read(n)

    @property
    def name(self) -> str:
        return str(getattr(self._fh, "name", ""))


def _populate_bundle(cfg: Any, bundle: zipfile.ZipFile) -> dict[str, Any]:
    """Write the deny-by-default archive into an open ZipFile."""
    data_dir = Path(cfg.data_dir)
    included: list[str] = []
    excluded: list[dict[str, str]] = []
    report = {
        "environment": environment_report(),
        "security": security_posture(cfg),
    }
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
        # Numbered by the archive, never named after the file on disk. A
        # log's *name* is attacker-influenced the moment anything writes
        # one named after a token, and it lands in two places no content
        # scrubber looks: the ZIP member name and the MANIFEST listing it.
        for index, log in enumerate(sorted(collected), start=1):
            if not log.is_file():
                continue
            member = f"logs/log-{index:04d}.json"
            bundle.writestr(member, _safe_read_tail(log))
            included.append(member)
    for name in _NEVER_COLLECT:
        if (data_dir / name).exists():
            excluded.append(
                {"path": name, "reason": "may contain research data or credentials"}
            )
    bundle.writestr(
        "MANIFEST.json",
        json.dumps({"included": included, "excluded": excluded}, indent=2),
    )
    return {"included": included, "excluded": excluded}


def _harden(path: Path) -> None:
    try:
        from openai4s.security.permissions import harden_file

        harden_file(path)
    except Exception:  # noqa: BLE001 - hardening is best-effort
        pass


def build_bundle(cfg: Any, destination: Path) -> dict:
    """Write a redacted diagnostic zip. Returns a manifest of what went in.

    The CLI destination is caller-chosen. The web path must not take a
    destination: it uses `write_bundle_file` on a process-owned temp file.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        result = _populate_bundle(cfg, bundle)
    _harden(destination)
    return {"path": str(destination), **result}


def write_bundle_file(
    cfg: Any, path: Path, *, max_bytes: int = BUNDLE_MAX_BYTES
) -> dict:
    """Write a capped archive to `path`. Raises `BundleTooLarge` over `max_bytes`.

    `path` is created by the caller (a temp file the HTTP handler will
    unlink on every exit, including client disconnect). This function
    never chooses a user-supplied location.
    """
    target = Path(path)
    with target.open("w+b") as handle:
        capped = _CappedIO(handle, max_bytes)
        with zipfile.ZipFile(capped, "w", zipfile.ZIP_DEFLATED) as bundle:
            result = _populate_bundle(cfg, bundle)
    _harden(target)
    return {"path": str(target), **result}


__all__ = [
    "BUNDLE_FILENAME",
    "BUNDLE_MAX_BYTES",
    "BUNDLE_TEMP_PREFIX",
    "BundleTooLarge",
    "LOG_KEEP",
    "LOG_MAX_BYTES",
    "archive_safe",
    "build_bundle",
    "environment_report",
    "passive_security_posture",
    "rotate_log",
    "security_posture",
    "write_bundle_file",
]
