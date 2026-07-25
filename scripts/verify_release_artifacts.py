#!/usr/bin/env python3
"""Validate OpenAI4S wheel/sdist contents using only the standard library."""

from __future__ import annotations

import argparse
import email.parser
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

_WHEEL_REQUIRED = frozenset(
    {
        "openai4s/__init__.py",
        "openai4s/cli/main.py",
        "openai4s/kernel/r_worker.R",
        "openai4s/compute/templates/run.sh.tmpl",
        "openai4s/compute/templates/wrapper.sh.tmpl",
        "openai4s/server/webui/index.html",
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
        "skills/remote-compute-nvidia/provider.json",
        "skills/remote-compute-nvidia/provider.py",
    }
)
_SDIST_REQUIRED = frozenset(
    {
        "CODE_OF_CONDUCT.md",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "SECURITY.md",
        "docs/release-validation.md",
        "pyproject.toml",
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


def _safe_names(names: list[str], *, archive: str) -> set[str]:
    normalized: set[str] = set()
    for raw in names:
        value = raw.rstrip("/")
        if not value:
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or "\\" in value or ".." in path.parts:
            raise ReleaseCheckError(f"{archive} contains unsafe path: {raw!r}")
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
    return normalized


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
        names = _safe_names([entry.filename for entry in entries], archive=path.name)
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
    names = _safe_names(raw_names, archive=path.name)
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
