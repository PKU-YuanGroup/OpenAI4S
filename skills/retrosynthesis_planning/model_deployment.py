"""Pure-stdlib deployment helpers for public RetroChimera checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
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


def _hash_stream(handle: BinaryIO) -> tuple[int, str, str]:
    size = 0
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    while True:
        chunk = handle.read(CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        md5.update(chunk)
        sha256.update(chunk)
    return size, md5.hexdigest(), sha256.hexdigest()


def verify_checkpoint(path: str | Path, spec: CheckpointSpec) -> dict[str, Any]:
    """Validate an archive against reviewed upstream metadata and hash it."""

    archive = Path(path).expanduser()
    with archive.open("rb") as handle:
        size, md5, sha256 = _hash_stream(handle)
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
        "source_url": spec.source_url,
    }


def download_checkpoint(
    spec: CheckpointSpec,
    destination: str | Path,
    *,
    allow_network: bool = False,
    timeout_seconds: float = 60.0,
    web_download: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Download and validate a checkpoint through the guarded Host capability."""

    if not allow_network:
        raise PermissionError("checkpoint download requires allow_network=True")
    if web_download is None:
        try:
            import host
        except ImportError as exc:
            raise CheckpointDeploymentError(
                "checkpoint download requires OpenAI4S host.web_download; "
                "run it inside an OpenAI4S Python cell, or acquire the archive "
                "with an operator-managed downloader and run verify"
            ) from exc
        web_download = host.web_download
    destination_path = Path(destination).expanduser()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        return verify_checkpoint(destination_path, spec)
    partial = destination_path.with_name(destination_path.name + ".part")
    partial.unlink(missing_ok=True)
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
        verification = verify_checkpoint(partial, spec)
        os.replace(partial, destination_path)
        return verification
    except Exception:
        partial.unlink(missing_ok=True)
        raise


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
        },
    }


def write_model_manifest(
    destination: str | Path, spec: CheckpointSpec, checkpoint_sha256: str
) -> Path:
    """Atomically write a public model manifest."""

    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        model_manifest(spec, checkpoint_sha256),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload + "\n")
    try:
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _safe_member_path(member: zipfile.ZipInfo) -> PurePosixPath:
    name = member.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or (path.parts and path.parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CheckpointDeploymentError(f"unsafe checkpoint member {name!r}")
    mode = member.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise CheckpointDeploymentError(f"checkpoint member is a symlink: {name!r}")
    return path


def extract_checkpoint(
    archive: str | Path,
    destination: str | Path,
    spec: CheckpointSpec,
) -> dict[str, Any]:
    """Verify and atomically extract a checkpoint without path traversal."""

    archive_path = Path(archive).expanduser()
    destination_path = Path(destination).expanduser()
    if destination_path.exists():
        raise FileExistsError(
            f"checkpoint destination already exists: {destination_path}"
        )
    verification = verify_checkpoint(archive_path, spec)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.stage-", dir=destination_path.parent
        )
    )
    try:
        with zipfile.ZipFile(archive_path) as bundle:
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
                output = stage.joinpath(*relative.parts)
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target, length=CHUNK_SIZE)
        os.replace(stage, destination_path)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        **verification,
        "model_dir": str(destination_path),
        "extracted_bytes": extracted_bytes,
        "member_count": len(members),
    }


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
            command.add_argument("--manifest", type=Path)
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
            result = extract_checkpoint(args.archive, args.model_dir, spec)
            if args.manifest is not None:
                manifest_path = write_model_manifest(
                    args.manifest, spec, result["checkpoint_sha256"]
                )
                result["manifest"] = str(manifest_path)
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
