#!/usr/bin/env python3
"""Validate OpenAI4S wheel/sdist contents using only the standard library."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import stat
import sys
import tarfile
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

_WHEEL_REQUIRED = frozenset(
    {
        "openai4s/__init__.py",
        "openai4s/cli/main.py",
        "openai4s/kernel/r_worker.R",
        "openai4s/compute/templates/run.sh.tmpl",
        "openai4s/compute/templates/wrapper.sh.tmpl",
        "openai4s/server/webui/index.html",
        "openai4s/server/webui/theme-bootstrap.js",
        "openai4s/server/webui/app.js",
        "openai4s/server/webui/style.css",
        "openai4s/server/webui/vendor/3Dmol-min.js",
        "openai4s_compute_provider/__init__.py",
        "openai4s_worker_runtime/__init__.py",
        "envs/python.yml",
        "envs/phylo.yml",
        "envs/r.yml",
        "envs/struct.yml",
        "skills/example_stats/SKILL.md",
        "skills/example_stats/kernel.py",
        "skills/bioskills/COLLECTION.json",
        "skills/bioskills/LICENSE",
        "skills/bioskills/MANIFEST.json",
        "skills/bioskills/README.md",
        "skills/bioskills/README_zh.md",
        "skills/bioskills/bio-structural-biology-structure-validation/SKILL.md",
        "skills/remote-compute-nvidia/provider.json",
        "skills/remote-compute-nvidia/provider.py",
    }
)

_COLLECTION_BOUNDARY_FILES = frozenset(
    {"COLLECTION.json", "MANIFEST.json", "README.md", "README_zh.md"}
)
REQUIRED_COLLECTIONS: dict[str, dict[str, str | int]] = {
    "skills/bioskills": {
        "id": "bioskills",
        "skill_count": 561,
        "upstream_commit": "d91ed3d563019e649dc854c56ccd62551359488a",
        "manifest_sha256": "e1747551da95e9320368d4d4f7002d3b9708a808d0b9b0f117e36ed66968530b",
    }
}
_SDIST_REQUIRED = frozenset(
    {
        "CODE_OF_CONDUCT.md",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "SECURITY.md",
        "docs/release-validation.md",
        "pyproject.toml",
        "scripts/import_bioskills.py",
        "scripts/release_import_smoke.py",
        "scripts/setup_envs.sh",
        "scripts/source_secret_scan.py",
        "scripts/verify_release_artifacts.py",
        "scripts/verify_release_tag.py",
        *_WHEEL_REQUIRED,
    }
)


class ReleaseCheckError(RuntimeError):
    pass


_RUNTIME_SENTINELS = {
    unicodedata.normalize("NFKC", name).casefold(): name
    for name in ("SKILL.md", "kernel.py", "COLLECTION.json", "MANIFEST.json")
}


def _safe_names(
    names: list[str], *, archive: str, directories: set[str] | None = None
) -> set[str]:
    normalized: set[str] = set()
    identities: dict[str, str] = {}
    prefix_identities: dict[str, str] = {}
    proper_prefix_identities: set[str] = set()
    directory_names = {name.rstrip("/") for name in directories or set()}
    for raw in names:
        value = raw.rstrip("/")
        if not value:
            raise ReleaseCheckError(f"{archive} contains an empty archive path")
        path = PurePosixPath(value)
        if (
            not path.parts
            or value == "."
            or path.is_absolute()
            or "\\" in value
            or ".." in path.parts
        ):
            raise ReleaseCheckError(f"{archive} contains unsafe path: {raw!r}")
        canonical = path.as_posix()
        if canonical != value:
            raise ReleaseCheckError(f"{archive} contains a non-canonical path: {raw!r}")
        identity = unicodedata.normalize("NFKC", canonical).casefold()
        previous = identities.get(identity)
        if previous is not None:
            raise ReleaseCheckError(
                f"{archive} contains colliding paths: {previous!r}, {raw!r}"
            )
        identities[identity] = raw
        for part in path.parts:
            sentinel = _RUNTIME_SENTINELS.get(
                unicodedata.normalize("NFKC", part).casefold()
            )
            if sentinel is not None and part != sentinel:
                raise ReleaseCheckError(
                    f"{archive} contains mis-cased runtime sentinel "
                    f"{part!r}; expected {sentinel!r}"
                )
        for index in range(1, len(path.parts) + 1):
            prefix_parts = path.parts[:index]
            prefix = "/".join(prefix_parts)
            prefix_identity = "/".join(
                unicodedata.normalize("NFKC", part).casefold() for part in prefix_parts
            )
            previous_prefix = prefix_identities.get(prefix_identity)
            if previous_prefix is not None and previous_prefix != prefix:
                raise ReleaseCheckError(
                    f"{archive} contains colliding path components: "
                    f"{previous_prefix!r}, {prefix!r}"
                )
            prefix_identities[prefix_identity] = prefix
            if index < len(path.parts):
                proper_prefix_identities.add(prefix_identity)
        lowered = {part.casefold() for part in path.parts}
        if ".git" in lowered or "__pycache__" in lowered:
            raise ReleaseCheckError(
                f"{archive} contains source-control/cache data: {raw}"
            )
        if path.suffix.casefold() in {".pyc", ".pyo"}:
            raise ReleaseCheckError(f"{archive} contains bytecode: {raw}")
        if path.name.casefold() == ".env" or path.name.casefold().startswith(".env."):
            raise ReleaseCheckError(f"{archive} contains environment secrets: {raw}")
        if value in normalized:
            raise ReleaseCheckError(f"{archive} contains a duplicate path: {raw}")
        normalized.add(value)
    for value in sorted(normalized - directory_names):
        identity = unicodedata.normalize("NFKC", value).casefold()
        if identity in proper_prefix_identities:
            raise ReleaseCheckError(
                f"{archive} contains regular file used as a directory prefix: "
                f"{value!r}"
            )
    return normalized


def _canonical_skill_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


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


def _skill_document_name(payload: bytes, *, path: str) -> str:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseCheckError(f"collection skill is not UTF-8: {path}") from error
    if not lines or lines[0] != "---":
        raise ReleaseCheckError(f"collection skill has no frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ReleaseCheckError(
            f"collection skill has unterminated frontmatter: {path}"
        ) from error
    value = _frontmatter_value(lines[1:end], "name")
    if value:
        return value
    raise ReleaseCheckError(f"collection skill has no declared name: {path}")


def _collection_relative_path(value: object, *, root: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseCheckError(f"collection {root} has an invalid {field}")
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or ".." in path.parts:
        raise ReleaseCheckError(f"collection {root} has an unsafe {field}: {value!r}")
    return path.as_posix()


def _catalog_document_name(payload: bytes, *, path: str, fallback: str) -> str:
    """Read a bundled Skill's public name with the runtime loader's fallback."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseCheckError(f"bundled skill is not UTF-8: {path}") from error
    if not text.startswith("---"):
        return fallback
    end = text.find("\n---", 3)
    if end == -1:
        return fallback
    value = _frontmatter_value(text[3:end].strip("\n").splitlines(), "name")
    return value or fallback


