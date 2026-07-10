"""Typed events emitted by the provider-neutral agent engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .actions import Action
from .models import EngineResult, ExecutionOutcome, ModelReply


@dataclass(frozen=True)
class RunStarted:
    max_turns: int
    history_size: int


@dataclass(frozen=True)
class TurnStarted:
    turn: int


@dataclass(frozen=True)
class TextDelta:
    text: str
    turn: int


@dataclass(frozen=True)
class ReplyReceived:
    reply: ModelReply
    turn: int


@dataclass(frozen=True)
class ActionRouted:
    action: Action | None
    turn: int


@dataclass(frozen=True)
class OutcomeProduced:
    outcome: ExecutionOutcome
    turn: int


@dataclass(frozen=True)
class RunFinished:
    result: EngineResult


AgentEvent: TypeAlias = (
    RunStarted
    | TurnStarted
    | TextDelta
    | ReplyReceived
    | ActionRouted
    | OutcomeProduced
    | RunFinished
)
