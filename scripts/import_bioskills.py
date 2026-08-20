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
import os
import re
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


# Curl flag spellings that mean "silent" without "fail on HTTP error". Matched
# on a word boundary so `ipython -m` / `curl -sS` style neighbours cannot be
# half-rewritten, and ordered longest-first so a prefix rule cannot shadow a
# longer one. `curl -s ` alone missed `curl -sSL`, the most common spelling of
# the trio, which is exactly the case the Nextflow rule below then could not
# match.
_CURL_SILENT = re.compile(r"\bcurl -(?:sSL|sL|fsS|sS|s)(?= )")
# `/` is excluded too: an explicit interpreter path (`/opt/env/bin/python -m`)
# names a binary that exists; appending a 3 to it names one that may not.
_PYTHON_CMD = re.compile(r"(?<![\w./-])python(?= -[mc] )")


def _compatibility_rewrites(text: str) -> str:
    """Apply the repository's documented command-text safety conventions.

    Deliberately narrow: this normalizes *spelling*, it does not audit the
    corpus. Plain `wget`, `pip install git+`, `install_github`, `docker run`
    and bare `python script.py` invocations survive untouched, and the
    manifest's `compatibility_rewrites` block must not be read as more than
    this.
    """

    text = _PYTHON_CMD.sub("python3", text)
    text = _CURL_SILENT.sub("curl -fsSL", text)
    return text.replace(
        "curl -fsSL https://get.nextflow.io | bash",
        "conda install -c bioconda nextflow",
    )


def _convert_document(
    raw: str, category: str, commit: str = UPSTREAM_COMMIT
) -> tuple[str, str]:
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
            f"    commit: {commit}",
            "    license: MIT",
        ]
    )
    converted = "\n".join(["---", *retained, "---", *lines[end + 1 :]]) + "\n"
    # The rewrite is NOT applied here: the tree-wide pass in
    # `import_collection` already covers every written file, including this
    # one. Applying it twice means a future non-idempotent rule would produce
    # two different conversions from one rule, with nothing to catch it.
    return name, converted


