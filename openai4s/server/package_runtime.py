"""Runtime evidence carried by a Session package.

A Session package always carried what a session *said and produced*: the
conversation, the Action Ledger, the Notebook, Artifacts and their lineage. It
did not carry what the runtime *did* around that, and every report that
arrived as a package still had to be finished from somebody's SQLite file:

* a delegated child's ledger is rooted at the child's own frame id, so its
  replies, tool calls and stop reason never reached ``ledger.json``;
* only review cards travelled, so an imported session showed none of the
  Writing / Saving / Running cards the user actually watched;
* ``host_call_log`` -- the trail the #174 reporter rebuilt by hand -- stayed
  behind, as did permission requests and compaction archives;
* nothing said which version, platform, model endpoint, streaming mode or
  timeouts the session ran under, or which capabilities the daemon resolved
  for that endpoint (a local endpoint silently loses native tool calls).

This module collects those records into optional ``runtime/*.json`` members.
They are listed in the manifest like every other file, so a schema-v1 importer
hash-checks and secret-scans them and then ignores them; nothing here changes
what an import restores. ``openai4s/package_diagnosis.py`` reads them.

Boundaries, stated because they are the reason for most of the shape below:

* **No URL, no credential.** A model endpoint is reduced to a class
  (loopback/private/public/hostname), whether it is the provider's default,
  and a truncated SHA-256 fingerprint of its origin. Relay host names are
  private to the people who run them.
* **No raw exception text.** A failure is recorded as its type chain, stable
  codes, flags, and the code locations it passed through inside this
  package -- never ``str(exc)``, which is where paths, argv and echoed
  secrets live. What the database already holds as ``str(exc)`` -- a
  delegated child's ``error``, a compute job's ``reason`` -- travels as a
  kind, a length and a fingerprint; a child's endpoint override as endpoint
  facts.
* **Payloads stay out.** Permission payloads and resolution contexts, raw
  compacted slices and delegated children's output text are not collected.
  An activity card, host-call preview or child-ledger entry that names a file
  the artifact filter refuses by name (``.env``, ``credentials.json``,
  ``*.pem`` ...) travels with its payload withheld: refusing the file while
  shipping what was written to it or read from it would refuse nothing.
* **Collection never fails an export.** A section that cannot be read is
  recorded as unavailable, with its error category, and the export goes on.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import platform
import re
import sys
import sysconfig
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

RUNTIME_SCHEMA_VERSION = 1

ENVIRONMENT_FILE = "runtime/environment.json"
FRAMES_FILE = "runtime/frames.json"
ACTIVITY_FILE = "runtime/activity.json"
HOST_CALLS_FILE = "runtime/host_calls.json"
PERMISSIONS_FILE = "runtime/permissions.json"
COMPACTIONS_FILE = "runtime/compactions.json"
COMPUTE_JOBS_FILE = "runtime/compute_jobs.json"
COLLECTION_FILE = "runtime/collection.json"
DIAGNOSIS_FILE = "runtime/diagnosis.json"
DIAGNOSTICS_FILE = "DIAGNOSTICS.md"

#: Bounds. A long run keeps its *newest* records: a report is about how a
#: session ended, and the calls that led into a failure are the ones to keep.
MAX_FRAMES = 2_000
MAX_CHILD_GROUPS = 20_000
MAX_ACTIVITY_STEPS = 25_000
MAX_HOST_CALLS = 20_000
MAX_PERMISSION_REQUESTS = 5_000
MAX_COMPACTIONS = 1_000
MAX_COMPUTE_JOBS = 500
MAX_COMPUTE_EVENTS = 200

#: Where a failure's code locations are kept from. Frames outside this package
#: are reduced to a base name so no absolute path is recorded.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_STDLIB_ROOT = Path(sysconfig.get_paths().get("stdlib") or "").resolve()
_MAX_CHAIN = 6
_MAX_WHERE = 8

_STREAM_OFF = frozenset({"0", "false", "no", "off"})
#: Wires whose adapters stream. Gemini has none; Responses is SSE-only.
_STREAMING_WIRES = frozenset({"openai", "anthropic", "responses"})

#: Posture knobs worth knowing before debugging one. Values are echoed only
#: when they belong to this vocabulary; anything else is reported as set.
_POSTURE_ENV = (
    ("kernel_sandbox", "OPENAI4S_KERNEL_SANDBOX"),
    ("egress", "OPENAI4S_EGRESS"),
    ("allow_network", "OPENAI4S_ALLOW_NETWORK"),
    ("secret_store", "OPENAI4S_SECRET_STORE"),
    ("unattended_approval", "OPENAI4S_UNATTENDED_APPROVAL"),
    ("notebook_repl", "OPENAI4S_NOTEBOOK_REPL"),
    ("structured_logs", "OPENAI4S_STRUCTURED_LOGS"),
    ("webui", "OPENAI4S_WEBUI"),
    ("provenance_off", "OPENAI4S_PROVENANCE_OFF"),
    ("llm_stream", "OPENAI4S_LLM_STREAM"),
    ("default_env", "OPENAI4S_DEFAULT_ENV"),
)
_POSTURE_VALUES = frozenset(
    {
        "auto",
        "enforce",
        "off",
        "on",
        "ask",
        "deny",
        "allow",
        "allowlist",
        "keychain",
        "plaintext",
        "env",
        "memory",
        "legacy",
        "0",
        "1",
        "true",
        "false",
        "yes",
        "no",
    }
)


# ------------------------------------------------------------------ facts
def _numeric_release(value: str) -> str:
    """The leading numeric components of a kernel release, and nothing else.

    ``6.6.87.2-microsoft-standard-WSL2`` becomes ``6.6.87.2``; the WSL fact it
    carried is reported as its own boolean.
    """

    parts: list[str] = []
    for chunk in str(value or "").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(digits)
        if digits != chunk:
            break
    return ".".join(parts)


def _posture() -> dict[str, str]:
    report: dict[str, str] = {}
    for name, variable in _POSTURE_ENV:
        raw = os.environ.get(variable)
        if raw is None:
            report[name] = "(default)"
            continue
        value = raw.strip().lower()
        report[name] = value if value in _POSTURE_VALUES else "(set)"
    return report


def _channel(cfg: Any) -> str:
    try:
        from openai4s.update.channel import detect

        return str(detect(cfg).id)
    except Exception:  # noqa: BLE001 - an unidentifiable install is a finding
        return "unknown"


def _version() -> str:
    try:
        from openai4s import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "unknown"


def process_facts(cfg: Any = None) -> dict[str, Any]:
    """Facts about the daemon process that exported the package."""

    try:
        from openai4s.security.wsl import is_wsl

        wsl = bool(is_wsl())
    except Exception:  # noqa: BLE001
        wsl = False
    channel = _channel(cfg)
    return {
        "openai4s": {"version": _version(), "channel": channel},
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": _numeric_release(platform.release()),
            "machine": platform.machine(),
            "wsl": wsl,
            "container": channel == "container",
        },
        "posture": _posture(),
    }


def _host_class(host: str) -> str:
    host = host.lower().rstrip(".")
    if not host:
        return "invalid"
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    if host.endswith(".local") or host == "host.docker.internal":
        return "private"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "hostname"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_private:
        return "private"
    return "public_ip"


def endpoint_facts(base_url: str, *, default_url: str = "") -> dict[str, Any]:
    """A model endpoint as a class and a fingerprint -- never the URL."""

    def normalize(value: str) -> str:
        return str(value or "").strip().rstrip("/")

    try:
        parsed = urlsplit(normalize(base_url))
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return {"class": "invalid"}
    origin = f"{parsed.scheme}://{host}:{port or ''}"
    return {
        "class": _host_class(host),
        "scheme": parsed.scheme if parsed.scheme in {"http", "https"} else "other",
        "provider_default": bool(default_url)
        and normalize(base_url) == normalize(default_url),
        "fingerprint": hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16],
        "custom_path": bool(parsed.path.strip("/")),
    }


def llm_facts(
    llm_cfg: Any,
    *,
    streams: bool = True,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The model configuration a turn would be dispatched under, key-free.

    ``streams`` is whether the caller's loop asks for deltas at all (the Web
    loop does; the CLI never streams). The resolved capabilities are the ones
    ``chat()`` itself applies: a local endpoint turns native tool calls off
    unless an override or a probe receipt turns them back on.
    """

    from openai4s.llm.capabilities import get_model_capabilities
    from openai4s.llm.registry import provider_spec
    from openai4s.server.errors import safe_type_name

    provider = str(getattr(llm_cfg, "provider", "") or "")
    facts: dict[str, Any] = {
        "provider": provider,
        "model": str(getattr(llm_cfg, "model", "") or ""),
        "timeout_s": _number(getattr(llm_cfg, "timeout_s", None)),
        "total_timeout_s": _number(getattr(llm_cfg, "total_timeout_s", None)),
        "max_tokens": _number(getattr(llm_cfg, "max_tokens", None)),
        "temperature": _number(getattr(llm_cfg, "temperature", None)),
    }
    try:
        spec = provider_spec(provider)
    except Exception as error:  # noqa: BLE001 - an unknown provider is a finding
        # What the configuration says is still worth having; only what the
        # registry would have added is missing, and the reason says why.
        facts["resolution"] = {"status": "unavailable", "reason": safe_type_name(error)}
        return facts
    base_url = str(getattr(llm_cfg, "base_url", "") or "") or str(spec["base_url"])
    model = facts["model"] or str(spec["model"])
    wire = str(spec.get("wire") or "")
    stream_env = os.environ.get("OPENAI4S_LLM_STREAM", "1").strip().lower()
    facts.update(
        {
            "wire": wire,
            "model": model,
            "stream": bool(streams)
            and stream_env not in _STREAM_OFF
            and wire in _STREAMING_WIRES,
            "endpoint": endpoint_facts(base_url, default_url=str(spec["base_url"])),
        }
    )
    try:
        capabilities = get_model_capabilities(provider, model, base_url=base_url)
    except Exception as error:  # noqa: BLE001
        facts["resolution"] = {"status": "unavailable", "reason": safe_type_name(error)}
        return facts
    facts["capabilities"] = {
        key: getattr(capabilities, key)
        for key in (
            "tool_calling",
            "parallel_tool_calls",
            "strict_tool_schema",
            "streaming",
            "vision",
            "reasoning",
            "context_window_tokens",
            "max_output_tokens",
            "local_endpoint",
            "custom_endpoint",
        )
    }
    if isinstance(receipt, Mapping):
        facts["capability_receipt"] = {
            key: receipt.get(key)
            for key in (
                "revision",
                "wire",
                "probe_version",
                "reachable",
                "native_tool_call",
                "streaming",
                "context_window_tokens",
                "max_output_tokens",
                "observed_at",
                "stale",
                "native_completion",
            )
            if key in receipt
        }
    return facts


