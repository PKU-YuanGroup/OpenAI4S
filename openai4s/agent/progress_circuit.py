"""Generic Agent no-progress circuit.

The Action Ledger — its action groups plus the latest external user message —
is the only durable authority. ``RunState.metadata`` holds a process-local
cache of the reconstructed object and must never be treated as persistence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol, Sequence

from .actions import Action, CodeCell, FinalizeAction, NativeToolBatch
from .models import ExecutionOutcome, ModelReply, RunState

#: An Engine ``stop_reason`` (and the ``frame_update.code`` a failed turn
#: carries). Distinct from Auto Mode's ``loop_detected`` terminal, whose
#: ``no_progress_turn_limit`` is a budget *field*; the harness fixture's
#: ``loop_kind: no_progress`` mirrors that field name, not this reason.
NO_PROGRESS_STOP_REASON = "no_progress"
PROGRESS_REASON_SAME_ACTION = "same_action"
PROGRESS_REASON_MALFORMED = "consecutive_malformed"
PROGRESS_REASON_TOOL_ERROR = "similar_tool_error"
PROGRESS_REASON_LONG_TEXT = "long_text_repeat"
PROGRESS_REASONS = frozenset(
    {
        PROGRESS_REASON_SAME_ACTION,
        PROGRESS_REASON_MALFORMED,
        PROGRESS_REASON_TOOL_ERROR,
        PROGRESS_REASON_LONG_TEXT,
    }
)

SAME_ACTION_THRESHOLD = 3
MALFORMED_THRESHOLD = 2
SIMILAR_ERROR_THRESHOLD = 2
LONG_TEXT_THRESHOLD = 3
LONG_TEXT_MIN_CHARS = 400
LONG_TEXT_NEAR_RATIO = 0.97
LONG_TEXT_COMPARE_CHARS = 8000
METADATA_KEY = "progress_circuit"

_USER_KIND = "user"
_NATIVE_KIND = "native_tools"
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
    """

    payload = [
        {
            "name": str(message.get("name") or ""),
            "is_error": bool(message.get("is_error")),
            "content": _normalize_whitespace(_result_text(message.get("content"))),
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
    trip_reason: str | None = None

    @property
    def tripped(self) -> bool:
        return self.trip_reason in PROGRESS_REASONS

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
            return
        if isinstance(action, CodeCell):
            self.observe_code_progress()

    def observe_native_batch(
        self,
        calls: Sequence[_CallLike],
        results: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        if not calls:
            return
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
        self._observe_tool_results(results or ())
        self._maybe_trip()

    def observe_assistant_text(self, content: str) -> None:
        # Anything shorter than the long-text floor -- an empty content with a
        # reasoning insert included -- is not observed at all, so it neither
        # counts as a repeat nor resets a streak.
        normalized = _normalize_whitespace(content if isinstance(content, str) else "")
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

    def observe_group(self, group: Mapping[str, Any]) -> None:
        kind = str(group.get("kind") or "")
        if kind == _NATIVE_KIND:
            self._observe_native_group(group)
            return
        if kind == _CODE_KIND:
            self.observe_code_progress()
            return
        if kind == _NO_ACTION_KIND:
            self.observe_assistant_text(_group_assistant_text(group))

    def _observe_native_group(self, group: Mapping[str, Any]) -> None:
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
        self.observe_native_batch(calls, results)

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

    def _reset_same_action(self) -> None:
        self.same_action_fingerprint = None
        self.same_result_digest = None
        self.same_action_streak = 0

    def _reset_error(self) -> None:
        self.error_fingerprint = None
        self.error_streak = 0

    def _maybe_trip(self) -> None:
        if self.trip_reason is not None:
            return
        if self.same_action_streak >= SAME_ACTION_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_SAME_ACTION
            return
        if self.malformed_streak >= MALFORMED_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_MALFORMED
            return
        if self.error_streak >= SIMILAR_ERROR_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_TOOL_ERROR
            return
        if self.long_text_streak >= LONG_TEXT_THRESHOLD:
            self.trip_reason = PROGRESS_REASON_LONG_TEXT


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


def _call_is_malformed(call: _CallLike) -> bool:
    if call.parse_error:
        return True
    return call.arguments is None


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
    "PROGRESS_REASON_TOOL_ERROR",
    "SAME_ACTION_THRESHOLD",
    "SIMILAR_ERROR_THRESHOLD",
    "ProgressCircuit",
    "attach_progress_circuit",
    "canonical_arguments",
    "circuit_from_state",
    "fingerprint_native_calls",
    "is_provider_transient_error",
    "normalize_tool_error",
    "reconstruct_progress_circuit",
]