def _verify_catalog_identities(
    file_names: set[str],
    read: "Callable[[str], bytes]",
    collection_skills: dict[str, dict[str, str]],
) -> None:
    """Match the runtime loader's catalog-wide collision boundary.

    Directory names are import identities while frontmatter names are public
    retrieval identities.  Both have to share one canonical registry across
    curated Skills and every collection; checking each manifest independently
    leaves cross-collection and collection-vs-curated shadows undetected.
    """

    claimed: dict[str, tuple[str, str, str, str]] = {}

    def claim(*, kind: str, spelling: str, path: str, owner: str) -> None:
        identity = _canonical_skill_name(spelling)
        if not identity:
            raise ReleaseCheckError(f"bundled Skill at {path} has an empty {kind}")
        previous = claimed.get(identity)
        if previous is not None and previous[3] != owner:
            raise ReleaseCheckError(
                f"bundled Skill catalog identity {identity!r} collides: "
                f"{previous[0]} {previous[1]!r} at {previous[2]} and "
                f"{kind} {spelling!r} at {path}"
            )
        claimed[identity] = (kind, spelling, path, owner)

    claim(
        kind="catalog namespace",
        spelling="skills",
        path="skills",
        owner="catalog:skills",
    )
    for root in sorted(collection_skills):
        claim(
            kind="collection root",
            spelling=PurePosixPath(root).name,
            path=root,
            owner=f"collection:{root}",
        )

    curated_documents: dict[str, str] = {}
    for name in sorted(file_names):
        parts = PurePosixPath(name).parts
        if len(parts) != 3 or parts[0] != "skills" or parts[2] != "SKILL.md":
            continue
        root = "/".join(parts[:2])
        if root in collection_skills:
            raise ReleaseCheckError(
                f"collection root {root} also ships a curated SKILL.md"
            )
        curated_documents[name] = parts[1]

    catalog_skills: list[tuple[str, str, str]] = []
    for path, directory in curated_documents.items():
        catalog_skills.append(
            (
                path,
                directory,
                _catalog_document_name(read(path), path=path, fallback=directory),
            )
        )
    for root, members in collection_skills.items():
        for directory, declared_name in members.items():
            catalog_skills.append(
                (f"{root}/{directory}/SKILL.md", directory, declared_name)
            )

    for path, directory, declared_name in sorted(catalog_skills):
        owner = f"skill:{path}"
        claim(kind="directory", spelling=directory, path=path, owner=owner)
        claim(kind="declared name", spelling=declared_name, path=path, owner=owner)


