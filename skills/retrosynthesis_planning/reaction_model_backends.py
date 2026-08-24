"""Stdlib host adapter for isolated planning and reaction-model inference."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .external_backends import (
    BackendExecutionError,
    BackendProtocolError,
    ModelManifest,
    load_model_manifest,
)

WIRE_SCHEMA_VERSION = 1
SUPPORTED_MODELS = {
    "aizynthfinder": "AiZynthFinder",
    "rxnmapper": "RXNMapper",
    "reactiont5_forward": "ReactionT5v2",
    "reactiont5_yield": "ReactionT5v2",
    "parrot": "Parrot",
}
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_PATH_IN_TEXT = re.compile(
    r"(?<![\w~])(?:~?/[^\s'\"<>,;)]*|[A-Za-z]:[\\/][^\s'\"<>,;)]*)"
)


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise BackendProtocolError(f"{field} must be JSON serializable") from exc


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackendProtocolError(f"{field} must be a non-empty string")
    return value.strip()


def _normalize_response(
    value: Any, *, request_id: str, manifest: ModelManifest
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendProtocolError("reaction backend response must be an object")
    allowed = {
        "schema_version",
        "request_id",
        "backend",
        "operation",
        "ok",
        "result",
        "runtime",
        "warnings",
        "model_manifest",
        "error",
        "elapsed_seconds",
    }
    if set(value) - allowed:
        raise BackendProtocolError(
            f"unsupported reaction backend fields {sorted(set(value) - allowed)}"
        )
    if (
        value.get("schema_version") != WIRE_SCHEMA_VERSION
        or value.get("request_id") != request_id
    ):
        raise BackendProtocolError("reaction backend schema or request ID mismatch")
    ok = value.get("ok")
    if not isinstance(ok, bool):
        raise BackendProtocolError("reaction backend ok must be boolean")
    operation = _text(value.get("operation"), field="operation")
    warnings = value.get("warnings") or []
    if not isinstance(warnings, list) or not all(
        isinstance(item, str) for item in warnings
    ):
        raise BackendProtocolError("warnings must be an array of strings")
    runtime = _json_copy(value.get("runtime") or {}, field="runtime")
    if not isinstance(runtime, dict):
        raise BackendProtocolError("runtime must be an object")
    result: dict[str, Any] = {
        "schema_version": WIRE_SCHEMA_VERSION,
        "request_id": request_id,
        "backend": _text(value.get("backend"), field="backend"),
        "operation": operation,
        "ok": ok,
        "runtime": runtime,
        "warnings": warnings,
        "elapsed_seconds": value.get("elapsed_seconds"),
    }
    if not ok:
        error = value.get("error")
        if (
            not isinstance(error, Mapping)
            or set(error) != {"code", "message", "retryable"}
            or not isinstance(error.get("retryable"), bool)
        ):
            raise BackendProtocolError("invalid structured reaction backend error")
        result["error"] = {
            "code": _text(error.get("code"), field="error.code"),
            "message": _text(error.get("message"), field="error.message"),
            "retryable": error["retryable"],
        }
        return result
    returned_manifest = value.get("model_manifest")
    if not isinstance(returned_manifest, Mapping):
        raise BackendProtocolError("successful response must echo model_manifest")
    returned = ModelManifest.from_mapping(returned_manifest)
    if returned.fingerprint != manifest.fingerprint:
        raise BackendExecutionError(
            "manifest_mismatch", "reaction backend changed the reviewed model manifest"
        )
    payload = _json_copy(value.get("result"), field="result")
    if not isinstance(payload, dict):
        raise BackendProtocolError(
            "successful reaction backend result must be an object"
        )
    result.update(
        {
            "result": payload,
            "model_manifest": returned.to_dict(),
            "manifest_fingerprint": returned.fingerprint,
            "provenance_status": returned.provenance_status,
        }
    )
    return result


class ReactionModelBackend:
    """Execute one reviewed reaction model in a foreign Python environment."""

    def __init__(
        self,
        model: str,
        *,
        manifest: ModelManifest | Mapping[str, Any] | str | Path,
        python_command: Iterable[str] | None = None,
        worker_path: str | Path | None = None,
        model_location: str | Path | None = None,
        repository_dir: str | Path | None = None,
        timeout_seconds: float = 600.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if model not in SUPPORTED_MODELS:
            raise ValueError(f"unsupported reaction model {model!r}")
        loaded = load_model_manifest(manifest)
        if loaded is None or loaded.model != SUPPORTED_MODELS[model]:
            raise ValueError("a matching reviewed model manifest is required")
        prefix = tuple(python_command or (sys.executable,))
        if not prefix or not all(
            isinstance(item, str) and item.strip() for item in prefix
        ):
            raise ValueError("python_command must contain non-empty arguments")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        self.model = model
        self.manifest = loaded
        self.command = (
            *prefix,
            str(worker_path or Path(__file__).with_name("reaction_model_worker.py")),
        )
        self.model_location = (
            str(Path(model_location).resolve()) if model_location else None
        )
        self.repository_dir = (
            str(Path(repository_dir).resolve()) if repository_dir else None
        )
        self.timeout_seconds = float(timeout_seconds)
        self.env = dict(env or {})

    def run(self, operation: str, inputs: Any, **options: Any) -> dict[str, Any]:
        request_id = str(options.pop("request_id", None) or uuid.uuid4())
        request = {
            "schema_version": WIRE_SCHEMA_VERSION,
            "request_id": request_id,
            "backend": self.model,
            "operation": operation,
            "inputs": _json_copy(inputs, field="inputs"),
            "options": _json_copy(options, field="options"),
            "model_location": self.model_location,
            "repository_dir": self.repository_dir,
            "model_manifest": self.manifest.to_dict(),
        }
        encoded = (json.dumps(request, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise BackendProtocolError("reaction backend request exceeds 8 MiB")
        process_env = os.environ.copy()
        process_env.update(self.env)
        try:
            completed = subprocess.run(
                self.command,
                input=encoded,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                env=process_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendExecutionError(
                "timeout", f"reaction backend exceeded {self.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise BackendExecutionError("spawn_failed", str(exc)) from exc
        if len(completed.stdout) > MAX_RESPONSE_BYTES:
            raise BackendExecutionError(
                "response_too_large", "reaction backend response exceeded 32 MiB"
            )
        if completed.returncode:
            stderr = _PATH_IN_TEXT.sub(
                "<redacted-path>", completed.stderr.decode("utf-8", errors="replace")
            )[-4000:]
            raise BackendExecutionError(
                "nonzero_exit",
                f"reaction backend exited {completed.returncode}: {stderr}",
            )
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendExecutionError(
                "invalid_json", "reaction backend did not return one JSON object"
            ) from exc
        return _normalize_response(
            response, request_id=request_id, manifest=self.manifest
        )

    def capabilities(self) -> dict[str, Any]:
        return self.run("capabilities", [])

    def map_reactions(
        self, reactions: list[dict[str, str]], *, batch_size: int = 32
    ) -> dict[str, Any]:
        return self.run("map_reactions", reactions, batch_size=batch_size)

    def plan_routes(
        self,
        targets: list[dict[str, str]],
        *,
        config_path: str,
        policies: Iterable[str] = (),
        filters: Iterable[str] = (),
        stocks: Iterable[str] = (),
        max_routes: int = 10,
    ) -> dict[str, Any]:
        return self.run(
            "plan_routes",
            targets,
            config_path=str(Path(config_path).resolve()),
            policies=list(policies),
            filters=list(filters),
            stocks=list(stocks),
            max_routes=max_routes,
        )

    def predict_products(
        self,
        records: list[dict[str, str]],
        *,
        top_k: int = 5,
        max_new_tokens: int = 128,
        device: str = "cpu",
    ) -> dict[str, Any]:
        return self.run(
            "predict_products",
            records,
            top_k=top_k,
            max_new_tokens=max_new_tokens,
            device=device,
        )

    def predict_yields(
        self,
        records: list[dict[str, str]],
        *,
        input_max_length: int = 400,
        device: str = "cpu",
    ) -> dict[str, Any]:
        return self.run(
            "predict_yields",
            records,
            input_max_length=input_max_length,
            device=device,
        )

    def recommend_conditions(
        self,
        reactions: list[dict[str, str]],
        *,
        config_path: str,
        workspace_dir: str,
        gpu: int = -1,
    ) -> dict[str, Any]:
        return self.run(
            "recommend_conditions",
            reactions,
            config_path=config_path,
            workspace_dir=workspace_dir,
            gpu=gpu,
        )


__all__ = ["ReactionModelBackend", "SUPPORTED_MODELS", "WIRE_SCHEMA_VERSION"]
