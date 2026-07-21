"""Deterministic, untrusted-safe scientific Session packages.

The package is deliberately a data interchange boundary, not a database dump.
It contains the canonical durable projections needed to inspect and recover a
session, but never settings, connector launch configuration, credentials,
pending approval payloads, daemon ownership, or a live Kernel namespace.

Import validates the entire archive before creating anything.  It then creates
new project/session identities, remaps cross-record references, restores only
safe regular files into the new private workspace, and leaves an ended Kernel
generation as an explicit view-only/recovery boundary.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import threading
import unicodedata
import uuid
import zipfile
import zlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from openai4s.storage.snapshots import WorkspaceCAS

PACKAGE_FORMAT = "openai4s.session"
PACKAGE_SCHEMA_VERSION = 1

MAX_ARCHIVE_BYTES = 128 << 20
MAX_UNCOMPRESSED_BYTES = 256 << 20
MAX_ENTRY_BYTES = 64 << 20
MAX_ENTRIES = 4096
MAX_COMPRESSION_RATIO = 200

_RECORD_LIMITS = {
    "messages": 50_000,
    "groups": 50_000,
    "execution_attempts": 100_000,
    "cells": 25_000,
    "branches": 512,
    "checkpoints": 25_000,
    "checkpoint_states": 25_000,
    "operations": 25_000,
    "recovery_journal": 100_000,
    "artifacts": 10_000,
    "artifact_versions": 50_000,
    "environment_snapshots": 50_000,
    "workspace_entries": 100_000,
    "generations": 25_000,
    "lineage_edges": 100_000,
    "plans": 5_000,
    "annotations": 25_000,
    "review_steps": 25_000,
    "memories": 25_000,
    "permission_rules": 25_000,
    "capability_states": 25_000,
}

_REQUIRED_JSON = (
    "session.json",
    "ledger.json",
    "notebook.json",
    "snapshots.json",
    "artifacts.json",
    "environment.json",
    "lineage.json",
    "plans.json",
    "review.json",
    "memory.json",
    "permissions.json",
    "capabilities.json",
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "service-account.json",
        "service_account.json",
    }
)
_SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
    r"credential|private[_-]?key)$",
    re.IGNORECASE,
)
_ENV_SECRET = re.compile(
    r"(?im)\b([A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|"
    r"PRIVATE_KEY)[A-Za-z0-9_]*)\s*=\s*(\"[^\"\n]*\"|'[^'\n]*'|[^\s#;]+)"
)
_JSON_SECRET = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|"
    r"passwd|secret|credential|private[_-]?key)[\"']?\s*:\s*)"
    r"(\"[^\"\n]*\"|'[^'\n]*'|[^,}\]\s]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_SECRET = re.compile(
    r"(?i)\b(?:sk|ark|ghp|gho|github_pat|hf|xox[baprs])[-_]" r"[A-Za-z0-9._-]{8,}\b"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?" r"-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_REDACTED = "[REDACTED]"
IMPORT_QUARANTINE_SETTING_PREFIX = "session:import-quarantine:"


class SessionPackageError(ValueError):
    """A Session package is malformed, unsafe, unsupported, or incomplete."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_text(value: Any) -> str:
    text = str(value or "")
    text = _PRIVATE_KEY_BLOCK.sub(_REDACTED, text)
    text = _BEARER_SECRET.sub(f"Bearer {_REDACTED}", text)
    text = _TOKEN_SECRET.sub(_REDACTED, text)
    text = _ENV_SECRET.sub(lambda match: f"{match.group(1)}={_REDACTED}", text)
    text = _JSON_SECRET.sub(lambda match: f'{match.group(1)}"{_REDACTED}"', text)
    return text


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 48:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _SECRET_KEY.search(key):
                output[key] = _REDACTED
            else:
                output[key] = _sanitize(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(value)


def _assert_secret_free(value: Any, *, path: str = "payload", depth: int = 0) -> None:
    if depth > 64:
        raise SessionPackageError(f"{path} is nested too deeply")
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if _SECRET_KEY.search(key):
                allowed = item is None or item is False
                if isinstance(item, str):
                    allowed = item in {"", _REDACTED, "<redacted>"}
                if not allowed:
                    raise SessionPackageError(
                        f"secret value is forbidden at {path}.{key}"
                    )
            _assert_secret_free(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_secret_free(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, str) and _safe_text(value) != value:
        raise SessionPackageError(f"secret-looking plaintext is forbidden at {path}")


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SessionPackageError("package path must be a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SessionPackageError(f"unsafe package path: {value!r}")
    return path.as_posix()


def _is_secret_path(value: str) -> bool:
    try:
        path = PurePosixPath(_safe_relative(value))
    except SessionPackageError:
        return True
    return any(
        part.lower() in _SECRET_NAMES or part.lower().startswith(".env.")
        for part in path.parts
    ) or (path.suffix.lower() in _SECRET_SUFFIXES)


def _safe_artifact_filename(value: Any) -> str:
    if not isinstance(value, str):
        raise SessionPackageError("artifact filename must be a string")
    filename = value.strip()
    if (
        not filename
        or filename != value
        or len(filename.encode("utf-8")) > 255
        or filename in {".", ".."}
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
        or PurePosixPath(filename).name != filename
        or _is_secret_path(filename)
    ):
        raise SessionPackageError("secret or unsafe artifact filename")
    return filename


def _secret_text_bytes(data: bytes) -> bool:
    # Entries are already bounded to MAX_ENTRY_BYTES. Decode lossily so binary,
    # NUL-containing, non-UTF-8, and large files cannot bypass ASCII credential
    # signatures; random scientific bytes remain exportable unless they contain
    # an actual high-confidence secret marker.
    text = data.decode("utf-8", errors="ignore")
    return _safe_text(text) != text


def session_import_quarantine_key(root_frame_id: str) -> str:
    return IMPORT_QUARANTINE_SETTING_PREFIX + str(root_frame_id)


def _import_permission_decision(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "ask")).strip().casefold()
    # Imported grants are never trusted. Unknown spellings also fail closed.
    return normalized if normalized in {"ask", "deny"} else "ask"


def _zip_bytes(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            preview = zlib.compress(data) if data else b""
            info.compress_type = (
                zipfile.ZIP_STORED
                if data and len(data) / max(1, len(preview)) > MAX_COMPRESSION_RATIO / 2
                else zipfile.ZIP_DEFLATED
            )
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    return output.getvalue()


class SessionPackageService:
    """Export and import a versioned scientific Session archive."""

    def __init__(
        self,
        store: Any,
        *,
        data_dir: str | Path,
        workspace: Callable[[str, str], str | Path],
        cas: WorkspaceCAS,
    ) -> None:
        self.store = store
        self.data_dir = Path(data_dir).expanduser().resolve()
        self._workspace = workspace
        self.cas = cas
        self._import_lock = threading.Lock()
        self._injection_flags = 0

    def _known_secret_bytes(self) -> tuple[bytes, ...]:
        """Return configured secret values without ever serializing them."""

        values: set[str] = set()

        def collect(value: Any, *, secret_key: bool = False) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    collect(item, secret_key=bool(_SECRET_KEY.search(str(key))))
            elif isinstance(value, list):
                for item in value:
                    collect(item, secret_key=secret_key)
            elif secret_key and isinstance(value, str) and len(value) >= 8:
                values.add(value)

        for name, value in os.environ.items():
            if _SECRET_KEY.search(name) and len(value) >= 8:
                values.add(value)
        for setting in ("llm_api_key", "model_profiles"):
            raw = self.store.get_setting(setting)
            if not raw:
                continue
            if setting == "llm_api_key" and len(raw) >= 8:
                values.add(raw)
                continue
            try:
                collect(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return tuple(
            sorted(
                (value.encode("utf-8") for value in values),
                key=lambda value: (-len(value), value),
            )
        )

    def _contains_secret_bytes(self, data: bytes) -> bool:
        if _secret_text_bytes(data):
            return True
        return any(secret in data for secret in self._known_secret_bytes())

    def _scan_untrusted_text(self, text: Any) -> str:
        """Annotate imported text that trips the static injection detector.

        Imported messages/cells are untrusted third-party content.  Prepending a
        loud banner is a human/model *hint*, not a guarantee — the real boundary
        remains quarantine + never-replay.  Because the annotated text is stored,
        it also reaches the model when ``restore_action_history`` rebuilds the
        provider history after a fresh restart.
        """

        value = str(text or "")
        if not value.strip():
            return value
        try:
            from openai4s.security.injection import scan_tool_result

            verdict = scan_tool_result(value, use_llm=False)
        except Exception:  # noqa: BLE001 - screening must never break an import
            return value
        if verdict.injected:
            self._injection_flags += 1
            return verdict.annotate(value)
        return value

    @staticmethod
    def _bounded_records(name: str, records: list[Any]) -> list[Any]:
        limit = _RECORD_LIMITS[name]
        if len(records) > limit:
            raise SessionPackageError(
                f"session has too many {name.replace('_', ' ')} to package safely"
            )
        return records

    # ------------------------------------------------------------------ export
    def export(self, root_frame_id: str) -> dict[str, Any]:
        frame = self.store.get_frame(root_frame_id)
        if frame is None:
            raise KeyError(f"unknown session {root_frame_id!r}")
        if (frame.get("root_frame_id") or root_frame_id) != root_frame_id:
            raise SessionPackageError("session export requires a root frame")
        project_id = str(frame.get("project_id") or "default")
        project = self.store.get_project(project_id) or {}
        active_branch = self.store.active_session_branch(root_frame_id)
        branches = self._bounded_records(
            "branches", self.store.list_session_branches(root_frame_id)
        )
        branch_ids = [str(item["branch_id"]) for item in branches]
        if root_frame_id not in branch_ids:
            branches.append(
                {
                    "branch_id": root_frame_id,
                    "root_frame_id": root_frame_id,
                    "parent_branch_id": None,
                    "base_checkpoint_id": None,
                    "head_checkpoint_id": None,
                    "name": None,
                    "created_at": frame.get("created_at"),
                    "updated_at": frame.get("updated_at") or frame.get("created_at"),
                }
            )
            branch_ids.insert(0, root_frame_id)
            self._bounded_records("branches", branches)
        if active_branch not in branch_ids:
            raise SessionPackageError("active session branch metadata is incomplete")

        messages = self._export_messages(root_frame_id)
        cells = self._export_cells(root_frame_id)
        groups: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        seen_groups: set[str] = set()
        seen_attempts: set[str] = set()
        for branch_id in branch_ids:
            for group in self.store.list_action_groups(
                root_frame_id, branch_id=branch_id
            ):
                group_id = str(group.get("group_id") or "")
                if group_id and group_id not in seen_groups:
                    seen_groups.add(group_id)
                    groups.append(self._safe_group(group))
            for attempt in self.store.list_execution_attempts(
                root_frame_id=root_frame_id, branch_id=branch_id
            ):
                attempt_id = str(attempt.get("attempt_id") or "")
                if attempt_id and attempt_id not in seen_attempts:
                    seen_attempts.add(attempt_id)
                    attempts.append(_sanitize(attempt))
        groups.sort(key=lambda item: (item["branch_id"], item["ordinal"]))
        attempts.sort(
            key=lambda item: (
                str(item.get("group_id") or ""),
                int(item.get("attempt_ordinal") or 0),
            )
        )
        self._bounded_records("groups", groups)
        self._bounded_records("execution_attempts", attempts)

        checkpoints = self._bounded_records(
            "checkpoints",
            self.store.list_session_checkpoints(
                root_frame_id, limit=_RECORD_LIMITS["checkpoints"] + 1
            ),
        )
        checkpoint_states = self._bounded_records(
            "checkpoint_states",
            [
                state
                for checkpoint in checkpoints
                if (
                    state := self.store.get_checkpoint_state_snapshot(
                        str(checkpoint.get("checkpoint_id") or ""),
                        include_state=True,
                    )
                )
                is not None
            ],
        )
        operations = self._bounded_records(
            "operations",
            self.store.list_snapshot_operations(
                root_frame_id, limit=_RECORD_LIMITS["operations"] + 1
            ),
        )
        recovery = self._bounded_records(
            "recovery_journal",
            self.store.list_recovery_events(
                root_frame_id=root_frame_id,
                limit=_RECORD_LIMITS["recovery_journal"] + 1,
            ),
        )
        workspace_files, workspace_projection = self._export_workspace(
            root_frame_id,
            active_branch=active_branch,
            checkpoints=checkpoints,
        )
        (
            artifact_files,
            artifact_projection,
            environment_snapshots,
        ) = self._export_artifacts(root_frame_id)
        safe_artifact_ids = {
            str(item.get("artifact_id"))
            for item in artifact_projection.get("artifacts") or []
            if item.get("artifact_id")
        }

        generations = self._bounded_records(
            "generations", self.store.list_kernel_generations(root_frame_id)
        )
        environment = {
            "generations": [_sanitize(item) for item in generations],
            "artifact_environment_snapshots": environment_snapshots,
        }
        permission_state = self.store.list_permission_rules_for_frame(
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        permissions = {
            "policy": "imported allow rules are downgraded to ask",
            "project": _sanitize(permission_state.get("project") or []),
            "conversation": _sanitize(permission_state.get("conversation") or []),
        }
        self._bounded_records("permission_rules", permissions["project"])
        if (
            len(permissions["project"]) + len(permissions["conversation"])
            > _RECORD_LIMITS["permission_rules"]
        ):
            raise SessionPackageError(
                "session has too many permission rules to package safely"
            )
        capability_rows = [
            _sanitize(item)
            for item in self.store.list_explicit_capability_states()
            if (item.get("scope") == "project" and item.get("scope_id") == project_id)
            or (
                item.get("scope") == "session" and item.get("scope_id") == root_frame_id
            )
        ]
        self._bounded_records("capability_states", capability_rows)
        lineage_edges = self._bounded_records(
            "lineage_edges", self._export_lineage(artifact_projection)
        )
        plans = self._bounded_records(
            "plans",
            self.store.list_plans(root_frame_id, limit=_RECORD_LIMITS["plans"] + 1),
        )
        plans = [
            {
                **item,
                "artifact_id": (
                    item.get("artifact_id")
                    if not item.get("artifact_id")
                    or str(item.get("artifact_id")) in safe_artifact_ids
                    else None
                ),
            }
            for item in plans
        ]
        annotations = self._bounded_records(
            "annotations", self.store.list_annotations(root_frame_id)
        )
        annotations = [
            item
            for item in annotations
            if str(item.get("artifact_id") or "") in safe_artifact_ids
        ]
        memories = self._bounded_records(
            "memories", self.store.list_memories(project_id=project_id)
        )
        step_count = int(self.store.step_count(root_frame_id) or 0)
        review_steps = self._bounded_records(
            "review_steps",
            [
                item
                for item in self.store.list_steps(
                    root_frame_id, limit=max(1, step_count + 1)
                )
                if str(item.get("kind") or "").casefold()
                in {"review", "review_settings"}
            ],
        )
        local_review_auto = self.store.get_setting(f"review:auto:{root_frame_id}")
        local_reviewer_model = self.store.get_setting(f"review:model:{root_frame_id}")
        review_settings = {
            "auto_review": (
                None
                if local_review_auto is None
                else str(local_review_auto).casefold() in {"1", "true", "yes", "on"}
            ),
            "reviewer_model": (
                None
                if local_reviewer_model in (None, "", "__agent__")
                else _safe_text(local_reviewer_model)
            ),
            "active_on_import": False,
        }

        files: dict[str, bytes] = {
            "session.json": _canonical_json(
                {
                    "schema_version": PACKAGE_SCHEMA_VERSION,
                    "source": {
                        "project_id": project_id,
                        "root_frame_id": root_frame_id,
                        "active_branch_id": active_branch,
                    },
                    "project": {
                        "name": _safe_text(project.get("name") or "Imported research"),
                        "description": _safe_text(project.get("description") or ""),
                    },
                    "frame": {
                        key: _sanitize(frame.get(key))
                        for key in (
                            "name",
                            "task_summary",
                            "model",
                            "effort",
                            "runtime_env",
                            "created_at",
                            "updated_at",
                        )
                    },
                    "messages": messages,
                }
            ),
            "ledger.json": _canonical_json(
                {"groups": groups, "execution_attempts": attempts}
            ),
            "notebook.json": _canonical_json({"cells": cells}),
            "snapshots.json": _canonical_json(
                {
                    "branches": _sanitize(branches),
                    "checkpoints": _sanitize(checkpoints),
                    "checkpoint_states": _sanitize(checkpoint_states),
                    "operations": _sanitize(operations),
                    "recovery_journal": _sanitize(recovery),
                    "workspace": workspace_projection,
                }
            ),
            "artifacts.json": _canonical_json(artifact_projection),
            "environment.json": _canonical_json(environment),
            "lineage.json": _canonical_json({"edges": lineage_edges}),
            "plans.json": _canonical_json({"plans": _sanitize(plans)}),
            "review.json": _canonical_json(
                {
                    "annotations": _sanitize(annotations),
                    "activity_steps": _sanitize(review_steps),
                    "settings": review_settings,
                }
            ),
            "memory.json": _canonical_json({"memories": _sanitize(memories)}),
            "permissions.json": _canonical_json(permissions),
            "capabilities.json": _canonical_json({"states": capability_rows}),
            **workspace_files,
            **artifact_files,
        }
        for name, payload in files.items():
            if self._contains_secret_bytes(payload):
                raise SessionPackageError(
                    f"session package export still contains secret material: {name}"
                )
        manifest_body = {
            "format": PACKAGE_FORMAT,
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "files": [
                {"path": name, "size": len(data), "sha256": _sha256(data)}
                for name, data in sorted(files.items())
            ],
        }
        manifest = {
            **manifest_body,
            "manifest_sha256": _sha256(_canonical_json(manifest_body)),
        }
        files["manifest.json"] = _canonical_json(manifest)
        if sum(len(payload) for payload in files.values()) > MAX_UNCOMPRESSED_BYTES:
            raise SessionPackageError("session package expands beyond its limit")
        data = _zip_bytes(files)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise SessionPackageError("session package archive exceeds its limit")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", root_frame_id).strip("-")
        return {
            "filename": f"{stem or 'session'}.openai4s-session.zip",
            "content_type": "application/vnd.openai4s.session+zip",
            "data": data,
            "size_bytes": len(data),
            "sha256": _sha256(data),
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "immutable": True,
        }

    def _export_messages(self, root_frame_id: str) -> list[dict[str, Any]]:
        count = int(self.store.message_count(root_frame_id) or 0)
        if count > _RECORD_LIMITS["messages"]:
            raise SessionPackageError("session has too many messages to package safely")
        messages = self.store.list_messages(root_frame_id, limit=max(1, count + 1))
        boundaries = self.store.list_message_boundaries(
            root_frame_id, limit=max(1, count + 1)
        )
        output: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            boundary = boundaries[index] if index < len(boundaries) else {}
            metadata = message.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, ValueError):
                    metadata = None
            output.append(
                {
                    "message_id": boundary.get("message_id"),
                    "branch_id": boundary.get("branch_id") or root_frame_id,
                    "seq": message.get("seq", index),
                    "role": str(message.get("role") or "assistant"),
                    "content": _safe_text(message.get("content") or ""),
                    "metadata": _sanitize(metadata),
                    "created_at": message.get("created_at"),
                }
            )
        return self._bounded_records("messages", output)

    def _export_cells(self, root_frame_id: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        summaries = self._bounded_records("cells", self.store.list_cells(root_frame_id))
        for summary in summaries:
            cell_id = str(summary.get("producing_cell_id") or "")
            cell = self.store.cell_detail(cell_id) or summary
            safe = _sanitize(cell)
            safe.pop("project_id", None)
            safe.pop("root_frame_id", None)
            safe.pop("frame_id", None)
            output.append(safe)
        output.sort(
            key=lambda item: (
                int(item.get("state_revision") or item.get("cell_index") or 0),
                str(item.get("producing_cell_id") or ""),
            )
        )
        return self._bounded_records("cells", output)

    @staticmethod
    def _safe_group(group: Mapping[str, Any]) -> dict[str, Any]:
        events = []
        for event in group.get("events") or []:
            events.append(
                {
                    key: _sanitize(event.get(key))
                    for key in (
                        "event_id",
                        "sequence",
                        "type",
                        "action_id",
                        "tool_call_id",
                        "wire_id",
                        "canonical_arguments",
                        "raw_arguments",
                        "result",
                        "side_effect_class",
                        "resource_keys",
                        "created_at",
                    )
                }
            )
        return {
            key: _sanitize(group.get(key))
            for key in (
                "group_id",
                "root_frame_id",
                "branch_id",
                "turn_id",
                "ordinal",
                "kind",
                "provider",
                "model",
                "wire_state",
                "assistant_content",
                "assistant_message",
                "usage",
                "cost_usd",
                "created_at",
            )
        } | {"events": events}

    def _export_workspace(
        self,
        root_frame_id: str,
        *,
        active_branch: str,
        checkpoints: list[dict[str, Any]],
    ) -> tuple[dict[str, bytes], dict[str, Any]]:
        source_ids = {
            str(item["workspace_tree_id"])
            for item in checkpoints
            if item.get("workspace_tree_id")
        }
        active_workspace = (
            Path(self._workspace(root_frame_id, active_branch)).expanduser().resolve()
        )
        active_workspace.mkdir(parents=True, exist_ok=True)
        current = self.cas.capture(active_workspace)
        source_ids.add(str(current["tree_id"]))
        files: dict[str, bytes] = {}
        tree_map: dict[str, str] = {}
        trees: list[dict[str, Any]] = []
        for source_id in sorted(source_ids):
            tree = self.cas.get_tree(source_id)
            entries = []
            skipped = list(tree.get("skipped") or [])
            for entry in tree.get("entries") or []:
                relative = _safe_relative(str(entry.get("path") or ""))
                data = self.cas.get_blob(str(entry.get("blob") or ""))
                if _is_secret_path(relative) or self._contains_secret_bytes(data):
                    skipped.append(
                        {"path": relative, "reason": "session_package_secret_filter"}
                    )
                    continue
                digest = _sha256(data)
                if digest != entry.get("blob"):
                    raise SessionPackageError("workspace CAS blob checksum mismatch")
                files[f"workspace/blobs/{digest}"] = data
                entries.append(
                    {
                        "path": relative,
                        "blob": digest,
                        "size": len(data),
                        "mode": int(entry.get("mode") or 0o600) & 0o777,
                    }
                )
            entries.sort(key=lambda item: item["path"])
            skipped = _sanitize(skipped)
            skipped.sort(
                key=lambda item: (item.get("path", ""), item.get("reason", ""))
            )
            body = {"version": 1, "entries": entries, "skipped": skipped}
            safe_tree_id = _sha256(_canonical_json(body))
            safe_tree = {**body, "tree_id": safe_tree_id}
            files[f"workspace/trees/{safe_tree_id}.json"] = _canonical_json(safe_tree)
            tree_map[source_id] = safe_tree_id
            trees.append({"source_tree_id": source_id, "tree_id": safe_tree_id})
        return files, {
            "active_branch_id": active_branch,
            "active_source_tree_id": str(current["tree_id"]),
            "tree_map": tree_map,
            "trees": trees,
        }

    def _export_artifacts(
        self, root_frame_id: str
    ) -> tuple[dict[str, bytes], dict[str, Any], list[dict[str, Any]]]:
        files: dict[str, bytes] = {}
        artifacts: list[dict[str, Any]] = []
        env_by_id: dict[str, dict[str, Any]] = {}
        excluded: list[dict[str, str]] = []
        source_artifacts = self._bounded_records(
            "artifacts", self.store.list_artifacts({"root_frame_id": root_frame_id})
        )
        version_count = 0
        for artifact in sorted(
            source_artifacts,
            key=lambda item: str(item.get("artifact_id") or ""),
        ):
            filename = str(artifact.get("filename") or "artifact")
            try:
                filename = _safe_artifact_filename(filename)
            except SessionPackageError:
                excluded.append(
                    {
                        "artifact_id": str(artifact.get("artifact_id") or ""),
                        "reason": "secret_filename",
                    }
                )
                continue
            versions = []
            source_versions = self.store.list_versions(artifact["artifact_id"])
            version_count += len(source_versions)
            if version_count > _RECORD_LIMITS["artifact_versions"]:
                raise SessionPackageError(
                    "session has too many artifact versions to package safely"
                )
            source_versions.sort(
                key=lambda item: (
                    int(item.get("created_at") or 0),
                    str(item.get("version_id") or ""),
                )
            )
            for version in source_versions:
                meta = self.store.version_meta(str(version["version_id"])) or version
                candidate = meta.get("snapshot_path") or meta.get("path")
                record = {
                    key: _sanitize(meta.get(key))
                    for key in (
                        "version_id",
                        "content_type",
                        "size_bytes",
                        "checksum",
                        "producing_cell_id",
                        "frame_id",
                        "created_at",
                        "env_snapshot_id",
                    )
                }
                record["filename"] = filename
                data: bytes | None = None
                if candidate:
                    try:
                        data = Path(str(candidate)).read_bytes()
                    except OSError:
                        data = None
                if (
                    data is None
                    or len(data) > MAX_ENTRY_BYTES
                    or self._contains_secret_bytes(data)
                    or (meta.get("checksum") and _sha256(data) != meta.get("checksum"))
                ):
                    record.update({"available": False, "snapshot_sha256": None})
                else:
                    digest = _sha256(data)
                    files[f"artifact-data/{digest}"] = data
                    record.update(
                        {
                            "available": True,
                            "snapshot_sha256": digest,
                            "size_bytes": len(data),
                            "checksum": digest,
                        }
                    )
                    snapshot = self.store.env_snapshot_for_artifact(
                        artifact["artifact_id"], str(version["version_id"])
                    )
                    if snapshot and snapshot.get("snapshot_id"):
                        env_by_id[str(snapshot["snapshot_id"])] = _sanitize(snapshot)
                versions.append(record)
            available_versions = [item for item in versions if item.get("available")]
            if not available_versions:
                excluded.append(
                    {
                        "artifact_id": str(artifact.get("artifact_id") or ""),
                        "reason": "no_importable_version",
                    }
                )
                continue
            latest_version_id = artifact.get("latest_version_id")
            if not any(
                item.get("version_id") == latest_version_id
                for item in available_versions
            ):
                latest_version_id = available_versions[-1].get("version_id")
            artifacts.append(
                {
                    key: _sanitize(artifact.get(key))
                    for key in (
                        "artifact_id",
                        "content_type",
                        "is_user_upload",
                        "priority",
                        "created_at",
                    )
                }
                | {
                    "filename": filename,
                    "latest_version_id": latest_version_id,
                    "versions": versions,
                }
            )
        return (
            files,
            {"artifacts": artifacts, "excluded": excluded},
            [env_by_id[key] for key in sorted(env_by_id)],
        )

    def _export_lineage(self, artifacts: Mapping[str, Any]) -> list[dict[str, Any]]:
        version_ids = {
            str(version.get("version_id"))
            for artifact in artifacts.get("artifacts") or []
            for version in artifact.get("versions") or []
            if version.get("available") and version.get("version_id")
        }
        edges = []
        for source in sorted(version_ids):
            for target in self.store.lineage_edges_for(source, "down"):
                if target in version_ids:
                    producing = self.store.producing_cell_for_version(target) or {}
                    edges.append(
                        {
                            "input_version_id": source,
                            "output_version_id": target,
                            "producing_cell_id": producing.get("producing_cell_id"),
                        }
                    )
        edges.sort(
            key=lambda item: (item["input_version_id"], item["output_version_id"])
        )
        return edges

    # ------------------------------------------------------------------ import
    def import_bytes(self, data: bytes) -> dict[str, Any]:
        """Import untrusted bytes under one stable validation error contract."""

        if not self._import_lock.acquire(blocking=False):
            raise SessionPackageError("another Session package import is in progress")
        try:
            try:
                return self._import_bytes(data)
            except SessionPackageError:
                raise
            except (
                AttributeError,
                KeyError,
                OverflowError,
                TypeError,
                ValueError,
                sqlite3.IntegrityError,
            ) as error:
                raise SessionPackageError(
                    "session package records are inconsistent"
                ) from error
        finally:
            self._import_lock.release()

    def _import_bytes(self, data: bytes) -> dict[str, Any]:
        self._injection_flags = 0
        files, manifest = self._read_untrusted(data)
        for name, payload in files.items():
            if name != "manifest.json" and self._contains_secret_bytes(payload):
                raise SessionPackageError(
                    f"session package contains secret material: {name}"
                )
        documents = {
            name: self._load_json(files[name], name) for name in _REQUIRED_JSON
        }
        for name, document in documents.items():
            _assert_secret_free(document, path=name)
        self._validate_documents(documents, files)
        package_sha256 = _sha256(data)
        new_project_id: str | None = None
        import_root: Path | None = None
        active_workspace: Path | None = None
        imported_env_ids: set[str] = set()
        created_tree_ids: set[str] = set()
        created_blob_ids: set[str] = set()
        try:
            session = documents["session.json"]
            source = session["source"]
            project_id = f"proj_import_{uuid.uuid4().hex}"
            while self.store.get_project(project_id) is not None:
                project_id = f"proj_import_{uuid.uuid4().hex}"
            project = self.store.create_project(
                name=f"Imported: {str(session.get('project', {}).get('name') or 'research')}",
                description="Imported OpenAI4S Session package (view-only until recovery)",
                context="",
                project_id=project_id,
            )
            new_project_id = str(project["project_id"])
            new_root = self.store.new_frame(
                project_id=new_project_id,
                kind="turn",
                name=_safe_text(
                    session.get("frame", {}).get("name") or "Imported session"
                ),
                model=session.get("frame", {}).get("model"),
                status="done",
            )
            self.store.update_frame(
                new_root,
                task_summary=_safe_text(
                    session.get("frame", {}).get("task_summary")
                    or "Imported scientific Session"
                ),
                runtime_env=session.get("frame", {}).get("runtime_env"),
                status="done",
            )
            import_root = self.data_dir / "session-imports" / new_root
            import_root.mkdir(parents=True, exist_ok=False)

            source_root = str(source["root_frame_id"])
            source_project = str(source["project_id"])
            snapshots = documents["snapshots.json"]
            source_branches = list(snapshots.get("branches") or [])
            source_branch_ids = {str(item.get("branch_id")) for item in source_branches}
            source_branch_ids.add(source_root)
            branch_map = {
                branch_id: (
                    new_root
                    if branch_id == source_root
                    else f"br-{uuid.uuid4().hex[:16]}"
                )
                for branch_id in sorted(source_branch_ids)
            }
            source_active = str(source.get("active_branch_id") or source_root)
            active_branch = branch_map[source_active]
            active_workspace = (
                Path(self._workspace(new_root, active_branch)).expanduser().resolve()
            )
            active_workspace.mkdir(parents=True, exist_ok=True)
            if any(active_workspace.iterdir()):
                raise SessionPackageError(
                    "new import workspace is unexpectedly non-empty"
                )

            message_map = self._import_messages(
                new_root,
                session.get("messages") or [],
                source_root=source_root,
                branch_map=branch_map,
            )
            group_map, action_map = self._import_ledger(
                new_root,
                branch_map,
                documents["ledger.json"],
            )
            cell_map, revision_map = self._import_cells(
                new_root,
                new_project_id,
                documents["notebook.json"].get("cells") or [],
            )
            self._import_attempts(
                documents["ledger.json"].get("execution_attempts") or [],
                group_map=group_map,
                cell_map=cell_map,
                revision_map=revision_map,
            )
            env_map = self._import_environment_snapshots(
                documents["environment.json"].get("artifact_environment_snapshots")
                or []
            )
            imported_env_ids.update(env_map.values())
            artifact_map, version_map, live_artifacts = self._import_artifacts(
                new_root,
                new_project_id,
                documents["artifacts.json"],
                files,
                import_root=import_root,
                active_workspace=active_workspace,
                cell_map=cell_map,
                env_map=env_map,
            )
            self._import_lineage(
                documents["lineage.json"],
                version_map=version_map,
                cell_map=cell_map,
                new_root=new_root,
            )
            generation_map = self._import_generations(
                new_root,
                branch_map,
                documents["environment.json"].get("generations") or [],
            )
            workspace_projection = snapshots.get("workspace") or {}
            package_tree_ids = {
                str(value)
                for value in (workspace_projection.get("tree_map") or {}).values()
            }
            package_blob_ids = self._workspace_blob_ids(workspace_projection, files)
            preexisting_trees = {
                tree_id
                for tree_id in package_tree_ids
                if (self.cas.trees_dir / f"{tree_id}.json").is_file()
            }
            preexisting_blobs = {
                blob_id
                for blob_id in package_blob_ids
                if (self.cas.blobs_dir / blob_id).is_file()
            }
            created_tree_ids.update(package_tree_ids - preexisting_trees)
            created_blob_ids.update(package_blob_ids - preexisting_blobs)
            tree_map = self._import_workspace(snapshots, files)
            checkpoint_map = self._import_snapshots(
                new_root,
                source_root=source_root,
                source_project=source_project,
                new_project=new_project_id,
                snapshots=snapshots,
                branch_map=branch_map,
                tree_map=tree_map,
                artifact_map=artifact_map,
                version_map=version_map,
                generation_map=generation_map,
                cell_map=cell_map,
                message_map=message_map,
                revision_map=revision_map,
            )
            self._import_policies(
                new_root,
                new_project_id,
                documents["permissions.json"],
                documents["capabilities.json"],
            )
            self._import_plans_review_memory(
                new_root,
                new_project_id,
                plans=documents["plans.json"],
                review=documents["review.json"],
                memories=documents["memory.json"],
                artifact_map=artifact_map,
            )
            self._import_operations_and_recovery(
                new_root,
                snapshots=snapshots,
                branch_map=branch_map,
                checkpoint_map=checkpoint_map,
                generation_map=generation_map,
            )

            active_checkpoint = self._source_branch_head(
                source_active, source_branches, checkpoint_map
            )
            if active_branch != new_root and active_checkpoint:
                self.store.activate_session_branch_checkpoint(
                    root_frame_id=new_root,
                    branch_id=active_branch,
                    checkpoint_id=active_checkpoint,
                    expected_current_branch_id=new_root,
                )
            if any(active_workspace.iterdir()):
                raise SessionPackageError(
                    "new import workspace is unexpectedly non-empty"
                )
            source_current_tree = workspace_projection.get("active_source_tree_id")
            current_tree = tree_map.get(str(source_current_tree or ""))
            if not current_tree:
                raise SessionPackageError("active workspace tree could not be remapped")
            restored = self.cas.restore(current_tree, active_workspace)
            if not restored.get("applied"):
                raise SessionPackageError("imported workspace could not be restored")
            self._materialize_live_artifacts(active_workspace, live_artifacts)

            marker = self.store.create_kernel_generation(
                root_frame_id=new_root,
                branch_id=active_branch,
                language="python",
                environment={
                    "imported": True,
                    "source_schema_version": PACKAGE_SCHEMA_VERSION,
                },
                bootstrap={
                    "view_only": True,
                    "explicit_recovery_required": True,
                    "trust_state": "quarantined",
                },
                owner_instance_id=None,
                state="starting",
            )
            self.store.finish_kernel_generation(
                marker["generation_id"],
                state="released",
                reason="session_package_import_view_only",
            )

            import_group = self.store.append_action_group(
                root_frame_id=new_root,
                branch_id=active_branch,
                turn_id=f"import-{uuid.uuid4().hex[:16]}",
                kind="session_import",
                assistant_content="Imported scientific Session package",
            )
            self.store.append_action_event(
                group_id=import_group["group_id"],
                type="session_imported",
                result={
                    "schema_version": PACKAGE_SCHEMA_VERSION,
                    "view_only": True,
                    "explicit_recovery_required": True,
                    "trust_state": "quarantined",
                },
                side_effect_class="metadata_write",
                resource_keys=[f"session:{new_root}"],
            )
            self.store.set_setting(
                session_import_quarantine_key(new_root),
                _canonical_json(
                    {
                        "state": "quarantined",
                        "reason": "untrusted_session_package",
                        "package_sha256": package_sha256,
                        "schema_version": PACKAGE_SCHEMA_VERSION,
                        "injection_flags": self._injection_flags,
                    }
                ).decode("utf-8"),
            )
            return {
                "ok": True,
                "project_id": new_project_id,
                "root_frame_id": new_root,
                "active_branch_id": active_branch,
                "package_sha256": package_sha256,
                "schema_version": manifest["schema_version"],
                "kernel_state": "ended",
                "view_only": True,
                "explicit_recovery_required": True,
                "trust_state": "quarantined",
                "identity_remap": {
                    "actions": len(action_map),
                    "artifacts": len(artifact_map),
                    "versions": len(version_map),
                    "cells": len(cell_map),
                    "checkpoints": len(checkpoint_map),
                },
            }
        except Exception as error:
            cleanup_errors: list[str] = []
            if new_project_id is not None:
                try:
                    self.store.delete_project(new_project_id)
                except Exception as cleanup_error:  # noqa: BLE001
                    cleanup_errors.append(f"database: {cleanup_error}")
            if imported_env_ids:
                try:
                    self.store.delete_env_snapshots_if_unreferenced(imported_env_ids)
                except Exception as cleanup_error:  # noqa: BLE001
                    cleanup_errors.append(f"environment snapshots: {cleanup_error}")
            if import_root is not None:
                try:
                    self._remove_private_import_root(import_root)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"import staging: {cleanup_error}")
            if active_workspace is not None:
                try:
                    self._remove_private_workspace(active_workspace)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"workspace: {cleanup_error}")
            try:
                self._release_import_cas(created_tree_ids, created_blob_ids)
            except Exception as cleanup_error:  # noqa: BLE001
                cleanup_errors.append(f"workspace CAS: {cleanup_error}")
            if cleanup_errors:
                raise SessionPackageError(
                    "session import failed and cleanup was incomplete: "
                    + "; ".join(cleanup_errors)
                ) from error
            raise

    def _read_untrusted(self, data: bytes) -> tuple[dict[str, bytes], dict[str, Any]]:
        if not isinstance(data, bytes):
            raise TypeError("session package must be bytes")
        if not data or len(data) > MAX_ARCHIVE_BYTES:
            raise SessionPackageError("session package archive size is invalid")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data), "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise SessionPackageError("session package is not a valid ZIP") from error
        files: dict[str, bytes] = {}
        seen_casefold: set[str] = set()
        with archive:
            try:
                infos = archive.infolist()
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise SessionPackageError(
                    "session package ZIP directory is corrupt"
                ) from error
            if not infos or len(infos) > MAX_ENTRIES:
                raise SessionPackageError("session package entry count is invalid")
            total = 0
            for info in infos:
                name = _safe_relative(info.filename)
                folded = unicodedata.normalize("NFKC", name).casefold()
                if name in files or folded in seen_casefold:
                    raise SessionPackageError(f"duplicate ZIP entry: {name}")
                seen_casefold.add(folded)
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode == stat.S_IFLNK:
                    raise SessionPackageError("directories and symlinks are forbidden")
                if info.flag_bits & 0x1:
                    raise SessionPackageError("encrypted ZIP entries are forbidden")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise SessionPackageError("unsupported ZIP compression method")
                if info.file_size < 0 or info.file_size > MAX_ENTRY_BYTES:
                    raise SessionPackageError(f"ZIP entry is too large: {name}")
                if info.compress_size < 0 or (
                    info.file_size > 0
                    and (
                        info.compress_size == 0
                        or info.file_size / max(1, info.compress_size)
                        > MAX_COMPRESSION_RATIO
                    )
                ):
                    raise SessionPackageError(
                        f"ZIP entry compression ratio is unsafe: {name}"
                    )
                total += int(info.file_size)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise SessionPackageError(
                        "session package expands beyond its limit"
                    )
                try:
                    payload = archive.read(info)
                except (
                    EOFError,
                    NotImplementedError,
                    OSError,
                    RuntimeError,
                    zipfile.BadZipFile,
                    zlib.error,
                ) as error:
                    raise SessionPackageError(
                        f"session package ZIP entry is corrupt: {name}"
                    ) from error
                if len(payload) != info.file_size:
                    raise SessionPackageError(f"ZIP entry size mismatch: {name}")
                files[name] = payload
        try:
            manifest = self._load_json(files["manifest.json"], "manifest.json")
        except KeyError as error:
            raise SessionPackageError("session package manifest is missing") from error
        if manifest.get("format") != PACKAGE_FORMAT:
            raise SessionPackageError("unsupported session package format")
        if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise SessionPackageError("unsupported session package schema version")
        body = {
            "format": manifest.get("format"),
            "schema_version": manifest.get("schema_version"),
            "files": manifest.get("files"),
        }
        if manifest.get("manifest_sha256") != _sha256(_canonical_json(body)):
            raise SessionPackageError("session package manifest checksum mismatch")
        listed: set[str] = set()
        for record in manifest.get("files") or []:
            if not isinstance(record, Mapping):
                raise SessionPackageError("manifest file record is invalid")
            name = _safe_relative(str(record.get("path") or ""))
            if name == "manifest.json" or name in listed:
                raise SessionPackageError("manifest contains a duplicate file")
            listed.add(name)
            payload = files.get(name)
            if payload is None:
                raise SessionPackageError(f"manifest file is missing: {name}")
            if record.get("size") != len(payload) or record.get("sha256") != _sha256(
                payload
            ):
                raise SessionPackageError(f"manifest hash mismatch: {name}")
        if set(files) != listed | {"manifest.json"}:
            raise SessionPackageError("session package contains unlisted files")
        missing = set(_REQUIRED_JSON) - listed
        if missing:
            raise SessionPackageError(
                "session package is incomplete: " + ", ".join(sorted(missing))
            )
        return files, manifest

    @staticmethod
    def _load_json(data: bytes, name: str) -> dict[str, Any]:
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise SessionPackageError(f"invalid JSON document: {name}") from error
        if not isinstance(value, dict):
            raise SessionPackageError(f"JSON document must be an object: {name}")
        return value

    def _validate_documents(
        self, documents: Mapping[str, dict[str, Any]], files: Mapping[str, bytes]
    ) -> None:
        def records(
            document_name: str,
            key: str,
            limit_name: str,
        ) -> list[Mapping[str, Any]]:
            value = documents[document_name].get(key)
            if not isinstance(value, list):
                raise SessionPackageError(f"{document_name}.{key} must be a list")
            self._bounded_records(limit_name, value)
            if any(not isinstance(item, Mapping) for item in value):
                raise SessionPackageError(
                    f"{document_name}.{key} contains an invalid record"
                )
            return value

        def identities(
            values: list[Mapping[str, Any]], field: str, label: str
        ) -> tuple[set[str], dict[str, Mapping[str, Any]]]:
            found: set[str] = set()
            indexed: dict[str, Mapping[str, Any]] = {}
            for item in values:
                raw = item.get(field)
                if not isinstance(raw, str) or not raw or len(raw) > 512:
                    raise SessionPackageError(f"{label} identity is missing or invalid")
                if raw in found:
                    raise SessionPackageError(f"duplicate {label} identity")
                found.add(raw)
                indexed[raw] = item
            return found, indexed

        def reference(value: Any, allowed: set[str], label: str) -> None:
            if value in (None, ""):
                return
            if not isinstance(value, str) or value not in allowed:
                raise SessionPackageError(f"{label} references an unknown identity")

        def required_reference(value: Any, allowed: set[str], label: str) -> None:
            if not isinstance(value, str) or not value:
                raise SessionPackageError(f"{label} identity is missing")
            reference(value, allowed, label)

        session = documents["session.json"]
        if session.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise SessionPackageError("session document schema version mismatch")
        source = session.get("source")
        if (
            not isinstance(source, Mapping)
            or not isinstance(source.get("project_id"), str)
            or not isinstance(source.get("root_frame_id"), str)
            or not source.get("project_id")
            or not source.get("root_frame_id")
        ):
            raise SessionPackageError("session source identity is missing")
        source_root = str(source["root_frame_id"])
        source_project = str(source["project_id"])
        documents["review.json"].setdefault("activity_steps", [])
        documents["review.json"].setdefault("settings", {})
        documents["snapshots.json"].setdefault("checkpoint_states", [])

        messages = records("session.json", "messages", "messages")
        groups = records("ledger.json", "groups", "groups")
        attempts = records("ledger.json", "execution_attempts", "execution_attempts")
        cells = records("notebook.json", "cells", "cells")
        branches = records("snapshots.json", "branches", "branches")
        checkpoints = records("snapshots.json", "checkpoints", "checkpoints")
        checkpoint_states = records(
            "snapshots.json", "checkpoint_states", "checkpoint_states"
        )
        operations = records("snapshots.json", "operations", "operations")
        recovery = records("snapshots.json", "recovery_journal", "recovery_journal")
        artifacts = records("artifacts.json", "artifacts", "artifacts")
        generations = records("environment.json", "generations", "generations")
        env_snapshots = records(
            "environment.json",
            "artifact_environment_snapshots",
            "environment_snapshots",
        )
        lineage = records("lineage.json", "edges", "lineage_edges")
        plans = records("plans.json", "plans", "plans")
        annotations = records("review.json", "annotations", "annotations")
        review_steps = records("review.json", "activity_steps", "review_steps")
        records("memory.json", "memories", "memories")
        capability_states = records("capabilities.json", "states", "capability_states")
        permission_project = records("permissions.json", "project", "permission_rules")
        permission_conversation = records(
            "permissions.json", "conversation", "permission_rules"
        )
        if (
            len(permission_project) + len(permission_conversation)
            > _RECORD_LIMITS["permission_rules"]
        ):
            raise SessionPackageError("session package has too many permission rules")

        message_ids, _ = identities(messages, "message_id", "message")
        group_ids, _ = identities(groups, "group_id", "action group")
        cell_ids, _ = identities(cells, "producing_cell_id", "cell")
        branch_ids, branch_by_id = identities(branches, "branch_id", "branch")
        checkpoint_ids, checkpoint_by_id = identities(
            checkpoints, "checkpoint_id", "checkpoint"
        )
        generation_ids, _ = identities(
            generations, "generation_id", "kernel generation"
        )
        env_ids, _ = identities(env_snapshots, "snapshot_id", "environment snapshot")

        if source_root not in branch_ids:
            raise SessionPackageError("root session branch metadata is missing")
        active_branch = source.get("active_branch_id") or source_root
        if not isinstance(active_branch, str) or active_branch not in branch_ids:
            raise SessionPackageError("active session branch is invalid")

        # Schema-v1 packages created before branch-aware conversations omitted
        # ``branch_id`` and therefore belong to the source root.  New packages
        # preserve the owning branch and must not be allowed to reference a
        # branch outside the imported DAG.
        for message in messages:
            source_branch = message.get("branch_id", source_root)
            required_reference(source_branch, branch_ids, "message")

        seen_revisions: set[int] = set()
        for cell in cells:
            try:
                revision = int(cell.get("state_revision") or cell.get("cell_index"))
            except (TypeError, ValueError):
                raise SessionPackageError("cell state revision is invalid") from None
            if revision < 1 or revision in seen_revisions:
                raise SessionPackageError(
                    "cell state revisions must be unique and positive"
                )
            seen_revisions.add(revision)
            if str(cell.get("language") or "python").lower() not in {"python", "r"}:
                raise SessionPackageError("cell language is invalid")

        seen_event_ids: set[str] = set()
        for group in groups:
            required_reference(group.get("branch_id"), branch_ids, "action group")
            if group.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError("action group belongs to another Session")
            assistant_message = group.get("assistant_message")
            if assistant_message is not None and not isinstance(
                assistant_message, Mapping
            ):
                raise SessionPackageError("action group assistant message is invalid")
            usage = group.get("usage")
            if usage is not None and not isinstance(usage, Mapping):
                raise SessionPackageError("action group usage is invalid")
            events = group.get("events")
            if not isinstance(events, list) or any(
                not isinstance(event, Mapping) for event in events
            ):
                raise SessionPackageError("action group events are invalid")
            sequences: set[int] = set()
            for event in events:
                event_id = event.get("event_id")
                if event_id not in (None, ""):
                    if not isinstance(event_id, str) or event_id in seen_event_ids:
                        raise SessionPackageError(
                            "duplicate or invalid action event identity"
                        )
                    seen_event_ids.add(event_id)
                try:
                    sequence = int(event.get("sequence") or 0)
                except (TypeError, ValueError):
                    raise SessionPackageError(
                        "action event sequence is invalid"
                    ) from None
                if sequence < 0 or sequence in sequences:
                    raise SessionPackageError("action event sequence is duplicated")
                sequences.add(sequence)

        attempt_ids, _ = identities(attempts, "attempt_id", "execution attempt")
        del attempt_ids
        for attempt in attempts:
            required_reference(attempt.get("group_id"), group_ids, "execution attempt")
            required_reference(
                attempt.get("producing_cell_id"), cell_ids, "execution attempt"
            )
            reference(
                attempt.get("replayed_from_cell_id"), cell_ids, "execution attempt"
            )
            reference(attempt.get("generation_id"), generation_ids, "execution attempt")

        for generation in generations:
            required_reference(
                generation.get("branch_id"), branch_ids, "kernel generation"
            )
            if generation.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError(
                    "kernel generation belongs to another Session"
                )

        artifact_ids, _ = identities(artifacts, "artifact_id", "artifact")
        version_ids: set[str] = set()
        version_count = 0
        artifact_names: set[str] = set()
        for artifact in artifacts:
            filename = _safe_artifact_filename(artifact.get("filename"))
            folded = unicodedata.normalize("NFKC", filename).casefold()
            if folded in artifact_names:
                raise SessionPackageError("artifact filenames collide")
            artifact_names.add(folded)
            versions = artifact.get("versions")
            if not isinstance(versions, list) or any(
                not isinstance(version, Mapping) for version in versions
            ):
                raise SessionPackageError("artifact versions are invalid")
            version_count += len(versions)
            if version_count > _RECORD_LIMITS["artifact_versions"]:
                raise SessionPackageError(
                    "session package has too many artifact versions"
                )
            local_versions: set[str] = set()
            available_versions: set[str] = set()
            available_count = 0
            for version in versions:
                version_id = version.get("version_id")
                if (
                    not isinstance(version_id, str)
                    or not version_id
                    or version_id in version_ids
                ):
                    raise SessionPackageError(
                        "duplicate or invalid artifact version identity"
                    )
                version_ids.add(version_id)
                local_versions.add(version_id)
                if _safe_artifact_filename(version.get("filename")) != filename:
                    raise SessionPackageError(
                        "artifact version filename is inconsistent"
                    )
                reference(
                    version.get("producing_cell_id"), cell_ids, "artifact version"
                )
                reference(version.get("env_snapshot_id"), env_ids, "artifact version")
                digest = version.get("snapshot_sha256")
                if version.get("available"):
                    available_count += 1
                    available_versions.add(version_id)
                    if not isinstance(digest, str) or len(digest) != 64:
                        raise SessionPackageError("artifact snapshot digest is invalid")
                    payload = files.get(f"artifact-data/{digest}")
                    if payload is None or _sha256(payload) != digest:
                        raise SessionPackageError(
                            "artifact snapshot is missing or corrupt"
                        )
                    if self._contains_secret_bytes(payload):
                        raise SessionPackageError("artifact snapshot contains a secret")
            if available_count == 0:
                raise SessionPackageError("artifact has no importable version")
            required_reference(
                artifact.get("latest_version_id"),
                available_versions,
                "artifact latest version",
            )

        for edge in lineage:
            required_reference(
                edge.get("input_version_id"), version_ids, "lineage edge"
            )
            required_reference(
                edge.get("output_version_id"), version_ids, "lineage edge"
            )
            reference(edge.get("producing_cell_id"), cell_ids, "lineage edge")
        for plan in plans:
            reference(plan.get("artifact_id"), artifact_ids, "plan")
        for annotation in annotations:
            required_reference(
                annotation.get("artifact_id"), artifact_ids, "annotation"
            )
        review_settings = documents["review.json"].get("settings")
        if not isinstance(review_settings, Mapping):
            raise SessionPackageError("review settings snapshot is invalid")
        for step in review_steps:
            if str(step.get("kind") or "").casefold() not in {
                "review",
                "review_settings",
            }:
                raise SessionPackageError("review activity kind is invalid")

        workspace = documents["snapshots.json"].get("workspace") or {}
        if not isinstance(workspace, Mapping):
            raise SessionPackageError("workspace projection is invalid")
        if workspace.get("active_branch_id") != active_branch:
            raise SessionPackageError("workspace active branch does not match session")
        tree_map = workspace.get("tree_map") or {}
        if not isinstance(tree_map, Mapping):
            raise SessionPackageError("workspace tree map is invalid")
        if any(not isinstance(key, str) or not key for key in tree_map):
            raise SessionPackageError("workspace source tree identity is invalid")
        active_tree = workspace.get("active_source_tree_id")
        if not isinstance(active_tree, str) or active_tree not in tree_map:
            raise SessionPackageError("active workspace tree is unavailable")
        workspace_entry_count = 0
        for _source_id, tree_id in tree_map.items():
            if not isinstance(tree_id, str) or len(tree_id) != 64:
                raise SessionPackageError("workspace tree id is invalid")
            raw = files.get(f"workspace/trees/{tree_id}.json")
            if raw is None:
                raise SessionPackageError("workspace tree manifest is missing")
            tree = self._load_json(raw, f"workspace tree {tree_id}")
            body = {
                "version": tree.get("version"),
                "entries": tree.get("entries"),
                "skipped": tree.get("skipped"),
            }
            if (
                tree.get("tree_id") != tree_id
                or _sha256(_canonical_json(body)) != tree_id
            ):
                raise SessionPackageError("workspace tree checksum mismatch")
            seen: set[str] = set()
            entries = tree.get("entries")
            if not isinstance(entries, list):
                raise SessionPackageError("workspace tree entries are invalid")
            workspace_entry_count += len(entries)
            if workspace_entry_count > _RECORD_LIMITS["workspace_entries"]:
                raise SessionPackageError(
                    "session package has too many workspace entries"
                )
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise SessionPackageError("workspace tree entry is invalid")
                relative = _safe_relative(str(entry.get("path") or ""))
                folded = unicodedata.normalize("NFKC", relative).casefold()
                if folded in seen or _is_secret_path(relative):
                    raise SessionPackageError("unsafe workspace tree entry")
                seen.add(folded)
                digest = str(entry.get("blob") or "")
                payload = files.get(f"workspace/blobs/{digest}")
                if payload is None or _sha256(payload) != digest:
                    raise SessionPackageError("workspace blob is missing or corrupt")
                if len(payload) != int(entry.get("size") or -1):
                    raise SessionPackageError("workspace blob size mismatch")
                if self._contains_secret_bytes(payload):
                    raise SessionPackageError("workspace blob contains a secret")

        checkpoint_state_ids: set[str] = set()
        for state in checkpoint_states:
            try:
                summary = self.store.validate_checkpoint_state_import(dict(state))
            except (TypeError, ValueError) as error:
                raise SessionPackageError(
                    f"checkpoint domain state is invalid: {error}"
                ) from error
            source_checkpoint = summary.get("checkpoint_id")
            required_reference(
                source_checkpoint, checkpoint_ids, "checkpoint domain state"
            )
            if source_checkpoint in checkpoint_state_ids:
                raise SessionPackageError("duplicate checkpoint domain state identity")
            checkpoint_state_ids.add(str(source_checkpoint))
            if (
                summary.get("root_frame_id") != source_root
                or summary.get("project_id") != source_project
            ):
                raise SessionPackageError(
                    "checkpoint domain state belongs to another Session"
                )
            state_branch = summary.get("branch_id")
            required_reference(
                state_branch, branch_ids, "checkpoint domain state branch"
            )
            if (
                checkpoint_by_id[str(source_checkpoint)].get("branch_id")
                != state_branch
            ):
                raise SessionPackageError(
                    "checkpoint domain state branch is inconsistent"
                )
            reference(
                summary.get("source_checkpoint_id"),
                checkpoint_ids,
                "checkpoint domain state source",
            )
            for state_artifact_id in summary.get("artifact_ids") or []:
                required_reference(
                    state_artifact_id,
                    artifact_ids,
                    "checkpoint domain state Artifact",
                )

        for checkpoint in checkpoints:
            branch_id = checkpoint.get("branch_id")
            required_reference(branch_id, branch_ids, "checkpoint")
            if checkpoint.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError("checkpoint belongs to another Session")
            reference(
                checkpoint.get("parent_checkpoint_id"), checkpoint_ids, "checkpoint"
            )
            reference(checkpoint.get("workspace_tree_id"), set(tree_map), "checkpoint")
            checkpoint_versions = checkpoint.get("artifact_versions") or []
            if not isinstance(checkpoint_versions, list):
                raise SessionPackageError("checkpoint Artifact versions are invalid")
            for version_id in checkpoint_versions:
                reference(version_id, version_ids, "checkpoint artifact version")
            source_kind = checkpoint.get("source_kind")
            source_id = checkpoint.get("source_id")
            if (source_kind is None) != (source_id is None):
                raise SessionPackageError("checkpoint source identity is incomplete")
            if source_kind == "cell":
                reference(source_id, cell_ids, "checkpoint source")
            elif source_kind == "message":
                reference(source_id, message_ids, "checkpoint source")
            elif source_kind is not None:
                raise SessionPackageError("checkpoint source kind is invalid")
            metadata = checkpoint.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                raise SessionPackageError("checkpoint metadata is invalid")
            for field in ("reverted_to", "undo_checkpoint_id"):
                reference(metadata.get(field), checkpoint_ids, "checkpoint metadata")
            projection = metadata.get("history_projection")
            if projection is not None:
                if not isinstance(projection, Mapping):
                    raise SessionPackageError(
                        "checkpoint history projection is invalid"
                    )
                if projection.get("version") != 1:
                    raise SessionPackageError(
                        "checkpoint history projection version is invalid"
                    )
                required_reference(
                    projection.get("base_checkpoint_id"),
                    checkpoint_ids,
                    "checkpoint history projection",
                )
                if metadata.get("reverted_to") not in (
                    None,
                    projection.get("base_checkpoint_id"),
                ):
                    raise SessionPackageError(
                        "checkpoint history projection target is inconsistent"
                    )
                cursors = projection.get("resume_cursors")
                if not isinstance(cursors, Mapping):
                    raise SessionPackageError(
                        "checkpoint history resume cursors are invalid"
                    )
                for key in ("action_cursor", "message_cursor", "cell_cursor"):
                    cursor = cursors.get(key)
                    if cursor is not None and (
                        isinstance(cursor, bool)
                        or not isinstance(cursor, int)
                        or cursor < 0
                    ):
                        raise SessionPackageError(
                            "checkpoint history resume cursor is invalid"
                        )

        root_branch = branch_by_id[source_root]
        if root_branch.get("parent_branch_id") or root_branch.get("base_checkpoint_id"):
            raise SessionPackageError("root branch ancestry is invalid")
        for branch_id, branch in branch_by_id.items():
            if branch.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError("branch belongs to another Session")
            if branch_id == source_root:
                continue
            parent = branch.get("parent_branch_id")
            base = branch.get("base_checkpoint_id")
            required_reference(parent, branch_ids, "branch parent")
            required_reference(base, checkpoint_ids, "branch base")
            if parent == branch_id:
                raise SessionPackageError("branch graph is cyclic")
            if checkpoint_by_id[str(base)].get("branch_id") != parent:
                raise SessionPackageError("branch base does not belong to its parent")
        for branch_id in branch_ids:
            visited: set[str] = set()
            cursor = branch_id
            while cursor != source_root:
                if cursor in visited:
                    raise SessionPackageError("branch graph is cyclic")
                visited.add(cursor)
                parent = branch_by_id[cursor].get("parent_branch_id")
                if not isinstance(parent, str) or parent not in branch_ids:
                    raise SessionPackageError("branch graph is incomplete")
                cursor = parent

        checkpoints_by_branch: dict[str, set[str]] = {
            branch_id: set() for branch_id in branch_ids
        }
        for checkpoint_id, checkpoint in checkpoint_by_id.items():
            branch_id = str(checkpoint.get("branch_id") or source_root)
            checkpoints_by_branch[branch_id].add(checkpoint_id)
        for branch_id, branch in branch_by_id.items():
            head = branch.get("head_checkpoint_id")
            base = (
                branch.get("base_checkpoint_id") if branch_id != source_root else None
            )
            reference(head, checkpoint_ids, "branch head")
            if branch_id != source_root and head in (None, ""):
                raise SessionPackageError("child branch head is missing")
            cursor = head
            visited: set[str] = set()
            while cursor not in (None, base):
                if cursor in visited:
                    raise SessionPackageError("checkpoint chain is cyclic")
                visited.add(str(cursor))
                checkpoint = checkpoint_by_id[str(cursor)]
                if str(checkpoint.get("branch_id") or source_root) != branch_id:
                    raise SessionPackageError("branch head belongs to another branch")
                cursor = checkpoint.get("parent_checkpoint_id")
            if branch_id != source_root and cursor != base:
                raise SessionPackageError(
                    "child checkpoint chain misses its branch base"
                )
            if visited != checkpoints_by_branch[branch_id]:
                raise SessionPackageError("checkpoint chain is branching or incomplete")

        active_head = branch_by_id[str(active_branch)].get("head_checkpoint_id")
        if active_branch != source_root and active_head in (None, ""):
            raise SessionPackageError("active child branch has no restorable head")

        for operation in operations:
            required_reference(
                operation.get("branch_id"), branch_ids, "snapshot operation"
            )
            if operation.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError(
                    "snapshot operation belongs to another Session"
                )
            reference(
                operation.get("source_checkpoint_id"),
                checkpoint_ids,
                "snapshot operation",
            )
            reference(
                operation.get("target_checkpoint_id"),
                checkpoint_ids,
                "snapshot operation",
            )
        for item in recovery:
            required_reference(item.get("branch_id"), branch_ids, "recovery event")
            if item.get("root_frame_id") not in (None, source_root):
                raise SessionPackageError("recovery event belongs to another Session")
            reference(
                item.get("source_generation_id"), generation_ids, "recovery event"
            )
            reference(
                item.get("candidate_generation_id"),
                generation_ids,
                "recovery event",
            )

        for state in capability_states:
            scope = state.get("scope")
            scope_id = state.get("scope_id")
            if (scope, scope_id) not in {
                ("project", source_project),
                ("session", source_root),
            }:
                raise SessionPackageError("capability scope is invalid")

    def _import_messages(
        self,
        new_root: str,
        messages: list[Any],
        *,
        source_root: str,
        branch_map: Mapping[str, str],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for item in sorted(messages, key=lambda value: int(value.get("seq") or 0)):
            role = str(item.get("role") or "assistant")
            if role not in {"user", "assistant"}:
                role = "assistant"
            source_branch = str(item.get("branch_id") or source_root)
            if source_branch not in branch_map:
                raise SessionPackageError("message references an unknown branch")
            before = self._injection_flags
            content = self._scan_untrusted_text(item.get("content"))
            metadata = (
                dict(item.get("metadata"))
                if isinstance(item.get("metadata"), dict)
                else None
            )
            if self._injection_flags != before:
                metadata = {**(metadata or {}), "injection_flagged": True}
            inserted = self.store.add_message(
                root_frame_id=new_root,
                branch_id=branch_map[source_branch],
                frame_id=new_root,
                role=role,
                content=content,
                metadata=metadata,
                created_at=item.get("created_at"),
            )
            source_id = item.get("message_id")
            if source_id:
                mapping[str(source_id)] = str(inserted["message_id"])
        return mapping

    def _import_ledger(
        self,
        new_root: str,
        branch_map: Mapping[str, str],
        ledger: Mapping[str, Any],
    ) -> tuple[dict[str, str], dict[str, str]]:
        group_map: dict[str, str] = {}
        action_map: dict[str, str] = {}
        tool_map: dict[str, str] = {}
        wire_map: dict[str, str] = {}
        turn_map: dict[str, str] = {}
        groups = sorted(
            ledger.get("groups") or [],
            key=lambda item: (
                str(item.get("branch_id") or ""),
                int(item.get("ordinal") or 0),
            ),
        )
        # Allocate every identity before inserting the first row. Provider wire
        # state and assistant tool declarations may point forward to event IDs,
        # so a one-pass import would either retain colliding source IDs or break
        # the declaration/result group consumed by the ledger reducer.
        for item in groups:
            source_group = str(item.get("group_id") or "")
            group_map[source_group] = f"ag-{uuid.uuid4().hex[:16]}"
            source_turn = str(item.get("turn_id") or source_group or uuid.uuid4())
            turn_map.setdefault(source_turn, f"turn-{uuid.uuid4().hex[:16]}")
            for event in item.get("events") or []:
                for source_id, mapping, prefix in (
                    (event.get("action_id"), action_map, "action"),
                    (event.get("tool_call_id"), tool_map, "call"),
                    (event.get("wire_id"), wire_map, "wire"),
                ):
                    if source_id:
                        mapping.setdefault(
                            str(source_id), f"{prefix}-{uuid.uuid4().hex[:16]}"
                        )
        identity_map = {
            **{str(key): str(value) for key, value in branch_map.items()},
            **group_map,
            **turn_map,
            **action_map,
            **tool_map,
            **wire_map,
        }
        for item in groups:
            source_group = str(item.get("group_id") or "")
            source_branch = str(item.get("branch_id") or "")
            if source_branch not in branch_map:
                raise SessionPackageError("ledger references an unknown branch")
            source_turn = str(item.get("turn_id") or source_group or uuid.uuid4())
            assistant_message = item.get("assistant_message")
            self.store.append_action_group(
                root_frame_id=new_root,
                branch_id=branch_map[source_branch],
                turn_id=turn_map[source_turn],
                ordinal=int(item.get("ordinal") or 0),
                kind=str(item.get("kind") or "imported"),
                provider=item.get("provider"),
                model=item.get("model"),
                wire_state=self._remap_nested(item.get("wire_state"), identity_map),
                assistant_content=(
                    self._scan_untrusted_text(item.get("assistant_content"))
                    if item.get("assistant_content")
                    else item.get("assistant_content")
                ),
                assistant_message=(
                    self._annotate_assistant_message(
                        self._remap_nested(assistant_message, identity_map)
                    )
                    if isinstance(assistant_message, Mapping)
                    else None
                ),
                usage=(
                    dict(item.get("usage") or {})
                    if isinstance(item.get("usage"), Mapping)
                    else None
                ),
                cost_usd=item.get("cost_usd"),
                group_id=group_map[source_group],
                created_at=item.get("created_at"),
            )
            for event in sorted(
                item.get("events") or [],
                key=lambda value: int(value.get("sequence") or 0),
            ):
                self.store.append_action_event(
                    group_id=group_map[source_group],
                    sequence=int(event.get("sequence") or 0),
                    type=str(event.get("type") or "imported_event"),
                    action_id=action_map.get(str(event.get("action_id") or "")),
                    tool_call_id=tool_map.get(str(event.get("tool_call_id") or "")),
                    wire_id=wire_map.get(str(event.get("wire_id") or "")),
                    canonical_arguments=event.get("canonical_arguments"),
                    raw_arguments=event.get("raw_arguments"),
                    result=event.get("result"),
                    side_effect_class=event.get("side_effect_class"),
                    resource_keys=list(event.get("resource_keys") or []),
                    created_at=event.get("created_at"),
                )
        return group_map, action_map

    def _annotate_assistant_message(self, message: Any) -> Any:
        """Banner the plain-text content of an untrusted assistant message."""

        if isinstance(message, dict) and isinstance(message.get("content"), str):
            annotated = dict(message)
            annotated["content"] = self._scan_untrusted_text(message["content"])
            return annotated
        return message

    def _import_cells(
        self,
        new_root: str,
        new_project: str,
        cells: list[Any],
    ) -> tuple[dict[str, str], dict[int, int]]:
        mapping = {
            str(item.get("producing_cell_id")): f"c-{uuid.uuid4().hex[:12]}"
            for item in cells
            if item.get("producing_cell_id")
        }
        revision_map: dict[int, int] = {}
        ordered = sorted(
            cells,
            key=lambda item: (
                int(item.get("state_revision") or item.get("cell_index") or 0),
                str(item.get("producing_cell_id") or ""),
            ),
        )
        for revision, item in enumerate(ordered, start=1):
            source_id = str(item.get("producing_cell_id") or f"source-{revision}")
            new_id = mapping.setdefault(source_id, f"c-{uuid.uuid4().hex[:12]}")
            source_revision = int(
                item.get("state_revision") or item.get("cell_index") or revision
            )
            revision_map[source_revision] = revision
            result = {
                "id": new_id,
                "stdout": item.get("stdout"),
                "stderr": item.get("stderr"),
                "error": item.get("error"),
                "interrupted": bool(item.get("interrupted")),
                "usage": {
                    "wall_s": item.get("wall_s"),
                    "cpu_s": item.get("cpu_s"),
                    "peak_rss_kb": item.get("peak_rss_kb"),
                },
            }
            code = str(item.get("code") or "")
            # Imported source is inspectable evidence, never trusted executable
            # recovery input. A confirmed fresh restart can later unlock the
            # session without replaying any package-authored Cell.
            policy = "never"
            self.store.log_cell(
                frame_id=new_root,
                root_frame_id=new_root,
                project_id=new_project,
                code=code,
                result=result,
                origin=str(item.get("origin") or "import"),
                cell_seq=revision,
                cell_index=revision,
                state_revision=revision,
                kernel_id=str(
                    item.get("kernel_id") or item.get("language") or "python"
                ),
                language=str(item.get("language") or "python").lower(),
                visibility=str(item.get("visibility") or "scientific"),
                pin=bool(item.get("pin")),
                replay_policy=policy,
                figures=list(item.get("figures") or []),
                files_read=[
                    path
                    for path in item.get("files_read") or []
                    if isinstance(path, str) and not _is_secret_path(path)
                ],
                files_written=[
                    path
                    for path in item.get("files_written") or []
                    if isinstance(path, str) and not _is_secret_path(path)
                ],
            )
        return mapping, revision_map

    def _import_attempts(
        self,
        attempts: list[Any],
        *,
        group_map: Mapping[str, str],
        cell_map: Mapping[str, str],
        revision_map: Mapping[int, int],
    ) -> None:
        for item in attempts:
            group_id = group_map.get(str(item.get("group_id") or ""))
            cell_id = cell_map.get(str(item.get("producing_cell_id") or ""))
            if not group_id or not cell_id:
                continue
            source_revision = item.get("state_revision")
            revision = (
                revision_map.get(int(source_revision))
                if source_revision is not None
                else None
            )
            attempt = self.store.allocate_execution_attempt(
                group_id=group_id,
                producing_cell_id=cell_id,
                state_revision=revision,
                generation_id=None,
                owner_instance_id=None,
                replayed_from_cell_id=cell_map.get(
                    str(item.get("replayed_from_cell_id") or "")
                ),
                attempt_ordinal=int(item.get("attempt_ordinal") or 0),
                allocated_at=item.get("allocated_at"),
            )
            if item.get("started_at") is not None:
                self.store.mark_execution_attempt_started(
                    attempt["attempt_id"], started_at=int(item["started_at"])
                )
            if item.get("response_at") is not None:
                self.store.mark_execution_attempt_response(
                    attempt["attempt_id"], response_at=int(item["response_at"])
                )
            if item.get("capture_at") is not None:
                self.store.mark_execution_attempt_capture(
                    attempt["attempt_id"], capture_at=int(item["capture_at"])
                )
            if item.get("terminal_state") and item.get("finished_at") is not None:
                self.store.finish_execution_attempt(
                    attempt["attempt_id"],
                    terminal_state=str(item["terminal_state"]),
                    error=item.get("error"),
                    finished_at=int(item["finished_at"]),
                )

    def _import_environment_snapshots(self, snapshots: list[Any]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for item in snapshots:
            source_id = item.get("snapshot_id")
            new_id = self.store.upsert_env_snapshot(dict(item))
            if source_id:
                mapping[str(source_id)] = str(new_id)
        return mapping

    def _import_artifacts(
        self,
        new_root: str,
        new_project: str,
        manifest: Mapping[str, Any],
        files: Mapping[str, bytes],
        *,
        import_root: Path,
        active_workspace: Path,
        cell_map: Mapping[str, str],
        env_map: Mapping[str, str],
    ) -> tuple[dict[str, str], dict[str, str], list[tuple[str, bytes]]]:
        artifact_map: dict[str, str] = {}
        version_map: dict[str, str] = {}
        live_artifacts: list[tuple[str, bytes]] = []
        for artifact_index, artifact in enumerate(manifest.get("artifacts") or []):
            source_artifact = str(artifact.get("artifact_id") or "")
            filename = _safe_artifact_filename(artifact.get("filename"))
            live_path = (active_workspace / filename).resolve()
            try:
                live_path.relative_to(active_workspace)
            except ValueError as error:
                raise SessionPackageError(
                    "artifact live path escapes the imported workspace"
                ) from error
            new_artifact: str | None = None
            newest_source = str(artifact.get("latest_version_id") or "")
            newest_new: str | None = None
            newest_payload: bytes | None = None
            for version_index, version in enumerate(artifact.get("versions") or []):
                if not version.get("available"):
                    continue
                digest = str(version["snapshot_sha256"])
                payload = files[f"artifact-data/{digest}"]
                destination = (
                    import_root
                    / "artifacts"
                    / f"{artifact_index:06d}-{version_index:06d}-{digest}.bin"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as handle:
                    handle.write(payload)
                os.chmod(destination, 0o600)
                record = self.store.save_artifact(
                    path=str(live_path),
                    snapshot_path=str(destination),
                    filename=filename,
                    content_type=version.get("content_type")
                    or artifact.get("content_type"),
                    size_bytes=len(payload),
                    checksum=digest,
                    producing_cell_id=cell_map.get(
                        str(version.get("producing_cell_id") or "")
                    ),
                    frame_id=new_root,
                    root_frame_id=new_root,
                    project_id=new_project,
                    artifact_id=new_artifact,
                    is_user_upload=bool(artifact.get("is_user_upload")),
                    priority=int(artifact.get("priority") or 0),
                    env_snapshot_id=env_map.get(
                        str(version.get("env_snapshot_id") or "")
                    ),
                )
                new_artifact = str(record["artifact_id"])
                if source_artifact:
                    artifact_map[source_artifact] = new_artifact
                source_version = str(version.get("version_id") or "")
                if source_version:
                    version_map[source_version] = str(record["version_id"])
                if source_version == newest_source:
                    newest_new = str(record["version_id"])
                    newest_payload = payload
            if new_artifact and newest_new:
                self.store.set_latest_version(new_artifact, newest_new)
                self.store.set_priority(
                    new_artifact, int(artifact.get("priority") or 0)
                )
                if newest_payload is not None:
                    live_artifacts.append((filename, newest_payload))
        return artifact_map, version_map, live_artifacts

    def _import_lineage(
        self,
        lineage: Mapping[str, Any],
        *,
        version_map: Mapping[str, str],
        cell_map: Mapping[str, str],
        new_root: str,
    ) -> None:
        for edge in lineage.get("edges") or []:
            source = version_map.get(str(edge.get("input_version_id") or ""))
            target = version_map.get(str(edge.get("output_version_id") or ""))
            if source and target:
                self.store.add_lineage_edge(
                    input_version_id=source,
                    output_version_id=target,
                    producing_cell_id=cell_map.get(
                        str(edge.get("producing_cell_id") or "")
                    ),
                    frame_id=new_root,
                )

    def _import_generations(
        self,
        new_root: str,
        branch_map: Mapping[str, str],
        generations: list[Any],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}
        ordered = sorted(
            generations,
            key=lambda item: (
                str(item.get("branch_id") or ""),
                str(item.get("language") or ""),
                int(item.get("ordinal") or 0),
            ),
        )
        for item in ordered:
            source_id = str(item.get("generation_id") or "")
            branch_id = branch_map.get(str(item.get("branch_id") or ""), new_root)
            record = self.store.create_kernel_generation(
                root_frame_id=new_root,
                branch_id=branch_id,
                language=str(item.get("language") or "python"),
                environment={
                    "imported": True,
                    "source_environment_name": _safe_text(
                        (item.get("environment") or {}).get("environment_name")
                        if isinstance(item.get("environment"), Mapping)
                        else ""
                    ),
                    "trusted": False,
                },
                bootstrap={
                    "imported": True,
                    "view_only": True,
                    "trusted": False,
                    "sidecars": [],
                    "init_hooks": [],
                },
                worker_pid=None,
                owner_instance_id=None,
                state="starting",
                started_at=item.get("started_at"),
            )
            source_started = item.get("started_at")
            source_ended = item.get("ended_at") or item.get("last_activity_at")
            finish_fields: dict[str, Any] = {}
            if source_started is not None or source_ended is not None:
                finish_fields["ended_at"] = max(
                    int(source_ended or source_started or 0),
                    int(source_started or 0),
                )
            self.store.finish_kernel_generation(
                record["generation_id"],
                state="released",
                reason="imported_historical_generation",
                **finish_fields,
            )
            if source_id:
                mapping[source_id] = str(record["generation_id"])
        return mapping

    def _import_workspace(
        self, snapshots: Mapping[str, Any], files: Mapping[str, bytes]
    ) -> dict[str, str]:
        projection = snapshots.get("workspace") or {}
        mapping: dict[str, str] = {}
        for source_id, tree_id in sorted((projection.get("tree_map") or {}).items()):
            tree = self._load_json(
                files[f"workspace/trees/{tree_id}.json"],
                f"workspace tree {tree_id}",
            )
            for entry in tree.get("entries") or []:
                digest = str(entry["blob"])
                actual = self.cas.put_blob(files[f"workspace/blobs/{digest}"])
                if actual != digest:
                    raise SessionPackageError("workspace blob import checksum mismatch")
            stored = self.cas.put_tree(
                tree.get("entries") or [], skipped=tree.get("skipped") or []
            )
            if stored["tree_id"] != tree_id:
                raise SessionPackageError("workspace tree import checksum mismatch")
            mapping[str(source_id)] = str(tree_id)
        return mapping

    def _import_snapshots(
        self,
        new_root: str,
        *,
        source_root: str,
        source_project: str,
        new_project: str,
        snapshots: Mapping[str, Any],
        branch_map: Mapping[str, str],
        tree_map: Mapping[str, str],
        artifact_map: Mapping[str, str],
        version_map: Mapping[str, str],
        generation_map: Mapping[str, str],
        cell_map: Mapping[str, str],
        message_map: Mapping[str, str],
        revision_map: Mapping[int, int],
    ) -> dict[str, str]:
        branches = list(snapshots.get("branches") or [])
        checkpoints = list(snapshots.get("checkpoints") or [])
        checkpoint_states = {
            str(item.get("checkpoint_id")): item
            for item in snapshots.get("checkpoint_states") or []
            if item.get("checkpoint_id")
        }
        checkpoint_map = {
            str(item["checkpoint_id"]): f"cp-{uuid.uuid4().hex[:16]}"
            for item in checkpoints
            if item.get("checkpoint_id")
        }
        self.store.ensure_session_branch(root_frame_id=new_root, branch_id=new_root)

        by_branch: dict[str, list[dict[str, Any]]] = {}
        for item in checkpoints:
            by_branch.setdefault(str(item.get("branch_id") or source_root), []).append(
                item
            )
        for items in by_branch.values():
            items.sort(
                key=lambda item: (
                    int(item.get("created_at") or 0),
                    str(item.get("checkpoint_id") or ""),
                )
            )

        branch_records = {str(item.get("branch_id")): item for item in branches}
        # The root chain establishes every base checkpoint a child branch may
        # reference.  Children are then created topologically from those exact
        # immutable bases; no branch or checkpoint is ever inserted twice.
        self._create_checkpoint_chain(
            new_root,
            source_root,
            by_branch.pop(source_root, []),
            branch_map=branch_map,
            checkpoint_map=checkpoint_map,
            tree_map=tree_map,
            version_map=version_map,
            artifact_map=artifact_map,
            generation_map=generation_map,
            cell_map=cell_map,
            message_map=message_map,
            revision_map=revision_map,
            checkpoint_states=checkpoint_states,
            source_root=source_root,
            source_project=source_project,
            new_project=new_project,
        )
        remaining = set(branch_map) - {source_root}
        while remaining:
            progressed = False
            for source_branch in sorted(tuple(remaining)):
                record = branch_records.get(source_branch)
                if record is None:
                    raise SessionPackageError("snapshot branch metadata is missing")
                parent = str(record.get("parent_branch_id") or source_root)
                base = str(record.get("base_checkpoint_id") or "")
                if parent not in (set(branch_map) - remaining):
                    continue
                mapped_base = checkpoint_map.get(base)
                if (
                    not mapped_base
                    or self.store.get_session_checkpoint(mapped_base) is None
                ):
                    raise SessionPackageError("snapshot branch base is unavailable")
                self.store.fork_session_branch(
                    root_frame_id=new_root,
                    from_checkpoint_id=mapped_base,
                    branch_id=branch_map[source_branch],
                    name=_safe_text(record.get("name") or "Imported branch"),
                )
                self._create_checkpoint_chain(
                    new_root,
                    source_branch,
                    by_branch.get(source_branch, []),
                    branch_map=branch_map,
                    checkpoint_map=checkpoint_map,
                    tree_map=tree_map,
                    version_map=version_map,
                    artifact_map=artifact_map,
                    generation_map=generation_map,
                    cell_map=cell_map,
                    message_map=message_map,
                    revision_map=revision_map,
                    checkpoint_states=checkpoint_states,
                    source_root=source_root,
                    source_project=source_project,
                    new_project=new_project,
                )
                remaining.remove(source_branch)
                progressed = True
            if not progressed:
                raise SessionPackageError(
                    "snapshot branch graph is cyclic or incomplete"
                )
        return checkpoint_map

    def _create_checkpoint_chain(
        self,
        new_root: str,
        source_branch: str,
        checkpoints: list[dict[str, Any]],
        **maps: Any,
    ) -> None:
        branch_map = maps["branch_map"]
        checkpoint_map = maps["checkpoint_map"]
        pending = list(checkpoints)
        while pending:
            current = self.store.get_session_branch(branch_map[source_branch])
            current_head = (current or {}).get("head_checkpoint_id")
            item = next(
                (
                    candidate
                    for candidate in pending
                    if (
                        checkpoint_map.get(
                            str(candidate.get("parent_checkpoint_id") or "")
                        )
                        if candidate.get("parent_checkpoint_id")
                        else None
                    )
                    == current_head
                ),
                None,
            )
            if item is None:
                raise SessionPackageError(
                    "checkpoint chain is cyclic, branching, or incomplete"
                )
            source_checkpoint = str(item.get("checkpoint_id") or "")
            parent = item.get("parent_checkpoint_id")
            mapped_parent = checkpoint_map.get(str(parent)) if parent else None
            source_kind = item.get("source_kind")
            source_id = item.get("source_id")
            if source_kind == "cell":
                source_id = maps["cell_map"].get(str(source_id or ""))
            elif source_kind == "message":
                source_id = maps["message_map"].get(str(source_id or ""))
            if source_kind and not source_id:
                source_kind = None
                source_id = None
            artifact_versions = [
                maps["version_map"][str(version)]
                for version in item.get("artifact_versions") or []
                if str(version) in maps["version_map"]
            ]
            cell_cursor = self._map_cursor(
                item.get("cell_cursor"), maps["revision_map"]
            )
            metadata = self._safe_import_checkpoint_metadata(
                item.get("metadata"),
                checkpoint_map=checkpoint_map,
                revision_map=maps["revision_map"],
            )
            self.store.create_session_checkpoint(
                root_frame_id=new_root,
                branch_id=branch_map[source_branch],
                checkpoint_id=checkpoint_map[source_checkpoint],
                parent_checkpoint_id=mapped_parent,
                reason=str(item.get("reason") or "imported"),
                workspace_tree_id=maps["tree_map"].get(
                    str(item.get("workspace_tree_id") or "")
                ),
                action_cursor=item.get("action_cursor"),
                message_cursor=item.get("message_cursor"),
                cell_cursor=cell_cursor,
                artifact_versions=artifact_versions,
                environment_pins={},
                generation_refs={},
                capability_state=self._safe_import_capabilities(
                    item.get("capability_state") or {},
                    maps["source_project"],
                    maps["new_project"],
                    maps["source_root"],
                    new_root,
                ),
                permission_state=self._safe_import_permissions(
                    item.get("permission_state") or {},
                    maps["source_project"],
                    maps["new_project"],
                    maps["source_root"],
                    new_root,
                ),
                recovery_recipe={
                    "version": 1,
                    "status": "quarantined_import",
                    "steps": [],
                    "required_symbols": [],
                    "uncertainties": ["untrusted_session_package"],
                    "replay_allowed": False,
                },
                metadata=metadata,
                source_kind=source_kind,
                source_id=source_id,
                internal=bool(item.get("internal")),
            )
            state = maps["checkpoint_states"].get(source_checkpoint)
            if state is not None:
                source_state_checkpoint = str(state.get("source_checkpoint_id") or "")
                mapped_source_state = (
                    checkpoint_map.get(source_state_checkpoint)
                    if source_state_checkpoint in maps["checkpoint_states"]
                    else None
                )
                self.store.import_quarantined_checkpoint_state(
                    dict(state),
                    checkpoint_id=checkpoint_map[source_checkpoint],
                    root_frame_id=new_root,
                    branch_id=branch_map[source_branch],
                    project_id=maps["new_project"],
                    artifact_id_map=maps["artifact_map"],
                    source_checkpoint_id=mapped_source_state,
                )
            pending.remove(item)

    def _import_policies(
        self,
        new_root: str,
        new_project: str,
        permissions: Mapping[str, Any],
        capabilities: Mapping[str, Any],
    ) -> None:
        for scope, scope_id, rows in (
            ("project", new_project, permissions.get("project") or []),
            ("conversation", new_root, permissions.get("conversation") or []),
        ):
            for item in rows:
                decision = _import_permission_decision(item.get("decision"))
                self.store.set_permission_rule(
                    scope=scope,
                    scope_id=scope_id,
                    tool=str(item.get("tool") or "*"),
                    pattern=str(item.get("pattern") or "*"),
                    decision=decision,
                )
        for item in capabilities.get("states") or []:
            source_scope = str(item.get("scope") or "")
            if source_scope not in {"project", "session"}:
                continue
            requested = bool(item.get("enabled"))
            metadata = dict(item.get("metadata") or {})
            metadata.update(
                {
                    "imported": True,
                    "imported_requested_enabled": requested,
                    "requires_explicit_enable": requested,
                }
            )
            self.store.set_capability_enabled(
                str(item.get("kind") or "skill"),
                str(item.get("name") or "imported"),
                False if requested else False,
                scope="project" if source_scope == "project" else "session",
                scope_id=new_project if source_scope == "project" else new_root,
                metadata=metadata,
            )

    def _import_plans_review_memory(
        self,
        new_root: str,
        new_project: str,
        *,
        plans: Mapping[str, Any],
        review: Mapping[str, Any],
        memories: Mapping[str, Any],
        artifact_map: Mapping[str, str],
    ) -> None:
        for item in reversed(plans.get("plans") or []):
            plan = self.store.create_plan(
                frame_id=new_root,
                project_id=new_project,
                title=item.get("title"),
                rationale=item.get("rationale"),
                confidence=item.get("confidence"),
                steps=list(item.get("steps") or []),
                artifact_id=artifact_map.get(str(item.get("artifact_id") or "")),
                status=str(item.get("status") or "draft"),
            )
            if item.get("step_status"):
                self.store.update_plan(
                    plan["plan_id"], step_status=dict(item["step_status"])
                )
        for item in memories.get("memories") or []:
            self.store.add_memory(
                project_id=new_project,
                block=str(item.get("block") or "general"),
                content=str(item.get("content") or ""),
            )
        for item in review.get("annotations") or []:
            artifact_id = artifact_map.get(str(item.get("artifact_id") or ""))
            if not artifact_id:
                continue
            annotation = self.store.add_annotation(
                root_frame_id=new_root,
                artifact_id=artifact_id,
                artifact_name=item.get("artifact_name"),
                rel_x=float(item.get("rel_x") or 0),
                rel_y=float(item.get("rel_y") or 0),
                body=str(item.get("body") or ""),
            )
            status = str(item.get("status") or "open")
            if status != "open":
                self.store.update_annotation(annotation["annotation_id"], status=status)
        for item in review.get("activity_steps") or []:
            source_status = str(item.get("status") or "done").casefold()
            status = (
                "done"
                if source_status in {"done", "completed", "pass", "passed"}
                else "error"
                if source_status in {"error", "failed", "failure"}
                else "stopped"
            )
            step_id = f"s-{uuid.uuid4().hex[:12]}"
            self.store.add_step(
                step_id=step_id,
                frame_id=new_root,
                kind=str(item.get("kind") or "review"),
                title=_safe_text(item.get("title") or "Imported evidence review"),
                input=(
                    dict(item.get("input") or {})
                    if isinstance(item.get("input"), Mapping)
                    else {}
                ),
                status=status,
            )
            self.store.update_step(
                step_id,
                status=status,
                output=(
                    dict(item.get("output") or {})
                    if isinstance(item.get("output"), Mapping)
                    else {}
                ),
                summary=_safe_text(item.get("summary") or ""),
            )
        settings = review.get("settings") or {}
        requested_auto = settings.get("auto_review")
        requested_model = settings.get("reviewer_model")
        has_settings_step = any(
            str(item.get("kind") or "").casefold() == "review_settings"
            for item in review.get("activity_steps") or []
        )
        if (requested_auto is not None or requested_model) and not has_settings_step:
            step_id = f"s-{uuid.uuid4().hex[:12]}"
            self.store.add_step(
                step_id=step_id,
                frame_id=new_root,
                kind="review_settings",
                title="Imported review settings (inactive)",
                input={
                    "requested_auto_review": bool(requested_auto),
                    "requested_reviewer_model": _safe_text(requested_model or ""),
                    "active": False,
                },
                status="done",
            )
            self.store.update_step(
                step_id,
                status="done",
                output={"active": False, "reason": "untrusted_session_package"},
                summary="Review automation remains disabled after import",
            )

    def _import_operations_and_recovery(
        self,
        new_root: str,
        *,
        snapshots: Mapping[str, Any],
        branch_map: Mapping[str, str],
        checkpoint_map: Mapping[str, str],
        generation_map: Mapping[str, str],
    ) -> None:
        for item in reversed(snapshots.get("operations") or []):
            branch = branch_map.get(str(item.get("branch_id") or ""), new_root)
            self.store.record_snapshot_operation(
                root_frame_id=new_root,
                branch_id=branch,
                kind=str(item.get("kind") or "imported"),
                status=str(item.get("status") or "completed"),
                preview=item.get("preview") or {},
                source_checkpoint_id=checkpoint_map.get(
                    str(item.get("source_checkpoint_id") or "")
                ),
                target_checkpoint_id=checkpoint_map.get(
                    str(item.get("target_checkpoint_id") or "")
                ),
                error=item.get("error"),
                finished=item.get("finished_at") is not None,
            )
        recovery_ids: dict[str, str] = {}
        for item in snapshots.get("recovery_journal") or []:
            source_recovery = str(item.get("recovery_id") or "imported")
            recovery_ids.setdefault(
                source_recovery, f"recovery-{uuid.uuid4().hex[:16]}"
            )
            self.store.append_recovery_event(
                recovery_id=recovery_ids[source_recovery],
                root_frame_id=new_root,
                branch_id=branch_map.get(str(item.get("branch_id") or ""), new_root),
                phase=str(item.get("phase") or "imported"),
                status=str(item.get("status") or "partial"),
                detail=item.get("detail") or {},
                source_generation_id=generation_map.get(
                    str(item.get("source_generation_id") or "")
                ),
                candidate_generation_id=generation_map.get(
                    str(item.get("candidate_generation_id") or "")
                ),
                sequence=int(item.get("sequence") or 0),
                created_at=item.get("created_at"),
            )

    @staticmethod
    def _safe_import_permissions(
        state: Any,
        source_project: str,
        new_project: str,
        source_root: str,
        new_root: str,
    ) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            return {}
        output: dict[str, Any] = {}
        for scope in ("project", "conversation"):
            rows = []
            for raw in state.get(scope) or []:
                item = dict(raw)
                item["scope_id"] = new_project if scope == "project" else new_root
                item["decision"] = _import_permission_decision(item.get("decision"))
                rows.append(item)
            output[scope] = rows
        del source_project, source_root
        return output

    @staticmethod
    def _safe_import_capabilities(
        state: Any,
        source_project: str,
        new_project: str,
        source_root: str,
        new_root: str,
    ) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            return {}
        rows = []
        for raw in state.get("states") or []:
            item = dict(raw)
            scope = item.get("scope")
            if scope == "project" and item.get("scope_id") == source_project:
                item["scope_id"] = new_project
            elif scope == "session" and item.get("scope_id") == source_root:
                item["scope_id"] = new_root
            else:
                continue
            item["metadata"] = {
                **dict(item.get("metadata") or {}),
                "imported_requested_enabled": bool(item.get("enabled")),
                "requires_explicit_enable": bool(item.get("enabled")),
            }
            item["enabled"] = False
            rows.append(item)
        return {"version": 1, "states": rows}

    @classmethod
    def _safe_import_checkpoint_metadata(
        cls,
        state: Any,
        *,
        checkpoint_map: Mapping[str, str],
        revision_map: Mapping[int, int],
    ) -> dict[str, Any]:
        """Preserve only branch-projection semantics from untrusted metadata."""

        source = state if isinstance(state, Mapping) else {}
        output: dict[str, Any] = {
            "imported": True,
            "explicit_recovery_required": True,
            "trust_state": "quarantined",
        }
        for field in ("reverted_to", "undo_checkpoint_id"):
            value = source.get(field)
            if isinstance(value, str) and value in checkpoint_map:
                output[field] = checkpoint_map[value]
        projection = source.get("history_projection")
        if isinstance(projection, Mapping):
            base = projection.get("base_checkpoint_id")
            cursors = projection.get("resume_cursors")
            if (
                isinstance(base, str)
                and base in checkpoint_map
                and isinstance(cursors, Mapping)
            ):
                output["history_projection"] = {
                    "version": 1,
                    "base_checkpoint_id": checkpoint_map[base],
                    "resume_cursors": {
                        "action_cursor": cls._safe_integer_cursor(
                            cursors.get("action_cursor")
                        ),
                        "message_cursor": cls._safe_integer_cursor(
                            cursors.get("message_cursor")
                        ),
                        "cell_cursor": cls._map_cursor(
                            cursors.get("cell_cursor"), revision_map
                        ),
                    },
                }
        return output

    @staticmethod
    def _safe_integer_cursor(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise SessionPackageError("checkpoint history cursor is invalid")
        cursor = int(value)
        if cursor < 0:
            raise SessionPackageError("checkpoint history cursor is invalid")
        return cursor

    @staticmethod
    def _map_cursor(value: Any, revision_map: Mapping[int, int]) -> int | None:
        if value is None:
            return None
        source = int(value)
        eligible = [mapped for old, mapped in revision_map.items() if old <= source]
        return max(eligible) if eligible else 0

    @classmethod
    def _remap_nested(cls, value: Any, mapping: Mapping[str, str]) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): cls._remap_nested(item, mapping)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._remap_nested(item, mapping) for item in value]
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    @staticmethod
    def _source_branch_head(
        source_branch: str,
        branches: list[dict[str, Any]],
        checkpoint_map: Mapping[str, str],
    ) -> str | None:
        for branch in branches:
            if str(branch.get("branch_id")) == source_branch:
                return checkpoint_map.get(str(branch.get("head_checkpoint_id") or ""))
        return None

    def _workspace_blob_ids(
        self, workspace: Mapping[str, Any], files: Mapping[str, bytes]
    ) -> set[str]:
        output: set[str] = set()
        for tree_id in (workspace.get("tree_map") or {}).values():
            tree = self._load_json(
                files[f"workspace/trees/{tree_id}.json"],
                f"workspace tree {tree_id}",
            )
            output.update(
                str(entry.get("blob"))
                for entry in tree.get("entries") or []
                if entry.get("blob")
            )
        return output

    @staticmethod
    def _materialize_live_artifacts(
        workspace: Path, artifacts: list[tuple[str, bytes]]
    ) -> None:
        workspace = workspace.expanduser().resolve()
        for filename, payload in artifacts:
            safe_name = _safe_artifact_filename(filename)
            target = (workspace / safe_name).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as error:
                raise SessionPackageError(
                    "artifact live path escapes the imported workspace"
                ) from error
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            os.chmod(target, 0o600)

    def _release_import_cas(self, tree_ids: set[str], blob_ids: set[str]) -> None:
        if not tree_ids and not blob_ids:
            return
        self.cas.release_trees(
            tree_ids,
            retained_tree_ids_provider=self.store.retained_workspace_tree_ids,
        )
        with self.cas.locked():
            referenced: set[str] = set()
            for manifest_path in self.cas.trees_dir.glob("*.json"):
                try:
                    tree = json.loads(manifest_path.read_text("utf-8"))
                except (OSError, TypeError, ValueError):
                    continue
                referenced.update(
                    str(entry.get("blob"))
                    for entry in tree.get("entries") or []
                    if isinstance(entry, Mapping) and entry.get("blob")
                )
            for blob_id in blob_ids - referenced:
                (self.cas.blobs_dir / blob_id).unlink(missing_ok=True)

    @staticmethod
    def _remove_private_import_root(path: Path) -> None:
        """Remove only the service-created data-dir import staging tree."""

        SessionPackageService._remove_private_tree(path)

    @staticmethod
    def _remove_private_workspace(path: Path) -> None:
        """Remove only the fresh workspace allocated to a failed import."""

        SessionPackageService._remove_private_tree(path)

    @staticmethod
    def _remove_private_tree(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        for child in sorted(
            path.rglob("*"), key=lambda item: len(item.parts), reverse=True
        ):
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "PACKAGE_FORMAT",
    "PACKAGE_SCHEMA_VERSION",
    "SessionPackageError",
    "SessionPackageService",
]
