"""Pure-stdlib split and manifest helpers for reproducible ML experiments."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_NAMES = ("train", "validation", "test")


def _fractions(values: Sequence[float]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise ValueError("fractions must contain train, validation, and test")
    fractions = tuple(float(value) for value in values)
    if any(value < 0 for value in fractions):
        raise ValueError("fractions must be non-negative")
    if not abs(sum(fractions) - 1.0) < 1e-9:
        raise ValueError("fractions must sum to 1")
    return fractions  # type: ignore[return-value]


def _sizes(size: int, fractions: Sequence[float]) -> tuple[int, int, int]:
    if size < 0:
        raise ValueError("size must be non-negative")
    train_fraction, validation_fraction, _ = _fractions(fractions)
    train = int(size * train_fraction)
    validation = int(size * validation_fraction)
    return train, validation, size - train - validation


def _partition(indices: Sequence[int], sizes: Sequence[int]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    cursor = 0
    for name, size in zip(_NAMES, sizes):
        result[name] = list(indices[cursor : cursor + size])
        cursor += size
    return result


def random_split(
    size: int,
    *,
    fractions: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 0,
) -> dict[str, list[int]]:
    """Shuffle independent row indices deterministically."""
    sizes = _sizes(size, fractions)
    indices = list(range(size))
    random.Random(seed).shuffle(indices)
    return _partition(indices, sizes)


def chronological_split(
    timestamps: Sequence[Any],
    *,
    fractions: Sequence[float] = (0.7, 0.15, 0.15),
) -> dict[str, list[int]]:
    """Split indices after stable ascending timestamp ordering."""
    sizes = _sizes(len(timestamps), fractions)
    try:
        indices = sorted(range(len(timestamps)), key=timestamps.__getitem__)
    except TypeError as exc:
        raise ValueError("timestamps must be mutually comparable") from exc
    return _partition(indices, sizes)


def grouped_split(
    groups: Sequence[Any],
    *,
    fractions: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 0,
) -> dict[str, list[int]]:
    """Assign every equal group value to exactly one split.

    Groups are ordered by decreasing size with seeded tie-breaking, then placed
    in the split with the lowest fill ratio relative to its target.
    """
    fractions_tuple = _fractions(fractions)
    encoded = [
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)
        for value in groups
    ]
    counts = Counter(encoded)
    rng = random.Random(seed)
    tie_breakers = {group: rng.random() for group in counts}
    ordered = sorted(counts, key=lambda group: (-counts[group], tie_breakers[group]))
    targets = [len(groups) * fraction for fraction in fractions_tuple]
    assigned_counts = [0, 0, 0]
    group_split: dict[str, int] = {}
    for group in ordered:
        eligible = [index for index, fraction in enumerate(fractions_tuple) if fraction]
        destination = min(
            eligible,
            key=lambda index: (
                (assigned_counts[index] + counts[group]) / targets[index],
                index,
            ),
        )
        group_split[group] = destination
        assigned_counts[destination] += counts[group]

    result = {name: [] for name in _NAMES}
    for index, group in enumerate(encoded):
        result[_NAMES[group_split[group]]].append(index)
    return result


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """SHA-256 of a canonical JSON-compatible experiment configuration."""
    payload = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def experiment_manifest(
    config: Mapping[str, Any],
    *,
    data_paths: Sequence[str | Path] = (),
    seeds: Sequence[int] = (),
    code_revision: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-compatible manifest without inventing environment state."""
    return {
        "config": dict(config),
        "config_fingerprint": config_fingerprint(config),
        "data": [
            {"path": str(path), "sha256": file_sha256(path)} for path in data_paths
        ],
        "seeds": [int(seed) for seed in seeds],
        "code_revision": code_revision,
    }
