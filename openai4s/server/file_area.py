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

from openai4s.config import DATA_ROOT_USERS_DIR as _USERS_DIR

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

    def __init__(
        self,
        roots: list[Path],
        *,
        policies: "list[tuple[Path, bool]] | None" = None,
    ):
        """`roots` keeps the old shape for every caller that has one.

        `policies` -- `(path, writable)` pairs -- is what a team daemon
        passes, because D8 names three kinds of root and one of them is
        read-only. A root with no policy is writable, which is what every
        root was before policies existed (INV-1).
        """
        resolved: list[Path] = []
        writable: dict[Path, bool] = {}
        for root in roots:
            try:
                path = Path(root).expanduser().resolve()
            except OSError:
                continue
            resolved.append(path)
            writable[path] = True
        for root, is_writable in policies or ():
            try:
                path = Path(root).expanduser().resolve()
            except OSError:
                continue
            if path not in writable:
                resolved.append(path)
            writable[path] = bool(is_writable)
        self.roots = resolved
        self._writable = writable

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

    def root_of(self, candidate: Path) -> Path | None:
        for root in self.roots:
            try:
                if candidate == root or root in candidate.parents:
                    return root
            except OSError:
                continue
        return None

    def is_writable_root(self, root: Path) -> bool:
        return bool(self._writable.get(root, True))

    @staticmethod
    def personal_area_owner(root: Path, candidate: Path) -> str | None:
        """Whose personal area a path is inside, or None for shared space.

        `<root>/users/<name>/...` belongs to `<name>`. A fixed namespace,
        so the question is about a path and never a guess about whether a
        directory called `alice` is a person or a dataset.
        """
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        parts = relative.parts
        if len(parts) >= 2 and parts[0] == _USERS_DIR:
            return parts[1]
        return None

    def _contained(self, candidate: Path) -> bool:
        for root in self.roots:
            try:
                if candidate == root or root in candidate.parents:
                    return True
            except OSError:
                continue
        return False

    def resolve(self, raw: str, *, reader: str | None = None) -> Path:
        """The real path behind a client-supplied one, or a refusal.

        The containment check runs on the fully resolved path — after
        symlinks and ``..`` — so no spelling of an outside path passes.

        `reader` is the requesting member, when there is one. Shared space
        -- the datasets and project areas D8 describes -- is readable by
        every member; another member's *personal* area is not. Same 404 as
        an outside path: which files exist in somebody else's scratch is
        not this API's information to give out.
        """
        self._require_configured()
        text = str(raw or "").strip()
        if not text:
            raise FileAreaError(400, "path is required", "path_required")
        try:
            candidate = Path(text).expanduser().resolve()
        except OSError as exc:
            raise FileAreaError(400, f"unresolvable path: {exc}") from exc
        root = self.root_of(candidate)
        if root is None:
            # The same sentence for "outside the roots" and "does not
            # exist": which paths exist outside the allowlist is not this
            # API's information to give out.
            raise FileAreaError(404, "path not found", "path_not_found")
        if reader:
            owner = self.personal_area_owner(root, candidate)
            if owner is not None and owner != reader:
                raise FileAreaError(404, "path not found", "path_not_found")
        return candidate

    def _scoped_upload_dir(self, directory: str, owner: str) -> str:
        """This member's subtree, created on demand.

        Computed from the identity and never from the client: a
        caller-supplied "whose directory is this" would be the same
        authorization the scoping replaces.
        """
        # The write side must name the directory the read side looks in.
        # This used to *filter* the username into a directory name while
        # `personal_area_owner` compared the raw one, so the two disagreed
        # for any name containing anything else: `alice@lab.org` wrote to
        # `users/alicelab.org/` and was then 404'd from her own uploads, and
        # a second account filtering to the same string shared her area
        # outright. `.` also survived the filter, so an owner named `..`
        # produced `<root>/users/..`, which `resolve()` collapses back to the
        # writable root -- containment passed and the upload landed beside
        # everybody else's files, the exact clobber this scoping prevents.
        #
        # No rewriting, then: a name that cannot be a directory segment is
        # refused. `storage.team.validate_username` keeps real accounts
        # inside this set, so the refusal is a backstop and not a gate
        # anybody meets.
        safe = str(owner)
        if (
            not safe
            or safe in (".", "..")
            or not all(ch.isalnum() or ch in "-_." for ch in safe)
        ):
            raise FileAreaError(400, "invalid upload owner", "invalid_owner")
        text = str(directory or "").strip()
        base: Path | None = None
        if text:
            candidate = self.resolve(text, reader=owner)
            base = self.root_of(candidate)
        if base is None:
            self._require_configured()
            # The first *writable* root, not the first root: a member with
            # no directory in mind must not land in the datasets area.
            base = next((r for r in self.roots if self.is_writable_root(r)), None)
            if base is None:
                raise FileAreaError(403, "no writable data root", "root_read_only")
        if not self.is_writable_root(base):
            # D8's read-only datasets area. Refused for everyone -- an admin
            # included -- because the point of a read-only root is that the
            # reference data every analysis reads cannot drift.
            raise FileAreaError(403, "this data root is read-only", "root_read_only")
        scoped = base / _USERS_DIR / safe
        try:
            scoped.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileAreaError(500, f"cannot create upload area: {exc}") from exc
        # A request that already targets inside this member's subtree keeps
        # its own sub-path; anything else is redirected to the subtree root.
        if text:
            candidate = self.resolve(text, reader=owner)
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
        parent_root = self.root_of(parent)
        if parent_root is not None and not self.is_writable_root(parent_root):
            raise FileAreaError(403, "this data root is read-only", "root_read_only")
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

    def list_dir(
        self, raw: str, *, limit: int = 2000, reader: str | None = None
    ) -> dict:
        target = self.resolve(raw, reader=reader)
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

    def resolve_download(self, raw: str, *, reader: str | None = None) -> Path:
        target = self.resolve(raw, reader=reader)
        if not target.is_file():
            raise FileAreaError(404, "path not found", "path_not_found")
        return target


__all__ = ["FileArea", "FileAreaError", "MAX_UPLOAD_BYTES"]
