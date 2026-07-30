"""Tri-state resource allowlists, and why `None` and `[]` must not agree.

A specialist may restrict which Skills its children reach. The stored value has
three meanings:

    None          inherit — no restriction of its own
    []            deny everything
    ["a", "b"]    exactly these

Python spells the first two false, and `if not allowed:` reads perfectly well
while turning "deny everything" into "allow everything" — a permission check
that fails *open*, silently, for the single configuration a user chose in order
to lock something down. That is the failure this module exists to make
impossible, so it is the first thing asserted.

Enforcement was deferred here on purpose: D5 fixed the P0 data-loss bug and
*hid* the restriction UI, so that no lock would be shown that was not enforced.
Until this landed, `skill_names` was stored on the specialist, inherited
through delegation, merged into the child spec — and read by nothing.
"""

from __future__ import annotations

import pytest

from openai4s.host import resource_allowlist as allowlist


def test_none_and_empty_are_opposites_despite_both_being_falsy():
    """The whole point, stated once, plainly."""
    assert not None and not []  # both falsy — this is the trap
    assert allowlist.permits(None, "chem") is True
    assert allowlist.permits([], "chem") is False
    assert allowlist.describe(None) == allowlist.INHERIT
    assert allowlist.describe([]) == allowlist.DENY_ALL


def test_an_explicit_list_permits_exactly_its_members():
    assert allowlist.permits(["chem", "bio"], "chem") is True
    assert allowlist.permits(["chem", "bio"], "physics") is False
    assert allowlist.describe(["chem"]) == allowlist.EXPLICIT


def test_an_uninterpretable_value_denies_rather_than_inherits():
    """A bare string is almost certainly a mistake (`"skills": "chem"`), and
    guessing it means one skill would be inventing intent. A value nobody can
    interpret is not permission to do everything."""
    for bad in ("chem", 42, {"a": 1}, object()):
        assert allowlist.permits(bad, "chem") is False, bad
        assert allowlist.describe(bad) == allowlist.DENY_ALL


# --------------------------------------------------------------------------
# narrowing: a child may only tighten
# --------------------------------------------------------------------------


def test_a_restricted_parent_cannot_be_widened_by_a_child_asking_for_nothing():
    """The case a naive implementation leaks through.

    Reading the child's `None` as "no opinion, so use the child's" hands a
    restricted parent's delegate an unrestricted set — and delegation becomes
    the way out of every allowlist that exists.
    """
    assert allowlist.narrow(["a"], None) == frozenset({"a"})
    assert allowlist.narrow(["a"], ["a", "b"]) == frozenset({"a"})
    assert allowlist.narrow(["a"], ["b"]) == frozenset()


def test_narrowing_composes_so_a_chain_cannot_regain_access():
    """Three levels deep, each asking for more than it was given."""
    effective = None
    for requested in (["a", "b", "c"], ["a", "b"], ["a", "b", "c", "d"]):
        effective = allowlist.narrow(effective, requested)
    assert effective == frozenset({"a", "b"})
    assert allowlist.permits(effective, "c") is False
    assert allowlist.permits(effective, "d") is False


def test_an_unrestricted_parent_still_honours_a_childs_own_restriction():
    assert allowlist.narrow(None, ["a"]) == frozenset({"a"})
    assert allowlist.narrow(None, []) == frozenset()
    assert allowlist.narrow(None, None) is None


def test_deny_all_survives_every_later_request():
    """`[]` is the strongest state and nothing may relax it."""
    effective = allowlist.narrow(None, [])
    for requested in (None, ["a"], ["a", "b"]):
        effective = allowlist.narrow(effective, requested)
        assert effective == frozenset(), requested


def test_storage_round_trip_keeps_the_three_states_distinct():
    """`None` must not serialise to `[]`, or a reload turns inherit into deny
    — or, far worse, the reverse."""
    assert allowlist.as_list(None) is None
    assert allowlist.as_list([]) == []
    assert allowlist.as_list(["b", "a"]) == ["a", "b"]  # sorted, so JSON is stable


# --------------------------------------------------------------------------
# enforcement: the exit criterion is four surfaces, not one
# --------------------------------------------------------------------------


def _service(tmp_path):
    from openai4s.config import Config
    from openai4s.host.skills import SkillService

    return SkillService(Config(data_dir=tmp_path / "data"))


def test_an_unlisted_skill_is_absent_from_the_catalogue_and_from_search(tmp_path):
    """The catalogue feeds the prompt, so filtering only `load` would leave the
    agent reading a menu of things it cannot open — both a leak and a dead end.
    """
    service = _service(tmp_path)
    service.loader.discover()
    everything = [row["name"] for row in service.list()]
    if len(everything) < 2:
        pytest.skip("needs at least two bundled skills to tell filtering apart")

    keep = everything[0]
    service.set_allowed_skills([keep])

    assert [row["name"] for row in service.list()] == [keep]
    for row in service.search({"query": everything[1], "limit": 5}):
        assert row["name"] == keep


def test_an_unlisted_skill_cannot_be_loaded_or_read_over_host_rpc(tmp_path):
    """The surface an agent reaches directly. A restriction enforced only in
    the catalogue is a suggestion."""
    service = _service(tmp_path)
    service.loader.discover()
    everything = [row["name"] for row in service.list()]
    if len(everything) < 2:
        pytest.skip("needs at least two bundled skills")

    keep, blocked = everything[0], everything[1]
    service.set_allowed_skills([keep])

    # Same answer as a skill that does not exist: a distinct refusal would
    # confirm it is there, which is most of what an enumerator wants.
    assert "error" in service.load(blocked)
    assert "no such skill" in service.load(blocked)["error"]
    with pytest.raises(KeyError):
        service.get(blocked)
    with pytest.raises(KeyError):
        service.read({"name": blocked})

    # ...and the permitted one still works, or the test proves only that
    # everything is broken.
    assert service.load(keep).get("name") == keep


def test_deny_all_really_denies_all_rather_than_inheriting(tmp_path):
    """`[]` through the service, not just through the helper. This is the
    configuration a falsy check turns into "unrestricted"."""
    service = _service(tmp_path)
    service.loader.discover()
    everything = [row["name"] for row in service.list()]
    if not everything:
        pytest.skip("no bundled skills")

    service.set_allowed_skills([])
    assert service.list() == []
    assert "error" in service.load(everything[0])


def test_setting_an_allowlist_twice_narrows_and_never_widens(tmp_path):
    """A delegation chain calls this repeatedly. If the second call replaced
    the first, delegating would clear the parent's restriction."""
    service = _service(tmp_path)
    service.loader.discover()
    everything = [row["name"] for row in service.list()]
    if len(everything) < 2:
        pytest.skip("needs at least two bundled skills")

    service.set_allowed_skills([everything[0]])
    service.set_allowed_skills(None)  # inherit: must not clear
    assert [row["name"] for row in service.list()] == [everything[0]]

    service.set_allowed_skills(everything)  # ask for more: must not widen
    assert [row["name"] for row in service.list()] == [everything[0]]
