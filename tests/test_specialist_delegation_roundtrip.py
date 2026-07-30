"""Delegating to a specialist that was actually saved.

`agents.unrestricted` is `INTEGER NOT NULL`, so SQLite hands it back as `0` or
`1`. The repository decoded `skill_names` and `connectors` from JSON and left
this column alone, and `child_execution_policy` refuses anything that is not
exactly a `bool`:

    if unrestricted is not None and type(unrestricted) is not bool:
        raise DelegationPolicyError("unrestricted must be a boolean")

So every stored specialist failed to delegate — the unrestricted ones and the
deliberately locked ones alike. Not an edge case: the column is NOT NULL, so
there is no stored profile this did not affect.

The strict check is right and is not what changed. A truthy string like
`"false"` must not pass for True, which is exactly what a lenient check would
allow. What was missing is the decode, and it belongs at the repository
boundary next to its two siblings so that one place owns the conversion rather
than every caller remembering to coerce — the UI projection already did
(`bool(r.get("unrestricted", 1))` in the gateway), which is why the settings
page showed a correct checkbox while delegation was broken.

The recovery is worse than the failure, which is why this is not a low-severity
type nit: a model that hits `unrestricted must be a boolean` and retries with
`unrestricted: true` sets a call-site value that wins over the profile — so the
obvious repair escalates a specialist its author had locked down.
"""

from __future__ import annotations

import pytest

from openai4s.host.delegation_policy import (
    DelegationPolicyError,
    child_execution_policy,
)
from openai4s.store import get_store


@pytest.fixture
def store(tmp_path):
    return get_store(tmp_path / "agents.db")


def _saved(store, name, *, unrestricted):
    """Write and read back. Constructing the profile in memory is what let the
    existing suite stay green — `tests/test_host_delegation_service.py` builds
    a FakeStore whose profile carries a genuine Python `False`, so the int
    never appears."""
    store.upsert_agent(
        name=name, description="d", system_prompt="p", unrestricted=unrestricted
    )
    return store.get_agent(name)


def test_a_saved_specialist_comes_back_as_a_real_boolean(store):
    """The defect, at the boundary where it belongs."""
    for name, value in (("open-one", True), ("locked-one", False)):
        profile = _saved(store, name, unrestricted=value)
        assert (
            profile["unrestricted"] is value
        ), f"{name} came back as {profile['unrestricted']!r}, not a bool"


def test_both_kinds_of_specialist_can_actually_delegate(store):
    """Restricted and unrestricted alike. The refusal hit both, so a fix that
    only rescued one would still be a broken feature."""
    for name, value in (("open-one", True), ("locked-one", False)):
        profile = _saved(store, name, unrestricted=value)
        policy = child_execution_policy(
            {"request": "do it", "unrestricted": profile["unrestricted"]}
        )
        assert policy is not None


def test_a_locked_specialist_stays_locked(store):
    """The type fix must not quietly become "treat everything as allowed".
    A profile saved with unrestricted=False has to produce a restricted child.
    """
    profile = _saved(store, "locked-one", unrestricted=False)
    policy = child_execution_policy(
        {"request": "do it", "unrestricted": profile["unrestricted"]}
    )
    assert policy.restricted is True
    assert policy.allowed == frozenset()


def test_an_unrestricted_specialist_is_not_restricted(store):
    profile = _saved(store, "open-one", unrestricted=True)
    policy = child_execution_policy(
        {"request": "do it", "unrestricted": profile["unrestricted"]}
    )
    assert policy.restricted is False


def test_the_strict_check_still_refuses_a_string(store):
    """What the strict check is for, and why the fix is a decode rather than a
    loosened comparison. `"false"` is truthy; accepting it would turn a locked
    specialist loose."""
    with pytest.raises(DelegationPolicyError):
        child_execution_policy({"request": "x", "unrestricted": "false"})
    with pytest.raises(DelegationPolicyError):
        child_execution_policy({"request": "x", "unrestricted": 1})


def test_a_partial_update_does_not_reintroduce_the_int(store):
    """`update` writes only the columns supplied, so it is a second path back
    out of the database and has to decode the same way."""
    _saved(store, "open-one", unrestricted=True)
    store.update_agent("open-one", description="edited")
    profile = store.get_agent("open-one")
    assert profile["unrestricted"] is True
    assert profile["description"] == "edited"


def test_the_listing_is_decoded_too(store):
    """`get` is implemented over `list`, but a future split must not leave one
    of them returning ints."""
    _saved(store, "a", unrestricted=True)
    _saved(store, "b", unrestricted=False)
    listed = {row["name"]: row["unrestricted"] for row in store.list_agents()}
    assert listed == {"a": True, "b": False}
    assert all(isinstance(v, bool) for v in listed.values())
