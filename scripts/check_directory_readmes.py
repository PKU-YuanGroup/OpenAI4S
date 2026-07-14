#!/usr/bin/env python3
"""Check bilingual per-directory README coverage for the maintained source tree."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

# These trees are third-party, generated, or byte-exact fixtures. Their owning
# parent README must describe the boundary, but documentation files must not be
# injected into the trees themselves.
EXCLUDED_PREFIXES = (
    PurePosixPath("openai4s/server/webui/vendor"),
    PurePosixPath("tests/fixtures"),
)
EXCLUDED_PARTS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".build"}
)
README_NAMES = frozenset({"README.md", "README_zh.md"})


def _excluded(path: PurePosixPath) -> bool:
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return True
    return any(path == prefix or prefix in path.parents for prefix in EXCLUDED_PREFIXES)


def _source_files() -> set[PurePosixPath]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    files: set[PurePosixPath] = set()
    for raw in result.stdout.decode("utf-8").split("\0"):
        if not raw:
            continue
        path = PurePosixPath(raw)
        if not _excluded(path):
            files.add(path)
    return files


def _source_directories(files: set[PurePosixPath]) -> set[PurePosixPath]:
    directories: set[PurePosixPath] = set()
    for path in files:
        parent = path.parent
        while parent != PurePosixPath("."):
            if not _excluded(parent):
                directories.add(parent)
            parent = parent.parent
    return directories


def _structure(text: str) -> tuple[list[str], int]:
    headings = re.findall(r"^(#{1,6})\s+", text, flags=re.MULTILINE)
    table_rows = sum(
        1 for line in text.splitlines() if line.startswith("|") and line.endswith("|")
    )
    return headings, table_rows


def _relative_links(text: str) -> list[str]:
    """Return local Markdown link destinations, excluding URLs and anchors."""
    links: list[str] = []
    for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", text):
        target = match.group(1).strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if target:
            links.append(target)
    return links


def main() -> int:
    files = _source_files()
    directories = _source_directories(files)
    errors: list[str] = []

    for directory in sorted(directories, key=str):
        local_dir = ROOT / directory
        english_path = local_dir / "README.md"
        chinese_path = local_dir / "README_zh.md"
        if not english_path.is_file():
            errors.append(f"missing {directory}/README.md")
            continue
        if not chinese_path.is_file():
            errors.append(f"missing {directory}/README_zh.md")
            continue

        english = english_path.read_text(encoding="utf-8")
        chinese = chinese_path.read_text(encoding="utf-8")
        if _structure(english) != _structure(chinese):
            errors.append(f"bilingual structure mismatch: {directory}")

        for readme_path, text in (
            (english_path, english),
            (chinese_path, chinese),
        ):
            for target in _relative_links(text):
                if not (readme_path.parent / target).resolve().exists():
                    errors.append(
                        f"broken relative link in {readme_path.relative_to(ROOT)}: "
                        f"{target}"
                    )

        direct_files = sorted(
            path.name
            for path in files
            if path.parent == directory and path.name not in README_NAMES
        )
        for name in direct_files:
            marker = f"`{name}`"
            if marker not in english:
                errors.append(f"{directory}/README.md does not mention {marker}")
            if marker not in chinese:
                errors.append(f"{directory}/README_zh.md does not mention {marker}")

        children = sorted(
            child.name for child in directories if child.parent == directory
        )
        for name in children:
            marker = f"`{name}/`"
            if marker not in english:
                errors.append(f"{directory}/README.md does not mention {marker}")
            if marker not in chinese:
                errors.append(f"{directory}/README_zh.md does not mention {marker}")

    if errors:
        for error in errors:
            print(f"directory docs: {error}", file=sys.stderr)
        return 1

    documented_files = {
        path
        for path in files
        if path.parent in directories and path.name not in README_NAMES
    }
    print(
        f"directory docs: {len(directories)} maintained directories, "
        f"{len(documented_files)} direct files/assets, complete bilingual coverage"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
