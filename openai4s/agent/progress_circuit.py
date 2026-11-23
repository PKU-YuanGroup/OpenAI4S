"""Generic Agent no-progress circuit.

The Action Ledger — its action groups plus the latest external user message —
is the only durable authority. ``RunState.metadata`` holds a process-local
cache of the reconstructed object and must never be treated as persistence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol, Sequence

from .actions import (
    ANNOUNCED_CELL_NOTE,
    CODE_RUNNING_TOOLS,
    ENGINE_NOTE_PREFIX,
    RUN_CELL_NAME,
    Action,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
)
from .models import ExecutionOutcome, ModelReply, RunState

#: An Engine ``stop_reason`` (and the ``frame_update.code`` a failed turn
#: carries). Distinct from Auto Mode's ``loop_detected`` terminal, whose
#: ``no_progress_turn_limit`` is a budget *field*; the harness fixture's
#: ``loop_kind: no_progress`` mirrors that field name, not this reason.
NO_PROGRESS_STOP_REASON = "no_progress"
PROGRESS_REASON_SAME_ACTION = "same_action"
PROGRESS_REASON_SAME_TARGET = "same_target"
PROGRESS_REASON_MALFORMED = "consecutive_malformed"
PROGRESS_REASON_TOOL_ERROR = "similar_tool_error"
PROGRESS_REASON_LONG_TEXT = "long_text_repeat"
PROGRESS_REASON_EMPTY_REPLY = "empty_reply_repeat"
PROGRESS_REASON_UNSENT_CELL = "announced_cell_repeat"
PROGRESS_REASONS = frozenset(
    {
        PROGRESS_REASON_SAME_ACTION,
        PROGRESS_REASON_SAME_TARGET,
        PROGRESS_REASON_MALFORMED,
        PROGRESS_REASON_TOOL_ERROR,
        PROGRESS_REASON_LONG_TEXT,
        PROGRESS_REASON_EMPTY_REPLY,
        PROGRESS_REASON_UNSENT_CELL,
    }
)

# A repeated successful call is answered in two stages. Nothing in an
# unchanged result says "this is a loop", so the third identical call used to
# end the turn as failed with no warning: three `query {"sql": "SELECT 1"}`
# no-ops cost a session its turn. At the threshold the Engine now appends a
# reminder that names the call; the turn is stopped only when the same call
# keeps coming for `*_GRACE` more replies. Errors and malformed calls keep
# their single stage: the error text is already that feedback.
SAME_ACTION_THRESHOLD = 3
SAME_ACTION_GRACE = 2
# The same file reached again through a different window, or rewritten with a
# different body, since the last executed cell. Identical arguments are the
# rule above; this one is the varied-argument loop it could not see -- one
# spilled file read twelve times at twelve offsets, one scratch script
# written four times -- in a run of 21 tool-only replies that never tripped.
SAME_TARGET_THRESHOLD = 5
SAME_TARGET_GRACE = 3
#: Distinct file targets remembered per streak; later ones are not counted.
SAME_TARGET_MAX_KEYS = 64
#: Arguments that select a window of a file or carry its new body. Two calls
#: that differ only in these reach for the same thing.
_WINDOW_ARGUMENTS = frozenset({"offset", "limit", "max_bytes", "max_chars", "content"})
#: The longest target a reminder or a stop notice quotes.
_TARGET_DISPLAY_CHARS = 160
MALFORMED_THRESHOLD = 2
SIMILAR_ERROR_THRESHOLD = 2
LONG_TEXT_THRESHOLD = 3
#: Consecutive replies with no text, no tool call and no cell. A reasoning
#: model that spends its whole completion budget thinking returns exactly that
#: (`finish_reason: length`, `content: null`); the nudge it gets back changes
#: nothing, so GLM 5.3 on the Ark Agent Plan looped, each turn costing a full
#: budget, until the turn limit.
EMPTY_REPLY_THRESHOLD = 2
# Replies since the last executed cell that promised a cell and sent tool
# calls instead (each drew the announced-cell note). The notes and the
# tool-less call they lead to are the first stage; a model that ignored the
# note four times and two tool-less calls in between is stopped on the fifth.
# A stubborn reply to the tool-less call is prose, which resets the
# consecutive count that withholds tools, so without this the cycle "promise,
# promise, prose" ran 59 model calls on one turn, ended only by an unrelated
# rule. Not consecutive: prose in between does not clear it, a cell does.
UNSENT_CELL_THRESHOLD = 5
LONG_TEXT_MIN_CHARS = 400
LONG_TEXT_NEAR_RATIO = 0.97
LONG_TEXT_COMPARE_CHARS = 8000
METADATA_KEY = "progress_circuit"

# The first stage of the two rules above. Both start with the Engine's note
# prefix, so the result digest does not read them as the tool's answer.
SAME_ACTION_NOTE = (
    ENGINE_NOTE_PREFIX
    + " this is identical call number {count} in a row to `{tool}`, and its "
    "result has not changed. Sending it again will not change it and is not "
    "progress. Use the result you already have, or take a different step; to "
    "run code, send one fenced ```python (or ```r) cell as the ONLY action "
    "of your next reply. The turn is stopped if the same call keeps repeating."
)
SAME_TARGET_NOTE = (
    ENGINE_NOTE_PREFIX
    + " `{tool}` has now reached `{target}` {count} times since the last "
    "code cell, changing only the window or the contents each time. Work "
    "from what those calls already returned. If you need the whole file, "
    "load it in one ```python cell instead of paging it through tool calls. "
    "The turn is stopped if this keeps repeating."
)

_USER_KIND = "user"
_NATIVE_KIND = "native_tools"
_FINALIZE_KIND = "finalize"
_CODE_KIND = "code"
_NO_ACTION_KIND = "no_action"
_TERMINAL_KIND = "terminal"

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+\b")
_WS_RE = re.compile(r"\s+")
_TOOL_ERROR_PREFIX_RE = re.compile(r"^\[tool error\]\s*", re.IGNORECASE)

# Provider-transport failures are not business no-progress. Tool-level
# application errors still count; these patterns match LLM/API transients.
_PROVIDER_TRANSIENT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "service unavailable",
    "temporarily unavailable",
    "provider error",
    "econnreset",
    "connection reset",
    # The LLM transport's own connection failures (``llm/transport.py``) name
    # neither a status nor the socket error, and a delegated child that died
    # on one reaches the parent only through this message.
    "llm connection error",
    "llm connection or response headers failed",
)


class _CallLike(Protocol):
    name: str
    arguments: Mapping[str, Any] | None
    parse_error: str | None


def canonical_arguments(value: Any) -> Any:
    """Stable JSON-like form: sorted object keys, no call identities."""

    if isinstance(value, Mapping):
        return {
            str(key): canonical_arguments(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_arguments(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, str):
        return value
    return str(value)


def fingerprint_native_calls(calls: Sequence[_CallLike]) -> str:
    """Hash tool name + canonical args; call ids are excluded."""

    payload = [
        {
            "name": str(call.name or ""),
            "arguments": canonical_arguments(call.arguments),
        }
        for call in calls
        if not _call_is_malformed(call)
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint_native_results(results: Sequence[Mapping[str, Any]]) -> str:
    """Hash the ordered tool results: name, error flag, normalized content.

    Call ids are excluded, as in `fingerprint_native_calls`. Numbers are
    deliberately *not* collapsed the way `normalize_tool_error` collapses
    them: "10%" then "55%" is the delta a poll exists to observe.

    A note the Engine appended is not part of the tool's answer. The recorded
    result carries it, so hashing it made the answer look as if it had moved
    on exactly the turn a reminder was sent, and an identical call that drew
    a note every other turn never built a streak.
    """

    payload = [
        {
            "name": str(message.get("name") or ""),
            "is_error": bool(message.get("is_error")),
            "content": _normalize_whitespace(
                _result_text(message.get("content")).split(ENGINE_NOTE_PREFIX, 1)[0]
            ),
        }
        for message in results
        if isinstance(message, Mapping)
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_tool_error(value: Any) -> str:
    """Collapse ids/numbers so the same error class compares equal."""

    text = _result_text(value).strip().lower()
    text = _TOOL_ERROR_PREFIX_RE.sub("", text)
    text = _UUID_RE.sub("<id>", text)
    text = _HEX_RE.sub("<id>", text)
    text = _NUMBER_RE.sub("<n>", text)
    return _WS_RE.sub(" ", text).strip()


def is_provider_transient_error(value: Any) -> bool:
    text = normalize_tool_error(value)
    return any(marker in text for marker in _PROVIDER_TRANSIENT_MARKERS)


def attach_progress_circuit(state: RunState, circuit: ProgressCircuit) -> None:
    """Cache the circuit on this process's RunState. Not durable authority."""

    state.metadata[METADATA_KEY] = circuit


