"""Recovery journal projection and verified pipeline composition.

The kernel recovery algorithm is intentionally callback-driven.  This service
binds its journal to durable Store ports, exposes a small safe status view, and
describes which recovery actions are currently possible.  It never claims a
checkpoint is restorable unless both a workspace tree and a complete bootstrap
manifest are present.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openai4s.kernel.recovery import (
    BootstrapManifest,
    KernelRecoveryOrchestrator,
    RecoveryRecipe,
    RecoveryStep,
)

_STATUSES = frozenset(
    {"started", "completed", "skipped", "partial", "failed", "cancelled"}
)


class RecoveryActionError(RuntimeError):
    """A requested mutation is unavailable under the current durable state."""


@dataclass(frozen=True)
class RecoveryActionPlan:
    action_id: str
    recovery_id: str
    root_frame_id: str
    branch_id: str
    checkpoint_id: str | None
    manifests: tuple[BootstrapManifest, ...]
    source_generation_ids: Mapping[str, str | None]
    recipe: RecoveryRecipe


class RecoveryStore(Protocol):
    def append_recovery_event(self, **fields: Any) -> dict:
        ...

    def list_recovery_events(self, **filters: Any) -> list[dict]:
        ...

    def get_session_branch(self, branch_id: str) -> dict | None:
        ...

    def get_session_checkpoint(self, checkpoint_id: str) -> dict | None:
        ...

    def latest_kernel_generation(
        self, root_frame_id: str, language: str, *, branch_id: str | None = None
    ) -> dict | None:
        ...


class RecoveryControlService:
    """Durable recovery status/actions with a journal-bound pipeline factory."""

    def __init__(
        self,
        store: RecoveryStore,
        *,
        workspace_tree_exists: Callable[[str], bool] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        payload_chars: int = 20_000,
    ) -> None:
        if payload_chars < 256:
            raise ValueError("payload_chars must be at least 256")
        self.store = store
        self._workspace_tree_exists = workspace_tree_exists or (lambda _tree_id: False)
        self._event_sink = event_sink or (lambda _event: None)
        self.payload_chars = payload_chars

    def record(self, event: Mapping[str, Any]) -> dict:
        """Append one orchestrator journal event without accepting opaque fields."""

        required = ("recovery_id", "root_frame_id", "phase", "status")
        missing = [name for name in required if not str(event.get(name) or "").strip()]
        if missing:
            raise ValueError("recovery event missing: " + ", ".join(missing))
        phase = str(event["phase"]).strip().lower()
        status = str(event["status"]).strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", phase) is None:
            raise ValueError(f"invalid recovery phase: {phase!r}")
        if status not in _STATUSES:
            raise ValueError(f"unknown recovery status: {status!r}")
        stored = self.store.append_recovery_event(
            recovery_id=str(event["recovery_id"]),
            root_frame_id=str(event["root_frame_id"]),
            branch_id=(str(event["branch_id"]) if event.get("branch_id") else None),
            source_generation_id=(
                str(event["source_generation_id"])
                if event.get("source_generation_id")
                else None
            ),
            candidate_generation_id=(
                str(event["candidate_generation_id"])
                if event.get("candidate_generation_id")
                else None
            ),
            phase=phase,
            status=status,
            # Recovery errors can contain echoed environment/HTTP details. Do
            # not persist credential-shaped fields merely to redact them later.
            detail=_redact(event.get("detail") or {}),
        )
        try:
            self._event_sink(
                {
                    "type": "recovery_log",
                    "root_frame_id": str(event["root_frame_id"]),
                    "branch_id": (
                        str(event["branch_id"])
                        if event.get("branch_id")
                        else str(event["root_frame_id"])
                    ),
                    "recovery_id": str(event["recovery_id"]),
                    "phase": phase,
                    "status": status,
                    "message": f"{phase}: {status}",
                    "at": stored.get("created_at"),
                }
            )
        except Exception:  # noqa: BLE001 - durable journal already committed
            pass
        return stored

    def pipeline(self, **ports: Any) -> KernelRecoveryOrchestrator:
        """Build a recovery pipeline whose every phase is durably journaled."""

        if "journal" in ports:
            raise ValueError("recovery journal is owned by RecoveryControlService")
        return KernelRecoveryOrchestrator(journal=self.record, **ports)

    def status(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        recovery_id: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        root_frame_id = _required("root_frame_id", root_frame_id)
        branch_id = branch_id or root_frame_id
        events = self.store.list_recovery_events(
            recovery_id=recovery_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            limit=limit,
            newest=True,
        )
        grouped: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            grouped[str(event.get("recovery_id") or "")].append(event)
        attempts = [self._attempt(rows) for rows in grouped.values() if rows]
        attempts.sort(
            key=lambda item: (
                item.get("updated_at") or 0,
                item.get("recovery_id") or "",
            ),
            reverse=True,
        )
        generations = {
            language: self.store.latest_kernel_generation(
                root_frame_id,
                language,
                branch_id=branch_id,
            )
            for language in ("python", "r")
        }
        current = attempts[0] if attempts else None
        state = (
            current["state"]
            if current is not None
            else _generation_state(generations.values())
        )
        return {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "state": state,
            "current": current,
            "attempts": attempts,
            "generations": {
                key: _public_generation(value) for key, value in generations.items()
            },
        }

    def actions(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        root_frame_id = _required("root_frame_id", root_frame_id)
        branch_id = branch_id or root_frame_id
        status = self.status(root_frame_id, branch_id=branch_id)
        branch = self.store.get_session_branch(branch_id)
        if branch is not None and branch.get("root_frame_id") != root_frame_id:
            raise PermissionError("branch belongs to another session")
        checkpoint = (
            self.store.get_session_checkpoint(branch.get("head_checkpoint_id"))
            if branch and branch.get("head_checkpoint_id")
            else None
        )
        restorable, unavailable = _restorable(checkpoint)
        if restorable:
            try:
                tree_exists = self._workspace_tree_exists(
                    str(checkpoint.get("workspace_tree_id"))
                )
            except Exception:  # noqa: BLE001 - CAS verification fails closed
                tree_exists = False
            if not tree_exists:
                restorable = False
                unavailable = "checkpoint workspace tree is missing or corrupt"
        busy = status["state"] in {
            "restoring",
            "bootstrapping",
            "hydrating",
            "replaying",
            "validating",
        }
        recoverable_state = status["state"] in {
            "none",
            "ended",
            "partial",
            "failed",
        }
        latest_state = (status.get("current") or {}).get("state")
        actions = [
            _action(
                "restore",
                restorable and recoverable_state and not busy,
                (
                    unavailable
                    if not restorable
                    else "recovery already running"
                    if busy
                    else "kernel is already active"
                    if not recoverable_state
                    else None
                ),
                requires_ticket=True,
            ),
            _action(
                "retry",
                restorable and latest_state in {"partial", "failed"} and not busy,
                (
                    unavailable
                    if not restorable
                    else "latest recovery is not partial or failed"
                    if latest_state not in {"partial", "failed"}
                    else "recovery already running"
                    if busy
                    else None
                ),
                requires_ticket=True,
            ),
            _action("inspect_log", True, None),
            _action("continue_view_only", True, None),
            _action(
                "restart_fresh",
                not busy,
                "recovery already running" if busy else None,
                requires_ticket=True,
                requires_confirmation=True,
            ),
        ]
        return {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "checkpoint_id": (checkpoint.get("checkpoint_id") if checkpoint else None),
            "state": status["state"],
            "actions": actions,
        }

    def prepare_action(
        self,
        root_frame_id: str,
        action_id: str,
        *,
        branch_id: str | None = None,
        confirmed: bool = False,
        fresh_manifests: tuple[BootstrapManifest, ...] = (),
    ) -> RecoveryActionPlan:
        """Re-check policy and freeze the exact inputs for one mutation.

        Callers invoke this only *after* acquiring their recovery execution
        ticket.  That closes the queue-time TOCTOU window between the read-only
        actions projection and the actual mutation.
        """

        root_frame_id = _required("root_frame_id", root_frame_id)
        action_id = _required("action_id", action_id).lower()
        branch_id = branch_id or root_frame_id
        projection = self.actions(root_frame_id, branch_id=branch_id)
        action = next(
            (item for item in projection["actions"] if item["id"] == action_id),
            None,
        )
        if action is None:
            raise RecoveryActionError(f"unknown recovery action: {action_id}")
        if not action.get("enabled"):
            raise RecoveryActionError(
                str(action.get("reason") or "recovery action is disabled")
            )
        if action.get("requires_confirmation") and not confirmed:
            raise RecoveryActionError(f"{action_id} requires explicit confirmation")

        checkpoint_id = projection.get("checkpoint_id")
        if action_id == "restart_fresh":
            if not fresh_manifests:
                raise RecoveryActionError("fresh restart has no resolvable runtime")
            manifests = tuple(fresh_manifests)
            recipe = RecoveryRecipe()
            sources = {
                manifest.language: (
                    (
                        self.store.latest_kernel_generation(
                            root_frame_id,
                            manifest.language,
                            branch_id=branch_id,
                        )
                        or {}
                    ).get("generation_id")
                )
                for manifest in manifests
            }
            checkpoint_id = None
        elif action_id in {"restore", "retry"}:
            checkpoint = (
                self.store.get_session_checkpoint(str(checkpoint_id))
                if checkpoint_id
                else None
            )
            restorable, reason = _restorable(checkpoint)
            if not restorable or checkpoint is None:
                raise RecoveryActionError(reason or "checkpoint is not restorable")
            refs = checkpoint.get("generation_refs") or {}
            parsed: list[BootstrapManifest] = []
            sources: dict[str, str | None] = {}
            for language in sorted(
                refs,
                key=lambda item: (item != "python", item != "r", str(item)),
            ):
                ref = refs[language]
                raw = (
                    ref.get("bootstrap_manifest") or ref.get("bootstrap")
                    if isinstance(ref, Mapping)
                    else None
                )
                try:
                    manifest = BootstrapManifest.from_record(raw or {})
                except (TypeError, ValueError) as error:
                    raise RecoveryActionError(
                        f"invalid {language} bootstrap manifest: {error}"
                    ) from error
                if manifest.language != str(language):
                    raise RecoveryActionError(
                        f"bootstrap language mismatch for {language}"
                    )
                parsed.append(manifest)
                sources[manifest.language] = (
                    str(ref["generation_id"])
                    if isinstance(ref, Mapping) and ref.get("generation_id")
                    else None
                )
            if not parsed:
                raise RecoveryActionError("checkpoint has no runtime manifests")
            manifests = tuple(parsed)
            recipe = _parse_recipe(
                checkpoint.get("recovery_recipe"),
                cell_cursor=int(checkpoint.get("cell_cursor") or 0),
            )
        else:
            raise RecoveryActionError(f"action {action_id} is read-only")

        return RecoveryActionPlan(
            action_id=action_id,
            recovery_id=f"recovery-{uuid.uuid4().hex[:16]}",
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            checkpoint_id=(str(checkpoint_id) if checkpoint_id else None),
            manifests=manifests,
            source_generation_ids=sources,
            recipe=recipe,
        )

    def _attempt(self, rows: list[dict]) -> dict[str, Any]:
        rows.sort(
            key=lambda item: (
                item.get("created_at") or 0,
                item.get("sequence") or 0,
                item.get("entry_id") or "",
            )
        )
        latest = rows[-1]
        return {
            "recovery_id": latest.get("recovery_id"),
            "state": _journal_state(rows),
            "phase": latest.get("phase"),
            "phase_status": latest.get("status"),
            "source_generation_id": latest.get("source_generation_id"),
            "candidate_generation_id": latest.get("candidate_generation_id"),
            "started_at": rows[0].get("created_at"),
            "updated_at": latest.get("created_at"),
            "events": [
                {
                    "entry_id": row.get("entry_id"),
                    "sequence": row.get("sequence"),
                    "phase": row.get("phase"),
                    "status": row.get("status"),
                    "detail": _bounded_public(row.get("detail"), self.payload_chars),
                    "created_at": row.get("created_at"),
                }
                for row in rows
            ],
        }


def _journal_state(rows: list[dict]) -> str:
    latest = rows[-1]
    phase = str(latest.get("phase") or "")
    status = str(latest.get("status") or "")
    if phase in {"publish", "session"} and status == "completed":
        return "active"
    if status == "partial":
        return "partial"
    if status in {"failed", "cancelled"}:
        return "failed"
    phase_states = {
        "build": "bootstrapping",
        "bootstrap": "bootstrapping",
        "hydrate_workspace": "hydrating",
        "hydrate_artifact": "hydrating",
        "replay": "replaying",
        "validate": "validating",
    }
    return phase_states.get(phase, "restoring")


def _generation_state(generations) -> str:
    states = {
        str(item.get("state") or "")
        for item in generations
        if isinstance(item, Mapping)
    }
    if states & {"active", "busy"}:
        return "active"
    if states & {"partial"}:
        return "partial"
    if states & {"failed", "crashed"}:
        return "failed"
    return "ended" if states else "none"


def _public_generation(value: Mapping[str, Any] | None) -> dict | None:
    if value is None:
        return None
    return {
        key: value.get(key)
        for key in (
            "generation_id",
            "language",
            "ordinal",
            "state",
            "bootstrap_manifest_id",
            "environment_manifest_id",
            "last_activity_at",
            "ended_at",
            "ended_reason",
            "recovered_from_generation_id",
        )
    }


def _restorable(checkpoint: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    if checkpoint is None:
        return False, "no checkpoint exists"
    if not checkpoint.get("workspace_tree_id"):
        return False, "checkpoint has no workspace tree"
    refs = checkpoint.get("generation_refs")
    if not isinstance(refs, Mapping) or not refs:
        return False, "checkpoint has no verifiable bootstrap manifest"
    for language, value in refs.items():
        manifest = (
            value.get("bootstrap_manifest") or value.get("bootstrap")
            if isinstance(value, Mapping)
            else None
        )
        if not isinstance(manifest, Mapping):
            return False, f"checkpoint lacks a verifiable {language} bootstrap manifest"
        try:
            parsed = BootstrapManifest.from_record(manifest)
        except (TypeError, ValueError):
            return False, f"checkpoint has an invalid {language} bootstrap manifest"
        if parsed.language != str(language):
            return False, f"checkpoint has a mismatched {language} bootstrap manifest"
    recipe = checkpoint.get("recovery_recipe")
    if not isinstance(recipe, Mapping) or int(recipe.get("version") or 0) != 1:
        return False, "checkpoint has no supported recovery recipe"
    return True, None


def _parse_recipe(value: Any, *, cell_cursor: int = 0) -> RecoveryRecipe:
    if not isinstance(value, Mapping) or int(value.get("version") or 0) != 1:
        raise RecoveryActionError("checkpoint has an unsupported recovery recipe")
    steps: list[RecoveryStep] = []
    for index, raw in enumerate(value.get("steps") or ()):
        if not isinstance(raw, Mapping):
            raise RecoveryActionError(f"invalid recovery step at index {index}")
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise RecoveryActionError(
                f"recovery step {index} payload must be an object"
            )
        identity = raw.get("step_id")
        if not identity:
            digest = hashlib.sha256(
                json.dumps(
                    raw,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=repr,
                ).encode("utf-8")
            ).hexdigest()[:12]
            identity = f"rs-{index}-{digest}"
        try:
            steps.append(
                RecoveryStep(
                    kind=str(raw.get("kind") or ""),
                    payload=dict(payload),
                    replay_policy=str(raw.get("replay_policy") or "never"),
                    step_id=str(identity),
                )
            )
        except (TypeError, ValueError) as error:
            raise RecoveryActionError(
                f"invalid recovery step at index {index}: {error}"
            ) from error

    required_raw = value.get("required_symbols") or {}
    artifacts_raw = value.get("artifact_hashes") or {}
    environment_raw = value.get("environment_requirements") or {}
    if not all(
        isinstance(item, Mapping)
        for item in (required_raw, artifacts_raw, environment_raw)
    ):
        raise RecoveryActionError("invalid recovery validation requirements")
    coverage = str(value.get("namespace_coverage") or "").lower()
    if not coverage:
        coverage = "unverified" if cell_cursor > 0 else "empty"
    try:
        return RecoveryRecipe(
            steps=tuple(steps),
            required_symbols={
                str(language): tuple(str(name) for name in (names or ()))
                for language, names in required_raw.items()
                if isinstance(names, (list, tuple))
            },
            artifact_hashes={
                str(name): str(digest) for name, digest in artifacts_raw.items()
            },
            environment_requirements=dict(environment_raw),
            namespace_coverage=coverage,
        )
    except ValueError as error:
        raise RecoveryActionError(str(error)) from error


def _action(
    action_id: str,
    enabled: bool,
    reason: str | None,
    *,
    requires_ticket: bool = False,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "enabled": bool(enabled),
        "reason": reason,
        "requires_execution_ticket": requires_ticket,
        "requires_confirmation": requires_confirmation,
    }


def _bounded_public(value: Any, limit: int) -> Any:
    safe = _redact(value)
    encoded = json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    if len(encoded) <= limit:
        return safe
    return {
        "truncated": True,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "original_chars": len(encoded),
        "preview": encoded[: limit - 1] + "…",
    }


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(
                    marker in str(key).casefold()
                    for marker in (
                        "secret",
                        "token",
                        "password",
                        "credential",
                        "api_key",
                    )
                )
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _required(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


__all__ = [
    "RecoveryActionError",
    "RecoveryActionPlan",
    "RecoveryControlService",
    "RecoveryStore",
]
