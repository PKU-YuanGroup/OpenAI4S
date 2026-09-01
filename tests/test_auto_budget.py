"""Atomic Auto Mode budget admission against a real SQLite repository."""

from __future__ import annotations

import threading
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from openai4s.config import AutoModeBudgets, AutoModeConfig, Config, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.auto_budget import (
    FIELD_AUTHORITIES,
    TERMINAL_USER_TRUTH,
    AutoBudgetAdmission,
    AutoBudgetDenied,
    canonical_action_fingerprint,
    execution_action_group,
    inspect_budget_wiring,
    is_completion_disguise,
    token_upper_bound,
    verifiable_token_usage,
)
from openai4s.server.auto_mode import AutoModeService
from openai4s.server.auto_repair import AutoRepairService
from openai4s.server.evidence_snapshot import freeze_evidence_snapshot
from openai4s.server.scientific_review import ScientificReviewService
from openai4s.storage.auto_mode import (
    AutoBudgetConflictError,
    create_auto_mode_budget_schema,
)
from openai4s.store import Store


def _store(tmp_path, name: str = "openai4s.db") -> Store:
    return Store(tmp_path / name)


def _budgets(**overrides) -> dict:
    return asdict(AutoModeBudgets(**overrides))


def _start(store: Store, **overrides):
    fields = {
        "run_id": "auto-run-1",
        "idempotency_key": "turn-1:auto-run",
        "root_frame_id": "root-1",
        "branch_id": "root-1",
        "turn_id": "turn-1",
        "execution_id": "execution-1",
        "mode": "auto_fix",
        "selection": {
            "preset": "autonomous",
            "result_review_mode": "auto_fix",
            "approvals_reviewer": "auto_review",
        },
        "budgets": _budgets(),
        "owner_instance_id": "daemon-1",
    }
    fields.update(overrides)
    root = fields["root_frame_id"]
    if store.get_frame(root) is None:
        project_id = f"project-{root}"
        store.create_project(name="Auto Budget test", project_id=project_id)
        store._conn.execute(
            "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
            "status,depth,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (root, None, project_id, root, "turn", "processing", 0, 1, 1),
        )
        store._conn.commit()
    store.ensure_session_branch(root_frame_id=root, branch_id=fields["branch_id"])
    return store.start_auto_mode_run(**fields)


def _reserve(store: Store, **overrides):
    fields = {
        "run_id": "auto-run-1",
        "admission_id": "adm-1",
        "consumer": "review",
        "action_group_id": "review-1",
        "amount": 1,
    }
    fields.update(overrides)
    return store.reserve_auto_mode_budget(**fields)


def _llm(model="reviewer-model"):
    return SimpleNamespace(
        provider="openai",
        model=model,
        base_url="https://review.example/v1",
        timeout_s=30,
        max_tokens=800,
    )


def _pass_chat(messages, cfg, **kwargs):
    del messages, cfg, kwargs
    return {
        "content": '{"verdict": "pass", "summary": "ok", "findings": []}',
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _snapshot():
    return freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": "root-1",
                "branch_id": "root-1",
                "turn_id": "turn-1",
                "execution_id": "execution-1",
            },
            "user_request": "report n",
            "candidate_answer": "resid.csv has n=2 and mean=2.0",
            "artifacts": [
                {
                    "artifact_id": "art-1",
                    "filename": "resid.csv",
                    "content_type": "text/csv",
                    "version_id": "ver-1",
                    "checksum": "a" * 64,
                    "exists": True,
                }
            ],
            "adapters": [
                {
                    "adapter": "table",
                    "version_id": "ver-1",
                    "artifact_id": "art-1",
                    "complete": True,
                    "summary": {
                        "row_count": 2,
                        "columns": {"value": {"mean": 2.0}},
                    },
                }
            ],
        }
    )


def test_v29_budget_schema_is_additive_and_repeatable(tmp_path):
    store = _store(tmp_path)
    create_auto_mode_budget_schema(store._conn)
    create_auto_mode_budget_schema(store._conn)
    tables = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "auto_mode_budget_state" in tables
    assert "auto_mode_budget_reservations" in tables
    store.close()


