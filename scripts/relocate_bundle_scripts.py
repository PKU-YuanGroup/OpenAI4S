#!/usr/bin/env python3
"""Make pip-generated Python commands use their adjacent bundled interpreter."""

from __future__ import annotations

import argparse
from pathlib import Path

HEADER = b"#!/bin/sh\n" b'\'\'\'exec\' "$(dirname "$0")/python3" "$0" "$@"\n' b"' '''\n"


def python_body(content: bytes) -> bytes | None:
    first, _, body = content.partition(b"\n")
    if first.startswith(b"#!") and b"python" in first:
        return body
    if first == b"#!/bin/sh" and body.startswith(b"'''exec'"):
        _, separator, tail = body.partition(b"\n' '''\n")
        if separator:
            return tail
    return None


def relocate(runtime: Path) -> int:
    count = 0
    for path in (runtime / "bin").iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as source:
            if source.read(2) != b"#!":
                continue
            source.seek(0)
            content = source.read()
        body = python_body(content)
        if body is not None:
            path.write_bytes(HEADER + body)
            count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    print(f"Relocated {relocate(args.runtime)} bundled Python commands")
