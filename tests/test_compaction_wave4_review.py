"""What the Wave 4 review found once the six lanes were integrated.

Each test here failed against the integrated tree before its fix: the
durable ledger record was written for compactions the policy then rejected;
coverage was keyed by ordinal, which restarts at 0 on a forked branch, and
guessed toward deleting the tail when the live history and the ledger could
not be aligned; a compaction group without its event was applied as an empty
note; the summary ``max_tokens`` was rejected by the capability validator on
any model capped under 8192; truncation was recognised only on the OpenAI
wire; a zero context budget compacted every turn; a poisoned workspace
``.openai4s/context`` tripped the failure breaker; the Artifact path never
wrote the kernel-readable copy; and a reopened breaker re-tripped on one
failure.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import openai4s.agent.compaction as comp_mod
import openai4s.agent.runtime as runtime
from openai4s.agent.actions import CodeCell
from openai4s.agent.compaction import (
    CompactionSummaryError,
    compact,
    estimate_context,
    externalize_large_outputs,
)
from openai4s.agent.events import ActionRouted, OutcomeProduced, ReplyReceived
from openai4s.agent.ledger import (
    RuntimeActionLedger,
    compaction_cover_group_id,
    reduce_action_groups,
    restore_action_history,
)
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState
from openai4s.agent.runtime import CompactionPolicy
from openai4s.config import get_config
from openai4s.llm import clear_capability_overrides, set_capability_override
from openai4s.store import get_store

_BUDGET = 10_000
_TRIGGER_RATIO = 0.75
_FILL = "k" * 20_000


def _handoff(token: str = "done") -> str:
    return "\n\n".join(f"## {field}\n- {token}." for field in comp_mod.HANDOFF_FIELDS)


def _partial_handoff() -> str:
    return "\n\n".join(f"## {field}\n- cut." for field in comp_mod.HANDOFF_FIELDS[:3])


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        compaction_dir="archive",
        context_window_tokens=_BUDGET,
        compaction_trigger_ratio=_TRIGGER_RATIO,
        llm=SimpleNamespace(max_tokens=8192),
    )


def _policy(**kwargs) -> CompactionPolicy:
    return CompactionPolicy(
        _cfg(), context_budget_provider=lambda _state: _BUDGET, **kwargs
    )


def _big_state() -> RunState:
    return RunState([{"role": "user", "content": "y" * (_BUDGET * 4)}])


def _append_code_observation(ledger: RuntimeActionLedger, index: int) -> None:
    content = f"```python\nprint({index})\n```"
    observation = f"[Observation]\nstdout:\n{index}"
    ledger.emit(ReplyReceived(ModelReply(content=content), index))
    ledger.emit(ActionRouted(CodeCell("python", f"print({index})\n"), index))
    ledger.emit(
        OutcomeProduced(
            ExecutionOutcome(
                ({"role": "user", "content": observation},), observation=observation
            ),
            index,
        )
    )


def _code_messages(index: int) -> list[dict]:
    return [
        {"role": "assistant", "content": f"```python\nprint({index})\n```"},
        {"role": "user", "content": f"[Observation]\nstdout:\n{index}"},
    ]


# --- V1: the durable record follows the policy's decision -------------------


def test_rejected_compaction_writes_no_durable_record(monkeypatch):
    recorded: list[dict] = []

    def not_smaller(messages, cfg, **kwargs):
        # compact() hands its archive payload to the sink before returning.
        kwargs["archive_sink"]({"handoff": "x", "compacted_messages": []})
        return list(messages)

    monkeypatch.setattr(runtime, "compact", not_smaller)
    policy = _policy(archive_sink=recorded.append)
    state = _big_state()

    prepared = policy.prepare(state)

    assert list(prepared) == state.messages
    assert recorded == [], "a rejected compaction was recorded as applied"


def test_adopted_compaction_writes_the_record_once(monkeypatch):
    recorded: list[dict] = []

    def smaller(messages, cfg, **kwargs):
        kwargs["archive_sink"]({"handoff": "x", "compacted_messages": []})
        return [{"role": "user", "content": "short"}]

    monkeypatch.setattr(runtime, "compact", smaller)
    policy = _policy(archive_sink=recorded.append)

    prepared = policy.prepare(_big_state())

    assert list(prepared) == [{"role": "user", "content": "short"}]
    assert len(recorded) == 1


def test_record_failure_means_the_compaction_is_not_adopted(monkeypatch):
    def smaller(messages, cfg, **kwargs):
        kwargs["archive_sink"]({"handoff": "x", "compacted_messages": []})
        return [{"role": "user", "content": "short"}]

    def refuse(_payload):
        raise RuntimeError("ledger admission refused")

    monkeypatch.setattr(runtime, "compact", smaller)
    policy = _policy(archive_sink=refuse)
    state = _big_state()

    prepared = policy.prepare(state)

    assert list(prepared) == state.messages
    assert policy.failure_streak == 1
    assert "ledger admission refused" in state.metadata["last_compaction_error"]


# --- V2 + V6: coverage is positional in the branch sequence -----------------


def test_fork_child_tail_survives_a_compaction_over_the_parent_prefix(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    root = store.new_frame(project_id="default", status="ready")
    parent = RuntimeActionLedger(store, root, "turn-parent")
    parent.append_user({"role": "user", "content": "task"})
    for index in range(4):
        _append_code_observation(parent, index)
    checkpoint = store.create_session_checkpoint(
        root_frame_id=root,
        branch_id=root,
        reason="fork base",
        workspace_tree_id="a" * 64,
        action_cursor=4,
    )
    store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id="br-child",
    )
    child = RuntimeActionLedger(store, root, "turn-child", branch_id="br-child")
    for index in range(10, 13):
        _append_code_observation(child, index)

    # Child ordinals restart at 0 behind the inherited parent prefix.
    child_groups = store.list_action_groups(root, branch_id="br-child")
    assert [group["ordinal"] for group in child_groups] == [0, 1, 2]

    middle: list[dict] = []
    for index in range(4):
        middle.extend(_code_messages(index))
    cover = child.covered_through_group_id(middle)
    parent_groups = store.list_action_groups(root, branch_id=root)
    assert cover == parent_groups[4]["group_id"]
    child.append_compaction(_handoff(), cover)

    restored = restore_action_history(store, root, branch_id="br-child")
    expected_tail: list[dict] = []
    for index in range(10, 13):
        expected_tail.extend(_code_messages(index))
    assert restored[0] == {"role": "user", "content": "task"}
    assert restored[1].get("compaction_handoff") is True
    assert restored[2:] == expected_tail
    store.close()


def test_misaligned_middle_covers_nothing_rather_than_the_tail(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-drift", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(4):
        _append_code_observation(ledger, index)
    groups = store.list_action_groups("root-drift")

    # More live messages than the ledger reconstructs: nothing is provable.
    too_many = [{"role": "user", "content": "x"}] * 20
    assert compaction_cover_group_id(groups, too_many) is None
    # A middle that ends inside one group covers no whole group either.
    assert compaction_cover_group_id(groups, _code_messages(0)[:1]) is None

    ledger.append_compaction(_handoff(), None)
    restored = restore_action_history(store, "root-drift")
    full: list[dict] = []
    for index in range(4):
        full.extend(_code_messages(index))
    assert restored[0] == {"role": "user", "content": "task"}
    assert restored[1].get("compaction_handoff") is True
    assert restored[2:] == full, "an unaligned bound must not drop anything"
    store.close()


# --- V13: a compaction group without its event is skipped -------------------


def test_compaction_group_without_an_event_keeps_the_previous_note(tmp_path):
    store = get_store(tmp_path / "openai4s.db")
    ledger = RuntimeActionLedger(store, "root-orphan", "turn-1")
    ledger.append_user({"role": "user", "content": "task"})
    for index in range(4):
        _append_code_observation(ledger, index)
    groups = store.list_action_groups("root-orphan")
    ledger.append_compaction("## Objective\nH1", groups[2]["group_id"])
    healthy = restore_action_history(store, "root-orphan")
    assert "H1" in healthy[1]["content"]

    # The event-less shape a crash between two statements would have left.
    store.append_action_group(
        root_frame_id="root-orphan",
        branch_id="root-orphan",
        turn_id="turn-1",
        kind="compaction",
        assistant_message=None,
    )

    assert restore_action_history(store, "root-orphan") == healthy
    assert not any(
        message.get("role") == "system" and not message.get("content")
        for message in reduce_action_groups(store.list_action_groups("root-orphan"))
    )
    store.close()


# --- V4 / V3 / V7: the summary call respects the wire it runs on ------------


@pytest.fixture
def _capability_override():
    yield
    clear_capability_overrides()


def test_summary_max_tokens_is_clamped_to_the_model_cap(
    monkeypatch, tmp_path, _capability_override
):
    # A catalogue provider with an exact-model override under the 8192 floor,
    # the documented mechanism for a self-hosted endpoint's output cap.
    set_capability_override("ark", model="tiny-cap", max_output_tokens=4096)
    cfg = _cfg()
    cfg.llm = SimpleNamespace(
        provider="ark", model="tiny-cap", base_url=None, max_tokens=8192
    )
    captured: list[dict] = []

    def fake_chat(messages, llm_cfg, **kwargs):
        captured.append(dict(kwargs))
        return {"content": _handoff(), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        *(m for i in range(6) for m in _code_messages(i)),
    ]
    compact(messages, cfg, keep_recent=2, archive_dir=tmp_path)

    assert captured and all(call["max_tokens"] == 4096 for call in captured)


@pytest.mark.parametrize(
    "reply",
    [
        {"finish_reason": "max_tokens"},
        {"finish_reason": "MAX_TOKENS"},
        {"finish_reason": "stop", "provider_finish_reason": "incomplete"},
    ],
)
def test_truncation_on_every_wire_raises_instead_of_padding(monkeypatch, reply):
    calls: list[int] = []

    def fake_chat(messages, llm_cfg, **kwargs):
        calls.append(kwargs["max_tokens"])
        return {"content": _partial_handoff(), **reply}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        *(m for i in range(6) for m in _code_messages(i)),
    ]
    with pytest.raises(CompactionSummaryError, match="truncated"):
        compact(messages, get_config(), keep_recent=2)
    # One retry at a larger budget, then the honest failure.
    assert len(calls) == 2 and calls[1] > calls[0]


def test_truncated_chunk_retries_once_with_double_budget(monkeypatch):
    calls: list[int] = []

    def fake_chat(messages, llm_cfg, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            return {"content": _partial_handoff(), "finish_reason": "length"}
        return {"content": _handoff(), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        *(m for i in range(6) for m in _code_messages(i)),
    ]
    out = compact(messages, get_config(), keep_recent=2)

    assert calls[1] == 2 * calls[0]
    assert any(m.get("compaction_handoff") for m in out)


# --- V8: a zero budget is unknown, not a zero window -------------------------


def test_zero_context_budget_falls_back_to_the_config_window(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        runtime, "compact", lambda messages, cfg, **kw: calls.append(1) or []
    )
    policy = CompactionPolicy(_cfg(), context_budget_provider=lambda _s: 0)
    state = RunState([{"role": "user", "content": "small"}])

    policy.prepare(state)

    assert calls == [], "a zero budget compacted a three-token history"
    assert policy._context_budget(state) is None


def test_compact_treats_zero_budget_as_unknown(monkeypatch):
    seen: list[int] = []

    def fake_chat(messages, llm_cfg, **kwargs):
        seen.append(estimate_context([messages[1]]).total)
        return {"content": _handoff(), "finish_reason": "stop"}

    monkeypatch.setattr(comp_mod, "chat", fake_chat)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        *(m for i in range(12) for m in _code_messages(i)),
    ]
    compact(messages, get_config(), keep_recent=2, context_budget=0)
    # One batch under the config window, not one chat() per atomic segment.
    assert len(seen) == 1


# --- V5 / V9: the kernel copy is best-effort and written on every path -------


def test_poisoned_workspace_context_dir_costs_only_the_kernel_copy(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".openai4s").mkdir(parents=True)
    (workspace / ".openai4s" / "context").write_text("not a directory")
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "```python\nprint(1)\n```"},
        {"role": "user", "content": "[Observation]\n" + _FILL},
    ]

    projected = externalize_large_outputs(
        messages, tmp_path / "archive", workspace=workspace
    )
    archive = projected[-1]["content_archive"]
    assert "archive_ref" in archive and "workspace_ref" not in archive

    inline = externalize_large_outputs(messages, None, workspace=workspace)
    assert inline[-1]["content"] == messages[-1]["content"]


def test_policy_drops_the_workspace_after_an_externalize_failure(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    seen: list[dict] = []

    def failing_externalize(messages, *_args, **_kwargs):
        raise OSError("read-only mount")

    def record(messages, cfg, **kwargs):
        seen.append(kwargs)
        return [{"role": "user", "content": "short"}]

    monkeypatch.setattr(runtime, "externalize_large_outputs", failing_externalize)
    monkeypatch.setattr(runtime, "compact", record)
    policy = _policy(workspace_provider=lambda _s: str(workspace))

    policy.prepare(_big_state())

    assert seen and seen[0]["workspace"] is None
    assert policy.failure_streak == 0


def test_artifact_path_also_writes_the_kernel_readable_copy(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "```python\nprint(1)\n```"},
        {"role": "user", "content": "[Observation]\n" + _FILL},
    ]

    projected = externalize_large_outputs(
        messages,
        None,
        artifact_archiver=lambda *_a: {"artifact_id": "art", "version_id": "v1"},
        workspace=workspace,
    )

    archive = projected[-1]["content_archive"]
    ref = archive["workspace_ref"]
    assert json.loads((workspace / ref).read_text("utf-8"))["content"].endswith(
        _FILL[-16:]
    )
    assert projected[-1]["artifact_refs"] == [
        {
            key: archive[key]
            for key in ("artifact_id", "version_id", "sha256", "original_chars")
        }
    ]
    assert f"json.load(open({ref!r}))['content']" in projected[-1]["content"]


# --- V12: a reopened breaker gets its full failure budget back ---------------


def test_reopened_breaker_gets_a_fresh_failure_budget(monkeypatch):
    calls: list[int] = []

    def boom(messages, cfg, **kwargs):
        calls.append(1)
        raise RuntimeError("summary 4xx")

    monkeypatch.setattr(runtime, "compact", boom)
    policy = _policy()
    state = _big_state()
    policy.prepare(state)
    policy.prepare(state)
    assert policy.circuit_open and policy.failure_streak == 2

    grown = int(policy.circuit_open_total * policy.circuit_retry_growth)
    while estimate_context(state.messages).total < grown:
        state.messages[0]["content"] += "z" * 1024
    policy.prepare(state)

    assert len(calls) == 3
    assert policy.circuit_open is False
    assert policy.failure_streak == 1
    policy.prepare(state)
    assert len(calls) == 4
