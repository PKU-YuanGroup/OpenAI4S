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
  secrets live.
* **Payloads stay out.** Permission payloads and resolution contexts, raw
  compacted slices and delegated children's output text are not collected.
* **Collection never fails an export.** A section that cannot be read is
  recorded as unavailable, with its error category, and the export goes on.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import platform
import sys
import sysconfig
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

RUNTIME_SCHEMA_VERSION = 1

ENVIRONMENT_FILE = "runtime/environment.json"
FRAMES_FILE = "runtime/frames.json"
ACTIVITY_FILE = "runtime/activity.json"
HOST_CALLS_FILE = "runtime/host_calls.json"
PERMISSIONS_FILE = "runtime/permissions.json"
COMPACTIONS_FILE = "runtime/compactions.json"
MODEL_CALLS_FILE = "runtime/model_calls.json"
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
MAX_MODEL_CALLS = 20_000

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

    provider = str(getattr(llm_cfg, "provider", "") or "")
    spec = provider_spec(provider)
    base_url = str(getattr(llm_cfg, "base_url", "") or "") or str(spec["base_url"])
    model = str(getattr(llm_cfg, "model", "") or "") or str(spec["model"])
    wire = str(spec.get("wire") or "")
    stream_env = os.environ.get("OPENAI4S_LLM_STREAM", "1").strip().lower()
    facts: dict[str, Any] = {
        "provider": provider,
        "wire": wire,
        "model": model,
        "stream": bool(streams)
        and stream_env not in _STREAM_OFF
        and wire in _STREAMING_WIRES,
        "timeout_s": _number(getattr(llm_cfg, "timeout_s", None)),
        "total_timeout_s": _number(getattr(llm_cfg, "total_timeout_s", None)),
        "max_tokens": _number(getattr(llm_cfg, "max_tokens", None)),
        "temperature": _number(getattr(llm_cfg, "temperature", None)),
        "endpoint": endpoint_facts(base_url, default_url=str(spec["base_url"])),
    }
    capabilities = get_model_capabilities(provider, model, base_url=base_url)
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
        relative = path.relative_to(_PACKAGE_ROOT)
        where = relative.as_posix()
    except ValueError:
        try:
            path.relative_to(_STDLIB_ROOT)
            where = f"<stdlib>/{path.name}"
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
            "error",
            "stop_reason",
            "task_status",
            "created_at",
            "started_at",
            "finished_at",
            "request_id",
            "attempt_id",
            "progress",
            "overrides",
        )
        if key in child
    }
    steering = child.get("steering")
    if isinstance(steering, Mapping):
        row["steering"] = {
            key: steering.get(key) for key in ("queued", "delivered", "discarded")
        }
    return row


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
    cfg: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``{package path: document}`` plus the collection report."""

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
        for frame_id in children:
            groups = store.list_action_groups(frame_id)
            groups_total += len(groups)
            if not groups or groups_seen >= MAX_CHILD_GROUPS:
                continue
            kept = groups[-(MAX_CHILD_GROUPS - groups_seen) :]
            groups_seen += len(kept)
            ledgers.append(
                {
                    "frame_id": frame_id,
                    "groups": [safe_group(group) for group in kept],
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
            "truncated": len(rows) > MAX_FRAMES or groups_seen < groups_total,
        }

    def activity() -> tuple[Any, dict[str, Any]]:
        budget = MAX_ACTIVITY_STEPS
        total = 0
        frames_out = []
        for frame_id in [root_frame_id, *child_ids()]:
            steps = store.list_steps_for_export(frame_id)
            total += len(steps)
            if not steps or budget <= 0:
                continue
            kept = steps[-budget:]
            budget -= len(kept)
            frames_out.append(
                {
                    "frame_id": frame_id,
                    "steps": kept,
                    "truncated": len(kept) < len(steps),
                }
            )
        kept_total = MAX_ACTIVITY_STEPS - budget
        return {"frames": frames_out}, {
            "records": kept_total,
            "total": total,
            "truncated": kept_total < total,
        }

    def host_calls() -> tuple[Any, dict[str, Any]]:
        totals = store.session_host_call_totals(root_frame_id)
        total = sum(int(item.get("calls") or 0) for item in totals)
        calls = store.list_session_host_calls(root_frame_id, limit=MAX_HOST_CALLS)
        return {
            "totals": totals,
            "total": total,
            "calls": calls,
            "truncated": len(calls) < total,
        }, {"records": len(calls), "total": total, "truncated": len(calls) < total}

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

    def model_calls() -> tuple[Any, dict[str, Any]]:
        reader = getattr(store, "list_session_model_calls", None)
        if not callable(reader):
            raise LookupError("model call telemetry is not recorded by this store")
        total, calls = reader(root_frame_id, limit=MAX_MODEL_CALLS)
        return {
            "total": total,
            "calls": calls,
            "truncated": len(calls) < total,
        }, {"records": len(calls), "total": total, "truncated": len(calls) < total}

    run("environment", ENVIRONMENT_FILE, environment)
    run("frames", FRAMES_FILE, frames)
    run("activity", ACTIVITY_FILE, activity)
    run("host_calls", HOST_CALLS_FILE, host_calls)
    run("permissions", PERMISSIONS_FILE, permissions)
    run("compactions", COMPACTIONS_FILE, compactions)
    run("model_calls", MODEL_CALLS_FILE, model_calls)
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
    "DIAGNOSIS_FILE",
    "DIAGNOSTICS_FILE",
    "ENVIRONMENT_FILE",
    "FRAMES_FILE",
    "HOST_CALLS_FILE",
    "MODEL_CALLS_FILE",
    "PERMISSIONS_FILE",
    "RUNTIME_SCHEMA_VERSION",
    "agent_facts",
    "collect_runtime_documents",
    "endpoint_facts",
    "failure_evidence",
    "llm_facts",
    "process_facts",
    "runtime_facts_for",
    "session_capability_receipt",
]
