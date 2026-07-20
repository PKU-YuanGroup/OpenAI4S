"""Local runtime adapters for the provider-neutral :mod:`agent.engine`.

The engine owns the turn state machine.  This module connects it to the
blocking LLM client, context compaction, persistent kernels, and the existing
dispatcher-backed control tools without importing those concrete services.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    execute_tool_call,
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
from .control import execute_native_batch, tool_parallel_policy
from .events import AgentEvent, OutcomeProduced, ReplyReceived
from .finalize import execute_finalize_action
from .models import ExecutionOutcome, ModelReply, RunState

LogFn = Callable[..., None]


def _null_log(*args: object) -> None:
    del args


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

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
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
    tool_schema_provider: Callable[
        [RunState], Sequence[Mapping[str, Any]]
    ] | None = None
    context_budget_provider: Callable[[RunState], int | None] | None = None
    artifact_archiver: Callable[
        [Any, Mapping[str, Any], dict[str, Any]], Mapping[str, Any]
    ] | None = None
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
    log: LogFn = _null_log
    tool_catalog: Any = None
    prose_nudge: str = NO_CODE_NUDGE
    action_ledger: Any = None

    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if isinstance(action, FinalizeAction):
            return execute_finalize_action(action)
        if isinstance(action, NativeToolBatch):
            return self._execute_native(action)
        if isinstance(action, CodeCell):
            return self._execute_code(action, reply, state)
        return self._execute_legacy_or_nudge(reply)

    def _execute_native(self, batch: NativeToolBatch) -> ExecutionOutcome:
        def invoke(call):
            payload = {"name": call.name, "arguments": call.arguments}
            binder = getattr(self.dispatcher, "bind_action_context", None)

            def execute():
                if self.tool_catalog is None:
                    return execute_tool_call(self.dispatcher, payload)
                return execute_tool_call(self.dispatcher, payload, self.tool_catalog)

            if not callable(binder):
                return execute()
            group_id = getattr(self.action_ledger, "current_group_id", None)
            with binder(
                {
                    "action_group_id": group_id,
                    "action_id": call.id,
                    "tool_call_id": call.id,
                }
            ):
                return execute()

        if self.tool_catalog is None:
            return execute_native_batch(
                batch,
                invoke,
                parallel_policy=tool_parallel_policy,
            )
        return execute_native_batch(
            batch,
            invoke,
            validate=lambda name, arguments: tool_validation_error(
                name, arguments, self.tool_catalog
            ),
            parallel_policy=lambda call: tool_parallel_policy(call, self.tool_catalog),
        )

    def _execute_code(
        self, action: CodeCell, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        refusal = self.pre_exec_gate(action.code, state.messages)
        if refusal is not None:
            self.log(f"[safety] cell not executed: {refusal}")
            return self._user_observation(refusal)
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

    def _execute_legacy_or_nudge(self, reply: ModelReply) -> ExecutionOutcome:
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
