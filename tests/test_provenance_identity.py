"""Whether a tagged object is still the object that was tagged.

The side table mapped `id(obj) -> tags`. An id is not an identity: CPython
reuses a freed object's address immediately, so tagging a tuple, letting it go,
and allocating another one of the same shape made the new object report the old
one's lineage. Reproduced on the *first* allocation, not after a long run.

That is a fabricated provenance edge — an object that never touched an artifact
naming that artifact as its source — in the subsystem whose entire claim is
that a result is reconstructible. This repository already states the principle
twice, about environment capture and about retrieval envelopes: provenance that
is wrong is worse than provenance that is absent, because it is the kind that
gets believed.

The side table is not an exotic corner either. Every builtin container lands
there — `list` and `dict` included, because neither accepts an attribute — so
it is the primary path for anything `json.loads` returns.
"""

from __future__ import annotations

import pytest

from openai4s.kernel import provenance


@pytest.fixture(autouse=True)
def _clean_side_table():
    provenance._side_tags.clear()
    provenance._side_tags_bytes[0] = 0
    provenance._side_tags_evicted[0] = 0
    yield
    provenance._side_tags.clear()
    provenance._side_tags_bytes[0] = 0
    provenance._side_tags_evicted[0] = 0


def _fresh(n):
    """A tuple built at runtime. A literal would be constant-folded and interned
    by the compiler, so its address never changes and the bug cannot appear —
    which is exactly how a test of this could pass while the defect is live."""
    return tuple([n, n + 1, n + 2])


def test_an_entry_is_never_answered_for_a_different_object():
    """The defect, stated so it can actually fail.

    An earlier version of this test freed a tagged object and allocated until
    CPython handed the address back. That reproduces the original bug, but it
    cannot *falsify the fix*: once an entry pins its object the address is
    never reused, the loop finds nothing, and the test falls through to a
    weaker assertion — so removing the identity check left it green. Mutation
    testing is what exposed that.

    So the collision is constructed rather than waited for: an entry filed
    under one object's id while a different object lives at that id is exactly
    the state address reuse produces, and answering from it is the bug.
    """
    owner = _fresh(1)
    stranger = _fresh(99)
    provenance.set_tags(owner, frozenset({"v-REAL-SOURCE"}))
    assert provenance.get_tags(owner) == frozenset({"v-REAL-SOURCE"})

    # what address reuse looks like from the table's point of view
    provenance._side_tags[id(stranger)] = (owner, frozenset({"v-REAL-SOURCE"}))
    assert (
        provenance.get_tags(stranger) == frozenset()
    ), "an unrelated object was handed another object's lineage"


def test_an_entry_pins_the_object_it_describes():
    """The other half of the fix, and the reason the collision above cannot
    arise naturally: while the entry lives, the address cannot be recycled."""
    tagged = _fresh(4)
    provenance.set_tags(tagged, frozenset({"v-source"}))
    entry = provenance._side_tags[id(tagged)]
    assert (
        entry[0] is tagged
    ), "the entry does not hold the object, so its id can be reused"


def test_the_real_allocator_no_longer_recycles_a_tagged_address():
    """The original reproduction, kept as an end-to-end check rather than as
    the primary assertion — it passes for the right reason only in company
    with the two above."""
    tagged = _fresh(1)
    provenance.set_tags(tagged, frozenset({"v-REAL-SOURCE"}))
    freed_id = id(tagged)
    del tagged
    for index in range(50_000):
        candidate = _fresh(index)
        if id(candidate) == freed_id:
            assert provenance.get_tags(candidate) == frozenset()
            return
        del candidate


def test_every_builtin_container_uses_this_path():
    """If only exotic scalars reached the side table this would be a corner
    case. `list` and `dict` reach it, which is what `json.loads` returns."""
    for value in ([1], {"a": 1}, (1, 2), {1, 2}, "text", b"bytes", 1, 1.5):
        try:
            object.__setattr__(value, "_openai4s_src", frozenset())
            attributable = True
        except (AttributeError, TypeError):
            attributable = False
        assert (
            not attributable
        ), f"{type(value).__name__} no longer needs the side table"


def test_a_tag_survives_for_the_object_that_earned_it():
    """The fix must not be "return nothing", which would also pass the test
    above. Provenance still has to work."""
    payload = _fresh(7)
    provenance.set_tags(payload, frozenset({"v-source"}))
    assert provenance.get_tags(payload) == frozenset({"v-source"})
    assert provenance.merge_tags(payload, _fresh(8)) == frozenset({"v-source"})


# --------------------------------------------------------------------------
# the bounds, and the direction they fail in
# --------------------------------------------------------------------------


def test_the_table_is_bounded_by_count():
    """Pinning objects to keep their identity valid is a real memory cost, so
    it has to stop somewhere.

    Both the loop and the expectation are fixed numbers rather than
    `MAX_SIDE_TAGS + 500`. Sizing the loop from the constant under test means
    raising the cap makes this run ten million iterations instead of failing —
    the test hangs rather than reports, which is how mutation testing found
    this version of it.
    """
    for index in range(3000):
        provenance.set_tags(_fresh(index), frozenset({f"v-{index}"}))
    assert len(provenance._side_tags) <= 2048
    assert provenance._side_tags_evicted[0] >= 900


def test_the_table_is_bounded_by_bytes_too():
    """A count cap alone would let fifty 2 MB payloads pin 100 MB — the same
    "a count cannot bound what varies in length" mistake the memory budgets
    were written for."""
    for index in range(50):
        provenance.set_tags(bytes(2_000_000), frozenset({f"v-{index}"}))
    assert provenance._side_tags_bytes[0] <= provenance.MAX_SIDE_TAG_BYTES
    assert len(provenance._side_tags) < 50


def test_eviction_loses_a_tag_rather_than_inventing_one():
    """The direction matters more than the bound. An evicted object must report
    no lineage — never a neighbour's."""
    first = _fresh(0)
    provenance.set_tags(first, frozenset({"v-FIRST"}))
    keep_alive = [first]
    for index in range(1, 3000):  # fixed, for the reason above
        item = _fresh(index)
        keep_alive.append(item)
        provenance.set_tags(item, frozenset({f"v-{index}"}))

    evicted = provenance.get_tags(first)
    assert evicted == frozenset(), "an evicted object should have no lineage"
    assert "v-1" not in evicted and "v-2" not in evicted


def test_retagging_the_same_object_does_not_double_count_its_size():
    """Otherwise the byte budget drifts up on every re-tag and evicts healthy
    entries for a cost that was never paid."""
    payload = bytes(1_000_000)
    provenance.set_tags(payload, frozenset({"v-a"}))
    after_first = provenance._side_tags_bytes[0]
    provenance.set_tags(payload, frozenset({"v-a", "v-b"}))
    assert provenance._side_tags_bytes[0] == after_first
    assert provenance.get_tags(payload) == frozenset({"v-a", "v-b"})
