"""Isolated JSON worker for optional Syntheseus and RetroChimera inference."""
from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import json
import math
import platform
import sys
import time
from collections.abc import Mapping
from typing import Any

WIRE_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
SUPPORTED_MODELS = {
    "RetroChimera": ("retrochimera", "RetroChimeraModel"),
    "RetroChimeraEdit": ("retrochimera", "RetroChimeraEditModel"),
    "RetroChimeraDeNovo": ("retrochimera", "RetroChimeraDeNovoModel"),
    "Chemformer": (
        "syntheseus.reaction_prediction.inference",
        "ChemformerModel",
    ),
    "GLN": ("syntheseus.reaction_prediction.inference", "GLNModel"),
    "Graph2Edits": (
        "syntheseus.reaction_prediction.inference",
        "Graph2EditsModel",
    ),
    "LocalRetro": (
        "syntheseus.reaction_prediction.inference",
        "LocalRetroModel",
    ),
    "MEGAN": ("syntheseus.reaction_prediction.inference", "MEGANModel"),
    "MHNreact": (
        "syntheseus.reaction_prediction.inference",
        "MHNreactModel",
    ),
    "RetroKNN": (
        "syntheseus.reaction_prediction.inference",
        "RetroKNNModel",
    ),
    "RootAligned": (
        "syntheseus.reaction_prediction.inference",
        "RootAlignedModel",
    ),
}


