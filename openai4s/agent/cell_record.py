"""Durable execution_log recording for delegated child Cells.

Every Cell a delegated child executes — failed and interrupted ones included —
becomes an append-only ``execution_log`` row keyed
``frame_id = root_frame_id = <child delegate frame>`` with
``origin="delegate"``.  Keying under the child frame gives each child an
independent ``state_revision`` cursor (no contention with the parent cursor or
concurrent siblings) and keeps child cells out of the root Notebook/branch
projection by construction: ``frame_detail(child)`` and
``GET /frames/{child}/execution-log`` serve them with zero projection changes,
and the lineage view's ``cell_recorded`` flips to true through its existing
``cell_detail`` lookup.

``DelegatedCellRecorder`` implements the same duck-typed cell-hooks contract
``LocalActionExecutor._execute_code`` already calls (``before``/``after``);
``ComposedCellHooks`` lets it run alongside the stage-1 Web Artifact capture
hooks, which stay optional while recording is unconditional.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, List


class DelegatedCellRecorder:
    """Record each delegated child Cell as a durable ``execution_log`` row.

    ``generation_id_for`` maps a language to the child's current durable
    kernel generation id (the same registration seam
    ``KernelGenerationRecorder`` maintains); it is bound after the child Agent
    exists via :meth:`bind_generation_source`.

    The recorder never raises into the executor: the Cell already ran, so
    losing its observation is strictly better than failing the child run —
    and the capture hooks composed after it must still get their turn.
    """

    def __init__(
        self,
        store: Any,
        frame_id: str,
        *,
        generation_id_for: Callable[[str], str | None] | None = None,
        log: Callable[[str], None] | None = None,
        origin: str = "delegate",
    ) -> None:
        # ``origin`` defaults to the delegated-child stamp this recorder was
        # built for; a root CLI Agent reuses the same recorder with
        # ``origin="agent"`` (the log_cell default the Web path records), so
        # an explicit code-mode run's test_evidence can name real rows.
        self._store = store
        self._frame_id = str(frame_id)
        self._generation_id_for = generation_id_for
        self._log = log
        self._origin = str(origin)
        self._lock = threading.Lock()
        self._ordinal: int | None = None
        self._project_id: str | None = None

    def bind_generation_source(
        self, generation_id_for: Callable[[str], str | None]
    ) -> None:
        """Late-bind the generation reader (the Agent exists after the hooks)."""
        self._generation_id_for = generation_id_for

    # -- the duck-typed cell-hooks contract ------------------------------
    def before(self, _action: object) -> None:
        return None

    def after(self, action: object, _token: object, result: object) -> None:
        try:
            self._record(action, result)
        except Exception as error:  # noqa: BLE001 - see class docstring
            if self._log is not None:
                try:
                    self._log(f"[delegate] cell record failed: {error}")
                except Exception:  # noqa: BLE001 - logging is best effort
                    pass

    # -- internals -------------------------------------------------------
    def _record(self, action: object, result: object) -> None:
        language = str(getattr(action, "language", None) or "python")
        code = str(getattr(action, "code", "") or "")
        if isinstance(result, dict):
            row_result: dict[str, Any] = result
        else:
            # agent/runtime.py calls after(action, token, None) when the
            # execution raised host-side; the exception itself is still
            # propagating there, so this row records that the Cell ran and
            # died without a kernel result — not the traceback.
            row_result = {
                "error": (
                    "host-side execution failure: the cell raised before a "
                    "kernel result was produced"
                ),
            }
        generation_id: str | None = None
        if self._generation_id_for is not None:
            try:
                value = self._generation_id_for(language)
                generation_id = str(value) if value else None
            except Exception:  # noqa: BLE001 - provenance stays optional
                generation_id = None
        with self._lock:
            ordinal = self._next_ordinal_locked()
            self._store.log_cell(
                frame_id=self._frame_id,
                root_frame_id=self._frame_id,
                project_id=self._project_locked(),
                code=code,
                result=row_result,
                origin=self._origin,
                cell_seq=ordinal,
                cell_index=ordinal,
                kernel_id=("r" if language == "r" else "python"),
                language=language,
                generation_id=generation_id,
            )

    def _next_ordinal_locked(self) -> int:
        if self._ordinal is None:
            # A child frame is freshly minted per run, so this normally seeds
            # at zero; reading the cursor keeps a reused frame append-safe.
            latest = 0
            reader = getattr(self._store, "latest_state_revision", None)
            if callable(reader):
                try:
                    latest = int(reader(self._frame_id) or 0)
                except Exception:  # noqa: BLE001 - seed conservatively
                    latest = 0
            self._ordinal = latest
        self._ordinal += 1
        return self._ordinal

    def _project_locked(self) -> str:
        if self._project_id is None:
            project = None
            reader = getattr(self._store, "get_frame", None)
            if callable(reader):
                try:
                    row = reader(self._frame_id)
                except Exception:  # noqa: BLE001 - scope falls back to default
                    row = None
                if isinstance(row, dict):
                    project = row.get("project_id")
            self._project_id = str(project) if project else "default"
        return self._project_id


class ComposedCellHooks:
    """Run several duck-typed cell hooks as one.

    ``before``/``before_native`` collect one token per inner hook; the
    ``after`` variants hand each inner hook back its own token.  Every inner
    ``after`` runs even when an earlier one raises — the first error is
    re-raised afterwards, preserving the single-hook failure contract while
    guaranteeing the recorder half its write.
    """

    def __init__(self, *hooks: Any) -> None:
        self._hooks: List[Any] = [hook for hook in hooks if hook is not None]

    def _tokens(self, token: object) -> List[Any]:
        if isinstance(token, list) and len(token) == len(self._hooks):
            return token
        return [None] * len(self._hooks)

    def before(self, action: object) -> List[Any]:
        tokens: List[Any] = []
        for hook in self._hooks:
            fn = getattr(hook, "before", None)
            tokens.append(fn(action) if callable(fn) else None)
        return tokens

    def after(self, action: object, token: object, result: object) -> None:
        self._fan_out("after", (action,), token, (result,))

    def before_native(self, action: object) -> List[Any]:
        tokens: List[Any] = []
        for hook in self._hooks:
            fn = getattr(hook, "before_native", None)
            tokens.append(fn(action) if callable(fn) else None)
        return tokens

    def after_native(self, call: object, token: object, result: object) -> None:
        self._fan_out("after_native", (call,), token, (result,))

    def after_native_with_receipts(
        self,
        call: object,
        token: object,
        result: object,
        receipts: List[Any],
    ) -> None:
        first_error: BaseException | None = None
        for hook, hook_token in zip(self._hooks, self._tokens(token)):
            with_receipts = getattr(hook, "after_native_with_receipts", None)
            plain = getattr(hook, "after_native", None)
            try:
                if callable(with_receipts):
                    with_receipts(call, hook_token, result, list(receipts))
                elif callable(plain):
                    plain(call, hook_token, result)
            except BaseException as error:  # noqa: BLE001 - first error wins
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _fan_out(
        self,
        method: str,
        head: tuple,
        token: object,
        tail: tuple,
    ) -> None:
        first_error: BaseException | None = None
        for hook, hook_token in zip(self._hooks, self._tokens(token)):
            fn = getattr(hook, method, None)
            if not callable(fn):
                continue
            try:
                fn(*head, hook_token, *tail)
            except BaseException as error:  # noqa: BLE001 - first error wins
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


def compose_cell_hooks(*hooks: Any) -> Any | None:
    """One hooks object (or None) from any mix of optional hooks.

    A single present hook is returned unwrapped so its token shapes and
    failure behavior stay byte-identical to the pre-composition wiring.
    """
    present = [hook for hook in hooks if hook is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return ComposedCellHooks(*present)


__all__ = [
    "ComposedCellHooks",
    "DelegatedCellRecorder",
    "compose_cell_hooks",
]
