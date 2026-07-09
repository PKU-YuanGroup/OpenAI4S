"""ReAct tool-surface contracts — openai4s.tools.

The tool layer is pure metadata routed through a HostDispatcher passed in by
the caller: it re-implements no fs/shell/web logic. These lock the registry
shape, the ```tool parse convention, the prompt rendering, the cheap static
prechecks, and the dispatch/observation contract — all without a real kernel
or dispatcher (a fake callable stands in).
"""
from openai4s.host_dispatch import HostDispatcher
from openai4s.tools import (
    MAX_TOOL_CALLS_PER_TURN,
    REGISTRY,
    execute_tool_call,
    parse_tool_calls,
    render_tools_prompt,
    run_tool_calls,
)
from openai4s.tools.bash import precheck_command

_F = "`" * 3  # triple-backtick fence delimiter


def _tool_block(json_body: str) -> str:
    return _F + "tool\n" + json_body + "\n" + _F


# --- registry ---------------------------------------------------------------
def test_registry_is_populated_and_every_tool_resolves_to_a_handler():
    """Every declared Tool has a non-empty name + host_method, and each
    host_method resolves to a real _m_<method> handler on HostDispatcher —
    the drift guard the routing depends on."""
    assert len(REGISTRY) >= 12
    names = [t.name for t in REGISTRY]
    assert len(set(names)) == len(names)  # no duplicate names
    for t in REGISTRY:
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.host_method, str) and t.host_method
        assert hasattr(
            HostDispatcher, f"_m_{t.host_method}"
        ), f"{t.name} -> unresolvable host_method {t.host_method!r}"


# --- parsing model replies --------------------------------------------------
def test_parse_single_valid_tool_block():
    reply = (
        "Let me read it.\n"
        '```tool\n{"name": "read_text_file", "arguments": {"path": "a.md"}}\n```'
    )
    calls, errors = parse_tool_calls(reply)
    assert errors == []
    assert calls == [{"name": "read_text_file", "arguments": {"path": "a.md"}}]


def test_parse_multiple_blocks_preserve_order():
    reply = (
        '```tool\n{"name": "list_dir", "arguments": {}}\n```\n'
        "some prose\n"
        '```tool\n{"name": "read_text_file", "arguments": {"path": "b.md"}}\n```'
    )
    calls, errors = parse_tool_calls(reply)
    assert errors == []
    assert [c["name"] for c in calls] == ["list_dir", "read_text_file"]


def test_parse_malformed_json_is_recorded_not_raised():
    reply = "```tool\n{not: valid json,}\n```"
    calls, errors = parse_tool_calls(reply)  # must not raise
    assert calls == []
    assert errors and "invalid JSON" in errors[0]


def test_parse_unknown_tool_never_raises_and_is_visible():
    reply = '```tool\n{"name": "not_a_real_tool", "arguments": {}}\n```'
    calls, errors = parse_tool_calls(reply)  # must not raise
    # dropped from calls, surfaced in errors so the loop can feed it back
    assert calls == []
    assert any("not_a_real_tool" in e for e in errors)


def test_unclosed_tool_fence_is_an_error_and_never_executes():
    """Streaming/incomplete model output must never become an action."""
    reply = '```tool\n{"name": "list_dir", "arguments": {}}'
    calls, errors = parse_tool_calls(reply)
    assert calls == []
    assert any("unclosed" in e for e in errors)


def test_tool_fence_nested_in_python_cell_is_not_parsed():
    """Fence-token collision guard: a ```tool block quoted INSIDE a ```python
    cell (e.g. the agent writing docs about this very syntax) must NOT be parsed
    as a call — otherwise the embedded command would execute and the real cell
    would be dropped."""
    reply = (
        "Writing the docs:\n"
        + _F
        + "python\n"
        + "content = '''\nExample:\n"
        + _tool_block('{"name": "bash", "arguments": {"command": "rm -rf /tmp/x"}}')
        + "\n'''\nopen('README.md','w').write(content)\n"
        + _F
    )
    calls, errors = parse_tool_calls(reply)
    assert calls == [] and errors == []


def test_tool_fence_nested_in_other_fence_is_not_parsed():
    """A ```tool nested inside a non-python fenced block is that block's content,
    not a call (paired-fence scanning, not a global regex)."""
    reply = (
        "See:\n"
        + _F
        + "text\n"
        + _tool_block('{"name": "list_dir", "arguments": {}}')
        + "\n"
        + _F
    )
    calls, errors = parse_tool_calls(reply)
    assert calls == []


def test_tool_fence_inside_longer_or_tilde_outer_fence_is_not_parsed():
    """CommonMark outer fences also isolate a triple-backtick tool example."""
    inner = _tool_block('{"name": "bash", "arguments": {"command": "echo pwned"}}')
    for outer, info in ((_F + "`", "python"), ("~~~", "text")):
        reply = outer + info + "\nquoted:\n" + inner + "\n" + outer
        calls, errors = parse_tool_calls(reply)
        assert calls == [] and errors == []


def test_run_tool_calls_caps_count_and_bounds_length():
    """A batch beyond MAX_TOOL_CALLS_PER_TURN runs only the cap and reports the
    rest as skipped (not silently dropped); the joined observation stays bounded."""
    seen = []

    def disp(method, args):
        seen.append(method)
        return {"entries": ["x"]}

    calls = [{"name": "list_dir", "arguments": {}}] * (MAX_TOOL_CALLS_PER_TURN + 5)
    obs = run_tool_calls(disp, calls, [])
    assert len(seen) == MAX_TOOL_CALLS_PER_TURN  # extras not executed
    assert "were NOT run" in obs
    assert len(obs) <= 60050  # MAX_TOOL_OBS_CHARS + truncation marker


# --- prompt rendering -------------------------------------------------------
def test_render_tools_prompt_lists_names_and_convention():
    prompt = render_tools_prompt()
    assert isinstance(prompt, str)
    assert "tool" in prompt
    for name in ("read_text_file", "content_search", "bash"):
        assert name in prompt


# --- static prechecks -------------------------------------------------------
def test_bash_precheck_flags_catastrophe_but_passes_benign():
    assert precheck_command("rm -rf /") is not None
    assert precheck_command("ls -la") is None


# --- execution through a fake dispatcher ------------------------------------
def test_execute_read_tool_routes_to_host_method_with_spec_list():
    calls = []

    def disp(method, args):
        calls.append((method, args))
        return {"ok": True, "stdout": "hi"}

    obs, ok = execute_tool_call(
        disp, {"name": "read_text_file", "arguments": {"path": "notes.md"}}
    )
    # routed to the tool's host_method with a single-element [spec] list
    assert calls == [("read_file", [{"path": "notes.md"}])]
    assert ok is True
    assert "hi" in obs
    assert obs.startswith("[Tool: read_text_file]")


def test_execute_dangerous_bash_blocks_before_dispatch():
    calls = []

    def disp(method, args):
        calls.append((method, args))
        return {"ok": True}

    obs, ok = execute_tool_call(
        disp, {"name": "bash", "arguments": {"command": "rm -rf /"}}
    )
    # the static precheck short-circuits — the dispatcher is never invoked
    assert calls == []
    assert ok is False
    assert "precheck" in obs


def test_execute_reports_error_only_result_as_not_ok():
    def disp(method, args):
        return {"error": "boom"}

    obs, ok = execute_tool_call(disp, {"name": "list_dir", "arguments": {}})
    assert ok is False
    assert "boom" in obs
