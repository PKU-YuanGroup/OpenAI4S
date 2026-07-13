#!/usr/bin/env python3
"""Fail closed unless a release tag matches every package version declaration."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

_TAG = re.compile(r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
_PROJECT_VERSION = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"\s*$')


class ReleaseTagError(RuntimeError):
    pass


def _pyproject_version(path: Path) -> str:
    in_project = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project:
            match = _PROJECT_VERSION.fullmatch(line)
            if match:
                return match.group("version")
    raise ReleaseTagError("pyproject.toml has no literal [project] version")


def _package_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "__version__":
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return node.value.value
            break
    raise ReleaseTagError("openai4s.__version__ must be a literal string")


def verify(root: Path, tag: str) -> str:
    match = _TAG.fullmatch(tag.strip())
    if match is None:
        raise ReleaseTagError("release tag must use vMAJOR.MINOR.PATCH")
    tagged_version = match.group("version")
    declared = {
        "pyproject.toml": _pyproject_version(root / "pyproject.toml"),
        "openai4s/__init__.py": _package_version(root / "openai4s" / "__init__.py"),
    }
    mismatches = [
        f"{path}={version}"
        for path, version in declared.items()
        if version != tagged_version
    ]
    if mismatches:
        raise ReleaseTagError(f"tag {tag!r} does not match " + ", ".join(mismatches))
    return tagged_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    try:
        version = verify(args.root.resolve(), args.tag)
    except (OSError, SyntaxError, ReleaseTagError) as error:
        print(f"release tag verification failed: {error}", file=sys.stderr)
        return 1
    print(f"release tag verified: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
