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
    # Canonical action declaration that owns this Cell. User-authored Notebook
    # Cells legitimately leave it unset; agent Cells bind it before execution
    # so every mid-cell Host RPC can inherit the same audit identity.
    action_group_id: str | None = None
    # Notebook is a projection over the immutable execution ledger.  These
    # labels never suppress persistence; they only describe how a Cell may be
    # shown and whether recovery is allowed to replay it.
    visibility: str | None = None
    pin: bool = False
    replay_policy: str | None = None


@dataclass
class CaptureResult:
    figures: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)


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
    # Whether a kernel actually ran this cell.  False for results synthesized
    # without execution — a safety-gate refusal or an unavailable runtime —
    # whose ``result`` dict is byte-identical to a real failure.  The agent
    # loop's execution-evidence ledger reads this: a refused cell must never
    # count as finalize-time evidence.
    executed: bool = True


__all__ = ["CaptureResult", "CellExecutionResult", "CellRequest"]
