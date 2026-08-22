"""Pure-stdlib deployment helpers for public RetroChimera checkpoints."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Sequence

CHUNK_SIZE = 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024 * 1024
MODEL_VERSION = "1.2.0"
CHECKPOINT_SHA256_SCOPE = "source_archive"
_HARDLINK_UNSUPPORTED = frozenset(
    {
        errno.EPERM,
        errno.EXDEV,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EPERM),
        getattr(errno, "EOPNOTSUPP", errno.EPERM),
    }
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
    | {f"COM{number}" for number in "¹²³"}
    | {f"LPT{number}" for number in "¹²³"}
)
_WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>"|?*')


class CheckpointDeploymentError(RuntimeError):
    """Raised when a checkpoint cannot be safely downloaded or installed."""


@dataclass(frozen=True, slots=True)
class CheckpointSpec:
    """Reviewed public metadata for one upstream checkpoint archive."""

    name: str
    dataset: str
    article_id: int
    file_id: int
    filename: str
    byte_size: int
    md5: str

    @property
    def download_url(self) -> str:
        return f"https://ndownloader.figshare.com/files/{self.file_id}"

    @property
    def source_url(self) -> str:
        return f"https://doi.org/10.6084/m9.figshare.{self.article_id}.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "article_id": self.article_id,
            "file_id": self.file_id,
            "filename": self.filename,
            "byte_size": self.byte_size,
            "md5": self.md5,
            "download_url": self.download_url,
            "source_url": self.source_url,
            "license": "MIT",
        }


CHECKPOINTS = {
    spec.name: spec
    for spec in (
        CheckpointSpec(
            name="pistachio",
            dataset="Pistachio",
            article_id=30591107,
            file_id=59468882,
            filename="retrochimera_pistachio.zip",
            byte_size=4_213_968_927,
            md5="50406d29b96b165a68fef73fa31448e3",
        ),
        CheckpointSpec(
            name="uspto50k",
            dataset="USPTO-50K",
            article_id=30601718,
            file_id=59511926,
            filename="retrochimera_uspto50k.zip",
            byte_size=284_852_815,
            md5="f85766b7b2b8693213b429bfb7b20dd6",
        ),
        CheckpointSpec(
            name="uspto-full",
            dataset="USPTO-FULL",
            article_id=30597563,
            file_id=59494598,
            filename="retrochimera_uspto_full.zip",
            byte_size=4_607_889_148,
            md5="47d9f2e3be297d32ce50eb3b7e61c868",
        ),
    )
}


def checkpoint_spec(name: str) -> CheckpointSpec:
    try:
        return CHECKPOINTS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown checkpoint {name!r}; expected one of "
            + ", ".join(sorted(CHECKPOINTS))
        ) from exc


def _hash_stream(
    handle: BinaryIO,
    *,
    copy_to: BinaryIO | None = None,
    max_bytes: int | None = None,
) -> tuple[int, str, str]:
    size = 0
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    while True:
        chunk = handle.read(CHUNK_SIZE)
        if not chunk:
            break
        next_size = size + len(chunk)
        if max_bytes is not None and next_size > max_bytes:
            raise CheckpointDeploymentError(
                f"checkpoint exceeds expected size {max_bytes} bytes"
            )
        if copy_to is not None:
            copy_to.write(chunk)
        size = next_size
        md5.update(chunk)
        sha256.update(chunk)
    return size, md5.hexdigest(), sha256.hexdigest()


def _open_regular_binary(path: Path) -> BinaryIO:
    """Open one regular file without ever blocking on a FIFO."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CheckpointDeploymentError(
                f"checkpoint archive is not a regular file: {path}"
            )
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _path_matches_regular_inode(path: Path, expected: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode) and os.path.samestat(expected, current)


def _open_directory_identity(path: Path) -> tuple[int | None, os.stat_result]:
    """Hold a directory inode open where the platform exposes directory fds."""

    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        descriptor = os.open(path, os.O_RDONLY | directory_flag)
        try:
            identity = os.fstat(descriptor)
            if not stat.S_ISDIR(identity.st_mode):
                raise CheckpointDeploymentError(
                    f"checkpoint staging path is not a directory: {path}"
                )
            return descriptor, identity
        except BaseException:
            os.close(descriptor)
            raise
    identity = os.lstat(path)
    if not stat.S_ISDIR(identity.st_mode):
        raise CheckpointDeploymentError(
            f"checkpoint staging path is not a directory: {path}"
        )
    return None, identity


