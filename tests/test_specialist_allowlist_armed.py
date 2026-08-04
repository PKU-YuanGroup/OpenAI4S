"""Whether the specialist Skill allowlist is ever actually switched on.

`SkillService.set_allowed_skills` narrows every read path — catalogue, search,
`load`/`get`, and `read` — and `tests/test_resource_allowlist.py` covers all
four thoroughly. It had one definition and six call sites, and every one of the
six was in that test file. No production code called it.

So `_allowed_skills` stayed `None` for the entire lifetime of every real
dispatcher, and `None` means permit everything. A specialist restricted to one
Skill was handed all 34 bundled ones from `skills_list` and could `skills_read`
the full body of any of them. The restriction was stored on the specialist,
inherited through delegation, merged into the child spec, echoed back by the
API — and enforced nowhere.

Measured before the fix: 34 visible, 33 of them not permitted, and
`skills_read` on one of the 33 returned 4972 characters.

This is the failure mode per-commit review structurally cannot see. The filter
was right, its tests were right, and the tests passed because they armed the
allowlist themselves — which is exactly what the thing under test never did.

The second half matters as much as the first. `_normalize_item` merged a
child's spec over its parent's with `dict.update`, so a nested child could
REPLACE the list it inherited: restricted to one Skill, ask for three, get
three. Arming a lock that delegation walks around is the same defect wearing a
fix. Inheritance now narrows.

The connector half was a build, not a wiring gap: there was no
`set_allowed_connectors` and nothing to arm. It is built now and armed at the
same call site, so the two halves cannot drift apart again — its contract lives
in `test_specialist_connector_allowlist.py`.
"""

from __future__ import annotations

import pytest

from openai4s.agent.delegation import _normalize_item
from openai4s.config import Config, LLMConfig
from openai4s.host.delegation_policy import child_execution_policy
from openai4s.host_dispatch import build_dispatcher


@pytest.fixture
def dispatcher(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return build_dispatcher(cfg, frame_id="f-child", workspace=workspace)


def _names(dispatcher) -> list[str]:
    return [row["name"] for row in dispatcher._skill_service.list()]


def _apply(dispatcher, spec) -> None:
    """Exactly what the spawn path does: build the policy, hand it over."""
    dispatcher.set_child_execution_policy(child_execution_policy(spec))


# --------------------------------------------------------------------------
# the leak
# --------------------------------------------------------------------------


def test_a_restricted_specialist_sees_only_its_own_skills(dispatcher):
    """The defect, as the two counts that differed."""
    everything = _names(dispatcher)
    assert len(everything) > 5, "no bundled skills loaded; this test proves nothing"
    keep = everything[0]

    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["skills"], "skill_names": [keep]},
    )
    assert _names(dispatcher) == [keep]


def test_a_skill_it_may_not_see_is_a_skill_it_cannot_read(dispatcher):
    """Filtering the catalogue while leaving `read` open would be a lock on the
    menu and not on the door — and `read` returns the whole SKILL.md body."""
    everything = _names(dispatcher)
    keep, denied = everything[0], everything[1]
    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["skills"], "skill_names": [keep]},
    )
    # It raises rather than returning the soft-fail dict, and the message is
    # "no such skill" rather than "not permitted" — deliberate: a denied
    # specialist should not learn the name exists from the refusal.
    with pytest.raises(KeyError) as caught:
        dispatcher._skill_service.read({"name": denied})
    assert "no such skill" in str(caught.value)


def test_search_is_filtered_too(dispatcher):
    """Progressive disclosure means search is how the agent finds a Skill. A
    name it can discover is a name it will ask for.

    The query has to be one that actually matches something. The first version
    of this test searched for "" — which returns 0 rows even unrestricted — so
    `all(...)` was vacuously true and the test passed with the allowlist
    disarmed. It only failed the mutation check that removed the arming
    because four OTHER tests caught it.
    """
    unrestricted = dispatcher._skill_service.search({"query": "protein"})
    assert len(unrestricted) > 1, "the query matches nothing; this proves nothing"
    keep = unrestricted[0]["name"]

    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["skills"], "skill_names": [keep]},
    )
    found = dispatcher._skill_service.search({"query": "protein"})
    assert [row["name"] for row in found] == [keep]


# --------------------------------------------------------------------------
# the tri-state, which Python spells wrong by default
# --------------------------------------------------------------------------


