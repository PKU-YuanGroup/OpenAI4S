"""Safe, versioned installation and rollback for writable Skills.

The immutable package bytes live in the Store.  Active personal/project
directories are materialized runtime views.  A new view is built off to the
side, verified, and swapped into place; the database activation uses compare
and-swap semantics.  If the database switch fails, the previous directory is
restored before the error escapes.

Only Python's standard library is used.  Package reads are bounded, symlinks
are rejected, and ``kernel.py`` is compile-checked but never executed here.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
import unicodedata
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from openai4s.config import Config, get_config

MAX_SKILL_FILES = 256
MAX_SKILL_FILE_BYTES = 2_000_000
MAX_SKILL_PACKAGE_BYTES = 10_000_000
_IGNORED_DIRECTORIES = frozenset({".git", ".openai4s", "__pycache__"})
_AUTO = object()
_MATERIALIZE_LOCK = threading.RLock()


def _canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("skill name is required")
    return slug[:64]


def personal_skills_root(cfg: Config) -> Path:
    return Path(cfg.data_dir) / "user-skills"


def project_skills_root(cfg: Config, project_id: str) -> Path:
    identity = str(project_id or "").strip()
    if not identity:
        raise ValueError("project skill scope requires project_id")
    if len(identity) > 512:
        raise ValueError("project_id is too long")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Path(cfg.data_dir) / "project-skills" / digest


def _safe_package_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part in _IGNORED_DIRECTORIES for part in path.parts)
        or "\x00" in raw
    ):
        raise ValueError(f"unsafe Skill package path: {value!r}")
    normalized = path.as_posix()
    if len(normalized) > 512:
        raise ValueError("Skill package path is too long")
    return normalized


class _StoreSkillVersionRepository:
    """Late-bind to the current Store generation for every operation."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _repository(self):
        from openai4s.store import get_store

        return get_store(self._db_path).skill_versions()

    def __getattr__(self, name: str):
        return getattr(self._repository(), name)


