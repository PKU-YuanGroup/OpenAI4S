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

from openai4s.execution import CaptureResult, CellExecutionResult, CellRequest
from openai4s.kernel import KernelLease, KernelSupervisor

NOTEBOOK_DIVIDER = "----- output -----"
EventSink = Callable[[dict[str, Any]], None]
ChunkSink = Callable[[str], None]


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
        [CellSession, CellRequest, ChunkSink | None, KernelLease | None],
        dict[str, Any],
    ]
    capture: Callable[
        [CellSession, int, str, Any, EventSink, str], CaptureResult
    ]
    emit_artifact_step: Callable[
        [CellSession, str, list[dict], EventSink], None
    ]
    record_cell: Callable[..., None]


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
        self, session: CellSession, request: CellRequest, emit: EventSink
    ) -> CellExecutionResult:
        session.cell_index += 1
        index = session.cell_index
        cell_id = self.id_factory()
        runtime_error = self.ports.prepare_language(session, request.language)
        kernel_id = self.ports.kernel_id(session, request.language)
        title = self.title_factory(request.code, index)
        on_chunk = self._start_stream(
            session, request, emit, index, kernel_id, title
        )

        before = self.ports.snapshot(session.workspace)
        self.ports.protect_versions(session)
        refusal = self.ports.safety_refusal(request.code, request.origin)
        if refusal is not None:
            return self._soft_error(
                session,
                request,
                emit,
                index,
                cell_id,
                kernel_id,
                refusal,
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
            )

        lease = session.kernels.lease("r") if request.language == "r" else None
        try:
            result = self.ports.run(session, request, on_chunk, lease)
        except BaseException:
            # A live R process can still be protocol-desynchronized when its
            # reader exits through a callback/parse error. Close only this lease;
            # watchdog recovery may already have advanced the generation.
            if lease is not None:
                session.kernels.shutdown_if_current(lease)
            raise

        result["id"] = cell_id
        if request.stream and result.get("error"):
            self._emit_error(emit, session.root_frame_id, str(result["error"]))
        capture = self.ports.capture(
            session,
            index,
            cell_id,
            before,
            emit,
            request.language,
        )
        if capture.artifacts and request.stream:
            self.ports.emit_artifact_step(
                session, title, capture.artifacts, emit
            )
        self._record(
            session,
            request,
            index,
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
        kernel_id: str,
        title: str,
    ) -> ChunkSink | None:
        if not request.stream:
            return None
        emit(
            {
                "type": "text_chunk",
                "frame_id": session.root_frame_id,
                "block_type": "tool",
                "chunk": f"⚙{title}\n",
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
            }
        )

        def on_chunk(text: str) -> None:
            emit(
                {
                    "type": "text_chunk",
                    "frame_id": session.root_frame_id,
                    "block_type": "tool",
                    "chunk": text,
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
    ) -> CellExecutionResult:
        result = _error_result(cell_id, message)
        if request.stream:
            self._emit_error(emit, session.root_frame_id, message)
        capture = CaptureResult()
        self._record(
            session,
            request,
            index,
            kernel_id,
            result,
            capture,
        )
        return CellExecutionResult(result, index, cell_id, capture)

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
    def _emit_error(emit: EventSink, frame_id: str, message: str) -> None:
        emit(
            {
                "type": "text_chunk",
                "frame_id": frame_id,
                "block_type": "tool",
                "chunk": "\n" + message,
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


__all__ = ["CellExecutionPorts", "CellExecutionService", "activity_title"]