def _verify_collections(file_names: set[str], read: "Callable[[str], bytes]") -> None:
    """Every shipped collection carries the whole payload its manifest claims.

    Three hardcoded paths cannot tell a wheel with 561 recipes from one with
    three: the collection is a pinned provenance boundary, so the artifact has
    to be checked against the inventory it ships rather than against a sample
    of it. Reads the manifest out of the archive, so this is a statement about
    the artifact and not about the working tree.
    """

    for root in REQUIRED_COLLECTIONS:
        marker = f"{root}/COLLECTION.json"
        manifest = f"{root}/MANIFEST.json"
        if marker not in file_names or manifest not in file_names:
            raise ReleaseCheckError(
                f"required collection {root} must ship COLLECTION.json and MANIFEST.json"
            )

    markers = sorted(n for n in file_names if n.endswith("/COLLECTION.json"))
    collection_skills: dict[str, dict[str, str]] = {}
    collection_ids: dict[str, str] = {}
    for marker in markers:
        marker_parts = PurePosixPath(marker).parts
        if (
            len(marker_parts) != 3
            or marker_parts[0] != "skills"
            or marker_parts[2] != "COLLECTION.json"
        ):
            raise ReleaseCheckError(
                "collection markers must be direct children of the skills catalog: "
                f"{marker}"
            )
        root = marker.rsplit("/", 1)[0]
        manifest_name = f"{root}/MANIFEST.json"
        if manifest_name not in file_names:
            raise ReleaseCheckError(f"collection {root} ships no MANIFEST.json")
        try:
            marker_document = json.loads(read(marker).decode("utf-8"))
            manifest_payload = read(manifest_name)
            manifest = json.loads(manifest_payload.decode("utf-8"))
        except (OSError, KeyError, ValueError) as error:
            raise ReleaseCheckError(
                f"collection {root} has unreadable metadata: {error}"
            ) from error
        if not isinstance(marker_document, dict):
            raise ReleaseCheckError(f"collection {root} marker must be a JSON object")
        if not isinstance(manifest, dict):
            raise ReleaseCheckError(f"collection {root} manifest must be a JSON object")
        required = REQUIRED_COLLECTIONS.get(root)
        raw_marker_id = marker_document.get("id")
        marker_id = raw_marker_id.strip() if isinstance(raw_marker_id, str) else ""
        if not marker_id:
            raise ReleaseCheckError(f"collection {root} marker has no id")
        previous_id_root = collection_ids.get(marker_id)
        if previous_id_root is not None:
            raise ReleaseCheckError(
                f"duplicate skill collection id {marker_id!r}: "
                f"{previous_id_root} and {root}"
            )
        collection_ids[marker_id] = root
        if required is not None and marker_id != required["id"]:
            raise ReleaseCheckError(
                f"required collection {root} marker id must be {required['id']!r}"
            )
        declared = manifest.get("skills")
        expected = manifest.get("skill_count")
        if not isinstance(declared, list):
            raise ReleaseCheckError(f"collection {root} manifest has no skills list")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ReleaseCheckError(f"collection {root} has an invalid skill_count")
        if len(declared) != expected:
            raise ReleaseCheckError(
                f"collection {root} manifest claims {expected} skills but lists "
                f"{len(declared)}"
            )
        if required is not None and expected != required["skill_count"]:
            raise ReleaseCheckError(
                f"required collection {root} must contain "
                f"{required['skill_count']} skills, not {expected}"
            )
        upstream = manifest.get("upstream")
        if required is not None and (
            not isinstance(upstream, dict)
            or upstream.get("commit") != required["upstream_commit"]
        ):
            raise ReleaseCheckError(
                f"required collection {root} must pin upstream commit "
                f"{required['upstream_commit']}"
            )
        if required is not None and hashlib.sha256(manifest_payload).hexdigest() != (
            required["manifest_sha256"]
        ):
            raise ReleaseCheckError(
                f"required collection {root} manifest digest does not match "
                "the reviewed pin"
            )
        directories: list[str] = []
        declared_names: dict[str, str] = {}
        names_by_directory: dict[str, tuple[str, str]] = {}
        directory_identities: dict[str, str] = {}
        for index, entry in enumerate(declared):
            if not isinstance(entry, dict):
                raise ReleaseCheckError(
                    f"collection {root} skill entry {index} is not an object"
                )
            directory = _collection_relative_path(
                entry.get("directory"), root=root, field="skill directory"
            )
            if len(PurePosixPath(directory).parts) != 1:
                raise ReleaseCheckError(
                    f"collection {root} skill directory must be one component: "
                    f"{directory!r}"
                )
            declared_name = entry.get("name")
            canonical_name = _canonical_skill_name(declared_name)
            if not canonical_name:
                raise ReleaseCheckError(
                    f"collection {root} skill entry {index} has no name"
                )
            previous_name = declared_names.get(canonical_name)
            if previous_name is not None:
                raise ReleaseCheckError(
                    f"collection {root} lists duplicate declared skill name "
                    f"identity {canonical_name!r}: {previous_name!r}, "
                    f"{declared_name!r}"
                )
            declared_names[canonical_name] = str(declared_name)
            names_by_directory[directory] = (str(declared_name), canonical_name)
            directory_identity = _canonical_skill_name(directory)
            previous_directory = directory_identities.get(directory_identity)
            if previous_directory is not None:
                raise ReleaseCheckError(
                    f"collection {root} lists duplicate skill directory identity "
                    f"{directory_identity!r}: {previous_directory!r}, {directory!r}"
                )
            directory_identities[directory_identity] = directory
            directories.append(directory)
        if len(set(directories)) != len(directories):
            raise ReleaseCheckError(
                f"collection {root} lists duplicate skill directories"
            )
        for identity in sorted(declared_names.keys() & directory_identities.keys()):
            declared_directory = next(
                directory
                for directory, (_name, canonical_name) in names_by_directory.items()
                if canonical_name == identity
            )
            directory_owner = directory_identities[identity]
            if declared_directory != directory_owner:
                raise ReleaseCheckError(
                    f"collection {root} catalog identity {identity!r} is both "
                    f"the declared name of {declared_directory!r} and the "
                    f"directory of {directory_owner!r}"
                )
        absent = sorted(
            f"{root}/{directory}/SKILL.md"
            for directory in directories
            if f"{root}/{directory}/SKILL.md" not in file_names
        )
        if absent:
            raise ReleaseCheckError(
                f"collection {root} is missing {len(absent)} of {len(declared)} "
                f"recipes, e.g. {', '.join(absent[:3])}"
            )
        rows = manifest.get("files")
        if not isinstance(rows, list):
            raise ReleaseCheckError(f"collection {root} manifest has no files list")
        recorded: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ReleaseCheckError(
                    f"collection {root} file entry {index} is not an object"
                )
            relative = _collection_relative_path(
                row.get("path"), root=root, field="payload path"
            )
            if relative in recorded:
                raise ReleaseCheckError(
                    f"collection {root} lists duplicate payload path: {relative}"
                )
            recorded.add(relative)
            archive_name = f"{root}/{relative}"
            if archive_name not in file_names:
                raise ReleaseCheckError(
                    f"collection {root} is missing manifested payload: {relative}"
                )
            expected_size = row.get("size")
            expected_hash = row.get("sha256")
            if (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or expected_size < 0
                or not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(char not in "0123456789abcdef" for char in expected_hash)
            ):
                raise ReleaseCheckError(
                    f"collection {root} has invalid digest metadata for {relative}"
                )
            payload = read(archive_name)
            if len(payload) != expected_size:
                raise ReleaseCheckError(
                    f"collection {root} payload size mismatch: {relative}"
                )
            if hashlib.sha256(payload).hexdigest() != expected_hash:
                raise ReleaseCheckError(
                    f"collection {root} payload hash mismatch: {relative}"
                )
        shipped = {
            name[len(root) + 1 :] for name in file_names if name.startswith(f"{root}/")
        }
        shipped_recipe_directories = {
            parts[0]
            for relative in shipped
            for parts in (PurePosixPath(relative).parts,)
            if len(parts) == 2 and parts[1] == "SKILL.md"
        }
        declared_directories = set(directories)
        if shipped_recipe_directories != declared_directories:
            missing_from_manifest = sorted(
                shipped_recipe_directories - declared_directories
            )
            missing_from_archive = sorted(
                declared_directories - shipped_recipe_directories
            )
            details = []
            if missing_from_manifest:
                details.append("unlisted recipe " + repr(missing_from_manifest[0]))
            if missing_from_archive:
                details.append("missing recipe " + repr(missing_from_archive[0]))
            raise ReleaseCheckError(
                f"collection {root} recipe inventory mismatch: " + "; ".join(details)
            )
        extra = sorted(shipped - recorded - _COLLECTION_BOUNDARY_FILES)
        if extra:
            raise ReleaseCheckError(
                f"collection {root} ships {len(extra)} unmanifested payload file(s), "
                f"e.g. {', '.join(extra[:3])}"
            )
        # Parse declared identities only after every recipe byte has matched
        # the reviewed manifest. This keeps a corrupted document on the
        # integrity-error path instead of interpreting unauthenticated text.
        for directory, (declared_name, canonical_name) in names_by_directory.items():
            skill_path = f"{root}/{directory}/SKILL.md"
            document_name = _skill_document_name(read(skill_path), path=skill_path)
            if _canonical_skill_name(document_name) != canonical_name:
                raise ReleaseCheckError(
                    f"collection {root} manifest/document name mismatch for "
                    f"{directory}: {declared_name!r} != {document_name!r}"
                )
        collection_skills[root] = {
            directory: values[0] for directory, values in names_by_directory.items()
        }

    _verify_catalog_identities(file_names, read, collection_skills)


