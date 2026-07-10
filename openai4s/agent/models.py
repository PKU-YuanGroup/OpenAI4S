"""Provider-neutral values owned by the agent engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, TypeAlias

from .actions import Action, NativeToolCall

Message: TypeAlias = dict[str, Any]


def _tool_call(value: NativeToolCall | Mapping[str, Any]) -> NativeToolCall:
    if isinstance(value, NativeToolCall):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("tool_calls must contain mappings or NativeToolCall values")
    return NativeToolCall(**dict(value))


@dataclass(frozen=True)
class ModelReply:
    """One normalized model response, including replay-ready wire history."""

    content: str = ""
    reasoning: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: Any = None
    raw: Any = None
    tool_calls: tuple[NativeToolCall, ...] = ()
    assistant_message: Message = field(default_factory=dict)
    wire_state: dict[str, Any] = field(default_factory=dict)
    provider_finish_reason: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep direct construction as replay-safe as ``from_mapping``."""
        if self.assistant_message:
            return
        message: Message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [asdict(call) for call in self.tool_calls]
        if self.wire_state:
            message["wire_state"] = dict(self.wire_state)
        object.__setattr__(self, "assistant_message", message)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelReply":
        """Accept the legacy five-key reply and the canonical native form."""
        if not isinstance(value, Mapping):
            raise TypeError("model reply must be a mapping")
        content_value = value.get("content")
        content = content_value if isinstance(content_value, str) else ""
        assistant_value = value.get("assistant_message")
        assistant_source = (
            dict(assistant_value) if isinstance(assistant_value, Mapping) else None
        )
        raw_calls = value.get("tool_calls")
        if raw_calls is None and assistant_source is not None:
            raw_calls = assistant_source.get("tool_calls")
        calls = tuple(_tool_call(call) for call in (raw_calls or ()))
        wire_value = value.get("wire_state")
        if not isinstance(wire_value, Mapping) and assistant_source is not None:
            wire_value = assistant_source.get("wire_state")
        wire_state = dict(wire_value) if isinstance(wire_value, Mapping) else {}
        if assistant_source is None:
            assistant_source = {"role": "assistant", "content": content}
            if calls:
                assistant_source["tool_calls"] = [asdict(call) for call in calls]
            if wire_state:
                assistant_source["wire_state"] = wire_state
        usage_value = value.get("usage")
        usage = dict(usage_value) if isinstance(usage_value, Mapping) else {}
        known = {
            "content",
            "reasoning",
            "usage",
            "finish_reason",
            "raw",
            "tool_calls",
            "assistant_message",
            "wire_state",
            "provider_finish_reason",
        }
        return cls(
            content=content,
            reasoning=value.get("reasoning"),
            usage=usage,
            finish_reason=value.get("finish_reason"),
            raw=value.get("raw"),
            tool_calls=calls,
            assistant_message=assistant_source,
            wire_state=wire_state,
            provider_finish_reason=value.get("provider_finish_reason"),
            extra={key: item for key, item in value.items() if key not in known},
        )


@dataclass
class RunState:
    messages: list[Message]
    max_turns: int = 32
    turn: int = 0
    last_reply: ModelReply | None = None
    last_action: Action | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutcome:
    history_messages: tuple[Message, ...] = ()
    observation: Any = None
    completion: Any = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class EngineResult:
    history_messages: tuple[Message, ...]
    completion: Any
    stop_reason: str
    turns: int
    last_reply: ModelReply | None

    @property
    def messages(self) -> tuple[Message, ...]:
        return self.history_messages
