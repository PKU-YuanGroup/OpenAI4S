#!/usr/bin/env python3
"""Vendor the pinned GPTomics/bioSkills release into the bundled catalog.

The importer intentionally accepts a local checkout only. Fetching is a
maintainer decision outside this script; conversion is deterministic and
offline once the audited checkout is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

UPSTREAM_REPOSITORY = "https://github.com/GPTomics/bioSkills"
UPSTREAM_COMMIT = "d91ed3d563019e649dc854c56ccd62551359488a"
EXPECTED_SKILLS = 561
EXCLUDED_TOP_LEVEL = frozenset({"clawhub-installer"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkout_commit(source: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _frontmatter_value(lines: list[str], key: str) -> str:
    prefix = f"{key}:"
    for line in lines:
        if line.startswith(prefix):
            return line.partition(":")[2].strip()
    return ""


def _compatibility_rewrites(text: str) -> str:
    """Apply the repository's documented command-text safety conventions."""

    text = text.replace("python -m ", "python3 -m ")
    text = text.replace("python -c ", "python3 -c ")
    text = text.replace("curl -sL ", "curl -fsSL ")
    text = text.replace("curl -s ", "curl -fsSL ")
    return text.replace(
        "curl -fsSL https://get.nextflow.io | bash",
        "conda install -c bioconda nextflow",
    )


def _convert_document(raw: str, category: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError("SKILL.md has no leading frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("SKILL.md has unterminated frontmatter") from exc

    frontmatter = lines[1:end]
    name = _frontmatter_value(frontmatter, "name")
    if not name:
        raise ValueError("SKILL.md frontmatter has no name")
    tool_type = _frontmatter_value(frontmatter, "tool_type")
    primary_tool = _frontmatter_value(frontmatter, "primary_tool")
    retained = [
        line
        for line in frontmatter
        if not line.startswith(("tool_type:", "primary_tool:"))
    ]
    retained.extend(
        [
            "origin: openai4s",
            f"category: bioskills/{category}",
            "metadata:",
            f"  tool_type: {tool_type}",
            f"  primary_tool: {primary_tool}",
            "  third_party:",
            "    name: GPTomics/bioSkills",
            f"    repository: {UPSTREAM_REPOSITORY}",
            f"    commit: {UPSTREAM_COMMIT}",
            "    license: MIT",
        ]
    )
    converted = "\n".join(["---", *retained, "---", *lines[end + 1 :]]) + "\n"
    # Keep imported guidance compatible with this repository's relay filter,
    # and do not preserve an unauthenticated download-to-shell installer when
    # the same tool is available from the already documented conda channel.
    converted = _compatibility_rewrites(converted)
    return name, converted


def _skill_sources(source: Path) -> list[Path]:
    result = []
    for skill_doc in source.glob("*/*/SKILL.md"):
        if skill_doc.relative_to(source).parts[0] not in EXCLUDED_TOP_LEVEL:
            result.append(skill_doc)
    return sorted(result)


def import_collection(source: Path, destination: Path) -> dict[str, object]:
    if _checkout_commit(source) != UPSTREAM_COMMIT:
        raise RuntimeError(f"source checkout must be pinned to {UPSTREAM_COMMIT}")
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"destination must be absent or empty: {destination}")

    sources = _skill_sources(source)
    if len(sources) != EXPECTED_SKILLS:
        raise RuntimeError(
            f"expected {EXPECTED_SKILLS} skills at pinned commit, found {len(sources)}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    skills: list[dict[str, object]] = []
    declared_names: set[str] = set()
    for source_doc in sources:
        category, local_name, _filename = source_doc.relative_to(source).parts
        directory = f"bio-{category}-{local_name}"
        target = destination / directory
        target.mkdir()

        declared_name, converted = _convert_document(
            source_doc.read_text("utf-8"), category
        )
        if declared_name in declared_names:
            raise RuntimeError(f"duplicate declared skill name: {declared_name}")
        declared_names.add(declared_name)
        (target / "SKILL.md").write_text(converted, encoding="utf-8")

        examples = source_doc.parent / "examples"
        if examples.is_dir():
            shutil.copytree(
                examples,
                target / "scripts",
                ignore=shutil.ignore_patterns("*.pyc", "__pycache__"),
            )
        usage = source_doc.parent / "usage-guide.md"
        if usage.is_file():
            references = target / "references"
            references.mkdir()
            shutil.copy2(usage, references / "usage-guide.md")
        skills.append(
            {
                "category": category,
                "directory": directory,
                "name": declared_name,
                "upstream_path": str(source_doc.parent.relative_to(source)),
            }
        )

    # Examples and usage guides are upstream text assets too. Apply the same
    # narrow relay/shell-safety rewrite without assuming every future asset is
    # UTF-8 (binary fixtures, if added upstream, remain byte-identical).
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        try:
            original = path.read_text("utf-8")
        except UnicodeDecodeError:
            continue
        rewritten = _compatibility_rewrites(original)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8")

    files = [
        {
            "path": str(path.relative_to(destination)),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "license": "MIT",
            "archived": True,
        },
        "conversion": {
            "directory_name": "bio-<category>-<upstream-directory>",
            "examples": "scripts/",
            "usage-guide.md": "references/usage-guide.md",
            "tool_type_and_primary_tool": "metadata",
            "openai4s_origin_and_provenance": "frontmatter",
            "compatibility_rewrites": [
                "python module/command snippets use python3",
                "silent curl snippets use fail-fast flags",
                "Nextflow install-from-pipe snippets use the bioconda package",
            ],
        },
        "skill_count": len(skills),
        "skills": skills,
        "files": files,
    }
    (destination / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pinned local bioSkills checkout")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("skills/bioskills"),
        help="empty destination (default: skills/bioskills)",
    )
    args = parser.parse_args()
    manifest = import_collection(args.source.resolve(), args.destination.resolve())
    print(
        f"imported {manifest['skill_count']} skills from "
        f"{UPSTREAM_COMMIT} into {args.destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