def _path_matches_directory_inode(path: Path, expected: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(current.st_mode) and os.path.samestat(expected, current)


def _checkpoint_verification(
    spec: CheckpointSpec, *, size: int, md5: str, sha256: str
) -> dict[str, Any]:
    if size != spec.byte_size:
        raise CheckpointDeploymentError(
            f"checkpoint size mismatch: expected {spec.byte_size}, got {size}"
        )
    if md5 != spec.md5:
        raise CheckpointDeploymentError(
            f"checkpoint MD5 mismatch: expected {spec.md5}, got {md5}"
        )
    return {
        "checkpoint": spec.name,
        "archive_bytes": size,
        "upstream_md5": md5,
        "checkpoint_sha256": sha256,
        "checkpoint_sha256_scope": CHECKPOINT_SHA256_SCOPE,
        "source_url": spec.source_url,
    }


def verify_checkpoint(path: str | Path, spec: CheckpointSpec) -> dict[str, Any]:
    """Validate an archive against reviewed upstream metadata and hash it."""

    archive = Path(path).expanduser()
    with _open_regular_binary(archive) as handle:
        verified_identity = os.fstat(handle.fileno())
        first_digest = _hash_stream(handle, max_bytes=spec.byte_size)
        verification = _checkpoint_verification(
            spec,
            size=first_digest[0],
            md5=first_digest[1],
            sha256=first_digest[2],
        )
        if not _path_matches_regular_inode(archive, verified_identity):
            raise CheckpointDeploymentError(
                "checkpoint path changed during verification"
            )
        handle.seek(0)
        if _hash_stream(handle, max_bytes=spec.byte_size) != first_digest:
            raise CheckpointDeploymentError(
                "checkpoint bytes changed during verification"
            )
        if not _path_matches_regular_inode(archive, verified_identity):
            raise CheckpointDeploymentError(
                "checkpoint path changed during final verification"
            )
        return verification


def download_checkpoint(
    spec: CheckpointSpec,
    destination: str | Path,
    *,
    allow_network: bool = False,
    timeout_seconds: float = 60.0,
    web_download: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Download and validate a checkpoint through the guarded Host capability."""

    if not isinstance(allow_network, bool):
        raise TypeError("allow_network must be a boolean")
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        raise TypeError("timeout_seconds must be a positive number")
    if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
    if not allow_network:
        raise PermissionError("checkpoint download requires allow_network=True")
    if web_download is None:
        raise CheckpointDeploymentError(
            "checkpoint download requires the injected OpenAI4S capability; "
            "pass web_download=host.web_download from a Python cell, or acquire "
            "the archive with an operator-managed downloader and run verify"
        )
    destination_path = Path(destination).expanduser()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.is_symlink():
        raise FileExistsError(
            f"checkpoint destination already exists: {destination_path}"
        )
    if destination_path.exists():
        return verify_checkpoint(destination_path, spec)
    download_stage = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.download-", dir=destination_path.parent
        )
    )
    # Do not use ``spec.filename`` as a path component. Built-in specs are
    # reviewed, but CheckpointSpec is public and a caller-constructed filename
    # must not be able to escape the private download directory.
    partial = download_stage / "checkpoint.archive"
    try:
        response = web_download(
            spec.download_url,
            str(partial),
            max_bytes=spec.byte_size,
            timeout=timeout_seconds,
        )
        if not isinstance(response, dict):
            raise CheckpointDeploymentError(
                "host.web_download returned an invalid response"
            )
        if response.get("error"):
            raise CheckpointDeploymentError(
                f"checkpoint download failed: {response['error']}"
            )
        with _open_regular_binary(partial) as source:
            first_digest = _hash_stream(source, max_bytes=spec.byte_size)
            verification = _checkpoint_verification(
                spec,
                size=first_digest[0],
                md5=first_digest[1],
                sha256=first_digest[2],
            )
            verified_identity = os.fstat(source.fileno())
            try:
                # The destination does not exist, so a hard link atomically
                # publishes the exact inode held by ``source`` without a
                # verify-by-fd/use-by-path gap. It also refuses a destination
                # created by a concurrent process instead of overwriting it.
                os.link(
                    partial,
                    destination_path,
                    follow_symlinks=False,
                )
            except OSError as exc:
                if exc.errno not in _HARDLINK_UNSUPPORTED:
                    raise CheckpointDeploymentError(
                        "could not atomically publish the verified checkpoint"
                    ) from exc
                # Some removable and network filesystems have atomic rename
                # but no hard links. Preserve byte binding with the held fd
                # and pre/post identity checks; callers must serialize writers
                # to this destination on the compatibility path.
                if not _path_matches_regular_inode(partial, verified_identity):
                    raise CheckpointDeploymentError(
                        "checkpoint staging path changed before publication"
                    )
                if destination_path.exists() or destination_path.is_symlink():
                    raise CheckpointDeploymentError(
                        "checkpoint destination appeared during publication"
                    )
                os.replace(partial, destination_path)
            if not _path_matches_regular_inode(destination_path, verified_identity):
                raise CheckpointDeploymentError(
                    "checkpoint staging path changed during verified publication"
                )

            # Re-read the still-open published inode. This detects in-place
            # mutation during the first hash or publication, while the inode
            # comparison above detects a pathname replacement.
            source.seek(0)
            second_digest = _hash_stream(source, max_bytes=spec.byte_size)
            if second_digest != first_digest:
                raise CheckpointDeploymentError(
                    "checkpoint bytes changed during verified publication"
                )
            if not _path_matches_regular_inode(destination_path, verified_identity):
                raise CheckpointDeploymentError(
                    "checkpoint destination changed during verified publication"
                )
            return verification
    finally:
        shutil.rmtree(download_stage, ignore_errors=True)


def model_manifest(spec: CheckpointSpec, checkpoint_sha256: str) -> dict[str, Any]:
    """Build the path-free manifest consumed by ``SyntheseusBackend``."""

    digest = checkpoint_sha256.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("checkpoint_sha256 must be a 64-character SHA-256")
    return {
        "schema_version": 1,
        "provider": "Microsoft Research",
        "model": "RetroChimera",
        "model_version": MODEL_VERSION,
        "checkpoint_id": f"figshare-file-{spec.file_id}-{spec.name}",
        "checkpoint_sha256": digest,
        "training_dataset": spec.dataset,
        "code_license": "MIT",
        "checkpoint_license": "MIT",
        "source_url": spec.source_url,
        "metadata": {
            "archive_bytes": spec.byte_size,
            "upstream_md5": spec.md5,
            "checkpoint_sha256_scope": CHECKPOINT_SHA256_SCOPE,
            "runtime_integrity": "unverified",
        },
    }


def _model_manifest_text(spec: CheckpointSpec, checkpoint_sha256: str) -> str:
    return (
        json.dumps(
            model_manifest(spec, checkpoint_sha256),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def write_model_manifest(
    destination: str | Path, spec: CheckpointSpec, checkpoint_sha256: str
) -> Path:
    """Atomically write a public model manifest."""

    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _model_manifest_text(spec, checkpoint_sha256).encode("utf-8")
    stage = tempfile.TemporaryDirectory(
        prefix=f".{output.name}.manifest-",
        dir=output.parent,
        ignore_cleanup_errors=True,
    )
    temporary = Path(stage.name) / "manifest.part"
    publish_link = Path(stage.name) / "publish.link"
    try:
        with temporary.open("x+b") as handle:
            handle.write(payload)
            handle.flush()
            verified_identity = os.fstat(handle.fileno())
            expected_digest = (len(payload), hashlib.sha256(payload).hexdigest())
            publication_source = publish_link
            try:
                os.link(temporary, publish_link, follow_symlinks=False)
            except OSError as exc:
                if exc.errno not in _HARDLINK_UNSUPPORTED:
                    raise CheckpointDeploymentError(
                        "could not stage the model manifest for publication"
                    ) from exc
                publication_source = temporary
            if not _path_matches_regular_inode(publication_source, verified_identity):
                raise CheckpointDeploymentError(
                    "model manifest staging path changed before publication"
                )
            handle.seek(0)
            size, _md5, sha256 = _hash_stream(handle, max_bytes=len(payload))
            if (size, sha256) != expected_digest:
                raise CheckpointDeploymentError(
                    "model manifest bytes changed before publication"
                )
            if not _path_matches_regular_inode(publication_source, verified_identity):
                raise CheckpointDeploymentError(
                    "model manifest staging path changed before publication"
                )
            os.replace(publication_source, output)
            if not _path_matches_regular_inode(output, verified_identity):
                raise CheckpointDeploymentError(
                    "model manifest destination changed during publication"
                )
            handle.seek(0)
            size, _md5, sha256 = _hash_stream(handle, max_bytes=len(payload))
            if (size, sha256) != expected_digest:
                raise CheckpointDeploymentError(
                    "model manifest bytes changed during publication"
                )
            if not _path_matches_regular_inode(output, verified_identity):
                raise CheckpointDeploymentError(
                    "model manifest destination changed during verification"
                )
    finally:
        stage.cleanup()
    return output


def _manifest_relative_path(
    destination: Path, manifest: str | Path | None
) -> Path | None:
    if manifest is None:
        return None
    root = destination.resolve(strict=False)
    requested = Path(manifest).expanduser()
    if requested.is_absolute():
        candidate = requested.resolve(strict=False)
    else:
        # Accept both useful spellings for a relative destination:
        # ``--manifest model-manifest.json`` (model-dir relative) and
        # ``--manifest models/checkpoint/model-manifest.json`` (cwd relative).
        # Always prefer the cwd interpretation when it already lands inside
        # the requested model directory; otherwise confine the name beneath it.
        cwd_candidate = requested.resolve(strict=False)
        try:
            cwd_candidate.relative_to(root)
        except ValueError:
            candidate = root.joinpath(requested).resolve(strict=False)
        else:
            candidate = cwd_candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "manifest must be located inside the new model directory"
        ) from exc
    if not relative.parts:
        raise ValueError("manifest must name a file inside the new model directory")
    return relative


def _safe_member_path(member: zipfile.ZipInfo) -> PurePosixPath:
    name = member.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
        or any(
            any(
                ord(char) < 32 or char in _WINDOWS_INVALID_FILENAME_CHARS
                for char in part
            )
            for part in path.parts
        )
        or any(part.endswith((" ", ".")) for part in path.parts)
        or any(
            part.rstrip(" .").split(".", 1)[0].rstrip(" ").upper()
            in _WINDOWS_RESERVED_NAMES
            for part in path.parts
        )
    ):
        raise CheckpointDeploymentError(f"unsafe checkpoint member {name!r}")
    mode = member.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise CheckpointDeploymentError(f"checkpoint member is a symlink: {name!r}")
    return path


def _parent_directory_names(path: PurePosixPath) -> set[str]:
    return {
        PurePosixPath(*path.parts[:index]).as_posix()
        for index in range(1, len(path.parts))
    }


def _copy_checkpoint_member(
    source: BinaryIO, target: BinaryIO, *, expected_size: int
) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise CheckpointDeploymentError(
                "checkpoint member exceeds its declared uncompressed size"
            )
        target.write(chunk)
        digest.update(chunk)
    if size != expected_size:
        raise CheckpointDeploymentError(
            "checkpoint member does not match its declared uncompressed size"
        )
    return size, digest.hexdigest()


def _stat_signature(identity: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        identity.st_dev,
        identity.st_ino,
        identity.st_size,
        identity.st_mtime_ns,
        identity.st_ctime_ns,
    )


def _verify_published_tree(
    root: Path,
    *,
    expected_directories: set[str],
    expected_files: dict[str, tuple[int, str]],
) -> None:
    """Bind the published directory contents to bytes read from the archive."""

    observed_directories: dict[str, tuple[int, int, int, int, int]] = {}
    observed_files: dict[str, tuple[int, int, int, int, int]] = {}
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError as exc:
        raise CheckpointDeploymentError(
            "could not enumerate the published checkpoint tree"
        ) from exc
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        try:
            identity = os.lstat(entry)
        except OSError as exc:
            raise CheckpointDeploymentError(
                "checkpoint tree changed during verification"
            ) from exc
        if stat.S_ISDIR(identity.st_mode):
            if relative not in expected_directories:
                raise CheckpointDeploymentError(
                    f"checkpoint tree contains an unexpected directory: {relative}"
                )
            observed_directories[relative] = _stat_signature(identity)
            continue
        if not stat.S_ISREG(identity.st_mode):
            raise CheckpointDeploymentError(
                f"checkpoint tree contains a non-regular entry: {relative}"
            )
        expected = expected_files.get(relative)
        if expected is None:
            raise CheckpointDeploymentError(
                f"checkpoint tree contains an unexpected file: {relative}"
            )
        if identity.st_size != expected[0]:
            raise CheckpointDeploymentError(
                f"checkpoint tree file size changed: {relative}"
            )
        with _open_regular_binary(entry) as handle:
            opened_identity = os.fstat(handle.fileno())
            if not os.path.samestat(identity, opened_identity):
                raise CheckpointDeploymentError(
                    f"checkpoint tree path changed before verification: {relative}"
                )
            size, _md5, sha256 = _hash_stream(handle, max_bytes=expected[0])
            final_identity = os.fstat(handle.fileno())
        if (
            (size, sha256) != expected
            or not os.path.samestat(opened_identity, final_identity)
            or not _path_matches_regular_inode(entry, final_identity)
        ):
            raise CheckpointDeploymentError(
                f"checkpoint tree file changed during verification: {relative}"
            )
        observed_files[relative] = _stat_signature(final_identity)

    if set(observed_directories) != expected_directories or set(observed_files) != set(
        expected_files
    ):
        raise CheckpointDeploymentError("checkpoint tree is missing expected entries")

    # A second metadata pass catches add/remove/replace/write races that occur
    # after an entry was hashed but before the whole tree was accepted.
    second_directories: dict[str, tuple[int, int, int, int, int]] = {}
    second_files: dict[str, tuple[int, int, int, int, int]] = {}
    try:
        second_entries = sorted(root.rglob("*"), key=lambda item: item.as_posix())
        for entry in second_entries:
            relative = entry.relative_to(root).as_posix()
            identity = os.lstat(entry)
            if stat.S_ISDIR(identity.st_mode):
                second_directories[relative] = _stat_signature(identity)
            elif stat.S_ISREG(identity.st_mode):
                second_files[relative] = _stat_signature(identity)
            else:
                raise CheckpointDeploymentError(
                    f"checkpoint tree contains a non-regular entry: {relative}"
                )
    except OSError as exc:
        raise CheckpointDeploymentError(
            "checkpoint tree changed during final verification"
        ) from exc
    if second_directories != observed_directories or second_files != observed_files:
        raise CheckpointDeploymentError(
            "checkpoint tree changed during final verification"
        )


def extract_checkpoint(
    archive: str | Path,
    destination: str | Path,
    spec: CheckpointSpec,
    *,
    manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Verify and atomically extract a checkpoint without path traversal."""

    archive_path = Path(archive).expanduser()
    destination_path = Path(destination).expanduser()
    if destination_path.exists() or destination_path.is_symlink():
        raise FileExistsError(
            f"checkpoint destination already exists: {destination_path}"
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_relative = _manifest_relative_path(destination_path, manifest)
    with (
        _open_regular_binary(archive_path) as source_archive,
        tempfile.TemporaryFile(
            "w+b",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.archive-",
        ) as archive_snapshot,
    ):
        size, md5, sha256 = _hash_stream(
            source_archive,
            copy_to=archive_snapshot,
            max_bytes=spec.byte_size,
        )
        verification = _checkpoint_verification(spec, size=size, md5=md5, sha256=sha256)
        archive_snapshot.flush()
        archive_snapshot.seek(0)
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{destination_path.name}.stage-", dir=destination_path.parent
            )
        )
        stage_descriptor: int | None = None
        stage_identity: os.stat_result | None = None
        try:
            stage_descriptor, stage_identity = _open_directory_identity(stage)
            stage_root = stage.resolve()
            expected_directories: set[str] = set()
            expected_files: dict[str, tuple[int, str]] = {}
            with zipfile.ZipFile(archive_snapshot) as bundle:
                members = bundle.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise CheckpointDeploymentError(
                        "checkpoint archive has too many members"
                    )
                extracted_bytes = sum(member.file_size for member in members)
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise CheckpointDeploymentError(
                        "checkpoint archive exceeds the extracted-size limit"
                    )
                for member in members:
                    relative = _safe_member_path(member)
                    relative_name = relative.as_posix()
                    expected_directories.update(_parent_directory_names(relative))
                    output = stage_root.joinpath(*relative.parts)
                    try:
                        output.resolve(strict=False).relative_to(stage_root)
                    except (OSError, ValueError) as exc:
                        raise CheckpointDeploymentError(
                            f"unsafe checkpoint member {member.filename!r}"
                        ) from exc
                    if member.is_dir():
                        output.mkdir(parents=True, exist_ok=True)
                        expected_directories.add(relative_name)
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, output.open("xb") as target:
                        expected_files[relative_name] = _copy_checkpoint_member(
                            source,
                            target,
                            expected_size=member.file_size,
                        )
            manifest_path: Path | None = None
            if manifest_relative is not None:
                staged_manifest = stage_root.joinpath(manifest_relative)
                if staged_manifest.exists() or staged_manifest.is_symlink():
                    raise FileExistsError(
                        "manifest destination collides with a checkpoint member: "
                        f"{manifest_relative}"
                    )
                write_model_manifest(
                    staged_manifest, spec, verification["checkpoint_sha256"]
                )
                manifest_relative_posix = PurePosixPath(*manifest_relative.parts)
                expected_directories.update(
                    _parent_directory_names(manifest_relative_posix)
                )
                manifest_payload = _model_manifest_text(
                    spec, verification["checkpoint_sha256"]
                ).encode("utf-8")
                expected_files[manifest_relative_posix.as_posix()] = (
                    len(manifest_payload),
                    hashlib.sha256(manifest_payload).hexdigest(),
                )
                manifest_path = destination_path.joinpath(manifest_relative)
            if not _path_matches_directory_inode(stage, stage_identity):
                raise CheckpointDeploymentError(
                    "checkpoint staging directory changed before publication"
                )
            os.replace(stage, destination_path)
            if not _path_matches_directory_inode(destination_path, stage_identity):
                raise CheckpointDeploymentError(
                    "checkpoint destination changed during publication"
                )
            _verify_published_tree(
                destination_path.resolve(),
                expected_directories=expected_directories,
                expected_files=expected_files,
            )
            if not _path_matches_directory_inode(destination_path, stage_identity):
                raise CheckpointDeploymentError(
                    "checkpoint destination changed during tree verification"
                )
        except BaseException:
            if stage_identity is not None:
                if _path_matches_directory_inode(stage, stage_identity):
                    shutil.rmtree(stage, ignore_errors=True)
            else:
                shutil.rmtree(stage, ignore_errors=True)
            raise
        finally:
            if stage_descriptor is not None:
                os.close(stage_descriptor)
    result = {
        **verification,
        "model_dir": str(destination_path),
        "extracted_bytes": extracted_bytes,
        "member_count": len(members),
    }
    if manifest_path is not None:
        result["manifest"] = str(manifest_path)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print reviewed checkpoint metadata")
    for name in ("verify", "extract"):
        command = commands.add_parser(name)
        command.add_argument("variant", choices=sorted(CHECKPOINTS))
        command.add_argument("archive", type=Path)
        if name == "extract":
            command.add_argument("model_dir", type=Path)
            command.add_argument(
                "--manifest",
                type=Path,
                help="write a manifest inside model_dir before atomic publication",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list":
        result: Any = [CHECKPOINTS[name].to_dict() for name in sorted(CHECKPOINTS)]
    else:
        spec = checkpoint_spec(args.variant)
        if args.command == "verify":
            result = verify_checkpoint(args.archive, spec)
        else:
            result = extract_checkpoint(
                args.archive,
                args.model_dir,
                spec,
                manifest=args.manifest,
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHECKPOINTS",
    "CheckpointDeploymentError",
    "CheckpointSpec",
    "checkpoint_spec",
    "download_checkpoint",
    "extract_checkpoint",
    "main",
    "model_manifest",
    "verify_checkpoint",
    "write_model_manifest",
]
