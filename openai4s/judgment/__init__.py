"""Experimental semantic judgment layer. Default-off; W0 has no runtime wiring."""

from __future__ import annotations

from openai4s.judgment.disclosure import (
    CAPABILITIES,
    DISCLOSURE_VERSION,
    is_acknowledged,
)
from openai4s.judgment.flags import (
    JUDGMENT_FLAG_PRECEDENCE,
    EffectiveJudgmentFlags,
    ResolvedFlag,
    resolve,
)
from openai4s.judgment.port import BackendError, JudgmentBackend, NullBackend
from openai4s.judgment.types import (
    Answer,
    BackendReply,
    Choice,
    JudgmentResult,
    Noul,
    Question,
    Score,
)

__all__ = (
    "CAPABILITIES",
    "DISCLOSURE_VERSION",
    "JUDGMENT_FLAG_PRECEDENCE",
    "Answer",
    "BackendError",
    "BackendReply",
    "Choice",
    "EffectiveJudgmentFlags",
    "JudgmentBackend",
    "JudgmentResult",
    "Noul",
    "NullBackend",
    "Question",
    "ResolvedFlag",
    "Score",
    "is_acknowledged",
    "resolve",
)