def agent_facts(cfg: Any) -> dict[str, Any]:
    """The loop limits that decide when a turn stops on its own."""

    return {
        key: _number(getattr(cfg, key, None))
        for key in (
            "max_turns",
            "explore_max_turns",
            "context_window_tokens",
            "compaction_trigger_ratio",
        )
    } | {"notebook_repl": bool(getattr(cfg, "notebook_repl", False))}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and value != value:
        return None
    return value


# ---------------------------------------------------------------- failures
def _type_label(error: BaseException) -> str:
    """A class name, kept only for this package's, builtins and the stdlib."""

    from openai4s.server.errors import safe_type_name

    try:
        kind = type(error)
        module = kind.__module__
        name = kind.__qualname__
    except Exception:  # noqa: BLE001
        return safe_type_name(error)
    if type(module) is not str or type(name) is not str:
        return safe_type_name(error)
    top = module.split(".", 1)[0]
    if module == "builtins":
        return name
    if top == "openai4s" or top in getattr(sys, "stdlib_module_names", ()):
        return f"{module}.{name}"
    return safe_type_name(error)


def _location(frame: traceback.FrameSummary) -> str:
    try:
        path = Path(frame.filename).resolve()
    except (OSError, ValueError):
        return f"<unknown>:{frame.lineno}"
    try:
        where = "openai4s/" + path.relative_to(_PACKAGE_ROOT).as_posix()
    except ValueError:
        try:
            where = "<stdlib>/" + path.relative_to(_STDLIB_ROOT).as_posix()
        except ValueError:
            where = f"<external>/{path.name}"
    return f"{where}:{frame.lineno}:{frame.name}"


