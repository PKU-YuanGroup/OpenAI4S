"""RL-native rollout contracts for OpenAI4S.

The package is deliberately small and dependency-free.  It records policy
sampling and execution facts without implementing a training algorithm.  A
trainer can consume :class:`Trajectory` JSON while the existing agent engine
continues to own execution and permissions.
"""

from typing import Any

from .host_events import HostCallLifecycle
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

_LAZY_EXPORTS = {
    "ATIFExporter": (".atif", "ATIFExporter"),
    "PolicySampler": (".rollout", "PolicySampler"),
    "AsyncPolicySampler": (".rollout", "AsyncPolicySampler"),
    "RolloutSession": (".rollout", "RolloutSession"),
    "TrajectoryEventSink": (".agent_sink", "TrajectoryEventSink"),
    "RewardVerifier": (".verifier", "RewardVerifier"),
    "apply_verifier": (".verifier", "apply_verifier"),
    "DatasetSnapshot": (".snapshot", "DatasetSnapshot"),
    "PolicyAdapter": (".policy", "PolicyAdapter"),
    "PolicyProvider": (".policy", "PolicyProvider"),
    "EpisodeReset": (".environment", "EpisodeReset"),
    "RolloutEnvironment": (".environment", "RolloutEnvironment"),
    "RolloutEnvironmentAdapter": (".environment", "RolloutEnvironmentAdapter"),
    "EnvironmentAdapter": (".environment", "EnvironmentAdapter"),
    "ReplayCursorError": (".replay", "ReplayCursorError"),
    "ReplayError": (".replay", "ReplayError"),
    "ReplayIntegrityError": (".replay", "ReplayIntegrityError"),
    "ReplayUnavailable": (".replay", "ReplayUnavailable"),
    "ReplayView": (".replay", "ReplayView"),
    "TrajectoryReplayer": (".replay", "TrajectoryReplayer"),
    "ReplayMode": (".replay", "ReplayMode"),
    "ExecutionReplayRunner": (".replay", "ExecutionReplayRunner"),
    "ArtifactChecker": (".replay", "ArtifactChecker"),
    "DurableFactProjector": (".projector", "DurableFactProjector"),
    "RecoveryResult": (".worker", "RecoveryResult"),
    "RolloutExport": (".worker", "RolloutExport"),
    "RolloutWorker": (".worker", "RolloutWorker"),
    "DEFAULT_MAX_LINE_BYTES": (".rpc", "DEFAULT_MAX_LINE_BYTES"),
    "PROTOCOL": (".rpc", "PROTOCOL"),
    "RPC_METHODS": (".rpc", "RPC_METHODS"),
    "RolloutRPC": (".rpc", "RolloutRPC"),
    "serve_rollout_rpc": (".rpc", "serve"),
    "WorkerFormat": (".worker", "WorkerFormat"),
    "ProviderCostEvidence": (".cost", "ProviderCostEvidence"),
    "provider_cost_record": (".cost", "provider_cost_record"),
    "AdmissionRecord": (".admission", "AdmissionRecord"),
    "AdmissionResult": (".admission", "AdmissionResult"),
    "ResourceAdmission": (".admission", "ResourceAdmission"),
    "DurableResourceAdmission": (".admission", "DurableResourceAdmission"),
    "ResourceCapacity": (".admission", "ResourceCapacity"),
    "ResourceRequest": (".admission", "ResourceRequest"),
    "BudgetExceeded": (".budget", "BudgetExceeded"),
    "BudgetLedger": (".budget", "BudgetLedger"),
    "BudgetReservation": (".budget", "BudgetReservation"),
    "BudgetState": (".budget", "BudgetState"),
    "DurableBudgetLedger": (".budget", "DurableBudgetLedger"),
    "RolloutDataset": (".dataset", "RolloutDataset"),
    "TrainingSample": (".dataset", "TrainingSample"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


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
    "ATIFExporter",
    "HostCallLifecycle",
    "PolicySampler",
    "AsyncPolicySampler",
    "RolloutSession",
    "RewardVerifier",
    "apply_verifier",
    "DatasetSnapshot",
    "PolicyAdapter",
    "PolicyProvider",
    "EpisodeReset",
    "RolloutEnvironment",
    "RolloutEnvironmentAdapter",
    "EnvironmentAdapter",
    "ReplayCursorError",
    "ReplayError",
    "ReplayIntegrityError",
    "ReplayUnavailable",
    "ReplayView",
    "TrajectoryReplayer",
    "ReplayMode",
    "ExecutionReplayRunner",
    "ArtifactChecker",
    "DurableFactProjector",
    "RecoveryResult",
    "RolloutExport",
    "RolloutWorker",
    "WorkerFormat",
    "DEFAULT_MAX_LINE_BYTES",
    "PROTOCOL",
    "RPC_METHODS",
    "RolloutRPC",
    "serve_rollout_rpc",
    "ProviderCostEvidence",
    "provider_cost_record",
    "AdmissionRecord",
    "AdmissionResult",
    "ResourceAdmission",
    "DurableResourceAdmission",
    "ResourceCapacity",
    "ResourceRequest",
    "BudgetExceeded",
    "BudgetLedger",
    "BudgetReservation",
    "BudgetState",
    "DurableBudgetLedger",
    "RolloutDataset",
    "TrainingSample",
]
