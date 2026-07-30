"""Tri-state resource allowlists, and the narrowing rule for delegation.

A specialist may restrict which Skills and Connectors its children can reach.
The stored value has three meanings and they are not interchangeable:

    None          inherit — no restriction of its own
    []            deny everything
    ["a", "b"]    exactly these

The distinction between `None` and `[]` is the whole point and it is the one a
reader loses first, because Python spells both of them false. `if not allowed:`
reads naturally and turns "deny everything" into "allow everything" — a
permission check that fails *open*, silently, for the one configuration a user
chose specifically to lock something down. Every check here is against `is
None`, and the tests assert the two do not behave alike.

Delegation may only narrow. A child's effective set is the intersection of its
own request with what its parent already had, so no chain of delegations can
end up with more access than it started with: a parent limited to `{a}` cannot
produce a child with `{a, b}` by asking for `b`, and cannot escape by asking
for `None` either — inheriting is not the same as widening.
"""

from __future__ import annotations

from typing import Iterable, Sequence

#: What `None` means, spelled out. Returned by `describe` so a UI can say
#: "inherited" rather than rendering an empty box that looks like "none".
INHERIT = "inherit"
DENY_ALL = "deny_all"
EXPLICIT = "explicit"


def normalise(value: object) -> frozenset[str] | None:
    """Coerce a stored allowlist to `None` (inherit) or a concrete set.

    An empty *list* is a real decision and becomes an empty frozenset, which is
    not the same as `None`. Anything unrecognisable — a string, a number, a
    dict — is treated as deny-all rather than inherit: a value nobody can
    interpret is not permission to do everything.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        # A bare string is almost certainly a mistake ("skills": "chem"), and
        # guessing that it means one skill would be inventing intent. Deny.
        return frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    return frozenset()


def describe(value: object) -> str:
    """`inherit` / `deny_all` / `explicit`, for a UI that must not show a lock
    it cannot explain."""
    resolved = normalise(value)
    if resolved is None:
        return INHERIT
    return DENY_ALL if not resolved else EXPLICIT


def permits(value: object, name: str) -> bool:
    """Is `name` reachable under this allowlist?

    `None` permits everything, an empty set permits nothing, and a populated
    set permits exactly its members. Written as three explicit branches rather
    than one clever expression because the clever expression is what collapses
    the first two.
    """
    resolved = normalise(value)
    if resolved is None:
        return True
    return str(name).strip() in resolved


def narrow(parent: object, child: object) -> frozenset[str] | None:
    """The effective allowlist for a child, which may only be tighter.

    Four cases, and the asymmetry is deliberate:

      parent None,  child None   -> None   (nobody restricted anything)
      parent None,  child X      -> X      (the child restricted itself)
      parent P,     child None   -> P      (inheriting is not widening)
      parent P,     child X      -> P ∩ X  (both apply; deny wins)

    The third case is where a naive implementation leaks: treating the child's
    `None` as "no opinion, so use the child's" would hand a restricted parent's
    delegate an unrestricted set, and delegation would become the way out of
    every restriction.
    """
    parent_set = normalise(parent)
    child_set = normalise(child)
    if parent_set is None:
        return child_set
    if child_set is None:
        return parent_set
    return parent_set & child_set


def filtered(value: object, names: Iterable[str]) -> list[str]:
    """The subset of `names` this allowlist permits, order preserved."""
    resolved = normalise(value)
    if resolved is None:
        return list(names)
    return [name for name in names if str(name).strip() in resolved]


def as_list(value: object) -> Sequence[str] | None:
    """Round-trip form for storage: `None` stays `None`, a set becomes a sorted
    list so the persisted JSON is stable across runs."""
    resolved = normalise(value)
    return None if resolved is None else sorted(resolved)


__all__ = [
    "DENY_ALL",
    "EXPLICIT",
    "INHERIT",
    "as_list",
    "describe",
    "filtered",
    "narrow",
    "normalise",
    "permits",
]
