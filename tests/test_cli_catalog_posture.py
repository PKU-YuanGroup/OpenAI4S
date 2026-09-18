"""The headless CLI offers the model only tools its posture could approve.

`define_dynamic_tool` (and the other approval-required lifecycle tools) stayed
in every CLI tool declaration although, with no approval channel and the
shipped unattended deny -- or under the Auto Mode Guardian, which never
approves a dangerous action -- every call was refused. Models reached for it
first on "define a reusable function" tasks and lost a turn each time. The
projection is now posture-aware; the permission gate is unchanged, so a hidden
tool that is called anyway is still refused and audited.
"""

from __future__ import annotations

import json

from openai4s.config import Config, LLMConfig

#: Approval-required tools whose default decision is `ask`, visible in every
#: CLI projection because their tool groups are always active.
_ASK_BY_DEFAULT = {
    "define_dynamic_tool",
    "promote_dynamic_tool",
    "activate_dynamic_tool_version",
    "rollback_dynamic_tool_version",
}


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )


def _declared_tools(tmp_path, monkeypatch, *, prepare=None) -> set[str]:
    """Run a real CLI Agent for one finalize-only turn and return the tool
    names the provider was actually offered."""

    from openai4s.agent import loop as loop_mod

    seen: list[set[str]] = []

    def finalize_chat(messages, cfg, **kwargs):
        del messages, cfg
        seen.append({tool.name for tool in kwargs["tools"]})
        arguments = {
            "summary": "Answered without running anything.",
            "completion_bullets": ["Answered the question"],
        }
        call = {
            "id": "final",
            "wire_id": "wire-final",
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

    monkeypatch.setattr(loop_mod, "chat", finalize_chat)
    agent = loop_mod.Agent(
        cfg=_cfg(tmp_path),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
    )
    cleanup = prepare(agent) if prepare is not None else None
    try:
        result = agent.run("Define a reusable function that computes n factorial.")
    finally:
        if callable(cleanup):
            cleanup()
    assert result["stop_reason"] == "submitted", result
    assert seen, "the provider was never called"
    return seen[0]


def test_default_headless_posture_hides_tools_nothing_could_approve(
    tmp_path, monkeypatch
):
    names = _declared_tools(tmp_path, monkeypatch)

    assert names.isdisjoint(_ASK_BY_DEFAULT), sorted(names & _ASK_BY_DEFAULT)
    # Tools the default rules already allow, and tools that need no approval,
    # are untouched.
    assert {"list_dir", "list_skills", "list_dynamic_tools"} <= names
    assert "finalize_response" in names


def test_a_standing_allow_rule_keeps_the_tool(tmp_path, monkeypatch):
    def allow(agent):
        agent.dispatcher.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool="dynamic_tool_define",
            pattern="*",
            decision="allow",
        )

    names = _declared_tools(tmp_path, monkeypatch, prepare=allow)
    assert "define_dynamic_tool" in names
    assert "promote_dynamic_tool" not in names


def test_explicit_unattended_allow_keeps_every_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    names = _declared_tools(tmp_path, monkeypatch)
    assert _ASK_BY_DEFAULT <= names


def test_an_attached_approval_channel_keeps_every_tool(tmp_path, monkeypatch):
    """The Web path: a human can answer, so nothing is hidden."""

    from openai4s.permissions import broker

    def attach(agent):
        root = str(agent.frame_id)
        broker().register_channel(root, lambda _event: None)
        return lambda: broker().unregister_channel(root)

    names = _declared_tools(tmp_path, monkeypatch, prepare=attach)
    assert _ASK_BY_DEFAULT <= names


def test_the_guardian_hides_what_it_never_approves(tmp_path, monkeypatch):
    """Under --auto the Guardian adjudicates, and the unattended env allow is
    not consulted -- so it cannot keep a dangerous tool visible either."""

    from openai4s.agent import loop as loop_mod

    for key, value in loop_mod.AUTO_RUN_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    names = _declared_tools(tmp_path, monkeypatch)
    assert names.isdisjoint(_ASK_BY_DEFAULT)
    assert "list_dir" in names


def test_a_deny_for_every_target_hides_the_tool(tmp_path, monkeypatch):
    def deny(agent):
        agent.dispatcher.store.set_permission_rule(
            scope="global", scope_id="", tool="list_dir", pattern="*", decision="deny"
        )

    names = _declared_tools(tmp_path, monkeypatch, prepare=deny)
    assert "list_dir" not in names


def test_a_hidden_tool_called_anyway_is_still_refused_and_audited(tmp_path):
    """Defence in depth: hiding is a projection, never the control."""

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.tools.registry import execute_tool_call

    workspace = tmp_path / "ws"
    workspace.mkdir()
    dispatcher = build_dispatcher(_cfg(tmp_path), workspace=workspace)
    dispatcher.frame_id = dispatcher.store.new_frame(kind="turn")
    catalog = dispatcher.tool_catalog()
    spec = {
        "name": "noop",
        "description": "noop",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "implementation": "def execute(args):\n    return {}\n",
        "smoke_args": {},
        "ttl_s": 60,
    }

    output, ok = execute_tool_call(
        dispatcher, {"name": "define_dynamic_tool", "arguments": spec}, catalog
    )

    assert ok is False
    assert "Permission denied" in output
    requests = dispatcher.store.list_permission_requests(
        root_frame_id=dispatcher.frame_id
    )
    assert [(row["tool"], row["state"]) for row in requests] == [
        ("dynamic_tool_define", "denied")
    ]


