"""The team file-area routes: list / download / upload (plan M1-8).

Same shape as `kernel_routes` and `team_routes`: a validated `RouteSpec`
table shared with the contract inventory, and a tri-state `handle()`.

The upload is genuinely streamed: `_prepare_request_body` skips the
whole-body pre-read for exactly this path, and the handler here copies
`rfile` to a same-directory temp file in 1 MiB chunks before an atomic
`os.replace` — a 512 MiB upload never transits daemon memory, and a
truncated one never leaves a half-written file under the final name.

Role rules (team mode): guests are read-nothing here (D3 — replay only);
members and admins may list, download, and upload. With team mode off the
routes work whenever `OPENAI4S_DATA_ROOTS` is configured, behind the same
credential gate as every other route.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from urllib.parse import quote

from . import contract
from .file_area import MAX_UPLOAD_BYTES, FileAreaError

_LIST = contract.RouteSpec("files.list", "GET", r"/files", mutates=False)
_DOWNLOAD = contract.RouteSpec(
    "files.download", "GET", r"/files/download", mutates=False
)
_UPLOAD = contract.RouteSpec("files.upload", "POST", r"/files/upload", mutates=True)

ROUTES = contract.validate_routes((_LIST, _DOWNLOAD, _UPLOAD))

_PATH_PREFIX = "/files"


def _refuse(self, error: FileAreaError) -> None:
    self._json({"error": str(error), "code": error.code}, error.status)


def _reader(self) -> str | None:
    """The member a read is scoped to, or None for admin / single-user.

    Shared space stays shared; another member's personal area does not.
    An admin sees everything, matching the rest of the team surface.
    """
    identity = getattr(self, "_team_identity", None)
    if identity is None or identity.is_admin:
        return None
    return str(identity.username)


def _guest_blocked(self, team_auth: Any) -> bool:
    if team_auth is None:
        return False
    identity = getattr(self, "_team_identity", None)
    if identity is not None and identity.role == "guest":
        self._json({"error": "guests are replay-only", "code": "guest_readonly"}, 403)
        return True
    return False


def handle(
    self, method: str, sub: str, q: dict, file_area: Any, team_auth: Any
) -> bool:
    """Answer a file-area route, or report that this group does not own it."""
    if not sub.split("?")[0].startswith(_PATH_PREFIX):
        return False
    if _DOWNLOAD.match(method, sub):
        if _guest_blocked(self, team_auth):
            return True
        raw = (q.get("path") or [""])[0]
        try:
            target = file_area.resolve_download(raw, reader=_reader(self))
        except FileAreaError as error:
            _refuse(self, error)
            return True
        self._stream_file(
            target,
            "application/octet-stream",
            extra={
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''" + quote(target.name)
                )
            },
        )
        return True
    if _UPLOAD.match(method, sub):
        if _guest_blocked(self, team_auth):
            return True
        directory = (q.get("dir") or [""])[0]
        name = (q.get("name") or [""])[0]
        overwrite = (q.get("overwrite") or ["0"])[0] in ("1", "true", "yes")
        try:
            identity = getattr(self, "_team_identity", None)
            owner = (
                identity.username
                if identity is not None and not identity.is_admin
                else None
            )
            target = file_area.resolve_upload_target(directory, name, owner=owner)
        except FileAreaError as error:
            _refuse(self, error)
            return True
        if target.exists() and not overwrite:
            self._json(
                {"error": "file exists; pass overwrite=1", "code": "file_exists"},
                409,
            )
            return True
        length = self._content_length()
        if length <= 0:
            self._json({"error": "empty upload", "code": "empty_upload"}, 400)
            return True
        if length > MAX_UPLOAD_BYTES:
            self.close_connection = True
            self._json(
                {
                    "error": f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
                    "code": "upload_too_large",
                },
                413,
            )
            return True
        written = 0
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.upload-", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as sink:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    sink.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            if written != length:
                # A truncated body must not surface as a shorter file.
                os.unlink(tmp_name)
                self.close_connection = True
                self._json(
                    {"error": "incomplete upload", "code": "incomplete_upload"}, 400
                )
                return True
            os.replace(tmp_name, target)
        except OSError as error:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            self._json(
                {"error": f"upload failed: {error}", "code": "upload_failed"}, 500
            )
            return True
        self._send(
            201,
            json.dumps(
                {"ok": True, "path": str(target), "size": written},
                ensure_ascii=False,
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )
        return True
    if _LIST.match(method, sub):
        if _guest_blocked(self, team_auth):
            return True
        raw = (q.get("path") or [""])[0]
        try:
            payload = (
                file_area.list_dir(raw, reader=_reader(self))
                if raw
                else file_area.list_roots()
            )
        except FileAreaError as error:
            _refuse(self, error)
            return True
        self._json(payload)
        return True
    return False
