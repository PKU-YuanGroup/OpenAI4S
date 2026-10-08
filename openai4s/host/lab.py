"""Shared daemon-only simulation Lab behavior, behind the Host policy envelope."""

from __future__ import annotations

import secrets
from typing import Any, Callable

from openai4s.lab.models import ErrorCode, LabCaller, LabError
from openai4s.lab.ports import LabManagerPort


def unavailable() -> dict[str, Any]:
    return {
        "error": "Lab is available only in the web daemon",
        "error_kind": "provider_unavailable",
    }


class LabService:
    def __init__(
        self,
        manager_provider: Callable[[], LabManagerPort | None],
        caller_factory: Callable[[], LabCaller],
    ) -> None:
        self._manager_provider = manager_provider
        self._caller_factory = caller_factory
        self.exporter: Callable[[LabCaller, str, bool], dict[str, Any]] | None = None

    def call(self, operation: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Call only the public manager projections; never interpret a receipt."""
        try:
            manager = self._manager_provider()
            if manager is None:
                return unavailable()
            if operation in {"observe_full", "observations_full"}:
                # Reuse the corresponding read Tool's schema, without exposing
                # a full-array native tool or allowing a caller-supplied full.
                from openai4s.tools.registry import get_tool

                read_tool = get_tool("lab_" + operation.removesuffix("_full"))
                if read_tool is None or read_tool.validation_error(spec):
                    raise LabError(
                        ErrorCode.INVALID_PARAMETERS, "Invalid Lab arguments"
                    )
            caller = self._caller_factory()
            # Defense in depth: even internal adapters cannot forward provider
            # options or spoof identity/approval/origin through a request.
            allowed = {
                "list": set(),
                "describe": {"device_id", "profile"},
                "create": {
                    "device_id",
                    "profile",
                    "seed",
                    "budgets",
                    "idempotency_key",
                },
                "execute": {
                    "run_id",
                    "operation",
                    "source",
                    "target",
                    "parameters",
                    "expected_revision",
                    "idempotency_key",
                },
                "observe": {"run_id"},
                "observe_full": {"run_id"},
                "status": {"run_id", "command_id"},
                "stop": {"run_id", "reason"},
                "export": {"run_id", "include_evaluation"},
                "commands": {"run_id", "after_seq", "limit"},
                "observations": {"run_id", "after_sequence", "limit"},
                "observations_full": {"run_id", "after_sequence", "limit"},
            }
            if not isinstance(spec, dict) or set(spec) - allowed[operation]:
                raise LabError(ErrorCode.INVALID_PARAMETERS, "Invalid Lab arguments")
            if operation == "list":
                return {
                    "devices": manager.list_devices(caller),
                    "runs": manager.list_runs(caller),
                }
            if operation == "describe":
                return manager.describe(caller, spec["device_id"], spec.get("profile"))
            if operation in {"create", "execute"}:
                request = dict(spec)
                request.setdefault("idempotency_key", "lab-" + secrets.token_hex(16))
                if operation == "execute":
                    request.setdefault("source", None)
                    request.setdefault("target", None)
                    request.setdefault("parameters", {})
                    return manager.execute(caller, request)
                result = manager.create_run(caller, request)
                # Run projections deliberately omit the internal create-key
                # column. Return the public request key so creation can retry.
                return {**result, "idempotency_key": request["idempotency_key"]}
            if operation in {"observe", "observe_full"}:
                return manager.observe(
                    caller, spec["run_id"], full=operation.endswith("_full")
                )
            if operation == "status":
                return manager.status(caller, spec["run_id"], spec.get("command_id"))
            if operation == "stop":
                return manager.stop(caller, spec["run_id"], spec.get("reason"))
            if operation == "export":
                if (
                    not isinstance(spec.get("run_id"), str)
                    or not spec["run_id"]
                    or type(spec.get("include_evaluation", False)) is not bool
                ):
                    raise LabError(
                        ErrorCode.INVALID_PARAMETERS, "Invalid Lab export arguments"
                    )
                if self.exporter is None:
                    return unavailable()
                return self.exporter(
                    caller, spec["run_id"], spec.get("include_evaluation", False)
                )
            if operation == "commands":
                return manager.commands(
                    caller,
                    spec["run_id"],
                    after_seq=spec.get("after_seq", 0),
                    limit=spec.get("limit", 50),
                )
            return manager.observations(
                caller,
                spec["run_id"],
                after_sequence=spec.get("after_sequence", -1),
                limit=spec.get("limit", 20),
                full=operation.endswith("_full"),
            )
        except LabError as exc:
            result = {"error": exc.message, "error_kind": exc.code.value}
            if exc.details is not None:
                result["details"] = dict(exc.details)
            return result
        except Exception:
            # Provider/Store failures may carry scientific values or local
            # configuration in their text. Never return that text to callers.
            return {
                "error": "Lab service is unavailable",
                "error_kind": "provider_unavailable",
            }


def activity_view(method: str, spec: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Bounded, input-only simulation card; no provider payloads or raw actions."""
    fields: dict[str, Any] = {"mode": "simulation"}
    for key in ("device_id", "run_id", "profile", "operation", "source", "target"):
        value = spec.get(key)
        if isinstance(value, str):
            fields[key] = value[:128]
    for key in ("expected_revision", "seed"):
        if type(spec.get(key)) is int:
            fields[key] = spec[key]
    parameters = spec.get("parameters")
    if isinstance(parameters, dict):
        fields["parameters"] = {
            str(name)[:64]: {"value": quantity["value"], "unit": quantity["unit"][:64]}
            for name, quantity in list(parameters.items())[:20]
            if isinstance(quantity, dict)
            and type(quantity.get("value")) in (int, float)
            and isinstance(quantity.get("unit"), str)
        }
    labels = {
        "lab_create": "Create run",
        "lab_execute": "Execute",
        "lab_stop": "Stop run",
    }
    title = "Simulation · " + labels[method]
    if "operation" in fields:
        title += " · " + fields["operation"]
    if fields.get("source") or fields.get("target"):
        title += f" · {fields.get('source', '—')} → {fields.get('target', '—')}"
    return "lab", title, fields
