"""Provider- and UI-neutral values for one scientific code-cell action."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CellRequest:
    code: str
    origin: str
    language: str = "python"
    stream: bool = True


@dataclass
class CaptureResult:
    figures: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)


@dataclass
class CellExecutionResult:
    result: dict[str, Any]
    cell_index: int
    cell_id: str
    capture: CaptureResult = field(default_factory=CaptureResult)
    # ``state_revision`` is the durable, session-monotonic scientific-state
    # ordinal.  It currently shares the Cell index allocation, but remains a
    # separately named contract so clients do not mistake display numbering
    # for a variable-value snapshot or recovery guarantee.
    state_revision: int | None = None
    # UUID of the exact persistent worker generation to which the durable
    # execution attempt was bound.  ``None`` is truthful for failures that
    # never acquired a worker (for example, an unavailable R runtime).
    generation_id: str | None = None


__all__ = ["CaptureResult", "CellExecutionResult", "CellRequest"]
