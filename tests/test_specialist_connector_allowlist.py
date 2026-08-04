"""The connector half of the specialist tri-state allowlist.

The Skill half was armed at `HostDispatcher.set_child_execution_policy` and is
covered by `test_specialist_allowlist_armed.py`. The connector half was not
built at all: `connectors` was stored on the specialist row, inherited through
delegation, narrowed in `_normalize_item`, echoed back by the API — and no
field carried it into the runtime and no setter enforced it. There was nothing
to arm.

Measured before this fix, through the real `HostDispatcher` with a policy whose
`connectors` was `["allowed"]`: `mcp_list` returned both connectors on the
host, `mcp_tools("denied")` returned the denied server's tool list, and the
manager was asked to launch it — a restriction the user set in the UI bought
exactly nothing.

Two rules the tests here keep separate on purpose:

* The tri-state. `None` inherits, `[]` denies everything, a list is exactly
  those. Python spells the middle one false, so `if not allowed:` turns "deny
  everything" into "allow everything" for the one configuration a user chose
  specifically to lock something down.
* Narrowing. A nested child gets the intersection with its parent and can
  never widen — including through the nested path, which narrows against the
  parent *child's* spec rather than against the delegate() call's kwargs, and
  which is a different function from the one the Skill fix touched.

Everything below drives the production path: a real `HostDispatcher` from
`build_dispatcher`, a real `Store`, and the real `child_execution_policy`. The
allowlist is never armed by the test — arming it in the test is the defect that
let the Skill half ship unenforced for days.
"""

from __future__ import annotations

import pytest

from openai4s.agent.delegation import _apply_parent_execution_ceiling, _normalize_item
from openai4s.config import Config, LLMConfig
from openai4s.host.delegation_policy import (
    DelegationPolicyError,
    child_execution_policy,
)
from openai4s.host_dispatch import build_dispatcher