def test_an_empty_list_denies_everything(dispatcher):
    """`[]` is a decision, and `if not allowed:` reads it as "no restriction" —
    fail-open on the one configuration chosen to lock something down. This is
    the case the whole tri-state exists for."""
    assert _names(dispatcher), "nothing to deny"
    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["skills"], "skill_names": []},
    )
    assert _names(dispatcher) == []


def test_an_unrestricted_specialist_still_sees_everything(dispatcher):
    """The fix must not become "deny by default", which would satisfy every
    test above and break every specialist that was never restricted."""
    before = _names(dispatcher)
    _apply(dispatcher, {"unrestricted": True})
    assert _names(dispatcher) == before


def test_omitting_skill_names_does_not_restrict(dispatcher):
    """`None` inherits. A child that names no skills is not a child denied all
    of them — that reading would silently disable every existing specialist."""
    before = _names(dispatcher)
    _apply(dispatcher, {"unrestricted": False, "capabilities": ["skills"]})
    assert _names(dispatcher) == before


def test_applying_twice_narrows_and_never_widens(dispatcher):
    """A delegation chain applies a policy per hop. Composition must be
    monotonic or the second hop is the way out of the first."""
    everything = _names(dispatcher)
    first, second = everything[0], everything[1]
    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["skills"], "skill_names": [first]},
    )
    _apply(
        dispatcher,
        {
            "unrestricted": False,
            "capabilities": ["skills"],
            "skill_names": [first, second],
        },
    )
    assert _names(dispatcher) == [first]


# --------------------------------------------------------------------------
# delegation must not be the way out
# --------------------------------------------------------------------------


def test_a_child_cannot_name_a_skill_its_parent_was_denied():
    """`dict.update` let the child's spec replace what it inherited. Restricted
    to one, ask for three, get three — a lock with a documented bypass."""
    merged = _normalize_item(
        {"request": "go", "skill_names": ["a", "b", "c"]},
        {"skill_names": ["a"], "unrestricted": False},
    )
    assert merged["skill_names"] == ["a"]


def test_a_child_that_names_nothing_inherits_the_restriction():
    merged = _normalize_item(
        {"request": "go"}, {"skill_names": ["a"], "unrestricted": False}
    )
    assert merged["skill_names"] == ["a"]


def test_a_child_may_still_narrow_further():
    """Narrowing is the point; only widening is refused."""
    merged = _normalize_item(
        {"request": "go", "skill_names": ["a"]},
        {"skill_names": ["a", "b"], "unrestricted": False},
    )
    assert merged["skill_names"] == ["a"]


def test_a_child_of_an_unrestricted_parent_can_restrict_itself():
    merged = _normalize_item(
        {"request": "go", "skill_names": ["a"]}, {"unrestricted": True}
    )
    assert merged["skill_names"] == ["a"]


def test_an_empty_child_list_survives_the_merge():
    """The falsy collapse again, one layer up: `[]` must reach the policy as a
    denial, not vanish because it is falsy."""
    merged = _normalize_item(
        {"request": "go", "skill_names": []}, {"skill_names": ["a", "b"]}
    )
    assert merged["skill_names"] == []


# --------------------------------------------------------------------------
# what is still not enforced
# --------------------------------------------------------------------------


def test_the_connector_half_is_armed_at_the_same_choke_point(dispatcher):
    """This used to assert the opposite — that `connectors` had no mechanism to
    arm — because it did not: stored, inherited and merged exactly like
    `skill_names`, and enforced nowhere. Both halves now hang off the one call,
    which is what keeps a future reader from re-splitting them. The connector
    contract itself is in `test_specialist_connector_allowlist.py`.
    """
    dispatcher.store.upsert_connector(
        connector_id="keep", name="Keep", command=["python", "-c", "pass"]
    )
    dispatcher.store.upsert_connector(
        connector_id="drop", name="Drop", command=["python", "-c", "pass"]
    )
    assert sorted(row["id"] for row in dispatcher._m_mcp_list()) == ["drop", "keep"]

    _apply(
        dispatcher,
        {"unrestricted": False, "capabilities": ["mcp"], "connectors": ["keep"]},
    )
    assert [row["id"] for row in dispatcher._m_mcp_list()] == ["keep"]


def test_the_dispatcher_is_where_the_arming_happens():
    """The gate is only as good as its call site, and this one had none for its
    entire existence. Pinning the wiring, not just the filter."""
    import inspect

    from openai4s.host_dispatch import HostDispatcher

    source = inspect.getsource(HostDispatcher.set_child_execution_policy)
    assert "set_allowed_skills" in source