def _missing(required: frozenset[str], names: set[str], *, label: str) -> None:
    missing = sorted(required - names)
    if missing:
        raise ReleaseCheckError(
            f"{label} is missing required files: {', '.join(missing)}"
        )


def _verify_metadata(payload: bytes) -> None:
    message = email.parser.BytesParser().parsebytes(payload)
    if message.get("Name") != "openai4s":
        raise ReleaseCheckError("wheel metadata Name must be openai4s")
    if not message.get("Version"):
        raise ReleaseCheckError("wheel metadata has no Version")
    if not (message.get("Summary") or "").strip():
        raise ReleaseCheckError("wheel metadata has no Summary")
    if message.get("License-Expression") != "MIT":
        raise ReleaseCheckError("wheel metadata License-Expression must be MIT")
    description_type = (message.get("Description-Content-Type") or "").partition(";")[0]
    if description_type.strip().casefold() != "text/markdown":
        raise ReleaseCheckError("wheel long description must be Markdown")
    project_urls = {
        value.partition(",")[0].strip(): value.partition(",")[2].strip()
        for value in message.get_all("Project-URL", [])
    }
    missing_urls = sorted(
        {"Homepage", "Documentation", "Issues", "Source"} - project_urls.keys()
    )
    if missing_urls:
        raise ReleaseCheckError(
            "wheel metadata is missing Project-URL entries: " + ", ".join(missing_urls)
        )
    requires_python = message.get("Requires-Python") or ""
    if ">=3.10" not in requires_python.replace(" ", ""):
        raise ReleaseCheckError("wheel metadata must preserve Requires-Python >=3.10")
    core_requirements = []
    for requirement in message.get_all("Requires-Dist", []):
        marker = requirement.partition(";")[2].replace(" ", "").casefold()
        if "extra==" not in marker:
            core_requirements.append(requirement)
    if core_requirements:
        raise ReleaseCheckError(
            "core wheel declares non-extra dependencies: "
            + ", ".join(core_requirements)
        )


