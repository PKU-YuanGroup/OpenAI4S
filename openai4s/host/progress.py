"""Session todo and approved-plan progress behavior for host RPC calls."""

from __future__ import annotations

from typing import Callable, Protocol

PLAN_STEP_STATUSES = frozenset(
    {"pending", "in_progress", "completed", "failed", "skipped"}
)
PlanSink = Callable[[dict], None]


class ProgressStore(Protocol):
    def get_plan(self, plan_id: str) -> dict | None:
        ...

    def get_plan_by_frame(self, frame_id: str) -> dict | None:
        ...

    def set_plan_step_status(
        self,
        plan_id: str,
        step_id: str,
        status: str,
        note: str | None = None,
    ) -> dict | None:
        ...

    def list_steps(
        self, frame_id: str, *, start: int = 0, limit: int = 800
    ) -> list[dict]:
        ...

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        ...


class ProgressService:
    """Own one dispatcher's transient todos and persisted plan-step ticks."""

    def __init__(
        self,
        store: ProgressStore,
        *,
        get_frame_id: Callable[[], str | None],
        get_plan_sink: Callable[[], PlanSink | None],
    ) -> None:
        self.store = store
        self.get_frame_id = get_frame_id
        self.get_plan_sink = get_plan_sink
        self._todos: list[dict] = []

    def todo_write(self, spec: dict) -> dict:
        todos = spec.get("todos") or []
        clean = []
        for todo in todos:
            if not isinstance(todo, dict):
                continue
            clean.append(
                {
                    "id": todo.get("id") or f"t{len(clean) + 1}",
                    "content": todo.get("content", ""),
                    "status": todo.get("status", "pending"),
                    "priority": todo.get("priority", "medium"),
                }
            )
        self._todos = clean
        return {"ok": True, "count": len(clean), "todos": clean}

    def todo_read(self) -> dict:
        return {"todos": self._todos}

    def plan_update(self, spec: dict) -> dict:
        step_id = spec.get("step_id") or spec.get("id")
        status = spec.get("status") or "in_progress"
        if status not in PLAN_STEP_STATUSES:
            status = "in_progress"
        note = spec.get("note")
        plan_id = spec.get("plan_id")
        if plan_id:
            plan = self.store.get_plan(plan_id)
        else:
            frame_id = self.get_frame_id()
            plan = self.store.get_plan_by_frame(frame_id) if frame_id else None
        if not plan:
            return {"error": "no active plan for this session"}
        if not step_id:
            return {"error": "plan_update requires step_id"}

        self.store.set_plan_step_status(plan["plan_id"], step_id, status, note)
        sink = self.get_plan_sink()
        if sink is not None:
            try:
                sink(
                    {
                        "plan_id": plan["plan_id"],
                        "step_id": step_id,
                        "status": status,
                        "note": note,
                    }
                )
            except Exception:  # noqa: BLE001 - progress telemetry is best effort
                pass
        return {
            "ok": True,
            "plan_id": plan["plan_id"],
            "step_id": step_id,
            "status": status,
        }

    def plan_read(self) -> dict:
        frame_id = self.get_frame_id()
        plan = self.store.get_plan_by_frame(frame_id) if frame_id else None
        return plan or {"plan": None}

    def review_status(self) -> dict:
        """Return a bounded public projection of evidence-review state."""

        frame_id = self.get_frame_id()
        if not frame_id:
            return {"enabled": False, "reviews": []}
        local_auto = self.store.get_setting(f"review:auto:{frame_id}")
        if local_auto is None:
            local_auto = self.store.get_setting("auto_review_enabled", "0")
        reviewer_model = self.store.get_setting(f"review:model:{frame_id}")
        if reviewer_model in (None, ""):
            reviewer_model = self.store.get_setting("reviewer_model")
        reviews: list[dict] = []
        for step in self.store.list_steps(frame_id, limit=800):
            if str(step.get("kind") or "").casefold() != "review":
                continue
            output = step.get("output")
            output = output if isinstance(output, dict) else {}
            issues = output.get("issues")
            reviewed = output.get("reviewed_artifacts")
            reviews.append(
                {
                    "step_id": step.get("step_id"),
                    "status": step.get("status"),
                    "title": step.get("title"),
                    "summary": step.get("summary") or output.get("summary"),
                    "verdict": output.get("verdict"),
                    "issues_count": len(issues) if isinstance(issues, list) else 0,
                    "reviewed_artifacts": (
                        [str(item) for item in reviewed[:100]]
                        if isinstance(reviewed, list)
                        else []
                    ),
                    "created_at": step.get("created_at"),
                }
            )
        return {
            "enabled": str(local_auto or "").casefold() in {"1", "true", "yes", "on"},
            "reviewer_model": (
                None if reviewer_model in (None, "", "__agent__") else reviewer_model
            ),
            "reviews": reviews[-20:],
        }


__all__ = ["PLAN_STEP_STATUSES", "ProgressService"]
