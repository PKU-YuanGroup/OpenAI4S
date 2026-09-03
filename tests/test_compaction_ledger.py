"""Compaction groups persist on the Action Ledger and survive daemon reopen."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from openai4s.agent.actions import CodeCell
from openai4s.agent.compaction import HANDOFF_FIELDS
from openai4s.agent.events import ActionRouted, OutcomeProduced, ReplyReceived
from openai4s.agent.ledger import (
    RuntimeActionLedger,
    compaction_cover_ordinal,
    reduce_action_groups,
    restore_action_history,
)
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.agent.runtime import CompactionPolicy
from openai4s.config import Config, LLMConfig
from openai4s.server.gateway import SessionRunner, SessionState
from openai4s.server.workbench_state import SessionWorkbenchStateService
from openai4s.store import get_store


class _Hub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, root_frame_id, event):
        self.events.append((root_frame_id, event))

    def emitter(self, root_frame_id):
        return lambda event: self.broadcast(root_frame_id, event)


def _runner(tmp_path):
    hub = _Hub()
    runner = SessionRunner(
        Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test"),
        ),
        hub,
        start_idle_sweeper=False,
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="science", status="ready")
    state = SessionState(frame_id, "science", runner.workspace_for(frame_id))
    return runner, hub, state


def _append_code_observation(
    ledger: RuntimeActionLedger, index: int, fill: str = ""
) -> None:
    content = f"```python\nprint({index})\n```"
    observation = f"[Observation]\nstdout:\n{index}{fill}"
    reply = ModelReply(
        content=content,
        wire_state={"response_id": f"r{index}"},
    )
    ledger.emit(ReplyReceived(reply, index))
    ledger.emit(ActionRouted(CodeCell("python", f"print({index})\n"), index))
    ledger.emit(
        OutcomeProduced(
            ExecutionOutcome(
                ({"role": "user", "content": observation},),
                observation=observation,
            ),
            index,
        )
    )


def _code_messages(index: int, fill: str = "") -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": f"```python\nprint({index})\n```",
            "wire_state": {"response_id": f"r{index}"},
        },
        {"role": "user", "content": f"[Observation]\nstdout:\n{index}{fill}"},
    ]


def _handoff_note(text: str) -> dict:
    return {"role": "system", "content": text, "compaction_handoff": True}


def _legacy_reduce_action_groups(
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Replica of reduce_action_groups before kind=compaction existed.

    Groups whose assistant_message is missing or not an assistant role are
    skipped, so a compaction row (assistant_message=None) degrades to a
    no-op and the uncompacted history is replayed.
    """
    history: list[dict[str, Any]] = []
    for group in groups:
        kind = str(group.get("kind") or "")
        if kind == "terminal":
            continue
        raw_message = group.get("assistant_message")
        message = (
            copy.deepcopy(dict(raw_message))
            if isinstance(raw_message, Mapping)
            else None
        )
        if kind in {"user", "system", "permission_resolution"}:
            if message and message.get("role") in {"user", "system"}:
                history.append(message)
            continue
        if message is None or message.get("role") != "assistant":
            continue
        events = list(group.get("events") or ())
        observation_messages: list[dict[str, Any]] = []
        for event in events:
            if event.get("type") != "observation":
                continue
            result = event.get("result")
            if not isinstance(result, Mapping):
                continue
            messages = result.get("messages")
            if isinstance(messages, list):
                observation_messages.extend(
                    copy.deepcopy(dict(item))
                    for item in messages
                    if isinstance(item, Mapping)
                )
            if not observation_messages and isinstance(result.get("observation"), str):
                observation_messages.append(
                    {"role": "user", "content": result["observation"]}
                )
        if not observation_messages:
            observation_messages = [
                {"role": "user", "content": "[Observation]\nERROR:\nmissing"}
            ]
        history.extend([message, *observation_messages])
    return history


def _nine_title_handoff() -> str:
    return "\n\n".join(f"## {field}\n- recorded." for field in HANDOFF_FIELDS)