class SkillVersionService:
    """Install, upgrade, publish, inspect, and roll back writable Skills."""

    def __init__(self, cfg: Config | None = None, *, repository=None) -> None:
        self.cfg = cfg or get_config()
        self.repository = repository or _StoreSkillVersionRepository(self.cfg.db_path)

    @staticmethod
    def slug(name: str) -> str:
        return _slug(name)

    def scope_root(
        self,
        *,
        scope: str = "personal",
        project_id: str | None = None,
    ) -> Path:
        scope = str(scope or "").strip().lower()
        if scope == "personal":
            if project_id:
                raise ValueError("personal Skill scope cannot have project_id")
            return personal_skills_root(self.cfg)
        if scope == "project":
            return project_skills_root(self.cfg, str(project_id or ""))
        raise ValueError("skill scope must be 'personal' or 'project'")

    def _reject_bundled_collision(self, name: str) -> None:
        # Lazy import prevents loader initialization from creating a cycle.
        from openai4s.skills_loader.loader import SkillLoader

        collision = SkillLoader(cfg=self.cfg).bundled_name_collision(name)
        if collision is not None:
            raise PermissionError(
                f"Skill {name!r} collides with read-only bundled Skill "
                f"{collision.name!r}"
            )

    @staticmethod
    def _normalize_files(files: Mapping[str, bytes | str]) -> dict[str, bytes]:
        if not isinstance(files, Mapping):
            raise TypeError("Skill files must be a mapping")
        if not files or len(files) > MAX_SKILL_FILES:
            raise ValueError(f"Skill packages must contain 1-{MAX_SKILL_FILES} files")
        output: dict[str, bytes] = {}
        portable_paths: set[str] = set()
        total = 0
        for supplied_path, supplied_content in files.items():
            path = _safe_package_path(str(supplied_path))
            if path in output:
                raise ValueError(f"duplicate Skill package path: {path}")
            portable = unicodedata.normalize("NFC", path).casefold()
            if portable in portable_paths:
                raise ValueError(f"non-portable duplicate Skill package path: {path}")
            portable_paths.add(portable)
            if isinstance(supplied_content, str):
                content = supplied_content.encode("utf-8")
            elif isinstance(supplied_content, (bytes, bytearray, memoryview)):
                content = bytes(supplied_content)
            else:
                raise TypeError(f"Skill file {path!r} must be bytes or text")
            if len(content) > MAX_SKILL_FILE_BYTES:
                raise ValueError(f"Skill file exceeds 2MB limit: {path}")
            total += len(content)
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise ValueError("Skill package exceeds 10MB limit")
            output[path] = content
        if "SKILL.md" not in output:
            raise ValueError("Skill package must contain SKILL.md")
        return output

    @staticmethod
    def read_package(root: Path) -> dict[str, bytes]:
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("unsafe or missing Skill directory")
        resolved_root = root.resolve()
        files: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Skill packages cannot contain symlinks")
            relative = path.relative_to(root)
            if any(part in _IGNORED_DIRECTORIES for part in relative.parts):
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"unsupported Skill package entry: {relative}")
            resolved = path.resolve()
            if resolved_root not in resolved.parents:
                raise ValueError("Skill package entry escapes its directory")
            files[relative.as_posix()] = path.read_bytes()
            if len(files) > MAX_SKILL_FILES:
                raise ValueError(f"Skill package exceeds {MAX_SKILL_FILES} files")
        return SkillVersionService._normalize_files(files)

    @staticmethod
    def _document_metadata(document: bytes) -> tuple[dict, str]:
        try:
            text = document.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("SKILL.md must be valid UTF-8") from error
        from openai4s.skills_loader.loader import _parse_frontmatter

        metadata, _body = _parse_frontmatter(text)
        return metadata, text

    def _manifest(
        self,
        *,
        name: str,
        slug: str,
        files: Mapping[str, bytes],
        scope: str,
    ) -> dict:
        metadata, _document = self._document_metadata(files["SKILL.md"])
        declared_name = str(metadata.get("name") or name).strip()
        if _canonical_name(declared_name) != _canonical_name(name):
            raise ValueError(f"SKILL.md declares {declared_name!r}, expected {name!r}")
        origin = str(metadata.get("origin") or "draft").strip().lower()
        if origin not in {"draft", "personal", "user"}:
            raise PermissionError(
                "writable Skills may only declare draft, personal, or user origin"
            )
        sidecar = files.get("kernel.py")
        sidecar_sha256 = None
        sidecar_gate: dict[str, Any] = {"ok": True, "error": None}
        if sidecar is not None:
            try:
                sidecar_text = sidecar.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("kernel.py must be valid UTF-8") from error
            try:
                compile(sidecar_text, "<skill:kernel.py>", "exec")
            except SyntaxError as error:
                sidecar_gate = {
                    "ok": False,
                    "error": f"{error.__class__.__name__}: {error}",
                }
            sidecar_sha256 = hashlib.sha256(sidecar).hexdigest()
        entries = [
            {
                "path": path,
                "sha256": hashlib.sha256(files[path]).hexdigest(),
                "size": len(files[path]),
            }
            for path in sorted(files)
        ]
        return {
            "schema_version": 1,
            "name": declared_name,
            "slug": slug,
            "origin": origin,
            "distribution_scope": scope,
            "document_sha256": hashlib.sha256(files["SKILL.md"]).hexdigest(),
            "sidecar": {
                "present": sidecar is not None,
                "sha256": sidecar_sha256,
                "size": len(sidecar) if sidecar is not None else 0,
                "gate": sidecar_gate,
            },
            "files": entries,
        }

    @staticmethod
    def _write_stage(base: Path, slug: str, files: Mapping[str, bytes]) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        if base.is_symlink():
            raise ValueError("unsafe Skill scope directory")
        stage = Path(tempfile.mkdtemp(prefix=f".{slug}.stage-", dir=base))
        try:
            for relative, content in files.items():
                target = stage.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            # Verify the materialized package before it can become active.
            verified = SkillVersionService.read_package(stage)
            if dict(verified) != dict(files):
                raise OSError("materialized Skill package verification failed")
            return stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    @staticmethod
    def _swap_stage(target: Path, stage: Path) -> tuple[Path | None, Any, Any]:
        if target.is_symlink():
            raise ValueError("unsafe user Skill path")
        backup: Path | None = None
        if target.exists():
            if not target.is_dir():
                raise ValueError("unsafe user Skill path")
            backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except Exception:
            if backup is not None and backup.exists():
                os.replace(backup, target)
            raise

        def rollback() -> None:
            if target.exists():
                shutil.rmtree(target)
            if backup is not None and backup.exists():
                os.replace(backup, target)

        def finalize() -> None:
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)

        return backup, rollback, finalize

    def install(
        self,
        name: str,
        files: Mapping[str, bytes | str],
        *,
        scope: str = "personal",
        project_id: str | None = None,
        event: str = "installed",
        expected_active_version_id: str | None | object = _AUTO,
        metadata: dict | None = None,
        slug: str | None = None,
        require_sidecar_gate: bool = True,
    ) -> dict:
        """Install or upgrade a package and atomically activate its version."""

        display_name = str(name or "").strip()
        if not display_name:
            raise ValueError("skill name is required")
        self._reject_bundled_collision(display_name)
        if slug is None:
            slug = _slug(display_name)
        else:
            supplied_slug = str(slug or "").strip()
            if supplied_slug != _slug(supplied_slug):
                raise ValueError("unsafe Skill directory name")
            slug = supplied_slug
        scope = str(scope or "").strip().lower()
        scope_id = str(project_id or "") if scope == "project" else ""
        base = self.scope_root(scope=scope, project_id=project_id)
        normalized_files = self._normalize_files(files)
        manifest = self._manifest(
            name=display_name,
            slug=slug,
            files=normalized_files,
            scope=scope,
        )
        if require_sidecar_gate and not manifest["sidecar"]["gate"]["ok"]:
            raise ValueError(
                "kernel.py failed compile gate: "
                f"{manifest['sidecar']['gate']['error']}"
            )
        version = self.repository.put_version(manifest, normalized_files)
        current = self.repository.get_installation(
            display_name,
            scope=scope,
            scope_id=scope_id,
        )
        actual = current.get("active_version_id") if current else None
        slug_owner = self.repository.get_active_by_slug(
            slug,
            scope=scope,
            scope_id=scope_id,
        )
        if slug_owner is not None and _canonical_name(
            slug_owner["name"]
        ) != _canonical_name(display_name):
            raise ValueError(
                f"Skill directory {slug!r} is already active for "
                f"{slug_owner['name']!r}"
            )
        expected = (
            actual
            if expected_active_version_id is _AUTO
            else expected_active_version_id
        )
        if actual != expected:
            raise RuntimeError(
                "Skill changed concurrently: expected active version "
                f"{expected!r}, found {actual!r}"
            )
        target = base / slug
        with _MATERIALIZE_LOCK:
            stage = self._write_stage(base, slug, normalized_files)
            try:
                _backup, rollback, finalize = self._swap_stage(target, stage)
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            try:
                activation = self.repository.activate(
                    display_name,
                    slug,
                    version["version_id"],
                    scope=scope,
                    scope_id=scope_id,
                    event=event,
                    expected_active_version_id=expected,
                    metadata=metadata,
                )
            except Exception:
                rollback()
                raise
            else:
                finalize()
        return {
            "ok": True,
            "name": display_name,
            "slug": slug,
            "scope": scope,
            "scope_id": scope_id,
            "version_id": version["version_id"],
            "previous_version_id": actual,
            "manifest": version["manifest"],
            "event_id": activation["event_id"],
        }

    def install_directory(
        self,
        name: str,
        root: Path,
        **kwargs,
    ) -> dict:
        return self.install(name, self.read_package(root), **kwargs)

    def upgrade(self, name: str, files: Mapping[str, bytes | str], **kwargs) -> dict:
        return self.install(name, files, event="upgraded", **kwargs)

    def publish(
        self,
        name: str,
        *,
        scope: str = "personal",
        project_id: str | None = None,
        expected_active_version_id: str | None | object = _AUTO,
        slug: str | None = None,
    ) -> dict:
        installation = self.repository.get_installation(
            name,
            scope=scope,
            scope_id=str(project_id or "") if scope == "project" else "",
        )
        slug = installation["slug"] if installation else (slug or _slug(name))
        root = self.scope_root(scope=scope, project_id=project_id) / slug
        files = self.read_package(root)
        metadata, text = self._document_metadata(files["SKILL.md"])
        if scope == "personal":
            if text.startswith("---"):
                if re.search(r"(?m)^origin\s*:", text):
                    text = re.sub(
                        r"(?m)^origin\s*:.*$", "origin: personal", text, count=1
                    )
                else:
                    text = text.replace("---", "---\norigin: personal", 1)
            else:
                text = f"---\nname: {name}\norigin: personal\n---\n" + text
            files["SKILL.md"] = text.encode("utf-8")
        return self.install(
            name,
            files,
            scope=scope,
            project_id=project_id,
            event="published",
            expected_active_version_id=expected_active_version_id,
            metadata={"previous_origin": metadata.get("origin")},
            slug=slug,
            require_sidecar_gate=True,
        )

    def rollback(
        self,
        name: str,
        version_id: str,
        *,
        scope: str = "personal",
        project_id: str | None = None,
        expected_active_version_id: str | None | object = _AUTO,
    ) -> dict:
        scope_id = str(project_id or "") if scope == "project" else ""
        installation = self.repository.get_installation(
            name,
            scope=scope,
            scope_id=scope_id,
        )
        if installation is None:
            raise KeyError(f"no installed Skill: {name!r}")
        if not self.repository.version_belongs_to(
            name,
            version_id,
            scope=scope,
            scope_id=scope_id,
        ):
            raise PermissionError("target version does not belong to this Skill")
        version = self.repository.get_version(version_id, include_files=True)
        current = installation.get("active_version_id")
        expected = (
            current
            if expected_active_version_id is _AUTO
            else expected_active_version_id
        )
        if current != expected:
            raise RuntimeError(
                "Skill changed concurrently: expected active version "
                f"{expected!r}, found {current!r}"
            )
        target = (
            self.scope_root(scope=scope, project_id=project_id) / installation["slug"]
        )
        files = self._normalize_files(version["files"])
        with _MATERIALIZE_LOCK:
            stage = self._write_stage(target.parent, target.name, files)
            try:
                _backup, undo, finalize = self._swap_stage(target, stage)
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            try:
                activation = self.repository.activate(
                    installation["name"],
                    installation["slug"],
                    version_id,
                    scope=scope,
                    scope_id=scope_id,
                    event="rolled_back",
                    expected_active_version_id=expected,
                    metadata={"rollback_from": current},
                )
            except Exception:
                undo()
                raise
            else:
                finalize()
        return {
            "ok": True,
            "name": installation["name"],
            "scope": scope,
            "scope_id": scope_id,
            "version_id": version_id,
            "previous_version_id": current,
            "manifest": version["manifest"],
            "event_id": activation["event_id"],
        }

    def delete(
        self,
        name: str,
        *,
        scope: str = "personal",
        project_id: str | None = None,
    ) -> dict:
        scope_id = str(project_id or "") if scope == "project" else ""
        installation = self.repository.get_installation(
            name,
            scope=scope,
            scope_id=scope_id,
        )
        if installation is None or not installation.get("active_version_id"):
            raise KeyError(f"no installed Skill: {name!r}")
        target = (
            self.scope_root(scope=scope, project_id=project_id) / installation["slug"]
        )
        with _MATERIALIZE_LOCK:
            if target.is_symlink():
                raise ValueError("unsafe user Skill path")
            backup = target.with_name(f".{target.name}.delete-{uuid.uuid4().hex}")
            if target.exists():
                os.replace(target, backup)
            try:
                result = self.repository.deactivate(
                    name,
                    scope=scope,
                    scope_id=scope_id,
                    expected_active_version_id=installation["active_version_id"],
                    metadata={"version_retained": True},
                )
            except Exception:
                if backup.exists():
                    os.replace(backup, target)
                raise
            else:
                shutil.rmtree(backup, ignore_errors=True)
        return result

    def history(
        self,
        name: str,
        *,
        scope: str = "personal",
        project_id: str | None = None,
        limit: int = 200,
    ) -> dict:
        self.scope_root(scope=scope, project_id=project_id)
        result = self.repository.history(
            name,
            scope=scope,
            scope_id=str(project_id or "") if scope == "project" else "",
            limit=limit,
        )
        version_ids: list[str] = []
        for event in result["events"]:
            for key in ("to_version_id", "from_version_id"):
                version_id = event.get(key)
                if version_id and version_id not in version_ids:
                    version_ids.append(version_id)
        active = result["installation"].get("active_version_id")
        versions = []
        for version_id in version_ids:
            version = self.repository.get_version(version_id, include_files=False)
            versions.append(
                {
                    "version_id": version["version_id"],
                    "created_at": version["created_at"],
                    "active": version["version_id"] == active,
                    "manifest": version["manifest"],
                }
            )
        return {**result, "versions": versions}

    def status(
        self,
        name: str,
        *,
        scope: str = "personal",
        project_id: str | None = None,
    ) -> dict:
        """Return the active pointer and safe manifest without package bytes."""

        self.scope_root(scope=scope, project_id=project_id)
        scope_id = str(project_id or "") if scope == "project" else ""
        installation = self.repository.get_installation(
            name,
            scope=scope,
            scope_id=scope_id,
        )
        active_version_id = (
            installation.get("active_version_id") if installation is not None else None
        )
        version = (
            self.repository.get_version(active_version_id, include_files=False)
            if active_version_id
            else None
        )
        return {
            "name": installation.get("name") if installation else str(name),
            "scope": scope,
            "scope_id": scope_id,
            "installed": installation is not None,
            "active": bool(active_version_id),
            "active_version_id": active_version_id,
            "manifest": version.get("manifest") if version else None,
            "created_at": version.get("created_at") if version else None,
            "rollback_available": bool(active_version_id),
        }


__all__ = [
    "MAX_SKILL_FILES",
    "MAX_SKILL_FILE_BYTES",
    "MAX_SKILL_PACKAGE_BYTES",
    "SkillVersionService",
    "personal_skills_root",
    "project_skills_root",
]
