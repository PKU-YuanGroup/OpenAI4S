"""Shared guarded download and pinned-workspace publication for native tools."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Callable

from openai4s import webtools
from openai4s.tools.contexts import WorkspaceToolContext


def download_into_workspace(
    workspace: WorkspaceToolContext,
    url: str,
    relative: str,
    *,
    timeout: float = 60.0,
    max_bytes: int = 64 * 1024 * 1024,
    user_agent: str | None = None,
    expected_size: int | None = None,
    expected_checksum: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Publish only bytes verified in private staging, through the workspace FD."""
    webtools.validate_download_expectation(expected_size, expected_checksum, max_bytes)
    webtools.check_download_cancelled(cancelled)
    checks = {
        key: value
        for key, value in (
            ("expected_size", expected_size),
            ("expected_checksum", expected_checksum),
        )
        if value is not None
    }
    parent = workspace.secure_parent(relative, create_parents=True)
    with parent:
        download_checks = dict(checks)
        if cancelled is not None:
            download_checks["cancelled"] = cancelled
        # Network staging is daemon-private and independent of the
        # workspace namespace. Only a complete, hash-verified response
        # is copied into a sibling opened through the pinned parent FD.
        with tempfile.TemporaryDirectory(
            prefix="openai4s-download-"
        ) as private_directory:
            private_path = Path(private_directory) / "response.bin"
            result = webtools.web_download(
                url,
                private_path,
                timeout=timeout,
                max_bytes=max_bytes,
                user_agent=user_agent,
                **download_checks,
            )
            if (
                expected_size is not None
                and expected_checksum is not None
                and result.get("verified_expectation")
                != {
                    "size_bytes": checks["expected_size"],
                    "checksum": checks["expected_checksum"].lower(),
                }
            ):
                raise RuntimeError(
                    "dataset download has no matching verification receipt"
                )
            source_descriptor = os.open(
                private_path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                staged_descriptor, staged = parent.create_staged(suffix=".download")
            except BaseException:
                try:
                    os.close(source_descriptor)
                except OSError:
                    pass
                raise
            source_descriptor_open = True
            staged_descriptor_open = True
            try:
                digest = hashlib.sha256()
                copied = 0
                source = os.fdopen(source_descriptor, "rb", closefd=True)
                source_descriptor_open = False
                try:
                    sink = os.fdopen(staged_descriptor, "wb", closefd=True)
                    staged_descriptor_open = False
                except BaseException:
                    source.close()
                    raise
                with source, sink:
                    source_metadata = os.fstat(source.fileno())
                    if not stat.S_ISREG(source_metadata.st_mode):
                        raise RuntimeError(
                            "download staging source is not a regular file"
                        )
                    while True:
                        webtools.check_download_cancelled(cancelled)
                        chunk = source.read(256 * 1024)
                        webtools.check_download_cancelled(cancelled)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > max_bytes or (
                            expected_size is not None and copied > expected_size
                        ):
                            raise webtools.ResponseTooLarge(
                                "workspace copy exceeds the download budget"
                            )
                        digest.update(chunk)
                        sink.write(chunk)
                    if copied != int(result.get("bytes", -1)) or (
                        digest.hexdigest() != str(result.get("sha256") or "")
                    ):
                        raise RuntimeError(
                            "download bytes changed before workspace publication"
                        )
                    webtools.check_download_cancelled(cancelled)
                    existing = parent.target_metadata()
                    if existing is not None and stat.S_ISREG(existing.st_mode):
                        os.fchmod(sink.fileno(), stat.S_IMODE(existing.st_mode))
                webtools.check_download_cancelled(cancelled)
                parent.publish(staged)
            except BaseException:
                if source_descriptor_open:
                    try:
                        os.close(source_descriptor)
                    except OSError:
                        pass
                if staged_descriptor_open:
                    try:
                        os.close(staged_descriptor)
                    except OSError:
                        pass
                parent.discard(staged)
                raise
    return {**result, "path": parent.target_relative}
