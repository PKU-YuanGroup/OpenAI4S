"""example_stats.kernel — importable sidecar for the `stats` skill.

Pure-stdlib descriptive statistics. Imported by the agent inside a kernel cell
via `from example_stats.kernel import summary, quantile, zscore, correlation`.
"""

from __future__ import annotations

import math
from typing import Sequence


def _check(xs: Sequence[float]) -> None:
    if not xs:
        raise ValueError("input sequence is empty")


def mean(xs: Sequence[float]) -> float:
    _check(xs)
    return sum(xs) / len(xs)


def std(xs: Sequence[float], population: bool = False) -> float:
    _check(xs)
    n = len(xs)
    if n == 1:
        return 0.0
    m = mean(xs)
    ss = sum((x - m) ** 2 for x in xs)
    denom = n if population else (n - 1)
    return math.sqrt(ss / denom)


def median(xs: Sequence[float]) -> float:
    _check(xs)
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2


def quantile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile, q in [0, 1]."""
    _check(xs)
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(s[lo])
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def zscore(xs: Sequence[float], population: bool = False) -> list[float]:
    _check(xs)
    m = mean(xs)
    sd = std(xs, population=population)
    if sd == 0:
        return [0.0 for _ in xs]
    return [(x - m) / sd for x in xs]


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    _check(xs)
    _check(ys)
    if len(xs) != len(ys):
        raise ValueError("series must have equal length")
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        raise ValueError("cannot correlate a constant series")
    return cov / (dx * dy)


def summary(xs: Sequence[float], population: bool = False) -> dict:
    _check(xs)
    return {
        "n": len(xs),
        "mean": mean(xs),
        "std": std(xs, population=population),
        "min": min(xs),
        "max": max(xs),
        "median": median(xs),
    }
