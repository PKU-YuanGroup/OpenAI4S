"""Reusable live-process admission for checkpoint-backed model operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ModelAdmissionError(RuntimeError):
    """A formal call or canary result does not satisfy the admission contract."""


class ModelAdmissionLedger:
    """Bind a verified canary to exact model bytes and an execution route.

    The ledger intentionally lives only as long as its owning backend process.
    Persisting it across a process restart would claim that a runtime which has
    not executed a canary is the same admitted runtime. Connectors provide the
    real inference and parser; this class supplies the reusable state machine.
    """

    def __init__(self, namespace: str) -> None:
        namespace = str(namespace).strip()
        if not namespace:
            raise ValueError("model admission namespace must not be empty")
        self.namespace = namespace
        self._records: dict[str, dict[str, Any]] = {}

    def key(
        self,
        *,
        operation: str,
        backend_revision: Any,
        checkpoint_digest: Any,
        execution_target: str,
    ) -> str | None:
        """Return the exact reusable identity, or ``None`` when no asset exists."""

        if not isinstance(checkpoint_digest, str):
            return None
        value = {
            "namespace": self.namespace,
            "operation": operation,
            "backend_revision": backend_revision,
            "checkpoint_digest": checkpoint_digest.lower(),
            "execution_target": execution_target,
        }
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def require(self, key: str | None) -> dict[str, Any] | None:
        """Return prior evidence, refusing a checkpointed call without it."""

        if key is None:
            return None
        record = self._records.get(key)
        if record is None:
            raise ModelAdmissionError(
                "backend/checkpoint is not admitted in this live process; run a "
                "real inference with run_mode=canary, verify its terminal output, "
                "then retry the formal operation with a new attempt_id"
            )
        return dict(record)

    def admit(
        self,
        key: str,
        *,
        canary_attempt_id: str,
        operation: str,
        backend_revision: Any,
        requested_checkpoint_digest: str,
        observed_checkpoint_digest: Any,
        execution_target: str,
        verified_at: str,
    ) -> dict[str, Any]:
        """Admit only a parsed canary that reports the requested model bytes."""

        expected = requested_checkpoint_digest.lower()
        if observed_checkpoint_digest != expected:
            raise ModelAdmissionError(
                "canary returned a checkpoint digest different from the requested digest"
            )
        record = {
            "status": "verified",
            "admission_key": key,
            "canary_attempt_id": canary_attempt_id,
            "tool": operation,
            "backend_revision": backend_revision,
            "checkpoint_digest": observed_checkpoint_digest,
            "execution_target": execution_target,
            "verified_at": verified_at,
        }
        self._records[key] = record
        return dict(record)

    def get(self, key: str | None) -> dict[str, Any] | None:
        """Return a defensive copy of current admission evidence."""

        record = self._records.get(key) if key is not None else None
        return dict(record) if record is not None else None


__all__ = ["ModelAdmissionError", "ModelAdmissionLedger"]
