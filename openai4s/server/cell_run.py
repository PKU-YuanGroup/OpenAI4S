"""Web-session orchestration for one scientific Python/R cell.

This service owns the transaction order (prepare -> safety -> execute -> capture
-> record) while all infrastructure stays behind injected ports. Finishing the
transaction is only an observation; it never decides that an agent task is done.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import signal
import stat
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.agent.actions import is_completion_only_cell
from openai4s.execution import CaptureResult, CellExecutionResult, CellRequest
from openai4s.execution.attempts import (
    attempt_state_for_exception,
    attempt_state_for_result,
)
from openai4s.execution.watchdog import (
    KernelCancellation,
    KernelNotResetCancellation,
    KernelNotResetTimeout,
    KernelResetUnavailableCancellation,
    KernelResetUnavailableTimeout,
)
from openai4s.kernel import KernelLease, KernelSupervisor
from openai4s.kernel.errors import KernelWorkerExited
from openai4s.server.artifacts import ArtifactOperationError, artifact_receipt_map

NOTEBOOK_DIVIDER = "----- output -----"
LIVE_CELL_OUTPUT_CHARS = 1_000_000
LIVE_OUTPUT_TRUNCATION = "\n...(live output truncated)"
EventSink = Callable[[dict[str, Any]], None]
ChunkSink = Callable[[str], None]


#: A running cell's stdout writes are gathered for this long into one live
#: event pair. Every `write()` used to become its own `notebook_cell_chunk` plus
#: its activity echo: `print(i, 'x' * 80)` is four writes, so 5000 lines were
#: 40,000 events, which overflowed the WebSocket queue and dropped the client
#: before the cell's terminal events reached it.
LIVE_CHUNK_WINDOW_S = 0.05
#: A batch is sent early once it holds this many characters.
LIVE_CHUNK_MAX_CHARS = 32_768


def _bounded_live_output(value: Any) -> str:
    """Bound one transient WebSocket field without mutating the real result."""

    text = str(value or "")
    if len(text) <= LIVE_CELL_OUTPUT_CHARS:
        return text
    return text[:LIVE_CELL_OUTPUT_CHARS] + LIVE_OUTPUT_TRUNCATION


class _LiveOutputBatcher:
    """At most one live delivery per window, plus one per full batch.

    The first write after a quiet window is delivered at once, so the first
    line of output (and an interrupt that waits for it) is not delayed; later
    writes inside the window are joined and delivered by a timer, so output
    printed just before a long quiet computation still shows. `close` delivers
    what is left; the caller closes before emitting the cell's terminal events,
    which therefore always follow its last chunk. Deliveries happen under one
    lock, so they keep the order the text was written in.
    """

    def __init__(
        self,
        deliver: Callable[[str], None],
        *,
        window_s: float = LIVE_CHUNK_WINDOW_S,
        max_chars: int = LIVE_CHUNK_MAX_CHARS,
    ) -> None:
        self._deliver = deliver
        self._window_s = window_s
        self._max_chars = max_chars
        self._lock = threading.Lock()
        self._parts: list[str] = []
        self._size = 0
        self._timer: threading.Timer | None = None
        self._last_delivery = float("-inf")
        self._closed = False

    def push(self, text: str) -> None:
        with self._lock:
            self._parts.append(text)
            self._size += len(text)
            now = time.monotonic()
            quiet = now - self._last_delivery >= self._window_s
            if self._closed or quiet or self._size >= self._max_chars:
                self._deliver_locked(now)
            elif self._timer is None:
                delay = self._window_s - (now - self._last_delivery)
                timer = threading.Timer(delay, self.flush)
                timer.daemon = True
                self._timer = timer
                timer.start()

    def flush(self) -> None:
        with self._lock:
            self._deliver_locked(time.monotonic())

    def close(self) -> None:
        with self._lock:
            self._deliver_locked(time.monotonic())
            self._closed = True

    def _deliver_locked(self, now: float) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if not self._parts:
            return
        text = "".join(self._parts)
        self._parts = []
        self._size = 0
        self._last_delivery = now
        self._deliver(text)


class _LiveCellStream:
    """The chunk sink one streaming cell hands its kernel; see `_start_stream`."""

    def __init__(self, deliver: Callable[[str], None]) -> None:
        self._batcher = _LiveOutputBatcher(deliver)
        self._streamed_chars = 0
        self._truncated = False
        #: What was shown live, bounded like the stream itself, so a cell
        #: whose kernel dies keeps it (see the worker-failure path).
        self._shown: list[str] = []

    def __call__(self, text: str) -> None:
        if self._truncated:
            return
        value = str(text or "")
        remaining = max(0, LIVE_CELL_OUTPUT_CHARS - self._streamed_chars)
        if len(value) > remaining:
            value = value[:remaining] + LIVE_OUTPUT_TRUNCATION
            self._truncated = True
        self._streamed_chars += min(len(str(text or "")), remaining)
        if value:
            self._shown.append(value)
            self._batcher.push(value)

    def close(self) -> None:
        self._batcher.close()

    def streamed_text(self) -> str:
        return "".join(self._shown)


#: NB-1 (owner decision 39): the most of a run-this-file Cell's script that is
#: kept with its execution record. A longer file keeps its head and says so.
INLINE_SCRIPT_MAX_BYTES = 256 * 1024
#: The only imports a run-this-file Python Cell may carry beside its one call.
_SCRIPT_RUNNER_MODULES = frozenset({"runpy", "pathlib"})
_R_SOURCE_CALL = re.compile(
    r"""^source\s*\(\s*(?:file\s*=\s*)?(["'])([^"'\n]+)\1\s*(?:,[^()\n]*)?\)\s*;?$"""
)


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _literal_path(call: ast.Call, keyword: str | None = None) -> str | None:
    node: ast.AST | None = call.args[0] if call.args else None
    if node is None and keyword is not None:
        node = next((k.value for k in call.keywords if k.arg == keyword), None)
    if isinstance(node, ast.Constant) and type(node.value) is str and node.value:
        return node.value
    return None


def _read_call_path(node: ast.AST) -> str | None:
    """``open("X", ...).read()`` or ``Path("X").read_text(...)`` -> ``X``."""

    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    opened = node.func.value
    if not isinstance(opened, ast.Call):
        return None
    opener = _dotted_name(opened.func)
    if node.func.attr == "read" and not node.args and opener in {"open", "io.open"}:
        mode = opened.args[1] if len(opened.args) > 1 else None
        mode = mode or next((k.value for k in opened.keywords if k.arg == "mode"), None)
        if mode is not None and not (
            isinstance(mode, ast.Constant)
            and type(mode.value) is str
            and not set(mode.value) & set("wax+")
        ):
            return None
        return _literal_path(opened, "file")
    if node.func.attr == "read_text" and opener in {"Path", "pathlib.Path"}:
        return _literal_path(opened)
    return None


def _python_script_call(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    name = _dotted_name(node.func)
    if name in {"runpy.run_path", "run_path"}:
        return _literal_path(node, "path_name")
    if name == "exec" and node.args:
        source = node.args[0]
        if (
            isinstance(source, ast.Call)
            and _dotted_name(source.func) == "compile"
            and source.args
        ):
            source = source.args[0]
        return _read_call_path(source)
    return None


def _magic_run_path(line: str) -> str | None:
    """IPython's ``%run [-flags] X [args]`` -> ``X``; ``%run -m`` names a module."""

    try:
        words = shlex.split(line.strip()[len("%run") :])
    except ValueError:
        return None
    for word in words:
        if word == "-m":
            return None
        if not word.startswith("-"):
            return word
    return None


def script_run_target(code: str, language: str) -> str | None:
    """The workspace file a Cell does nothing but run, as the Cell spells it.

    Python: ``exec(open("X").read())`` (any quoting, an ``encoding=`` or a
    read mode, an ``exec(compile(...))`` wrapper, ``Path("X").read_text()``),
    ``runpy.run_path("X")``, or ``%run X``; R: ``source("X")``. Comments,
    blank lines and the ``runpy``/``pathlib`` imports may surround the call.
    Anything else is ordinary code whose source already says what ran.
    """

    lines = [
        line.strip()
        for line in str(code or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return None
    if str(language or "python").lower() == "r":
        if len(lines) != 1:
            return None
        match = _R_SOURCE_CALL.match(lines[0])
        return match.group(2) if match else None
    if len(lines) == 1 and re.match(r"%run\s", lines[0]):
        return _magic_run_path(lines[0])
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    target = None
    for statement in tree.body:
        if isinstance(statement, ast.Import) and all(
            alias.name in _SCRIPT_RUNNER_MODULES for alias in statement.names
        ):
            continue
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.module in _SCRIPT_RUNNER_MODULES
        ):
            continue
        if target is not None or not isinstance(statement, ast.Expr):
            return None
        target = _python_script_call(statement.value)
        if target is None:
            return None
    return target


def read_workspace_script(
    workspace: Path, spelled: str, *, limit: int = INLINE_SCRIPT_MAX_BYTES
) -> dict[str, Any] | None:
    """The bytes of one regular file inside ``workspace``, as they are now.

    Read by the daemon, which the kernel sandbox does not confine, so the
    file must resolve inside the session workspace: a Cell naming
    ``~/.ssh/id_rsa`` or a symlink out of the workspace records nothing. A
    FIFO or device is never opened for reading (O_NONBLOCK, then S_ISREG).
    """

    # `~` means the home directory to R and a folder named `~` to Python;
    # neither is a file this record can claim the Cell ran.
    if not spelled or "\x00" in spelled or spelled.startswith("~"):
        return None
    try:
        root = Path(workspace).resolve(strict=True)
        candidate = Path(spelled)
        if not candidate.is_absolute():
            candidate = root / candidate
        target = candidate.resolve(strict=True)
        relative = target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    truncated = len(data) > limit
    kept = data[:limit]
    content = kept.decode("utf-8", errors="replace")
    if truncated:
        # A cut through a multi-byte character decodes to one replacement mark.
        content = content.rstrip("�")
    return {
        "path": relative.as_posix(),
        "content": content,
        "size_bytes": max(int(info.st_size), len(data)),
        "truncated": truncated,
        "sha256": None if truncated else hashlib.sha256(data).hexdigest(),
    }


def inline_cell_script(
    workspace: Path, code: str, language: str
) -> dict[str, Any] | None:
    """NB-1: what a run-this-file Cell ran, read just before it runs.

    Best effort: a Cell is never refused or failed because its script could
    not be recorded; it then simply carries no inlined script.
    """

    try:
        spelled = script_run_target(code, language)
        if spelled is None:
            return None
        return read_workspace_script(workspace, spelled)
    except Exception:  # noqa: BLE001 - evidence must not break a Cell
        return None


def _runtime_files_read(result: dict[str, Any]) -> list[str]:
    """Validate the bounded worker observation without inventing evidence."""

    raw = result.get("files_read")
    if raw is None:
        # Compatibility workers predate the observation field.  Missing proof
        # means no read evidence; the host must not fall back to source strings.
        return []
    if not isinstance(raw, list) or len(raw) > 256:
        raise RuntimeError("kernel file-read evidence is invalid")
    paths: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise RuntimeError("kernel file-read evidence is invalid")
        if value not in paths:
            paths.append(value)
    return paths


def _take_artifact_receipts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove and validate Host-owned receipts before persisting Cell output."""

    raw = result.pop("_openai4s_artifact_receipts", None)
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 512:
        raise RuntimeError("kernel Artifact receipt evidence is invalid")
    try:
        return list(artifact_receipt_map(raw).values())
    except ArtifactOperationError as error:
        raise RuntimeError("kernel Artifact receipt evidence is invalid") from error


def _no_attempt_allocate(
    session: "CellSession",
    request: CellRequest,
    cell_id: str,
    action_group_id: str | None,
) -> None:
    del session, request, cell_id, action_group_id
    return None


def _allow_cell(session: "CellSession", request: CellRequest) -> None:
    del session, request


def _no_capture_lease(session: "CellSession", request: CellRequest) -> Any:
    del session, request
    return nullcontext()


def _no_attempt_milestone(attempt_id: str) -> None:
    del attempt_id


def _no_attempt_generation(
    attempt_id: str, session: "CellSession", language: str
) -> None:
    del attempt_id, session, language


def _never_cancelled(session: "CellSession") -> bool:
    return False


def _no_attempt_finish(attempt_id: str, terminal_state: str, error: Any = None) -> None:
    del attempt_id, terminal_state, error


class CellSession(Protocol):
    root_frame_id: str
    project_id: str
    workspace: Path
    cell_index: int
    kernels: KernelSupervisor


@dataclass(frozen=True)
class CellExecutionPorts:
    prepare_language: Callable[[CellSession, str], str | None]
    kernel_id: Callable[[CellSession, str], str]
    snapshot: Callable[[Path], Any]
    protect_versions: Callable[[CellSession], None]
    # Takes the session as well as the cell. The biosecurity screener judges a
    # *trajectory* -- what the user asked for across the conversation against
    # what the agent has been doing -- so a port that only sees one cell can
    # only ever run the static classifier, which is what the Web path did.
    safety_refusal: Callable[[Any, str, str], str | None]
    run: Callable[
        [CellSession, CellRequest, str, ChunkSink | None, KernelLease | None],
        dict[str, Any],
    ]
    # (..., language, artifact_receipts, cell_failed). ``cell_failed`` keeps a
    # failed Cell's half-drawn open figures from being saved as deliverables.
    capture: Callable[
        [
            CellSession,
            int,
            str,
            Any,
            EventSink,
            str,
            list[dict[str, Any]],
            bool,
        ],
        CaptureResult,
    ]
    # (session, title, artifacts, emit, environment, language). The last two
    # are this Cell's runtime label and language: the step card is a second
    # record of which runtime wrote the files, and it said "python" for every
    # R Cell while the artifact's env snapshot said "r".
    emit_artifact_step: Callable[
        [CellSession, str, list[dict], EventSink, str, str], None
    ]
    record_cell: Callable[..., None]
    # Admission runs before allocating a Cell id/revision/attempt or touching a
    # runtime. Stage 1 uses it for local environment readiness; a refusal must
    # not materialise the very Cell whose first ImportError it is preventing.
    admit: Callable[[CellSession, CellRequest], None] = _allow_cell
    # Held from before admission through final capture/recording.  The Web
    # adapter uses it to keep background writers out of a shared-workspace
    # diff; headless/legacy callers retain the no-op default.
    capture_lease: Callable[[CellSession, CellRequest], Any] = _no_capture_lease
    allocate_attempt: Callable[
        [CellSession, CellRequest, str, str | None], str | None
    ] = _no_attempt_allocate
    bind_attempt_generation: Callable[[str, CellSession, str], None] = (
        _no_attempt_generation
    )
    mark_attempt_started: Callable[[str], None] = _no_attempt_milestone
    mark_attempt_response: Callable[[str], None] = _no_attempt_milestone
    mark_attempt_capture: Callable[[str], None] = _no_attempt_milestone
    finish_attempt: Callable[[str, str, Any], None] = _no_attempt_finish
    bind_lineage: Callable[..., list[str]] | None = None
    # Whether this Cell's execution was cancelled (a Stop, a session close,
    # daemon shutdown). Consulted once more after the runtime is prepared and
    # immediately before user code would start: preparation can take seconds
    # (a cold kernel's bootstrap) and nothing can interrupt it, so a
    # cancellation that lands there must not be answered by running the Cell.
    cancelled: Callable[[CellSession], bool] = _never_cancelled


#: Author-written, never a rendering of an exception. The timeout wording keeps
#: the actionable half of what the watchdog knows -- that the kernel was reset,
#: so earlier variables are gone -- without quoting it.
KERNEL_FAILURE_MESSAGE = "the kernel did not finish this cell"
#: The worker process itself ended mid-cell. Worded once here because the
#: Notebook recognises it to show the same explanation in the reader's language.
KERNEL_EXITED_MESSAGE = (
    "the kernel process exited before this cell finished ({detail}); variables "
    "from earlier cells are lost and the next cell starts a fresh kernel"
)


def _kernel_exit_detail(exit_code: int | None) -> str:
    """Say what an exit status means, without quoting anything the worker wrote.

    `-9` is Popen's spelling of SIGKILL and `137` a wrapper's (128 + 9); on a
    workstation that is almost always the out-of-memory killer.
    """
    if exit_code is None:
        return "exit status unknown"
    if exit_code in (-9, 137):
        return (
            f"exit code {exit_code}: killed with SIGKILL, which usually means it "
            "ran out of memory"
        )
    if exit_code < 0:
        try:
            name = signal.Signals(-exit_code).name
        except ValueError:
            name = ""
        return f"killed by signal {-exit_code}" + (f" ({name})" if name else "")
    return f"exit code {exit_code}"


CELL_TIMEOUT_MESSAGE = (
    "the cell exceeded its time limit and was stopped; the kernel was reset, so "
    "variables from earlier cells were cleared"
)
#: A local reset did destroy the old namespace, but the replacement failed its
#: bootstrap and was detached. It must not reuse the no-reset cluster warning:
#: there is no old allocation that may still be running this cell.
CELL_TIMEOUT_RESET_UNAVAILABLE_MESSAGE = (
    "the cell exceeded its time limit and was stopped; the old kernel was reset "
    "and variables from earlier cells were cleared, but its replacement could "
    "not be initialized; retry to start a fresh kernel"
)
#: The same event where the reset did not happen -- a worker this daemon did
#: not spawn cannot be respawned by it. Saying "variables were cleared" there
#: is not a harmless simplification: it tells the user the work stopped, and
#: on a cluster session the allocation may still be running the cell.
CELL_TIMEOUT_NO_RESET_MESSAGE = (
    "the cell exceeded its time limit and was stopped here, but its kernel "
    "could not be reset from this daemon; if the session runs on a cluster the "
    "work may still be running on its allocation"
)
CELL_CANCELLED_MESSAGE = (
    "the cell was cancelled and required a hard stop; the kernel was reset, so "
    "variables from earlier cells were cleared"
)
CELL_CANCELLED_RESET_UNAVAILABLE_MESSAGE = (
    "the cell was cancelled and required a hard stop; the old kernel was reset "
    "and variables from earlier cells were cleared, but its replacement could "
    "not be initialized; retry to start a fresh kernel"
)
#: Nothing ran, so nothing was reset: the Cell was cancelled while its runtime
#: was still being prepared, and its code was never started.
CELL_CANCELLED_BEFORE_START_MESSAGE = (
    "the cell was cancelled before it started, so none of its code ran"
)
CELL_CANCELLED_NO_RESET_MESSAGE = (
    "the cell was cancelled and stopped here, but its kernel could not be reset "
    "from this daemon; if the session runs on a cluster the work may still be "
    "running on its allocation"
)


def _worker_failure_text(exc: BaseException, public: dict) -> str:
    """What a failed cell may say, by exception type -- never by `str(exc)`.

    A `GatewayError` is the one message with known provenance: `public_exception`
    passes it through because someone wrote it for a client to read. Everything
    else is a sentence from this module plus the stable code and request id the
    projector assigned, so the failure stays correlatable without being quoted.
    """
    from openai4s.server.errors import GatewayError

    if isinstance(exc, GatewayError):
        base = str(public.get("error") or KERNEL_FAILURE_MESSAGE)
    elif isinstance(exc, KernelNotResetCancellation):
        base = CELL_CANCELLED_NO_RESET_MESSAGE
    elif isinstance(exc, KernelResetUnavailableCancellation):
        base = CELL_CANCELLED_RESET_UNAVAILABLE_MESSAGE
    elif isinstance(exc, KernelCancellation):
        base = CELL_CANCELLED_MESSAGE
    elif isinstance(exc, KernelNotResetTimeout):
        base = CELL_TIMEOUT_NO_RESET_MESSAGE
    elif isinstance(exc, KernelResetUnavailableTimeout):
        base = CELL_TIMEOUT_RESET_UNAVAILABLE_MESSAGE
    elif isinstance(exc, TimeoutError):
        base = CELL_TIMEOUT_MESSAGE
    elif isinstance(exc, KernelWorkerExited):
        base = KERNEL_EXITED_MESSAGE.format(detail=_kernel_exit_detail(exc.exit_code))
    else:
        base = KERNEL_FAILURE_MESSAGE
    code = str(public.get("code") or "")
    request_id = str(public.get("request_id") or "")
    suffix = " ".join(part for part in (code, request_id) if part)
    return f"{base} ({suffix})" if suffix else base


class CellExecutionService:
    def __init__(
        self,
        ports: CellExecutionPorts,
        *,
        id_factory: Callable[[], str] | None = None,
        title_factory: Callable[[str, int], str] | None = None,
    ) -> None:
        self.ports = ports
        self.id_factory = id_factory or (lambda: f"c-{uuid.uuid4().hex[:12]}")
        self.title_factory = title_factory or activity_title

    def execute(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        *,
        action_group_id: str | None = None,
    ) -> CellExecutionResult:
        # Acquire before readiness or identity allocation.  A background race
        # must refuse the Cell without inventing a Cell id/attempt, touching a
        # runtime, or allowing any workspace side effect.
        capture_lease = self.ports.capture_lease(session, request)
        lease_type = type(capture_lease)
        if not callable(getattr(lease_type, "__enter__", None)) or not callable(
            getattr(lease_type, "__exit__", None)
        ):
            # CPython 3.10 raises AttributeError here while 3.11+ raises
            # TypeError.  Reject the malformed port explicitly so the safety
            # boundary has one stable failure contract on every supported
            # interpreter.
            raise TypeError("capture_lease must return a context manager")
        with capture_lease:
            return self._execute_admitted(
                session,
                request,
                emit,
                action_group_id=action_group_id,
            )

    def _execute_admitted(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        *,
        action_group_id: str | None = None,
    ) -> CellExecutionResult:
        action_group_id = action_group_id or request.action_group_id
        self.ports.admit(session, request)
        session.cell_index += 1
        index = session.cell_index
        cell_id = self.id_factory()
        # Attempt identity is durable before *any* language preparation,
        # safety classification, runtime acquisition, or worker interaction.
        attempt_id = self.ports.allocate_attempt(
            session, request, cell_id, action_group_id
        )
        if attempt_id is not None:
            try:
                self.ports.mark_attempt_started(attempt_id)
            except BaseException as exc:
                # A milestone write that raises (e.g. SQLite busy) must still
                # finalize the attempt, or its terminal_state stays NULL and the
                # action timeline shows the group "running" forever.
                self._finish_attempt(attempt_id, "record_failed", exc)
                raise
        # Before `prepare_language`, which runs Skill bootstrap (sidecar
        # imports, `origin="system"`) on the worker it prepares. Admission
        # further down needs that worker's measured posture and so cannot
        # move; the unconditional half needs no posture and belongs here,
        # or a blocked Skill's bootstrap has already executed by the time
        # the user Cell is refused. The CLI sink is ordered the same way.
        from openai4s.server.skill_network_admission import admit_cell_preflight

        preflight = admit_cell_preflight(frame_id=session.root_frame_id)
        if not preflight.allowed:
            refused = self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                None,
                preflight.refusal_message(),
                attempt_id,
                "skill_network_refused",
                None,
            )
            if isinstance(refused.result, dict):
                refused.result["skill_network"] = preflight.as_dict()
            return refused
        try:
            runtime_error = self.ports.prepare_language(session, request.language)
            kernel_id = self.ports.kernel_id(session, request.language)
            if attempt_id is not None and runtime_error is None:
                self.ports.bind_attempt_generation(
                    attempt_id, session, request.language
                )
            generation_id = (
                self._generation_id(session, request.language)
                if runtime_error is None
                else None
            )
        except BaseException as exc:
            egress_decision = _egress_decision_from_failure(exc)
            if egress_decision is not None:
                return self._refuse_egress_boundary(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    None,
                    attempt_id,
                    None,
                    egress_decision,
                )
            self._finish_attempt(attempt_id, "prepare_failed", exc)
            raise
        try:
            title = self.title_factory(request.code, index)
            show_in_notebook = not (
                request.origin == "agent"
                and is_completion_only_cell(request.code, request.language)
            )
            on_chunk = (
                self._start_stream(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    kernel_id,
                    title,
                    generation_id,
                )
                if show_in_notebook
                else None
            )
        except BaseException as exc:
            self._finish_attempt(attempt_id, "projection_failed", exc)
            raise

        try:
            before = self.ports.snapshot(session.workspace)
            self.ports.protect_versions(session)
            refusal = self.ports.safety_refusal(session, request.code, request.origin)
        except BaseException as exc:
            self._finish_attempt(attempt_id, "prepare_failed", exc)
            raise
        if refusal is not None:
            return self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                refusal,
                attempt_id,
                "safety_refused",
                generation_id,
            )
        if runtime_error is not None:
            # R bootstrap is stringified before this service sees it:
            # ``R kernel unavailable: R kernel bootstrap failed:
            # egress_boundary_unavailable: …``. That happens before the
            # posture precheck below, and the refused worker has already
            # been shut down, so the precheck cannot recover the code.
            egress_decision = _egress_decision_from_failure(RuntimeError(runtime_error))
            if egress_decision is not None:
                return self._refuse_egress_boundary(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    kernel_id,
                    attempt_id,
                    generation_id,
                    egress_decision,
                )
            return self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                runtime_error,
                attempt_id,
                "runtime_unavailable",
                generation_id,
            )

        from openai4s.egress import cell_admission_refusal
        from openai4s.server.skill_network_admission import admit_cell, frame_scope

        sandbox_status = _session_sandbox_status(session, request.language)
        egress_decision = cell_admission_refusal(sandbox_status)
        if egress_decision is not None:
            return self._refuse_egress_boundary(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                attempt_id,
                generation_id,
                egress_decision,
            )
        network_decision = admit_cell(
            frame_id=session.root_frame_id,
            sandbox_status=sandbox_status,
        )
        if not network_decision.allowed:
            refused = self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                network_decision.refusal_message(),
                attempt_id,
                "skill_network_refused",
                generation_id,
            )
            if isinstance(refused.result, dict):
                refused.result["skill_network"] = network_decision.as_dict()
            return refused

        # The last check before user code. Everything above can take seconds
        # -- a fresh kernel's bootstrap runs inside `prepare_language`, before
        # any lease exists for a Stop to interrupt -- and the watchdog only
        # looks at cancellation once the Cell is already running. A daemon
        # shutdown landing in that window used to start the user's code anyway.
        if self.ports.cancelled(session):
            return self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                CELL_CANCELLED_BEFORE_START_MESSAGE,
                attempt_id,
                "interrupted",
                generation_id,
                interrupted=True,
            )

        # NB-1: a Cell that only runs a workspace script keeps that script as
        # it is now, just before it runs, so the record survives the file
        # being rewritten or deleted later.
        inlined_script = inline_cell_script(
            session.workspace, request.code, request.language
        )
        lease = session.kernels.lease("r") if request.language == "r" else None
        try:
            with frame_scope(session.root_frame_id):
                try:
                    result = self.ports.run(session, request, cell_id, on_chunk, lease)
                finally:
                    # Deliver the last batched output before anything below
                    # emits this cell's error or terminal events.
                    if on_chunk is not None:
                        on_chunk.close()
        except BaseException as exc:
            egress_decision = _egress_decision_from_failure(exc)
            if egress_decision is not None:
                # The kernel is still usable: a boundary refusal sends no
                # Cell, so the R lease stays published for a later Cell.
                return self._refuse_egress_boundary(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    kernel_id,
                    attempt_id,
                    generation_id,
                    egress_decision,
                )
            # A live R process can still be protocol-desynchronized when its
            # reader exits through a callback/parse error. Close only this lease;
            # watchdog recovery may already have advanced the generation.
            if lease is not None:
                session.kernels.shutdown_if_current(lease)
            # `str(exc)` for a worker death is
            # `kernel worker exited unexpectedly: <drained stderr tail>` --
            # the worker's uncaught traceback, an R `system()`'s output, or
            # anything any child wrote to fd2, with the absolute paths and argv
            # that come with it. It landed in the persisted cell row, the
            # `notebook_cell_finished` frame and `GET /frames/{id}/execution-log`,
            # so one crash published it on three surfaces and kept it.
            from openai4s.server.errors import public_exception

            if isinstance(exc, KernelCancellation):
                code = "cell_cancelled"
            elif isinstance(exc, TimeoutError):
                code = "cell_timeout"
            else:
                code = "kernel_execution_failed"
            public, _status = public_exception(
                exc, surface="cell:worker", error_code=code
            )
            failed_result = _error_result(cell_id, _worker_failure_text(exc, public))
            # What the cell printed before the kernel died. The live view
            # showed it, and the terminal record used to replace it with an
            # empty stdout -- so a crash erased the very output that says how
            # far the cell got.
            partial = getattr(exc, "partial_stdout", "") or (
                on_chunk.streamed_text() if on_chunk is not None else ""
            )
            if partial:
                failed_result["stdout"] = partial
            try:
                # A worker/protocol failure is still an immutable Cell
                # transaction.  Persist its source and terminal observation so
                # the session revision cannot disappear or be reused after a
                # daemon reopen.  No response/capture milestones are invented.
                self._record(
                    session,
                    request,
                    index,
                    kernel_id,
                    failed_result,
                    CaptureResult(),
                    inlined_script=inlined_script,
                )
            except BaseException as record_exc:
                self._finish_attempt(attempt_id, "record_failed", record_exc)
                raise record_exc from exc
            self._finish_attempt(
                attempt_id,
                attempt_state_for_exception(exc, otherwise="worker_died"),
                exc,
            )
            if show_in_notebook and request.stream:
                self._emit_finished(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    kernel_id,
                    failed_result,
                    CaptureResult(),
                    generation_id,
                    inlined_script=inlined_script,
                )
            raise

        result["id"] = cell_id
        result["skill_network"] = network_decision.as_dict()
        if attempt_id is not None:
            try:
                self.ports.mark_attempt_response(attempt_id)
            except BaseException as exc:
                self._finish_attempt(attempt_id, "record_failed", exc)
                raise
        if request.stream and result.get("error"):
            try:
                self._emit_error(
                    emit,
                    session.root_frame_id,
                    str(result["error"]),
                    producing_cell_id=cell_id,
                )
            except BaseException as exc:
                self._finish_attempt(attempt_id, "projection_failed", exc)
                raise
        try:
            artifact_receipts = _take_artifact_receipts(result)
            capture = self.ports.capture(
                session,
                index,
                cell_id,
                before,
                emit,
                request.language,
                artifact_receipts,
                bool(result.get("error") or result.get("interrupted")),
            )
            capture.files_read = _runtime_files_read(result)
            if attempt_id is not None:
                self.ports.mark_attempt_capture(attempt_id)
            if capture.artifacts and request.stream:
                self.ports.emit_artifact_step(
                    session,
                    title,
                    capture.artifacts,
                    emit,
                    kernel_id,
                    request.language,
                )
            if self.ports.bind_lineage is not None:
                # Stage 8 lineage is evidence used by review/completion.  A
                # failed binding must therefore make capture fail, not publish
                # a successful Cell after silently replacing its read set with
                # an empty list.
                capture.files_read = list(
                    self.ports.bind_lineage(session, request, before, capture, cell_id)
                    or []
                )
        except BaseException as exc:
            self._finish_attempt(attempt_id, "capture_failed", exc)
            raise
        try:
            self._record(
                session,
                request,
                index,
                kernel_id,
                result,
                capture,
                inlined_script=inlined_script,
            )
        except BaseException as exc:
            self._finish_attempt(attempt_id, "record_failed", exc)
            raise
        self._finish_attempt(
            attempt_id,
            attempt_state_for_result(result),
            result.get("error") or None,
        )
        if show_in_notebook and request.stream:
            self._emit_finished(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                result,
                capture,
                generation_id,
                inlined_script=inlined_script,
            )
        return CellExecutionResult(
            result,
            index,
            cell_id,
            capture,
            state_revision=index,
            generation_id=generation_id,
        )

    def _start_stream(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        index: int,
        cell_id: str,
        kernel_id: str,
        title: str,
        generation_id: str | None,
    ) -> _LiveCellStream | None:
        if not request.stream:
            return None
        # NB-4: when the cell started, on both start events, so a client that
        # reopens a session while this cell still runs (its execution-log row
        # does not exist yet) counts from here and not from the reload.
        started_at = int(time.time() * 1000)
        emit(
            {
                "type": "notebook_cell_start",
                "frame_id": session.root_frame_id,
                "root_frame_id": session.root_frame_id,
                "producing_cell_id": cell_id,
                "cell_index": index,
                "state_revision": index,
                "generation_id": generation_id,
                "kernel_id": kernel_id,
                "language": request.language,
                "origin": request.origin,
                "source": request.code,
                "title": title,
                "status": "running",
                "started_at": started_at,
            }
        )
        # Keep the text activity stream for older clients and for the chat-side
        # activity card.  ``producing_cell_id`` tells newer clients that the
        # structured Notebook lifecycle above is authoritative.
        emit(
            {
                "type": "text_chunk",
                "frame_id": session.root_frame_id,
                "block_type": "tool",
                "chunk": f"⚙{title}\n",
                "producing_cell_id": cell_id,
                "cell_index": index,
                "kernel_id": kernel_id,
                "language": request.language,
                "started_at": started_at,
            }
        )
        emit(
            {
                "type": "text_chunk",
                "frame_id": session.root_frame_id,
                "block_type": "tool",
                "chunk": request.code + "\n" + NOTEBOOK_DIVIDER + "\n",
                "producing_cell_id": cell_id,
            }
        )

        def deliver(value: str) -> None:
            emit(
                {
                    "type": "notebook_cell_chunk",
                    "frame_id": session.root_frame_id,
                    "root_frame_id": session.root_frame_id,
                    "producing_cell_id": cell_id,
                    "stream": "stdout",
                    "chunk": value,
                }
            )
            emit(
                {
                    "type": "text_chunk",
                    "frame_id": session.root_frame_id,
                    "block_type": "tool",
                    "chunk": value,
                    "producing_cell_id": cell_id,
                }
            )

        return _LiveCellStream(deliver)

    def _refuse_egress_boundary(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        index: int,
        cell_id: str,
        kernel_id: str | None,
        attempt_id: str | None,
        generation_id: str | None,
        decision: dict[str, Any],
    ) -> CellExecutionResult:
        code = str(decision.get("code") or "egress_boundary_unavailable")
        reason = str(decision.get("reason") or "")
        refused = self._soft_error(
            session,
            request,
            emit,
            index,
            cell_id,
            kernel_id,  # type: ignore[arg-type]
            f"{code}: {reason}",
            attempt_id,
            "egress_boundary_refused",
            generation_id,
        )
        if isinstance(refused.result, dict):
            refused.result["egress_boundary"] = dict(decision)
        return refused

    def _soft_error(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        index: int,
        cell_id: str,
        kernel_id: str,
        message: str,
        attempt_id: str | None,
        terminal_state: str,
        generation_id: str | None,
        *,
        interrupted: bool = False,
    ) -> CellExecutionResult:
        result = _error_result(cell_id, message)
        if interrupted:
            result["interrupted"] = True
        if attempt_id is not None:
            try:
                self.ports.mark_attempt_response(attempt_id)
            except BaseException as exc:
                self._finish_attempt(attempt_id, "record_failed", exc)
                raise
        if request.stream:
            try:
                self._emit_error(
                    emit,
                    session.root_frame_id,
                    message,
                    producing_cell_id=cell_id,
                )
            except BaseException as exc:
                self._finish_attempt(attempt_id, "projection_failed", exc)
                raise
        capture = CaptureResult()
        try:
            if attempt_id is not None:
                self.ports.mark_attempt_capture(attempt_id)
            self._record(
                session,
                request,
                index,
                kernel_id,
                result,
                capture,
            )
        except BaseException as exc:
            self._finish_attempt(attempt_id, "record_failed", exc)
            raise
        self._finish_attempt(
            attempt_id,
            terminal_state,
            # A cancellation is a user intent with a stable code, not a failure
            # message; `_finish_attempt` persists it as such.
            KernelCancellation(message) if interrupted else message,
        )
        show_in_notebook = not (
            request.origin == "agent"
            and is_completion_only_cell(request.code, request.language)
        )
        if request.stream and show_in_notebook:
            self._emit_finished(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                result,
                capture,
                generation_id,
            )
        return CellExecutionResult(
            result,
            index,
            cell_id,
            capture,
            state_revision=index,
            generation_id=generation_id,
            # No kernel touched this cell: the refusal/unavailability result
            # is synthesized.  The agent loop's evidence ledger must not count
            # it as an executed cell.
            executed=False,
        )

    def _finish_attempt(
        self,
        attempt_id: str | None,
        terminal_state: str,
        error: Any = None,
    ) -> None:
        if attempt_id is None:
            return
        payload = None
        if error not in (None, ""):
            if isinstance(error, KernelCancellation):
                # Cancellation is a user intent, not an execution failure. Its
                # stable code is safe to persist and lets the Action Timeline
                # distinguish it without rendering the watchdog exception.
                payload = {
                    "kind": "ExecutionCancelled",
                    "message": "the execution attempt was cancelled",
                    "code": "cell_cancelled",
                }
                self.ports.finish_attempt(attempt_id, terminal_state, payload)
                return
            # Generic, not redacted. This row is projected into the Action
            # Timeline the UI renders (`action_timeline._attempt` sends `error`
            # straight through) *and* written into the exported Session
            # package, which the user shares. Plan item 16 puts credential,
            # absolute-path and shell-command canaries on exactly those
            # surfaces, and redaction is the wrong instrument for them:
            # `redact_text` fingerprints credential-shaped tokens and collapses
            # only *this* account's home, so a path under another user, a
            # `/srv/...` path, or the argv of a failed spawn all survive it
            # intact. Nothing here is safe to keep, so nothing is kept.
            #
            # `kind` stays. It is the exception class's name -- `PermissionError`,
            # `EOFError` -- which is a fact about the failure's shape and carries
            # no argument, path or credential from the raised instance. It is
            # also what makes the row useful at all once the message is gone.
            #
            # The failure goes to `record_diagnostic`, which pairs it with a
            # request id a support ticket can quote. Not "the original": that
            # claim was true when written and stopped being true twice over --
            # `record_diagnostic` reaches `logs/app.out`, which the support
            # bundle now collects, and it no longer renders the exception at
            # all. What it records is the surface, the class and a fingerprint.
            from openai4s.server.errors import record_diagnostic

            if isinstance(error, BaseException):
                record_diagnostic(error, surface="cell:attempt")
            payload = {
                "kind": type(error).__name__,
                "message": "the execution attempt failed",
                "code": "attempt_failed",
            }
        self.ports.finish_attempt(attempt_id, terminal_state, payload)

    @staticmethod
    def _emit_finished(
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        index: int,
        cell_id: str,
        kernel_id: str,
        result: dict[str, Any],
        capture: CaptureResult,
        generation_id: str | None,
        *,
        inlined_script: dict[str, Any] | None = None,
    ) -> None:
        status = (
            "interrupted"
            if result.get("interrupted")
            else ("error" if result.get("error") else "ok")
        )
        emit(
            {
                "type": "notebook_cell_finished",
                "frame_id": session.root_frame_id,
                "root_frame_id": session.root_frame_id,
                "producing_cell_id": cell_id,
                "cell_index": index,
                "state_revision": index,
                "generation_id": generation_id,
                "kernel_id": kernel_id,
                "language": request.language,
                "origin": request.origin,
                "source": request.code,
                # The execution result and durable Cell record retain the full
                # observation.  Only this transient WS projection is bounded,
                # otherwise a large terminal frame can overflow and disconnect
                # an otherwise healthy client after its live chunks were capped.
                "stdout": _bounded_live_output(result.get("stdout")),
                "stderr": _bounded_live_output(result.get("stderr")),
                "error": _bounded_live_output(result.get("error")),
                "status": status,
                "figures": list(capture.figures),
                "files_written": list(capture.files_written),
                "files_read": list(capture.files_read),
                "inlined_script": inlined_script,
                "cpu_seconds": (result.get("usage") or {}).get("cpu_s"),
                "peak_rss_kb": (result.get("usage") or {}).get("peak_rss_kb"),
            }
        )

    def _record(
        self,
        session: CellSession,
        request: CellRequest,
        index: int,
        kernel_id: str,
        result: dict[str, Any],
        capture: CaptureResult,
        *,
        inlined_script: dict[str, Any] | None = None,
    ) -> None:
        completion_only = request.origin == "agent" and is_completion_only_cell(
            request.code, request.language
        )
        self.ports.record_cell(
            frame_id=session.root_frame_id,
            root_frame_id=session.root_frame_id,
            code=request.code,
            result=result,
            origin=request.origin,
            cell_seq=index,
            cell_index=index,
            state_revision=index,
            project_id=session.project_id,
            kernel_id=kernel_id,
            language=request.language,
            figures=capture.figures,
            files_written=capture.files_written,
            files_read=list(capture.files_read),
            visibility=("system" if completion_only else request.visibility),
            pin=(False if completion_only else request.pin),
            replay_policy=("never" if completion_only else request.replay_policy),
            inlined_script=inlined_script,
        )

    @staticmethod
    def _generation_id(session: CellSession, language: str) -> str | None:
        """Return the exact acquired worker UUID without synthesizing one."""

        try:
            value = session.kernels.status(language).get("generation_id")
        except Exception:  # noqa: BLE001 - projection must not break a Cell
            return None
        return str(value) if value else None

    @staticmethod
    def _emit_error(
        emit: EventSink,
        frame_id: str,
        message: str,
        *,
        producing_cell_id: str | None = None,
    ) -> None:
        emit(
            {
                "type": "text_chunk",
                "frame_id": frame_id,
                "block_type": "tool",
                "chunk": "\n" + message,
                **(
                    {"producing_cell_id": producing_cell_id}
                    if producing_cell_id
                    else {}
                ),
            }
        )


#: A run of three or more rule characters at either end of a comment line.
_BANNER = re.compile(r"^(?:[-=*~_#+]\s*){3,}|(?:\s*[-=*~_#+]){3,}$")


def activity_title(code: str, index: int) -> str:
    """Use a leading comment as the activity-card title when present."""
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # A banner is not a title: "# ---------- QC ----------" reads
            # "QC", and a rule made only of dashes is skipped for the next line.
            title = _BANNER.sub("", stripped.lstrip("#").strip()).strip()
            if title:
                return title[:90]
        elif stripped:
            break
    return f"Running analysis · cell {index}"


def _egress_decision_from_failure(exc: BaseException) -> dict[str, Any] | None:
    """Project a trusted boundary refusal, or None when ``exc`` is not one.

    Recognition is ``egress.is_boundary_refusal``: a typed boundary
    exception on the cause/context chain, or one of the two exact bootstrap
    prefixes while allowlist is on. A typed refusal carries the decision of
    the kernel that refused. A prefix refusal comes from a bootstrap
    candidate that was never published, so the session's slot -- a previous
    worker, or none -- is not its posture, and the sandbox fields stay null.

    A cancellation or timeout is the user's or the watchdog's verdict on the
    Cell. A refusal it happens to wrap does not replace it.
    """

    from openai4s.egress import boundary_refusal_decision, is_boundary_refusal

    if isinstance(exc, (KernelCancellation, TimeoutError)):
        return None
    if not is_boundary_refusal(exc):
        return None
    return boundary_refusal_decision(exc)


def _session_sandbox_status(
    session: CellSession, language: str
) -> dict[str, Any] | None:
    """Measured sandbox posture of the language slot, if a worker exists."""

    slot = "r" if str(language or "").strip().lower() == "r" else "python"
    try:
        kernel = session.kernels.kernel(slot)
    except Exception:  # noqa: BLE001 - admission fails closed on missing status
        return None
    if kernel is None:
        return None
    status = getattr(kernel, "sandbox_status", None)
    if callable(status):
        try:
            status = status()
        except Exception:  # noqa: BLE001
            return None
    return dict(status) if isinstance(status, dict) else None


def _error_result(cell_id: str, message: str) -> dict[str, Any]:
    return {
        "type": "response",
        "id": cell_id,
        "stdout": "",
        "stderr": "",
        "error": message,
        "interrupted": False,
        "trace": {"error_lineno": None, "error_call": None},
        "usage": {},
    }


__all__ = ["CellExecutionPorts", "CellExecutionService", "activity_title"]
