"""Safe Context and Security projections for the scientific workbench.

These read models deliberately depend on callbacks instead of Gateway.  They
never start a dispatcher or kernel, never expose message content/permission
payloads, and never claim an OS sandbox exists before a real worker reports its
self-test result.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

from openai4s.agent.compaction import estimate_context
from openai4s.llm.capabilities import get_model_capabilities


class WorkbenchStore(Protocol):
    def get_frame(self, frame_id: str) -> dict | None:
        ...

    def latest_kernel_generation(
        self,
        root_frame_id: str,
        language: str,
        *,
        branch_id: str | None = None,
    ) -> dict | None:
        ...

    def active_session_branch(self, root_frame_id: str) -> str:
        ...

    def list_compaction_archives(self, frame_id: str, *, limit: int = 50) -> list[dict]:
        ...

    def delegation_tree(self, root_frame_id: str) -> dict:
        ...


StateProvider = Callable[[str], Any | None]
HistoryProvider = Callable[[str], Sequence[Mapping[str, Any]]]
LLMConfigProvider = Callable[[Any | None], Any]
PendingProvider = Callable[[str], Iterable[Mapping[str, Any]]]
ToolSchemaProvider = Callable[[Any | None], Iterable[Mapping[str, Any]]]


class SessionWorkbenchStateService:
    """Project current context composition and enforced security state."""

    def __init__(
        self,
        store: WorkbenchStore,
        *,
        state_for: StateProvider,
        history_for: HistoryProvider,
        llm_config_for: LLMConfigProvider,
        pending_for: PendingProvider,
        context_window_fallback: int,
        tool_schemas_for: ToolSchemaProvider | None = None,
    ) -> None:
        self.store = store
        self._state_for = state_for
        self._history_for = history_for
        self._llm_config_for = llm_config_for
        self._pending_for = pending_for
        self._context_window_fallback = max(1, int(context_window_fallback))
        self._tool_schemas_for = tool_schemas_for or (lambda _state: ())

    def context(self, root_frame_id: str) -> dict[str, Any]:
        self._root_frame(root_frame_id)
        state = self._state_for(root_frame_id)
        messages = list(getattr(state, "messages", ()) or ())
        if not messages:
            messages = [dict(item) for item in self._history_for(root_frame_id)]
        try:
            tool_schemas = tuple(self._tool_schemas_for(state))
        except Exception:  # noqa: BLE001 - projection remains available
            tool_schemas = ()
        estimate = estimate_context(messages, tool_schemas)
        llm = self._llm_config_for(state)
        token_limit = self._context_window_fallback
        output_reserve = 0
        try:
            capabilities = get_model_capabilities(
                str(getattr(llm, "provider", "") or ""),
                str(getattr(llm, "model", "") or ""),
                base_url=str(getattr(llm, "base_url", "") or ""),
            )
            token_limit = int(
                capabilities.usable_context_tokens
                or capabilities.context_window_tokens
                or token_limit
            )
            output_reserve = int(capabilities.max_output_tokens or 0)
        except Exception:  # noqa: BLE001 - projection keeps a truthful fallback
            pass
        components = estimate.as_dict()
        component_names = (
            "text",
            "images",
            "tool_schemas",
            "tool_calls",
            "tool_results",
            "artifact_refs",
            "wire_state",
        )
        layers = [
            {
                "name": name.replace("_", " ").title(),
                "kind": name,
                "token_count": int(components[name]),
                "status": "active" if components[name] else "empty",
                "compressed": False,
            }
            for name in component_names
        ]
        handoff = any(bool(message.get("compaction_handoff")) for message in messages)
        try:
            archives = self.store.list_compaction_archives(root_frame_id, limit=50)
        except Exception:  # noqa: BLE001 - old/test stores remain compatible
            archives = []
        history = [self._compaction_history(item) for item in archives]
        return {
            "root_frame_id": root_frame_id,
            "token_count": estimate.total,
            "token_limit": token_limit,
            "output_reserve": output_reserve,
            "message_count": len(messages),
            "handoff": handoff,
            "compressed": handoff or bool(history),
            "compaction_count": len(history),
            "compaction_history": history,
            "layers": layers,
        }

    @staticmethod
    def _compaction_history(item: Mapping[str, Any]) -> dict[str, Any]:
        before = item.get("context_before")
        after = item.get("context_after")
        refs = item.get("artifact_refs")
        return {
            "archive_id": str(item.get("archive_id") or "")[:120],
            "created_at": int(item.get("created_at") or 0),
            "branch_id": str(item.get("branch_id") or "")[:120],
            "ledger_cursor": item.get("ledger_cursor"),
            "recovery_pointer": item.get("recovery_pointer"),
            "generation_id": str(item.get("generation_id") or "")[:120],
            "message_count": int(item.get("n_messages") or 0),
            "tokens_before": int(before.get("total") or 0)
            if isinstance(before, Mapping)
            else 0,
            "tokens_after": int(after.get("total") or 0)
            if isinstance(after, Mapping)
            else 0,
            "artifact_refs": [
                {
                    "artifact_id": str(ref.get("artifact_id") or "")[:120],
                    "version_id": str(ref.get("version_id") or "")[:120],
                    "sha256": str(ref.get("sha256") or "")[:64],
                }
                for ref in (refs if isinstance(refs, list) else [])[:100]
                if isinstance(ref, Mapping)
            ],
        }

    def security(self, root_frame_id: str) -> dict[str, Any]:
        self._root_frame(root_frame_id)
        state = self._state_for(root_frame_id)
        sandbox = self._sandbox(root_frame_id, state)
        pending = list(self._pending_for(root_frame_id))
        try:
            from openai4s import egress

            network_policy = egress.egress_mode()
        except Exception:  # noqa: BLE001 - unknown is safer than a false claim
            network_policy = "unknown"
        current = sandbox.get("network_policy")
        # ``"unknown"`` is a truthy placeholder used before a kernel starts; it
        # must not shadow the real process-wide egress mode computed above.
        if not current or current == "unknown":
            sandbox["network_policy"] = network_policy
        return {
            "root_frame_id": root_frame_id,
            "sandbox": sandbox,
            "permission": {
                "mode": "durable-policy",
                "pending_count": len(pending),
                "unattended": "pending-or-deny",
            },
            "notebook": {
                "interactive": bool(
                    str(os.environ.get("OPENAI4S_NOTEBOOK_REPL", "")).strip().lower()
                    in {"1", "true", "yes", "on"}
                )
            },
        }

    def delegation(self, root_frame_id: str) -> dict[str, Any]:
        """Return the durable delegation tree without reviving a child."""

        self._root_frame(root_frame_id)
        project = getattr(self.store, "delegation_tree", None)
        if not callable(project):
            return {
                "root_frame_id": root_frame_id,
                "initialized": False,
                "budget": None,
                "stats": {
                    "total": 0,
                    "pending": 0,
                    "running": 0,
                    "done": 0,
                    "failed": 0,
                    "stopped": 0,
                },
                "children": [],
            }
        return project(root_frame_id)

    def _root_frame(self, root_frame_id: str) -> dict:
        if not isinstance(root_frame_id, str) or not root_frame_id.strip():
            raise ValueError("root_frame_id is required")
        frame = self.store.get_frame(root_frame_id)
        if frame is None:
            raise KeyError(f"unknown session {root_frame_id!r}")
        if (frame.get("root_frame_id") or root_frame_id) != root_frame_id:
            raise ValueError("workbench projections require a root frame")
        return frame

    def _sandbox(self, root_frame_id: str, state: Any | None) -> dict[str, Any]:
        runtimes: list[dict[str, Any]] = []
        for language, attribute in (("python", "kernel"), ("r", "r_kernel")):
            try:
                kernel = getattr(state, attribute, None) if state is not None else None
                status = (
                    getattr(kernel, "sandbox_status", None)
                    if kernel is not None
                    else None
                )
            except Exception:  # noqa: BLE001 - never invent enforcement state
                status = None
            if isinstance(status, Mapping):
                runtimes.append(
                    {
                        "language": language,
                        **self._public_sandbox(status),
                    }
                )
                continue
            persisted = self._persisted_sandbox(root_frame_id, language)
            if persisted is not None:
                runtimes.append(persisted)
        if runtimes:
            return self._aggregate_sandboxes(runtimes)
        mode = str(os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto") or "auto")
        return {
            "mode": mode,
            "state": "not_started",
            "backend": None,
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "unknown",
            "detail": "Sandbox status is verified only after a kernel worker starts.",
            "warning": None,
            "runtimes": [],
        }

    def _persisted_sandbox(
        self, root_frame_id: str, language: str
    ) -> dict[str, Any] | None:
        latest = getattr(self.store, "latest_kernel_generation", None)
        if not callable(latest):
            return None
        try:
            active = getattr(self.store, "active_session_branch", None)
            branch_id = active(root_frame_id) if callable(active) else root_frame_id
            generation = latest(root_frame_id, language, branch_id=branch_id)
        except Exception:  # noqa: BLE001 - persistence cannot invent a claim
            return None
        if not isinstance(generation, Mapping):
            return None
        environment = generation.get("environment")
        status = (
            environment.get("sandbox") if isinstance(environment, Mapping) else None
        )
        if not isinstance(status, Mapping):
            return None
        ended_at = generation.get("ended_at")
        return {
            "language": language,
            "source": "persisted_generation",
            **self._public_sandbox(status),
            "generation_id": generation.get("generation_id"),
            "generation_state": generation.get("state"),
            "generation_ended": ended_at is not None,
            "generation_ended_at": ended_at,
            "generation_ended_reason": generation.get("ended_reason"),
        }

    @staticmethod
    def _public_sandbox(status: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: status.get(key)
            for key in (
                "mode",
                "state",
                "backend",
                "enforced",
                "self_test_passed",
                "network_policy",
                "detail",
                "warning",
            )
        }

    @staticmethod
    def _aggregate_sandboxes(
        runtimes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        def common(field: str) -> Any:
            values = {
                str(runtime.get(field))
                for runtime in runtimes
                if runtime.get(field) not in (None, "")
            }
            if not values:
                return None
            return next(iter(values)) if len(values) == 1 else "mixed"

        details = []
        for runtime in runtimes:
            detail = runtime.get("detail") or runtime.get("warning")
            if detail:
                details.append(f"{runtime['language']}: {str(detail)[:200]}")
            if runtime.get("generation_ended") is True:
                reason = runtime.get("generation_ended_reason") or "ended"
                details.append(
                    f"{runtime['language']}: generation ended ({str(reason)[:80]})"
                )
        public_runtimes = []
        for runtime in runtimes:
            public = {
                key: runtime.get(key)
                for key in (
                    "language",
                    "mode",
                    "state",
                    "backend",
                    "enforced",
                    "self_test_passed",
                    "network_policy",
                )
            }
            for key in (
                "source",
                "generation_id",
                "generation_state",
                "generation_ended",
                "generation_ended_at",
                "generation_ended_reason",
            ):
                if key in runtime:
                    public[key] = runtime.get(key)
            public_runtimes.append(public)
        ended_languages = [
            runtime["language"]
            for runtime in runtimes
            if runtime.get("generation_ended") is True
        ]
        return {
            "mode": common("mode"),
            "state": common("state"),
            "backend": common("backend"),
            "enforced": all(runtime.get("enforced") is True for runtime in runtimes),
            "self_test_passed": all(
                runtime.get("self_test_passed") is True for runtime in runtimes
            ),
            "network_policy": common("network_policy"),
            "detail": "; ".join(details)[:500] or None,
            "warning": common("warning"),
            "runtimes": public_runtimes,
            "generation_ended": len(ended_languages) == len(runtimes),
            "ended_languages": ended_languages,
        }


__all__ = ["SessionWorkbenchStateService", "WorkbenchStore"]
