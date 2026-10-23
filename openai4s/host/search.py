"""What web search does when Doubao's quota is spent.

Doubao answers first whenever an Agent Plan key is configured, and a rejected
key stays a hard error. A spent quota or rate limit is different: the user may
fall back to one of their switched-on backup engines, but it is their call, not
the agent's. They make it on the ordinary approval card, which here offers the
engines as choices; the one they pick is also the pattern of a remembered rule.

A remembered rule is a decision already made, so it applies with nobody
watching. A new prompt is only shown when a person can answer it: a delegated
child, a run with no UI channel, and an active Auto Mode run all get the quota
error instead of a silent switch to another provider.
"""

from __future__ import annotations

from typing import Any, Sequence

from openai4s import search_settings

#: The permission tool name the rules and the approval card are filed under.
FALLBACK_PERMISSION = "web_search_fallback"
ENGINE_LABELS = {
    "tavily": "Tavily",
    "you": "You.com",
    "builtin": "DuckDuckGo / Bing / Mojeek",
}


def confirm_quota_fallback(
    *,
    store,
    frame_id: str | None,
    engines: Sequence[str],
    query: str,
    delegated: bool,
    action_context: dict[str, Any],
    permission_broker=None,
) -> str | None:
    """The backup engine to use after a Doubao quota error, or None for none.

    ``engines`` is the switched-on chain in the user's order; Tavily without a
    key is left out because it cannot answer. Engines are checked against
    remembered rules first (an ``allow`` picks that engine, a ``deny`` removes
    it from the offer); only then, and only with a person present, is the user
    asked.
    """

    candidates = [
        engine
        for engine in engines
        if engine != "tavily" or search_settings.has_key(store, engine)
    ]
    if not candidates:
        return None
    scope = store.resolve_frame_scope(frame_id)
    root = scope.get("root_frame_id")
    project = scope.get("project_id") or "default"
    offered: list[str] = []
    for engine in candidates:
        decision = store.resolve_permission(
            root_frame_id=root,
            project_id=project,
            tool=FALLBACK_PERMISSION,
            pattern_input=engine,
        )
        if decision == "allow":
            return engine
        if decision != "deny":
            offered.append(engine)
    if not offered or delegated:
        return None
    if permission_broker is None:
        from openai4s.permissions import broker

        permission_broker = broker()
    if not permission_broker.human_reachable(
        store=store,
        frame_id=frame_id,
        action_group_id=action_context.get("action_group_id"),
    ):
        return None
    gate = permission_broker.gate(
        store=store,
        frame_id=frame_id,
        method=FALLBACK_PERMISSION,
        target=offered[0],
        view=(FALLBACK_PERMISSION, "", {"query": query, "engines": offered}),
        action_group_id=action_context.get("action_group_id"),
        action_id=action_context.get("action_id"),
        tool_call_id=action_context.get("tool_call_id"),
        # A person decides this, never the Guardian: the point of the prompt is
        # that switching provider is the user's choice.
        approvals_reviewer="user",
        choices=[{"id": e, "label": ENGINE_LABELS[e]} for e in offered],
    )
    if not gate.get("allow"):
        return None
    choice = gate.get("choice") or offered[0]
    return choice if choice in offered else None
