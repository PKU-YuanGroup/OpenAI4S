"""Provider-wire placement of system messages.

Anthropic and Gemini take the initial policy as a separate top-level field, and
both adapters used to hoist *every* system message into it regardless of
position. Compaction emits its summary as a system message in the middle of the
timeline, so every compaction reframed a transient "here is what happened
earlier" as durable policy — and on Anthropic mutated the top-level `system`,
which is also the prompt-cache prefix, invalidating the cache exactly when the
context had just grown expensive enough to need it.

(_body()'s strict parsing is covered against the real handler in
tests/test_gateway.py::test_body_rejects_unparseable_json_with_an_explicit_4xx.)
"""

# --------------------------------------------------------------------------
# system-message placement on the provider wires
# --------------------------------------------------------------------------


def test_only_leading_system_messages_become_initial_policy():
    """A mid-timeline system message is timeline content, not policy.

    Anthropic and Gemini take the initial policy as a separate top-level field,
    and both adapters used to hoist *every* system message into it regardless
    of position. Compaction emits its summary as a system message in the middle
    of the timeline, so every compaction (a) reframed a transient "here is what
    happened earlier" as durable policy, and (b) on Anthropic mutated the
    top-level `system` — which is also the prompt-cache prefix, so the cache was
    invalidated exactly when the context had just grown expensive.
    """
    from openai4s.llm.messages import _anthropic_messages, _gemini_contents

    messages = [
        {"role": "system", "content": "POLICY"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "work"},
        {"role": "system", "content": "SUMMARY", "compaction_handoff": True},
        {"role": "assistant", "content": "RECENT"},
    ]

    system_txt, conv = _anthropic_messages(messages)
    assert system_txt == "POLICY"
    assert "SUMMARY" not in system_txt
    assert any("SUMMARY" in str(m) for m in conv)

    gemini_system, contents = _gemini_contents(messages)
    assert gemini_system == "POLICY"
    assert any("SUMMARY" in str(c) for c in contents)


def test_consecutive_leading_system_messages_all_become_policy():
    """Splitting policy across several system messages is legitimate and must
    keep working — position, not count, is the rule."""
    from openai4s.llm.messages import _anthropic_messages

    system_txt, conv = _anthropic_messages(
        [
            {"role": "system", "content": "POLICY-A"},
            {"role": "system", "content": "POLICY-B"},
            {"role": "user", "content": "TASK"},
        ]
    )
    assert "POLICY-A" in system_txt and "POLICY-B" in system_txt
    assert len(conv) == 1


def test_a_mid_timeline_system_message_is_marked_as_such():
    """Rendered as a user turn, so it must say what it is — otherwise the model
    reads a system note as the user speaking."""
    from openai4s.llm.messages import _anthropic_messages

    _, conv = _anthropic_messages(
        [
            {"role": "system", "content": "POLICY"},
            {"role": "user", "content": "TASK"},
            {"role": "system", "content": "NOTE"},
        ]
    )
    assert conv[-1]["content"] == "[system] NOTE"


def _stopped_turn_history():
    from openai4s.agent.recovery import recovery_message

    call = {
        "id": "c1",
        "wire_id": "toolu_1",
        "name": "list_dir",
        "arguments": {},
        "raw_arguments": "{}",
    }
    return [
        {"role": "system", "content": "POLICY"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "wire_id": "toolu_1",
            "name": "list_dir",
            "content": "ok",
        },
        recovery_message("no_progress", tool_names=["list_dir"]),
        {"role": "user", "content": "continue"},
    ]


def test_a_system_note_after_tool_results_never_splits_the_anthropic_tool_pair():
    """The stopped-turn recovery note is recorded right after a tool batch.
    Emitted before the pending results were flushed, it sat between the
    assistant's tool_use and its tool_result -- a request Anthropic rejects,
    from durable history, so every later turn of the session failed too."""
    from openai4s.llm.messages import _anthropic_messages

    _, conv = _anthropic_messages(_stopped_turn_history())
    index = next(i for i, m in enumerate(conv) if m["role"] == "assistant")
    assert conv[index + 1]["content"][0]["type"] == "tool_result"
    assert conv[index + 2]["content"].startswith("[system] [Stopped turn recovery]")
    assert conv[index + 3]["content"] == "continue"


def test_a_system_note_after_tool_results_never_splits_the_gemini_call_pair():
    from openai4s.llm.messages import _gemini_contents

    _, contents = _gemini_contents(_stopped_turn_history())
    index = next(i for i, m in enumerate(contents) if m["role"] == "model")
    assert "functionResponse" in contents[index + 1]["parts"][0]
    assert contents[index + 2]["parts"][0]["text"].startswith("[system] ")
    assert contents[index + 3]["parts"][0]["text"] == "continue"


def test_a_trailing_system_note_after_tool_results_is_still_delivered():
    from openai4s.llm.messages import _anthropic_messages, _gemini_contents

    history = _stopped_turn_history()[:-1]
    _, conv = _anthropic_messages(history)
    assert conv[-2]["content"][0]["type"] == "tool_result"
    assert conv[-1]["content"].startswith("[system] ")
    _, contents = _gemini_contents(history)
    assert "functionResponse" in contents[-2]["parts"][0]
    assert contents[-1]["parts"][0]["text"].startswith("[system] ")
