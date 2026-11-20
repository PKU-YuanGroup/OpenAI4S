"""Pure orchestration contracts for :mod:`openai4s.agent.engine`."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import openai4s.agent.engine as engine_module
import openai4s.agent.progress_circuit as progress_circuit_module
from openai4s.agent.actions import CodeCell, FinalizeAction, NativeToolBatch
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import (
    ActionRouted,
    OutcomeProduced,
    ReplyReceived,
    RunFinished,
    RunStarted,
    TextDelta,
    TurnStarted,
)
from openai4s.agent.finalize import execute_finalize_action
from openai4s.agent.models import ExecutionOutcome, ModelReply, RunState


def _call(call_id="call_1", name="request_network_access", arguments=None):
    arguments = {"domain": "example.org"} if arguments is None else arguments
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": 0,
        "raw_arguments": '{"domain":"example.org"}',
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _reply(content="", *, tool_calls=(), assistant_message=None):
    message = assistant_message or {"role": "assistant", "content": content}
    return {
        "content": content,
        "reasoning": None,
        "usage": {},
        "finish_reason": "stop",
        "raw": {"test": True},
        "tool_calls": list(tool_calls),
        "assistant_message": message,
    }


class FakeModel:
    def __init__(self, replies, delta_batches=()):
        self.replies = list(replies)
        self.delta_batches = list(delta_batches)
        self.calls = []

    def complete(self, messages, on_delta):
        self.calls.append(copy.deepcopy(list(messages)))
        index = len(self.calls) - 1
        deltas = self.delta_batches[index] if index < len(self.delta_batches) else ()
        for delta in deltas:
            on_delta(delta)
        return self.replies.pop(0)


class FakeContext:
    def __init__(self, prefix=()):
        self.prefix = list(prefix)
        self.calls = []

    def prepare(self, state):
        self.calls.append(copy.deepcopy(state.messages))
        return [*self.prefix, *state.messages]


class FakeExecutor:
    def __init__(self, outcomes=(), handler=None):
        self.outcomes = list(outcomes)
        self.handler = handler
        self.calls = []

    def execute(self, action, reply, state):
        self.calls.append(
            {
                "action": action,
                "reply": reply,
                "messages": copy.deepcopy(state.messages),
            }
        )
        if self.handler is not None:
            return self.handler(action, reply, state)
        return self.outcomes.pop(0) if self.outcomes else ExecutionOutcome()


class FakeEvents:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeCancellation:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = 0

    def cancelled(self):
        self.calls += 1
        return self.answers.pop(0) if self.answers else False


class FakeCompletion:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = 0

    def completion(self):
        self.calls += 1
        return self.answers.pop(0) if self.answers else None


class FakeInterceptor:
    def __init__(self, replacement=None):
        self.replacement = replacement
        self.calls = []

    def intercept(self, reply, state):
        self.calls.append((reply, state.turn))
        if callable(self.replacement):
            return self.replacement(reply, state)
        return self.replacement


def _engine(replies, outcomes=(), *, max_turns=4, **overrides):
    model = FakeModel(replies, overrides.pop("delta_batches", ()))
    context = overrides.pop("context", FakeContext())
    executor = overrides.pop("executor", FakeExecutor(outcomes))
    events = overrides.pop("events", FakeEvents())
    cancellation = overrides.pop("cancellation", FakeCancellation())
    interceptor = overrides.pop("interceptor", FakeInterceptor())
    assert not overrides
    engine = AgentEngine(
        model,
        executor,
        context_policy=context,
        event_sink=events,
        cancellation=cancellation,
        reply_interceptor=interceptor,
        max_turns=max_turns,
    )
    return engine, model, context, executor, events, cancellation, interceptor


def test_code_action_is_routed_to_the_executor_through_fake_ports():
    context = FakeContext([{"role": "system", "content": "prepared"}])
    outcome = ExecutionOutcome(
        history_messages=({"role": "user", "content": "[Observation]\n42"},)
    )
    engine, model, context, executor, events, _, _ = _engine(
        [_reply("working\n```python\nprint(6 * 7)\n```")],
        [outcome],
        max_turns=1,
        context=context,
    )

    result = engine.run([{"role": "user", "content": "compute"}])

    assert executor.calls[0]["action"] == CodeCell("python", "print(6 * 7)\n")
    assert model.calls[0][0] == {"role": "system", "content": "prepared"}
    assert len(context.calls) == 1
    assert result.messages[-1] == {"role": "user", "content": "[Observation]\n42"}
    assert isinstance(events.events[3], ActionRouted)


def test_native_calls_take_priority_over_fenced_code():
    call = _call()
    outcome = ExecutionOutcome(completion={"allowed": True})
    engine, _, _, executor, _, _, _ = _engine(
        [_reply("```python\nraise AssertionError\n```", tool_calls=[call])],
        [outcome],
    )

    result = engine.run([{"role": "user", "content": "continue"}])

    action = executor.calls[0]["action"]
    assert isinstance(action, NativeToolBatch)
    assert [item.id for item in action.calls] == ["call_1"]
    assert result.stop_reason == "submitted"


def test_replay_ready_assistant_message_and_tool_results_stay_grouped():
    calls = [_call("call_1"), _call("call_2", "delegate", {"task": "check"})]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": calls,
        "wire_state": {"openai_message": {"opaque": True}},
    }
    tool_results = (
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "allowed",
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": "delegated",
        },
    )
    engine, _, _, executor, _, _, _ = _engine(
        [_reply(tool_calls=calls, assistant_message=assistant)],
        [ExecutionOutcome(history_messages=tool_results, completion={"done": True})],
    )
    user = {"role": "user", "content": "use both"}

    result = engine.run([user])

    assert list(result.messages) == [user, assistant, *tool_results]
    assert executor.calls[0]["messages"][-1] == assistant


def test_plain_reply_is_a_no_action_turn_and_not_success():
    engine, _, _, executor, _, _, _ = _engine(
        [_reply("A prose-only answer is not completion.")], max_turns=1
    )

    result = engine.run([{"role": "user", "content": "answer"}])

    assert executor.calls[0]["action"] is None
    assert result.stop_reason == "max_turns"
    assert result.completion is None


def test_direct_model_reply_gets_a_replay_safe_assistant_message():
    model = FakeModel([ModelReply(content="still working")])
    executor = FakeExecutor([ExecutionOutcome(completion={"done": True})])
    engine = AgentEngine(model, executor)

    result = engine.run([{"role": "user", "content": "continue"}])

    assert result.messages[-1] == {
        "role": "assistant",
        "content": "still working",
    }


def test_completion_without_a_custom_reason_is_the_submitted_success_path():
    engine, _, _, _, _, _, _ = _engine(
        [_reply("```python\nhost.submit_output(...)\n```")],
        [ExecutionOutcome(completion={"answer": 42})],
    )

    result = engine.run([{"role": "user", "content": "finish"}])

    assert result.stop_reason == "submitted"
    assert result.completion == {"answer": 42}
    assert result.turns == 1


def test_max_turns_is_a_hard_bound():
    engine, model, _, executor, events, cancellation, _ = _engine(
        [_reply("still working"), _reply("still working")], max_turns=2
    )

    result = engine.run([{"role": "user", "content": "loop"}])

    assert result.stop_reason == "max_turns"
    assert result.turns == 2
    assert len(model.calls) == len(executor.calls) == 2
    # Check once before the model and again before accepting an outcome.
    assert cancellation.calls == 4
    assert isinstance(events.events[-1], RunFinished)


def test_cancellation_stops_before_context_model_or_executor_work():
    cancellation = FakeCancellation([True])
    engine, model, context, executor, events, _, interceptor = _engine(
        [_reply("must not run")], cancellation=cancellation
    )

    result = engine.run([{"role": "user", "content": "cancel"}])

    assert result.stop_reason == "cancelled"
    assert result.turns == 0
    assert not model.calls and not context.calls and not executor.calls
    assert not interceptor.calls
    assert [type(event) for event in events.events] == [RunStarted, RunFinished]


def test_external_completion_stops_before_another_model_call():
    model = FakeModel([_reply("must not run")])
    executor = FakeExecutor()
    completion = FakeCompletion([{"background": "done"}])
    engine = AgentEngine(model, executor, completion=completion)

    result = engine.run([{"role": "user", "content": "wait"}])

    assert result.stop_reason == "submitted"
    assert result.completion == {"background": "done"}
    assert result.turns == 0
    assert not model.calls and not executor.calls


def test_cancellation_after_execution_wins_over_simultaneous_submit():
    model = FakeModel([_reply("```python\nhost.submit_output(...)\n```")])
    executor = FakeExecutor([ExecutionOutcome(completion={"done": True})])
    cancellation = FakeCancellation([False, True])
    engine = AgentEngine(model, executor, cancellation=cancellation)

    result = engine.run([{"role": "user", "content": "finish"}])

    assert result.stop_reason == "cancelled"
    assert result.completion is None
    assert result.turns == 1


def test_interceptor_can_replace_the_reply_before_a_custom_plan_stop():
    replacement = _reply("```r\nplan <- 'pause'\n```")

    def stop_plan(action, reply, state):
        assert action == CodeCell("r", "plan <- 'pause'\n")
        assert reply.content == replacement["content"]
        assert state.messages[-1] == replacement["assistant_message"]
        return ExecutionOutcome(
            completion={"plan": "pause"}, stop_reason="planned_stop"
        )

    interceptor = FakeInterceptor(replacement)
    executor = FakeExecutor(handler=stop_plan)
    engine, _, _, _, _, _, _ = _engine(
        [_reply("unintercepted")],
        executor=executor,
        interceptor=interceptor,
    )

    result = engine.run([{"role": "user", "content": "make a plan"}])

    assert len(interceptor.calls) == 1
    assert result.stop_reason == "planned_stop"
    assert result.completion == {"plan": "pause"}
    assert result.last_reply == ModelReply.from_mapping(replacement)


def test_stream_text_deltas_are_typed_events_in_turn_order():
    engine, _, _, _, events, _, _ = _engine(
        [_reply("hello")],
        [ExecutionOutcome(completion="done")],
        delta_batches=[("hel", "lo")],
    )

    engine.run([{"role": "user", "content": "stream"}])

    deltas = [event for event in events.events if isinstance(event, TextDelta)]
    assert deltas == [TextDelta("hel", 0), TextDelta("lo", 0)]
    assert [type(event) for event in events.events] == [
        RunStarted,
        TurnStarted,
        TextDelta,
        TextDelta,
        ReplyReceived,
        ActionRouted,
        OutcomeProduced,
        RunFinished,
    ]


def test_engine_has_no_direct_runtime_infrastructure_imports():
    source = Path(engine_module.__file__).read_text(encoding="utf-8")
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            if module == "openai4s":
                imports.extend(f"openai4s.{alias.name}" for alias in node.names)

    forbidden = (
        "openai4s.kernel",
        "openai4s.host_dispatch",
        "openai4s.store",
        "openai4s.server",
    )
    assert not [
        name
        for name in imports
        if any(name == root or name.startswith(root + ".") for root in forbidden)
    ]


def test_cancelled_model_reply_is_not_recorded_as_history():
    """A mid-call cancellation must not leave an empty assistant turn behind.

    ``ChatModel`` answers a call cancelled while the request was on the wire
    with the canonical no-op reply (``finish_reason == "cancelled"``). The Web
    session list and the Action Ledger both alias ``state.messages``, so
    appending that reply's empty assistant message poisoned every later turn
    of the session on the Anthropic and Gemini wires, which reject a non-final
    empty assistant message. The engine finishes before the reply is recorded,
    routed or executed -- exactly as a cancellation seen between turns does.
    """
    cancelled_reply = {
        "content": "",
        "tool_calls": [],
        "assistant_message": {"role": "assistant", "content": ""},
        "finish_reason": "cancelled",
    }
    engine, model, _context, executor, events, _, interceptor = _engine(
        [cancelled_reply], cancellation=FakeCancellation([False, True])
    )
    history = [{"role": "user", "content": "first request"}]

    result = engine.run(history)

    assert result.stop_reason == "cancelled"
    assert result.turns == 0
    assert len(model.calls) == 1
    assert not executor.calls and not interceptor.calls
    assert [type(event) for event in events.events] == [
        RunStarted,
        TurnStarted,
        RunFinished,
    ]
    assert result.messages[-1] == {"role": "user", "content": "first request"}


_WRITE = {"path": "cell1_setup_probe.py", "content": "import sys\nprint(sys.version)\n"}
_WRITTEN = '{"path": "cell1_setup_probe.py", "bytes": 32}'


@pytest.mark.parametrize("name", ["write_file", "finalize_response"])
def test_native_call_that_displaces_a_cell_says_the_cell_did_not_run(name):
    # The Web session that prompted this: the model wrote a probe script with
    # write_file and ran it in a ```python cell of the SAME reply. Only the
    # native call runs, the model saw nothing but a byte count, sent the same
    # pair again, and the circuit stopped the turn on the third write.
    call = _call(name=name, arguments=dict(_WRITE))
    result_message = {"role": "tool", "tool_call_id": "call_1", "content": _WRITTEN}
    engine, model, _, _, events, _, _ = _engine(
        [
            _reply(
                "```python\nexec(open('cell1_setup_probe.py').read())\n```",
                tool_calls=[call],
            ),
            _reply("Done."),
        ],
        [
            ExecutionOutcome(
                history_messages=(result_message,), observation="[Tool Results]"
            )
        ],
        max_turns=2,
    )

    engine.run([{"role": "user", "content": "probe the environment"}])

    seen = model.calls[1][-1]
    note = seen["content"][len(_WRITTEN) :]
    assert seen["role"] == "tool" and seen["content"].startswith(_WRITTEN)
    assert "```python code cell. It was NOT executed" in note
    assert "ONLY action of your next reply" in note
    # The Action Ledger records OutcomeProduced, so a restored history reads
    # the same words the live model did.
    produced = [e.outcome for e in events.events if isinstance(e, OutcomeProduced)]
    assert produced[0].history_messages[-1] == seen
    assert produced[0].observation == "[Tool Results]" + note
    assert result_message["content"] == _WRITTEN


@pytest.mark.parametrize(
    "content",
    [
        "Writing the probe script now.",
        '```json\n{"path": "cell1_setup_probe.py"}\n```',
        "```python\nprint('never closed')\n",
    ],
)
def test_native_call_without_a_displaced_cell_is_left_alone(content):
    call = _call(name="write_file", arguments=dict(_WRITE))
    result_message = {"role": "tool", "tool_call_id": "call_1", "content": _WRITTEN}
    engine, model, _, _, _, _, _ = _engine(
        [_reply(content, tool_calls=[call]), _reply("Done.")],
        [ExecutionOutcome(history_messages=(result_message,))],
        max_turns=2,
    )

    engine.run([{"role": "user", "content": "probe the environment"}])

    assert model.calls[1][-1] == result_message


_SEARCHED = "[Tool: web_search]\nresults (1)"


def _run_native_reply(content, *, name="web_search", arguments=None):
    """One native call whose reply says ``content``; returns what the model read."""
    call = _call(name=name, arguments=arguments or {"query": "artemisinin"})
    result_message = {"role": "tool", "tool_call_id": "call_1", "content": _SEARCHED}
    engine, model, _, _, _, _, _ = _engine(
        [_reply(content, tool_calls=[call]), _reply("Done.")],
        [ExecutionOutcome(history_messages=(result_message,))],
        max_turns=2,
    )
    engine.run([{"role": "user", "content": "find the data"}])
    return model.calls[1][-1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "Now I'll download the PDF in the kernel and extract its table.",
        "I'll run this directly as a code cell.",
        "Let me do it in a Python cell next.",
        "Running it as a normal foreground cell instead.",
        "我会在内核里下载并解析这个 PDF。",
        "接下来用代码单元提取表格。",
        # The wordings of the same promise that the first pattern missed.
        "Now entering the Python kernel directly.",
        "Now I'll execute Python: create scratch space, check PDF tooling.",
        "Now I'll move into the Python runtime to download and parse the papers.",
        "ACS blocks direct fetch. I'll switch to the Python runtime and retry.",
        "Enough searching - entering the runtime now.",
        # What Doubao Seed wrote across twenty-one tool-only replies of one
        # Chinese session. The noun list matched the last of these and none of
        # the others.
        "绘图规范已加载。现在用 Python 检查 Excel 的工作表和 Layout 布局。",
        "规范已掌握，停止重复阅读。现在直接用 Python 读取 Excel，检查工作表与 Layout。",
        "规范已足够，现在正式运行 Python 读取 Excel 结构。",
        "规范阅读完毕。现在正式运行 Python 检查 Excel 工作表与 Layout。",
        "规范已充分掌握，现在正式运行代码检查 Excel 结构。",
        "明白，直接运行检查脚本。",
        "现在运行脚本检查 Excel 结构。",
        "运行解析脚本，得到链统计表和独立的结构文件。",
        "现在直接在代码单元中运行检查。",
        # The same promise in the other shapes the verbs take.
        "接下来执行 R 脚本画图。",
        "下面用 R 绘制年份分布图。",
        "我来写一段代码检查数据形状。",
        "把脚本跑一下看看输出。",
        # SCI-16: six replies of one session, each followed by one more read
        # of the same PDB file, named the cell by its count, not its language.
        "Now I'll run the full pipeline in a single cell.",
        "All record layouts confirmed. Running the complete pipeline now in a "
        "single cell.",
        "Next, I will execute the whole analysis in one cell.",
        "现在在一个单元里跑完整流程。",
        "接下来我会在一个 cell 中完成全部分析。",
        "下面我把整个流程放在一个cell里执行。",
    ],
)
def test_native_call_that_promises_a_cell_says_no_code_ran(content):
    # Doubao Seed 2.1 Pro said this in 30 of 48 consecutive replies and sent a
    # web search each time; the results never told it that no code had run.
    seen = _run_native_reply(content)

    assert seen.startswith(_SEARCHED)
    note = seen[len(_SEARCHED) :]
    assert "carried no ```python cell, so no code ran" in note
    assert "native tool calls cannot run Python" in note
    assert "ONLY action of your next reply" in note


@pytest.mark.parametrize(
    "content",
    [
        "Searching the literature for solubility tables.",
        "Single-cell data and T cell markers are out of scope here.",
        "The Linux kernel is not involved.",
        "Let me read the 2024 paper and search for paclitaxel tables.",
        "Checking what the search results say about solvent polarity.",
        "",
        # A report of code that did run is not a promise to run it.
        "脚本已运行完毕，结果见上。现在检索相关文献。",
        "上一步已经运行了脚本，现在检索文献。",
        "代码运行结果显示共有 3 个工作表，接下来检索文献。",
        "已用 Python 完成解析，现在把报告写入文件。",
        "运行完成后我会汇总结果。",
        "本次运行时间约 3 秒，接下来检索文献。",
        # A later item of a stated plan is not a promise about this reply: the
        # reviewed literature session opened with this and three searches.
        "我来完成这个任务：先检索文献和结构，再下载结构做链统计、用 R 画图，"
        "最后写中文综述。先并行检索文献数据库。",
        "先把脚本写入文件，然后运行脚本检查输出。",
        # Neither is a step that names no code at all.
        "文件已导入。现在检查 Excel 的工作表结构和原始数据格式。",
        "检索 RNA 聚合酶相关文献，并用 RCSB 接口核实条目。",
        "执行计划已更新：下一步检索数据库。",
        # A cell that is biology, a spreadsheet's cell, or a later plan step.
        "Expression was measured in a single cell type and in a single cell "
        "suspension.",
        "Loading it as a single cell-level matrix.",
        "每个 cell 中的 UMI 数差别很大，先检索相关方法。",
        "在一个单元格里填入样本编号。",
        "先读取文件，最后在一个单元里汇总。",
    ],
)
def test_native_call_that_does_not_promise_a_cell_is_left_alone(content):
    assert _run_native_reply(content) == _SEARCHED


def test_a_promise_next_to_a_tool_that_runs_code_is_not_a_broken_promise():
    seen = _run_native_reply(
        "I'll run this in the kernel as a background job.",
        name="exec_background",
        arguments={"code": "print(1)"},
    )

    assert seen == _SEARCHED


def test_a_displaced_cell_gets_only_its_own_note():
    seen = _run_native_reply(
        "Running it in the kernel.\n```python\nprint(1)\n```",
        name="write_file",
        arguments=dict(_WRITE),
    )

    assert "It was NOT executed" in seen
    assert "carried no ```python cell" not in seen


def _tool_only_run(contents, *, cell_after=None):
    """One ``web_search`` reply per entry of ``contents``, each a new query.

    Returns what the model read back from each tool result. ``cell_after``
    turns the reply at that index into a code cell instead.
    """
    replies = []
    for index, content in enumerate(contents):
        if index == cell_after:
            replies.append(_reply("```python\nprint(1)\n```"))
            continue
        call = _call(f"call_{index}", "web_search", {"query": f"term {index}"})
        replies.append(_reply(content, tool_calls=[call]))

    def executed(action, reply, state):
        if not isinstance(action, NativeToolBatch):
            return ExecutionOutcome(
                history_messages=({"role": "user", "content": "[Observation] 1"},)
            )
        return ExecutionOutcome(
            history_messages=(
                {
                    "role": "tool",
                    "tool_call_id": action.calls[0].id,
                    "content": _SEARCHED,
                },
            )
        )

    engine, _, _, _, events, _, _ = _engine(
        replies, executor=FakeExecutor(handler=executed), max_turns=len(replies)
    )
    engine.run([{"role": "user", "content": "analyse the plate"}])
    return [
        event.outcome.history_messages[-1]["content"]
        for event in events.events
        if isinstance(event, OutcomeProduced)
    ]


def test_a_tool_only_streak_is_told_once_that_tool_calls_run_no_code():
    # The replies no wording rule can read: "现在读取 Excel，检查工作表与
    # Layout" names neither Python nor code, was sent with a tool call eleven
    # times in the reviewed session, and drew no reminder at all.
    seen = _tool_only_run(["现在读取 Excel，检查工作表与 Layout。"] * 14)

    noted = [index for index, text in enumerate(seen) if text != _SEARCHED]
    assert noted == [5], "one note, on the sixth tool-only reply"
    note = seen[5][len(_SEARCHED) :]
    assert "your last 6 replies were tool calls only" in note
    assert "Tool calls never execute Python or R" in note
    assert "If this task needs no code, carry on." in note


def test_a_cell_ends_the_streak_and_the_next_one_is_counted_from_zero():
    seen = _tool_only_run([""] * 16, cell_after=3)

    # Three tool-only replies, a cell, then twelve more: the first streak is
    # too short for a note and the second earns exactly one, on its sixth.
    noted = [
        index
        for index, text in enumerate(seen)
        if _SEARCHED in text and text != _SEARCHED
    ]
    assert noted == [9]


def test_a_reply_that_named_the_cell_gets_that_note_not_the_generic_one():
    seen = _tool_only_run(
        [""] * 5 + ["现在正式运行 Python 读取 Excel 结构。"] + [""] * 3
    )

    assert "carried no ```python cell" in seen[5]
    assert "tool calls only" not in "".join(seen)


_PROMISE = "现在用 Python 读取 Excel。"
_CELL = (
    "```python\nimport pandas as pd\nprint(pd.ExcelFile('demo.xlsx').sheet_names)\n```"
)
# The filler tools the reviewed session sent, one different call per reply.
_FILLERS = (
    "glob_files",
    "write_todos",
    "list_dir",
    "list_artifacts",
    "load_skill",
    "review_status",
)


class PromisingModel:
    """Promises a cell and sends a filler tool whenever tools are on offer.

    ``offered`` records, per call, whether native tools were offered. With
    none on offer the reply is ``untooled`` -- by default the cell it keeps
    promising. ``contents`` overrides the text of the tooled reply by index.
    """

    def __init__(self, *, untooled=_CELL, contents=None):
        self.untooled = untooled
        self.contents = dict(contents or {})
        self.offered = []

    def complete(self, messages, on_delta, *, offer_tools=True):
        index = len(self.offered)
        self.offered.append(offer_tools)
        if not offer_tools:
            return _reply(self.untooled)
        name = _FILLERS[index % len(_FILLERS)]
        call = _call(f"call_{index}", name, {"path": f"work/{index}"})
        return _reply(self.contents.get(index, _PROMISE), tool_calls=[call])


def _answer(action, reply, state):
    if isinstance(action, NativeToolBatch):
        return ExecutionOutcome(
            history_messages=tuple(
                {"role": "tool", "tool_call_id": call.id, "content": f"{call.name} ok"}
                for call in action.calls
            )
        )
    if isinstance(action, CodeCell):
        observation = "[Observation] ['Layout', 'Data']"
    else:
        observation = "[system] No executable action found."
    return ExecutionOutcome(
        history_messages=({"role": "user", "content": observation},),
        observation=observation,
    )


def _promising_run(model, max_turns):
    executor = FakeExecutor(handler=_answer)
    engine = AgentEngine(model, executor, max_turns=max_turns)
    result = engine.run([{"role": "user", "content": "分析 demo.xlsx 的 Layout 表"}])
    kinds = [
        (
            "tools"
            if isinstance(call["action"], NativeToolBatch)
            else "cell" if isinstance(call["action"], CodeCell) else "prose"
        )
        for call in executor.calls
    ]
    return result, kinds


def test_a_model_that_keeps_promising_a_cell_gets_one_call_without_tools():
    # The reviewed run: twelve replies in a row promised Python, each drew the
    # announced-cell note, and each sent a different filler tool. The note
    # works; a model with tools on offer kept taking the tool.
    model = PromisingModel()

    result, kinds = _promising_run(model, max_turns=7)

    assert model.offered == [True, True, False, True, True, False, True]
    assert kinds == ["tools", "tools", "cell", "tools", "tools", "cell", "tools"]
    assert result.stop_reason == "max_turns"
    tool_results = [m["content"] for m in result.messages if m.get("role") == "tool"]
    # The second note in a row says what the next call will be; the first
    # does not, and neither does the first one after a cell ran.
    withheld = ["Tools are withheld for your next reply" in t for t in tool_results]
    assert withheld == [False, True, False, True, False]
    assert all("carried no ```python cell" in text for text in tool_results)


def test_without_the_withheld_call_the_same_model_spends_the_whole_budget(
    monkeypatch,
):
    # The control for the test above: the scripted model never sends its cell
    # while tools are on offer, which is what the live run looked like. It
    # describes the Engine before the switch, so it holds without it too.
    monkeypatch.setattr(
        engine_module, "ANNOUNCED_CELL_WITHHOLD_AFTER", 10**6, raising=False
    )
    # Nor the stop on the fifth promise, which came after both.
    monkeypatch.setattr(progress_circuit_module, "UNSENT_CELL_THRESHOLD", 10**6)
    model = PromisingModel()

    result, kinds = _promising_run(model, max_turns=12)

    assert model.offered == [True] * 12
    assert kinds == ["tools"] * 12
    assert result.stop_reason == "max_turns"


def test_a_note_free_batch_between_two_notes_resets_the_count():
    # Reply 1 searches without promising a cell: no note, so the two notes
    # around it are not in a row.
    model = PromisingModel(contents={1: "检索 RNA 聚合酶相关文献。"})

    _, kinds = _promising_run(model, max_turns=5)

    assert model.offered == [True, True, True, True, False]
    assert kinds == ["tools", "tools", "tools", "tools", "cell"]


def test_a_prose_answer_to_the_tool_less_call_does_not_end_the_turn():
    # finalize_response is a native tool, so it is not on offer either. Prose
    # gets the ordinary no-action nudge, tools come back on the next call, and
    # two more notes in a row earn the next tool-less call.
    model = PromisingModel(untooled="好的，我现在就运行 Python。")

    result, kinds = _promising_run(model, max_turns=7)

    assert model.offered == [True, True, False, True, True, False, True]
    assert kinds == ["tools", "tools", "prose", "tools", "tools", "prose", "tools"]
    assert result.stop_reason == "max_turns" and result.completion is None


def test_a_model_that_never_sends_the_promised_cell_is_stopped_on_the_fifth():
    # SYNC-6 / SCI-16: the same stubborn model with the whole Web budget. The
    # prose answer resets the consecutive count that withholds tools, so the
    # cycle "promise, promise, prose" used to run until max_turns (59 model
    # calls in a replay through the daemon). The count since the last cell
    # stops it on the fifth promise: four notes and two tool-less calls later.
    model = PromisingModel(untooled="好的，我现在就运行 Python。")

    result, kinds = _promising_run(model, max_turns=64)

    assert kinds == ["tools", "tools", "prose", "tools", "tools", "prose", "tools"]
    assert (result.stop_reason, result.progress_reason) == (
        "no_progress",
        "announced_cell_repeat",
    )
    assert result.completion is None


def test_a_cell_between_promises_starts_the_count_again():
    # The cooperative model sends its cell on every tool-less call, so no run
    # of promises reaches five and the turn continues.
    model = PromisingModel()

    result, kinds = _promising_run(model, max_turns=16)

    assert kinds.count("cell") == 5
    assert result.stop_reason == "max_turns"


def test_an_external_policy_is_offered_no_tools_on_the_withheld_call():
    engine = AgentEngine(
        PromisingModel(),
        FakeExecutor(handler=_answer),
        tool_schema_provider=lambda state: (
            {"name": "list_dir", "description": "List.", "input_schema": {}},
        ),
    )
    state = RunState([{"role": "user", "content": "分析 demo.xlsx"}], max_turns=4)

    offered = []
    for _ in range(4):
        offered.append(bool(engine.prepare_tools(state)))
        engine.step(state)

    assert offered == [True, True, False, True]


def test_a_tool_only_note_does_not_hide_an_identical_call_from_the_circuit():
    # The note rides on the recorded result. Read as part of the answer, it
    # made the sixth identical call look like a new result and restarted the
    # same-action count on the very turn the reminder was sent.
    from openai4s.agent.actions import tool_only_streak_note
    from openai4s.agent.progress_circuit import fingerprint_native_results

    plain = [{"role": "tool", "name": "query", "content": _SEARCHED}]
    note = tool_only_streak_note(NativeToolBatch(()), 6)
    noted = [{"role": "tool", "name": "query", "content": _SEARCHED + note}]

    assert note
    assert fingerprint_native_results(noted) == fingerprint_native_results(plain)


def test_a_model_that_ignores_the_note_is_still_stopped_by_the_circuit():
    call = _call(name="write_file", arguments=dict(_WRITE))
    reply = _reply("```python\nprint(1)\n```", tool_calls=[call])

    def written(action, reply, state):
        return ExecutionOutcome(
            history_messages=(
                {"role": "tool", "tool_call_id": "call_1", "content": _WRITTEN},
            )
        )

    engine, model, _, _, _, _, _ = _engine(
        [copy.deepcopy(reply) for _ in range(8)],
        executor=FakeExecutor(handler=written),
        max_turns=8,
    )

    result = engine.run([{"role": "user", "content": "probe the environment"}])

    assert (result.stop_reason, result.progress_reason) == (
        "no_progress",
        "same_action",
    )
    # Three identical calls earn the circuit's reminder, two more its stop.
    assert len(model.calls) == 5
    third = model.calls[3][-1]["content"]
    assert "It was NOT executed" in third
    assert "identical call number 3 in a row to `write_file`" in third


# The 2026-09-24 Web repro: `output` where the schema wants `summary`.
_UNFINISHED = {"output": {"answer": 42}, "completion_bullets": ["Answered it"]}
_FINISHED = {"summary": "The answer is 42.", "completion_bullets": ["Answered it"]}


def _finalize(call_id, arguments):
    return _reply(tool_calls=[_call(call_id, "finalize_response", dict(arguments))])


def _host_finalize(action, reply, state):
    # The real Host contract, not a fake result: the circuit reads the exact
    # tool result the CLI and Web executors produce.
    assert isinstance(action, FinalizeAction)
    return execute_finalize_action(action)


def test_a_repeated_invalid_finalize_stops_with_no_progress_not_max_turns():
    # A mock provider answered every turn of a real Web session with the same
    # schema-invalid finalize_response. The circuit only ever observed native
    # batches, so the engine called the model 64 times -- the whole
    # OPENAI4S_MAX_TURNS budget -- and ended max_turns.
    engine, model, _, executor, _, _, _ = _engine(
        [_finalize(f"call_{index}", _UNFINISHED) for index in range(64)],
        executor=FakeExecutor(handler=_host_finalize),
        max_turns=64,
    )

    result = engine.run([{"role": "user", "content": "What is 6 * 7?"}])

    assert (result.stop_reason, result.progress_reason) == (
        "no_progress",
        "similar_tool_error",
    )
    assert result.completion is None
    assert len(model.calls) == len(executor.calls) == 2
    refusal = model.calls[1][-1]
    assert refusal["role"] == "tool" and refusal["is_error"] is True
    assert refusal["content"] == (
        "[Tool error] finalize_response: invalid arguments: "
        "$.summary: required property is missing; "
        "$.output: unknown property is not allowed"
    )
    # The continuation note names the call the model has to stop repeating.
    assert "Recent tool identifiers: finalize_response." in (
        result.messages[-1]["content"]
    )


def test_a_repeated_malformed_finalize_stops_as_consecutive_malformed():
    call = {
        **_call("call_0", "finalize_response", None),
        "raw_arguments": '{"summary": ',
        "arguments": None,
        "parse_error": "invalid JSON",
    }
    engine, model, _, _, _, _, _ = _engine(
        [_reply(tool_calls=[dict(call, id=f"call_{i}")]) for i in range(8)],
        executor=FakeExecutor(handler=_host_finalize),
        max_turns=8,
    )

    result = engine.run([{"role": "user", "content": "What is 6 * 7?"}])

    assert (result.stop_reason, result.progress_reason) == (
        "no_progress",
        "consecutive_malformed",
    )
    assert len(model.calls) == 2


def test_a_finalize_the_model_repairs_is_submitted_not_stopped():
    # One refusal, then a different mistake, then the fix: a model that
    # changes its call is trying, and a valid finalize is the completion.
    engine, model, _, _, _, _, _ = _engine(
        [
            _finalize("call_0", _UNFINISHED),
            _finalize("call_1", {**_FINISHED, "completion_bullets": []}),
            _finalize("call_2", _FINISHED),
        ],
        executor=FakeExecutor(handler=_host_finalize),
        max_turns=8,
    )

    result = engine.run([{"role": "user", "content": "What is 6 * 7?"}])

    assert (result.stop_reason, result.progress_reason) == ("submitted", None)
    assert result.completion == {
        "output": {"summary": "The answer is 42."},
        "completion_bullets": ["Answered it"],
    }
    assert len(model.calls) == 3
