"""Local runtime adapters for the provider-neutral :mod:`agent.engine`.

The engine owns the turn state machine.  This module connects it to the
blocking LLM client, context compaction, persistent kernels, and the existing
dispatcher-backed control tools without importing those concrete services.
"""

from __future__ import annotations

import hashlib
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
    compact,
    estimate_context,
    externalize_large_outputs,
    safe_keep_recent,
    should_compact,
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


@dataclass
class CompactionPolicy:
    """Apply Context Policy V2 with a consecutive-low-yield breaker.

    ``metadata_provider`` is the persistence-neutral seam for Web runtimes to
    attach branch, ledger cursor, recovery pointer, and Kernel generation.  If
    omitted, the same keys are read from ``RunState.metadata``.
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

    def prepare(self, state: RunState) -> Sequence[Mapping[str, Any]]:
        if self.minimum_yield_ratio < 0 or self.minimum_yield_ratio >= 1:
            raise ValueError("minimum_yield_ratio must be in [0, 1)")
        if self.max_low_yield_attempts < 1:
            raise ValueError("max_low_yield_attempts must be positive")

        metadata = self._metadata(state)
        try:
            tool_schemas = tuple(
                self.tool_schema_provider(state)
                if self.tool_schema_provider is not None
                else ()
            )
        except Exception:  # noqa: BLE001 - schema accounting is fail-soft
            tool_schemas = ()
        try:
            context_budget = (
                self.context_budget_provider(state)
                if self.context_budget_provider is not None
                else None
            )
        except Exception:  # noqa: BLE001 - config fallback remains available
            context_budget = None
        try:
            messages = externalize_large_outputs(
                state.messages,
                self.cfg.compaction_dir,
                threshold_chars=self.large_output_chars,
                archive_metadata=metadata,
                artifact_archiver=self.artifact_archiver,
            )
        except Exception as error:  # noqa: BLE001 - preserve the live context
            state.metadata["last_externalization_error"] = str(error)[:500]
            self.log(f"[context output kept inline] Artifact archive failed: {error}")
            messages = state.messages
        before = estimate_context(messages, tool_schemas)
        state.metadata["context_estimate"] = before.as_dict()

        if not should_compact(
            messages,
            self.cfg,
            tool_schemas=tool_schemas,
            context_budget=context_budget,
        ):
            return messages
        if self.circuit_open:
            # The breaker prevents *repeated futile* compaction, not compaction
            # forever: once the context has grown materially past the size at
            # which it tripped, there is new compactible material, so reset and
            # retry.  Without this the breaker permanently disables compaction
            # for the run and the context grows unbounded into a provider 4xx.
            if before.total < self.circuit_open_total * self.circuit_retry_growth:
                self.log(
                    "[compaction skipped] circuit breaker open after "
                    f"{self.low_yield_streak} low-yield attempts"
                )
                return messages
            self.log(
                "[compaction retry] context grew "
                f"{before.total} >= {self.circuit_retry_growth}x "
                f"{self.circuit_open_total}; reopening compaction"
            )
            self.circuit_open = False
            self.low_yield_streak = 0
            self.circuit_open_total = 0

        try:
            prepared = compact(
                messages,
                self.cfg,
                keep_recent=safe_keep_recent(messages),
                archive_dir=self.cfg.compaction_dir,
                archive_metadata=metadata,
                large_output_chars=self.large_output_chars,
                artifact_archiver=self.artifact_archiver,
                archive_sink=self.archive_sink,
                tool_schemas=tool_schemas,
            )
        except Exception as error:  # noqa: BLE001 - compaction cannot kill a run
            state.metadata["last_compaction_error"] = str(error)[:500]
            self.log(f"[compaction skipped] durable archive failed: {error}")
            return messages
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
        return prepared

    def _metadata(self, state: RunState) -> CompactionArchiveMetadata:
        source = (
            self.metadata_provider(state)
            if self.metadata_provider is not None
            else state.metadata
        )
        return CompactionArchiveMetadata.from_mapping(source)


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

    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if isinstance(action, FinalizeAction):
            return execute_finalize_action(
                action, evidence=execution_evidence(state.metadata)
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
        refusal = self.pre_exec_gate(action.code, state.messages)
        if refusal is not None:
            self.log(f"[safety] cell not executed: {refusal}")
            return self._user_observation(refusal)
        hooks = self.cell_hooks
        token = hooks.before(action) if hooks is not None else None
        result: dict | None = None
        receipt_binder = getattr(self.dispatcher, "bind_artifact_receipt_scope", None)
        try:
            with (
                receipt_binder() if callable(receipt_binder) else nullcontext([])
            ) as artifact_receipts:
                if action.language == "r":
                    result = self.execute_r(action.code)
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
                            result = self.kernel.execute(action.code, origin="agent")
                    else:
                        result = self.kernel.execute(action.code, origin="agent")
                    self._record_kernel_generation(state)
            if result is not None and artifact_receipts:
                result["_openai4s_artifact_receipts"] = list(artifact_receipts)
        except BaseException:
            if hooks is not None:
                hooks.after(action, token, None)
            raise
        if hooks is not None:
            hooks.after(action, token, result)
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
        completion = getattr(self.dispatcher, "last_output", None)
        return self._user_observation(observation, completion=completion)

    def _record_kernel_generation(self, state: RunState) -> None:
        """Publish generation continuity without inventing missing identity."""
        generation = getattr(self.kernel, "generation", None)
        if generation is None:
            return
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
    "LocalActionExecutor",
    "TranscriptEventSink",
    "TranscriptTurn",
    "format_observation",
]
