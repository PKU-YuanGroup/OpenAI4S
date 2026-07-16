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
