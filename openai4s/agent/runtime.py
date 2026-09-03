"""Local runtime adapters for the provider-neutral :mod:`agent.engine`.

The engine owns the turn state machine.  This module connects it to the
blocking LLM client, context compaction, persistent kernels, and the existing
dispatcher-backed control tools without importing those concrete services.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    execute_tool_call,
    get_tool,
    parse_tool_calls,
    run_tool_calls,
    tool_validation_error,
)

from .actions import (
    INCOMPLETE_CELL_NUDGE,
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    NO_NATIVE_COMPLETION_NUDGE,
    Action,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    count_code_blocks,
    has_incomplete_code_block,
)
from .compaction import (
    DEFAULT_LARGE_OUTPUT_CHARS,
    CompactionArchiveMetadata,
    ContextEstimate,
    compact,
    estimate_context,
    externalize_large_outputs,
    safe_keep_recent,
)
from .control import (
    call_reaches_dispatcher,
    execute_native_batch,
    tool_parallel_policy,
)
from .events import AgentEvent, OutcomeProduced, ReplyReceived
from .finalize import (
    execute_finalize_action,
    execution_evidence,
    note_execution_evidence,
)
from .models import ExecutionOutcome, ModelReply, RunState

LogFn = Callable[..., None]


def _null_log(*args: object) -> None:
    del args


def _allow_cell(_action: CodeCell) -> None:
    return None


@dataclass(frozen=True)
class TranscriptTurn:
    role: str
    content: str


@dataclass
class CompletionSignal:
    read: Callable[[], Any]

    def completion(self) -> Any:
        return self.read()


@dataclass
class ChatModel:
    """Adapt the blocking ``chat`` function to ``ModelPort``."""

    cfg: Any
    chat_fn: Callable[..., Mapping[str, Any]]
    tools: Sequence[Any] | Callable[..., Sequence[Any]] = ()
    stream: bool = False
    cancellation: Any = None
    #: Team-mode quota gate (M2-6): called before every provider request;
    #: raises to refuse. None (the default and the CLI's value) is a no-op,
    #: so single-user behavior is untouched (INV-1).
    quota_gate: Callable[[], None] | None = None

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
        if self.quota_gate is not None:
            self.quota_gate()
        if callable(self.tools):
            try:
                source = self.tools(messages)
            except TypeError as original:
                try:
                    source = self.tools()
                except TypeError:
                    raise original
        else:
            source = self.tools
        kwargs: dict[str, Any] = {"tools": tuple(source)}
        if self.stream:
            kwargs["on_delta"] = on_delta
        if self.cancellation is not None:
            # The engine checks cancellation between turns, which does nothing
            # while a rate-limited provider holds this call through a full
            # retry budget. The transport polls this during backoff.
            kwargs["should_cancel"] = self.cancellation.cancelled
        return self.chat_fn([dict(message) for message in messages], self.cfg, **kwargs)


def _positive_float(value: Any) -> float | None:
    """Return a finite number strictly greater than zero, else ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed != parsed or parsed == float("inf"):
        return None
    return parsed


