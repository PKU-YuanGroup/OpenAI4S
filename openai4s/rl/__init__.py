"""RL-native rollout contracts for OpenAI4S.

The package is deliberately small and dependency-free.  It records policy
sampling and execution facts without implementing a training algorithm.  A
trainer can consume :class:`Trajectory` JSON while the existing agent engine
continues to own execution and permissions.
"""

from .agent_sink import TrajectoryEventSink
from .models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    RunReceipt,
    Segment,
    Trajectory,
)
from .recorder import TrajectoryRecorder
from .rollout import PolicySampler, RolloutSession

__all__ = [
    "CostRecord",
    "Episode",
    "ExecutionEvent",
    "Observation",
    "PolicyDecision",
    "RewardAnnotation",
    "RunReceipt",
    "Segment",
    "Trajectory",
    "TrajectoryRecorder",
    "TrajectoryEventSink",
    "PolicySampler",
    "RolloutSession",
]