#: The full default-posture effect, as the tests README pair documents it:
#: hidden from the first turn (always-active groups) ...
_HIDDEN_FROM_FIRST_TURN = _ASK_BY_DEFAULT | {"web_download", "rollback_skill_version"}
#: ... and hidden once their progressive groups activate.
_HIDDEN_ONCE_ACTIVE = {
    "restore_artifact_version",
    "exec_background",
    "read_mcp_resource",
    "get_mcp_prompt",
    "request_network_access",
    "stage_model_asset",
    "register_remote_capability",
    "compute_submit",
}


def test_the_documented_hidden_set_is_the_whole_default_posture_effect(
    tmp_path, monkeypatch
):
    """The projection is a general rule, so its reach is easy to understate
    (the first report named only the four dynamic-tool tools). Pin the whole
    default-posture hidden set against the real projection, and the doc rows
    that tell a release reader what disappears."""

    from pathlib import Path

    from openai4s.agent import loop as loop_mod

    first_turn = _declared_tools(tmp_path / "first", monkeypatch)

    agent = loop_mod.Agent(
        cfg=_cfg(tmp_path / "all"),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
    )
    catalog = agent.dispatcher.tool_catalog()
    groups = catalog.group_metadata()
    always = {name for group in groups if group["always"] for name in group["tools"]}
    catalog.activate_groups(*(str(group["id"]) for group in groups))
    offered = {spec.name for spec in agent._model_tool_specs(catalog, [])}
    hidden = {tool.name for tool in catalog.tools()} - offered

    assert (always - first_turn) == _HIDDEN_FROM_FIRST_TURN
    assert hidden == _HIDDEN_FROM_FIRST_TURN | _HIDDEN_ONCE_ACTIVE
    # Every hidden tool is approval-required: the projection never drops a
    # tool the gate would have run without asking.
    assert all(catalog.get(name).requires_approval for name in hidden)

    tests_dir = Path(__file__).resolve().parent
    for readme in ("README.md", "README_zh.md"):
        row = next(
            line
            for line in (tests_dir / readme).read_text(encoding="utf-8").splitlines()
            if line.startswith("| [`test_cli_catalog_posture.py`]")
        )
        missing = sorted(
            name
            for name in (_HIDDEN_FROM_FIRST_TURN | _HIDDEN_ONCE_ACTIVE)
            - _ASK_BY_DEFAULT
            if f"`{name}`" not in row
        )
        assert not missing, (readme, missing)


def _tool_call(name: str, arguments: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "wire_id": f"wire-{call_id}",
        "name": name,
        "ordinal": 0,
        "raw_arguments": json.dumps(arguments),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _search_then_finalize(tmp_path, monkeypatch, query: str):
    """A real CLI Agent whose model searches capabilities, then finalizes.

    Returns the parsed `search_capabilities` result and the tool names the
    provider was offered on the turn that read it."""

    from openai4s.agent import loop as loop_mod

    offered: list[set[str]] = []
    results: list[dict] = []

    def chat(messages, cfg, **kwargs):
        del cfg
        offered.append({tool.name for tool in kwargs["tools"]})
        if len(offered) == 1:
            call = _tool_call("search_capabilities", {"query": query}, "search")
        else:
            prefix = "[Tool: search_capabilities]\n"
            content = next(
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "tool"
            )
            assert content.startswith(prefix), content
            results.append(json.loads(content[len(prefix) :]))
            call = _tool_call(
                "finalize_response",
                {"summary": "Searched.", "completion_bullets": ["Searched tools"]},
                "final",
            )
        return {
            "content": "",
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [call],
            },
        }

    monkeypatch.setattr(loop_mod, "chat", chat)
    agent = loop_mod.Agent(
        cfg=Config(
            data_dir=tmp_path / "data",
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=3,
        ),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
    )
    result = agent.run("Compute 17*23 in a Python cell and report it.")
    assert result["stop_reason"] == "submitted", result
    assert len(offered) == 2 and len(results) == 1
    return results[0], offered[1]


def test_search_capabilities_never_advertises_a_tool_the_projection_hides(
    tmp_path, monkeypatch
):
    """The live failure: a headless model searched, read `exec_background`
    under `visible_tools`, called it, and was refused -- a tool the provider
    `tools=` list had never offered."""

    result, offered = _search_then_finalize(
        tmp_path, monkeypatch, "run python cell computation"
    )

    assert set(result["visible_tools"]) <= offered, sorted(
        set(result["visible_tools"]) - offered
    )
    for group in result["matched_groups"]:
        assert set(group["tools"]) <= offered, (group["id"], group["tools"])
    unavailable = {row["name"]: row["reason"] for row in result["unavailable_tools"]}
    assert unavailable.get("exec_background") == "approval_unreachable"
    assert set(unavailable).isdisjoint(offered)
    # The group still matched and activated: hiding is a projection, the
    # model is told the tool exists but cannot run under this posture.
    assert "background" in {group["id"] for group in result["matched_groups"]}
    assert {"exec_list", "exec_peek"} <= set(result["visible_tools"])


def test_search_capabilities_lists_everything_when_the_posture_can_approve(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    result, offered = _search_then_finalize(
        tmp_path, monkeypatch, "run python cell computation"
    )

    assert "exec_background" in result["visible_tools"]
    assert "exec_background" in offered
    assert "unavailable_tools" not in result


def test_an_unfiltered_catalog_search_is_unchanged():
    """The Web root never sets the hook: its result carries no new key and
    lists every tool of every active group, exactly as before."""

    from openai4s.tools.catalog import SessionToolCatalog

    plain = SessionToolCatalog().search_capabilities("background")
    assert "unavailable_tools" not in plain
    assert "exec_background" in plain["visible_tools"]
    everything = SessionToolCatalog().search_capabilities(
        "background", offered=lambda _tool: True
    )
    assert everything == plain