def test_new_run_creates_budget_state_and_legacy_run_is_readonly(tmp_path):
    store = _store(tmp_path)
    _start(store)
    state = store.get_auto_mode_budget_state("auto-run-1")
    assert state is not None
    assert state["root_run_id"] == "auto-run-1"
    store._conn.execute(
        "DELETE FROM auto_mode_budget_state WHERE run_id=?", ("auto-run-1",)
    )
    store._conn.commit()
    projected = AutoBudgetAdmission(store).project_usage("auto-run-1")
    assert projected["legacy"] is True
    with pytest.raises(AutoBudgetDenied) as denied:
        _reserve(store, admission_id="legacy-1")
    assert denied.value.reason == "legacy_run_readonly"
    store.close()


def test_review_limit_two_blocks_third_inference(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    called = {"n": 0}

    def chat(messages, cfg, **kwargs):
        called["n"] += 1
        return _pass_chat(messages, cfg, **kwargs)

    service = ScientificReviewService(
        store=store,
        config=Config(
            auto_mode=AutoModeConfig(result_review_mode="review_only"),
            roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True),
        ),
        chat_call=chat,
    )
    for _ in range(3):
        result = service.evaluate(
            _snapshot(),
            result_review_mode="review_only",
            agent_cfg=_llm("agent"),
            reviewer_cfg=_llm("reviewer"),
            chat_call=chat,
            run_id="auto-run-1",
        )
    assert called["n"] == 2
    assert result["reason"] == "budget_exhausted"
    assert result["verdict"] != "pass"
    assert is_completion_disguise(result["verdict"], result["reason"]) is False
    store.close()