def _locations(tb: Any, limit: int) -> list[str]:
    try:
        frames = traceback.extract_tb(tb)
    except Exception:  # noqa: BLE001
        return []
    return [_location(frame) for frame in frames[-limit:]]


def failure_evidence(error: BaseException) -> dict[str, Any]:
    """What a failed turn's terminal event records about the exception.

    Enough to place the failure -- its type chain, the stable codes and flags
    the LLM layer sets deliberately, and where it passed through this package
    -- and correlate it: ``error_class`` is the same fingerprint the daemon log
    and the diagnostics bundle carry. Never the message text.

    Total by construction: it runs inside the handler that writes the turn's
    terminal record, and evidence that raised there would cost the record.
    """

    try:
        return _failure_evidence(error)
    except Exception:  # noqa: BLE001
        return {"status": "unavailable"}


def _failure_evidence(error: BaseException) -> dict[str, Any]:
    from openai4s.llm.models import llm_failure_code
    from openai4s.server.errors import error_class, safe_type_name

    detail: dict[str, Any] = {
        "category": safe_type_name(error),
        "error_class": error_class(error),
    }
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    root = error
    while current is not None and len(chain) < _MAX_CHAIN and id(current) not in seen:
        seen.add(id(current))
        chain.append(_type_label(current))
        root = current
        nxt = current.__cause__
        if nxt is None and not current.__suppress_context__:
            nxt = current.__context__
        current = nxt
    detail["chain"] = chain
    code = llm_failure_code(error)
    if code:
        detail["code"] = code
    status = getattr(error, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        detail["status"] = status
    for flag in ("output_committed", "retryable", "llm_not_started"):
        value = getattr(error, flag, None)
        if isinstance(value, bool):
            detail[flag] = value
    where = _locations(error.__traceback__, _MAX_WHERE)
    if where:
        detail["where"] = where
    if root is not error:
        root_where = _locations(root.__traceback__, 4)
        if root_where:
            detail["root_where"] = root_where
    call = getattr(error, "call_telemetry", None)
    if isinstance(call, Mapping):
        detail["call"] = {
            key: value
            for key, value in call.items()
            if isinstance(key, str)
            and (value is None or isinstance(value, (bool, int, float, str)))
        }
    return detail


# ------------------------------------------------------------- collection
def _frame_row(frame: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: frame.get(key)
        for key in (
            "frame_id",
            "parent_id",
            "kind",
            "name",
            "status",
            "model",
            "effort",
            "depth",
            "runtime_env",
            "model_profile_id",
            "model_profile_revision",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "created_at",
            "updated_at",
        )
        if key in frame
    }


#: A stored failure string's leading LLM prefix or exception type. Only that
#: kind survives: the rest of a `str(exc)` is where provider bodies, relay URLs
#: (userinfo included), host names and paths live.
_ERROR_KIND = re.compile(
    r"^\s*(?:(LLM HTTP) (\d{3})|(LLM connection error)|"
    r"(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*(?:Error|Exception|Timeout|Interrupt|Exit"
    r"|Failure)))\b"
)
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def error_summary(text: Any) -> dict[str, Any] | None:
    """A free-text failure as a kind, its length and a fingerprint -- never text.

    A delegated child's `error` and a compute job's `reason` are recorded as
    `str(exc)`. The fingerprint still lets two reports of one failure match.
    """

    if not isinstance(text, str) or not text.strip():
        return None
    match = _ERROR_KIND.match(text)
    kind = "text"
    if match and match.group(1):
        kind = f"llm_http_{match.group(2)}"
    elif match and match.group(3):
        kind = "llm_connection_error"
    elif match:
        kind = match.group(4)
    return {
        "kind": kind,
        "length": len(text),
        "fingerprint": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12],
    }


