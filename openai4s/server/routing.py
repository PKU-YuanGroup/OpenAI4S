"""Small declarative primitives for the externally reachable HTTP surface.

A route's method and matcher are protocol facts.  Keeping them in executable
objects lets the runtime and the contract inventory consume the same declaration
instead of teaching a source-code scraper every spelling a handler may use.

This module deliberately owns no handlers and imports no server composition.
That keeps it safe to use from both route modules and contract tooling without
creating a gateway import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Match


@dataclass(frozen=True)
class RouteSpec:
    """One HTTP method + path matcher shared by routing and contract code."""

    name: str
    method: str
    pattern: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("route name must be non-empty")
        if self.method != self.method.upper() or not self.method.isalpha():
            raise ValueError(
                f"route method must be uppercase HTTP verb: {self.method!r}"
            )
        if not self.pattern.startswith("/"):
            raise ValueError(
                f"route pattern must start with '/': {self.pattern!r}"
            )
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(
                f"invalid route pattern {self.pattern!r}: {exc}"
            ) from exc

    def match(self, method: str, path: str) -> Match[str] | None:
        """Match only when both the HTTP method and path belong to this route.

        Wrong-method matches deliberately return ``None``.  The gateway's route
        chain must then continue to its ordinary 404 instead of treating a path
        match as a handled request.
        """

        if method != self.method:
            return None
        return re.fullmatch(self.pattern, path)


__all__ = ["RouteSpec"]