def test_review_retry_has_unique_admission_and_marks_started_token_unknown(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    calls = {"n": 0}

    def flaky_chat(messages, cfg, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider response was lost")
        return _pass_chat(messages, cfg, **kwargs)

    service = ScientificReviewService(
        store=store,
        config=Config(
            auto_mode=AutoModeConfig(result_review_mode="review_only"),
            roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True),
        ),
        chat_call=flaky_chat,
    )
    result = service.evaluate(
        _snapshot(),
        result_review_mode="review_only",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=flaky_chat,
        run_id="auto-run-1",
        action_group_id="caller-review-group",
    )
    assert calls["n"] == 2
    assert result["verdict"] == "pass"
    rows = store.list_auto_mode_budget_reservations("auto-run-1")
    review_rows = [item for item in rows if item["consumer"] == "review"]
    token_rows = [item for item in rows if item["consumer"] == "token"]
    assert [item["state"] for item in review_rows] == ["unknown", "committed"]
    assert [item["state"] for item in token_rows] == ["unknown", "committed"]
    assert len({item["action_group_id"] for item in review_rows}) == 2
    assert all(
        item["action_group_id"].startswith("caller-review-group:")
        for item in review_rows
    )
    store.close()


def test_same_action_fourth_call_is_refused_before_invoke(tmp_path):
    store = _store(tmp_path)
    _start(store)
    fingerprint = canonical_action_fingerprint(
        kind="tool", name="read_file", arguments={"path": "a.csv"}
    )
    invoked = {"n": 0}
    for index in range(4):
        try:
            _reserve(
                store,
                admission_id=f"tool-{index}",
                consumer="native_tool",
                action_group_id=f"tool-{index}",
                action_sha256=fingerprint,
            )
            invoked["n"] += 1
        except AutoBudgetDenied as denied:
            assert denied.reason == "loop_detected"
            assert invoked["n"] == 3
            store.close()
            return
    raise AssertionError("fourth same-action reserve was not refused")


def test_five_no_delta_turns_refuse_the_sixth(tmp_path):
    store = _store(tmp_path)
    _start(store)
    admitted = 0
    for index in range(6):
        try:
            _reserve(
                store,
                admission_id=f"model-{index}",
                consumer="model",
                action_group_id=f"model-{index}",
                enforce_field_limit=False,
            )
            admitted += 1
        except AutoBudgetDenied as denied:
            assert denied.reason == "loop_detected"
            assert admitted == 5
            store.close()
            return
    raise AssertionError("sixth no-progress turn was not refused")


def test_concurrent_last_slot_has_one_winner(tmp_path):
    path = tmp_path / "race.db"
    store_a = Store(path)
    _start(store_a, budgets=_budgets(max_review_rounds=2))
    first = _reserve(store_a, admission_id="review-0", action_group_id="review-0")
    store_a.commit_auto_mode_budget(
        first["reservation"]["admission_id"], committed_amount=1
    )
    store_b = Store(path)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(store: Store, admission_id: str) -> None:
        barrier.wait()
        try:
            store.reserve_auto_mode_budget(
                run_id="auto-run-1",
                admission_id=admission_id,
                consumer="review",
                action_group_id=admission_id,
                amount=1,
            )
            outcomes.append("ok")
        except AutoBudgetDenied:
            outcomes.append("denied")

    threads = [
        threading.Thread(target=attempt, args=(store_a, "review-a")),
        threading.Thread(target=attempt, args=(store_b, "review-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("denied") == 1
    rows = [
        row
        for row in store_a.list_auto_mode_budget_reservations("auto-run-1")
        if row["consumer"] == "review" and row["state"] == "reserved"
    ]
    assert len(rows) == 1
    store_a.close()
    store_b.close()


def test_ten_identical_admission_ids_create_one_reservation(tmp_path):
    store = _store(tmp_path)
    _start(store)
    for _ in range(10):
        _reserve(store, admission_id="same-adm", action_group_id="same-group")
    rows = [
        row
        for row in store.list_auto_mode_budget_reservations("auto-run-1")
        if row["admission_id"] == "same-adm"
    ]
    assert len(rows) == 1
    store.close()


def test_restart_does_not_increase_remaining(tmp_path):
    path = tmp_path / "restart.db"
    store = Store(path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    reserved = _reserve(store, admission_id="r1", action_group_id="r1")
    store.commit_auto_mode_budget(
        reserved["reservation"]["admission_id"], committed_amount=1
    )
    before = AutoBudgetAdmission(store).project_usage("auto-run-1")
    remaining_before = before["budget_usage"]["max_review_rounds"]["remaining"]
    used_before = before["budget_usage"]["max_review_rounds"]["used"]
    store.close()
    reopened = Store(path)
    after = AutoBudgetAdmission(reopened).project_usage("auto-run-1")
    assert after["budget_usage"]["max_review_rounds"]["used"] == used_before
    assert after["budget_usage"]["max_review_rounds"]["remaining"] == remaining_before
    assert after["budget_usage"]["max_review_rounds"]["remaining"] == 1
    reopened.close()


def test_started_without_receipt_refunds_zero(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    reserved = _reserve(store, admission_id="open-1", action_group_id="open-1")
    admission_id = reserved["reservation"]["admission_id"]
    settled = store.release_auto_mode_budget(admission_id, started=True)
    assert settled["reservation"]["state"] == "unknown"
    usage = AutoBudgetAdmission(store).project_usage("auto-run-1")
    assert usage["budget_usage"]["max_review_rounds"]["used"] == 1
    assert usage["budget_usage"]["max_review_rounds"]["remaining"] == 1
    never = _reserve(store, admission_id="never-1", action_group_id="never-1")
    store.release_auto_mode_budget(never["reservation"]["admission_id"], started=False)
    usage = AutoBudgetAdmission(store).project_usage("auto-run-1")
    assert usage["budget_usage"]["max_review_rounds"]["used"] == 1
    assert usage["budget_usage"]["max_review_rounds"]["remaining"] == 1
    store.close()


def test_trip_stops_further_provider_tool_and_cell_sinks(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=1, max_extra_cells=1))
    first = _reserve(store, admission_id="rev-0", action_group_id="rev-0")
    store.commit_auto_mode_budget(
        first["reservation"]["admission_id"], committed_amount=1
    )
    before = len(store.list_auto_mode_budget_reservations("auto-run-1"))
    for consumer, group in (
        ("review", "rev-1"),
        ("native_tool", "tool-1"),
        ("extra_cell", "cell-1"),
        ("model", "model-1"),
    ):
        with pytest.raises(AutoBudgetDenied):
            _reserve(
                store,
                admission_id=f"{consumer}-blocked",
                consumer=consumer,
                action_group_id=group,
                enforce_field_limit=consumer != "model",
            )
    after = store.list_auto_mode_budget_reservations("auto-run-1")
    assert len(after) == before
    store.close()


def test_each_public_field_has_one_authority_and_zero_sink_bypasses():
    inventory = inspect_budget_wiring()
    assert inventory["missing_authorities"] == []
    assert inventory["duplicate_authorities"] == []
    assert inventory["sink_bypass_count"] == 0
    assert inventory["ga_ready"] is True
    assert set(FIELD_AUTHORITIES) == set(AutoModeBudgets.__dataclass_fields__)
    authorities = list(FIELD_AUTHORITIES.values())
    assert authorities.count("guardian") == 4
    assert all(
        FIELD_AUTHORITIES[name] == "guardian"
        for name in (
            "guardian_timeout_s",
            "guardian_consecutive_denial_limit",
            "guardian_window_size",
            "guardian_window_denial_limit",
        )
    )


def test_guardian_meters_are_projected_from_authority_not_copied(tmp_path):
    store = _store(tmp_path)
    _start(store)
    state = store.get_auto_mode_budget_state("auto-run-1")
    assert "guardian_consecutive_denial_limit" not in state
    assert "guardian_window_denial_limit" not in state
    usage = AutoBudgetAdmission(store, AutoModeBudgets()).project_usage(
        "auto-run-1", root_frame_id="root-1"
    )
    guardian = usage["budget_usage"]["guardian_consecutive_denial_limit"]
    assert guardian["authority"] == "guardian"
    assert guardian["used"] == 0
    store.close()


def test_get_auto_mode_projects_budget_usage_and_circuit(tmp_path):
    store = _store(tmp_path)
    _start(store)
    frame = store.get_frame("root-1")
    assert frame is not None
    service = AutoModeService(store=store, config=Config())
    view = service.get("root-1")
    run = view["run"]
    assert run["legacy"] is False
    assert set(run["budget_usage"]) == set(FIELD_AUTHORITIES)
    for name, meter in run["budget_usage"].items():
        assert meter["authority"] == FIELD_AUTHORITIES[name]
        assert {
            "limit",
            "used",
            "reserved",
            "remaining",
            "exhausted",
            "authority",
        } <= set(meter)
    assert run["circuit"]["state"] == "closed"
    store.close()


def test_unverified_tokens_fail_closed_and_are_not_completion(tmp_path):
    assert verifiable_token_usage({}) is None
    assert verifiable_token_usage({"prompt_tokens": 1, "completion_tokens": 1}) == 2
    store = _store(tmp_path)
    _start(store)
    store.freeze_auto_mode_budget_initial_tokens(
        "auto-run-1", 10, extra_token_multiplier=1.5
    )
    with pytest.raises(AutoBudgetDenied) as denied:
        store.reserve_auto_mode_budget(
            run_id="auto-run-1",
            admission_id="tok-1",
            consumer="token",
            action_group_id="tok-1",
            token_upper_bound=None,
        )
    assert denied.value.reason == "budget_measurement_unavailable"
    assert TERMINAL_USER_TRUTH[denied.value.reason] == "无法验证 token 预算"
    assert is_completion_disguise("completed_with_issues", denied.value.reason)
    store.close()


def test_pre_provider_token_bound_covers_prompt_and_completion():
    cfg = _llm()
    assert token_upper_bound(cfg) is None  # output-only is not a total bound
    short = token_upper_bound(
        cfg,
        messages=[{"role": "user", "content": "x"}],
        tools=[],
    )
    long = token_upper_bound(
        cfg,
        messages=[{"role": "user", "content": "x" * 10_000}],
        tools=[],
    )
    assert short is not None and short > cfg.max_tokens
    assert long is not None and long > short
    assert token_upper_bound(cfg, messages=[{"content": object()}]) is None


def test_token_settlement_cannot_exceed_reserved_or_frozen_hard_limit(tmp_path):
    store = _store(tmp_path)
    _start(store)
    store.freeze_auto_mode_budget_initial_tokens(
        "auto-run-1", 100, extra_token_multiplier=1.5
    )
    reserved = store.reserve_auto_mode_budget(
        run_id="auto-run-1",
        admission_id="token-overrun",
        consumer="token",
        action_group_id="token-overrun",
        token_upper_bound=100,
    )
    with pytest.raises(AutoBudgetDenied) as denied:
        store.commit_auto_mode_budget(
            reserved["reservation"]["admission_id"], committed_amount=1000
        )
    assert denied.value.reason == "budget_exhausted"
    row = next(
        item
        for item in store.list_auto_mode_budget_reservations("auto-run-1")
        if item["admission_id"] == "token-overrun"
    )
    assert row["state"] == "committed"
    assert row["committed_amount"] == 1000
    assert (
        AutoBudgetAdmission(store).project_usage("auto-run-1")["circuit"]["state"]
        == "tripped"
    )
    store.close()


def test_execution_admission_is_single_use_and_replay_identity_is_exact(tmp_path):
    store = _store(tmp_path)
    _start(store)
    budget = AutoBudgetAdmission(store)
    fingerprint = canonical_action_fingerprint(
        kind="tool", name="write_file", arguments={"path": "a.txt"}
    )
    fields = {
        "run_id": "auto-run-1",
        "admission_id": "single-use",
        "consumer": "native_tool",
        "action_group_id": "batch:call-1",
        "action_sha256": fingerprint,
    }
    budget.reserve(**fields)
    with pytest.raises(AutoBudgetDenied, match="already used"):
        budget.reserve(**fields)
    changed = canonical_action_fingerprint(
        kind="tool", name="write_file", arguments={"path": "b.txt"}
    )
    with pytest.raises(AutoBudgetConflictError):
        store.reserve_auto_mode_budget(**{**fields, "action_sha256": changed})
    assert execution_action_group("batch", "call-1") == "batch:call-1"
    assert execution_action_group("batch", "call-1") != execution_action_group(
        "batch", "call-2"
    )
    assert execution_action_group("model") != execution_action_group("model")
    store.close()


def test_replay_cannot_bypass_an_open_circuit(tmp_path):
    store = _store(tmp_path)
    _start(store)
    fields = {
        "run_id": "auto-run-1",
        "admission_id": "before-trip",
        "consumer": "review",
        "action_group_id": "before-trip",
    }
    store.reserve_auto_mode_budget(**fields)
    store.trip_auto_mode_budget_circuit(
        "auto-run-1", reason="loop_detected", field="same_action_no_delta_limit"
    )
    with pytest.raises(AutoBudgetDenied) as denied:
        store.reserve_auto_mode_budget(**fields)
    assert denied.value.reason == "loop_detected"
    store.close()


def test_gateway_token_denial_precedes_provider_invocation(tmp_path):
    store = _store(tmp_path)
    _start(store)
    store.freeze_auto_mode_budget_initial_tokens(
        "auto-run-1", 1, extra_token_multiplier=1.0
    )
    runner = object.__new__(gateway_mod.SessionRunner)
    runner.store = store
    runner.cfg = Config()
    state = SimpleNamespace(
        active_auto_mode_run_id="auto-run-1",
        active_action_group_id=None,
        cell_index=1,
        auto_budget_terminal_reason=None,
        cancel=threading.Event(),
    )
    provider_calls = {"n": 0}

    def provider(*_args, **_kwargs):
        provider_calls["n"] += 1
        return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    with pytest.raises(AutoBudgetDenied) as denied:
        runner._invoke_model_with_auto_budget(
            state,
            [{"role": "user", "content": "must not reach provider"}],
            _llm("agent"),
            provider,
        )
    assert denied.value.reason == "budget_exhausted"
    assert provider_calls["n"] == 0
    rows = store.list_auto_mode_budget_reservations("auto-run-1")
    assert [item["state"] for item in rows if item["consumer"] == "model"] == [
        "released"
    ]
    assert [item for item in rows if item["consumer"] == "token"] == []
    store.close()


def test_parent_and_child_share_root_run_id(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    store.ensure_auto_mode_budget_state(
        "child-run",
        root_run_id="auto-run-1",
        started_at=200,
    )
    _reserve(store, admission_id="root-r", action_group_id="root-r")
    store.reserve_auto_mode_budget(
        run_id="child-run",
        admission_id="child-r",
        consumer="review",
        action_group_id="child-r",
        amount=1,
    )
    usage = AutoBudgetAdmission(store).project_usage("child-run")
    assert usage["budget_usage"]["max_review_rounds"]["reserved"] == 2
    store.close()


def test_repair_budget_exhaustion_is_not_a_pass(tmp_path):
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_repair_rounds=1, max_review_rounds=2))
    review = ScientificReviewService(
        store=store,
        config=Config(
            auto_mode=AutoModeConfig(
                result_review_mode="auto_fix",
                budgets=AutoModeBudgets(max_repair_rounds=1),
            ),
            roadmap_features=RoadmapFeatureFlags(stage5_auto_repair=True),
        ),
        chat_call=_pass_chat,
    )
    broken = freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": "root-1",
                "branch_id": "root-1",
                "turn_id": "turn-1",
                "execution_id": "execution-1",
            },
            "user_request": "report n",
            "candidate_answer": "resid.csv has n=99 and mean=2.0",
            "artifacts": [
                {
                    "artifact_id": "art-1",
                    "filename": "resid.csv",
                    "content_type": "text/csv",
                    "version_id": "ver-1",
                    "checksum": "a" * 64,
                    "exists": True,
                }
            ],
            "adapters": [
                {
                    "adapter": "table",
                    "version_id": "ver-1",
                    "artifact_id": "art-1",
                    "complete": True,
                    "summary": {
                        "row_count": 2,
                        "columns": {"value": {"mean": 2.0}},
                    },
                }
            ],
        }
    )
    initial = {
        "verdict": "issues",
        "findings": [
            {"severity": "high", "fingerprint": "same-finding", "finding_id": "f1"}
        ],
        "snapshot": broken,
    }

    def noop_repair(snapshot, findings):
        del findings
        return {
            "changed": True,
            "self_certified": False,
            "candidate_answer": snapshot.get("candidate_answer"),
            "after_version_ids": [],
            "artifacts": list(snapshot.get("artifacts") or []),
        }

    repaired = AutoRepairService(
        store=store,
        config=Config(
            auto_mode=AutoModeConfig(
                result_review_mode="auto_fix",
                budgets=AutoModeBudgets(max_repair_rounds=1),
            ),
            roadmap_features=RoadmapFeatureFlags(stage5_auto_repair=True),
        ),
        scientific_review=review,
        repair_fn=noop_repair,
    ).run(
        initial=initial,
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        run_id="auto-run-1",
    )
    assert repaired.get("verdict") != "pass"
    assert repaired.get("stop_reason") in {"budget_exhausted", "loop_detected"}
    store.close()


def test_repeated_finding_reservations_are_settled_not_left_reserved(tmp_path):
    """A reservation that is never settled is a permanent charge.

    `AutoRepairService` reserves against `repeated_finding` and `repair` in
    the same block, but built the finding admission id inline, so the only id
    that reached commit/mark_unknown was the repair one. Reserved counts
    against remaining exactly like committed does -- that is the whole point
    of reserving before acting -- so every repair round permanently consumed
    a `repeated_finding` slot that no fact ever confirmed.
    """

    from openai4s.config import AutoModeConfig, RoadmapFeatureFlags
    from openai4s.server.auto_repair import AutoRepairService

    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_repair_rounds=2))
    cfg = Config(
        roadmap_features=RoadmapFeatureFlags(stage5_auto_repair=True),
        auto_mode=AutoModeConfig(
            result_review_mode="auto_fix",
            approvals_reviewer="auto_review",
            budgets=AutoModeBudgets(max_repair_rounds=2),
        ),
    )
    service = AutoRepairService(
        store=store,
        config=cfg,
        scientific_review=SimpleNamespace(
            evaluate=lambda *a, **k: {"verdict": "pass", "findings": []}
        ),
        repair_fn=lambda snapshot, findings: {
            "changed": True,
            "candidate_answer": "corrected",
        },
    )
    service.run(
        initial={
            "verdict": "issues",
            "snapshot": {"candidate_answer": "wrong"},
            "findings": [{"severity": "high", "claim": "n=9", "id": "f-1"}],
        },
        result_review_mode="auto_fix",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        run_id="auto-run-1",
    )

    rows = store.list_auto_mode_budget_reservations("auto-run-1")
    finding_rows = [item for item in rows if item["consumer"] == "repeated_finding"]
    assert finding_rows, "no repeated_finding reservation was taken at all"
    assert [item["state"] for item in finding_rows] == [
        "committed"
    ], "repeated_finding reservation was never settled"
    store.close()


