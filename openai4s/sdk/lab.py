"""Worker-side ``host.lab`` facade for simulation-only Lab operations.

The injected callback is the Host facade's shared wire codec. Permissions,
approval, idempotency keys, validation and projection belong to the Host.
"""

from __future__ import annotations

from typing import Any, Callable


class _Lab:
    """Simulation devices and runs available through the web daemon.

    Rejected commands are ordinary results: inspect ``command.state`` and
    ``command.error``. An unknown outcome raises ``RuntimeError`` with
    ``error_kind`` and ``details.command_id``; query that command with
    ``status`` instead of resubmitting it with a new idempotency key.
    """

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._host_call = host_call

    def _call(self, method: str, args: list) -> Any:
        result = self._host_call(method, args)
        if isinstance(result, dict) and "error" in result:
            error = RuntimeError(
                f"host.lab.{method.removeprefix('lab_')}: {result['error']}"
            )
            setattr(error, "error_kind", result.get("error_kind"))
            setattr(error, "details", result.get("details"))
            raise error
        return result

    def list(self) -> dict[str, Any]:
        """List registered simulation devices and this session's runs."""
        return self._call("lab_list", [])

    def describe(self, device_id: str, profile: str | None = None) -> dict[str, Any]:
        """Read the simulation device's capability and observation manifest."""
        return self._call(
            "lab_describe", [{"device_id": device_id, "profile": profile}]
        )

    def create(
        self,
        device_id: str,
        profile: str,
        *,
        seed: int | None = None,
        budgets: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create an approved simulation run; budgets may only tighten limits.

        The Host generates an idempotency key when none is supplied. Reuse
        the returned key when retrying the same request.
        """
        return self._call(
            "lab_create",
            [
                {
                    "device_id": device_id,
                    "profile": profile,
                    "seed": seed,
                    "budgets": budgets,
                    "idempotency_key": idempotency_key,
                }
            ],
        )

    def observe(self, run_id: str, *, full: bool = False) -> dict[str, Any]:
        """Read the latest observation without advancing the simulation.

        ``full=True`` returns complete arrays inside Python; the default
        projection summarizes arrays containing more than 256 elements.
        """
        if full:
            return self._call("lab_observe_full", [{"run_id": run_id}])
        return self._call("lab_observe", [{"run_id": run_id}])

    def execute(
        self,
        run_id: str,
        operation: str,
        *,
        source: str | None = None,
        target: str | None = None,
        parameters: dict[str, Any] | None = None,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute an approved simulation command at the observed revision.

        Read ``expected_revision`` from a recent observation or status.
        Rejections return normally, including the available parameter levels
        in ``command.error``. Query an unknown outcome with ``status`` using
        the exception's ``details["command_id"]``; never resend with a new key.
        """
        return self._call(
            "lab_execute",
            [
                {
                    "run_id": run_id,
                    "operation": operation,
                    "source": source,
                    "target": target,
                    "parameters": parameters,
                    "expected_revision": expected_revision,
                    "idempotency_key": idempotency_key,
                }
            ],
        )

    def status(self, run_id: str, command_id: str | None = None) -> dict[str, Any]:
        """Read status and reconcile the same unknown command without resending."""
        return self._call("lab_status", [{"run_id": run_id, "command_id": command_id}])

    def stop(self, run_id: str, reason: str | None = None) -> dict[str, Any]:
        """Stop this session's simulation run without waiting for approval."""
        return self._call("lab_stop", [{"run_id": run_id, "reason": reason}])

    def export(self, run_id: str, include_evaluation: bool = False) -> dict[str, Any]:
        """Export recorded evidence as exact versions; truth is strictly opt-in."""
        return self._call(
            "lab_export", [{"run_id": run_id, "include_evaluation": include_evaluation}]
        )

    def commands(
        self, run_id: str, *, after_seq: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """Read a page of recorded commands without contacting the provider."""
        return self._call(
            "lab_commands",
            [{"run_id": run_id, "after_seq": after_seq, "limit": limit}],
        )

    def observations(
        self,
        run_id: str,
        *,
        after_sequence: int = -1,
        limit: int = 20,
        full: bool = False,
    ) -> dict[str, Any]:
        """Read recorded observations, optionally with complete Python arrays."""
        args = [{"run_id": run_id, "after_sequence": after_sequence, "limit": limit}]
        if full:
            return self._call("lab_observations_full", args)
        return self._call("lab_observations", args)
