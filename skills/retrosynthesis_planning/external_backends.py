"""Stdlib-only adapters for isolated retrosynthesis model processes."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WIRE_SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
SCIENTIFIC_DISCLAIMER = (
    "Model proposals and scores are hypotheses for expert review. They are not "
    "experimental success probabilities, yields, safety assessments, or evidence "
    "that a reaction is feasible."
)

SUPPORTED_SYNTHESEUS_MODELS = (
    "RetroChimera",
    "RetroChimeraEdit",
    "RetroChimeraDeNovo",
    "Chemformer",
    "GLN",
    "Graph2Edits",
    "LocalRetro",
    "MEGAN",
    "MHNreact",
    "RetroKNN",
    "RootAligned",
)


REDACTED_PATH = "<redacted-path>"
#: Deliberately duplicated from ``syntheseus_worker`` rather than imported: the
#: worker has to stay standalone so it can run inside a foreign model
#: environment that does not have this package on its path.
_PATH_IN_TEXT = re.compile(
    r"(?<![\w~])(?:~?/[^\s'\"<>,;)]*|[A-Za-z]:[\\/][^\s'\"<>,;)]*)"
)


def _scrub_paths(text: str) -> str:
    """Strip filesystem paths out of text captured from the backend process.

    The worker points its stdout at stderr, so everything a native model
    library prints — checkpoint locations included — now arrives on the
    stream this class embeds verbatim into a ``nonzero_exit`` message. Without
    this the descriptor swap would trade a corrupted response for a published
    workstation path.
    """
    return _PATH_IN_TEXT.sub(REDACTED_PATH, text)


class BackendProtocolError(ValueError):
    """Raised when a backend request or response violates the wire contract."""


class BackendExecutionError(RuntimeError):
    """Raised when an isolated backend process cannot return a response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any, *, field_name: str, max_length: int = 500) -> str:
    if not isinstance(value, str):
        raise BackendProtocolError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise BackendProtocolError(f"{field_name} must not be empty")
    if len(cleaned) > max_length:
        raise BackendProtocolError(
            f"{field_name} must contain at most {max_length} characters"
        )
    return cleaned


def _optional_text(value: Any, *, field_name: str, max_length: int = 500) -> str | None:
    if value in (None, ""):
        return None
    return _clean_text(value, field_name=field_name, max_length=max_length)


def _json_copy(value: Any, *, field_name: str) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise BackendProtocolError(f"{field_name} must be JSON serializable") from exc


