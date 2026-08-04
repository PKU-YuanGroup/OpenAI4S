"""Isolated JSON worker for optional Syntheseus and RetroChimera inference."""
from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import time
from collections.abc import Mapping
from typing import Any

WIRE_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
REDACTED_PATH = "<redacted-path>"
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


#: Values shaped like an absolute filesystem path, a home-relative path, a UNC
#: share or a ``file://`` URL. The ``~`` branch requires a username-shaped run
#: before the separator: a looser ``~[^/\\]*[/\\]`` also swallows ordinary
#: approximate quantities such as ``~5 kcal/mol``.
_PATH_LIKE_VALUE = re.compile(
    r"^(?:/|~[A-Za-z0-9._-]*[/\\]|\\\\|[A-Za-z]:[\\/]|file://)", re.IGNORECASE
)
#: The same shapes, found anywhere inside free text. Only ever applied to
#: message strings, never to model metadata: the leading-``/`` branch would
#: also fire on the bond-direction slashes inside a SMILES like ``F/C=C/F``.
_PATH_IN_TEXT = re.compile(
    r"(?<![\w~])(?:~?/[^\s'\"<>,;)]*|[A-Za-z]:[\\/][^\s'\"<>,;)]*)"
)


def _is_path_like_value(value: str) -> bool:
    """Match a value that *begins* with a filesystem location.

    Key names alone are not a boundary — a model wrapper is free to report a
    checkpoint or cache location under any name it likes — so the value shape
    is what actually keeps a workstation path out of a published artifact.

    The match is anchored on purpose. A path mentioned mid-sentence is left
    alone because the unanchored form cannot tell ``kcal/mol`` or the bond
    directions in ``F/C=C/F`` from a directory, and mangling chemistry to
    catch a prose mention is the worse trade. `_scrub_text` covers the one
    place where free text is expected: an error message.
    """
    text = value.strip()
    return bool(text) and _PATH_LIKE_VALUE.match(text) is not None


def _scrub_text(text: str) -> str:
    """Replace filesystem paths anywhere inside a free-text message.

    Exception text is the one channel that reliably carries the caller's
    ``model_dir``: a missing checkpoint surfaces as ``FileNotFoundError:
    [Errno 2] ... '/home/chemist/private/model.ckpt'``, which would otherwise
    publish the exact path the success response deliberately withholds.
    """
    return _PATH_IN_TEXT.sub(REDACTED_PATH, text)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<max-depth>"
    if isinstance(value, str):
        return REDACTED_PATH if _is_path_like_value(value) else value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            # The substring rule stays narrow. Widening it to every
            # ``dir``/``file``/``cache`` token also deletes ``use_cache``,
            # ``bond_dir`` and ``n_files``, none of which can hold a path;
            # the value check below is what closes the leak instead.
            if any(token in key_text.lower() for token in ("path", "directory")):
                continue
            if _is_path_like_value(key_text):
                key_text = REDACTED_PATH
            result[key_text] = _json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    text = str(value)[:500]
    return REDACTED_PATH if _is_path_like_value(text) else text


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
    """Echo the caller's manifest back without rewriting it.

    Deliberately not passed through `_json_safe`. The manifest is authored by
    the operator, is contractually path-free, and the host recomputes
    `manifest_fingerprint` from whatever comes back — so filtering it here
    would mean the published fingerprint no longer reproduces from the
    reviewed manifest file, and two manifests differing only in a filtered
    field would publish the same fingerprint. Redaction belongs on
    model-reported metadata, which the worker does not control.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RequestError("invalid_manifest", "model_manifest must be an object")
    try:
        copied = json.loads(json.dumps(dict(value), sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise RequestError(
            "invalid_manifest", "model_manifest must be JSON serializable"
        ) from exc
    if not isinstance(copied, dict):
        raise RequestError("invalid_manifest", "model_manifest must be an object")
    return copied


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
            # Scrubbed here rather than at each raise site so that every error
            # channel out of this worker is covered, including the ones that
            # carry a third-party exception's text verbatim.
            "message": _scrub_text(message)[:2000],
            "retryable": retryable,
        },
    }


_RESERVED_FD: int | None = None


def _close_reserved_fd_in_child() -> None:
    """Drop the reserved descriptor in a forked child.

    ``os.dup`` marks the copy non-inheritable, but that only takes effect at
    ``exec``. A model that forks without exec — a PyTorch ``DataLoader`` on the
    default Linux start method does exactly this — would otherwise keep the
    host's stdout pipe open for the life of the child, so the host blocks in
    ``communicate()`` until the timeout on a run whose response was already
    written correctly and whose worker already exited 0.
    """
    global _RESERVED_FD
    if _RESERVED_FD is None:
        return
    try:
        os.close(_RESERVED_FD)
    except OSError:
        pass
    _RESERVED_FD = None


def _reserve_protocol_stdout() -> int | None:
    """Point fd 1 at stderr and return a private duplicate of the original.

    ``contextlib.redirect_stdout`` only rebinds the ``sys.stdout`` object.
    A native library that writes to descriptor 1 directly — PyTorch, DGL, CUDA
    and RDKit all do — would still land inside the response and break the
    one-JSON-object contract, with no way for the host to attribute the
    failure. Swapping the descriptor itself is what ``kernel/worker.py`` does
    with ``dup2`` and what ``kernel/r_worker.R`` does with shell redirection.

    Returns ``None`` when there is no usable stderr to hide behind, in which
    case the caller writes the response on the inherited stdout and the
    process is no better protected than it was before — but visibly so, rather
    than through a swap that silently did nothing.

    Deliberately not flushed first: bytes buffered in ``sys.stdout`` before
    this runs are better delivered at interpreter shutdown, by which time fd 1
    is stderr. Flushing here is the only thing that would push them into the
    response. Output already written *through* fd 1 before this point — an
    interpreter-startup banner from ``sitecustomize`` reached over an
    inherited ``PYTHONPATH``, say — is beyond any in-process fix.
    """
    global _RESERVED_FD
    try:
        # A closed fd 2 would make os.dup(1) below return descriptor 2, and
        # os.dup2(2, 1) would then alias fd 1 onto the reserved copy: a swap
        # that reports success and protects nothing.
        os.fstat(2)
        reserved = os.dup(1)
        os.dup2(2, 1)
    except (AttributeError, OSError):
        return None
    _RESERVED_FD = reserved
    register_at_fork = getattr(os, "register_at_fork", None)
    if register_at_fork is not None:
        register_at_fork(after_in_child=_close_reserved_fd_in_child)
    return reserved


def _emit_response(response: Mapping[str, Any], *, reserved_fd: int | None) -> None:
    """Write exactly one JSON object on the reserved protocol descriptor."""
    global _RESERVED_FD
    payload = (json.dumps(response, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    if reserved_fd is None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    _RESERVED_FD = None
    with os.fdopen(reserved_fd, "wb", closefd=True) as handle:
        handle.write(payload)
        handle.flush()


def main() -> int:
    reserved_fd = _reserve_protocol_stdout()
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        response = _error_response(
            request_id="unknown",
            operation="unknown",
            code="request_too_large",
            message="request exceeded 1 MiB",
            retryable=False,
        )
        _emit_response(response, reserved_fd=reserved_fd)
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
    _emit_response(response, reserved_fd=reserved_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
