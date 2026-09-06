#!/usr/bin/env python3
"""Fail if a stylesheet's ``var(--x)`` set is not a subset of its ``--x:`` set.

F-21: ``var(--x)`` 引用集 − 定义集 = ∅. A custom-property *declaration* is
``--name:`` at property position (including ``.step-search{--k:…}``). A
*reference* is the first argument of each ``var(--name)`` / ``var(--name,
fallback)``. Nested ``var()`` still counts — ``var(--a, var(--b))``
references both ``--a`` and ``--b``. A fallback does not declare the name.

The F-21 audit's 40 were the hard ``--text-100`` (7) + ``--text-300`` (33)
holes: ``var(--text-300)`` used only as the nested fallback of
``var(--text-200, var(--text-300))`` is excluded from that subset. This
checker still reports every undefined name so a later alias cannot hide.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSS = ROOT / "openai4s" / "server/webui/style.css"

# ``--name:`` at property position. The preceding char must not be a
# custom-property name char, so ``--k-search:`` is not also ``--k:``.
_DECL = re.compile(r"(?:^|[^A-Za-z0-9_-])(--[A-Za-z0-9-]+)\s*:")

# F-21 audit subset. Nested-fallback uses of these names are excluded from
# the subset count so an unfixed tree still prints "40".
_AUDIT_SUBSET = frozenset({"--text-100", "--text-300"})


@dataclass(frozen=True)
class Ref:
    name: str
    line: int
    has_fallback: bool
    nested_fallback: bool


def _strip_comments(src: str) -> str:
    """Blank comment bodies but keep newlines so reported line numbers match."""

    def repl(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return re.sub(r"/\*.*?\*/", repl, src, flags=re.S)


def _line_at(src: str, index: int) -> int:
    return src.count("\n", 0, index) + 1


def declarations(src: str) -> set[str]:
    """Custom-property names declared in ``src`` (comments already stripped)."""
    return {m.group(1) for m in _DECL.finditer(src)}


def _var_calls(src: str) -> list[tuple[int, str, bool]]:
    """``(start_index, inner, nested_fallback)`` for every ``var(…)`` call."""
    found: list[tuple[int, str, bool]] = []
    i = 0
    n = len(src)
    while True:
        j = src.find("var(", i)
        if j < 0:
            break
        depth = 1
        k = j + 4
        while k < n and depth:
            ch = src[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            k += 1
        inner = src[j + 4 : k - 1]
        # A call is a nested fallback when the nearest non-space char before
        # ``var(`` is a comma inside an outer ``var(``.
        p = j - 1
        while p >= 0 and src[p] in " \t\n\r":
            p -= 1
        nested = p >= 0 and src[p] == ","
        found.append((j, inner, nested))
        i = j + 4
    return found


def references(src: str) -> list[Ref]:
    """Every ``var(--name)`` primary argument in ``src``."""
    out: list[Ref] = []
    for start, inner, nested in _var_calls(src):
        stripped = inner.strip()
        m = re.match(r"(--[A-Za-z0-9-]+)", stripped)
        if not m:
            continue
        rest = stripped[m.end() :].lstrip()
        out.append(
            Ref(
                name=m.group(1),
                line=_line_at(src, start),
                has_fallback=rest.startswith(","),
                nested_fallback=nested,
            )
        )
    return out


def undefined(src: str) -> list[Ref]:
    defined = declarations(src)
    return [ref for ref in references(src) if ref.name not in defined]


def audit_subset(missing: list[Ref]) -> list[Ref]:
    """The 40-count F-21 subset: hard ``--text-100`` / ``--text-300`` refs."""
    return [
        ref for ref in missing if ref.name in _AUDIT_SUBSET and not ref.nested_fallback
    ]


def format_report(path: Path, missing: list[Ref]) -> str:
    counts = Counter(ref.name for ref in missing)
    subset = audit_subset(missing)
    lines = [
        f"{path}: {len(missing)} undefined var() reference(s), "
        f"{len(counts)} name(s)",
        f"F-21 audit subset (hard --text-100/--text-300): {len(subset)}",
        "",
    ]
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {name}: {n}")
    lines.append("")
    for ref in sorted(missing, key=lambda r: (r.line, r.name)):
        flag = []
        if ref.has_fallback:
            flag.append("fallback")
        if ref.nested_fallback:
            flag.append("nested-fallback")
        suffix = f" ({', '.join(flag)})" if flag else ""
        lines.append(f"  {path}:{ref.line}: {ref.name}{suffix}")
    return "\n".join(lines) + "\n"


def check_file(path: Path) -> int:
    raw = path.read_text(encoding="utf-8")
    src = _strip_comments(raw)
    missing = undefined(src)
    if not missing:
        print(f"{path}: ok (var(--x) ⊆ --x:)")
        return 0
    sys.stderr.write(format_report(path, missing))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "css",
        nargs="*",
        type=Path,
        default=[DEFAULT_CSS],
        help="Stylesheet(s) to check (default: openai4s/server/webui/style.css)",
    )
    args = parser.parse_args(argv)
    status = 0
    for path in args.css:
        if not path.is_file():
            sys.stderr.write(f"{path}: not a file\n")
            status = 1
            continue
        status |= check_file(path)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