@dataclass
class CompactionPolicy:
    """Apply Context Policy V2 with low-yield and consecutive-failure breakers.

    ``metadata_provider`` is the persistence-neutral seam for Web runtimes to
    attach branch, ledger cursor, recovery pointer, and Kernel generation.  If
    omitted, the same keys are read from ``RunState.metadata``.
    ``workspace_provider`` returns the kernel cwd so oversized outputs can be
    copied next to the worker; a raising provider is treated as no workspace.
    """

    cfg: Any
    log: LogFn = _null_log
    metadata_provider: Callable[[RunState], Mapping[str, Any] | None] | None = None
    tool_schema_provider: Callable[[RunState], Sequence[Mapping[str, Any]]] | None = (
        None
    )
    context_budget_provider: Callable[[RunState], int | None] | None = None
    artifact_archiver: (
        Callable[[Any, Mapping[str, Any], dict[str, Any]], Mapping[str, Any]] | None
    ) = None
    archive_sink: Callable[[Mapping[str, Any]], Any] | None = None
    workspace_provider: Callable[[RunState], str | None] | None = None
    minimum_yield_ratio: float = 0.10
    max_low_yield_attempts: int = 2
    large_output_chars: int = DEFAULT_LARGE_OUTPUT_CHARS
    low_yield_streak: int = field(default=0, init=False)
    circuit_open: bool = field(default=False, init=False)
    # Context size (tokens) when the breaker last tripped, so it can re-open a
    # retry once genuinely new material has accumulated.
    circuit_open_total: int = field(default=0, init=False)
    # Multiple of ``circuit_open_total`` at which compaction is retried.
    circuit_retry_growth: float = 1.5
    max_failure_attempts: int = 2
    failure_streak: int = field(default=0, init=False)

    def prepare(self, state: RunState) -> Sequence[Mapping[str, Any]]:
        if self.minimum_yield_ratio < 0 or self.minimum_yield_ratio >= 1:
            raise ValueError("minimum_yield_ratio must be in [0, 1)")
        if self.max_low_yield_attempts < 1:
            raise ValueError("max_low_yield_attempts must be positive")
        if self.max_failure_attempts < 1:
            raise ValueError("max_failure_attempts must be positive")

        ratio = self._calibration_ratio(state)
        tool_schemas = self._tool_schemas(state)
        context_budget = self._context_budget(state)
        prepared = self._prepare(state, tool_schemas, context_budget, ratio)
        state.metadata["compaction_failure_streak"] = self.failure_streak
        state.metadata["compaction_circuit_open"] = self.circuit_open
        state.metadata["context_estimate_sent"] = estimate_context(
            prepared, tool_schemas
        ).total
        return prepared

    def _prepare(
        self,
        state: RunState,
        tool_schemas: Sequence[Mapping[str, Any]],
        context_budget: int | None,
        calibration: float,
    ) -> Sequence[Mapping[str, Any]]:
        metadata = self._metadata(state)
        workspace: str | None = None
        provider = self.workspace_provider
        if provider is not None:
            try:
                provided = provider(state)
            except Exception:
                provided = None
            workspace = None if provided is None else str(provided)
        try:
            messages = externalize_large_outputs(
                state.messages,
                self.cfg.compaction_dir,
                threshold_chars=self.large_output_chars,
                archive_metadata=metadata,
                artifact_archiver=self.artifact_archiver,
                workspace=workspace,
            )
        except Exception as error:  # noqa: BLE001 - preserve the live context
            state.metadata["last_externalization_error"] = str(error)[:500]
            self.log(f"[context output kept inline] Artifact archive failed: {error}")
            messages = state.messages
            # compact() externalizes again on its way in; handing it the same
            # workspace would fail the same way and count as a compaction
            # failure, tripping the breaker over a write that is best-effort.
            workspace = None
        before = estimate_context(messages, tool_schemas)
        state.metadata["context_estimate"] = before.as_dict()
        calibrated_total = before.total * calibration
        state.metadata["context_estimate_calibrated_total"] = calibrated_total

        if not self._should_trigger(
            messages,
            tool_schemas=tool_schemas,
            context_budget=context_budget,
            calibrated_total=calibrated_total,
        ):
            return messages
        if self.circuit_open:
            # The breaker prevents *repeated futile* compaction, not compaction
            # forever: once the context has grown materially past the size at
            # which it tripped, there is new compactible material, so reset and
            # retry.  Without this the breaker permanently disables compaction
            # for the run and the context grows unbounded into a provider 4xx.
            if before.total < self.circuit_open_total * self.circuit_retry_growth:
                reason = (
                    f"{self.low_yield_streak} low-yield attempts"
                    if self.low_yield_streak
                    else f"{self.failure_streak} consecutive failures"
                )
                self.log(f"[compaction skipped] circuit breaker open after {reason}")
                return messages
            self.log(
                "[compaction retry] context grew "
                f"{before.total} >= {self.circuit_retry_growth}x "
                f"{self.circuit_open_total}; reopening compaction"
            )
            # A reopened breaker gets its full budget back on both counters;
            # the failure streak counts *consecutive* failures, and the trip
            # that opened the circuit is not one of the retry's.
            self.circuit_open = False
            self.low_yield_streak = 0
            self.failure_streak = 0
            self.circuit_open_total = 0

        # compact() hands the durable record to the sink from inside its
        # archive step, before this policy has decided whether the projection
        # is adopted.  Buffer it: a rejected compaction recorded as applied
        # makes the history a restart rebuilds disagree with the one the run
        # kept using.
        deferred: list[Mapping[str, Any]] = []
        try:
            prepared = compact(
                messages,
                self.cfg,
                keep_recent=safe_keep_recent(messages),
                archive_dir=self.cfg.compaction_dir,
                archive_metadata=metadata,
                large_output_chars=self.large_output_chars,
                artifact_archiver=self.artifact_archiver,
                archive_sink=deferred.append if self.archive_sink is not None else None,
                tool_schemas=tool_schemas,
                context_budget=context_budget,
                workspace=workspace,
            )
        except Exception as error:  # noqa: BLE001 - compaction cannot kill a run
            return self._compaction_failed(state, before, error, messages)
        self.failure_streak = 0
        after = estimate_context(prepared, tool_schemas)
        gain = max(0, before.total - after.total)
        ratio = gain / max(1, before.total)
        state.metadata["context_estimate"] = after.as_dict()
        state.metadata["last_compaction_yield_ratio"] = ratio
        if ratio < self.minimum_yield_ratio:
            self.low_yield_streak += 1
            if self.low_yield_streak >= self.max_low_yield_attempts:
                self.circuit_open = True
                self.circuit_open_total = before.total
            self.log(
                "[compaction low-yield] "
                f"ratio={ratio:.3f} streak={self.low_yield_streak} "
                f"circuit_open={self.circuit_open}"
            )
        else:
            self.low_yield_streak = 0
            self.log(
                f"[compacted] messages -> {len(prepared)} "
                f"tokens {before.total}->{after.total} ({ratio:.1%} saved)"
            )

        state.metadata["compaction_low_yield_streak"] = self.low_yield_streak
        state.metadata["compaction_circuit_open"] = self.circuit_open
        # A non-shrinking summary must not replace a smaller, replay-valid
        # projection.  It still counts as a low-yield attempt and remains in
        # the audit archive, allowing the breaker to stop a second recurrence.
        if after.total >= before.total:
            state.metadata["context_estimate"] = before.as_dict()
            return messages
        if self.archive_sink is not None:
            try:
                for payload in deferred:
                    self.archive_sink(payload)
            except Exception as error:  # noqa: BLE001 - unrecorded is not adopted
                state.metadata["context_estimate"] = before.as_dict()
                return self._compaction_failed(state, before, error, messages)
        return prepared

    def _compaction_failed(
        self,
        state: RunState,
        before: ContextEstimate,
        error: BaseException,
        messages: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        self.failure_streak += 1
        state.metadata["last_compaction_error"] = str(error)[:500]
        self.log(f"[compaction skipped] durable archive failed: {error}")
        if self.failure_streak >= self.max_failure_attempts:
            self.circuit_open = True
            self.circuit_open_total = before.total
            self.log(
                "[compaction circuit open] "
                f"{self.failure_streak} consecutive failures: {error}"
            )
        return messages

    def _calibration_ratio(self, state: RunState) -> float:
        previous = _positive_float(state.metadata.get("context_estimate_calibration"))
        if previous is None:
            previous = 1.0
        actual = 0.0
        reply = state.last_reply
        if reply is not None:
            usage = reply.usage
            if isinstance(usage, Mapping):
                actual_value = _positive_float(usage.get("input_tokens"))
                actual = actual_value if actual_value is not None else 0.0
        sent = _positive_float(state.metadata.get("context_estimate_sent")) or 0.0
        if actual > 0 and sent > 0:
            ratio = min(8.0, max(0.5, actual / sent))
        else:
            ratio = previous
        state.metadata["context_estimate_calibration"] = ratio
        return ratio

    def _should_trigger(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tool_schemas: Sequence[Mapping[str, Any]],
        context_budget: int | None,
        calibrated_total: float,
    ) -> bool:
        del messages, tool_schemas  # accounted for in ``calibrated_total``
        window = int(
            context_budget
            if context_budget is not None
            else self.cfg.context_window_tokens
        )
        trigger = int(
            window * float(getattr(self.cfg, "compaction_trigger_ratio", 0.75))
        )
        return calibrated_total > trigger

    def _tool_schemas(self, state: RunState) -> tuple[Mapping[str, Any], ...]:
        try:
            return tuple(
                self.tool_schema_provider(state)
                if self.tool_schema_provider is not None
                else ()
            )
        except Exception:  # noqa: BLE001 - schema accounting is fail-soft
            return ()

    def _context_budget(self, state: RunState) -> int | None:
        try:
            budget = (
                self.context_budget_provider(state)
                if self.context_budget_provider is not None
                else None
            )
        except Exception:  # noqa: BLE001 - config fallback remains available
            return None
        # 0 is what a capability entry whose max_output equals its window
        # reports; it means "unknown", and a zero window would compact on
        # every turn with a zero chunk budget.
        return int(budget) if budget is not None and int(budget) > 0 else None

    def _metadata(self, state: RunState) -> CompactionArchiveMetadata:
        source = (
            self.metadata_provider(state)
            if self.metadata_provider is not None
            else state.metadata
        )
        return CompactionArchiveMetadata.from_mapping(source)


def _generation_json_safe(value: Any) -> Any:
    """The supervisor's JSON coercion, mirrored: canonical JSON must not fail."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_generation_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _generation_json_safe(item) for key, item in value.items()}
    return str(value)


class KernelGenerationRecorder:
    """Durable ``kernel_generations`` rows for Agent-owned local kernels.

    Web session kernels get their rows from ``KernelSupervisor``; CLI and
    delegated-child kernels are LazyKernel-spawned by the Agent itself and had
    none, so artifact environment provenance under a child frame could only
    degrade to the "assumed" daemon fallback.  The environment metadata here
    mirrors the field layout ``KernelSupervisor._begin_generation`` writes,
    without importing the supervisor (the runtime is language-normalized so
    provenance readers recognize it).  ``close`` finishes the open row; a
    child thread dying uncleanly may leave its row unfinished, which is
    acceptable — such a row is abandoned like any other live generation at
    the next daemon boot.
    """

    def __init__(self, store: Any, root_frame_id: str) -> None:
        self._store = store
        self._root_frame_id = str(root_frame_id)
        # language -> (generation_id, worker identity key)
        self._open: dict[str, tuple[str, tuple[Any, Any]]] = {}

    def observe(self, kernel: Any, *, language: str = "python") -> str | None:
        """Ensure a durable row describes the live worker; rotate on respawn."""
        inner = getattr(kernel, "current", None)
        if inner is None:
            inner = kernel
        generation = getattr(inner, "generation", None)
        if generation is None:
            open_row = self._open.get(language)
            return open_row[0] if open_row is not None else None
        # The per-instance authorization id (a fresh UUID per worker object)
        # guards against id() reuse after a replaced kernel is collected.
        identity = getattr(inner, "authorization_generation", None) or id(inner)
        key = (identity, generation)
        open_row = self._open.get(language)
        if open_row is not None and open_row[1] == key:
            return open_row[0]
        self.close(language=language, reason="kernel_respawned")
        generation_id = self._create(inner, language)
        if generation_id is not None:
            self._open[language] = (generation_id, key)
        return generation_id

    def current(self, language: str = "python") -> str | None:
        """The open durable generation id for ``language``, if any."""
        open_row = self._open.get(language)
        return open_row[0] if open_row is not None else None

    def close(
        self, *, language: str | None = None, reason: str = "run_finished"
    ) -> None:
        """Finish the open row(s); persistence failures never break a run."""
        languages = [language] if language is not None else list(self._open)
        for name in languages:
            open_row = self._open.pop(name, None)
            if open_row is None:
                continue
            try:
                self._store.finish_kernel_generation(
                    open_row[0], state="released", reason=reason
                )
            except Exception:  # noqa: BLE001 - provenance cannot break a run
                pass

    def _create(self, kernel: Any, language: str) -> str | None:
        argv = getattr(kernel, "argv", None)
        interpreter = getattr(kernel, "python", None)
        if language == "r" and isinstance(argv, (list, tuple)) and len(argv) >= 2:
            # r_kernel.r_argv ends with ``<Rscript> <r_worker.R>``.
            interpreter = argv[-2]
        environment: dict[str, Any] = {
            "key": None,
            "runtime": "r" if language == "r" else "python",
            "interpreter": interpreter,
            "worker_argv": _generation_json_safe(argv),
            "environment_root": getattr(kernel, "env_root", None),
            "environment_name": getattr(kernel, "env_name", None),
            "working_directory": getattr(kernel, "cwd", None),
        }
        try:
            sandbox = getattr(kernel, "sandbox_status", None)
            if sandbox is not None:
                environment["sandbox"] = _generation_json_safe(sandbox)
        except Exception:  # noqa: BLE001 - metadata must not break a cell
            pass
        try:
            json.dumps(environment)
        except (TypeError, ValueError):
            environment = {
                key: value
                for key, value in environment.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            }
        pid = getattr(kernel, "pid", None)
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        try:
            row = self._store.create_kernel_generation(
                root_frame_id=self._root_frame_id,
                branch_id=self._root_frame_id,
                language=language,
                environment=environment,
                bootstrap={"status": "agent_managed", "loaded_sidecars": []},
                worker_pid=pid,
                state="active",
            )
        except Exception:  # noqa: BLE001 - provenance cannot break a run
            return None
        generation_id = row.get("generation_id") if isinstance(row, dict) else None
        return str(generation_id) if generation_id else None


@dataclass
class LocalActionExecutor:
    """Execute one selected action against a run-scoped local runtime."""

    kernel: Any
    dispatcher: Any
    pre_exec_gate: Callable[[str, list[dict]], str | None]
    execute_r: Callable[[str], dict]
    admit_cell: Callable[[CodeCell], None] = _allow_cell
    cell_hooks: Any = None
    log: LogFn = _null_log
    tool_catalog: Any = None
    prose_nudge: str = NO_CODE_NUDGE
    action_ledger: Any = None
    # Applies a host.env.use() request recorded by the dispatcher callback at
    # the next Python-cell boundary — never mid-cell (the Web pending-env
    # model). None preserves the fixed-kernel contract.
    apply_pending_env: Callable[[], None] | None = None
    # Durable kernel-generation registration for Agent-owned workers; None
    # keeps the historical in-memory-only continuity metadata.
    generation_recorder: KernelGenerationRecorder | None = None

    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if isinstance(action, FinalizeAction):
            return execute_finalize_action(
                action,
                evidence=execution_evidence(state.metadata),
                code_evidence=getattr(self.dispatcher, "verify_code_evidence", None),
            )
        if isinstance(action, NativeToolBatch):
            return self._execute_native(action, state)
        if isinstance(action, CodeCell):
            # Keep the lazy runtime genuinely lazy: readiness and other local
            # admission checks run before safety, action-context binding, or a
            # Python/R worker can be created.
            self.admit_cell(action)
            return self._execute_code(action, reply, state)
        return self._execute_legacy_or_nudge(reply, state)

    def _execute_native(
        self, batch: NativeToolBatch, state: RunState
    ) -> ExecutionOutcome:
        # Evidence counts dispatched calls, not declared ones: the batch
        # answers parse/validation/limit refusals without invoking anything,
        # and an unknown tool name reaches invoke but is refused before the
        # dispatcher — none executed work. Count only calls that actually reach
        # the dispatcher, so a refused/hallucinated call cannot back a later
        # execution-shaped finalize claim. list.append is atomic under the GIL,
        # so parallel read waves count safely.
        invoked: list[Any] = []

        def invoke(call):
            if call_reaches_dispatcher(call.name, self.tool_catalog, call.arguments):
                invoked.append(call)
            payload = {"name": call.name, "arguments": call.arguments}
            binder = getattr(self.dispatcher, "bind_action_context", None)

            def execute():
                if self.tool_catalog is None:
                    return execute_tool_call(self.dispatcher, payload)
                return execute_tool_call(self.dispatcher, payload, self.tool_catalog)

            resolver = (
                get_tool
                if self.tool_catalog is None
                else getattr(self.tool_catalog, "get", None)
            )
            tool = resolver(call.name) if callable(resolver) else None
            hooks = self.cell_hooks
            before_native = getattr(hooks, "before_native", None)
            after_native = getattr(hooks, "after_native", None)
            after_native_receipts = getattr(hooks, "after_native_with_receipts", None)

            def execute_with_capture():
                metadata_for = getattr(
                    self.dispatcher, "control_tool_execution_metadata", None
                )
                metadata = metadata_for(call.name) if callable(metadata_for) else {}
                writing = tool is not None and bool(
                    metadata.get("writes_files")
                    if "writes_files" in metadata
                    else getattr(tool, "writes_files", False)
                )
                if not writing:
                    return execute()
                token = before_native(call) if callable(before_native) else None
                receipt_binder = getattr(
                    self.dispatcher, "bind_artifact_receipt_scope", None
                )
                try:
                    with (
                        receipt_binder()
                        if callable(receipt_binder)
                        else nullcontext([])
                    ) as artifact_receipts:
                        result = execute()
                except BaseException:
                    # Preserve the writing tool's primary failure. The hook
                    # records exact changed-file claims before raising, so an
                    # enclosing parent capture still fails closed if durable
                    # attribution also failed.
                    if callable(after_native):
                        try:
                            after_native(call, token, None)
                        except BaseException:
                            pass
                    raise
                if callable(after_native_receipts):
                    after_native_receipts(call, token, result, list(artifact_receipts))
                elif callable(after_native):
                    after_native(call, token, result)
                return result

            if not callable(binder):
                return execute_with_capture()
            group_id = getattr(self.action_ledger, "current_group_id", None)
            with binder(
                {
                    "action_group_id": group_id,
                    "action_id": call.id,
                    "tool_call_id": call.id,
                }
            ):
                return execute_with_capture()

        metadata_resolver = getattr(
            self.dispatcher, "control_tool_execution_metadata", None
        )
        if self.tool_catalog is None:
            outcome = execute_native_batch(
                batch,
                invoke,
                parallel_policy=lambda call: tool_parallel_policy(
                    call,
                    metadata_resolver=(
                        metadata_resolver if callable(metadata_resolver) else None
                    ),
                ),
            )
        else:
            outcome = execute_native_batch(
                batch,
                invoke,
                validate=lambda name, arguments: tool_validation_error(
                    name, arguments, self.tool_catalog
                ),
                parallel_policy=lambda call: tool_parallel_policy(
                    call,
                    self.tool_catalog,
                    metadata_resolver=(
                        metadata_resolver if callable(metadata_resolver) else None
                    ),
                ),
            )
        if invoked:
            note_execution_evidence(state.metadata, tool_calls=len(invoked))
        return outcome

    def _execute_code(
        self, action: CodeCell, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if action.language != "r" and self.apply_pending_env is not None:
            self.apply_pending_env()
        refusal = self.pre_exec_gate(action.code, state.messages)
        if refusal is not None:
            self.log(f"[safety] cell not executed: {refusal}")
            return self._user_observation(refusal)
        attempt = self._allocate_code_attempt()
        hooks = self.cell_hooks
        try:
            token = hooks.before(action) if hooks is not None else None
        except BaseException:
            if attempt is not None:
                self._finish_code_attempt(attempt, "prepare_failed")
            raise
        result: dict | None = None
        receipt_binder = getattr(self.dispatcher, "bind_artifact_receipt_scope", None)
        try:
            with (
                receipt_binder() if callable(receipt_binder) else nullcontext([])
            ) as artifact_receipts:
                if action.language == "r":
                    if attempt is None:
                        result = self.execute_r(action.code)
                    else:
                        result = self.execute_r(action.code, cell_id=attempt[2])
                else:
                    group_id = getattr(self.action_ledger, "current_group_id", None)
                    context = (
                        {
                            "action_group_id": group_id,
                            "action_id": f"{group_id}:action",
                            "tool_call_id": None,
                        }
                        if group_id
                        else None
                    )
                    binder = getattr(self.kernel, "bind_action_context", None)
                    if callable(binder):
                        with binder(context):
                            result = self.kernel.execute(
                                action.code,
                                origin="agent",
                                **({"cell_id": attempt[2]} if attempt else {}),
                            )
                    else:
                        result = self.kernel.execute(
                            action.code,
                            origin="agent",
                            **({"cell_id": attempt[2]} if attempt else {}),
                        )
                    self._record_kernel_generation(state)
                if attempt is not None:
                    assert result is not None
                    result["id"] = attempt[2]
                    durable_generation = (
                        state.metadata.get("durable_kernel_generation_id")
                        if action.language != "r"
                        else None
                    )
                    if durable_generation:
                        attempt[0].bind_execution_attempt_generation(
                            attempt[1], str(durable_generation)
                        )
                    attempt[0].mark_execution_attempt_response(attempt[1])
            if result is not None and artifact_receipts:
                result["_openai4s_artifact_receipts"] = list(artifact_receipts)
        except BaseException:
            try:
                if hooks is not None:
                    failed_result = (
                        {"id": attempt[2], "error": "host-side execution failure"}
                        if attempt is not None
                        else None
                    )
                    hooks.after(action, token, failed_result)
            finally:
                if attempt is not None:
                    self._finish_code_attempt(attempt, "failed")
            raise
        try:
            if hooks is not None:
                hooks.after(action, token, result)
            if attempt is not None:
                attempt[0].mark_execution_attempt_capture(attempt[1])
                self._finish_code_attempt(
                    attempt,
                    self._attempt_terminal_state(result),
                )
        except BaseException:
            if attempt is not None:
                self._finish_code_attempt(attempt, "record_failed")
            raise
        assert result is not None
        # Hooks consume the Host-owned evidence before durable/output
        # projection. A headless CLI has no Artifact capture and discards it;
        # the private receipt must never enter the model transcript.
        result.pop("_openai4s_artifact_receipts", None)
        # Recorded here, after the kernel ran — a safety-gate refusal above
        # returned already and must never count as finalize-time evidence.
        # The same goes for the R runner's spawn-failure / pre-execution
        # soft errors: a kernel-produced result always carries "stdout",
        # while those synthesized dicts carry only "error".
        if isinstance(result, dict) and "stdout" in result:
            note_execution_evidence(state.metadata, cells=1)
        observation = format_observation(result)
        if count_code_blocks(reply.content) > 1 or has_incomplete_code_block(
            reply.content
        ):
            observation += MULTI_CELL_NOTE
        revalidate = getattr(self.dispatcher, "revalidate_pending_completion", None)
        post_capture_error = revalidate() if callable(revalidate) else None
        if post_capture_error:
            observation += (
                "\n\n[Completion evidence rejected after cell capture]\n"
                + str(post_capture_error)
            )
        completion = getattr(self.dispatcher, "last_output", None)
        return self._user_observation(observation, completion=completion)

    def _allocate_code_attempt(self) -> tuple[Any, str, str] | None:
        """Allocate a durable CLI Cell attempt before its worker can run."""

        group_id = getattr(self.action_ledger, "current_group_id", None)
        store = getattr(self.action_ledger, "store", None)
        required = (
            "allocate_execution_attempt",
            "mark_execution_attempt_started",
            "mark_execution_attempt_response",
            "mark_execution_attempt_capture",
            "finish_execution_attempt",
        )
        if (
            not group_id
            or store is None
            or not all(callable(getattr(store, name, None)) for name in required)
        ):
            return None
        cell_id = str(uuid.uuid4())
        attempt = store.allocate_execution_attempt(
            group_id=str(group_id),
            producing_cell_id=cell_id,
        )
        attempt_id = str(attempt.get("attempt_id") or "")
        if not attempt_id:
            raise RuntimeError("execution attempt allocation returned no identity")
        try:
            store.mark_execution_attempt_started(attempt_id)
        except BaseException:
            try:
                store.finish_execution_attempt(
                    attempt_id,
                    terminal_state="record_failed",
                )
            except BaseException:
                pass
            raise
        return store, attempt_id, cell_id

    @staticmethod
    def _finish_code_attempt(attempt: tuple[Any, str, str], state: str) -> None:
        attempt[0].finish_execution_attempt(
            attempt[1],
            terminal_state=state,
        )

    @staticmethod
    def _attempt_terminal_state(result: dict | None) -> str:
        if not isinstance(result, dict):
            return "failed"
        if result.get("interrupted"):
            return "interrupted"
        error = str(result.get("error") or "")
        if "timed out" in error.lower() or "timeout" in error.lower():
            return "timed_out"
        return "failed" if error else "completed"

    def _record_kernel_generation(self, state: RunState) -> None:
        """Publish generation continuity without inventing missing identity."""
        generation = getattr(self.kernel, "generation", None)
        if generation is None:
            return
        if self.generation_recorder is not None:
            durable = self.generation_recorder.observe(self.kernel)
            if durable is not None:
                state.metadata["durable_kernel_generation_id"] = durable
        previous = state.metadata.get("active_kernel_generation")
        if previous is not None and str(previous) != str(generation):
            state.metadata["previous_kernel_generation"] = previous
            state.metadata["kernel_restarted"] = True
        state.metadata["active_kernel_generation"] = generation

    def _execute_legacy_or_nudge(
        self, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if self.tool_catalog is None:
            calls, errors = parse_tool_calls(reply.content)
        else:
            calls, errors = parse_tool_calls(reply.content, self.tool_catalog)
        if calls or errors:
            if self.tool_catalog is None:
                observation = run_tool_calls(self.dispatcher, calls, errors)
            else:
                observation = run_tool_calls(
                    self.dispatcher,
                    calls,
                    errors,
                    self.tool_catalog,
                )
            # ``run_tool_calls`` dispatches only the first
            # MAX_TOOL_CALLS_PER_TURN parsed calls; the remainder never ran.
            # Of those, count only calls naming a known tool: an unknown name
            # is refused before the dispatcher and executed nothing, so it must
            # not back a later execution-shaped finalize claim.
            executed = sum(
                1
                for call in calls[:MAX_TOOL_CALLS_PER_TURN]
                if call_reaches_dispatcher(
                    (call or {}).get("name"),
                    self.tool_catalog,
                    (call or {}).get("arguments"),
                )
            )
            if executed:
                note_execution_evidence(state.metadata, tool_calls=executed)
        elif has_incomplete_code_block(reply.content):
            observation = INCOMPLETE_CELL_NUDGE
        else:
            observation = self.prose_nudge
        return self._user_observation(observation)

    @staticmethod
    def _user_observation(
        observation: str, *, completion: Any = None
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            ({"role": "user", "content": observation},),
            observation=observation,
            completion=completion,
        )


@dataclass
class TranscriptEventSink:
    """Project typed engine events onto the stable CLI transcript."""

    transcript: list[TranscriptTurn]
    log: LogFn = _null_log

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, ReplyReceived):
            self.transcript.append(TranscriptTurn("assistant", event.reply.content))
            self.log(f"\n--- turn {event.turn} (assistant) ---\n{event.reply.content}")
        elif (
            isinstance(event, OutcomeProduced) and event.outcome.observation is not None
        ):
            content = str(event.outcome.observation)
            self.transcript.append(TranscriptTurn("observation", content))
            self.log(f"--- turn {event.turn} (observation) ---\n{content}")


# Per-section ceiling on what reaches the model. A cell that prints a 2M-char
# dataframe used to have every character forwarded, which is not a large
# observation so much as a destroyed turn: it evicts the task from the context
# window (or exceeds it outright) and bills for the privilege. The full bytes
# are not discarded — they spill to a file the agent can open, which is more
# useful than a tail it cannot search.
OBSERVATION_SECTION_BUDGET = 12_000
_PREVIEW_HEAD = 6_000
_PREVIEW_TAIL = 4_000
# Inside the workspace so the agent can open it with the relative path it is
# given (the kernel's cwd is the workspace), and under `.openai4s/` because
# that directory is already excluded from workspace snapshots — an observation
# dump must not become part of a checkpoint's content-addressed tree.
_SPILL_DIR = ".openai4s/observations"


def _spill_observation(text: str, kind: str, workspace: str | None) -> str | None:
    """Write the full section and return a WORKSPACE-RELATIVE reference.

    Relative, and content-addressed to a fixed width, on purpose. An absolute
    path would leak $HOME into the model's context and would make the rendered
    observation's length depend on where the data directory happens to live —
    which breaks byte-identical trace comparison across machines.
    """
    if not workspace:
        return None
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
    rel = f"{_SPILL_DIR}/obs-{kind}-{digest}.txt"
    try:
        target = Path(workspace) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(text, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return rel


def _budgeted(text: str, kind: str, workspace: str | None) -> str:
    """A section trimmed to the budget, with the full bytes still reachable.

    The marker states all three things the model needs: that something is
    missing, how much, and where the rest is. A silent truncation invites it to
    reason about a tail as though it were the whole.
    """
    if len(text) <= OBSERVATION_SECTION_BUDGET:
        return text
    ref = _spill_observation(text, kind, workspace)
    omitted = len(text) - _PREVIEW_HEAD - _PREVIEW_TAIL
    where = (
        f"full {len(text):,} chars at content ref={ref}"
        if ref
        else f"full {len(text):,} chars could not be saved"
    )
    marker = (
        f"\n\n[... {omitted:,} characters omitted — {where} ...]\n"
        f"[system] This is a preview, not the output. Do not infer what is in "
        f"the gap"
        + (
            f"; read the full text with open({ref!r}).read() if you need it.\n\n"
            if ref
            else ".\n\n"
        )
    )
    return text[:_PREVIEW_HEAD] + marker + text[-_PREVIEW_TAIL:]


def format_observation(result: dict) -> str:
    """Format one kernel result as the stable observation protocol.

    Oversized stdout/stderr are previewed and their full bytes spilled to a
    workspace-relative content reference the agent can open.
    """
    parts = ["[Observation]"]
    out = result.get("stdout") or ""
    err = result.get("stderr") or ""
    error = result.get("error")
    workspace = result.get("cwd")
    if out:
        parts.append(f"stdout:\n{_budgeted(out.rstrip(), 'stdout', workspace)}")
    if err:
        parts.append(f"stderr:\n{_budgeted(err.rstrip(), 'stderr', workspace)}")
    if error:
        trace = result.get("trace") or {}
        line = trace.get("error_lineno")
        location = f" (cell line {line})" if line else ""
        parts.append(f"ERROR{location}:\n{error.rstrip()}")
        parts.append(
            "[system] The cell stopped at the first exception. Statements "
            "after that line did not run, and their variables/files must not "
            "be assumed to exist. Repair with one complete cell beginning "
            "before the failed dependency; never send only a continuation "
            "fragment."
        )
        if "No module named 'host'" in str(error) or 'No module named "host"' in str(
            error
        ):
            parts.append(
                "[system] `host` is a pre-injected Python singleton. Use it "
                "directly; never `import host` or `from host import ...`."
            )
    if not out and not err and not error:
        parts.append("(no output)")
    usage = result.get("usage") or {}
    if usage:
        parts.append(
            f"[usage wall={usage.get('wall_s')}s "
            f"cpu={usage.get('cpu_s')}s rss={usage.get('peak_rss_kb')}kb]"
        )
    return "\n".join(parts)


__all__ = [
    "ChatModel",
    "CompactionPolicy",
    "CompletionSignal",
    "KernelGenerationRecorder",
    "LocalActionExecutor",
    "TranscriptEventSink",
    "TranscriptTurn",
    "format_observation",
]
