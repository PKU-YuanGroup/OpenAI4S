"""Local runtime adapters for the provider-neutral :mod:`agent.engine`.

The engine owns the turn state machine.  This module connects it to the
blocking LLM client, context compaction, persistent kernels, and the existing
dispatcher-backed control tools without importing those concrete services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    execute_tool_call,
    parse_tool_calls,
    run_tool_calls,
)

from .actions import (
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    Action,
    CodeCell,
    NativeToolBatch,
    count_code_blocks,
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
from .control import execute_native_batch
from .events import AgentEvent, OutcomeProduced, ReplyReceived
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
    tools: Sequence[Any] = ()
    stream: bool = False

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
        kwargs: dict[str, Any] = {"tools": tuple(self.tools)}
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
    minimum_yield_ratio: float = 0.10
    max_low_yield_attempts: int = 2
    large_output_chars: int = DEFAULT_LARGE_OUTPUT_CHARS
    low_yield_streak: int = field(default=0, init=False)
    circuit_open: bool = field(default=False, init=False)

    def prepare(self, state: RunState) -> Sequence[Mapping[str, Any]]:
        if self.minimum_yield_ratio < 0 or self.minimum_yield_ratio >= 1:
            raise ValueError("minimum_yield_ratio must be in [0, 1)")
        if self.max_low_yield_attempts < 1:
            raise ValueError("max_low_yield_attempts must be positive")

        metadata = self._metadata(state)
        messages = externalize_large_outputs(
            state.messages,
            self.cfg.compaction_dir,
            threshold_chars=self.large_output_chars,
            archive_metadata=metadata,
        )
        before = estimate_context(messages)
        state.metadata["context_estimate"] = before.as_dict()

        if not should_compact(messages, self.cfg):
            return messages
        if self.circuit_open:
            self.log(
                "[compaction skipped] circuit breaker open after "
                f"{self.low_yield_streak} low-yield attempts"
            )
            return messages

        prepared = compact(
            messages,
            self.cfg,
            keep_recent=safe_keep_recent(messages),
            archive_dir=self.cfg.compaction_dir,
            archive_metadata=metadata,
            large_output_chars=self.large_output_chars,
        )
        after = estimate_context(prepared)
        gain = max(0, before.total - after.total)
        ratio = gain / max(1, before.total)
        state.metadata["context_estimate"] = after.as_dict()
        state.metadata["last_compaction_yield_ratio"] = ratio
        if ratio < self.minimum_yield_ratio:
            self.low_yield_streak += 1
            if self.low_yield_streak >= self.max_low_yield_attempts:
                self.circuit_open = True
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

    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        if isinstance(action, NativeToolBatch):
            return self._execute_native(action)
        if isinstance(action, CodeCell):
            return self._execute_code(action, reply, state)
        return self._execute_legacy_or_nudge(reply)

    def _execute_native(self, batch: NativeToolBatch) -> ExecutionOutcome:
        def invoke(call):
            return execute_tool_call(
                self.dispatcher,
                {"name": call.name, "arguments": call.arguments},
            )

        return execute_native_batch(batch, invoke)

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
            result = self.kernel.execute(action.code, origin="agent")
            self._record_kernel_generation(state)
        observation = format_observation(result)
        if count_code_blocks(reply.content) > 1:
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
        calls, errors = parse_tool_calls(reply.content)
        if calls or errors:
            observation = run_tool_calls(self.dispatcher, calls, errors)
        else:
            observation = NO_CODE_NUDGE
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
            self.log(
                f"\n--- turn {event.turn} (assistant) ---\n{event.reply.content}"
            )
        elif (
            isinstance(event, OutcomeProduced)
            and event.outcome.observation is not None
        ):
            content = str(event.outcome.observation)
            self.transcript.append(TranscriptTurn("observation", content))
            self.log(f"--- turn {event.turn} (observation) ---\n{content}")


def format_observation(result: dict) -> str:
    """Format one kernel result as the stable observation protocol."""
    parts = ["[Observation]"]
    out = result.get("stdout") or ""
    err = result.get("stderr") or ""
    error = result.get("error")
    if out:
        parts.append(f"stdout:\n{out.rstrip()}")
    if err:
        parts.append(f"stderr:\n{err.rstrip()}")
    if error:
        trace = result.get("trace") or {}
        line = trace.get("error_lineno")
        location = f" (cell line {line})" if line else ""
        parts.append(f"ERROR{location}:\n{error.rstrip()}")
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