class RequestError(ValueError):
    """Structured request failure returned to the host as JSON."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _clean_text(value: Any, *, field_name: str, max_length: int = 10000) -> str:
    if not isinstance(value, str):
        raise RequestError("invalid_request", f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise RequestError("invalid_request", f"{field_name} must not be empty")
    if len(cleaned) > max_length:
        raise RequestError(
            "invalid_request",
            f"{field_name} must contain at most {max_length} characters",
        )
    return cleaned


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _runtime_info() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "packages": {
            "syntheseus": _package_version("syntheseus"),
            "retrochimera": _package_version("retrochimera"),
        },
    }


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<max-depth>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in ("path", "directory")):
                continue
            result[key_text] = _json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    return str(value)[:500]


def _score_from_metadata(
    metadata: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    for key in ("probability", "score", "confidence", "log_probability"):
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            return number, key
    return None, None


def _normalize_manifest(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RequestError("invalid_manifest", "model_manifest must be an object")
    return _json_safe(dict(value))


def _load_model_class(model_name: str) -> type[Any]:
    try:
        module_name, class_name = SUPPORTED_MODELS[model_name]
    except KeyError as exc:
        raise RequestError(
            "unsupported_model",
            f"unsupported model {model_name!r}; expected one of "
            + ", ".join(SUPPORTED_MODELS),
        ) from exc
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RequestError(
            "dependency_missing",
            f"could not import {module_name!r}; install the optional model environment",
        ) from exc
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise RequestError(
            "dependency_incompatible",
            f"{module_name!r} does not export {class_name!r}",
        ) from exc


def _validate_base_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RequestError("invalid_request", "request must be a JSON object")
    allowed = {
        "schema_version",
        "request_id",
        "operation",
        "target_smiles",
        "model",
        "model_dir",
        "num_results",
        "allow_model_download",
        "model_manifest",
    }
    unknown = set(value) - allowed
    if unknown:
        raise RequestError(
            "invalid_request",
            "unsupported request field(s): " + ", ".join(sorted(unknown)),
        )
    if value.get("schema_version") != WIRE_SCHEMA_VERSION:
        raise RequestError(
            "unsupported_schema",
            f"unsupported schema_version {value.get('schema_version')!r}",
        )
    request_id = _clean_text(value.get("request_id"), field_name="request_id")
    operation = _clean_text(value.get("operation"), field_name="operation")
    return {**dict(value), "request_id": request_id, "operation": operation}


def _capabilities(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": WIRE_SCHEMA_VERSION,
        "request_id": request["request_id"],
        "backend": "syntheseus",
        "operation": "capabilities",
        "ok": True,
        "capabilities": {
            "operations": ["capabilities", "single_step"],
            "models": list(SUPPORTED_MODELS),
            "max_num_results": 10,
            "automatic_checkpoint_download_default": False,
            "protocol": "one-request-one-response-json",
        },
        "runtime": _runtime_info(),
        "warnings": [],
        "elapsed_seconds": 0.0,
    }


def _single_step(request: Mapping[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    target = _clean_text(request.get("target_smiles"), field_name="target_smiles")
    model_name = _clean_text(request.get("model"), field_name="model")
    if model_name not in SUPPORTED_MODELS:
        raise RequestError(
            "unsupported_model",
            f"unsupported model {model_name!r}; expected one of "
            + ", ".join(SUPPORTED_MODELS),
        )
    allow_download = request.get("allow_model_download", False)
    if not isinstance(allow_download, bool):
        raise RequestError("invalid_request", "allow_model_download must be a boolean")
    model_dir_value = request.get("model_dir")
    model_dir = None
    if model_dir_value not in (None, ""):
        model_dir = _clean_text(
            model_dir_value, field_name="model_dir", max_length=5000
        )
    if model_dir is None and not allow_download:
        raise RequestError(
            "checkpoint_required",
            "model_dir is required because automatic checkpoint downloads are disabled",
        )
    num_results = request.get("num_results", 5)
    if isinstance(num_results, bool) or not isinstance(num_results, int):
        raise RequestError(
            "invalid_request", "num_results must be an integer between 1 and 10"
        )
    if not 1 <= num_results <= 10:
        raise RequestError("invalid_request", "num_results must be between 1 and 10")
    manifest = _normalize_manifest(request.get("model_manifest"))
    warnings: list[str] = []
    if manifest is None:
        warnings.append(
            "No model manifest was supplied; checkpoint provenance is incomplete."
        )
    if model_dir is None:
        warnings.append(
            "Automatic checkpoint download was explicitly enabled for this run."
        )

    try:
        with contextlib.redirect_stdout(sys.stderr):
            syntheseus = importlib.import_module("syntheseus")
            molecule_class = getattr(syntheseus, "Molecule")
            model_class = _load_model_class(model_name)
            kwargs = {"model_dir": model_dir} if model_dir is not None else {}
            model = model_class(**kwargs)
            molecule = molecule_class(target)
            batches = model([molecule], num_results=num_results)
    except RequestError:
        raise
    except Exception as exc:
        raise RequestError(
            "inference_failed",
            f"{type(exc).__name__}: {str(exc)[:1500]}",
            retryable=False,
        ) from exc

    first_batch = list(batches[0]) if batches else []
    predictions: list[dict[str, Any]] = []
    for rank, prediction in enumerate(first_batch[:num_results], start=1):
        reactants = getattr(prediction, "reactants_str", None)
        if not isinstance(reactants, str) or not reactants.strip():
            reactants_value = getattr(prediction, "reactants", None)
            if reactants_value is not None:
                reactants = ".".join(
                    sorted(
                        str(getattr(molecule, "smiles", molecule))
                        for molecule in reactants_value
                    )
                )
        if not isinstance(reactants, str) or not reactants.strip():
            continue
        reaction_smiles = getattr(prediction, "reaction_smiles", None)
        if not isinstance(reaction_smiles, str) or not reaction_smiles.strip():
            reaction_smiles = f"{reactants}>>{target}"
        metadata_value = getattr(prediction, "metadata", {})
        metadata = (
            _json_safe(metadata_value) if isinstance(metadata_value, Mapping) else {}
        )
        score, score_type = _score_from_metadata(
            metadata_value if isinstance(metadata_value, Mapping) else {}
        )
        predictions.append(
            {
                "rank": rank,
                "reactants_smiles": reactants,
                "reaction_smiles": reaction_smiles,
                "score": score,
                "score_type": score_type,
                "metadata": metadata,
            }
        )

    return {
        "schema_version": WIRE_SCHEMA_VERSION,
        "request_id": request["request_id"],
        "backend": "syntheseus",
        "operation": "single_step",
        "ok": True,
        "target_smiles": target,
        "model": model_name,
        "predictions": predictions,
        "model_manifest": manifest,
        "runtime": _runtime_info(),
        "warnings": warnings,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


def handle_request(value: Any) -> dict[str, Any]:
    request = _validate_base_request(value)
    if request["operation"] == "capabilities":
        return _capabilities(request)
    if request["operation"] == "single_step":
        return _single_step(request)
    raise RequestError(
        "unsupported_operation", f"unsupported operation {request['operation']!r}"
    )


def _error_response(
    *,
    request_id: str,
    operation: str,
    code: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "schema_version": WIRE_SCHEMA_VERSION,
        "request_id": request_id,
        "backend": "syntheseus",
        "operation": operation,
        "ok": False,
        "runtime": _runtime_info(),
        "warnings": [],
        "elapsed_seconds": None,
        "error": {
            "code": code,
            "message": message[:2000],
            "retryable": retryable,
        },
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        response = _error_response(
            request_id="unknown",
            operation="unknown",
            code="request_too_large",
            message="request exceeded 1 MiB",
            retryable=False,
        )
        print(json.dumps(response, sort_keys=True))
        return 0
    request_id = "unknown"
    operation = "unknown"
    try:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, Mapping):
            if isinstance(payload.get("request_id"), str):
                request_id = payload["request_id"]
            if isinstance(payload.get("operation"), str):
                operation = payload["operation"]
        response = handle_request(payload)
    except json.JSONDecodeError:
        response = _error_response(
            request_id=request_id,
            operation=operation,
            code="invalid_json",
            message="stdin did not contain one JSON object",
            retryable=False,
        )
    except RequestError as exc:
        response = _error_response(
            request_id=request_id,
            operation=operation,
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
        )
    except Exception as exc:
        response = _error_response(
            request_id=request_id,
            operation=operation,
            code="worker_failure",
            message=f"{type(exc).__name__}: {str(exc)[:1500]}",
            retryable=False,
        )
    print(json.dumps(response, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
