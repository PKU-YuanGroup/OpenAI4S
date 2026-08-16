"""openai4s gateway — full web UI + REST + WebSocket over the stdlib.

This is the merge layer: it serves the rich openai4s-local web UI (dashboard +
conversation + tabbed right dock + 3Dmol viewer + notebook) and backs it with the
hybrid AgentEngine (native control tools + persistent science kernels), host SDK,
and SQLite store.

  * Static UI          GET /            GET /static/*
  * REST API           /api/*           (projects, frames, messages, artifacts,
                                          execution-log, lineage, models, skills…)
  * WebSocket          GET /api/v1/ws   (view_session/ping ; text_reset/text_chunk/
                                          frame_update/artifact_created)

Each user message runs the shared AgentEngine against a session-scoped control
runtime; persistent Python/R kernels are acquired only for scientific Cells.
Prose streams as text chunks, code + output stream as tool chunks, and every
cell's figures / written files are captured as versioned artifacts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import queue
import re
import struct
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse

from openai4s import datapro, execution_principal, memory_budget
from openai4s.agent.actions import NO_NATIVE_COMPLETION_NUDGE
from openai4s.agent.engine import AgentEngine
from openai4s.agent.finalize import with_finalize_response
from openai4s.agent.ledger import (
    RuntimeActionLedger,
    new_turn_id,
    restore_action_history,
)
from openai4s.agent.loop import SYSTEM_PROMPT
from openai4s.agent.models import RunState
from openai4s.agent.runtime import ChatModel, CompactionPolicy, CompletionSignal
from openai4s.config import Config, get_config
from openai4s.execution import (
    CaptureResult,
    CellRequest,
    QueueDepthExceeded,
    WatchdogPolicy,
    execute_with_watchdog,
)
from openai4s.host_dispatch import build_dispatcher
from openai4s.kernel import Kernel, KernelLease, KernelSupervisor
from openai4s.llm import (
    PROVIDERS,
    chat,
    get_model_capabilities,
    llm_failure_code,
    provider_specs,
)
from openai4s.observability import (
    carry_context,
    correlation_id,
    log_event,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from openai4s.review import review_evidence
from openai4s.server import (
    artifact_refs,
    compute_session_routes,
    compute_tasks,
    contract,
    file_routes,
    governance_routes,
    kernel_routes,
    local_auth,
    orchestration_routes,
    retrieval_source,
    team_policy,
    team_routes,
    ws_frames,
)
from openai4s.server.action_timeline import ActionTimelineService
from openai4s.server.agent_run import EventCancellation
from openai4s.server.agent_run import ProseStreamer as _ProseStreamer
from openai4s.server.agent_run import WebActionExecutor, WebEventSink
from openai4s.server.artifacts import (
    ArtifactManager,
    ArtifactOperationError,
    PromotionTarget,
)
from openai4s.server.cell_run import CellExecutionPorts, CellExecutionService
from openai4s.server.completions import completion_message, response_language
from openai4s.server.errors import (
    ERROR_CODES,
    INTERNAL_ERROR_MESSAGE,
    GatewayError,
    error_code_for,
    gateway_error_payload,
    public_exception,
    public_failure,
    record_diagnostic,
)
from openai4s.server.execution_coordinator import (
    ExecutionCancelled,
    WebExecutionCoordinator,
)
from openai4s.server.execution_views import ExecutionViewService
from openai4s.server.global_views import GlobalResearchViewService
from openai4s.server.model_discovery import LocalModelDiscoveryService
from openai4s.server.model_profiles import ModelProfileError, ModelProfileService
from openai4s.server.model_profiles import clean_api_key as _clean_api_key
from openai4s.server.model_profiles import migrate_provider_alias
from openai4s.server.model_profiles import resolve_profile_key as _resolve_profile_key

# Keep the former gateway helper names as compatibility aliases; plan behavior
# itself now lives together in PlanService.
from openai4s.server.plans import PlanService
from openai4s.server.plans import extract_plan_json as _extract_plan_json
from openai4s.server.plans import normalize_plan as _normalize_plan
from openai4s.server.plans import public_plan as _plan_public
from openai4s.server.plans import short_hash as _short_hash
from openai4s.server.plans import slugify as _slugify
from openai4s.server.recovery_control import RecoveryActionError
from openai4s.server.recovery_runtime import (
    RecoveryRuntimePorts,
    SessionRecoveryRuntime,
    bootstrap_python_generation,
    bootstrap_r_generation,
    python_runtime_spec,
)
from openai4s.server.reviews import ReviewPorts, ReviewService
from openai4s.server.security_headers import security_headers
from openai4s.server.session_deletion import SessionDeletionService
from openai4s.server.session_domain import (
    CursorCheckpointUnavailable,
    SessionDomainService,
)
from openai4s.server.session_package import (
    MAX_ARCHIVE_BYTES,
    SessionPackageError,
    session_import_quarantine_key,
)
from openai4s.server.session_recovery import PROCESS_INSTANCE_ID, SessionRecoveryService
from openai4s.server.session_runtime import SessionRuntime
from openai4s.server.share_projection import ShareProjectionBuilder
from openai4s.server.share_router import ShareRouter
from openai4s.server.share_service import ShareConflict, ShareService
from openai4s.server.skill_sidecars import GenerationSidecarRecorder
from openai4s.server.skills import SKILL_FAILURE_STATUS, SkillCustomizationService
from openai4s.server.titles import SessionTitleService
from openai4s.server.variable_inspector import VariableInspectorService
from openai4s.server.workbench_state import SessionWorkbenchStateService
from openai4s.skills_loader import SkillLoader
from openai4s.storage.connectors import public_connector
from openai4s.storage.governance import QuotaExceeded
from openai4s.storage.memories import ALL_PROJECTS as MEMORY_ALL_PROJECTS
from openai4s.storage.memories import GLOBAL_SCOPE as MEMORY_GLOBAL_SCOPE
from openai4s.storage.memories import MemoryLimitError
from openai4s.store import Store, get_store
from openai4s.tools import control_tool_specs, get_tool

os.environ.setdefault("MPLBACKEND", "Agg")  # headless matplotlib for figure capture

WEBUI_DIR = Path(__file__).resolve().parent / "webui"
_SHARE_ASSET_DIR = WEBUI_DIR / "share"
# Files the read-only share viewer is allowed to serve from memory (loaded once).
_SHARE_ASSET_NAMES = (
    "share.html",
    "share.js",
    "share.css",
    "scientific_renderers.js",
    "vendor/3Dmol-min.js",
)


def _load_share_assets() -> dict[str, bytes]:
    """Load the static viewer assets into memory once at startup.

    Viewer JS/CSS live under ``webui/share/``; ``scientific_renderers.js`` and
    the vendored 3Dmol bundle are reused from ``webui/``.
    """

    assets: dict[str, bytes] = {}
    for name in _SHARE_ASSET_NAMES:
        for base in (_SHARE_ASSET_DIR, WEBUI_DIR):
            candidate = base / name
            if candidate.is_file():
                try:
                    assets[name] = candidate.read_bytes()
                except OSError:
                    pass
                break
    return assets


def _share_expires_at(body: dict) -> tuple[bool, int | None]:
    """Map a share request body's ``expires_in`` (seconds) to an epoch-ms expiry.

    Returns ``(present, expires_at)``: ``present`` is True when the caller sent an
    ``expires_in`` key at all (so an update can distinguish "clear it" from
    "leave it"); a value of 0/null/negative means no expiry.
    """

    if "expires_in" not in body:
        return False, None
    raw = body.get("expires_in")
    try:
        seconds = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return True, None
    if seconds <= 0:
        return True, None
    return True, int(time.time() * 1000) + seconds * 1000


_WATCHDOG_INTERRUPT_GRACE_S = 10.0
_WATCHDOG_KILL_GRACE_S = 10.0


# Re-exported from openai4s.server.errors, which owns them so that route
# modules can raise GatewayError without importing this file (that import is a
# cycle: GatewayError sat ~5,800 lines below gateway's own imports, so a sibling
# importing it failed the daemon at boot).
#: Re-exported so the gate and the CLI cannot disagree about the spelling.
_TOKEN_HEADER = local_auth.TOKEN_HEADER


def _strip_token_from_url(path: str, query: str) -> str:
    """The same URL without the `token` parameter.

    Only the credential is dropped, not the whole query string: the bootstrap
    URL may carry the caller's own parameters alongside the token, and
    discarding them would silently rewrite where the page thinks it was opened.
    The entire point of the redirect is that the address bar, the history entry
    and every later Referer hold a URL with no secret in it, so anything that
    leaves `token` behind here defeats it.
    """
    remaining = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key != "token"
    ]
    if not remaining:
        return path or "/"
    return f"{path or '/'}?{urlencode(remaining)}"


_ERROR_CODES = ERROR_CODES
_error_code_for = error_code_for
_public_failure = public_failure

#: Hard ceiling on one message page. The route is walked page by page by a
#: client now, so an unbounded ``limit`` is an invitation to project an entire
#: branch -- the whole conversation, in one response -- from a query string.
#: 1000 is well past any page the UI asks for and is still a bound.
MAX_MESSAGE_PAGE = 1000


def _encode_frame_cursor(created_at: int, frame_id: str) -> str:
    """Opaque cursor. Opaque on purpose: a client that parses it becomes
    coupled to the sort key, and the key could not then be changed without
    breaking it."""
    raw = f"{int(created_at)}:{frame_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_frame_cursor(value: str | None) -> tuple[int, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        created, _, frame_id = (
            base64.urlsafe_b64decode(padded).decode("utf-8").partition(":")
        )
        if not frame_id:
            raise ValueError("missing frame id")
        return (int(created), frame_id)
    except Exception as e:  # noqa: BLE001
        # A cursor we cannot read must not silently become "start from the
        # beginning" — the client would loop over page one forever.
        raise GatewayError(400, f"invalid cursor: {e}", "invalid_cursor")


# One definition, in `contract.py`, so the prefix the gateway routes on and the
# prefix the CLI builds daemon URLs from cannot drift apart. They had: every
# `openai4s share` subcommand hard-coded "/api/" and 404'd.
_API_ROOT = contract.API_ROOT

#: Reachable without a credential. `/health` is a liveness probe. `/auth/status`
#: joins it because a client cannot be told it needs a token by a response it
#: is not allowed to read -- and the route answers with a mode string only,
#: never with any part of the token.
_UNAUTHENTICATED_PATHS = frozenset({"/health", _API_ROOT + "/auth/status"})

#: The release by which `OPENAI4S_REQUIRE_TOKEN=0` must be gone.
#:
#: "Kept for one minor release" was written in a comment below and restated in
#: three docs, and none of the four said *which* release or would ever notice
#: the deadline passing. The variable turns off the only credential check in
#: front of `kernel/execute`, `compute/jobs` and `host.bash`, so an escape hatch
#: that quietly becomes permanent is the entire cost of the decision arriving
#: without the deadline it was granted on. `tests/test_auth_exit_matrix.py`
#: fails once `openai4s.__version__` reaches this, which puts the decision in
#: front of a person instead of leaving it to nobody's memory.
LEGACY_TOKEN_OPT_OUT_REMOVED_IN = "0.2.0"


def _wants_html(headers) -> bool:
    """Is this a person in a browser, or a script?

    Browsers send `text/html` first in Accept; `curl`, `fetch` and every SDK do
    not. Getting this wrong in the permissive direction only means a script
    receives a readable page instead of a JSON error, so it errs toward HTML
    only on an explicit html preference.
    """
    accept = str(headers.get("Accept", "") or "")
    return "text/html" in accept


def _unauthorized_page() -> bytes:
    """What a first-run user sees instead of `{"error": "unauthorized"}`.

    No token in it, and nothing fetched: the page is the whole recovery path,
    because every asset it could load is behind the same gate.
    """
    return (
        "<!doctype html><meta charset=utf-8>"
        "<title>OpenAI4S — access token required</title>"
        "<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;"
        "max-width:34rem;margin:12vh auto;padding:0 1.5rem;color:#222}"
        "code{background:#f4f4f5;padding:.15em .4em;border-radius:4px;"
        "font:13px ui-monospace,SFMono-Regular,Menlo,monospace}"
        "p{margin:.9em 0}@media(prefers-color-scheme:dark){body{background:#18181b;"
        "color:#e4e4e7}code{background:#27272a}}</style>"
        "<h1>Access token required</h1>"
        "<p>This daemon can execute code, so it does not answer without a "
        "credential \u2014 even on this machine.</p>"
        "<p>Run this in a terminal and open the URL it prints:</p>"
        "<p><code>openai4s url</code></p>"
        "<p>The same URL is printed on startup. Opening it once sets a cookie "
        "for this browser; you will not need it again.</p>"
    ).encode("utf-8")


_API_PREFIX = _API_ROOT + "/"
_API_WS = _API_ROOT + "/ws"
_MAX_JSON_BODY_BYTES = MAX_ARCHIVE_BYTES

#: One chat message. Deliberately the same number as ``MAX_REF_BYTES``: a
#: message a person types or pastes may be as large as one referenced file and
#: no larger, because anything bigger belongs on disk where the agent can read
#: the part it needs instead of carrying all of it in every later prompt.
MAX_MESSAGE_CHARS = 200_000

#: How much of a queued message the FIFO projection repeats back. A queue entry
#: has to be recognisable -- "which of the three did I want to drop" is the only
#: question a cancel control ever answers -- but the queue snapshot is broadcast
#: to every subscriber of the session on every queue change, so carrying the
#: whole 200,000-character message there would multiply it across the wire.
QUEUE_PREVIEW_CHARS = 160


def queue_preview(text: str) -> str:
    """One line of a queued message, short enough to broadcast repeatedly."""

    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= QUEUE_PREVIEW_CHARS:
        return collapsed
    return collapsed[: QUEUE_PREVIEW_CHARS - 1] + "…"


def _skill_result_status(payload: object) -> int:
    """The status a Customize skill result should be answered with.

    These routes answered 200 with an ``{"error": ...}`` body. The service
    returns soft dictionaries by design -- see ``server/skills.py`` -- but the
    *gateway* is where a domain failure becomes an HTTP one, and it was not
    making that translation. Three things followed. The body never reached
    ``errors.public_failure``, so it carried no ``request_id``; a client had
    nothing to branch on but the prose, which the contract says is not an
    interface; and ``api()`` in the web client only throws on a non-2xx, so a
    failed save was reported to the user as a successful one.

    Read from the code, never from the message. Mapping prose to a status is
    the thing this change exists to remove, and an unrecognised code answers
    400 rather than 200 -- a failure whose kind is unknown is still a failure.
    """
    if not isinstance(payload, dict) or not payload.get("error"):
        return 200
    return SKILL_FAILURE_STATUS.get(str(payload.get("code") or ""), 400)


#: What one turn may attach as images, in three dimensions. None of these
#: existed: `_build_annotated_content` attached every pinned figure at full
#: size, re-encoded as PNG, so eight pins on a 3000x2200 raster sent ~10 MiB to
#: the provider and eighty sent ten times that. The failure is not subtle when
#: it lands -- a provider rejects the request, or bills for it -- but nothing
#: in the product said a limit existed, because none did.
#:
#: Enforced at assembly time, and reported. Silently dropping the ninth figure
#: would mean a user pins something, asks about it, and is answered about a
#: picture the model never saw.
MAX_ATTACHED_IMAGES = 8
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
#: The three budgets above bound what leaves this process. This one bounds what
#: enters it: the pinned bytes must be read whole to be hashed against the
#: version's recorded checksum, so without a source cap a 2 GiB file named
#: `figure.png` is loaded into memory before any of the other limits can look
#: at it.
MAX_SOURCE_IMAGE_BYTES = 64 * 1024 * 1024


#: One definition for both of the places that ask "does this request change
#: state": the Origin/CSRF guard and the query-string credential refusal below.
#: They were two literal tuples one edit apart from disagreeing, and a method
#: that counts as mutating for one guard and not the other is a hole in
#: whichever of them forgot it.
#: A client-generated admission id: long enough not to collide across
#: sessions or restarts, and narrow enough to be safe as a key.
_CLIENT_RESERVATION = re.compile(r"[A-Za-z0-9_-]{24,96}")

# Written in the same transaction as the pins they describe, so they are
# evidence rather than a cached guess, and reconciliation does not re-derive
# them from rows that have since moved on.
_TERMINAL_ADMISSION_STATES = frozenset({"sent", "released"})
# A pin that has been sent, and everything a review action can do to it
# afterwards. All of them mean the same thing to a lost 202: consumed.
_CONSUMED_ANNOTATION_STATES = frozenset({"sent", "resolved", "dismissed"})

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The team scope guard's matchers (M1-6). Compiled here, deliberately NOT in
#: the inline `re.fullmatch(r"...", sub)` form: the contract scanner reads
#: that form as a *route*, and a guard that matches every frame path is not a
#: route — inlining it published `/artifacts/([^/]+)(?:/.*)?` as an endpoint.
_TEAM_SCOPE_FRAME = re.compile(r"/frames/([^/]+)(?:/.*)?")
_TEAM_SCOPE_ARTIFACT = re.compile(r"/artifacts/([^/]+)(?:/.*)?")
#: Same rule, same reason (M2 project participation guard): written inline it
#: was scanned as a route and published `/projects/([^/]+)(?:/.*)?` as an
#: endpoint. A guard that matches every project path is not an endpoint.
_TEAM_SCOPE_PROJECT = re.compile(r"/projects/([^/]+)(?:/.*)?")
#: Same rule again, for shares (M2 hardening, external review #5). A share is
#: addressed by its own id, so the frame matcher above never saw it and
#: `GET /shares` listed -- and `DELETE /shares/{id}` revoked -- every user's.
_TEAM_SCOPE_SHARE = re.compile(r"/shares/([^/]+)")

#: The guest gate's copy of the replay matcher (M2-3/D3). The *route* itself
#: is dispatched with the inline scannable form in `_api` (which is what the
#: contract inventory discovers); this compiled twin exists because the guest
#: gate consults the same pattern and a gate is not a route. Keep the two
#: spellings identical.
_REPLAY_ROUTE = re.compile(r"/sessions/([^/]+)/replay")

#: The only paths a `?token=` may be traded for a cookie on -- an allowlist,
#: not a subtraction. The rule used to be "anything that is not `/api/v1/` and
#: not `/static/`", which its own docstring described as "paths that serve the
#: SPA shell". `/preview/<id>` is neither: it answers with artifact bytes, so
#: `/preview/<id>?token=...` was a link that set a durable cookie and then
#: handed the file to whoever held the link -- precisely the thing that
#: docstring promised could not happen. A subtractive rule re-opens that hole
#: every time a non-API route is added; an allowlist fails closed, and the root
#: page is the only URL this product ever hands to a person (`openai4s url`,
#: the startup banner, the .app).
_BOOTSTRAP_PATHS = frozenset({"/", "/index.html"})


def _is_bootstrap_path(path: str) -> bool:
    """May a `?token=` here be exchanged for the cookie?

    Root page only. The cost of being wrong is asymmetric: on the root page the
    link buys an empty SPA shell and the 303 strips the credential before
    anything renders, while on a path that answers with data the link *is* the
    data. Deep-link bootstrapping was the convenience being paid for, and
    nothing in the product ever generated such a link -- `_url()` builds the
    origin and `/?token=`.
    """
    return path in _BOOTSTRAP_PATHS


def _presented_token(headers: Any) -> str | None:
    """The credential a non-browser client sent, from either accepted spelling.

    `Authorization: Bearer` is what a generic HTTP client, an SDK or `curl -H`
    reaches for without being told; `X-OpenAI4S-Token` is unambiguous when
    something upstream already owns `Authorization`. Neither is preferred --
    whichever is present is checked, and both are compared in constant time by
    the caller.
    """
    explicit = headers.get(local_auth.TOKEN_HEADER)
    if explicit:
        return str(explicit)
    raw = str(headers.get("Authorization") or "")
    scheme, _, value = raw.partition(" ")
    if scheme.strip().casefold() == "bearer" and value.strip():
        return value.strip()
    return None


# --------------------------------------------------------------------------- #
#  small helpers
# --------------------------------------------------------------------------- #
def _iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        return (
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
        )
    except (ValueError, OSError, TypeError):
        return None


def _guess_ctype(name: str) -> str:
    low = name.lower()
    # structure / science formats first (mimetypes mis-maps some, e.g. .pdb)
    if low.endswith((".pdb", ".cif", ".mmcif", ".ent")):
        return "chemical/x-pdb"
    if low.endswith((".mol", ".mol2", ".sdf")):
        return "chemical/x-mdl-sdfile"
    if low.endswith(".xyz"):
        return "chemical/x-xyz"
    if low.endswith((".fasta", ".fa", ".nwk", ".treefile", ".log")):
        return "text/plain; charset=utf-8"
    ctype, _ = mimetypes.guess_type(name)
    if ctype:
        return ctype
    if low.endswith((".md", ".markdown", ".txt", ".tsv")):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _sanitize_header_value(value: str) -> str:
    """Remove CR/LF from an HTTP header value so a user-influenced value cannot
    inject extra headers or split the response (CWE-113)."""
    return str(value).replace("\r", "").replace("\n", "")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
#  WebSocket (RFC 6455) — shared hardened codec (openai4s.server.ws_frames)
# --------------------------------------------------------------------------- #
# The daemon reads client frames tolerantly (expect_mask=None) but now with a
# bounded payload, canonical-length, opcode, and RSV/FIN checks the old inline
# reader lacked. The share tunnel/relay use the same module in strict role-aware
# mode. Aliases keep gateway call sites and existing tests unchanged.
_WS_GUID = ws_frames.WS_GUID
_ws_accept = ws_frames.ws_accept

# Server frames are never masked; ``ws_encode`` defaults mask=False.
_ws_encode = ws_frames.ws_encode

# 16 MiB matches the daemon's largest realistic control/notebook event.
_GATEWAY_MAX_FRAME = 16 << 20


def _ws_read_frame(rfile) -> tuple[int, bytes] | None:
    """Read one client frame (tolerant compat mode); (opcode, payload) or None."""

    return ws_frames.ws_read_frame(rfile, expect_mask=None, max_len=_GATEWAY_MAX_FRAME)


_WS_RESUME_BUFFER_CAP = 4000
_WS_RESUME_BUFFER_BYTE_CAP = 8 * 1024 * 1024
_WS_REPLAY_ENVELOPE_EVENTS = 2  # replay_begin + replay_end
_WS_REPLAY_QUEUE_HEADROOM = 128
_WS_REPLAY_QUEUE_BYTE_HEADROOM = 2 * 1024 * 1024


class WSConnection:
    """A WS client. Sends are DECOUPLED from producers: `send_json`/`send_raw`
    only enqueue (never block), and a dedicated writer thread drains the queue to
    the socket. A client that stops reading fills its TCP buffer and would
    otherwise block `wfile.write` — and since broadcasts run on the TURN thread,
    that would hang the whole turn ("runs but never returns"). Here the turn
    thread never blocks: if a slow client's backlog overflows we simply drop it."""

    # A reconnect enqueues one complete resume snapshot atomically.  Keep the
    # outbound queue strictly larger than that snapshot plus its begin/end
    # envelope, with a little room for the execution/approval projections that
    # immediately follow subscription.
    _QUEUE_CAP = (
        _WS_RESUME_BUFFER_CAP + _WS_REPLAY_ENVELOPE_EVENTS + _WS_REPLAY_QUEUE_HEADROOM
    )
    _QUEUE_BYTE_CAP = _WS_RESUME_BUFFER_BYTE_CAP + _WS_REPLAY_QUEUE_BYTE_HEADROOM

    def __init__(self, wfile) -> None:
        self.wfile = wfile
        self.subs: set[str] = set()
        self.alive = True
        self._q: "queue.Queue" = queue.Queue(maxsize=self._QUEUE_CAP)
        self._q_budget_lock = threading.Lock()
        self._queued_bytes = 0
        self._writer = threading.Thread(target=self._drain, daemon=True)
        self._writer.start()

    def _enqueue(self, frame: bytes) -> None:
        size = len(frame)
        overflow = False
        with self._q_budget_lock:
            if not self.alive:
                return
            if size > self._QUEUE_BYTE_CAP or (
                self._queued_bytes + size > self._QUEUE_BYTE_CAP
            ):
                overflow = True
            else:
                try:
                    self._q.put_nowait(frame)
                    self._queued_bytes += size
                except queue.Full:
                    overflow = True
        if overflow:
            self._drop()  # slow client — never block the producer (turn thread)

    def send_json(self, obj: dict) -> None:
        self._enqueue(_ws_encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")))

    def send_raw(self, payload: bytes, opcode: int) -> None:
        self._enqueue(_ws_encode(payload, opcode))

    def _drop(self) -> None:
        """Mark dead, discard its backlog, and wake the writer exactly once."""

        with self._q_budget_lock:
            self.alive = False
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            self._queued_bytes = 0
            try:
                self._q.put_nowait(None)
            except queue.Full:  # pragma: no cover - queue was drained above
                pass

    def close(self) -> None:
        self._drop()

    def _drain(self) -> None:
        while True:
            frame = self._q.get()
            if frame is None:
                break
            try:
                self.wfile.write(frame)
                self.wfile.flush()
            except (OSError, ValueError):
                self._drop()
                break
            with self._q_budget_lock:
                # Count the frame until socket write+flush succeeds: a writer
                # blocked in the OS is still outbound backlog, not free budget.
                self._queued_bytes = max(0, self._queued_bytes - len(frame))


class WSHub:
    """Broadcasts frame events to subscribed WS clients AND keeps a per-frame
    buffer of the current turn's stream so a client that (re)opens a session
    mid-turn can REPLAY what it missed — the turn keeps running server-side even
    after every client disconnects (fire-and-forget MessageJob), and the buffer
    lets a reconnecting client resume the live view."""

    _BUFFER_CAP = _WS_RESUME_BUFFER_CAP  # max events retained per live frame
    _BUFFER_BYTE_CAP = _WS_RESUME_BUFFER_BYTE_CAP
    _MAX_MERGED_CHUNK_CHARS = 1_000_000
    _MAX_RESUME_FIELD_CHARS = 1_000_000

    def __init__(self) -> None:
        self._conns: set[WSConnection] = set()
        self._lock = threading.Lock()
        # per-frame live-turn buffer: {frame_id: {"events": [...], "running": bool}}
        self._live: dict[str, dict] = {}
        # Monotonic per-frame event counter. Never reset while the daemon lives,
        # including across turns: a client that reconnects mid-turn compares its
        # cursor against this, and a counter that restarted would silently look
        # like "you already have everything".
        #
        # Only events that go through `broadcast` are sequenced. Point-to-point
        # snapshots a subscriber receives directly (`execution_queue`, pending
        # approval cards) and the replay control frames carry no `seq` on
        # purpose: they are not positions in the stream, they are state handed
        # over once, so numbering them would make two clients' cursors disagree
        # about the same stream.
        self._seq: dict[str, int] = {}
        # Identifies this daemon process to resuming clients. The counter above
        # is in-process, so a restart puts it back to zero while a client is
        # still holding a cursor from the previous run -- exactly the "silently
        # look like you already have everything" case the comment above warns
        # about, which the counter alone cannot detect once it has been reset.
        # A client echoes the epoch it last saw; a mismatch means its cursor
        # describes a stream this process never produced.
        self._epoch = uuid.uuid4().hex[:16]

    def _next_seq_locked(self, root_frame_id: str) -> int:
        nxt = self._seq.get(root_frame_id, 0) + 1
        self._seq[root_frame_id] = nxt
        return nxt

    def add(self, c: WSConnection) -> None:
        with self._lock:
            self._conns.add(c)

    def remove(self, c: WSConnection) -> None:
        with self._lock:
            self._conns.discard(c)

    @property
    def epoch(self) -> str:
        """This process's stream identity. Cursors are only valid within it."""
        return self._epoch

    def subscribe(
        self,
        root_frame_id: str,
        conn: "WSConnection",
        since_seq: int = 0,
        epoch: str | None = None,
    ) -> None:
        """Subscribe and enqueue any live replay as one ordered transaction.

        ``broadcast`` uses the same lock while enqueueing.  A newly arriving
        chunk therefore lands either wholly before this subscription (and is
        included in the snapshot) or wholly after ``replay_end``; it can never
        be interleaved into the replay stream.
        """

        with self._lock:
            conn.subs.add(root_frame_id)
            buf = self._live.get(root_frame_id)
            stale = self._cursor_is_stale_locked(root_frame_id, since_seq, epoch)
            if stale:
                # Declare the gap and replay nothing. The client refetches the
                # session on `gap`, so anything sent here is rendered and then
                # immediately discarded -- and replaying the buffer from the
                # start to serve a cursor we cannot place is exactly the
                # wrap-around a fabricated cursor must never cause.
                #
                # Saying nothing at all, which is what happened before, left
                # the client believing it was caught up on a stream it had
                # entirely missed.
                self._enqueue_replay_locked(
                    root_frame_id, conn, [], since_seq, forced_gap=True
                )
            elif buf and buf.get("running") and buf.get("events"):
                self._enqueue_replay_locked(
                    root_frame_id, conn, buf["events"], since_seq
                )
            else:
                # Idle, and the cursor (if any) is placeable. Still send the
                # epoch handshake — an empty replay envelope carries it — so the
                # client records its next cursor stamped with *this* daemon's
                # epoch. Without it, a subscription that hit neither branch left
                # the client with a null epoch, and after a restart the numeric
                # stale check would accept that epoch-less cursor and skip the
                # new daemon's early events. The envelope is two frames with no
                # payload; the epoch is the point.
                self._enqueue_replay_locked(root_frame_id, conn, [], since_seq)

    def _cursor_is_stale_locked(
        self, root_frame_id: str, since_seq: int, epoch: str | None
    ) -> bool:
        """Is the client's cursor stale — unplaceable in *this* daemon's
        stream, so the subscription must declare a gap?

        The name is the contract, and the return follows it: ``True`` means
        "cannot be placed, refetch"; ``False`` means placeable, which includes
        the trivial "asks for everything" case. An earlier header asked the
        mirror-image "can we honour it?", which read as if ``True`` were yes.

        A cursor is only placeable if it numbers *this* daemon's stream, and
        the epoch is the only thing that says so. Three cases:

        * ``since_seq`` of zero asks for everything and places trivially;
        * a *different* epoch means the cursor numbers a stream some earlier
          daemon produced;
        * **no epoch at all cannot be placed either way**, so it is a gap.

        That last one used to be treated as placeable, and then checked
        numerically: our own counter sitting below the cursor proves we never
        emitted it. But the converse does not hold. An old tab reconnecting
        after a restart with ``since_seq=2`` meets a new daemon that has since
        emitted two events of its own, so the counter is *not* below the
        cursor, the cursor was declared fresh, and replay filtered the new
        daemon's events 1 and 2 out as already seen. The client was then
        silently missing the beginning of the stream it believed it was caught
        up on -- which is exactly the failure the numeric check was added to
        catch, surviving in the one case it cannot see.

        A legacy client cannot prove its cursor belongs to this stream, so it
        refetches. That costs one extra fetch on reconnect and is the only
        answer that cannot be wrong; every current client sends the epoch,
        which the empty replay envelope hands it even on an idle subscribe.
        """
        if not since_seq:
            return False
        if not epoch or epoch != self._epoch:
            return True
        return self._seq.get(root_frame_id, 0) < int(since_seq)

    def unsubscribe(self, root_frame_id: str, conn: "WSConnection") -> None:
        with self._lock:
            conn.subs.discard(root_frame_id)

    _MAX_LIVE_FRAMES = 64  # bound the resume-buffer dict (memory leak otherwise)

    def _evict_live(self) -> None:
        """Enforce a hard frame count, preferring completed buffers first."""

        cap = max(0, int(self._MAX_LIVE_FRAMES))
        while len(self._live) > cap:
            victim = next(
                (k for k, v in self._live.items() if not v.get("running")), None
            )
            if victim is None:
                # A resume window is a cache, not execution ownership.  Under a
                # true all-running flood, discard the oldest window rather than
                # letting the daemon grow without bound; the turn itself keeps
                # running and durable state remains available after completion.
                victim = next(iter(self._live), None)
            if victim is None:
                break
            self._live.pop(victim, None)

    def _install_live_buffer(self, rid: str, buf: dict) -> dict:
        # Replacing an older turn for the same frame makes it newest for hard
        # eviction order; assigning an existing dict key alone would not.
        self._live.pop(rid, None)
        self._live[rid] = buf
        self._evict_live()
        return buf

    def drop_frame(self, rid: str) -> None:
        """Forget a frame's resume buffer (called when a frame/project is deleted)."""
        with self._lock:
            self._live.pop(rid, None)

    @staticmethod
    def _event_wire_size(obj: dict) -> int:
        """Exact unmasked server-frame bytes for this event's JSON encoding."""

        payload_size = len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        return ws_frames.frame_header_size(payload_size) + payload_size

    def _prepare_live_event(self, obj: dict) -> tuple[dict, int]:
        """Bound large public string fields, then measure the event once."""

        prepared = obj
        for key in ("chunk", "source", "stdout", "stderr", "error"):
            value = prepared.get(key)
            if not isinstance(value, str) or len(value) <= self._MAX_RESUME_FIELD_CHARS:
                continue
            if prepared is obj:
                prepared = dict(obj)
            prepared[key] = (
                value[: self._MAX_RESUME_FIELD_CHARS] + "\n...(resume field truncated)"
            )

        size = self._event_wire_size(prepared)
        if size <= self._BUFFER_BYTE_CAP:
            return prepared, size

        # Nested step/plan payloads can still exceed the frame budget after
        # direct strings are bounded.  Preserve only routing/protocol identity;
        # durable REST projections remain the source for their full payload.
        identity_keys = (
            "type",
            "frame_id",
            "root_frame_id",
            "producing_cell_id",
            "cell_id",
            "draft_id",
            "revision",
            "cell_index",
            "stream",
            "block_type",
            "status",
            "language",
            "origin",
        )
        prepared = {key: prepared[key] for key in identity_keys if key in prepared}
        prepared["resume_truncated"] = True
        size = self._event_wire_size(prepared)
        return prepared, size

    def _new_live_buffer(
        self,
        events: list[dict],
        *,
        scope: str,
        execution_id: str = "",
    ) -> dict:
        prepared: list[dict] = []
        sizes: list[int] = []
        for event in events:
            bounded, size = self._prepare_live_event(event)
            prepared.append(bounded)
            sizes.append(size)
        return {
            "events": prepared,
            "event_sizes": sizes,
            "event_bytes": sum(sizes),
            "running": True,
            "active_cells": {},
            "active_cell_sizes": {},
            "scope": scope,
            # Which execution this window belongs to. Stated rather than
            # inferred from the last event that happened to arrive: the whole
            # problem is that events arrive out of order, so the last one is
            # exactly the wrong thing to trust.
            "execution_id": str(execution_id or ""),
        }

    def _ensure_live_accounting(self, buf: dict) -> None:
        events = buf.setdefault("events", [])
        sizes = buf.get("event_sizes")
        if not isinstance(sizes, list) or len(sizes) != len(events):
            sizes = [self._event_wire_size(event) for event in events]
            buf["event_sizes"] = sizes
            buf["event_bytes"] = sum(sizes)
        elif not isinstance(buf.get("event_bytes"), int):
            buf["event_bytes"] = sum(sizes)
        buf.setdefault("active_cell_sizes", {})

    def _append_live_event(self, buf: dict, obj: dict) -> tuple[dict, int]:
        self._ensure_live_accounting(buf)
        event, size = self._prepare_live_event(obj)
        buf["events"].append(event)
        buf["event_sizes"].append(size)
        buf["event_bytes"] += size
        return event, size

    def _replace_live_event(self, buf: dict, index: int, obj: dict) -> tuple[dict, int]:
        self._ensure_live_accounting(buf)
        if index < 0:
            index += len(buf["events"])
        event, size = self._prepare_live_event(obj)
        previous_size = buf["event_sizes"][index]
        buf["events"][index] = event
        buf["event_sizes"][index] = size
        buf["event_bytes"] += size - previous_size
        return event, size

    def _remove_live_events(self, buf: dict, predicate) -> None:
        self._ensure_live_accounting(buf)
        kept_events: list[dict] = []
        kept_sizes: list[int] = []
        for event, size in zip(buf["events"], buf["event_sizes"]):
            if predicate(event):
                continue
            kept_events.append(event)
            kept_sizes.append(size)
        buf["events"] = kept_events
        buf["event_sizes"] = kept_sizes
        buf["event_bytes"] = sum(kept_sizes)

    #: Event types that belong to ONE turn's stream. Everything else -- kernel
    #: status, permission cards, metadata deltas -- is frame state that no
    #: execution owns, and withholding those would break surfaces that have
    #: nothing to do with turn ordering.
    _TURN_SCOPED_TYPES = frozenset({"text_reset", "text_chunk", "frame_update"})

    def _refuses_event_locked(self, rid: str, obj: dict) -> bool:
        """Should this event be withheld from the buffer AND from live sockets?

        Dropping it from the resume window alone is not enough. `broadcast`
        still delivered it, and a tab that joined during B has no stored
        identity for A -- so its own filter reads "one side silent", which
        means current, and A's late terminal closes B. The hub is the only
        place that knows both identities, so the fence has to be here.
        """
        t = obj.get("type")
        if t not in self._TURN_SCOPED_TYPES:
            return False
        if t == "frame_update" and obj.get("status") == "processing":
            # A boundary announces a new execution; it is never stale against
            # the one it replaces.
            return False
        return self._is_stale_for_buffer(
            self._live.get(rid), str(obj.get("execution_id") or "")
        )

    @staticmethod
    def _is_stale_for_buffer(buf: dict | None, event_execution: str) -> bool:
        """Does this event belong to an execution the live window has moved on from?

        Both sides must name one. A daemon that predates execution ids on the
        wire, and the identity-less stream events a current turn still emits
        between its `processing` and its terminal, both fall through as current
        -- which is the only answer that cannot strand a running turn.
        """
        if not buf or not buf.get("running") or not event_execution:
            return False
        active = str(buf.get("execution_id") or "")
        return bool(active) and active != event_execution

    def _record(self, rid: str, obj: dict) -> None:
        t = obj.get("type")
        # Approval cards have their own durable replay source.  In particular,
        # resolving a card after daemon restart is not a live Agent turn and
        # must not create a phantom running resume buffer.
        if t in {"await_permission", "permission_resolved"}:
            return
        buf = self._live.get(rid)
        event_execution = str(obj.get("execution_id") or "")
        # A `processing` naming a new execution is a BOUNDARY, and it has to be
        # read before the staleness test -- which would otherwise judge it
        # against the window it is replacing and drop it, leaving the previous
        # execution's window live forever.
        if (
            t == "frame_update"
            and obj.get("status") == "processing"
            and (
                buf is None
                or not buf.get("running")
                or (
                    event_execution
                    and str(buf.get("execution_id") or "") != event_execution
                )
            )
        ):
            # A manual Reviewer (or another activity without a text stream)
            # starts after the prior turn's buffer has ended; a queued turn
            # starts while the previous one is still unwinding. Either way this
            # is the live window now.
            self._install_live_buffer(
                rid,
                self._new_live_buffer(
                    [obj], scope="turn", execution_id=event_execution
                ),
            )
            return
        if self._is_stale_for_buffer(buf, event_execution):
            # This event is the tail of an execution that is no longer the live
            # one. The client-side filter is not enough on its own: the resume
            # buffer is what a RECONNECTING client replays and what
            # `is_running` answers from, so a late `text_reset` replacing the
            # window -- or a late terminal clearing `running` -- makes the turn
            # that is genuinely still running look finished to every client
            # that arrives afterwards, including the one that reconnects.
            return
        if t == "text_reset":
            # A new turn begins -- but the identity is INHERITED when the event
            # does not name one. The stream events a running turn emits carry
            # no execution id today, so taking the field verbatim would wipe
            # the id the `processing` boundary just established and hand the
            # window straight back to whichever late event arrived next.
            self._install_live_buffer(
                rid,
                self._new_live_buffer(
                    [obj],
                    scope="turn",
                    execution_id=event_execution
                    or (str(buf.get("execution_id") or "") if buf else ""),
                ),
            )
            return
        if t == "notebook_cell_start" and (buf is None or not buf.get("running")):
            # User-REPL/lifecycle execution has no Agent text_reset.  Its
            # structured Cell start is nevertheless an explicit live boundary,
            # and the matching finish closes this cell-scoped resume window.
            buf = self._install_live_buffer(
                rid,
                self._new_live_buffer([], scope="cell"),
            )
        elif buf is None:
            # Idle kernel status, metadata PATCHes and terminal frame updates
            # are broadcast-only state deltas.  Treating an arbitrary stray
            # event as a live turn creates a phantom running session that can
            # never receive a matching terminal marker.
            return

        # CellExecutionService emits each stdout write twice: once as the
        # structured Notebook chunk, then immediately as a legacy tool-text
        # echo for the chat activity card.  A replay needs both projections,
        # but not thousands of tiny duplicate events.  Coalesce the adjacent
        # pair into one bounded Notebook chunk and one bounded activity chunk.
        # The one-shot signature makes this fail safe: any intervening event
        # clears it, so unrelated text is never merged merely because its bytes
        # happen to match.
        pending_echo = buf.pop("pending_cell_tool_echo", None)
        coalescible_pair = buf.pop("coalescible_cell_pair", None)
        if (
            t == "text_chunk"
            and obj.get("block_type") == "tool"
            and pending_echo is not None
            and (pending_echo[0], pending_echo[2])
            == (self._cell_event_id(obj), obj.get("chunk"))
        ):
            previous = buf["events"][-1] if buf["events"] else None
            if self._is_cell_tool_echo(previous, pending_echo[0]):
                self._replace_live_event(
                    buf,
                    -1,
                    {
                        **previous,
                        "chunk": self._merge_resume_chunk(
                            previous.get("chunk", ""), obj.get("chunk", "")
                        ),
                    },
                )
            else:
                self._append_live_event(buf, obj)
            buf["coalescible_cell_pair"] = (pending_echo[0], pending_echo[1])
            self._trim_live_events(buf)
            return

        if t == "notebook_cell_draft":
            # A draft is replace-in-place UI state, not an append-only Cell.
            # Keep only its newest revision so reconnect replay cannot render a
            # ladder of partial model tokens.
            draft_id = obj.get("draft_id")
            self._remove_live_events(
                buf,
                lambda event: (
                    event.get("type") == "notebook_cell_draft"
                    and event.get("draft_id") == draft_id
                ),
            )
            self._append_live_event(buf, obj)
            self._trim_live_events(buf)
            return
        if t == "notebook_cell_chunk":
            cell_id = self._cell_event_id(obj)
            chunk = obj.get("chunk")
            if cell_id and isinstance(chunk, str):
                buf["pending_cell_tool_echo"] = (
                    cell_id,
                    obj.get("stream", "stdout"),
                    chunk,
                )
            previous = buf["events"][-1] if buf["events"] else None
            pair_chunk_index = None
            if (
                coalescible_pair == (cell_id, obj.get("stream", "stdout"))
                and self._is_cell_tool_echo(previous, cell_id)
                and len(buf["events"]) >= 2
            ):
                candidate = buf["events"][-2]
                if (
                    candidate.get("type") == "notebook_cell_chunk"
                    and self._cell_event_id(candidate) == cell_id
                    and candidate.get("stream", "stdout") == obj.get("stream", "stdout")
                ):
                    pair_chunk_index = len(buf["events"]) - 2
            if (
                previous
                and previous.get("type") == "notebook_cell_chunk"
                and self._cell_event_id(previous) == cell_id
                and previous.get("stream", "stdout") == obj.get("stream", "stdout")
                and isinstance(previous.get("chunk"), str)
                and isinstance(chunk, str)
            ):
                self._replace_live_event(
                    buf,
                    -1,
                    {
                        **previous,
                        "chunk": self._merge_resume_chunk(previous["chunk"], chunk),
                    },
                )
            elif pair_chunk_index is not None and isinstance(chunk, str):
                paired = buf["events"][pair_chunk_index]
                self._replace_live_event(
                    buf,
                    pair_chunk_index,
                    {
                        **paired,
                        "chunk": self._merge_resume_chunk(
                            paired.get("chunk", ""), chunk
                        ),
                    },
                )
            else:
                self._append_live_event(buf, obj)
            self._trim_live_events(buf)
            return

        cell_id = self._cell_event_id(obj)
        if t == "notebook_cell_finished" and cell_id:
            buf.setdefault("active_cells", {}).pop(cell_id, None)
            buf.setdefault("active_cell_sizes", {}).pop(cell_id, None)
        if t in (
            "text_chunk",
            "notebook_cell_start",
            "notebook_cell_finished",
            "kernel_status",
            "artifact_created",
            "step",
            "step_update",
            "plan_ready",
            "plan_progress",
            "execution_state",
            "execution_queue",
            "execution_owner",
        ):
            event, size = self._append_live_event(buf, obj)
            if t == "notebook_cell_start" and cell_id:
                buf.setdefault("active_cells", {})[cell_id] = event
                buf.setdefault("active_cell_sizes", {})[cell_id] = size
            self._trim_live_events(buf)
            if (
                t == "notebook_cell_finished"
                and buf.get("scope") == "cell"
                and not buf.get("active_cells")
            ):
                buf["running"] = False
            elif buf.get("scope") == "cell" and (
                (
                    t == "execution_state"
                    and obj.get("status") in {"completed", "failed", "cancelled"}
                )
                or (t == "execution_owner" and not obj.get("owner"))
            ):
                # A preparation/projection failure can occur after Cell start
                # but before notebook_cell_finished.  The execution coordinator
                # is the authoritative fallback terminal boundary.
                buf.setdefault("active_cells", {}).clear()
                buf.setdefault("active_cell_sizes", {}).clear()
                buf["running"] = False
        elif t == "frame_update":
            self._append_live_event(buf, obj)
            self._trim_live_events(buf)
            if obj.get("status") in (
                "completed",
                "done",
                "failed",
                "cancelled",
                "success",
                "ready",
            ):
                buf["running"] = False

    @staticmethod
    def _cell_event_id(obj: dict) -> str | None:
        value = obj.get("producing_cell_id") or obj.get("cell_id")
        return str(value) if value not in (None, "") else None

    @classmethod
    def _is_cell_tool_echo(cls, obj: dict | None, cell_id: str | None) -> bool:
        return bool(
            obj
            and cell_id
            and obj.get("type") == "text_chunk"
            and obj.get("block_type") == "tool"
            and cls._cell_event_id(obj) == cell_id
            and isinstance(obj.get("chunk"), str)
        )

    @classmethod
    def _merge_resume_chunk(cls, previous: str, chunk: str) -> str:
        merged = str(previous or "") + str(chunk or "")
        if len(merged) <= cls._MAX_MERGED_CHUNK_CHARS:
            return merged
        return merged[: cls._MAX_MERGED_CHUNK_CHARS] + "\n...(resume output truncated)"

    def _trim_live_events(self, buf: dict) -> None:
        """Bound event count and wire bytes while retaining replay anchors."""

        self._ensure_live_accounting(buf)
        events = buf["events"]
        sizes = buf["event_sizes"]
        count_cap = max(1, int(self._BUFFER_CAP))
        byte_cap = max(1, int(self._BUFFER_BYTE_CAP))
        if len(events) <= count_cap and buf["event_bytes"] <= byte_cap:
            return

        head_events = (
            events[:1] if events and events[0].get("type") == "text_reset" else []
        )
        head_sizes = sizes[:1] if head_events else []
        count_room = count_cap - len(head_events)
        byte_room = byte_cap - sum(head_sizes)
        active = list((buf.get("active_cells") or {}).items())
        active_sizes = buf.get("active_cell_sizes") or {}

        # Keep the newest active starts that fit.  In normal operation there is
        # one FIFO execution owner, but this remains bounded under malformed or
        # future multi-runtime producers.
        selected_active_reversed: list[tuple[str, dict, int]] = []
        if count_room > 0 and byte_room > 0:
            for cell_id, event in reversed(active):
                size = int(active_sizes.get(cell_id) or self._event_wire_size(event))
                if len(selected_active_reversed) >= count_room:
                    break
                if size > byte_room:
                    continue
                selected_active_reversed.append((cell_id, event, size))
                byte_room -= size
        selected_active = list(reversed(selected_active_reversed))
        active_ids = {cell_id for cell_id, _event, _size in selected_active}
        unselected_active_ids = {
            cell_id for cell_id, _event in active if cell_id not in active_ids
        }
        start_events = [event for _cell_id, event, _size in selected_active]
        start_sizes = [size for _cell_id, _event, size in selected_active]
        count_room -= len(start_events)

        # Once a Cell is active, older completed activity is less useful than a
        # valid start-before-chunk protocol for that Cell.  Tail selection is
        # therefore anchored after the earliest retained active start.
        start_positions = [
            index
            for index, event in enumerate(events)
            if event.get("type") == "notebook_cell_start"
            and self._cell_event_id(event) in active_ids
        ]
        tail_offset = min(start_positions) + 1 if start_positions else len(head_events)
        tail_source = [
            (event, size)
            for event, size in zip(events[tail_offset:], sizes[tail_offset:])
            if not (
                (
                    event.get("type") == "notebook_cell_start"
                    and self._cell_event_id(event) in active_ids
                )
                or self._cell_event_id(event) in unselected_active_ids
            )
        ]
        tail_reversed: list[tuple[dict, int]] = []
        if count_room > 0 and byte_room > 0:
            for event, size in reversed(tail_source):
                if len(tail_reversed) >= count_room or size > byte_room:
                    break
                tail_reversed.append((event, size))
                byte_room -= size
        tail = list(reversed(tail_reversed))

        buf["events"] = head_events + start_events + [event for event, _ in tail]
        buf["event_sizes"] = head_sizes + start_sizes + [size for _, size in tail]
        buf["event_bytes"] = sum(buf["event_sizes"])

    def broadcast(self, root_frame_id: str | None, obj: dict) -> None:
        if root_frame_id is None:
            # No caller passes None today, and a None fan-out would reach
            # every connection regardless of subscription — in team mode that
            # is a cross-user leak by construction (M1-7). Dropped rather than
            # asserted: a future caller's bug should lose one event, not the
            # daemon.
            return
        with self._lock:
            if root_frame_id:
                # Stamped under the hub lock, so the number a client sees is the
                # same order the buffer recorded and the same order every other
                # subscriber receives. Assigning it outside the lock would let
                # two producers interleave and hand out a sequence that does not
                # match delivery order — which is the one thing a resume cursor
                # cannot tolerate.
                if self._refuses_event_locked(root_frame_id, obj):
                    # Not recorded, not delivered, and NOT given a sequence
                    # number: it is not part of this frame's stream, so it must
                    # not advance a cursor either.
                    return
                obj["seq"] = self._next_seq_locked(root_frame_id)
                self._record(root_frame_id, obj)
            # ``send_json`` only performs JSON encoding + a non-blocking queue
            # put.  Keeping enqueue under the hub lock makes its order atomic
            # with subscribe/replay without coupling producers to socket I/O.
            for c in tuple(self._conns):
                if c.alive and (root_frame_id is None or root_frame_id in c.subs):
                    c.send_json(obj)

    def is_running(self, root_frame_id: str) -> bool:
        with self._lock:
            return bool(self._live.get(root_frame_id, {}).get("running"))

    def has_subscriber(self, root_frame_id: str) -> bool:
        """True iff a live WS client is currently viewing this conversation — so
        the permission gate only prompts (and blocks) when someone can answer."""
        with self._lock:
            conns = list(self._conns)
        return any(c.alive and root_frame_id in c.subs for c in conns)

    def replay(self, root_frame_id: str, conn: "WSConnection") -> None:
        """Send the buffered current-turn events to a single (re)connecting
        client so it can resume the live stream from the beginning of the turn."""
        with self._lock:
            buf = self._live.get(root_frame_id)
            events = list(buf["events"]) if buf else []
            if events:
                self._enqueue_replay_locked(root_frame_id, conn, events)

    def _enqueue_replay_locked(
        self,
        root_frame_id: str,
        conn: "WSConnection",
        events: list[dict],
        since_seq: int = 0,
        *,
        forced_gap: bool = False,
    ) -> None:
        """Replay buffered events, optionally only those after ``since_seq``.

        ``replay_begin`` carries ``from_seq``/``to_seq``, this process's
        ``epoch``, and whether the window was complete. A client that was away
        longer than the buffer retains cannot be served by a cursor, and
        telling it so (``gap: true``) lets it refetch state instead of
        resuming from a hole it cannot see.
        """
        selected = [e for e in events if int(e.get("seq") or 0) > since_seq]
        first = int(selected[0].get("seq") or 0) if selected else since_seq
        last = int(selected[-1].get("seq") or 0) if selected else since_seq
        conn.send_json(
            {
                "type": "replay_begin",
                "root_frame_id": root_frame_id,
                "from_seq": first,
                "to_seq": last,
                # Echoed so the client can tell one daemon's stream from
                # another's and drop a cursor that belongs to neither.
                "epoch": self._epoch,
                # The buffer is capped, so the oldest event it still holds may
                # be newer than the cursor+1 the client asked for.
                "gap": bool(
                    forced_gap or (since_seq and selected and first > since_seq + 1)
                ),
            }
        )
        for event in selected:
            conn.send_json(event)
        conn.send_json(
            {"type": "replay_end", "root_frame_id": root_frame_id, "to_seq": last}
        )

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.broadcast(root_frame_id, event)

        return emit


# --------------------------------------------------------------------------- #
#  Session runner — Code-as-Action turn on a persistent per-session kernel
# --------------------------------------------------------------------------- #
class SessionState:
    def __init__(
        self,
        root_frame_id: str,
        project_id: str,
        workspace: Path,
        *,
        branch_id: str | None = None,
        kernel_generations=None,
        owner_instance_id: str | None = None,
        clock_ms=None,
    ):
        self.root_frame_id = root_frame_id
        self.project_id = project_id
        self.branch_id = branch_id or root_frame_id
        self.workspace = workspace
        #: `(profile_id, revision)` this turn was ACCEPTED under, when it came
        #: through the queue. `_pinned_llm_config` prefers it over the frame's
        #: current pin, because the frame's pin is mutable by design and an item
        #: already in the FIFO must not follow it. `None` for a direct turn, where
        #: the frame is the freshest answer there is.
        self.frozen_model_binding: tuple[str, int] | None = None
        # One owner for both persistent execution channels.  ``Kernel`` keeps
        # sole ownership of protocol I/O; the supervisor only coordinates
        # lifecycle and exact-worker identity across cancellation/watchdogs.
        self.kernels = KernelSupervisor(
            root_frame_id=root_frame_id,
            branch_id=self.branch_id,
            generations=kernel_generations,
            owner_instance_id=owner_instance_id,
            clock_ms=clock_ms,
        )
        # The JSON control plane belongs to the session, not to either language
        # worker.  It is constructed lazily and survives kernel stop/restart.
        self.runtime = SessionRuntime()
        self.messages: list[dict] = []
        # What this turn's budgets left out of the context, by kind. Read by
        # the Context projection; rebuilt with the system prompt each turn.
        self.context_omissions: dict[str, list[dict]] = {}
        self.cell_index = 0
        self.booted = False
        self.turn_lock = threading.Lock()
        # Stop intent is visible before Stop waits for ``turn_lock``. New turns
        # back off instead of clearing cancellation and overtaking the stop.
        self.stop_requested = threading.Event()
        self.stop_finished = threading.Event()
        self.stop_finished.set()
        self.stop_lock = threading.Lock()
        # Admission intent is shorter-lived than ``turn_lock``.  It closes the
        # tiny race between a lifecycle Stop reserving FIFO ownership and a new
        # message/REPL ticket being submitted.
        self.admission_lock = threading.Lock()
        self.cancel = threading.Event()
        # Per-session model override (from the composer dropdown) + plan flag.
        self.model: str | None = None
        self.plan: bool = False
        # Explore mode: autonomous deep exploration — larger turn budget and the
        # turn only ends via host.submit_output (prose-only replies are nudged).
        self.explore: bool = False
        self.last_model_prose: str = ""
        self.last_engine_completion = None
        # Set only around one AgentEngine CodeCell dispatch so the compatible
        # ``_execute_and_log`` call shape need not expose ledger internals.
        self.active_action_group_id: str | None = None
        self.active_action_ledger: RuntimeActionLedger | None = None
        # `env_name` is the environment the current kernel actually runs in;
        # `desired_env` is the user's/agent's pinned selection. They differ only
        # during a transient fallback to base when the pin cannot be resolved.
        # `pending_env` is a switch requested mid-turn (host.env.use); it is
        # applied between cells so the agent never restarts its running kernel.
        self.env_name: str | None = None
        self.desired_env: str | None = None
        self.pending_env: str | None = None
        # One delegation tree belongs to the whole Web session.  Re-creating a
        # runner on every user turn used to orphan async children and reset the
        # shared fan-out budget, making collect/steer/cancel unreliable after
        # the next message.
        self.delegation_runner = None
        # R execution channel: the persistent R kernel serving ```r cells —
        # spawned lazily on first use, retargeted when host.env.use() picks an
        # R-only env (dispatcher.active_r_env), torn down with the session.
        # `r_env_name` records which env the running R kernel resolved against
        # (None = default resolution: the 'r' env, else Rscript on PATH).
        self.r_env_name: str | None = None

    @property
    def kernel(self) -> Kernel | None:
        """Current Python worker (compatibility view; lifecycle lives above)."""
        return self.kernels.kernel("python")

    @property
    def dispatcher(self):
        """Compatible view of the session-scoped control-plane dispatcher."""
        return self.runtime.dispatcher

    @dispatcher.setter
    def dispatcher(self, value) -> None:
        self.runtime.dispatcher = value

    @property
    def r_kernel(self) -> Kernel | None:
        """Current R worker (compatibility view; lifecycle lives above)."""
        return self.kernels.kernel("r")

    @property
    def kernel_manual_stop(self) -> bool:
        return bool(self.kernels.status("python")["manual_stop"])

    @contextmanager
    def execution_barrier(self):
        """Serialize a turn while giving an already-requested Stop priority."""
        while True:
            self.turn_lock.acquire()
            # Admission and cancellation reset are one critical section. If a
            # Stop arrives after this clear, its newly-set signal survives; if
            # it arrived before, stop_requested makes this entrant yield.
            self.cancel.clear()
            if not self.stop_requested.is_set():
                break
            self.turn_lock.release()
            self.stop_finished.wait()
        try:
            yield
        finally:
            self.turn_lock.release()


class MessageJob:
    def __init__(self, job_id: str, root_frame_id: str) -> None:
        self.job_id = job_id
        self.root_frame_id = root_frame_id
        self.done = threading.Event()
        self.result: dict | None = None
        self.error: str | None = None
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.thread: threading.Thread | None = None
        self.execution_id: str | None = None
        self.execution_owner: dict[str, str] | None = None
        # Captured here, on the request thread that constructs the job. The
        # failure a user reads and the log line for the work that failed have
        # to be the same id, or the id ties nothing to anything.
        # `or new_correlation_id()`: a direct submit -- the CLI, a recovery
        # replay -- has no HTTP request behind it, and an empty id here made
        # the 202 and the job result nameless while `run_message` minted its
        # own for the socket. Two ids for one turn is worse than none.
        self.request_id: str = correlation_id() or new_correlation_id()
        # Captured on the request thread, for the same reason as the id above:
        # the turn runs on a worker thread, a ContextVar does not follow a
        # bare `threading.Thread`, and the turn is where `host.frames` and
        # every delegated child actually read user data. Held on the ticket
        # rather than on the session's `HostDispatcher` -- the dispatcher is
        # built once and serves every turn, so an identity stored there would
        # be one turn's caller answering another turn's authorization
        # question.
        self.principal = execution_principal.current()
        # The model configuration this job was ACCEPTED under. `submit_message`
        # froze the identity at send, but onto the *frame* -- and the frame's pin
        # is mutable by design, because `POST /frames/{id}/model-binding` is the
        # answer to a dangling one. So an item accepted under P and still in the
        # FIFO was re-resolved from the frame at dequeue and could run on Q, with
        # the client already told 202 under P. Frozen on the ticket, the item
        # cannot drift no matter what the frame says later.
        self.model_profile_id: str = ""
        self.model_profile_revision: int = 0
        # Set by `project` below. A job failure is read back over HTTP 200
        # (`{"status": "failed", ...}` is the result, not an error envelope),
        # so `Handler._json` never enriches it and the code has to be carried
        # here or it does not exist on this surface at all.
        self.error_code: str = ""
        #: Whether the failure happened after output was already committed --
        #: bytes streamed, or a tool run. `llm/models.py` calls it the retry
        #: veto; it is kept here so the socket and the job query can both say
        #: so, which is what stops the UI offering a retry that would duplicate
        #: work that already happened.
        self.output_committed: bool = False
        #: The branch this turn was accepted on, resolved at submit time while
        #: the Store is known to be working. Resolving it again during a
        #: failure is the wrong moment: the failure is frequently the Store,
        #: and a lookup that falls back to the root frame yields a *different*
        #: key from the one the turn filed its note under.
        self.branch_id: str = ""

    def finish(self, result: dict | None = None, error: str | None = None) -> None:
        self.result = result
        self.error = error
        self.finished_at = time.time()
        self.done.set()

    def project(self, exc: BaseException, surface: str) -> str:
        """Record the diagnostic once, and return the one sentence this failure
        is allowed to say.

        The three spawners each did `job.finish(error=str(e))`, and the message
        turn additionally streamed the same `str(e)` into a `text_chunk` — so a
        `PermissionError` naming a path under $HOME, or a provider error
        echoing the credential it was sent, reached the browser twice over two
        different transports. Projecting here rather than at each call site
        means the WebSocket chunk and the job result cannot disagree about what
        happened, and the original is written to the operator diagnostic once
        rather than once per surface.
        """
        body, _status = public_exception(
            exc, surface=surface, request_id=self.request_id
        )
        self.error_code = str(body.get("code") or "internal_error")
        # OR, never assign. Both handlers can fire for one turn -- `_loop`
        # fails and the inner one records it, then the tail fails and leaves
        # through the outer one -- and the second exception is usually an
        # ordinary one. Assigning let it *downgrade* the veto the first had
        # earned, so a turn that had already run a tool went back to being
        # offered a retry. A veto is a fact about the request, not about
        # whichever exception was projected last.
        self.output_committed = self.output_committed or bool(
            body.get("output_committed")
        )
        return str(body.get("error") or INTERNAL_ERROR_MESSAGE)

    def wait_result(self) -> dict:
        self.done.wait()
        if self.result is not None:
            return self.result
        failure = {
            "status": "failed",
            "frame_id": self.root_frame_id,
            "job_id": self.job_id,
            "error": self.error or INTERNAL_ERROR_MESSAGE,
            "code": self.error_code or "internal_error",
        }
        # Only when there is one. A null field here would read as "this request
        # had no id", when what it means is that the job was built outside a
        # request -- and the error envelope already distinguishes those.
        if self.request_id:
            failure["request_id"] = self.request_id
        if self.execution_id:
            # The id that tells this failure from a later turn's. A poll and
            # the socket must agree about which execution ended, or a client
            # that missed the event cannot reconstruct what the socket said.
            failure["execution_id"] = self.execution_id
        if self.output_committed:
            failure["output_committed"] = True
        return failure


def _maybe_call(v):
    """Return v() if v is callable (property vs. method tolerant), else v or ''."""
    try:
        v = v() if callable(v) else v
    except Exception:
        return ""
    return v or ""


_REMOTE_GPU_TASK_RE = re.compile(
    r"(remote\s*gpu|gpu|a100|esm(?:fold)?|proteinmpnn|protein\s+mpnn|"
    r"single[- ]?mutation|variant[- ]?effect|mutation|alphafold|protenix|"
    r"boltz|chai|protein language model|fasta|enzyme|protein sequence|"
    r"amino acid|folding)",
    re.I,
)
_REMOTE_GPU_CORE_CAPS = ("fold", "score_mutations")


def _remote_gpu_runtime_context(user_text: str | None = None) -> str:
    """Prompt fragment reflecting the current remote-GPU registry.

    Sessions can be created before the user adds a GPU in Settings, so this
    context is injected both into the initial system prompt and into later turns.
    """
    try:
        from openai4s.compute import registry as _reg

        hosts_reg = _reg.list_hosts()
        default = _reg.default_host()
    except Exception:  # noqa: BLE001
        return ""
    if not hosts_reg:
        return ""

    cap_names = set()
    host_lines = []
    for alias, h in hosts_reg.items():
        caps = h.get("capabilities") or {}
        cap_names.update(caps.keys())
        cap_text = (
            ", ".join(
                f"{c} ({(m or {}).get('engine') or 'registered'})"
                for c, m in caps.items()
            )
            or "no services provisioned yet"
        )
        host_lines.append(
            f"- {alias}{' [default]' if alias == default else ''}: "
            f"{h.get('gpus') or 'GPU details unknown'}; {cap_text}"
        )

    lower = (user_text or "").lower()
    proteinish = any(
        k in lower
        for k in (
            "protein",
            "enzyme",
            "fasta",
            "sequence",
            "amino acid",
            "recombinase",
            "mutation",
            "variant",
            "esm",
            "proteinmpnn",
            "protein mpnn",
        )
    )
    requested_caps: set[str] = set()
    if (
        any(
            k in lower
            for k in (
                "fold",
                "folding",
                "structure",
                "alphafold",
                "protenix",
                "esmfold",
                "boltz",
                "chai",
            )
        )
        and proteinish
    ):
        requested_caps.add("fold")
    if any(
        k in lower
        for k in (
            "esm",
            "mutation",
            "variant",
            "single-mutation",
            "single mutation",
            "variant-effect",
            "variant effect",
        )
    ):
        requested_caps.add("score_mutations")
    if "proteinmpnn" in lower or "protein mpnn" in lower:
        requested_caps.add("proteinmpnn")
    task_needs_gpu = bool(user_text and _REMOTE_GPU_TASK_RE.search(user_text))
    if task_needs_gpu and not requested_caps:
        requested_caps.update(_REMOTE_GPU_CORE_CAPS)
    missing = sorted(c for c in requested_caps if c not in cap_names)

    lines = [
        "Remote GPU state for this turn:",
        *host_lines,
        "Use `host.remote_gpu_status()` for the machine-readable view.",
    ]
    if task_needs_gpu and missing:
        lines.extend(
            [
                "REMOTE GPU PROVISIONING REQUIRED: the user has provided a remote GPU "
                f"host, but these requested/core services are missing: {', '.join(missing)}.",
                "Before saying the remote pipeline is not configured, delegate a "
                "self-contained setup task with "
                '`host.delegate(..., name="REMOTE_GPU_PROVISIONER", wait=True)`. '
                "Ask that specialist to inspect the SSH host, provision or locate real "
                "wrappers, verify them, and register capabilities with "
                "`host.register_remote_capability(...)`. After it returns, re-check "
                "`host.remote_gpu_status()` and continue with `host.fold` / "
                "`host.score_mutations` if verified.",
            ]
        )
    return "\n".join(lines)


_GATEWAY_PROMPT_EXTRA = """

You are not a "write one big script" agent. You work like a scientist at a bench: \
you look things up, prepare the environment, pull up the right protocol, run \
steps, inspect results, edit your report, and save deliverables. Each meaningful \
step produces a visible action card. A card may come from (a) an exact declared \
native JSON tool, (b) a foreground fenced Cell, or (c) a `host.*` RPC written \
inside a fenced Python Cell. These are distinct and are not interchangeable: \
`host.*` syntax is Python source, never a native function name, and a foreground \
Cell has no runner function. DO NOT collapse a whole analysis into a single Python \
dump; move one meaningful step at a time.

START INSTANTLY. Your FIRST move of a turn is the first concrete action (a search, \
a fetch, a code cell) — or simply the answer, if the question is conversational. \
Do NOT open with a plan: no upfront `host.todo_write`, no prose step list, no \
"here is my plan first". When the user wants to review a plan before execution they \
switch on Plan mode (which the server enforces and announces in the message); \
otherwise they chose instant execution, so deliver progress from the very first \
card. Only for a genuinely long campaign (≳4 distinct stages) may you drop a \
`host.todo_write` progress tracker — AFTER the work is visibly underway — and \
keep its statuses current as you go.

Recommended workflow for a real analysis task (mirror this — it is what the user \
expects to SEE happen, each as its own card):
1. SEARCH — MANDATORY whenever the task touches external facts, datasets, accession \
numbers, sequences, or published methods: call `host.web_search("...")` (and \
`host.web_fetch(url)` to read a hit) BEFORE you write any analysis code, and cite what \
you find. Do NOT answer such a task from memory or jump straight to synthetic/approximate \
data — look it up first; synthetic data is a fallback ONLY after a real fetch has failed. \
Make queries SMART: short keyword phrases (3–8 terms), never full sentences; put a DOI \
/ arXiv ID / accession directly in the query when you have one (identifier queries are \
routed to Crossref/arXiv automatically); if results look thin, CHANGE the terms \
(synonyms, a site: filter, the dataset name) instead of re-running the same query. \
Pure computation on data the user already supplied (or classic textbook math) needs no search.
2. PICK THE RIGHT ENVIRONMENT before importing domain packages. Several PREBUILT \
environments ship ready (each already stocked for a domain) — do NOT pip-install \
every task. Call `host.env.list(["biotite","mafft"])` to see them + which already has \
what you need, then `host.env.use("struct")` to run the following cells in it (switch \
in its own cell, import in the next). Rough guide: general data-science → `python`; \
structure / mmCIF / PDB / biotite → `struct`; sequence alignment & trees with REAL \
MAFFT/IQ-TREE/trimAl/FastTree → `phylo`; R/ggplot2/tidyverse → write ```r cells (they \
run on a persistent R kernel that resolves the prebuilt `r` env automatically; \
`host.env.use("r")` pins it explicitly, and ggsave() your plots so they are captured). \
Only if NO prebuilt env has the package, `host.env.create(name, [pkgs])` to pip-install it.
3. LOAD THE SKILL: when the declared native `search_skills` and `load_skill` \
functions are available, call those exact native functions directly. Inside a \
fenced Python Cell, use `host.search_skills(...)` and `host.load_skill(...)` \
instead. To enumerate or audit all skills, call the exact native `list_skills` \
function first, then call exact native `load_skill` with each returned `name`; \
catalog metadata is never a workspace path. Only inside a fenced Python Cell use \
`host.skills.list()` and then `host.skills.get(...)` / `host.skills.read(...)` as \
needed; do not use `list_dir` or `write_file`; do not use `read_text_file` or \
`glob_files` either. Never invent `run_python_cell` or fall back to \
`exec_background`.
4. GET DATA / RUN: to READ a paper, abstract, web page, or HTTP/JSON API (e.g. the \
GEO/PubMed/UniProt record behind an accession), use `host.web_fetch(url)` — it renders a \
visible "Reading …" card and IS the research step the user wants to see. Reserve \
`host.bash("curl -L ...")` for downloading BINARY or large data files (.gz, .h5, .tar, \
archives) that web_fetch would mangle; do NOT use curl/`requests` to read pages you could \
`host.web_fetch`. Then run normal Python cells (import the domain packages and run the \
real pipeline). Emit those cells directly as fenced assistant content.
5. WRITE THE REPORT with `host.write_file("summary_report.md", ...)` and refine it \
with `host.edit_file(...)` — these render as write/edit cards.
6. Save any deliverable files to the working directory (auto-captured as artifacts).

Output style (each code cell + each host.* call renders as an activity card):
- Write a short sentence of PROSE before each step explaining what you are about to \
do and why (this streams live to the user).
- A reply that contains an action is ordered as `public prose -> ONE tool batch or \
ONE code cell`, with the action LAST. Never place prose after the action fence and \
never predict stdout, files, metrics, or conclusions before execution. On the NEXT \
model turn, after the real tool result / Cell Observation is available, state the \
CONCRETE observed result that affects the next step in 1-3 short sentences. Report \
observations and conclusions, not hidden chain-of-thought. Activity cards alone are \
not a user-facing analysis.
- Keep each cell SMALL and focused on ONE action — one search, one env step, one \
skill load, one download, one figure, one edit. The timeline then reads as a clean \
sequence of steps, exactly like the reference. A leading `# gerund comment` on a \
pure-compute cell titles that card.
- Produce real result FILES for anything worth keeping (save plots with matplotlib \
`savefig`, tables with `df.to_csv`, reports via `host.write_file`). Every file you \
create in the working directory is AUTOMATICALLY captured as an artifact the user can \
open. You do NOT need to call `host.save_artifact`; writing the file is enough.
- Before calling `host.submit_output(...)`, write a short final one-paragraph prose \
summary based only on already-observed results and name the deliverable files. Put a \
PURE protocol-only submit cell last in that same reply; it is hidden from the \
Notebook. The submitted output should normally contain `summary`, `findings`, \
`metrics`, and `limitations` fields so the durable completion view remains useful.

Harness tools (an opencode-parity toolset, callable from any ```python cell as host.*):
- `host.todo_write([{content,status}]) / host.todo_read()` — OPTIONAL progress \
tracker for a long multi-stage task; never your first move of a turn (start with a \
real action instead); statuses ∈ pending|in_progress|completed.
- `host.plan_update(step_id, status)` — when auto-executing an APPROVED structured \
plan, tick a step (status ∈ pending|in_progress|completed|failed|skipped) so the plan \
review card checks it off live; `host.plan_read()` returns the approved plan + status.
- `host.web_search(query, num_results=8)` — LIVE web search → {results:[{title,url,\
snippet}]}; multi-engine with automatic fallback, and a DOI or arXiv ID in the query \
is answered straight from Crossref/arXiv.
- `host.web_fetch(url, format="markdown")` — download a page/API and get markdown/text/json.
- `host.env.list([pkgs])` — the PREBUILT environments (python/struct/phylo/r) + which \
already has what you need; `host.env.use("struct")` — run the next cells in one of them \
(no install needed); `host.env.create(name, [pkgs])` — pip-install into the current \
kernel only when NO prebuilt env has the package.
- `host.search_skills("...")` — find relevant skills; `host.load_skill("name")` — load \
one skill's full protocol (SKILL.md) and follow it.
- `host.bash(cmd, timeout=..., workdir=...)` — run a shell command INSIDE the kernel \
process, in your working directory (networking is on: curl/wget/git/pip all work; the \
host itself never executes shell — only your python/R cells do).
- `host.read_file / host.write_file / host.edit_file / host.glob / host.grep / \
host.list_dir` — file tools scoped to your working directory (edit_file does an exact \
string replace; grep/glob search your files).
- `host.remote_gpu_status()` — inspect configured remote GPU hosts and which real \
services are provisioned; `host.register_remote_capability(...)` — used by the remote \
GPU provisioning specialist after verifying a service on the SSH host.
- `host.delegate(request, name="SPECIALIST")` — hand a self-contained sub-task to a \
specialist; `host.mcp.call(server, tool, args)` — call a connector (MCP) tool.
`import requests`/`httpx` and raw Python are available too, but they do NOT replace \
`host.web_search`/`host.web_fetch`/`host.bash` for looking things up: the host tools \
render as activity cards, go through the network + provenance layer, and are what the \
user expects to SEE happen. For any external lookup, reach for `host.web_search` FIRST — \
do not silently substitute a raw-Python script for the visible research step.

Environment (this is a real, networked CPU kernel — NOT an offline sandbox):
- Networking is AVAILABLE. Prefer REAL data, and do the lookup with the VISIBLE web \
tools so the user sees it happen: `host.web_search("...")` to find papers/datasets/ \
accessions/methods, then `host.web_fetch(url)` to READ a hit or an HTTP/JSON record from \
NCBI/UniProt/PDB/Ensembl/GEO/arXiv/PubMed. Use `host.bash("curl -L ...")` ONLY to pull \
down the actual data files (`.gz`/`.h5`/archives) — not to read pages (that hides the \
research as a shell card). Prefer `host.web_fetch` over raw `requests`/`curl` for any \
readable page or API. Only fall back to synthetic/approximate data when a real fetch \
genuinely fails or is too large.
- Runtime packages differ by the session's selected environment. NEVER assume a \
package is installed merely because it is common: first use `host.env.list([pkg])` \
and switch to a reported prebuilt environment when needed. The base environment may \
contain only a small subset. Guard genuinely optional imports and use a stdlib or \
matplotlib fallback when the optional presentation package is not essential.
- If you DO need an extra package, FIRST check the prebuilt envs with `host.env.list([pkg])` \
and `host.env.use(...)` the one that has it — real MAFFT / IQ-TREE / trimAl / FastTree live \
in the `phylo` env, biotite in `struct`, the full DS stack in `python`. Only if none has it, \
`host.bash("pip install --break-system-packages <pkg>")` (a restart may be needed for a \
clean import). Never claim a package is "unavailable" before checking the envs or installing.
- REMOTE GPU SERVICES ARE DYNAMIC — do not assume folding/scoring services are already \
provisioned just because a GPU host exists. Inspect `host.remote_gpu_status()` when a task \
needs GPU-only protein models. If `fold` is registered, call `host.fold(sequence, \
name="...")`: it runs the real remote folder and returns `{pdb, plddt_csv, confidence, \
mean_plddt, ptm, length}`. Write the model with `host.write_file("<name>_model.pdb", \
result["pdb"])` so it opens in the 3D viewer, and plot per-residue pLDDT from \
`result["plddt_csv"]` (chain,resid,resname,plddt). NEVER hand-write a synthetic backbone, \
a geometric spiral, or a "placeholder" `.pdb`, and NEVER fabricate a pLDDT curve. If a \
remote GPU host exists but `fold` / `score_mutations` / another requested GPU service is \
missing, first delegate a provisioning sub-task to \
`host.delegate(..., name="REMOTE_GPU_PROVISIONER")`; only report unavailable after that \
specialist verifies provisioning cannot be completed.
- NO FABRICATION — absolute rule. NEVER invent scientific results with `np.random`, \
hardcoded numbers, or synthetic stand-in data, and NEVER present a heuristic as if it were \
a deep-learning model or a real measurement. Specifically forbidden: randomised or made-up \
mutation/variant scores; fake "conservation" not computed from a REAL alignment; \
hand-written / placeholder / spiral structures; invented datasets; simulated off-target \
sets; and "method comparison" figures of numbers you made up. A smaller HONEST result \
beats a rich fabricated one.
- Real capabilities go to the real service; if a remote GPU exists but a required service \
is not available, FIRST delegate to `REMOTE_GPU_PROVISIONER` to provision/verify it. If \
provisioning fails, ERROR OUT and say so — do NOT substitute fabricated data:
    * 3D structure → `host.fold(sequence, ...)` after `fold` is registered.
    * mutation / variant-effect scores → `host.score_mutations(sequence, ...)` (real ESM \
on the remote GPU), which returns real per-substitution scores. If this raises because no \
scoring service is configured or the host is unreachable, delegate provisioning once and \
retry only if a verified service is registered; otherwise report that this step cannot be \
done for real — do NOT fall back to BLOSUM-as-ESM, random noise, or a fake heatmap. \
(BLOSUM62 / physicochemical deltas / entropy from an alignment you ACTUALLY built may \
appear ONLY as clearly-labelled descriptive annotations — never as a predictor, never \
randomised, never labelled ESM/ProteinMPNN.)
    * any other GPU-only model with no real service here (off-targets at scale, etc.) → \
report it as not-yet-available for this session rather than simulating it.
- REAL data only: fetch actual records (NCBI/UniProt/PDB/GEO/Ensembl via `host.web_fetch` \
or the DB API). If a fetch genuinely fails, report the failure and proceed with what you \
DID retrieve — never GENERATE a synthetic dataset to stand in for real data.
- Genuinely-real CPU tools are NOT fabrication — run them for real: MAFFT / IQ-TREE / \
trimAl / FastTree (`host.env.use("phylo")`), and scanpy/Leiden/UMAP/DE on REAL fetched data.
- `host` is already injected as a global — call `host.fold(...)` etc. directly; NEVER \
write `import host` (there is no such module). `host.submit_output(...)` takes \
`completion_bullets` as a list of 1–4 short strings.
- Deliverables: generate the FULL set of figures (publication-quality matplotlib PNGs), \
CSV/JSON tables, a Markdown or HTML report, and any structure/sequence files the task \
asks for — matching the shape of a top scientist's answer. Do the ENTIRE task \
end-to-end (all steps), not just the first step.
- No intermediate clutter: only write meaningful FINAL deliverables to the working dir. \
Do NOT leave scratch/temp files (use /tmp or delete them). Reference any file over ~1 MB \
by name in the summary instead of linking it. When you need a tool/repo (e.g. \
`git clone`, download model weights, `pip install --target`), put it in /tmp or a scratch \
dir OUTSIDE the working directory and run it from there — the working dir is for \
deliverables only, NEVER a checkout of a cloned repo and its weights/examples.
- If an input file is attached (mentioned in the task), it has been placed in your \
working directory — just open it by its filename.
"""


_EXPLORE_PROTOCOL = """\
[EXPLORE MODE — autonomous deep exploration]
Treat the question above as an open-ended research task and drive it END-TO-END \
on your own. The user is away: do not ask questions or wait for confirmation.
Protocol:
1. DECOMPOSE the question into concrete sub-questions and lay them out with \
`host.todo_write([...])`; keep statuses current as you work.
2. GROUND every claim in real evidence: `host.web_search` / `host.web_fetch` for \
literature and facts, public datasets/APIs for numbers. Prefer real data; label \
any synthetic fallback clearly.
3. ANALYZE quantitatively: run the actual computation, don't just narrate. \
Produce publication-quality figures (savefig) and tables (to_csv) as you go.
4. SELF-CHECK before finishing: re-read your sub-questions — is each answered \
with evidence? Are numbers sanity-checked (units, magnitudes)? If a result looks \
off, investigate it; note remaining uncertainties honestly.
5. DELIVER a final `report.md` via `host.write_file` that a domain scientist \
could act on: question, methods, quantified findings (with figures/tables \
referenced by filename), limitations, and cited sources (URLs).
The task is NOT complete until you call `host.submit_output({...}, [...])` — \
prose alone never ends an exploration."""

#: How long a cell may wait for this session's cluster worker to dial in.
#: Short on purpose: the queue wait belongs to the reconciler and to the
#: readiness projection, not to a request thread. A worker that has not
#: arrived yet leaves the session local for this attempt and is asked for
#: again on the next one.
_REMOTE_ATTACH_TIMEOUT_S = 5.0

_EXPLORE_NUDGE = (
    "[system] Explore mode: the investigation is not finished — no "
    "host.submit_output(...) call has run. Continue with the next "
    "```python step (finish remaining todo items, verify results, "
    "write report.md), then call host.submit_output(...)."
)

_SUBMIT_NUDGE = (
    "[system] Prose is not a completion signal. If this conversational or "
    "tool-only task is complete, call finalize_response as the ONLY native "
    "tool call on the next turn; do NOT start a Python/R kernel merely to "
    "finish. If scientific runtime work is still required, continue with one "
    "complete ```python or ```r cell, and finish that scientific work with "
    "host.submit_output(...)."
)


def _submit_nudge_for(llm_cfg) -> str:
    """Choose a completion route the configured endpoint can actually emit."""

    try:
        capabilities = get_model_capabilities(
            getattr(llm_cfg, "provider", ""),
            getattr(llm_cfg, "model", ""),
            base_url=getattr(llm_cfg, "base_url", ""),
        )
    except Exception:  # noqa: BLE001 - preserve compatible provider behavior
        return _SUBMIT_NUDGE
    return _SUBMIT_NUDGE if capabilities.tool_calling else NO_NATIVE_COMPLETION_NUDGE


class SessionRunner:
    def __init__(
        self,
        cfg: Config,
        hub: WSHub,
        *,
        clock=None,
        start_idle_sweeper: bool = True,
    ) -> None:
        self.cfg = cfg
        self.hub = hub
        self._clock = clock or time.time
        self._owner_instance_id = PROCESS_INSTANCE_ID
        self.store = get_store(cfg.db_path)
        self.skills = SkillLoader(cfg=cfg)
        self._sessions: dict[str, SessionState] = {}
        self._jobs: dict[str, MessageJob] = {}
        #: The row an in-flight turn has already written as its terminal
        #: failure, so the *outer* handler for the same turn amends it rather
        #: than appending a second one.
        #:
        #: Keyed by job id and cleared when that job's function returns, which
        #: is the only lifetime that is correct. Keyed by request id it was a
        #: leak with teeth: only the outer handler consumed a note, so an
        #: ordinary inner failure left one behind forever, and a client reusing
        #: `X-Request-Id` -- which clients do -- had its next unrelated failure
        #: amend a finished turn's message and record nothing of its own.
        self._terminal_failures: dict[str, dict] = {}
        #: Which job the current thread is running, so `run_message` can file
        #: its note without being handed the ticket.
        self._turn_scope = threading.local()
        self._lock = threading.Lock()
        self._closed = False
        self._deleting_projects: set[str] = set()
        # One per daemon, so the startup opt-in and the on-demand route cannot
        # both be seeding the example at the same time.
        self.example_seed = _ExampleSeedState()
        self.executions = WebExecutionCoordinator(
            lambda root_frame_id, event: self.hub.emitter(root_frame_id)(event),
            clock=self._clock,
        )
        # Compatibility spelling used by recovery/runtime probes.
        self.coordinator = self.executions
        self._turn_local = threading.local()
        self.reviews = ReviewService(
            store=lambda: self.store,
            lock=self._lock,
            jobs=self._jobs,
            ports=ReviewPorts(
                state_for=lambda root_frame_id, project_id: self._state(
                    root_frame_id, project_id
                ),
                emitter_for=lambda root_frame_id: self.hub.emitter(root_frame_id),
                llm_config_for=lambda state: self._llm_cfg(state),
                review_evidence=lambda evidence, config, root_frame_id: (
                    self.enforce_llm_quota(root_frame_id),
                    review_evidence(evidence, config),
                )[1],
                providers=lambda: PROVIDERS,
                clean_api_key=lambda value: _clean_api_key(value),
                resolve_profile_key=lambda profile: _resolve_profile_key(
                    self.store, profile
                ),
                job_factory=lambda job_id, root_frame_id: MessageJob(
                    job_id, root_frame_id
                ),
                busy_error=lambda code, message: GatewayError(code, message),
                run_reviewer=lambda *args, **kwargs: self._run_reviewer(
                    *args, **kwargs
                ),
                review_config_for=lambda state: self._review_llm_cfg(state),
                artifact_excerpt=lambda artifact: self._review_artifact_excerpt(
                    artifact
                ),
            ),
        )
        self._review_ops = self.reviews.operations
        self._review_calls = self.reviews.provider_calls
        self._ws_root = cfg.data_dir / "agent-workspaces"
        self._ws_root.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactManager(
            data_dir=cfg.data_dir,
            store=self.store,
            workspace_for=self.active_workspace_for,
            broadcast=getattr(
                self.hub,
                "broadcast",
                lambda root_frame_id, event: self.hub.emitter(root_frame_id)(event),
            ),
            guess_content_type=_guess_ctype,
            checksum=_sha256,
        )
        self.session_domain = SessionDomainService(
            self.store,
            data_dir=self.cfg.data_dir,
            workspace=self.workspace_for_branch,
            event_sink=lambda event: self.hub.emitter(event["root_frame_id"])(event),
        )
        # Web share: an outbound read-only snapshot tunnel. The tunnel client is
        # created lazily and only when sharing is both enabled and configured, so
        # a default install starts zero share network threads.
        self._share_tunnel = None
        self._share_router = None
        share_builder = ShareProjectionBuilder(
            self.store,
            data_dir=self.cfg.data_dir,
            workspace=self.workspace_for_branch,
            cas=self.session_domain.cas,
            extra_secret_values=lambda: (
                (self.cfg.share.auth_token,) if self.cfg.share.auth_token else ()
            ),
        )
        self.shares = ShareService(
            self.store,
            builder=share_builder,
            shares_dir=self.cfg.shares_dir,
            public_url=self.cfg.share.public_url,
            active_branch=self.store.active_session_branch,
            run_in_ticket=self._share_run_in_ticket,
            tunnel=None,
        )
        self._share_router = ShareRouter(self.shares, _load_share_assets())

        # Cluster orchestration (M3a). Lazy in spirit: the local backend
        # spawns nothing until a workload asks, the cluster backend is only
        # constructed when cluster.toml configures one, and the reconciler
        # thread starts only when there is a backend for it to drive.
        self.orchestration_backends: dict[str, Any] = {}
        self.default_backend = "local"
        self.cluster_config = None
        self.reconciler = None
        self._init_orchestration()
        self.deletions = SessionDeletionService(
            self.store,
            data_dir=self.cfg.data_dir,
            cas=self.session_domain.cas,
            drop_runtime=lambda root_frame_id, reason: self.drop_session(
                root_frame_id, reason=reason
            ),
            drop_resume_window=getattr(
                self.hub, "drop_frame", lambda _root_frame_id: None
            ),
            revoke_shares=self.shares.revoke_for_session,
            release_compute=self._release_session_compute,
        )
        self.sidecar_manifests = GenerationSidecarRecorder(self.store)
        self.workbench = SessionWorkbenchStateService(
            self.store,
            state_for=self._existing_state,
            history_for=lambda root_frame_id: restore_action_history(
                self.store,
                root_frame_id,
                branch_id=self.store.active_session_branch(root_frame_id),
            ),
            llm_config_for=lambda state: self._llm_cfg(state),
            pending_for=self._pending_permissions,
            context_window_fallback=self.cfg.context_window_tokens,
            tool_schemas_for=lambda state: (
                state.dispatcher.tool_catalog().specs_for(state.messages)
                if state is not None and state.dispatcher is not None
                else ()
            ),
        )
        self.plans = PlanService(
            store=self.store,
            emitter_for=lambda root_frame_id: self.hub.emitter(root_frame_id),
            run_message=lambda *args, **kwargs: self.run_message(*args, **kwargs),
        )
        self.titles = SessionTitleService(
            store=lambda: self.store,
            broadcast=lambda root_frame_id, event: self.hub.broadcast(
                root_frame_id, event
            ),
            chat_call=lambda messages, llm_cfg, **kwargs: chat(
                messages, llm_cfg, **kwargs
            ),
            summarize_call=lambda user_text, llm_cfg: self._summarize_title(
                user_text, llm_cfg
            ),
        )
        self.cells = CellExecutionService(
            CellExecutionPorts(
                prepare_language=self._prepare_language,
                kernel_id=lambda st, language: (
                    self._r_kernel_id(st) if language == "r" else self._kernel_id(st)
                ),
                snapshot=self.artifacts.snapshot,
                protect_versions=self.artifacts.protect_latest,
                safety_refusal=lambda st, code, origin: (
                    self._safety_refusal(st, code, origin)
                ),
                run=lambda st, request, cell_id, on_chunk, lease: (
                    self._execute_with_watchdog(
                        st,
                        request.code,
                        request.origin,
                        on_chunk,
                        language=request.language,
                        lease=lease,
                        cell_id=cell_id,
                        action_group_id=request.action_group_id,
                    )
                ),
                capture=self._capture_artifacts,
                emit_artifact_step=self._emit_artifact_step,
                record_cell=self._record_cell_with_cursor_checkpoint,
                allocate_attempt=self._allocate_cell_attempt,
                bind_attempt_generation=self._bind_cell_attempt_generation,
                mark_attempt_started=lambda attempt_id: (
                    self.store.mark_execution_attempt_started(attempt_id)
                ),
                mark_attempt_response=lambda attempt_id: (
                    self.store.mark_execution_attempt_response(attempt_id)
                ),
                mark_attempt_capture=lambda attempt_id: (
                    self.store.mark_execution_attempt_capture(attempt_id)
                ),
                finish_attempt=lambda attempt_id, terminal_state, error: (
                    self.store.finish_execution_attempt(
                        attempt_id,
                        terminal_state=terminal_state,
                        error=error,
                    )
                ),
            )
        )
        self.recovery = SessionRecoveryService(
            store=self.store,
            sessions=self._session_snapshot,
            turn_active=self._execution_active,
            approval_pending=self._permission_pending,
            background_active=self._background_active,
            background_last_activity_ms=self._background_last_activity_ms,
            release_idle=self._release_idle_session,
            owner_instance_id=self._owner_instance_id,
            clock=self._clock,
        )
        self.recovery.reconcile_startup()
        self.variables = VariableInspectorService(
            state_for=self._existing_state,
            execution_snapshot=self.executions.snapshot,
            recovering=self.recovery.is_recovering,
            latest_generation=self.store.latest_kernel_generation,
            latest_state_revision=self.store.latest_state_revision,
            active_branch=self.store.active_session_branch,
        )
        if start_idle_sweeper:
            self.recovery.start()
            self._share_boot_restore()
            self._recover_stranded_admissions()

    def _recover_stranded_admissions(self) -> int:
        """Release pins held by a request that did not survive the process.

        A daemon that dies between reserving and finalising leaves `reserved`
        rows nothing will ever release: not sent, not available, and invisible
        in the composer forever. At startup no request is in flight by
        definition, so anything still held is stranded. Best-effort, because a
        recovery pass must not be the reason a daemon fails to boot.
        """
        try:
            recovered = self.store.recover_stranded_admissions()
        except Exception:  # noqa: BLE001 - never block startup
            traceback.print_exc()
            return 0
        if recovered:
            print(
                f"[openai4s] released {recovered} pinned comment(s) held by a "
                "request that did not finish",
                file=sys.stderr,
            )
        return recovered

    def _live_delegation_child(self, root_frame_id: str, child_id: str):
        """The live child a control action can actually reach, or a refusal.

        Three answers, and collapsing any two of them tells the user something
        untrue:

          404  no such child in the durable record — it never existed here
          409  the record has it, but nothing live can act on it
          ok   a running child this runner owns

        The 409 case is the interesting one and it is ordinary rather than
        exotic. A daemon restart marks every `pending`/`running` child
        `stopped` with `stop_reason='daemon_restart'` and discards queued
        steering, so a page opened before the restart is holding ids for
        children that are gone. Answering 404 there would say "that never
        existed" about work the user watched run; answering 200 would claim a
        stop that stopped nothing.
        """
        tree = self.store.delegation_tree(root_frame_id) or {}
        record = next(
            (
                child
                for child in (tree.get("children") or [])
                if str(child.get("child_id") or "") == child_id
            ),
            None,
        )
        if record is None:
            raise GatewayError(404, f"no such sub-agent {child_id}", "not_found")

        state = self._existing_state(root_frame_id)
        runner = state.delegation_runner if state is not None else None
        if runner is None:
            raise GatewayError(
                409,
                "this sub-agent belongs to a run that is no longer active; "
                "reload the session to see its final state",
                "delegation_record_stale",
            )
        try:
            with runner._tree.lock:  # noqa: SLF001 - same module boundary
                live = runner._children.get(child_id)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - a broken runner is a stale record
            live = None
        if live is None:
            raise GatewayError(
                409,
                f"sub-agent {child_id} is recorded as "
                f"'{record.get('status') or 'unknown'}' and cannot be steered "
                "or stopped from here",
                "delegation_record_stale",
            )
        return runner, record

    def stop_delegation_subtree(self, root_frame_id: str, child_id: str) -> dict:
        """Stop one child and everything below it, and nothing beside it.

        `_stop_subtree` walks `descendants`, which follows `parent_child_id`,
        so a sibling is structurally outside the walk rather than spared by a
        filter somebody has to remember.
        """
        runner, _record = self._live_delegation_child(root_frame_id, child_id)
        try:
            return runner._stop_subtree(child_id, "stopped by user")  # noqa: SLF001
        except KeyError as error:
            # Lost the race with its own completion between the check and here.
            raise GatewayError(
                409, f"sub-agent {child_id} finished first", "delegation_record_stale"
            ) from error

    def steer_delegation_child(
        self, root_frame_id: str, child_id: str, message: str
    ) -> dict:
        """Queue a message for delivery at the child's next turn boundary.

        Never mid-turn: a child that received text in the middle of a tool call
        would act on it with half its own reasoning already committed.
        """
        text = str(message or "").strip()
        if not text:
            raise GatewayError(400, "message is required", "bad_request")
        if len(text) > MAX_MESSAGE_CHARS:
            raise GatewayError(
                413,
                f"steering message is {len(text):,} characters; the limit is "
                f"{MAX_MESSAGE_CHARS:,}",
                "message_too_large",
            )
        runner, _record = self._live_delegation_child(root_frame_id, child_id)
        result = runner.send_message({"child_id": child_id, "message": text})
        if not result.get("ok"):
            # `send_message` answers a refusal with `{"ok": False, …}` and a
            # 200 would carry it as success. A child that reached a terminal
            # state between the read and the send is precisely a stale record.
            raise GatewayError(
                409,
                str(result.get("reason") or "the sub-agent is no longer accepting"),
                "delegation_record_stale",
            )
        return result

    def refresh_compute_task(self, root_frame_id: str, job_id: str) -> dict:
        """Contact the remote for ONE job, because a person asked.

        `ComputeManager.result()` is the probe, and in this system the probe is
        also the harvest: it pulls output files back into the workspace and
        closes the job. There is no read-only way to ask a provider how a job is
        doing, which is the whole reason the listing beside this does not poll.

        The manager does **not** register artifacts -- this docstring used to say
        it did, and nothing on this route took a snapshot, so a person clicking
        Refresh got the bytes published into `hpc/<job_id>/` and no Artifact
        version, no Timeline entry and no lineage. Capture is bracketed around the
        harvest here, the same way the native control-tool wrapper does it for
        `compute_result`; the mtime diff needs a `before` taken while the files do
        not exist yet, so it cannot be added after the fact.

        The manager is built with this session's workspace, so its owner scope
        is the same one the listing reads. A job id belonging to another
        session resolves to "no such job" through the manager's own
        owner-scoped `_jobs` map -- the same predicate `job_history` uses, and
        for the same reason: a distinct refusal would confirm the job exists.
        """
        workspace = self.active_workspace_for(root_frame_id)
        dispatcher = build_dispatcher(
            self.cfg, frame_id=root_frame_id, workspace=workspace
        )
        try:
            manager = dispatcher.compute
        except Exception as error:  # noqa: BLE001 - no provider configured
            # The reason used to be interpolated in. It is raised by provider
            # shim code loaded from `skills/remote-compute-<id>/provider.py`,
            # so its text is whatever a third party wrote -- routinely the
            # config path it read and the env var it could not find.
            record_diagnostic(
                error, surface="compute:provider", request_id=correlation_id()
            )
            raise GatewayError(
                503, "remote compute is not available here", "no_provider"
            ) from error
        st = self._state(root_frame_id, "default")
        emit = self.hub.emitter(root_frame_id)
        before = self.artifacts.snapshot(workspace)
        self.artifacts.protect_latest(st)
        try:
            outcome = manager.result({"job_id": job_id})
        except Exception as error:  # noqa: BLE001
            # `not_found` is a client error, not a server fault; anything else
            # is the remote or the transport failing, which the user can retry.
            code = getattr(error, "kind", "") or getattr(error, "code", "")
            if str(code) == "not_found":
                raise GatewayError(404, f"no such job {job_id}", "not_found") from error
            # The provider's own text does not go in. A remote SDK's error
            # quotes the endpoint it called, the credential prefix it used and
            # the *provider's* request id -- and that last one is the worst of
            # the three, because it reads like the id to quote in a support
            # ticket while naming a request neither the user nor this daemon
            # can look up. `public_exception` answers with this daemon's local
            # correlation id instead, and the original reaches the operator
            # diagnostic only.
            record_diagnostic(
                error, surface="compute:refresh", request_id=correlation_id()
            )
            raise GatewayError(
                502, "remote compute refresh failed", "refresh_failed"
            ) from error
        finally:
            # In `finally`, not after: a harvest that extracted some outputs and
            # then failed has still written real bytes into the workspace, and
            # leaving those unregistered is the same gap on a narrower path.
            try:
                self.artifacts.capture(
                    st,
                    st.cell_index,
                    None,
                    before,
                    emit,
                    language="native",
                    # The one path whose files genuinely came from another
                    # machine, and the only capture call that was not draining.
                    # Two consequences, both of them the failure this subsystem
                    # exists to prevent. A harvested artifact was stamped with
                    # the *local* environment and carried no record of the host
                    # that produced it. And because the drain never ran here,
                    # the remote entry stayed buffered and was attached to
                    # whatever cell wrote a file next -- the fold in cell 3
                    # becoming the provenance of a figure from cell 7, which
                    # the comment in `capture` describes as already fixed.
                    drain_remote_provenance=self._remote_provenance_drain(st),
                )
            except Exception:  # noqa: BLE001
                # Capture must not convert a successful harvest into an error;
                # the files remain on disk and the next capture will see them.
                pass
        # Project the durable record rather than the call's return value, so
        # the refreshed row and the listing beside it are the same shape from
        # the same source. `hasattr`-guarding this would have hidden the fact
        # that the method was named something else -- a guard that always takes
        # the fallback looks like tolerance and is really a silent miss.
        record = self.store.get_compute_job(job_id) or {"job_id": job_id, **outcome}
        task = compute_tasks.public_task(record)
        # Named, because this is the one response in the pair that DID reach a
        # provider. The listing says `polled: False` for the same reason.
        task["polled"] = True
        return task

    def workspace_for(self, root_frame_id: str) -> Path:
        ws = self._ws_root / root_frame_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def workspace_for_branch(self, root_frame_id: str, branch_id: str) -> Path:
        """Return an isolated writable directory for a checkpoint branch."""

        if branch_id == root_frame_id:
            return self.workspace_for(root_frame_id)
        root_key = hashlib.sha256(root_frame_id.encode("utf-8")).hexdigest()[:24]
        branch_key = hashlib.sha256(branch_id.encode("utf-8")).hexdigest()[:24]
        workspace = self._ws_root / ".branches" / root_key / branch_key
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def active_workspace_for(self, root_frame_id: str) -> Path:
        """Resolve the live workspace without guessing the canonical branch."""

        state = self._existing_state(root_frame_id)
        if state is not None:
            return state.workspace
        branch_id = self.store.active_session_branch(root_frame_id)
        return self.workspace_for_branch(root_frame_id, branch_id)

    def _existing_state(self, root_frame_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(root_frame_id)

    def _pending_permissions(self, root_frame_id: str) -> list[dict]:
        try:
            from openai4s.permissions import broker

            return list(broker().pending_events(root_frame_id, store=self.store))
        except Exception:  # noqa: BLE001 - status fails closed to no payload
            return []

    def _session_snapshot(self) -> list[SessionState]:
        with self._lock:
            return list(self._sessions.values())

    def _execution_active(self, root_frame_id: str) -> bool:
        """Cover current MessageJobs and a present/future coordinator queue."""

        if self.is_running(root_frame_id):
            return True
        coordinator = getattr(self, "coordinator", None)
        if coordinator is None:
            with self._lock:
                state = self._sessions.get(root_frame_id)
            coordinator = getattr(state, "coordinator", None) if state else None
        if coordinator is None:
            return False
        try:
            snapshot = coordinator.snapshot(root_frame_id)
            owner = snapshot.get("owner")
            current = self.executions.current(root_frame_id)
            owns_only_recovery_ticket = bool(
                current
                and current.owner.kind == "recovery"
                and owner
                and owner.get("execution_id") == current.execution_id
                and not snapshot.get("queued_count")
                and not snapshot.get("queue")
            )
            return bool(
                not owns_only_recovery_ticket
                and (owner or snapshot.get("queued_count") or snapshot.get("queue"))
            )
        except Exception:  # noqa: BLE001 — unknown coordinator state is occupied
            return True

    @staticmethod
    def _permission_pending(root_frame_id: str) -> bool:
        try:
            from openai4s.permissions import broker

            return bool(broker().is_pending(root_frame_id))
        except Exception:  # noqa: BLE001 — telemetry cannot release a kernel
            return True

    @staticmethod
    def _background_jobs(st: SessionState) -> list[dict]:
        dispatcher = st.dispatcher
        executor = getattr(dispatcher, "_bg_executor", None) if dispatcher else None
        if executor is None:
            return []
        try:
            return list(executor.list_jobs())
        except Exception:  # noqa: BLE001 — unknown background state is occupied
            return [{"status": "running"}]

    def _background_active(self, st: SessionState) -> bool:
        return any(
            str(job.get("status") or "").lower() == "running"
            for job in self._background_jobs(st)
        )

    def _background_last_activity_ms(self, st: SessionState) -> int | None:
        timestamps = [
            int(value)
            for job in self._background_jobs(st)
            for value in (job.get("ended_at"), job.get("started_at"))
            if isinstance(value, (int, float))
        ]
        return max(timestamps) if timestamps else None

    def _interrupt_background(self, st: SessionState) -> None:
        dispatcher = st.dispatcher
        executor = getattr(dispatcher, "_bg_executor", None) if dispatcher else None
        if executor is None:
            return
        shutdown = getattr(executor, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:  # noqa: BLE001 — continue session cleanup
                pass
            return
        for job in self._background_jobs(st):
            if str(job.get("status") or "").lower() != "running":
                continue
            try:
                executor.interrupt(job["exec_id"])
            except Exception:  # noqa: BLE001 — cleanup remains best-effort
                pass

    def _release_idle_session(self, st: SessionState, reason: str) -> bool:
        """Cross the session barrier and release both slots if still eligible."""

        emit = self.hub.emitter(st.root_frame_id)
        with st.stop_lock:
            ticket = self.executions.submit(
                st.root_frame_id,
                owner="recovery",
                owner_id=f"idle-{uuid.uuid4().hex[:12]}",
                branch_id=st.branch_id,
                resource_keys=("workspace", "kernel:python", "kernel:r"),
                metadata={"reason": reason},
            )
            try:
                with self.executions.admitted(
                    ticket, cancel_event=st.cancel, timeout=0.0
                ):
                    # A pre-coordinator compatibility holder may still own the
                    # old lock. Never let the sweeper wait for it.
                    if not st.turn_lock.acquire(blocking=False):
                        return False
                    try:
                        # Admission is now closed. Recheck every external blocker
                        # so an optimistic sweeper snapshot cannot win a race.
                        if self.recovery.blocked(st) or not self.recovery.idle_expired(
                            st
                        ):
                            return False
                        stopped = st.kernels.stop("python", manual=False, reason=reason)
                        stopped += st.kernels.stop("r", manual=False, reason=reason)
                        if stopped:
                            # The provider history is the largest thing a cold
                            # session holds — measured at ~1.1 MB for a 200-turn
                            # conversation, and essentially all of a
                            # SessionState's resident cost. The sweeper has just
                            # decided this session is cold enough to tear its
                            # kernels down, and ``_seed_messages`` rebuilds the
                            # history from ``restore_action_history`` because the
                            # store is the canonical provider history. So this
                            # leaves the session in exactly the state a daemon
                            # restart leaves it in — a state every reader already
                            # handles, since after a restart no session is
                            # resident. What it stops is a daemon accumulating
                            # every conversation it has ever served: nothing
                            # removed a SessionState from ``_sessions`` short of
                            # an explicit close, so 100 idle sessions held 110 MB
                            # of history for kernels that no longer existed.
                            st.messages = []
                            st.context_omissions = {}
                    finally:
                        st.turn_lock.release()
                    if not stopped:
                        return False
                    status = st.kernels.status("python")
                    self.executions.mark_finalizing(
                        ticket, reason="publishing idle kernel release"
                    )
                    emit(
                        {
                            "type": "kernel_status",
                            "frame_id": st.root_frame_id,
                            "status": "ended",
                            "state": "ended",
                            "generation_id": status.get("generation_id"),
                            "ended_reason": reason,
                        }
                    )
                    return True
            except (ExecutionCancelled, TimeoutError):
                return False

    def drop_session(
        self, root_frame_id: str, *, reason: str = "session_closed"
    ) -> bool:
        """Cancel and fully detach one in-memory session before deletion/close."""

        with self._lock:
            st = self._sessions.get(root_frame_id)
        if st is None:
            return False
        with st.stop_lock:
            st.stop_finished.clear()
            st.stop_requested.set()
            try:
                self._cancel_current_for_lifecycle(
                    root_frame_id,
                    reason=reason,
                )
                self.cancel_review(root_frame_id)
                self.executions.close_session(root_frame_id, reason=reason)
                runner = st.delegation_runner
                if runner is not None:
                    runner.close(cancel=True)
                    st.delegation_runner = None
                self._interrupt_background(st)
                with st.turn_lock:
                    st.kernels.stop("python", manual=False, reason=reason)
                    st.kernels.stop("r", manual=False, reason=reason)
            finally:
                st.stop_requested.clear()
                st.stop_finished.set()
        with self._lock:
            self._sessions.pop(root_frame_id, None)
        try:
            from openai4s.permissions import broker

            broker().unregister_channel(root_frame_id)
        except Exception:  # noqa: BLE001 — session resources are already stopped
            pass
        return True

    def delete_session(self, root_frame_id: str) -> dict[str, Any]:
        return self.deletions.delete_session(root_frame_id)

    def _may_create_session_in(self, project_id: str, user_id: str) -> bool:
        """Whether this user may put a session in this project.

        An unclaimed project -- no members, nobody else's sessions -- stays
        open, which is what keeps a fresh install's seeded `default` project
        usable before anybody has organised anything. A project somebody
        has claimed needs participation.
        """
        from openai4s.server import team_policy

        if not getattr(self.cfg, "team_mode", False):
            return True
        try:
            user = self.store.team.get_user(user_id)
        except Exception:  # noqa: BLE001
            user = None
        if user is None:
            return True  # service/loopback identity, or team mode not seeded
        if str(user.get("role") or "") == "admin":
            return True

        class _Id:
            def __init__(self, uid: str) -> None:
                self.user_id = uid
                self.is_admin = False

        return team_policy.may_create_session_in(self.store, _Id(user_id), project_id)

    def create_session(
        self,
        project_id: str,
        *,
        model: str | None = None,
        owner_user_id: str | None = None,
    ) -> str:
        """Create a root frame atomically with project-deletion admission.

        ``owner_user_id`` (team mode, M1-6) records the session's owner in
        the same locked section as the frame insert, so no enumeration can
        observe the frame before its ownership row exists.
        """

        if owner_user_id:
            # Session-creation quota (M2-6). By frozen decision, a *broken*
            # quota check admits the request and leaves an audit row —
            # availability over bookkeeping.
            try:
                self.store.governance.check_quota(
                    user_id=owner_user_id,
                    project_id=project_id,
                    kind="sessions_created",
                )
            except QuotaExceeded as e:
                raise GatewayError(429, str(e), "QUOTA_EXCEEDED") from e
            except Exception as e:  # noqa: BLE001
                try:
                    self.store.team.audit(
                        actor=owner_user_id,
                        action="quota_check_failed",
                        detail=str(e)[:200],
                    )
                except Exception:  # noqa: BLE001
                    pass
        with self._lock:
            if project_id in self._deleting_projects:
                raise GatewayError(409, "project deletion is in progress")
            if self.store.get_project(project_id) is None:
                raise GatewayError(404, "project not found")
            if owner_user_id and not self._may_create_session_in(
                project_id, owner_user_id
            ):
                # Creating a session here also *joins* the project, because
                # participation is "a membership row OR a session of mine in
                # it". Unauthorized, that is a self-join: name somebody
                # else's project, post a frame, and become a participant of
                # it. 404 rather than 403, matching the project guard --
                # which projects exist is itself protected.
                raise GatewayError(404, "project not found")
            fid = self.store.new_frame(
                kind="turn",
                project_id=project_id,
                model=model,
                status="ready",
            )
            if owner_user_id:
                self.store.team.set_session_owner(
                    fid, owner_user_id, project_id=project_id
                )
                try:
                    self.store.governance.record_usage(
                        user_id=owner_user_id,
                        kind="sessions_created",
                        amount=1,
                        project_id=project_id,
                        ref=fid,
                    )
                except Exception:  # noqa: BLE001
                    pass
            return fid

    def delete_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            if project_id in self._deleting_projects:
                raise GatewayError(409, "project deletion is already in progress")
            self._deleting_projects.add(project_id)
        try:
            return self.deletions.delete_project(project_id)
        finally:
            with self._lock:
                self._deleting_projects.discard(project_id)

    def _init_orchestration(self) -> None:
        """Build the backends this daemon can reach, and start the loop.

        Kept out of __init__ proper so a failure here — a malformed
        cluster.toml above all — degrades to "local only" with a printed
        reason rather than refusing to boot. An operator's typo in a cluster
        file should not take the workbench down.
        """
        from openai4s.orchestration.local import LocalBackend
        from openai4s.orchestration.reconciler import Reconciler

        log_dir = self.cfg.data_dir / "orchestration-logs"
        self.orchestration_backends["local"] = LocalBackend(log_dir=log_dir)

        try:
            from openai4s.orchestration.slurm import (
                ClusterConfigError,
                SlurmBackend,
                load_cluster_config,
            )

            cluster = load_cluster_config(self.cfg.data_dir)
            self.cluster_config = cluster
            if cluster.configured:
                self.orchestration_backends["cluster"] = SlurmBackend(
                    cluster=cluster, log_dir=str(log_dir)
                )
        except ClusterConfigError as exc:
            print(
                f"[openai4s] cluster.toml ignored: {exc}", file=sys.stderr, flush=True
            )
        except Exception as exc:  # noqa: BLE001 — never block boot on this
            print(
                f"[openai4s] cluster configuration unavailable: {exc}",
                file=sys.stderr,
                flush=True,
            )

        # The cadence is the plan's 5s by default. It is settable because
        # every end-to-end test of this subsystem otherwise spends its life
        # waiting for the next tick — and a test suite that takes minutes to
        # say "the batch pipeline works" gets run less often, which is the
        # expensive kind of slow.
        try:
            interval_s = float(os.environ.get("OPENAI4S_RECONCILE_INTERVAL", "5"))
        except ValueError:
            interval_s = 5.0
        # A worker listener, a session manager and a lease reclaimer — all
        # three only when an operator has asked for them. A daemon with no
        # OPENAI4S_WORKER_LISTEN binds nothing, starts no extra thread, and
        # is byte-for-byte the single-user daemon it was (INV-1): a listener
        # on by default would be an attack surface on every laptop that
        # will never run a cluster job.
        self.compute_sessions = None
        self.worker_gateway = None
        self.lease_reclaimer = None
        prepare_attempt = None
        on_state_lost = None
        try:
            from openai4s.orchestration.bootstrap import (
                BootstrapAuthority,
                load_or_mint_secret,
            )
            from openai4s.orchestration.reclaimer import LeaseReclaimer
            from openai4s.orchestration.session import (
                AttemptPreparer,
                ComputeSessionManager,
            )
            from openai4s.orchestration.worker_gateway import gateway_from_environment

            authority = BootstrapAuthority(
                load_or_mint_secret(self.cfg.data_dir),
                # The fence outlives the process. A credential file sits on
                # the shared filesystem the job was given and stays valid for
                # its whole TTL, so an in-memory nonce set un-burns every
                # outstanding credential on restart.
                state_path=self.cfg.data_dir / "worker-bootstrap-state.json",
            )
            worker_gateway = gateway_from_environment(authority)
            if worker_gateway is not None:
                worker_gateway.start()
                self.worker_gateway = worker_gateway
                manager = ComputeSessionManager(
                    store=self.store,
                    gateway=worker_gateway,
                    authority=authority,
                    workspace_root=self.cfg.data_dir / "cluster-workspaces",
                    on_event=self._on_orchestration_event,
                )
                self.compute_sessions = manager
                prepare_attempt = AttemptPreparer(
                    authority=authority,
                    listen_address=lambda: worker_gateway.address,
                    runtime_dir=manager.runtime_dir,
                    advertise_host=os.environ.get("OPENAI4S_WORKER_ADVERTISE") or None,
                )

                # The reconciler decides a session was lost; the manager is
                # what a browser asks. Wiring them here rather than letting
                # either import the other keeps the loop testable without a
                # session manager and the manager testable without a loop.
                def on_state_lost(workload, allocation, _m=manager):
                    _m.note_state_lost(workload.id, epoch=allocation.epoch)

                self.lease_reclaimer = LeaseReclaimer(
                    leases=self.store.leases,
                    workloads=self.store.workloads,
                    on_event=self._on_orchestration_event,
                )
                self.lease_reclaimer.start()
        except Exception as exc:  # noqa: BLE001 — never block boot on this
            print(
                f"[openai4s] cluster sessions unavailable: {exc}",
                file=sys.stderr,
                flush=True,
            )

        self.reconciler = Reconciler(
            store=self.store.workloads,
            backends=self.orchestration_backends,
            default_backend=self.default_backend,
            interval_s=max(0.05, interval_s),
            prepare_attempt=prepare_attempt,
            on_state_lost=on_state_lost,
            on_event=self._on_orchestration_event,
        )
        # Started on demand, not on construction. Most daemons (and every
        # test that never submits a job) have nothing for this loop to do,
        # and a thread per SessionRunner that polls a database forever is
        # both overhead and noise. It starts here only when a previous run
        # left work in flight — a restart must resume those — and otherwise
        # when the first job is submitted.
        try:
            if self.store.workloads.workloads_needing_attention():
                self.reconciler.start()
        except Exception:  # noqa: BLE001 — never block boot on this
            pass

    def ensure_reconciler(self) -> None:
        """Start the orchestration loop if it is not already running.

        Idempotent: `Reconciler.start` returns immediately when a thread
        already exists, so every submission may call this.

        It starts things and stops nothing. This method used to open by
        calling `.stop()` on `lease_reclaimer` and `worker_gateway` — both
        started once in `_init_orchestration` and restarted nowhere. So the
        *first* submission closed the listening socket every remote worker
        dials into: `WorkerGateway.stop()` nulls `_server`, `address` then
        returns None, and `AttemptPreparer` refuses the allocation with "a
        cluster session needs a worker listener" on every tick afterwards.
        The first cluster session was never placed, and no idle lease was
        ever reclaimed, for the life of the daemon. Shutdown belongs in
        `close()`, which is where it is.
        """
        reconciler = getattr(self, "reconciler", None)
        if reconciler is not None:
            reconciler.start()

    def _on_orchestration_event(self, kind: str, payload: dict) -> None:
        """Orchestration events are daemon-level, not session-level.

        There is no root_frame_id to broadcast on for a batch job, so these
        are logged rather than pushed at a WebSocket — inventing a session
        to carry them would put one user's job events on another user's
        stream.
        """
        if kind in ("reconcile_error", "workload_terminal"):
            print(f"[openai4s] orchestration {kind}: {payload}", file=sys.stderr)

    def close(self) -> None:
        """Stop the sweeper, turns, background workers, and all session slots."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        reconciler = getattr(self, "reconciler", None)
        if reconciler is not None:
            reconciler.stop()
        # The worker listener and the lease sweeper are daemon-lifetime
        # components: started once in `_init_orchestration`, stopped here and
        # nowhere else. `ensure_reconciler` used to stop them on every
        # submission, which is what left a daemon unable to place its first
        # cluster session.
        for attr in ("lease_reclaimer", "worker_gateway"):
            component = getattr(self, attr, None)
            if component is not None:
                try:
                    component.stop()
                except Exception:  # noqa: BLE001
                    pass
        for backend in (getattr(self, "orchestration_backends", None) or {}).values():
            closer = getattr(backend, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass
        recovery = getattr(self, "recovery", None)
        if recovery is not None:
            recovery.stop()
        shares = getattr(self, "shares", None)
        if shares is not None:
            shares.stop_sweeper()
        tunnel = getattr(self, "_share_tunnel", None)
        if tunnel is not None:
            tunnel.close()
        self.executions.close(reason="daemon_shutdown")
        for st in self._session_snapshot():
            self.drop_session(st.root_frame_id, reason="daemon_shutdown")
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            thread = job.thread
            if thread is None or thread is threading.current_thread():
                continue
            # `is_alive()` before `join()`, because a thread that was never
            # started raises "cannot join thread before it is started" -- and
            # `_spawn_job` registers a job *before* calling `start()`, so a
            # refused spawn leaves exactly that. Shutdown is the worst possible
            # place to discover it: nothing can be done about the exception and
            # every job after this one in the list goes unjoined. A finished
            # thread reports not-alive too, and joining one is a no-op, so the
            # guard costs nothing on the normal path.
            if not thread.is_alive():
                continue
            thread.join(timeout=5.0)
        with self._lock:
            self._jobs.clear()

    # --- artifact version snapshots --------------------------------------
    def _versions_dir(self) -> Path:
        return self.artifacts.versions_dir()

    def live_artifact_path(self, a: dict) -> Path:
        return self.artifacts.live_path(a)

    def _write_version_snapshot(
        self,
        version_id: str,
        filename: str,
        *,
        src_path: Path | None = None,
        data: bytes | None = None,
    ) -> None:
        self.artifacts.write_version_snapshot(
            version_id, filename, src_path=src_path, data=data
        )

    def _protect_latest_version_snapshots(self, st: SessionState) -> None:
        self.artifacts.protect_latest(st)

    def restore_version(self, artifact_id: str, version_id: str) -> dict:
        result = self.artifacts.restore(artifact_id, version_id)
        if result.get("ok") and result.get("artifact"):
            result = dict(result)
            result["artifact"] = _artifact_json(result["artifact"])
        return result

    def mutate_session_domain(
        self,
        root_frame_id: str,
        project_id: str,
        *,
        operation: str,
        mutate,
        invalidate_kernel: bool = False,
    ) -> dict:
        """Serialize one checkpoint/branch mutation with scientific writers."""

        st = self._state(root_frame_id, project_id)
        with self._session_execution(
            st,
            owner="lifecycle",
            owner_id=f"{operation}-{uuid.uuid4().hex[:12]}",
            reason=operation.replace("_", " "),
        ) as execution:
            result = mutate()
            if invalidate_kernel and result.get("ok"):
                st.kernels.stop(
                    "python", manual=False, reason="branch_revert_requires_recovery"
                )
                st.kernels.stop(
                    "r", manual=False, reason="branch_revert_requires_recovery"
                )
                # Revert/Undo publishes the same checkpoint-backed Artifact,
                # environment, capability, and permission projection as branch
                # activation.  Discard in-memory provider/control-plane caches
                # so the next turn is rebuilt from that durable head instead of
                # retaining messages or policy from the abandoned interval.
                if st.delegation_runner is not None:
                    st.delegation_runner.close(cancel=True)
                    st.delegation_runner = None
                st.runtime = SessionRuntime()
                st.messages = []
                st.env_name = None
                st.pending_env = None
                checkpoint = result.get("checkpoint") or {}
                pins = (
                    checkpoint.get("environment_pins")
                    if isinstance(checkpoint, Mapping)
                    else {}
                )
                pins = pins if isinstance(pins, Mapping) else {}
                st.desired_env = str(pins["python"]) if pins.get("python") else None
                st.r_env_name = str(pins["r"]) if pins.get("r") else None
                self._seed_messages(st)
                emit = self.hub.emitter(root_frame_id)
                emit(
                    {
                        "type": "kernel_status",
                        "frame_id": root_frame_id,
                        "status": "ended",
                        "state": "ended",
                        "ended_reason": "branch_revert_requires_recovery",
                        "requires_kernel_recovery": True,
                    }
                )
                emit(
                    {
                        "type": "branch_projection_restored",
                        "frame_id": root_frame_id,
                        "branch_id": st.branch_id,
                        "checkpoint_id": (
                            checkpoint.get("checkpoint_id")
                            if isinstance(checkpoint, Mapping)
                            else None
                        ),
                    }
                )
            self.executions.mark_finalizing(
                execution, reason=f"persisting {operation.replace('_', ' ')}"
            )
            return result

    def activate_session_branch(
        self,
        root_frame_id: str,
        project_id: str,
        branch_id: str,
    ) -> dict[str, Any]:
        """Switch the live scientific runtime to an immutable branch head.

        Validation/materialization happens while the old branch still owns the
        FIFO writer ticket.  Only then are its workers stopped and the target
        checkpoint projection published atomically.  Namespace recovery is
        attempted on the new branch and reported truthfully; a partial/failed
        recovery never masquerades as a restored kernel.
        """

        old = self._state(root_frame_id, project_id)
        branch_id = str(branch_id or "").strip()
        if not branch_id:
            raise GatewayError(400, "branch_id is required")
        if branch_id == old.branch_id:
            return {
                "ok": True,
                "status": "active",
                "activation_state": "Active",
                "root_frame_id": root_frame_id,
                "previous_branch_id": old.branch_id,
                "current_branch_id": old.branch_id,
                "already_active": True,
            }

        emit = self.hub.emitter(root_frame_id)
        owner_id = f"activate-{uuid.uuid4().hex[:12]}"
        with self._session_execution(
            old,
            owner="lifecycle",
            owner_id=owner_id,
            reason=f"activate branch {branch_id}",
        ) as execution:
            prepared = self.session_domain.prepare_activation(
                root_frame_id,
                branch_id=branch_id,
            )
            checkpoint = dict(prepared["checkpoint"])
            candidate = SessionState(
                root_frame_id,
                old.project_id,
                Path(prepared["workspace"]),
                branch_id=branch_id,
                kernel_generations=self.store,
                owner_instance_id=self._owner_instance_id,
                clock_ms=lambda: int(self._clock() * 1000),
            )
            candidate.model = old.model
            candidate.plan = old.plan
            candidate.explore = old.explore
            candidate.cell_index = self.store.latest_state_revision(root_frame_id)
            pins = checkpoint.get("environment_pins") or {}
            if isinstance(pins, Mapping):
                candidate.desired_env = (
                    str(pins["python"]) if pins.get("python") else None
                )
                candidate.r_env_name = str(pins["r"]) if pins.get("r") else None

            # The admitted lifecycle ticket guarantees there is no protocol
            # reader left in either old slot before detachment.
            self._interrupt_background(old)
            if old.delegation_runner is not None:
                old.delegation_runner.close(cancel=True)
                old.delegation_runner = None
            old.kernels.stop("python", manual=False, reason="branch_activated")
            old.kernels.stop("r", manual=False, reason="branch_activated")

            projection = self.session_domain.publish_activation(
                root_frame_id,
                branch_id=branch_id,
                checkpoint_id=str(prepared["checkpoint_id"]),
                expected_current_branch_id=old.branch_id,
            )
            with self._lock:
                if self._sessions.get(root_frame_id) is not old:
                    raise RuntimeError(
                        "session runtime changed during branch activation"
                    )
                self._sessions[root_frame_id] = candidate

            # Provider history is rebuilt only from the inherited branch prefix
            # plus branch-local groups; this does not start a kernel.
            self._seed_messages(candidate)
            recovery_result: dict[str, Any] | None = None
            generation_refs = checkpoint.get("generation_refs") or {}
            if generation_refs:
                try:
                    plan = self.session_domain.recovery.prepare_action(
                        root_frame_id,
                        "restore",
                        branch_id=branch_id,
                    )
                    # A fork's frozen manifests name its source workspace.  The
                    # bytes/env/sidecars remain immutable, while execution must
                    # be rebound to the isolated target workspace.
                    import dataclasses

                    rebound = tuple(
                        dataclasses.replace(
                            manifest,
                            working_directory=str(candidate.workspace.resolve()),
                        )
                        for manifest in plan.manifests
                    )
                    plan = dataclasses.replace(plan, manifests=rebound)
                    runtime = self._recovery_runtime(candidate, emit)
                    with self.recovery.recovery_scope(candidate):
                        recovery_result = runtime.run(plan)
                except Exception as error:  # noqa: BLE001 - branch remains selected
                    recovery_result = {
                        "ok": False,
                        "status": "failed",
                        "issues": [
                            f"branch head namespace recovery failed ({type(error).__name__})"
                        ],
                    }

            status = str((recovery_result or {}).get("status") or "active").lower()
            if status not in {"active", "partial", "failed", "cancelled"}:
                status = "failed"
            metadata = checkpoint.get("metadata") or {}
            plans = metadata.get("plans") if isinstance(metadata, Mapping) else None
            memories = (
                metadata.get("memories") if isinstance(metadata, Mapping) else None
            )
            dimensions = {
                "workspace": {
                    "applied": True,
                    **prepared.get("workspace_preview", {}),
                },
                "environment": projection["environment"],
                "artifacts": projection["artifacts"],
                "capabilities": projection["capabilities"],
                "permissions": projection["permissions"],
                "provider_history": {"applied": True},
                "plans": {
                    "applied": not bool(plans),
                    "reason": (
                        "checkpoint stores plan identities only; plan bodies are not versioned"
                        if plans
                        else None
                    ),
                },
                "memories": {
                    "applied": not bool(memories),
                    "reason": (
                        "checkpoint stores memory hashes only; memory bodies are project-scoped"
                        if memories
                        else None
                    ),
                },
                "namespace": {
                    "applied": status == "active",
                    "status": status,
                    "issues": list(
                        (recovery_result or {}).get("issues")
                        or [
                            issue
                            for item in (recovery_result or {}).get("results", ())
                            for issue in (item.get("issues") or ())
                        ]
                    ),
                },
            }
            if status == "active" and (
                not dimensions["plans"]["applied"]
                or not dimensions["memories"]["applied"]
            ):
                status = "partial"
            self.executions.mark_finalizing(
                execution, reason="publishing active branch runtime"
            )
            event = {
                "type": "branch_activation_state",
                "frame_id": root_frame_id,
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "checkpoint_id": prepared["checkpoint_id"],
                "status": status,
                "state": status,
            }
            emit(event)
            return {
                "ok": status == "active",
                "status": status,
                "activation_state": status.title(),
                "root_frame_id": root_frame_id,
                "previous_branch_id": old.branch_id,
                "current_branch_id": branch_id,
                "checkpoint_id": prepared["checkpoint_id"],
                "execution_id": execution.execution_id,
                "owner": execution.owner.as_dict(),
                "dimensions": dimensions,
                "recovery": recovery_result,
            }

    def execute_recovery_action(
        self,
        root_frame_id: str,
        project_id: str,
        action_id: str,
        *,
        branch_id: str | None = None,
        confirmed: bool = False,
    ) -> dict:
        """Run one enabled recovery mutation under an exact FIFO ticket."""

        quarantine = self.import_quarantine(root_frame_id)
        if quarantine and (action_id != "restart_fresh" or not confirmed):
            raise RecoveryActionError(
                "imported Session is quarantined; only an explicitly confirmed "
                "restart_fresh can establish a trusted runtime"
            )
        st = self._state(root_frame_id, project_id, allow_quarantined=True)
        branch_id = branch_id or st.branch_id
        if branch_id != st.branch_id:
            raise RecoveryActionError(
                "live recovery requires the current active branch"
            )
        owner_id = f"{action_id}-{uuid.uuid4().hex[:12]}"
        emit = self.hub.emitter(root_frame_id)
        with self._session_execution(
            st,
            owner="recovery",
            owner_id=owner_id,
            reason=f"kernel recovery: {action_id}",
        ) as execution:
            runtime = self._recovery_runtime(st, emit)
            fresh = runtime.fresh_manifests() if action_id == "restart_fresh" else ()
            # Re-check enabled/confirmation after FIFO admission, before
            # recovery_scope changes any live generation state.
            plan = self.session_domain.recovery.prepare_action(
                root_frame_id,
                action_id,
                branch_id=branch_id,
                confirmed=confirmed,
                fresh_manifests=fresh,
            )
            with self.recovery.recovery_scope(st):
                result = runtime.run(plan)
                self.executions.mark_finalizing(
                    execution, reason="publishing recovery state"
                )
                emit(runtime.kernel_status_event(result, plan.recovery_id))
            if (
                quarantine
                and action_id == "restart_fresh"
                and str(result.get("status") or "").lower() == "active"
            ):
                self.store.delete_setting(session_import_quarantine_key(root_frame_id))
                trust_group = self.store.append_action_group(
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    turn_id=f"import-trust-{uuid.uuid4().hex[:16]}",
                    kind="session_import_trust",
                    assistant_content=(
                        "Imported Session runtime established by fresh restart"
                    ),
                )
                self.store.append_action_event(
                    group_id=trust_group["group_id"],
                    type="session_import_trusted",
                    result={
                        "trust_state": "trusted",
                        "method": "confirmed_restart_fresh",
                        "replayed_package_code": False,
                    },
                    side_effect_class="runtime_mutation",
                    resource_keys=[f"session:{root_frame_id}"],
                )
                result["quarantine_cleared"] = True
                result["trust_state"] = "trusted"
            result.update(
                {
                    "execution_id": execution.execution_id,
                    "owner": execution.owner.as_dict(),
                }
            )
            return result

    def _recovery_runtime(self, st: SessionState, emit) -> SessionRecoveryRuntime:
        from openai4s.kernel import environments as envmod

        def python_runtime():
            environment = envmod.get_environment(self._selected_env_name(st))
            if environment is None or environment.interpreter is None:
                environment = envmod.get_environment("base")
            if environment is None or environment.interpreter is None:
                raise RecoveryActionError("no Python runtime is available")
            return python_runtime_spec(environment)

        def python_published(name, factory, bin_dir) -> None:
            st.env_name = st.desired_env = name
            st.booted = True
            dispatcher = self._ensure_runtime(st)
            dispatcher.active_env_bin = bin_dir
            dispatcher.background_kernel_factory = factory
            self._persist_env(st.root_frame_id, name)

        return SessionRecoveryRuntime(
            RecoveryRuntimePorts(
                root_frame_id=st.root_frame_id,
                workspace=st.workspace,
                kernels=st.kernels,
                control=self.session_domain.recovery,
                cas=self.session_domain.cas,
                checkpoint=self.store.get_session_checkpoint,
                artifact_version=self.store.version_meta,
                dispatcher=lambda: self._ensure_runtime(st),
                python_runtime=python_runtime,
                bootstrap_code=lambda: _maybe_call(
                    getattr(self._skills_for(st), "bootstrap_code", "")
                ),
                python_published=python_published,
                r_published=lambda key: setattr(st, "r_env_name", key),
                bind_candidate=lambda candidate, interrupt: (
                    self.executions.bind_lease(candidate, interrupt)
                ),
                unbind_candidate=self.executions.unbind_lease,
                cancelled=st.cancel.is_set,
                event_sink=emit,
            )
        )

    # --- web share lifecycle ------------------------------------------------
    def _share_enabled(self) -> bool:
        return self.store.get_setting("sharing_enabled") == "1"

    def _share_run_in_ticket(self, root_frame_id: str, branch_id: str, fn):
        """Run the share projection build under one exact FIFO ticket."""

        scope = self.store.resolve_frame_scope(
            root_frame_id, fallback_project="default"
        )
        st = self._state(root_frame_id, scope["project_id"], allow_quarantined=True)
        with self._session_execution(
            st,
            owner="share",
            owner_id=f"share-{uuid.uuid4().hex[:12]}",
            reason="publishing share snapshot",
        ):
            return fn(st.cancel)

    def enforce_llm_quota(self, root_frame_id: str) -> None:
        """Team-mode LLM quota (M2-6), consulted before a provider request.

        One method rather than a closure so every LLM entry point the daemon
        owns can share it: the turn loop's ChatModel and the reviewer, which
        calls the provider through its own port and would otherwise be an
        unmetered, user-triggered way around an exhausted quota.

        Frozen decision: a *broken* check admits and audits — availability
        over bookkeeping.
        """
        try:
            owner = self.store.team.session_owner(root_frame_id)
        except Exception:  # noqa: BLE001
            owner = None
        if owner is None:
            return
        try:
            for kind in ("llm_input_tokens", "llm_output_tokens"):
                self.store.governance.check_quota(
                    user_id=owner["user_id"],
                    project_id=owner["project_id"],
                    kind=kind,
                )
        except QuotaExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            try:
                self.store.team.audit(
                    actor=owner["user_id"],
                    action="quota_check_failed",
                    detail=str(e)[:200],
                )
            except Exception:  # noqa: BLE001
                pass

    def session_replay_view(self, root_frame_id: str) -> bytes:
        """The sanitized read-only view.json bytes for a session (M2-3).

        Reuses the web-share projection builder verbatim — the replay
        surface IS the audited read-only surface, which is what makes it
        the only data shape a guest may touch (D3) — but writes nothing:
        no shares row, no snapshot directory, no tunnel, and no relay
        configuration required.
        """
        branch = self.store.active_session_branch(root_frame_id)
        projection = self._share_run_in_ticket(
            root_frame_id,
            branch,
            lambda cancel: self.shares.builder.build(
                root_frame_id, branch, cancel_event=cancel
            ),
        )
        return self.shares.builder.serialize_view(projection)

    def ensure_share_tunnel(self):
        """Lazily create/start the tunnel when sharing is enabled + configured."""

        if not (self._share_enabled() and self.cfg.share.configured):
            return None
        if self._share_tunnel is None:
            from openai4s.share.tunnel import TunnelClient

            tunnel = TunnelClient(
                self.cfg.share.relay_url,
                self.cfg.share.auth_token,
                self._share_router.handle,
                allow_insecure=self.cfg.share.allow_insecure,
            )
            self._share_tunnel = tunnel
            self.shares.tunnel = tunnel
        return self._share_tunnel

    def share_status(self) -> dict[str, Any]:
        if not self._share_enabled():
            return {"state": "disabled", "configured": self.cfg.share.configured}
        if not self.cfg.share.configured:
            missing = [
                name
                for name, value in (
                    ("relay_url", self.cfg.share.relay_url),
                    ("auth_token", self.cfg.share.auth_token),
                )
                if not value
            ]
            return {"state": "unconfigured", "missing": missing}
        tunnel = self._share_tunnel
        if tunnel is None:
            return {"state": "connecting", "configured": True}
        status = tunnel.status()
        return {
            "state": "connected" if status.get("connected") else "connecting",
            "configured": True,
            **status,
        }

    def set_sharing_enabled(self, enabled: bool) -> dict[str, Any]:
        self.store.set_setting("sharing_enabled", "1" if enabled else "0")
        if enabled:
            tunnel = self.ensure_share_tunnel()
            if tunnel is not None:
                desired = {
                    str(row["share_id"]): {} for row in self.store.list_active_shares()
                }
                # set_shares({}) means "no shares -> disconnect"; at enable time
                # with none yet, just hold the connection open so the next create
                # registers immediately.
                if desired:
                    tunnel.set_shares(desired)
                else:
                    tunnel.ensure_connected()
        else:
            # Disable = take shares offline but keep rows + snapshots for later.
            if self._share_tunnel is not None:
                self._share_tunnel.close()
                self._share_tunnel = None
                self.shares.tunnel = None
        return self.share_status()

    def _share_boot_restore(self) -> None:
        try:
            desired = self.shares.restore()
        except Exception:  # noqa: BLE001 - share recovery must never block boot
            return
        if desired and self._share_enabled() and self.cfg.share.configured:
            tunnel = self.ensure_share_tunnel()
            if tunnel is not None:
                tunnel.set_shares({sid: {} for sid in desired})
        # Auto-revoke shares whose expiry lapses while the daemon runs.
        try:
            self.shares.start_sweeper()
        except Exception:  # noqa: BLE001 - the sweeper is best-effort
            pass

    def import_quarantine(self, root_frame_id: str) -> dict[str, Any] | None:
        raw = self.store.get_setting(session_import_quarantine_key(root_frame_id))
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {"state": "quarantined", "reason": "invalid_quarantine_record"}
        return (
            dict(value)
            if isinstance(value, Mapping)
            else {"state": "quarantined", "reason": "invalid_quarantine_record"}
        )

    def require_session_writable(self, root_frame_id: str, operation: str) -> None:
        if self.import_quarantine(root_frame_id):
            raise GatewayError(
                423,
                "imported Session is quarantined and view-only; use the "
                "confirmed restart_fresh recovery action before " + operation,
            )

    def _state(
        self,
        root_frame_id: str,
        project_id: str,
        *,
        allow_quarantined: bool = False,
    ) -> SessionState:
        scope = self.store.resolve_frame_scope(
            root_frame_id,
            fallback_project=project_id,
        )
        if scope["root_frame_id"] != root_frame_id:
            raise ValueError("Web session operations require a root frame id")
        if not allow_quarantined:
            self.require_session_writable(root_frame_id, "starting a live runtime")
        project_id = scope["project_id"]
        with self._lock:
            if project_id in self._deleting_projects:
                raise GatewayError(409, "project deletion is in progress")
            st = self._sessions.get(root_frame_id)
            if st is None:
                # A handful of in-process/compatibility callers intentionally
                # exercise an ephemeral SessionState without first persisting a
                # frame. Durable Web sessions always have a frame and therefore
                # publish an atomic branch selection; an ephemeral test/runtime
                # state stays root-bound instead of creating dangling rows.
                branch_id = (
                    self.store.ensure_active_session_branch(root_frame_id)
                    if self.store.get_frame(root_frame_id) is not None
                    else root_frame_id
                )
                st = SessionState(
                    root_frame_id,
                    project_id,
                    self.workspace_for_branch(root_frame_id, branch_id),
                    branch_id=branch_id,
                    kernel_generations=self.store,
                    owner_instance_id=self._owner_instance_id,
                    clock_ms=lambda: int(self._clock() * 1000),
                )
                # A direct REPL Cell allocates its attempt before lazy language
                # preparation calls ``_seed_messages``.  Seed the durable
                # cursor at SessionState creation so a daemon reopen can never
                # reserve revision 1 over an existing session history.
                st.cell_index = self.store.latest_state_revision(root_frame_id)
                self._sessions[root_frame_id] = st
            return st

    def _queue_execution(
        self,
        st: SessionState,
        *,
        owner: str,
        owner_id: str,
        execution_id: str | None = None,
        language: str | None = None,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
    ):
        """Submit after any already-reserved Stop, without holding a long lock.

        ``metadata`` rides on the ticket and therefore appears in every queue
        snapshot and ``execution_queue`` broadcast. It is the only place a
        *queued* item can describe itself: the ticket is all that exists until
        the item is admitted, so anything the client needs in order to name the
        item it wants cancelled has to be frozen here, at submit.
        """

        while True:
            st.stop_finished.wait()
            with st.admission_lock:
                if st.stop_requested.is_set():
                    continue
                try:
                    return self.executions.submit(
                        st.root_frame_id,
                        owner=owner,
                        owner_id=owner_id,
                        execution_id=execution_id,
                        branch_id=st.branch_id,
                        language=language,
                        resource_keys=(
                            "workspace",
                            f"kernel:{language or 'control'}",
                        ),
                        metadata={"reason": reason, **dict(metadata or {})},
                    )
                except QueueDepthExceeded as error:
                    # A full queue surfaced as HTTP 500 `internal_error`, which
                    # is wrong about both halves: nothing failed internally, and
                    # a client that retries 5xx would loop against a queue that
                    # cannot accept anything until the user waits or cancels --
                    # which is exactly what the message already tells them.
                    raise GatewayError(429, str(error), "queue_full") from error

    @contextmanager
    def _session_execution(
        self,
        st: SessionState,
        *,
        owner: str,
        owner_id: str,
        execution_id: str | None = None,
        language: str | None = None,
        reason: str,
        ticket=None,
    ):
        """Combine FIFO ownership with the compatible turn-lock barrier.

        Admission always happens before ``turn_lock``.  No path may hold the
        old lock while waiting for a FIFO ticket, which prevents a two-lock
        cycle during the incremental migration.
        """

        current = self.executions.current(st.root_frame_id)
        owns_admission = current is None
        ticket = current or ticket
        if ticket is None:
            ticket = self._queue_execution(
                st,
                owner=owner,
                owner_id=owner_id,
                execution_id=execution_id,
                language=language,
                reason=reason,
            )

        @contextmanager
        def turn_barrier():
            held = getattr(self._turn_local, "sessions", None)
            if held is None:
                held = self._turn_local.sessions = []
            if st.root_frame_id in held:
                yield
                return
            with st.execution_barrier():
                # An exact cancel may arrive after admission but before a
                # legacy holder releases turn_lock.  execution_barrier clears
                # the old Event on entry, so restore the ticket-owned signal.
                if ticket.cancellation.is_set():
                    st.cancel.set()
                held.append(st.root_frame_id)
                try:
                    yield
                finally:
                    held.pop()

        if owns_admission:
            with self.executions.admitted(ticket, cancel_event=st.cancel):
                with turn_barrier():
                    yield ticket
            return
        with turn_barrier():
            yield ticket

    def _seed_messages(self, st: SessionState) -> None:
        """Build the system prompt (+ project context + skills + memory) once,
        seeding the in-memory conversation. Kept separate from kernel spawn so a
        stop→start cycle keeps the conversation intact."""
        if st.messages:
            return
        ctx = SYSTEM_PROMPT + _GATEWAY_PROMPT_EXTRA
        # Safety fragments (report biO + oiO): the enforcement side lives in the
        # pre-exec classifier (_execute_and_log), the in-kernel audit hook, and
        # the dispatcher injection screen — this is the prompt-level guidance.
        try:
            sec = self.cfg.security
            if sec.code_gate_enabled:
                from openai4s import prompts as _prompts

                ctx += "\n\n" + _prompts.SECURITY_GENERAL
            if sec.biosecurity:
                from openai4s.security.biosecurity import BIOSECURITY_PROMPT

                ctx += "\n\n" + BIOSECURITY_PROMPT
        except Exception:  # noqa: BLE001
            pass
        proj = self.store.get_project(st.project_id) if st.project_id else None
        if proj and (proj.get("context") or "").strip():
            ctx += "\n\nProject context:\n" + proj["context"].strip()
        skills = self._skills_for(st)
        sctx = _maybe_call(getattr(skills, "system_context", ""))
        if sctx:
            ctx += "\n\n" + sctx
        # long-term memory: inject saved memory blocks when the feature is on
        try:
            if self.store.get_setting("memory_enabled", "0") == "1":
                # This session's project plus the global tier it inherits, and
                # never every project. `or "all"` here meant a session with a
                # falsy project_id seeded its system prompt with the whole
                # installation's remembered context; "default" matches what
                # `resolve_frame_scope` falls back to, so the two agree.
                mems = self.store.list_memories(project_id=st.project_id or "default")
                if mems:
                    # `mems[:50]` bounded the count and nothing else. Fifty
                    # memories of a pasted protocol is ~600k characters —
                    # roughly 150k tokens against a 262k window, spent on
                    # background before the user has said anything, on every
                    # turn. A count cannot bound this because length is what
                    # varies.
                    kept, dropped = memory_budget.select(mems)
                    block = memory_budget.render(kept, dropped)
                    if block:
                        ctx += "\n\n" + block
                    # The Context panel reports this. A budget the user cannot
                    # see is one they discover by noticing the agent has
                    # forgotten something, which is the worst way to learn it.
                    if dropped:
                        st.context_omissions["memory"] = list(dropped)
                    else:
                        st.context_omissions.pop("memory", None)
        except Exception:  # noqa: BLE001
            pass
        # Specialists the agent can delegate to (host.delegate(request, name=...))
        try:
            specialists = self.store.specialist_profiles(
                project_id=st.project_id,
                session_id=st.root_frame_id,
            )
            builtin = specialists.filter_profiles(_BUILTIN_AGENTS)
            custom = self.store.list_agents(
                project_id=st.project_id,
                session_id=st.root_frame_id,
            )
            specs = list(builtin) + list(custom)
            if specs:
                ctx += (
                    "\n\nAvailable specialists — delegate a self-contained "
                    'sub-task to one with `host.delegate("<task>", '
                    'name="<specialist>")` and it will act with that persona:\n'
                    + "\n".join(
                        f"- {s['name']}: {s.get('description') or ''}"
                        for s in specs[:20]
                    )
                )
        except Exception:  # noqa: BLE001
            pass
        remote_ctx = _remote_gpu_runtime_context()
        if remote_ctx:
            ctx += "\n\n" + remote_ctx
        # Connectors (MCP tools) the agent can call
        try:
            conns = [c for c in self.store.list_connectors() if c.get("enabled")]
            if conns:
                ctx += (
                    "\n\nConnectors (MCP tool servers) — list a server's tools "
                    'with `host.mcp.tools("<id>")` and call one with '
                    '`host.mcp.call("<id>", "<tool>", {...})`:\n'
                    + "\n".join(
                        f"- {c['connector_id']}: {c.get('description') or c['name']}"
                        for c in conns[:20]
                    )
                )
        except Exception:  # noqa: BLE001
            pass
        # Prebuilt environments actually present on THIS host, so the agent picks
        # from the real set (with host.env.use) instead of installing every task.
        try:
            from openai4s.kernel import environments as envmod

            envs = envmod.discover_environments()
            cur = st.env_name or envmod.default_env_name()
            lines = []
            for e in envs:
                tag = (
                    " (current)"
                    if e.name == cur
                    else ("" if e.interpreter else " [R — use ```r cells]")
                )
                note = ", ".join(e.notable(6)) or e.description()
                lines.append(f"- {e.name}{tag}: {note}")
            if lines:
                ctx += (
                    "\n\nPrebuilt runtime environments (the notebook kernel runs "
                    'in ONE at a time — switch with `host.env.use("<name>")`, '
                    "inspect with `host.env.list([pkgs])`). PREFER an env that "
                    "already has what you need over pip-installing:\n"
                    + "\n".join(lines)
                )
        except Exception:  # noqa: BLE001
            pass
        # The Action Ledger, rather than UI prose/execution-log projections,
        # is the canonical provider history.  Rebuild complete action groups
        # after the freshly composed system prompt on every daemon resume.
        st.messages = [
            {"role": "system", "content": ctx},
            *restore_action_history(
                self.store,
                st.root_frame_id,
                branch_id=st.branch_id,
            ),
        ]
        # Re-seed from the durable transaction cursor, not row count.  Failed
        # attempts can reserve a revision before an execution-log row exists,
        # and that ordinal must never be reused after daemon reopen.
        st.cell_index = max(
            st.cell_index,
            self.store.latest_state_revision(st.root_frame_id),
        )

    def _skills_for(self, st: SessionState):
        """Return the exact project/session-scoped loader used by Host RPC.

        Prompt disclosure, host.search_skills/read, and kernel bootstrap must
        all observe one capability snapshot.  Falling back to the runner-level
        loader keeps lightweight tests that inject a dispatcher compatible.
        """

        dispatcher = st.dispatcher
        loader = getattr(dispatcher, "skill_loader", None) if dispatcher else None
        if loader is not None:
            return loader
        try:
            return self.skills.scoped(
                project_id=st.project_id,
                session_id=st.root_frame_id,
            )
        except Exception:  # noqa: BLE001 - prompt/bootstrap remains available
            return self.skills

    def _ensure_runtime(self, st: SessionState):
        """Build the session control plane without acquiring a language worker."""

        def factory():
            disp = build_dispatcher(
                self.cfg,
                frame_id=st.root_frame_id,
                workspace=st.workspace,
            )
            bind_session_domain = getattr(disp, "set_session_domain", None)
            if callable(bind_session_domain):
                bind_session_domain(self.session_domain)
            # Project every visible host.* call into persisted UI activity.
            disp.on_step = self._make_step_sink(st)
            disp.on_plan = self._make_plan_sink(st)
            disp.on_env_switch = self._make_env_switch_sink(st)

            # A selected environment is meaningful before its worker exists:
            # env_list should report the persisted pin, but no process starts.
            try:
                from openai4s.kernel import environments as envmod

                selected = envmod.get_environment(self._selected_env_name(st))
                if selected is not None and selected.interpreter is not None:
                    disp.active_env_bin = selected.bin_dir
            except Exception:  # noqa: BLE001 — runtime creation must stay usable
                pass

            try:
                from openai4s.permissions import broker

                rid = st.root_frame_id
                broker().register_channel(
                    rid,
                    self.hub.emitter(rid),
                    cancel_event=st.cancel,
                    watching=lambda r=rid: self.hub.has_subscriber(r),
                    store=self.store,
                )
            except Exception:  # noqa: BLE001
                pass
            return disp

        dispatcher = st.runtime.ensure(factory)
        # Refresh per-turn model/delegation wiring without replacing the stable
        # dispatcher (and without starting Python).
        self._wire_delegation(st)
        return dispatcher

    def _release_session_compute(self, root_frame_id: str) -> None:
        """Give a deleted session's cluster resource back.

        Recorded rather than executed: `release` writes the durable stop
        request and ends the lease, and the reconciler's cancel barrier does
        the talking on its next tick. That is what makes the release survive
        a daemon restart -- deleting a session while the scheduler is
        unreachable must not leave a job nobody will ever cancel.
        """
        manager = getattr(self, "compute_sessions", None)
        if manager is None or not root_frame_id:
            return
        from openai4s.orchestration.models import Reason

        manager.release(root_frame_id, reason=Reason.USER_CANCELLED)

    def _touch_compute_lease(self, st: "SessionState") -> None:
        """Renew this session's cluster lease, if it has one."""
        manager = getattr(self, "compute_sessions", None)
        if manager is None:
            return
        session_id = getattr(st, "root_frame_id", "") or ""
        if not session_id:
            return
        try:
            manager.touch(session_id)
        except Exception:  # noqa: BLE001 — a lease must never fail a cell
            pass

    def _remote_kernel_factory(self, st: "SessionState", disp):
        """A factory for this session's cluster kernel, or None for local.

        None is the answer for every session on a daemon with no worker
        listener, every session that never asked for a cluster, and every
        session whose allocation is not yet granted — so the default install
        takes the identical path it always took (INV-1).

        The wait is bounded and short. A worker that has not dialled in yet
        is not an error: the reconciler is still placing the job, readiness
        says which condition is outstanding, and the next execution asks
        again. Blocking a cell for the length of a queue wait would be worse
        than saying "not ready".
        """
        manager = getattr(self, "compute_sessions", None)
        if manager is None:
            return None
        session_id = getattr(st, "root_frame_id", "") or ""
        if not session_id:
            return None
        try:
            workload_id = self.store.leases.workload_for_session(session_id)
        except Exception:  # noqa: BLE001 — no binding, no cluster kernel
            return None
        if not workload_id:
            return None

        runtime = manager.runtime(session_id)
        if runtime is None or runtime.registration is None:
            try:
                attached = manager.attach_worker(
                    session_id, timeout_s=_REMOTE_ATTACH_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[openai4s] cluster worker attach failed for {session_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                return None
            if not attached:
                return None
            runtime = manager.runtime(session_id)
        if runtime is None or runtime.registration is None:
            return None

        transport = getattr(runtime.registration, "transport", None)
        if transport is None:
            return None

        def build() -> Kernel:
            kernel = Kernel(
                dispatcher=disp,
                cwd=str(manager.workspace_for(workload_id)),
                mode="repl",
                transport_factory=lambda: transport,
            )
            manager.bind_kernel(session_id, kernel)
            return kernel

        # The epoch is in the key so a recovery — a new epoch, a new worker —
        # is a different kernel rather than a reused lease pointing at a
        # socket whose far end is gone.
        return build, ("cluster", workload_id, str(runtime.epoch))

    def _spawn_kernel(self, st: SessionState) -> KernelLease:
        """Ensure Python matches the selected environment, build-first.

        The session dispatcher is deliberately not part of worker replacement.
        A failed candidate leaves the old worker, dispatcher, and active runtime
        metadata intact.
        """
        disp = self._ensure_runtime(st)
        previous_env = st.env_name
        env = self._resolve_env(st)
        env_key = (
            env.name,
            str(env.interpreter or ""),
            str(env.root) if getattr(env, "is_conda", False) else None,
        )

        kernel_options = {
            "cwd": str(st.workspace),
            "mode": "repl",
            "python": env.interpreter,
            "env_root": str(env.root) if env.is_conda else None,
            "env_name": env.name,
        }

        # A session that asked to run on a cluster gets its cells executed by
        # the worker that dialled in for it, not by a child of this daemon
        # (M3b-3). Resolved here because `_spawn_kernel` is the one place a
        # session's Python kernel is created — wiring it at any other call
        # site would leave the others quietly local, which is exactly how
        # this feature shipped unreachable the first time.
        remote = self._remote_kernel_factory(st, disp)
        if remote is not None:
            remote_factory, remote_key = remote
            env_key = remote_key

            def factory() -> Kernel:
                return remote_factory()

        else:

            def factory() -> Kernel:
                return Kernel(dispatcher=disp, **kernel_options)

        previous_lease = st.kernels.lease("python")
        try:
            lease = st.kernels.ensure("python", env_key, factory)
        except BaseException:
            st.env_name = previous_env
            raise
        # Publish environment-dependent dispatcher hooks only after the worker
        # replacement has committed.  This preserves build-first semantics.
        disp.active_env_bin = env.bin_dir
        disp.background_kernel_factory = lambda: Kernel(
            dispatcher=disp,
            **kernel_options,
        )
        if previous_lease is None or previous_lease.kernel is not lease.kernel:
            # Run outside the supervisor lock so cancellation can interrupt a
            # slow sidecar.  The caller's turn_lock still prevents execution
            # from racing this one-time bootstrap.
            bootstrap = self._run_bootstrap(st, lease.kernel) or {}
            if bootstrap.get("status") == "failed":
                st.kernels.shutdown_if_current(
                    lease,
                    reason="bootstrap_failed",
                    terminal_state="failed",
                )
                st.booted = False
                raise RuntimeError(
                    "kernel bootstrap failed: "
                    + str(bootstrap.get("error") or "unknown bootstrap error")
                )
        st.booted = True
        return lease

    def _wire_delegation(self, st: SessionState, dispatcher=None) -> None:
        """Enable delegation on the Web session's stable dispatcher.

        The standalone Agent wires this in its __post_init__, but the web UI uses
        a persistent SessionRuntime. Without this hook `host.delegate(...)`
        exists in the SDK yet fails at runtime with "no sub-agent runner wired".
        Rewire per turn so delegated specialists inherit the currently selected
        model from the composer dropdown.
        """
        disp = dispatcher if dispatcher is not None else st.dispatcher
        if disp is None:
            return
        delegation_enabled = str(
            self.store.get_setting(f"delegation:{st.root_frame_id}", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not delegation_enabled:
            disp._delegate_fn = None
            runner = st.delegation_runner
            # Existing async children remain observable and cancellable even
            # after new delegation has been disabled for the session.
            disp.steer_fns = (
                {
                    "children": runner.children,
                    "collect": runner.collect,
                    "stop_child": runner.stop_child,
                    "send_message": runner.send_message,
                    "delegation_stats": runner.delegation_stats,
                }
                if runner is not None
                else {}
            )
            return
        try:
            import dataclasses as _dc

            from openai4s.agent.delegation import DelegationRunner

            child_cfg = _dc.replace(self.cfg, llm=self._llm_cfg(st))
            runner = st.delegation_runner
            if runner is None:
                runner = DelegationRunner(
                    child_cfg,
                    depth=0,
                    parent_frame_id=st.root_frame_id,
                    store=self.store,
                    owner_instance_id=self._owner_instance_id,
                    # Without this, a delegated child falls back to
                    # os.getcwd() — the daemon's launch directory — so its
                    # kernels and relative writes pollute the checkout and
                    # stay invisible to this session's artifact capture.
                    workspace=st.workspace,
                )
                st.delegation_runner = runner
            else:
                # Future children inherit the current composer model while the
                # tree, running children, steering inboxes, and session budget
                # remain intact across Web turns.
                runner.cfg = child_cfg
                # Branch fork/activate can retarget the live workspace; future
                # children must follow it, not the one at runner creation.
                runner.workspace = st.workspace
            disp._delegate_fn = runner
            disp.steer_fns = {
                "children": runner.children,
                "collect": runner.collect,
                "stop_child": runner.stop_child,
                "send_message": runner.send_message,
                "delegation_stats": runner.delegation_stats,
            }
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _resolve_env(self, st: SessionState):
        """The Environment this session's kernel should run in. Sets st.env_name
        to the resolved name (defaulting, and falling back to base for a missing
        or non-Python env)."""
        from openai4s.kernel import environments as envmod

        name = (
            st.desired_env
            or st.env_name
            or self._persisted_env(st.root_frame_id)
            or envmod.default_env_name()
        )
        env = envmod.get_environment(name)
        if env is None or env.interpreter is None:
            # The requested env is not resolvable right now (e.g. conda envs not
            # yet discovered after a restart). Run on base for THIS spawn but do
            # NOT overwrite the stored pin — a later spawn, once the env is
            # discoverable again, must still find the original selection.
            st.desired_env = name
            st.env_name = "base"
            return envmod.get_environment("base")
        st.desired_env = name
        st.env_name = name
        self._persist_env(st.root_frame_id, name)
        return env

    def _selected_env_name(self, st: SessionState) -> str:
        """Environment visible to the session, with or without a live worker."""
        from openai4s.kernel import environments as envmod

        if st.kernels.alive("python") and st.env_name:
            return st.env_name
        selected = st.desired_env or self._persisted_env(st.root_frame_id)
        if selected:
            environment = envmod.get_environment(selected)
            if environment is not None and environment.interpreter is not None:
                return selected
        return st.env_name or envmod.default_env_name()

    def _persisted_env(self, root_frame_id: str) -> "str | None":
        """The runtime env this session last selected (frames.runtime_env), or None."""
        try:
            f = self.store.get_frame(root_frame_id) or {}
            v = (f.get("runtime_env") or "").strip()
            return v or None
        except Exception:
            return None

    def _persist_env(self, root_frame_id: str, name: str) -> None:
        """Remember the selected runtime env so a resumed session (new kernel,
        same conversation) starts in it. Workspace files survive; in-memory
        variables do not — this only pins the env, not the namespace."""
        try:
            self.store.update_frame(root_frame_id, runtime_env=name)
        except Exception:
            pass

    def _make_env_switch_sink(self, st: SessionState):
        """Return the dispatcher hook host.env.use() calls: record a requested
        env switch to apply between cells (never mid-cell — that would restart the
        kernel under the agent's own running code)."""

        def sink(name: str) -> None:
            st.pending_env = name

        return sink

    def _ensure_kernel(self, st: SessionState) -> None:
        if st.kernels.alive("python"):
            return
        self._ensure_runtime(st)
        self._seed_messages(st)
        self._spawn_kernel(st)

    def _prepare_language(self, st: SessionState, language: str) -> str | None:
        """Acquire the requested execution plane at the Cell boundary.

        ``CellExecutionService`` calls this only after allocating the durable
        execution attempt, so a spawn failure remains recoverable and auditable.
        """
        self._ensure_runtime(st)
        # A user is executing something. That -- and an explicit renewal --
        # is the ONLY thing that renews a cluster lease (M3b-4): a worker
        # being alive is not a user being present, so nothing in the
        # transport, the watchdog or the reclaimer's own probe may reach
        # this. Placed at the Cell boundary because it is the narrowest
        # point that every user execution passes through and no background
        # machinery does.
        self._touch_compute_lease(st)
        if language == "r":
            return self._ensure_r_kernel(st)
        if language == "python":
            self._ensure_kernel(st)
            return None
        return f"unsupported kernel language: {language}"

    def _make_step_sink(self, st: SessionState):
        """Return the dispatcher's on_step callback: persist each semantic step
        and stream it to the UI. Stable per session (bound to the frame)."""
        rid = st.root_frame_id
        emit = self.hub.emitter(rid)
        store = self.store

        def sink(ev: dict) -> None:
            try:
                sid = ev.get("step_id")
                if ev.get("phase") == "begin":
                    store.add_step(
                        step_id=sid,
                        frame_id=rid,
                        kind=ev.get("kind"),
                        title=ev.get("title"),
                        input=ev.get("input"),
                        status="running",
                    )
                    emit(
                        {
                            "type": "step",
                            "frame_id": rid,
                            "step_id": sid,
                            "kind": ev.get("kind"),
                            "title": ev.get("title"),
                            "input": ev.get("input"),
                            "status": "running",
                        }
                    )
                else:  # end
                    store.update_step(
                        sid,
                        status=ev.get("status"),
                        output=ev.get("output"),
                        summary=ev.get("summary"),
                    )
                    emit(
                        {
                            "type": "step_update",
                            "frame_id": rid,
                            "step_id": sid,
                            "status": ev.get("status"),
                            "output": ev.get("output"),
                            "summary": ev.get("summary"),
                        }
                    )
            except Exception:  # noqa: BLE001 — telemetry must never break a turn
                pass

        return sink

    def _make_plan_sink(self, st: SessionState):
        """Return the dispatcher's on_plan callback: stream a `plan_progress`
        event when the agent ticks a plan step during auto-execution, so the
        review card checkbox flips live (and replays on reconnect)."""
        rid = st.root_frame_id
        emit = self.hub.emitter(rid)

        def sink(ev: dict) -> None:
            try:
                emit(
                    {
                        "type": "plan_progress",
                        "frame_id": rid,
                        "plan_id": ev.get("plan_id"),
                        "step_id": ev.get("step_id"),
                        "status": ev.get("status"),
                        "note": ev.get("note"),
                    }
                )
            except Exception:  # noqa: BLE001 — telemetry must never break a turn
                pass

        return sink

    def cancel(
        self,
        root_frame_id: str,
        execution_id: str | None = None,
        *,
        owner: dict | str | None = None,
        owner_id: str | None = None,
        reason: str = "cancelled by user",
    ) -> dict:
        """Cancel only an explicitly identified execution ticket and owner."""

        owner_kind = owner.get("kind") if isinstance(owner, dict) else owner
        owner_id = (
            owner.get("id") if isinstance(owner, dict) else owner_id
        ) or owner_id
        if not execution_id or not owner_kind or not owner_id:
            return {
                "ok": False,
                "frame_id": root_frame_id,
                "execution_id": execution_id,
                "reason": (
                    "exact cancellation requires execution_id, owner.kind, "
                    "and owner.id"
                ),
            }
        result = self.executions.cancel(
            root_frame_id,
            execution_id=execution_id,
            owner=str(owner_kind),
            owner_id=str(owner_id),
            reason=reason,
        )
        return self._after_execution_cancel(root_frame_id, result)

    def _cancel_current_for_lifecycle(
        self,
        root_frame_id: str,
        *,
        reason: str,
    ) -> dict:
        """Trusted lifecycle-only broad cancellation before close or stop."""

        result = self.executions.cancel_current(root_frame_id, reason=reason)
        return self._after_execution_cancel(root_frame_id, result)

    def cancel_review(self, root_frame_id: str) -> dict:
        """Cancel the root-scoped evidence review operation, if present."""

        with self._lock:
            if root_frame_id not in self.reviews.operations:
                return {
                    "ok": False,
                    "frame_id": root_frame_id,
                    "scope": "review",
                    "reason": "no_active_review",
                }
            self.reviews.cancel_locked(root_frame_id)
        return {"ok": True, "frame_id": root_frame_id, "scope": "review"}

    def _after_execution_cancel(
        self,
        root_frame_id: str,
        result: dict,
    ) -> dict:
        # A queued cancellation must not release the active Agent's approval or
        # reviewer.  Those session-global compatibility paths are touched only
        # after the coordinator proved the exact running owner.
        if not result.get("ok"):
            return result
        if result.get("scope") != "running":
            return result
        owner_result = result.get("owner") or {}
        if owner_result.get("kind") == "agent":
            with self._lock:
                state = self._sessions.get(root_frame_id)
            runner = state.delegation_runner if state is not None else None
            if runner is not None:
                try:
                    runner.cancel_all("parent execution cancelled")
                except Exception:  # noqa: BLE001 - parent cancel still succeeds
                    traceback.print_exc()
        with self._lock:
            self.reviews.cancel_locked(root_frame_id)
        # Release any pending permission prompt for this conversation (deny).
        try:
            from openai4s.permissions import broker

            broker().cancel_root(root_frame_id)
        except Exception:  # noqa: BLE001
            pass
        return result

    def interrupt_kernel(
        self,
        root_frame_id: str,
        execution_id: str | None = None,
        *,
        owner: dict | str | None = None,
        owner_id: str | None = None,
    ) -> dict:
        """Interrupt only the frozen lease owned by an exact execution ticket."""

        owner_kind = owner.get("kind") if isinstance(owner, dict) else owner
        owner_id = (
            owner.get("id") if isinstance(owner, dict) else owner_id
        ) or owner_id
        if not execution_id or not owner_kind or not owner_id:
            return {
                "ok": False,
                "frame_id": root_frame_id,
                "execution_id": execution_id,
                "reason": (
                    "exact kernel interrupt requires execution_id, owner.kind, "
                    "and owner.id"
                ),
            }
        result = self.executions.interrupt(
            root_frame_id,
            execution_id=str(execution_id),
            owner=str(owner_kind),
            owner_id=str(owner_id),
            reason="kernel interrupt requested by user",
        )
        return self._after_execution_cancel(root_frame_id, result)

    def _run_bootstrap(self, st: SessionState, kernel: Kernel | None = None) -> dict:
        """Run and persist the bootstrap facts observed for one generation."""

        target = kernel if kernel is not None else st.kernel
        boot = _maybe_call(getattr(self._skills_for(st), "bootstrap_code", ""))
        if target is None:
            return {"status": "failed", "error": "Python kernel is unavailable"}
        metadata = bootstrap_python_generation(target, st.workspace, boot)
        lifecycle_state = (
            "active" if metadata["status"] in {"active", "skipped"} else "bootstrapping"
        )
        st.kernels.record_bootstrap_if_current(
            "python", target, metadata, state=lifecycle_state
        )
        return metadata

    def restart_kernel(self, root_frame_id: str, project_id: str) -> dict:
        """Tear down + respawn the session's kernel (fresh namespace).

        Fixes the 'pip install then no way to restart the kernel' problem: the
        namespace is cleared, newly installed packages become importable in the
        clean process, and skill bootstrap is re-run. Variables from prior cells
        are gone (that is the point of a restart); the notebook history is kept.
        """
        st = self._state(root_frame_id, project_id)
        emit = self.hub.emitter(root_frame_id)
        with self._session_execution(
            st,
            owner="lifecycle",
            owner_id=f"restart-{uuid.uuid4().hex[:12]}",
            reason="kernel restart",
        ) as execution:
            self.recovery.touch(st)
            # the R kernel restarts with the session: drop it here and let the
            # next ```r cell respawn it fresh (same lazy path as first use)
            st.kernels.stop("r", manual=False, reason="session_restart")
            if st.kernel is None:
                self._ensure_kernel(st)
                lease = st.kernels.lease("python")
            elif st.desired_env and st.desired_env != st.env_name:
                # The active kernel is a transient base fallback. A full spawn
                # re-runs environment resolution so a recovered pinned env can
                # finally take effect; Kernel.restart() would reuse base Python.
                previous = st.kernels.lease("python")
                lease = self._spawn_kernel(st)
                if previous is not None and lease.kernel is previous.kernel:
                    # The pin is still unavailable, so resolution selected the
                    # same fallback key and ensure() correctly reused it. An
                    # explicit Restart must still clear that base namespace.
                    lease = st.kernels.restart(
                        "python",
                        after_restart=lambda kernel: self._run_bootstrap(st, kernel),
                    )
            else:
                lease = st.kernels.restart(
                    "python",
                    after_restart=lambda kernel: self._run_bootstrap(st, kernel),
                )
            gen = lease.generation if lease is not None else 0
            self.executions.mark_finalizing(
                execution, reason="publishing restarted kernel state"
            )
            emit(
                {
                    "type": "kernel_status",
                    "frame_id": root_frame_id,
                    "status": "restarted",
                    "generation": gen,
                    "generation_id": lease.generation_id if lease else None,
                }
            )
        return {
            "ok": True,
            "status": "restarted",
            "generation": gen,
            "generation_id": lease.generation_id if lease else None,
            "frame_id": root_frame_id,
        }

    def install_packages(
        self,
        packages: list[str],
        root_frame_id: str | None = None,
        project_id: str | None = None,
        restart: bool = True,
    ) -> dict:
        """pip-install package(s) into the kernel interpreter, then (optionally)
        restart the session kernel so they are importable in a clean process."""
        from openai4s.kernel import preinstall

        res = preinstall.install(packages)
        res["restarted"] = False
        if res.get("ok"):
            # The freeze cache is keyed by kernel generation on the premise that
            # an environment cannot change within one. An install breaks that:
            # with `restart: false` — or when the restart below fails — the same
            # generation's interpreter now has packages the cached list does not
            # mention, and later artifacts would be stamped with the pre-install
            # environment. That is provenance that is wrong, not missing.
            self.artifacts.invalidate_freeze_cache()
        if res.get("ok") and restart and root_frame_id:
            try:
                self.restart_kernel(root_frame_id, project_id or "default")
                res["restarted"] = True
            except Exception as error:  # noqa: BLE001
                # `POST /frames/<id>/kernel/install` returns this dict straight
                # to the client, so `str(e)` was a public body. A restart fails
                # through the kernel spawn and the sandbox setup, and an
                # `OSError` from either names the interpreter it tried to run
                # and the workspace directory it tried to run it in -- an
                # absolute path, and with it the account's username. The
                # install itself succeeded; what the caller needs to know is
                # that the restart did not, and that is what it now says.
                record_diagnostic(error, surface="kernel:restart_after_install")
                res["restart_error"] = "the kernel could not be restarted"
                res["restart_error_code"] = "kernel_restart_failed"
        if root_frame_id:
            emit = self.hub.emitter(root_frame_id)
            emit(
                {
                    "type": "kernel_status",
                    "frame_id": root_frame_id,
                    "status": "packages_installed",
                    "installed": res.get("installed", []),
                    "ok": res.get("ok", False),
                }
            )
        return res

    # -- kernel lifecycle: stop / start / status (per-session "notebook") ----
    def running_frames(self) -> set:
        """Set of root_frame_ids with a live turn — compute ONCE for list views
        instead of re-scanning _jobs per row."""
        return {
            j.root_frame_id for j in list(self._jobs.values()) if not j.done.is_set()
        }

    def is_running(self, root_frame_id: str) -> bool:
        """True while an agent turn is executing for this frame (survives client
        disconnect — the MessageJob runs in a daemon thread)."""
        for job in list(self._jobs.values()):
            if job.root_frame_id == root_frame_id and not job.done.is_set():
                return True
        return False

    def kernel_alive(self, root_frame_id: str) -> bool:
        """Cheap 'is this session's kernel process live' — no job scan (unlike
        kernel_status)."""
        st = self._sessions.get(root_frame_id)
        return bool(st and st.kernels.alive("python"))

    def kernel_status(self, root_frame_id: str) -> dict:
        """Report a session's notebook/kernel state so the UI can offer
        stop/start/resume."""
        st = self._sessions.get(root_frame_id)
        supervisor_status = st.kernels.status("python") if st else None
        persisted = (
            None
            if supervisor_status is not None
            else self.store.latest_kernel_generation(
                root_frame_id,
                "python",
                branch_id=self.store.active_session_branch(root_frame_id),
            )
        )
        alive = bool(supervisor_status and supervisor_status["alive"])
        if st is None:
            state = "ended" if persisted is not None else "none"
        else:
            state = supervisor_status["state"]
        quarantine = self.import_quarantine(root_frame_id)
        return {
            "frame_id": root_frame_id,
            "branch_id": (
                st.branch_id
                if st is not None
                else self.store.active_session_branch(root_frame_id)
            ),
            "state": state,  # none | running | stopped | ended
            "alive": alive,
            "generation": supervisor_status["generation"] if supervisor_status else 0,
            "generation_id": (
                supervisor_status.get("generation_id")
                if supervisor_status
                else (persisted or {}).get("generation_id")
            ),
            "generation_ordinal": (
                supervisor_status.get("generation_ordinal")
                if supervisor_status
                else (persisted or {}).get("ordinal")
            ),
            "last_activity_at": (
                supervisor_status.get("last_activity_at")
                if supervisor_status
                else (persisted or {}).get("last_activity_at")
            ),
            "ended_reason": (
                supervisor_status.get("ended_reason")
                if supervisor_status
                else (persisted or {}).get("ended_reason")
            ),
            "turn_running": self.is_running(root_frame_id),
            "cell_count": (st.cell_index if st else 0),
            "manual_stop": bool(supervisor_status and supervisor_status["manual_stop"]),
            "env": self._env_summary(st),
            "repl_enabled": bool(self.cfg.notebook_repl),
            "view_only": bool(quarantine),
            "trust_state": "quarantined" if quarantine else "trusted",
            "quarantine_reason": (
                str(quarantine.get("reason") or "untrusted_session_package")
                if quarantine
                else None
            ),
        }

    def _env_summary(self, st: SessionState | None) -> dict:
        """Small {name, language, python_version, pending} describing the env this
        session's kernel runs in — for the Notebook env chip. Cheap (versions are
        cached on the Environment)."""
        from openai4s.kernel import environments as envmod

        name = self._selected_env_name(st) if st else envmod.default_env_name()
        env = envmod.get_environment(name)
        return {
            "name": name,
            "language": env.language if env else "python",
            "python_version": env.python_version() if env else None,
            "pending": (st.pending_env if st else None),
            # Canonical cell-grouping label so the frontend labels live cells the
            # SAME way the server labels persisted ones (it must not re-derive
            # from `name`, which disagrees when OPENAI4S_DEFAULT_ENV is a non-base
            # env — the default env always collapses to plain "python").
            "kernel_id": self._env_label(name),
        }

    @staticmethod
    def _env_label(name: "str | None") -> str:
        """Runtime segment label for an env name: 'python' for the default/base
        env, 'python — <env>' for a switched prebuilt env. Groups Notebook cells."""
        from openai4s.kernel import environments as envmod

        name = (name or "").strip()
        if not name or name in ("python", "base") or name == envmod.default_env_name():
            return "python"
        return f"python — {name}"

    def _kernel_id(self, st: "SessionState | None") -> str:
        """Runtime segment label for the cells a session's python kernel runs."""
        return self._env_label(getattr(st, "env_name", None))

    def _kernel_language(self, st: "SessionState | None") -> str:
        """Syntax language for a python-kernel cell (REPL/manual paths)."""
        return "python"

    def _r_kernel_id(self, st: "SessionState | None") -> str:
        """Runtime segment label for ```r cells: 'r' for the default resolution
        (the prebuilt 'r' env or Rscript on PATH), 'r — <env>' when retargeted."""
        name = (getattr(st, "r_env_name", None) or "").strip()
        if not name or name == "r":
            return "r"
        return f"r — {name}"

    def _ensure_r_kernel(self, st: SessionState) -> str | None:
        """Make the supervised R slot live and targeted, or soft-fail.

        Mirrors agent/loop.py Agent._execute_r: respawn when the worker died or
        host.env.use() retargeted the R channel (dispatcher.active_r_env). The
        model sees a missing R as an error observation and can fall back to
        python — this never raises.
        """
        dispatcher = self._ensure_runtime(st)
        want = getattr(dispatcher, "active_r_env", None)
        from openai4s.kernel.environments import get_environment
        from openai4s.kernel.r_kernel import spawn_r_kernel

        try:
            previous = st.kernels.lease("r")
            lease = st.kernels.ensure(
                "r",
                want,
                lambda: spawn_r_kernel(
                    cwd=str(st.workspace), env=get_environment(want)
                ),
            )
            if previous is None or previous.kernel is not lease.kernel:
                bootstrap_r_generation(st.kernels, st.workspace, lease)
        except Exception as e:  # noqa: BLE001 — soft-fail into the observation
            return f"R kernel unavailable: {e}"
        st.r_env_name = lease.key
        return None

    def stop_kernel(self, root_frame_id: str, project_id: str = "default") -> dict:
        """Shut the kernel process down (free its resources) but keep the session
        — conversation, notebook history and workspace files all survive so it
        can be started again to resume. A running turn is cancelled first."""
        st = self._sessions.get(root_frame_id)
        if st is None:
            # Same shape as the stopped case. A caller should not have to
            # handle two response shapes from one route depending on whether
            # the session happened to be resident.
            return {
                "ok": True,
                "state": "none",
                "frame_id": root_frame_id,
                "cancelled_queued": [],
            }
        emit = self.hub.emitter(root_frame_id)
        with st.stop_lock:
            try:
                # Reserve Stop intent and its FIFO ticket atomically with respect
                # to new message/REPL/lifecycle admission.  The outer finally
                # also reopens admission if coordinator submission itself fails.
                with st.admission_lock:
                    st.stop_finished.clear()
                    st.stop_requested.set()
                    cancel_result = self._cancel_current_for_lifecycle(
                        root_frame_id,
                        reason="manual kernel stop",
                    )
                    # ...and everything queued behind it. Stop used to cancel
                    # only the running execution, then submit its own ticket to
                    # the back of the same FIFO — so anything already waiting
                    # ran first, and a turn admitted after `stop_requested` is
                    # set blocks on `stop_finished` as soon as it submits
                    # anything, which is exactly what Stop sets when it
                    # finishes. Measured: no return after 40s with three items
                    # queued behind a turn that cancelled correctly.
                    #
                    # Cancelling them is also what the user asked for: a queued
                    # follow-up is waiting for a kernel that is being stopped.
                    drained = self.executions.drain_queued(
                        root_frame_id, reason="kernel stopped"
                    )
                    ticket = self.executions.submit(
                        root_frame_id,
                        owner="lifecycle",
                        owner_id=f"stop-{uuid.uuid4().hex[:12]}",
                        branch_id=st.branch_id,
                        resource_keys=("workspace", "kernel:python", "kernel:r"),
                        metadata={"reason": "manual kernel stop"},
                    )
                # A pre-coordinator legacy holder has no execution id to cancel.
                # Freeze its leases and use ABA-safe exact interrupts rather
                # than the old broad supervisor interrupt.
                if not (cancel_result or {}).get("ok"):
                    for language in ("python", "r"):
                        lease = st.kernels.lease(language)
                        if lease is not None:
                            st.kernels.interrupt_if_current(lease)
                with self.executions.admitted(ticket, cancel_event=st.cancel):
                    # Wait for the single protocol reader to leave before
                    # detaching and shutting down its exact worker slots.
                    with st.turn_lock:
                        st.kernels.stop("python", manual=True, reason="manual_stop")
                        st.kernels.stop("r", manual=True, reason="manual_stop")
                    stopped_status = st.kernels.status("python")
                    self.executions.mark_finalizing(
                        ticket, reason="publishing stopped kernel state"
                    )
                    # Publish before waking a queued start; its later "started"
                    # event must remain the final visible lifecycle state.
                    emit(
                        {
                            "type": "kernel_status",
                            "frame_id": root_frame_id,
                            "status": "stopped",
                            "generation_id": stopped_status.get("generation_id"),
                            "ended_reason": "manual_stop",
                        }
                    )
                # Preserve the compatible stopped marker until a new admitted
                # execution clears it; do this after the lifecycle ticket exits
                # so Stop itself is not projected as cancelled.
                st.cancel.set()
            finally:
                st.stop_requested.clear()
                st.stop_finished.set()
        # Reported, not discarded. A queued follow-up that will never run is
        # something the user is entitled to know about — silently dropping work
        # they submitted is the same failure as silently dropping a referenced
        # file from a prompt.
        return {
            "ok": True,
            "state": "stopped",
            "frame_id": root_frame_id,
            "cancelled_queued": drained,
        }

    def start_kernel(self, root_frame_id: str, project_id: str = "default") -> dict:
        """(Re)start a stopped/absent kernel WITHOUT wiping the conversation, so
        the user can resume. Idempotent when already running."""
        st = self._state(root_frame_id, project_id)
        emit = self.hub.emitter(root_frame_id)
        with self._session_execution(
            st,
            owner="lifecycle",
            owner_id=f"start-{uuid.uuid4().hex[:12]}",
            reason="kernel start",
        ) as execution:
            self._ensure_kernel(st)
            lease = st.kernels.lease("python")
            gen = lease.generation if lease is not None else 0
            self.executions.mark_finalizing(
                execution, reason="publishing started kernel state"
            )
            emit(
                {
                    "type": "kernel_status",
                    "frame_id": root_frame_id,
                    "status": "started",
                    "generation": gen,
                    "generation_id": lease.generation_id if lease else None,
                }
            )
        return {
            "ok": True,
            "state": "running",
            "generation": gen,
            "generation_id": lease.generation_id if lease else None,
            "frame_id": root_frame_id,
        }

    # -- prebuilt environments: list / select (per-session runtime) ---------
    def list_environments(self, root_frame_id: str | None = None) -> dict:
        """The offerable prebuilt environments + which one this session uses.

        Powers the Notebook env selector and host.env.list(): the agent/user
        picks an env that already has the needed packages instead of installing
        into one kernel every task."""
        from openai4s.kernel import environments as envmod

        st = self._sessions.get(root_frame_id) if root_frame_id else None
        current = self._selected_env_name(st) if st else envmod.default_env_name()
        return {
            "environments": envmod.list_environments(with_packages=True),
            "current": current,
            "default": envmod.default_env_name(),
            "pending": (st.pending_env if st else None),
        }

    def set_env(
        self, root_frame_id: str, env_name: str, project_id: str = "default"
    ) -> dict:
        """Select a prebuilt Python environment for this session.

        A live worker is replaced build-first.  Before the first worker (or
        after Stop), only the selection is persisted; selection never allocates
        compute by itself.
        """
        from openai4s.kernel import environments as envmod

        env = envmod.get_environment(env_name)
        if env is None:
            return {"error": f"unknown environment: {env_name!r}"}
        if env.interpreter is None:
            return {
                "error": (
                    f"'{env_name}' is a {env.language} environment with "
                    "no Python — the notebook kernel needs a Python "
                    "interpreter. R-only envs run ```r cells (the agent can "
                    'pin one with host.env.use("' + env_name + '")).'
                )
            }
        st = self._state(root_frame_id, project_id)
        emit = self.hub.emitter(root_frame_id)
        with self._session_execution(
            st,
            owner="lifecycle",
            owner_id=f"env-{uuid.uuid4().hex[:12]}",
            reason="kernel environment change",
        ) as execution:
            st.pending_env = None
            alive = st.kernels.alive("python")
            already = alive and st.env_name == env_name
            st.desired_env = env_name
            self._persist_env(root_frame_id, env_name)
            if alive and not already:
                lease = self._spawn_kernel(st)
            else:
                lease = st.kernels.lease("python")
                if not alive and st.dispatcher is not None:
                    st.dispatcher.active_env_bin = env.bin_dir
            gen = lease.generation if lease is not None else 0
            lifecycle = st.kernels.status("python")["state"]
            self.executions.mark_finalizing(
                execution, reason="publishing environment state"
            )
            emit(
                {
                    "type": "kernel_status",
                    "frame_id": root_frame_id,
                    "status": "env_changed",
                    "generation": gen,
                    "generation_id": lease.generation_id if lease else None,
                    "env": self._env_summary(st),
                }
            )
        return {
            "ok": True,
            "state": lifecycle,
            "env": env_name,
            "generation": gen,
            "generation_id": lease.generation_id if lease else None,
            "language": env.language,
            "python_version": env.python_version(),
            "frame_id": root_frame_id,
        }

    def _apply_pending_env(self, st: SessionState, emit) -> None:
        """If the agent requested an env switch (host.env.use) during the turn,
        apply it before the next cell so its imports land in the chosen env. Runs
        under the caller's turn_lock. A no-op unless the target differs and is a
        valid Python env."""
        target = st.pending_env
        st.pending_env = None
        if not target:
            return
        from openai4s.kernel import environments as envmod

        env = envmod.get_environment(target)
        if env is None:
            return
        if env.interpreter is None:
            # R-only env: the python kernel is untouched. The dispatcher already
            # set active_r_env (host.env.use), and the next ```r cell's
            # _ensure_r_kernel respawns the R kernel against it — nothing to do
            # here beyond not treating it as a python switch.
            return
        st.desired_env = target
        self._persist_env(st.root_frame_id, target)
        if not st.kernels.alive("python"):
            if st.dispatcher is not None:
                st.dispatcher.active_env_bin = env.bin_dir
            status = st.kernels.status("python")
            emit(
                {
                    "type": "kernel_status",
                    "frame_id": st.root_frame_id,
                    "status": "env_changed",
                    "generation": status["generation"],
                    "generation_id": status.get("generation_id"),
                    "env": self._env_summary(st),
                }
            )
            return
        if target == st.env_name:
            return
        lease = self._spawn_kernel(st)
        emit(
            {
                "type": "kernel_status",
                "frame_id": st.root_frame_id,
                "status": "env_changed",
                "generation": lease.generation,
                "generation_id": lease.generation_id,
                "env": self._env_summary(st),
            }
        )

    def submit_message(
        self,
        root_frame_id: str,
        project_id: str,
        user_text: str,
        model: str | None = None,
        plan: bool = False,
        annos: list | None = None,
        explore: bool = False,
        on_admitted: Callable[[MessageJob], None] | None = None,
    ) -> MessageJob:
        """Start a user turn in a background thread.

        The HTTP handler may still wait for completion for legacy frontend
        compatibility, but the work is no longer tied to the client socket.

        Everything that can refuse this turn runs *here*, synchronously, before
        a ticket exists. Both checks below used to happen later -- one inside
        the worker thread, one nowhere at all -- and "later" is the wrong place
        for a refusal twice over: the client has already been told 202
        accepted, and a queued follow-up would not discover the problem until
        it reached the head of a queue the user had since walked away from.
        """
        st = self._state(root_frame_id, project_id)

        # 1. Bound the text. The only limit was `_MAX_JSON_BODY_BYTES`, which is
        #    the *session archive* cap (128 MiB) doing duty as a chat-message
        #    cap. An 8 MiB message is persisted, replayed into every later turn,
        #    and is eight times the whole context window on its own -- so the
        #    session is bricked, and compaction cannot rescue it because
        #    summarising the message means sending it. Refusing costs the user
        #    one paste; accepting costs them the session.
        text = str(user_text or "")
        if len(text) > MAX_MESSAGE_CHARS:
            raise GatewayError(
                413,
                f"this message is {len(text):,} characters; the limit is "
                f"{MAX_MESSAGE_CHARS:,}. Save the text as a file and reference "
                "it with @name so the agent reads it from disk instead.",
                "message_too_large",
            )

        # 2. Freeze the model identity at send, not at dequeue. Binding when the
        #    turn finally runs meant a follow-up sitting in the queue adopted
        #    whatever the profile said by then. `run_message` still calls this --
        #    it is idempotent, and other callers (plans) come in that way.
        #
        #    Frozen onto the *ticket* as well, not only onto the frame. The frame's
        #    pin is mutable by design -- `POST /frames/{id}/model-binding` rewrites
        #    it, which is the documented answer to a dangling pin -- so an item
        #    accepted under P and still in the FIFO was re-resolved from the frame
        #    at dequeue and could run on Q. The client was told 202 under P.
        frozen = self.freeze_model_binding(root_frame_id)

        job = MessageJob(f"job-{uuid.uuid4().hex[:12]}", root_frame_id)
        job.model_profile_id = frozen["model_profile_id"]
        job.model_profile_revision = frozen["model_profile_revision"]
        ticket = self._queue_execution(
            st,
            owner="agent",
            owner_id=job.job_id,
            reason="user message",
            # What the browser needs to show a queued follow-up and to name the
            # one it wants dropped. Read off the ticket rather than re-derived:
            # the profile pair below is the one this item was *accepted* under,
            # and the frame's pin -- the only other place it is written -- is
            # rewritable while the item waits, so re-reading it at render time
            # would show a queued item running under a configuration it is not.
            metadata={
                "preview": queue_preview(text),
                "model_profile_id": job.model_profile_id,
                "model_profile_revision": job.model_profile_revision,
            },
        )
        job.execution_id = ticket.execution_id
        job.execution_owner = ticket.owner.as_dict()
        with self._lock:
            # prune finished jobs so _jobs (and is_running scans) stay bounded,
            # keeping the most recent finished one per frame for wait_result races
            done = [
                jid
                for jid, j in self._jobs.items()
                if j.done.is_set() and (time.time() - (j.finished_at or 0)) > 300
            ]
            for jid in done:
                self._jobs.pop(jid, None)
            self._jobs[job.job_id] = job
        # The branch this turn was admitted on, taken from the ticket that
        # admitted it. No second lookup: the ticket already resolved this, and
        # asking the Store again during a failure is precisely the query most
        # likely to fail alongside it.
        job.branch_id = ticket.branch_id or st.branch_id or ""

        def _target() -> None:
            # The scope wraps the handlers as well as the call: the note exists
            # so the `except` below can amend the row `run_message` already
            # wrote, and a context manager closing as the exception unwinds
            # would take it away a moment before that handler runs.
            self._enter_turn_scope(job.job_id)
            # The turn runs on this thread under the id its ticket was issued
            # with. `carry_context` copies whatever the request thread had --
            # which is nothing for a direct submit -- so without this
            # `run_message` mints a second id and the socket disagrees with the
            # 202 about which request just failed.
            token = set_correlation_id(job.request_id)
            # And under the identity its ticket was issued to. Re-set from the
            # ticket rather than relied upon from `carry_context`, for the same
            # reason as the id: a queued turn is dequeued by whichever thread
            # gets there, which may be a later request's, and inheriting *that*
            # request's identity would run one member's turn as another.
            principal_token = execution_principal.set_principal(job.principal)
            #: Filled inside the lease, published after it. Both halves
            #: matter: the side effects must happen while this turn still owns
            #: the session, and the completion must not become visible while
            #: its ticket is still active -- `runner.is_running` reads both, so
            #: finishing inside the lease opens a window where a done job and a
            #: live ticket disagree.
            outcome: dict = {}
            try:
                # Every durable and broadcast effect this turn owes -- the
                # projection, the persisted row, the frame's status, the prose
                # and the terminal event -- happens while the lease is still
                # held. `job.finish` deliberately does NOT: publishing the
                # outcome inside the lease would set `job.done` while the
                # ticket is still active, and `runner.is_running` reads both.
                # The side effects used to run after the `with` closed, so the
                # next turn was already promoted and had written `processing`
                # when A's `update_frame(status="failed")` landed: the durable
                # status said failed while B was running, `/status` and the
                # session list contradicted the socket, and crash recovery
                # would have treated B as the failure.
                #
                # An owner check before the write cannot fix it -- B can be
                # promoted between the check and the write. Only holding the
                # lease can.
                with self.executions.admitted(ticket, cancel_event=st.cancel):
                    try:
                        result = self.run_message(
                            root_frame_id,
                            project_id,
                            user_text,
                            model,
                            plan,
                            annos,
                            explore,
                            # What this item was accepted under, carried from the
                            # request thread rather than re-read from the frame.
                            frozen_binding=(
                                (job.model_profile_id, job.model_profile_revision)
                                if job.model_profile_id
                                else None
                            ),
                        )
                        result.setdefault("job_id", job.job_id)
                        result.setdefault("execution_id", ticket.execution_id)
                        result.setdefault("owner", ticket.owner.as_dict())
                        outcome["result"] = result
                    except ExecutionCancelled:
                        # Handled once, outside the lease. A cancellation raised
                        # *by* `admitted` -- a queued item stopped before it was
                        # ever admitted -- never reaches this clause at all, so
                        # projecting here would either miss that case or do it
                        # twice.
                        raise
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc()
                        emit = self.hub.emitter(root_frame_id)
                        message = job.project(e, "web:message")
                        self._persist_outer_failure(root_frame_id, job, message)
                        self._best_effort(
                            "frame_status",
                            lambda: self.store.update_frame(
                                root_frame_id, status="failed"
                            ),
                        )
                        self._best_effort(
                            "prose",
                            lambda: (
                                emit(
                                    {
                                        "type": "text_reset",
                                        "frame_id": root_frame_id,
                                        # Same identity as the terminal event: a
                                        # failure that arrives after the next turn has
                                        # started would otherwise wipe that turn's
                                        # stream and print its predecessor's error into
                                        # it.
                                        **(
                                            {"execution_id": job.execution_id}
                                            if job.execution_id
                                            else {}
                                        ),
                                    }
                                ),
                                emit(
                                    {
                                        "type": "text_chunk",
                                        "frame_id": root_frame_id,
                                        "block_type": "text",
                                        "chunk": f"\n\n_Error: {message}_\n",
                                        **(
                                            {"execution_id": job.execution_id}
                                            if job.execution_id
                                            else {}
                                        ),
                                    }
                                ),
                            ),
                        )
                        self._best_effort(
                            "terminal",
                            lambda: emit(
                                self._terminal_failure_event(root_frame_id, job)
                            ),
                        )
                        outcome["error"] = message
                        # Re-raised after the side effects, so the coordinator
                        # marks this ticket FAILED. Swallowing it left the lease
                        # exiting cleanly: the execution log read
                        # queued -> running -> completed while the job and the
                        # socket both said failed.
                        outcome["handled"] = e
                        raise
            except ExecutionCancelled as e:
                # First: `ExecutionCancelled` is an `Exception`, so the generic
                # clause below would otherwise swallow every cancellation and
                # report it as a failure.
                if not job.done.is_set():
                    job.finish(
                        result={
                            "status": "cancelled",
                            "frame_id": root_frame_id,
                            "job_id": job.job_id,
                            "execution_id": ticket.execution_id,
                            "owner": ticket.owner.as_dict(),
                            "reason": str(e),
                        }
                    )
            except Exception as e:  # noqa: BLE001
                if outcome.get("handled") is not e:
                    # Not ours. The lease itself refused, or something outside
                    # the inner handler failed -- project it once, here.
                    outcome["error"] = job.project(e, "web:message")
            finally:
                if not job.done.is_set():
                    # The ticket is released by now, so `runner.is_running`
                    # and `job.done` cannot disagree.
                    if "result" in outcome:
                        job.finish(result=outcome["result"])
                    elif outcome.get("error"):
                        job.finish(error=outcome["error"])
                self._exit_turn_scope(job.job_id)
                reset_correlation_id(token)
                try:
                    execution_principal.reset(principal_token)
                except Exception:  # noqa: BLE001 — never fail a finished turn
                    pass

        try:
            # Durable correlation BEFORE the worker exists.
            #
            # The admission ledger used to be stamped with this turn's request
            # and job ids *after* `submit_message` returned -- so a transient
            # write failure produced a 202 and a running turn whose ledger row
            # still carried no correlation at all. That is byte-for-byte the
            # shape a synchronous refusal leaves, and it is the one distinction
            # a client whose 202 was lost has to make: resending is the right
            # answer to a refusal and the wrong answer to an accepted turn.
            #
            # Here, and inside this `try`, so a failure takes the same
            # unstarted-job path as a failed `Thread.start`: nothing runs, the
            # pins go back, and the caller is told the turn was not accepted
            # rather than being handed an accepted-but-uncorrelated job.
            if on_admitted is not None:
                on_admitted(job)
            t = threading.Thread(
                target=carry_context(_target),
                name=f"openai4s-turn-{root_frame_id}",
                daemon=True,
            )
            job.thread = t
            t.start()
        except BaseException as error:
            self._abort_unstarted_job(job, ticket, error)
            raise
        return job

    def reconcile_admission(
        self, root_frame_id: str, reservation_id: str
    ) -> dict | None:
        """What happened to one admission, for a client whose answer was lost.

        Scoped by frame: a reservation id travels in a response, so it is a
        value a caller holds rather than a capability. Returns None when this
        session has no such admission, which is the same answer a caller
        guessing an id deserves.
        """
        record = self.store.get_admission(reservation_id, root_frame_id=root_frame_id)
        if record is None:
            return None
        # Derived from the pins, not read off the ledger.
        #
        # The ledger records intent and is written by the same request that can
        # fail: an update fault after the consume leaves it saying `reserved`
        # while the pins are already `sent`, and a client asking "what
        # happened" would be told the opposite of the truth. The annotations
        # are the authority -- they are what the turn actually consumed -- so
        # the state is computed from them, and the ledger supplies correlation.
        ids = list(record["annotation_ids"] or [])
        state = record["state"]
        if state not in _TERMINAL_ADMISSION_STATES and ids:
            # Row-derived, and only here. The terminal states are written in
            # the same transaction as the row change they describe, so they are
            # evidence about what the *turn* did and stay true afterwards: a
            # pin that was sent and is later resolved, dismissed or deleted
            # does not un-send the message it went out on. Deriving `sent` from
            # `status == 'sent'` reported exactly that as `released`, telling a
            # client its comments were never taken when the model had already
            # answered them.
            #
            # `reserved` and `pending` are the states a fault can leave stale,
            # and there the rows really are the authority.
            present = [
                row for row in (self.store.get_annotation(a) for a in ids) if row
            ]
            if any(
                row.get("reservation_id") == reservation_id
                and row.get("status") == "reserved"
                for row in present
            ):
                state = "pending"
            elif not present:
                state = "unknown"
            elif all(
                row.get("status") in _CONSUMED_ANNOTATION_STATES for row in present
            ):
                state = "sent"
            elif all(row.get("status") == "open" for row in present):
                state = "released"
            else:
                state = "unknown"
        return {
            "state_from_ledger": record["state"],
            "reservation_id": record["reservation_id"],
            "state": state,
            "annotations": ids,
            "request_id": record["request_id"],
            "job_id": record["job_id"],
        }

    def submit_review(self, root_frame_id: str, project_id: str) -> MessageJob:
        return self.reviews.submit(root_frame_id, project_id)

    # -- capture figures + written files after a cell -> artifacts ---------
    def _snapshot(self, ws: Path) -> dict[str, int]:
        return self.artifacts.snapshot(ws)

    def _register_file(
        self,
        st: SessionState,
        path: Path,
        cell_id: str,
        emit,
        env_snapshot_id: str | None = None,
    ) -> dict | None:
        return self.artifacts.register_file(
            st,
            path,
            cell_id,
            emit,
            env_snapshot_id=env_snapshot_id,
        )

    def _capture(
        self,
        st: SessionState,
        cell_index: int,
        cell_id: str,
        before: dict[str, int],
        emit,
        language: str = "python",
    ) -> tuple[list, list, list]:
        captured = self._capture_artifacts(
            st,
            cell_index,
            cell_id,
            before,
            emit,
            language,
        )
        return captured.figures, captured.files_written, captured.artifacts

    def _capture_artifacts(
        self,
        st: SessionState,
        cell_index: int,
        cell_id: str,
        before: dict[str, int],
        emit,
        language: str,
    ) -> CaptureResult:
        kernel = st.kernel
        run_system_cell = (
            (lambda code: kernel.execute(code, origin="system"))
            if kernel is not None
            else None
        )
        return self.artifacts.capture(
            st,
            cell_index,
            cell_id,
            before,
            emit,
            language=language,
            run_system_cell=run_system_cell,
            drain_remote_provenance=self._remote_provenance_drain(st),
        )

    def _invoke_control_with_artifacts(self, st, call, emit, invoke):
        """Capture files written by model-native control tools exactly once.

        Kernel-side ``host.write_file`` remains inside the normal Cell
        transaction.  This wrapper is intentionally only installed around the
        model's native/legacy JSON control-tool boundary, where no Cell
        snapshot exists.
        """
        ledger = getattr(st, "active_action_ledger", None)
        group_id = getattr(ledger, "current_group_id", None)
        call_id = (
            call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
        )
        binder = getattr(st.dispatcher, "bind_action_context", None)
        if callable(binder):
            with binder(
                {
                    "action_group_id": group_id,
                    "action_id": call_id,
                    "tool_call_id": call_id,
                }
            ):
                return self._invoke_control_with_artifacts_bound(st, call, emit, invoke)
        return self._invoke_control_with_artifacts_bound(st, call, emit, invoke)

    def _invoke_control_with_artifacts_bound(self, st, call, emit, invoke):
        """Run one already-attributed native action and capture its files."""

        self.recovery.touch(st)
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
        tool = get_tool(name)
        if tool is None or not tool.writes_files:
            try:
                return invoke()
            finally:
                self.recovery.touch(st)

        before = self.artifacts.snapshot(st.workspace)
        self.artifacts.protect_latest(st)
        try:
            return invoke()
        finally:
            try:
                captured = self.artifacts.capture(
                    st,
                    st.cell_index,
                    None,
                    before,
                    emit,
                    language="native",
                    drain_remote_provenance=self._remote_provenance_drain(st),
                )
                if captured.artifacts:
                    self._emit_artifact_step(
                        st,
                        "Saving "
                        + (
                            captured.artifacts[0]["filename"]
                            if len(captured.artifacts) == 1
                            else f"{len(captured.artifacts)} artifacts"
                        ),
                        captured.artifacts,
                        emit,
                    )
            except Exception:  # noqa: BLE001 — capture cannot mask tool outcome
                traceback.print_exc()
            self.recovery.touch(st)

    def _capture_env_snapshot(self, st=None) -> str | None:
        return self.artifacts.capture_environment(self._remote_provenance_drain(st))

    @staticmethod
    def _remote_provenance_drain(st):
        dispatcher = getattr(st, "dispatcher", None)
        if dispatcher is not None and hasattr(dispatcher, "pop_remote_provenance"):
            return dispatcher.pop_remote_provenance
        return None

    # -- run one user message ---------------------------------------------
    def effective_api_key(self) -> str:
        """The API key actually in effect (runtime settings override → cfg).

        Placeholder stubs persisted before the config-level filter existed
        (e.g. a seeded profile activated with `your-api-key-here`) are ignored
        so the UI banner matches what `_llm_cfg` actually sends.
        """
        try:
            v = _clean_api_key(self.store.get_secret_setting("llm_api_key"))
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
        return self.cfg.llm.api_key or ""

    def _llm_cfg(self, st: "SessionState | None" = None):
        """Effective LLM config = base cfg + runtime overrides (Customize→Models)
        + the session's chosen model. Makes the model selector real.

        Reads the 4 settings once (callers should resolve this once per turn, not
        per loop iteration — see _loop). When the PROVIDER is overridden we must
        NOT inherit the base provider's concrete base_url/model, or requests go to
        the wrong endpoint; leaving them empty lets LLMConfig.__post_init__
        re-resolve the new provider's defaults.
        """
        # The resolution itself lives in openai4s.llm.resolve, shared with
        # `doctor`. It had a second implementation there that read cfg.llm
        # alone, so an install configured entirely through the UI — the
        # documented path, since the daemon boots with no key — was diagnosed
        # `model FAIL` while working perfectly.
        from openai4s.llm.resolve import resolve_llm_config

        # Honour the session's pin before falling back to whatever is active.
        #
        # `bind_model_revision` wrote `model_profile_id` / `model_profile_revision`
        # on every session and `revision_config` was used only as an existence
        # test — so the pin was write-only, and the turn was dispatched to the
        # globally active profile's provider, endpoint, model AND credential
        # while the database recorded revision N of a different profile. A
        # session pinned to A and continued after B was activated ran on B and
        # said it ran on A. That is the whole thing D2 exists to prevent, and it
        # was recorded rather than enforced.
        pinned = self._pinned_llm_config(st)
        if pinned is not None:
            return self._apply_user_llm_key(pinned, st)
        return self._apply_user_llm_key(
            resolve_llm_config(
                self.cfg.llm,
                self.store,
                model_override=(st.model if (st is not None and st.model) else None),
            ),
            st,
        )

    def _apply_user_llm_key(self, cfg, st: "SessionState | None"):
        """Swap in the session owner's own credential, if they have one (M4-1).

        Applied here rather than at each call site because this method is the
        single place a Web turn's LLM configuration is decided — the turn
        loop, the reviewer and every other provider request downstream all
        read what it returns. A per-call-site override is how one of them
        ends up billing the group for a user who thought they were paying
        their own way.

        The override is per *provider*: a user with their own Anthropic
        account and no OpenAI key runs on their key for one and the group's
        for the other, which is the ordinary arrangement rather than an
        exotic one. Absence of a row is the fallback, so a single-user
        install and a team member with no key of their own are the same code
        path as before (INV-1).

        A configured-but-unreadable key is a refusal, not a silent fallback:
        the user asked for their own credential to be used, and quietly
        charging the group instead is a decision they did not make. A
        *lookup* that fails for infrastructural reasons is different, and
        falls back — availability over bookkeeping, matching the quota gate.
        """
        if st is None or not getattr(st, "root_frame_id", ""):
            return cfg
        provider = getattr(cfg, "provider", "") or ""
        if not provider:
            return cfg
        try:
            owner = self.store.team.session_owner(st.root_frame_id)
        except Exception:  # noqa: BLE001 — no ownership record, no override
            return cfg
        if not owner:
            return cfg
        try:
            record = self.store.user_keys.get(owner["user_id"], provider)
        except Exception:  # noqa: BLE001
            return cfg
        if record is None:
            return cfg
        try:
            secret = self.store.secrets.get(record.secret_ref)
        except Exception:  # noqa: BLE001
            # A reference the broker will not even parse — a row from a
            # database moved between machines, or a backend that changed
            # under it. Same answer as an empty slot: the user asked for
            # their key, and we cannot honour it.
            secret = None
        if not secret:
            raise GatewayError(
                409,
                f"your own {provider} key is configured but could not be read; "
                f"set it again or remove it to fall back to the shared key",
                "user_key_unreadable",
            )
        from dataclasses import replace

        return replace(cfg, api_key=secret)

    def _pinned_llm_config(self, st: "SessionState | None"):
        """The configuration this session named, or None when it named none.

        `None` now means exactly one thing: there is no pin, so the active
        profile is the right answer. It used to also mean "there is a pin and it
        cannot be honoured", and the caller could not tell the two apart -- so a
        profile that went away, a revision missing from the history or a revoked
        credential silently ran the turn on whichever profile happens to be
        active, while the frame went on recording the pinned one. Recorded as A,
        executed as B. That is precisely what D2 exists to prevent, and being
        "conservative" about it meant preferring a wrong answer to a refusal.

        A pin that cannot be honoured now raises `GatewayError(409,
        model_revision_unavailable)`, which `POST /frames/{id}/model-binding`
        already answers.
        """
        if st is None or not getattr(st, "root_frame_id", ""):
            return None
        try:
            frozen = getattr(st, "frozen_model_binding", None)
            if frozen:
                # What this turn was accepted under. Read before the frame on
                # purpose: an item that has been sitting in the queue must not
                # adopt a pin the user changed after it was admitted.
                profile_id, revision = str(frozen[0] or ""), int(frozen[1] or 0)
            else:
                frame = self.store.get_frame(st.root_frame_id) or {}
                profile_id = str(frame.get("model_profile_id") or "")
                revision = int(frame.get("model_profile_revision") or 0)
            if not profile_id or revision <= 0:
                return None
            profile = next(
                (
                    item
                    for item in self.store.list_model_profiles()
                    if item.get("id") == profile_id
                ),
                None,
            )
            unavailable = GatewayError(
                409,
                "this session is pinned to a model configuration that is no "
                "longer usable; rebind it to continue",
                "model_revision_unavailable",
            )
            if profile is None:
                raise unavailable
            recorded = ModelProfileService.revision_config(profile, revision)
            if not recorded:
                raise unavailable
            service = ModelProfileService(
                self.store, self.cfg, providers=lambda: PROVIDERS
            )
            api_key = service.resolve_key(profile)
            if not api_key:
                # A revoked or cleared key. Falling through to the active profile
                # here is the substitution this method exists to stop.
                raise unavailable
            from dataclasses import replace

            return replace(
                self.cfg.llm,
                provider=str(recorded.get("provider") or "") or self.cfg.llm.provider,
                base_url=str(recorded.get("base_url") or "") or None,
                # The recorded model, not `st.model`. This used to prefer
                # `st.model` -- the request's bare `model` string, which the
                # browser sends on *every* message -- so provider, endpoint and
                # credential came from the pin while the model name came from the
                # header selector: a configuration that exists in no profile.
                # Changing model is a rebind, not a field on a message.
                model=str(recorded.get("model") or ""),
                api_key=api_key,
            )
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001
            # Not swallowed: an unreadable pin is a pin that cannot be honoured,
            # and the previous blanket `return None` turned every one of those
            # into a silent dispatch somewhere else.
            raise GatewayError(
                409,
                "this session's pinned model configuration could not be read; "
                "rebind it to continue",
                "model_revision_unavailable",
            ) from error

    @staticmethod
    def _friendly_error(
        exc: Exception, safe: dict | None = None, *, language: str = "zh"
    ) -> str:
        """The next step to offer, chosen from CONTROLLED signals only.

        This used to classify by substring-matching `str(exc)` and to end with
        `f"**这一轮出错了。** {msg[:300]}"`. Both halves are the leak Plan item
        16 is about, and the tail is the worse one: it reaches a `text_chunk`,
        the persisted assistant message, and `GET /frames/{id}/messages`, so a
        provider error echoing a credential, a `PermissionError` naming an
        absolute path, or a subprocess failure carrying an argv was published
        on three surfaces and then kept forever.

        Branches now read the exception's type and its `status`/`error_code` --
        fields this codebase sets deliberately. The fallback is the projector's
        own sentence, which is author-written (a `GatewayError`) or generic by
        construction, never the exception's text.
        """
        from openai4s.llm.models import LLMError, TransportError

        status = getattr(exc, "status", None)
        code = str(getattr(exc, "error_code", "") or "")
        failure_code = llm_failure_code(exc)
        zh = language == "zh"
        if failure_code == "llm_request_burst":
            if getattr(exc, "output_committed", False):
                return (
                    "**触发了模型服务的突发流量保护。** 这一轮已经产生部分输出，系统为避免重复执行没有自动重试；"
                    "这不是 API Key 配置问题。请稍后在当前会话继续，或临时切换模型。"
                    if zh
                    else "**The model provider's burst-traffic protection was triggered.** "
                    "This turn had already produced output, so it was not retried "
                    "automatically to avoid duplicate execution. This is not an API-key "
                    "configuration problem. Continue this session later or temporarily "
                    "switch models."
                )
            return (
                "**触发了模型服务的突发流量保护。** 系统已自动放慢请求并退避重试；"
                "这不是 API Key 配置问题。若仍未恢复，请稍后在当前会话继续，或临时切换模型。"
                if zh
                else "**The model provider's burst-traffic protection was triggered.** "
                "The request was slowed down and retried automatically; this is not "
                "an API-key configuration problem. If it still does not recover, "
                "continue this session later or temporarily switch models."
            )
        if failure_code == "llm_upstream_overloaded":
            return (
                "**模型服务当前过载。** 系统已自动退避重试；这不是 API Key 配置问题。"
                "请稍后在当前会话继续，或临时切换模型。"
                if zh
                else "**The model provider is currently overloaded.** The request was "
                "retried with backoff; this is not an API-key configuration problem. "
                "Continue this session later or temporarily switch models."
            )
        if status == 401 or code in ("invalid_api_key", "unauthorized"):
            return (
                "**LLM 认证失败（API Key 无效或缺失）。** 请在 Customize → Models "
                "填写有效的 API Key，或在 `.env` 设置 `OPENAI4S_LLM_API_KEY` 后重启。"
                if zh
                else "**LLM authentication failed (the API key is missing or invalid).** "
                "Enter a valid key in Customize → Models, or set "
                "`OPENAI4S_LLM_API_KEY` in `.env` and restart."
            )
        if failure_code == "llm_rate_limited":
            return (
                "**模型服务正在限流。** 系统已自动退避重试；若仍未恢复，请稍后在当前会话继续或更换模型。"
                if zh
                else "**The model provider is rate-limiting requests.** Automatic "
                "backoff retries were attempted; if it still does not recover, "
                "continue this session later or switch models."
            )
        if status == 408:
            return (
                "**LLM 请求超时。** 可能是网络不稳或模型响应慢——请重试;必要时在 "
                "`.env` 调大 `OPENAI4S_LLM_TIMEOUT`。"
                if zh
                else "**The LLM request timed out.** The network may be unstable or "
                "the model may be slow. Try again, or increase "
                "`OPENAI4S_LLM_TIMEOUT` in `.env`."
            )
        if isinstance(exc, TransportError) and status is None:
            # A transport error with no HTTP status never reached the service:
            # it is a connect, DNS, or read failure by construction.
            return (
                "**无法连接到 LLM 服务(或请求中断)。** 请检查网络与 "
                "`OPENAI4S_LLM_BASE_URL`(Customize → Network 可确认联网是否开启)。"
                if zh
                else "**The LLM service could not be reached, or the request was "
                "interrupted.** Check the network and `OPENAI4S_LLM_BASE_URL`; "
                "Customize → Network shows whether network access is enabled."
            )
        if isinstance(exc, LLMError):
            return (
                "**LLM 调用失败。** 请在 Customize → Models 确认模型与 API Key "
                "配置后重试。"
                if zh
                else "**The LLM call failed.** Check the model and API key in "
                "Customize → Models, then try again."
            )
        return ("**这一轮出错了。** " if zh else "**This turn failed.** ") + str(
            (safe or {}).get("error") or INTERNAL_ERROR_MESSAGE
        )

    def _auto_review_enabled(self, root_frame_id: str) -> bool:
        return self.reviews.auto_enabled(root_frame_id)

    def _review_llm_cfg(self, st: SessionState):
        return self.reviews.llm_config(st)

    @staticmethod
    def _review_artifact_excerpt(artifact: dict) -> str | None:
        return ReviewService.artifact_excerpt(artifact)

    def _run_reviewer(
        self,
        st: SessionState,
        emit,
        *,
        user_text: str,
        assistant_text: str,
        artifact_versions_before: dict[str, str | None],
        cell_count_before: int,
        step_count_before: int = 0,
        mode: str = "auto",
    ) -> dict | None:
        return self.reviews.run(
            st,
            emit,
            user_text=user_text,
            assistant_text=assistant_text,
            artifact_versions_before=artifact_versions_before,
            cell_count_before=cell_count_before,
            step_count_before=step_count_before,
            mode=mode,
        )

    def review_call_inflight(self, root_frame_id: str) -> bool:
        return self.reviews.call_inflight(root_frame_id)

    def _summarize_title(self, user_text: str, llm_cfg) -> str | None:
        return self.titles.summarize(user_text, llm_cfg)

    def _spawn_title_summary(
        self, root_frame_id: str, user_text: str, llm_cfg, placeholder: str
    ) -> None:
        self.titles.spawn(root_frame_id, user_text, llm_cfg, placeholder)

    def _build_annotated_content(self, st, text: str, annos: list):
        """Turn an annotation turn into a MULTIMODAL user message: the text
        block plus each pinned figure with a marker drawn at the pin, so a
        vision model SEES exactly what the user pointed at instead of guessing
        from an (x%, y%) coordinate. Falls back to plain text when the active
        provider has no vision support (else chat() would raise)."""
        try:
            from openai4s import llm

            # The exact provider+endpoint+model triple, not the provider. A
            # provider-level answer describes the provider's DEFAULT model, so a
            # session pinned to a text-only model on a vision-capable provider
            # passed this pre-flight and was then refused by chat()'s own
            # _guard_vision -- losing the whole turn instead of falling back to
            # the text the user actually wrote.
            if not llm.supports_vision_for(self._llm_cfg(st)):
                return text
        except Exception:  # noqa: BLE001 — never break a turn over the image
            return text
        parts: list = [{"type": "text", "text": text}]
        by_art: dict = {}
        for a in annos:
            by_art.setdefault(a.get("artifact_id"), []).append(a)
        attached = 0
        total_bytes = 0
        dropped: list[dict] = []
        for art_id, pins in by_art.items():
            name = pins[0].get("artifact_name") or "figure"
            if attached >= MAX_ATTACHED_IMAGES:
                dropped.append(
                    {"name": name, "reason": "too_many", "limit": MAX_ATTACHED_IMAGES}
                )
                continue
            try:
                raw, problem = _pinned_image_bytes(self.store, pins)
                if problem:
                    dropped.append({"name": name, **problem})
                    continue
                data, mime = _figure_with_pins(raw, pins)
                if not data:
                    # PIL absent, or bytes that sniffed as a raster and still
                    # would not decode. Reported rather than skipped: the pin
                    # existed, so its absence has to be accounted for.
                    dropped.append({"name": name, "reason": "decode_failed"})
                    continue
                # Measured after the pin markers are drawn, because that is
                # what actually goes on the wire -- the re-encode can be larger
                # than the file on disk.
                size = len(data)
                if size > MAX_IMAGE_BYTES:
                    dropped.append(
                        {
                            "name": name,
                            "reason": "too_large",
                            "bytes": size,
                            "limit": MAX_IMAGE_BYTES,
                        }
                    )
                    continue
                if total_bytes + size > MAX_TOTAL_IMAGE_BYTES:
                    dropped.append(
                        {
                            "name": name,
                            "reason": "budget_exhausted",
                            "limit": MAX_TOTAL_IMAGE_BYTES,
                        }
                    )
                    continue
                attached += 1
                total_bytes += size
                parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"下面是图像「{name}」，红色圆圈标出了图钉的确切位置"
                            "（圈内数字与上面的标注编号一一对应）。请对照圆圈定位要修改的元素："
                        ),
                    }
                )
                parts.append({"type": "image", "data": data, "mime": mime})
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                dropped.append({"name": name, "reason": "decode_failed"})
        if dropped:
            # Told to the user, and told to the model. The user needs to know
            # their pin was not sent; the model needs to know the picture it is
            # being asked about is missing, rather than answering confidently
            # about an image it never received.
            self.hub.emitter(st.root_frame_id)(
                {
                    "type": "attachment_problems",
                    "frame_id": st.root_frame_id,
                    "problems": dropped[:8],
                }
            )
            # The reason travels with the name. The note used to assert a budget
            # overrun for every case, so a figure the user had deleted, or one
            # overwritten after it was pinned, was reported to the model as "too
            # big" -- a wrong explanation, which is worse than none because the
            # model then relays it to the user.
            names = "、".join(
                f"{item['name']}({item['reason']})" for item in dropped[:8]
            )
            parts.append(
                {
                    "type": "text",
                    "text": (
                        "[System note: the following pinned figures were NOT "
                        f"sent, each with the reason: {names}. Do not describe "
                        "them; say they were not received. `version_changed` "
                        "means the file was overwritten after the user pinned "
                        "it, so the image they annotated no longer exists; ask "
                        "before acting on those pins.]"
                    ),
                }
            )
        return parts if len(parts) > 1 else text

    def bind_model_revision(self, root_frame_id: str) -> dict:
        """Pin this session to the exact model configuration it is about to use.

        D2: a session binds `profile_id + revision`, never "whatever the
        profile says today". A frame used to store a model *string*, which
        answers "which model name" and not "which configuration" -- and those
        differ in the case that matters, because two profiles can name the same
        model against different providers, and editing a profile rewrote it in
        place, so a replayed session reported today's settings rather than the
        ones it ran under.

        Called on the send path only. Reading a session never binds it: an
        unbound legacy session stays fully readable -- history, artifacts,
        Notebook -- and only continuing it asks for a decision.

        Raises `GatewayError(409, ...)` when the session is bound to a revision
        that no longer exists, which is the rebind prompt. Guessing the nearest
        revision would be the silent-follow-latest behaviour being removed.
        """
        frame = self.store.get_frame(root_frame_id) or {}
        bound_id = str(frame.get("model_profile_id") or "")
        bound_revision = frame.get("model_profile_revision")
        profiles = self.store.list_model_profiles()

        if bound_id:
            profile = next(
                (item for item in profiles if item.get("id") == bound_id), None
            )
            recorded = (
                ModelProfileService.revision_config(profile, int(bound_revision or 0))
                if profile is not None
                else None
            )
            usable = profile is not None and recorded is not None
            if usable and not profile.get("deleted_at"):
                # The credential too, not just the revision's existence. Without
                # this a revoked key passed the bind and was only discovered at
                # dispatch, where the old code answered by silently using the
                # active profile instead.
                service = ModelProfileService(
                    self.store, self.cfg, providers=lambda: PROVIDERS
                )
                if not service.resolve_key(profile):
                    usable = False
            elif usable:
                # A tombstoned profile keeps its revisions so history stays
                # readable, but it may not be bound to going forward.
                usable = False
            if not usable:
                raise GatewayError(
                    409,
                    "this session is pinned to a model configuration that no "
                    "longer exists; choose one to continue",
                    "model_revision_unavailable",
                )
            return {
                "model_profile_id": bound_id,
                "model_profile_revision": int(bound_revision or 0),
                "bound": False,
            }

        # A session that already has history is a *legacy* one: it ran under
        # some configuration, and D2 says to recover that rather than to adopt
        # whatever happens to be active now. The only thing a pre-upgrade frame
        # recorded is a model string, so that is what there is to match on.
        recorded = str(frame.get("model") or "").strip()
        if recorded and self.store.message_count(root_frame_id) > 0:
            matches = [
                item
                for item in profiles
                if str(item.get("model") or "").strip() == recorded
            ]
            if len(matches) == 1:
                target = matches[0]
                revision = int(target.get("revision") or 0) or 1
                self.store.update_frame(
                    root_frame_id,
                    model_profile_id=str(target.get("id") or ""),
                    model_profile_revision=revision,
                )
                return {
                    "model_profile_id": str(target.get("id") or ""),
                    "model_profile_revision": revision,
                    "bound": True,
                    "backfilled": True,
                }
            if len(matches) > 1:
                # Two profiles name this model against different providers or
                # endpoints, so "which one did it use" has no answer in the
                # data. Picking either would be a guess presented as a fact,
                # which is the whole failure D2 removes -- so it asks.
                raise GatewayError(
                    409,
                    f"more than one model profile matches {recorded!r}; choose "
                    "which configuration this session continues under",
                    "model_revision_ambiguous",
                )
            # Zero matches, and this is the case that fell through to the active
            # profile below -- which is exactly what the comment above this
            # block forbids. This session ran under a configuration that is not
            # in the profile list any more, so nothing here knows which one it
            # was. Binding it to whatever happens to be active now does not
            # recover the answer; it writes a different one and stamps a
            # revision on it, so the session's own record then claims it ran
            # under a configuration it never used.
            #
            # Unbound is the honest state and an already-supported one: it is
            # what the `active is None` branch below returns, for an install
            # driven entirely by `.env`. The session keeps running on the
            # global configuration and `POST /frames/{id}/model-binding` is
            # there when the user wants to name one.
            return {"model_profile_id": "", "model_profile_revision": 0, "bound": False}

        active_id = str(self.store.get_setting("active_model_profile") or "")
        active = next((item for item in profiles if item.get("id") == active_id), None)
        if active is None:
            # Nothing to bind to. Deliberately not an error: an install driven
            # entirely by .env has no profiles at all, and refusing to run would
            # break a configuration this project documents as supported.
            return {"model_profile_id": "", "model_profile_revision": 0, "bound": False}

        revision = int(active.get("revision") or 0)
        if not revision:
            # A profile written before revisions existed. Seal one now rather
            # than binding to a number that names nothing.
            def _seal(items):
                for item in items:
                    if item.get("id") == active_id:
                        return ModelProfileService._seal_revision(
                            item, now_ms=int(time.time() * 1000)
                        )
                return 0

            revision = int(self.store.mutate_model_profiles(_seal) or 1)
        self.store.update_frame(
            root_frame_id,
            model_profile_id=active_id,
            model_profile_revision=revision,
        )
        return {
            "model_profile_id": active_id,
            "model_profile_revision": revision,
            "bound": True,
        }

    def freeze_model_binding(self, root_frame_id: str) -> dict:
        """Bind if needed and return the exact pair to carry on a ticket.

        `bind_model_revision` already returns this, but going through a named
        method makes the freeze a thing callers ask for rather than a side effect
        they have to remember to read -- which is how the queued case came to have
        the binding written to the frame and nowhere the item could see it.
        """
        binding = self.bind_model_revision(root_frame_id)
        return {
            "model_profile_id": str(binding.get("model_profile_id") or ""),
            "model_profile_revision": int(binding.get("model_profile_revision") or 0),
        }

    def run_message(
        self,
        root_frame_id: str,
        project_id: str,
        user_text: str,
        model: str | None = None,
        plan: bool = False,
        annos: list | None = None,
        explore: bool = False,
        frozen_binding: tuple[str, int] | None = None,
    ) -> dict:
        st = self._state(root_frame_id, project_id)
        if frozen_binding:
            # A queued item runs under what it was admitted with. Re-binding here
            # is what let a follow-up adopt a pin the user changed after 202 was
            # returned; the frame is no longer consulted for this turn.
            st.frozen_model_binding = (
                str(frozen_binding[0]),
                int(frozen_binding[1]),
            )
        else:
            st.frozen_model_binding = None
            # A direct turn: the frame is the freshest answer there is. Raises 409
            # for a dangling pin, before anything runs.
            self.bind_model_revision(root_frame_id)
        if model:
            st.model = model
        st.plan = bool(plan)
        # plan mode wins: a plan turn never executes, so explore is meaningless
        st.explore = bool(explore) and not st.plan
        # Frozen above the `processing` event rather than in the failure
        # handler, because that event is how a *queued* turn announces itself:
        # its 202 resolved while an earlier turn still owned the screen, so the
        # socket is the only place its id can become current.
        # `or new_correlation_id()`: a direct call -- the CLI, a recovery
        # replay, a test -- has no HTTP request behind it, and an empty id on
        # the `processing` and terminal events is a field a client must special
        # case. Under a job this is the contextvar the 202 already read, so the
        # two are the same string by construction.
        turn_request_id = correlation_id() or new_correlation_id()
        emit = self.hub.emitter(root_frame_id)
        with self._session_execution(
            st,
            owner="agent",
            owner_id=f"direct-{uuid.uuid4().hex[:12]}",
            reason="user message",
        ) as execution:
            self._bind_execution_to_turn(getattr(execution, "execution_id", ""))
            self.recovery.touch(st)
            # Tool-only and plan turns need the control plane and provider
            # history, not a scientific worker.  A CodeCell acquires its kernel
            # later through CellExecutionService.prepare_language.
            self._ensure_runtime(st)
            self._seed_messages(st)
            self.store.update_frame(root_frame_id, status="processing")
            emit(
                {
                    "type": "frame_update",
                    "frame_id": root_frame_id,
                    "status": "processing",
                    # The same id the 202 returned. A queued follow-up's 202
                    # resolves while the previous turn still owns the screen,
                    # so this event -- "your turn is running now" -- is the
                    # moment its id becomes the current one.
                    "request_id": turn_request_id,
                    # And which execution it is, so a terminal event arriving
                    # out of order can be told from this turn's own. A client
                    # may reuse `X-Request-Id`; execution ids are minted here.
                    **(
                        {"execution_id": execution.execution_id}
                        if getattr(execution, "execution_id", "")
                        else {}
                    ),
                }
            )
            # first user message names the session. The truncation is set at once
            # as an instant placeholder (and the fallback), then upgraded to a
            # concise LLM-written summary in the background — off the turn's path.
            frame = self.store.get_frame(root_frame_id) or {}
            if not (frame.get("name") or frame.get("task_summary")):
                placeholder = re.sub(r"\s+", " ", user_text).strip()[:80]
                self.store.update_frame(root_frame_id, task_summary=placeholder)
                self._spawn_title_summary(
                    root_frame_id, user_text, self._llm_cfg(st), placeholder
                )
            stored_user_message = self.store.add_message(
                root_frame_id=root_frame_id,
                branch_id=st.branch_id,
                role="user",
                content=user_text,
                frame_id=root_frame_id,
            )
            # This is the exact branch point for an alternative answer: the
            # user message is durable, while no later model action or Cell has
            # touched the workspace yet. Snapshot failure is separately audited
            # and never changes the successful message write above.
            self._capture_cursor_checkpoint_best_effort(
                root_frame_id,
                source_kind="message",
                source_id=stored_user_message["message_id"],
                branch_id=st.branch_id,
            )
            # resolve @filename references → inject the artifact content (M4)
            resolved, message_refs = self._resolve_mentions(st, user_text)
            if message_refs:
                # Stamped after the row exists, not passed at INSERT: resolving
                # can materialise a sibling session's file into this workspace,
                # and the message plus its fork checkpoint above are the branch
                # point that has to be durable before anything writes. Durable
                # here is what makes the chip survive reopen, branch and export
                # -- and what records which version the model actually read,
                # which the `@name#v-id` text cannot say after a copy.
                self.store.update_message_metadata(
                    stored_user_message["message_id"],
                    {"artifact_refs": message_refs},
                )
            remote_ctx = _remote_gpu_runtime_context(user_text)
            if remote_ctx:
                resolved = (
                    resolved + "\n\n[System note: dynamic remote GPU "
                    "configuration context]\n" + remote_ctx
                )
            if st.explore:
                resolved = resolved + "\n\n" + _EXPLORE_PROTOCOL
            # attach the pinned figure(s) with the pin marker drawn on, so a
            # vision model SEES what the user pointed at (not an x%/y% guess)
            content = (
                self._build_annotated_content(st, resolved, annos)
                if annos
                else resolved
            )
            llm_cfg = self._llm_cfg(st)
            catalog_factory = getattr(st.dispatcher, "tool_catalog", None)
            tool_catalog = catalog_factory() if callable(catalog_factory) else None
            tool_resolver = (
                getattr(tool_catalog, "get", None) if tool_catalog is not None else None
            )
            action_ledger = RuntimeActionLedger(
                self.store,
                root_frame_id,
                new_turn_id(),
                provider=getattr(llm_cfg, "provider", None),
                model=getattr(llm_cfg, "model", None),
                branch_id=st.branch_id,
                tool_resolver=(tool_resolver if callable(tool_resolver) else None),
            )
            user_message = {"role": "user", "content": content}
            action_ledger.append_user(user_message)
            st.messages.append(user_message)
            auto_review = self._auto_review_enabled(root_frame_id)
            artifact_versions_before = {
                (a.get("artifact_id") or a.get("id")): a.get("latest_version_id")
                for a in self.store.list_artifacts({"root_frame_id": root_frame_id})
                if (a.get("artifact_id") or a.get("id"))
            }
            cell_count_before = self.store.cell_count(root_frame_id)
            step_count_before = self.store.step_count(root_frame_id)
            emit({"type": "text_reset", "frame_id": root_frame_id})
            assistant_visible: list[dict] = []
            status = "completed"
            err_text: str | None = None
            # What a client needs to act on a failure, captured where the
            # failure actually lands. `run_message` catches its own exceptions
            # and *returns* a failed dict, so `MessageJob.project` -- which is
            # where these three were being filled in -- never runs for any
            # failure a user can reach. Only a fault outside this try reached
            # it, which is to say almost none of them.
            # Frozen at the top of the turn, not inside a handler. The Plan
            # asks every HTTP/WS/job/message response to carry a local request
            # id, and a turn can end `failed` with no exception at all --
            # `max_turns` is the common one -- so deriving the id from an
            # `except` clause would leave the most ordinary failure in the
            # product with nothing to quote on any of its three surfaces.
            # Filled only by the exception path: a code the projector chose,
            # and the retry veto if it read one. The id above is not in here,
            # because it exists whether or not anything was raised.
            failure_meta: dict[str, object] = {}
            loop_reason: str | None = None
            try:
                st.dispatcher.last_output = None
                st.last_engine_completion = None
                st.active_action_ledger = action_ledger
                try:
                    # Keep the historical three-argument composition seam so
                    # tests/extensions that replace ``_loop`` remain valid.
                    loop_reason = self._loop(st, emit, assistant_visible)
                finally:
                    st.active_action_ledger = None
                action_ledger.append_terminal(
                    loop_reason or "unknown",
                    completion=(
                        st.last_engine_completion
                        or getattr(st.dispatcher, "last_output", None)
                    ),
                )
                if loop_reason == "max_turns":
                    status = "failed"
                    # A stable, non-exception code: this failure is a product
                    # outcome, not an error, and it must still be nameable.
                    failure_meta["code"] = "max_turns"
                    err_text = (
                        "Agent reached its configured turn limit without calling "
                        "host.submit_output(...)."
                        if st.explore
                        else (
                            "Agent reached its configured turn limit without a "
                            "structured completion signal (finalize_response or "
                            "host.submit_output(...))."
                        )
                    )
                    emit(
                        {
                            "type": "text_chunk",
                            "frame_id": root_frame_id,
                            "block_type": "text",
                            "chunk": "\n\n" + err_text + "\n",
                        }
                    )
            except Exception as e:  # noqa: BLE001
                status = "failed"
                # Projected ONCE, before anything is shown or stored, and every
                # public field below is built from what it returned. The
                # projector is the only place that decides a code, reads the
                # retry veto, and writes the operator diagnostic -- calling it
                # after composing the prose would mean the prose came from
                # somewhere else, which is exactly how `str(exc)` got onto
                # three surfaces.
                stable_failure_code = llm_failure_code(e)
                safe, _status_code = public_exception(
                    e,
                    surface="web:turn",
                    request_id=correlation_id(),
                    error_code=stable_failure_code,
                )
                err_text = self._friendly_error(
                    e, safe, language=response_language(user_text)
                )
                failure_meta = {
                    "request_id": str(safe.get("request_id") or correlation_id()),
                    "code": str(safe.get("code") or "internal_error"),
                }
                if safe.get("output_committed"):
                    # Only when true. Absent is "no claim"; `False` would
                    # assert a safety nothing here can know.
                    failure_meta["output_committed"] = True
                try:
                    action_ledger.append_terminal(
                        "runtime_error",
                        error={"type": type(e).__name__, "message": err_text},
                    )
                except Exception:  # noqa: BLE001 — preserve the primary failure
                    traceback.print_exc()
                emit(
                    {
                        "type": "text_chunk",
                        "frame_id": root_frame_id,
                        "block_type": "text",
                        "chunk": "\n\n" + err_text + "\n",
                    }
                )
                traceback.print_exc()
            if st.cancel.is_set():
                status = "cancelled"
            if status == "completed" and loop_reason == "submitted":
                current_artifacts = self.store.list_artifacts(
                    {"root_frame_id": root_frame_id}
                )
                produced_artifacts = [
                    artifact
                    for artifact in current_artifacts
                    if artifact_versions_before.get(
                        artifact.get("artifact_id") or artifact.get("id")
                    )
                    != artifact.get("latest_version_id")
                ]
                prior_text = "\n\n".join(
                    str(block.get("text") or "") for block in assistant_visible
                ).strip()
                final_text = completion_message(
                    st.last_engine_completion
                    or getattr(st.dispatcher, "last_output", None),
                    produced_artifacts,
                    previous_text=prior_text,
                    language=response_language(user_text),
                    require_fallback=not bool(st.last_model_prose.strip()),
                )
                if final_text:
                    assistant_visible.append(
                        {"at": int(time.time() * 1000), "text": final_text}
                    )
                    emit(
                        {
                            "type": "text_chunk",
                            "frame_id": root_frame_id,
                            "block_type": "text",
                            "chunk": final_text + "\n",
                        }
                    )
            # Persist each visible prose block with the time it was produced (see
            # _loop) rather than collapsing the whole turn's text into one message
            # stamped at turn-end. The latter sorted every step card into a single
            # pile ahead of the prose on reopen; per-block, back-dated timestamps
            # let the UI interleave text with the steps that ran between blocks —
            # matching the live stream. Written here at the turn boundary (not
            # mid-loop) so an in-flight resume still rebuilds text from the WS
            # replay alone, with nothing double-rendered.
            had_prose = False
            for blk in assistant_visible:
                if not (blk.get("text") or "").strip():
                    continue
                had_prose = True
                self.store.add_message(
                    root_frame_id=root_frame_id,
                    branch_id=st.branch_id,
                    role="assistant",
                    content=blk["text"],
                    frame_id=root_frame_id,
                    created_at=blk.get("at"),
                )
            # A friendly error, a cancel note, or an empty-turn placeholder is not
            # one of the prose blocks — persist it as a trailing assistant message
            # (stamped now, so it lands after the last step) so it survives reload.
            # C2: an error must never be silent on reload.
            # One id on every terminal surface, a code whenever this turn
            # failed for any reason. `output_committed` only ever appears when
            # the projector actually read it -- absent is "no claim".
            turn_identity: dict[str, object] = {
                "request_id": turn_request_id,
                **(
                    {"execution_id": execution.execution_id}
                    if getattr(execution, "execution_id", "")
                    else {}
                ),
            }
            if status == "failed":
                turn_identity["code"] = str(failure_meta.get("code") or "turn_failed")
                if failure_meta.get("output_committed"):
                    turn_identity["output_committed"] = True
            tail = ""
            if status == "failed" and err_text:
                tail = err_text
            elif status == "cancelled" and not had_prose:
                tail = "_已取消。_"
            elif status == "completed" and loop_reason != "submitted" and not had_prose:
                tail = "_(no textual response)_"
            if tail:
                tail_row = self.store.add_message(
                    root_frame_id=root_frame_id,
                    branch_id=st.branch_id,
                    role="assistant",
                    content=tail,
                    frame_id=root_frame_id,
                    # Reopening a session rebuilt the failure from this row's
                    # prose alone, so the support id and the retry veto were
                    # lost the moment the socket event scrolled away -- and a
                    # user who closes a tab after a failure is the likeliest
                    # person to need both. Three scalar fields the projector
                    # already decided are safe to publish; nothing derived from
                    # the exception itself goes in here.
                    metadata=(
                        {"failure": dict(turn_identity)} if status == "failed" else None
                    ),
                )
                if status == "failed":
                    # So the outer handler amends this row instead of adding a
                    # second one. Keyed by request *and* branch: "some failure
                    # already exists" is a different question, and answering it
                    # would swallow a genuinely separate failure on a sibling.
                    self._remember_terminal_failure(
                        turn_request_id,
                        st.branch_id or root_frame_id,
                        tail_row.get("message_id"),
                        turn_identity,
                    )
            if (
                auto_review
                and status == "completed"
                and loop_reason == "submitted"
                and not st.plan
            ):
                assistant_text = "\n\n".join(
                    str(blk.get("text") or "") for blk in assistant_visible
                ).strip()
                self._run_reviewer(
                    st,
                    emit,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    artifact_versions_before=artifact_versions_before,
                    cell_count_before=cell_count_before,
                    step_count_before=step_count_before,
                )
                if st.cancel.is_set():
                    status = "cancelled"
            self.store.update_frame(
                root_frame_id, status=("done" if status == "completed" else status)
            )
            self.executions.mark_finalizing(
                execution,
                reason=(
                    "persisting completion"
                    if status == "completed"
                    else f"persisting {status} result"
                ),
            )
            if status == "failed":
                # While the lease is still held. A turn that fails inside this
                # method returns normally -- the handler above caught the
                # exception and reported it -- so without this the coordinator
                # saw a clean exit and logged `completed` for a turn every
                # other surface calls failed.
                self.executions.mark_failed(execution, reason="the turn failed")
            self.recovery.touch(st)
            response = {
                "status": status,
                "frame_id": root_frame_id,
                "execution_id": execution.execution_id,
                "owner": execution.owner.as_dict(),
                "error": err_text if status == "failed" else None,
                **turn_identity,
            }
        # For direct (non-MessageJob) calls the coordinator completes while the
        # context exits. Keep the historical terminal frame event last; queued
        # MessageJobs still complete their outer ticket immediately afterward.
        emit(
            {
                "type": "frame_update",
                "frame_id": root_frame_id,
                "status": status,
                # The stream is the surface the user is watching, and it is the
                # one that said only "failed".
                **turn_identity,
            }
        )
        return response

    def _resolve_mentions(self, st: SessionState, text: str) -> tuple[str, list[dict]]:
        """Append the content of any @-referenced artifact to the prompt.

        The resolution itself lives in `server/artifact_refs.py`. What used to
        be here read the artifact's *live path*, so the same reference meant
        different bytes once a later cell overwrote the file, and an
        unresolvable name was dropped in silence -- the user asked a question
        about a file the model never received.

        A failed reference is now surfaced to the session rather than swallowed.

        The second return value is the structured record of what was actually
        sent -- one `ArtifactRef` per reference whose bytes reached the prompt.
        It exists because the token in the message text is not enough: it names
        the version the *user* picked, which is not the version the model read
        once a sibling session's file has been copied in.
        """
        sent: list[dict] = []
        resolved, problems = artifact_refs.resolve_message_refs(
            text,
            store=self.store,
            root_frame_id=st.root_frame_id,
            project_id=st.project_id,
            materialise=lambda version_id, name: self._materialise_for_message(
                st, version_id, name
            ),
            on_resolved=sent.append,
        )
        if problems:
            # Emitted, not raised: the turn should still run. A user who
            # referenced four files and mistyped one wants an answer about the
            # other three plus a note, not a refusal.
            self.hub.emitter(st.root_frame_id)(
                {
                    "type": "artifact_ref_problems",
                    "frame_id": st.root_frame_id,
                    "problems": problems[:8],
                }
            )
        return resolved, sent

    def _materialise_for_message(
        self, st: SessionState, version_id: str, name: str
    ) -> dict:
        """Bring a sibling session's version into this one, at send time.

        Through the dispatcher, not through `_data_service` directly. Reaching
        past `HostDispatcher.__call__` for the private attribute did give the
        scope rule and the atomic write one implementation -- which is what the
        old docstring claimed -- but it skipped everything the dispatcher is:
        the permission gate, `log_host_call`, and the step event. So the copy
        was unapproved and unaudited on this path even once the Host RPC path
        was gated, and a `@mention` in model-authored plan text reaches it.
        """
        dispatcher = st.dispatcher
        if dispatcher is None:
            raise RuntimeError("this session cannot materialise artifacts")
        return dispatcher(
            "materialise_artifact", [{"version_id": version_id, "filename": name}]
        )

    def _context_archive_metadata(
        self, st: SessionState, action_ledger: RuntimeActionLedger | None
    ) -> dict[str, Any]:
        """Project durable Web runtime identity into Context Policy V2."""

        group_id = getattr(action_ledger, "current_group_id", None)
        group = (
            self.store.get_action_group(group_id, include_events=False)
            if group_id
            else None
        )
        if group is None:
            groups = self.store.list_action_groups(
                st.root_frame_id,
                branch_id=st.branch_id,
                include_events=False,
            )
            group = groups[-1] if groups else None
        checkpoints = self.store.list_session_checkpoints(
            st.root_frame_id,
            branch_id=st.branch_id,
            limit=1,
        )
        checkpoint = checkpoints[0] if checkpoints else None
        statuses = st.kernels.status()
        generations = {
            language: status.get("generation_id")
            for language, status in statuses.items()
            if status.get("generation_id")
        }
        restarted = any(
            int(status.get("generation_ordinal") or 0) > 0
            for status in statuses.values()
        )
        return {
            "branch_id": st.branch_id,
            "ledger_cursor": (
                {
                    "group_id": group.get("group_id"),
                    "ordinal": group.get("ordinal"),
                    "turn_id": group.get("turn_id"),
                }
                if group
                else None
            ),
            "recovery_pointer": (
                {
                    "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "state_revision": checkpoint.get("state_revision"),
                }
                if checkpoint
                else None
            ),
            "active_kernel_generation": generations or None,
            "kernel_restarted": restarted,
        }

    def _archive_context_output(
        self,
        st: SessionState,
        content: Any,
        message: dict[str, Any],
        archive: dict[str, Any],
    ) -> dict[str, Any]:
        """Store one large context result as a real immutable Artifact version."""

        del message
        digest = str(archive.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("context Artifact requires a SHA-256 digest")
        filename = f"context-output-{digest[:16]}.json"
        existing = self.store.artifact_by_filename(
            filename, st.root_frame_id, strict=True
        )
        if existing and existing.get("latest_version_id"):
            return {
                "artifact_id": existing["artifact_id"],
                "version_id": existing["latest_version_id"],
                "sha256": digest,
            }
        directory = (st.workspace / ".openai4s-context").resolve()
        workspace = st.workspace.resolve()
        if workspace not in directory.parents:
            raise ValueError("context Artifact directory escaped the workspace")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.json"
        payload = json.dumps(
            {
                "schema_version": 1,
                "kind": "context_output",
                "sha256": digest,
                "content": content,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        ).encode("utf-8")
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError("context Artifact digest collision")
        checksum = hashlib.sha256(payload).hexdigest()
        record = self.store.save_artifact(
            path=str(path),
            filename=filename,
            content_type="application/json",
            size_bytes=len(payload),
            checksum=checksum,
            frame_id=st.root_frame_id,
            root_frame_id=st.root_frame_id,
            project_id=st.project_id,
        )
        self.hub.broadcast(
            st.root_frame_id,
            {
                "type": "artifact_created",
                "root_frame_id": st.root_frame_id,
                "artifact": {
                    "id": record["artifact_id"],
                    **record,
                },
            },
        )
        return record

    def _archive_compaction_record(
        self, st: SessionState, payload: dict[str, Any]
    ) -> str:
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        compacted = payload.get("compacted_messages")
        compacted = compacted if isinstance(compacted, list) else []
        artifact_refs = [
            ref
            for message in compacted
            if isinstance(message, dict)
            for ref in (
                message.get("artifact_refs")
                if isinstance(message.get("artifact_refs"), list)
                else []
            )
            if isinstance(ref, dict)
        ]
        return self.store.archive_compaction(
            frame_id=st.root_frame_id,
            project_id=st.project_id,
            branch_id=metadata.get("branch"),
            ledger_cursor=metadata.get("ledger_cursor"),
            recovery_pointer=metadata.get("recovery_pointer"),
            generation_id=metadata.get("active_kernel_generation"),
            metadata=metadata,
            summary=str(payload.get("summary") or ""),
            handoff=str(payload.get("handoff") or ""),
            compacted=compacted,
            context_before=(payload.get("context_estimate_before") or {}),
            context_after=(payload.get("context_estimate_after") or {}),
            artifact_refs=artifact_refs,
        )

    def _loop(
        self,
        st: SessionState,
        emit,
        assistant_visible: list[dict],
        *,
        action_ledger: RuntimeActionLedger | None = None,
        llm_cfg=None,
    ) -> str:
        """Run one Web turn through the shared provider-neutral AgentEngine."""
        action_ledger = action_ledger or getattr(st, "active_action_ledger", None)
        rid = st.root_frame_id
        max_turns = self.cfg.max_turns or 12
        if st.explore:
            max_turns = max(max_turns, self.cfg.explore_max_turns or 0)
        llm_cfg = llm_cfg or self._llm_cfg(st)

        def add_usage(usage: dict) -> None:
            self.store.add_frame_tokens(
                rid,
                input_tokens=usage.get("prompt_tokens", 0) or 0,
                output_tokens=usage.get("completion_tokens", 0) or 0,
            )

        latest_user_text = next(
            (
                message.get("content", "")
                for message in reversed(st.messages)
                if message.get("role") == "user"
            ),
            "",
        )
        events = WebEventSink(
            emit,
            rid,
            assistant_visible,
            add_usage,
            language=response_language(latest_user_text),
            narrate_actions=not st.plan,
            cancelled=st.cancel.is_set,
            action_ledger=action_ledger,
        )

        def apply_pending() -> None:
            if st.pending_env:
                self._apply_pending_env(st, emit)

        def execute_cell(action) -> dict:
            st.active_action_group_id = (
                action_ledger.current_group_id if action_ledger else None
            )
            try:
                # The full outcome (not just ["result"]): the executor needs
                # the "executed" bit to keep refused cells out of the
                # finalize-evidence ledger, and unwraps "result" itself.
                return self._execute_and_log(
                    st,
                    action.code,
                    "agent",
                    emit,
                    stream=True,
                    language=action.language,
                )
            finally:
                st.active_action_group_id = None

        def finalize_plan(reply, prose: str) -> None:
            try:
                self._finalize_plan(st, reply.content, prose, emit)
            except Exception:  # noqa: BLE001 — plan capture must not break a turn
                traceback.print_exc()

        tool_catalog = None
        if not st.plan:
            catalog_factory = getattr(st.dispatcher, "tool_catalog", None)
            if callable(catalog_factory):
                tool_catalog = catalog_factory()
        model_tools = ()
        if not st.plan:
            model_tools = (
                (
                    lambda messages: with_finalize_response(
                        tool_catalog.specs_for(messages)
                    )
                )
                if tool_catalog is not None
                else with_finalize_response(control_tool_specs())
            )

        def _llm_quota_gate() -> None:
            self.enforce_llm_quota(st.root_frame_id)

        engine = AgentEngine(
            ChatModel(
                llm_cfg,
                chat,
                tools=model_tools,
                stream=True,
                # Same signal the engine gets below, so Stop also interrupts a
                # retry backoff rather than only the gap between turns.
                cancellation=EventCancellation(st.cancel),
                quota_gate=_llm_quota_gate,
            ),
            WebActionExecutor(
                dispatcher=lambda: st.dispatcher,
                apply_pending=apply_pending,
                execute_cell=execute_cell,
                events=events,
                prose_nudge=_submit_nudge_for(llm_cfg),
                explore_nudge=_EXPLORE_NUDGE,
                native_wrapper=lambda call, invoke: (
                    self._invoke_control_with_artifacts(st, call, emit, invoke)
                ),
                explore_mode=st.explore,
                plan_mode=st.plan,
                finalize_plan=finalize_plan,
                cancelled=st.cancel.is_set,
                tool_catalog=tool_catalog,
            ),
            context_policy=CompactionPolicy(
                self.cfg,
                metadata_provider=lambda _state: self._context_archive_metadata(
                    st, action_ledger
                ),
                tool_schema_provider=lambda state: (
                    model_tools(state.messages)
                    if callable(model_tools)
                    else model_tools
                ),
                context_budget_provider=lambda _state: (
                    get_model_capabilities(
                        llm_cfg.provider,
                        llm_cfg.model,
                        base_url=llm_cfg.base_url,
                    ).usable_context_tokens
                ),
                artifact_archiver=lambda content, message, archive: (
                    self._archive_context_output(st, content, dict(message), archive)
                ),
                archive_sink=lambda payload: self._archive_compaction_record(
                    st, dict(payload)
                ),
            ),
            event_sink=events,
            cancellation=EventCancellation(st.cancel),
            completion=CompletionSignal(
                lambda: getattr(st.dispatcher, "last_output", None)
            ),
            max_turns=max_turns,
        )
        state = RunState(st.messages, max_turns=max_turns)
        result = engine.run(state)
        st.last_engine_completion = result.completion
        st.last_model_prose = events.model_prose
        self._telemetry_turn(st, result)
        return result.stop_reason

    def _telemetry_turn(self, st: SessionState, result: Any) -> None:
        """Opt-in lifecycle telemetry for a completed turn. A no-op unless the
        user recorded consent; it cannot raise and does not block the turn.

        `session_start` is deduplicated to the first turn of each session in
        this process, so it marks "a session did some work" rather than "a page
        was opened", which is the more honest and the less identifying signal.
        """
        try:
            from openai4s.telemetry.emit import emit, emit_session_start, turn_outcome

            store = self.store
            emit_session_start(st.root_frame_id, store=store, surface="web")
            emit(
                "turn_complete",
                store=store,
                surface="web",
                outcome=turn_outcome(getattr(result, "stop_reason", "")),
                count=1,
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            pass

    def _execute_with_watchdog(
        self,
        st: SessionState,
        code: str,
        origin: str,
        on_chunk,
        language: str = "python",
        lease: KernelLease | None = None,
        cell_id: str | None = None,
        action_group_id: str | None = None,
    ) -> dict:
        """Web adapter for the protocol-neutral exact-lease cell watchdog."""
        lease = lease or st.kernels.lease(language)
        if lease is None:
            raise RuntimeError(f"{language} kernel is not available")
        try:
            from openai4s.permissions import broker as _perm_broker

            permission_broker = _perm_broker()
        except Exception:  # noqa: BLE001
            permission_broker = None

        def permission_pending() -> bool:
            return bool(
                permission_broker and permission_broker.is_pending(st.root_frame_id)
            )

        policy = WatchdogPolicy.from_environment(
            interrupt_grace_s=_WATCHDOG_INTERRUPT_GRACE_S,
            kill_grace_s=_WATCHDOG_KILL_GRACE_S,
        )
        after_restart = (
            (lambda target: self._run_bootstrap(st, target))
            if language == "python"
            else None
        )
        self.recovery.touch(st, language, state="busy")
        self.executions.bind_lease(lease, st.kernels.interrupt_if_current)
        action_context = (
            {
                "action_group_id": action_group_id,
                "action_id": f"{action_group_id}:action",
                "tool_call_id": None,
            }
            if action_group_id
            else None
        )

        def run_cell(kernel):
            binder = getattr(kernel, "bind_action_context", None)
            if callable(binder):
                with binder(action_context):
                    return kernel.execute(
                        code,
                        origin=origin,
                        on_chunk=on_chunk,
                        cell_id=cell_id,
                    )
            return kernel.execute(
                code,
                origin=origin,
                on_chunk=on_chunk,
                cell_id=cell_id,
            )

        try:
            result = execute_with_watchdog(
                st.kernels,
                lease,
                run_cell,
                policy=policy,
                cancelled=st.cancel.is_set,
                paused=permission_pending,
                after_restart=after_restart,
                thread_name=f"os-cell-{st.root_frame_id}",
            )
            if language == "python":
                self.sidecar_manifests.record_result(st.kernels, lease, result)
            return result
        finally:
            self.executions.unbind_lease(lease)
            # A watchdog may have replaced the captured lease. Touch whichever
            # exact generation is current rather than mutating a stale record.
            self.recovery.touch(st, language, state="active")

    def _safety_refusal(self, st: Any, code: str, origin: str) -> str | None:
        """Pre-exec safety verdict for an agent cell (reports e6w and diO).

        Returns an error-observation string if the cell is refused, else None.
        Only `agent`-origin cells are screened; user/system cells pass through.
        Fails open (None) on any error -- a broken gate must not break a turn.

        Two screens, and only the first used to run here. `OPENAI4S_BIOSECURITY`
        is documented as doing two things -- "splice the calibrated-
        accountability prompt AND run the diO trajectory screener" -- and is on
        by default, but on the Web daemon only the prompt half happened. The
        CLI ran both. So the same cell that `uv run openai4s run` refused was
        executed by `./start.sh`, which is the surface people actually use: the
        model got a prompt asking it to behave, and nothing checked whether it
        had.

        The screener judges a *trajectory*, not a cell, which is why the port
        had to widen to pass the session. `gather_trajectory` lives in
        `openai4s.security` beside the screener that consumes it, so both
        surfaces share one definition -- two copies of "what counts as the
        trajectory" would be two safety policies wearing one name.
        """
        if origin != "agent":
            return None
        try:
            security = self.cfg.security
            if security.code_gate_enabled:
                from openai4s.security import classify_code

                verdict = classify_code(code, self.cfg)
                if verdict is not None and not verdict.safe:
                    return verdict.as_observation()
        except Exception:  # noqa: BLE001 - the gate must never break a turn
            pass

        try:
            if not self.cfg.security.biosecurity:
                return None
            from openai4s.security import gather_trajectory, screen_trajectory

            messages = list(getattr(st, "messages", ()) or ())
            user_text, actions = gather_trajectory(messages, code)
            screen = screen_trajectory(user_text, actions, self.cfg)
        except Exception:  # noqa: BLE001
            return None
        # Only BLOCK stops a cell. ESCALATE stays advisory here for the same
        # reason it is advisory in the CLI loop: there is no human in the
        # execution path to escalate to, and turning it into a refusal would
        # deadlock the turn rather than get anyone consulted.
        if screen is not None and screen.blocked:
            return (
                "[BLOCKED by the biosecurity trajectory screener] "
                f"{screen.reason}. This cell was NOT executed. If this is "
                "legitimate research, stop and explain the scientific context "
                "and safeguards to the user rather than proceeding."
            )
        return None

    def _capture_cursor_checkpoint_best_effort(
        self,
        root_frame_id: str,
        *,
        source_kind: str,
        source_id: str,
        branch_id: str | None = None,
    ) -> dict | None:
        """Never turn snapshot infrastructure failure into source failure."""

        try:
            return self.session_domain.capture_cursor_checkpoint(
                root_frame_id,
                source_kind=source_kind,
                source_id=source_id,
                branch_id=(
                    branch_id or self.store.active_session_branch(root_frame_id)
                ),
            )
        except Exception:  # noqa: BLE001 - Cell/message persistence already won
            return None

    def _record_cell_with_cursor_checkpoint(self, **record: Any) -> str:
        cell_id = self.store.log_cell(**record)
        root_frame_id = record.get("root_frame_id")
        if root_frame_id and record.get("origin") in {"agent", "user"}:
            # Team-mode metering (M2-5): the kernel's getrusage delta for a
            # live cell, attributed to the session owner. Origin-filtered
            # here, so a session-package import replay is never billed. With
            # no ownership row this reads and never writes (INV-1).
            try:
                usage = (record.get("result") or {}).get("usage") or {}
                cpu_s = usage.get("cpu_s")
                if cpu_s:
                    owner = self.store.team.session_owner(str(root_frame_id))
                    if owner is not None:
                        self.store.governance.record_usage(
                            user_id=owner["user_id"],
                            kind="kernel_cpu_s",
                            amount=float(cpu_s),
                            project_id=owner["project_id"],
                            ref=str(root_frame_id),
                        )
            except Exception:  # noqa: BLE001 — metering must not break a cell
                pass
            state = self._existing_state(str(root_frame_id))
            self._capture_cursor_checkpoint_best_effort(
                str(root_frame_id),
                source_kind="cell",
                source_id=cell_id,
                branch_id=(
                    state.branch_id
                    if state is not None
                    else self.store.active_session_branch(str(root_frame_id))
                ),
            )
        return cell_id

    def _allocate_cell_attempt(
        self,
        st: SessionState,
        request: CellRequest,
        cell_id: str,
        action_group_id: str | None,
    ) -> str:
        """Allocate durable Cell identity before any runtime work begins."""
        group_id = action_group_id
        if group_id is None:
            # User REPL and compatibility callers do not pass through an
            # AgentEngine ActionRouted event.  Keep their execution attempts in
            # the same append-only ledger without projecting them into model
            # history on resume.
            group = self.store.append_action_group(
                root_frame_id=st.root_frame_id,
                branch_id=st.branch_id,
                turn_id=f"cell-{cell_id}",
                kind="execution",
            )
            group_id = group["group_id"]
            self.store.append_action_event(
                group_id=group_id,
                type="proposed",
                action_id=f"{group_id}:action",
                canonical_arguments={
                    "language": request.language,
                    "code": request.code,
                    "origin": request.origin,
                },
                resource_keys=[f"kernel:{request.language}"],
            )
        status = st.kernels.status(request.language)
        attempt = self.store.allocate_execution_attempt(
            group_id=group_id,
            producing_cell_id=cell_id,
            state_revision=st.cell_index,
            generation_id=(
                status.get("generation_id") if status.get("alive") else None
            ),
            owner_instance_id=self._owner_instance_id,
        )
        return attempt["attempt_id"]

    def _bind_cell_attempt_generation(
        self, attempt_id: str, st: SessionState, language: str
    ) -> None:
        generation_id = st.kernels.status(language).get("generation_id")
        if not generation_id:
            raise RuntimeError(
                f"{language} execution attempt has no live kernel generation"
            )
        self.store.bind_execution_attempt_generation(attempt_id, generation_id)

    def _execute_and_log(
        self,
        st: SessionState,
        code: str,
        origin: str,
        emit,
        stream: bool = True,
        language: str = "python",
        action_group_id: str | None = None,
    ) -> dict:
        """Compatibility façade over the typed cell execution service."""
        request = CellRequest(
            code=code,
            origin=origin,
            language=language,
            stream=stream,
            action_group_id=(
                action_group_id or getattr(st, "active_action_group_id", None)
            ),
        )
        executed = self.cells.execute(
            st,
            request,
            emit,
            action_group_id=(
                action_group_id or getattr(st, "active_action_group_id", None)
            ),
        )
        return {
            "result": executed.result,
            "idx": executed.cell_index,
            "cell_id": executed.cell_id,
            "state_revision": executed.state_revision,
            "generation_id": executed.generation_id,
            "figures": executed.capture.figures,
            "files_written": executed.capture.files_written,
            "saved": executed.capture.artifacts,
            # Whether a kernel really ran the cell (False for safety-refused /
            # runtime-unavailable soft errors, whose result dict is identical
            # to a real failure).  The agent executor's evidence ledger keys
            # off this.
            "executed": executed.executed,
        }

    def _emit_artifact_step(
        self, st: SessionState, title: str, saved: list[dict], emit
    ) -> None:
        """Persist + stream a completed artifact-kind step for the files a cell
        produced. Mirrors the host.save_artifact step shape (kind='artifact',
        input={files, environment}, output={artifacts:[…]}) so the same step
        renderer and the reopen reconstruction both show a "Saving …" card."""
        rid = st.root_frame_id
        files = [a["filename"] for a in saved]
        label = (
            title
            if title and not title.startswith("Running analysis")
            else (
                "Saving " + (files[0] if len(files) == 1 else f"{len(files)} artifacts")
            )
        )
        step_input = {"files": files, "environment": self._kernel_id(st)}
        step_output = {"artifacts": saved}
        summary = f"{len(saved)} artifact" + ("" if len(saved) == 1 else "s")
        sid = "s-" + uuid.uuid4().hex[:12]
        try:
            self.store.add_step(
                step_id=sid,
                frame_id=rid,
                kind="artifact",
                title=label,
                input=step_input,
                status="done",
            )
            self.store.update_step(sid, output=step_output)
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass
        # Emit begin+end back-to-back: the step is already complete, but sending
        # both keeps the live renderer's create→patch path identical to host steps.
        emit(
            {
                "type": "step",
                "frame_id": rid,
                "step_id": sid,
                "kind": "artifact",
                "title": label,
                "input": step_input,
                "status": "running",
            }
        )
        emit(
            {
                "type": "step_update",
                "frame_id": rid,
                "step_id": sid,
                "status": "done",
                "output": step_output,
                "summary": summary,
            }
        )

    # -- structured plan: capture / persist / approve / revise / discard ----
    def _finalize_plan(self, st: SessionState, reply: str, prose: str, emit) -> None:
        self.plans.finalize(st, reply, prose, emit)

    def _write_plan_artifact(
        self, st: SessionState, plan: dict, artifact_id: str | None, emit
    ) -> dict | None:
        return self.plans.write_artifact(st, plan, artifact_id, emit)

    def _emit_plan_ready(self, emit, rid: str, plan: dict | None) -> None:
        self.plans.emit_ready(emit, rid, plan)

    def get_plan_state(self, root_frame_id: str) -> dict:
        return self.plans.get_state(root_frame_id)

    def discard_plan(self, root_frame_id: str) -> dict:
        return self.plans.discard(root_frame_id)

    def _plan_exec_seed(self, plan: dict) -> str:
        return self.plans.execution_seed(plan)

    def run_plan_execution(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> dict:
        return self.plans.run_execution(
            root_frame_id, project_id, model, claimed_plan_id=claimed_plan_id
        )

    def run_plan_revision(
        self,
        root_frame_id: str,
        project_id: str,
        changes: str,
        model: str | None = None,
    ) -> dict:
        return self.plans.run_revision(root_frame_id, project_id, changes, model)

    def submit_plan_approval(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> "MessageJob":
        return self._spawn_job(
            root_frame_id,
            lambda: self.run_plan_execution(
                root_frame_id, project_id, model, claimed_plan_id=claimed_plan_id
            ),
            project_id=project_id,
            reason="plan approval",
            claimed_plan_id=claimed_plan_id,
            # What the route claimed *from*, which is where a never-started
            # worker has to put it back. `cancelled_plan_status` is the
            # cancellation terminal and is a different question: rolling an
            # approved plan back to `paused` would strand it, because approve
            # swaps against `draft`.
            claimed_from_status="draft",
            # Approving a draft and then cancelling leaves work to finish, so
            # the plan is paused rather than back to `draft`: the steps it has
            # already run are real, and resume is the operation that continues
            # them. `run_execution` draws the same line once the turn started.
            cancelled_plan_status="paused",
        )

    def claim_plan_approval(self, root_frame_id: str) -> dict:
        """Compare-and-swap the draft into `executing` for exactly one caller."""
        return self.plans.claim_approval(root_frame_id)

    def claim_plan_resume(self, root_frame_id: str) -> dict:
        """Compare-and-swap the plan into `executing` for exactly one caller."""
        return self.plans.claim_resume(root_frame_id)

    def run_plan_resume(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> dict:
        return self.plans.resume_execution(
            root_frame_id, project_id, model, claimed_plan_id=claimed_plan_id
        )

    def submit_plan_resume(
        self,
        root_frame_id: str,
        project_id: str,
        model: str | None = None,
        *,
        claimed_plan_id: str | None = None,
    ) -> "MessageJob":
        return self._spawn_job(
            root_frame_id,
            lambda: self.run_plan_resume(
                root_frame_id, project_id, model, claimed_plan_id=claimed_plan_id
            ),
            project_id=project_id,
            reason="plan resume",
            claimed_plan_id=claimed_plan_id,
            claimed_from_status="paused",
            cancelled_plan_status="paused",
        )

    def submit_plan_revision(
        self,
        root_frame_id: str,
        project_id: str,
        changes: str,
        model: str | None = None,
    ) -> "MessageJob":
        return self._spawn_job(
            root_frame_id,
            lambda: self.run_plan_revision(root_frame_id, project_id, changes, model),
            project_id=project_id,
            reason="plan revision",
            # Revising claims nothing: the row stays a draft throughout, and a
            # failed revision leaves the draft the user already had.
        )

    def _settle_claimed_plan(
        self, root_frame_id: str, plan_id: str | None, status: str
    ) -> None:
        """Move a row the route claimed out of `executing`, or leave it alone.

        The approve and resume routes compare-and-swap the plan into
        `executing` before answering 202, because a status read taken inside
        the background thread cannot decide who owns the execution. That is
        right, and it hands the background thread an obligation: the row it was
        given has to reach a settled status no matter how the turn ends.

        A failure before the turn reached `run_message` -- the Store refusing
        the re-read, `emit_ready` throwing, the seed builder raising -- met no
        settle point at all, and the row stayed `executing` with nothing
        running. That state is unrecoverable rather than merely wrong: approve
        swaps against `draft` and resume against `paused`, so both lose
        forever, and `get_by_frame` prefers the newest non-discarded plan, so
        the stuck row also shadows every draft the session makes afterwards.
        One failed turn took planning away from the session permanently.

        Compare-and-swap, not a write, and only ever from `executing`: by the
        time this runs the plan path may have settled the row itself, and
        overwriting a `completed` with `failed` would be this function causing
        the damage it exists to prevent.
        """
        if not plan_id:
            return
        try:
            moved = self.store.compare_and_set_plan_status(
                plan_id, expected="executing", new_status=status
            )
        except Exception:  # noqa: BLE001 - the original failure is the news
            traceback.print_exc()
            return
        if not moved:
            return
        self._best_effort(
            "plan_ready",
            lambda: self.plans.emit_ready(
                self.hub.emitter(root_frame_id),
                root_frame_id,
                self.store.get_plan(plan_id),
            ),
        )

    @staticmethod
    def _best_effort(step: str, action) -> None:
        """Run one terminal-failure side effect, and never let it stop the next.

        These were a single `try`, so the first one to fail cancelled the rest:
        an `OperationalError` from `update_frame` skipped the terminal
        `frame_update` entirely, and the client -- which had been told 202 and
        was watching the socket -- was left with a turn that never ended. The
        frame's stored status, the prose, and the terminal event are three
        independent obligations to three different readers.
        """
        try:
            action()
        except Exception:  # noqa: BLE001 - the original failure is the news
            traceback.print_exc()

    def _terminal_failure_event(self, root_frame_id: str, job: "MessageJob") -> dict:
        """The one terminal `frame_update` a failed turn owes the socket."""
        return {
            "type": "frame_update",
            "frame_id": root_frame_id,
            "status": "failed",
            # The same local id the submit 202 and the job query carry.
            "request_id": job.request_id,
            # Which *execution* this is the end of. A request id is not enough
            # to tell a stale terminal from a current one: a client may reuse
            # `X-Request-Id`, and the ordering that produced this bug --
            # processing(A), processing(B), failed(A) -- then looks like B's
            # own terminal event and closes B's turn.
            **({"execution_id": job.execution_id} if job.execution_id else {}),
            "code": job.error_code or "internal_error",
            # Only when true: absent means "no claim", and a false would
            # assert a safety this cannot know.
            **({"output_committed": True} if job.output_committed else {}),
        }

    def _remember_terminal_failure(
        self,
        request_id: str,
        branch_id: str,
        message_id: str | None,
        identity: dict,
    ) -> None:
        """Note which row is this TURN's authoritative terminal failure."""
        job_id = getattr(self._turn_scope, "job_id", "")
        if not job_id or not request_id or not message_id:
            return
        with self._lock:
            self._terminal_failures[job_id] = {
                "message_id": message_id,
                "identity": dict(identity),
                "request_id": request_id,
                "branch_id": branch_id,
            }

    def _take_terminal_failure(self, job_id: str, request_id: str) -> dict | None:
        """The note this job filed, if it is for this request.

        The job id is already unique, and the note froze its own branch, so
        there is nothing left to re-derive. Matching on a branch resolved
        *during* the failure was worse than useless: a failed lookup fell back
        to the root frame, which is a different key from the one the note was
        filed under, so the correct hand-off was dropped and a duplicate row
        written -- the exact defect this exists to prevent.
        """
        with self._lock:
            note = self._terminal_failures.get(job_id)
            if not note:
                return None
            if note.get("request_id") != request_id:
                # A different request is a different failure, not this one
                # seen twice. Left in place: it belongs to this job either way
                # and the outermost `finally` will clear it.
                return None
            return self._terminal_failures.pop(job_id)

    def _bind_execution_to_turn(self, execution_id: str) -> None:
        """Give this thread's ticket the execution the turn actually got.

        The message spawner already knows it -- it holds the coordinator ticket
        -- so this is a no-op there. The plan spawner does not, and its job
        carried a synthetic id until this line. The two are never both in
        flight: the synthetic one is only ever emitted by a failure that
        happened before the turn reached an execution at all.
        """
        job_id = getattr(self._turn_scope, "job_id", "")
        if not job_id or not execution_id:
            return
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            job.execution_id = execution_id

    def _enter_turn_scope(self, job_id: str) -> None:
        """Bind this thread's turn to `job_id`."""
        self._turn_scope.job_id = job_id

    def _exit_turn_scope(self, job_id: str) -> None:
        """Drop the binding and whatever note this turn left behind.

        Called from the outermost `finally` of the job target -- after the
        outer handler has had its chance to consume the note, after the socket
        event, after `job.finish`. Anywhere earlier and the hand-off is taken
        away from the handler it was filed for; anywhere later and there is no
        `anywhere later`.
        """
        self._turn_scope.job_id = ""
        with self._lock:
            self._terminal_failures.pop(job_id, None)

    def _persist_outer_failure(
        self, root_frame_id: str, job: "MessageJob", message: str
    ) -> None:
        """Store the tail of a failure that never reached `run_message`.

        The outer catches are real paths -- a fault before the turn is entered,
        or after it returns, and for the plan spawner anything its `fn` raises
        outside `run_message`. They were given the live surfaces (the socket
        event and the job result) and nothing durable, so the identity survived
        exactly as long as the tab did: `GET /frames/{id}/messages` had no row
        to project and a reopened session showed a failure with no support id
        and no retry veto.

        `job.project` has already run the projector once -- it is what produced
        `message`, `error_code` and `output_committed` -- so nothing here calls
        it again. A second call would write a second operator diagnostic for
        one failure, which is how two records of the same event drift apart.
        """
        try:
            self._persist_outer_failure_inner(root_frame_id, job, message)
        except Exception:  # noqa: BLE001
            # NOTHING here may escape. This runs inside the outer handler, on
            # the job thread, and an exception leaving it kills that thread
            # before `job.finish` -- so the socket stays silent, `wait_result`
            # blocks forever, and a poll never terminates. The original failure
            # is frequently the Store being unavailable, which is exactly when
            # the branch lookup and the insert below are most likely to fail
            # too, so this is the expected case rather than the exotic one.
            traceback.print_exc()

    def _persist_outer_failure_inner(
        self, root_frame_id: str, job: "MessageJob", message: str
    ) -> None:
        prior = self._take_terminal_failure(job.job_id, job.request_id)
        # Frozen at submit, or carried on the note. Either way this path asks
        # the Store nothing to decide where the row goes.
        branch_id = (prior or {}).get("branch_id") or job.branch_id or root_frame_id
        if prior:
            # The inner handler already wrote this request's terminal row. Two
            # exceptions, one thing that happened to the user -- so this amends
            # rather than appends, and the veto is OR-ed: an ordinary tail
            # failure must not un-say that a tool had already run.
            merged = dict(prior["identity"])
            if job.output_committed:
                merged["output_committed"] = True
            job.output_committed = bool(merged.get("output_committed"))
            job.error_code = str(merged.get("code") or job.error_code)
            self.store.update_message_metadata(prior["message_id"], {"failure": merged})
            return
        identity: dict[str, object] = {
            "request_id": job.request_id,
            "code": job.error_code or "internal_error",
        }
        if job.output_committed:
            # Only when true; absent is "no claim".
            identity["output_committed"] = True
        # Unguarded on purpose: `_persist_outer_failure` holds the single
        # catch for this whole operation. Nested try/excepts here made that
        # outer one unreachable, which reads as defence and is decoration --
        # no test can tell whether it is still there.
        self.store.add_message(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            role="assistant",
            content=message,
            frame_id=root_frame_id,
            metadata={"failure": identity},
        )

    #: What a caller waiting on a job whose worker never started is told.
    #: Fixed, like the ticket's reason: the original exception reaches the
    #: submitter by being re-raised, and a waiter gets a sentence rather than
    #: a rendering of something that may refuse to render.
    UNSTARTED_WORKER_MESSAGE = "the worker for this turn could not be started"

    def _abort_unstarted_job(
        self, job, ticket, error, *, claimed_plan_id=None, rollback_status=None
    ) -> None:
        """Undo everything a submission did once its worker refused to start.

        Order matters. The ticket goes back first, because it is what holds the
        session and blocks every later turn; then the job, because `is_running`
        answers from `_jobs` and would otherwise report a running turn for a
        session with nothing in it; then the plan row, back to the status its
        own route can claim again.

        Each step is independently guarded: a failure to undo one must not stop
        the others, or a thread-start failure becomes a session that is wedged
        in a *different* way.
        """
        for step, action in (
            ("ticket", lambda: self.executions.abort_unstarted(ticket, error)),
            # Terminalised, not merely forgotten. `wait_result()` blocks on
            # `job.done`, which only `finish` sets -- so popping the job left
            # any caller already waiting (the `wait:true` branch of the message
            # route is exactly that) blocked forever on an event nobody would
            # set, for a job the registry had already discarded.
            ("wake", lambda: job.finish(error=self.UNSTARTED_WORKER_MESSAGE)),
            ("job", lambda: self._jobs.pop(job.job_id, None)),
            (
                "plan",
                lambda: (
                    self._settle_claimed_plan(
                        job.root_frame_id, claimed_plan_id, rollback_status
                    )
                    if claimed_plan_id and rollback_status
                    else None
                ),
            ),
        ):
            try:
                action()
            except Exception:  # noqa: BLE001 — every undo runs, whatever failed
                print(
                    f"openai4s: could not undo {step} for an unstarted worker",
                    file=sys.stderr,
                )
                traceback.print_exc()

    def _spawn_job(
        self,
        root_frame_id: str,
        fn,
        *,
        project_id: str = "default",
        reason: str = "plan turn",
        claimed_plan_id: str | None = None,
        cancelled_plan_status: str = "paused",
        claimed_from_status: str | None = None,
    ) -> "MessageJob":
        """Run `fn` in a background daemon thread as a tracked MessageJob (shared
        machinery behind submit_message / plan approve / plan revise).

        ``claimed_plan_id`` is the row the *route* already compare-and-swapped
        into `executing` before answering 202. Handing it to the spawner is
        what turns that claim into something the background thread can settle:
        see `_settle_claimed_plan`.
        """
        st = self._state(root_frame_id, project_id)
        job = MessageJob(f"job-{uuid.uuid4().hex[:12]}", root_frame_id)
        # A real ticket, taken here rather than deep inside `run_message`.
        # This spawner used to mint `plan-<job id>` and hand it to the client on
        # the 202, which is not an execution at all: the FIFO had never heard of
        # it, so a plan turn was not queued behind the running one, did not hold
        # the session while it wrote its own outcome, and named a different id
        # on the 202 than the socket carried a moment later. `run_message`
        # reuses whatever `executions.current` finds, so taking the ticket at
        # submit gives the whole turn -- seed, agent loop, plan row, terminal
        # event -- one identity and one lease.
        ticket = self._queue_execution(
            st,
            owner="agent",
            owner_id=job.job_id,
            reason=reason,
        )
        job.execution_id = ticket.execution_id
        job.execution_owner = ticket.owner.as_dict()
        # Resolved by the ticket, on the submitting thread, while the Store is
        # known to work. The helper used to fall back to the root frame when
        # this was missing, which writes the failure onto the wrong branch
        # whenever the active one is a sibling -- a user on a fork would see
        # nothing and the trunk would grow a failure that never happened there.
        job.branch_id = ticket.branch_id or st.branch_id or ""
        with self._lock:
            done = [
                jid
                for jid, j in self._jobs.items()
                if j.done.is_set() and (time.time() - (j.finished_at or 0)) > 300
            ]
            for jid in done:
                self._jobs.pop(jid, None)
            self._jobs[job.job_id] = job

        def _target() -> None:
            self._enter_turn_scope(job.job_id)
            token = set_correlation_id(job.request_id)
            #: Filled inside the lease, published after it -- the same split
            #: `submit_message` makes, and for the same reason: `job.done` and
            #: an active ticket must never disagree, because `is_running`
            #: reads both.
            outcome: dict = {}
            try:
                # The lease covers `fn` *and* everything the failure path owes.
                # A plan turn writes its outcome after `run_message` returns --
                # the plan row's final status, a `plan_ready`, and on failure
                # the frame's status and the terminal event -- and holding no
                # lease meant all of it landed while the next queued turn was
                # already `processing`.
                with self.executions.admitted(ticket, cancel_event=st.cancel):
                    try:
                        result = fn() or {}
                        result.setdefault("job_id", job.job_id)
                        result.setdefault("execution_id", ticket.execution_id)
                        result.setdefault("owner", ticket.owner.as_dict())
                        outcome["result"] = result
                    except ExecutionCancelled as e:
                        # Cancelling is not failing. Without this clause a
                        # cancelled plan turn fell into the catch-all below and
                        # wrote `status="failed"` onto the frame -- so a user
                        # who pressed stop was shown an error, and the session
                        # carried a failure it never had. `submit_message` has
                        # always distinguished the two; the plan approve/revise
                        # path shares this spawner and did not.
                        self._settle_claimed_plan(
                            root_frame_id, claimed_plan_id, cancelled_plan_status
                        )
                        outcome["result"] = {
                            "status": "cancelled",
                            "frame_id": root_frame_id,
                            "job_id": job.job_id,
                            "execution_id": ticket.execution_id,
                            "owner": ticket.owner.as_dict(),
                            "reason": str(e),
                        }
                        outcome["handled"] = e
                        raise
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc()
                        message = job.project(e, "web:plan")
                        self._persist_outer_failure(root_frame_id, job, message)
                        emit = self.hub.emitter(root_frame_id)
                        self._settle_claimed_plan(
                            root_frame_id, claimed_plan_id, "failed"
                        )
                        self._best_effort(
                            "frame_status",
                            lambda: self.store.update_frame(
                                root_frame_id, status="failed"
                            ),
                        )
                        # Separately guarded, for the same reason as the message
                        # turn: a Store that cannot record the status must not
                        # also cost the client its terminal event.
                        self._best_effort(
                            "terminal",
                            lambda: emit(
                                self._terminal_failure_event(root_frame_id, job)
                            ),
                        )
                        outcome["error"] = message
                        # Re-raised after the side effects so the coordinator
                        # marks this ticket FAILED. Swallowing it left the lease
                        # exiting cleanly and the execution log reading
                        # queued -> running -> completed for a failed turn.
                        outcome["handled"] = e
                        raise
            except ExecutionCancelled as e:
                if outcome.get("handled") is not e:
                    # Raised BY `admitted`, not by `fn`: the item was cancelled
                    # while it was still queued -- a session Stop drains the
                    # FIFO -- so the turn never ran and the inner handler never
                    # saw it. The route had already claimed the row, though, so
                    # without this the plan is stranded `executing` by the one
                    # action a user takes expecting nothing to be left behind.
                    self._settle_claimed_plan(
                        root_frame_id, claimed_plan_id, cancelled_plan_status
                    )
                    outcome["result"] = {
                        "status": "cancelled",
                        "frame_id": root_frame_id,
                        "job_id": job.job_id,
                        "execution_id": ticket.execution_id,
                        "owner": ticket.owner.as_dict(),
                        "reason": str(e),
                    }
            except Exception as e:  # noqa: BLE001
                if outcome.get("handled") is not e:
                    # Not ours: the lease itself refused, or something outside
                    # the inner handler failed. Project it once, here -- and
                    # settle the claim, which is the case the row would
                    # otherwise be stranded `executing` by.
                    outcome["error"] = job.project(e, "web:plan")
                    self._settle_claimed_plan(root_frame_id, claimed_plan_id, "failed")
            finally:
                if not job.done.is_set():
                    # After the lease, so `job.done` and the ticket agree.
                    if "result" in outcome:
                        job.finish(result=outcome["result"])
                    else:
                        job.finish(error=outcome.get("error") or INTERNAL_ERROR_MESSAGE)
                self._exit_turn_scope(job.job_id)
                reset_correlation_id(token)

        # Constructed *and* started inside the guard. `Thread(...)` allocates,
        # so it can fail too, and a failure there leaves exactly the same
        # wreckage as a failure in `start()`.
        try:
            t = threading.Thread(
                target=carry_context(_target),
                name=f"openai4s-plan-{root_frame_id}",
                daemon=True,
            )
            job.thread = t
            t.start()
        except BaseException as error:
            self._abort_unstarted_job(
                job,
                ticket,
                error,
                claimed_plan_id=claimed_plan_id,
                rollback_status=claimed_from_status,
            )
            # Re-raised, never swallowed into a 202: "accepted, it is running"
            # is the one answer a caller cannot recover from here, because it
            # will wait for a terminal event nobody will emit.
            raise
        return job

    def run_repl(
        self,
        root_frame_id: str,
        project_id: str,
        code: str,
        language: str = "python",
        execution_id: str | None = None,
    ) -> dict:
        """Execute code directly in the session kernel (notebook REPL, no LLM)."""
        st = self._state(root_frame_id, project_id)
        emit = self.hub.emitter(root_frame_id)
        execution_id = str(execution_id or f"repl-{uuid.uuid4().hex}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", execution_id):
            raise ValueError("execution_id must be a portable identifier")
        with self._session_execution(
            st,
            owner="user_repl",
            owner_id=execution_id,
            execution_id=execution_id,
            language=language,
            reason="user notebook cell",
        ) as execution:
            # CellExecutionService allocates the durable attempt before its
            # prepare_language hook lazily starts Python.
            #
            # A REPL cell still needs the structured Notebook lifecycle while
            # it is running.  Keep chat-only compatibility events out of the
            # conversation: direct Notebook execution is not an Agent turn.
            def emit_notebook(event: dict) -> None:
                if event.get("type") not in {"text_chunk", "step", "step_update"}:
                    emit(event)

            info = self._execute_and_log(
                st,
                code,
                "user",
                emit_notebook,
                stream=True,
                language=language,
            )
            r = info["result"]
            # ``_execute_and_log`` predates the typed Cell result.  Its legacy
            # compatibility shape exposed ``idx`` but not the newer durable
            # revision/generation fields.  Keep direct callers and test
            # adapters working while preserving the exact values supplied by
            # CellExecutionService on the normal path.  The execution lease is
            # still held here, so reading the current slot cannot race a
            # lifecycle writer.
            state_revision = (
                info["state_revision"] if "state_revision" in info else info["idx"]
            )
            generation_id = (
                info["generation_id"]
                if "generation_id" in info
                else st.kernels.status(language).get("generation_id")
            )
            self.executions.mark_finalizing(
                execution, reason="persisting notebook cell"
            )
            emit(
                {"type": "frame_update", "frame_id": root_frame_id, "status": "success"}
            )
            return {
                "status": (
                    "cancelled" if execution.cancellation.is_set() else "completed"
                ),
                "execution_id": execution.execution_id,
                "owner": execution.owner.as_dict(),
                "cell": {
                    "cell_index": info["idx"],
                    "state_revision": state_revision,
                    "generation_id": generation_id,
                    "kernel_id": (
                        self._r_kernel_id(st)
                        if language == "r"
                        else self._kernel_id(st)
                    ),
                    "language": language,
                    "source": code,
                    "stdout": r.get("stdout") or "",
                    "stderr": r.get("stderr") or "",
                    "status": (
                        "interrupted"
                        if r.get("interrupted")
                        else ("error" if r.get("error") else "ok")
                    ),
                    "error": r.get("error"),
                    "figures": info["figures"],
                    "files_written": info["files_written"],
                    "files_read": [],
                },
            }

    def submit_repl(
        self,
        root_frame_id: str,
        project_id: str,
        code: str,
        *,
        language: str = "python",
        execution_id: str | None = None,
    ) -> MessageJob:
        """Queue one Notebook Cell and return its durable execution identity."""

        st = self._state(root_frame_id, project_id)
        execution_id = str(execution_id or f"repl-{uuid.uuid4().hex}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", execution_id):
            raise ValueError("execution_id must be a portable identifier")
        job = MessageJob(f"job-{uuid.uuid4().hex[:12]}", root_frame_id)
        ticket = self._queue_execution(
            st,
            owner="user_repl",
            owner_id=execution_id,
            execution_id=execution_id,
            language=language,
            reason="user notebook cell",
        )
        job.execution_id = ticket.execution_id
        job.execution_owner = ticket.owner.as_dict()
        with self._lock:
            expired = [
                job_id
                for job_id, previous in self._jobs.items()
                if previous.done.is_set()
                and (time.time() - (previous.finished_at or 0)) > 300
            ]
            for job_id in expired:
                self._jobs.pop(job_id, None)
            self._jobs[job.job_id] = job

        def target() -> None:
            try:
                with self.executions.admitted(ticket, cancel_event=st.cancel):
                    result = self.run_repl(
                        root_frame_id,
                        project_id,
                        code,
                        language=language,
                        execution_id=execution_id,
                    )
                result.setdefault("job_id", job.job_id)
                job.finish(result=result)
            except ExecutionCancelled as error:
                job.finish(
                    result={
                        "status": "cancelled",
                        "frame_id": root_frame_id,
                        "job_id": job.job_id,
                        "execution_id": ticket.execution_id,
                        "owner": ticket.owner.as_dict(),
                        "reason": str(error),
                    }
                )
            except Exception as error:  # noqa: BLE001 - job owns its failure
                traceback.print_exc()
                # A *kernel* error is not this path: a traceback from the
                # user's own cell arrives as a normal result and is the whole
                # point of a REPL. This clause only fires when the machinery
                # around the cell threw, which is machinery detail.
                job.finish(error=job.project(error, "web:repl"))

        try:
            thread = threading.Thread(
                target=carry_context(target),
                name=f"openai4s-repl-{root_frame_id}",
                daemon=True,
            )
            job.thread = thread
            thread.start()
        except BaseException as error:
            self._abort_unstarted_job(job, ticket, error)
            raise
        return job


# --------------------------------------------------------------------------- #
#  Customize-panel payloads (agents / compute / environment / network / memory)
# --------------------------------------------------------------------------- #
# Built-in agent roster surfaced in Customize → Agents. These describe the
# Code-as-Action harness the way opencode describes its build/plan/explore/
# general agents: a primary scientist plus specialised sub-agents you can
# host.delegate() to.
_BUILTIN_AGENTS = [
    {
        "name": "SCIENTIST",
        "mode": "primary",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": True,
        "unrestricted": True,
        "description": "Primary research agent. Writes Python that calls the full "
        "host.* toolset (bash, web_search/web_fetch, file + grep/glob "
        "tools, delegate, skills) and produces publication-grade "
        "figures, tables and reports.",
    },
    {
        "name": "EXPLORE",
        "mode": "subagent",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": False,
        "unrestricted": False,
        "description": "Read-only scout. Searches the literature and your files "
        "(web_search, web_fetch, grep, glob, read_file) and returns a "
        "concise map — no writes.",
    },
    {
        "name": "GENERAL",
        "mode": "subagent",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": False,
        "unrestricted": True,
        "description": "General-purpose sub-agent for a self-contained sub-task; "
        "runs the full toolset and returns a structured result via "
        "host.delegate(...).",
    },
    {
        "name": "REMOTE_GPU_PROVISIONER",
        "mode": "subagent",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": False,
        "unrestricted": True,
        "description": "Remote GPU setup specialist. When an SSH GPU host exists "
        "but fold / ESM mutation scoring / ProteinMPNN services are "
        "not provisioned, it inspects the host, installs or locates "
        "real wrappers, verifies them, and registers capabilities.",
    },
    {
        "name": "PLAN",
        "mode": "primary",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": True,
        "unrestricted": False,
        "description": "Planning agent (Plan mode). Investigates and proposes a "
        "step-by-step plan without executing changes.",
    },
    {
        "name": "REVIEWER",
        "mode": "subagent",
        "healthy": True,
        "source": "bundled",
        "supportsPlanMode": False,
        "unrestricted": False,
        "description": "Evidence-grounded reviewer. Checks a completed answer, "
        "execution trace, and produced artifacts for unsupported claims, missing "
        "deliverables, provenance gaps, and reproducibility risks without writing "
        "files or calling tools.",
    },
]

# Connectors directory: ready-to-add MCP servers. The bundled "example" always
# works (pure-stdlib, no deps); the npx-based official servers work when Node is
# installed. Users can also add any custom stdio MCP server by command.
_CONNECTOR_DIRECTORY = [
    {
        "id": "example",
        "name": "Example (bundled)",
        "description": "A local demo MCP server (echo / now / calc / random_int) — "
        "always available, no install needed.",
        "command": [sys.executable, "-m", "openai4s.mcp_servers.example_server"],
        "always": True,
    },
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Read/list files under a root dir (official MCP server; needs Node).",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "description": "Fetch a URL and return its content (official; needs Node).",
        "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
    },
    {
        "id": "time",
        "name": "Time",
        "description": "Time / timezone tools (official; needs Node).",
        "command": ["npx", "-y", "@modelcontextprotocol/server-time"],
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Structured step-by-step reasoning tool (official; needs Node).",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
    },
]


# Network egress groups shown in Customize → Network (the domains agent tools may
# reach). This is the SAME canonical allowlist that openai4s.egress ENFORCES
# when OPENAI4S_EGRESS=allowlist — one source of truth for both
# the display here and the fence in webtools/host.bash. The on/off master switch
# for networking is OPENAI4S_ALLOW_NETWORK; the allowlist-vs-off egress mode is
# OPENAI4S_EGRESS (default off → fail-open, unchanged behaviour).
from openai4s.egress import EGRESS_GROUPS as _NETWORK_GROUPS


def _memory_enabled(store) -> bool:
    return store.get_setting("memory_enabled", "0") == "1"


def _memory_scope(store, raw: Any) -> str:
    """Where a memory write lands, named by the caller and never guessed.

    The default used to be the literal string ``"default"``. Nothing on this
    installation creates a project by that name -- every Web session belongs to
    a real ``proj_*`` -- and injection reads *the session's* project. So a save
    from the Memory pane went to a scope no session has ever read: the pane
    listed it, the toggle said Enabled, and not one turn ever saw it. Refusing
    an unnamed scope is the only version of this that cannot come back, because
    a default is exactly what was wrong.
    """
    scope = str(raw or "").strip()
    if not scope:
        raise GatewayError(
            400,
            f"memory writes require project_id: {MEMORY_GLOBAL_SCOPE!r} for "
            "every project, or one project id",
            "memory_scope_required",
        )
    if scope == MEMORY_ALL_PROJECTS:
        raise GatewayError(
            400,
            f"{MEMORY_ALL_PROJECTS!r} is a read-only view; write to "
            f"{MEMORY_GLOBAL_SCOPE!r} or to one project",
            "memory_scope_invalid",
        )
    if scope != MEMORY_GLOBAL_SCOPE and store.get_project(scope) is None:
        # A memory addressed to a project that does not exist is the original
        # defect with a different spelling: accepted, stored, never read.
        raise GatewayError(400, f"unknown project {scope!r}", "memory_scope_unknown")
    return scope


# --- user skill authoring helpers ------------------------------------------
def _skill_slug(name: str) -> str:
    return SkillCustomizationService.slug(name)


def _parse_skill_md(content: str) -> tuple[dict, str]:
    return SkillCustomizationService.parse_document(content)


def _write_user_skill(
    loader, name: str, description: str, body: str, existing: bool = False
) -> dict:
    return SkillCustomizationService(loader).create_or_update(
        name,
        description,
        body,
        existing=existing,
    )


def _read_user_skill(loader, name: str) -> dict:
    return SkillCustomizationService(loader).get(name)


def _delete_user_skill(loader, name: str) -> dict:
    return SkillCustomizationService(loader).delete(name)


def _detect_gpu() -> dict:
    """Best-effort local GPU probe (nvidia-smi). CPU-only hosts report unavailable."""
    import shutil as _sh
    import subprocess as _sp

    if _sh.which("nvidia-smi"):
        try:
            out = _sp.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if out.returncode == 0 and out.stdout.strip():
                first = out.stdout.strip().splitlines()[0].split(",")
                return {
                    "available": True,
                    "gpu_name": first[0].strip(),
                    "gpu_count": len(out.stdout.strip().splitlines()),
                    "cuda_version": (first[2].strip() if len(first) > 2 else None),
                    # Present in both branches on purpose. A response whose *key
                    # set* depends on the host is not a contract: with `note`
                    # only on CPU-only hosts, the frozen shape said "guarantees
                    # note" on the machine that captured it and every GPU host
                    # then failed the gate with four breaking changes that had
                    # nothing to do with the API. The two host-valued fields
                    # (`gpu_name`, `cuda_version`) are recorded as machine state
                    # instead; this one stays a plain string in both.
                    "note": "GPU detected via nvidia-smi.",
                }
        except Exception:  # noqa: BLE001
            pass
    return {
        "available": False,
        "gpu_name": None,
        "gpu_count": 0,
        "cuda_version": None,
        "note": "CPU-only host — GPU-only models run as labelled CPU proxies.",
    }


_REMOTE_COMPUTE_CACHE: dict = {}


def _ssh_config_aliases() -> list[str]:
    """Concrete Host aliases from ~/.ssh/config (skips wildcard patterns) — the
    candidates a user can pick as a remote GPU."""
    out: list[str] = []
    p = Path.home() / ".ssh" / "config"
    try:
        for line in p.read_text("utf-8", "replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            k, _, v = s.partition(" ")
            if k.lower() == "host":
                for tok in v.split():
                    if tok and "*" not in tok and "?" not in tok and tok not in out:
                        out.append(tok)
    except OSError:
        pass
    return out


def _probe_remote_gpu(alias: str) -> dict:
    """Best-effort ssh nvidia-smi probe → {reachable, gpus, gpu_count}."""
    import subprocess as _sp

    try:
        out = _sp.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "BatchMode=yes",
                alias,
                "nvidia-smi --query-gpu=name --format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if lines:
            return {
                "reachable": True,
                "gpu_count": len(lines),
                "gpus": f"{len(lines)}× {lines[0]}",
            }
    except Exception:  # noqa: BLE001
        pass
    return {"reachable": False, "gpu_count": 0, "gpus": None}


def _remote_compute_info() -> dict:
    """Registry-backed view of configured remote GPU hosts + their provisioned
    capabilities (the persistent 'memory'), for Settings → Remote GPU.
    Reachability is probed per host and cached ~60s."""
    from openai4s.compute import registry as _reg

    hosts_reg = _reg.list_hosts()
    now = time.time()
    hosts = []
    for alias, h in hosts_reg.items():
        cached = _REMOTE_COMPUTE_CACHE.get(alias)
        if cached and (now - cached.get("_ts", 0) < 60):
            probe = cached
        else:
            probe = _probe_remote_gpu(alias)
            probe["_ts"] = now
            _REMOTE_COMPUTE_CACHE[alias] = probe
        caps = h.get("capabilities") or {}
        hosts.append(
            {
                "alias": alias,
                "label": h.get("label") or alias,
                "provider": f"ssh:{alias}",
                "gpus": probe.get("gpus") or h.get("gpus"),
                "gpu_count": probe.get("gpu_count") or h.get("gpu_count", 0),
                "reachable": probe.get("reachable", False),
                "capabilities": [
                    {
                        "name": c,
                        "engine": (m or {}).get("engine"),
                        "verified": bool((m or {}).get("verified_at")),
                    }
                    for c, m in caps.items()
                ],
            }
        )
    return {
        "configured": bool(hosts),
        "hosts": hosts,
        "default_host": _reg.default_host(),
        "available_aliases": _ssh_config_aliases(),
    }


def _host_info() -> dict:
    import platform as _pf

    info = {
        "python": _pf.python_version(),
        "platform": _pf.platform(),
        "machine": _pf.machine(),
        "cpu_count": os.cpu_count(),
    }
    try:  # memory (best-effort, no hard dep)
        import shutil as _sh

        info["disk_free_gb"] = round(_sh.disk_usage("/").free / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        info["ram_gb"] = round(page * pages / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        pass
    return info


def _environment_snapshot() -> dict:
    """This **daemon process's** interpreter, version, platform and package set.

    Read the scope literally. It used to be documented as "the kernel's compute
    environment" and used as artifact provenance, on the reasoning that a
    kernel is spawned with ``sys.executable`` and shares this interpreter's
    site-packages. That stopped being true once a cell could run in a selected
    conda environment or in R, and the result was artifacts stamped with a
    Python package list that had never been theirs.

    Artifact provenance now comes from the kernel generation instead -- see
    ``ArtifactManager.capture_environment``. What remains here serves the two
    REST reads that genuinely ask about the daemon: the environment probe and
    the workbench's runtime panel."""
    import platform as _pf

    from openai4s.kernel import preinstall

    packages = preinstall.full_freeze()
    return {
        "kind": "python",
        "python_version": _pf.python_version(),
        "implementation": _pf.python_implementation(),
        "platform": _pf.platform(),
        "package_count": len(packages),
        "packages": packages,
    }


# --------------------------------------------------------------------------- #
#  HTTP + WS request handler
# --------------------------------------------------------------------------- #
#: What each `POST /frames/<id>/decision` refusal means on the wire.
#:
#: They are not one status: a malformed body, a decision that belongs to another
#: session, one already being resolved, and one whose approval was written but
#: whose continuation failed are four different things, and a client that
#: retries them all the same way is wrong about three.
_DECISION_REFUSAL_STATUS = {
    "decision_id_required": 400,
    # Not 403. A decision for another frame and one that never existed answer
    # identically, or the refusal is an existence oracle.
    "decision_not_found": 404,
    "decision_in_flight": 409,
    "decision_already_resolved": 409,
    "decision_immutable": 409,
    "decision_expired": 410,
    # The approval is recorded. `output_committed` on the body is what stops the
    # UI offering a retry that would submit it twice.
    "decision_continuation_failed": 500,
}


def make_handler(cfg: Config, hub: WSHub, runner: SessionRunner):
    store = get_store(cfg.db_path)
    model_discovery = LocalModelDiscoveryService()
    execution_views = ExecutionViewService(
        store=store,
        format_timestamp=lambda value: _iso(value),
    )
    runner_domain = getattr(runner, "session_domain", None)
    timeline = getattr(runner_domain, "timeline", None) or ActionTimelineService(store)
    global_views = GlobalResearchViewService(store, timeline)
    skill_customization = SkillCustomizationService(SkillLoader(cfg=cfg))
    _disabled_skills = skill_customization.disabled_names
    # Seeded from the store first. It used to read `cfg.llm.model` alone --
    # the *process* config, whose `__post_init__` fills a concrete provider
    # default when the field is blank. So a daemon whose model was configured
    # through the UI (the documented path) came back after a restart offering
    # only the stored model in `GET /models` while reporting a
    # `default_model_id` that appeared in none of them; app.js assigns that id
    # to `S.defaultModel`, no option matches, and the next message posts a
    # model the user never chose to the provider they did.
    _default_model = {
        "id": (store.get_setting("llm_model") or "").strip()
        or cfg.llm.model
        or "default"
    }
    model_profiles = ModelProfileService(
        store,
        cfg,
        providers=provider_specs,
    )

    def _disconnect_managed_datapro_session() -> None:
        """Invalidate DataPro only for this live Store generation."""

        from openai4s.mcp_client import manager as _mcp_manager

        _mcp_manager().disconnect(
            datapro.CONNECTOR_ID,
            cache_scope=datapro.runtime_cache_scope(store),
        )

    def _disconnect_datapro_if_credential_changed(previous: str) -> None:
        """Drop a cached session only when its effective credential moved."""

        if datapro.resolve_agent_plan_key(store) != previous:
            _disconnect_managed_datapro_session()

    def _disconnect_datapro_if_auth_context_changed(
        previous_credential: str, previous_provider: str
    ) -> None:
        """Drop a session when either credential resolution input moved."""

        provider = (
            str(store.get_setting("llm_provider") or cfg.llm.provider or "")
            .strip()
            .lower()
        )
        if (
            provider != previous_provider
            or datapro.resolve_agent_plan_key(store) != previous_credential
        ):
            _disconnect_managed_datapro_session()

    def _save_shared_agent_plan_key(value: Any) -> None:
        """Save the one Ark Agent Plan credential used by managed products.

        DataPro and Doubao Search intentionally share this boundary.  Keeping
        the active Ark profile update here means either UI password field has
        the same zero-friction semantics, without ever returning the secret.
        """

        previous = datapro.resolve_agent_plan_key(store)
        datapro.save_agent_plan_key(store, value)

        # Keep an active Ark profile coherent with the live key.  Otherwise a
        # credential saved through a managed-product field would work only
        # until that same model profile was reactivated.
        # Gated on the endpoint, not just the protocol name: overwriting the
        # profile key destroys the previous one through `_forget_key`, so an
        # ark-protocol profile pointed at another vendor's endpoint would lose
        # its credential irrecoverably and then send a DataPro key there.
        #
        # The gate reads the *profile being rotated*, not the global settings.
        # A profile carries its own provider and base_url, and the active one
        # can disagree with the settings row (it is edited independently, and
        # the settings are only refreshed on activation) -- so checking the
        # settings would authorise destroying a credential belonging to an
        # endpoint nobody verified.
        active_id = str(store.get_setting("active_model_profile") or "").strip()
        profile = next(
            (
                item
                for item in store.list_model_profiles()
                if item.get("id") == active_id and not item.get("deleted_at")
            ),
            None,
        )
        if (
            active_id
            and profile is not None
            and str(profile.get("provider") or "").strip().lower() == "ark"
            and datapro.is_volcengine_endpoint(str(profile.get("base_url") or ""))
        ):
            try:
                model_profiles.edit(active_id, {"api_key": value})
            except ModelProfileError:
                # The live brokered Ark key was already updated.  A stale or
                # tombstoned profile id must not turn that save into failure.
                pass

        # A Streamable HTTP MCP session may be bound to the old account/key.
        # Either managed-product password field rotates the same credential,
        # so invalidate this Store's session when the effective key changed.
        _disconnect_datapro_if_credential_changed(previous)

    def _project_skill_customization(project_id: str) -> SkillCustomizationService:
        project_id = str(project_id or "").strip()
        if not project_id or store.get_project(project_id) is None:
            raise GatewayError(404, "project not found")
        return SkillCustomizationService(
            SkillLoader(cfg=cfg, project_id=project_id),
            scope="project",
            project_id=project_id,
        )

    def _datapro_config_payload() -> dict[str, Any]:
        connector = store.get_connector(datapro.CONNECTOR_ID)
        return {
            **datapro.credential_state(store),
            "connector_id": datapro.CONNECTOR_ID,
            "connector_enabled": bool(connector and connector.get("enabled")),
            "skill_name": datapro.SKILL_NAME,
            "skill_enabled": datapro.SKILL_NAME not in _disabled_skills,
        }

    def _doubao_search_config_payload() -> dict[str, Any]:
        # Lazy by design: the direct stdlib client is an owning service, while
        # this compatibility facade only projects its public configuration.
        from openai4s.doubao_search import DoubaoSearchService

        service = DoubaoSearchService(store)
        return {
            **datapro.credential_state(store),
            "key_configured": service.configured(),
            "provider": "doubao-search",
            "primary": True,
        }

    def _skill_history_payload(
        service: SkillCustomizationService,
        name: str,
        *,
        limit: int,
    ) -> dict:
        history = service.history(name, limit=max(1, min(int(limit), 200)))
        if history.get("error"):
            return history
        return {**history, "status": service.status(name)}

    def _require_canonical_session_root(frame_id: str) -> dict:
        """The frame a pin or an admission may be written against.

        Both routes took the id on trust. `store.get_frame(fid) or {}` meant a
        frame that does not exist fell through to project `default` and reached
        the reservation, so a request naming nothing wrote a real admission
        ledger row -- an orphan, because `submit_message` refuses afterwards
        and the refusal path only releases. A child frame did the same and was
        worse: the row named the child as its root, and the deletion cascade
        walks canonical roots, so deleting the session left it behind.

        Checked before anything is written rather than after, because the write
        is what has to not happen.
        """
        frame = store.get_frame(frame_id)
        if not frame:
            raise GatewayError(404, "session not found")
        canonical = str(frame.get("root_frame_id") or frame.get("frame_id") or "")
        if canonical != frame_id:
            raise GatewayError(
                404,
                "comments belong to a Session, not to one of its sub-frames",
                "not_a_session_root",
            )
        return frame

    def _require_session_writable(root_frame_id: str, operation: str) -> None:
        """Keep old lightweight test adapters compatible without weakening quarantine."""

        guard = getattr(runner, "require_session_writable", None)
        if callable(guard):
            guard(root_frame_id, operation)
            return
        if store.get_setting(session_import_quarantine_key(root_frame_id)):
            raise GatewayError(
                423,
                "imported Session is quarantined and view-only; use the "
                "confirmed restart_fresh recovery action before " + operation,
            )

    from openai4s.jobs import JobManager

    _jobs_mgr = JobManager(cfg.data_dir / "compute-jobs")
    # M2: the daemon exposes unauthenticated code-exec endpoints (kernel/execute,
    # compute/jobs, host.bash). On loopback that's fine (single-user local tool);
    # if bound to a non-loopback address (or OPENAI4S_REQUIRE_TOKEN=1) we gate
    # every request behind a one-time token (first `?token=` sets a cookie).
    import secrets as _secrets

    _loopback = cfg.host in ("127.0.0.1", "localhost", "::1")
    # Required by default (decision D1). It used to be opt-in on loopback, on
    # the reasoning that a single-user local tool needs no gate -- but the
    # daemon exposes unauthenticated code execution (kernel/execute,
    # compute/jobs, host.bash), and "local" includes every other process on the
    # machine and every web page the user visits. The Host and Origin guards
    # cover the browser; they do not cover a local process.
    #
    # `OPENAI4S_REQUIRE_TOKEN=0` is the escape hatch, and it lives until
    # `LEGACY_TOKEN_OPT_OUT_REMOVED_IN` above -- a version rather than "one
    # minor release", because the second is not a date anything can check. It
    # is the same variable that used to opt *in*, with its sense reversed: a
    # script setting it to 1 keeps working and simply asks for what is now the
    # default.
    #
    # It is honoured on loopback only. A non-loopback bind is reachable by
    # anything that can route to it, and there is no configuration under which
    # that should answer without a credential.
    _legacy_opt_out = os.environ.get("OPENAI4S_REQUIRE_TOKEN", "").strip().casefold()
    _needs_token = (not _loopback) or _legacy_opt_out not in ("0", "false", "no")
    # Persisted, not per-boot. A token minted into a closure changed on every
    # restart, which invalidated every cookie already issued -- tolerable for a
    # gate that is off by default, not for one that is on. It also has to be
    # readable by the CLI, which must present a credential once the gate is
    # required and cannot import the web server to find out what it is.
    _auth_token = local_auth.load_or_mint(cfg.data_dir) if _needs_token else None
    # stderr and flushed, like every other startup notice here. On plain
    # `print` this went to stdout, which is block-buffered whenever it is not a
    # TTY -- so under nohup, systemd, Docker or any redirect to a log file, the
    # one line a user needs in order to open their own daemon sat in a buffer
    # and did not appear. It showed up in a terminal, which is exactly why it
    # survived: the configuration that hides it is the one nobody develops in.
    if _auth_token:
        # Rendered, not echoed. A wildcard bind names interfaces rather than an
        # address, so `http://0.0.0.0:8760/` is a URL nothing dials -- and a
        # container has no other way to be reachable, which makes the one line
        # an operator needs the one line that was wrong for them.
        _reachable = "localhost" if cfg.host in ("0.0.0.0", "::", "") else cfg.host
        print(
            f"[openai4s] access token required.\n"
            f"  open: http://{_reachable}:{cfg.port}/?token={_auth_token}",
            file=sys.stderr,
            flush=True,
        )
    elif _loopback:
        print(
            "[openai4s] WARNING: OPENAI4S_REQUIRE_TOKEN=0 — this daemon answers "
            "without a credential, and it can execute code. Any other process "
            "on this machine can drive it. This opt-out is removed in the next "
            "minor release.",
            file=sys.stderr,
            flush=True,
        )
    # honour persisted network toggle on boot
    if store.get_setting("network_enabled") == "0":
        os.environ["OPENAI4S_ALLOW_NETWORK"] = "0"

    # Team mode (docs/team-server-plan.md M1): when ON, the team guard below
    # replaces the single-credential token gate — every request resolves to a
    # user (login cookie) or the loopback-CLI service identity before routing.
    # When OFF, `_team_auth` is None and nothing in this block runs (INV-1).
    from openai4s.server.team_auth import SERVICE_IDENTITY as _TEAM_SERVICE_IDENTITY
    from openai4s.server.team_auth import TEAM_COOKIE as _TEAM_COOKIE
    from openai4s.server.team_auth import TeamAuthService as _TeamAuthService

    _team_auth = _TeamAuthService(store) if cfg.team_mode else None
    from openai4s.config import data_root_policies as _data_root_policies
    from openai4s.server.file_area import FileArea as _FileArea

    # The team file area (M1-8). Dormant with no roots; independent of
    # team_mode so a single-user install can also mount data directories.
    _file_area = _FileArea(
        list(cfg.data_roots),
        # D8's read-only datasets area rides on the same env value as the
        # paths (`path=ro`); the plain list keeps its old shape for INV-1.
        policies=_data_root_policies(),
    )
    #: Reachable without a login in team mode. The login page and the login
    #: POST are the recovery path; /auth/status and /health answer with mode
    #: strings only; /static assets are the login page's css/js (the app
    #: source is public anyway, and every data route stays behind the guard).
    _TEAM_EXEMPT_PATHS = frozenset(
        {
            "/health",
            "/login",
            _API_ROOT + "/auth/status",
            _API_ROOT + "/auth/login",
            # An invite holder has no account yet; the invite token is the
            # credential, checked inside the route (M2-4).
            _API_ROOT + "/auth/redeem-invite",
        }
    )

    # DNS-rebinding defense (CWE-346 / CWE-350): the Origin==Host guard in
    # _route() stops classic cross-origin CSRF, but DNS rebinding defeats it —
    # an attacker points evil.test at 127.0.0.1, so the browser sends
    # Origin==Host==evil.test (equal → that check passes) while the write still
    # lands on this loopback daemon (→ unauthenticated RCE via /compute/jobs and
    # the other exec endpoints). Pin the Host header to an address we actually
    # bind and reject the rest before routing.
    _bind_is_wildcard = cfg.host in ("0.0.0.0", "::", "")
    _allowed_hostnames = {"127.0.0.1", "localhost", "::1"}
    if not _bind_is_wildcard:
        _allowed_hostnames.add(cfg.host.strip().strip("[]").lower())
    _allowed_port = int(cfg.port)

    class Handler(BaseHTTPRequestHandler):
        server_version = "openai4s-gateway/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        # ---- io helpers -------------------------------------------------
        def _send(
            self, code: int, body: bytes, ctype: str, extra: dict | None = None
        ) -> None:
            self._last_status = code
            self.send_response(code)
            self.send_header("Content-Type", _sanitize_header_value(ctype))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            # Echoed so a user reporting a failure can hand over an id that ties
            # their request to this daemon's log line for it.
            request_id = getattr(self, "_correlation_id", "")
            if request_id:
                self.send_header("X-Request-Id", _sanitize_header_value(request_id))
            # Applied here rather than at the HTML route so no response can be
            # added later that quietly opts out.
            for k, v in security_headers(WEBUI_DIR / "index.html").items():
                self.send_header(k, v)
            for k, v in (extra or {}).items():
                self.send_header(k, _sanitize_header_value(v))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _is_authenticated(self) -> bool:
            """Whether this request carried a valid credential.

            Shared with the gate so `/auth/status` cannot drift from what the
            gate actually accepts -- a status route that answers from its own
            reasoning is how the old hardcoded "none" survived.
            """
            if not _auth_token:
                return True
            from http.cookies import SimpleCookie

            jar = SimpleCookie(self.headers.get("Cookie", "") or "")
            cookie = jar.get("os_token")
            if local_auth.matches(
                cookie.value if cookie is not None else None, _auth_token
            ):
                return True
            return local_auth.matches(_presented_token(self.headers), _auth_token)

        # ---- team guard (active only when OPENAI4S_TEAM_MODE is on) -----
        def _peer_is_loopback(self) -> bool:
            try:
                return str(self.client_address[0]) in ("127.0.0.1", "::1")
            except Exception:
                return False

        def _team_identity_from_request(self):
            """Resolve this request's identity, or None.

            A login cookie wins; the daemon access token presented in a
            header *from loopback* is the CLI's admin-equivalent service
            path (M1-4: the Bearer channel stays open for the server-side
            management CLI, and only there — over the network the token is
            a shared machine secret, not a person).
            """
            from http.cookies import SimpleCookie

            jar = SimpleCookie(self.headers.get("Cookie", "") or "")
            morsel = jar.get(_TEAM_COOKIE)
            if morsel is not None and _team_auth is not None:
                identity = _team_auth.resolve(morsel.value)
                if identity is not None:
                    return identity
            if (
                _auth_token
                and local_auth.matches(_presented_token(self.headers), _auth_token)
                and self._peer_is_loopback()
            ):
                return _TEAM_SERVICE_IDENTITY
            return None

        def _team_visibility_filter(self) -> str | None:
            """The user_id session enumeration must be filtered by, or None
            when this caller sees everything (team mode off, admin, CLI)."""
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None or identity.is_admin:
                return None
            return identity.user_id

        def _team_owner_user_id(self) -> str | None:
            """Who a session created by this request belongs to (team mode)."""
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None:
                return None
            return identity.user_id

        def _team_identity_dict(self) -> dict | None:
            identity = getattr(self, "_team_identity", None)
            if identity is None:
                return None
            return {
                "id": identity.user_id,
                "role": identity.role,
                "kind": identity.kind,
            }

        def _team_claim_imported(self, imported: dict) -> None:
            """An imported session belongs to whoever imported it (M1-6)."""
            owner = self._team_owner_user_id()
            root = (imported or {}).get("root_frame_id")
            if owner and root:
                store.team.set_session_owner(
                    str(root), owner, project_id=imported.get("project_id")
                )

        def _team_audit_admin_private_read(self, root: str) -> None:
            """D4/INV-12: an admin viewing a private session they do not own
            leaves an audit row — every view, not the first."""
            identity = getattr(self, "_team_identity", None)
            if identity is None:
                return
            try:
                owner = store.team.session_owner(root)
                if (
                    owner is not None
                    and owner["visibility"] == "private"
                    and owner["user_id"] != identity.user_id
                ):
                    store.team.audit(
                        actor=identity.username,
                        action="admin_read_private",
                        user_id=owner["user_id"],
                        project_id=owner["project_id"],
                        target=root,
                    )
            except Exception:  # noqa: BLE001 — auditing must not break the read
                pass

        def _team_root_of_artifact_meta(self, meta: dict | None) -> str | None:
            """The session root a resolved artifact/version row belongs to.

            Three shapes reach here, and only the first carries the root
            directly: an `artifacts` row (`root_frame_id`), an
            `artifact_versions` row (`frame_id`, often NULL, plus
            `artifact_id`), and a filename match (an `artifacts` row again).
            Reading only `root_frame_id` made the version-addressed case
            resolve to None — which the caller then treated as "nothing to
            check", i.e. exactly the leak the guard exists to stop.
            """
            if not meta:
                return None
            root = meta.get("root_frame_id")
            if root:
                return str(root)
            fid = meta.get("frame_id")
            if fid:
                try:
                    resolved = store.resolve_frame_scope(str(fid)).get("root_frame_id")
                except Exception:  # noqa: BLE001
                    resolved = None
                if resolved:
                    return str(resolved)
            artifact_id = meta.get("artifact_id")
            if artifact_id:
                try:
                    parent = store.get_artifact(str(artifact_id))
                except Exception:  # noqa: BLE001
                    parent = None
                if parent and parent.get("root_frame_id"):
                    return str(parent["root_frame_id"])
            return None

        def _team_guard_served_artifact(self, meta: dict | None) -> None:
            """The real byte chokepoint (INV-13): enforced inside _serve_artifact
            so it covers /preview/ (dispatched before _api) AND version- or
            filename-addressed serves that the path-based _team_scope_guard —
            which resolves artifact_id only, inside _api — cannot see. A guest
            fails visibility on everything here, which is correct: a guest's
            only data surface is the sanitized replay (D3)."""
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None:
                return
            root = self._team_root_of_artifact_meta(meta)
            if identity.is_admin:
                if root:
                    self._team_audit_admin_private_read(root)
                return
            # An artifact whose session cannot be resolved is admin-only, the
            # same fail-closed rule an unowned session gets. Treating
            # "unknown owner" as "no restriction" is how a version-addressed
            # serve walked past this check.
            if root is None or not store.team.session_visible_to(
                root, self._team_identity_dict()
            ):
                raise GatewayError(404, "artifact not found")

        def _team_filter_artifacts(self, artifacts: list[dict]) -> list[dict]:
            """Keep only the artifacts whose session this caller may see
            (project-level metadata/zip routes; INV-13). Admin keeps all and
            audits each private one."""
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None:
                return artifacts
            if identity.is_admin:
                for a in artifacts:
                    root = self._team_root_of_artifact_meta(a)
                    if root:
                        self._team_audit_admin_private_read(root)
                return artifacts
            user = self._team_identity_dict()
            kept = []
            for a in artifacts:
                root = self._team_root_of_artifact_meta(a)
                if root and store.team.session_visible_to(root, user):
                    kept.append(a)
            return kept

        def _team_guard_memory_scope(self, scope: str | None) -> None:
            """Standing context is injected into other people's turns.

            The project guard matches `/projects/{id}` in the *path*, and
            memory carries its scope in `?project_id=` or a JSON body -- so
            every project-addressed-by-parameter route was outside it by
            construction. The write side is the worse half: a member could
            put text into another project's standing context, which then
            rides into every turn its members run.
            """
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None or identity.is_admin:
                return
            if not team_policy.may_use_memory_scope(store, identity, scope):
                # 404 + the project guard's wording: non-membership must be
                # indistinguishable from non-existence.
                raise GatewayError(404, "project not found")

        def _team_guard_share(self, method: str, sub: str) -> None:
            """A share belongs to the session it projects (external review #5).

            Addressed by `share_id`, so the frame matcher never covered it:
            any member could list every share URL in the org and revoke or
            republish anybody's snapshot. Guarded here rather than in the
            two handlers, because patching handlers one at a time is how
            this class of defect keeps recurring in this file.

            404, matching the frame guard: which shares exist is itself the
            protected fact.
            """
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None:
                return
            m = _TEAM_SCOPE_SHARE.fullmatch(sub.split("?")[0])
            if not m:
                return
            try:
                row = store.get_share(unquote(m.group(1)))
            except Exception:  # noqa: BLE001 — undecidable is refused
                raise GatewayError(404, "unknown share") from None
            if row is None:
                return  # a share that does not exist answers as it always did
            root = str(row.get("root_frame_id") or "")
            if identity.is_admin:
                if root:
                    self._team_audit_admin_private_read(root)
                return
            if not team_policy.may_use_share(store, identity, row):
                raise GatewayError(404, "unknown share")

        def _team_guard_instance_config(self, method: str, sub: str) -> None:
            """Refuse a member's reach into instance-global surfaces (M4).

            Three families, all in one guard so a new one is a line in
            `team_policy` and not a new call site: configuration writes,
            daemon-level operations (`/compute/jobs`, which runs
            `bash -c <command>` as the daemon's own uid -- arbitrary command
            execution for every member until this guard existed), and
            instance-global mutations such as installing packages into the
            shared venv or publishing a skill every member's agent loads.

            Not merely "overwrite the group's API key". The same request
            writes `llm_base_url`, so one member can point *every* user's
            provider traffic at a host they control -- which hands them
            everyone's prompts, session content and tool output, and the
            group credential in the outgoing Authorization header. There is
            no per-user variant of that setting to fall back on, which is
            what makes it an operator's action rather than a preference.

            403 rather than the frame guard's 404: these are management
            surfaces whose existence is not a secret, and the UI already
            reads `admin_only` to decide which Customize panes to show.
            """
            path = sub.split("?")[0]
            if not team_policy.is_admin_only_surface(method, path):
                return
            if team_policy.may_change_instance_config(self):
                return
            raise GatewayError(403, "admin only", "admin_only")

        def _team_guard_project(self, method: str, sub: str) -> None:
            """Refuse a project-addressed route to a non-participant (M2-1).
            404, matching the frame guard: which projects exist is protected.
            Admin passes. The bare `/projects` list is filtered at its handler,
            not here."""
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None or identity.is_admin:
                return
            m = _TEAM_SCOPE_PROJECT.fullmatch(sub)
            if not m:
                return
            pid = unquote(m.group(1))
            if not store.governance.is_project_participant(pid, identity.user_id):
                raise GatewayError(404, "project not found")
            # Reading is participation; changing or destroying is membership.
            # Participation is a union that includes "owns a session here",
            # and any member can create a session anywhere they can name --
            # so trusting it for DELETE hands one member the power to erase
            # another team's project, with every member's sessions,
            # artifacts and workspaces inside it.
            if method in ("DELETE", "PUT", "PATCH", "POST") and not (
                team_policy.may_administer_project(store, identity, pid)
            ):
                raise GatewayError(403, "project membership required", "not_a_member")

        def _team_scope_guard(self, method: str, sub: str) -> None:
            """Refuse frame- and artifact-addressed routes whose session this
            caller may not see (M1-6, INV-13). 404, not 403: which sessions
            exist is itself the information being protected.

            Session resolution goes through the *root* frame — a child frame
            id must not answer differently from its root. An artifact is as
            visible as the session that produced it. Admin reads pass, but a
            private session leaves an audit row per view (D4). This path-based
            guard is a first line only for artifacts: the authoritative byte
            check is _team_guard_served_artifact inside _serve_artifact.
            """
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None:
                return
            if identity.is_admin:
                if method == "GET":
                    m = _TEAM_SCOPE_FRAME.fullmatch(sub)
                    if m:
                        frame = store.get_frame(m.group(1))
                        if frame is not None:
                            self._team_audit_admin_private_read(
                                str(frame.get("root_frame_id") or m.group(1))
                            )
                return
            user = self._team_identity_dict()
            m = _TEAM_SCOPE_FRAME.fullmatch(sub)
            if m:
                frame = store.get_frame(m.group(1))
                if frame is not None:
                    root = frame.get("root_frame_id") or m.group(1)
                    if not store.team.session_visible_to(root, user):
                        raise GatewayError(404, "session not found")
                return
            m = _TEAM_SCOPE_ARTIFACT.fullmatch(sub)
            if m:
                try:
                    artifact = store.get_artifact(unquote(m.group(1)))
                except Exception:  # noqa: BLE001 - unknown ids fall through
                    artifact = None
                if artifact is not None:
                    # Resolved through the same helper the byte guard uses,
                    # and failing closed the same way. Reading `root_frame_id`
                    # raw and testing `if root and ...` meant a NULL root --
                    # which `POST /uploads` with no `frame_id` produces, and
                    # the column permits -- short-circuited the `and` and ran
                    # no check at all. The metadata and destructive verbs
                    # (/edit, /rename, DELETE, /versions, /lineage) never
                    # reach `_serve_artifact`, so this is their only guard.
                    root = self._team_root_of_artifact_meta(artifact)
                    if root is None or not store.team.session_visible_to(root, user):
                        raise GatewayError(404, "artifact not found")

        def _team_guard_owned_resource(self, method: str, sub: str) -> None:
            """Refuse an id-addressed resource whose owner this caller may not
            reach (annotations, notes, folders).

            These are the same defect three more times: the *collection*
            route carries a project or frame id and is guarded, while the
            sibling addressed by the resource's own id matches no scope
            regex and was reachable by anybody logged in. Annotations are
            the sharp one -- a pinned body is folded into the session's next
            turn, so rewriting a colleague's annotation is prompt injection
            into their run, and it kept working after they made the session
            private because only their *reads* were being guarded.

            Resolution goes id -> owner (root frame, or project) -> the same
            predicate the rest of the surface asks, so a fourth resource of
            this shape is a row here rather than a new guard.
            """
            identity = getattr(self, "_team_identity", None)
            if _team_auth is None or identity is None or identity.is_admin:
                return
            path = sub.split("?")[0]
            m = re.fullmatch(r"/annotations/([^/]+)", path)
            if m:
                if method in ("GET", "HEAD"):
                    return
                try:
                    row = store.get_annotation(unquote(m.group(1)))
                except Exception:  # noqa: BLE001
                    row = None
                if row is None:
                    return  # unknown id: the handler's own 404 says so
                root = str(row.get("root_frame_id") or "")
                if not team_policy.may_use_session(store, identity, root):
                    raise GatewayError(404, "annotation not found")
                return
            for pattern, resolve in (
                (r"/notes/([^/]+)", store.project_of_note),
                (r"/folders/([^/]+)", store.project_of_folder),
            ):
                m = re.fullmatch(pattern, path)
                if not m:
                    continue
                if method in ("GET", "HEAD"):
                    return
                try:
                    project_id = resolve(unquote(m.group(1)))
                except Exception:  # noqa: BLE001 — undecidable is refused
                    raise GatewayError(404, "not found") from None
                if project_id is None:
                    return  # unknown id: the handler answers for it
                if not team_policy.may_read_project(store, identity, project_id):
                    raise GatewayError(404, "not found")
                return

        def _team_admit(self, method: str, path: str) -> bool:
            """Admit or answer. True -> continue routing with
            `self._team_identity` bound; False -> a response was sent."""
            identity = self._team_identity_from_request()
            self._team_identity = identity
            # Bind the execution principal for this request thread as well.
            # `_team_identity` answers "who is this HTTP request"; the
            # principal is the same answer in the form everything *past* the
            # handler can read -- the turn thread, the kernel RPC, a
            # delegated sub-agent -- none of which take a handler.
            # `_route` resets it in its finally, so one request cannot leave
            # its identity set for the next one on this thread.
            self._principal_token = execution_principal.set_principal(
                execution_principal.for_identity(identity)
            )
            if identity is not None:
                return True
            if path in _TEAM_EXEMPT_PATHS or (
                method == "GET" and path.startswith("/static/")
            ):
                return True
            self.close_connection = True
            if method == "GET" and _wants_html(self.headers):
                # A person in a browser: send them to the login page rather
                # than a JSON refusal they cannot act on.
                self._last_status = 303
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            self._json({"error": "login required", "code": "login_required"}, 401)
            return False

        def _json(self, obj, code: int = 200) -> None:
            # Every error response carries a stable `code` and the request's
            # correlation id, enriched at this one chokepoint rather than at
            # ~29 call sites so a new route cannot forget. The rule itself
            # lives in errors.py so the contract capture can apply the same
            # one: enriching only here is what let the frozen artifacts record
            # a body the server does not send.
            obj = _public_failure(obj, code, getattr(self, "_correlation_id", ""))
            self._send(
                code,
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _content_length(self) -> int:
            transfer_encoding = str(
                self.headers.get("Transfer-Encoding", "") or ""
            ).strip()
            if transfer_encoding:
                self.close_connection = True
                raise GatewayError(400, "Transfer-Encoding is not supported")
            get_all = getattr(self.headers, "get_all", None)
            values = get_all("Content-Length") if callable(get_all) else None
            if values and len(values) > 1:
                self.close_connection = True
                raise GatewayError(400, "ambiguous Content-Length")
            raw = self.headers.get("Content-Length", "0") or "0"
            try:
                length = int(raw)
            except (TypeError, ValueError) as error:
                self.close_connection = True
                raise GatewayError(400, "invalid Content-Length") from error
            if length < 0:
                self.close_connection = True
                raise GatewayError(400, "invalid Content-Length")
            return length

        def _read_request_body(
            self,
            *,
            limit: int,
            required: bool = False,
            required_message: str = "request body is required",
            too_large_message: str = "request body is too large",
        ) -> bytes:
            tracking = getattr(self, "_request_body_tracking_active", False)
            if tracking and getattr(self, "_request_body_ready", False):
                payload = self._request_body_payload
                if len(payload) > limit:
                    self.close_connection = True
                    raise GatewayError(413, too_large_message)
                if required and not payload:
                    raise GatewayError(400, required_message)
                return payload

            length = self._content_length()
            if length > limit:
                self.close_connection = True
                raise GatewayError(413, too_large_message)
            try:
                payload = self.rfile.read(length) if length else b""
            except OSError as error:
                self.close_connection = True
                raise GatewayError(400, "incomplete request body") from error
            if len(payload) != length:
                self.close_connection = True
                raise GatewayError(400, "incomplete request body")
            if tracking:
                self._request_body_payload = payload
                self._request_body_ready = True
            if required and not payload:
                raise GatewayError(400, required_message)
            return payload

        def _prepare_request_body(self, path: str, method: str) -> None:
            if path == _API_ROOT + "/files/upload" and method == "POST":
                # Streamed by the file route itself (M1-8): a 512 MiB upload
                # must not transit daemon memory, so the pre-read is skipped
                # and the handler consumes rfile in chunks. The connection is
                # closed afterwards by _close_on_unread_request_body's
                # not-ready rule, which is correct for a one-shot upload.
                return
            is_session_import = (
                path in (_API_ROOT + "/sessions/import", _API_ROOT + "/sessions/verify")
                and method == "POST"
            )
            self._read_request_body(
                limit=MAX_ARCHIVE_BYTES if is_session_import else _MAX_JSON_BODY_BYTES,
                too_large_message=(
                    "session package is too large"
                    if is_session_import
                    else "request body is too large"
                ),
            )

        def _body(self) -> dict:
            """Parse a JSON request body, or fail with an explicit 4xx.

            Malformed JSON used to become ``{}``. That is the worst possible
            answer: every route reads its fields with ``b.get(...)``, so a
            truncated or mistyped body did not fail — it silently became "the
            client supplied nothing", and the request no-opped while returning
            200. A client cannot tell that from success, so the bug lands on
            whoever later wonders why their setting never saved.

            An empty body stays valid and yields ``{}``: routes with only
            optional fields legitimately accept one. It is *unparseable* input
            that is now an error, not absent input.
            """
            payload = self._read_request_body(limit=_MAX_JSON_BODY_BYTES)
            if not payload:
                return {}
            try:
                parsed = json.loads(payload)
            except (ValueError, TypeError) as e:
                raise GatewayError(
                    400, f"request body is not valid JSON: {e}", "malformed_json"
                ) from e
            if not isinstance(parsed, dict):
                # `[1,2]` parses fine and then AttributeErrors on the first
                # .get() — a 500 for what is squarely a client error.
                raise GatewayError(
                    400,
                    f"request body must be a JSON object, got "
                    f"{type(parsed).__name__}",
                    "invalid_body_type",
                )
            return parsed

        def _body_bytes(self, *, limit: int) -> bytes:
            return self._read_request_body(
                limit=limit,
                required=True,
                required_message="session package body is required",
                too_large_message="session package is too large",
            )

        def _close_on_unread_request_body(self) -> None:
            """Never parse leftover bytes as a second HTTP/1.1 request.

            Accepted requests are read once, before dispatch, and cached for
            ``_body`` / ``_body_bytes``. Rejected requests are not drained after
            a response (which could block on a lying client); their connection
            is simply made non-reusable.
            """

            if getattr(self, "_request_is_websocket", False):
                self.close_connection = True
                return
            if getattr(self, "_request_body_ready", False):
                return
            if self.headers.get("Transfer-Encoding") or self.headers.get(
                "Content-Length"
            ):
                self.close_connection = True

        def _query(self) -> dict:
            return parse_qs(urlparse(self.path).query)

        # ---- dispatch ---------------------------------------------------
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_PATCH(self):
            self._route("PATCH")

        def do_DELETE(self):
            self._route("DELETE")

        @staticmethod
        def _split_host_header(raw: str):
            """(hostname_lower, port|None); (None, None) if missing/malformed.

            Handles IPv6 bracket forms ([::1], [::1]:8760), ordinary host:port,
            bare hostnames, and case-insensitivity.
            """
            h = (raw or "").strip()
            if not h:
                return (None, None)
            if h.startswith("["):
                end = h.find("]")
                if end == -1:
                    return (None, None)  # unterminated IPv6 literal
                host = h[1:end].lower()
                rest = h[end + 1 :]
                if rest == "":
                    return (host, None)
                if not rest.startswith(":"):
                    return (None, None)
                port_s = rest[1:]
            else:
                # A bare unbracketed IPv6 address (>1 colon) is an invalid Host
                # per RFC 7230 — reject rather than mis-split on the last colon.
                if h.count(":") > 1:
                    return (None, None)
                if ":" in h:
                    host, port_s = h.rsplit(":", 1)
                    host = host.lower()
                else:
                    return (h.lower(), None)
            if port_s == "":
                return (host, None)
            try:
                return (host, int(port_s))
            except ValueError:
                return (None, None)

        def _host_header_allowed(self) -> bool:
            # Wildcard bind (0.0.0.0/::): the set of valid external Host names is
            # unknowable, so the token gate — always required on a non-loopback
            # bind — is the authoritative control and we don't second-guess Host.
            if _bind_is_wildcard:
                return True
            raw = self.headers.get("Host", "")
            if not (raw or "").strip():
                # Absent Host: a browser (the only rebinding vector) ALWAYS sends
                # one, so an empty Host is a non-browser local client (curl/CLI).
                # Pass it, mirroring the Origin guard's "curl with no Origin
                # passes" stance — the rebind defense targets forged Host values.
                return True
            host, port = self._split_host_header(raw)
            if host is None or host not in _allowed_hostnames:
                return False
            if port is None:
                # A portless Host is only legitimate when we serve the scheme's
                # default port (80); on any other port a real browser always
                # sends the port, so treat portless as a mismatch.
                return _allowed_port == 80
            return port == _allowed_port

        def _route(self, method: str) -> None:
            self._request_body_tracking_active = True
            self._request_body_ready = False
            self._request_body_payload = b""
            self._request_is_websocket = False
            parsed = urlparse(self.path)
            path = parsed.path
            # Bind an id for this request before anything can fail, so even a
            # rejected request is traceable. A client-supplied id is honoured so
            # a caller can stitch its own trace to ours, but it is bounded and
            # stripped of anything that could forge a log line.
            supplied = _sanitize_header_value(self.headers.get("X-Request-Id", ""))
            self._correlation_id = (
                "".join(c for c in supplied if c.isalnum() or c in "-_")[:64]
                or new_correlation_id()
            )
            correlation_token = set_correlation_id(self._correlation_id)
            try:
                # DNS-rebinding defense: pin the Host header to an address we
                # bind, on EVERY request (GET included) and BEFORE the Origin/
                # token checks. A rebound page is same-origin, so it can also
                # read GET response bodies, and origin-less GETs skip the Origin
                # guard entirely — so the Host allowlist must cover all methods.
                if not self._host_header_allowed():
                    self.close_connection = True
                    self._json({"error": "host not allowed"}, 403)
                    return
                # CSRF guard: the daemon exposes unauthenticated code-exec endpoints
                # (kernel/execute, compute/jobs, host.bash). A malicious page the
                # user visits could POST to them cross-origin (CORS "simple" request,
                # no preflight) → drive-by RCE. Browsers always send Origin on such
                # cross-origin writes; reject any mutating /api request whose Origin
                # is not this same server. Same-origin app fetches + curl (no Origin)
                # pass through.
                # The /api/v1/ws upgrade is a GET, but WebSocket handshakes are
                # exempt from CORS entirely and the socket accepts state-changing
                # commands (cancel_execution) and streams session output plus
                # pending approval prompts. Apply the same Origin==Host check so a
                # foreign page cannot open ws://127.0.0.1:.../api/v1/ws cross-origin.
                # Browsers always send Origin on WS upgrades; non-browser clients
                # send none and pass.
                if path == _API_WS or (
                    method in _MUTATING_METHODS and path.startswith(_API_PREFIX)
                ):
                    origin = self.headers.get("Origin")
                    if origin:
                        onl = urlparse(origin).netloc
                        host = self.headers.get("Host", "")
                        if onl and host and onl != host:
                            self.close_connection = True
                            self._json({"error": "cross-origin request refused"}, 403)
                            return
                # Team guard (OPENAI4S_TEAM_MODE): resolves every request to a
                # user or the loopback-CLI service identity, and *replaces*
                # the single-credential token gate below — a member's browser
                # holds a login cookie, not the machine token. Off by default;
                # the elif keeps the legacy gate byte-identical then (INV-1).
                if _team_auth is not None:
                    if not self._team_admit(method, path):
                        return
                # M2: token gate (only active when bound non-loopback / opt-in).
                elif _auth_token and path not in _UNAUTHENTICATED_PATHS:
                    from http.cookies import SimpleCookie

                    jar = SimpleCookie(self.headers.get("Cookie", "") or "")
                    cookie = jar.get("os_token")
                    # Constant-time. `==` on a secret leaks its prefix through
                    # timing -- weak over loopback, real over a tunnel, and the
                    # fix costs nothing.
                    have_cookie = local_auth.matches(
                        cookie.value if cookie is not None else None, _auth_token
                    )
                    header_token = _presented_token(self.headers)
                    qtok = parse_qs(parsed.query).get("token", [None])[0]
                    if qtok is not None and method in _MUTATING_METHODS:
                        # Refused outright, before any other credential is even
                        # consulted. The gate already declined to *authenticate*
                        # a mutation from the query string -- but a request that
                        # also carried a valid cookie sailed straight through
                        # with the credential still sitting in its URL, which is
                        # accepted-with-a-warning nobody reads. That URL is in
                        # the browser history, the proxy log and the next
                        # Referer, so honouring it normalises the leak: the
                        # caller has no way to find out they are shipping a
                        # secret, because it works. Failing is what makes it
                        # discoverable, and the remedy is always the same one.
                        self.close_connection = True
                        self._json(
                            {
                                "error": (
                                    "a credential in the query string is refused "
                                    f"on {method}; send {_TOKEN_HEADER} or "
                                    "Authorization: Bearer instead"
                                )
                            },
                            401,
                        )
                        return
                    if have_cookie or local_auth.matches(header_token, _auth_token):
                        pass  # already authenticated
                    elif (
                        local_auth.matches(qtok, _auth_token)
                        and method == "GET"
                        and _is_bootstrap_path(path)
                    ):
                        # The root-page bootstrap: set the cookie and redirect
                        # to the same page with the credential stripped, so it
                        # survives in neither the address bar, the history
                        # entry, nor the next Referer.
                        #
                        # Restricted to `_BOOTSTRAP_PATHS`. A URL carrying a
                        # credential is a shareable credential, and on a path
                        # that answers with data it is worse than on the shell:
                        # the response *is* the data, delivered straight to
                        # whoever holds the link, with no redirect and no cookie
                        # hand-off in between. Here the link buys an empty page.
                        scrubbed = _strip_token_from_url(path, parsed.query)
                        # Recorded for the access log in the `finally` below,
                        # which reads `_last_status`. Only `_send` sets it and
                        # this branch writes its own status line, so the single
                        # most security-relevant request the daemon serves was
                        # logged as `status=None` -- indistinguishable from a
                        # request that died before answering.
                        self._last_status = 303
                        self.send_response(303)
                        # The one `send_header` in this file that did not go
                        # through the sanitiser its five siblings use. It is
                        # safe today because CPython's `urlsplit` strips
                        # \t\r\n (`_UNSAFE_URL_BYTES_TO_REMOVE`, guaranteed by
                        # `requires-python >= 3.10`) before the path reaches
                        # here — but that is a property of the stdlib two
                        # layers away, and `_strip_token_from_url` returns the
                        # path completely raw when `token` is the only query
                        # parameter, which is exactly the bootstrap URL. Making
                        # the guarantee local costs nothing and stops the next
                        # reader having to rediscover the stdlib detail.
                        self.send_header("Location", _sanitize_header_value(scrubbed))
                        self.send_header(
                            "Set-Cookie",
                            f"os_token={_auth_token}; Path=/; HttpOnly; "
                            "SameSite=Strict",
                        )
                        self.send_header("Content-Length", "0")  # keep-alive
                        self.end_headers()
                        return
                    else:
                        # A non-GET may not authenticate from the query string.
                        # A URL carrying a credential is logged by proxies, kept
                        # in history and leaked by Referer, and a mutation is the
                        # request least able to afford that; the browser has the
                        # cookie and a script can send the header.
                        self.close_connection = True
                        if _wants_html(self.headers) and method == "GET":
                            # A person, in a browser, who opened the URL the CLI
                            # and the .app print. They used to get raw JSON —
                            # and `/static/app.js` is behind this same gate, so
                            # the SPA cannot load and cannot offer a way in. The
                            # only working URL went to stderr, which the .app
                            # redirects into a log file. Say what to do, in the
                            # one place they are actually looking.
                            self._send(
                                401, _unauthorized_page(), "text/html; charset=utf-8"
                            )
                            return
                        self._json(
                            {
                                "error": (
                                    "unauthorized — open the printed URL once to "
                                    f"set the cookie, or send {_TOKEN_HEADER}"
                                )
                            },
                            401,
                        )
                        return
                # websocket upgrade
                if path == _API_WS:
                    if method != "GET":
                        self.close_connection = True
                        raise GatewayError(405, "websocket upgrade requires GET")
                    if self._content_length() != 0:
                        self.close_connection = True
                        raise GatewayError(400, "websocket upgrade cannot carry a body")
                    self._request_body_ready = True
                    self._request_is_websocket = True
                    self.close_connection = True
                    self._handle_ws()
                    return
                self._prepare_request_body(path, method)
                if path == "/health" and method == "GET":
                    self._json(
                        {
                            "status": "ok",
                            "model": cfg.llm.model,
                        }
                    )
                    return
                if method == "GET" and path == "/login":
                    # The team login page. Served in both modes (its script
                    # redirects home when team mode is off), so the guard's
                    # 303 target exists before the guard does.
                    self._serve_file(
                        WEBUI_DIR / "login.html", "text/html; charset=utf-8"
                    )
                    return
                if method == "GET" and path == "/replay":
                    # The read-only replay viewer (M2-3): a guest's whole UI,
                    # and a member's quick look. Behind the login guard.
                    self._serve_file(
                        WEBUI_DIR / "replay.html", "text/html; charset=utf-8"
                    )
                    return
                # static / SPA shell
                if method == "GET" and self._serve_static(path):
                    return
                if path.startswith(_API_PREFIX):
                    self._api(method, path[len(_API_ROOT) :])
                    return
                if path == "/api" or path.startswith("/api/"):
                    # An un-versioned or wrong-version API path. Without this it
                    # would fall through to the SPA shell below and answer 200
                    # with HTML — a client would read that as success and then
                    # fail parsing JSON, which is a worse failure than a clear
                    # one. Say what happened and where the surface went.
                    self._json(
                        {
                            "error": (
                                f"the API is versioned; use {_API_ROOT} "
                                f"(this daemon serves contract v1 only)"
                            ),
                            "path": path,
                            "api_root": _API_ROOT,
                        },
                        404,
                    )
                    return
                if method == "GET" and path.startswith("/preview/"):
                    self._serve_artifact(
                        unquote(path[len("/preview/") :]), force_html=True
                    )
                    return
                if method == "GET" and path == "/ketcher":
                    self._send(
                        200, _KETCHER_HTML.encode("utf-8"), "text/html; charset=utf-8"
                    )
                    return
                # unknown non-API GET -> SPA shell (deep-linking)
                if method == "GET":
                    self._serve_index()
                    return
                self._json({"error": "not found"}, 404)
            except GatewayError as ge:
                try:
                    self._json(gateway_error_payload(ge), ge.code)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                try:
                    # Projected, not enveloped. `_json` adds `code`/`status`/
                    # `request_id` to whatever body it is handed, so a raw
                    # `str(e)` used to be shipped with a tidy `code` bolted on
                    # -- the envelope made the leak look deliberate. Anything
                    # that reaches this clause is by definition a failure
                    # nobody wrote a message for, so it gets the generic one
                    # and the original goes to the operator diagnostic.
                    body, status = public_exception(
                        e,
                        surface=f"http:{method}",
                        request_id=getattr(self, "_correlation_id", ""),
                    )
                    self._json(body, status)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
            finally:
                self._close_on_unread_request_body()
                # A keep-alive Handler can outlive this request for minutes.
                # Release a potentially large Session package/upload buffer as
                # soon as synchronous dispatch has finished.
                self._request_body_payload = b""
                self._request_body_ready = False
                self._request_body_tracking_active = False
                # Path only, never the query string: tokens and ids ride there.
                log_event(
                    "http_request",
                    method=method,
                    path=path,
                    status=getattr(self, "_last_status", None),
                )
                reset_correlation_id(correlation_token)
                # Same reason the correlation id is reset here: this thread
                # serves the next request too, and an identity left bound is
                # one request answering another request's authorization
                # question.
                token = getattr(self, "_principal_token", None)
                if token is not None:
                    self._principal_token = None
                    try:
                        execution_principal.reset(token)
                    except Exception:  # noqa: BLE001 — never fail a response
                        pass

        # ---- static -----------------------------------------------------
        def _serve_index(self) -> None:
            self._serve_file(WEBUI_DIR / "index.html", "text/html; charset=utf-8")

        def _serve_static(self, path: str) -> bool:
            if path in ("/", "/index.html"):
                self._serve_index()
                return True
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                # Normalize the requested path and require it to share the web-UI
                # root as a common path prefix, so it cannot escape via ".." or an
                # absolute path.
                base = os.path.realpath(str(WEBUI_DIR))
                target_s = os.path.normpath(os.path.join(base, rel))
                if os.path.commonpath((base, target_s)) != base:
                    self._json({"error": "forbidden"}, 403)
                    return True
                target = Path(target_s)
                if target.is_file():
                    ctype = _guess_ctype(target.name)
                    self._serve_file(target, ctype)
                else:
                    self._json({"error": "not found"}, 404)
                return True
            return False

        def _serve_file(self, path: Path, ctype: str) -> None:
            try:
                body = path.read_bytes()
            except OSError:
                self._json({"error": "not found"}, 404)
                return
            self._send(200, body, ctype)

        def _stream_file(
            self, path: Path, ctype: str, extra: dict | None = None
        ) -> None:
            """Send a potentially large local file without loading it into RAM."""
            try:
                source = path.open("rb")
                size = path.stat().st_size
            except OSError:
                self._json({"error": "not found"}, 404)
                return
            with source:
                self.send_response(200)
                self.send_header("Content-Type", _sanitize_header_value(ctype))
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-cache")
                # This path streams artifact bytes — agent-authored content, so
                # the one that most needs nosniff and a closed CSP. It builds
                # its own headers instead of going through _send, so it has to
                # opt in explicitly.
                for key, value in security_headers(WEBUI_DIR / "index.html").items():
                    self.send_header(key, value)
                for key, value in (extra or {}).items():
                    self.send_header(key, _sanitize_header_value(value))
                self.end_headers()
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        # ---- artifact bytes --------------------------------------------
        def _serve_artifact(self, ident: str, force_html: bool = False) -> None:
            path = store.resolve_artifact_path(ident)
            meta = None
            if path is None:
                # Only when the name is unambiguous. This used to take the most
                # recently created artifact with that filename *anywhere*, so
                # `/artifacts/report.pdf` served whichever project last made a
                # `report.pdf` -- an arbitrary cross-project match, delivered
                # with a straight face. The UI never sends a filename here (it
                # always sends `a.id`), so nothing first-party relied on the
                # guess.
                meta = store.artifact_by_unique_filename(unquote(ident))
                if meta:
                    path = meta.get("path")
            else:
                # ident may be an artifact_id OR a version_id — fall back to the
                # version row so a historical version serves its OWN content_type
                meta = store.get_artifact(ident) or store.version_meta(ident)
            if not path or not Path(path).is_file():
                self._json({"error": "artifact not found"}, 404)
                return
            # Team scope (INV-13): the real byte chokepoint. /preview/<id> is
            # dispatched before _api, and version-/filename-addressed serves
            # are invisible to the path-based scope guard — both reach here.
            self._team_guard_served_artifact(meta)
            ctype = (meta or {}).get("content_type") or _guess_ctype(Path(path).name)
            if force_html:
                ctype = "text/html; charset=utf-8"
            self._serve_file(Path(path), ctype)

        def _serve_artifact_bundle(self, artifacts: list[dict], filename: str) -> None:
            """Download a frame/project's current artifact versions as one zip."""
            tmp = tempfile.NamedTemporaryFile(
                prefix="openai4s-artifacts-", suffix=".zip", delete=False
            )
            tmp_path = Path(tmp.name)
            tmp.close()
            used: set[str] = set()
            try:
                with zipfile.ZipFile(
                    tmp_path, "w", compression=zipfile.ZIP_DEFLATED
                ) as zf:
                    for artifact in artifacts:
                        path = artifact.get("path") or store.resolve_artifact_path(
                            artifact.get("artifact_id") or artifact.get("id") or ""
                        )
                        if not path or not Path(path).is_file():
                            continue
                        raw_name = str(
                            artifact.get("filename") or Path(path).name
                        ).replace("\\", "/")
                        parts = [
                            p for p in raw_name.split("/") if p not in ("", ".", "..")
                        ]
                        arcname = "/".join(parts) or Path(path).name
                        if arcname in used:
                            stem, suffix = os.path.splitext(arcname)
                            n = 2
                            while f"{stem}-{n}{suffix}" in used:
                                n += 1
                            arcname = f"{stem}-{n}{suffix}"
                        used.add(arcname)
                        try:
                            zf.write(path, arcname)
                        except OSError:
                            continue
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-")
                if not safe_name.lower().endswith(".zip"):
                    safe_name += ".zip"
                self._stream_file(
                    tmp_path,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        # ---- REST API ---------------------------------------------------
        def _api(self, method: str, sub: str) -> None:
            q = self._query()
            # Team auth routes (login/logout/me). Dispatched first: they
            # depend on no session state and must answer before any frame
            # guard can object. Deterministic in both modes — the contract
            # capture drives them with team mode off.
            if team_routes.handle(self, method, sub, _team_auth, store):
                return
            # Guest gate (D3): a guest's whole API surface is auth + replay.
            # Sits after the auth routes (logout must work) and before
            # everything else, so no later route needs its own guest check.
            _identity = getattr(self, "_team_identity", None)
            if (
                _team_auth is not None
                and _identity is not None
                and _identity.kind == "user"
                and _identity.role == "guest"
                and not (method == "GET" and _REPLAY_ROUTE.fullmatch(sub))
            ):
                self._json(
                    {"error": "guests are replay-only", "code": "guest_readonly"},
                    403,
                )
                return
            # Read-only session replay (M2-3): the web-share sanitized view,
            # served in place — no shares row, no snapshot, no relay. The
            # {id} segment cannot collide with /sessions/import|verify —
            # those carry no /replay suffix.
            m = re.fullmatch(r"/sessions/([^/]+)/replay", sub)
            if m and method == "GET":
                rid = m.group(1)
                frame = store.get_frame(rid)
                if frame is None or (frame.get("root_frame_id") or rid) != rid:
                    raise GatewayError(404, "session not found")
                if _team_auth is not None:
                    user = self._team_identity_dict()
                    if not store.team.session_replayable_by(rid, user):
                        raise GatewayError(404, "session not found")
                    self._team_audit_admin_private_read(rid)
                try:
                    payload = runner.session_replay_view(rid)
                except Exception as e:  # noqa: BLE001
                    raise GatewayError(500, f"replay build failed: {e}") from e
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if governance_routes.handle(self, method, sub, q, _team_auth, store):
                return
            if orchestration_routes.handle(self, method, sub, q, store, runner):
                return
            if compute_session_routes.handle(self, method, sub, q, store, runner):
                return
            if file_routes.handle(self, method, sub, q, _file_area, _team_auth):
                return
            # Session visibility toggle (M2-2, D4): owner-only.
            m = re.fullmatch(r"/frames/([^/]+)/visibility", sub)
            if m and method == "POST":
                if _team_auth is None:
                    self._json(
                        {"error": "team mode is disabled", "code": "team_off"}, 403
                    )
                    return
                identity = getattr(self, "_team_identity", None)
                visibility = str(self._body().get("visibility") or "")
                if visibility not in ("project", "private"):
                    self._json(
                        {"error": "visibility must be 'project' or 'private'"}, 400
                    )
                    return
                changed = identity is not None and store.team.set_session_visibility(
                    m.group(1), visibility, user_id=identity.user_id
                )
                if not changed:
                    # not the owner, or no ownership row: same 404 as the
                    # scope guard — existence stays protected
                    raise GatewayError(404, "session not found")
                self._json({"ok": True, "visibility": visibility})
                return
            # Ownership scope (team mode): every frame-/artifact-addressed
            # route below answers 404 unless the caller may see its session,
            # and every /projects/<pid>/* route unless the caller participates
            # in that project (bare /projects list is filtered at its handler).
            self._team_scope_guard(method, sub)
            self._team_guard_project(method, sub)
            self._team_guard_owned_resource(method, sub)
            self._team_guard_instance_config(method, sub)
            self._team_guard_share(method, sub)
            if sub == "/sessions/verify" and method == "POST":
                # Verification before import, so a recipient can check what
                # they were handed without first admitting it to their
                # database. `verify_package` reads only the archive -- no
                # daemon state, no network -- which is what makes the answer
                # trustworthy to someone who does not yet trust this host.
                from openai4s.evidence import EvidenceError, verify_package

                payload = self._body_bytes(limit=MAX_ARCHIVE_BYTES)
                with tempfile.NamedTemporaryFile(
                    suffix=".openai4s-session.zip", delete=False
                ) as handle:
                    handle.write(payload)
                    staged = Path(handle.name)
                try:
                    report = verify_package(staged)
                except EvidenceError as error:
                    raise GatewayError(400, str(error)) from error
                finally:
                    staged.unlink(missing_ok=True)
                self._json(report)
                return
            if sub == "/sessions/import" and method == "POST":
                payload = self._body_bytes(limit=MAX_ARCHIVE_BYTES)
                try:
                    imported = runner.session_domain.session_import(payload)
                except SessionPackageError as error:
                    raise GatewayError(400, str(error)) from error
                self._team_claim_imported(imported)
                self._json(imported, 201)
                return
            if sub == "/sessions/import-url" and method == "POST":
                from openai4s.share.fetch import BundleFetchError, fetch_bundle

                body = self._body()
                url = str(body.get("url") or "").strip()
                if not url:
                    raise GatewayError(400, "url is required")
                try:
                    payload = fetch_bundle(url, allow_insecure=cfg.share.allow_insecure)
                    imported = runner.session_domain.session_import(payload)
                except BundleFetchError as error:
                    raise GatewayError(400, str(error)) from error
                except SessionPackageError as error:
                    raise GatewayError(400, str(error)) from error
                self._team_claim_imported(imported)
                self._json(imported, 201)
                return
            # ---- web shares ----
            if sub == "/share/settings":
                if method == "GET":
                    self._json({"enabled": runner._share_enabled()})
                    return
                if method in ("PUT", "POST", "PATCH"):
                    body = self._body()
                    self._json(runner.set_sharing_enabled(bool(body.get("enabled"))))
                    return
            if sub == "/share/status" and method == "GET":
                self._json(runner.share_status())
                return
            if sub == "/shares" and method == "GET":
                shares = runner.shares.list_all()
                identity = getattr(self, "_team_identity", None)
                if _team_auth is not None and identity is not None:
                    # No id in the path, so the guard above cannot help:
                    # filtered here the way artifacts are. A share URL is a
                    # capability -- anyone holding it reads the session --
                    # so listing every one of them is handing them out.
                    visible = []
                    for entry in shares:
                        row = None
                        try:
                            row = store.get_share(str(entry.get("share_id") or ""))
                        except Exception:  # noqa: BLE001
                            row = None
                        if identity.is_admin or team_policy.may_use_share(
                            store, identity, row
                        ):
                            visible.append(entry)
                    shares = visible
                self._json({"shares": shares})
                return
            share_create = re.fullmatch(r"/frames/([^/]+)/shares", sub)
            if share_create and method == "POST":
                if not runner._share_enabled():
                    raise GatewayError(
                        403, "sharing is disabled; enable it in Settings"
                    )
                if not cfg.share.configured:
                    raise GatewayError(
                        409, "sharing is not configured (relay URL and token required)"
                    )
                runner.ensure_share_tunnel()
                body = self._body()
                _has_ttl, _exp = _share_expires_at(body)
                try:
                    record = runner.shares.create(
                        share_create.group(1),
                        title=body.get("title"),
                        expires_at=_exp,
                    )
                except ShareConflict as error:
                    self._json(
                        {
                            "error": "a share already exists for this session",
                            "existing_share_id": error.existing_share_id,
                        },
                        409,
                    )
                    return
                except SessionPackageError as error:
                    raise GatewayError(400, str(error)) from error
                self._json(record, 201)
                return
            if share_create and method == "GET":
                self._json(
                    {"shares": runner.shares.list_for_frame(share_create.group(1))}
                )
                return
            share_item = re.fullmatch(r"/shares/([^/]+)", sub)
            if share_item and method == "PUT":
                runner.ensure_share_tunnel()
                body = self._body()
                has_ttl, exp = _share_expires_at(body)
                kwargs = {"expires_at": exp} if has_ttl else {}
                try:
                    self._json(runner.shares.update(share_item.group(1), **kwargs))
                except KeyError:
                    raise GatewayError(404, "unknown share") from None
                except SessionPackageError as error:
                    raise GatewayError(400, str(error)) from error
                return
            if share_item and method == "DELETE":
                self._json(runner.shares.revoke(share_item.group(1)))
                return
            frame_mutation = re.fullmatch(r"/frames/([^/]+)(?:/.*)?", sub)
            if frame_mutation and method != "GET":
                delete_session = method == "DELETE" and sub == (
                    f"/frames/{frame_mutation.group(1)}"
                )
                confirmed_fresh_restart = bool(
                    re.fullmatch(r"/frames/[^/]+/recovery/actions/restart_fresh", sub)
                    and method == "POST"
                )
                read_only_preview = bool(
                    method == "POST"
                    and re.fullmatch(
                        r"/frames/[^/]+/(?:revert/preview|branches/revert-preview)",
                        sub,
                    )
                )
                # Publishing a share is a read-only snapshot of the session, so a
                # quarantined imported session may still be (re-)shared.
                share_publish = bool(
                    method == "POST" and re.fullmatch(r"/frames/[^/]+/shares", sub)
                )
                if not (
                    delete_session
                    or confirmed_fresh_restart
                    or read_only_preview
                    or share_publish
                ):
                    _require_session_writable(
                        frame_mutation.group(1), "mutating the Session"
                    )
            # ---- identity / meta (no-auth local mode) ----
            if sub == "/me":
                self._json(
                    {
                        "user_id": "local-dev",
                        "email": None,
                        "provider": store.get_setting("llm_provider")
                        or cfg.llm.provider,
                        "has_api_key": bool(runner.effective_api_key()),
                        "shared_api_key": False,
                        "auth_mode": "token" if _auth_token else "none",
                    }
                )
                return
            # ---- editable LLM config (Customize → Models) ----
            if sub == "/config/llm":
                if method == "GET":
                    self._json(
                        {
                            "provider": store.get_setting("llm_provider")
                            or cfg.llm.provider,
                            "model": store.get_setting("llm_model")
                            or _default_model["id"],
                            "base_url": store.get_setting("llm_base_url")
                            or cfg.llm.base_url,
                            "has_api_key": bool(runner.effective_api_key()),
                        }
                    )
                    return
                if method in ("POST", "PUT", "PATCH"):
                    b = self._body()
                    previous_datapro_credential = datapro.resolve_agent_plan_key(store)
                    previous_provider = (
                        str(store.get_setting("llm_provider") or cfg.llm.provider or "")
                        .strip()
                        .lower()
                    )
                    requested_provider = (
                        str(b["provider"]).strip().lower()
                        if "provider" in b and b["provider"] is not None
                        else previous_provider
                    )
                    provider_changed = requested_provider != previous_provider
                    for field, key in (
                        ("provider", "llm_provider"),
                        ("model", "llm_model"),
                        ("base_url", "llm_base_url"),
                    ):
                        if field in b and b[field] is not None:
                            store.set_setting(key, str(b[field]).strip())
                    if b.get("api_key"):  # only overwrite when a value is supplied
                        store.set_secret_setting(
                            "llm_api_key", _clean_api_key(b["api_key"]), scope="llm"
                        )
                    # A live key belongs to its provider.  Switching protocols
                    # without a replacement must not reinterpret (for example)
                    # an OpenAI key as an Ark Agent Plan key and send it to
                    # Volcengine.  The dedicated Agent Plan key, if any, stays
                    # independent and becomes DataPro's fallback.
                    if provider_changed and not _clean_api_key(b.get("api_key")):
                        store.set_secret_setting("llm_api_key", "", scope="llm")
                    if b.get("clear_api_key"):
                        store.set_secret_setting("llm_api_key", "", scope="llm")
                    if b.get("model"):
                        _default_model["id"] = str(b["model"]).strip()
                    _disconnect_datapro_if_auth_context_changed(
                        previous_datapro_credential, previous_provider
                    )
                    self._json(
                        {"ok": True, "has_api_key": bool(runner.effective_api_key())}
                    )
                    return
            if sub == "/auth/status":
                # Reachable without a credential, so a client can discover that
                # it needs one. It reported `auth_mode: "none"` unconditionally
                # -- a daemon running with the gate on told every caller there
                # was no gate, and the frontend had no way to learn otherwise.
                #
                # Says whether a token is required and whether this request
                # carried a valid one. Never any part of the token itself.
                self._json(
                    {
                        "authenticated": self._is_authenticated(),
                        "auth_mode": "token" if _auth_token else "none",
                        "token_header": _TOKEN_HEADER if _auth_token else None,
                    }
                )
                return
            if sub == "/csrf":
                self._json({"csrf_token": "local"})
                return
            # ---- global search (⌘K command palette) ----
            if sub.split("?")[0] == "/search" and method == "GET":
                query = (q.get("q") or [""])[0]
                payload = (
                    store.search(query)
                    if query.strip()
                    else {"sessions": [], "artifacts": [], "datapro": []}
                )
                if self._team_visibility_filter() is not None:
                    # Team mode (INV-13): the command palette must not
                    # enumerate other people's sessions or their artifacts.
                    _user = self._team_identity_dict()
                    payload["sessions"] = [
                        s
                        for s in payload.get("sessions", [])
                        if store.team.session_visible_to(str(s.get("id")), _user)
                    ]
                    payload["artifacts"] = [
                        a
                        for a in payload.get("artifacts", [])
                        if a.get("root_frame_id")
                        and store.team.session_visible_to(
                            str(a["root_frame_id"]), _user
                        )
                    ]
                    # The third family. Two of three were filtered and the
                    # third -- indexed DataPro content, which is exactly the
                    # query-matched *scientific* text -- rode through
                    # unchanged. Same predicate, same fail-closed shape: a
                    # hit with no root to check against is not shown.
                    payload["datapro"] = [
                        d
                        for d in payload.get("datapro", [])
                        if d.get("root_frame_id")
                        and store.team.session_visible_to(
                            str(d["root_frame_id"]), _user
                        )
                    ]
                self._json(payload)
                return
            if sub in ("", "/"):
                self._json({"service": "openai4s", "ok": True})
                return

            # ---- models ----
            if sub == "/models" and method == "GET":
                self._json(self._models_payload())
                return
            if sub == "/model-endpoints/discover" and method == "GET":
                force = (q.get("force") or [""])[0].strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
                self._json(model_discovery.discover(force=force))
                return
            if sub == "/models/default":
                if method == "GET":
                    self._json({"default_model_id": _default_model["id"]})
                else:
                    previous_datapro_credential = datapro.resolve_agent_plan_key(store)
                    previous_provider = (
                        str(store.get_setting("llm_provider") or cfg.llm.provider or "")
                        .strip()
                        .lower()
                    )
                    chosen = str(self._body().get("model_id") or "").strip()
                    if chosen:
                        _default_model["id"] = chosen
                    # The selector's option value is now a `profile_id`, because
                    # deduping the list by bare model name made two profiles
                    # sharing a model against different providers indistinguishable
                    # -- and unreachable, since only one survived. Choosing an
                    # entry therefore activates a *configuration*, which is what
                    # the header control has always meant.
                    #
                    # A value that is not a known profile id is still written to
                    # `llm_model`: `.env`-configured installs and older clients
                    # name a model directly and must keep working.
                    known = {
                        str(p.get("id") or ""): p
                        for p in store.list_model_profiles()
                        if not p.get("deleted_at")
                    }
                    if chosen in known:
                        try:
                            _payload, effective = model_profiles.activate(chosen)
                        except ModelProfileError as exc:
                            self._json({"error": str(exc)}, exc.status_code)
                            return
                        _default_model["id"] = chosen
                        if effective:
                            store.set_setting("llm_model", effective)
                    elif chosen:
                        store.set_setting("llm_model", chosen)
                    _disconnect_datapro_if_auth_context_changed(
                        previous_datapro_credential, previous_provider
                    )
                    self._json({"default_model_id": _default_model["id"]})
                return

            # ---- model profiles (saved LLM/API configs: add / switch / delete) ----
            # Each profile is a full API config; activating one copies its fields
            # into the live llm_* settings so switching APIs is one click.
            if sub == "/model-profiles" and method == "GET":
                self._json(self._model_profiles_payload())
                return
            if sub == "/model-profiles" and method == "POST":
                try:
                    self._json(model_profiles.create(self._body()), 201)
                except ModelProfileError as exc:
                    self._json({"error": str(exc)}, exc.status_code)
                return
            m = re.fullmatch(r"/frames/([^/]+)/model-binding", sub)
            if m and method == "POST":
                # The answer to `model_revision_unavailable`. That 409 says
                # "choose one to continue" and, until this existed, nothing
                # could: the two writers of `model_profile_id` sit past the
                # raise, `PATCH /frames/{id}` allowlists name and task_summary,
                # and forking inherits the pin. A session was unsendable for
                # good.
                #
                # Deliberately its own route rather than a flag on send. The
                # client sends `model` on EVERY message, so treating a supplied
                # model as consent would rebind silently on every turn — the
                # drift D2 removed. Re-pinning is a thing someone asks for.
                # No explicit writability check: the blanket
                # `frame_mutation` gate above already covers every non-GET
                # under `/frames/{id}/...`, and a second one here reads as
                # though this route protects itself — which would invite moving
                # it above the real gate some day. Verified by a test that
                # drives a quarantined session and expects 423.
                frame_id = m.group(1)
                store.unpin_model(frame_id)
                self._json(
                    {"ok": True, "binding": runner.bind_model_revision(frame_id)}
                )
                return
            m = re.fullmatch(r"/model-profiles/([^/]+)/probe", sub)
            if m and method == "POST":
                # POST, not GET, because this spends a request against the
                # user's own provider quota. A GET invites a prefetch, a
                # refresh loop or a link crawler to spend it for them, and the
                # whole point of an *explicit* probe is that a human asked.
                try:
                    self._json(model_profiles.probe(m.group(1)))
                except ModelProfileError as exc:
                    self._json({"error": str(exc)}, exc.status_code)
                return
            m = re.fullmatch(r"/model-profiles/([^/]+)/activate", sub)
            if m and method == "POST":
                previous_datapro_credential = datapro.resolve_agent_plan_key(store)
                previous_provider = (
                    str(store.get_setting("llm_provider") or cfg.llm.provider or "")
                    .strip()
                    .lower()
                )
                try:
                    payload, effective_model = model_profiles.activate(m.group(1))
                except ModelProfileError as exc:
                    self._json({"error": str(exc)}, exc.status_code)
                    return
                _default_model["id"] = effective_model or _default_model["id"]
                _disconnect_datapro_if_auth_context_changed(
                    previous_datapro_credential, previous_provider
                )
                self._json(
                    {
                        **payload,
                        "has_api_key": bool(runner.effective_api_key()),
                    }
                )
                return
            m = re.fullmatch(r"/model-profiles/([^/]+)", sub)
            if m and method in ("PUT", "PATCH"):
                previous_datapro_credential = datapro.resolve_agent_plan_key(store)
                previous_provider = (
                    str(store.get_setting("llm_provider") or cfg.llm.provider or "")
                    .strip()
                    .lower()
                )
                try:
                    profile, effective_model = model_profiles.edit(
                        m.group(1), self._body()
                    )
                except ModelProfileError as exc:
                    self._json({"error": str(exc)}, exc.status_code)
                    return
                if effective_model:
                    _default_model["id"] = effective_model
                _disconnect_datapro_if_auth_context_changed(
                    previous_datapro_credential, previous_provider
                )
                self._json(profile)
                return
            m = re.fullmatch(r"/model-profiles/([^/]+)", sub)
            if m and method == "DELETE":
                deleting_active_profile = str(
                    store.get_setting("active_model_profile") or ""
                ) == m.group(1)
                model_profiles.delete(m.group(1))
                if deleting_active_profile:
                    _disconnect_managed_datapro_session()
                self._json({"ok": True})
                return

            # ---- projects ----
            if sub == "/example/session" and method in ("GET", "POST"):
                # The example analysis, on demand. It used to run itself on
                # first boot; see `_demo_seed_enabled` for why that was wrong.
                # GET reports state so the UI can offer the button, hide it once
                # the example exists, and show progress while it runs.
                existing = _example_session_frame(cfg)
                started = False
                if method == "POST" and existing is None:
                    # The confirmation is in the body, not implied by the verb.
                    # This route executes six cells and calls two external APIs,
                    # so "someone sent a POST" is not enough evidence of intent
                    # -- and anything that drives the surface generically (the
                    # contract capture, a route-coverage sweep, a client
                    # retrying a queue) sends exactly that. Requiring a field
                    # makes the expensive path unreachable by accident rather
                    # than relying on every driver to know about this route.
                    if self._body().get("confirm") is not True:
                        raise GatewayError(
                            400,
                            "the example analysis runs code and calls external "
                            'APIs; POST {"confirm": true} to run it',
                            "confirmation_required",
                        )
                    started = runner.example_seed.start(cfg, runner)
                self._json(
                    {
                        "seeded": existing is not None,
                        "frame_id": (existing or {}).get("frame_id")
                        or (existing or {}).get("id"),
                        "project_id": "proj_example",
                        # Distinguishable on purpose: `started` false with
                        # `running` true means someone else's request is already
                        # doing it, which is a different thing from a refusal.
                        "started": started,
                        "running": runner.example_seed.running(),
                        "seeds_at_startup": _demo_seed_enabled(),
                        "error": runner.example_seed.last_error(),
                    }
                )
                return
            if sub == "/projects" and method == "GET":
                projects = store.list_projects()
                # Team mode (INV-13): a non-admin sees only projects they
                # participate in — otherwise the list leaks every team's
                # project names and agent-context prose.
                filt = self._team_visibility_filter()
                if filt is not None:
                    allowed = store.governance.participant_project_ids(filt)
                    projects = [
                        p for p in projects if str(p.get("project_id")) in allowed
                    ]
                self._json(
                    {
                        "projects": [_project_json(p) for p in projects],
                        "total": len(projects),
                    }
                )
                return
            if sub == "/projects" and method == "POST":
                b = self._body()
                p = store.create_project(
                    name=b.get("name") or "Untitled project",
                    description=b.get("description") or "",
                    context=b.get("context") or "",
                )
                # Team mode: the creator becomes a member, so the project
                # guard above lets them back into the project they just made.
                _creator = self._team_owner_user_id()
                if _creator:
                    try:
                        store.governance.set_member(p["project_id"], _creator)
                    except Exception:  # noqa: BLE001
                        pass
                self._json(
                    _project_json(
                        {
                            **p,
                            "conversation_count": 0,
                            "last_active_at": p["updated_at"],
                        }
                    )
                )
                return
            m = re.fullmatch(r"/projects/([^/]+)", sub)
            if m:
                pid = m.group(1)
                if method == "DELETE":
                    self._json(runner.delete_project(pid))
                    return
                if method in ("PUT", "PATCH"):
                    store.update_project(
                        pid,
                        **{
                            k: v
                            for k, v in self._body().items()
                            if k in ("name", "description", "context")
                        },
                    )
                    self._json(_project_json(store.get_project(pid) or {}))
                    return
                if method == "GET":
                    p = store.get_project(pid)
                    self._json(_project_json(p) if p else {})
                    return
            m = re.fullmatch(r"/projects/([^/]+)/notes", sub)
            if m:
                pid = m.group(1)
                if method == "GET":
                    self._json(
                        {"notes": [_note_json(n) for n in store.list_notes(pid)]}
                    )
                    return
                if method == "POST":
                    n = store.add_note(
                        project_id=pid, content=self._body().get("content") or ""
                    )
                    self._json(_note_json(n))
                    return
            m = re.fullmatch(r"/notes/([^/]+)", sub)
            if m and method == "DELETE":
                store.delete_note(m.group(1))
                self._json({"ok": True})
                return

            # ---- folders (session grouping) ----
            m = re.fullmatch(r"/projects/([^/]+)/folders", sub)
            if m:
                pid = m.group(1)
                if method == "GET":
                    self._json({"folders": store.list_folders(pid)})
                    return
                if method == "POST":
                    self._json(
                        store.create_folder(
                            project_id=pid,
                            name=self._body().get("name") or "New folder",
                        )
                    )
                    return
            m = re.fullmatch(r"/projects/([^/]+)/action-timeline", sub)
            if m and method == "GET":
                limit = int((q.get("limit") or ["500"])[0])
                self._json(
                    global_views.timeline_view(
                        unquote(m.group(1)),
                        limit=limit,
                        visible_to_user_id=self._team_visibility_filter(),
                    )
                )
                return
            m = re.fullmatch(r"/projects/([^/]+)/lineage", sub)
            if m and method == "GET":
                limit = int((q.get("limit") or ["2000"])[0])
                self._json(
                    global_views.lineage_view(
                        unquote(m.group(1)),
                        limit=limit,
                        visible_to_user_id=self._team_visibility_filter(),
                    )
                )
                return
            m = re.fullmatch(r"/folders/([^/]+)", sub)
            if m:
                folder_id = m.group(1)
                if method in ("PUT", "PATCH"):
                    store.rename_folder(folder_id, self._body().get("name") or "")
                    self._json({"ok": True})
                    return
                if method == "DELETE":
                    store.delete_folder(folder_id)
                    self._json({"ok": True})
                    return
            m = re.fullmatch(r"/frames/([^/]+)/folder", sub)
            if m and method in ("POST", "PUT", "PATCH"):
                store.set_frame_folder(
                    m.group(1), self._body().get("folder_id") or None
                )
                self._json({"ok": True})
                return

            # ---- frames (sessions) ----
            if sub.split("?")[0] == "/frames" or sub.startswith("/frames?"):
                if method == "GET":
                    pid = (q.get("project_id") or [None])[0]
                    try:
                        limit = max(1, min(200, int((q.get("limit") or ["100"])[0])))
                    except (TypeError, ValueError):
                        raise GatewayError(
                            400, "limit must be an integer", "invalid_limit"
                        )
                    cursor = _decode_frame_cursor((q.get("cursor") or [None])[0])
                    running = runner.running_frames()  # scan jobs ONCE, not per row

                    # Collect one MORE than the page size, then report
                    # has_more from that. The obvious version — fetch a batch,
                    # filter, stop at `limit` — cannot tell a short page from
                    # the last page, because the filter runs after the read: a
                    # project whose sessions are mostly hidden returns fewer
                    # rows than asked and the client reads that as the end.
                    # Asking for one extra makes "is there another page" an
                    # observation instead of an inference.
                    out: list[dict] = []
                    want = limit + 1
                    while len(out) < want:
                        batch = store.browse_frames(
                            project_id=pid or "all",
                            roots_only=True,
                            limit=limit * 2,
                            before=cursor,
                            visible_to_user_id=self._team_visibility_filter(),
                        )
                        if not batch:
                            break
                        last = batch[-1]
                        cursor = (int(last["created_at"] or 0), last["frame_id"])
                        store_drained = len(batch) < limit * 2
                        for f in batch:
                            fj = _frame_json(f, store)
                            # hide abandoned empty sessions (no messages, no
                            # cells, no title) — but keep REPL-only sessions
                            if (
                                not fj["message_count"]
                                and not fj.get("name")
                                and not fj.get("task_summary")
                                and not store.cell_count(f["frame_id"])
                            ):
                                continue
                            fj["running"] = f["frame_id"] in running
                            fj["kernel_alive"] = runner.kernel_alive(f["frame_id"])
                            fj["_cursor"] = (
                                int(f["created_at"] or 0),
                                f["frame_id"],
                            )
                            out.append(fj)
                            if len(out) >= want:
                                break
                        if store_drained:
                            break

                    has_more = len(out) > limit
                    page = out[:limit]
                    next_cursor = None
                    if has_more and page:
                        tail = page[-1]["_cursor"]
                        next_cursor = _encode_frame_cursor(tail[0], tail[1])
                    for row in page:
                        row.pop("_cursor", None)
                    self._json(
                        {
                            "frames": page,
                            "next_cursor": next_cursor,
                            "has_more": has_more,
                        }
                    )
                    return
                if method == "POST":
                    b = self._body()
                    pid = b.get("project_id") or "default"
                    fid = runner.create_session(
                        pid,
                        model=b.get("model"),
                        owner_user_id=self._team_owner_user_id(),
                    )
                    self._json(_frame_json(store.get_frame(fid), store))
                    return
            m = re.fullmatch(r"/frames/([^/]+)", sub)
            if m:
                fid = m.group(1)
                if method == "GET":
                    f = store.get_frame(fid)
                    self._json(_frame_json(f, store) if f else {})
                    return
                if method == "PATCH":
                    store.update_frame(
                        fid,
                        **{
                            k: v
                            for k, v in self._body().items()
                            if k in ("name", "task_summary")
                        },
                    )
                    hub.broadcast(
                        fid,
                        {"type": "frame_update", "frame_id": fid, "status": "updated"},
                    )
                    self._json(_frame_json(store.get_frame(fid), store))
                    return
                if method == "DELETE":
                    runner.delete_session(fid)
                    self._json({"ok": True})
                    return
            m = re.fullmatch(r"/frames/([^/]+)/messages", sub)
            if m and method == "GET":
                fid = m.group(1)
                # Validated like the session list's, and for the same reason:
                # both ends of this were wrong once a client walked it page by
                # page. `?limit=banana` raised ValueError out of the route and
                # reached the browser as a 500 `internal_error`, which a paging
                # client cannot tell from a broken server -- while the sibling
                # `before_seq=banana` on this very route already answered 400.
                # `?limit=-5` was worse: 200, an empty page, and
                # `has_earlier: false`, which reads as "this is the start of
                # history" and silently ends the walk.
                try:
                    start = max(0, int((q.get("from") or ["0"])[0]))
                    limit = int((q.get("limit") or ["300"])[0])
                except (TypeError, ValueError):
                    raise GatewayError(
                        400, "from and limit must be integers", "invalid_limit"
                    )
                limit = max(1, min(MAX_MESSAGE_PAGE, limit))
                branch_id = (q.get("branch_id") or [None])[0]
                # `before_seq` opts into latest-first. Absent, the response is
                # exactly what it always was: oldest-first from `from`. A long
                # session opened without it returns messages 0-299 of 640,
                # which is the wrong end -- so the client asks for the newest
                # page and walks back.
                raw_before = (q.get("before_seq") or [None])[0]
                try:
                    before_seq = (
                        int(raw_before) if raw_before not in (None, "") else None
                    )
                except (TypeError, ValueError):
                    raise GatewayError(
                        400, "before_seq must be an integer", "invalid_cursor"
                    )
                newest_first = before_seq is not None or (
                    (q.get("newest_first") or ["0"])[0] in ("1", "true", "yes")
                )
                msgs = store.list_branch_message_boundaries(
                    fid,
                    branch_id=(branch_id or store.active_session_branch(fid)),
                    start=start,
                    limit=limit,
                    before_seq=before_seq,
                    newest_first=newest_first,
                )
                payload = {
                    "messages": [
                        {
                            "message_id": mm.get("message_id"),
                            "role": mm["role"],
                            "content": mm["content"],
                            "created_at": _iso(mm["created_at"]),
                            "seq": mm.get("seq"),
                            "fork_checkpoint_id": mm.get("fork_checkpoint_id"),
                            # What this message was actually sent with. Reopen
                            # used to hand the client the raw `@name#v-id`
                            # text and nothing else, so the composer chip could
                            # only be guessed at by re-parsing prose -- and a
                            # cross-session reference could not be reconstructed
                            # at all, because the text names the source version
                            # and the model read the local copy.
                            "artifact_refs": _message_artifact_refs(mm),
                            # Absent unless the turn failed, so an ordinary
                            # message is not given a null field to interpret.
                            **(
                                {"failure": _message_failure(mm)}
                                if _message_failure(mm)
                                else {}
                            ),
                        }
                        for mm in msgs
                    ]
                }
                if newest_first:
                    # The cursor for the *next* (older) page, and whether one
                    # exists. Reported rather than inferred from a short page:
                    # a page can be short because the branch projection hid
                    # rows, which a client cannot tell from the end of history.
                    oldest = min((int(mm.get("seq") or 0) for mm in msgs), default=None)
                    payload["next_before_seq"] = oldest
                    payload["has_earlier"] = bool(
                        oldest is not None
                        and store.list_branch_message_boundaries(
                            fid,
                            branch_id=(branch_id or store.active_session_branch(fid)),
                            before_seq=oldest,
                            newest_first=True,
                            limit=1,
                        )
                    )
                self._json(payload)
                return
            m = re.fullmatch(r"/frames/([^/]+)/review-settings", sub)
            if m and method in ("GET", "PUT", "PATCH"):
                fid = m.group(1)
                if not store.get_frame(fid):
                    self._json({"error": "frame not found"}, 404)
                    return
                if method in ("PUT", "PATCH"):
                    b = self._body()
                    if "auto_review" in b:
                        store.set_setting(
                            f"review:auto:{fid}", "1" if b.get("auto_review") else "0"
                        )
                    if "reviewer_model" in b:
                        reviewer_model = str(b.get("reviewer_model") or "").strip()
                        store.set_setting(
                            f"review:model:{fid}",
                            reviewer_model or "__agent__",
                        )
                    if "delegation_enabled" in b:
                        store.set_setting(
                            f"delegation:{fid}",
                            "1" if b.get("delegation_enabled") else "0",
                        )
                local_auto = store.get_setting(f"review:auto:{fid}")
                local_model = store.get_setting(f"review:model:{fid}")
                effective_model = (
                    ""
                    if local_model == "__agent__"
                    else local_model or store.get_setting("reviewer_model") or ""
                )
                self._json(
                    {
                        "auto_review": runner._auto_review_enabled(fid),  # noqa: SLF001
                        "reviewer_model": effective_model,
                        "delegation_enabled": str(
                            store.get_setting(f"delegation:{fid}", "1") or "1"
                        ).lower()
                        in {"1", "true", "yes", "on"},
                        "inherits_auto_review": local_auto is None,
                    }
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/steps", sub)
            if m and method == "GET":
                self._json({"steps": store.list_steps(m.group(1))})
                return
            m = re.fullmatch(r"/frames/([^/]+)/review", sub)
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if not frame:
                    self._json({"error": "frame not found"}, 404)
                    return
                if runner.review_call_inflight(fid):
                    self._json(
                        {"error": "a previous review call is still finishing"}, 409
                    )
                    return
                job = runner.submit_review(fid, frame.get("project_id") or "default")
                self._json(
                    {
                        "status": "accepted",
                        "frame_id": fid,
                        "job_id": job.job_id,
                        "request_id": job.request_id,
                        # The plan spawner holds no coordinator ticket, so this
                        # is synthetic until the turn reaches a real execution.
                        # It is still the id a pre-run failure will be reported
                        # under, and a client needs it to tell that failure
                        # from the next turn's.
                        "execution_id": job.execution_id,
                    },
                    202,
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/message", sub)
            if m and method == "POST":
                fid = m.group(1)
                b = self._body()
                req = (
                    (b.get("input_data") or {}).get("request") or b.get("request") or ""
                )
                f = store.get_frame(fid) or {}
                pid = f.get("project_id") or "default"
                # Fold pinned image annotations into the message so the remote
                # agent receives the exact figure + pin location + comment and
                # can regenerate / edit the file accordingly.
                ann_ids = b.get("annotation_ids") or []
                annos: list = []
                reservation_id = ""
                if ann_ids:
                    # Before the reservation, not after it. The admission is a
                    # durable row, and a request naming a missing or child
                    # frame used to write one and only then be refused.
                    _require_canonical_session_root(fid)
                    # Reserved, not read. `get_annotation` filters no status and
                    # dedupes nothing, so an already-`sent` id re-entered the
                    # prompt, a repeated id entered twice, and two concurrent
                    # requests both carried the same open pin. The reservation
                    # is one atomic UPDATE, so exactly one request claims a
                    # given pin and only what it actually claimed is quoted.
                    # Client-generated, because the case this whole mechanism
                    # exists for is the one where the client never sees the
                    # response. A server-minted id is unknown to a browser
                    # whose 202 was lost, so there is nothing for it to ask
                    # about. The client stores its own id *before* dispatch and
                    # reconciles with it afterwards.
                    #
                    # That makes the id untrusted input, so it is validated for
                    # shape and claimed rather than upserted: a second request
                    # naming an existing id is a replay, not an adoption.
                    supplied = b.get("annotation_reservation_id")
                    if supplied is not None:
                        if type(
                            supplied
                        ) is not str or not _CLIENT_RESERVATION.fullmatch(supplied):
                            raise GatewayError(
                                400,
                                "annotation_reservation_id must be 24-96 chars "
                                "of [A-Za-z0-9_-]",
                                "invalid_reservation_id",
                            )
                        reservation_id = supplied
                    else:
                        # Full 128 bits. A truncated id collides across
                        # sessions and restarts, and what it keys is a claim
                        # on somebody's unpublished comment.
                        reservation_id = f"resv-{uuid.uuid4().hex}"
                    admitted, annos = store.reserve_with_admission(
                        reservation_id=reservation_id,
                        root_frame_id=fid,
                        annotation_ids=ann_ids,
                    )
                    if not admitted:
                        raise GatewayError(
                            409,
                            "this admission id has already been used",
                            "admission_replayed",
                        )

                    block = _format_annotations_block(annos)
                    if block:
                        req = (req + "\n\n" + block).strip() if req.strip() else block
                # The job id the refusal path must be able to retract,
                # recorded BEFORE the write rather than after it.
                #
                # A commit whose outcome is unknown to the caller is the whole
                # problem: wrapping the write and raising *after* it committed
                # leaves the correlation durable while a flag set on the return
                # path never gets set. Cleanup then falls back to a plain
                # release and the row keeps its request and job ids -- still
                # wearing the signature of accepted work. So the candidate is
                # written down first and retracted unconditionally, by CAS on
                # that exact id: if the write never landed the CAS matches
                # nothing and the exact release runs instead.
                correlated: dict = {}

                def _persist_correlation(started_job) -> None:
                    """Which request and job this admission belongs to, durable
                    before the worker starts.

                    Raising here is the point: `submit_message` runs this
                    inside the same guard as `Thread.start`, so a failure
                    aborts the unstarted turn instead of producing a 202 whose
                    ledger row is indistinguishable from a refusal's.
                    """
                    if not reservation_id:
                        return
                    correlated["job_id"] = started_job.job_id
                    if not store.update_admission(
                        reservation_id,
                        root_frame_id=fid,
                        request_id=started_job.request_id,
                        job_id=started_job.job_id,
                    ):
                        raise GatewayError(
                            500,
                            "the admission could not be recorded; the turn was "
                            "not started",
                            "admission_not_recorded",
                        )

                try:
                    job = runner.submit_message(
                        fid,
                        pid,
                        req,
                        b.get("model"),
                        plan=bool(b.get("plan")),
                        annos=annos,
                        explore=bool(b.get("explore")),
                        on_admitted=_persist_correlation,
                    )
                except BaseException:
                    # Every synchronous refusal lands here -- the 413 on an
                    # oversized message, a 409, a 429, and the `Thread.start`
                    # failure that would otherwise strand the turn. The pins go
                    # back to `open` so the composer can retry with them, and
                    # only this request's reservation is released.
                    if reservation_id:
                        try:
                            # Two shapes of refusal, and they are not the
                            # same cleanup.
                            #
                            # Refused before correlation: nothing was written,
                            # so releasing the pins is the whole job, and the
                            # absent job id is what marks it a refusal.
                            #
                            # Refused *after* correlation -- a `Thread.start`
                            # that fails once the ids are already durable --
                            # needs those ids retracted too. Left behind they
                            # read as `released` with a request and a job,
                            # which is the signature of accepted work, and a
                            # reconcile would tell the client not to resend a
                            # turn that never ran.
                            retracted = False
                            candidate = correlated.get("job_id")
                            if candidate:
                                retracted = store.abandon_admission(
                                    reservation_id,
                                    root_frame_id=fid,
                                    job_id=candidate,
                                )
                            if not retracted:
                                store.release_annotations(
                                    reservation_id, root_frame_id=fid
                                )
                        except Exception:  # noqa: BLE001
                            traceback.print_exc()
                    raise
                # Consumed only by a message the server accepted, and that
                # ordering is the whole guarantee. This ran *before*
                # `submit_message`, which is where every refusal this route can
                # make happens -- its own docstring says so, and the
                # oversized-text 413 is one of them. `mark_sent` is one-way
                # (`WHERE status='open'`, with nothing to set it back), so a
                # message that was never accepted destroyed the user's pinned
                # comments: not on a turn, because there was no turn, and not
                # in the composer either. The browser told them the opposite in
                # as many words -- "POST failed → annotations were never
                # consumed server-side" -- and reconciled against the server on
                # that basis, so the UI reported the loss as success.
                #
                # Still guarded on `annos` for the original reason: a batch
                # filtered empty by the frame check flips nothing.
                annotations_state = "none"
                if reservation_id and annos:
                    annotations_state = "sent"
                    try:
                        if not store.finalize_annotations_sent(
                            reservation_id,
                            expected_ids=[a["annotation_id"] for a in annos],
                            root_frame_id=fid,
                            request_id=job.request_id,
                            job_id=job.job_id,
                        ):
                            # The set moved underneath the reservation. Neither
                            # consumed nor free: say so rather than claim it.
                            annotations_state = "pending"
                    except Exception:  # noqa: BLE001
                        # The turn is accepted and running. Reporting an HTTP
                        # failure now would tell the client the message was not
                        # taken, and it would retry -- sending the work twice.
                        # The pins stay `reserved`, which is neither lost nor
                        # double-spent, and the answer says so rather than
                        # claiming they were consumed.
                        annotations_state = "pending"
                        traceback.print_exc()
                if reservation_id and annotations_state == "pending":
                    # The only state this line still moves.
                    #
                    # Correlation is already durable -- `_persist_correlation`
                    # wrote it before the worker started, which is what makes
                    # an accepted turn distinguishable from a refusal even when
                    # the 202 never arrives. `sent` and `released` are written
                    # in the same transaction as the rows they describe. That
                    # leaves `pending`: the consume neither confirmed nor
                    # failed, which is the one outcome no other writer knows
                    # about. It is a CAS in the Store, so it cannot overwrite a
                    # terminal state that landed in between.
                    try:
                        store.update_admission(
                            reservation_id, root_frame_id=fid, state="pending"
                        )
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                if b.get("wait", True) is False:
                    snapshot = runner.executions.snapshot(fid)
                    queued = next(
                        (
                            item
                            for item in snapshot.get("queue", [])
                            if item.get("execution_id") == job.execution_id
                        ),
                        (
                            snapshot.get("owner")
                            if (snapshot.get("owner") or {}).get("execution_id")
                            == job.execution_id
                            else None
                        ),
                    )
                    self._json(
                        {
                            "status": "accepted",
                            "frame_id": fid,
                            "job_id": job.job_id,
                            "execution_id": job.execution_id,
                            "owner": job.execution_owner,
                            "queue_position": (queued or {}).get("queue_position"),
                            # The id the socket event and the job query will
                            # both name for this turn. A 202 says "accepted,
                            # watch elsewhere", so it is the one place a client
                            # can learn which request the later failure belongs
                            # to -- and it was the only one of the three that
                            # did not say.
                            "request_id": job.request_id,
                            # What became of the pins this message carried.
                            # `sent` means consumed exactly once; `pending`
                            # means the turn was accepted but the consume did
                            # not confirm, so they are still reserved -- not
                            # lost, not double-spent, and not to be retried
                            # blindly. The reservation id is what a reconcile
                            # asks about.
                            **(
                                {
                                    "annotations": annotations_state,
                                    "annotation_reservation_id": reservation_id,
                                }
                                if reservation_id
                                else {}
                            ),
                        },
                        202,
                    )
                else:
                    # The same admission facts on both branches. `wait:true` is
                    # the branch a script uses and the one with no socket to
                    # reconcile from later, so telling it *less* about its own
                    # pins than the async branch gets is exactly backwards.
                    waited = job.wait_result()
                    if reservation_id and isinstance(waited, dict):
                        waited = {
                            **waited,
                            "annotations": annotations_state,
                            "annotation_reservation_id": reservation_id,
                        }
                    self._json(waited)
                return
            m = re.fullmatch(r"/frames/([^/]+)/cancel", sub)
            if m and method == "POST":
                b = self._body()
                owner = b.get("owner") or b.get("owner_kind")
                owner_kind = owner.get("kind") if isinstance(owner, dict) else owner
                owner_id = (
                    owner.get("id") if isinstance(owner, dict) else b.get("owner_id")
                )
                if not b.get("execution_id") or not owner_kind or not owner_id:
                    self._json(
                        {
                            "ok": False,
                            "frame_id": m.group(1),
                            "error": (
                                "execution_id, owner.kind, and owner.id are required"
                            ),
                            "reason": (
                                "execution_id, owner.kind, and owner.id are required"
                            ),
                        },
                        400,
                    )
                    return
                self._json(
                    runner.cancel(
                        m.group(1),
                        b.get("execution_id"),
                        owner=owner,
                        owner_id=str(owner_id),
                        reason=b.get("reason") or "cancelled by user",
                    )
                )
                return
            # ---- permission gate: answer a pending tool-call approval ----
            m = re.fullmatch(r"/frames/([^/]+)/decision", sub)
            if m and method == "POST":
                b = self._body()
                from openai4s.permissions import broker

                frame = store.get_frame(m.group(1))
                if frame is None:
                    self._json({"ok": False, "error": "session not found"}, 404)
                    return
                root = frame.get("root_frame_id") or m.group(1)
                resolution = broker().resolve_result(
                    b.get("decision_id"),
                    allow=bool(b.get("allow")),
                    scope=b.get("scope") or "once",
                    pattern=b.get("pattern"),
                    message=b.get("message"),
                    store=store,
                    root_frame_id=root,
                )
                if (
                    resolution.get("ok")
                    and resolution.get("resolution_context") == "after_restart"
                ):
                    hub.broadcast(
                        root,
                        {
                            "type": "permission_resolved",
                            "frame_id": root,
                            "decision_id": b.get("decision_id"),
                            "allow": bool(resolution.get("allow")),
                            "scope": resolution.get("scope"),
                            "resolution_context": "after_restart",
                            "requires_continue": bool(
                                resolution.get("requires_continue")
                            ),
                            "original_action_executed": False,
                            "continuation_expires_at": resolution.get(
                                "continuation_expires_at"
                            ),
                            "continuation_authorization": resolution.get(
                                "continuation_authorization"
                            ),
                        },
                    )
                if resolution.get("ok") is not True:
                    # One envelope, like every other refusal on this surface.
                    # These eight answered HTTP 200 with `{ok: false}` while the
                    # `session not found` branch fifteen lines above already used
                    # 404 -- the same handler, two contracts. `public_failure`
                    # is skipped by a 2xx, so none of them carried a code, a
                    # status or a request id, and `_json` enriches only once the
                    # status says failure.
                    #
                    # The frontend needs no change to receive these: its call
                    # site already throws on `ok !== true` and catches an
                    # `ApiError` from `api()` in the same block. `code` and
                    # `output_committed` ride on the payload, which
                    # `public_failure` preserves rather than overwrites.
                    self._json(
                        resolution,
                        _DECISION_REFUSAL_STATUS.get(
                            str(resolution.get("code") or ""), 400
                        ),
                    )
                    return
                self._json(resolution)
                return
            # ---- permission rules: list (per conversation) / upsert / delete ----
            m = re.fullmatch(r"/frames/([^/]+)/permissions", sub)
            if m and method == "GET":
                fr = store.get_frame(m.group(1)) or {}
                root = fr.get("root_frame_id") or m.group(1)
                proj = fr.get("project_id") or "default"
                self._json(
                    {
                        "root_frame_id": root,
                        "project_id": proj,
                        "rules": store.list_permission_rules_for_frame(
                            root_frame_id=root, project_id=proj
                        ),
                    }
                )
                return
            if sub == "/permissions" and method == "POST":
                b = self._body()
                scope = b.get("scope") or "global"
                scope_id = b.get("scope_id")
                if scope_id is None and b.get("frame_id"):
                    fr = store.get_frame(b["frame_id"]) or {}
                    scope_id = {
                        "conversation": fr.get("root_frame_id") or b["frame_id"],
                        "project": fr.get("project_id") or "default",
                        "global": "",
                    }.get(scope, "")
                if scope == "conversation" and scope_id:
                    _require_session_writable(
                        str(scope_id), "changing Session permissions"
                    )
                # A standing rule is authorization for *future* actions, and
                # a global one is authorization for everybody's. In team
                # mode a member may write rules for what they can reach --
                # their own session, a project they participate in -- and
                # nothing wider: the default scope is `global`, so an
                # unqualified POST from a member would otherwise plant an
                # "allow" that every other user's agent then honours.
                _identity = getattr(self, "_team_identity", None)
                if (
                    _team_auth is not None
                    and _identity is not None
                    and (not _identity.is_admin)
                ):
                    if scope == "global":
                        raise GatewayError(
                            403, "global permission rules are admin only", "admin_only"
                        )
                    if scope == "project" and not team_policy.may_read_project(
                        store, _identity, str(scope_id or "")
                    ):
                        raise GatewayError(404, "project not found")
                    if scope == "conversation" and not team_policy.may_use_session(
                        store, _identity, str(scope_id or "")
                    ):
                        raise GatewayError(404, "session not found")
                rid = store.set_permission_rule(
                    scope=scope,
                    scope_id=scope_id or "",
                    tool=b.get("tool") or "*",
                    pattern=b.get("pattern") or "*",
                    decision=b.get("decision") or "ask",
                )
                self._json({"ok": True, "rule_id": rid})
                return
            if sub == "/permissions/reset" and method == "POST":
                store.seed_default_permission_rules(force=True)
                self._json(
                    {
                        "ok": True,
                        "rules": store.get_permission_rules(
                            scope="global", scope_id=""
                        ),
                    }
                )
                return
            m = re.fullmatch(r"/permissions/([^/]+)", sub)
            if m and method == "DELETE":
                rule = store.get_permission_rule(m.group(1))
                if rule and rule.get("scope") == "conversation":
                    _require_session_writable(
                        str(rule.get("scope_id") or ""),
                        "deleting Session permissions",
                    )
                # The same scope rule POST applies, on the verb that destroys
                # rather than creates. Guarding only the create left the
                # asymmetry open: a member cannot write a global rule, but
                # could delete the admin's -- and the ids arrive through a
                # route they legitimately pass, since
                # `/frames/{own}/permissions` returns the global tier
                # alongside the session's. `resolve()` treats any matching
                # deny as an absolute veto, so deleting a global deny is how
                # a standing refusal becomes an allow for everyone.
                _identity = getattr(self, "_team_identity", None)
                if (
                    rule
                    and _team_auth is not None
                    and _identity is not None
                    and (not _identity.is_admin)
                ):
                    _scope = str(rule.get("scope") or "")
                    _scope_id = str(rule.get("scope_id") or "")
                    if _scope == "global":
                        raise GatewayError(
                            403, "global permission rules are admin only", "admin_only"
                        )
                    if _scope == "project" and not team_policy.may_read_project(
                        store, _identity, _scope_id
                    ):
                        raise GatewayError(404, "project not found")
                    if _scope == "conversation" and not team_policy.may_use_session(
                        store, _identity, _scope_id
                    ):
                        raise GatewayError(404, "session not found")
                store.delete_permission_rule(m.group(1))
                self._json({"ok": True})
                return
            m = re.fullmatch(r"/frames/([^/]+)/feedback", sub)
            if m and method == "POST":
                fid = m.group(1)
                b = self._body()
                store.set_feedback(fid, str(b.get("key") or "0"), b.get("rating"))
                self._json({"ok": True})
                return
            m = re.fullmatch(r"/frames/([^/]+)/feedback", sub)
            if m and method == "GET":
                self._json({"feedback": store.list_feedback(m.group(1))})
                return
            # ---- structured plan: get / approve / revise / discard ----
            m = re.fullmatch(r"/frames/([^/]+)/plan", sub)
            if m and method == "GET":
                self._json(runner.get_plan_state(m.group(1)))
                return
            m = re.fullmatch(
                r"/frames/([^/]+)/plan/(approve|resume|revise|discard)", sub
            )
            if m and method == "POST":
                fid, action = m.group(1), m.group(2)
                b = self._body()
                f = store.get_frame(fid) or {}
                pid = f.get("project_id") or "default"
                model = b.get("model")
                if action == "approve":
                    # Claimed here, synchronously, for the same reason `resume`
                    # below is: this route answers 202 and then runs the plan on
                    # a background thread, so a status check made inside that
                    # thread cannot decide who owns the execution. Two POSTs
                    # both read `draft`, both were accepted, and both turns ran
                    # the same steps against the same session.
                    claim = runner.claim_plan_approval(fid)
                    if not claim.get("ok"):
                        # 404 when there is no plan at all, 409 when there is
                        # one and it is in the wrong state. They are different
                        # answers to different questions -- "you are looking at
                        # nothing" and "somebody else already has this" -- and
                        # collapsing them makes the second unrecognisable.
                        if claim.get("plan_id") is None:
                            raise GatewayError(404, claim["error"], "plan_not_found")
                        raise GatewayError(409, claim["error"], "plan_not_draft")
                    # The claim already moved the row to `executing`, which is
                    # what makes the 202 mean something. If the job then never
                    # starts -- a process that cannot make another thread is
                    # the realistic case -- the plan is left `executing` with
                    # nothing running, and it is stuck there permanently:
                    # every later approve compare-and-swaps against `draft` and
                    # every later resume against `paused`, so both lose
                    # forever. Releasing the claim is what keeps it a claim
                    # rather than a one-way door.
                    try:
                        job = runner.submit_plan_approval(
                            fid, pid, model, claimed_plan_id=claim["plan_id"]
                        )
                    except Exception:
                        store.compare_and_set_plan_status(
                            claim["plan_id"], expected="executing", new_status="draft"
                        )
                        raise
                    self._json(
                        {
                            "status": "accepted",
                            "frame_id": fid,
                            "job_id": job.job_id,
                            "request_id": job.request_id,
                            # The coordinator's own id, taken at submit --
                            # the same one the socket, the poll and a pre-run
                            # failure all name.
                            "execution_id": job.execution_id,
                        },
                        202,
                    )
                elif action == "resume":
                    # The paused -> executing transition *is* the acceptance,
                    # so it happens here, before the 202, and it is a
                    # compare-and-swap rather than a status read. It used to be
                    # a read here plus an unconditional write in the job this
                    # spawns -- and since the job runs on a background thread,
                    # two POSTs on the threading server both read `paused`,
                    # both were accepted, and both turns executed the same
                    # steps. Now exactly one caller wins the swap; the other is
                    # refused synchronously with the status it lost to.
                    claim = runner.claim_plan_resume(fid)
                    if not claim.get("ok"):
                        # Same split as `approve` above.
                        if claim.get("plan_id") is None:
                            raise GatewayError(404, claim["error"], "plan_not_found")
                        raise GatewayError(409, claim["error"], "plan_not_paused")
                    # Same one-way door as `approve` above, and only a
                    # `paused` row can be resumed.
                    try:
                        job = runner.submit_plan_resume(
                            fid, pid, model, claimed_plan_id=claim["plan_id"]
                        )
                    except Exception:
                        store.compare_and_set_plan_status(
                            claim["plan_id"], expected="executing", new_status="paused"
                        )
                        raise
                    self._json(
                        {
                            "status": "accepted",
                            "frame_id": fid,
                            "job_id": job.job_id,
                            "request_id": job.request_id,
                            # The coordinator's own id, taken at submit --
                            # the same one the socket, the poll and a pre-run
                            # failure all name.
                            "execution_id": job.execution_id,
                        },
                        202,
                    )
                elif action == "revise":
                    changes = (b.get("changes") or b.get("feedback") or "").strip()
                    if not changes:
                        self._json({"error": "changes required"}, 400)
                        return
                    job = runner.submit_plan_revision(fid, pid, changes, model)
                    self._json(
                        {
                            "status": "accepted",
                            "frame_id": fid,
                            "job_id": job.job_id,
                            "request_id": job.request_id,
                            # The coordinator's own id, taken at submit --
                            # the same one the socket, the poll and a pre-run
                            # failure all name.
                            "execution_id": job.execution_id,
                        },
                        202,
                    )
                else:  # discard
                    self._json(runner.discard_plan(fid))
                return
            # ---- image annotations (figure review) ----
            m = re.fullmatch(r"/frames/([^/]+)/annotations", sub)
            if m and method == "GET":
                fid = m.group(1)
                art = (q.get("artifact_id") or [None])[0]
                annos = store.list_annotations(fid, artifact_id=art)
                self._json({"annotations": [_annotation_json(a) for a in annos]})
                return
            if m and method == "POST":
                fid = m.group(1)
                b = self._body()
                body_text = (b.get("body") or b.get("text") or "").strip()
                art_id = b.get("artifact_id")
                if not body_text or not art_id:
                    self._json({"error": "artifact_id and body required"}, 400)
                    return
                # A pin is Session-scoped, and `add_annotation` writes whatever
                # root it is handed. A comment filed against a child frame is
                # unreachable from the Session that owns it and survives that
                # Session's deletion.
                _require_canonical_session_root(fid)
                # Bind the pin to the version on screen right now, not to the
                # artifact. The client is not asked for it: a version id it
                # supplied would be a claim about what it was displaying, and
                # the point of the binding is that it is the server's own
                # record. See `_pinned_image_bytes` for why re-resolving the
                # artifact at send time is the wrong answer.
                bound = store.get_artifact(str(art_id)) or {}
                anno = store.add_annotation(
                    root_frame_id=fid,
                    artifact_id=str(art_id),
                    artifact_name=b.get("artifact_name"),
                    rel_x=b.get("x", b.get("rel_x", 0)),
                    rel_y=b.get("y", b.get("rel_y", 0)),
                    body=body_text,
                    version_id=bound.get("latest_version_id"),
                    checksum=bound.get("checksum"),
                )
                self._json({"annotation": _annotation_json(anno)}, 201)
                return
            m = re.fullmatch(r"/frames/([^/]+)/admissions/([^/]+)", sub)
            if m and method == "GET":
                # What a client asks after its 202 never arrived. Without this
                # its only options are to resend (double work) or abandon the
                # comments (silent loss).
                # The frame first. A deleted session takes its pins and its
                # ledger with it, but a client holding the old frame id and a
                # reservation id would still have been answered 200 from
                # whatever survived -- a session that no longer exists
                # reporting on comments that no longer exist.
                if store.get_frame(m.group(1)) is None:
                    raise GatewayError(404, "no such session")
                record = runner.reconcile_admission(m.group(1), m.group(2))
                if record is None:
                    raise GatewayError(404, "no such admission")
                self._json(record)
                return
            m = re.fullmatch(r"/annotations/([^/]+)", sub)
            if m and method in ("PATCH", "POST", "PUT"):
                current_annotation = store.get_annotation(m.group(1))
                if current_annotation and current_annotation.get("root_frame_id"):
                    _require_session_writable(
                        str(current_annotation["root_frame_id"]),
                        "editing Session annotations",
                    )
                b = self._body()
                # No `annotation_is_reserved()` first. The check and the write
                # have to be one statement: `Store`'s lock is per instance and
                # the daemon has more than one instance on one file, so a read
                # here and a write below is a real race -- measured, it left a
                # row `open` with its `reservation_id` still set.
                try:
                    anno = store.update_annotation(
                        m.group(1),
                        body=b.get("body"),
                        status=b.get("status"),
                    )
                except ValueError as invalid:
                    raise GatewayError(
                        400, "unsupported annotation status", "invalid_status"
                    ) from invalid
                if anno is None and store.get_annotation(m.group(1)) is not None:
                    raise GatewayError(
                        409,
                        "this comment is being sent with a turn already in "
                        "flight; wait for that turn to finish",
                        "annotation_reserved",
                    )
                if anno is None:
                    # The one refusal on this surface that did not carry the
                    # PublicFailure envelope: `{"annotation": null}` with a 404
                    # has no `error`, no stable `code`, no `request_id`. The
                    # UI's own `api()` builds an ApiError out of `j.error` for
                    # every non-2xx, so this arrived as a failure with nothing
                    # in it -- and the frozen contract published that as the
                    # route's error shape. Raising is what the rest of the
                    # gateway does, and the dispatcher enriches it.
                    raise GatewayError(404, "annotation not found")
                self._json({"annotation": _annotation_json(anno)})
                return
            if m and method == "DELETE":
                current_annotation = store.get_annotation(m.group(1))
                if current_annotation and current_annotation.get("root_frame_id"):
                    _require_session_writable(
                        str(current_annotation["root_frame_id"]),
                        "deleting Session annotations",
                    )
                existed = store.get_annotation(m.group(1)) is not None
                if not store.delete_unreserved_annotation(m.group(1)) and existed:
                    raise GatewayError(
                        409,
                        "this comment is being sent with a turn already in "
                        "flight; wait for that turn to finish",
                        "annotation_reserved",
                    )
                self._json({"ok": True})
                return
            m = re.fullmatch(r"/frames/([^/]+)/artifacts\.zip", sub)
            if m and method == "GET":
                fid = m.group(1)
                self._serve_artifact_bundle(
                    store.list_artifacts({"root_frame_id": fid}),
                    f"session-{fid}-artifacts.zip",
                )
                return
            m = re.fullmatch(r"/projects/([^/]+)/artifacts\.zip", sub)
            if m and method == "GET":
                pid = m.group(1)
                self._serve_artifact_bundle(
                    self._team_filter_artifacts(
                        store.list_artifacts({"project_id": pid})
                    ),
                    f"project-{pid}-artifacts.zip",
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/artifacts/promote", sub)
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "unknown session")
                _require_session_writable(fid, "promoting a cell to an Artifact")
                cell_id = str(self._body().get("cell_id") or "").strip()
                if not cell_id:
                    raise GatewayError(400, "cell_id is required")
                cell = next(
                    (
                        c
                        for c in self._exec_log(fid).get("entries", [])
                        if str(c.get("producing_cell_id")) == cell_id
                    ),
                    None,
                )
                if cell is None:
                    raise GatewayError(404, "unknown cell")
                metadata = runner.artifacts.promote_cell(
                    PromotionTarget(
                        root_frame_id=fid,
                        project_id=str(frame.get("project_id") or ""),
                        workspace=runner.active_workspace_for(fid),
                    ),
                    cell,
                    runner.hub.emitter(fid),
                )
                if metadata is None:
                    raise GatewayError(500, "promotion failed")
                self._json(metadata)
                return
            m = re.fullmatch(r"/frames/([^/]+)/artifacts", sub)
            if m and method == "GET":
                fid = m.group(1)
                arts = store.list_artifacts({"root_frame_id": fid})
                self._json([_artifact_json(a) for a in arts])
                return
            m = re.fullmatch(r"/projects/([^/]+)/artifacts", sub)
            if m and method == "GET":
                # Every artifact produced across all of a project's conversations
                # (frames) — powers the Files panel's "project" scope so files
                # aren't siloed per conversation.
                pid = m.group(1)
                arts = self._team_filter_artifacts(
                    store.list_artifacts({"project_id": pid})
                )
                self._json([_artifact_json(a) for a in arts])
                return
            m = re.fullmatch(r"/frames/([^/]+)/execution-log", sub)
            if m and method == "GET":
                self._json(self._exec_log(m.group(1)))
                return
            # ---- scientific session workbench projections -------------
            # These routes all describe an existing durable research session.
            # Validate that boundary once so an unknown id cannot look like a
            # truthful empty timeline/queue/recovery state (or leak a KeyError
            # as a 500 from the stricter projections).
            workbench = re.fullmatch(
                r"/frames/([^/]+)/(?:"
                r"action-timeline|execution-queue|context|security|"
                r"delegations|"
                r"recovery(?:/actions(?:/(?:restore|retry|restart_fresh))?)?|"
                r"branches(?:/(?:checkpoints|fork|revert-preview|revert|[^/]+/activate))?|"
                r"checkpoints|revert/(?:preview|apply|undo|operations)|"
                r"notebook/export|session/export|kernel/variables|execution)",
                sub,
            )
            if workbench and store.get_frame(workbench.group(1)) is None:
                raise GatewayError(404, "session not found")
            m = re.fullmatch(r"/frames/([^/]+)/action-timeline", sub)
            if m and method == "GET":
                after = (q.get("after_ordinal") or [None])[0]
                before = (q.get("before_ordinal") or [None])[0]
                raw_limit = (q.get("limit") or ["500"])[0]
                try:
                    after_ordinal = int(after) if after not in (None, "") else None
                    before_ordinal = int(before) if before not in (None, "") else None
                    limit = int(raw_limit)
                except (TypeError, ValueError):
                    self._json(
                        {
                            "error": (
                                "after_ordinal, before_ordinal, and limit must "
                                "be integers"
                            )
                        },
                        400,
                    )
                    return
                invalid_cursor = (
                    (after_ordinal is not None and after_ordinal < 0)
                    or (before_ordinal is not None and before_ordinal < 0)
                    or (after_ordinal is not None and before_ordinal is not None)
                )
                if invalid_cursor or not (1 <= limit <= 500):
                    self._json(
                        {
                            "error": (
                                "timeline cursors must be non-negative and "
                                "mutually exclusive; limit must be between 1 "
                                "and 500"
                            )
                        },
                        400,
                    )
                    return
                self._json(
                    runner.session_domain.action_timeline(
                        m.group(1),
                        branch_id=(q.get("branch_id") or [None])[0],
                        after_ordinal=after_ordinal,
                        before_ordinal=before_ordinal,
                        limit=limit,
                    )
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/execution-queue", sub)
            if m and method == "GET":
                self._json(runner.executions.snapshot(m.group(1)))
                return
            m = re.fullmatch(r"/frames/([^/]+)/context", sub)
            if m and method == "GET":
                self._json(runner.workbench.context(m.group(1)))
                return
            m = re.fullmatch(r"/frames/([^/]+)/security", sub)
            if m and method == "GET":
                self._json(runner.workbench.security(m.group(1)))
                return
            m = re.fullmatch(r"/frames/([^/]+)/delegations", sub)
            if m and method == "GET":
                self._json(runner.workbench.delegation(m.group(1)))
                return
            m = re.fullmatch(r"/frames/([^/]+)/recovery", sub)
            if m and method == "GET":
                self._json(
                    runner.session_domain.recovery_status(
                        m.group(1),
                        branch_id=(q.get("branch_id") or [None])[0],
                    )
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/recovery/actions", sub)
            if m and method == "GET":
                self._json(
                    runner.session_domain.recovery_actions(
                        m.group(1),
                        branch_id=(q.get("branch_id") or [None])[0],
                    )
                )
                return
            m = re.fullmatch(
                r"/frames/([^/]+)/recovery/actions/" r"(restore|retry|restart_fresh)",
                sub,
            )
            if m and method == "POST":
                fid, action_id = m.groups()
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "session not found")
                body = self._body()
                try:
                    result = runner.execute_recovery_action(
                        fid,
                        frame.get("project_id") or "default",
                        action_id,
                        branch_id=body.get("branch_id"),
                        confirmed=body.get("confirm") is True,
                    )
                except RecoveryActionError as error:
                    raise GatewayError(409, str(error)) from error
                self._json(result, 200 if result.get("ok") else 409)
                return
            m = re.fullmatch(r"/frames/([^/]+)/branches", sub)
            if m and method == "GET":
                self._json(runner.session_domain.branches(m.group(1)))
                return
            m = re.fullmatch(r"/frames/([^/]+)/branches/([^/]+)/activate", sub)
            if m and method == "POST":
                frame_id = m.group(1)
                frame = store.get_frame(frame_id) or {}
                result = runner.activate_session_branch(
                    frame_id,
                    str(frame.get("project_id") or "default"),
                    unquote(m.group(2)),
                )
                self._json(result)
                return
            m = re.fullmatch(
                r"/frames/([^/]+)/(?:checkpoints|branches/checkpoints)", sub
            )
            if m and method == "GET":
                self._json(
                    runner.session_domain.checkpoints(
                        m.group(1),
                        branch_id=(q.get("branch_id") or [None])[0],
                    )
                )
                return
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "session not found")
                body = self._body()
                self._json(
                    runner.mutate_session_domain(
                        fid,
                        frame.get("project_id") or "default",
                        operation="create_checkpoint",
                        mutate=lambda: runner.session_domain.create_checkpoint(
                            fid,
                            branch_id=body.get("branch_id"),
                            reason=body.get("reason") or "manual",
                            expected_head=body.get("expected_head"),
                        ),
                    )
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/branches/fork", sub)
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "session not found")
                body = self._body()
                source_fields = (
                    "from_checkpoint_id",
                    "from_cell_id",
                    "from_message_id",
                )
                if sum(bool(body.get(field)) for field in source_fields) != 1:
                    raise GatewayError(
                        400,
                        "provide exactly one fork source",
                    )
                try:
                    result = runner.mutate_session_domain(
                        fid,
                        frame.get("project_id") or "default",
                        operation="fork_branch",
                        mutate=lambda: runner.session_domain.fork_branch(
                            fid,
                            from_checkpoint_id=body.get("from_checkpoint_id"),
                            from_cell_id=body.get("from_cell_id"),
                            from_message_id=body.get("from_message_id"),
                            branch_id=body.get("branch_id"),
                            name=body.get("name"),
                        ),
                    )
                except CursorCheckpointUnavailable as error:
                    raise GatewayError(
                        409,
                        "historical source has no exact cursor checkpoint",
                    ) from error
                self._json(result)
                return
            m = re.fullmatch(
                r"/frames/([^/]+)/(?:revert/preview|branches/revert-preview)", sub
            )
            if m and method == "POST":
                body = self._body()
                target = body.get("target_checkpoint_id")
                if not target:
                    raise GatewayError(400, "target_checkpoint_id is required")
                self._json(
                    {
                        "preview": runner.session_domain.revert_preview(
                            m.group(1),
                            target_checkpoint_id=target,
                            branch_id=body.get("branch_id"),
                        )
                    }
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/(?:revert/apply|branches/revert)", sub)
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "session not found")
                body = self._body()
                target = body.get("target_checkpoint_id")
                if not target:
                    raise GatewayError(400, "target_checkpoint_id is required")
                result = runner.mutate_session_domain(
                    fid,
                    frame.get("project_id") or "default",
                    operation="revert_session",
                    mutate=lambda: runner.session_domain.revert_apply(
                        fid,
                        target_checkpoint_id=target,
                        branch_id=body.get("branch_id"),
                    ),
                    invalidate_kernel=True,
                )
                self._json(result, 200 if result.get("ok") else 409)
                return
            m = re.fullmatch(r"/frames/([^/]+)/revert/undo", sub)
            if m and method == "POST":
                fid = m.group(1)
                frame = store.get_frame(fid)
                if frame is None:
                    raise GatewayError(404, "session not found")
                body = self._body()
                revert_checkpoint = body.get("revert_checkpoint_id")
                if not revert_checkpoint:
                    raise GatewayError(400, "revert_checkpoint_id is required")
                result = runner.mutate_session_domain(
                    fid,
                    frame.get("project_id") or "default",
                    operation="undo_revert",
                    mutate=lambda: runner.session_domain.revert_undo(
                        fid,
                        revert_checkpoint_id=revert_checkpoint,
                        branch_id=body.get("branch_id"),
                    ),
                    invalidate_kernel=True,
                )
                self._json(result, 200 if result.get("ok") else 409)
                return
            m = re.fullmatch(r"/frames/([^/]+)/revert/operations", sub)
            if m and method == "GET":
                self._json(
                    {
                        "operations": runner.session_domain.revert_operations(
                            m.group(1),
                            branch_id=(q.get("branch_id") or [None])[0],
                        )
                    }
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/notebook/export", sub)
            if m and method == "GET":
                language = (q.get("language") or [None])[0]
                if language is not None and str(language).lower() not in {
                    "python",
                    "r",
                    "bundle",
                    # A reading form, not a re-running one: both languages in
                    # execution order in one document, for an issue or a
                    # methods section. It rides this route rather than a new
                    # one because it answers the same question about the same
                    # branch, and a second route would be a second place for
                    # "which cells belong to this branch" to be decided.
                    "markdown",
                }:
                    self._json(
                        {
                            "error": (
                                "notebook language must be python, r, bundle, "
                                "or markdown"
                            )
                        },
                        400,
                    )
                    return
                exported = runner.session_domain.notebook_export(
                    m.group(1), language=language
                )
                self._send(
                    200,
                    exported["data"],
                    exported["content_type"],
                    {
                        "Content-Disposition": (
                            f'attachment; filename="{exported["filename"]}"'
                        ),
                        "X-Content-SHA256": exported["sha256"],
                    },
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/session/export", sub)
            if m and method == "GET":
                exported = runner.session_domain.session_export(m.group(1))
                self._send(
                    200,
                    exported["data"],
                    exported["content_type"],
                    {
                        "Content-Disposition": (
                            f'attachment; filename="{exported["filename"]}"'
                        ),
                        "X-Content-SHA256": exported["sha256"],
                        "X-OpenAI4S-Session-Schema": str(exported["schema_version"]),
                    },
                )
                return
            # ---- kernel (extracted; see openai4s/server/kernel_routes.py) ----
            # Must stay here: after the frame_mutation guard above, which is the
            # only write-protection on the seven mutating routes in that module,
            # and after the workbench guard, which is what makes
            # GET /frames/{id}/execution 404 for an unknown session.
            if kernel_routes.handle(self, method, sub, q, runner, store):
                return

            # ---- artifacts ----
            if sub == "/renderers" and method == "GET":
                self._json({"renderers": runner.session_domain.renderer_catalog()})
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/renderer", sub)
            if m and method == "GET":
                # The domain layer signals these two the Python way; every
                # sibling route here turns them into a status. Without the
                # translation an unknown id left `KeyError` to reach the
                # catch-all and answered 500 — a client asking for an artifact
                # that is merely gone got a server error — and the route's
                # published contract was assembled from its *other* verbs'
                # dispatcher 404s, because the capture driver never saw this
                # verb answer at all.
                try:
                    self._json(
                        runner.session_domain.artifact_renderer(
                            m.group(1),
                            version_id=(q.get("version") or [None])[0],
                            root_frame_id=(q.get("root_frame_id") or [None])[0],
                        )
                    )
                except KeyError:
                    self._json({"error": "artifact not found"}, 404)
                except PermissionError:
                    self._json({"error": "artifact belongs to another session"}, 403)
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/lineage", sub)
            if m and method == "GET":
                self._json(self._lineage(m.group(1)))
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/environment", sub)
            if m and method == "GET":
                # Env snapshot bound to THIS artifact's production run (Provenance
                # → Environment). Falls back to a live freeze for artifacts with
                # no recorded snapshot (uploads / produced before this existed).
                vid = q.get("version", [None])[0]
                snap = store.env_snapshot_for_artifact(m.group(1), version_id=vid)
                if snap:
                    snap["source"] = "captured"
                else:
                    snap = _environment_snapshot()
                    snap["source"] = "live"
                self._json(snap)
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/priority", sub)
            if m and method in ("POST", "PUT", "PATCH"):
                artifact = store.get_artifact(m.group(1))
                if artifact and artifact.get("root_frame_id"):
                    _require_session_writable(
                        str(artifact["root_frame_id"]),
                        "changing Artifact priority",
                    )
                rec = store.set_priority(
                    m.group(1), int(self._body().get("priority", 0))
                )
                self._json(
                    {"ok": True, "artifact": _artifact_json(rec) if rec else None}
                )
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/versions", sub)
            if m and method == "GET":
                vs = store.list_versions(m.group(1))
                self._json(
                    {
                        "versions": [
                            {
                                "version_id": v["version_id"],
                                "ordinal": v["ordinal"],
                                "is_latest": v["is_latest"],
                                "size_bytes": v["size_bytes"],
                                "content_type": v["content_type"],
                                "checksum": v.get("checksum"),
                                "producing_cell_id": v.get("producing_cell_id"),
                                "created_at": _iso(v["created_at"]),
                                # Where retrieved data came from, allowlisted,
                                # bounded and redacted. Stored since retrieval
                                # provenance was added and never sent anywhere,
                                # so a figure built on a live API fetch looked
                                # exactly like one computed from nothing.
                                # Omitted entirely when there is none: most
                                # artifacts are computed, and an empty panel
                                # reads as a finding about the data.
                                **(
                                    {"retrieval_source": projected}
                                    if (
                                        projected := retrieval_source.public_source(
                                            v.get("source")
                                        )
                                    )
                                    else {}
                                ),
                            }
                            for v in vs
                        ]
                    }
                )
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/versions/([^/]+)/restore", sub)
            if m and method == "POST":
                res = self._restore_version(m.group(1), m.group(2))
                self._json(res, 404 if res.get("error") else 200)
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/edit", sub)
            if m and method in ("POST", "PUT", "PATCH"):
                self._json(
                    self._edit_artifact(m.group(1), self._body().get("content", ""))
                )
                return
            m = re.fullmatch(r"/artifacts/([^/]+)/rename", sub)
            if m and method in ("POST", "PUT", "PATCH"):
                self._json(
                    self._rename_artifact(m.group(1), self._body().get("filename"))
                )
                return
            m = re.fullmatch(r"/artifacts/([^/]+)", sub)
            if m and method == "DELETE":
                self._json(self._delete_artifact(m.group(1)))
                return
            m = re.fullmatch(r"/artifacts/(.+)", sub)
            if m and method == "GET":
                self._serve_artifact(m.group(1))
                return
            if sub == "/uploads" and method == "POST":
                self._json(self._upload(self._body()))
                return

            # ---- skills / customize panels ----
            if sub == "/skills/catalog" and method == "GET":
                self._json({"skills": self._skills_catalog(_disabled_skills)})
                return
            m = re.fullmatch(r"/projects/([^/]+)/skills/catalog", sub)
            if m and method == "GET":
                project_service = _project_skill_customization(unquote(m.group(1)))
                self._json(
                    {
                        "skills": [
                            item
                            for item in project_service.catalog()
                            if item.get("scope") == "project"
                        ]
                    }
                )
                return
            m = re.fullmatch(r"/skills/catalog/([^/]+)/enabled", sub)
            if m and method in ("PUT", "PATCH"):
                name = unquote(m.group(1))
                self._json(
                    skill_customization.set_enabled(
                        name,
                        self._body().get("enabled"),
                    )
                )
                return
            # ---- skill authoring (create / edit / import / delete) ----
            if sub == "/skills" and method == "POST":
                b = self._body()
                created = skill_customization.create_or_update(
                    b.get("name") or "",
                    b.get("description") or "",
                    b.get("body") or b.get("content") or "",
                )
                self._json(created, _skill_result_status(created))
                return
            if sub == "/skills/import" and method == "POST":
                b = self._body()
                imported = skill_customization.import_document(
                    content=b.get("content") or "",
                    name=b.get("name") or "",
                    description=b.get("description") or "",
                    body=b.get("body") or "",
                )
                self._json(imported, _skill_result_status(imported))
                return
            m = re.fullmatch(r"/skills/([^/]+)/versions", sub)
            if m and method == "GET":
                try:
                    limit = int((q.get("limit") or [50])[0])
                except (TypeError, ValueError):
                    raise GatewayError(400, "invalid Skill history limit")
                payload = _skill_history_payload(
                    skill_customization,
                    unquote(m.group(1)),
                    limit=limit,
                )
                self._json(payload, 404 if payload.get("error") else 200)
                return
            m = re.fullmatch(r"/skills/([^/]+)/rollback", sub)
            if m and method == "POST":
                version_id = str(self._body().get("version_id") or "").strip()
                if not version_id:
                    raise GatewayError(400, "version_id is required")
                payload = skill_customization.rollback(
                    unquote(m.group(1)),
                    version_id,
                )
                self._json(payload, 409 if payload.get("error") else 200)
                return
            m = re.fullmatch(r"/projects/([^/]+)/skills/([^/]+)/versions", sub)
            if m and method == "GET":
                try:
                    limit = int((q.get("limit") or [50])[0])
                except (TypeError, ValueError):
                    raise GatewayError(400, "invalid Skill history limit")
                project_service = _project_skill_customization(unquote(m.group(1)))
                payload = _skill_history_payload(
                    project_service,
                    unquote(m.group(2)),
                    limit=limit,
                )
                self._json(payload, 404 if payload.get("error") else 200)
                return
            m = re.fullmatch(r"/projects/([^/]+)/skills/([^/]+)/rollback", sub)
            if m and method == "POST":
                version_id = str(self._body().get("version_id") or "").strip()
                if not version_id:
                    raise GatewayError(400, "version_id is required")
                project_service = _project_skill_customization(unquote(m.group(1)))
                payload = project_service.rollback(
                    unquote(m.group(2)),
                    version_id,
                )
                self._json(payload, 409 if payload.get("error") else 200)
                return
            m = re.fullmatch(r"/skills/([^/]+)", sub)
            if m and sub not in ("/skills/catalog", "/skills/import"):
                name = unquote(m.group(1))
                if method == "GET":
                    fetched = skill_customization.get(name)
                    self._json(fetched, _skill_result_status(fetched))
                    return
                if method in ("PUT", "PATCH"):
                    b = self._body()
                    updated = skill_customization.create_or_update(
                        name,
                        b.get("description") or "",
                        b.get("body") or b.get("content") or "",
                        existing=True,
                    )
                    self._json(updated, _skill_result_status(updated))
                    return
                if method == "DELETE":
                    removed = skill_customization.delete(name)
                    self._json(removed, _skill_result_status(removed))
                    return
            # ---- agents ----
            if sub == "/agents" and method == "GET":
                self._json(self._agents_payload())
                return
            m = re.fullmatch(r"/agents/([^/]+)/enabled", sub)
            if m and method in ("PUT", "PATCH"):
                name = unquote(m.group(1))
                enabled = bool(self._body().get("enabled", True))
                state = store.set_capability_enabled(
                    "specialist",
                    name,
                    enabled,
                    scope="global",
                    metadata={"source": "web"},
                )
                self._json(
                    {
                        "ok": True,
                        "name": name,
                        "enabled": state["enabled"],
                        "scope": state["scope"],
                    }
                )
                return
            m = re.fullmatch(r"/agents/([^/]+)", sub)
            if m and method == "GET":
                name = unquote(m.group(1))
                for a in self._agents_payload():
                    if a["name"] == name:
                        self._json(a)
                        return
                self._json({"error": "unknown agent"}, 404)
                return

            # ---- specialists (user-defined agents) ----
            if sub == "/specialists" and method == "GET":
                self._json(
                    {
                        "builtin": store.specialist_profiles().filter_profiles(
                            _BUILTIN_AGENTS, include_disabled=True
                        ),
                        "specialists": store.list_agents(include_disabled=True),
                    }
                )
                return
            if sub == "/specialists" and method == "POST":
                b = self._body()
                nm = (b.get("name") or "").strip()
                if not nm:
                    self._json({"error": "name required"}, 400)
                    return
                self._json(
                    store.upsert_agent(
                        name=nm,
                        description=b.get("description") or "",
                        system_prompt=b.get("system_prompt") or "",
                        skill_names=b.get("skills"),
                        connectors=b.get("connectors"),
                        unrestricted=b.get("unrestricted", True),
                    )
                )
                return
            m = re.fullmatch(r"/specialists/([^/]+)", sub)
            if m:
                nm = unquote(m.group(1))
                if method == "GET":
                    a = store.get_agent(nm)
                    self._json(a or {"error": "not found"}, 200 if a else 404)
                    return
                if method in ("PUT", "PATCH"):
                    b = self._body()
                    # Partial: only what the body actually carries. This used
                    # to call `upsert_agent`, which writes every column, while
                    # the editor sends three of them -- so each edit wrote NULL
                    # over `skills` and `connectors` and reset `unrestricted`
                    # to True. A resource restriction silently became no
                    # restriction, which is the direction that matters.
                    fields: dict[str, Any] = {}
                    for key, column in (
                        ("description", "description"),
                        ("system_prompt", "system_prompt"),
                        ("skills", "skill_names"),
                        ("connectors", "connectors"),
                        ("unrestricted", "unrestricted"),
                    ):
                        if key in b:
                            fields[column] = b[key]
                    updated = store.update_agent(nm, **fields)
                    if updated is None:
                        raise GatewayError(404, "specialist not found")
                    self._json(updated)
                    return
                if method == "DELETE":
                    store.delete_agent(nm)
                    self._json({"ok": True})
                    return

            # ---- Doubao Search (primary, direct API, never falls back) ----
            if sub == "/doubao-search/config":
                if method == "GET":
                    self._json(_doubao_search_config_payload())
                    return
                if method == "POST":
                    body = self._body()
                    try:
                        _save_shared_agent_plan_key(body.get("agent_plan_key"))
                    except ValueError as error:
                        raise GatewayError(400, str(error)) from error
                    self._json({"ok": True, **_doubao_search_config_payload()})
                    return

            if sub == "/doubao-search/search" and method == "POST":
                body = self._body()
                query_value = body.get("query")
                if not isinstance(query_value, str):
                    raise GatewayError(400, "query must be a string")
                query = query_value.strip()
                if not query:
                    raise GatewayError(400, "query is required")
                if len(query) > 100:
                    raise GatewayError(400, "query exceeds 100 characters")
                num_results = body.get("num_results", 8)
                if type(num_results) is not int or not 1 <= num_results <= 50:
                    raise GatewayError(
                        400, "num_results must be an integer between 1 and 50"
                    )

                from openai4s.doubao_search import (
                    DoubaoSearchAuthError,
                    DoubaoSearchError,
                    DoubaoSearchService,
                )

                service = DoubaoSearchService(store)
                secret_before = datapro.resolve_agent_plan_key(store)
                try:
                    searched = service.search(query, num_results=num_results)
                except DoubaoSearchAuthError:
                    raise GatewayError(
                        401,
                        "豆包搜索鉴权失败；请检查 Agent Plan Key、额度或套餐权限。",
                        "doubao_search_auth_failed",
                    ) from None
                except DoubaoSearchError as error:
                    safe, status = public_exception(
                        error,
                        surface="doubao-search:search",
                        request_id=getattr(self, "_correlation_id", ""),
                        status=502,
                        error_code="doubao_search_failed",
                    )
                    self._json(safe, status)
                    return

                if not isinstance(searched, Mapping):
                    raise GatewayError(
                        502,
                        "豆包搜索返回了无效响应。",
                        "doubao_search_invalid_response",
                    )
                secret_after = datapro.resolve_agent_plan_key(store)
                safe_result: Any = dict(searched)
                safe_query: Any = query
                for secret in sorted(
                    {value for value in (secret_before, secret_after) if value},
                    key=len,
                    reverse=True,
                ):
                    safe_result = datapro.redact_secret(safe_result, secret)
                    safe_query = datapro.redact_secret(safe_query, secret)
                if not isinstance(safe_result, Mapping):
                    raise GatewayError(
                        502,
                        "豆包搜索返回了无效响应。",
                        "doubao_search_invalid_response",
                    )
                if searched.get("source") != "doubao":
                    # This dedicated product route must never disguise a
                    # fallback engine as a successful Doubao Search call.
                    raise GatewayError(
                        502,
                        "豆包搜索返回了非豆包来源的响应。",
                        "doubao_search_source_mismatch",
                    )
                raw_results = safe_result.get("results")
                if not isinstance(raw_results, list):
                    raise GatewayError(
                        502,
                        "豆包搜索返回了无效结果列表。",
                        "doubao_search_invalid_response",
                    )
                results = []
                for item in raw_results:
                    if not isinstance(item, Mapping):
                        continue
                    url_value = item.get("url")
                    if not isinstance(url_value, str):
                        continue
                    url = url_value.strip()
                    parsed_url = urlparse(url)
                    if (
                        parsed_url.scheme not in {"http", "https"}
                        or not parsed_url.netloc
                    ):
                        continue
                    title_value = item.get("title")
                    title = (
                        title_value.strip()
                        if isinstance(title_value, str) and title_value.strip()
                        else url
                    )
                    results.append({**dict(item), "title": title, "url": url})
                available = bool(results)
                self._json(
                    {
                        **dict(safe_result),
                        "query": safe_query,
                        "source": "doubao",
                        "count": len(results),
                        "results": results,
                        "available": available,
                        "message": (
                            "豆包搜索可用" if available else "豆包搜索未返回可用结果"
                        ),
                    }
                )
                return

            # ---- Volcengine DataPro (managed Streamable HTTP MCP) ----
            if sub == "/datapro/config":
                if method == "GET":
                    self._json(_datapro_config_payload())
                    return
                if method in ("POST", "PUT", "PATCH"):
                    body = self._body()
                    key = body.get("agent_plan_key")
                    try:
                        _save_shared_agent_plan_key(key)
                    except ValueError as error:
                        raise GatewayError(400, str(error)) from error

                    store.set_connector_enabled(datapro.CONNECTOR_ID, True)
                    self._json({"ok": True, **_datapro_config_payload()})
                    return

            if sub == "/datapro/search" and method == "POST":
                body = self._body()
                try:
                    query = datapro.validate_query(body.get("query"))
                except ValueError as error:
                    raise GatewayError(400, str(error)) from error
                connector = store.get_connector(datapro.CONNECTOR_ID)
                if connector is None:
                    raise GatewayError(503, "DataPro connector is not installed")
                if not connector.get("enabled"):
                    raise GatewayError(409, "DataPro connector is disabled")

                secret = datapro.resolve_agent_plan_key(store)
                if not secret:
                    raise GatewayError(400, "Agent Plan Key is not configured")

                frame_id = str(body.get("frame_id") or "").strip() or None
                if frame_id:
                    _require_canonical_session_root(frame_id)
                    _require_session_writable(frame_id, "saving a DataPro query result")

                from openai4s.mcp_client import manager

                receipt = None
                artifact = None
                try:
                    called = manager().call_tool(
                        datapro.CONNECTOR_ID,
                        datapro.connector_runtime_config(store, connector),
                        datapro.TOOL_NAME,
                        {"query": query},
                    )
                    result = datapro.public_search_result(called, secret)
                    source_result = datapro.redact_mcp_result(called, secret)
                    current_secret = datapro.resolve_agent_plan_key(store)
                    if current_secret and current_secret != secret:
                        result = datapro.redact_secret(result, current_secret)
                        source_result = datapro.redact_secret(
                            source_result, current_secret
                        )
                    safe_query = datapro.redact_secret(query, secret)
                    if current_secret and current_secret != secret:
                        safe_query = datapro.redact_secret(safe_query, current_secret)
                    receipt = datapro.index_successful_search(
                        store,
                        query=safe_query,
                        result=result,
                        frame_id=frame_id,
                        secrets=(secret, current_secret),
                        source_result=source_result,
                    )
                    if receipt is not None:
                        result = {**result, "index": receipt}
                    # Only a strict code-0 response is worth saving.  The upload
                    # used to be unconditional while the index was gated, so a
                    # transport-level success carrying e.g. code 4011 still wrote
                    # a `datapro-search-*.json` into the workspace and broadcast
                    # `artifact_created` -- the UI then rendered "已保存" beside
                    # the error text, and every retry left another dead file.
                    if datapro.is_successful_search(result):
                        artifact = runner.artifacts.upload(
                            datapro.result_artifact_payload(
                                query=safe_query,
                                result=result,
                                frame_id=frame_id,
                            )
                        )
                    # `receipt` can now be an explicit incomplete receipt with no
                    # batch_id (the index hit a capacity ceiling), so link only
                    # a batch that actually exists.
                    if (
                        isinstance(receipt, dict)
                        and receipt.get("batch_id")
                        and artifact
                        and artifact.get("id")
                    ):
                        store.link_datapro_index_artifact(
                            receipt["batch_id"], artifact["id"]
                        )
                except Exception as error:  # noqa: BLE001
                    # The product route promises that an available result is
                    # both indexed and saved. Compensate either half if upload
                    # or linking fails, so a 502 cannot leave a palette-visible
                    # ghost batch (or an orphaned result Artifact).
                    artifact_id = (
                        artifact.get("id") if isinstance(artifact, dict) else None
                    )
                    if artifact_id:
                        try:
                            runner.artifacts.delete(artifact_id)
                        except Exception:  # noqa: BLE001 - preserve root failure
                            pass
                    if isinstance(receipt, dict) and receipt.get("batch_id"):
                        try:
                            store.delete_datapro_index_batch(receipt["batch_id"])
                        except Exception:  # noqa: BLE001 - preserve root failure
                            pass
                    safe, status = public_exception(
                        error,
                        surface="datapro:search",
                        request_id=getattr(self, "_correlation_id", ""),
                        status=502,
                        error_code="datapro_failed",
                    )
                    self._json(safe, status)
                    return
                self._json({**result, "artifact": artifact})
                return

            # ---- connectors (MCP servers) ----
            if sub == "/connectors" and method == "GET":
                self._json({"connectors": self._connectors_payload(store)})
                return
            if sub == "/connectors" and method == "POST":
                b = self._body()
                nm = (b.get("name") or "").strip()
                cmd = b.get("command")
                if not nm or not cmd:
                    self._json({"error": "name and command required"}, 400)
                    return
                cid = b.get("connector_id") or _skill_slug(nm)
                if cid == datapro.CONNECTOR_ID:
                    raise GatewayError(403, "DataPro is a managed connector")
                # Drop any cached process first: it was spawned from the old
                # command/env and would keep serving from them. Only DELETE
                # disconnected, so editing a connector left the previous
                # configuration running and answering.
                from openai4s.mcp_client import manager as _mcp_manager

                _mcp_manager().disconnect(cid)
                # upsert_connector re-reads the row, so echoing its return value
                # replayed the env the client just sent straight back out.
                self._json(
                    public_connector(
                        store.upsert_connector(
                            connector_id=cid,
                            name=nm,
                            description=b.get("description") or "",
                            command=cmd,
                            args=b.get("args"),
                            env=b.get("env"),
                            enabled=b.get("enabled", True),
                        )
                    )
                )
                return
            if sub == "/connectors/directory" and method == "GET":
                self._json({"directory": _CONNECTOR_DIRECTORY})
                return
            m = re.fullmatch(r"/connectors/([^/]+)/enabled", sub)
            if m and method in ("PUT", "PATCH"):
                enabled = bool(self._body().get("enabled", True))
                store.set_connector_enabled(m.group(1), enabled)
                if not enabled:
                    # Disabling wrote the row and left the child running. A
                    # connector the user has switched off should not still be a
                    # live process holding whatever it holds.
                    if m.group(1) == datapro.CONNECTOR_ID:
                        # The process-wide manager may host another Store with
                        # its own DataPro account/session. A local disable must
                        # revoke only this Store generation's connection.
                        _disconnect_managed_datapro_session()
                    else:
                        from openai4s.mcp_client import manager as _mcp_manager

                        _mcp_manager().disconnect(m.group(1))
                self._json({"ok": True})
                return
            m = re.fullmatch(r"/connectors/([^/]+)/probe", sub)
            if m and method == "POST":
                if m.group(1) == datapro.CONNECTOR_ID:
                    raise GatewayError(
                        400,
                        "DataPro availability requires a real dataPro_search call",
                    )
                c = store.get_connector(m.group(1))
                if not c:
                    self._json({"error": "connector not found"}, 404)
                    return
                from openai4s.mcp_client import manager

                mcfg = datapro.connector_runtime_config(store, c)
                self._json(manager().probe(mcfg))
                return
            m = re.fullmatch(r"/connectors/([^/]+)/call", sub)
            if m and method == "POST":
                c = store.get_connector(m.group(1))
                if not c:
                    self._json({"error": "connector not found"}, 404)
                    return
                if not c.get("enabled"):
                    raise GatewayError(409, "connector is disabled")
                from openai4s.mcp_client import manager

                b = self._body()
                call_args = b.get("args") or {}
                frame_id = None
                if c["connector_id"] == datapro.CONNECTOR_ID:
                    frame_id = str(b.get("frame_id") or "").strip() or None
                    if frame_id:
                        _require_canonical_session_root(frame_id)
                        _require_session_writable(frame_id, "indexing a DataPro result")
                    if b.get("tool") != datapro.TOOL_NAME:
                        raise GatewayError(400, "DataPro only permits dataPro_search")
                    args = b.get("args")
                    if not isinstance(args, dict) or set(args) != {"query"}:
                        raise GatewayError(
                            400,
                            "dataPro_search requires exactly one string query",
                        )
                    try:
                        call_args = {"query": datapro.validate_query(args.get("query"))}
                    except ValueError as error:
                        raise GatewayError(400, str(error)) from error
                mcfg = datapro.connector_runtime_config(store, c)
                try:
                    secret_before = ""
                    if c["connector_id"] == datapro.CONNECTOR_ID:
                        secret_before = datapro.resolve_agent_plan_key(store)
                    result = manager().call_tool(
                        c["connector_id"], mcfg, b.get("tool"), call_args
                    )
                    if c["connector_id"] == datapro.CONNECTOR_ID:
                        secret_after = datapro.resolve_agent_plan_key(store)
                        result = datapro.redact_mcp_result(result, secret_before)
                        if secret_after and secret_after != secret_before:
                            result = datapro.redact_secret(result, secret_after)
                        receipt = datapro.index_successful_search(
                            store,
                            query=call_args["query"],
                            result=result,
                            frame_id=frame_id,
                            secrets=(secret_before, secret_after),
                        )
                        if receipt is not None:
                            result["index"] = receipt
                    self._json(result)
                except Exception as e:  # noqa: BLE001
                    # 502, not the 200 this answered with. An MCP server is a
                    # subprocess this daemon spawned and talked to; when that
                    # conversation fails the request did not succeed, and
                    # `api()` in app.js only rejects on a non-2xx -- so a
                    # connector that never ran was reported as one that did.
                    # The message was `str(e)` from a third-party server whose
                    # errors routinely quote the argv and env it was launched
                    # with, which is the launch command and its secrets.
                    body, status = public_exception(
                        e,
                        surface="connector:call",
                        request_id=getattr(self, "_correlation_id", ""),
                        status=502,
                        error_code="connector_failed",
                    )
                    self._json(body, status)
                return
            m = re.fullmatch(r"/connectors/([^/]+)", sub)
            if m and method == "DELETE":
                if m.group(1) == datapro.CONNECTOR_ID:
                    raise GatewayError(403, "DataPro is a managed connector")
                from openai4s.mcp_client import manager

                manager().disconnect(m.group(1))
                store.delete_connector(m.group(1))
                self._json({"ok": True})
                return

            # ---- compute / environment / kernel packages ----
            if sub == "/compute/gpu" and method == "GET":
                self._json(_detect_gpu())
                return
            if sub == "/compute/ssh-aliases" and method == "GET":
                self._json({"aliases": _ssh_config_aliases()})
                return
            if sub == "/compute/remote" and method == "GET":
                self._json(_remote_compute_info())
                return
            if sub == "/compute/remote" and method == "POST":
                from openai4s.compute import registry as _reg

                b = self._body()
                alias = (b.get("alias") or "").strip()
                if not alias:
                    self._json({"error": "alias required"}, 400)
                    return
                if alias not in _ssh_config_aliases():
                    self._json(
                        {
                            "error": f"'{alias}' is not a Host entry in your "
                            "~/.ssh/config — add it there first"
                        },
                        400,
                    )
                    return
                probe = _probe_remote_gpu(alias)
                _REMOTE_COMPUTE_CACHE[alias] = {**probe, "_ts": time.time()}
                _reg.add_host(
                    alias,
                    label=(b.get("label") or alias),
                    gpus=probe.get("gpus"),
                    gpu_count=probe.get("gpu_count", 0),
                )
                self._json(
                    {
                        "ok": True,
                        "alias": alias,
                        **probe,
                        "info": _remote_compute_info(),
                    }
                )
                return
            m = re.fullmatch(r"/compute/remote/([^/]+)", sub)
            if m and method == "DELETE":
                from openai4s.compute import registry as _reg

                self._json({"ok": _reg.remove_host(m.group(1))})
                return
            if sub == "/compute/providers" and method == "GET":
                self._json({"providers": self._compute_providers()})
                return
            if sub == "/compute/local/hostinfo" and method == "GET":
                self._json(_host_info())
                return
            # ---- compute jobs (submit / monitor / cancel) ----
            m = re.fullmatch(r"/frames/([^/]+)/delegations/([^/]+)/stop", sub)
            if m and method == "POST":
                fid, child_id = m.groups()
                self._json(runner.stop_delegation_subtree(fid, child_id))
                return
            m = re.fullmatch(r"/frames/([^/]+)/delegations/([^/]+)/steer", sub)
            if m and method == "POST":
                fid, child_id = m.groups()
                self._json(
                    runner.steer_delegation_child(
                        fid, child_id, (self._body() or {}).get("message") or ""
                    )
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/compute/tasks", sub)
            if m and method == "GET":
                # Read-only, owner-scoped, and it does not contact a remote.
                # That is structural rather than a promise: `compute_tasks`
                # takes a Store and has no import of ComputeManager, so there
                # is no code path from opening this page to probing a provider.
                # It matters because in this system the probe *is* the harvest
                # -- `result()` pulls files back and closes the job -- so a
                # self-refreshing page would harvest into a session nobody was
                # watching, on a schedule nobody chose.
                fid = m.group(1)
                if store.get_frame(fid) is None:
                    raise GatewayError(404, "session not found")
                self._json(
                    compute_tasks.owner_tasks(
                        store, str(runner.active_workspace_for(fid))
                    )
                )
                return
            m = re.fullmatch(r"/frames/([^/]+)/compute/tasks/([^/]+)/refresh", sub)
            if m and method == "POST":
                # The explicit action, and the only one that reaches a remote.
                # It harvests, which is why it is a POST a person has to press
                # rather than something the page does on a timer.
                fid, job_id = m.groups()
                if store.get_frame(fid) is None:
                    raise GatewayError(404, "session not found")
                self._json(runner.refresh_compute_task(fid, job_id))
                return
            if sub == "/compute/jobs" and method == "GET":
                self._json({"jobs": _jobs_mgr.list()})
                return
            if sub == "/compute/jobs" and method == "POST":
                b = self._body()
                self._json(
                    _jobs_mgr.submit(
                        b.get("command") or b.get("code") or "",
                        kind=b.get("kind") or "bash",
                        cwd=b.get("cwd"),
                        # Optional, and bounded by the manager. Omitting it
                        # takes the default deadline rather than the unbounded
                        # run this route used to give every caller.
                        deadline_s=b.get("deadline_s"),
                    )
                )
                return
            m = re.fullmatch(r"/compute/jobs/([^/]+)/cancel", sub)
            if m and method == "POST":
                self._json(_jobs_mgr.cancel(m.group(1)))
                return
            m = re.fullmatch(r"/compute/jobs/([^/]+)", sub)
            if m and method == "GET":
                self._json(_jobs_mgr.get(m.group(1)))
                return
            if sub == "/environments/status" and method == "GET":
                self._json(self._environments_status())
                return
            if sub == "/environments" and method == "GET":
                # The prebuilt runtime environments the notebook kernel can run in.
                self._json(runner.list_environments(None))
                return
            if sub == "/kernel/packages" and method == "GET":
                from openai4s.kernel import preinstall

                self._json(
                    {
                        "packages": preinstall.installed_report(),
                        "preinstall": preinstall.status(),
                    }
                )
                return
            if sub == "/kernel/environment" and method == "GET":
                # Full env freeze (all installed dists) for Provenance→Environment.
                self._json(_environment_snapshot())
                return
            if sub == "/kernel/install" and method == "POST":
                from openai4s.kernel import preinstall

                b = self._body()
                pkgs = b.get("packages") or ([b["package"]] if b.get("package") else [])
                self._json(preinstall.install(pkgs))
                return

            # ---- memory ----
            if sub == "/memory/enabled":
                if method == "GET":
                    self._json({"enabled": _memory_enabled(store), "override": None})
                    return
                if method in ("PUT", "PATCH", "POST"):
                    val = bool(self._body().get("enabled"))
                    store.set_setting("memory_enabled", "1" if val else "0")
                    self._json({"enabled": val})
                    return
            if sub.split("?")[0] == "/memory" and method == "GET":
                # Explicit or scoped: the cross-project view is a real
                # feature (Customize -> Memory asks for it by name), but
                # it must never be what a caller gets for saying nothing.
                pid = (q.get("project_id") or ["default"])[0]
                self._team_guard_memory_scope(pid)
                self._json(
                    {
                        "enabled": _memory_enabled(store),
                        "memories": store.list_memories(project_id=pid),
                    }
                )
                return
            if sub == "/telemetry/consent":
                from openai4s.telemetry import consent as _consent

                if method == "GET":
                    active = _consent.read(store)
                    self._json(
                        {
                            "enabled": active is not None,
                            # The environment can veto; say so, so the UI does
                            # not present a toggle that silently does nothing.
                            "env_locked": _consent.env_forbids(),
                        }
                    )
                    return
                if method in ("PUT", "PATCH", "POST"):
                    # Changing the recorded consent is a deliberate act by a
                    # person using this install; granting mints the anonymous
                    # id, revoking destroys it. Neither ever sends.
                    #
                    # The JSON type has to be Boolean, not merely truthy.
                    # `bool()` maps the string "false", `{}` with any key, and
                    # `[]` with any element onto True — so a form serialiser
                    # that sends `"false"`, or any client that does not read
                    # this contract closely, would *grant* telemetry consent
                    # while asking to revoke it. A privacy boundary must fail
                    # with a 400 rather than resolve an ambiguous request in
                    # the permissive direction.
                    want = self._body().get("enabled")
                    if not isinstance(want, bool):
                        self._json(
                            {
                                "error": "telemetry consent requires "
                                "'enabled' to be a JSON boolean",
                                "received_type": type(want).__name__,
                            },
                            400,
                        )
                        return
                    if want:
                        granted = _consent.grant(store)
                        self._json(
                            {
                                "enabled": granted is not None,
                                "env_locked": _consent.env_forbids(),
                            }
                        )
                    else:
                        _consent.revoke(store)
                        self._json({"enabled": False, "env_locked": False})
                    return
            if sub == "/memory" and method == "POST":
                b = self._body()
                scope = _memory_scope(store, b.get("project_id"))
                self._team_guard_memory_scope(scope)
                try:
                    # Refused before the row exists, not trimmed after: see
                    # MemoryRepository.add. The code travels so a client can
                    # tell "too long" from "this scope is full" without
                    # matching on English.
                    saved = store.add_memory(
                        content=b.get("content") or "",
                        block=b.get("block") or "general",
                        project_id=scope,
                    )
                except MemoryLimitError as error:
                    raise GatewayError(400, str(error), error.code) from error
                self._json(saved)
                return
            if sub in ("/memory/categories", "/memory/context") and method == "GET":
                # Explicit or scoped: the cross-project view is a real
                # feature (Customize -> Memory asks for it by name), but
                # it must never be what a caller gets for saying nothing.
                pid = (q.get("project_id") or ["default"])[0]
                self._team_guard_memory_scope(pid)
                if sub.endswith("categories"):
                    self._json({"categories": store.memory_blocks(project_id=pid)})
                else:
                    # Preview what is actually injected, budgets included.
                    # Joining every memory here showed a context the prompt
                    # never receives -- a preview that is wrong in the one
                    # direction that matters, since it is the surface a user
                    # checks precisely when they suspect something was lost.
                    resolved = store.resolve_memories(pid)
                    kept, dropped = memory_budget.select(resolved["memories"])
                    self._json(
                        {
                            "context": memory_budget.render(kept, dropped),
                            "included_count": len(kept),
                            "omitted": [
                                {
                                    "reason": item.get("reason"),
                                    "limit": item.get("limit"),
                                    "chars": item.get("chars"),
                                }
                                for item in dropped
                            ],
                            # Which scope this preview is for, and what the
                            # global tier contributed to it. A pane that shows
                            # only the merged text cannot distinguish "no such
                            # memory" from "this project overrode that block",
                            # and those call for opposite actions.
                            "project_id": pid,
                            "inherited_count": resolved["inherited"],
                            "overridden_count": resolved["overridden"],
                        }
                    )
                return
            # `[^/]+` matches `categories`, `context` and `enabled` too, and
            # those are sub-resources of `/memory`, not memory ids. Their GET
            # handlers run above, so a GET was always answered correctly -- but
            # every other verb fell through to here and was interpreted as an
            # operation on a memory called "categories". A `DELETE
            # /memory/categories` was answered "memory deletes require a
            # project_id" rather than 404, which reads as "supply one and this
            # will work"; it would not have, and the shape of the reply said it
            # would. Reserved names 404 like any unknown path.
            m = re.fullmatch(r"/memory/([^/]+)", sub)
            if m and m.group(1) in ("categories", "context", "enabled"):
                m = None
            if m and method == "PATCH":
                # Scoped exactly like the DELETE below, and for the same
                # reason: an id-only edit would rewrite a memory belonging to
                # whichever project happens to own it. Correcting standing
                # context used to mean delete-and-rewrite, which loses the
                # row's place in the newest-first order and can leave the user
                # with neither version if the second call hits the scope cap.
                b = self._body()
                scope = (q.get("project_id") or [""])[0].strip()
                if not scope:
                    raise GatewayError(
                        400,
                        "memory edits require a project_id query parameter "
                        f"({MEMORY_GLOBAL_SCOPE!r} or a project id)",
                        "memory_scope_required",
                    )
                # The same guard the three reading routes get. Unguarded, an
                # edit *rewrites* instance-wide standing context, and standing
                # context is injected into every other member's turns -- a
                # write here is a write into somebody else's prompts, which is
                # the sentence `may_use_memory_scope` exists to enforce.
                self._team_guard_memory_scope(scope)
                try:
                    edited = store.update_memory(
                        m.group(1),
                        content=b.get("content"),
                        block=b.get("block"),
                        project_id=scope,
                    )
                except MemoryLimitError as error:
                    raise GatewayError(400, str(error), error.code) from error
                if edited is None:
                    raise GatewayError(
                        404,
                        f"no memory {m.group(1)!r} in scope {scope!r}",
                        "memory_not_found",
                    )
                self._json(edited)
                return
            if m and method == "DELETE":
                # Scoped, and the scope is the caller's to state. An id-only
                # delete removes a memory from whichever project happens to own
                # it, so a stale tab listing another project's rows could delete
                # across the boundary and be answered {"ok": true} either way.
                scope = (q.get("project_id") or [""])[0].strip()
                if not scope:
                    raise GatewayError(
                        400,
                        "memory deletes require a project_id query parameter "
                        f"({MEMORY_GLOBAL_SCOPE!r}, a project id, or "
                        f"{MEMORY_ALL_PROJECTS!r} for the cross-project view)",
                        "memory_scope_required",
                    )
                # As above. The scope was the caller's to *state* and nobody's
                # to *check*: the cross-project view deletes by id with no
                # project predicate at all, and the ids come free with a read
                # of the caller's own project, because `resolve()` returns the
                # global tier alongside it. A member could delete standing
                # context they are not permitted to read.
                self._team_guard_memory_scope(scope)
                if not store.delete_memory(m.group(1), project_id=scope):
                    raise GatewayError(
                        404,
                        f"no memory {m.group(1)!r} in scope {scope!r}",
                        "memory_not_found",
                    )
                self._json({"ok": True})
                return

            # ---- network ----
            if sub == "/network/status":
                import os as _os

                if method in ("PUT", "PATCH", "POST"):
                    val = bool(self._body().get("enabled", True))
                    _os.environ["OPENAI4S_ALLOW_NETWORK"] = "1" if val else "0"
                    store.set_setting("network_enabled", "1" if val else "0")
                from openai4s import webtools

                self._json({"enabled": webtools.network_allowed()})
                return
            if sub == "/preferences/builtin-allowlist" and method == "GET":
                from openai4s import egress, webtools

                self._json(
                    {
                        "enabled": webtools.network_allowed(),
                        "egress_mode": egress.egress_mode(),
                        "granted": sorted(egress.granted_domains()),
                        "groups": _NETWORK_GROUPS,
                    }
                )
                return

            # ---- web-search API key (Tavily; endpoint is fixed) ----
            if sub == "/search/config":
                import os as _os

                if method in ("PUT", "PATCH", "POST"):
                    b = self._body()
                    if b.get("clear_api_key"):
                        store.set_secret_setting("tavily_api_key", "", scope="search")
                        _os.environ.pop("OPENAI4S_TAVILY_API_KEY", None)
                    else:
                        key = (b.get("api_key") or "").strip()
                        if key:
                            store.set_secret_setting(
                                "tavily_api_key", key, scope="search"
                            )
                            _os.environ["OPENAI4S_TAVILY_API_KEY"] = key
                configured = bool(
                    (_os.environ.get("OPENAI4S_TAVILY_API_KEY") or "").strip()
                    # A reference is truthy but is not a key; ask the broker
                    # whether one is actually stored.
                    or (store.get_secret_setting("tavily_api_key") or "").strip()
                )
                self._json(
                    {
                        "endpoint": "https://api.tavily.com/search",
                        "api_key_configured": configured,
                    }
                )
                return

            self._json({"error": "not found", "path": sub, "method": method}, 404)

        # ---- payload builders ------------------------------------------
        def _models_payload(self) -> dict:
            return model_profiles.models_payload(_default_model["id"])

        def _mask_profile(self, p: dict) -> dict:
            return model_profiles.public_profile(p)

        def _model_profiles_payload(self) -> dict:
            # `profiles_payload` returns `(payload, None)` unconditionally, so
            # the branch that used to live here never ran. It was the intended
            # repair for the drift above, which is now fixed where the drift
            # started -- at the seed -- rather than by a later route happening
            # to be visited. Keeping a dead correction reads as coverage.
            payload, _ = model_profiles.profiles_payload()
            return payload

        def _skills_catalog(self, disabled: set[str]) -> list[dict]:
            return skill_customization.catalog(disabled)

        def _agents_payload(self) -> list[dict]:
            out = []
            capability_state = store.capability_state()
            for a in _BUILTIN_AGENTS:
                out.append(
                    {
                        **a,
                        "enabled": capability_state.is_enabled("specialist", a["name"]),
                        "parameters": {},
                        "systemPrompt": None,
                        "userHidden": False,
                        "skillsLocked": False,
                    }
                )
            # merge any user-defined agents persisted in the store
            try:
                for r in store.list_agents(include_disabled=True):
                    if r.get("name") in {x["name"] for x in out}:
                        continue
                    out.append(
                        {
                            "name": r["name"],
                            "description": r.get("description") or "",
                            "mode": "subagent",
                            "healthy": True,
                            "source": "custom",
                            "supportsPlanMode": False,
                            "unrestricted": bool(r.get("unrestricted", 1)),
                            "enabled": capability_state.is_enabled(
                                "specialist", r["name"]
                            ),
                            "parameters": {},
                            "systemPrompt": None,
                        }
                    )
            except Exception:  # noqa: BLE001 - custom agents are optional
                pass
            return out

        def _connectors_payload(self, store) -> list[dict]:
            # Cheap: return stored connectors (no probe — probing spawns a
            # process; the UI probes on demand). Mark the argv for display.
            #
            # Projected, never spread: a connector's `env` holds the credentials
            # its MCP server is launched with, and `{**c}` handed every one of
            # them to the browser.
            out = []
            for c in store.list_connectors():
                cmd = c.get("command")
                managed_datapro = c.get("connector_id") == datapro.CONNECTOR_ID
                if managed_datapro:
                    display = "Streamable HTTP · " + datapro.ENDPOINT
                else:
                    display = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
                out.append(
                    {
                        **public_connector(c),
                        "command_display": display,
                        "managed": managed_datapro,
                        "transport": (
                            "streamable_http" if managed_datapro else "stdio"
                        ),
                    }
                )
            return out

        def _compute_providers(self) -> list[dict]:
            provs = [
                {
                    "name": "local",
                    "kind": "local",
                    "healthy": True,
                    "description": "This machine's CPU kernel (default).",
                }
            ]
            try:
                disp = build_dispatcher(cfg, frame_id="_probe")
                if disp._compute_available():  # noqa: SLF001
                    for p in disp.compute.list_providers():  # type: ignore[attr-defined]
                        provs.append({"name": p, "kind": "remote", "healthy": True})
            except Exception:  # noqa: BLE001 - providers are optional
                pass
            return provs

        def _environments_status(self) -> dict:
            from openai4s.kernel import preinstall

            report = preinstall.installed_report()
            ready = sum(1 for r in report if r["installed"])
            pstat = preinstall.status()
            return {
                "environments": [
                    {
                        "language": "python",
                        "status": (
                            "installing"
                            if pstat.get("phase") == "installing"
                            else "ready"
                        ),
                        "python_version": _host_info().get("python"),
                        "package_count": ready,
                        "packages": report,
                        "preinstall": pstat,
                    }
                ]
            }

        def _exec_log(self, root_frame_id: str) -> dict:
            return execution_views.execution_log(
                root_frame_id,
                branch_id=runner.store.active_session_branch(root_frame_id),
            )

        def _lineage(self, artifact_id: str) -> dict:
            return execution_views.artifact_lineage(artifact_id)

        def _edit_artifact(self, artifact_id: str, content: str) -> dict:
            try:
                artifact = store.get_artifact(artifact_id)
                if artifact and artifact.get("root_frame_id"):
                    _require_session_writable(
                        str(artifact["root_frame_id"]), "editing an Artifact"
                    )
                return runner.artifacts.edit(
                    artifact_id,
                    content,
                    broadcast=lambda root_frame_id, event: hub.broadcast(
                        root_frame_id, event
                    ),
                )
            except ArtifactOperationError as error:
                raise GatewayError(error.code, error.message) from error

        def _restore_version(self, artifact_id: str, version_id: str) -> dict:
            artifact = store.get_artifact(artifact_id)
            if artifact and artifact.get("root_frame_id"):
                _require_session_writable(
                    str(artifact["root_frame_id"]), "restoring an Artifact"
                )
            return runner.restore_version(artifact_id, version_id)

        def _rename_artifact(self, artifact_id: str, filename: str | None) -> dict:
            try:
                artifact = store.get_artifact(artifact_id)
                if artifact and artifact.get("root_frame_id"):
                    _require_session_writable(
                        str(artifact["root_frame_id"]), "renaming an Artifact"
                    )
                return runner.artifacts.rename(
                    artifact_id,
                    filename,
                    broadcast=lambda root_frame_id, event: hub.broadcast(
                        root_frame_id, event
                    ),
                )
            except ArtifactOperationError as error:
                raise GatewayError(error.code, error.message) from error

        def _upload(self, b: dict) -> dict:
            try:
                frame_id = b.get("frame_id")
                if frame_id:
                    frame = store.get_frame(str(frame_id)) or {}
                    _require_session_writable(
                        str(frame.get("root_frame_id") or frame_id),
                        "uploading a Session Artifact",
                    )
                return runner.artifacts.upload(
                    b,
                    broadcast=lambda root_frame_id, event: hub.broadcast(
                        root_frame_id, event
                    ),
                )
            except ArtifactOperationError as error:
                raise GatewayError(error.code, error.message) from error

        def _delete_artifact(self, artifact_id: str) -> dict:
            try:
                artifact = store.get_artifact(artifact_id)
                if artifact and artifact.get("root_frame_id"):
                    _require_session_writable(
                        str(artifact["root_frame_id"]), "deleting an Artifact"
                    )
                return runner.artifacts.delete(
                    artifact_id,
                    broadcast=lambda root_frame_id, event: hub.broadcast(
                        root_frame_id, event
                    ),
                )
            except ArtifactOperationError as error:
                raise GatewayError(error.code, error.message) from error

        # ---- websocket --------------------------------------------------
        def _handle_ws(self) -> None:
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self._json({"error": "expected websocket"}, 400)
                return
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", _ws_accept(key))
            self.end_headers()
            try:
                self.wfile.flush()
            except OSError:
                return
            conn = WSConnection(self.wfile)
            # Team mode (M1-7): the identity is RE-RESOLVED from the persisted
            # handshake headers on every message, not captured once. A member
            # who is disabled or whose password is reset (both delete their
            # auth_sessions) then fails resolution mid-connection — so a stale
            # long-lived socket cannot keep subscribing to new sessions or
            # cancelling executions with authority its owner no longer has.

            def _ws_user_now() -> dict | None:
                if _team_auth is None:
                    return None
                identity = self._team_identity_from_request()
                self._team_identity = identity  # keep audit helpers coherent
                return self._team_identity_dict() if identity is not None else None

            def _ws_session_visible(rid: str) -> bool:
                if _team_auth is None:
                    return True
                user = _ws_user_now()
                if user is None:
                    # identity revoked since upgrade: deny everything
                    return False
                frame = store.get_frame(rid)
                if frame is None:
                    # An unknown id must not be pre-subscribable: frame ids
                    # are random, and a lucky guess parked on a future
                    # session would stream it from its first event.
                    return bool(
                        user.get("role") == "admin" or user.get("kind") == "service"
                    )
                root = frame.get("root_frame_id") or rid
                return store.team.session_visible_to(str(root), user)

            hub.add(conn)
            try:
                while conn.alive:
                    frame = _ws_read_frame(self.rfile)
                    if frame is None:
                        break
                    opcode, data = frame
                    if opcode == 0x8:  # close
                        break
                    if opcode == 0x9:  # ping -> pong
                        conn.send_raw(data, 0xA)
                        continue
                    if opcode not in (0x1, 0x2):
                        continue
                    try:
                        msg = json.loads(data.decode("utf-8") or "{}")
                    except (ValueError, UnicodeDecodeError):
                        continue
                    t = msg.get("type")
                    if t == "ping":
                        conn.send_json({"type": "pong"})
                    elif t == "view_session":
                        rid = msg.get("root_frame_id") or msg.get("frame_id")
                        if rid and not _ws_session_visible(str(rid)):
                            # Same sentence as the HTTP guard's 404: which
                            # sessions exist is itself protected (INV-13).
                            # Refused before hub.subscribe, so the replay
                            # buffer, pending-approval prompts, and the
                            # queue snapshot below are all behind this one
                            # check.
                            conn.send_json(
                                {
                                    "type": "view_denied",
                                    "frame_id": rid,
                                    "reason": "session not found",
                                }
                            )
                            continue
                        if rid and _team_auth is not None:
                            # A live subscription is a view too (D4): an
                            # admin watching a private session leaves the
                            # same audit row as an HTTP read.
                            self._team_audit_admin_private_read(str(rid))
                        if rid:
                            # Subscription and replay share the hub's enqueue
                            # order with live broadcasts, so a new Cell event
                            # can never interleave into an older snapshot.
                            #
                            # `since_seq` is the resume cursor: a client that
                            # dropped mid-turn sends the highest seq it actually
                            # rendered and gets only what it missed, instead of
                            # re-receiving the whole turn and having to
                            # de-duplicate it. Absent or 0 means "send
                            # everything buffered", which is the old behaviour.
                            try:
                                since_seq = int(msg.get("since_seq") or 0)
                            except (TypeError, ValueError):
                                since_seq = 0
                            # The epoch the client last saw. A cursor is only
                            # meaningful within the daemon run that issued it.
                            client_epoch = msg.get("epoch")
                            hub.subscribe(
                                rid,
                                conn,
                                max(0, since_seq),
                                str(client_epoch) if client_epoch else None,
                            )
                            # re-surface any tool-call approval prompt that is
                            # still pending, so a mid-pause reconnect can answer.
                            try:
                                from openai4s.permissions import broker

                                for ev in broker().pending_events(rid, store=store):
                                    conn.send_json(ev)
                            except Exception:  # noqa: BLE001
                                pass
                            snapshot = runner.executions.snapshot(rid)
                            conn.send_json(
                                {
                                    "type": "execution_queue",
                                    "frame_id": rid,
                                    **snapshot,
                                }
                            )
                    elif t in {"cancel_execution", "cancel"}:
                        rid = msg.get("root_frame_id") or msg.get("frame_id")
                        if not rid:
                            conn.send_json(
                                {
                                    "type": "execution_cancel_result",
                                    "ok": False,
                                    "reason": "root_frame_id is required",
                                }
                            )
                            continue
                        if not _ws_session_visible(str(rid)):
                            # The one WS inbound that mutates: cancelling
                            # someone else's running turn is a write, and it
                            # answers exactly like an unknown session.
                            conn.send_json(
                                {
                                    "type": "execution_cancel_result",
                                    "ok": False,
                                    "reason": "session not found",
                                }
                            )
                            continue
                        result = runner.cancel(
                            rid,
                            msg.get("execution_id"),
                            owner=msg.get("owner") or msg.get("owner_kind"),
                            owner_id=msg.get("owner_id"),
                            reason=msg.get("reason") or "cancelled over websocket",
                        )
                        conn.send_json({"type": "execution_cancel_result", **result})
                    elif t == "unview_session":
                        rid = msg.get("root_frame_id") or msg.get("frame_id")
                        if rid:
                            hub.unsubscribe(rid, conn)
            finally:
                conn.close()  # stop the writer thread + mark dead
                hub.remove(conn)

    # Published on the class so `server_close` can reach it. The manager owns
    # real process trees, and it lived only in this closure -- so the daemon
    # exiting left them running, reparented, with nothing recording that they
    # existed. The server owns the socket and the runner; it has to own this
    # too, which is what P0-3 means by "server-owned close".
    Handler.jobs_manager = _jobs_mgr

    return Handler


# --------------------------------------------------------------------------- #
#  JSON serializers (module-level so both handler + tests can use them)
# --------------------------------------------------------------------------- #
def _frame_json(f: dict | None, store: Store) -> dict:
    if not f:
        return {}
    fid = f["frame_id"]
    return {
        "id": fid,
        "root_frame_id": f.get("root_frame_id") or fid,
        "parent_frame_id": f.get("parent_id"),
        "project_id": f.get("project_id"),
        "name": f.get("name"),
        "task_summary": f.get("task_summary"),
        "model": f.get("model"),
        "status": f.get("status"),
        "folder_id": f.get("folder_id"),
        "conversation_type": "agent",
        "message_count": store.message_count(fid),
        "input_tokens": f.get("input_tokens"),
        "output_tokens": f.get("output_tokens"),
        "created_at": _iso(f.get("created_at")),
        "updated_at": _iso(f.get("updated_at")),
    }


def _project_json(p: dict) -> dict:
    if not p:
        return {}
    return {
        "project_id": p["project_id"],
        "id": p["project_id"],
        "name": p.get("name"),
        "description": p.get("description"),
        "context": p.get("context"),
        "conversation_count": p.get("conversation_count", 0),
        "last_active_at": _iso(p.get("last_active_at") or p.get("updated_at")),
        "created_at": _iso(p.get("created_at")),
        "updated_at": _iso(p.get("updated_at")),
        "is_example": bool(p.get("is_example")),
    }


def _message_failure(message: dict) -> dict | None:
    """The failure identity stored on one message, projected safely.

    Same allowlist discipline as `_message_artifact_refs`, and for the same
    reason -- but this one exists because a reopened session had no way to
    recover either fact. The socket event that carried them is gone once the
    tab closes, and the row's prose is a sentence, not an id. So a user coming
    back to a failed turn could neither quote a support id nor learn that
    retrying would re-run a tool that already ran.

    Three scalars, each already published on the live surfaces. Never the
    exception, never a path.
    """
    raw = message.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    failure = raw.get("failure")
    if not isinstance(failure, dict):
        return None
    out: dict = {}
    request_id = failure.get("request_id")
    if isinstance(request_id, str) and request_id:
        out["request_id"] = request_id
    code = failure.get("code")
    if isinstance(code, str) and code:
        out["code"] = code
    # Only ever True, the same contract the wire carries.
    if failure.get("output_committed") is True:
        out["output_committed"] = True
    return out or None


def _message_artifact_refs(message: dict) -> list[dict]:
    """The structured references stored on one message, projected safely.

    An allowlist rather than the raw blob: the metadata column is shared, and
    handing a client everything anyone ever stamped on a message is how an
    internal field becomes a published contract by accident.
    """
    raw = message.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, dict):
        return []
    refs = raw.get("artifact_refs")
    if not isinstance(refs, list):
        return []
    projected: list[dict] = []
    for ref in refs[:8]:
        if not isinstance(ref, dict):
            continue
        row = {
            "artifact_id": str(ref.get("artifact_id") or ""),
            "version_id": str(ref.get("version_id") or ""),
            "sha256": str(ref.get("sha256") or ""),
            "display_name": str(ref.get("display_name") or ""),
            "source_session": str(ref.get("source_session") or ""),
            "sent_bytes": int(ref.get("sent_bytes") or 0),
            "materialized_target": (
                str(ref["materialized_target"])
                if ref.get("materialized_target")
                else None
            ),
        }
        # Only when true, mirroring the record: an absent key is "not
        # truncated", and every untruncated ref keeps the shape it had. Without
        # this pair the fact that the model was handed a partial file dies with
        # the WebSocket event, which is the hole `_message_failure` above was
        # written to close for failures.
        if ref.get("truncated"):
            row["truncated"] = True
        projected.append(row)
    return projected


def _artifact_json(a: dict) -> dict:
    return {
        "id": a["artifact_id"],
        "artifact_id": a["artifact_id"],
        "filename": a.get("filename"),
        "content_type": a.get("content_type"),
        "size_bytes": a.get("size_bytes"),
        "version_id": a.get("latest_version_id"),  # UI cache-bust key on overwrite
        "checksum": a.get("checksum"),
        "project_id": a.get("project_id"),
        "root_frame_id": a.get("root_frame_id"),
        "priority": a.get("priority", 0),
        "created_at": _iso(a.get("created_at")),
        # True when the user uploaded this file (vs. produced by a code cell), so
        # the UI can label it "uploaded" instead of "generated".
        "is_user_upload": bool(a.get("is_user_upload", 0)),
    }


def _note_json(n: dict) -> dict:
    return {
        "note_id": n.get("note_id"),
        "id": n.get("note_id"),
        "content": n.get("content"),
        "created_at": _iso(n.get("created_at")),
        "updated_at": _iso(n.get("updated_at") or n.get("created_at")),
    }


def _annotation_json(a: dict | None) -> dict | None:
    if not a:
        return None
    return {
        "id": a["annotation_id"],
        "annotation_id": a["annotation_id"],
        "root_frame_id": a.get("root_frame_id"),
        "artifact_id": a.get("artifact_id"),
        "artifact_name": a.get("artifact_name"),
        "x": a.get("rel_x"),
        "y": a.get("rel_y"),
        "number": a.get("number"),
        "body": a.get("body"),
        "status": a.get("status", "open"),
        # The version this pin was taken against, so a client can tell a pin on
        # the figure now on screen from one taken before the agent re-plotted.
        "version_id": a.get("version_id"),
        "created_at": _iso(a.get("created_at")),
        "updated_at": _iso(a.get("updated_at") or a.get("created_at")),
    }


#: Magic numbers, not filenames. A pinned artifact holds whatever the cell wrote
#: to that path: `figure.png` containing a PDF used to reach PIL and be dropped
#: with no reason given, while a genuine PNG written as `figure.dat` was skipped
#: because its extension was not on a list. Neither the extension nor the
#: recorded content_type is evidence about bytes -- both are declarations.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_mime(raw: bytes) -> str | None:
    """Return the MIME type these BYTES are, or None if they are not a raster."""
    for magic, mime in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    # "BM" on its own is two bytes and matches plenty of ordinary text, so the
    # BMP header's own total-size field is checked against the real length.
    if raw[:2] == b"BM" and len(raw) >= 26:
        if int.from_bytes(raw[2:6], "little") == len(raw):
            return "image/bmp"
    return None


def _pinned_image_bytes(store, pins: list) -> tuple[bytes | None, dict | None]:
    """Read the exact artifact VERSION a pin was taken against.

    A pin is a statement about one picture: the user clicked a point on the
    image that was in front of them. Resolving `artifact_id` at send time
    answered "whatever that file holds now" instead, so an agent that re-plotted
    between the pin and the send changed what the model received while the pin
    coordinates still described the old figure -- wrong rather than absent, and
    invisible to everyone involved.

    So the annotation records `version_id` + `checksum` when it is created and
    this reads *that* version: its immutable snapshot when one exists, otherwise
    the live path verified against the recorded checksum. A live file that no
    longer hashes to what was pinned is refused, never substituted.

    Returns ``(raw_bytes, None)`` or ``(None, problem)`` -- exactly one is set.
    The problem is the dict the UI card and the model note both read, so a
    refusal carries its own numbers rather than being reconstructed by either.
    """
    head = pins[0] if pins else {}
    version_id = str((head or {}).get("version_id") or "")
    checksum = str((head or {}).get("checksum") or "")
    # Annotations pinned before the binding columns existed carry neither, and
    # nothing can reconstruct which version they meant. Refusing them would
    # discard a user's pending pins on upgrade, so they keep the old
    # artifact-latest resolution -- the only case where "whatever it holds now"
    # is still the best available answer.
    ident = version_id or str((head or {}).get("artifact_id") or "")
    if not ident:
        return None, {"reason": "not_found"}
    path = store.resolve_artifact_path(ident)
    if not path:
        return None, {"reason": "not_found"}
    try:
        size = os.path.getsize(path)
        if size > MAX_SOURCE_IMAGE_BYTES:
            return None, {
                "reason": "too_large",
                "bytes": size,
                "limit": MAX_SOURCE_IMAGE_BYTES,
            }
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        # Deleted, moved, or unreadable underneath the pin.
        return None, {"reason": "not_found"}
    if checksum and hashlib.sha256(raw).hexdigest() != checksum:
        return None, {"reason": "version_changed"}
    if _sniff_image_mime(raw) is None:
        return None, {"reason": "unsupported_type"}
    return raw, None


def _figure_with_pins(raw: bytes, pins: list) -> tuple[str | None, str]:
    """Composite a numbered red marker at each pin's (rel_x, rel_y) onto a COPY
    of the figure; return (base64_png, "image/png"). The original file is never
    touched -- and is never re-opened either: these are the bytes already
    verified against the pinned version's checksum, so nothing can change
    between the check and the draw. Returns (None, "") if PIL is unavailable or
    the bytes will not decode."""
    try:
        from PIL import Image, ImageDraw

        with Image.open(io.BytesIO(raw)) as _src:
            im = _src.convert("RGB")
    except Exception:  # noqa: BLE001 — missing PIL / undecodable → reported
        return None, ""
    draw = ImageDraw.Draw(im)
    w, h = im.size
    r = max(9, int(min(w, h) * 0.02))
    lw = max(2, r // 4)
    red = (214, 40, 40)
    for a in pins:
        x = float(a.get("rel_x") or 0) * w
        y = float(a.get("rel_y") or 0) * h
        draw.ellipse([x - r, y - r, x + r, y + r], outline=red, width=lw)
        draw.line([x - r, y, x + r, y], fill=red, width=max(1, lw // 2))
        draw.line([x, y - r, x, y + r], fill=red, width=max(1, lw // 2))
        draw.text((x + r + 3, y - r - 2), str(a.get("number") or ""), fill=red)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/png"


def _format_annotations_block(annos: list) -> str:
    """Render pinned image annotations as a compact feedback block the agent can
    act on: which file, where on it (fraction + rough zone), and the comment.
    The actual marked-up figure rides along as an image part (see
    _build_annotated_content); this text is the instructions + comments."""
    annos = [a for a in (annos or []) if a]
    if not annos:
        return ""

    def _zone(x: float, y: float) -> str:
        col = "左" if x < 0.34 else ("中" if x < 0.67 else "右")
        row = "上" if y < 0.34 else ("中" if y < 0.67 else "下")
        return row + col  # e.g. 上右 / 中中

    # group by artifact so the agent sees one file at a time
    by_art: dict = {}
    for a in annos:
        by_art.setdefault(
            (a.get("artifact_id"), a.get("artifact_name") or "artifact"), []
        ).append(a)
    lines = [
        "【图像标注反馈】用户直接在生成的图像上用图钉标注了修改意见。"
        "本条消息随附了标注后的图像（红色圆圈=图钉位置，圈内数字=下列标注编号）——"
        "请先看图、对照红圈确认要改的元素，再修改并重新出图：",
        "1) 先定位生成下述图像的代码——查看本会话此前的代码单元与工作区文件；"
        "若不确定，用 host.glob/host.grep 按文件名或绘图关键字（savefig/plt/matplotlib）搜索。"
        "自动截图名形如 figure_cellN_*.png，其中 N 是生成它的代码单元序号。",
        "2) 逐条应用标注意见。以随附图上的红圈为准定位对应的子图/柱子/标签/元素；"
        "文字里的百分比坐标 (x 向右, y 向下) 仅作辅助。",
        "3) 重新运行绘图代码，覆盖写回同名图像文件（不要改文件名），确保每条改动在新图上可见；完成后简述改了什么。",
        "需要修改的图像：",
    ]
    for (art_id, name), items in by_art.items():
        mm = re.search(r"figure_cell(\d+)_", str(name or ""))
        cell = f"（由本会话第 {mm.group(1)} 个代码单元生成）" if mm else ""
        lines.append(f"• {name}{cell}")
        for a in sorted(items, key=lambda r: r.get("number") or 0):
            x = float(a.get("rel_x") or 0)
            y = float(a.get("rel_y") or 0)
            lines.append(
                f"    [{a.get('number')}] (x={x * 100:.0f}%, y={y * 100:.0f}%，"
                f"{_zone(x, y)}区)：{a.get('body', '').strip()}"
            )
    return "\n".join(lines)


_KETCHER_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Ketcher</title></head><body style="font:14px system-ui;padding:2rem;color:#444">
<p>Chemical structure editor placeholder. Bundle Ketcher assets here to enable
in-browser structure drawing.</p></body></html>"""


# --------------------------------------------------------------------------- #
#  server bootstrap
# --------------------------------------------------------------------------- #
class _GatewayHTTPServer(ThreadingHTTPServer):
    """HTTP server whose resource close also closes every SessionRunner slot."""

    def __init__(self, *args, runner: SessionRunner, **kwargs) -> None:
        self.runner = runner
        super().__init__(*args, **kwargs)

    def server_close(self) -> None:
        try:
            self.runner.close()
        finally:
            try:
                # The process-wide MCP manager caches live connections by id.
                # Close it with this Store generation so a later in-process
                # daemon cannot reuse a DataPro header-provider closure bound
                # to the previous SecretBroker (and therefore its credential).
                from openai4s.mcp_client import manager as _mcp_manager

                _mcp_manager().shutdown()
            finally:
                try:
                    # Local jobs are process groups this daemon started. Closed
                    # in its own `finally` so either runner or MCP teardown
                    # cannot leave them orphaned, and after the runner because
                    # a cell may still be watching one.
                    manager = getattr(self.RequestHandlerClass, "jobs_manager", None)
                    if manager is not None:
                        manager.close()
                finally:
                    super().server_close()


def build_app_server(cfg: Config | None = None) -> ThreadingHTTPServer:
    cfg = cfg or get_config()
    cfg.ensure_dirs()
    # Report what the scientific stack is missing; do NOT install it. Starting
    # the daemon must not mutate the user's Python environment — this used to
    # call ensure_core(background=True), which resolved ~23 unpinned names
    # against PyPI and installed them with --break-system-packages on a thread
    # nobody was watching. The UI surfaces the plan (Customize → Compute) and
    # `openai4s setup` applies it.
    try:
        from openai4s.kernel import preinstall

        plan = preinstall.core_plan()
        if plan["missing"]:
            print(
                f"[openai4s] {len(plan['missing'])} scientific package(s) are not "
                f"installed: {', '.join(plan['missing'][:6])}"
                f"{' …' if len(plan['missing']) > 6 else ''}\n"
                f"[openai4s] startup does not install packages. Run "
                f"`openai4s setup`, or install from Customize → Compute.",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 - diagnostics must never block startup
        traceback.print_exc()
    hub = WSHub()
    runner = SessionRunner(cfg, hub)
    # Seed the security-first permission defaults once (idempotent).
    try:
        get_store(cfg.db_path).seed_default_permission_rules()
    except Exception:  # noqa: BLE001 - seeding must never block startup
        traceback.print_exc()
    try:
        _migrate_legacy_provider(cfg)
    except Exception:  # noqa: BLE001 - migration must never block startup
        traceback.print_exc()
    # Load a UI-saved web-search (Tavily) key into the env webtools reads, unless
    # an explicit env/.env value is already set (which wins).
    # Move any plaintext credential out of the database and behind a broker
    # reference. Ordered write -> verify -> replace, so an interruption leaves
    # the old plaintext authoritative and the next start retries; a key that
    # cannot be migrated keeps working as plaintext rather than being lost.
    try:
        from openai4s.security.secret_migration import (
            migrate_connector_env,
            migrate_settings_secrets,
        )

        _store = get_store(cfg.db_path)
        _report = migrate_settings_secrets(_store, _store.secrets)
        if _report.migrated:
            print(
                f"[openai4s] moved {len(_report.migrated)} credential(s) into "
                f"{_store.secrets.posture()['backend']}: "
                f"{', '.join(_report.migrated)}",
                file=sys.stderr,
            )
        if _report.failed:
            print(
                "[openai4s] one or more settings credentials could not be "
                "migrated — they remain stored in plaintext",
                file=sys.stderr,
            )
        if _report.reentry_required:
            print(
                "[openai4s] one or more settings credentials must be saved "
                "again: their legacy system credentials have no Store namespace "
                "and were not read",
                file=sys.stderr,
            )

        # Each saved model profile carries its own key inside the
        # model_profiles blob; the active one is only mirrored into
        # llm_api_key, so migrating that alone would leave every other
        # configured endpoint's key in the clear.
        _profiles = ModelProfileService(_store, cfg, providers=lambda: PROVIDERS)
        _pr = _profiles.migrate_profile_keys()
        if _pr["migrated"]:
            print(
                f"[openai4s] moved {len(_pr['migrated'])} model-profile key(s) "
                f"into {_store.secrets.posture()['backend']}",
                file=sys.stderr,
            )
        for _failure in _pr["failed"]:
            print(
                f"[openai4s] could not migrate profile {_failure['id']}: "
                f"({_failure['error']}) — its key remains in plaintext",
                file=sys.stderr,
            )
        for _profile_id in _pr["reentry_required"]:
            print(
                f"[openai4s] model profile {_profile_id} needs its credential "
                f"saved again; its prior reference was not read",
                file=sys.stderr,
            )

        _cr = migrate_connector_env(_store)
        if _cr["migrated"]:
            print(
                f"[openai4s] moved env for {len(_cr['migrated'])} connector(s) "
                f"into {_store.secrets.posture()['backend']}",
                file=sys.stderr,
            )
        for _failure in _cr["failed"]:
            print(
                f"[openai4s] could not migrate connector {_failure['id']}: "
                f"({_failure['error']}) — its env remains in plaintext",
                file=sys.stderr,
            )
        for _connector_id in _cr["reentry_required"]:
            print(
                f"[openai4s] connector {_connector_id} needs its credential "
                f"environment saved again; its prior reference was not read",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 - never block startup on this
        traceback.print_exc()

    try:
        _tav = get_store(cfg.db_path).get_secret_setting("tavily_api_key")
        if _tav and not os.environ.get("OPENAI4S_TAVILY_API_KEY"):
            os.environ["OPENAI4S_TAVILY_API_KEY"] = _tav
    except Exception:  # noqa: BLE001
        pass
    try:
        _seed_example_project(cfg)
        _seed_example_connector(cfg)
        _seed_datapro_connector(cfg)
        handler = make_handler(cfg, hub, runner)
        httpd = _GatewayHTTPServer((cfg.host, cfg.port), handler, runner=runner)
    except BaseException:
        # By this point the runner has live resources (recovery sweeper,
        # coordinator).  Every raise site after its creation is covered —
        # the seeds and make_handler can fail on a locked/corrupt store or an
        # unwritable data dir, not just the bind at the end — because a
        # caller that survives the failure (the CLI's port-collision message,
        # an embedder retrying another port) must not inherit them as
        # orphans.  ``runner.close()`` is idempotent, so the bind path's
        # server_close() closing it again is safe.
        runner.close()
        raise
    httpd.daemon_threads = True
    if _demo_seed_enabled():
        # Opt-in only (`OPENAI4S_SEED_DEMO=1`), because this runs real cells:
        # UniProt/RCSB network, a gated MCP call whose approval can block up to
        # DEFAULT_TIMEOUT, and four artifacts. It must never run on the
        # synchronous startup path or the daemon never binds its port, so it
        # goes through the same background seeder the route uses -- one seeder,
        # so an operator who sets the variable *and* clicks the button gets one
        # run rather than two.
        runner.example_seed.start(cfg, runner)

    # Opt-in, off by default: a no-op that reads one settings row unless the
    # user has recorded consent. It cannot raise (emit swallows everything) and
    # cannot block (it sends on a daemon thread), so it is safe on the path that
    # has to bind the port.
    from openai4s.telemetry.emit import emit as _telemetry_emit

    _telemetry_emit("daemon_start", store=get_store(cfg.db_path), surface="web")
    return httpd


def _demo_seed_enabled() -> bool:
    """Whether the daemon seeds the example session *at startup*. Off by default.

    It used to default on, and what that meant on a fresh data dir was: the
    daemon binds its port, then a background thread starts a Python kernel,
    executes six cells, calls the UniProt and RCSB REST APIs, spawns the
    bundled MCP connector and writes four artifacts -- before the user has
    typed anything. Every one of those is a thing this application otherwise
    asks permission for. An air-gapped install saw failing network calls it
    never made; a regulated one saw outbound traffic in its logs from a tool
    that had, as far as its operator knew, only been started.

    The example itself is worth keeping, so it did not get deleted -- it moved
    behind `POST /example/session`, which the user triggers. `OPENAI4S_SEED_DEMO=1`
    restores the startup behaviour for a demo machine that wants it.
    """
    return os.environ.get("OPENAI4S_SEED_DEMO", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


class _ExampleSeedState:
    """Serialises the on-demand example seed and reports what it is doing.

    `_seed_demo_session` is idempotent by session name, which is enough to stop
    it *duplicating* the example but not enough to stop two concurrent requests
    both starting it -- the name check and the insert are not one transaction,
    and the seed runs for as long as its six cells take. Two clicks would run
    twelve cells and two sets of live API calls.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self, cfg: Config, runner: "SessionRunner") -> bool:
        """Begin seeding on a background thread. False if one is already going.

        Background because the seed runs real cells against live APIs: on the
        request thread it would hold the connection open for as long as the
        network takes, and a client that gave up would leave the seed running
        with nothing to report to.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._last_error = None

            def _run() -> None:
                try:
                    _seed_demo_session(cfg, runner)
                except Exception as exc:  # noqa: BLE001 - reported, never raised
                    with self._lock:
                        self._last_error = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()

            self._thread = threading.Thread(
                target=carry_context(_run),
                name="openai4s-example-seed",
                daemon=True,
            )
            self._thread.start()
            return True


def _example_session_frame(cfg: Config) -> dict[str, Any] | None:
    """The seeded example session, if it is there. Never raises: this answers a
    status route, and a store that cannot be read is 'not seeded', not a 500."""
    try:
        roots = get_store(cfg.db_path).browse_frames(
            project_id="proj_example", roots_only=True, limit=200
        )
    except Exception:  # noqa: BLE001
        return None
    for row in roots:
        if (row.get("name") or "") == _DEMO_SESSION_NAME:
            return row
    return None


def run_server(httpd: ThreadingHTTPServer) -> None:
    """Serve ``httpd`` until KeyboardInterrupt, then tear everything down.

    The one blocking service loop.  ``serve_app`` and the CLI's
    ``openai4s serve`` both drive it, so a lifecycle fix (teardown ordering,
    a drain, a close timeout) cannot land on one path and silently miss the
    other.  ``server_close`` is what tears down the runner and jobs, not just
    the socket; ``shutdown`` is a no-op after ``serve_forever`` has already
    returned, and stops a loop another thread may be running.
    """
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


def serve_app(cfg: Config | None = None, *, block: bool = True) -> ThreadingHTTPServer:
    cfg = cfg or get_config()
    httpd = build_app_server(cfg)
    if block:
        run_server(httpd)
    else:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _migrate_legacy_provider(cfg: Config) -> None:
    """Rewrite the retired ``doubao`` provider id to ``ark`` in any persisted
    runtime setting or saved model profile, so an install created before the Ark
    plan/v3 switch keeps working (an unknown provider would raise on chat).
    Idempotent: no-op once nothing references ``doubao``."""
    migrate_provider_alias(
        get_store(cfg.db_path),
        provider_specs(),
        old="doubao",
        new="ark",
    )


def _seed_example_project(cfg: Config) -> None:
    """Create an Example project (empty) on first boot so the dashboard isn't bare."""
    store = get_store(cfg.db_path)
    if not store.get_project("proj_example"):
        store.create_project(
            name="Example project",
            description="Sample project",
            project_id="proj_example",
            is_example=True,
        )


def _seed_example_connector(cfg: Config) -> None:
    """Register the bundled example MCP server on first boot so Connectors is
    immediately usable (probe + call work with zero setup)."""
    store = get_store(cfg.db_path)
    if store.get_connector("example"):
        return
    try:
        store.upsert_connector(
            connector_id="example",
            name="Example (bundled)",
            description="Local demo MCP server: echo / now / calc / random_int.",
            command=[sys.executable, "-m", "openai4s.mcp_servers.example_server"],
            enabled=True,
        )
    except Exception:  # noqa: BLE001
        pass


def _seed_datapro_connector(cfg: Config) -> None:
    """Register the single managed Volcengine DataPro connector.

    Only public transport metadata is persisted.  The Agent Plan Key and both
    outbound headers are resolved inside ``openai4s.datapro`` at request time.
    An existing row keeps the user's enabled/disabled choice across restarts.
    """

    store = get_store(cfg.db_path)
    if store.get_connector(datapro.CONNECTOR_ID):
        return
    try:
        store.upsert_connector(
            connector_id=datapro.CONNECTOR_ID,
            name="Volcengine DataPro",
            description="Professional dataset search through dataPro_search.",
            command=datapro.managed_connector_command(),
            enabled=True,
        )
    except Exception:  # noqa: BLE001 - optional connector must not block startup
        pass


# The example session is built from six deterministic cells run through the
# notebook REPL (no LLM key needed). Every scientific value it shows is REAL:
# records + sequences come from the live UniProt REST API (with a small bundle of
# REAL reference sequences as an offline fallback — real public data, never
# fabricated), the biochemistry / hydropathy / pairwise-identity numbers are
# deterministic computations over those real sequences (Biopython + the
# Kyte-Doolittle scale), and the 3D structure is a real coordinate download from
# the RCSB PDB API. Nothing uses np.random, hardcoded stand-ins, or a placeholder
# structure — consistent with the app's no-fabrication policy. A failed live
# fetch degrades to the bundled real data or an honest "unavailable" note, never
# to invented results. `entries`, `api_source`, `ref` and `struct_source` persist
# across cells in the kernel namespace (real REPL semantics).
_DEMO_UNIPROT = r"""
# Cell 1/6 -- REAL family records + sequences from the UniProt REST API.
import json
# Offline fallback = REAL reference sequences (human NIF3L1, E. coli YbgI): real
# public data, not fabricated, used only if the live API is unreachable.
_FALLBACK = [
    {'accession': 'Q9GZT8', 'organism': 'Homo sapiens', 'sequence': (
        'MLSSCVRPVPTTVRFVDSLICNSSRSFMDLKALLSSLNDFASLSFAESWDNVGLLVEPSPP'
        'HTVNTLFLTNDLTEEVMEEVLQKKADLILSYHPPIFRPMKRITWNTWKERLVIRALENRV'
        'GIYSPHTAYDAAPQGVNNWLAKGLGACTSRPIHPSKAPNYPTEGNHRVEFNVNYTQDLDK'
        'VMSAVKGIDGVSVTSFSARTGNEEQTRINLNCTQKALMQVVDFLSRNKQLYQKTEILSLE'
        'KPLLLHTGMGRLCTLDESVSLATMIDRIKRHLKLSHIRLALGVGRTLESQVKVVALCAGS'
        'GSSVLQGVEADLYLTGEMSHHDTLDAASQGINVILCEHSNTERGFLSDLRDMLDSHLENK'
        'INIILSETDRDPLQVV')},
    {'accession': 'P0AFP6', 'organism': 'Escherichia coli (K12)', 'sequence': (
        'MKNTELEQLINEKLNSAAISDYAPNGLQVEGKETVQKIVTGVTASQALLDEAVRLGADAV'
        'IVHHGYFWKGESPVIRGMKRNRLKTLLANDINLYGWHLPLDAHPELGNNAQLAALLGITV'
        'MGEIEPLVPWGELTMPVPGLELASWIEARLGRKPLWCGDTGPEVVQRVAWCTGGGQSFID'
        'SAARFGVDAFITGEVSEQTIHSAREQGLHFYAAGHHATERGGIRALSEWLNENTDLDVTF'
        'IDIPNPA')},
]
entries, api_source = _FALLBACK, 'bundled real reference sequences (offline)'
try:
    _u = ('https://rest.uniprot.org/uniprotkb/search'
          '?query=protein_name:NIF3+AND+reviewed:true'
          '&fields=accession,organism_name,length,sequence&format=json&size=4')
    _rows = json.loads(host.web_fetch(_u, format='json', timeout=25,
                                      max_chars=400000)['content'])
    _live = []
    for _it in _rows.get('results', []):
        _seq = (_it.get('sequence') or {}).get('value')
        _acc = _it.get('primaryAccession')
        if _acc and _seq:
            _live.append({'accession': _acc,
                          'organism': (_it.get('organism') or {}).get('scientificName', '?'),
                          'sequence': _seq})
    if _live:
        entries, api_source = _live, 'UniProt REST API (live)'
except Exception as _exc:
    api_source = 'bundled real reference sequences (UniProt API unreachable: %s)' % _exc

# Reference = the human record if present, else the longest sequence.
ref = next((e for e in entries if 'sapiens' in e['organism'].lower()),
           max(entries, key=lambda e: len(e['sequence'])))
print('Retrieved %d NIF3/DUF34 records via %s:' % (len(entries), api_source))
for _e in entries:
    _mark = '  <- reference' if _e is ref else ''
    print('  %-8s %-30s %4d aa%s'
          % (_e['accession'], _e['organism'], len(_e['sequence']), _mark))
"""
_DEMO_MCP = r"""
# Cell 2/6 -- the bundled MCP connector (Customize -> Connectors), on real inputs.
try:
    _total = sum(len(e['sequence']) for e in entries)
    _calc = host.mcp.call('example', 'calc',
                          {'expression': '%d + %d' % (_total, len(entries))})
    _now = host.mcp.call('example', 'now', {})
    if _calc.get('is_error'):
        raise RuntimeError(_calc.get('text') or 'calc failed')
    print('MCP connector "example" reachable:')
    print('  example.calc(total_residues + n_seqs) ->', _calc.get('text'))
    print('  example.now()                         ->', _now.get('text'))
except Exception as _exc:
    print('MCP connector call skipped:', _exc)
"""
_DEMO_PLOT = r"""
# Cell 3/6 -- REAL Kyte-Doolittle hydropathy profile of the reference sequence
# (a deterministic function of the real amino-acid sequence; no fabrication).
import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
_KD = {'A': 1.8, 'R': -4.5, 'N': -3.5, 'D': -3.5, 'C': 2.5, 'Q': -3.5,
       'E': -3.5, 'G': -0.4, 'H': -3.2, 'I': 4.5, 'L': 3.8, 'K': -3.9,
       'M': 1.9, 'F': 2.8, 'P': -1.6, 'S': -0.8, 'T': -0.7, 'W': -0.9,
       'Y': -1.3, 'V': 4.2}
_seq = ref['sequence']
_w = 19
_half = _w // 2
_vals = np.array([_KD.get(c, 0.0) for c in _seq])
_x, _y = [], []
for _i in range(_half, len(_seq) - _half):
    _x.append(_i + 1)
    _y.append(float(_vals[_i - _half:_i + _half + 1].mean()))
fig, ax = plt.subplots(figsize=(7, 3.6))
ax.axhline(0, color='0.7', lw=0.8)
ax.plot(_x, _y, color='#2b6cb0', lw=1.3)
ax.fill_between(_x, _y, 0, where=[v > 0 for v in _y],
                color='#f6ad55', alpha=0.6, label='hydrophobic')
ax.fill_between(_x, _y, 0, where=[v <= 0 for v in _y],
                color='#63b3ed', alpha=0.5, label='hydrophilic')
ax.set_title('Kyte-Doolittle hydropathy (window %d) - %s, %s'
             % (_w, ref['accession'], ref['organism']))
ax.set_xlabel('residue position')
ax.set_ylabel('mean hydropathy')
ax.legend(loc='upper right', fontsize=8)
plt.tight_layout()
print('rendered hydropathy profile for %s (%d residues) via %s'
      % (ref['accession'], len(_seq), api_source))
"""
_DEMO_CSV = r"""
# Cell 4/6 -- REAL per-protein biochemistry (Biopython ProtParam) + REAL pairwise
# %% identity to the reference (global alignment). Every number is computed from
# the real sequences above; nothing is randomised or hardcoded.
import pandas as pd
from Bio.SeqUtils.ProtParam import ProteinAnalysis
try:
    from Bio import Align
    from Bio.Align import substitution_matrices
    _al = Align.PairwiseAligner()
    _al.mode = 'global'
    _al.open_gap_score = -10
    _al.extend_gap_score = -0.5
    _al.substitution_matrix = substitution_matrices.load('BLOSUM62')
except Exception:
    _al = None

def _pct_identity(a, b):
    if a == b:
        return 100.0
    if _al is None:
        return None
    try:
        _aln = _al.align(a, b)[0]
        _s1, _s2 = str(_aln[0]), str(_aln[1])
        _cols = [(x, y) for x, y in zip(_s1, _s2) if x != '-' and y != '-']
        if not _cols:
            return None
        _match = sum(1 for x, y in _cols if x == y)
        return round(100.0 * _match / len(_cols), 1)
    except Exception:
        return None

_STD = set('ACDEFGHIKLMNPQRSTVWY')
_rows = []
for e in entries:
    _seq = e['sequence']
    _row = {'accession': e['accession'], 'organism': e['organism'],
            'length': len(_seq)}
    if set(_seq) <= _STD:
        _pa = ProteinAnalysis(_seq)
        _row.update({'molecular_weight_da': round(_pa.molecular_weight(), 1),
                     'isoelectric_point': round(_pa.isoelectric_point(), 2),
                     'gravy': round(_pa.gravy(), 3),
                     'aromaticity': round(_pa.aromaticity(), 3),
                     'instability_index': round(_pa.instability_index(), 1)})
    else:
        _row.update({'molecular_weight_da': None, 'isoelectric_point': None,
                     'gravy': None, 'aromaticity': None, 'instability_index': None})
    _row['pct_identity_to_ref'] = _pct_identity(ref['sequence'], _seq)
    _rows.append(_row)
df = pd.DataFrame(_rows)
df.to_csv('family_biochemistry.csv', index=False)
print(df.to_string(index=False))
"""
_DEMO_PDB = r"""
# Cell 5/6 -- REAL representative 3D structure from the RCSB PDB API (full-text
# search -> coordinate download). If the API is unreachable we record that
# honestly and skip; we never write a placeholder / geometric structure.
import json, urllib.parse
pdb_id, pdb_text, struct_source = None, None, None
try:
    _q = {'query': {'type': 'terminal', 'service': 'full_text',
                    'parameters': {'value': 'NIF3 DUF34'}},
          'return_type': 'entry',
          'request_options': {'paginate': {'start': 0, 'rows': 1}}}
    _su = ('https://search.rcsb.org/rcsbsearch/v2/query?json='
           + urllib.parse.quote(json.dumps(_q)))
    _hit = json.loads(host.web_fetch(_su, format='json', timeout=25)['content'])
    pdb_id = (_hit.get('result_set') or [{}])[0].get('identifier')
    if pdb_id:
        _raw = host.web_fetch('https://files.rcsb.org/download/%s.pdb' % pdb_id,
                              format='text', timeout=30, max_chars=4000000)['content']
        if 'ATOM' in _raw and _raw.count(chr(10)) > 20:
            pdb_text = _raw
            struct_source = 'RCSB PDB entry %s (live download)' % pdb_id
except Exception as _exc:
    struct_source = 'unavailable (RCSB API unreachable: %s)' % _exc

if pdb_text:
    open('nif3_structure.pdb', 'w').write(pdb_text)
    _n = sum(1 for _ln in pdb_text.splitlines()
             if _ln.startswith(('ATOM', 'HETATM')))
    print('wrote nif3_structure.pdb (%d atoms) - source: %s' % (_n, struct_source))
else:
    struct_source = struct_source or 'unavailable offline'
    print('no structure written - %s (never substituting a placeholder)'
          % struct_source)
"""
_DEMO_MD = r"""
# Cell 6/6 -- summary report citing only what was really fetched / computed.
import pandas as pd
_df = pd.read_csv('family_biochemistry.csv')
_r = _df[_df['accession'] == ref['accession']].iloc[0]
_recs = '\n'.join('- `%s` - %s (%d aa)'
                  % (row['accession'], row['organism'], int(row['length']))
                  for _, row in _df.iterrows())
_struct_line = ('- Representative 3D structure: %s' % struct_source
                if struct_source else '- Representative 3D structure: not fetched')
_mw = '(non-standard residues)' if pd.isna(_r['molecular_weight_da']) \
    else '%.0f Da' % float(_r['molecular_weight_da'])
_pi = 'n/a' if pd.isna(_r['isoelectric_point']) \
    else '%.2f' % float(_r['isoelectric_point'])
_gv = 'n/a' if pd.isna(_r['gravy']) else '%.3f' % float(_r['gravy'])
_report = (
    '# NIF3 / DUF34 family - real records, biochemistry & structure\n\n'
    'A small, fully reproducible pass over the NIF3 / DUF34 protein family.\n'
    'Every number below is computed from real data - no simulated or\n'
    'placeholder values.\n\n'
    '## Data sources\n'
    '- Sequence records: ' + str(api_source) + '\n'
    + _struct_line + '\n\n'
    '## Family records\n' + _recs + '\n\n'
    '## Reference protein (' + str(ref['accession']) + ', '
    + str(ref['organism']) + ')\n'
    '- Length: %d aa\n' % int(_r['length'])
    + '- Molecular weight: ' + _mw + '\n'
    '- Isoelectric point (pI): ' + _pi + '\n'
    '- GRAVY (mean Kyte-Doolittle hydropathy): ' + _gv + '\n\n'
    '## What was computed\n'
    '- Per-protein biochemistry (length, MW, pI, GRAVY, aromaticity,\n'
    '  instability) via Biopython ProtParam -> family_biochemistry.csv\n'
    '- Pairwise % identity to the reference via a real global alignment (BLOSUM62)\n'
    '- A Kyte-Doolittle hydropathy profile of the reference (see the figure)\n\n'
    '## Provenance\n'
    'UniProt REST API, RCSB PDB API, Biopython (ProtParam / PairwiseAligner),\n'
    'and the Kyte-Doolittle hydropathy scale. Re-running these cells reproduces\n'
    'every value.\n'
)
open('nif3_report.md', 'w').write(_report)
print('wrote nif3_report.md')
"""

_DEMO_SESSION_NAME = "NIF3/DUF34 family (real UniProt + biochemistry + RCSB PDB)"

# Demo-session names seeded by older versions of this function. When the example
# is upgraded (a new _DEMO_SESSION_NAME), any of these still present in an
# existing install is retired — frame AND the artifacts it produced — so the
# example project shows only the current, fully-real session instead of
# accumulating a stale fabricated one alongside it. Matched by EXACT name, so a
# user's own sessions are never touched.
_LEGACY_DEMO_NAMES = ("NIF3/DUF34 phylogeny (live UniProt + RCSB PDB + MCP)",)


def _retire_demo_frame(runner: "SessionRunner", frame_id: str) -> None:
    """Delete a superseded demo through the complete session lifecycle."""
    try:
        runner.delete_session(frame_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _seed_demo_session(cfg: Config, runner: "SessionRunner") -> None:
    """Populate the example project with one real, fully-executed session that
    calls live external APIs (UniProt REST, RCSB PDB) and the bundled MCP
    connector, so the UI (thumbnails, 3Dmol viewer, notebook, provenance) has
    working, API-driven data on boot. Idempotent: keyed on the session name, so
    an existing install picks up the upgraded example on the next restart, and
    any demo session from an older version is retired in the process."""
    store = get_store(cfg.db_path)
    roots = store.browse_frames(project_id="proj_example", roots_only=True, limit=200)
    # Retire superseded demo sessions (exact legacy names only) so the upgraded
    # example replaces the old one rather than coexisting with it.
    for r in roots:
        if (r.get("name") or "") in _LEGACY_DEMO_NAMES:
            _retire_demo_frame(runner, r.get("frame_id") or r.get("id"))
    if any((r.get("name") or "") == _DEMO_SESSION_NAME for r in roots):
        return  # current demo already present
    fid = store.new_frame(
        kind="turn", project_id="proj_example", status="done", model=cfg.llm.model
    )
    # Cell 2 calls the bundled `example` connector and the global default for
    # `mcp_call` is "ask". The seed runs on a background thread with a live
    # permission channel but no human guaranteed to be watching the brand-new
    # session, so that prompt would sit pending for the broker's full
    # 15-minute backstop — and a scripted `POST /example/session` has no
    # approver at all. The user's explicit `{"confirm": true}` already
    # authorized exactly what the demo does, so pre-authorize precisely the
    # two demo tools, scoped to this one conversation. A standing operator
    # `deny` rule still vetoes these (deny is absolute in resolve()), and
    # every other tool, connector and session keeps asking.
    for pattern in ("example/calc", "example/now"):
        store.set_permission_rule(
            scope="conversation",
            scope_id=fid,
            tool="mcp_call",
            pattern=pattern,
            decision="allow",
        )
    store.update_frame(
        fid,
        name=_DEMO_SESSION_NAME,
        task_summary="Pull NIF3/DUF34 family records + sequences from the UniProt "
        "REST API, compute real per-protein biochemistry and pairwise "
        "identity (Biopython) with a Kyte-Doolittle hydropathy "
        "profile, fetch a representative structure from the RCSB PDB "
        "API, and write a reproducible report — every value real.",
    )
    demo_user_message = store.add_message(
        root_frame_id=fid,
        role="user",
        frame_id=fid,
        content="Analyse the NIF3/DUF34 protein family using real data only: pull "
        "family records and sequences from the UniProt REST API, compute "
        "per-protein biochemistry (MW, pI, GRAVY) and pairwise sequence "
        "identity, plot a Kyte-Doolittle hydropathy profile, fetch a "
        "representative 3D structure from the RCSB PDB API, and write a "
        "short reproducible report.",
    )
    runner._capture_cursor_checkpoint_best_effort(  # noqa: SLF001
        fid,
        source_kind="message",
        source_id=demo_user_message["message_id"],
    )
    # One row per demo cell: its source plus, where the cell has a
    # user-visible deliverable, how to recognize it in the artifact store and
    # the success line to render. The execution loop and the material lines
    # both derive from this table — the cell number in each fallback comes
    # from the same enumeration — so inserting or reordering a demo cell
    # cannot leave the message citing the wrong cell, and a cell's expected
    # artifact lives on the same row as the cell that writes it.
    demo_plan: tuple[tuple[str, dict[str, Any] | None], ...] = (
        (_DEMO_UNIPROT, None),
        (_DEMO_MCP, None),
        (
            _DEMO_PLOT,
            {
                # Any .png, not an exact name: the figure filename carries the
                # producing cell's index. If _DEMO_PLOT ever exports another
                # format, update this predicate on the same row.
                "produced": lambda names: any(
                    str(name or "").endswith(".png") for name in names
                ),
                "line": "- **hydropathy figure (PNG)** — Kyte-Doolittle "
                "profile of the reference sequence\n",
                "label": "hydropathy figure",
            },
        ),
        (
            _DEMO_CSV,
            {
                "filename": "family_biochemistry.csv",
                "line": "- **family_biochemistry.csv** — per-protein length "
                "/ MW / pI / GRAVY / % identity (Biopython)\n",
                "label": "biochemistry table",
            },
        ),
        (
            _DEMO_PDB,
            {
                "filename": "nif3_structure.pdb",
                "line": "- **nif3_structure.pdb** — real RCSB structure "
                "(opens in the 3Dmol viewer)\n",
                # Bespoke fallback: a missing structure is a deliberate
                # skip (Cell 5 writes it only on a successful live RCSB
                # download), not a crash to send the reader hunting for.
                "missing": "- _3D structure_ — skipped this run: the RCSB "
                "download was unreachable (no placeholder is ever "
                "substituted; see nif3_report.md)\n",
            },
        ),
        (
            _DEMO_MD,
            {
                "filename": "nif3_report.md",
                "line": "- **nif3_report.md** — reproducible summary with "
                "data provenance\n",
                "label": "summary report",
            },
        ),
    )
    cell_failures: list[str] = []
    for index, (code, _material) in enumerate(demo_plan, start=1):
        label = f"cell {index}/{len(demo_plan)}"
        try:
            outcome = runner.run_repl(fid, "proj_example", code)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            cell_failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        cell = outcome.get("cell") if isinstance(outcome, dict) else None
        error = (cell or {}).get("error")
        if error:
            # The kernel error is a traceback whose last line is the exception;
            # that one line is what the summary message can honestly cite.
            summary_line = str(error).strip().splitlines()[-1]
            cell_failures.append(f"{label}: {summary_line}")
        elif (isinstance(outcome, dict) and outcome.get("status") == "cancelled") or (
            cell or {}
        ).get("status") == "interrupted":
            # ``run_repl`` reports Stop/shutdown/watchdog interrupts as a
            # cancelled run or an interrupted cell with ``error`` None —
            # treating those as successes reopened the all-green "Done —
            # every value is real" header over cells that never finished.
            cell_failures.append(f"{label}: interrupted before completion")
    # Describe only the materials that were actually produced. Every
    # deliverable is conditional in practice — the structure needs a live
    # RCSB download, the figure/table/report need optional science libraries
    # the lightweight install does not ship (matplotlib, Biopython, pandas) —
    # so every line branches on the artifact store rather than over-claiming.
    _produced = {
        a.get("filename") for a in store.list_artifacts({"root_frame_id": fid})
    }
    _material_lines: list[str] = []
    for index, (_code, material) in enumerate(demo_plan, start=1):
        if material is None:
            continue
        produced = (
            material["produced"](_produced)
            if "produced" in material
            else material["filename"] in _produced
        )
        if produced:
            _material_lines.append(material["line"])
        elif "missing" in material:
            _material_lines.append(material["missing"])
        else:
            _material_lines.append(
                f"- _{material['label']}_ — not produced this run (Cell "
                f"{index} did not complete; see its error in the Notebook "
                "tab)\n"
            )
    if cell_failures:
        # An honest header beats the reassuring one: some cells crashed, so
        # "every value is real" must not be claimed on their behalf.
        _header = (
            f"{len(cell_failures)} of {len(demo_plan)} example cells did not "
            "complete on this install (commonly a missing optional science "
            "dependency such as Biopython or pandas):\n"
            + "".join(f"- {failure}\n" for failure in cell_failures)
            + "\nEvery value that **was** produced is computed from real data "
            "(no simulated or placeholder values), and the materials below "
            "list only what was actually produced."
        )
    else:
        _header = (
            "Done — every value in this session is computed from real data "
            "(no simulated or placeholder values)."
        )
    store.add_message(
        root_frame_id=fid,
        role="assistant",
        frame_id=fid,
        content=_header + "\n\n"
        "**Real inputs**\n"
        "- **UniProt REST API** — NIF3/DUF34 family records + sequences\n"
        "- **RCSB PDB API** — full-text search + coordinate download of a "
        "representative structure\n"
        "- **MCP connector `example`** — `calc` / `now` tools over the "
        "Connectors bridge\n\n"
        "**Materials — click any artifact to view**\n"
        + "".join(_material_lines)
        + "\nOpen the **Notebook** tab to replay the executed cells, or the "
        "**Files** panel to view each material.",
    )
    store.update_frame(fid, status="done")