def circuit_from_state(state: RunState) -> ProgressCircuit | None:
    value = state.metadata.get(METADATA_KEY)
    return value if isinstance(value, ProgressCircuit) else None


def reconstruct_progress_circuit(
    groups: Sequence[Mapping[str, Any]],
) -> ProgressCircuit:
    """Rebuild the circuit from Action Ledger groups.

    Only groups after the latest ``kind=user`` row are considered. Missing a
    provable external user message yields an empty epoch rather than guessing
    from compacted history or a checkpoint.
    """

    circuit = ProgressCircuit()
    for group in _epoch_groups(groups):
        circuit.observe_group(group)
    return circuit


def sent_through_run_cell(group: Mapping[str, Any]) -> bool:
    """Whether a ledger group is a cell the model sent by calling ``run_cell``.

    Routing makes a ``code`` group out of a reply with native calls only when
    that reply was one valid ``run_cell`` call, so the call is the one the
    recorded assistant message carries.
    """
    if str(group.get("kind") or "") != _CODE_KIND:
        return False
    message = _mapping(group.get("assistant_message"))
    calls = message.get("tool_calls") if message is not None else None
    return isinstance(calls, list) and any(
        isinstance(call, Mapping) and call.get("name") == RUN_CELL_NAME
        for call in calls
    )


