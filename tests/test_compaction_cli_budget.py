"""CLI CompactionPolicy must compact against the model window, not 262144.

The Web session and delegated children already pass a context-budget provider
and (for Web) the live tool catalogue. The CLI Agent loop was the third call
site and omitted both, so a 128k model would only compact after the provider
had already rejected the request.
"""

import json
from types import SimpleNamespace

import openai4s.agent.loop as loop_mod
import openai4s.agent.runtime as runtime
import openai4s.llm as llm_mod
from openai4s.agent import Agent


class ScriptedLLM:
    """Returns queued replies in order; each call pops one."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def __call__(self, messages, cfg, **kw):
        self.calls.append(messages)
        content = self._replies.pop(0) if self._replies else ""
        return {
            "content": content,
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }


def _finalize_chat(messages, cfg, **kwargs):
    del messages, cfg, kwargs
    arguments = {
        "summary": "The requested explanation was completed.",
        "completion_bullets": ["Completed the requested explanation"],
    }
    call = {
        "id": "final-cli",
        "wire_id": "wire-final-cli",
        "name": "finalize_response",
        "ordinal": 0,
        "raw_arguments": json.dumps(arguments),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }
    return {
        "content": "",
        "tool_calls": [call],
        "assistant_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [call],
        },
    }


# ~13k chars / ~3.2k estimated tokens each, under DEFAULT_LARGE_OUTPUT_CHARS so
# externalization cannot shrink the estimate before should_compact sees it.
# Each turn's prose differs by more than the no-progress circuit's
# near-duplicate ratio (letters, not digits, which it normalizes away), so
# ten long replies accumulate context instead of tripping ``no_progress``.
_LONG_REPLIES = [
    f"token-budget-{letter} {letter * 3} " * 800 for letter in "abcdefghij"
]


def _patch_capabilities(monkeypatch, impl):
    """Patch every get_model_capabilities binding the CLI budget path can see.

    ``loop.py`` binds the name at import for the tool-calling probe.
    ``_child_context_budget`` re-imports it from ``openai4s.llm`` on each call.
    """
    monkeypatch.setattr(loop_mod, "get_model_capabilities", impl)
    monkeypatch.setattr(llm_mod, "get_model_capabilities", impl)


def _count_compact(monkeypatch):
    calls = []

    def fake_compact(messages, cfg, **kwargs):
        calls.append(list(messages))
        return list(messages)[:3]

    monkeypatch.setattr(runtime, "compact", fake_compact)
    return calls


def _schema_name(schema):
    name = getattr(schema, "name", None)
    if name:
        return name
    if not isinstance(schema, dict):
        return None
    function = schema.get("function")
    if isinstance(function, dict) and function.get("name"):
        return function["name"]
    return schema.get("name")


def test_cli_compacts_against_model_usable_window(monkeypatch):
    _patch_capabilities(
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            usable_context_tokens=20_000,
            context_window_tokens=20_000,
            tool_calling=True,
        ),
    )
    compact_calls = _count_compact(monkeypatch)
    monkeypatch.setattr(loop_mod, "chat", ScriptedLLM(list(_LONG_REPLIES)))

    result = Agent(use_skills=False, allow_delegate=False, max_turns=10).run(
        "accumulate enough context to force compaction"
    )

    assert result["stop_reason"] == "max_turns"
    assert len(compact_calls) >= 1


def test_cli_capability_lookup_failure_falls_back_without_compacting(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("capability lookup failed")

    _patch_capabilities(monkeypatch, boom)
    compact_calls = _count_compact(monkeypatch)
    monkeypatch.setattr(loop_mod, "chat", ScriptedLLM(list(_LONG_REPLIES)))

    result = Agent(use_skills=False, allow_delegate=False, max_turns=10).run(
        "accumulate enough context that a 20k window would compact"
    )

    assert result["stop_reason"] == "max_turns"
    assert compact_calls == []


def test_cli_compaction_counts_finalize_response_schema(monkeypatch):
    seen_schemas = []
    original = runtime.estimate_context

    def capturing(messages, tool_schemas=(), *args, **kwargs):
        seen_schemas.append(tuple(tool_schemas))
        return original(messages, tool_schemas, *args, **kwargs)

    monkeypatch.setattr(runtime, "estimate_context", capturing)
    monkeypatch.setattr(loop_mod, "chat", _finalize_chat)

    result = Agent(use_skills=False, allow_delegate=False, max_turns=1).run(
        "submit a short answer"
    )

    assert result["stop_reason"] == "submitted"
    assert any(
        schemas
        and any(_schema_name(schema) == "finalize_response" for schema in schemas)
        for schemas in seen_schemas
    )