def test_restore_keeps_head_handoff_and_uncovered_groups(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-compact", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(10):
        _append_code_observation(ledger, index)

    groups = store.list_action_groups("root-compact")
    assert [group["kind"] for group in groups] == ["user"] + ["code"] * 10
    # Numbered among the 10 code/observation groups: cover through the 6th
    # (groups[6]), restore messages from the 7th-10th (groups[7:11]).
    sixth = groups[6]
    ledger.append_compaction("## Objective\nX", sixth["ordinal"])

    history = restore_action_history(store, "root-compact")
    expected_tail: list[dict] = []
    for index in range(6, 10):
        expected_tail.extend(_code_messages(index))
    assert history == [
        {"role": "user", "content": "task"},
        _handoff_note("## Objective\nX"),
        *expected_tail,
    ]
    store.close()


def test_second_compaction_replaces_the_earlier_note(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-stack", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(10):
        _append_code_observation(ledger, index)

    groups = store.list_action_groups("root-stack")
    ledger.append_compaction("## Objective\nfirst", groups[4]["ordinal"])
    ledger.append_compaction("## Objective\nsecond", groups[7]["ordinal"])

    history = restore_action_history(store, "root-stack")
    expected_tail: list[dict] = []
    for index in range(7, 10):
        expected_tail.extend(_code_messages(index))
    assert history == [
        {"role": "user", "content": "task"},
        _handoff_note("## Objective\nsecond"),
        *expected_tail,
    ]
    assert not any(item.get("content") == "## Objective\nfirst" for item in history)
    store.close()


def test_compaction_group_is_structurally_compatible_with_legacy_reduce(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-compat", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(4):
        _append_code_observation(ledger, index)
    groups_before = store.list_action_groups("root-compat")
    ledger.append_compaction("## Objective\nX", groups_before[2]["ordinal"])

    groups = store.list_action_groups("root-compat")
    compaction = next(group for group in groups if group["kind"] == "compaction")
    assert compaction["assistant_message"] is None
    event = compaction["events"][0]
    assert event["type"] == "compaction"
    assert event["result"]["covered_through_ordinal"] == groups_before[2]["ordinal"]

    legacy = _legacy_reduce_action_groups(groups)
    full = [
        {"role": "user", "content": "task"},
        *_code_messages(0),
        *_code_messages(1),
        *_code_messages(2),
        *_code_messages(3),
    ]
    assert legacy == full
    compacted = reduce_action_groups(groups)
    assert len(compacted) < len(legacy)
    store.close()


def test_restore_survives_store_close_and_reopen(tmp_path):
    db_path = tmp_path / "openai4s.db"
    store = get_store(db_path)
    ledger = RuntimeActionLedger(store, "root-reopen", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(10):
        _append_code_observation(ledger, index)
    groups = store.list_action_groups("root-reopen")
    ledger.append_compaction("## Objective\nX", groups[6]["ordinal"])
    before = restore_action_history(store, "root-reopen")
    store.close()

    reopened = get_store(db_path)
    after = restore_action_history(reopened, "root-reopen")
    assert after == before
    reopened.close()


def test_covered_through_maps_middle_count_onto_last_fully_covered_group(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-map", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(10):
        _append_code_observation(ledger, index)
    groups = store.list_action_groups("root-map")
    # Middle is code groups 1-6 (12 messages after the head user). Tail is
    # the remaining 4 code groups. Cover bound is the 6th code group.
    compacted_messages: list[dict] = []
    for index in range(6):
        compacted_messages.extend(_code_messages(index))
    assert compaction_cover_ordinal(groups, compacted_messages) == groups[6]["ordinal"]
    assert ledger.covered_through_ordinal(compacted_messages) == groups[6]["ordinal"]
    store.close()


def test_web_compaction_survives_seed_messages_rebuild(monkeypatch, tmp_path):
    import openai4s.agent.compaction as comp_mod

    monkeypatch.setattr(
        comp_mod,
        "chat",
        lambda *_args, **_kwargs: {
            "content": _nine_title_handoff(),
            "finish_reason": "stop",
        },
    )
    runner, _hub, state = _runner(tmp_path)
    try:
        ledger = RuntimeActionLedger(
            runner.store,
            state.root_frame_id,
            "turn-web",
            branch_id=state.branch_id,
        )
        ledger.append_user({"role": "user", "content": "task"})
        fill = "k" * 4000
        for index in range(10):
            _append_code_observation(ledger, index, fill=fill)

        state.messages = []
        runner._seed_messages(state)
        full_len = len(state.messages)
        assert full_len > 10
        state.active_action_ledger = ledger

        policy = CompactionPolicy(
            runner.cfg,
            metadata_provider=lambda _s: runner._context_archive_metadata(
                state, ledger
            ),
            archive_sink=lambda payload: runner._archive_compaction_record(
                state, dict(payload), ledger
            ),
            context_budget_provider=lambda _s: 2_000,
        )
        prepared = list(policy.prepare(RunState(state.messages)))
        compacted_len = len(prepared)
        assert compacted_len < full_len

        groups = runner.store.list_action_groups(
            state.root_frame_id, branch_id=state.branch_id
        )
        compaction = next(group for group in groups if group["kind"] == "compaction")
        assert compaction["assistant_message"] is None

        state.messages = []
        runner._seed_messages(state)
        assert len(state.messages) == compacted_len
        assert any(message.get("compaction_handoff") for message in state.messages)

        workbench = SessionWorkbenchStateService(
            runner.store,
            state_for=lambda _root: state,
            history_for=lambda _root: list(state.messages),
            llm_config_for=lambda _state: LLMConfig(
                provider="deepseek", model="deepseek-chat", api_key="test"
            ),
            pending_for=lambda _root: (),
            context_window_fallback=10_000,
        )
        assert workbench.context(state.root_frame_id)["handoff"] is True
    finally:
        state.active_action_ledger = None
        runner.close()
