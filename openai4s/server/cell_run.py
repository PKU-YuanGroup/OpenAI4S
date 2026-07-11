"""Web-session orchestration for one scientific Python/R cell.

This service owns the transaction order (prepare -> safety -> execute -> capture
-> record) while all infrastructure stays behind injected ports. Finishing the
transaction is only an observation; it never decides that an agent task is done.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.agent.actions import is_completion_only_cell
from openai4s.execution import CaptureResult, CellExecutionResult, CellRequest
from openai4s.kernel import KernelLease, KernelSupervisor

NOTEBOOK_DIVIDER = "----- output -----"
EventSink = Callable[[dict[str, Any]], None]
ChunkSink = Callable[[str], None]


def _no_attempt_allocate(
    session: "CellSession",
    request: CellRequest,
    cell_id: str,
    action_group_id: str | None,
) -> None:
    del session, request, cell_id, action_group_id
    return None


def _no_attempt_milestone(attempt_id: str) -> None:
    del attempt_id


def _no_attempt_generation(
    attempt_id: str, session: "CellSession", language: str
) -> None:
    del attempt_id, session, language


def _no_attempt_finish(
    attempt_id: str, terminal_state: str, error: Any = None
) -> None:
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
    safety_refusal: Callable[[str, str], str | None]
    run: Callable[
        [CellSession, CellRequest, str, ChunkSink | None, KernelLease | None],
        dict[str, Any],
    ]
    capture: Callable[
        [CellSession, int, str, Any, EventSink, str], CaptureResult
    ]
    emit_artifact_step: Callable[
        [CellSession, str, list[dict], EventSink], None
    ]
    record_cell: Callable[..., None]
    allocate_attempt: Callable[
        [CellSession, CellRequest, str, str | None], str | None
    ] = _no_attempt_allocate
    bind_attempt_generation: Callable[
        [str, CellSession, str], None
    ] = _no_attempt_generation
    mark_attempt_started: Callable[[str], None] = _no_attempt_milestone
    mark_attempt_response: Callable[[str], None] = _no_attempt_milestone
    mark_attempt_capture: Callable[[str], None] = _no_attempt_milestone
    finish_attempt: Callable[[str, str, Any], None] = _no_attempt_finish


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
        session.cell_index += 1
        index = session.cell_index
        cell_id = self.id_factory()
        # Attempt identity is durable before *any* language preparation,
        # safety classification, runtime acquisition, or worker interaction.
        attempt_id = self.ports.allocate_attempt(
            session, request, cell_id, action_group_id
        )
        if attempt_id is not None:
            self.ports.mark_attempt_started(attempt_id)
        try:
            runtime_error = self.ports.prepare_language(session, request.language)
            kernel_id = self.ports.kernel_id(session, request.language)
            if attempt_id is not None and runtime_error is None:
                self.ports.bind_attempt_generation(
                    attempt_id, session, request.language
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
            refusal = self.ports.safety_refusal(request.code, request.origin)
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
            self._finish_attempt(attempt_id, "worker_died", exc)
            if show_in_notebook and request.stream:
                self._emit_finished(
                    session,
                    request,
                    emit,
                    index,
                    cell_id,
                    kernel_id,
                    _error_result(cell_id, str(exc)),
                    CaptureResult(),
                )
            raise

        result["id"] = cell_id
        if attempt_id is not None:
            self.ports.mark_attempt_response(attempt_id)
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
                self.ports.emit_artifact_step(
                    session, title, capture.artifacts, emit
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
            )
        return CellExecutionResult(result, index, cell_id, capture)

    def _start_stream(
        self,
        session: CellSession,
        request: CellRequest,
        emit: EventSink,
        index: int,
        cell_id: str,
        kernel_id: str,
        title: str,
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

        def on_chunk(text: str) -> None:
            emit(
                {
                    "type": "notebook_cell_chunk",
                    "frame_id": session.root_frame_id,
                    "root_frame_id": session.root_frame_id,
                    "producing_cell_id": cell_id,
                    "stream": "stdout",
                    "chunk": text,
                }
            )
            emit(
                {
                    "type": "text_chunk",
                    "frame_id": session.root_frame_id,
                    "block_type": "tool",
                    "chunk": text,
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
    ) -> CellExecutionResult:
        result = _error_result(cell_id, message)
        if attempt_id is not None:
            self.ports.mark_attempt_response(attempt_id)
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
            )
        return CellExecutionResult(result, index, cell_id, capture)

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
            payload = {
                "kind": type(error).__name__,
                "message": str(error),
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
    ) -> None:
        status = (
            "error"
            if result.get("error")
            else ("interrupted" if result.get("interrupted") else "ok")
        )
        emit(
            {
                "type": "notebook_cell_finished",
                "frame_id": session.root_frame_id,
                "root_frame_id": session.root_frame_id,
                "producing_cell_id": cell_id,
                "cell_index": index,
                "kernel_id": kernel_id,
                "language": request.language,
                "origin": request.origin,
                "source": request.code,
                "stdout": result.get("stdout") or "",
                "stderr": result.get("stderr") or "",
                "error": result.get("error") or "",
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
        self.ports.record_cell(
            frame_id=session.root_frame_id,
            root_frame_id=session.root_frame_id,
            code=request.code,
            result=result,
            origin=request.origin,
            cell_seq=index,
            cell_index=index,
            project_id=session.project_id,
            kernel_id=kernel_id,
            language=request.language,
            figures=capture.figures,
            files_written=capture.files_written,
            files_read=[],
        )

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
