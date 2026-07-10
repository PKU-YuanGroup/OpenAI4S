"""Action-parsing core contracts — openai4s.agent.actions.

Both outer loops (agent/loop.py Agent.run and server/gateway.py
SessionRunner._loop) parse a reply through this single module, so these tests
lock the language whitelist, the one-cell-per-step document-order rule, and
the fence-collision guarantees the dual loop depends on.
"""
from typing import get_args

from openai4s.agent.actions import (
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    Action,
    CodeCell,
    NativeToolBatch,
    NativeToolCall,
    count_code_blocks,
    extract_action,
    route_action,
)

_F = "`" * 3


def _cell(info: str, body: str) -> str:
    return f"{_F}{info}\n{body}\n{_F}"


def test_python_infos_and_bare_fence_mean_python():
    for info in ("python", "py", ""):
        action = extract_action(f"prose\n{_cell(info, 'x = 1')}")
        assert action == CodeCell("python", "x = 1\n")


def test_r_fence_is_first_class_and_case_insensitive():
    for info in ("r", "R"):
        action = extract_action(_cell(info, "x <- 1"))
        assert action == CodeCell("r", "x <- 1\n")


def test_document_order_decides_between_languages():
    reply = _cell("r", "a <- 1") + "\nprose\n" + _cell("python", "b = 2")
    assert extract_action(reply).language == "r"
    reply = _cell("python", "b = 2") + "\nprose\n" + _cell("r", "a <- 1")
    assert extract_action(reply).language == "python"


def test_non_action_fences_are_ignored():
    assert extract_action(_cell("json", '{"a": 1}')) is None
    assert extract_action(_cell("tool", '{"name": "list_dir"}')) is None
    assert extract_action("just prose") is None


def test_unclosed_fence_is_never_executable():
    assert extract_action(f"{_F}python\nx = 1\n") is None
    assert extract_action(f"{_F}r\nx <- 1\n") is None


def test_nested_tool_example_stays_inside_the_cell():
    body = 'doc = """\n' + _cell("tool", '{"name": "x"}') + '\n"""'
    action = extract_action(_cell("python", body))
    assert action is not None and action.language == "python"
    assert '{"name": "x"}' in action.code


def test_count_code_blocks_spans_both_languages():
    reply = (
        _cell("python", "a = 1")
        + "\n"
        + _cell("r", "b <- 2")
        + "\n"
        + _cell("json", "{}")
    )
    assert count_code_blocks(reply) == 2
    assert count_code_blocks("prose only") == 0


def test_shared_texts_mention_both_channels():
    assert "```python" in NO_CODE_NUDGE and "```r" in NO_CODE_NUDGE
    assert "only the FIRST" in MULTI_CELL_NOTE


def test_action_type_covers_cells_and_native_batches():
    assert set(get_args(Action)) == {CodeCell, NativeToolBatch}


def test_structured_native_call_wins_over_code_cell():
    call = NativeToolCall(
        id="call_1",
        wire_id="call_1",
        name="request_network_access",
        ordinal=0,
        raw_arguments='{"domain":"example.org"}',
        arguments={"domain": "example.org"},
        provider_meta={"provider": "openai"},
    )

    action = route_action(_cell("python", "raise AssertionError"), [call])

    assert action == NativeToolBatch((call,))
    assert action.calls[0] is call


def test_empty_native_calls_keep_existing_first_cell_rule():
    reply = _cell("r", "a <- 1") + "\n" + _cell("python", "b = 2")

    assert route_action(reply) == CodeCell("r", "a <- 1\n")
    assert route_action(reply, []) == extract_action(reply)
    assert route_action("prose only", ()) is None


def test_native_batch_preserves_order_and_lossless_call_details():
    malformed = {
        "id": "synthetic:gemini:0",
        "wire_id": None,
        "name": "delegate",
        "ordinal": 0,
        "raw_arguments": '{"task":',
        "arguments": None,
        "parse_error": "Expecting value: line 1 column 9 (char 8)",
        "provider_meta": {
            "provider": "gemini",
            "function_call": {"name": "delegate", "args": '{"task":'},
        },
    }
    valid = NativeToolCall(
        id="toolu_2",
        wire_id="toolu_2",
        name="compute",
        ordinal=1,
        raw_arguments='{"gpu":1}',
        arguments={"gpu": 1},
        provider_meta={"provider": "anthropic", "index": 2},
    )

    action = route_action(
        _cell("python", "should_not_run = True"),
        (call for call in [malformed, valid]),
    )

    assert isinstance(action, NativeToolBatch)
    assert action.calls == (NativeToolCall(**malformed), valid)
    assert action.calls[0].raw_arguments == '{"task":'
    assert action.calls[0].wire_id is None
    assert action.calls[0].ordinal == 0
    assert action.calls[0].arguments is None
    assert action.calls[0].parse_error == (
        "Expecting value: line 1 column 9 (char 8)"
    )
    assert action.calls[0].provider_meta == malformed["provider_meta"]
