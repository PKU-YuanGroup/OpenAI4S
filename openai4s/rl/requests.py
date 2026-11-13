"""Canonical rollout request identity and frozen trajectory decoding.

The durable Run Receipt path must hash the caller's logical request, not the
objects a retry happens to construct.  Generated reset/workspace UUIDs and the
Episode's creation timestamp are therefore excluded; every other identity input
is bound into one strict, canonical JSON document.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    Segment,
    Trajectory,
)

REQUEST_PROTOCOL = 2
TRAJECTORY_SCHEMA = "openai4s.science-trajectory.v1"

_TRAJECTORY_KEYS = frozenset(
    {
        "schema_version",
        "episode",
        "observations",
        "decisions",
        "segments",
        "execution_events",
        "rewards",
        "costs",
    }
)


def _json_value(value: Any, path: str = "$") -> Any:
    """Return a detached strict-JSON value or fail loudly.

    The receipt fingerprint is compared across processes and restarts, so a
    silent ``str()`` coercion or a NaN that serializes differently in another
    encoder would be a false identity match.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            result[key] = _json_value(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, f"{path}[{index}]") for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} contains unsupported value {type(value)!r}")


def canonical_json(value: Any) -> str:
    """Serialize one value with the single canonical JSON encoding."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fingerprint_document(request: Any) -> Any:
    """Drop only the dynamic Episode timestamp from a request document.

    The normalized payload keeps the original ``created_at`` for replay and
    reconstruction, but a caller retry at a later wall-clock time is the same
    logical request.  Every other field remains bound.
    """

    document = _json_value(request)
    if isinstance(document, Mapping):
        document = dict(document)
        episode = document.get("episode")
        if isinstance(episode, Mapping):
            episode = dict(episode)
            episode.pop("created_at", None)
            document["episode"] = episode
    return document


def request_fingerprint(request: Any) -> str:
    """Return the SHA-256 of one request's canonical identity document."""

    return hashlib.sha256(
        canonical_json(_fingerprint_document(request)).encode("utf-8")
    ).hexdigest()


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    return _text(name, value)