@dataclass
class ProgressCircuit:
    same_action_fingerprint: str | None = None
    #: The result the last counted call came back with. A poll is the same
    #: call every time -- `collect_children(timeout=...)`, `exec_peek`,
    #: `compute_result` are designed to be repeated with identical
    #: arguments -- and it is progress exactly when the answer moves.
    same_result_digest: str | None = None
    same_action_streak: int = 0
    malformed_streak: int = 0
    error_fingerprint: str | None = None
    error_streak: int = 0
    long_text_normalized: str | None = None
    long_text_streak: int = 0
    empty_reply_streak: int = 0
    #: Consecutive native batches since the last executed cell. It never
    #: trips the circuit: the Engine reads it to send its tool-only reminder
    #: once, and a restart rebuilds it so the reminder is not sent twice.
    tool_only_streak: int = 0
    #: Consecutive replies whose tool results carried the announced-cell
    #: note. It never trips either: at ``ANNOUNCED_CELL_WITHHOLD_AFTER`` the
    #: Engine makes the next model call with no tools. It is read off the
    #: results the model read, so a restart rebuilds it and that call is
    #: neither lost nor made twice.
    announced_cell_streak: int = 0
    #: The same notes counted since the last executed cell, prose replies in
    #: between included. This one trips (``UNSENT_CELL_THRESHOLD``).
    unsent_cell_count: int = 0
    #: Whether the session has sent a cell through ``run_cell``. Unlike the
    #: counts it is not the epoch's and it never resets: once set, every
    #: later call offers ``run_cell`` beside the other tools. A restore reads
    #: it from the whole branch, not only the epoch.
    run_cell_used: bool = False
    #: Per file target: how many differing calls reached it, and the last
    #: call's fingerprint. Cleared by an executed cell.
    target_calls: dict[str, tuple[int, str]] = field(default_factory=dict)
    same_target_streak: int = 0
    same_target_tool: str | None = None
    same_target_path: str | None = None
    #: What the last observation asks the Engine to tell the model, as
    #: ``(reason, tool, count, target)``. Every observation overwrites it, so
    #: it is rebuilt with the rest and is due only on the reply that set it.
    reminder: tuple[str, str, int, str] | None = None
    trip_reason: str | None = None

    @property
    def tripped(self) -> bool:
        return self.trip_reason in PROGRESS_REASONS

    @property
    def trip_count(self) -> int:
        """How many repeats the tripped rule counted; 0 while not tripped."""
        return {
            PROGRESS_REASON_SAME_ACTION: self.same_action_streak,
            PROGRESS_REASON_SAME_TARGET: self.same_target_streak,
            PROGRESS_REASON_MALFORMED: self.malformed_streak,
            PROGRESS_REASON_TOOL_ERROR: self.error_streak,
            PROGRESS_REASON_LONG_TEXT: self.long_text_streak,
            PROGRESS_REASON_EMPTY_REPLY: self.empty_reply_streak,
            PROGRESS_REASON_UNSENT_CELL: self.unsent_cell_count,
        }.get(self.trip_reason or "", 0)

    def reminder_note(self) -> str:
        """The first-stage reminder the last observation earned, else ``""``."""
        if self.reminder is None or self.tripped:
            return ""
        reason, tool, count, target = self.reminder
        if reason == PROGRESS_REASON_SAME_TARGET:
            return SAME_TARGET_NOTE.format(tool=tool, count=count, target=target)
        return SAME_ACTION_NOTE.format(tool=tool, count=count)

    def observe_routed_action(self, action: Action | None, reply: ModelReply) -> None:
        """A prose reply is observed here; native/code/finalize wait for execution.

        Every trip is enforced at the next turn boundary (the Engine checks
        ``tripped`` before calling the model again), so the current group's
        Action Ledger row is always written first. There is deliberately no
        pre-dispatch refusal: a threshold is reached only inside an
        ``observe_*`` call, which trips immediately, so such a refusal could
        never fire.
        """

        if isinstance(action, (NativeToolBatch, CodeCell, FinalizeAction)):
            return
        self.observe_assistant_text(reply.content)

    def observe_execution(
        self, action: Action | None, outcome: ExecutionOutcome
    ) -> None:
        if isinstance(action, NativeToolBatch):
            self.observe_native_batch(action.calls, outcome.history_messages)
            self._count_tool_only_turn(action.calls)
            return
        if isinstance(action, FinalizeAction):
            self.observe_finalize(action.call, outcome.history_messages)
            return
        if isinstance(action, CodeCell):
            self.observe_code_progress()
            if action.tool_call is not None:
                self.run_cell_used = True

    def observe_announced_cell(
        self, action: Action | None, results: Sequence[Mapping[str, Any]]
    ) -> None:
        """Count a native batch whose results carry the announced-cell note.

        Observed after the Engine attached its notes, from the results the
        model reads and the ledger records. Any other reply resets the count.
        """
        self._count_announced_cell(
            results if isinstance(action, NativeToolBatch) else ()
        )

    def observe_finalize(
        self,
        call: _CallLike,
        results: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Count a refused ``finalize_response`` exactly as a one-call batch.

        A refused finalization is answered with a tool error, and the model
        that keeps resending it is looping like any other failing call: a
        Web session that answered every turn with the same schema-invalid
        finalization ran the whole turn budget out, because this circuit only
        ever saw native batches. An accepted one is not observed at all. It
        ends the run, and leaving the circuit untouched is what makes a valid
        completion unable to trip it, live or rebuilt from the ledger. Both
        paths decide acceptance from the recorded tool result, never from the
        live-only completion record.
        """

        if _finalize_accepted(results):
            return
        self.observe_native_batch((call,), results)

    def observe_native_batch(
        self,
        calls: Sequence[_CallLike],
        results: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self.reminder = None
        if not calls:
            return
        # A call, even a malformed one, is not an empty reply.
        self.empty_reply_streak = 0
        if all(_call_is_malformed(call) for call in calls):
            self.malformed_streak += 1
            self._maybe_trip()
            return
        self.malformed_streak = 0
        fingerprint = fingerprint_native_calls(calls)
        # A group without result rows (an interrupted batch) keeps counting on
        # the call alone; with results, the same call counts only when the
        # answer did not move either. The ledger persists result content, so
        # the reconstruct path applies the identical rule; persisted content
        # is redacted, so across a restart a secret-bearing answer reads as
        # "moved" -- the side that does not count.
        digest = fingerprint_native_results(results) if results else None
        if fingerprint == self.same_action_fingerprint and (
            digest is None or digest == self.same_result_digest
        ):
            self.same_action_streak += 1
        else:
            self.same_action_fingerprint = fingerprint
            self.same_result_digest = digest
            self.same_action_streak = 1
        if self.same_action_streak == SAME_ACTION_THRESHOLD:
            self.reminder = (
                PROGRESS_REASON_SAME_ACTION,
                _tool_names(calls),
                self.same_action_streak,
                "",
            )
        self._observe_file_targets(calls)
        self._observe_tool_results(results or ())
        self._maybe_trip()

    def observe_assistant_text(self, content: str) -> None:
        # Anything shorter than the long-text floor is not observed as text:
        # it neither counts as a repeat nor resets a streak. A reply with no
        # text at all is the exception -- it is counted as an empty reply.
        self.reminder = None
        normalized = _normalize_whitespace(content if isinstance(content, str) else "")
        if not normalized:
            self.empty_reply_streak += 1
            self._maybe_trip()
            return
        self.empty_reply_streak = 0
        if len(normalized) < LONG_TEXT_MIN_CHARS:
            return
        previous = self.long_text_normalized
        if previous is not None and _near_duplicate(normalized, previous):
            self.long_text_streak += 1
        else:
            self.long_text_normalized = normalized
            self.long_text_streak = 1
            if previous is not None:
                self._reset_same_action()
                self.malformed_streak = 0
                self._reset_error()
        self._maybe_trip()

    def observe_code_progress(self) -> None:
        self._reset_same_action()
        self.malformed_streak = 0
        self._reset_error()
        self.long_text_normalized = None
        self.long_text_streak = 0
        self.tool_only_streak = 0
        self.unsent_cell_count = 0
        self.target_calls = {}
        self.same_target_streak = 0
        self.same_target_tool = None
        self.same_target_path = None
        self.reminder = None

    def observe_group(self, group: Mapping[str, Any]) -> None:
        kind = str(group.get("kind") or "")
        if kind == _NATIVE_KIND:
            calls, results = _group_calls_and_results(group)
            self.observe_native_batch(calls, results)
            self._count_tool_only_turn(calls)
            self._count_announced_cell(results)
            return
        if kind == _FINALIZE_KIND:
            # The ledger writes a finalize group exactly like a one-call
            # native group -- one proposed event, one result event -- so the
            # rebuild reads the same call and result the live run observed.
            calls, results = _group_calls_and_results(group)
            if calls:
                self.observe_finalize(calls[0], results)
            self._count_announced_cell(())
            return
        if kind == _CODE_KIND:
            self.observe_code_progress()
            self._count_announced_cell(())
            if sent_through_run_cell(group):
                self.run_cell_used = True
            return
        if kind == _NO_ACTION_KIND:
            self.observe_assistant_text(_group_assistant_text(group))
            self._count_announced_cell(())

    def _observe_tool_results(self, results: Sequence[Mapping[str, Any]]) -> None:
        error_messages = [
            message
            for message in results
            if isinstance(message, Mapping) and message.get("is_error")
        ]
        success = [
            message
            for message in results
            if isinstance(message, Mapping) and not message.get("is_error")
        ]
        if success:
            self._reset_error()
            return
        if not error_messages:
            return
        fingerprints = [
            normalize_tool_error(message.get("content")) for message in error_messages
        ]
        fingerprints = [item for item in fingerprints if item]
        if not fingerprints:
            return
        if any(is_provider_transient_error(item) for item in fingerprints):
            return
        if len(set(fingerprints)) != 1:
            self._reset_error()
            return
        fingerprint = fingerprints[0]
        if fingerprint == self.error_fingerprint:
            self.error_streak += 1
        else:
            self.error_fingerprint = fingerprint
            self.error_streak = 1

    def _observe_file_targets(self, calls: Sequence[_CallLike]) -> None:
        """Count calls that reach an already-seen file a different way.

        An identical call is not counted here: with an unchanged result it is
        the same-action rule's, and with a moving one it is a poll.
        """
        for call in calls:
            if _call_is_malformed(call):
                continue
            target = _file_target(call)
            if target is None:
                continue
            key, path = target
            fingerprint = fingerprint_native_calls((call,))
            seen = self.target_calls.get(key)
            if seen is None:
                if len(self.target_calls) < SAME_TARGET_MAX_KEYS:
                    self.target_calls[key] = (1, fingerprint)
                continue
            count, last = seen
            if fingerprint == last:
                continue
            count += 1
            self.target_calls[key] = (count, fingerprint)
            if count > self.same_target_streak:
                self.same_target_streak = count
                self.same_target_tool = str(call.name or "")
                self.same_target_path = path
            if count == SAME_TARGET_THRESHOLD:
                self.reminder = (
                    PROGRESS_REASON_SAME_TARGET,
                    str(call.name or ""),
                    count,
                    path,
                )

    def _count_tool_only_turn(self, calls: Sequence[_CallLike]) -> None:
        """One more reply that was tool calls only, unless one of them ran code.

        A refused ``finalize_response`` is not counted: it reaches
        ``observe_native_batch`` to be weighed as a failing call, but it is a
        completion attempt, not a reply that put off running code.
        """
        if not calls:
            return
        if any(call.name in CODE_RUNNING_TOOLS for call in calls):
            self.tool_only_streak = 0
        else:
            self.tool_only_streak += 1

    def _count_announced_cell(self, results: Sequence[Mapping[str, Any]]) -> None:
        if any(
            ANNOUNCED_CELL_NOTE in _result_text(message.get("content"))
            for message in results
            if isinstance(message, Mapping)
        ):
            self.announced_cell_streak += 1
            self.unsent_cell_count += 1
            self._maybe_trip()
        else:
            self.announced_cell_streak = 0

    def _reset_same_action(self) -> None:
        self.same_action_fingerprint = None
        self.same_result_digest = None
        self.same_action_streak = 0
        self.empty_reply_streak = 0

    def _reset_error(self) -> None:
        self.error_fingerprint = None
        self.error_streak = 0

    def _maybe_trip(self) -> None:
        if self.trip_reason is not None:
            return
        if self.same_action_streak >= SAME_ACTION_THRESHOLD + SAME_ACTION_GRACE:
            self.trip_reason = PROGRESS_REASON_SAME_ACTION
            return
        if self.same_target_streak >= SAME_TARGET_THRESHOLD + SAME_TARGET_GRACE:
            self.trip_reason = PROGRESS_REASON_SAME_TARGET
            return
        if self.malformed_streak >= MALFORMED_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_MALFORMED
            return
        if self.error_streak >= SIMILAR_ERROR_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_TOOL_ERROR
            return
        if self.long_text_streak >= LONG_TEXT_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_LONG_TEXT
            return
        if self.empty_reply_streak >= EMPTY_REPLY_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_EMPTY_REPLY
            return
        if self.unsent_cell_count >= UNSENT_CELL_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_UNSENT_CELL


@dataclass(frozen=True)
class _ProposedCall:
    name: str
    arguments: Mapping[str, Any] | None
    parse_error: str | None = None


def _epoch_groups(
    groups: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    last_user = -1
    for index, group in enumerate(groups):
        if str(group.get("kind") or "") == _USER_KIND:
            last_user = index
    if last_user < 0:
        return []
    epoch: list[Mapping[str, Any]] = []
    for group in groups[last_user + 1 :]:
        kind = str(group.get("kind") or "")
        if kind == _TERMINAL_KIND:
            continue
        epoch.append(group)
    return epoch


def _file_target(call: _CallLike) -> tuple[str, str] | None:
    """``(key, path)`` for a call aimed at one file, else ``None``.

    The key is the tool and every argument except the window and the body, so
    two reads of one path at different offsets share it, and a search of that
    path for a different pattern does not.
    """
    arguments = call.arguments
    if not isinstance(arguments, Mapping):
        return None
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    rest = {
        str(key): value
        for key, value in arguments.items()
        if str(key) not in _WINDOW_ARGUMENTS
    }
    key = fingerprint_native_calls((_ProposedCall(str(call.name or ""), rest),))
    return key, path.strip()[:_TARGET_DISPLAY_CHARS]


def _tool_names(calls: Sequence[_CallLike]) -> str:
    names = list(
        dict.fromkeys(
            str(call.name or "") for call in calls if not _call_is_malformed(call)
        )
    )
    return ", ".join(name for name in names[:3] if name)


def _call_is_malformed(call: _CallLike) -> bool:
    if call.parse_error:
        return True
    return call.arguments is None


def _finalize_accepted(results: Sequence[Mapping[str, Any]] | None) -> bool:
    """Whether the Host accepted a finalization, read off its tool result.

    ``execute_finalize_action`` answers every declaration with exactly one
    result whose ``is_error`` is false only when the completion was accepted,
    and the ledger persists that flag. No result at all -- a group the daemon
    never closed -- is not an acceptance.
    """

    messages = [message for message in results or () if isinstance(message, Mapping)]
    return bool(messages) and not any(message.get("is_error") for message in messages)


def _group_calls_and_results(
    group: Mapping[str, Any],
) -> tuple[list[_ProposedCall], list[Mapping[str, Any]]]:
    events = [
        event for event in (group.get("events") or ()) if isinstance(event, Mapping)
    ]
    calls: list[_ProposedCall] = []
    results: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("type") == "proposed":
            call = _call_from_proposed(event)
            if call is not None:
                calls.append(call)
        elif event.get("type") == "result":
            result = _mapping(event.get("result"))
            if result is not None:
                results.append(result)
    return calls, results


def _call_from_proposed(event: Mapping[str, Any]) -> _ProposedCall | None:
    canonical = event.get("canonical_arguments")
    if not isinstance(canonical, Mapping):
        return None
    arguments = canonical.get("arguments")
    parsed_arguments = arguments if isinstance(arguments, Mapping) else None
    parse_error = canonical.get("parse_error")
    return _ProposedCall(
        name=str(canonical.get("name") or ""),
        arguments=parsed_arguments,
        parse_error=str(parse_error) if parse_error else None,
    )


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _group_assistant_text(group: Mapping[str, Any]) -> str:
    content = group.get("assistant_content")
    if isinstance(content, str) and content.strip():
        return content
    message = group.get("assistant_message")
    if isinstance(message, Mapping) and isinstance(message.get("content"), str):
        return str(message["content"])
    return ""


def _normalize_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def _near_duplicate(left: str, right: str) -> bool:
    if left == right:
        return True
    longer = max(len(left), len(right))
    shorter = min(len(left), len(right))
    if shorter < LONG_TEXT_MIN_CHARS:
        return False
    if longer - shorter > int(0.05 * longer):
        return False
    window = LONG_TEXT_COMPARE_CHARS
    sample_left = left[:window]
    sample_right = right[:window]
    if sample_left == sample_right:
        return True
    return SequenceMatcher(None, sample_left, sample_right).ratio() >= (
        LONG_TEXT_NEAR_RATIO
    )


def _result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        try:
            return json.dumps(
                canonical_arguments(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


__all__ = [
    "LONG_TEXT_MIN_CHARS",
    "LONG_TEXT_THRESHOLD",
    "MALFORMED_THRESHOLD",
    "METADATA_KEY",
    "NO_PROGRESS_STOP_REASON",
    "PROGRESS_REASONS",
    "PROGRESS_REASON_LONG_TEXT",
    "PROGRESS_REASON_MALFORMED",
    "PROGRESS_REASON_SAME_ACTION",
    "PROGRESS_REASON_SAME_TARGET",
    "PROGRESS_REASON_TOOL_ERROR",
    "PROGRESS_REASON_UNSENT_CELL",
    "SAME_ACTION_GRACE",
    "SAME_ACTION_THRESHOLD",
    "SAME_TARGET_GRACE",
    "SAME_TARGET_THRESHOLD",
    "SIMILAR_ERROR_THRESHOLD",
    "UNSENT_CELL_THRESHOLD",
    "ProgressCircuit",
    "attach_progress_circuit",
    "canonical_arguments",
    "circuit_from_state",
    "fingerprint_native_calls",
    "is_provider_transient_error",
    "normalize_tool_error",
    "reconstruct_progress_circuit",
]
