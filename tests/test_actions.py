"""Action-parsing core contracts — openai4s.agent.actions.

Both outer loops (agent/loop.py Agent.run and server/gateway.py
SessionRunner._loop) parse a reply through this single module, so these tests
lock the language whitelist, the one-cell-per-step document-order rule, and
the fence-collision guarantees the dual loop depends on.
"""

import ast
from typing import get_args

import pytest

from openai4s.agent.actions import (
    INCOMPLETE_CELL_NUDGE,
    MULTI_CELL_NOTE,
    NO_CODE_NUDGE,
    Action,
    CodeCell,
    FinalizeAction,
    NativeToolBatch,
    NativeToolCall,
    count_code_blocks,
    extract_action,
    has_incomplete_code_block,
    is_completion_only_cell,
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
    assert has_incomplete_code_block(f"{_F}python\nx = 1\n") is True
    assert has_incomplete_code_block(f"{_F}r\nx <- 1\n") is True
    assert has_incomplete_code_block(f"{_F}json\n{{}}\n") is False
    assert "NOTHING" in INCOMPLETE_CELL_NUDGE


def test_nested_tool_example_stays_inside_the_cell():
    body = 'doc = """\n' + _cell("tool", '{"name": "x"}') + '\n"""'
    action = extract_action(_cell("python", body))
    assert action is not None and action.language == "python"
    assert '{"name": "x"}' in action.code


def _lines(*lines: str) -> str:
    return "".join(line + "\n" for line in lines)


_DQ3 = '"' * 3
_SQ3 = "'" * 3

# SCI-25: the twelve-line shape of the cell that was cut at line 4. A report
# template in a triple-quoted string holds a Markdown code block.
_REPORT_CELL_BODY = _lines(
    "seq = 'TTCCPSIVARSNFNVCRLPGTPEALCATYTGCIIIPGATCPGDYAN'",
    "report = f" + _DQ3 + "# Report",
    "The sequence in FASTA format:",
    "",
    _F,
    "{seq}",
    _F,
    "",
    "Done.",
    _DQ3,
    "open('report.md', 'w').write(report)",
    "print(len(report))",
)


def test_a_bare_fence_in_a_triple_quoted_string_does_not_end_the_cell():
    """SCI-25: the cell ran only up to the bare fence in its report template.

    The truncated half failed with an unterminated-string SyntaxError, twice
    in a row, and the rest of the reply was miscounted as a second cell.
    """
    reply = f"I will write the report.\n{_F}python\n{_REPORT_CELL_BODY}{_F}\nDone."
    action = extract_action(reply)
    assert action == CodeCell("python", _REPORT_CELL_BODY)
    ast.parse(action.code)
    assert count_code_blocks(reply) == 1
    assert has_incomplete_code_block(reply) is False
    assert route_action(reply, []) == action


@pytest.mark.parametrize("info", ["python", "py", ""])
@pytest.mark.parametrize("quote", [_DQ3, _SQ3])
def test_every_python_fence_form_keeps_a_fenced_string_whole(info, quote):
    body = _lines("doc = " + quote, _F, "plain block", _F, quote, "print(doc)")
    assert extract_action(f"{_F}{info}\n{body}{_F}") == CodeCell("python", body)


def test_a_string_that_closes_on_a_fence_line_still_ends():
    # The inner block's last fence carries the closing quotes on the same line.
    body = _lines("doc = " + _DQ3, _F, "code", _F + _DQ3, "print(doc)")
    assert extract_action(f"{_F}python\n{body}{_F}\ntail") == CodeCell("python", body)


def test_quotes_in_comments_and_one_line_strings_do_not_open_a_string():
    """A stray quote must not swallow the real closing fence."""
    body = _lines(
        '# it\'s a "comment" with ' + _SQ3 + " in it",
        "a = '" + _DQ3 + "'",
        'b = "it\'s"',
        "c = 'unterminated",
        "d = r'\\''",
    )
    reply = f"{_F}python\n{body}{_F}\nprose\n{_F}python\nsecond = 1\n{_F}"
    assert extract_action(reply) == CodeCell("python", body)
    assert count_code_blocks(reply) == 2


def test_an_r_string_may_hold_a_fenced_block_too():
    body = _lines(
        "# don't open a string here",
        "md <- 'intro",
        _F,
        "x <- 1",
        _F,
        "'",
        'writeLines(md, "report.md")',
    )
    assert extract_action(f"{_F}r\n{body}{_F}") == CodeCell("r", body)


def test_r_raw_strings_and_backtick_names_are_read_as_r():
    body = _lines(
        'pat <- r"(a "quoted" \\d+)"',
        "df$`it's` <- 1",
        'md <- r"---[',
        _F,
        "block",
        _F,
        ']---"',
    )
    reply = f"{_F}r\n{body}{_F}\nprose\n{_F}r\ny <- 2\n{_F}"
    assert extract_action(reply) == CodeCell("r", body)
    assert count_code_blocks(reply) == 2


def test_longer_and_tilde_fences_keep_their_meaning():
    """Nested ```` and ~~~ fences: pairing outside strings is unchanged."""
    four = "`" * 4
    # A four-backtick cell holds three-backtick lines as plain content.
    body = _lines("text = 1", _F, "not code", _F)
    assert extract_action(f"{four}python\n{body}{four}") == CodeCell("python", body)
    # A tilde block inside a cell's string never touched the backtick pairing.
    body = _lines("doc = " + _DQ3, "~~~", "block", "~~~", _DQ3)
    assert extract_action(f"{_F}python\n{body}{_F}") == CodeCell("python", body)
    # A four-backtick line inside a string is content, like a three-backtick one.
    body = _lines("doc = " + _DQ3, four, "block", four, _DQ3)
    assert extract_action(f"{_F}python\n{body}{_F}") == CodeCell("python", body)
    # A tilde-fenced block is still never an executable cell.
    assert extract_action("~~~python\nx = 1\n~~~") is None
    # Outside a string a bare fence still ends the cell.
    reply = f"{_F}python\nx = 1\n{_F}\nprose\n{_F}\ny = 2\n{_F}"
    assert extract_action(reply) == CodeCell("python", "x = 1\n")
    assert count_code_blocks(reply) == 2


def test_a_cell_cut_off_inside_its_string_falls_back_to_the_plain_reading():
    """The string rule only applies when it yields a complete cell.

    A reply that ends inside the string (or a quote the lexer misjudged) is
    read exactly as before, so the rule can never leave a cell unrunnable
    that used to run.
    """
    cut = f"{_F}python\n" + _lines("doc = " + _DQ3, _F, "block", _F) + "still inside"
    assert extract_action(cut) == CodeCell("python", "doc = " + _DQ3 + "\n")
    # An earlier complete cell keeps its string-aware reading.
    reply = f"{_F}python\n{_REPORT_CELL_BODY}{_F}\nprose\n{cut}"
    assert extract_action(reply) == CodeCell("python", _REPORT_CELL_BODY)


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


def test_completion_only_cell_detection_is_strict_and_python_only():
    direct = """# protocol-only completion
host.submit_output(
    {"answer": answer},
    ["已完成分析"],
)
"""
    assert is_completion_only_cell(direct)
    assert is_completion_only_cell(CodeCell("python", direct))

    assert not is_completion_only_cell(
        "result = compute()\nhost.submit_output(result, ['Computed it'])"
    )
    assert not is_completion_only_cell(
        "host.submit_output(build_result(), ['Computed it'])"
    )
    assert not is_completion_only_cell(
        "host.submit_output({'rows': [f(x) for x in xs]}, ['Computed it'])"
    )
    assert not is_completion_only_cell("submit_output({}, ['Computed it'])")
    assert not is_completion_only_cell(direct, "r")


def test_action_type_covers_cells_native_batches_and_finalization():
    assert set(get_args(Action)) == {CodeCell, NativeToolBatch, FinalizeAction}


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


def _run_cell_call(call_id="call_1", **arguments):
    return NativeToolCall(
        id=call_id,
        wire_id=call_id,
        name="run_cell",
        ordinal=0,
        raw_arguments="{}",
        arguments=arguments,
    )


def test_a_sole_run_cell_call_is_the_cell_only_where_it_was_offered():
    call = _run_cell_call(language=" R ", code="a <- 1")

    offered = route_action("prose", [call], run_cell_offered=True)
    stray = route_action("prose", [call])

    assert offered == CodeCell("r", "a <- 1") and offered.tool_call is call
    assert isinstance(stray, NativeToolBatch)
    assert stray.calls[0].parse_error.startswith("unknown tool: 'run_cell'")
    # A fenced cell on the offered call routes exactly as on any other call.
    reply = _cell("python", "b = 2")
    assert route_action(reply, run_cell_offered=True) == CodeCell("python", "b = 2\n")
    assert route_action(reply, run_cell_offered=True).tool_call is None


def test_run_cell_beside_another_call_or_with_bad_arguments_runs_nothing():
    finalize = NativeToolCall("f", "f", "finalize_response", 1, "{}", arguments={})
    cases = [
        ([_run_cell_call(language="python", code="x = 1"), finalize], "only one call"),
        ([_run_cell_call(language="julia", code="x = 1")], "language must be"),
        ([_run_cell_call(language="python", code="")], "code must be"),
        ([_run_cell_call(language="python")], "code must be"),
    ]
    for calls, problem in cases:
        action = route_action("", calls, run_cell_offered=True)
        assert isinstance(action, NativeToolBatch)
        assert all(problem in call.parse_error for call in action.calls)
        assert all("Nothing in this reply ran" in c.parse_error for c in action.calls)


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
    assert action.calls[0].parse_error == ("Expecting value: line 1 column 9 (char 8)")
    assert action.calls[0].provider_meta == malformed["provider_meta"]