def _finite_number(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BackendProtocolError(f"{field_name} must be a finite number or null")
    number = float(value)
    if not math.isfinite(number):
        raise BackendProtocolError(f"{field_name} must be finite")
    return number


def _normalize_sha256(value: Any, *, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    digest = _clean_text(value, field_name=field_name, max_length=64).lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise BackendProtocolError(f"{field_name} must be a 64-character SHA-256")
    return digest


def _normalize_source_url(value: Any) -> str | None:
    url = _optional_text(value, field_name="source_url", max_length=2000)
    if url is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BackendProtocolError("source_url must be an absolute HTTP(S) URL")
    return url


@dataclass(frozen=True, slots=True)
class ModelManifest:
    """Public, path-free provenance for one model checkpoint."""

    provider: str
    model: str
    model_version: str
    checkpoint_id: str
    training_dataset: str
    code_license: str
    checkpoint_license: str
    checkpoint_sha256: str | None = None
    source_url: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = WIRE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != WIRE_SCHEMA_VERSION
        ):
            raise BackendProtocolError(
                f"unsupported model manifest schema_version {self.schema_version!r}"
            )
        for field_name in (
            "provider",
            "model",
            "model_version",
            "checkpoint_id",
            "training_dataset",
            "code_license",
            "checkpoint_license",
        ):
            object.__setattr__(
                self,
                field_name,
                _clean_text(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "checkpoint_sha256",
            _normalize_sha256(self.checkpoint_sha256, field_name="checkpoint_sha256"),
        )
        object.__setattr__(self, "source_url", _normalize_source_url(self.source_url))
        metadata = _json_copy(dict(self.metadata), field_name="metadata")
        if not isinstance(metadata, dict):
            raise BackendProtocolError("metadata must be a mapping")
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelManifest":
        allowed = {
            "schema_version",
            "provider",
            "model",
            "model_version",
            "checkpoint_id",
            "checkpoint_sha256",
            "training_dataset",
            "code_license",
            "checkpoint_license",
            "source_url",
            "metadata",
        }
        unknown = set(value) - allowed
        if unknown:
            names = ", ".join(sorted(unknown))
            raise BackendProtocolError(f"unsupported model manifest field(s): {names}")
        required = {
            "provider",
            "model",
            "model_version",
            "checkpoint_id",
            "training_dataset",
            "code_license",
            "checkpoint_license",
        }
        missing = sorted(required - set(value))
        if missing:
            raise BackendProtocolError(
                "model manifest missing required field(s): " + ", ".join(missing)
            )
        return cls(
            schema_version=value.get("schema_version", WIRE_SCHEMA_VERSION),
            provider=value["provider"],
            model=value["model"],
            model_version=value["model_version"],
            checkpoint_id=value["checkpoint_id"],
            checkpoint_sha256=value.get("checkpoint_sha256"),
            training_dataset=value["training_dataset"],
            code_license=value["code_license"],
            checkpoint_license=value["checkpoint_license"],
            source_url=value.get("source_url"),
            metadata=value.get("metadata") or {},
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ModelManifest":
        manifest_path = Path(path).expanduser()
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise BackendProtocolError("model manifest file must contain an object")
        return cls.from_mapping(payload)

    @property
    def provenance_status(self) -> str:
        license_values = {
            self.code_license.strip().lower(),
            self.checkpoint_license.strip().lower(),
        }
        incomplete_licenses = {"unknown", "unspecified", "review-required"}
        if (
            self.checkpoint_sha256
            and self.training_dataset.strip().lower() not in {"unknown", "unspecified"}
            and not (license_values & incomplete_licenses)
        ):
            return "complete"
        return "incomplete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_sha256": self.checkpoint_sha256,
            "training_dataset": self.training_dataset,
            "code_license": self.code_license,
            "checkpoint_license": self.checkpoint_license,
            "source_url": self.source_url,
            "metadata": dict(self.metadata),
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def load_model_manifest(
    value: ModelManifest | Mapping[str, Any] | str | Path | None,
) -> ModelManifest | None:
    """Load a model manifest from an object, mapping, JSON path, or ``None``."""
    if value is None:
        return None
    if isinstance(value, ModelManifest):
        return value
    if isinstance(value, Mapping):
        return ModelManifest.from_mapping(value)
    if isinstance(value, (str, Path)):
        return ModelManifest.from_json_file(value)
    raise TypeError("manifest must be a ModelManifest, mapping, JSON path, or None")


def _normalize_error(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendProtocolError("error response must contain an error object")
    allowed = {"code", "message", "retryable"}
    unknown = set(value) - allowed
    if unknown:
        raise BackendProtocolError(
            "unsupported backend error field(s): " + ", ".join(sorted(unknown))
        )
    retryable = value.get("retryable", False)
    if not isinstance(retryable, bool):
        raise BackendProtocolError("error.retryable must be a boolean")
    return {
        "code": _clean_text(value.get("code"), field_name="error.code"),
        "message": _clean_text(
            value.get("message"), field_name="error.message", max_length=2000
        ),
        "retryable": retryable,
    }


def _normalize_prediction(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendProtocolError(f"prediction {index} must be an object")
    allowed = {
        "rank",
        "reactants_smiles",
        "reaction_smiles",
        "score",
        "score_type",
        "metadata",
    }
    unknown = set(value) - allowed
    if unknown:
        raise BackendProtocolError(
            f"prediction {index} has unsupported field(s): "
            + ", ".join(sorted(unknown))
        )
    rank = value.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise BackendProtocolError(f"prediction {index}.rank must be positive")
    metadata = _json_copy(value.get("metadata") or {}, field_name="prediction.metadata")
    if not isinstance(metadata, dict):
        raise BackendProtocolError("prediction.metadata must be a mapping")
    return {
        "rank": rank,
        "reactants_smiles": _clean_text(
            value.get("reactants_smiles"),
            field_name="prediction.reactants_smiles",
            max_length=10000,
        ),
        "reaction_smiles": _optional_text(
            value.get("reaction_smiles"),
            field_name="prediction.reaction_smiles",
            max_length=20000,
        ),
        "score": _finite_number(value.get("score"), field_name="prediction.score"),
        "score_type": _optional_text(
            value.get("score_type"), field_name="prediction.score_type"
        ),
        "metadata": metadata,
    }


def normalize_external_backend_response(
    value: Any, *, expected_request_id: str | None = None
) -> dict[str, Any]:
    """Validate and normalize a versioned isolated-backend response."""
    if not isinstance(value, Mapping):
        raise BackendProtocolError("backend response must be an object")
    allowed = {
        "schema_version",
        "request_id",
        "backend",
        "operation",
        "ok",
        "target_smiles",
        "model",
        "predictions",
        "model_manifest",
        "runtime",
        "warnings",
        "elapsed_seconds",
        "capabilities",
        "error",
    }
    unknown = set(value) - allowed
    if unknown:
        raise BackendProtocolError(
            "unsupported backend response field(s): " + ", ".join(sorted(unknown))
        )
    if value.get("schema_version") != WIRE_SCHEMA_VERSION:
        raise BackendProtocolError(
            f"unsupported backend schema_version {value.get('schema_version')!r}"
        )
    request_id = _clean_text(value.get("request_id"), field_name="request_id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise BackendProtocolError(
            f"backend response request_id {request_id!r} does not match request"
        )
    ok = value.get("ok")
    if not isinstance(ok, bool):
        raise BackendProtocolError("ok must be a boolean")
    backend = _clean_text(value.get("backend"), field_name="backend")
    operation = _clean_text(value.get("operation"), field_name="operation")
    warnings_value = value.get("warnings") or []
    if not isinstance(warnings_value, list) or not all(
        isinstance(item, str) for item in warnings_value
    ):
        raise BackendProtocolError("warnings must be a list of strings")
    normalized: dict[str, Any] = {
        "schema_version": WIRE_SCHEMA_VERSION,
        "request_id": request_id,
        "backend": backend,
        "operation": operation,
        "ok": ok,
        "warnings": [item.strip() for item in warnings_value if item.strip()],
        "elapsed_seconds": _finite_number(
            value.get("elapsed_seconds"), field_name="elapsed_seconds"
        ),
        "scientific_disclaimer": SCIENTIFIC_DISCLAIMER,
    }
    runtime = _json_copy(value.get("runtime") or {}, field_name="runtime")
    if not isinstance(runtime, dict):
        raise BackendProtocolError("runtime must be a mapping")
    normalized["runtime"] = runtime

    if not ok:
        normalized["error"] = _normalize_error(value.get("error"))
        return normalized

    if operation == "capabilities":
        capabilities = _json_copy(
            value.get("capabilities") or {}, field_name="capabilities"
        )
        if not isinstance(capabilities, dict):
            raise BackendProtocolError("capabilities must be a mapping")
        normalized["capabilities"] = capabilities
        return normalized

    if operation != "single_step":
        raise BackendProtocolError(f"unsupported successful operation {operation!r}")
    normalized["target_smiles"] = _clean_text(
        value.get("target_smiles"), field_name="target_smiles", max_length=10000
    )
    normalized["model"] = _clean_text(value.get("model"), field_name="model")
    predictions_value = value.get("predictions")
    if not isinstance(predictions_value, list):
        raise BackendProtocolError("predictions must be a list")
    predictions = [
        _normalize_prediction(item, index=index)
        for index, item in enumerate(predictions_value, start=1)
    ]
    ranks = [item["rank"] for item in predictions]
    if len(ranks) != len(set(ranks)):
        raise BackendProtocolError("prediction ranks must be unique")
    predictions.sort(key=lambda item: item["rank"])
    normalized["predictions"] = predictions

    manifest_value = value.get("model_manifest")
    if manifest_value is None:
        normalized["model_manifest"] = None
        normalized["provenance_status"] = "incomplete"
    elif isinstance(manifest_value, Mapping):
        manifest = ModelManifest.from_mapping(manifest_value)
        if manifest.model != normalized["model"]:
            raise BackendProtocolError(
                "model_manifest.model does not match the response model"
            )
        normalized["model_manifest"] = manifest.to_dict()
        normalized["manifest_fingerprint"] = manifest.fingerprint
        normalized["provenance_status"] = manifest.provenance_status
    else:
        raise BackendProtocolError("model_manifest must be an object or null")
    return normalized


class SubprocessRetrosynthesisBackend:
    """Execute a versioned backend worker without ``shell=True``."""

    def __init__(
        self,
        command: Iterable[str],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(command, (str, bytes)):
            raise TypeError("command must be an iterable of arguments, not a string")
        cleaned = []
        for arg in command:
            if not isinstance(arg, str) or not arg.strip():
                raise TypeError("command arguments must be non-empty strings")
            cleaned.append(arg)
        if not cleaned:
            raise ValueError("command must not be empty")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds must be a positive number")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        if isinstance(max_response_bytes, bool) or not isinstance(
            max_response_bytes, int
        ):
            raise TypeError("max_response_bytes must be a positive integer")
        if not 1024 <= max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 KiB and 64 MiB")
        self.command = tuple(cleaned)
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.env = dict(env or {})

    def run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = _json_copy(dict(payload), field_name="backend request")
        if not isinstance(request, dict):
            raise BackendProtocolError("backend request must be an object")
        request_id = _clean_text(request.get("request_id"), field_name="request_id")
        encoded = (
            json.dumps(request, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > DEFAULT_MAX_REQUEST_BYTES:
            raise BackendProtocolError("backend request exceeds 1 MiB")
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
                "timeout",
                f"backend process exceeded {self.timeout_seconds:g} seconds",
            ) from exc
        except OSError as exc:
            raise BackendExecutionError("spawn_failed", str(exc)) from exc

        if len(completed.stdout) > self.max_response_bytes:
            raise BackendExecutionError(
                "response_too_large",
                f"backend stdout exceeded {self.max_response_bytes} bytes",
            )
        if completed.returncode != 0:
            stderr = _scrub_paths(
                completed.stderr.decode("utf-8", errors="replace").strip()
            )
            if len(stderr) > 4000:
                stderr = stderr[-4000:]
            message = f"backend exited with status {completed.returncode}"
            if stderr:
                message += f": {stderr}"
            raise BackendExecutionError("nonzero_exit", message)
        raw = completed.stdout.decode("utf-8", errors="strict").strip()
        if not raw:
            raise BackendExecutionError("empty_response", "backend returned no JSON")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendExecutionError(
                "invalid_json", "backend stdout was not one JSON object"
            ) from exc
        return normalize_external_backend_response(
            response, expected_request_id=request_id
        )


class SyntheseusBackend:
    """Optional single-step backend for RetroChimera and Syntheseus models."""

    name = "syntheseus"
    supported_models = SUPPORTED_SYNTHESEUS_MODELS

    def __init__(
        self,
        *,
        model: str = "RetroChimera",
        model_dir: str | Path | None = None,
        manifest: ModelManifest | Mapping[str, Any] | str | Path | None = None,
        allow_model_download: bool = False,
        python_command: Iterable[str] | None = None,
        worker_path: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        model_name = _clean_text(model, field_name="model")
        if model_name not in self.supported_models:
            raise ValueError(
                f"unsupported Syntheseus model {model_name!r}; expected one of "
                + ", ".join(self.supported_models)
            )
        if not isinstance(allow_model_download, bool):
            raise TypeError("allow_model_download must be a boolean")
        loaded_manifest = load_model_manifest(manifest)
        if loaded_manifest is not None and loaded_manifest.model != model_name:
            raise ValueError("manifest model does not match configured model")
        if isinstance(python_command, (str, bytes)):
            raise TypeError(
                "python_command must be an iterable of arguments, not a string"
            )
        command_prefix = tuple(python_command or (sys.executable,))
        if not command_prefix or not all(
            isinstance(item, str) and item.strip() for item in command_prefix
        ):
            raise ValueError("python_command must contain non-empty arguments")
        worker = Path(worker_path or Path(__file__).with_name("syntheseus_worker.py"))
        self.model = model_name
        self.model_dir = (
            str(Path(model_dir).expanduser()) if model_dir is not None else None
        )
        self.manifest = loaded_manifest
        self.allow_model_download = allow_model_download
        self.worker_path = str(worker)
        self.process = SubprocessRetrosynthesisBackend(
            [*command_prefix, self.worker_path], timeout_seconds=timeout_seconds
        )

    def capabilities(self, *, request_id: str | None = None) -> dict[str, Any]:
        identifier = request_id or str(uuid.uuid4())
        return self.process.run(
            {
                "schema_version": WIRE_SCHEMA_VERSION,
                "request_id": identifier,
                "operation": "capabilities",
            }
        )

    def single_step(
        self,
        target_smiles: str,
        *,
        num_results: int = 5,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        target = _clean_text(
            target_smiles, field_name="target_smiles", max_length=10000
        )
        if self.model_dir is None and not self.allow_model_download:
            raise ValueError(
                "model_dir is required unless allow_model_download=True; automatic "
                "checkpoint downloads are disabled by default"
            )
        if isinstance(num_results, bool) or not isinstance(num_results, int):
            raise TypeError("num_results must be an integer between 1 and 10")
        if not 1 <= num_results <= 10:
            raise ValueError("num_results must be between 1 and 10")
        identifier = request_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "schema_version": WIRE_SCHEMA_VERSION,
            "request_id": identifier,
            "operation": "single_step",
            "target_smiles": target,
            "model": self.model,
            "model_dir": self.model_dir,
            "num_results": num_results,
            "allow_model_download": self.allow_model_download,
            "model_manifest": self.manifest.to_dict() if self.manifest else None,
        }
        return self.process.run(payload)


__all__ = [
    "BackendExecutionError",
    "BackendProtocolError",
    "ModelManifest",
    "SCIENTIFIC_DISCLAIMER",
    "SUPPORTED_SYNTHESEUS_MODELS",
    "SubprocessRetrosynthesisBackend",
    "SyntheseusBackend",
    "WIRE_SCHEMA_VERSION",
    "load_model_manifest",
    "normalize_external_backend_response",
]
