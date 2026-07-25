"""What telemetry is allowed to say, declared once.

The frozen decision (docs/v02-decisions.md) is "counts and enumerations only --
zero free text", enforced by "an allowlist ... asserting the outgoing payload
contains no key outside it". A **key** allowlist does not do that job. Both of
these pass a key check:

    {"error_type": "ValueError"}                     # an enumeration
    {"error_type": "FileNotFoundError: /home/y/unpublished/cohort_2026.csv"}

Same key, and the second is a research subject and a person's home directory.
So the allowlist here is over **values**: every field declares a domain, and a
value outside its domain never reaches the wire.

The domains are deliberately few, and there is deliberately no STRING, TEXT,
JSON, MAP or LIST among them. Adding a field that *could* carry free text
therefore requires adding a new domain class -- a visible diff that reads as a
privacy change -- rather than one more line in a table that reads as routine.

What emphatically must NOT be reused here is `openai4s.observability.redact`.
It is calibrated for credentials, and `_looks_opaque` exempts anything starting
with `/` on purpose, with a test pinning that behaviour. Pointed at research
data it does the opposite of what is wanted: it passes an absolute path through
untouched and redacts a harmless environment name. A gate that looks like
protection and is not is worse than no gate.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

#: Upper bound on a count. Values above it are clamped, never sent as-is: an
#: unbounded integer is a fingerprint (a cell count of 41,318 identifies an
#: install about as well as a name does).
MAX_COUNT = 1000

#: Records per envelope. A bound the collector can also enforce before parsing.
MAX_RECORDS = 64


class Domain:
    """A finite set of things a field may say."""

    kind = "abstract"

    def accepts(self, value: Any) -> bool:
        raise NotImplementedError

    def coerce(self, value: Any) -> Any:
        """The value to send, or None to drop the field entirely.

        Dropping rather than substituting: a field silently replaced by a
        default is a field whose absence nobody notices.
        """
        return value if self.accepts(value) else None


class Enum(Domain):
    """One of a fixed, literal set of members.

    The members are written out in this file. They are never derived from
    observed data, from a provider's response, or from anything a user can
    name -- that is how an "enumeration" quietly becomes a free-text channel.
    """

    kind = "enum"

    def __init__(self, *members: str) -> None:
        self.members = frozenset(members)

    def accepts(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.members


class Count(Domain):
    """A non-negative integer, clamped to MAX_COUNT."""

    kind = "count"

    def accepts(self, value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def coerce(self, value: Any) -> Any:
        if not self.accepts(value):
            return None
        return min(int(value), MAX_COUNT)


class Bucket(Domain):
    """A duration, reported as which bucket it fell in and never as a number.

    A millisecond figure is closer to a fingerprint than to a measurement, and
    the question telemetry can honestly answer is "is this slow", not "how slow
    was this particular run on this particular machine".
    """

    kind = "bucket"

    LABELS = ("lt_1s", "lt_10s", "lt_60s", "lt_10m", "lt_1h", "ge_1h")
    _EDGES = (
        (1.0, "lt_1s"),
        (10.0, "lt_10s"),
        (60.0, "lt_60s"),
        (600.0, "lt_10m"),
        (3600.0, "lt_1h"),
    )

    def accepts(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.LABELS

    @classmethod
    def of(cls, seconds: float) -> str:
        for edge, label in cls._EDGES:
            if seconds < edge:
                return label
        return "ge_1h"


class Version(Domain):
    """This package's own version, and nothing else.

    Not "a version-shaped string": the only version worth reporting is the one
    running, and accepting any `\\d+.\\d+.\\d+` would let a caller pass a
    dependency's version -- or a number derived from user data.
    """

    kind = "version"

    def accepts(self, value: Any) -> bool:
        from openai4s import __version__

        return isinstance(value, str) and value == __version__


class OpaqueId(Domain):
    """A locally generated 32-hex install id. The only opaque field there is."""

    kind = "opaque_id"

    def accepts(self, value: Any) -> bool:
        if not isinstance(value, str) or len(value) != 32:
            return False
        return all(c in "0123456789abcdef" for c in value)


#: The complete set of domain kinds. Frozen by a test: a new kind is a privacy
#: decision and has to be made deliberately.
DOMAIN_KINDS = frozenset({"enum", "count", "bucket", "version", "opaque_id"})


# ---------------------------------------------------------------------------
# the declaration
# ---------------------------------------------------------------------------

#: Error classes worth counting, written out. Deliberately NOT
#: `type(e).__name__`: a kernel cell runs agent-authored code, so a class name
#: is user-authored text -- `class Cohort4471NonResponder(Exception)` reports a
#: research subject. Anything not on this list becomes "other".
ERROR_TYPES = Enum(
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "FileNotFoundError",
    "PermissionError",
    "OSError",
    "RuntimeError",
    "NotImplementedError",
    "TimeoutError",
    "ConnectionError",
    "MemoryError",
    "RecursionError",
    "SyntaxError",
    "IndentationError",
    "ZeroDivisionError",
    "StopIteration",
    "AssertionError",
    "UnicodeDecodeError",
    "JSONDecodeError",
    "KeyboardInterrupt",
    "other",
)

EVENTS = Enum(
    "daemon_start",
    "session_start",
    "turn_complete",
    "cell_execute",
    "kernel_start",
    "kernel_restart",
    "skill_load",
    "tool_call",
    "artifact_write",
    "delegation",
    "compute_job",
    "connector_call",
)

ENVELOPE: dict[str, Domain] = {
    "schema": Count(),
    "install_id": OpaqueId(),
    "app_version": Version(),
    "os": Enum("darwin", "linux", "windows", "other"),
    "arch": Enum("arm64", "x86_64", "other"),
    "python": Enum("3.10", "3.11", "3.12", "3.13", "other"),
}

RECORD: dict[str, Domain] = {
    "event": EVENTS,
    "outcome": Enum("ok", "error", "cancelled", "timeout", "denied"),
    "count": Count(),
    "duration_bucket": Bucket(),
    "error_type": ERROR_TYPES,
    "language": Enum("python", "r", "none"),
    "surface": Enum("cli", "web"),
    "skill_source": Enum("bundled", "user", "none"),
    "tool_family": Enum(
        "files",
        "web",
        "science",
        "skills",
        "artifacts",
        "data",
        "memory",
        "mcp",
        "none",
    ),
}


def classify_error(exc: BaseException | type[BaseException] | None) -> str:
    """The one permitted way to turn an exception into a telemetry value.

    Membership, never passthrough. An unrecognised class is "other" rather
    than its name -- because its name is the thing being kept off the wire.
    """
    if exc is None:
        return "other"
    cls = exc if isinstance(exc, type) else type(exc)
    name = getattr(cls, "__name__", "")
    return name if name in ERROR_TYPES.members else "other"


def sanitise_record(record: dict[str, Any]) -> dict[str, Any]:
    """Keep only declared fields holding in-domain values.

    Unknown keys are dropped, not rejected: a caller that adds a field is a
    caller whose field does not travel, which is the safe direction. The
    authoring-time gates are what make the omission visible.
    """
    clean: dict[str, Any] = {}
    for key, value in record.items():
        domain = RECORD.get(key)
        if domain is None:
            continue
        coerced = domain.coerce(value)
        if coerced is not None:
            clean[key] = coerced
    return clean


def sanitise_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in envelope.items():
        domain = ENVELOPE.get(key)
        if domain is None:
            continue
        coerced = domain.coerce(value)
        if coerced is not None:
            clean[key] = coerced
    return clean


__all__ = [
    "Bucket",
    "Count",
    "DOMAIN_KINDS",
    "Domain",
    "ENVELOPE",
    "ERROR_TYPES",
    "EVENTS",
    "Enum",
    "MAX_COUNT",
    "MAX_RECORDS",
    "OpaqueId",
    "RECORD",
    "SCHEMA_VERSION",
    "Version",
    "classify_error",
    "sanitise_envelope",
    "sanitise_record",
]
