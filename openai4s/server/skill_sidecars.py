"""Durably extend one exact Python generation with observed Skill sidecars.

The worker is the only process that knows which source bytes actually executed.
This service validates those bounded result records, merges them into the
content-addressed bootstrap manifest, and persists with compare-and-swap.  It
never imports or executes a sidecar in the Host process.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from openai4s.kernel.recovery import merge_bootstrap_sidecar_loads
from openai4s.kernel.supervisor import KernelLease, KernelSupervisor

RESULT_KEY = "skill_sidecar_loads"


class SidecarGenerationStore(Protocol):
    def get_kernel_generation(self, generation_id: str) -> dict | None:
        ...

    def compare_and_swap_kernel_bootstrap(
        self,
        generation_id: str,
        *,
        expected_manifest_id: str | None,
        bootstrap: Any,
        at: int | None = None,
    ) -> dict | None:
        ...


class GenerationSidecarRecorder:
    """Persist successful imports for a frozen, still-current worker lease."""

    def __init__(self, store: SidecarGenerationStore, *, max_retries: int = 4) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be positive")
        self.store = store
        self.max_retries = max_retries

    def record_result(
        self,
        supervisor: KernelSupervisor,
        lease: KernelLease,
        result: dict[str, Any],
    ) -> dict | None:
        """Consume and persist private sidecar load records from one Cell.

        The records are removed before the ordinary Cell result reaches the
        execution log, Notebook, or model observation.  Source bytes live only
        in the generation bootstrap manifest used by checkpoint/recovery.
        """

        raw_events = result.pop(RESULT_KEY, None)
        if raw_events in (None, []):
            return None
        try:
            if lease.language != "python":
                raise RuntimeError("Skill sidecar records are only valid for Python")
            if not isinstance(raw_events, list) or not all(
                isinstance(item, Mapping) for item in raw_events
            ):
                raise RuntimeError("invalid worker sidecar event payload")
            generation_id = str(lease.generation_id or "")
            if not generation_id:
                raise RuntimeError("sidecar import has no durable kernel generation")

            for _attempt in range(self.max_retries):
                if not _lease_is_current(supervisor, lease):
                    raise RuntimeError(
                        "kernel generation changed before sidecar capture"
                    )
                current = self.store.get_kernel_generation(generation_id)
                if current is None or current.get("ended_at") is not None:
                    raise RuntimeError("kernel generation ended before sidecar capture")
                bootstrap = current.get("bootstrap")
                if not isinstance(bootstrap, Mapping):
                    raise RuntimeError("generation has no bootstrap manifest")
                merged = merge_bootstrap_sidecar_loads(bootstrap, raw_events)
                updated = self.store.compare_and_swap_kernel_bootstrap(
                    generation_id,
                    expected_manifest_id=current.get("bootstrap_manifest_id"),
                    bootstrap=merged,
                )
                if updated is not None:
                    return updated
            raise RuntimeError("concurrent bootstrap manifest updates did not converge")
        except Exception as error:  # noqa: BLE001 - Cell already executed
            durable = self._mark_failed(lease, str(error))
            _attach_capture_warning(result, durable=durable)
            return None

    def _mark_failed(self, lease: KernelLease, reason: str) -> bool:
        """Make later recovery fail closed when exact capture cannot be trusted."""

        generation_id = str(lease.generation_id or "")
        if not generation_id:
            return False
        try:
            for _attempt in range(self.max_retries):
                current = self.store.get_kernel_generation(generation_id)
                if current is None or current.get("ended_at") is not None:
                    return False
                bootstrap = current.get("bootstrap")
                if not isinstance(bootstrap, Mapping):
                    return False
                failed = dict(bootstrap)
                failed["sidecar_capture_status"] = "failed"
                failed["sidecar_capture_error"] = " ".join(
                    str(reason or "capture failed").split()
                )[:300]
                updated = self.store.compare_and_swap_kernel_bootstrap(
                    generation_id,
                    expected_manifest_id=current.get("bootstrap_manifest_id"),
                    bootstrap=failed,
                )
                if updated is not None:
                    return True
        except Exception:  # noqa: BLE001 - preserve the successful Cell result
            return False
        return False


def _lease_is_current(supervisor: KernelSupervisor, expected: KernelLease) -> bool:
    current = supervisor.lease(expected.language)
    return bool(
        current is not None
        and current.kernel is expected.kernel
        and current.generation == expected.generation
        and current.generation_id == expected.generation_id
    )


def _attach_capture_warning(result: dict[str, Any], *, durable: bool) -> None:
    warnings = result.setdefault("runtime_warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        result["runtime_warnings"] = warnings
    warnings.append(
        {
            "type": "skill_sidecar_recovery_capture_failed",
            "message": (
                "The Cell already executed, but its exact Skill "
                "sidecar recovery snapshot could not be persisted. Do not "
                "automatically rerun the Cell."
            ),
            "generation_marked_unrecoverable": bool(durable),
        }
    )


__all__ = ["GenerationSidecarRecorder", "RESULT_KEY"]