def _skill_sources(source: Path) -> list[Path]:
    # `glob("*/*/SKILL.md")` is case-insensitive on macOS and case-sensitive on
    # Linux: a mis-cased upstream `skill.md` is silently imported -- and the
    # returned Path reports its name as `SKILL.md`, so it is renamed too -- on a
    # Mac, while the same command trips the count check on CI. Matching the name
    # explicitly makes the two platforms agree on what the pin contains.
    result = []
    for category in sorted(source.iterdir()):
        if not category.is_dir() or category.name in EXCLUDED_TOP_LEVEL:
            continue
        for skill_dir in sorted(category.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_doc = skill_dir / "SKILL.md"
            if any(child.name == "SKILL.md" for child in skill_dir.iterdir()):
                result.append(skill_doc)
    return sorted(result, key=lambda path: path.relative_to(source).as_posix())


def import_collection(
    source: Path,
    destination: Path,
    *,
    expected_commit: str = UPSTREAM_COMMIT,
    expected_skills: int = EXPECTED_SKILLS,
) -> dict[str, object]:
    """Convert a pinned checkout into `destination`, or leave it untouched.

    The pins are arguments so the conversion rules can be exercised against a
    small fixture instead of only against a 561-skill refresh. Production
    callers get the module constants and nothing changes for them.
    """

    if _checkout_commit(source) != expected_commit:
        raise RuntimeError(f"source checkout must be pinned to {expected_commit}")
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"destination must be absent or empty: {destination}")

    sources = _skill_sources(source)
    if len(sources) != expected_skills:
        raise RuntimeError(
            f"expected {expected_skills} skills at pinned commit, found {len(sources)}"
        )

    # Built beside the destination and moved into place at the end. Writing in
    # place meant a failure partway through -- a malformed frontmatter, a
    # duplicate declared name, a full disk -- left a partial tree that the
    # "destination must be absent or empty" guard then refused to overwrite,
    # so the recovery for a failed import was `rm -rf` by hand.
    final = destination
    staging = destination.parent / f".{destination.name}.incoming"
    if staging.exists():
        shutil.rmtree(staging)
    destination = staging
    destination.mkdir(parents=True, exist_ok=True)
    try:
        return _convert_tree(source, destination, final, sources, expected_commit)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _convert_tree(
    source: Path,
    destination: Path,
    final: Path,
    sources: list[Path],
    commit: str,
) -> dict[str, object]:
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    skills: list[dict[str, object]] = []
    declared_names: set[str] = set()
    for source_doc in sources:
        category, local_name, _filename = source_doc.relative_to(source).parts
        directory = f"bio-{category}-{local_name}"
        target = destination / directory
        target.mkdir()

        declared_name, converted = _convert_document(
            source_doc.read_text("utf-8"), category, commit
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
            with path.open("r", encoding="utf-8", newline="") as stream:
                original = stream.read()
        except UnicodeDecodeError:
            continue
        rewritten = _compatibility_rewrites(original)
        if rewritten != original:
            # newline="" on both sides: the default read translates CRLF to LF
            # and the default write translates LF back to os.linesep, so a
            # four-token safety rewrite would silently re-line-end the whole
            # file, differently on each maintainer's platform.
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(rewritten)

    # Sorted and rendered as POSIX. `sorted()` over Path objects compares
    # `_str_normcase` (lower-cased, backslash-separated on Windows), so the
    # one artifact whose whole job is reproducible provenance would otherwise
    # be re-ordered and backslash-pathed depending on who ran the importer.
    files = [
        {
            "path": path.relative_to(destination).as_posix(),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(
            (p for p in destination.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(destination).as_posix(),
        )
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            # The commit actually converted, not the module default: a manifest
            # that records a pin it did not read is exactly the kind of
            # provenance that is wrong rather than absent.
            "commit": commit,
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
    if final.exists():
        final.rmdir()  # verified empty by the guard above
    os.replace(destination, final)
    return manifest


#: Files the importer does not write and therefore cannot hash: the manifest
#: itself, and the hand-authored bilingual boundary docs added beside it.
UNMANIFESTED = frozenset(
    {"COLLECTION.json", "MANIFEST.json", "README.md", "README_zh.md"}
)


def verify_collection(
    destination: Path,
    *,
    expected_commit: str = UPSTREAM_COMMIT,
    expected_skills: int = EXPECTED_SKILLS,
) -> list[str]:
    """Re-derive every recorded hash against the tree on disk.

    A manifest nothing rechecks is a claim about a commit, not a property of
    the checkout: `README.md` calls it "the authoritative inventory" and the
    tree is excluded from pre-commit and from the directory-README gate, so
    this is the only thing that can notice a later edit, a bad merge, or a
    platform that rewrote line endings underneath it. Returns the problems
    found, empty when the tree matches.
    """

    manifest_path = destination / "MANIFEST.json"
    if not manifest_path.is_file():
        return [f"missing manifest: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text("utf-8"))
    problems: list[str] = []

    upstream = manifest.get("upstream") or {}
    if upstream.get("commit") != expected_commit:
        problems.append(
            f"manifest pins {upstream.get('commit')!r}, importer pins "
            f"{expected_commit!r}"
        )
    if manifest.get("skill_count") != expected_skills:
        problems.append(
            f"manifest records {manifest.get('skill_count')} skills, "
            f"expected {expected_skills}"
        )

    recorded = {str(row.get("path")): row for row in (manifest.get("files") or [])}
    for path, row in sorted(recorded.items()):
        target = destination / path
        if not target.is_file():
            problems.append(f"missing payload: {path}")
            continue
        if _sha256(target) != row.get("sha256"):
            problems.append(f"payload changed since import: {path}")
        elif target.stat().st_size != row.get("size"):
            problems.append(f"payload size changed since import: {path}")

    on_disk = {
        p.relative_to(destination).as_posix()
        for p in destination.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    for extra in sorted(on_disk - set(recorded) - UNMANIFESTED):
        problems.append(f"untracked file under the pinned collection: {extra}")
    for skill in manifest.get("skills") or []:
        directory = str(skill.get("directory") or "")
        if not (destination / directory / "SKILL.md").is_file():
            problems.append(f"missing skill document: {directory}/SKILL.md")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="pinned local bioSkills checkout (not needed with --check)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("skills/bioskills"),
        help="empty destination (default: skills/bioskills)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed tree against its manifest and exit",
    )
    args = parser.parse_args()
    if args.check:
        destination = args.destination.resolve()
        problems = verify_collection(destination)
        for problem in problems:
            print(f"error: {problem}")
        if problems:
            print(f"{len(problems)} problem(s) in {args.destination}")
            return 1
        print(f"verified pinned collection at {args.destination}")
        return 0
    if args.source is None:
        parser.error("source is required unless --check is given")
    manifest = import_collection(args.source.resolve(), args.destination.resolve())
    print(
        f"imported {manifest['skill_count']} skills from "
        f"{UPSTREAM_COMMIT} into {args.destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