def _mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _artifacts(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _mapping("initial_artifacts", value)
    result: dict[str, str] = {}
    for key, item in mapping.items():
        result[_text("initial_artifacts key", key)] = _text(
            "initial_artifacts value", item
        )
    return result


_RESET_METADATA_KEYS = frozenset(
    {"reset_id", "workspace_id", "initial_artifacts", "config", "budget"}
)


def _generated_uuid(prefix: str, value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value[len(prefix) :]
    return len(suffix) == 32 and all(
        character in "0123456789abcdef" for character in suffix
    )


def _is_generated_reset_identity(reset: Mapping[str, Any]) -> bool:
    """Whether a metadata reset block is exactly the environment's projection.

    Caller metadata is never discarded just because a key is named ``reset``,
    ``reset_id``, or ``workspace_id``.  Only the shape produced by
    :meth:`EpisodeReset.public_metadata` (generated UUIDs, at most the known
    public fields, no caller extras) is treated as retry noise.
    """

    keys = set(reset)
    if not keys or not keys <= _RESET_METADATA_KEYS:
        return False
    if not {"reset_id", "workspace_id"} <= keys:
        return False
    return _generated_uuid("reset-", reset["reset_id"]) and _generated_uuid(
        "workspace-", reset["workspace_id"]
    )


def normalize_request(
    episode: Episode,
    messages: Iterable[Mapping[str, Any]],
    initial_artifacts: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    max_turns: int | None = 32,
) -> dict[str, Any]:
    """Return the canonical identity document for one rollout request.

    The original ``Episode.created_at`` is retained in the stored details for
    replay, but :func:`request_fingerprint` excludes it: a caller retry at a
    later wall-clock time is the same logical request.  Only a generated
    environment reset/workspace identity is excluded from the payload;
    arbitrary caller metadata is always bound into the fingerprint.
    """

    if not isinstance(episode, Episode):
        raise TypeError("episode must be an Episode")
    if max_turns is None:
        max_turns = 32
    if isinstance(max_turns, bool) or not isinstance(max_turns, int):
        raise TypeError("max_turns must be an integer")
    if max_turns < 0:
        raise ValueError("max_turns must be non-negative")

    metadata = dict(episode.metadata)
    reset = metadata.get("reset")
    if isinstance(reset, Mapping) and _is_generated_reset_identity(reset):
        normalized_reset = dict(reset)
        normalized_reset.pop("reset_id", None)
        normalized_reset.pop("workspace_id", None)
        metadata["reset"] = normalized_reset

    normalized_messages: list[dict[str, Any]] = []
    for message in messages:
        normalized_messages.append(dict(_mapping("message", message)))

    return _json_value(
        {
            "episode": {
                "episode_id": _text("episode_id", episode.episode_id),
                "task": _text("task", episode.task),
                "session_id": _text("session_id", episode.session_id),
                "created_at": episode.created_at,
                "policy_name": _optional_text("policy_name", episode.policy_name),
                "policy_version": _optional_text(
                    "policy_version", episode.policy_version
                ),
                "parent_episode_id": _optional_text(
                    "parent_episode_id", episode.parent_episode_id
                ),
                "metadata": metadata,
            },
            "messages": normalized_messages,
            "initial_artifacts": _artifacts(initial_artifacts),
            "config": _json_value(
                {} if config is None else dict(_mapping("config", config))
            ),
            "budget": _json_value(
                {} if budget is None else dict(_mapping("budget", budget))
            ),
            "max_turns": max_turns,
        }
    )


def _record(value: Any, record_type: Any, field: str) -> Any:
    if not isinstance(value, Mapping):
        raise ValueError(f"trajectory {field} entries must be objects")
    return record_type(**dict(value))


def _records(value: Any, record_type: Any, field: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"trajectory {field} must be a list")
    return tuple(_record(item, record_type, field) for item in value)


def decode_trajectory(value: str | bytes | Mapping[str, Any]) -> Trajectory:
    """Decode one frozen canonical trajectory envelope.

    The schema is closed: an unknown top-level key or a future schema version
    is rejected rather than partially decoded into a trajectory that looks
    complete while dropping evidence.
    """

    if isinstance(value, (str, bytes)):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("trajectory payload is not valid JSON") from exc
    else:
        decoded = value
    if not isinstance(decoded, Mapping):
        raise ValueError("trajectory payload must be an object")

    unknown = set(decoded) - _TRAJECTORY_KEYS
    if unknown:
        raise ValueError(
            "trajectory payload has unsupported fields: "
            + ", ".join(sorted(str(key) for key in unknown))
        )
    schema_version = decoded.get("schema_version", TRAJECTORY_SCHEMA)
    if schema_version != TRAJECTORY_SCHEMA:
        raise ValueError(f"unsupported trajectory schema {schema_version!r}")

    episode = decoded.get("episode")
    if not isinstance(episode, Mapping):
        raise ValueError("trajectory episode must be an object")

    return Trajectory(
        episode=Episode(**dict(episode)),
        observations=_records(decoded.get("observations"), Observation, "observations"),
        decisions=_records(decoded.get("decisions"), PolicyDecision, "decisions"),
        segments=_records(decoded.get("segments"), Segment, "segments"),
        execution_events=_records(
            decoded.get("execution_events"), ExecutionEvent, "execution_events"
        ),
        rewards=_records(decoded.get("rewards"), RewardAnnotation, "rewards"),
        costs=_records(decoded.get("costs"), CostRecord, "costs"),
        schema_version=schema_version,
    )


__all__ = [
    "REQUEST_PROTOCOL",
    "TRAJECTORY_SCHEMA",
    "canonical_json",
    "decode_trajectory",
    "normalize_request",
    "request_fingerprint",
]
