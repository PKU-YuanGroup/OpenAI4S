"""Web adapters that project :class:`AgentEngine` onto gateway contracts."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai4s.agent.actions import (
    INCOMPLETE_CELL_NUDGE,
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    Action,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    count_code_blocks,
    has_incomplete_code_block,
    is_completion_only_cell,
)
from openai4s.agent.control import execute_native_batch, tool_parallel_policy
from openai4s.agent.events import (
    ActionRouted,
    AgentEvent,
    OutcomeProduced,
    ReplyReceived,
    TextDelta,
    TurnStarted,
)
from openai4s.agent.finalize import execute_finalize_action
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.agent.runtime import format_observation
from openai4s.server.completions import action_narration, outcome_narration
from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    execute_tool_call,
    finalize_tool_batch,
    parse_fence_delimiter,
    parse_tool_calls,
    scan_fenced_blocks,
    strip_fenced_blocks,
    tool_validation_error,
)


def _never_cancelled() -> bool:
    return False


def _is_action_fence(fence_char: str, info: str) -> bool:
    """Match the fence kinds understood by the action/legacy routers."""
    return fence_char == "`" and info in {"", "python", "py", "r", "tool"}


def _public_prose_before_action(text: str) -> str:
    """Return visible prose before the first executable top-level fence.

    Documentation fences such as ``json`` are still hidden from this plain
    prose projection, but they do not seal the stream: prose following their
    closing delimiter remains public until a Python/R/legacy-tool action.
    """
    blocks = scan_fenced_blocks(text)
    cutoff = next(
        (
            block.start
            for block in blocks
            if _is_action_fence(block.fence_char, block.info)
        ),
        len(text),
    )
    return strip_fenced_blocks(text[:cutoff])


def _first_code_draft(text: str) -> tuple[str, str, bool] | None:
    """Project the first Python/R action fence, including an open live draft."""

    for block in scan_fenced_blocks(text):
        if not _is_action_fence(block.fence_char, block.info):
            continue
        # A legacy ``tool`` fence is not a Python/R draft and does not prevent
        # the real action router from selecting a later executable Cell.
        # Continue scanning so the live Notebook projection cannot drift from
        # ``route_action`` for replies that contain both forms.
        if block.fence_char != "`" or block.info == "tool":
            continue
        language = "r" if block.info == "r" else "python"
        return language, block.body, block.closed
    return None


class CodeDraftStreamer:
    """Publish one replace-in-place Notebook draft while the model writes.

    Drafts are transient UI projections, never Cells or execution records.  A
    stable ``draft_id`` lets the browser replace the last block as tokens arrive
    instead of appending a succession of broken-looking fragments.
    """

    _MAX_REPLY_CHARS = 200_000
    # Once a Cell fence is active, projecting the complete accumulated source
    # on every streamed newline makes both scanning and WebSocket traffic
    # quadratic for long scientific cells.  The initial scan remains eager so
    # a newly opened fence appears quickly; active drafts update in bounded
    # chunks and always receive one final projection at ReplyReceived.
    _INITIAL_SCAN_STEP = 128
    _SCAN_STEP = 512

    def __init__(self, send: Callable[[dict], None], root_frame_id: str, turn: int):
        self.send = send
        self.rid = root_frame_id
        self.draft_id = f"draft:{root_frame_id}:{turn}"
        self.acc = ""
        self.last_scan_at = 0
        self.last_source: str | None = None
        self.last_complete = False
        self.revision = 0
        self.active = False

    def feed(self, delta: str) -> None:
        if not delta or len(self.acc) >= self._MAX_REPLY_CHARS:
            return
        remaining = self._MAX_REPLY_CHARS - len(self.acc)
        self.acc += str(delta)[:remaining]
        distance = len(self.acc) - self.last_scan_at
        should_scan = (
            not self.active
            and ("\n" in str(delta) or distance >= self._INITIAL_SCAN_STEP)
        ) or (self.active and (distance >= self._SCAN_STEP or "```" in str(delta)))
        if should_scan:
            self._project()

    def finalize(self, content: str) -> None:
        self.acc = str(content or "")[: self._MAX_REPLY_CHARS]
        self._project()

    def clear(self, reason: str) -> None:
        if not self.active:
            return
        self.revision += 1
        self.send(
            {
                "type": "notebook_cell_draft",
                "frame_id": self.rid,
                "root_frame_id": self.rid,
                "draft_id": self.draft_id,
                "revision": self.revision,
                "status": "discarded",
                "reason": str(reason or "discarded")[:80],
            }
        )
        self.active = False

    def _project(self) -> None:
        self.last_scan_at = len(self.acc)
        draft = _first_code_draft(self.acc)
        if draft is None:
            return
        language, source, complete = draft
        if source == self.last_source and complete == self.last_complete:
            return
        self.revision += 1
        self.last_source = source
        self.last_complete = complete
        self.active = True
        self.send(
            {
                "type": "notebook_cell_draft",
                "frame_id": self.rid,
                "root_frame_id": self.rid,
                "draft_id": self.draft_id,
                "revision": self.revision,
                "language": language,
                "source": source,
                "complete": complete,
                "status": "ready" if complete else "drafting",
            }
        )


class ProseStreamer:
    """Stream narration while hiding top-level fenced blocks.

    ``before_first_action`` is enabled by the Web event adapter.  The default
    retains the older standalone helper behaviour for compatibility; actual
    user-visible Web turns never resume streaming after their first action.
    """

    def __init__(
        self,
        send: Callable[[dict], None],
        root_frame_id: str,
        *,
        before_first_action: bool = False,
    ):
        self.send = send
        self.rid = root_frame_id
        self.before_first_action = before_first_action
        self.acc = ""
        self.line_buf = ""
        self.fence_stack: list[tuple[str, int]] = []
        self.action_started = False
        self.emitted_any = False
        self.emitted = ""

    def feed(self, delta: str) -> None:
        self.acc += delta
        self.line_buf += delta
        out: list[str] = []
        while True:
            newline = self.line_buf.find("\n")
            if newline < 0:
                break
            line = self.line_buf[: newline + 1]
            self.line_buf = self.line_buf[newline + 1 :]
            delimiter = parse_fence_delimiter(line)
            if delimiter:
                fence_char, fence_length, info = delimiter
                if not self.fence_stack:
                    if _is_action_fence(fence_char, info):
                        self.action_started = True
                    self.fence_stack.append((fence_char, fence_length))
                elif (
                    fence_char != self.fence_stack[-1][0]
                    or fence_length < self.fence_stack[-1][1]
                ):
                    pass
                elif info:
                    self.fence_stack.append((fence_char, fence_length))
                else:
                    self.fence_stack.pop()
            elif not self.fence_stack and not (
                self.before_first_action and self.action_started
            ):
                out.append(line)
        self._emit_prose("".join(out))

    def _emit_prose(self, chunk: str) -> None:
        if not chunk:
            return
        self.send(
            {
                "type": "text_chunk",
                "frame_id": self.rid,
                "block_type": "text",
                "chunk": chunk,
            }
        )
        self.emitted += chunk
        self.emitted_any = True

    def finalize(self) -> None:
        target = (
            _public_prose_before_action(self.acc)
            if self.before_first_action
            else strip_fenced_blocks(self.acc)
        )
        if target.startswith(self.emitted) and len(target) > len(self.emitted):
            self._emit_prose(target[len(self.emitted) :])


@dataclass
class WebEventSink:
    """Translate typed engine events to stable WebSocket/store projections."""

    send: Callable[[dict], None]
    root_frame_id: str
    assistant_visible: list[dict]
    add_usage: Callable[[dict], None]
    language: str = "en"
    narrate_actions: bool = True
    cancelled: Callable[[], bool] = _never_cancelled
    action_ledger: Any | None = None
    current_prose: str = field(default="", init=False)
    model_prose: str = field(default="", init=False)
    _streamer: ProseStreamer | None = field(default=None, init=False)
    _code_draft: CodeDraftStreamer | None = field(default=None, init=False)
    _current_action: Action | None = field(default=None, init=False)

    def emit(self, event: AgentEvent) -> None:
        # Persist the canonical engine event before projecting it to transient
        # WebSocket/UI state.  If persistence fails, execution must not advance
        # past an unrecorded action boundary.
        if self.action_ledger is not None:
            self.action_ledger.emit(event)
        if isinstance(event, TurnStarted):
            self.current_prose = ""
            self.model_prose = ""
            self._current_action = None
            self._streamer = ProseStreamer(
                self.send,
                self.root_frame_id,
                before_first_action=True,
            )
            self._code_draft = CodeDraftStreamer(
                self.send,
                self.root_frame_id,
                event.turn,
            )
        elif isinstance(event, TextDelta):
            self._ensure_streamer().feed(event.text)
            if self._code_draft is not None:
                self._code_draft.feed(event.text)
        elif isinstance(event, ReplyReceived):
            streamer = self._ensure_streamer()
            streamer.finalize()
            if self._code_draft is not None:
                self._code_draft.finalize(event.reply.content)
            if event.reply.usage:
                self.add_usage(event.reply.usage)
            prose = _public_prose_before_action(event.reply.content).strip()
            self.model_prose = prose
            self.current_prose = prose
            if prose:
                self.assistant_visible.append(
                    {"at": int(time.time() * 1000) - 1, "text": prose}
                )
                if not streamer.emitted_any:
                    self.send(
                        {
                            "type": "text_chunk",
                            "frame_id": self.root_frame_id,
                            "block_type": "text",
                            "chunk": prose + "\n",
                        }
                    )
        elif isinstance(event, ActionRouted):
            self._current_action = event.action
            if self._code_draft is not None and (
                not isinstance(event.action, CodeCell)
                or is_completion_only_cell(event.action)
            ):
                self._code_draft.clear("not_executed")
            if self.narrate_actions and not self.current_prose and not self.cancelled():
                self._publish(
                    action_narration(event.action, self.language), before_action=True
                )
        elif isinstance(event, OutcomeProduced):
            if self._code_draft is not None:
                self._code_draft.clear("action_finished")
            if self.narrate_actions and not self.cancelled():
                self._publish(
                    outcome_narration(
                        self._current_action,
                        event.outcome,
                        self.language,
                        had_public_prose=bool(self.model_prose),
                    ),
                    before_action=False,
                )

    def _ensure_streamer(self) -> ProseStreamer:
        if self._streamer is None:
            self._streamer = ProseStreamer(
                self.send,
                self.root_frame_id,
                before_first_action=True,
            )
        return self._streamer

    def _publish(self, prose: str, *, before_action: bool) -> None:
        if not prose:
            return
        self.current_prose = prose
        self.assistant_visible.append(
            {
                "at": int(time.time() * 1000) - (1 if before_action else 0),
                "text": prose,
            }
        )
        self.send(
            {
                "type": "text_chunk",
                "frame_id": self.root_frame_id,
                "block_type": "text",
                "chunk": prose + "\n",
            }
        )


@dataclass
class EventCancellation:
    event: Any

    def cancelled(self) -> bool:
        return bool(self.event.is_set())


@dataclass
class WebActionExecutor:
    """Execute routed actions through the gateway's persistent session runtime."""

    dispatcher: Callable[[], Any]
    apply_pending: Callable[[], None]
    execute_cell: Callable[[CodeCell], dict]
    events: WebEventSink
    prose_nudge: str
    explore_nudge: str
    native_wrapper: (
        Callable[[Any, Callable[[], tuple[str, bool]]], tuple[str, bool]] | None
    ) = None
    explore_mode: bool = False
    plan_mode: bool = False
    finalize_plan: Callable[[ModelReply, str], None] | None = None
    cancelled: Callable[[], bool] = _never_cancelled
    tool_catalog: Any = None

    def execute(
        self, action: Action | None, reply: ModelReply, state: RunState
    ) -> ExecutionOutcome:
        del state
        if self.cancelled():
            if isinstance(action, NativeToolBatch):
                return self._refuse_native(
                    action, "run was cancelled before execution", "cancelled"
                )
            if isinstance(action, FinalizeAction):
                return execute_finalize_action(
                    action,
                    refusal="run was cancelled before execution",
                    stop_reason="cancelled",
                )
            return ExecutionOutcome(stop_reason="cancelled")
        if self.plan_mode:
            return self._capture_plan(action, reply)
        if isinstance(action, FinalizeAction):
            return execute_finalize_action(action)
        if isinstance(action, NativeToolBatch):
            kwargs = {
                "cancelled": self.cancelled,
                "prepare_group": self.apply_pending,
                "parallel_policy": lambda call: tool_parallel_policy(
                    call, self.tool_catalog
                ),
            }
            if self.tool_catalog is not None:
                kwargs["validate"] = lambda name, arguments: tool_validation_error(
                    name, arguments, self.tool_catalog
                )
            outcome = execute_native_batch(
                action,
                lambda call: self._invoke_native(call, apply_pending=False),
                **kwargs,
            )
            if self.cancelled():
                return outcome
            return self._apply_trailing_pending(outcome)
        if isinstance(action, CodeCell):
            self.apply_pending()
            result = self.execute_cell(action)
            observation = format_observation(result)
            if count_code_blocks(reply.content) > 1 or has_incomplete_code_block(
                reply.content
            ):
                observation += MULTI_CELL_NOTE
            completion = getattr(self.dispatcher(), "last_output", None)
            return self._user_observation(observation, completion=completion)
        return self._legacy_or_nudge(reply)

    def _capture_plan(
        self, action: Action | None, reply: ModelReply
    ) -> ExecutionOutcome:
        if self.finalize_plan is not None:
            self.finalize_plan(reply, self.events.current_prose)
        if isinstance(action, NativeToolBatch):
            return self._refuse_native(
                action, "tools are disabled in plan mode", "plan"
            )
        if isinstance(action, FinalizeAction):
            return execute_finalize_action(
                action,
                refusal="structured finalization is disabled in plan mode",
                stop_reason="plan",
            )
        return ExecutionOutcome(stop_reason="plan")

    @staticmethod
    def _refuse_native(
        action: NativeToolBatch, reason: str, stop_reason: str
    ) -> ExecutionOutcome:
        # Cancellation and plan mode happen *before* execution.  Close the
        # provider-native batch with one canonical result per declaration, but
        # do not run ordinary argument/schema validation: no tool was eligible
        # to execute, and a validation error would incorrectly hide the real
        # terminal reason (and make plan/cancel replay misleading).
        parts: list[str] = []
        history: list[dict] = []
        for call in action.calls:
            text = f"[Tool error] {call.name or '<unnamed>'}: {reason}"
            parts.append(text)
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "wire_id": call.wire_id,
                    "name": call.name,
                    "content": text,
                    "is_error": True,
                }
            )
        return ExecutionOutcome(
            tuple(history),
            observation=finalize_tool_batch(parts, len(action.calls), []),
            stop_reason=stop_reason,
        )

    def _invoke_native(self, call, *, apply_pending: bool = True) -> tuple[str, bool]:
        if apply_pending:
            self.apply_pending()
        payload = (
            {"name": call.name, "arguments": call.arguments}
            if hasattr(call, "name")
            else call
        )

        def invoke() -> tuple[str, bool]:
            if self.tool_catalog is None:
                return execute_tool_call(self.dispatcher(), payload)
            return execute_tool_call(self.dispatcher(), payload, self.tool_catalog)

        return self.native_wrapper(call, invoke) if self.native_wrapper else invoke()

    def _apply_trailing_pending(self, outcome: ExecutionOutcome) -> ExecutionOutcome:
        try:
            self.apply_pending()
            return outcome
        except Exception as exc:  # noqa: BLE001 — keep native history replayable
            notice = f"[Tool error] pending environment switch failed: {exc}"
            history = [dict(message) for message in outcome.history_messages]
            if history:
                target = next(
                    (
                        index
                        for index in range(len(history) - 1, -1, -1)
                        if not history[index].get("is_error")
                    ),
                    len(history) - 1,
                )
                history[target]["content"] += "\n" + notice
                history[target]["is_error"] = True
            observation = str(outcome.observation or "") + "\n" + notice
            return ExecutionOutcome(tuple(history), observation=observation)

    def _legacy_or_nudge(self, reply: ModelReply) -> ExecutionOutcome:
        if self.tool_catalog is None:
            calls, errors = parse_tool_calls(reply.content)
        else:
            calls, errors = parse_tool_calls(reply.content, self.tool_catalog)
        if calls or errors:
            parts: list[str] = []
            for call in calls[:MAX_TOOL_CALLS_PER_TURN]:
                if self.cancelled():
                    errors.append("remaining legacy tool calls skipped: cancelled")
                    break
                try:
                    text, _ok = self._invoke_native(call)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"pending environment switch failed: {exc}")
                    break
                parts.append(text)
            if not self.cancelled():
                try:
                    self.apply_pending()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"pending environment switch failed: {exc}")
            observation = finalize_tool_batch(parts, len(calls), errors)
        elif has_incomplete_code_block(reply.content):
            observation = INCOMPLETE_CELL_NUDGE
        elif self.events.current_prose:
            observation = self.explore_nudge if self.explore_mode else self.prose_nudge
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


__all__ = [
    "CodeDraftStreamer",
    "EventCancellation",
    "ProseStreamer",
    "WebActionExecutor",
    "WebEventSink",
]