def verify_wheel(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        for entry in entries:
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ReleaseCheckError(f"wheel contains a symlink: {entry.filename}")
        names = _safe_names(
            [entry.filename for entry in entries],
            archive=path.name,
            directories={
                entry.filename.rstrip("/") for entry in entries if entry.is_dir()
            },
        )
        file_names = {
            entry.filename.rstrip("/") for entry in entries if not entry.is_dir()
        }
        _missing(_WHEEL_REQUIRED, names, label="wheel")
        if any(name.startswith("tests/") for name in names):
            raise ReleaseCheckError("wheel must not ship the test suite")
        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ReleaseCheckError("wheel must contain exactly one METADATA file")
        _verify_metadata(archive.read(metadata_names[0]))
        entry_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_names) != 1 or b"openai4s = openai4s.cli:main" not in archive.read(
            entry_names[0]
        ):
            raise ReleaseCheckError(
                "wheel does not expose the openai4s console entry point"
            )
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        if len(wheel_names) != 1 or b"Tag: py3-none-any" not in archive.read(
            wheel_names[0]
        ):
            raise ReleaseCheckError(
                "wheel must remain platform-independent (py3-none-any)"
            )
        _verify_collections(file_names, archive.read)
    return names


def verify_sdist(path: Path) -> set[str]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise ReleaseCheckError(
                    f"sdist contains a link or special file: {member.name}"
                )
        raw_names = [member.name for member in members]
        names = _safe_names(
            raw_names,
            archive=path.name,
            directories={
                member.name.rstrip("/") for member in members if member.isdir()
            },
        )
        roots = {PurePosixPath(name).parts[0] for name in names}
        if len(roots) != 1:
            raise ReleaseCheckError("sdist must contain one top-level directory")
        root = next(iter(roots))
        relative = {
            PurePosixPath(name).relative_to(root).as_posix()
            for name in names
            if name != root
        }
        _missing(_SDIST_REQUIRED, relative, label="sdist")
        file_members = {
            PurePosixPath(member.name).relative_to(root).as_posix(): member
            for member in members
            if member.isfile()
        }

        def read_member(name: str) -> bytes:
            member = file_members.get(name)
            if member is None:
                raise KeyError(name)
            stream = archive.extractfile(member)
            if stream is None:
                raise OSError(f"cannot read {name}")
            return stream.read()

        _verify_collections(set(file_members), read_member)
    return relative


def verify(dist_dir: Path) -> tuple[Path, Path]:
    dist_dir = dist_dir.resolve()
    wheels = sorted(dist_dir.glob("openai4s-*.whl"))
    sdists = sorted(dist_dir.glob("openai4s-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseCheckError(
            f"expected one openai4s wheel and one sdist, found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    verify_wheel(wheels[0])
    verify_sdist(sdists[0])
    return wheels[0], sdists[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        wheel, sdist = verify(args.dist_dir)
    except (OSError, ReleaseCheckError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"release artifact verification failed: {error}", file=sys.stderr)
        return 1
    print(f"release artifacts verified: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