def _code_or_text(value: Any) -> Any:
    """A recorded code, or ``"text"`` where free text was recorded instead."""

    if value is None or (isinstance(value, str) and _CODE.fullmatch(value)):
        return value
    return "text"


def _portable_overrides(value: Any, depth: int = 0) -> Any:
    """A child's override spec with every endpoint reduced to endpoint facts.

    A parent may point a child at its own model endpoint (`model.base_url`);
    the package reports endpoints as a class and a fingerprint, never a URL.
    """

    if depth > 16:
        return None
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key == "base_url" and isinstance(item, str):
                out["endpoint"] = endpoint_facts(item)
            else:
                out[key] = _portable_overrides(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_portable_overrides(item, depth + 1) for item in value]
    if isinstance(value, str) and "://" in value:
        return endpoint_facts(value)
    return value


def _child_row(child: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        key: child.get(key)
        for key in (
            "child_id",
            "name",
            "status",
            "depth",
            "parent_child_id",
            "parent_frame_id",
            "frame_id",
            "task_status",
            "created_at",
            "started_at",
            "finished_at",
            "request_id",
            "attempt_id",
            "progress",
        )
        if key in child
    }
    # The three fields that can carry free text or an endpoint, each reduced
    # the way the rest of this module reduces its own: a failure to its kind,
    # a stop to its code, an endpoint to its facts.
    if "error" in child:
        row["error"] = error_summary(child.get("error"))
    if "stop_reason" in child:
        row["stop_reason"] = _code_or_text(child.get("stop_reason"))
    if "overrides" in child:
        row["overrides"] = _portable_overrides(child.get("overrides"))
    steering = child.get("steering")
    if isinstance(steering, Mapping):
        row["steering"] = {
            key: steering.get(key) for key in ("queued", "delivered", "discarded")
        }
    return row


