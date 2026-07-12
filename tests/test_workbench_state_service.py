from __future__ import annotations

from types import SimpleNamespace

from openai4s.config import LLMConfig
from openai4s.server.workbench_state import SessionWorkbenchStateService
from openai4s.store import Store


class _Store:
    def __init__(self, generations=None):
        self.generations = generations or {}

    def get_frame(self, frame_id):
        return {"frame_id": frame_id, "root_frame_id": frame_id}

    def latest_kernel_generation(self, root_frame_id, language, *, branch_id=None):
        del root_frame_id, branch_id
        return self.generations.get(language)


def _service(state=None, pending=(), generations=None, store=None):
    return SessionWorkbenchStateService(
        store or _Store(generations),
        state_for=lambda _root: state,
        history_for=lambda _root: [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world", "compaction_handoff": True},
        ],
        llm_config_for=lambda _state: LLMConfig(
            provider="deepseek", model="deepseek-chat", api_key="test"
        ),
        pending_for=lambda _root: pending,
        context_window_fallback=10_000,
    )


def test_context_projects_components_without_message_content():
    result = _service().context("root")
    assert result["token_count"] > 0
    assert result["handoff"] is True
    assert {item["kind"] for item in result["layers"]} == {
        "text",
        "images",
        "tool_schemas",
        "tool_calls",
        "tool_results",
        "artifact_refs",
        "wire_state",
    }
    assert "hello" not in repr(result)


def test_context_projects_safe_persistent_compaction_history(tmp_path):
    store = Store(tmp_path / "workbench-context.db")
    root_frame_id = store.new_frame(project_id="science")
    store.archive_compaction(
        frame_id=root_frame_id,
        project_id="science",
        branch_id=root_frame_id,
        ledger_cursor={"group_id": "ag-1", "ordinal": 3},
        recovery_pointer={"checkpoint_id": "cp-1"},
        generation_id="generation-1",
        summary="sensitive summary",
        handoff="sensitive handoff",
        compacted=[{"role": "tool", "content": "secret raw output"}],
        context_before={"total": 1200},
        context_after={"total": 400},
        artifact_refs=[{"artifact_id": "a-1", "version_id": "v-1"}],
    )

    context = _service(store=store).context(root_frame_id)

    assert context["compaction_count"] == 1
    history = context["compaction_history"][0]
    assert history["tokens_before"] == 1200
    assert history["tokens_after"] == 400
    assert history["artifact_refs"] == [
        {"artifact_id": "a-1", "version_id": "v-1", "sha256": ""}
    ]
    assert "secret raw output" not in repr(context)
    assert "sensitive summary" not in repr(context)


def test_security_never_claims_unstarted_sandbox(monkeypatch):
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "enforce")
    result = _service(pending=({"decision_id": "secret"},)).security("root")
    assert result["sandbox"]["state"] == "not_started"
    assert result["sandbox"]["enforced"] is False
    assert result["permission"]["pending_count"] == 1
    assert "secret" not in repr(result)


def test_security_uses_only_public_live_sandbox_fields():
    kernel = SimpleNamespace(
        sandbox_status={
            "mode": "auto",
            "state": "enforced",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
            "workspace": "/private/session",
            "temp_dir": "/private/tmp",
            "detail": "verified",
        }
    )
    state = SimpleNamespace(kernel=kernel)
    result = _service(state=state).security("root")
    assert result["sandbox"]["enforced"] is True
    assert result["sandbox"]["backend"] == "seatbelt"
    assert "workspace" not in result["sandbox"]
    assert "/private" not in repr(result)


def test_security_projects_an_r_only_worker_without_claiming_not_started():
    r_kernel = SimpleNamespace(
        sandbox_status={
            "mode": "auto",
            "state": "enforced",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        }
    )
    state = SimpleNamespace(kernel=None, r_kernel=r_kernel)

    result = _service(state=state).security("root")

    assert result["sandbox"]["state"] == "enforced"
    assert result["sandbox"]["enforced"] is True
    assert result["sandbox"]["runtimes"] == [
        {
            "language": "r",
            "mode": "auto",
            "state": "enforced",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        }
    ]


def test_security_aggregates_python_and_r_to_the_weakest_truthful_claim():
    python = SimpleNamespace(
        sandbox_status={
            "mode": "auto",
            "state": "enforced",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        }
    )
    r = SimpleNamespace(
        sandbox_status={
            "mode": "auto",
            "state": "warning",
            "backend": "none",
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "unknown",
            "detail": "R sandbox unavailable",
            "workspace": "/must/not/leak",
        }
    )

    result = _service(state=SimpleNamespace(kernel=python, r_kernel=r)).security("root")

    assert result["sandbox"]["state"] == "mixed"
    assert result["sandbox"]["backend"] == "mixed"
    assert result["sandbox"]["enforced"] is False
    assert result["sandbox"]["self_test_passed"] is False
    assert len(result["sandbox"]["runtimes"]) == 2
    assert "/must/not/leak" not in repr(result)


def test_security_keeps_last_verified_generation_after_ttl_release(tmp_path):
    store = Store(tmp_path / "workbench.db")
    root_frame_id = store.new_frame(project_id="science")
    generation = store.create_kernel_generation(
        root_frame_id=root_frame_id,
        language="python",
        environment={
            "sandbox": {
                "mode": "auto",
                "state": "enabled",
                "backend": "seatbelt",
                "enforced": True,
                "self_test_passed": True,
                "network_policy": "blocked",
                "detail": "verified",
                "workspace": "/must/not/leak",
            }
        },
        state="active",
        started_at=1000,
    )
    store.finish_kernel_generation(
        generation["generation_id"],
        state="released",
        reason="idle_ttl",
        ended_at=2000,
    )

    sandbox = _service(store=store).security(root_frame_id)["sandbox"]

    assert sandbox["state"] == "enabled"
    assert sandbox["enforced"] is True
    assert sandbox["self_test_passed"] is True
    assert sandbox["generation_ended"] is True
    assert sandbox["ended_languages"] == ["python"]
    assert sandbox["runtimes"][0]["source"] == "persisted_generation"
    assert sandbox["runtimes"][0]["generation_ended_reason"] == "idle_ttl"
    assert "generation ended (idle_ttl)" in sandbox["detail"]
    assert "/must/not/leak" not in repr(sandbox)


def test_live_and_ended_language_sandboxes_still_aggregate_to_weaker_claim():
    python = SimpleNamespace(
        sandbox_status={
            "mode": "auto",
            "state": "enabled",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        }
    )
    ended_r = {
        "generation_id": "generation-r",
        "state": "released",
        "ended_at": 3000,
        "ended_reason": "manual_stop",
        "environment": {
            "sandbox": {
                "mode": "auto",
                "state": "unavailable",
                "backend": "none",
                "enforced": False,
                "self_test_passed": False,
                "network_policy": "not_enforced",
                "detail": "R sandbox unavailable",
            }
        },
    }

    sandbox = _service(
        state=SimpleNamespace(kernel=python, r_kernel=None),
        generations={"r": ended_r},
    ).security("root")["sandbox"]

    assert sandbox["state"] == "mixed"
    assert sandbox["enforced"] is False
    assert sandbox["self_test_passed"] is False
    assert sandbox["generation_ended"] is False
    assert sandbox["ended_languages"] == ["r"]
    assert {runtime["language"] for runtime in sandbox["runtimes"]} == {
        "python",
        "r",
    }
