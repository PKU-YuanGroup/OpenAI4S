"""The team file area: allowlisted roots, traversal-safe resolution (M1-8).

`OPENAI4S_DATA_ROOTS` names the only directories the file routes may touch
(decision D8: a read-only datasets area, project workspaces, personal
scratch — whatever the operator mounts). Empty means the whole feature is
dormant and the routes answer their stable "not configured" shape.

The security core is one function: :meth:`FileArea.resolve` takes the
client-supplied path, resolves it (symlinks and ``..`` included), and
requires the result to sit under one of the resolved roots — the containment
check runs on the *resolved* path, so a symlink pointing outside a root is
refused even though its own name sits inside one. Everything else (listing,
download, upload) goes through it first.

Upload targets get one extra rule: the *parent* must resolve into a root and
the final name must be a plain filename — no separators, no dot-dot, not a
symlink hop — so an upload can create a new file without being usable to
reach outside.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Default per-file upload ceiling (plan M1-8).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class FileAreaError(Exception):
    """A refusal with an HTTP status and a stable code."""

    def __init__(self, status: int, message: str, code: str = "file_area"):
        super().__init__(message)
        self.status = status
        self.code = code


class FileArea:
    """Path policy + listing for the allowlisted roots."""

    def __init__(self, roots: list[Path]):
        resolved: list[Path] = []
        for root in roots:
            try:
                resolved.append(Path(root).expanduser().resolve())
            except OSError:
                continue
        self.roots = resolved

    @property
    def configured(self) -> bool:
        return bool(self.roots)

    # --- resolution ------------------------------------------------------

    def _require_configured(self) -> None:
        if not self.configured:
            raise FileAreaError(
                404,
                "file area is not configured (OPENAI4S_DATA_ROOTS)",
                "no_data_roots",
            )

    def _contained(self, candidate: Path) -> bool:
        for root in self.roots:
            try:
                if candidate == root or root in candidate.parents:
                    return True
            except OSError:
                continue
        return False

    def resolve(self, raw: str) -> Path:
        """The real path behind a client-supplied one, or a refusal.

        The containment check runs on the fully resolved path — after
        symlinks and ``..`` — so no spelling of an outside path passes.
        """
        self._require_configured()
        text = str(raw or "").strip()
        if not text:
            raise FileAreaError(400, "path is required", "path_required")
        try:
            candidate = Path(text).expanduser().resolve()
        except OSError as exc:
            raise FileAreaError(400, f"unresolvable path: {exc}") from exc
        if not self._contained(candidate):
            # The same sentence for "outside the roots" and "does not
            # exist": which paths exist outside the allowlist is not this
            # API's information to give out.
            raise FileAreaError(404, "path not found", "path_not_found")
        return candidate

    def _scoped_upload_dir(self, directory: str, owner: str) -> str:
        """This member's subtree, created on demand.

        Computed from the identity and never from the client: a
        caller-supplied "whose directory is this" would be the same
        authorization the scoping replaces.
        """
        safe = "".join(ch for ch in str(owner) if ch.isalnum() or ch in "-_.")
        if not safe:
            raise FileAreaError(400, "invalid upload owner", "invalid_owner")
        text = str(directory or "").strip()
        base: Path | None = None
        if text:
            candidate = self.resolve(text)
            for root in self.roots:
                if candidate == root or root in candidate.parents:
                    base = root
                    break
        if base is None:
            self._require_configured()
            base = self.roots[0]
        scoped = base / safe
        try:
            scoped.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileAreaError(500, f"cannot create upload area: {exc}") from exc
        # A request that already targets inside this member's subtree keeps
        # its own sub-path; anything else is redirected to the subtree root.
        if text:
            candidate = self.resolve(text)
            if candidate == scoped or scoped in candidate.parents:
                return str(candidate)
        return str(scoped)

    def resolve_upload_target(
        self, directory: str, name: str, *, owner: str | None = None
    ) -> Path:
        """Where an upload may land: an allowlisted dir + a plain filename.

        `owner` scopes the *writable* area in team mode. Containment inside
        the roots says where a file may live; it says nothing about whose it
        is, and every file here is written by the daemon's own uid -- so
        "is this yours?" cannot be answered after the fact and an
        `overwrite=1` was a cross-user clobber of anything a colleague had
        put in the shared area.

        Scoping by construction is the only version of this that works:
        each member writes under `<root>/<their name>/`. Reads stay shared,
        which is what the file area is for. `owner` is None for the
        single-user daemon and for an admin, and both then behave exactly as
        before (INV-1).
        """
        if owner:
            directory = self._scoped_upload_dir(directory, owner)
        parent = self.resolve(directory)
        if not parent.is_dir():
            raise FileAreaError(404, "upload directory not found", "path_not_found")
        clean = str(name or "").strip()
        if (
            not clean
            or clean in (".", "..")
            or "/" in clean
            or "\\" in clean
            or "\x00" in clean
        ):
            raise FileAreaError(400, "invalid filename", "invalid_filename")
        target = parent / clean
        # The name itself must not be a symlink hop out of the root.
        if target.is_symlink():
            raise FileAreaError(400, "target is a symlink", "invalid_filename")
        return target

    # --- listing ---------------------------------------------------------

    def list_roots(self) -> dict:
        self._require_configured()
        roots = []
        for root in self.roots:
            try:
                exists = root.is_dir()
            except OSError:
                exists = False
            roots.append({"path": str(root), "exists": exists})
        return {"roots": roots}

    def list_dir(self, raw: str, *, limit: int = 2000) -> dict:
        target = self.resolve(raw)
        if not target.exists():
            raise FileAreaError(404, "path not found", "path_not_found")
        if not target.is_dir():
            raise FileAreaError(400, "not a directory", "not_a_directory")
        entries = []
        truncated = False
        try:
            with os.scandir(target) as it:
                for entry in it:
                    if len(entries) >= limit:
                        truncated = True
                        break
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        entries.append(
                            {
                                "name": entry.name,
                                "dir": entry.is_dir(follow_symlinks=False),
                                "size": int(stat.st_size),
                                "mtime": int(stat.st_mtime),
                            }
                        )
                    except OSError:
                        continue
        except OSError as exc:
            raise FileAreaError(400, f"cannot list directory: {exc}") from exc
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {
            "path": str(target),
            "entries": entries,
            "truncated": truncated,
        }

    def resolve_download(self, raw: str) -> Path:
        target = self.resolve(raw)
        if not target.is_file():
            raise FileAreaError(404, "path not found", "path_not_found")
        return target


__all__ = ["FileArea", "FileAreaError", "MAX_UPLOAD_BYTES"]
