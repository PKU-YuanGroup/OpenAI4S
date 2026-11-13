"""Authenticated provider-cost evidence for RL trajectories.

The runtime can prove that a compute job was submitted and can measure local
wall time.  Neither fact is a provider invoice.  This module is the narrow
boundary where a trusted provider adapter may attach an explicitly attested
cost or GPU-duration measurement to a durable compute-job receipt.

Values are never estimated here.  In particular, the elapsed time between a
compute job's ``submitted_at`` and ``terminal_at`` is retained as metadata and
is not presented as ``gpu_time_ms``.  A caller that has no provider evidence
can leave ``cost_usd`` and ``gpu_time_ms`` unset.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import CostRecord


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _timestamp(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0")
    return value


def _amount(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number >= 0 or None")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite number >= 0 or None")
    return result


def _duration(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0 or None")
    return value


def _json_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TypeError("metadata must contain JSON-compatible values") from exc
    if not isinstance(decoded, dict):
        raise TypeError("metadata must be a JSON object")
    return decoded


@dataclass(frozen=True)
class ProviderCostEvidence:
    """One provider-attested measurement bound to a compute receipt.

    ``authenticated`` is deliberately required by policy rather than inferred
    from a string such as ``source``.  The provider adapter is responsible for
    establishing that assertion (for example, by verifying a signed billing
    response); this class only refuses an un-attested value at the RL boundary.
    """

    job_id: str
    receipt: str
    source: str
    observed_at: int
    authenticated: bool = False
    provider: str | None = None
    cost_usd: float | None = None
    gpu_time_ms: int | None = None
    wall_time_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("job_id", self.job_id)
        _text("receipt", self.receipt)
        _text("source", self.source)
        _timestamp("observed_at", self.observed_at)
        if not isinstance(self.authenticated, bool):
            raise TypeError("authenticated must be a bool")
        if not self.authenticated:
            raise ValueError("provider cost evidence must be authenticated")
        if self.provider is not None:
            _text("provider", self.provider)
        object.__setattr__(self, "cost_usd", _amount("cost_usd", self.cost_usd))
        object.__setattr__(
            self, "gpu_time_ms", _duration("gpu_time_ms", self.gpu_time_ms)
        )
        object.__setattr__(
            self, "wall_time_ms", _duration("wall_time_ms", self.wall_time_ms)
        )
        object.__setattr__(self, "metadata", _json_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "receipt": self.receipt,
            "source": self.source,
            "observed_at": self.observed_at,
            "authenticated": self.authenticated,
            "provider": self.provider,
            "cost_usd": self.cost_usd,
            "gpu_time_ms": self.gpu_time_ms,
            "wall_time_ms": self.wall_time_ms,
            "metadata": dict(self.metadata),
        }


def _job_time_metadata(job: Mapping[str, Any]) -> dict[str, Any]:
    """Copy durable job timing as evidence without calling it GPU time."""

    result: dict[str, Any] = {}
    for name in ("created_at", "submitted_at", "terminal_at"):
        value = job.get(name)
        if value is None:
            continue
        result[name] = _timestamp(f"compute_job.{name}", value)
    submitted = result.get("submitted_at")
    terminal = result.get("terminal_at")
    if submitted is not None and terminal is not None:
        if terminal < submitted:
            raise ValueError("compute_job.terminal_at precedes submitted_at")
        result["elapsed_ms"] = terminal - submitted
    return result


def _validate_job(
    evidence: ProviderCostEvidence, compute_job: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(compute_job, Mapping):
        raise TypeError("compute_job must be a mapping")
    raw_job_id = compute_job.get("job_id")
    raw_receipt = compute_job.get("receipt")
    if not isinstance(raw_job_id, str):
        raise TypeError("compute_job.job_id must be a non-empty string")
    if not isinstance(raw_receipt, str):
        raise TypeError("compute_job.receipt must be a non-empty string")
    job_id = _text("compute_job.job_id", raw_job_id)
    receipt = _text("compute_job.receipt", raw_receipt)
    if job_id != evidence.job_id:
        raise ValueError("provider cost evidence job_id does not match compute job")
    if receipt != evidence.receipt:
        raise ValueError("provider cost evidence receipt does not match compute job")
    provider = compute_job.get("provider")
    if provider is not None:
        if not isinstance(provider, str):
            raise TypeError("compute_job.provider must be a non-empty string")
        provider = _text("compute_job.provider", provider)
        if evidence.provider is not None and provider != evidence.provider:
            raise ValueError(
                "provider cost evidence provider does not match compute job"
            )
    created_at = compute_job.get("created_at")
    if created_at is not None:
        created_at = _timestamp("compute_job.created_at", created_at)
        if evidence.observed_at < created_at:
            raise ValueError("provider cost evidence predates compute job creation")
    timing = _job_time_metadata(compute_job)
    return {
        "job_id": job_id,
        "provider": provider,
        "receipt": receipt,
        "status": compute_job.get("status"),
        "timing": timing,
    }


def provider_cost_record(
    episode_id: str,
    evidence: ProviderCostEvidence,
    compute_job: Mapping[str, Any],
    *,
    decision_id: str | None = None,
    cost_id: str | None = None,
) -> CostRecord:
    """Build a :class:`CostRecord` from matched provider evidence.

    The compute row is supplied by the Store owner so this function cannot
    silently look up a mutable latest job or bind evidence to another job.
    ``cost_id`` is deterministic when omitted, making retries idempotent when
    passed to :meth:`TrajectoryRecorder.add_cost`.
    """

    _text("episode_id", episode_id)
    if not isinstance(evidence, ProviderCostEvidence):
        raise TypeError("evidence must be ProviderCostEvidence")
    job_metadata = _validate_job(evidence, compute_job)
    if cost_id is None:
        digest = hashlib.sha256(
            json.dumps(
                evidence.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        cost_id = f"{episode_id}:provider-cost:{digest}"
    _text("cost_id", cost_id)
    metadata = dict(evidence.metadata)
    metadata["provider_cost_evidence"] = evidence.to_dict()
    metadata["compute_job"] = job_metadata
    return CostRecord(
        cost_id=cost_id,
        episode_id=episode_id,
        created_at=evidence.observed_at,
        gpu_time_ms=evidence.gpu_time_ms,
        wall_time_ms=evidence.wall_time_ms,
        cost_usd=evidence.cost_usd,
        decision_id=decision_id,
        source=evidence.source,
        metadata=metadata,
    )


__all__ = ["ProviderCostEvidence", "provider_cost_record"]