#: What separates a path from the text around it in a command, an argument
#: list or a JSON preview. Each token is then judged by its path components.
_PATH_TOKEN_SPLIT = re.compile(r"[\s\"'`,;()\[\]{}<>|&=]+")
_PATH_PARTS = re.compile(r"[\\/]")
WITHHELD = "names_a_secret_file"


def _secret_file_rules() -> tuple[frozenset[str], frozenset[str]]:
    """The artifact and workspace filter's own sets, so the three cannot drift.

    Read lazily: ``session_package`` imports this module.
    """

    from openai4s.server.session_package import _SECRET_NAMES, _SECRET_SUFFIXES

    return _SECRET_NAMES, _SECRET_SUFFIXES


def names_secret_file(text: str) -> bool:
    """Whether a string names a file the package refuses to carry as a file.

    Artifact and workspace export drop `.env`, `credentials.json`, `*.pem` and
    the rest by name, so the *content* of such a file must not travel through
    the evidence either: a Writing card holds up to 6,000 characters of what
    was written, a Reading card what was read, a `cat .env` card what it
    printed, and a host-call preview the first 500 characters of the call.
    Deliberately broad -- a false positive withholds one record's payload, a
    false negative ships a credential.
    """

    names, suffixes = _secret_file_rules()
    for token in _PATH_TOKEN_SPLIT.split(text[:65536].lower()):
        parts = [part for part in _PATH_PARTS.split(token) if part]
        if not parts:
            continue
        if any(part in names or part.startswith(".env.") for part in parts):
            return True
        if any(parts[-1].endswith(suffix) for suffix in suffixes):
            return True
    return False


