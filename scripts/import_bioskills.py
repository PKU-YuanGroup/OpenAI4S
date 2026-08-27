#!/usr/bin/env python3
"""Vendor the pinned GPTomics/bioSkills release into the bundled catalog.

The importer intentionally accepts a local checkout only. Fetching is a
maintainer decision outside this script; conversion is deterministic and
offline once the audited checkout is present.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath

UPSTREAM_REPOSITORY = "https://github.com/GPTomics/bioSkills"
UPSTREAM_COMMIT = "d91ed3d563019e649dc854c56ccd62551359488a"
EXPECTED_SKILLS = 561
EXCLUDED_TOP_LEVEL = frozenset({"clawhub-installer"})


def _git_environment() -> dict[str, str]:
    """Disable object overlays and implicit network fetches for pinned reads."""

    environment = dict(os.environ)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


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
        env=_git_environment(),
    )
    return result.stdout.strip()


def _validate_checkout(source: Path, expected_commit: str) -> None:
    """Require an exact, clean repository root at ``expected_commit``.

    A matching ``HEAD`` alone does not pin any bytes: tracked files can be
    modified and untracked files can be copied from ``examples/`` while the
    manifest still claims the committed revision.  The conversion below reads
    blobs from the Git object database, but refusing a dirty checkout as well
    keeps an accidental local edit from being silently ignored during a
    maintainer refresh.
    """

    if _checkout_commit(source) != expected_commit:
        raise RuntimeError(f"source checkout must be pinned to {expected_commit}")
    top_level = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
        env=_git_environment(),
    ).stdout.strip()
    if Path(top_level).resolve() != source.resolve():
        raise RuntimeError(f"source must be the checkout root: {source}")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ],
        check=True,
        capture_output=True,
        env=_git_environment(),
    ).stdout
    if status:
        raise RuntimeError("source checkout must be clean (including untracked files)")
    replace_refs = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "for-each-ref",
            "--format=%(refname)",
            "refs/replace",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_git_environment(),
    ).stdout.strip()
    if replace_refs:
        raise RuntimeError("source checkout must not contain Git replace refs")
    grafts_name = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--git-path", "info/grafts"],
        check=True,
        capture_output=True,
        text=True,
        env=_git_environment(),
    ).stdout.strip()
    grafts = Path(grafts_name)
    if not grafts.is_absolute():
        grafts = source / grafts
    if grafts.is_file() and grafts.read_bytes().strip():
        raise RuntimeError("source checkout must not contain Git grafts")


def _selected_tree_entries(source: Path, commit: str) -> list[tuple[str, str, str]]:
    """Return ``(mode, object id, POSIX path)`` for imported Git blobs."""

    result = subprocess.run(
        ["git", "-C", str(source), "ls-tree", "-r", "-z", "--full-tree", commit],
        check=True,
        capture_output=True,
        env=_git_environment(),
    )
    selected: list[tuple[str, str, str]] = []
    casefolded: dict[str, str] = {}
    casefolded_prefixes: dict[str, str] = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        header, separator, encoded_path = raw.partition(b"\t")
        if not separator:
            raise RuntimeError("pinned Git tree contains a malformed entry")
        try:
            mode, kind, object_id = header.decode("ascii").split()
            relative = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise RuntimeError(
                "pinned Git tree contains an unsupported entry"
            ) from error
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative:
            raise RuntimeError(f"pinned Git tree contains an unsafe path: {relative!r}")
        parts = path.parts
        imported = relative == "LICENSE" or (
            len(parts) >= 3
            and parts[0] not in EXCLUDED_TOP_LEVEL
            and (
                (len(parts) == 3 and parts[2] in {"SKILL.md", "usage-guide.md"})
                or (len(parts) >= 4 and parts[2] == "examples")
            )
        )
        if not imported:
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError(
                f"imported upstream path must be a regular file: {relative}"
            )
        identity = unicodedata.normalize("NFKC", relative).casefold()
        collision = casefolded.get(identity)
        if collision is not None and collision != relative:
            raise RuntimeError(
                "pinned Git tree contains paths that collide on Windows/macOS: "
                f"{collision!r}, {relative!r}"
            )
        casefolded[identity] = relative
        for index in range(1, len(parts) + 1):
            prefix_parts = parts[:index]
            prefix = "/".join(prefix_parts)
            prefix_identity = "/".join(
                unicodedata.normalize("NFKC", part).casefold() for part in prefix_parts
            )
            previous_prefix = casefolded_prefixes.get(prefix_identity)
            if previous_prefix is not None and previous_prefix != prefix:
                raise RuntimeError(
                    "pinned Git tree contains path components that collide on "
                    f"Windows/macOS: {previous_prefix!r}, {prefix!r}"
                )
            casefolded_prefixes[prefix_identity] = prefix
        selected.append((mode, object_id, relative))
    if not any(relative == "LICENSE" for _mode, _object_id, relative in selected):
        raise RuntimeError("pinned Git tree has no LICENSE")
    return sorted(selected, key=lambda row: row[2])


def _materialize_pinned_tree(source: Path, commit: str, destination: Path) -> None:
    """Write the exact committed blobs used by the conversion.

    Reading the object database, rather than checkout files, also makes output
    independent of ``core.autocrlf`` and of filesystem symlink behaviour.
    ``git cat-file --batch`` keeps the refresh to one subprocess instead of one
    process per upstream asset.
    """

    entries = _selected_tree_entries(source, commit)
    requests = b"".join(f"{object_id}\n".encode("ascii") for _, object_id, _ in entries)
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "cat-file", "--batch"],
            input=requests,
            check=True,
            capture_output=True,
            env=_git_environment(),
        )
    except subprocess.CalledProcessError as error:
        # Git 2.43+ exits 128 when a partial clone has an unavailable promisor
        # remote, instead of returning a per-object ``missing`` batch header.
        # Keep that version-dependent transport detail behind the importer's
        # stable fail-closed contract, and never create the destination tree.
        raise RuntimeError("cannot read pinned Git blob stream") from error
    stream = io.BytesIO(result.stdout)
    destination.mkdir(parents=True)
    for mode, expected_object, relative in entries:
        header = stream.readline().rstrip(b"\n").split()
        if len(header) != 3:
            raise RuntimeError(f"cannot read pinned Git blob for {relative}")
        object_id, kind, raw_size = header
        try:
            size = int(raw_size)
        except ValueError as error:
            raise RuntimeError(
                f"invalid pinned Git blob size for {relative}"
            ) from error
        if object_id.decode("ascii") != expected_object or kind != b"blob":
            raise RuntimeError(f"unexpected pinned Git object for {relative}")
        payload = stream.read(size)
        if len(payload) != size or stream.read(1) != b"\n":
            raise RuntimeError(f"truncated pinned Git blob for {relative}")
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(0o755 if mode == "100755" else 0o644)
    if stream.read(1):
        raise RuntimeError("pinned Git blob stream contains unexpected data")


def _frontmatter_value(lines: list[str], key: str) -> str:
    for index, line in enumerate(lines):
        if (
            not line
            or line[0] in {" ", "\t", "#", "-"}
            or ":" not in line
            or line.partition(":")[0].strip().lower() != key.lower()
        ):
            continue
        value = line.partition(":")[2].strip()
        if value and value[0] in "|>" and value[1:] in {"", "-", "+"}:
            folded = value[0] == ">"
            block: list[str] = []
            for continuation in lines[index + 1 :]:
                if continuation and continuation[0] not in {" ", "\t"}:
                    break
                block.append(continuation)
            indents = [
                len(item) - len(item.lstrip(" \t")) for item in block if item.strip()
            ]
            pad = min(indents) if indents else 0
            dedented = [item[pad:] if item.strip() else "" for item in block]
            separator = " " if folded else "\n"
            return separator.join(
                item.strip() if folded else item for item in dedented
            ).strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            return value[1:-1]
        return value.split(" #", 1)[0].strip()
    return ""


def _without_top_level_frontmatter_fields(
    lines: list[str], fields: frozenset[str]
) -> list[str]:
    """Remove selected top-level scalars with the runtime parser's boundaries.

    A block scalar is its header *and* every following blank/indented line.
    Removing only the header leaves those continuation lines attached to the
    preceding retained field, which can silently change (for example) a
    folded ``description`` when the converted document is loaded.  Nested
    keys with the same spelling are deliberately preserved.
    """

    targets = {field.lower() for field in fields}
    retained: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        key = None
        if line and line[0] not in {" ", "\t", "#", "-"} and ":" in line:
            key = line.partition(":")[0].strip().lower()
        if key not in targets:
            retained.append(line)
            index += 1
            continue

        marker = line.partition(":")[2].strip()
        index += 1
        if marker and marker[0] in "|>" and marker[1:] in {"", "-", "+"}:
            while index < len(lines) and (
                not lines[index] or lines[index][0] in {" ", "\t"}
            ):
                index += 1
    return retained


def _nested_metadata_scalar_lines(key: str, value: str) -> list[str]:
    """Render a converted metadata scalar without leaking lines to top level."""

    if "\n" not in value:
        return [f"  {key}: {value}"]
    return [f"  {key}: |-", *(f"    {line}" for line in value.split("\n"))]


def _canonical_skill_name(value: str) -> str:
    """Match the runtime loader's declared-name collision identity."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


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
    retained = _without_top_level_frontmatter_fields(
        frontmatter, frozenset({"tool_type", "primary_tool"})
    )
    retained.extend(
        [
            "origin: openai4s",
            f"category: bioskills/{category}",
            "metadata:",
            *_nested_metadata_scalar_lines("tool_type", tool_type),
            *_nested_metadata_scalar_lines("primary_tool", primary_tool),
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

    _validate_checkout(source, expected_commit)
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"destination must be absent or empty: {destination}")

    # Built beside the destination and moved into place at the end. Writing in
    # place meant a failure partway through -- a malformed frontmatter, a
    # duplicate declared name, a full disk -- left a partial tree that the
    # "destination must be absent or empty" guard then refused to overwrite,
    # so the recovery for a failed import was `rm -rf` by hand.
    final = destination
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openai4s-bioskills-source-") as temporary:
        pinned_source = Path(temporary) / "tree"
        _materialize_pinned_tree(source, expected_commit, pinned_source)
        sources = _skill_sources(pinned_source)
        if len(sources) != expected_skills:
            raise RuntimeError(
                f"expected {expected_skills} skills at pinned commit, "
                f"found {len(sources)}"
            )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{final.name}.incoming-", dir=final.parent)
        )
        # mkdtemp deliberately creates 0700 directories.  The atomic rename
        # preserves that mode, while Git records no directory modes, so a
        # successful maintainer refresh would otherwise leave this shipped
        # data tree unreadable to other local users even though an ordinary
        # checkout is traversable.  Set the final intended mode before the
        # rename; files keep their independently assigned 0644/0755 modes.
        staging.chmod(0o755)
        try:
            return _convert_tree(
                pinned_source, staging, final, sources, expected_commit
            )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
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
    declared_names: dict[str, tuple[str, str]] = {}
    directory_identities: dict[str, tuple[str, str]] = {}
    reserved_identities: dict[str, tuple[str, str]] = {}
    for kind, spelling in (
        ("catalog namespace", final.parent.resolve().name),
        ("collection root", final.name),
    ):
        identity = _canonical_skill_name(spelling)
        previous = reserved_identities.get(identity)
        if previous is not None:
            raise RuntimeError(
                f"destination {kind} identity {identity!r} collides with "
                f"{previous[0]} {previous[1]!r}"
            )
        reserved_identities[identity] = (kind, spelling)
    for source_doc in sources:
        category, local_name, _filename = source_doc.relative_to(source).parts
        directory = f"bio-{category}-{local_name}"
        identity = _canonical_skill_name(directory)
        relative = source_doc.relative_to(source).as_posix()
        reserved = reserved_identities.get(identity)
        if reserved is not None:
            raise RuntimeError(
                "generated skill directory identity collides with reserved "
                f"{reserved[0]} identity {identity!r}: {relative} "
                f"({directory!r}) and {reserved[1]!r}"
            )
        previous = directory_identities.get(identity)
        if previous is not None:
            raise RuntimeError(
                "duplicate generated skill directory identity "
                f"{identity!r}: {previous[1]} ({previous[0]!r}) and "
                f"{relative} ({directory!r})"
            )
        directory_identities[identity] = (directory, relative)
    for source_doc in sources:
        category, local_name, _filename = source_doc.relative_to(source).parts
        directory = f"bio-{category}-{local_name}"
        target = destination / directory
        target.mkdir()

        declared_name, converted = _convert_document(
            source_doc.read_text("utf-8"), category, commit
        )
        canonical_name = _canonical_skill_name(declared_name)
        reserved = reserved_identities.get(canonical_name)
        if reserved is not None:
            raise RuntimeError(
                "declared skill name identity collides with reserved "
                f"{reserved[0]} identity {canonical_name!r}: "
                f"{source_doc.relative_to(source).as_posix()} "
                f"({declared_name!r}) and {reserved[1]!r}"
            )
        previous = declared_names.get(canonical_name)
        if previous is not None:
            previous_name, previous_path = previous
            raise RuntimeError(
                "duplicate declared skill name identity "
                f"{canonical_name!r}: {previous_path} ({previous_name!r}) and "
                f"{source_doc.relative_to(source).as_posix()} ({declared_name!r})"
            )
        directory_owner = directory_identities.get(canonical_name)
        relative = source_doc.relative_to(source).as_posix()
        if directory_owner is not None and directory_owner[1] != relative:
            raise RuntimeError(
                "declared skill name collides with generated directory identity "
                f"{canonical_name!r}: {relative} ({declared_name!r}) and "
                f"{directory_owner[1]} ({directory_owner[0]!r})"
            )
        declared_names[canonical_name] = (
            declared_name,
            relative,
        )
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
                "upstream_path": source_doc.parent.relative_to(source).as_posix(),
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
