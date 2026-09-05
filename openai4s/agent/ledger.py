"""Runtime writer and canonical-history reducer for the Action Ledger.

The storage repository deliberately knows nothing about agent semantics.  This
module is the small semantic boundary that translates typed ``AgentEngine``
events into immutable groups/events and reconstructs a provider-safe message
history after a daemon restart.

Native tool declarations and their results are reduced atomically.  A crash
may leave an append-only group without every result; the reducer closes each
missing call with a canonical error/cancellation result instead of ever
passing a half-open provider tool batch back to an LLM.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from openai4s.llm.capabilities import calculate_usage_cost_usd, get_model_capabilities
from openai4s.storage.branch_projection import project_branch_records
from openai4s.tools import get_tool

from .actions import (
    NO_CODE_NUDGE,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    NativeToolCall,
)
from .compaction import COMPACTION_NOTE_PREFIX, content_digest
from .events import (
    ActionRouted,
    AgentEvent,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
)
from .models import ModelReply

REDACTED = "<redacted>"

_COMMON_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "secret_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "session_cookie",
    }
)
_SECRET_FORMATS = frozenset(
    {"password", "secret", "token", "credential", "private-key"}
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class LedgerStore(Protocol):
    """Narrow Store surface used by the runtime writer and reducer."""

    def append_action_group(self, **values: Any) -> dict: ...

    def append_action_event(self, **values: Any) -> dict: ...

    def append_tool_action_group(self, **values: Any) -> dict: ...

    def append_action_group_with_events(self, **values: Any) -> dict: ...

    def list_action_groups(self, root_frame_id: str, **filters: Any) -> list[dict]: ...


def _key_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


ToolResolver = Callable[[str], Any | None]
ToolPolicyResolver = Callable[[str, Any], tuple[str, list[str]]]


def _resolve_tool(name: str, resolver: ToolResolver | None = None) -> Any | None:
    if resolver is None:
        return get_tool(name)
    try:
        return resolver(name)
    except Exception:  # noqa: BLE001 - audit metadata must remain total
        return None


def _tool_secret_keys(
    name: str, resolver: ToolResolver | None = None
) -> frozenset[str]:
    """Read explicit/plugin metadata and JSON-schema secret annotations."""
    tool = _resolve_tool(name, resolver)
    if tool is None:
        return frozenset()
    keys: set[str] = set()
    for attribute in ("secret_argument_keys", "sensitive_argument_keys"):
        declared = getattr(tool, attribute, ()) or ()
        if isinstance(declared, str):
            declared = (declared,)
        try:
            keys.update(_key_name(item) for item in declared if str(item).strip())
        except TypeError:
            pass
    properties = (getattr(tool, "parameters", {}) or {}).get("properties") or {}
    for key, schema in properties.items():
        if not isinstance(schema, Mapping):
            continue
        secret = bool(
            schema.get("writeOnly")
            or schema.get("x-secret")
            or schema.get("secret")
            or str(schema.get("format") or "").lower() in _SECRET_FORMATS
        )
        if secret:
            keys.add(_key_name(key))
    return frozenset(keys)


def _redact_value(value: Any, secret_keys: frozenset[str]) -> Any:
    sensitive = _COMMON_SECRET_KEYS | secret_keys
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = _key_name(key)
            if normalized in sensitive:
                out[key] = REDACTED
            else:
                out[key] = _redact_value(item, secret_keys)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secret_keys) for item in value]
    return value


def _redact_free_text(text: str, secret_keys: frozenset[str]) -> str:
    """Redact bounded key assignments and bearer credentials in free text."""
    redacted = _BEARER_RE.sub("Bearer " + REDACTED, text)
    names = sorted(_COMMON_SECRET_KEYS | secret_keys, key=len, reverse=True)
    # Only a finite metadata/common-key vocabulary is considered; arbitrary
    # prose is not heuristically erased.  Each value pattern is delimiter-
    # bounded so it cannot consume a following field or line.
    for name in names:
        flexible = re.escape(name).replace(r"\_", "[-_ ]?")
        pattern = re.compile(
            rf'(?i)(?P<prefix>["\']?{flexible}["\']?\s*[:=]\s*)'
            r'(?P<value>"(?:\\.|[^"\\\r\n])*"|'
            r"'(?:\\.|[^'\\\r\n])*'|[^\s,;}]+)"
        )

        def replace(match: re.Match[str]) -> str:
            value = match.group("value")
            quote = (
                value[0]
                if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]
                else ""
            )
            return match.group("prefix") + quote + REDACTED + quote

        redacted = pattern.sub(replace, redacted)
    return redacted


def _redact_message(message: Mapping[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    clean = _redact_value(dict(message), keys)
    content = clean.get("content")
    if isinstance(content, str):
        clean["content"] = _redact_free_text(content, keys)
    elif isinstance(content, list):
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                part["text"] = _redact_free_text(part["text"], keys)
    return clean


def _redact_raw_arguments(raw: Any, secret_keys: frozenset[str]) -> Any:
    """Redact a provider's raw JSON argument string without dropping evidence."""
    if not isinstance(raw, str):
        return _redact_value(raw, secret_keys)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # Malformed arguments still matter for diagnosis.  Apply a conservative
        # textual pass for conventional keys and bearer credentials.
        return _redact_free_text(raw, secret_keys)
    return json.dumps(
        _redact_value(parsed, secret_keys),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def redact_tool_call(
    call: NativeToolCall,
    tool_resolver: ToolResolver | None = None,
) -> tuple[dict[str, Any], Any]:
    """Return redacted canonical declaration and provider raw arguments."""
    keys = _tool_secret_keys(call.name, tool_resolver)
    canonical = {
        "name": call.name,
        "ordinal": call.ordinal,
        "arguments": _redact_value(call.arguments, keys),
        "parse_error": call.parse_error,
        "provider_meta": _redact_value(call.provider_meta, keys),
    }
    return canonical, _redact_raw_arguments(call.raw_arguments, keys)


def _sanitize_reply(
    reply: ModelReply,
    tool_resolver: ToolResolver | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep normalized replay state while removing secret tool arguments."""
    message = copy.deepcopy(dict(reply.assistant_message))
    wire_state = copy.deepcopy(dict(reply.wire_state))
    all_keys: set[str] = set()
    sanitized_calls: list[dict[str, Any]] = []
    for item in message.get("tool_calls") or ():
        call = item if isinstance(item, Mapping) else {}
        name = str(call.get("name") or "")
        keys = _tool_secret_keys(name, tool_resolver)
        all_keys.update(keys)
        clean = _redact_value(dict(call), keys)
        if "raw_arguments" in clean:
            clean["raw_arguments"] = _redact_raw_arguments(
                call.get("raw_arguments"), keys
            )
        sanitized_calls.append(clean)
    if "tool_calls" in message:
        message["tool_calls"] = sanitized_calls
    union = frozenset(all_keys)
    message = _redact_message(message, union)
    wire_state = _redact_value(wire_state, union)
    # Provider wire state often nests JSON arguments as strings.  Walk those
    # conventional fields once more so tool-specific annotations apply there.
    wire_state = _redact_embedded_argument_json(wire_state, union)
    if wire_state:
        message["wire_state"] = copy.deepcopy(wire_state)
    return message, wire_state


def _redact_embedded_argument_json(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if _key_name(key) in {"arguments", "raw_arguments", "input"} and isinstance(
                item, str
            ):
                out[key] = _redact_raw_arguments(item, keys)
            else:
                out[key] = _redact_embedded_argument_json(item, keys)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_embedded_argument_json(item, keys) for item in value]
    return value


def _tool_policy(
    name: str,
    arguments: Any,
    resolver: ToolResolver | None = None,
) -> tuple[str, list[str]]:
    tool = _resolve_tool(name, resolver)
    if tool is None:
        return "unknown", [f"tool:{name or '<unnamed>'}"]
    side_effect = str(getattr(tool, "side_effect_class", "") or "unknown")
    try:
        resources = list(tool.resource_keys(arguments or {}))
    except Exception:  # noqa: BLE001 — audit metadata must remain total
        resources = [f"tool:{name}"]
    return side_effect, resources


@dataclass
class RuntimeActionLedger:
    """Append engine events for one user turn to the durable ledger."""

    store: LedgerStore
    root_frame_id: str
    turn_id: str
    provider: str | None = None
    model: str | None = None
    branch_id: str | None = None
    tool_resolver: ToolResolver | None = field(default=None, repr=False)
    tool_policy_resolver: ToolPolicyResolver | None = field(default=None, repr=False)
    current_group_id: str | None = field(default=None, init=False)
    terminal_recorded: bool = field(default=False, init=False)
    _reply: ModelReply | None = field(default=None, init=False, repr=False)
    _action: CodeCell | NativeToolBatch | FinalizeAction | None = field(
        default=None, init=False, repr=False
    )

    def append_user(self, message: Mapping[str, Any] | Any) -> dict:
        if isinstance(message, Mapping):
            normalized = copy.deepcopy(dict(message))
            normalized.setdefault("role", "user")
        else:
            normalized = {"role": "user", "content": message}
        return self.store.append_action_group(
            root_frame_id=self.root_frame_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            kind="user",
            provider=self.provider,
            model=self.model,
            assistant_message=_redact_message(normalized, frozenset()),
        )

    def emit(self, event: AgentEvent) -> None:
        if isinstance(event, ReplyReceived):
            self._reply = event.reply
            self._action = None
            self.current_group_id = None
            return
        if isinstance(event, ActionRouted):
            self._append_action(event.action)
            return
        if isinstance(event, OutcomeProduced):
            self._append_outcome(event)
            return
        if isinstance(event, RunFinished):
            self.append_terminal(
                event.result.stop_reason,
                completion=event.result.completion,
                progress_reason=getattr(event.result, "progress_reason", None),
            )

    def _append_action(
        self, action: CodeCell | NativeToolBatch | FinalizeAction | None
    ) -> None:
        if self._reply is None:
            raise RuntimeError("ActionRouted arrived before ReplyReceived")
        reply = self._reply
        message, wire_state = _sanitize_reply(reply, self.tool_resolver)
        usage, cost_usd = self._reply_accounting(reply)
        self._record_team_usage(usage)
        self._action = action
        if isinstance(action, FinalizeAction):
            call = action.call
            canonical, raw = redact_tool_call(call, self.tool_resolver)
            group = self.store.append_action_group(
                root_frame_id=self.root_frame_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                kind="finalize",
                provider=self.provider,
                model=self.model,
                wire_state=wire_state,
                assistant_content=(
                    message.get("content")
                    if isinstance(message.get("content"), str)
                    else _redact_free_text(reply.content, frozenset())
                ),
                assistant_message=message,
                usage=usage,
                cost_usd=cost_usd,
            )
            self.store.append_action_event(
                group_id=group["group_id"],
                type="proposed",
                action_id=call.id,
                tool_call_id=call.id,
                wire_id=call.wire_id,
                canonical_arguments=canonical,
                raw_arguments=raw,
                side_effect_class="read_only",
                resource_keys=["agent:completion"],
            )
        elif isinstance(action, NativeToolBatch):
            events: list[dict[str, Any]] = []
            for sequence, call in enumerate(action.calls):
                canonical, raw = redact_tool_call(call, self.tool_resolver)
                if self.tool_policy_resolver is None:
                    side_effect, resources = _tool_policy(
                        call.name,
                        canonical.get("arguments"),
                        self.tool_resolver,
                    )
                else:
                    try:
                        side_effect, resources = self.tool_policy_resolver(
                            call.name, canonical.get("arguments")
                        )
                    except Exception:  # noqa: BLE001 - audit metadata stays total
                        side_effect, resources = _tool_policy(
                            call.name,
                            canonical.get("arguments"),
                            self.tool_resolver,
                        )
                events.append(
                    {
                        "sequence": sequence,
                        "type": "proposed",
                        "action_id": call.id,
                        "tool_call_id": call.id,
                        "wire_id": call.wire_id,
                        "canonical_arguments": canonical,
                        "raw_arguments": raw,
                        "side_effect_class": side_effect,
                        "resource_keys": resources,
                    }
                )
            group = self.store.append_tool_action_group(
                root_frame_id=self.root_frame_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                provider=self.provider,
                model=self.model,
                wire_state=wire_state,
                assistant_content=(
                    message.get("content")
                    if isinstance(message.get("content"), str)
                    else _redact_free_text(reply.content, frozenset())
                ),
                assistant_message=message,
                usage=usage,
                cost_usd=cost_usd,
                events=events,
            )
        else:
            kind = "code" if isinstance(action, CodeCell) else "no_action"
            group = self.store.append_action_group(
                root_frame_id=self.root_frame_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                kind=kind,
                provider=self.provider,
                model=self.model,
                wire_state=wire_state,
                assistant_content=(
                    message.get("content")
                    if isinstance(message.get("content"), str)
                    else _redact_free_text(reply.content, frozenset())
                ),
                assistant_message=message,
                usage=usage,
                cost_usd=cost_usd,
            )
            proposed = (
                {"language": action.language, "code": action.code}
                if isinstance(action, CodeCell)
                else {"action": None}
            )
            self.store.append_action_event(
                group_id=group["group_id"],
                type="proposed",
                action_id=f"{group['group_id']}:action",
                canonical_arguments=proposed,
                resource_keys=(
                    [f"kernel:{action.language}"]
                    if isinstance(action, CodeCell)
                    else []
                ),
            )
        self.current_group_id = group["group_id"]

    def _record_team_usage(self, usage: dict[str, int] | None) -> None:
        """Best-effort team-mode metering (M2-5, decision D10 账本).

        Attribution walks ``root_frame_id -> session root -> session owner``;
        with no ownership row (single-user installs, unowned sessions) this
        is two SELECTs and no write, so the off state stays inert (INV-1).
        Accounting must never fail an action — same contract as
        ``_reply_accounting``.
        """
        if not usage:
            return
        # One metering hook, shared with the reviewer's provider path: a
        # second copy of this loop is how review calls came to bill only the
        # per-frame counters and never the ledger the quota check reads.
        from openai4s.storage.governance import record_session_llm_usage

        record_session_llm_usage(self.store, self.root_frame_id, usage)

    def record_abandoned_usage(self, usage: Mapping[str, Any] | None) -> None:
        """Meter a cancelled provider call whose late reply cannot be replayed.

        No action group is appended: the reply was deliberately quarantined
        and must never become conversation history. Only the provider counters
        are retained so Stop cannot bypass the team quota ledger.
        """

        if not isinstance(usage, Mapping):
            return
        normalized: dict[str, int] = {}
        for key, value in usage.items():
            if isinstance(key, str) and isinstance(value, (int, float)):
                normalized[key] = max(0, int(value))
        # ``record_session_llm_usage`` reads the canonical names only. A reply
        # that carried just the OpenAI-style aliases would meter as zero, which
        # is precisely the Stop-bypasses-the-quota-ledger hole this exists to
        # close -- and it would do so silently.
        for canonical, alias in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ):
            if not normalized.get(canonical) and normalized.get(alias):
                normalized[canonical] = normalized[alias]
        self._record_team_usage(normalized)

    def _reply_accounting(
        self, reply: ModelReply
    ) -> tuple[dict[str, int] | None, float | None]:
        """Return canonical counters and a price-derived charge, if known."""

        allowed = (
            "input_tokens",
            "output_tokens",
            "cache_read",
            "cache_write",
            "reasoning_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )
        usage: dict[str, int] = {}
        source = reply.usage if isinstance(reply.usage, Mapping) else {}
        for key in allowed:
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if parsed >= 0:
                usage[key] = parsed
        if not usage:
            return None, None
        if not self.provider:
            return usage, None
        try:
            capabilities = get_model_capabilities(self.provider, self.model)
            cost_usd = calculate_usage_cost_usd(usage, capabilities.cost)
        except (LookupError, TypeError, ValueError):
            # Accounting metadata must never make an otherwise valid action
            # fail. Unknown provider/model pricing remains visibly unknown.
            cost_usd = None
        return usage, cost_usd

    def _append_outcome(self, event: OutcomeProduced) -> None:
        group_id = self.current_group_id
        if group_id is None:
            raise RuntimeError("OutcomeProduced arrived without an action group")
        if isinstance(self._action, (NativeToolBatch, FinalizeAction)):
            calls = (
                self._action.calls
                if isinstance(self._action, NativeToolBatch)
                else (self._action.call,)
            )
            results = {
                str(message.get("tool_call_id")): dict(message)
                for message in event.outcome.history_messages
                if message.get("role") == "tool" and message.get("tool_call_id")
            }
            for call in calls:
                message = results.get(str(call.id))
                if message is None:
                    # The reducer will close a genuinely interrupted batch.  In
                    # a normal engine outcome, make the missing result explicit.
                    message = _synthetic_tool_result(
                        call,
                        cancelled=False,
                        detail="tool executor returned no canonical result",
                    )
                keys = _tool_secret_keys(call.name, self.tool_resolver)
                self.store.append_action_event(
                    group_id=group_id,
                    type="result",
                    action_id=call.id,
                    tool_call_id=call.id,
                    wire_id=call.wire_id,
                    result=_redact_message(message, keys),
                )
            return
        messages = [
            _redact_message(dict(message), frozenset())
            for message in event.outcome.history_messages
        ]
        result: dict[str, Any] = {
            "messages": messages,
            "observation": (
                _redact_free_text(event.outcome.observation, frozenset())
                if isinstance(event.outcome.observation, str)
                else _redact_value(event.outcome.observation, frozenset())
            ),
        }
        if event.outcome.completion is not None:
            result["completion"] = _redact_value(event.outcome.completion, frozenset())
        self.store.append_action_event(
            group_id=group_id,
            type="observation",
            action_id=f"{group_id}:action",
            result=result,
        )

    def append_terminal(
        self,
        reason: str,
        *,
        completion: Any = None,
        error: Any = None,
        progress_reason: Any = None,
    ) -> dict | None:
        if self.terminal_recorded:
            return None
        reason = str(reason or "unknown")
        group = self.store.append_action_group(
            root_frame_id=self.root_frame_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            kind="terminal",
            provider=self.provider,
            model=self.model,
        )
        payload = {"reason": reason}
        if completion is not None:
            payload["completion"] = _redact_value(completion, frozenset())
        if error is not None:
            payload["error"] = _redact_value(error, frozenset())
        if progress_reason:
            payload["progress_reason"] = str(progress_reason)
        self.store.append_action_event(
            group_id=group["group_id"],
            type=(
                "completed"
                if reason == "submitted"
                else ("cancelled" if reason == "cancelled" else "failed")
            ),
            result=payload,
        )
        self.terminal_recorded = True
        return group

    def append_compaction(
        self,
        handoff: str,
        covered_through_group_id: str | None,
        archive_id: str | None = None,
    ) -> dict:
        """Append a kind=compaction group that old reducers skip.

        ``assistant_message`` is left unset so a pre-compaction
        ``reduce_action_groups`` treats the row as incomplete and continues
        without raising, replaying the full uncompacted history.  Coverage
        and the handoff text live on the event payload, matching
        ``append_terminal``.  Group and event land in one transaction: a
        compaction group without its event would restore an empty note in
        place of the previous handoff.
        """
        return self.store.append_action_group_with_events(
            root_frame_id=self.root_frame_id,
            branch_id=self.branch_id or self.root_frame_id,
            turn_id=self.turn_id,
            kind="compaction",
            group_id=f"ag-{uuid.uuid4().hex[:16]}",
            admission_operation="action_group:append",
            events=[
                {
                    "type": "compaction",
                    "result": {
                        "handoff": _redact_free_text(str(handoff or ""), frozenset()),
                        "covered_through_group_id": (
                            str(covered_through_group_id)
                            if covered_through_group_id
                            else None
                        ),
                        "archive_id": archive_id,
                    },
                }
            ],
        )

    def covered_through_group_id(self, compacted_messages: Sequence[Any]) -> str | None:
        """Map compact() middle messages onto this branch's coverage bound.

        See :func:`compaction_cover_group_id` for the group→message rule.
        """
        return compaction_cover_group_id(
            branch_action_groups(
                self.store, self.root_frame_id, branch_id=self.branch_id
            ),
            compacted_messages,
        )


def _terminal_reasons(groups: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for group in groups:
        if group.get("kind") != "terminal":
            continue
        for event in group.get("events") or ():
            result = event.get("result")
            if isinstance(result, Mapping) and result.get("reason"):
                reasons[str(group.get("turn_id") or "")] = str(result["reason"])
    return reasons


def _call_from_mapping(value: Mapping[str, Any], ordinal: int) -> NativeToolCall:
    raw = value.get("raw_arguments")
    if not isinstance(raw, str):
        raw = json.dumps(
            value.get("arguments") or {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return NativeToolCall(
        id=str(value.get("id") or f"recovered-call-{ordinal}"),
        wire_id=(str(value["wire_id"]) if value.get("wire_id") else None),
        name=str(value.get("name") or ""),
        ordinal=int(value.get("ordinal", ordinal) or ordinal),
        raw_arguments=raw,
        arguments=(
            dict(value["arguments"])
            if isinstance(value.get("arguments"), Mapping)
            else None
        ),
        parse_error=(str(value["parse_error"]) if value.get("parse_error") else None),
        provider_meta=(
            dict(value["provider_meta"])
            if isinstance(value.get("provider_meta"), Mapping)
            else {}
        ),
    )


def _synthetic_tool_result(
    call: NativeToolCall,
    *,
    cancelled: bool,
    detail: str | None = None,
) -> dict[str, Any]:
    state = "cancelled" if cancelled else "interrupted"
    explanation = detail or (
        "the run was cancelled before this call produced a result"
        if cancelled
        else "the daemon stopped before this call produced a result"
    )
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "wire_id": call.wire_id,
        "name": call.name,
        "content": f"[Tool error] {call.name or '<unnamed>'}: {state}; {explanation}",
        "is_error": True,
    }


def _compaction_event_fields(group: Mapping[str, Any]) -> tuple[str | None, str]:
    """Read the coverage bound (a group id) and handoff text from a group."""
    for event in group.get("events") or ():
        if not isinstance(event, Mapping) or event.get("type") != "compaction":
            continue
        payload = event.get("result")
        if not isinstance(payload, Mapping):
            payload = event
        handoff = str(payload.get("handoff") or "")
        covered = payload.get("covered_through_group_id")
        return (str(covered) if covered else None), handoff
    return None, ""


def _apply_compaction(
    history: list[tuple[int, dict[str, Any]]],
    note_index: int,
    covered_index: int,
    handoff: str,
) -> list[tuple[int, dict[str, Any]]]:
    """Keep the earliest user, drop covered messages, insert the new handoff.

    Positions are indexes into the branch's spliced group sequence, never
    ordinals: ordinals restart at 0 on a forked branch behind the inherited
    parent prefix, so they do not order that sequence.  A later compaction
    replaces any earlier handoff note.  Observation messages also have
    ``role=user``; the head is the first user message, which is the original
    task (ledger has no system prompt).
    """
    user_messages = [
        (index, message)
        for index, message in history
        if message.get("role") == "user" and not message.get("compaction_handoff")
    ]
    first_user = min(user_messages, key=lambda item: item[0]) if user_messages else None
    kept: list[tuple[int, dict[str, Any]]] = []
    skipped_head = False
    for index, message in history:
        if (
            not skipped_head
            and first_user is not None
            and index == first_user[0]
            and message.get("role") == "user"
        ):
            skipped_head = True
            continue
        if message.get("compaction_handoff"):
            continue
        if index <= covered_index:
            continue
        kept.append((index, message))
    content = handoff
    if not content.startswith(COMPACTION_NOTE_PREFIX):
        # Restore must hand the model the same note compact() built in memory;
        # a bare handoff reads as standing instruction, not compacted history.
        content = COMPACTION_NOTE_PREFIX + content
    note = {
        "role": "system",
        "content": content,
        "compaction_handoff": True,
    }
    out: list[tuple[int, dict[str, Any]]] = []
    if first_user is not None:
        out.append(first_user)
    out.append((note_index, note))
    out.extend(kept)
    return out


def _reduce_action_groups_annotated(
    groups: Sequence[Mapping[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Reduce groups into ``(position, message)`` pairs, applying compaction.

    ``position`` is the group's index in ``groups`` — the branch's spliced
    sequence — which is the only axis that orders an inherited parent prefix
    and the child's own groups together.
    """
    history: list[tuple[int, dict[str, Any]]] = []
    terminal = _terminal_reasons(groups)
    positions: dict[str, int] = {}
    for index, group in enumerate(groups):
        group_id = str(group.get("group_id") or "")
        if group_id and group_id not in positions:
            positions[group_id] = index
    for index, group in enumerate(groups):
        kind = str(group.get("kind") or "")
        if kind == "terminal":
            continue
        if kind == "compaction":
            covered_id, handoff = _compaction_event_fields(group)
            if not handoff.strip():
                # A group without its event (or with nothing to hand over)
                # covers nothing and must not erase the previous note.
                continue
            covered_index = positions.get(covered_id or "", -1)
            if covered_index >= index:
                covered_index = -1
            history = _apply_compaction(history, index, covered_index, handoff)
            continue
        raw_message = group.get("assistant_message")
        message = (
            copy.deepcopy(dict(raw_message))
            if isinstance(raw_message, Mapping)
            else None
        )
        if kind in {"user", "system", "permission_resolution"}:
            if message and message.get("role") in {"user", "system"}:
                history.append((index, message))
            continue
        if message is None or message.get("role") != "assistant":
            # A corrupt/incomplete group must not leak a partial action.
            continue
        events = list(group.get("events") or ())
        if kind in {"native_tools", "finalize"}:
            raw_calls = message.get("tool_calls")
            if not isinstance(raw_calls, list) or not raw_calls:
                continue
            calls = [
                _call_from_mapping(call, index)
                for index, call in enumerate(raw_calls)
                if isinstance(call, Mapping)
            ]
            if len(calls) != len(raw_calls):
                continue
            by_call: dict[str, dict[str, Any]] = {}
            for event in events:
                if event.get("type") != "result":
                    continue
                result = event.get("result")
                call_id = event.get("tool_call_id")
                if call_id is not None and isinstance(result, Mapping):
                    by_call.setdefault(str(call_id), copy.deepcopy(dict(result)))
            cancelled = terminal.get(str(group.get("turn_id") or "")) == "cancelled"
            stop_reason = terminal.get(str(group.get("turn_id") or ""))
            unit = [message]
            for call in calls:
                result = by_call.get(str(call.id))
                if result is None:
                    detail = (
                        "plan mode ended before tools were executed"
                        if stop_reason == "plan"
                        else None
                    )
                    result = _synthetic_tool_result(
                        call,
                        cancelled=cancelled,
                        detail=detail,
                    )
                else:
                    result.setdefault("role", "tool")
                    result.setdefault("tool_call_id", call.id)
                    result.setdefault("wire_id", call.wire_id)
                    result.setdefault("name", call.name)
                unit.append(result)
            history.extend((index, item) for item in unit)
            continue

        observation_messages: list[dict[str, Any]] = []
        for event in events:
            if event.get("type") != "observation":
                continue
            result = event.get("result")
            if not isinstance(result, Mapping):
                continue
            messages = result.get("messages")
            if isinstance(messages, list):
                observation_messages.extend(
                    copy.deepcopy(dict(item))
                    for item in messages
                    if isinstance(item, Mapping)
                )
            if not observation_messages and isinstance(result.get("observation"), str):
                observation_messages.append(
                    {"role": "user", "content": result["observation"]}
                )
        if not observation_messages:
            reason = terminal.get(str(group.get("turn_id") or ""))
            if kind == "no_action":
                if reason == "plan":
                    detail = "[system] Plan mode ended; no action was executed."
                elif reason == "cancelled":
                    detail = "[system] This turn was cancelled after the response."
                else:
                    detail = NO_CODE_NUDGE
            elif reason == "plan":
                detail = (
                    "[system] Plan mode captured this response; the code cell "
                    "was not executed."
                )
            elif reason == "cancelled":
                detail = "[Observation]\nERROR:\nexecution was cancelled before an observation was recorded"
            else:
                detail = "[Observation]\nERROR:\nexecution was interrupted before an observation was recorded"
            observation_messages = [{"role": "user", "content": detail}]
        history.extend((index, item) for item in (message, *observation_messages))
    return history


def _same_message(reconstructed: Mapping[str, Any], live: Any) -> bool:
    """Whether a ledger-reconstructed message is the live message compact() saw.

    The live copy may have been externalized, in which case its content is a
    marker and ``content_archive`` names the digest of the original bytes
    the ledger still holds.
    """
    if not isinstance(live, Mapping):
        return False
    if live.get("role") != reconstructed.get("role"):
        return False
    if live.get("content") == reconstructed.get("content"):
        return True
    archive = live.get("content_archive")
    if isinstance(archive, Mapping) and archive.get("sha256"):
        return content_digest(reconstructed.get("content")) == archive["sha256"]
    return False


def compaction_cover_group_id(
    groups: Sequence[Mapping[str, Any]],
    compacted_messages: Sequence[Any],
) -> str | None:
    """Map compact() middle messages onto the last fully covered group.

    compact() keeps a two-message head (system prompt + first task) and a
    recent tail.  The Action Ledger has no system prompt, so the head is the
    earliest user message.  Each remaining group contributes the same
    messages ``reduce_action_groups`` would emit.

    Drop handoff notes, skip the head user, then treat the next
    ``len(compacted_messages)`` reconstructed messages as the compacted
    middle.  The cover bound is the id of the last group whose messages all
    lie in that middle.  A group that straddles middle/tail is not covered,
    so restore cannot drop a tail observation from the same cell.

    When the count cannot be aligned with the reconstructed body — the live
    history carried a message the ledger has no group for, or the reverse —
    the answer is ``None``: nothing is provably covered, and a bound guessed
    in the other direction deletes the tail compact() deliberately kept.
    """
    annotated = _reduce_action_groups_annotated(groups)
    n_middle = len(compacted_messages)
    body = [
        (index, message)
        for index, message in annotated
        if not message.get("compaction_handoff")
    ]
    if not body or n_middle <= 0:
        return None
    rest = body[1:]
    if n_middle > len(rest):
        return None
    middle = rest[:n_middle]
    # The count alone cannot tell a live-only message inside the middle from
    # a shorter ledger: both ends of the compacted middle must be the same
    # messages the ledger reconstructs there, or the bound would slide into
    # the tail compact() kept.
    if not (
        _same_message(middle[0][1], compacted_messages[0])
        and _same_message(middle[-1][1], compacted_messages[-1])
    ):
        return None
    tail_indexes = {index for index, _ in rest[n_middle:]}
    fully_covered = [index for index, _ in middle if index not in tail_indexes]
    if not fully_covered:
        return None
    group_id = str(groups[fully_covered[-1]].get("group_id") or "")
    return group_id or None


def reduce_action_groups(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce immutable groups into canonical messages after the system prompt.

    Each group contributes either a complete message unit or nothing.  In
    particular, native assistant declarations are always followed by exactly
    one tool result per declared call, including synthetic crash/cancel results.

    A ``kind="compaction"`` group keeps the earliest user message, drops every
    other message from a group at or before ``covered_through_group_id`` in
    the branch sequence, inserts the structured handoff note, and lets later
    groups append normally.  A later compaction replaces the previous note.
    """
    return [message for _, message in _reduce_action_groups_annotated(groups)]


def branch_action_groups(
    store: LedgerStore,
    root_frame_id: str,
    *,
    branch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return one branch's inherited prefix plus its local append-only groups.

    A fork does not copy provider wire history.  Its immutable base checkpoint
    instead freezes the parent branch's local ``action_cursor``.  Recursively
    reducing that prefix and then the child-local groups restores exactly the
    context visible at the fork without admitting later parent actions.
    """

    get_branch = getattr(store, "get_session_branch", None)
    get_checkpoint = getattr(store, "get_session_checkpoint", None)
    if not callable(get_branch) or not callable(get_checkpoint):
        if (branch_id or root_frame_id) != root_frame_id:
            raise ValueError("branch history requires checkpoint repository access")
        return store.list_action_groups(root_frame_id, branch_id=root_frame_id)
    return project_branch_records(
        store,
        root_frame_id,
        branch_id or root_frame_id,
        list_local=lambda selected: store.list_action_groups(
            root_frame_id,
            branch_id=selected,
        ),
        record_position=lambda group: int(group.get("ordinal") or 0),
        cursor_key="action_cursor",
    )


def restore_action_history(
    store: LedgerStore,
    root_frame_id: str,
    *,
    branch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load and reduce the canonical provider history for one branch."""

    return reduce_action_groups(
        branch_action_groups(store, root_frame_id, branch_id=branch_id)
    )


def restore_progress_circuit(
    store: LedgerStore,
    root_frame_id: str,
    *,
    branch_id: str | None = None,
) -> Any:
    """Rebuild the generic no-progress circuit from durable action groups.

    ``RunState.metadata`` is only a process cache. Callers that resume after a
    restart or compaction must use this entry point rather than a checkpoint.

    Only the groups after the latest external ``user`` group matter. That
    anchor is found with an index probe (the latest ``user`` ordinal) and a
    second probe says whether anything follows it; only then are the epoch's
    groups read with their events. The earlier "outline" pass still ran
    ``SELECT *`` over every group on the branch -- wire state, assistant
    message and usage blobs included, under the Store lock -- on every Web
    message, for a circuit that, right after ``append_user``, is empty by
    definition. A store that cannot answer the probes (or a branch with no
    local user group, whose epoch may start in an inherited prefix) takes
    the full, fork-aware path.
    """

    from .progress_circuit import reconstruct_progress_circuit

    latest = getattr(store, "latest_action_group_ordinal", None)
    list_groups = getattr(store, "list_action_groups", None)
    if callable(latest) and callable(list_groups):
        selected = branch_id or root_frame_id
        try:
            last_user = latest(root_frame_id, branch_id=selected, kind="user")
        except TypeError:
            last_user = None
        if isinstance(last_user, int) and not isinstance(last_user, bool):
            # The epoch is anchored on the user group itself, so it rides
            # along (events are irrelevant for a user row); the groups after
            # it are the only ones read with their events.
            anchor: Mapping[str, Any] = {"kind": "user", "ordinal": last_user}
            if latest(root_frame_id, branch_id=selected) == last_user:
                return reconstruct_progress_circuit([anchor])
            epoch = [anchor]
            epoch.extend(
                dict(group)
                for group in list_groups(
                    root_frame_id, branch_id=selected, after_ordinal=last_user
                )
            )
            return reconstruct_progress_circuit(epoch)
    return reconstruct_progress_circuit(
        branch_action_groups(store, root_frame_id, branch_id=branch_id)
    )


def new_turn_id() -> str:
    return f"turn-{uuid.uuid4().hex[:16]}"


__all__ = [
    "REDACTED",
    "RuntimeActionLedger",
    "branch_action_groups",
    "compaction_cover_group_id",
    "new_turn_id",
    "redact_tool_call",
    "reduce_action_groups",
    "restore_action_history",
    "restore_progress_circuit",
]
