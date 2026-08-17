"""Web-session orchestration for one scientific Python/R cell.

This service owns the transaction order (prepare -> safety -> execute -> capture
-> record) while all infrastructure stays behind injected ports. Finishing the
transaction is only an observation; it never decides that an agent task is done.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.agent.actions import is_completion_only_cell
from openai4s.execution import CaptureResult, CellExecutionResult, CellRequest
from openai4s.kernel import KernelLease, KernelSupervisor

NOTEBOOK_DIVIDER = "----- output -----"
LIVE_CELL_OUTPUT_CHARS = 1_000_000
LIVE_OUTPUT_TRUNCATION = "\n...(live output truncated)"
EventSink = Callable[[dict[str, Any]], None]
ChunkSink = Callable[[str], None]


def _bounded_live_output(value: Any) -> str:
    """Bound one transient WebSocket field without mutating the real result."""

    text = str(value or "")
    if len(text) <= LIVE_CELL_OUTPUT_CHARS:
        return text
    return text[:LIVE_CELL_OUTPUT_CHARS] + LIVE_OUTPUT_TRUNCATION


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
    capture: Callable[[CellSession, int, str, Any, EventSink, str], CaptureResult]
    emit_artifact_step: Callable[[CellSession, str, list[dict], EventSink], None]
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


#: Author-written, never a rendering of an exception. The timeout wording keeps
#: the actionable half of what the watchdog knows -- that the kernel was reset,
#: so earlier variables are gone -- without quoting it.
KERNEL_FAILURE_MESSAGE = "the kernel did not finish this cell"
CELL_TIMEOUT_MESSAGE = (
    "the cell exceeded its time limit and was stopped; the kernel was reset, so "
    "variables from earlier cells were cleared"
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
    elif isinstance(exc, TimeoutError):
        base = CELL_TIMEOUT_MESSAGE
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
        with self.ports.capture_lease(session, request):
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

        lease = session.kernels.lease("r") if request.language == "r" else None
        try:
            result = self.ports.run(session, request, cell_id, on_chunk, lease)
        except BaseException as exc:
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

            code = (
                "cell_timeout"
                if isinstance(exc, TimeoutError)
                else "kernel_execution_failed"
            )
            public, _status = public_exception(
                exc, surface="cell:worker", error_code=code
            )
            failed_result = _error_result(cell_id, _worker_failure_text(exc, public))
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
                )
            except BaseException as record_exc:
                self._finish_attempt(attempt_id, "record_failed", record_exc)
                raise record_exc from exc
            self._finish_attempt(attempt_id, "worker_died", exc)
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
                )
            raise

        result["id"] = cell_id
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
            capture = self.ports.capture(
                session,
                index,
                cell_id,
                before,
                emit,
                request.language,
            )
            if attempt_id is not None:
                self.ports.mark_attempt_capture(attempt_id)
            if capture.artifacts and request.stream:
                self.ports.emit_artifact_step(session, title, capture.artifacts, emit)
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
            )
        except BaseException as exc:
            self._finish_attempt(attempt_id, "record_failed", exc)
            raise
        self._finish_attempt(
            attempt_id,
            _terminal_state(result),
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
    ) -> ChunkSink | None:
        if not request.stream:
            return None
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

        streamed_chars = 0
        stream_truncated = False

        def on_chunk(text: str) -> None:
            nonlocal streamed_chars, stream_truncated
            if stream_truncated:
                return
            value = str(text or "")
            remaining = max(0, LIVE_CELL_OUTPUT_CHARS - streamed_chars)
            if len(value) > remaining:
                value = value[:remaining] + LIVE_OUTPUT_TRUNCATION
                stream_truncated = True
            streamed_chars += min(len(str(text or "")), remaining)
            if not value:
                return
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

        return on_chunk

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
    ) -> CellExecutionResult:
        result = _error_result(cell_id, message)
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
        self._finish_attempt(attempt_id, terminal_state, message)
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
                "files_read": [],
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
            files_read=[],
            visibility=("system" if completion_only else request.visibility),
            pin=(False if completion_only else request.pin),
            replay_policy=("never" if completion_only else request.replay_policy),
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


def activity_title(code: str, index: int) -> str:
    """Use a leading comment as the activity-card title when present."""
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:90]
        elif stripped:
            break
    return f"Running analysis · cell {index}"


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


def _terminal_state(result: dict[str, Any]) -> str:
    if result.get("interrupted"):
        return "interrupted"
    error = str(result.get("error") or "")
    lowered = error.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timed_out"
    if error:
        return "failed"
    return "completed"


__all__ = ["CellExecutionPorts", "CellExecutionService", "activity_title"]