def test_every_declared_consumer_has_a_sink_or_is_named_unwired():
    """A published limit with no sink permits an unbounded number of actions.

    `repair_turn` sat in CONSUMERS with a limit, a projected `used`, and no
    reserve call anywhere -- so the field read as measured while measuring
    nothing. Splitting the inventory means a consumer cannot be silently
    unmeasured: it is either wired, or it is named here with a reason.
    """

    from openai4s.server.auto_budget import (
        CONSUMERS,
        SINK_REGISTRY,
        UNWIRED_CONSUMERS,
    )

    assert not (set(SINK_REGISTRY) & set(UNWIRED_CONSUMERS))
    assert set(SINK_REGISTRY) | set(UNWIRED_CONSUMERS) == set(
        CONSUMERS
    ), "a consumer is neither wired to a sink nor declared unwired"


def test_missing_token_ceiling_pauses_only_once_ceilings_bind(tmp_path, monkeypatch):
    """An adapter with no stateable ceiling must not pause a pre-freeze run.

    The Web turn loop gates its whole token block on `token_phase_active`, so
    before the extra phase an adapter that cannot state a prompt-plus-
    completion bound simply runs. The review path applied the same check
    unconditionally and treated the silence as `budget_measurement_
    unavailable`, pausing the entire run at its first review -- one missing
    capability, two answers, decided by which sink saw it first.

    Once a review round has landed the ceiling does bind, and the same
    adapter is correctly fatal.
    """

    from openai4s.server import scientific_review as review_mod

    monkeypatch.setattr(review_mod, "token_upper_bound", lambda *a, **k: None)
    store = _store(tmp_path)
    _start(store, budgets=_budgets(max_review_rounds=2))
    service = ScientificReviewService(
        store=store,
        config=Config(
            auto_mode=AutoModeConfig(result_review_mode="review_only"),
            roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True),
        ),
        chat_call=_pass_chat,
    )
    call = dict(
        result_review_mode="review_only",
        agent_cfg=_llm("agent"),
        reviewer_cfg=_llm("reviewer"),
        chat_call=_pass_chat,
        run_id="auto-run-1",
    )

    first = service.evaluate(_snapshot(), **call)
    assert first["verdict"] == "pass", first.get("reason")
    assert first.get("reason") != "budget_measurement_unavailable"
    assert not [
        item
        for item in store.list_auto_mode_budget_reservations("auto-run-1")
        if item["consumer"] == "token"
    ], "a token reservation was taken with no ceiling to reserve against"

    # The committed review round is what makes ceilings bind from here on.
    assert AutoBudgetAdmission(store).token_phase_active("auto-run-1") is True
    second = service.evaluate(_snapshot(), **call)
    assert second["reason"] == "budget_measurement_unavailable"
    store.close()
