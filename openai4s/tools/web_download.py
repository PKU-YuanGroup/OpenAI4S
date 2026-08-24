"""Fetch a URL straight into the session workspace, bounded and confined.

`web_fetch` decodes a response to text, so it is the wrong shape for a ZIP, a
tarball or a coordinate file. Without this, a bundled skill that needed one
reached for raw ``urllib`` -- and a request made that way is subject to neither
the egress allowlist nor the SSRF guard that every hop of ``webtools._http_get``
applies. The point of this tool is not convenience; it is that the bytes come
through the same door as everything else.

Two guards meet here and neither is optional. The URL side is `web_fetch`'s:
network must be enabled, the domain allowlist is checked per redirect hop, and
a hop that resolves to a private or metadata address is refused. The path side
is `write_file`'s: the destination resolves against the session workspace and a
parent-directory, absolute-path or symlink escape is rejected. A capability
that writes wherever it is told is a capability that writes outside the
session, so `webtools.web_download` deliberately does no confinement of its own
and this tool does it before calling.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import WorkspaceToolContext

#: Default ceiling on one download. Comfortably above a real spectra archive or
#: structure bundle, and far below "whatever the server decides to send". A
#: caller may lower it; `webtools` enforces whatever it is given while reading,
#: because a cap applied after the fact describes an allocation rather than
#: bounding one.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class WebDownloadTool(Tool):
    """Download one URL into the session workspace."""

    name = "web_download"
    host_method = "web_download"
    description = (
        "Download a URL to a file in the workspace. Use for binary or archive "
        "content that web_fetch cannot represent as text."
    )
    parameters = {
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "URL to download.",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Workspace-relative destination file.",
            },
            "max_bytes": {
                "type": "integer",
                "description": (
                    f"Refuse a body larger than this (default {DEFAULT_MAX_BYTES})."
                ),
            },
            "timeout": {
                "type": "number",
                "description": "Seconds to wait before giving up (default 60).",
            },
            "user_agent": {
                "type": "string",
                "description": (
                    "Override the User-Agent. Some scholarly APIs serve their "
                    "polite pool only to callers that send a contactable one."
                ),
            },
        },
        "required": ["url", "path"],
    }
    read_only = False
    writes_files = True
    needs_network = True
    # Required of every network tool, and not a formality here even though the
    # downloaded bytes never reach the model. What does reach it is the final
    # URL after redirects and the server's `Content-Type` -- both chosen by the
    # remote host, both able to carry an injection payload into the context.
    screen_untrusted_output = True
    # The permission the user is asked for is the network one: the interesting
    # question is which host this contacts, not which local filename it picks.
    # The workspace write is already fenced by `resolve` below, and cannot
    # leave the session whatever the answer.
    permission_target_key = "url"
    # Orthogonal to the permission target above, and the reason that comment is
    # not the whole story. `secret_path_key` is not about *asking*; it is the
    # hard refusal that stops any tool overwriting a credential file, and
    # `write_file` and `edit_file` both declare it while this one -- the third
    # tool in the codebase with `writes_files = True` -- did not. So
    # `write_file(".env", ...)` was refused outright and
    # `web_download(url, ".env")` was not, on the same workspace. Under the CLI
    # that workspace is the user's cwd, so it overwrote a real project `.env`;
    # and the written file is then invisible to `glob`/`grep`, which filter
    # secret basenames, so nothing afterwards shows what happened.
    secret_path_key = "path"
    side_effect_class = "workspace_write"
    resource_key_prefix = "network"
    resource_target_key = "url"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        import re

        url = str(arguments.get("url") or "")
        return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]

    def execute(self, workspace: WorkspaceToolContext, arguments: dict) -> dict:
        from openai4s import egress, webtools

        # Resolved BEFORE the request. A download that escapes the workspace
        # should fail without having contacted anything -- otherwise a rejected
        # path still leaks that the URL was reachable.
        relative = arguments.get("path", "")
        try:
            parent = workspace.secure_parent(relative, create_parents=True)
        except ValueError as error:
            return {"error": str(error)}

        try:
            with parent:
                # Network staging is daemon-private and independent of the
                # workspace namespace. Only a complete, hash-verified response
                # is copied into a sibling opened through the pinned parent FD.
                with tempfile.TemporaryDirectory(
                    prefix="openai4s-download-"
                ) as private_directory:
                    private_path = Path(private_directory) / "response.bin"
                    result = webtools.web_download(
                        str(arguments.get("url") or ""),
                        private_path,
                        timeout=float(arguments.get("timeout") or 60),
                        max_bytes=int(arguments.get("max_bytes") or DEFAULT_MAX_BYTES),
                        user_agent=arguments.get("user_agent") or None,
                    )
                    source_descriptor = os.open(
                        private_path,
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                    )
                    try:
                        staged_descriptor, staged = parent.create_staged(
                            suffix=".download"
                        )
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
                                chunk = source.read(256 * 1024)
                                if not chunk:
                                    break
                                copied += len(chunk)
                                digest.update(chunk)
                                sink.write(chunk)
                            if copied != int(result.get("bytes", -1)) or (
                                digest.hexdigest() != str(result.get("sha256") or "")
                            ):
                                raise RuntimeError(
                                    "download bytes changed before workspace publication"
                                )
                            existing = parent.target_metadata()
                            if existing is not None and stat.S_ISREG(existing.st_mode):
                                os.fchmod(sink.fileno(), stat.S_IMODE(existing.st_mode))
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
        except (
            webtools.NetworkDisabled,
            webtools.SSRFBlocked,
            webtools.ResponseTooLarge,
            egress.EgressBlocked,
        ) as error:
            return {"error": str(error)}
        except Exception as error:  # noqa: BLE001
            return {"error": f"web_download: {error}"}

        # Reported workspace-relative, like every other file-producing tool, so
        # the absolute path (which contains the data dir, and therefore $HOME)
        # never reaches the model or a stored frame.
        return {**result, "path": parent.target_relative}


__all__ = ["WebDownloadTool", "DEFAULT_MAX_BYTES"]
