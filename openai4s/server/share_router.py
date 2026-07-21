"""Read-only public request handler for one web share.

Two — and only two — read roots exist: the in-memory viewer asset set loaded
once at construction, and the current immutable snapshot directory resolved via a
SnapshotLease.  The handler never touches the Store (beyond the lease status
check), the kernel, the dispatcher, subprocesses, or any gateway route.  Every
response is GET/HEAD only, carries strict security headers, and an unknown /
revoked / offline share returns one byte-identical 404.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openai4s.server.share_service import ShareService

_SHA256 = re.compile(r"^[a-f0-9]{64}$")

# Artifact content types the viewer may render inline; everything else (HTML,
# SVG, JSON, unknown) is forced to an attachment download in v1.
_INLINE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
        "text/csv",
    }
)

_STATIC_WHITELIST = frozenset(
    {
        "share.js",
        "share.css",
        "md_renderer.js",
        "scientific_renderers.js",
        "vendor/3Dmol-min.js",
    }
)

_VIEWER_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; worker-src blob:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_NOT_FOUND_BODY = b"This share is unavailable.\n"
_STREAM_CHUNK = 1 << 20


class ShareRouter:
    def __init__(self, service: ShareService, assets: dict[str, bytes]) -> None:
        self.service = service
        self.assets = dict(assets)

    # ------------------------------------------------------------------ entry
    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "GET").upper()
        if method not in ("GET", "HEAD"):
            return self._error(405, "method not allowed")
        share_id = str(request.get("share_id") or "")
        path = str(request.get("path") or "/")
        # Every path is gated behind a valid ready share so that unknown /
        # revoked / offline all collapse to one 404 with no oracle.
        acquired = self.service.acquire(share_id) if share_id else None
        if acquired is None:
            return self._not_found()
        snapshot_id, snap_dir = acquired

        def release():
            return self.service.release(share_id, snapshot_id)

        try:
            response = self._route(method, path, request, snap_dir)
        except Exception:
            release()
            raise
        body = response.get("body")
        if isinstance(body, _LeasedStream):
            body.on_close = release
            return response
        release()
        return response

    # ------------------------------------------------------------------ routes
    def _route(
        self, method: str, path: str, request: dict[str, Any], snap_dir: Path
    ) -> dict[str, Any]:
        if path == "/":
            return self._asset_response(
                method, "share.html", ctype="text/html; charset=utf-8", csp=True
            )
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            if name not in _STATIC_WHITELIST:
                return self._not_found()
            return self._asset_response(method, name, ctype=_asset_ctype(name))
        if path == "/api/meta":
            return self._snapshot_file(
                method, snap_dir, "meta.json", "application/json; charset=utf-8"
            )
        if path == "/api/view":
            return self._snapshot_file(
                method, snap_dir, "view.json", "application/json; charset=utf-8"
            )
        if path == "/bundle":
            return self._bundle(method, request, snap_dir)
        m = re.fullmatch(r"/api/artifacts/([^/]+)", path)
        if m:
            return self._artifact(method, request, snap_dir, m.group(1))
        return self._not_found()

    def _asset_response(
        self, method: str, name: str, *, ctype: str, csp: bool = False
    ) -> dict[str, Any]:
        data = self.assets.get(name)
        if data is None:
            return self._not_found()
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = ctype
        headers["Content-Length"] = str(len(data))
        if csp:
            headers["Content-Security-Policy"] = _VIEWER_CSP
            headers["Cache-Control"] = "no-store"
        else:
            headers["Cache-Control"] = "public, max-age=3600"
        return {
            "status": 200,
            "headers": headers,
            "body": None if method == "HEAD" else data,
        }

    def _snapshot_file(
        self, method: str, snap_dir: Path, name: str, ctype: str
    ) -> dict[str, Any]:
        target = self._safe_join(snap_dir, name)
        if target is None or not target.is_file():
            return self._not_found()
        data = target.read_bytes()
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = ctype
        headers["Content-Length"] = str(len(data))
        headers["Cache-Control"] = "no-store"
        return {
            "status": 200,
            "headers": headers,
            "body": None if method == "HEAD" else data,
        }

    def _artifact(
        self, method: str, request: dict[str, Any], snap_dir: Path, sha: str
    ) -> dict[str, Any]:
        if not _SHA256.match(sha):
            return self._not_found()
        target = self._safe_join(snap_dir / "artifacts", sha)
        if target is None or not target.is_file():
            return self._not_found()
        ctype, filename = self._artifact_meta(snap_dir, sha)
        inline = ctype in _INLINE_TYPES
        disposition = "inline" if inline else "attachment"
        served_type = ctype if inline else "application/octet-stream"
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = served_type
        headers["ETag"] = f'"{sha}"'
        headers["Accept-Ranges"] = "bytes"
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or sha) or sha
        headers["Content-Disposition"] = f'{disposition}; filename="{safe_name}"'
        return self._file_body(method, request, target, headers)

    def _bundle(
        self, method: str, request: dict[str, Any], snap_dir: Path
    ) -> dict[str, Any]:
        target = self._safe_join(snap_dir, "bundle.zip")
        if target is None or not target.is_file():
            return self._not_found()
        sha = ""
        meta = self._read_json(snap_dir / "meta.json")
        if meta:
            sha = str((meta.get("bundle") or {}).get("sha256") or "")
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = "application/vnd.openai4s.session+zip"
        headers["Accept-Ranges"] = "bytes"
        headers["Cache-Control"] = "no-store"
        headers[
            "Content-Disposition"
        ] = 'attachment; filename="session.openai4s-session.zip"'
        if sha:
            headers["X-Content-SHA256"] = sha
        return self._file_body(method, request, target, headers)

    # ------------------------------------------------------------------ bytes
    def _file_body(
        self,
        method: str,
        request: dict[str, Any],
        target: Path,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        size = target.stat().st_size
        rng = self._parse_range(request, size)
        if rng is None and str((request.get("headers") or {}).get("range", "")).strip():
            # A malformed / unsatisfiable range.
            headers = dict(headers)
            headers["Content-Range"] = f"bytes */{size}"
            headers["Content-Length"] = "0"
            return {"status": 416, "headers": headers, "body": None}
        if rng is not None:
            start, end = rng
            length = end - start + 1
            headers = dict(headers)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(length)
            if method == "HEAD":
                return {"status": 206, "headers": headers, "body": None}
            return {
                "status": 206,
                "headers": headers,
                "body": _LeasedStream(target, start, length),
            }
        headers = dict(headers)
        headers["Content-Length"] = str(size)
        if method == "HEAD":
            return {"status": 200, "headers": headers, "body": None}
        return {
            "status": 200,
            "headers": headers,
            "body": _LeasedStream(target, 0, size),
        }

    @staticmethod
    def _parse_range(request: dict[str, Any], size: int) -> tuple[int, int] | None:
        raw = str((request.get("headers") or {}).get("range", "")).strip()
        if not raw:
            return None
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", raw)
        if not m or size == 0:
            return None
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            return None
        if start_s == "":
            suffix = int(end_s)
            if suffix == 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        if start > end or start >= size:
            return None
        return start, min(end, size - 1)

    # ------------------------------------------------------------------ helpers
    def _artifact_meta(self, snap_dir: Path, sha: str) -> tuple[str, str]:
        view = self._read_json(snap_dir / "view.json") or {}
        for artifact in view.get("artifacts") or ():
            if str(artifact.get("sha256")) == sha:
                return (
                    str(artifact.get("content_type") or "application/octet-stream"),
                    str(artifact.get("filename") or sha),
                )
        return "application/octet-stream", sha

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_bytes())
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _safe_join(base: Path, name: str) -> Path | None:
        base = base.resolve()
        candidate = (base / name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate

    def _not_found(self) -> dict[str, Any]:
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = "text/plain; charset=utf-8"
        headers["Content-Length"] = str(len(_NOT_FOUND_BODY))
        headers["Cache-Control"] = "no-store"
        return {"status": 404, "headers": headers, "body": _NOT_FOUND_BODY}

    def _error(self, status: int, message: str) -> dict[str, Any]:
        body = (message + "\n").encode("utf-8")
        headers = dict(_SECURITY_HEADERS)
        headers["Content-Type"] = "text/plain; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "no-store"
        return {"status": status, "headers": headers, "body": body}


class _LeasedStream:
    """A file byte range iterator that releases its SnapshotLease when done."""

    def __init__(self, path: Path, start: int, length: int) -> None:
        self._path = path
        self._start = start
        self._length = length
        self.on_close = None  # set by ShareRouter.handle

    def __iter__(self) -> Iterator[bytes]:
        remaining = self._length
        try:
            with open(self._path, "rb") as handle:
                handle.seek(self._start)
                while remaining > 0:
                    chunk = handle.read(min(_STREAM_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        finally:
            if self.on_close is not None:
                self.on_close()


def _asset_ctype(name: str) -> str:
    if name.endswith(".js"):
        return "text/javascript; charset=utf-8"
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    return "application/octet-stream"


__all__ = ["ShareRouter"]