def _mentions_secret_file(value: Any, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if isinstance(value, str):
        return names_secret_file(value)
    if isinstance(value, Mapping):
        return any(
            names_secret_file(str(key)) or _mentions_secret_file(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_mentions_secret_file(item, depth + 1) for item in value)
    return False


def _withhold_card(step: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    if not (
        _mentions_secret_file(step.get("input"))
        or _mentions_secret_file(step.get("output"))
    ):
        return dict(step), False
    return {
        **step,
        "input": {"withheld": WITHHELD},
        "output": {"withheld": WITHHELD},
    }, True


def _withhold_group(group: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """A child ledger group, with its content withheld if it names a secret file.

    Kinds, ids, timestamps, tool names, parse errors and the call telemetry
    stay, so the group still counts in the diagnosis.
    """

    if not _mentions_secret_file(
        {
            "assistant_content": group.get("assistant_content"),
            "assistant_message": group.get("assistant_message"),
            "events": [
                event
                for event in group.get("events") or ()
                if isinstance(event, Mapping) and event.get("type") != "model_call"
            ],
        }
    ):
        return dict(group), False
    events = []
    for event in group.get("events") or ():
        if not isinstance(event, Mapping) or event.get("type") == "model_call":
            events.append(event)
            continue
        arguments = event.get("canonical_arguments")
        kept = (
            {
                key: arguments.get(key)
                for key in ("name", "ordinal", "parse_error")
                if key in arguments
            }
            if isinstance(arguments, Mapping)
            else {}
        )
        events.append(
            {
                **event,
                "canonical_arguments": {**kept, "withheld": WITHHELD},
                "raw_arguments": None,
                "result": (
                    {
                        **{
                            key: event["result"].get(key)
                            for key in ("reason", "progress_reason", "is_error", "name")
                            if key in event["result"]
                        },
                        "withheld": WITHHELD,
                    }
                    if isinstance(event.get("result"), Mapping)
                    else None
                ),
            }
        )
    return {
        **group,
        "assistant_content": None,
        "assistant_message": {"withheld": WITHHELD},
        "wire_state": None,
        "events": events,
    }, True


_PERMISSION_FIELDS = (
    "decision_id",
    "frame_id",
    "action_group_id",
    "action_id",
    "tool_call_id",
    "tool",
    "target",
    "side_effect_class",
    "resource_keys",
    "dangerous",
    "state",
    "scope",
    "continuation_required",
    "continuation_expires_at",
    "continuation_consumed_at",
    "created_at",
    "expires_at",
    "resolved_at",
)

_COMPACTION_FIELDS = (
    "archive_id",
    "frame_id",
    "branch_id",
    "generation_id",
    "n_messages",
    "context_before",
    "context_after",
    "summary",
    "metadata",
    "created_at",
)


def collect_runtime_documents(
    store: Any,
    root_frame_id: str,
    *,
    facts: Mapping[str, Any] | None,
    scrub: Callable[[Any], Any],
    safe_group: Callable[[Mapping[str, Any]], dict[str, Any]],
    cell_frames: Mapping[str, str] | None = None,
    workspaces: Sequence[str] = (),
    cfg: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``{package path: document}`` plus the collection report.

    ``workspaces`` are the session's workspace paths: a remote compute job is
    owned by the workspace that submitted it, which is the only link from a
    job back to a session.
    """

    from openai4s.server.errors import safe_type_name

    documents: dict[str, Any] = {}
    sections: dict[str, dict[str, Any]] = {}

    def run(name: str, path: str, build: Callable[[], tuple[Any, dict[str, Any]]]):
        try:
            document, info = build()
        except Exception as error:  # noqa: BLE001 - evidence must not fail export
            sections[name] = {"status": "unavailable", "reason": safe_type_name(error)}
            return
        documents[path] = scrub({"schema_version": RUNTIME_SCHEMA_VERSION, **document})
        sections[name] = {"status": "ok", **info}

    frames_cache: list[dict[str, Any]] = []

    def session_frames() -> list[dict[str, Any]]:
        if not frames_cache:
            frames_cache.extend(store.list_session_frames(root_frame_id))
        return frames_cache

    def child_ids() -> list[str]:
        return [
            str(frame.get("frame_id"))
            for frame in session_frames()[:MAX_FRAMES]
            if frame.get("frame_id") and frame.get("frame_id") != root_frame_id
        ]

    def environment() -> tuple[Any, dict[str, Any]]:
        document = process_facts(cfg)
        for key, value in (facts or {}).items():
            document[key] = value
        return document, {}

    def frames() -> tuple[Any, dict[str, Any]]:
        rows = session_frames()
        children = child_ids()
        ledgers = []
        groups_seen = 0
        groups_total = 0
        withheld = 0
        for frame_id in children:
            groups = store.list_action_groups(frame_id)
            groups_total += len(groups)
            if not groups or groups_seen >= MAX_CHILD_GROUPS:
                continue
            kept = groups[-(MAX_CHILD_GROUPS - groups_seen) :]
            groups_seen += len(kept)
            safe = []
            for group in kept:
                projected, hidden = _withhold_group(safe_group(group))
                withheld += hidden
                safe.append(projected)
            ledgers.append(
                {
                    "frame_id": frame_id,
                    "groups": safe,
                    "truncated": len(kept) < len(groups),
                }
            )
        generations = []
        for frame_id in children:
            for generation in store.list_kernel_generations(frame_id):
                generations.append(dict(generation))
        tree = store.delegation_tree(root_frame_id) or {}
        delegation = {
            "initialized": bool(tree.get("initialized")),
            "budget": tree.get("budget"),
            "stats": tree.get("stats"),
            "children": [
                _child_row(child)
                for child in tree.get("children") or []
                if isinstance(child, Mapping)
            ],
        }
        document = {
            "root_frame_id": root_frame_id,
            "frames": [_frame_row(frame) for frame in rows[:MAX_FRAMES]],
            "delegation": delegation,
            "child_ledgers": ledgers,
            "child_generations": generations,
            "cell_frames": dict(sorted((cell_frames or {}).items())),
        }
        return document, {
            "records": len(rows[:MAX_FRAMES]),
            "total": len(rows),
            "child_groups": groups_seen,
            "child_groups_total": groups_total,
            "withheld": withheld,
            "truncated": len(rows) > MAX_FRAMES or groups_seen < groups_total,
        }

    def activity() -> tuple[Any, dict[str, Any]]:
        budget = MAX_ACTIVITY_STEPS
        total = 0
        withheld = 0
        frames_out = []
        for frame_id in [root_frame_id, *child_ids()]:
            steps = store.list_steps_for_export(frame_id)
            total += len(steps)
            if not steps or budget <= 0:
                continue
            kept = steps[-budget:]
            budget -= len(kept)
            cards = []
            for step in kept:
                card, hidden = _withhold_card(step)
                withheld += hidden
                cards.append(card)
            frames_out.append(
                {
                    "frame_id": frame_id,
                    "steps": cards,
                    "truncated": len(kept) < len(steps),
                }
            )
        kept_total = MAX_ACTIVITY_STEPS - budget
        return {"frames": frames_out}, {
            "records": kept_total,
            "total": total,
            "truncated": kept_total < total,
            "withheld": withheld,
        }

    def host_calls() -> tuple[Any, dict[str, Any]]:
        totals = store.session_host_call_totals(root_frame_id)
        total = sum(int(item.get("calls") or 0) for item in totals)
        calls = store.list_session_host_calls(root_frame_id, limit=MAX_HOST_CALLS)
        withheld = 0
        for call in calls:
            if names_secret_file(str(call.get("args_preview") or "")):
                call["args_preview"] = f"<withheld: {WITHHELD}>"
                withheld += 1
        return {
            "totals": totals,
            "total": total,
            "calls": calls,
            "truncated": len(calls) < total,
        }, {
            "records": len(calls),
            "total": total,
            "truncated": len(calls) < total,
            "withheld": withheld,
        }

    def permissions() -> tuple[Any, dict[str, Any]]:
        requests = store.list_permission_requests(root_frame_id=root_frame_id)
        kept = requests[-MAX_PERMISSION_REQUESTS:]
        return {
            "policy": "payloads and resolution contexts are never exported",
            "requests": [
                {key: item.get(key) for key in _PERMISSION_FIELDS if key in item}
                for item in kept
            ],
        }, {
            "records": len(kept),
            "total": len(requests),
            "truncated": len(kept) < len(requests),
        }

    def compactions() -> tuple[Any, dict[str, Any]]:
        archives: list[dict[str, Any]] = []
        for frame_id in [root_frame_id, *child_ids()]:
            for item in store.list_compaction_archives(frame_id, limit=500):
                archives.append(
                    {key: item.get(key) for key in _COMPACTION_FIELDS if key in item}
                )
        archives.sort(
            key=lambda item: (int(item.get("created_at") or 0), item.get("archive_id"))
        )
        kept = archives[-MAX_COMPACTIONS:]
        return {
            "policy": "the compacted message slices themselves are not exported",
            "archives": kept,
        }, {
            "records": len(kept),
            "total": len(archives),
            "truncated": len(kept) < len(archives),
        }

    def compute_jobs() -> tuple[Any, dict[str, Any]]:
        jobs: dict[str, Mapping[str, Any]] = {}
        for key in sorted({str(value) for value in workspaces if value}):
            for job in store.compute_jobs_for_owner(key, MAX_COMPUTE_JOBS):
                jobs[str(job.get("job_id"))] = job
        rows = []
        for job_id, job in sorted(
            jobs.items(),
            key=lambda item: (int(item[1].get("created_at") or 0), item[0]),
        )[-MAX_COMPUTE_JOBS:]:
            # `ssh:<alias>` / `byoc:<id>`: the kind is ours, the name is the
            # user's host alias, so only its fingerprint travels. Remote paths,
            # process ids and the provider's receipt stay behind.
            kind, _, name = str(job.get("provider") or "").partition(":")
            events = store.compute_job_events(job_id)
            rows.append(
                {
                    "job_id": job_id,
                    "provider_kind": kind or "unknown",
                    "provider_fingerprint": (
                        hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
                        if name
                        else None
                    ),
                    # `reason` is often `str(exc)` from an ssh or provider
                    # call; `termination_reason` is the closed vocabulary.
                    "reason": error_summary(job.get("reason")),
                    **{
                        key: job.get(key)
                        for key in (
                            "status",
                            "exit_code",
                            "termination_reason",
                            "created_at",
                            "submitted_at",
                            "terminal_at",
                            "updated_at",
                        )
                    },
                    "input_versions": len(job.get("input_versions") or []),
                    "events": [
                        {
                            "seq": event.get("seq"),
                            "kind": event.get("kind"),
                            "at": event.get("at"),
                        }
                        for event in events[-MAX_COMPUTE_EVENTS:]
                    ],
                    "events_total": len(events),
                }
            )
        return {"jobs": rows}, {"records": len(rows), "total": len(jobs)}

    run("environment", ENVIRONMENT_FILE, environment)
    run("frames", FRAMES_FILE, frames)
    run("activity", ACTIVITY_FILE, activity)
    run("host_calls", HOST_CALLS_FILE, host_calls)
    run("permissions", PERMISSIONS_FILE, permissions)
    run("compactions", COMPACTIONS_FILE, compactions)
    run("compute_jobs", COMPUTE_JOBS_FILE, compute_jobs)
    return documents, {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "sections": dict(sorted(sections.items())),
    }


def session_capability_receipt(
    store: Any, cfg: Any, root_frame_id: str
) -> Mapping[str, Any] | None:
    """The probe receipt of the profile this session resolves to, if any.

    The session's pinned profile when it has one, else the active profile --
    the same order ``_llm_cfg`` dispatches in. SQLite only.
    """

    from openai4s.llm.registry import PROVIDERS
    from openai4s.server.model_profiles import ModelProfileService

    frame = store.get_frame(root_frame_id) or {}
    profile_id = str(frame.get("model_profile_id") or "") or str(
        store.get_setting("active_model_profile") or ""
    )
    if not profile_id:
        return None
    profile = next(
        (item for item in store.list_model_profiles() if item.get("id") == profile_id),
        None,
    )
    if profile is None:
        return None
    return ModelProfileService(
        store, cfg, providers=lambda: PROVIDERS
    ).capability_receipt(profile)


def runtime_facts_for(
    cfg: Any,
    *,
    llm_config: Callable[[], Any],
    receipt: Callable[[], Mapping[str, Any] | None] | None = None,
    streams: bool = True,
) -> dict[str, Any]:
    """The daemon-side facts one export adds to ``runtime/environment.json``.

    Each part is resolved on its own, so a session whose pinned model can no
    longer be resolved still exports -- saying so -- rather than failing.
    """

    from openai4s.server.errors import safe_type_name

    facts: dict[str, Any] = {}
    try:
        facts["agent"] = agent_facts(cfg)
    except Exception as error:  # noqa: BLE001
        facts["agent"] = {"status": "unavailable", "reason": safe_type_name(error)}
    try:
        resolved = llm_config()
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "error_code", None)
        facts["llm"] = {
            "status": "unavailable",
            "reason": code if isinstance(code, str) and code else safe_type_name(error),
        }
        return facts
    receipt_value = None
    if receipt is not None:
        try:
            receipt_value = receipt()
        except Exception:  # noqa: BLE001 - a receipt is optional evidence
            receipt_value = None
    try:
        facts["llm"] = llm_facts(resolved, streams=streams, receipt=receipt_value)
    except Exception as error:  # noqa: BLE001
        facts["llm"] = {"status": "unavailable", "reason": safe_type_name(error)}
    return facts


__all__ = [
    "ACTIVITY_FILE",
    "COLLECTION_FILE",
    "COMPACTIONS_FILE",
    "COMPUTE_JOBS_FILE",
    "DIAGNOSIS_FILE",
    "DIAGNOSTICS_FILE",
    "ENVIRONMENT_FILE",
    "FRAMES_FILE",
    "HOST_CALLS_FILE",
    "PERMISSIONS_FILE",
    "RUNTIME_SCHEMA_VERSION",
    "agent_facts",
    "collect_runtime_documents",
    "endpoint_facts",
    "error_summary",
    "failure_evidence",
    "llm_facts",
    "names_secret_file",
    "process_facts",
    "runtime_facts_for",
    "session_capability_receipt",
]