class SpyManager:
    """Stands in for the MCP process manager and records every launch.

    Its only job is to make "the process was started" observable. A denied
    connector must not reach it at all — `zero spawn` is the claim, so an empty
    call log is the assertion.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_tools(self, connector_id, config):
        self.calls.append(("list_tools", connector_id))
        return [{"name": "search"}]

    def call_tool(self, connector_id, config, tool, args):
        self.calls.append(("call_tool", connector_id, tool))
        return {"text": "done"}

    def list_resources(self, connector_id, config, cursor):
        self.calls.append(("list_resources", connector_id))
        return {"resources": []}

    def read_resource(self, connector_id, config, uri):
        self.calls.append(("read_resource", connector_id, uri))
        return {"contents": []}

    def list_prompts(self, connector_id, config, cursor):
        self.calls.append(("list_prompts", connector_id))
        return {"prompts": []}

    def get_prompt(self, connector_id, config, name, arguments):
        self.calls.append(("get_prompt", connector_id, name))
        return {"messages": []}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A real dispatcher over a real store, with only the launcher replaced.

    The manager is patched on `openai4s.mcp_client`, which is where the service
    resolves it at call time — not injected into the service — so the lookup
    under test is the one production uses.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-child", workspace=workspace)
    for connector_id, name in (("allowed", "Allowed"), ("denied", "Denied")):
        dispatcher.store.upsert_connector(
            connector_id=connector_id,
            name=name,
            command=["python", "-c", "pass"],
            enabled=True,
        )
    spy = SpyManager()
    import openai4s.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "manager", lambda: spy)
    return dispatcher, spy


def _apply(dispatcher, spec) -> None:
    """Exactly what the spawn path does: build the policy, hand it over."""

    dispatcher.set_child_execution_policy(child_execution_policy(spec))


def _restrict(dispatcher, connectors) -> None:
    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["mcp"], "connectors": connectors},
    )


def _ids(dispatcher) -> list[str]:
    return [row["id"] for row in dispatcher._m_mcp_list()]


# --------------------------------------------------------------------------
# the leak
# --------------------------------------------------------------------------


def test_a_restricted_specialist_lists_only_its_own_connectors(wired):
    """The defect, as the two listings that differed."""

    dispatcher, _spy = wired
    assert sorted(_ids(dispatcher)) == ["allowed", "denied"]

    _restrict(dispatcher, ["allowed"])
    assert _ids(dispatcher) == ["allowed"]


def test_a_denied_connector_is_never_launched(wired):
    """`enabled` already gated the spawn; the allowlist has to as well.

    Discovery is what starts the process, so a gate on `mcp_call` alone would
    let a denied specialist make the host run a command out of a connector row
    it was not granted. The empty call log is the point of this test — the
    error string alone would also be produced by a refusal issued after launch.
    """

    dispatcher, spy = wired
    _restrict(dispatcher, ["allowed"])

    result = dispatcher._m_mcp_tools("denied")
    assert result == {"error": "connector 'denied' not found"}
    assert spy.calls == []


def test_direct_host_rpc_call_is_refused_too(wired):
    """The surface the audit measured: `mcp_call` straight through Host RPC,
    with no control tool and no UI in front of it."""

    dispatcher, spy = wired
    _restrict(dispatcher, ["allowed"])

    result = dispatcher._m_mcp_call({"server": "denied", "tool": "anything"})
    assert result == {"error": "connector 'denied' not found"}
    assert spy.calls == []


def test_every_connector_rpc_surface_is_covered(wired):
    """One gate, six entry points. They all resolve through `connector()`, and
    this is what says so — a per-method check is the kind that grows a seventh
    method without one."""

    dispatcher, spy = wired
    _restrict(dispatcher, ["allowed"])

    denials = [
        dispatcher._m_mcp_tools("denied"),
        dispatcher._m_mcp_resources({"server": "denied"}),
        dispatcher._m_mcp_resource_read({"server": "denied", "uri": "x://y"}),
        dispatcher._m_mcp_prompts({"server": "denied"}),
        dispatcher._m_mcp_prompt_get({"server": "denied", "name": "p"}),
        dispatcher._m_mcp_call({"server": "denied", "tool": "t"}),
    ]
    assert all(isinstance(row, dict) and "error" in row for row in denials)
    assert spy.calls == []


def test_the_permitted_connector_still_works(wired):
    """The fix must not become "deny everything", which would satisfy every
    denial test above and break every specialist that was granted something."""

    dispatcher, spy = wired
    _restrict(dispatcher, ["allowed"])

    assert dispatcher._m_mcp_tools("allowed") == {"tools": [{"name": "search"}]}
    assert spy.calls == [("list_tools", "allowed")]


def test_a_denied_connector_cannot_be_reached_by_its_display_name(wired):
    """`connector()` resolves an id first and an exact display name second, so
    an allowlist that only understood ids would be bypassed by spelling the
    name instead."""

    dispatcher, spy = wired
    _restrict(dispatcher, ["allowed"])

    assert "error" in dispatcher._m_mcp_tools("Denied")
    assert spy.calls == []


def test_a_permitted_connector_may_be_named_either_way(wired):
    """The same resolution rule, in the direction that must not over-deny: a
    user who allowlisted the display name still gets the connector."""

    dispatcher, _spy = wired
    _restrict(dispatcher, ["Allowed"])

    assert _ids(dispatcher) == ["allowed"]
    assert dispatcher._m_mcp_tools("allowed") == {"tools": [{"name": "search"}]}


# --------------------------------------------------------------------------
# the tri-state, which Python spells wrong by default
# --------------------------------------------------------------------------


def test_an_empty_list_denies_every_connector(wired):
    """`[]` is a decision. `if not allowed:` reads it as "no restriction" —
    fail-open on the one configuration chosen to lock everything down."""

    dispatcher, spy = wired
    assert _ids(dispatcher), "nothing to deny"

    _restrict(dispatcher, [])
    assert _ids(dispatcher) == []
    assert "error" in dispatcher._m_mcp_tools("allowed")
    assert spy.calls == []


def test_omitting_connectors_does_not_restrict(wired):
    """`None` inherits. A child that names no connector is not a child denied
    all of them — that reading would silently disable every specialist that
    never set the field."""

    dispatcher, _spy = wired
    before = sorted(_ids(dispatcher))

    _apply(dispatcher, {"unrestricted": False, "capabilities": ["mcp"]})
    assert sorted(_ids(dispatcher)) == before


def test_an_unrestricted_specialist_still_sees_everything(wired):
    dispatcher, _spy = wired
    before = sorted(_ids(dispatcher))

    _apply(dispatcher, {"unrestricted": True})
    assert sorted(_ids(dispatcher)) == before


def test_applying_twice_narrows_and_never_widens(wired):
    """A delegation chain applies a policy per hop. Composition has to be
    monotonic or the second hop is the way out of the first."""

    dispatcher, _spy = wired
    _restrict(dispatcher, ["allowed"])
    _restrict(dispatcher, ["allowed", "denied"])

    assert _ids(dispatcher) == ["allowed"]
    assert "error" in dispatcher._m_mcp_tools("denied")


def test_a_bare_string_is_not_read_as_one_connector():
    """`"connectors": "slack"` is a mistake, and guessing it means one
    connector invents intent. The policy refuses it rather than silently
    granting or silently denying."""

    with pytest.raises(DelegationPolicyError):
        child_execution_policy({"unrestricted": False, "connectors": "slack"})


# --------------------------------------------------------------------------
# delegation must not be the way out — of either half
# --------------------------------------------------------------------------


def test_a_child_cannot_name_a_connector_its_parent_was_denied():
    merged = _normalize_item(
        {"request": "go", "connectors": ["a", "b", "c"]},
        {"connectors": ["a"], "unrestricted": False},
    )
    assert merged["connectors"] == ["a"]


def test_an_empty_child_connector_list_survives_the_merge():
    """The falsy collapse one layer up: `[]` must reach the policy as a denial,
    not vanish because it is falsy."""

    merged = _normalize_item(
        {"request": "go", "connectors": []}, {"connectors": ["a", "b"]}
    )
    assert merged["connectors"] == []
    assert child_execution_policy(merged).connector_names == frozenset()


def test_a_nested_child_cannot_widen_its_parents_connectors():
    """The hole `_normalize_item` does not cover, and the reason a second
    narrowing exists.

    `_normalize_item` narrows an item against the delegate() call's own kwargs.
    Only `_apply_parent_execution_ceiling` sees the parent CHILD's spec, so
    only it can bound a grandchild — and it narrowed capabilities and
    permissions while leaving both resource allowlists alone.
    """

    merged = _apply_parent_execution_ceiling(
        {"request": "go", "connectors": ["a", "b"]},
        {"connectors": ["a"], "unrestricted": False, "capabilities": ["mcp"]},
    )
    assert merged["connectors"] == ["a"]


def test_a_nested_child_cannot_widen_its_parents_skills_either():
    """The same hole on the half that was recorded as done. Arming a lock that
    the next delegation hop walks around is the same defect wearing a fix."""

    merged = _apply_parent_execution_ceiling(
        {"request": "go", "skill_names": ["a", "b"]},
        {"skill_names": ["a"], "unrestricted": False, "capabilities": ["skills"]},
    )
    assert merged["skill_names"] == ["a"]


def test_a_nested_child_that_names_nothing_still_inherits():
    merged = _apply_parent_execution_ceiling(
        {"request": "go"},
        {
            "connectors": ["a"],
            "skill_names": ["s"],
            "unrestricted": False,
            "capabilities": ["mcp", "skills"],
        },
    )
    assert merged["connectors"] == ["a"]
    assert merged["skill_names"] == ["s"]


def test_a_nested_child_of_an_unrestricted_parent_can_restrict_itself():
    """Narrowing is the point; only widening is refused. An unrestricted parent
    must not have its child's own restriction erased."""

    merged = _apply_parent_execution_ceiling(
        {"request": "go", "connectors": ["a"]}, {"unrestricted": True}
    )
    assert merged["connectors"] == ["a"]


# --------------------------------------------------------------------------
# the wiring, which is the part that was missing
# --------------------------------------------------------------------------


def test_the_policy_carries_connectors_into_the_runtime():
    """`connectors` had nowhere to go: the spec held it, the policy dropped it.
    A value that never reaches an enforcement point is a setting, not a lock."""

    policy = child_execution_policy(
        {"unrestricted": False, "capabilities": ["mcp"], "connectors": ["a"]}
    )
    assert policy.connector_names == frozenset({"a"})
    assert child_execution_policy({"unrestricted": False}).connector_names is None