# --------------------------------------------------------------------------
# the allowlist is armed now; three ways it still did not bind
# --------------------------------------------------------------------------
#
# Measured on this branch before the fix:
#   (c) row {skill_names: ["only-this"], connectors: [], unrestricted: False}
#       + call {skill_names: [3 names]}  -> the child got all three.
#       + call {connectors: ["anything"]} -> enabled, despite [] meaning denied.
#       + call {unrestricted: True}       -> True.
#   (a) a child restricted to one skill still had all 34 rendered into its
#       system prompt, by name and summary.
#   (b) a denied skill's `kernel.py` was still importable in the child's cell:
#       the in-kernel gate knew `disabled` (the user's switch) and had no
#       concept of `denied` (the policy's).
#
# (c) is the one that makes the other two moot on their own: if the caller
# chooses the allowlist, filtering the prompt and the import gate against it
# filters against a value the caller picked.


def _profile_merge(spec: dict, row: dict) -> dict:
    from openai4s.host.delegation import _with_profile_overrides

    return _with_profile_overrides(dict(spec), dict(row))


_ROW = {"skill_names": ["only-this"], "connectors": [], "unrestricted": False}


def test_a_call_site_cannot_widen_the_stored_skill_allowlist():
    merged = _profile_merge({"skill_names": ["only-this", "and-this", "more"]}, _ROW)
    assert merged["skill_names"] == ["only-this"]


def test_a_call_site_cannot_reopen_a_denied_connector_set():
    """`[]` is not "unset". It is the tri-state's deny."""
    merged = _profile_merge({"connectors": ["anything"]}, _ROW)
    assert merged["connectors"] == []


def test_a_call_site_cannot_lift_its_own_restriction():
    merged = _profile_merge({"unrestricted": True}, _ROW)
    assert merged["unrestricted"] is False


def test_narrowing_and_inheriting_still_work():
    """The refusal must not be an outage: a child may still tighten, and a
    call that names nothing inherits rather than escaping."""
    assert _profile_merge({"skill_names": []}, _ROW)["skill_names"] == []
    assert _profile_merge({}, _ROW)["skill_names"] == ["only-this"]
    # A row that restricts nothing leaves a caller free to restrict itself.
    assert _profile_merge({"skill_names": ["a"]}, {})["skill_names"] == ["a"]


def test_the_prompt_lists_only_permitted_skills(tmp_path):
    """The exit criterion names the prompt as a surface, and it was rendered
    from the raw corpus: `HostDispatcher.skill_loader` is every skill on disk,
    and `Agent` bound that."""
    from openai4s.config import Config
    from openai4s.host.skills import SkillService

    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    service = SkillService(cfg)
    service.loader.discover()
    names = sorted(service.loader.skills())
    assert len(names) > 5, "an empty corpus would make this pass vacuously"

    unrestricted = service.system_context()
    assert all(name in unrestricted for name in names[:3])

    service.set_allowed_skills([names[0]])
    restricted = service.system_context()
    assert names[0] in restricted
    assert not any(name in restricted for name in names[1:]), restricted


def test_the_kernel_import_gate_knows_about_denied_not_only_disabled(tmp_path):
    from openai4s.config import Config
    from openai4s.host.skills import SkillService

    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    service = SkillService(cfg)
    service.loader.discover()
    names = sorted(service.loader.skills())

    assert "_o4s_denied_skills = set()" in service.bootstrap_code()

    service.set_allowed_skills([names[0]])
    gated = service.bootstrap_code()
    denied_line = gated.split("_o4s_denied_skills = ", 1)[1].split("\n", 1)[0]
    assert denied_line != "set()", "the gate was generated with nothing denied"
    assert "is not available to this agent" in gated


def test_the_dispatcher_exposes_the_filtered_view_not_the_corpus(tmp_path):
    """`skill_disclosure` and `skill_loader` must be different objects, or the
    wiring change is cosmetic."""
    from openai4s.config import Config
    from openai4s.host_dispatch import HostDispatcher

    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    dispatcher = HostDispatcher(cfg=cfg, frame_id="f-x")

    assert dispatcher.skill_disclosure is not dispatcher.skill_loader
    assert hasattr(dispatcher.skill_disclosure, "set_allowed_skills")
